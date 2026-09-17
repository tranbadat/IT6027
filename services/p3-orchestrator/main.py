"""P3 — Pipeline Orchestration.

Theo dõi tiến độ xử lý toàn pipeline qua event bus và expose trạng thái qua HTTP.

- Thread consumer (kết nối bus riêng): nhận log.# + attack.detected + anomaly.scored
  + alert.triggered, tăng bộ đếm theo (domain, stage).
- Thread publisher (kết nối bus RIÊNG — pika BlockingConnection không thread-safe):
  định kỳ publish stage.completed cho mỗi (domain, stage) có thay đổi.
- Thread chính: FastAPI/uvicorn phục vụ GET /healthz và GET /pipelines.
"""
import threading
import time

import uvicorn
from fastapi import FastAPI

from common import bus as bus_mod
from common import config
from common import logging as log

import pipelines as pipelines_mod
import state as state_mod

SOURCE = "p3-orchestrator"

# Ánh xạ event (routing key) -> stage trong pipeline.
EVENT_STAGE = {
    "log.raw.ingested": "ingestion",
    "log.normalized": "normalize",
    "log.enriched": "enrich",
    "attack.detected": "detect",
    "anomaly.scored": "score",
    "alert.triggered": "alert",
}

app = FastAPI(title="P3 Orchestrator Service", version="0.1.0")

# Tham chiếu dùng bởi HTTP handler (gán trong main() trước khi uvicorn chạy).
_state = state_mod.PipelineState()
_pipelines = []


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/pipelines")
def list_pipelines() -> dict:
    """Cấu hình pipeline đã nạp + trạng thái runtime (đếm theo stage, last_event_at)."""
    return {"pipelines": _pipelines, "runtime": _state.snapshot()}


def build_handler(state):
    """Tạo handler consume: map event -> stage rồi ghi nhận theo (domain, stage)."""
    def handle(env: dict, routing_key: str) -> None:
        stage = EVENT_STAGE.get(env["event"])
        if stage is None:
            log.debug("event without stage mapping, ignored", event=env["event"])
            return
        state.record(env["domain"], stage, env.get("timestamp"))
        log.debug("stage progressed", domain=env["domain"], stage=stage)

    return handle


def run_consumer(bus_url, queue, state):
    """Thread nền: kết nối bus riêng và consume vô hạn."""
    bus = bus_mod.EventBus(bus_url, source=SOURCE).connect()
    log.info("consumer started", queue=queue)
    bus.consume(queue, build_handler(state))


def run_publisher(bus_url, state, domain_to_pipeline, window_seconds):
    """Thread nền: kết nối bus RIÊNG, định kỳ publish stage.completed cho thay đổi."""
    bus = bus_mod.EventBus(bus_url, source=SOURCE).connect()
    log.info("publisher started", window_seconds=window_seconds)
    while True:
        time.sleep(window_seconds)
        _publish_changes(bus, state, domain_to_pipeline, window_seconds)


def _publish_changes(bus, state, domain_to_pipeline, window_seconds):
    for domain, stage, processed in state.drain_changes():
        pipeline = domain_to_pipeline.get(domain, domain)
        try:
            # stage.completed KHÔNG cần request_id/session_id (theo envelope contract).
            bus.publish("stage.completed", domain=domain, data={
                "pipeline": pipeline,
                "stage": stage,
                "processed": processed,
                "window_seconds": window_seconds,
            })
            log.info("stage.completed published", domain=domain, stage=stage,
                     processed=processed)
        except Exception as exc:  # không để 1 lỗi publish làm chết vòng lặp
            log.error("failed to publish stage.completed", domain=domain,
                      stage=stage, error=str(exc))


def _start_thread(target, name, args):
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread


def main() -> None:
    global _pipelines
    log.set_service(SOURCE)
    bus_url = config.env("EVENT_BUS_URL", required=True)
    queue = config.env("EVENT_QUEUE", "q.p3-orchestrator")
    pipelines_dir = config.env("PIPELINES_DIR", "/config/pipelines")
    window_seconds = config.env_int("STAGE_WINDOW_SECONDS", 10)

    _pipelines = pipelines_mod.load_pipelines(pipelines_dir)
    domain_to_pipeline = pipelines_mod.domain_index(_pipelines)

    _start_thread(run_consumer, "consumer", (bus_url, queue, _state))
    _start_thread(run_publisher, "publisher",
                  (bus_url, _state, domain_to_pipeline, window_seconds))

    log.info("p3-orchestrator started", queue=queue, pipelines=len(_pipelines))
    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000),
                access_log=False)


if __name__ == "__main__":
    main()
