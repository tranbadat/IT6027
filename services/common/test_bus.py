"""Test EventBus trên Kafka bằng producer/consumer giả, KHÔNG cần broker.

Chạy: PYTHONPATH=/app python /app/common/test_bus.py  (thoát khác 0 nếu có test fail).
Bao phủ: key theo client, topic theo tên event, lỗi giao nhận, consume theo batch + commit sau khi
giao output, dead-letter khi JSON sai hoặc handler lỗi, bỏ qua lỗi không nghiêm trọng.
"""
import json
import threading

from common import bus as bus_mod
from common import envelope as envelope_mod


class FakeKafkaError:
    def __init__(self, text, fatal=False):
        self._text = text
        self._fatal = fatal

    def fatal(self):
        return self._fatal

    def __str__(self):
        return self._text


class FakeMessage:
    def __init__(self, topic, value, key=None, error=None, partition=0, offset=7):
        self._topic = topic
        self._value = value
        self._key = key
        self._error = error
        self._partition = partition
        self._offset = offset

    def topic(self):
        return self._topic

    def value(self):
        return self._value

    def key(self):
        return self._key

    def error(self):
        return self._error

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset


class FakeProducer:
    def __init__(self, fail_with=None, journal=None):
        self.sent = []
        self._pending = []
        self._fail_with = fail_with
        self.journal = journal if journal is not None else []

    def produce(self, topic, value=None, key=None, headers=None, on_delivery=None):
        record = {"topic": topic, "key": key, "value": value, "headers": dict(headers or [])}
        self.sent.append(record)
        self._pending.append((record, on_delivery))

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        self.journal.append("flush")
        for record, callback in self._pending:
            if callback is not None:
                callback(self._fail_with, FakeMessage(record["topic"], record["value"]))
        self._pending = []
        return 0

    def list_topics(self, timeout=None):
        return object()


class FakeConsumer:
    def __init__(self, messages, journal=None):
        self._messages = list(messages)
        self.subscribed = None
        self.committed = []
        self.poll_timeouts = []
        self.closed = False
        self.journal = journal if journal is not None else []

    def subscribe(self, topics):
        self.subscribed = list(topics)

    def poll(self, timeout=None):
        self.poll_timeouts.append(timeout)
        return self._messages.pop(0) if self._messages else None

    def commit(self, message=None, offsets=None, asynchronous=True):
        self.journal.append("commit")
        self.committed.append(sorted((tp.topic, tp.partition, tp.offset) for tp in offsets or []))

    def close(self):
        self.closed = True


def _bus(producer, consumer=None, max_batch=bus_mod.DEFAULT_MAX_BATCH, clock=None):
    return bus_mod.EventBus(
        "kafka:19092", source="test",
        producer_factory=lambda conf: producer,
        consumer_factory=lambda conf: consumer,
        max_batch=max_batch,
        **({"clock": clock} if clock else {}),
    ).connect()


def _republish(bus):
    """Handler kiểu C2/C3: mỗi message vào phát một message ra."""
    def handler(env, topic):
        bus.publish("log.normalized", domain="shop.local", data={"src_ip": "10.0.0.1"}, request_id="r")
    return handler


def _envelope_bytes(event="log.enriched", src_ip="10.0.0.1"):
    env = envelope_mod.make(event, "Shop.Local", {"src_ip": src_ip, "path": "/"},
                            source="c3-enrichment", request_id="r-1")
    return json.dumps(env).encode("utf-8")


def test_message_key_groups_events_by_client():
    env = envelope_mod.make("log.enriched", "Shop.Local", {"src_ip": "10.0.0.1"},
                            source="t", request_id="r")
    assert bus_mod.message_key(env) == "shop.local|10.0.0.1", bus_mod.message_key(env)


def test_message_key_falls_back_to_domain_without_src_ip():
    env = envelope_mod.make("stage.completed", "Shop.Local", {"stage": "c2"}, source="t")
    assert bus_mod.message_key(env) == "shop.local", bus_mod.message_key(env)


def test_client_key_lowercases_domain():
    assert bus_mod.client_key("Lab.Local", "10.0.0.1") == "lab.local|10.0.0.1"


