"""Event envelope dùng chung toàn hệ thống: { event, request_id | session_id, timestamp, data }."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# Tên event do Người 1 phát ra
LOG_RAW_INGESTED = "log.raw.ingested"
LOG_NORMALIZED = "log.normalized"
LOG_ENRICHED = "log.enriched"
# Event phụ: dòng log không parse được (dead-letter, không bắt buộc ai consume)
LOG_PARSE_FAILED = "log.parse_failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_request_id() -> str:
    return uuid.uuid4().hex


def make_event(
    event: str,
    data: dict[str, Any],
    request_id: str,
    session_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "event": event,
        "request_id": request_id,
        "timestamp": timestamp or utc_now_iso(),
        "data": data,
    }
    if session_id is not None:
        env["session_id"] = session_id
    return env


def dumps(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def loads(payload: str) -> dict[str, Any]:
    return json.loads(payload)
