"""Handler + thread nền tiêu thụ q.p5-dashboard (tất cả loại event).

Cập nhật AggregateStore và đẩy các event realtime vào StreamHub.
"""
import threading

from common import bus as bus_mod
from common import logging as log

SOURCE = "p5-dashboard"

# Chỉ các event này được đẩy realtime qua SSE; stage.completed chỉ vào tổng hợp.
_REALTIME_EVENTS = {"attack.detected", "anomaly.scored", "alert.triggered", "log.enriched"}


def build_handler(store, hub):
    def handle(env: dict, routing_key: str) -> None:
        store.record(env)
        if env.get("event") in _REALTIME_EVENTS:
            hub.publish_threadsafe({
                "event": env.get("event"),
                "domain": env.get("domain"),
                "timestamp": env.get("timestamp"),
                "data": env.get("data") or {},
            })

    return handle


def start_consumer_thread(bus_url: str, queue: str, handler) -> threading.Thread:
    """Chạy consumer ở thread nền với kết nối bus RIÊNG (pika không thread-safe)."""
    def _run() -> None:
        bus = bus_mod.EventBus(bus_url, source=SOURCE).connect()
        log.info("p5-dashboard consumer started", queue=queue)
        bus.consume(queue, handler)

    thread = threading.Thread(target=_run, daemon=True, name="p5-consumer")
    thread.start()
    return thread
