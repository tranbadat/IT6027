import threading
import time

import fakeredis
import pytest

from wafcollect.common import events
from wafcollect.common.bus import BusUnavailable, InMemoryBus, RedisStreamBus, RetryLater


def ev(n=1):
    return events.make_event("log.raw.ingested", {"n": n}, request_id=f"r{n}")


def run_bus(bus):
    stop = threading.Event()
    t = threading.Thread(target=bus.run, args=(stop,), daemon=True)
    t.start()
    return stop, t


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_inmemory_dispatch():
    bus, got = InMemoryBus(), []
    bus.subscribe("log.raw.ingested", "g", got.append)
    bus.publish(ev(1))
    assert got[0]["data"]["n"] == 1 and len(bus.of("log.raw.ingested")) == 1


def test_redis_stream_publish_and_consume():
    r = fakeredis.FakeRedis(decode_responses=True)
    bus, got = RedisStreamBus(client=r, block_ms=50), []
    bus.subscribe("log.raw.ingested", "svc", got.append)
    for i in range(5):
        bus.publish(ev(i))
    stop, t = run_bus(bus)
    assert wait_for(lambda: len(got) == 5)
    stop.set(); t.join(3)
    assert [g["data"]["n"] for g in got] == [0, 1, 2, 3, 4]
    assert r.xpending("waf:events:log.raw.ingested", "svc")["pending"] == 0   # đã ack hết


def test_each_group_gets_every_message():
    r = fakeredis.FakeRedis(decode_responses=True)
    a, b = [], []
    bus = RedisStreamBus(client=r, block_ms=50)
    bus.subscribe("log.raw.ingested", "g1", a.append)
    bus.subscribe("log.raw.ingested", "g2", b.append)
    bus.publish(ev(1))
    stop, t = run_bus(bus)
    assert wait_for(lambda: a and b)
    stop.set(); t.join(3)


def test_consumer_started_late_still_gets_earlier_events():
    r = fakeredis.FakeRedis(decode_responses=True)
    RedisStreamBus(client=r).publish(ev(7))
    got = []
    bus = RedisStreamBus(client=r, block_ms=50)
    bus.subscribe("log.raw.ingested", "late", got.append)
    stop, t = run_bus(bus)
    assert wait_for(lambda: len(got) == 1)
    stop.set(); t.join(3)


def test_retry_later_keeps_message_until_handler_succeeds():
    r = fakeredis.FakeRedis(decode_responses=True)
    calls = []

    def handler(e):
        calls.append(1)
        if len(calls) < 3:
            raise RetryLater("scope down")

    bus = RedisStreamBus(client=r, block_ms=50)
    bus.subscribe("log.raw.ingested", "svc", handler)
    bus.publish(ev(1))
    stop, t = run_bus(bus)
    assert wait_for(lambda: len(calls) >= 3, timeout=8)
    stop.set(); t.join(3)


def test_unacked_message_is_redelivered_after_restart():
    r = fakeredis.FakeRedis(decode_responses=True)
    bus1 = RedisStreamBus(client=r, block_ms=50)
    bus1.publish(ev(1))
    started, release = threading.Event(), threading.Event()

    def stuck(e):
        started.set()
        raise RetryLater("never succeeds")

    bus1.subscribe("log.raw.ingested", "svc", stuck)
    stop1, t1 = run_bus(bus1)
    assert started.wait(3)
    stop1.set(); t1.join(5)                          # tắt giữa chừng, message chưa ack
    assert r.xpending("waf:events:log.raw.ingested", "svc")["pending"] == 1

    got = []
    bus2 = RedisStreamBus(client=r, block_ms=50)     # cùng tên consumer mặc định
    bus2.subscribe("log.raw.ingested", "svc", got.append)
    stop2, t2 = run_bus(bus2)
    assert wait_for(lambda: len(got) == 1)
    stop2.set(); t2.join(3)


def test_crashing_handler_does_not_block_pipeline():
    r = fakeredis.FakeRedis(decode_responses=True)
    got = []

    def handler(e):
        if e["data"]["n"] == 1:
            raise ValueError("bug")
        got.append(e)

    bus = RedisStreamBus(client=r, block_ms=50)
    bus.subscribe("log.raw.ingested", "svc", handler)
    for i in (1, 2):
        bus.publish(ev(i))
    stop, t = run_bus(bus)
    assert wait_for(lambda: len(got) == 1)
    stop.set(); t.join(3)


def test_corrupt_payload_is_skipped():
    r = fakeredis.FakeRedis(decode_responses=True)
    r.xadd("waf:events:log.raw.ingested", {"payload": "{not json"})
    got = []
    bus = RedisStreamBus(client=r, block_ms=50)
    bus.subscribe("log.raw.ingested", "svc", got.append)
    bus.publish(ev(2))
    stop, t = run_bus(bus)
    assert wait_for(lambda: len(got) == 1)
    stop.set(); t.join(3)


def test_publish_failure_raises_bus_unavailable():
    import redis

    class Broken:
        def xadd(self, *a, **k):
            raise redis.ConnectionError("down")

    with pytest.raises(BusUnavailable):
        RedisStreamBus(client=Broken()).publish(ev(1))