def test_publish_uses_explicit_key_when_given():
    producer = FakeProducer()
    bus = _bus(producer)
    bus.publish("log.raw.ingested", domain="lab.local", data={"raw": "x"}, request_id="r",
                key="lab.local|203.0.113.7")
    assert producer.sent[0]["key"] == "lab.local|203.0.113.7", producer.sent[0]


def test_publish_sends_envelope_to_topic_named_after_event():
    producer = FakeProducer()
    bus = _bus(producer)
    env = bus.publish("attack.detected", domain="shop.local",
                      data={"src_ip": "10.0.0.9", "rule_id": "SQLI_UNION_SELECT"},
                      request_id="r-9")
    sent = producer.sent[0]
    assert sent["topic"] == "attack.detected", sent
    assert sent["key"] == "shop.local|10.0.0.9", sent
    assert json.loads(sent["value"])["event_id"] == env["event_id"], sent
    assert sent["headers"]["source"] == "test", sent


def test_publish_raises_when_broker_rejects_delivery():
    bus = _bus(FakeProducer(fail_with=FakeKafkaError("broker down")))
    try:
        bus.publish("log.enriched", domain="shop.local", data={"src_ip": "1.1.1.1"},
                    request_id="r")
    except RuntimeError as exc:
        assert "broker down" in str(exc), exc
        return
    raise AssertionError("publish must raise when delivery fails")


def test_consume_dispatches_then_commits_after_flush():
    journal = []
    producer = FakeProducer(journal=journal)
    consumer = FakeConsumer([FakeMessage("log.enriched", _envelope_bytes())], journal=journal)
    seen = []
    bus = _bus(producer, consumer)
    bus.consume("p5-dashboard", lambda env, topic: seen.append((env["event"], topic)),
                topics=["log.enriched"], max_messages=1)
    assert consumer.subscribed == ["log.enriched"], consumer.subscribed
    assert seen == [("log.enriched", "log.enriched")], seen
    assert journal == ["flush", "commit"], journal


def test_consume_dead_letters_invalid_json_and_commits():
    producer = FakeProducer()
    consumer = FakeConsumer([FakeMessage("log.enriched", b"{not json")])
    bus = _bus(producer, consumer)
    bus.consume("p5-dashboard", lambda env, topic: None, topics=["log.enriched"], max_messages=1)
    dead = [m for m in producer.sent if m["topic"] == bus_mod.DEAD_LETTER_TOPIC]
    assert len(dead) == 1, producer.sent
    assert dead[0]["headers"]["source_topic"] == "log.enriched", dead[0]
    assert dead[0]["headers"]["consumer_group"] == "p5-dashboard", dead[0]
    assert len(consumer.committed) == 1, consumer.committed


def test_consume_dead_letters_when_handler_fails():
    producer = FakeProducer()
    consumer = FakeConsumer([FakeMessage("attack.detected", _envelope_bytes("attack.detected"))])

    def boom(env, topic):
        raise ValueError("bad data")

    bus = _bus(producer, consumer)
    bus.consume("p4-alerting", boom, topics=["attack.detected"], max_messages=1)
    dead = [m for m in producer.sent if m["topic"] == bus_mod.DEAD_LETTER_TOPIC]
    assert len(dead) == 1 and "bad data" in dead[0]["headers"]["error"], dead
    assert len(consumer.committed) == 1, consumer.committed


def test_consume_skips_non_fatal_errors():
    producer = FakeProducer()
    consumer = FakeConsumer([
        FakeMessage("log.enriched", None, error=FakeKafkaError("unknown topic")),
        FakeMessage("log.enriched", _envelope_bytes()),
    ])
    seen = []
    bus = _bus(producer, consumer)
    bus.consume("g", lambda env, topic: seen.append(env["event"]),
                topics=["log.enriched"], max_messages=2)
    assert seen == ["log.enriched"], seen
    assert len(consumer.committed) == 1, consumer.committed


def test_consume_requires_topics():
    bus = _bus(FakeProducer(), FakeConsumer([]))
    try:
        bus.consume("g", lambda env, topic: None, topics=[])
    except ValueError:
        return
    raise AssertionError("consume without topics must raise ValueError")


