"""Kết nối PostgreSQL (psycopg 3), có retry khi DB chưa sẵn sàng."""
import time

import psycopg

from . import logging as log

_CONNECT_RETRIES = 30
_CONNECT_DELAY_SECONDS = 2


def connect(dsn: str, autocommit: bool = True) -> psycopg.Connection:
    last_error: Exception | None = None
    for attempt in range(1, _CONNECT_RETRIES + 1):
        try:
            conn = psycopg.connect(dsn, autocommit=autocommit)
            log.info("connected to database", attempt=attempt)
            return conn
        except Exception as exc:
            last_error = exc
            log.warning("database not ready, retrying", attempt=attempt, error=str(exc))
            time.sleep(_CONNECT_DELAY_SECONDS)
    raise RuntimeError(f"cannot connect to database: {last_error}")
