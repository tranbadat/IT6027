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
