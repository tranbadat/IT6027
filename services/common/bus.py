"""Client RabbitMQ dùng chung: publish (có confirm) và consume (ack thủ công + dead-letter).

Dùng hai channel trên cùng một kết nối: một để consume, một để publish, để có thể
publish event mới ngay trong handler đang xử lý message (C2, C3, D1, D3, P3, P4, P5).
"""
import json
import time

import pika

from . import envelope as envelope_mod
from . import logging as log

_CONNECT_RETRIES = 30
_CONNECT_DELAY_SECONDS = 2


class EventBus:
    def __init__(self, url: str, source: str, exchange: str = "waf.events",
                 dlx: str = "waf.events.dlx"):
        self._url = url
        self._source = source
        self._exchange = exchange
        self._dlx = dlx
        self._conn: pika.BlockingConnection | None = None
        self._pub = None   # channel publish (bật confirm)
        self._sub = None   # channel consume

    def connect(self) -> "EventBus":
        params = pika.URLParameters(self._url)
        params.heartbeat = 60
        params.blocked_connection_timeout = 30
        last_error: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                self._conn = pika.BlockingConnection(params)
                self._pub = self._conn.channel()
                self._pub.confirm_delivery()
                log.info("connected to event bus", attempt=attempt)
                return self
            except Exception as exc:  # pika ném nhiều loại lỗi kết nối khác nhau
                last_error = exc
                log.warning("event bus not ready, retrying", attempt=attempt, error=str(exc))
                time.sleep(_CONNECT_DELAY_SECONDS)
        raise RuntimeError(f"cannot connect to event bus: {last_error}")

    def publish(self, event: str, domain: str, data: dict,
                request_id: str | None = None, session_id: str | None = None) -> dict:
        env = envelope_mod.make(event, domain, data, source=self._source,
                                request_id=request_id, session_id=session_id)
        self.publish_envelope(env)
        return env

    def _publisher_healthy(self) -> bool:
        return (self._conn is not None and self._conn.is_open
                and self._pub is not None and self._pub.is_open)

    def _reconnect(self) -> None:
        try:
            self.close()
        except Exception:
            pass
        self._conn = None
        self._pub = None
        self._sub = None
        self.connect()

    def publish_envelope(self, env: dict) -> None:
        body = json.dumps(env, ensure_ascii=False).encode("utf-8")
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=env.get("event_id"),
            type=env.get("event"),
            app_id=self._source,
        )
        # Publisher chỉ-publish (vd C1) có thể bị broker đóng kết nối sau thời gian nghỉ
        # dài (không có frame nào giữ heartbeat). Tự kết nối lại rồi thử lại một lần.
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                if not self._publisher_healthy():
                    self._reconnect()
                self._pub.basic_publish(self._exchange, routing_key=env["event"],
                                        body=body, properties=props)
                return
            except Exception as exc:
                last_error = exc
                log.warning("publish failed, reconnecting", attempt=attempt, error=str(exc))
                self._reconnect()
        raise RuntimeError(f"publish failed after reconnect: {last_error}")

    def process_events(self, seconds: float) -> None:
        """Phục vụ I/O của kết nối (giữ heartbeat) trong ~seconds giây.

        Dùng thay time.sleep trong vòng lặp của publisher idle (vd C1) để broker
        không đóng kết nối vì thiếu heartbeat.
        """
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.process_data_events(time_limit=seconds)
            else:
                time.sleep(seconds)
        except Exception:
            time.sleep(seconds)

    def dead_letter(self, body: bytes, routing_key: str, error: str, source_queue: str) -> None:
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers={"x-error": error[:500], "x-source-queue": source_queue,
                     "x-death-time": envelope_mod.now_rfc3339()},
        )
        self._pub.basic_publish(self._dlx, routing_key=routing_key or "", body=body,
                                properties=props)

    def consume(self, queue: str, handler, prefetch: int = 20) -> None:
        """Vòng lặp consume vô hạn. handler(envelope: dict, routing_key: str).

        handler ném lỗi -> message được chuyển sang dead-letter rồi ack (không requeue vô hạn).
        """
        self._sub = self._conn.channel()
        self._sub.basic_qos(prefetch_count=prefetch)
        log.info("consuming", queue=queue, prefetch=prefetch)
        for method, _props, body in self._sub.consume(queue, inactivity_timeout=1):
            if method is None:
                continue  # timeout rỗng -> giữ heartbeat sống
            try:
                env = json.loads(body)
                envelope_mod.validate(env)
                handler(env, method.routing_key)
                self._sub.basic_ack(method.delivery_tag)
            except Exception as exc:
                log.error("handler failed, dead-lettering", queue=queue,
                          routing_key=method.routing_key, error=str(exc))
                try:
                    self.dead_letter(body, method.routing_key, str(exc), queue)
                finally:
                    self._sub.basic_ack(method.delivery_tag)

    def close(self) -> None:
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:
            pass
