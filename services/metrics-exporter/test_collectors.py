"""Unit test cho collectors: nạp envelope mẫu, kiểm tra counter tăng đúng."""
from prometheus_client import REGISTRY

import collectors


def _val(name, labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_attack_and_alert_counters():
    before = _val("waf_attacks_detected_total",
                  {"domain": "shop.local", "attack_type": "sql_injection",
                   "severity": "high", "rule_id": "sqli-boolean-or"})
    collectors.update({
        "event": "attack.detected", "domain": "shop.local",
        "data": {"attack_type": "sql_injection", "severity": "high",
                 "rule_id": "sqli-boolean-or", "url": "/x"},
    })
    after = _val("waf_attacks_detected_total",
                 {"domain": "shop.local", "attack_type": "sql_injection",
                  "severity": "high", "rule_id": "sqli-boolean-or"})
    assert after == before + 1

    collectors.update({
        "event": "alert.triggered", "domain": "shop.local",
        "data": {"severity": "high", "attack_types": ["sql_injection"]},
    })
    assert _val("waf_alerts_triggered_total",
                {"domain": "shop.local", "severity": "high",
                 "attack_type": "sql_injection"}) >= 1


def test_http_status_class_and_events():
    collectors.update({
        "event": "log.enriched", "domain": "shop.local",
        "data": {"status": 404},
    })
    assert _val("waf_http_requests_total",
                {"domain": "shop.local", "status_class": "4xx"}) >= 1
    assert _val("waf_events_total",
                {"event": "log.enriched", "domain": "shop.local"}) >= 1


def test_missing_fields_safe():
    collectors.update({"event": "attack.detected", "domain": "", "data": {}})
    assert _val("waf_attacks_detected_total",
                {"domain": "unknown", "attack_type": "unknown",
                 "severity": "unknown", "rule_id": "unknown"}) >= 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
