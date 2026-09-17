"""Unit test P5: tổng hợp AggregateStore (traffic/top attack/top ip/alerts/stages).

Chạy: python /app/p5-dashboard/test_dashboard.py  (PYTHONPATH=/app để import common).
"""
import store as store_mod


def _enriched(domain, ts):
    return {"event": "log.enriched", "domain": domain, "event_id": "e", "timestamp": ts,
            "request_id": "r", "data": {"request_ts": ts, "src_ip": "1.1.1.1"}}


def _attack(domain, attack_type, src_ip):
    return {"event": "attack.detected", "domain": domain, "event_id": "e",
            "timestamp": "2026-09-16T09:00:00+00:00", "request_id": "r",
            "data": {"attack_type": attack_type, "src_ip": src_ip, "url": "/x"}}


def test_traffic_buckets_counted_per_minute():
    store = store_mod.AggregateStore()
    store.record(_enriched("shop.local", "2026-09-16T09:00:10+00:00"))
    store.record(_enriched("shop.local", "2026-09-16T09:00:40+00:00"))
    store.record(_enriched("shop.local", "2026-09-16T09:01:05+00:00"))
    traffic = {(t["domain"], t["minute"]): t["count"] for t in store.summary()["traffic"]}
    assert traffic[("shop.local", "2026-09-16T09:00")] == 2
    assert traffic[("shop.local", "2026-09-16T09:01")] == 1


def test_traffic_pruned_to_window():
    store = store_mod.AggregateStore()
    for i in range(store_mod.TRAFFIC_MINUTES + 10):
        store.record(_enriched("shop.local", f"2026-09-16T10:{i:02d}:00+00:00"))
    minutes = [t for t in store.summary()["traffic"] if t["domain"] == "shop.local"]
    assert len(minutes) == store_mod.TRAFFIC_MINUTES


def test_top_attacks_and_ips_ranked():
    store = store_mod.AggregateStore()
    for _ in range(3):
        store.record(_attack("shop.local", "sqli", "1.2.3.4"))
    store.record(_attack("shop.local", "xss", "9.9.9.9"))
    summary = store.summary()
    assert summary["top_attacks"][0] == {"attack_type": "sqli", "count": 3}
    assert summary["top_ips"][0] == {"ip": "1.2.3.4", "count": 3}


def test_alerts_history_newest_first_and_capped():
    store = store_mod.AggregateStore()
    for i in range(store_mod.ALERTS_CAP + 5):
        store.record({"event": "alert.triggered", "domain": "shop.local", "event_id": str(i),
                      "timestamp": f"2026-09-16T11:00:{i % 60:02d}+00:00", "request_id": "r",
                      "data": {"src_ip": "1.2.3.4", "attack_types": ["sqli"], "severity": "high",
                               "reason": f"r{i}"}})
    alerts = store.summary()["alerts"]
    assert len(alerts) == store_mod.ALERTS_CAP
    assert alerts[0]["reason"] == f"r{store_mod.ALERTS_CAP + 4}"  # mới nhất trước


def test_stage_completed_recorded():
    store = store_mod.AggregateStore()
    store.record({"event": "stage.completed", "domain": "shop.local", "event_id": "s1",
                  "timestamp": "2026-09-16T09:00:00+00:00",
                  "data": {"pipeline": "default", "stage": "enrich", "processed": 10,
                           "window_seconds": 60}})
    stages = store.summary()["stages"]
    assert stages[0]["stage"] == "enrich"
    assert stages[0]["processed"] == 10


def test_recent_feed_only_realtime_events():
    store = store_mod.AggregateStore()
    store.record(_attack("shop.local", "sqli", "1.2.3.4"))
    store.record({"event": "stage.completed", "domain": "shop.local", "event_id": "s1",
                  "timestamp": "2026-09-16T09:00:00+00:00", "data": {"stage": "x"}})
    events = [item["event"] for item in store.summary()["recent"]]
    assert "attack.detected" in events
    assert "stage.completed" not in events  # stage không vào feed realtime


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, exc)
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")
