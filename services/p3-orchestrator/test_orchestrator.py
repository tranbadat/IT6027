"""Unit test P3: nạp cấu hình pipeline, đếm theo (domain, stage), phát stage.completed.

Chạy: python test_orchestrator.py (thoát khác 0 nếu có test fail). Không cần Kafka hay HTTP.
"""
import os
import tempfile

import main as main_mod
import pipelines as pipelines_mod
import state as state_mod


def _write(directory, name, text):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write(text)


def _env(event, domain="shop.local", event_id="e-1", timestamp="2026-10-14T03:00:00+00:00"):
    return {"event": event, "event_id": event_id, "domain": domain, "timestamp": timestamp,
            "source": "test", "data": {}}


def test_load_pipelines_normalizes_and_skips_bad_files():
    with tempfile.TemporaryDirectory() as directory:
        _write(directory, "shop.local.yaml", "domain: shop.local\nstages: [ingestion, detect]\n")
        _write(directory, "blog.yml", "pipeline: blog-pipeline\n")
        _write(directory, "broken.yaml", "stages: [unclosed\n")
        _write(directory, "notes.txt", "không phải YAML pipeline")
        loaded = pipelines_mod.load_pipelines(directory)
    assert loaded == [
        {"pipeline": "blog-pipeline", "domain": "blog-pipeline", "stages": []},
        {"pipeline": "shop.local", "domain": "shop.local", "stages": ["ingestion", "detect"]},
    ], loaded
    assert pipelines_mod.domain_index(loaded) == {"blog-pipeline": "blog-pipeline", "shop.local": "shop.local"}


def test_load_pipelines_missing_directory_returns_empty():
    assert pipelines_mod.load_pipelines("/khong/ton/tai") == []


def test_handler_maps_events_to_stages_and_ignores_others():
    state = state_mod.PipelineState()
    handle = main_mod.build_handler(state)
    for index, event in enumerate(("log.raw.ingested", "log.enriched", "attack.detected", "stage.completed")):
        handle(_env(event, event_id=f"e-{index}"), event)
    stages = {(row["domain"], row["stage"]): row["count"] for row in state.snapshot()}
    assert stages == {("shop.local", "ingestion"): 1, ("shop.local", "enrich"): 1,
                      ("shop.local", "detect"): 1}, stages


def test_redelivered_event_is_counted_once():
    state = state_mod.PipelineState()
    handle = main_mod.build_handler(state)
    handle(_env("log.enriched", event_id="same"), "log.enriched")
    handle(_env("log.enriched", event_id="same"), "log.enriched")   # Kafka giao lại (at-least-once)
    assert state.snapshot()[0]["count"] == 1, state.snapshot()


def test_drain_changes_returns_delta_since_last_publish():
    state = state_mod.PipelineState()
    state.record("shop.local", "detect", "t1", "a")
    state.record("shop.local", "detect", "t2", "b")
    assert state.drain_changes() == [("shop.local", "detect", 2)]
    assert state.drain_changes() == []
    state.record("shop.local", "detect", "t3", "c")
    assert state.drain_changes() == [("shop.local", "detect", 1)]
    snapshot = state.snapshot()
    assert snapshot == [{"domain": "shop.local", "stage": "detect", "count": 3, "last_event_at": "t3"}], snapshot


def test_publish_changes_emits_stage_completed_with_pipeline_name():
    class FakeBus:
        def __init__(self):
            self.published = []

        def publish(self, event, domain, data, request_id=None, session_id=None):
            self.published.append((event, domain, data))

    state = state_mod.PipelineState()
    state.record("shop.local", "detect", "t1", "a")
    bus = FakeBus()
    main_mod._publish_changes(bus, state, {"shop.local": "shop-pipeline"}, 10)
    assert bus.published == [("stage.completed", "shop.local",
                              {"pipeline": "shop-pipeline", "stage": "detect", "processed": 1,
                               "window_seconds": 10})], bus.published


def _run() -> None:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
