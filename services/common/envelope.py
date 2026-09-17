"""Tạo và kiểm tra vỏ event dùng chung (contracts/events/envelope.schema.json)."""
import datetime
import uuid

EVENTS = (
    "log.raw.ingested",
    "log.normalized",
    "log.enriched",
    "attack.detected",
    "anomaly.scored",
    "alert.triggered",
    "stage.completed",
)

# Event không bắt buộc có request_id/session_id.
_NO_ID_REQUIRED = {"stage.completed"}


def now_rfc3339() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def make(
    event: str,
    domain: str,
    data: dict,
    source: str,
    request_id: str | None = None,
    session_id: str | None = None,
    schema_version: int = 1,
) -> dict:
    """Tạo một envelope hợp lệ. Immutable: luôn trả về dict mới."""
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}")
    envelope: dict = {
        "event": event,
        "event_id": str(uuid.uuid4()),
        "schema_version": schema_version,
        "timestamp": now_rfc3339(),
        "source": source,
        "domain": domain,
        "data": data,
    }
    if request_id is not None:
        envelope["request_id"] = request_id
    if session_id is not None:
        envelope["session_id"] = session_id
    return envelope


def validate(envelope: dict) -> None:
    """Kiểm tra tối thiểu. Ném ValueError nếu sai (consumer sẽ dead-letter)."""
    if not isinstance(envelope, dict):
        raise ValueError("envelope is not an object")
    for field in ("event", "event_id", "timestamp", "source", "domain", "data"):
        if field not in envelope:
            raise ValueError(f"missing field {field!r}")
    if envelope["event"] not in EVENTS:
        raise ValueError(f"unknown event {envelope['event']!r}")
    if not isinstance(envelope["data"], dict):
        raise ValueError("data is not an object")
    if envelope["event"] not in _NO_ID_REQUIRED:
        if "request_id" not in envelope and "session_id" not in envelope:
            raise ValueError("missing request_id or session_id")
