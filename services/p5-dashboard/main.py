"""P5 — Dashboard realtime.

Consume q.p5-dashboard (tất cả event) ở thread nền với kết nối bus RIÊNG, phục vụ HTTP
(UI + summary + SSE) ở thread chính. StreamHub được gắn event loop trong startup rồi
consumer mới được khởi động, đảm bảo call_soon_threadsafe luôn có loop hợp lệ.
"""
import asyncio

import uvicorn

from common import config
from common import logging as log

import app as app_mod
import consumer as consumer_mod
import store as store_mod
from stream import StreamHub

SOURCE = "p5-dashboard"


def main() -> None:
    log.set_service(SOURCE)

    queue = config.env("EVENT_QUEUE", "q.p5-dashboard")
    bus_url = config.env("EVENT_BUS_URL", required=True)

    store = store_mod.AggregateStore()
    hub = StreamHub()
    handler = consumer_mod.build_handler(store, hub)
    app = app_mod.create_app(store, hub)

    @app.on_event("startup")
    def _startup() -> None:
        hub.bind_loop(asyncio.get_running_loop())
        consumer_mod.start_consumer_thread(bus_url, queue, handler)
        log.info("p5-dashboard started", queue=queue)

    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000), access_log=False)


if __name__ == "__main__":
    main()