def test_consume_reraises_publish_failure_without_commit_or_dead_letter():
    producer = FakeProducer(fail_with=FakeKafkaError("broker down"))
    consumer = FakeConsumer([FakeMessage("log.normalized", _envelope_bytes("log.normalized"))])
    bus = _bus(producer, consumer)

    def handler(env, topic):
        bus.publish("log.enriched", domain="shop.local", data={"src_ip": "10.0.0.1"}, request_id="r")

    try:
        bus.consume("c3-enrichment", handler, topics=["log.normalized"], max_messages=1)
    except bus_mod.DeliveryError:
        assert consumer.committed == [], consumer.committed
        assert not any(m["topic"] == bus_mod.DEAD_LETTER_TOPIC for m in producer.sent), producer.sent
        return
    raise AssertionError("publish failure inside handler must propagate as DeliveryError")


def test_deferred_delivery_waits_once_for_many_publishes():
    journal = []
    producer = FakeProducer(journal=journal)
    bus = _bus(producer)
    with bus.deferred_delivery("log.raw.ingested"):
        for i in range(3):
            bus.publish("log.raw.ingested", domain="lab.local", data={"raw": str(i)}, request_id=f"r-{i}")
        assert journal == [], journal
    assert journal == ["flush"], journal
    assert len(producer.sent) == 3, producer.sent


def test_deferred_delivery_raises_when_any_message_is_not_delivered():
    bus = _bus(FakeProducer(fail_with=FakeKafkaError("broker down")))
    try:
        with bus.deferred_delivery("log.raw.ingested"):
            bus.publish("log.raw.ingested", domain="lab.local", data={"raw": "x"}, request_id="r")
    except bus_mod.DeliveryError as exc:
        assert "broker down" in str(exc), exc
        return
    raise AssertionError("deferred_delivery must raise DeliveryError when delivery fails")


def test_deferred_delivery_ends_when_block_raises():
    journal = []
    producer = FakeProducer(journal=journal)
    bus = _bus(producer)
    try:
        with bus.deferred_delivery("log.raw.ingested"):
            raise ValueError("boom")
    except ValueError:
        pass
    bus.publish("log.raw.ingested", domain="lab.local", data={"raw": "x"}, request_id="r")
    assert journal == ["flush"], journal  # publish sau khối lại chờ xác nhận ngay


def test_consume_flushes_and_commits_once_per_batch():
    journal = []
    producer = FakeProducer(journal=journal)
    consumer = FakeConsumer([FakeMessage("log.raw.ingested", _envelope_bytes("log.raw.ingested"), offset=i)
                             for i in range(3)], journal=journal)
    bus = _bus(producer, consumer)
    bus.consume("c2-parser", _republish(bus), topics=["log.raw.ingested"], max_messages=3)
    assert len([m for m in producer.sent if m["topic"] == "log.normalized"]) == 3, producer.sent
    assert journal == ["flush", "commit"], journal
    assert consumer.committed == [[("log.raw.ingested", 0, 3)]], consumer.committed


def test_consume_commits_next_offset_of_each_partition():
    consumer = FakeConsumer([
        FakeMessage("log.raw.ingested", _envelope_bytes("log.raw.ingested"), partition=0, offset=10),
        FakeMessage("log.raw.ingested", _envelope_bytes("log.raw.ingested"), partition=1, offset=4),
        FakeMessage("log.raw.ingested", _envelope_bytes("log.raw.ingested"), partition=1, offset=5),
    ])
    bus = _bus(FakeProducer(), consumer)
    bus.consume("c2-parser", lambda env, topic: None, topics=["log.raw.ingested"], max_messages=3)
    assert consumer.committed == [[("log.raw.ingested", 0, 11), ("log.raw.ingested", 1, 6)]], consumer.committed


def test_consume_caps_batch_size():
    consumer = FakeConsumer([FakeMessage("log.enriched", _envelope_bytes(), offset=i) for i in range(5)])
    bus = _bus(FakeProducer(), consumer, max_batch=2)
    bus.consume("g", lambda env, topic: None, topics=["log.enriched"], max_messages=5)
    assert consumer.committed == [[("log.enriched", 0, 2)], [("log.enriched", 0, 4)], [("log.enriched", 0, 5)]], \
        consumer.committed


