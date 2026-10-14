"""Handler + thread nền tiêu thụ topic anomaly.scored + alert.triggered (consumer group p4-alerting).

Nguyên tắc phản ứng: CHỈ cảnh báo (email/webhook), KHÔNG tự chặn IP hay đổi cấu hình.
Nếu sau này thêm phản ứng tự động thì phải qua phê duyệt thủ công và mặc định dry-run.
"""
import threading

from common import bus as bus_mod
from common import logging as log

import alerts as alerts_mod
import notify as notify_mod
import thresholds as thresholds_mod

SOURCE = "p4-alerting"
TOPICS = ["anomaly.scored", "alert.triggered"]


def _deliver(alert: dict, smtp_cfg, webhook_url, store) -> None:
    """Gửi email rồi webhook. Mỗi kênh lỗi độc lập, không làm chết handler."""
    try:
        notify_mod.send_email(smtp_cfg, alert)
        store.incr("emails_sent")
    except Exception as exc:  # SMTP có thể tạm lỗi -> log, không dead-letter cảnh báo
        store.incr("emails_failed")
        log.error("email send failed", error=str(exc), domain=alert.get("domain"))

    if not webhook_url:
        return
    try:
        notify_mod.send_webhook(webhook_url, alert)
        store.incr("webhooks_sent")
    except Exception as exc:  # lỗi webhook KHÔNG được làm chết handler
        store.incr("webhooks_failed")
        log.warning("webhook post failed", error=str(exc), url=webhook_url)


def _persist(alert, repo, store) -> None:
    """Lưu lịch sử cảnh báo vào DB (best-effort, không làm chết handler)."""
    if repo is None:
        return
    try:
        repo.insert(alert)
    except Exception as exc:
        store.incr("db_errors")
        log.warning("persist alert failed", error=str(exc), event_id=alert.get("event_id"))


def _configured_threshold(thresholds, domain):
    """Ngưỡng cấu hình cho domain (kể cả khoá mặc định "*"); None nếu không cấu hình."""
    return thresholds_mod.resolve_threshold(thresholds, domain or "", None)


def _notify(alert, store, cooldown, smtp_cfg, webhook_url, repo) -> None:
    """Chống gửi trùng theo (domain, src_ip, loại tấn công chính), lưu lịch sử rồi gửi email/webhook."""
    key = alerts_mod.dedup_key(alert)
    if not cooldown.allow(key):
        store.incr("alerts_suppressed")
        log.info("alert suppressed by cooldown", domain=alert["domain"],
                 src_ip=alert["src_ip"], attack_type=alert["main_attack_type"])
        return
    store.add_alert(alert)
    _persist(alert, repo, store)
    log.info("alert triggered", domain=alert["domain"], severity=alert["severity"],
             attack_type=alert["main_attack_type"], src_ip=alert["src_ip"])
    _deliver(alert, smtp_cfg, webhook_url, store)


def _on_alert(env, store, cooldown, thresholds, smtp_cfg, webhook_url, repo) -> None:
    """alert.triggered: gửi nếu điểm đạt ngưỡng riêng của domain (khi có cấu hình), ngược lại gửi như cũ."""
    data = env.get("data") or {}
    score = data.get("score")
    configured = _configured_threshold(thresholds, env.get("domain"))
    if configured is not None and isinstance(score, (int, float)) and score < configured:
        store.incr("alerts_below_threshold")
        log.info("alert below domain threshold", domain=env.get("domain"), score=score,
                 threshold=configured)
        return
    alert = alerts_mod.build_alert(env)
    if configured is not None:
        alert = {**alert, "threshold": configured}
    _notify(alert, store, cooldown, smtp_cfg, webhook_url, repo)


def _on_anomaly(env, store, cooldown, thresholds, smtp_cfg, webhook_url, repo) -> None:
    """anomaly.scored: chỉ đếm, trừ khi domain có ngưỡng riêng và điểm đạt ngưỡng đó.

    Cho phép domain nhạy cảm cảnh báo sớm hơn ngưỡng chung của detector. Cùng request còn phát
    alert.triggered thì cooldown ở _notify chặn email thứ hai.
    """
    data = env.get("data") or {}
    score = data.get("score")
    store.incr("anomalies")
    configured = _configured_threshold(thresholds, env.get("domain"))
    if configured is None or not isinstance(score, (int, float)) or score < configured:
        return
    store.incr("anomalies_flagged")
    reasons = data.get("reasons") or []
    alert = {**alerts_mod.build_alert(env), "threshold": configured,
             "reason": f"score {score} >= ngưỡng domain {configured}; signals={list(reasons)}"}
    _notify(alert, store, cooldown, smtp_cfg, webhook_url, repo)


def build_handler(store, cooldown, thresholds, smtp_cfg, webhook_url, repo=None):
    def handle(env: dict, routing_key: str) -> None:
        event = env.get("event")
        if event == "alert.triggered":
            _on_alert(env, store, cooldown, thresholds, smtp_cfg, webhook_url, repo)
        elif event == "anomaly.scored":
            _on_anomaly(env, store, cooldown, thresholds, smtp_cfg, webhook_url, repo)
        else:
            log.debug("ignore event", event=event)

    return handle


def start_consumer_thread(bus_url: str, group: str, handler) -> threading.Thread:
    """Chạy consumer ở thread nền với client Kafka RIÊNG; consumer lỗi thì thoát process để Docker khởi động lại."""
    log.info("p4-alerting consumer starting", group=group, topics=TOPICS)
    return bus_mod.start_consumer_thread(lambda: bus_mod.EventBus(bus_url, source=SOURCE), group,
                                         handler, TOPICS, name="p4-consumer")
