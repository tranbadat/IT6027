"""Client Kafka dùng chung: publish (đợi broker xác nhận) và consume (commit thủ công + dead-letter).

Quy ước topology (chi tiết: contracts/events/README.md):
- Mỗi event là một topic cùng tên (log.enriched, attack.detected, ...).
- Mỗi service đọc bằng một consumer group riêng, tự khai báo danh sách topic cần đọc.
- Key message = "domain|src_ip" (domain viết thường) nếu event có src_ip, ngược lại là domain;
  publish có thể truyền key riêng (C1 lấy IP ở đầu dòng log thô).
  Mọi event của cùng một client vào cùng partition: giữ thứ tự và để detector giữ trạng thái
  cửa sổ theo client trên một instance.
- Offset chỉ được commit SAU KHI handler xong và mọi message handler phát ra đã được broker
  xác nhận (at-least-once). Consumer xử lý idempotent theo event_id.
- Consume theo batch: xử lý lần lượt các message đã có sẵn (tối đa max_batch message hoặc
  _BATCH_MAX_SECONDS), rồi flush producer và commit MỘT lần cho cả batch. Lưu lượng thấp thì batch
  chỉ có một message nên độ trễ không đổi; lưu lượng cao thì không phải chờ broker sau từng message.
"""
import contextlib
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
DEFAULT_MAX_BATCH = 500
# Batch kết thúc khi đã xử lý quá thời gian này, để giới hạn số message phải xử lý lại sau sự cố
# khi handler chậm (P4 gửi email, webhook).
_BATCH_MAX_SECONDS = 1.0


class DeliveryError(RuntimeError):
    """Broker không xác nhận message. Không dead-letter: process phải khởi động lại và đọc lại
    message chưa commit (at-least-once)."""


def client_key(domain: str, src_ip: str) -> str:
    """Key theo client: "domain|src_ip", domain viết thường."""
    return f"{domain.lower()}|{src_ip}"


def message_key(env: dict) -> str:
    """Key phân vùng theo client: "domain|src_ip", hoặc domain nếu event không gắn IP."""
    domain = env.get("domain") or ""
    src_ip = (env.get("data") or {}).get("src_ip")
    return client_key(domain, src_ip) if src_ip else domain.lower()


def _raise_if_undelivered(target: str, errors: list, remaining: int) -> None:
    if errors or remaining:
        reason = errors[0] if errors else f"{remaining} message chưa được xác nhận"
        raise DeliveryError(f"publish {target} failed: {reason}")


def _topic_partition(topic: str, partition: int, offset: int):
    from confluent_kafka import TopicPartition
    return TopicPartition(topic, partition, offset)


def _next_offsets(messages: list) -> list:
    """Offset cần commit cho batch: offset lớn nhất + 1 của mỗi (topic, partition)."""
    latest: dict[tuple[str, int], int] = {}
    for msg in messages:
        position = (msg.topic(), msg.partition())
        latest[position] = max(latest.get(position, -1), msg.offset())
    return [_topic_partition(topic, partition, offset + 1)
            for (topic, partition), offset in sorted(latest.items())]


def _default_producer(conf: dict):
    from confluent_kafka import Producer
    return Producer(conf)


def _default_consumer(conf: dict):
    from confluent_kafka import Consumer
    return Consumer(conf)


