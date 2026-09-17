"""HTTP API cho P5 (FastAPI).

Route:
- GET /                    -> trang HTML dashboard (public, gateway map "/").
- GET /dashboard/summary   -> JSON tổng hợp (gateway map /api/dashboard/summary, cần JWT).
- GET /dashboard/stream    -> SSE (gateway map /api/dashboard/stream, cần JWT).
- GET /healthz             -> healthcheck.
"""
import asyncio
import json
import os

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse

_INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.html")
_RULES_PATH = os.path.join(os.path.dirname(__file__), "rules.html")
# Khoảng thời gian gửi comment keep-alive khi không có event (giữ kết nối SSE sống).
_KEEPALIVE_SECONDS = 15


def _load_index_html() -> str:
    with open(_INDEX_PATH, encoding="utf-8") as handle:
        return handle.read()


def _format_sse(item: dict) -> str:
    event = item.get("event") or "message"
    payload = json.dumps(item, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _event_stream(request: Request, hub):
    queue = hub.subscribe()
    try:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield _format_sse(item)
    finally:
        hub.unsubscribe(queue)


def _load_html(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def create_app(store, hub, repo=None) -> FastAPI:
    app = FastAPI(title="P5 Dashboard Service", version="0.1.0")
    index_html = _load_index_html()
    rules_html = _load_html(_RULES_PATH)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(content=index_html)

    # Trang quản trị rule (gateway map "/" -> P5; gọi API D2 qua /api/rules/*).
    @app.get("/rules", response_class=HTMLResponse)
    def rules_admin() -> HTMLResponse:
        return HTMLResponse(content=rules_html)

    @app.get("/dashboard/summary")
    def summary() -> dict:
        return store.summary()

    # Lịch sử tấn công BỀN từ DB (sống sót restart). Gateway: /api/dashboard/attacks (JWT).
    @app.get("/dashboard/attacks")
    def attacks(limit: int = Query(default=100, ge=1, le=1000),
                domain: str | None = Query(default=None),
                attack_type: str | None = Query(default=None)) -> dict:
        if repo is None:
            return {"attacks": [], "persisted": False}
        try:
            return {"attacks": repo.recent(limit, domain, attack_type),
                    "count": repo.count(), "persisted": True}
        except Exception:
            return {"attacks": [], "persisted": False}

    @app.get("/dashboard/stream")
    async def stream(request: Request) -> StreamingResponse:
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # phòng khi có proxy buffer (gateway đã tắt buffering)
        }
        return StreamingResponse(
            _event_stream(request, hub),
            media_type="text/event-stream",
            headers=headers,
        )

    return app
