"""Khởi tạo logging và xử lý tín hiệu dừng (SIGTERM/SIGINT) cho các service."""
from __future__ import annotations

import logging
import signal
import threading

from .config import env_str


def setup_logging() -> None:
    logging.basicConfig(
        level=env_str("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def install_stop_handler() -> threading.Event:
    stop = threading.Event()

    def _handler(signum, frame):  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    return stop