def test_consume_waits_only_for_first_message_of_batch():
    # None ở giữa: message thứ ba tới sau khi hai message đầu đã được lấy.
    consumer = FakeConsumer([FakeMessage("log.enriched", _envelope_bytes(), offset=0),
                             FakeMessage("log.enriched", _envelope_bytes(), offset=1),
                             None,
                             FakeMessage("log.enriched", _envelope_bytes(), offset=2)])
    bus = _bus(FakeProducer(), consumer)
    bus.consume("g", lambda env, topic: None, topics=["log.enriched"], max_messages=3)
    # Lấy hết message có sẵn mà không chờ; batch kết thúc ngay khi hết (lưu lượng thấp không bị trễ thêm).
    assert consumer.poll_timeouts == [bus_mod._POLL_SECONDS, 0, 0, bus_mod._POLL_SECONDS], consumer.poll_timeouts
    assert consumer.committed == [[("log.enriched", 0, 2)], [("log.enriched", 0, 3)]], consumer.committed


def test_consume_handles_each_message_before_polling_the_next():
    # Handler chậm (P4 gửi email) không làm consumer quá max.poll.interval.ms: poll giữa mọi message.
    consumer = FakeConsumer([FakeMessage("alert.triggered", _envelope_bytes("alert.triggered"), offset=i)
                             for i in range(3)])
    polls_seen_by_handler = []
    bus = _bus(FakeProducer(), consumer)
    bus.consume("p4-alerting", lambda env, topic: polls_seen_by_handler.append(len(consumer.poll_timeouts)),
                topics=["alert.triggered"], max_messages=3)
    assert polls_seen_by_handler == [1, 2, 3], polls_seen_by_handler
    assert consumer.committed == [[("alert.triggered", 0, 3)]], consumer.committed


def test_consume_ends_batch_after_time_budget():
    now = [0.0]

    def slow_handler(env, topic):
        now[0] += 0.6 * bus_mod._BATCH_MAX_SECONDS

    consumer = FakeConsumer([FakeMessage("alert.triggered", _envelope_bytes("alert.triggered"), offset=i)
                             for i in range(3)])
    bus = _bus(FakeProducer(), consumer, clock=lambda: now[0])
    bus.consume("p4-alerting", slow_handler, topics=["alert.triggered"], max_messages=3)
    # Message thứ hai làm batch vượt ngân sách thời gian: commit, rồi message thứ ba sang batch mới.
    assert consumer.committed == [[("alert.triggered", 0, 2)], [("alert.triggered", 0, 3)]], consumer.committed


def test_consume_commits_nothing_when_any_delivery_in_batch_fails():
    journal = []
    producer = FakeProducer(fail_with=FakeKafkaError("broker down"), journal=journal)
    consumer = FakeConsumer([FakeMessage("log.raw.ingested", _envelope_bytes("log.raw.ingested"), offset=i)
                             for i in range(3)], journal=journal)
    bus = _bus(producer, consumer)
    try:
        bus.consume("c2-parser", _republish(bus), topics=["log.raw.ingested"], max_messages=3)
    except bus_mod.DeliveryError as exc:
        assert "broker down" in str(exc), exc
        assert consumer.committed == [], consumer.committed
        assert journal == ["flush"], journal
        return
    raise AssertionError("delivery failure in a batch must raise DeliveryError without committing")


def test_publish_from_another_thread_during_batch_still_waits_for_delivery():
    journal = []
    producer = FakeProducer(journal=journal)
    consumer = FakeConsumer([FakeMessage("log.enriched", _envelope_bytes())], journal=journal)
    bus = _bus(producer, consumer)

    def handler(env, topic):
        other = threading.Thread(target=lambda: bus.publish(
            "stage.completed", domain="shop.local", data={"stage": "c3"}))
        other.start()
        other.join()
        journal.append("other-thread-done")

    bus.consume("g", handler, topics=["log.enriched"], max_messages=1)
    assert journal == ["flush", "other-thread-done", "flush", "commit"], journal


def test_consumer_thread_exits_process_when_consume_fails():
    exits = []

    class FailingBus:
        def connect(self):
            return self

        def consume(self, group, handler, topics):
            raise RuntimeError("commit failed")

    thread = bus_mod.start_consumer_thread(lambda: FailingBus(), "p4-alerting", lambda e, t: None,
                                           ["alert.triggered"], exit_fn=exits.append)
    thread.join(timeout=5)
    assert exits == [1], exits


def _run() -> None:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
