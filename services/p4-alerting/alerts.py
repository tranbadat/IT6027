"""Dựng bản tóm tắt cảnh báo (immutable) từ envelope alert.triggered.

Tách riêng khỏi consumer/notify để test được logic thuần mà không cần SMTP/bus.
"""
from common import envelope as envelope_mod

_UNKNOWN = "unknown"


def main_attack_type(attack_types) -> str:
    """Loại tấn công chính = phần tử đầu của attack_types, mặc định 'unknown'."""
    if isinstance(attack_types, (list, tuple)) and attack_types:
        return str(attack_types[0])
    return _UNKNOWN


def build_alert(env: dict) -> dict:
    """Trả dict tóm tắt cảnh báo mới (không sửa envelope gốc)."""
    data = env.get("data") or {}
    attack_types = list(data.get("attack_types") or [])
    return {
        "event_id": env.get("event_id"),
        "domain": env.get("domain"),
        "src_ip": data.get("src_ip") or _UNKNOWN,
        "host": data.get("host"),
        "url": data.get("url"),
        "attack_types": attack_types,
        "main_attack_type": main_attack_type(attack_types),
        "rule_ids": list(data.get("rule_ids") or []),
        "severity": data.get("severity") or _UNKNOWN,
        "score": data.get("score"),
        "threshold": data.get("threshold"),
        "reason": data.get("reason"),
        "triggered_at": env.get("timestamp") or envelope_mod.now_rfc3339(),
        "request_id": env.get("request_id"),
        "session_id": env.get("session_id"),
    }


def dedup_key(alert: dict) -> tuple:
    """Khoá chống trùng: (domain, src_ip, loại tấn công chính)."""
    return (alert.get("domain"), alert.get("src_ip"), alert.get("main_attack_type"))
