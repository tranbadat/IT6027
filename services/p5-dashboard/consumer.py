"""Handler + thread nền tiêu thụ các topic event (consumer group p5-dashboard).

Cập nhật AggregateStore và đẩy các event realtime vào StreamHub.
"""
import threading

from common import bus as bus_mod
from common import logging as log

SOURCE = "p5-dashboard"
TOPICS = ["log.enriched", "attack.detected", "anomaly.scored", "alert.triggered",
          "stage.completed"]

# Chỉ các event này được đẩy realtime qua SSE; stage.completed chỉ vào tổng hợp.
_REALTIME_EVENTS = {"attack.detected", "anomaly.scored", "alert.triggered", "log.enriched"}


def build_handler(store, hub, repo=None):
    def handle(env: dict, routing_key: str) -> None:
        store.record(env)
        if repo is not None and env.get("event") == "attack.detected":
            try:
                repo.insert_from_event(env)
            except Exception as exc:  # best-effort, không làm chết handler
                log.warning("persist attack failed", error=str(exc), event_id=env.get("event_id"))
        if env.get("event") in _REALTIME_EVENTS:
            hub.publish_threadsafe({
                "event": env.get("event"),
                "domain": env.get("domain"),
                "timestamp": env.get("timestamp"),
                "data": env.get("data") or {},
            })

    return handle


def start_consumer_thread(bus_url: str, group: str, handler) -> threading.Thread:
    """Chạy consumer ở thread nền với client Kafka RIÊNG; consumer lỗi thì thoát process để Docker khởi động lại."""
    log.info("p5-dashboard consumer starting", group=group, topics=TOPICS)
    return bus_mod.start_consumer_thread(lambda: bus_mod.EventBus(bus_url, source=SOURCE), group,
                                         handler, TOPICS, name="p5-consumer")
