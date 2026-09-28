"""Đọc cấu hình từ biến môi trường. Fail fast nếu thiếu biến bắt buộc."""
import os


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"missing required env var {name}")
    return value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"env var {name} must be an integer, got {raw!r}")


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"env var {name} must be a number, got {raw!r}")


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]
