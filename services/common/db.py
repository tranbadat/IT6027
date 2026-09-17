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


def pool(dsn: str, max_size: int = 5):
    """Mở connection pool an toàn đa luồng (consumer ghi, HTTP đọc dùng chung).

    Dùng khi service vừa consume ở thread nền vừa phục vụ HTTP ở threadpool.
    Có retry chờ DB sẵn sàng.
    """
    from psycopg_pool import ConnectionPool

    last_error: Exception | None = None
    for attempt in range(1, _CONNECT_RETRIES + 1):
        cpool = None
        try:
            cpool = ConnectionPool(dsn, min_size=1, max_size=max_size,
                                   kwargs={"autocommit": True}, open=True)
            cpool.wait(timeout=5)
            log.info("database pool ready", attempt=attempt)
            return cpool
        except Exception as exc:
            last_error = exc
            log.warning("database pool not ready, retrying", attempt=attempt, error=str(exc))
            if cpool is not None:
                try:
                    cpool.close()
                except Exception:
                    pass
            time.sleep(_CONNECT_DELAY_SECONDS)
    raise RuntimeError(f"cannot open database pool: {last_error}")
