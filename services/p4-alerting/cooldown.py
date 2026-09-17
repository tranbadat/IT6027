"""Chống trùng cảnh báo (dedup) theo cửa sổ thời gian cooldown.

Cùng một khoá (domain, src_ip, attack_type chính) chỉ được gửi lại sau khi đã qua
ALERT_COOLDOWN_SECONDS. Đồng hồ được tiêm vào để test không phụ thuộc thời gian thực.
"""
import threading
import time


class CooldownStore:
    def __init__(self, seconds: int, clock=time.monotonic):
        self._seconds = seconds
        self._clock = clock
        self._last: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: tuple) -> bool:
        """True nếu khoá được phép gửi (và ghi nhận thời điểm); False nếu còn trong cooldown.

        Cooldown <= 0 nghĩa là tắt dedup: luôn cho phép.
        """
        if self._seconds <= 0:
            return True
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and (now - last) < self._seconds:
                return False
            self._last[key] = now
            return True
