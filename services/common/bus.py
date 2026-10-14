"""Client Kafka dùng chung: publish (đợi broker xác nhận) và consume (commit thủ công + dead-letter).

Quy ước topology (chi tiết: contracts/events/README.md):
- Mỗi event là một topic cùng tên (log.enriched, attack.detected, ...).
- Mỗi service đọc bằng một consumer group riêng, tự khai báo danh sách topic cần đọc.
- Key message = "domain|src_ip" (domain viết thường) nếu event có src_ip, ngược lại là domain.
  Mọi event của cùng một client vào cùng partition: giữ thứ tự và để detector giữ trạng thái
  cửa sổ theo client trên một instance.
- Offset chỉ được commit SAU KHI handler xong và mọi message handler phát ra đã được broker
  xác nhận (at-least-once). Consumer xử lý idempotent theo event_id.
"""
import json
import os
import threading
import time

from . import envelope as envelope_mod
from . import logging as log

DEAD_LETTER_TOPIC = "waf.deadletter"

_CONNECT_RETRIES = 30
_CONNECT_DELAY_SECONDS = 2
_FLUSH_TIMEOUT_SECONDS = 30
_POLL_SECONDS = 1.0
_MAX_ERROR_HEADER = 500


class DeliveryError(RuntimeError):
    """Broker không xác nhận message. Không dead-letter: process phải khởi động lại và đọc lại
    message chưa commit (at-least-once)."""


def message_key(env: dict) -> str:
    """Key phân vùng theo client: "domain|src_ip", hoặc domain nếu event không gắn IP."""
    domain = (env.get("domain") or "").lower()
    src_ip = (env.get("data") or {}).get("src_ip")
    return f"{domain}|{src_ip}" if src_ip else domain


def _default_producer(conf: dict):
    from confluent_kafka import Producer
    return Producer(conf)


def _default_consumer(conf: dict):
    from confluent_kafka import Consumer
    return Consumer(conf)


