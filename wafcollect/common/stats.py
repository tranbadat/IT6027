"""Bộ đếm đơn giản, log định kỳ để theo dõi service đang sống và đang xử lý gì."""
from __future__ import annotations

import logging
import threading
from collections import Counter


class Stats:
    def __init__(self, name: str):
        self.name = name
        self._c: Counter[str] = Counter()
        self._lock = threading.Lock()

    def incr(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._c[key] += n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._c)

    def start_reporter(self, stop: threading.Event, interval: float = 30.0) -> threading.Thread:
        log = logging.getLogger(self.name)

        def loop() -> None:
            while not stop.wait(interval):
                log.info("stats %s", self.snapshot())

        t = threading.Thread(target=loop, name=f"{self.name}-stats", daemon=True)
        t.start()
        return t
