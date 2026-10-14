"""metrics-exporter — biến event bus thành /metrics cho Prometheus.

Consume mọi topic event (consumer group metrics-exporter) và cập nhật counter. Không sửa service nào khác.
Prometheus scrape http://metrics-exporter:8000/metrics.
"""
from prometheus_client import start_http_server

from common import bus as bus_mod
from common import config
from common import logging as log

import collectors

SOURCE = "metrics-exporter"
TOPICS = ["log.raw.ingested", "log.normalized", "log.enriched", "attack.detected",
          "anomaly.scored", "alert.triggered", "stage.completed"]


def main() -> None:
    log.set_service(SOURCE)
    port = config.env_int("HTTP_PORT", 8000)
    start_http_server(port)  # phục vụ /metrics ở một thread nền
    log.info("metrics /metrics endpoint up", port=port)

    group = config.env("EVENT_GROUP", SOURCE)
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    log.info("metrics-exporter started", group=group, topics=TOPICS)
    bus.consume(group, lambda env, topic: collectors.update(env), topics=TOPICS)


if __name__ == "__main__":
    main()