class EventBus:
    def __init__(self, bootstrap_servers: str, source: str,
                 dead_letter_topic: str = DEAD_LETTER_TOPIC,
                 producer_factory=None, consumer_factory=None):
        self._bootstrap = bootstrap_servers
        self._source = source
        self._dead_letter_topic = dead_letter_topic
        self._producer_factory = producer_factory or _default_producer
        self._consumer_factory = consumer_factory or _default_consumer
        self._producer = None
        self._consumer = None

    def connect(self) -> "EventBus":
        """Tạo producer và đợi broker sẵn sàng (Kafka có thể khởi động chậm hơn service)."""
        self._producer = self._producer_factory({
            "bootstrap.servers": self._bootstrap,
            "client.id": self._source,
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 5,
            "compression.type": "lz4",
        })
        last_error: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                self._producer.list_topics(timeout=5)
                log.info("connected to event bus", attempt=attempt)
                return self
            except Exception as exc:  # broker chưa lên hoặc chưa phân giải được DNS
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

    def publish_envelope(self, env: dict) -> None:
        """Gửi envelope và chờ broker xác nhận. Ném DeliveryError nếu giao nhận thất bại."""
        body = json.dumps(env, ensure_ascii=False).encode("utf-8")
        headers = [("event", env["event"]), ("event_id", env.get("event_id", "")),
                   ("source", self._source)]
        self._send(env["event"], message_key(env), body, headers)

    def _send(self, topic: str, key: str | None, body: bytes, headers: list) -> None:
        errors: list = []

        def on_delivery(err, _msg):
            if err is not None:
                errors.append(err)

        self._producer.produce(topic, value=body, key=key, headers=headers,
                               on_delivery=on_delivery)
        remaining = self._producer.flush(_FLUSH_TIMEOUT_SECONDS)
        if errors or remaining:
            reason = errors[0] if errors else f"{remaining} message chưa được xác nhận"
            raise DeliveryError(f"publish to {topic} failed: {reason}")

    def process_events(self, seconds: float) -> None:
        """Chờ ~seconds giây trong khi phục vụ callback của producer (dùng cho C1 khi rảnh)."""
        if self._producer is not None:
            self._producer.poll(0)
        time.sleep(seconds)

    def dead_letter(self, body: bytes | None, source_topic: str, error: str, group: str) -> None:
        headers = [
            ("error", error[:_MAX_ERROR_HEADER]),
            ("source_topic", source_topic),
            ("consumer_group", group),
            ("dead_lettered_at", envelope_mod.now_rfc3339()),
        ]
        self._send(self._dead_letter_topic, None, body or b"", headers)

    def consume(self, group: str, handler, topics: list[str], max_messages: int | None = None) -> None:
        """Vòng lặp consume. handler(envelope: dict, topic: str).

        Message sai schema hoặc handler ném lỗi -> chuyển sang dead-letter rồi commit,
        không xử lý lại vô hạn. Lỗi giao nhận khi publish/dead-letter -> ném ra ngoài để
        process khởi động lại và đọc lại message chưa commit.
        max_messages chỉ dùng cho test (dừng sau khi xử lý đủ số message).
        """
        if not topics:
            raise ValueError("consume requires at least one topic")
        self._consumer = self._consumer_factory({
            "bootstrap.servers": self._bootstrap,
            "group.id": group,
            "client.id": f"{self._source}-{group}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "partition.assignment.strategy": "cooperative-sticky",
            # Process bị dừng đột ngột không kịp rời group: broker chỉ chờ 10s (mặc định 45s)
            # trước khi chia lại partition, nên service khởi động lại nhận message sớm hơn.
            "session.timeout.ms": 10000,
            "heartbeat.interval.ms": 3000,
        })
        self._consumer.subscribe(list(topics))
        log.info("consuming", group=group, topics=list(topics))
        handled = 0
        try:
            while max_messages is None or handled < max_messages:
                msg = self._consumer.poll(_POLL_SECONDS)
                if msg is None:
                    continue
                if msg.error() is not None:
                    if msg.error().fatal():
                        raise RuntimeError(f"fatal consumer error: {msg.error()}")
                    log.warning("consumer error (non-fatal)", group=group, error=str(msg.error()))
                    if max_messages is not None:
                        handled += 1
                    continue
                self._handle(msg, group, handler)
                self._producer.flush(_FLUSH_TIMEOUT_SECONDS)
                self._consumer.commit(message=msg, asynchronous=False)
                handled += 1
        finally:
            self._consumer.close()

    def _handle(self, msg, group: str, handler) -> None:
        body = msg.value()
        try:
            env = json.loads(body)
            envelope_mod.validate(env)
            handler(env, msg.topic())
        except DeliveryError:
            raise  # lỗi broker, không phải lỗi dữ liệu: để process khởi động lại
        except Exception as exc:
            log.error("handler failed, dead-lettering", group=group, topic=msg.topic(),
                      error=str(exc))
            self.dead_letter(body, msg.topic(), str(exc), group)

    def close(self) -> None:
        if self._producer is not None:
            self._producer.flush(_FLUSH_TIMEOUT_SECONDS)


def start_consumer_thread(bus_factory, group: str, handler, topics: list[str],
                          name: str | None = None, exit_fn=os._exit) -> threading.Thread:
    """Chạy consume ở thread nền (cho service vừa consume vừa phục vụ HTTP như P3, P4, P5).

    consume chỉ dừng khi có lỗi (broker không xác nhận, commit lỗi, lỗi nghiêm trọng). Khi đó
    thoát cả process với mã 1 để Docker khởi động lại, thay vì để HTTP vẫn báo healthy trong khi
    không còn tiêu thụ event. bus_factory() trả về EventBus chưa connect.
    """
    def _run() -> None:
        try:
            bus_factory().connect().consume(group, handler, topics=topics)
            log.error("consumer stopped unexpectedly, exiting for restart", group=group)
        except Exception as exc:
            log.error("consumer failed, exiting for restart", group=group, error=str(exc))
        exit_fn(1)

    thread = threading.Thread(target=_run, daemon=True, name=name or f"{group}-consumer")
    thread.start()
    return thread
