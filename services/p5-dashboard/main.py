"""P5 — Dashboard realtime.

Consume q.p5-dashboard (tất cả event) ở thread nền với kết nối bus RIÊNG, phục vụ HTTP
(UI + summary + SSE) ở thread chính. StreamHub được gắn event loop trong startup rồi
consumer mới được khởi động, đảm bảo call_soon_threadsafe luôn có loop hợp lệ.
"""
import asyncio

import uvicorn

from common import config
from common import db as db_mod
from common import logging as log

import app as app_mod
import consumer as consumer_mod
import repo as repo_mod
import store as store_mod
from stream import StreamHub

SOURCE = "p5-dashboard"


def _init_repo():
    """Mở kho lịch sử tấn công (Postgres) nếu có DATABASE_URL. Lỗi -> degrade in-memory."""
    dsn = config.env("DATABASE_URL", "")
    if not dsn:
        return None
    try:
        repo = repo_mod.AttackRepo(db_mod.pool(dsn))
        repo.ensure_schema()
        return repo
    except Exception as exc:
        log.error("cannot init attack repo, bỏ qua lưu DB", error=str(exc))
        return None


def main() -> None:
    log.set_service(SOURCE)

    queue = config.env("EVENT_QUEUE", "q.p5-dashboard")
    bus_url = config.env("EVENT_BUS_URL", required=True)

    store = store_mod.AggregateStore()
    hub = StreamHub()
    repo = _init_repo()
    handler = consumer_mod.build_handler(store, hub, repo)
    app = app_mod.create_app(store, hub, repo)

    @app.on_event("startup")
    def _startup() -> None:
        hub.bind_loop(asyncio.get_running_loop())
        consumer_mod.start_consumer_thread(bus_url, queue, handler)
        log.info("p5-dashboard started", queue=queue)

    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000), access_log=False)


if __name__ == "__main__":
    main()
