"""Chuyển event trên bus thành metric Prometheus.

Tách khỏi main để unit test không cần RabbitMQ. Nhãn (label) được giữ ở mức bounded
(domain, attack_type, severity, rule_id, stage) để tránh nổ cardinality.
"""
from prometheus_client import Counter, Histogram

EVENTS = Counter(
    "waf_events_total", "Tổng số event nhận từ bus", ["event", "domain"])
LOG_INGESTED = Counter(
    "waf_log_ingested_total", "Số dòng log thô thu thập (C1)", ["domain", "source"])
HTTP_REQUESTS = Counter(
    "waf_http_requests_total", "Request đã chuẩn hoá theo lớp mã trạng thái",
    ["domain", "status_class"])
ATTACKS = Counter(
    "waf_attacks_detected_total", "Tấn công phát hiện (D1)",
    ["domain", "attack_type", "severity", "rule_id"])
ANOMALIES = Counter(
    "waf_anomalies_scored_total", "Lần chấm điểm bất thường (D3)",
    ["domain", "severity"])
ANOMALY_SCORE = Histogram(
    "waf_anomaly_score", "Phân bố điểm bất thường", ["domain"],
    buckets=(10, 20, 30, 40, 50, 60, 70, 80, 90, 100))
ALERTS = Counter(
    "waf_alerts_triggered_total", "Cảnh báo kích hoạt (D3/P4)",
    ["domain", "severity", "attack_type"])
STAGES = Counter(
    "waf_stage_completed_total", "stage.completed (P3)", ["domain", "stage"])


def _status_class(status) -> str:
    try:
        return f"{int(status) // 100}xx"
    except (TypeError, ValueError):
        return "unknown"


def update(env: dict) -> None:
    """Cập nhật metric từ một envelope event. An toàn với dữ liệu thiếu."""
    event = env.get("event", "")
    domain = env.get("domain") or "unknown"
    data = env.get("data") or {}

    EVENTS.labels(event=event, domain=domain).inc()

    if event == "log.raw.ingested":
        LOG_INGESTED.labels(domain=domain, source=data.get("log_source") or "").inc()
    elif event == "log.enriched":
        HTTP_REQUESTS.labels(domain=domain, status_class=_status_class(data.get("status"))).inc()
    elif event == "attack.detected":
        ATTACKS.labels(
            domain=domain,
            attack_type=data.get("attack_type") or "unknown",
            severity=data.get("severity") or "unknown",
            rule_id=data.get("rule_id") or "unknown",
        ).inc()
    elif event == "anomaly.scored":
        ANOMALIES.labels(domain=domain, severity=data.get("severity") or "unknown").inc()
        score = data.get("score")
        if isinstance(score, (int, float)):
            ANOMALY_SCORE.labels(domain=domain).observe(score)
    elif event == "alert.triggered":
        attack_types = data.get("attack_types") or ["unknown"]
        ALERTS.labels(
            domain=domain,
            severity=data.get("severity") or "unknown",
            attack_type=attack_types[0] if attack_types else "unknown",
        ).inc()
    elif event == "stage.completed":
        STAGES.labels(domain=domain, stage=data.get("stage") or "unknown").inc()