class EventBus:
    def __init__(self, bootstrap_servers: str, source: str,
                 dead_letter_topic: str = DEAD_LETTER_TOPIC,
                 producer_factory=None, consumer_factory=None,
                 max_batch: int = DEFAULT_MAX_BATCH, clock=time.monotonic):
        if max_batch < 1:
            raise ValueError("max_batch must be >= 1")
        self._bootstrap = bootstrap_servers
        self._source = source
        self._dead_letter_topic = dead_letter_topic
        self._producer_factory = producer_factory or _default_producer
        self._consumer_factory = consumer_factory or _default_consumer
        self._max_batch = max_batch
        self._clock = clock
        self._producer = None
        self._consumer = None
        # Danh sách lỗi giao nhận của batch đang xử lý, riêng cho thread đang chạy consume: publish từ
        # thread khác (nếu có) vẫn chờ xác nhận như bình thường.
        self._batch = threading.local()

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
                request_id: str | None = None, session_id: str | None = None,
                key: str | None = None) -> dict:
        env = envelope_mod.make(event, domain, data, source=self._source,
                                request_id=request_id, session_id=session_id)
        self.publish_envelope(env, key=key)
        return env

    def publish_envelope(self, env: dict, key: str | None = None) -> None:
        """Gửi envelope; key mặc định theo message_key(env).

        Ngoài consume: chờ broker xác nhận, ném DeliveryError nếu giao nhận thất bại. Trong handler
        của consume: chỉ đưa vào hàng đợi; consume flush và kiểm tra một lần khi hết batch.
        """
        body = json.dumps(env, ensure_ascii=False).encode("utf-8")
        headers = [("event", env["event"]), ("event_id", env.get("event_id", "")),
                   ("source", self._source)]
        self._send(env["event"], key or message_key(env), body, headers)

    def _send(self, topic: str, key: str | None, body: bytes, headers: list) -> None:
        batch_errors = getattr(self._batch, "errors", None)
        if batch_errors is not None:
            self._produce(topic, key, body, headers, batch_errors)
            return
        errors: list = []
        self._produce(topic, key, body, headers, errors)
        _raise_if_undelivered(f"to {topic}", errors, self._producer.flush(_FLUSH_TIMEOUT_SECONDS))

    def _produce(self, topic: str, key: str | None, body: bytes, headers: list, errors: list) -> None:
        def on_delivery(err, _msg):
            if err is not None:
                errors.append(err)

        def send():
            self._producer.produce(topic, value=body, key=key, headers=headers,
                                   on_delivery=on_delivery)

        try:
            send()
        except BufferError:
            # Hàng đợi cục bộ của producer đầy (batch lớn): đợi broker nhận bớt rồi gửi lại.
            self._producer.flush(_FLUSH_TIMEOUT_SECONDS)
            send()

    @contextlib.contextmanager
    def deferred_delivery(self, target: str):
        """Gom các publish (và dead-letter) trong khối của thread hiện tại: chỉ đưa vào hàng đợi, khi
        ra khỏi khối flush một lần và ném DeliveryError nếu có message không được broker xác nhận.
        Khối ném lỗi thì lỗi được ném tiếp, không flush. Dùng khi gửi nhiều message liền nhau (C1, consume).
        """
        errors: list = []
        self._batch.errors = errors
        try:
            yield
        finally:
            self._batch.errors = None
        _raise_if_undelivered(target, errors, self._producer.flush(_FLUSH_TIMEOUT_SECONDS))

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
        """Vòng lặp consume theo batch. handler(envelope: dict, topic: str).

        Message sai schema hoặc handler ném lỗi -> chuyển sang dead-letter rồi commit,
        không xử lý lại vô hạn. Lỗi giao nhận khi publish/dead-letter -> ném ra ngoài, không
        commit batch đó, để process khởi động lại và xử lý lại từ offset đã commit.
        max_messages chỉ dùng cho test (dừng sau khi poll được đủ số message).
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
                limit = (self._max_batch if max_messages is None
                         else min(self._max_batch, max_messages - handled))
                handled += self._consume_batch(group, handler, limit)
        finally:
            self._consumer.close()

    def _consume_batch(self, group: str, handler, limit: int) -> int:
        """Một batch: chờ tối đa _POLL_SECONDS cho message đầu tiên, rồi xử lý tiếp các message đã có
        sẵn (poll không chờ) cho tới khi hết, đủ limit hoặc quá _BATCH_MAX_SECONDS; sau đó flush
        producer và commit offset kế tiếp của từng partition một lần.

        Mỗi message được xử lý ngay sau khi poll, nên poll được gọi giữa mọi message: handler chậm
        không làm consumer quá max.poll.interval.ms và bị loại khỏi group. Output nào không được
        broker xác nhận -> DeliveryError, không commit batch. Trả số lượt poll có kết quả.
        """
        polled, done = 0, []
        timeout, deadline = _POLL_SECONDS, None
        with self.deferred_delivery(f"output of a batch consumed by {group}"):
            while polled < limit and (deadline is None or self._clock() < deadline):
                msg = self._consumer.poll(timeout)
                if msg is None:
                    break
                polled += 1
                timeout = 0
                if deadline is None:
                    deadline = self._clock() + _BATCH_MAX_SECONDS
                if msg.error() is not None:
                    if msg.error().fatal():
                        raise RuntimeError(f"fatal consumer error: {msg.error()}")
                    log.warning("consumer error (non-fatal)", group=group, error=str(msg.error()))
                    continue
                self._handle(msg, group, handler)
                done.append(msg)
        if done:
            self._consumer.commit(offsets=_next_offsets(done), asynchronous=False)
        return polled

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
