"""Heartbeat file cho service worker (không có cổng HTTP).

Healthcheck trong Dockerfile kiểm tra file này còn mới hay không.
"""
import threading
import time

HEARTBEAT_PATH = "/tmp/alive"
_INTERVAL_SECONDS = 10


def start_heartbeat(path: str = HEARTBEAT_PATH, interval: int = _INTERVAL_SECONDS) -> None:
    def _loop() -> None:
        while True:
            try:
                with open(path, "w") as handle:
                    handle.write(str(time.time()))
            except OSError:
                pass
            time.sleep(interval)

    thread = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    thread.start()
