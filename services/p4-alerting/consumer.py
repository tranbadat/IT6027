"""Handler + thread nền tiêu thụ q.p4-alerting (anomaly.scored + alert.triggered).

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


def _on_alert(env, store, cooldown, smtp_cfg, webhook_url, repo) -> None:
    alert = alerts_mod.build_alert(env)
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


def _on_anomaly(env, store, thresholds) -> None:
    """anomaly.scored: chỉ ghi nhận, KHÔNG gửi mail (tránh trùng với alert.triggered)."""
    data = env.get("data") or {}
    score = data.get("score")
    event_threshold = data.get("threshold")
    fallback = event_threshold if isinstance(event_threshold, (int, float)) else float("inf")
    threshold = thresholds_mod.resolve_threshold(thresholds, env.get("domain", ""), fallback)
    flagged = isinstance(score, (int, float)) and score >= threshold
    store.incr("anomalies")
    if flagged:
        store.incr("anomalies_flagged")
        log.debug("anomaly flagged", domain=env.get("domain"), score=score, threshold=threshold)


def build_handler(store, cooldown, thresholds, smtp_cfg, webhook_url, repo=None):
    def handle(env: dict, routing_key: str) -> None:
        event = env.get("event")
        if event == "alert.triggered":
            _on_alert(env, store, cooldown, smtp_cfg, webhook_url, repo)
        elif event == "anomaly.scored":
            _on_anomaly(env, store, thresholds)
        else:
            log.debug("ignore event", event=event)

    return handle


def start_consumer_thread(bus_url: str, queue: str, handler) -> threading.Thread:
    """Chạy consumer ở thread nền với kết nối bus RIÊNG (pika không thread-safe)."""
    def _run() -> None:
        bus = bus_mod.EventBus(bus_url, source=SOURCE).connect()
        log.info("p4-alerting consumer started", queue=queue)
        bus.consume(queue, handler)

    thread = threading.Thread(target=_run, daemon=True, name="p4-consumer")
    thread.start()
    return thread
