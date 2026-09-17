"""Lịch sử cảnh báo + bộ đếm trong bộ nhớ (có cap), an toàn đa luồng.

Consumer chạy ở thread nền ghi vào store; HTTP handler ở thread chính đọc ra.
"""
import collections
import threading

# Số bản ghi cảnh báo gần nhất giữ lại phục vụ GET /alerts.
HISTORY_CAP = 500

_COUNTER_NAMES = (
    "anomalies",          # tổng anomaly.scored nhận được
    "anomalies_flagged",  # anomaly vượt ngưỡng (chỉ đánh dấu, không gửi mail)
    "alerts",             # alert.triggered đã đưa vào lịch sử
    "alerts_suppressed",  # alert bị bỏ do trùng/cooldown
    "emails_sent",
    "emails_failed",
    "webhooks_sent",
    "webhooks_failed",
)


class AlertStore:
    def __init__(self, cap: int = HISTORY_CAP):
        self._alerts: collections.deque = collections.deque(maxlen=cap)
        self._counters: dict[str, int] = {name: 0 for name in _COUNTER_NAMES}
        self._lock = threading.Lock()

    def add_alert(self, alert: dict) -> None:
        with self._lock:
            self._alerts.append(alert)
            self._counters["alerts"] += 1

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            if name in self._counters:
                self._counters[name] += amount

    def recent_alerts(self, limit: int | None = None) -> list[dict]:
        """Danh sách cảnh báo mới nhất trước. Trả bản sao list để không lộ deque nội bộ."""
        with self._lock:
            items = list(self._alerts)
        items.reverse()
        return items[:limit] if limit else items

    def snapshot_counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)
