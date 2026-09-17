"""D3 — Anomaly Scoring.

consume log.enriched + attack.detected (q.d3-anomaly-scorer):
- log.enriched -> cập nhật thống kê tần suất/lỗi;
- attack.detected -> cập nhật rule_hits, tính score, publish anomaly.scored,
  và publish alert.triggered nếu score >= threshold và qua cooldown.
KHÔNG chặn IP hay đổi cấu hình — chỉ chấm điểm và cảnh báo.
"""
import time

from common import bus as bus_mod
from common import config
from common import health
from common import logging as log
from common import scope as scope_mod

import scorer as scorer_mod

SOURCE = "d3-anomaly-scorer"


def _scored_data(src_ip, url, host, threshold, result):
    """Dựng anomaly.scored.data theo schema."""
    return {
        "score": result.score,
        "threshold": threshold,
        "severity": result.severity,
        "src_ip": src_ip,
        "url": url,
        "host": host,
        "attack_types": list(result.attack_types),
        "signals": result.signals,
    }


def _alert_data(src_ip, url, host, threshold, result):
    """Dựng alert.triggered.data theo schema."""
    reason = (f"score {result.score} >= threshold {threshold}; "
              f"rule_hits={result.signals['rule_hits']}, "
              f"attack_types={list(result.attack_types)}")
    return {
        "score": result.score,
        "threshold": threshold,
        "severity": result.severity,
        "src_ip": src_ip,
        "url": url,
        "host": host,
        "attack_types": list(result.attack_types),
        "rule_ids": list(result.rule_ids),
        "reason": reason,
    }


def _url_from_enriched(data):
    """Dựng url từ log.enriched (path + query) để đưa vào cảnh báo hành vi."""
    path = data.get("path") or ""
    query = data.get("query_string") or ""
    return path + (("?" + query) if query else "")


def _handle_attack(bus, scorer, threshold, env, now):
    domain = env["domain"]
    data = env["data"]
    src_ip = data.get("src_ip") or ""
    url = data.get("url") or ""
    host = data.get("host") or ""

    scorer.record_attack(domain, src_ip, data.get("severity"),
                         data.get("attack_type"), data.get("rule_id"), now)
    result = scorer.evaluate(domain, src_ip, now)
    bus.publish("anomaly.scored", domain=domain,
                data=_scored_data(src_ip, url, host, threshold, result),
                request_id=env.get("request_id"), session_id=env.get("session_id"))

    if result.score >= threshold and scorer.should_alert(src_ip, now):
        scorer.mark_alert(src_ip, now)
        bus.publish("alert.triggered", domain=domain,
                    data=_alert_data(src_ip, url, host, threshold, result),
                    request_id=env.get("request_id"),
                    session_id=env.get("session_id"))
        log.info("alert triggered", domain=domain, src_ip=src_ip, score=result.score)


def _behavioral_summary(result):
    """Mô tả hành vi để hiển thị thay cho URL tình cờ (cảnh báo hành vi là mức IP, không
    gắn với một request cụ thể)."""
    s = result.signals
    kind = result.attack_types[0] if result.attack_types else "behavioral"
    if kind == "traffic_flood":
        return f"[flood] {s.get('req_rate_1m')} req/phút · {s.get('req_count')} req/5m"
    return f"[error_scan] {s.get('error_count')} lỗi · tỉ lệ {s.get('error_rate')}"


def _handle_traffic(bus, scorer, threshold, env, now):
    """log.enriched: cập nhật thống kê; nếu HÀNH VI (flood/quét lỗi) vượt ngưỡng thì
    tự phát anomaly.scored + alert.triggered dù KHÔNG có rule nào khớp."""
    domain = env["domain"]
    data = env["data"]
    src_ip = data.get("src_ip") or ""
    scorer.record_request(domain, src_ip, data.get("status"), now, data.get("path") or "")

    result = scorer.evaluate_behavioral(domain, src_ip, now, threshold)
    if result is None or not scorer.should_alert(src_ip, now):
        return
    # Cảnh báo hành vi là mức IP: dùng mô tả hành vi thay vì URL request tình cờ (vd /favicon.ico).
    url = _behavioral_summary(result)
    host = data.get("host") or ""
    scorer.mark_alert(src_ip, now)
    bus.publish("anomaly.scored", domain=domain,
                data=_scored_data(src_ip, url, host, threshold, result),
                request_id=env.get("request_id"), session_id=env.get("session_id"))
    bus.publish("alert.triggered", domain=domain,
                data=_alert_data(src_ip, url, host, threshold, result),
                request_id=env.get("request_id"), session_id=env.get("session_id"))
    log.info("behavioral alert", domain=domain, src_ip=src_ip, score=result.score,
             kind=result.attack_types[0] if result.attack_types else "")


def build_handler(bus, scope, scorer, threshold):
    def handle(env, routing_key):
        domain = env["domain"]
        if not scope.allowed(domain):
            log.info("skip out-of-scope event", domain=domain, event=env["event"])
            return
        event = env["event"]
        now = time.time()
        if event == "attack.detected":
            _handle_attack(bus, scorer, threshold, env, now)
        elif event == "log.enriched":
            _handle_traffic(bus, scorer, threshold, env, now)
        else:
            log.debug("ignore event", event=event)

    return handle


def main():
    log.set_service(SOURCE)
    health.start_heartbeat()
    queue = config.env("EVENT_QUEUE", "q.d3-anomaly-scorer")
    threshold = config.env_float("ANOMALY_THRESHOLD", 70)

    scorer = scorer_mod.Scorer(
        flood_rate=config.env_int("D3_FLOOD_RATE_1M", scorer_mod.FLOOD_RATE_1M),
        error_burst=config.env_int("D3_ERROR_BURST", scorer_mod.ERROR_BURST),
        error_rate_min=config.env_float("D3_ERROR_RATE_MIN", scorer_mod.ERROR_RATE_MIN),
    )
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    log.info("d3-anomaly-scorer started", queue=queue, threshold=threshold)
    bus.consume(queue, build_handler(bus, scope, scorer, threshold))


if __name__ == "__main__":
    main()
