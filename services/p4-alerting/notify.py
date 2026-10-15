"""Gửi cảnh báo qua email (SMTP) và webhook (httpx).

Mặc định gửi tới Mailpit (không TLS, không đăng nhập); gửi ra ngoài qua SMTP relay bằng SMTP_TLS
(STARTTLS/SSL) và SMTP_USERNAME/SMTP_PASSWORD.
Lỗi gửi được ném ra để caller (consumer) quyết định; caller bọc try/except và
KHÔNG để lỗi làm chết handler.
"""
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

# Timeout tách biệt: email theo máy chủ SMTP, webhook theo spec là 5s.
_EMAIL_TIMEOUT_SECONDS = 10
_WEBHOOK_TIMEOUT_SECONDS = 5
# none: SMTP thường (Mailpit); starttls: nâng cấp TLS sau khi kết nối (cổng 25/587);
# ssl: TLS ngay từ đầu (cổng 465).
TLS_MODES = ("none", "starttls", "ssl")


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    recipient: str
    tls: str = "none"
    username: str = ""
    password: str = ""
    # False = TLS cơ hội: vẫn mã hoá nhưng không xác minh chứng chỉ (cách MTA gửi tới MX). Chỉ dùng
    # khi máy chủ nhận có chứng chỉ không hợp lệ (hết hạn, tự ký).
    verify_tls: bool = True

    def __post_init__(self):
        if self.tls not in TLS_MODES:
            raise ValueError(f"SMTP_TLS must be one of {', '.join(TLS_MODES)}, got {self.tls!r}")


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


def _tls_context(cfg: SmtpConfig) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not cfg.verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    if cfg.tls == "ssl":
        return smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=_EMAIL_TIMEOUT_SECONDS, context=_tls_context(cfg))
    return smtplib.SMTP(cfg.host, cfg.port, timeout=_EMAIL_TIMEOUT_SECONDS)


def send_email(cfg: SmtpConfig, alert: dict) -> None:
    """Gửi một email cảnh báo. Chứng chỉ TLS được xác minh trừ khi cấu hình tắt rõ ràng (verify_tls)."""
    msg = build_email(cfg, alert)
    with _connect(cfg) as smtp:
        if cfg.tls == "starttls":
            smtp.starttls(context=_tls_context(cfg))
        if cfg.username:
            smtp.login(cfg.username, cfg.password)
        smtp.send_message(msg)


def send_webhook(url: str, payload: dict) -> None:
    httpx.post(url, json=payload, timeout=_WEBHOOK_TIMEOUT_SECONDS)
