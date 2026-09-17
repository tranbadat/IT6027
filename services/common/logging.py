"""Log JSON một dòng ra stdout. Mức log lấy từ LOG_LEVEL (mặc định info)."""
import datetime
import json
import os
import sys

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_threshold = _LEVELS.get((os.environ.get("LOG_LEVEL") or "info").lower(), 20)
_service = os.environ.get("SERVICE_NAME", "")


def set_service(name: str) -> None:
    """Đặt tên service hiển thị trong log (gọi sớm trong main)."""
    global _service
    _service = name


def _emit(level: str, msg: str, **fields) -> None:
    if _LEVELS[level] < _threshold:
        return
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": level,
        "service": _service,
        "msg": msg,
    }
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)


def debug(msg: str, **fields) -> None:
    _emit("debug", msg, **fields)


def info(msg: str, **fields) -> None:
    _emit("info", msg, **fields)


def warning(msg: str, **fields) -> None:
    _emit("warning", msg, **fields)


def error(msg: str, **fields) -> None:
    _emit("error", msg, **fields)
