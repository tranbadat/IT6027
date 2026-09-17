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


def _handle_attack(bus, scorer, threshold, env):
    domain = env["domain"]
    data = env["data"]
    src_ip = data.get("src_ip") or ""
    url = data.get("url") or ""
    host = data.get("host") or ""
    now = time.time()

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


def build_handler(bus, scope, scorer, threshold):
    def handle(env, routing_key):
        domain = env["domain"]
        if not scope.allowed(domain):
            log.info("skip out-of-scope event", domain=domain, event=env["event"])
            return
        event = env["event"]
        if event == "attack.detected":
            _handle_attack(bus, scorer, threshold, env)
        elif event == "log.enriched":
            data = env["data"]
            scorer.record_request(domain, data.get("src_ip") or "",
                                  data.get("status"), time.time())
        else:
            log.debug("ignore event", event=event)

    return handle


def main():
    log.set_service(SOURCE)
    health.start_heartbeat()
    queue = config.env("EVENT_QUEUE", "q.d3-anomaly-scorer")
    threshold = config.env_float("ANOMALY_THRESHOLD", 70)

    scorer = scorer_mod.Scorer()
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    log.info("d3-anomaly-scorer started", queue=queue, threshold=threshold)
    bus.consume(queue, build_handler(bus, scope, scorer, threshold))


if __name__ == "__main__":
    main()
