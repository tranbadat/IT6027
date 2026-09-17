"""Unit test cho engine khớp mẫu D1. Chạy được KHÔNG cần RabbitMQ.

Nạp CHÍNH các file trong config/rules (mount vào /data/rules) và kiểm tra:
- từng loại matcher: SQL Injection, XSS, path traversal (kể cả dạng %-encode);
- traffic bình thường KHÔNG tạo false positive.
Chạy: python /app/d1-rule-engine/test_matcher.py  (đặt RULES_DIR=/data/rules).
"""
import os

import matcher as matcher_mod

RULES_DIR = os.environ.get("RULES_DIR", "/data/rules")


def _rules():
    rules = matcher_mod.load_rules(RULES_DIR)
    assert rules, f"no rules loaded from {RULES_DIR}"
    return rules


def _base(**overrides):
    """log.enriched.data mẫu; override field cần cho từng ca."""
    data = {
        "src_ip": "203.0.113.9", "method": "GET", "host": "shop.local",
        "path": "/api/products", "query_string": "", "protocol": "HTTP/1.1",
        "status": 200, "user_agent": "Mozilla/5.0", "referer": "",
        "path_decoded": "/api/products", "query_decoded": "",
    }
    data.update(overrides)
    return data


def _types(detections):
    return {d["attack_type"] for d in detections}


def test_rules_load_and_have_each_type():
    rules = _rules()
    kinds = {r.attack_type for r in rules}
    assert "sql_injection" in kinds
    assert "xss" in kinds
    assert "path_traversal" in kinds


def test_sqli_boolean_or():
    data = _base(query_string="id=1%27%20OR%20%271%27%3D%271",
                 query_decoded="id=1' OR '1'='1")
    dets = matcher_mod.match_event(_rules(), data)
    assert "sql_injection" in _types(dets)


def test_sqli_union_select():
    data = _base(query_string="q=1+UNION+SELECT+name+FROM+users",
                 query_decoded="q=1 UNION SELECT name FROM users")
    dets = matcher_mod.match_event(_rules(), data)
    assert "sql_injection" in _types(dets)


def test_xss_script_tag():
    payload = "<script>alert(1)</script>"
    data = _base(query_string="c=" + payload, query_decoded="c=" + payload)
    dets = matcher_mod.match_event(_rules(), data)
    assert "xss" in _types(dets)


def test_xss_img_onerror():
    payload = "<img src=x onerror=alert(1)>"
    data = _base(query_string="c=" + payload, query_decoded="c=" + payload)
    dets = matcher_mod.match_event(_rules(), data)
    assert "xss" in _types(dets)


def test_path_traversal_literal():
    data = _base(path="/download",
                 path_decoded="/download/../../../../etc/passwd")
    dets = matcher_mod.match_event(_rules(), data)
    assert "path_traversal" in _types(dets)


def test_path_traversal_encoded_only():
    # Chỉ có dạng %-encode trong query_string thô (chưa giải mã).
    data = _base(query_string="file=..%2f..%2f..%2fetc%2fpasswd", query_decoded="")
    dets = matcher_mod.match_event(_rules(), data)
    assert "path_traversal" in _types(dets)


def test_normal_traffic_no_false_positive():
    data = _base(query_string="id=42&sort=name&order=asc",
                 query_decoded="id=42&sort=name&order=asc",
                 user_agent="Mozilla/5.0 (X11; Linux x86_64) curl/8.7.1",
                 referer="https://shop.local/catalog")
    dets = matcher_mod.match_event(_rules(), data)
    assert dets == [], f"unexpected detections: {dets}"


def test_detection_shape_and_matched_value_cap():
    data = _base(query_string="c=<script>x</script>",
                 query_decoded="c=<script>x</script>")
    dets = matcher_mod.match_event(_rules(), data)
    assert dets, "expected at least one detection"
    det = dets[0]
    for key in ("url", "method", "src_ip", "host", "rule_id", "rule_name",
                "attack_type", "severity", "field", "matched_value", "evidence"):
        assert key in det, f"missing key {key}"
    assert len(det["matched_value"]) <= matcher_mod.MAX_MATCHED_LEN


def test_multiple_rules_can_match_one_event():
    data = _base(query_string="q=1 UNION SELECT 1&c=<script>x</script>",
                 query_decoded="q=1 UNION SELECT 1&c=<script>x</script>")
    dets = matcher_mod.match_event(_rules(), data)
    assert {"sql_injection", "xss"} <= _types(dets)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # báo lỗi rõ ràng, tiếp tục các test khác
                failures += 1
                print("FAIL", name, "->", repr(exc))
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")
