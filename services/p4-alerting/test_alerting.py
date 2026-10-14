"""Unit test P4: parse ALERT_THRESHOLDS + logic cooldown/dedup (không cần SMTP thật).

Chạy: python /app/p4-alerting/test_alerting.py  (PYTHONPATH=/app để import common).
"""
import alerts as alerts_mod
import thresholds as thresholds_mod
from cooldown import CooldownStore


def test_parse_thresholds_basic():
    # Arrange / Act
    parsed = thresholds_mod.parse_thresholds("shop.local:0.8,blog.local:0.9")
    # Assert
    assert parsed == {"shop.local": 0.8, "blog.local": 0.9}


def test_parse_thresholds_empty_returns_empty_dict():
    assert thresholds_mod.parse_thresholds("") == {}
    assert thresholds_mod.parse_thresholds(None) == {}


def test_parse_thresholds_skips_malformed_entries():
    parsed = thresholds_mod.parse_thresholds("shop.local:0.8, nope, bad:xx ,:0.5")
    assert parsed == {"shop.local": 0.8}


def test_resolve_threshold_prefers_domain_then_wildcard_then_fallback():
    parsed = thresholds_mod.parse_thresholds("shop.local:0.8,*:0.6")
    assert thresholds_mod.resolve_threshold(parsed, "shop.local", 0.5) == 0.8
    assert thresholds_mod.resolve_threshold(parsed, "other.local", 0.5) == 0.6
    assert thresholds_mod.resolve_threshold({}, "other.local", 0.5) == 0.5


def test_dedup_key_uses_domain_ip_main_attack():
    env = {"event": "alert.triggered", "domain": "shop.local", "event_id": "e1",
           "timestamp": "2026-09-16T00:00:00+00:00",
           "data": {"src_ip": "1.2.3.4", "attack_types": ["sqli", "xss"]}}
    alert = alerts_mod.build_alert(env)
    assert alerts_mod.dedup_key(alert) == ("shop.local", "1.2.3.4", "sqli")


def test_build_alert_defaults_when_missing_fields():
    env = {"event": "alert.triggered", "domain": "blog.local", "event_id": "e2",
           "timestamp": "2026-09-16T00:00:00+00:00", "data": {}}
    alert = alerts_mod.build_alert(env)
    assert alert["main_attack_type"] == "unknown"
    assert alert["src_ip"] == "unknown"
    assert alert["attack_types"] == []


def test_cooldown_suppresses_within_window_and_allows_after():
    # Arrange: đồng hồ giả điều khiển được
    fake = {"t": 100.0}
    cooldown = CooldownStore(seconds=60, clock=lambda: fake["t"])
    key = ("shop.local", "1.2.3.4", "sqli")
    # Act / Assert
    assert cooldown.allow(key) is True          # lần đầu: cho phép
    assert cooldown.allow(key) is False         # ngay sau đó: bị chặn
    fake["t"] = 159.0
    assert cooldown.allow(key) is False         # vẫn trong cửa sổ 60s
    fake["t"] = 161.0
    assert cooldown.allow(key) is True          # đã qua cooldown: cho phép lại


def test_cooldown_independent_per_key():
    fake = {"t": 0.0}
    cooldown = CooldownStore(seconds=60, clock=lambda: fake["t"])
    assert cooldown.allow(("d", "ip1", "sqli")) is True
    assert cooldown.allow(("d", "ip2", "sqli")) is True   # khoá khác không bị ảnh hưởng


def test_cooldown_disabled_when_zero():
    cooldown = CooldownStore(seconds=0)
    key = ("d", "ip", "sqli")
    assert cooldown.allow(key) is True
    assert cooldown.allow(key) is True


class _FakeStore:
    def __init__(self, alerts):
        self._alerts = alerts

    def snapshot_counters(self):
        return {"alerts": len(self._alerts)}

    def recent_alerts(self, limit):
        return self._alerts[:limit]


class _FakeRepo:
    def recent(self, limit, domain=None):
        return [{"event_id": "a1"}]

    def count(self):
        return 42


def test_alerts_api_reports_persisted_total_from_db():
    from fastapi.testclient import TestClient
    import app as app_mod
    client = TestClient(app_mod.create_app(_FakeStore([]), _FakeRepo()))
    body = client.get("/alerts?limit=1").json()
    assert body["count"] == 42, body       # tổng trong DB, không bị đặt lại khi P4/P5 khởi động lại
    assert body["persisted"] is True, body


def test_alerts_api_falls_back_to_memory_count_without_db():
    from fastapi.testclient import TestClient
    import app as app_mod
    client = TestClient(app_mod.create_app(_FakeStore([{"event_id": "x"}, {"event_id": "y"}]), None))
    body = client.get("/alerts?limit=1").json()
    assert body["count"] == 2, body
    assert body["persisted"] is False, body


# ---------- Ngưỡng cảnh báo riêng theo domain (ALERT_THRESHOLDS) ----------

def _env(event, domain, score, threshold=70, src_ip="10.0.0.1", types=("sql_injection",)):
    return {"event": event, "event_id": f"{event}-{domain}-{score}", "timestamp": "2026-10-14T03:00:00+00:00",
            "domain": domain, "request_id": "r-1",
            "data": {"score": score, "threshold": threshold, "severity": "high", "src_ip": src_ip,
                     "attack_types": list(types), "url": "/x"}}


def _handler(thresholds):
    import consumer as consumer_mod
    import notify as notify_mod
    import store as store_mod
    sent = []
    notify_mod.send_email = lambda cfg, alert: sent.append(alert)   # không gửi SMTP thật
    store = store_mod.AlertStore()
    handler = consumer_mod.build_handler(store, CooldownStore(60), thresholds, None, "", None)
    return handler, store, sent


def test_alert_below_domain_threshold_is_not_sent():
    handler, store, sent = _handler({"shop.local": 90.0})
    handler(_env("alert.triggered", "shop.local", 75), "alert.triggered")
    assert sent == [], sent
    counters = store.snapshot_counters()
    assert counters["alerts_below_threshold"] == 1 and counters["alerts"] == 0, counters


def test_alert_at_or_above_domain_threshold_is_sent():
    handler, store, sent = _handler({"shop.local": 90.0})
    handler(_env("alert.triggered", "shop.local", 92), "alert.triggered")
    assert len(sent) == 1, sent


def test_anomaly_above_lower_domain_threshold_is_sent():
    handler, store, sent = _handler({"blog.local": 50.0})
    handler(_env("anomaly.scored", "blog.local", 60), "anomaly.scored")
    assert len(sent) == 1, sent
    assert "50" in (sent[0]["reason"] or ""), sent[0]
    assert sent[0]["threshold"] == 50.0, sent[0]


def test_anomaly_without_domain_threshold_is_only_counted():
    handler, store, sent = _handler({})
    handler(_env("anomaly.scored", "shop.local", 95), "anomaly.scored")
    assert sent == [], sent
    assert store.snapshot_counters()["anomalies"] == 1


def test_anomaly_and_alert_of_same_request_send_one_email():
    handler, store, sent = _handler({"blog.local": 50.0})
    handler(_env("anomaly.scored", "blog.local", 80), "anomaly.scored")
    handler(_env("alert.triggered", "blog.local", 80), "alert.triggered")
    assert len(sent) == 1, sent
    assert store.snapshot_counters()["alerts_suppressed"] == 1


def test_without_thresholds_alert_is_sent_as_before():
    handler, store, sent = _handler({})
    handler(_env("alert.triggered", "shop.local", 75), "alert.triggered")
    assert len(sent) == 1, sent


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
