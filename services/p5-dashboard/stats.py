"""Hàm thuần để tổng hợp số liệu dashboard từ kết quả truy vấn DB (không phụ thuộc Postgres).

Khung thời gian: cửa sổ hiện tại [start, end) và cửa sổ liền trước [prev_start, start) cùng độ dài
(để tính % thay đổi). Xu hướng nhỏ (sparkline) chia cửa sổ thành TREND_BUCKETS khoảng; biểu đồ theo
thời gian chia thành khoảng TIMELINE_TARGET_BUCKETS khoảng.
"""
import math
from datetime import date, datetime, timedelta, timezone

TREND_BUCKETS = 24
TIMELINE_TARGET_BUCKETS = 48
HEATMAP_DAYS = 7
MAX_WINDOW_HOURS = 24 * 30
SEVERITIES = ("critical", "high", "medium", "low")


def window(hours: int, now: datetime | None = None) -> dict:
    hours = min(max(int(hours), 1), MAX_WINDOW_HOURS)
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    timeline_minutes = max(1, math.ceil(hours * 60 / TIMELINE_TARGET_BUCKETS))
    return {
        "hours": hours,
        "start": start,
        "end": end,
        "prev_start": start - timedelta(hours=hours),
        "trend_seconds": hours * 3600 / TREND_BUCKETS,
        "timeline_minutes": timeline_minutes,
        "timeline_buckets": math.ceil(hours * 60 / timeline_minutes),
    }


def fill(rows, size: int) -> list[int]:
    """rows = [(chỉ số khoảng, số lượng)] -> danh sách `size` phần tử; chỉ số ngoài khoảng bị bỏ."""
    buckets = [0] * size
    for index, count in rows:
        if index is not None and 0 <= int(index) < size:
            buckets[int(index)] += int(count)
    return buckets


def change_pct(current: int, previous: int) -> float | None:
    if not previous:
        return None
    return round((current - previous) * 100.0 / previous, 1)


def timeline(rows, size: int) -> list[dict]:
    """rows = [(chỉ số khoảng, mức độ, số lượng)] -> mỗi khoảng một dict theo 4 mức độ."""
    buckets = [dict.fromkeys(SEVERITIES, 0) for _ in range(size)]
    for index, severity, count in rows:
        if index is not None and 0 <= int(index) < size and severity in SEVERITIES:
            buckets[int(index)][severity] += int(count)
    return buckets


def heatmap(rows, today: date) -> list[dict]:
    """rows = [(ngày địa phương, giờ 0-23, số lượng)] -> 7 ngày gần nhất (cũ trước), mỗi ngày 24 giờ."""
    days = [today - timedelta(days=offset) for offset in range(HEATMAP_DAYS - 1, -1, -1)]
    matrix = {day: [0] * 24 for day in days}
    for day, hour, count in rows:
        if day in matrix and 0 <= int(hour) < 24:
            matrix[day][int(hour)] += int(count)
    return [{"date": day.isoformat(), "weekday": day.weekday(), "hours": matrix[day]} for day in days]


def escape_like(text: str) -> str:
    """Thoát ký tự đại diện của ILIKE (dùng với ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
