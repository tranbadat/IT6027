"""Gửi cảnh báo qua email (mailpit, không TLS) và webhook (httpx).

Lỗi gửi được ném ra để caller (consumer) quyết định; caller bọc try/except và
KHÔNG để lỗi làm chết handler.
"""
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

# Timeout tách biệt: email cho mailpit nội bộ, webhook theo spec là 5s.
_EMAIL_TIMEOUT_SECONDS = 10
_WEBHOOK_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    recipient: str


def _format_body(alert: dict) -> str:
    attack = ", ".join(alert.get("attack_types") or []) or "unknown"
    rules = ", ".join(alert.get("rule_ids") or []) or "-"
    lines = [
        f"Domain      : {alert.get('domain')}",
        f"Attack types: {attack}",
        f"URL         : {alert.get('url')}",
        f"Source IP   : {alert.get('src_ip')}",
        f"Severity    : {alert.get('severity')}",
        f"Score/Thresh: {alert.get('score')} / {alert.get('threshold')}",
        f"Rule IDs    : {rules}",
        f"Reason      : {alert.get('reason')}",
        f"Time        : {alert.get('triggered_at')}",
    ]
    return "\n".join(lines) + "\n"


def build_email(cfg: SmtpConfig, alert: dict) -> EmailMessage:
    """Dựng EmailMessage utf-8. Subject: [<SEVERITY>] <attack_types> on <domain>."""
    attack = ", ".join(alert.get("attack_types") or []) or "unknown"
    severity = str(alert.get("severity") or "unknown").upper()
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = cfg.recipient
    msg["Subject"] = f"[{severity}] {attack} on {alert.get('domain')}"
    msg.set_content(_format_body(alert), charset="utf-8")
    return msg


def send_email(cfg: SmtpConfig, alert: dict) -> None:
    msg = build_email(cfg, alert)
    with smtplib.SMTP(cfg.host, cfg.port, timeout=_EMAIL_TIMEOUT_SECONDS) as smtp:
        smtp.send_message(msg)


def send_webhook(url: str, payload: dict) -> None:
    httpx.post(url, json=payload, timeout=_WEBHOOK_TIMEOUT_SECONDS)
