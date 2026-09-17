"""Unit test cho scorer D3. Chạy được KHÔNG cần RabbitMQ.

Chạy: python /app/d3-anomaly-scorer/test_scorer.py
"""
import scorer as scorer_mod

DOMAIN = "shop.local"
IP = "203.0.113.9"


def test_severity_from_score_thresholds():
    assert scorer_mod.severity_from_score(95) == "critical"
    assert scorer_mod.severity_from_score(70) == "high"
    assert scorer_mod.severity_from_score(40) == "medium"
    assert scorer_mod.severity_from_score(10) == "low"


def test_single_attack_accumulates_weight():
    s = scorer_mod.Scorer()
    s.record_attack(DOMAIN, IP, "high", "sql_injection", "sqli-1", now=1000.0)
    result = s.evaluate(DOMAIN, IP, now=1000.0)
    assert result.score == 30  # high = 30, chưa có request/lỗi
    assert result.signals["rule_hits"] == 1
    assert "sql_injection" in result.attack_types


def test_repeated_attacks_escalate_and_cap():
    s = scorer_mod.Scorer()
    for i in range(5):
        s.record_attack(DOMAIN, IP, "critical", "xss", f"xss-{i}", now=1000.0)
    result = s.evaluate(DOMAIN, IP, now=1000.0)
    assert result.score == 100  # min(100, 5*50)
    assert result.severity == "critical"


def test_requests_and_errors_contribute():
    s = scorer_mod.Scorer()
    for i in range(10):
        status = 500 if i % 2 == 0 else 200
        s.record_request(DOMAIN, IP, status, now=1000.0 + i)
    result = s.evaluate(DOMAIN, IP, now=1010.0)
    assert result.signals["req_count"] == 10
    assert result.signals["error_count"] == 5
    assert result.signals["req_rate_1m"] == 10
    assert result.score > 0


def test_sliding_window_prunes_old_events():
    s = scorer_mod.Scorer(window=300)
    s.record_attack(DOMAIN, IP, "critical", "xss", "old", now=1000.0)
    # 400s sau -> ngoài cửa sổ 300s
    result = s.evaluate(DOMAIN, IP, now=1400.0)
    assert result.signals["rule_hits"] == 0
    assert result.score == 0


def test_cooldown_blocks_repeat_alert():
    s = scorer_mod.Scorer(cooldown=60)
    assert s.should_alert(IP, now=1000.0) is True
    s.mark_alert(IP, now=1000.0)
    assert s.should_alert(IP, now=1030.0) is False  # còn trong cooldown
    assert s.should_alert(IP, now=1070.0) is True   # đã qua cooldown


def test_memory_cap_evicts_oldest_key():
    s = scorer_mod.Scorer(max_keys=3)
    for i in range(5):
        s.record_request(DOMAIN, f"10.0.0.{i}", 200, now=1000.0 + i)
    # Chỉ giữ tối đa 3 key gần nhất.
    assert len(s._stats) == 3


def test_signals_shape():
    s = scorer_mod.Scorer()
    s.record_attack(DOMAIN, IP, "medium", "sql_injection", "r1", now=1000.0)
    result = s.evaluate(DOMAIN, IP, now=1000.0)
    for key in ("rule_hits", "req_count", "error_count", "error_rate", "req_rate_1m"):
        assert key in result.signals


def test_behavioral_error_scan_triggers_without_rule():
    # Quét gây nhiều lỗi, KHÔNG có rule nào khớp -> vẫn phát hiện độc lập.
    s = scorer_mod.Scorer(error_burst=5, error_rate_min=0.5, flood_rate=1000)
    for i in range(6):
        s.record_request(DOMAIN, IP, 404, now=1000.0 + i)
    result = s.evaluate_behavioral(DOMAIN, IP, now=1006.0, threshold=70)
    assert result is not None
    assert result.attack_types[0] == "error_scan"
    assert result.score >= 70
    assert result.signals["rule_hits"] == 0  # không cần rule


def test_behavioral_flood_triggers():
    s = scorer_mod.Scorer(flood_rate=10, error_burst=1000)
    for i in range(12):
        s.record_request(DOMAIN, IP, 200, now=1000.0 + i * 0.1)
    result = s.evaluate_behavioral(DOMAIN, IP, now=1002.0, threshold=70)
    assert result is not None
    assert result.attack_types[0] == "traffic_flood"
    assert result.score >= 70


def test_static_asset_404_not_counted_as_error():
    s = scorer_mod.Scorer(error_burst=5, error_rate_min=0.5, flood_rate=1000)
    for i in range(8):
        s.record_request(DOMAIN, IP, 404, now=1000.0 + i, path="/favicon.ico")
    # 404 của favicon KHÔNG tính là lỗi quét -> không kích hoạt error_scan.
    assert s.evaluate_behavioral(DOMAIN, IP, now=1008.0, threshold=70) is None
    r = s.evaluate(DOMAIN, IP, now=1008.0)
    assert r.signals["error_count"] == 0


def test_behavioral_none_on_normal_traffic():
    s = scorer_mod.Scorer(flood_rate=100, error_burst=20)
    for i in range(5):
        s.record_request(DOMAIN, IP, 200, now=1000.0 + i)
    assert s.evaluate_behavioral(DOMAIN, IP, now=1005.0, threshold=70) is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failures += 1
                print("FAIL", name, "->", repr(exc))
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")
