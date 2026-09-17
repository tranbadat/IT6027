"""P4 — Alerting.

Consume q.p4-alerting (anomaly.scored + alert.triggered) ở thread nền, phục vụ HTTP
(/healthz, /alerts) ở thread chính. Trên alert.triggered: gửi email + webhook, có
dedup/cooldown. Trên anomaly.scored: chỉ ghi nhận. KHÔNG tự chặn IP / đổi cấu hình.
"""
import uvicorn

from common import config
from common import logging as log

import app as app_mod
import consumer as consumer_mod
import notify as notify_mod
import store as store_mod
import thresholds as thresholds_mod
from cooldown import CooldownStore

SOURCE = "p4-alerting"
_DEFAULT_COOLDOWN_SECONDS = 60


def _load_smtp_config() -> notify_mod.SmtpConfig:
    return notify_mod.SmtpConfig(
        host=config.env("SMTP_HOST", "mailpit"),
        port=config.env_int("SMTP_PORT", 1025),
        sender=config.env("ALERT_EMAIL_FROM", required=True),
        recipient=config.env("ALERT_EMAIL_TO", required=True),
    )


def main() -> None:
    log.set_service(SOURCE)

    queue = config.env("EVENT_QUEUE", "q.p4-alerting")
    bus_url = config.env("EVENT_BUS_URL", required=True)
    webhook_url = config.env("ALERT_WEBHOOK_URL", "")
    cooldown_seconds = config.env_int("ALERT_COOLDOWN_SECONDS", _DEFAULT_COOLDOWN_SECONDS)
    thresholds = thresholds_mod.parse_thresholds(config.env("ALERT_THRESHOLDS", ""))

    store = store_mod.AlertStore()
    cooldown = CooldownStore(cooldown_seconds)
    smtp_cfg = _load_smtp_config()

    handler = consumer_mod.build_handler(store, cooldown, thresholds, smtp_cfg, webhook_url)
    consumer_mod.start_consumer_thread(bus_url, queue, handler)

    log.info("p4-alerting http starting", queue=queue, cooldown_seconds=cooldown_seconds,
             thresholds=len(thresholds), webhook=bool(webhook_url))
    app = app_mod.create_app(store)
    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000), access_log=False)


if __name__ == "__main__":
    main()
