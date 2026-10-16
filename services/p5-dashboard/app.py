"""HTTP API cho P5 (FastAPI).

Route:
- GET /                    -> trang HTML dashboard (public, gateway map "/").
- GET /dashboard/summary   -> JSON tổng hợp (gateway map /api/dashboard/summary, cần JWT).
- GET /dashboard/stream    -> SSE (gateway map /api/dashboard/stream, cần JWT).
- GET /dashboard/links     -> liên kết trang Cài đặt từ DASHBOARD_LINKS (cần JWT).
- GET /dashboard/overview  -> số liệu trang Tổng quan trong cửa sổ `hours` (cần JWT).
- GET /dashboard/attacks   -> lịch sử có lọc, phân trang, phân bố theo thời gian (cần JWT).
- GET /dashboard/stats/domains, /dashboard/stats/rules -> số tấn công và xu hướng theo domain/rule.
- GET /static/...          -> CSS, JS, icon của giao diện.
- GET /healthz             -> healthcheck.
"""
import asyncio
import json
import os

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from common import logging as log

import stats as stats_mod

_INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.html")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_DEFAULT_HOURS = 24
_MAX_QUERY = 200
# Khoảng thời gian gửi comment keep-alive khi không có event (giữ kết nối SSE sống).
_KEEPALIVE_SECONDS = 15
_LINK_SCHEMES = ("http://", "https://")


def parse_links(raw: str | None) -> list[dict]:
    """DASHBOARD_LINKS = "Tên|URL;Tên 2|URL 2" -> [{"name", "url"}]. Bỏ mục sai định dạng và URL
    không phải http(s) (URL được chèn vào href của trang)."""
    links = []
    for entry in (raw or "").split(";"):
        name, sep, url = entry.partition("|")
        name, url = name.strip(), url.strip()
        if sep and name and url.lower().startswith(_LINK_SCHEMES):
            links.append({"name": name, "url": url})
    return links


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


class _RevalidatedStaticFiles(StaticFiles):
    """File tĩnh không có hash phiên bản trong tên: `no-cache` buộc trình duyệt hỏi lại mỗi lần (ETag -> 304),
    tránh trộn module JS cũ và mới sau khi triển khai bản mới."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(store, hub, repo=None, links=None, stats=None) -> FastAPI:
    app = FastAPI(title="P5 Dashboard Service", version="0.1.0")
    index_html = _load_index_html()
    settings_links = list(links or [])
    if os.path.isdir(_STATIC_DIR):
        app.mount("/static", _RevalidatedStaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(content=index_html)

    # Quản trị rule nằm trong SPA (view "Rule pattern", gọi Rule API qua /api/rules/*).
    @app.get("/rules")
    def rules_admin() -> RedirectResponse:
        return RedirectResponse(url="/#rules", status_code=307)

    @app.get("/dashboard/summary")
    def summary() -> dict:
        return store.summary()

    # Rỗng -> trang Cài đặt dùng liên kết mặc định theo host:cổng (chạy local).
    @app.get("/dashboard/links")
    def settings_page_links() -> dict:
        return {"links": settings_links}

    # Lịch sử tấn công BỀN từ DB (sống sót restart). Gateway: /api/dashboard/attacks (JWT).
    @app.get("/dashboard/attacks")
    def attacks(limit: int = Query(default=100, ge=1, le=1000),
                offset: int = Query(default=0, ge=0, le=1_000_000),
                hours: int | None = Query(default=None, ge=1, le=stats_mod.MAX_WINDOW_HOURS),
                q: str | None = Query(default=None, max_length=_MAX_QUERY),
                domain: str | None = Query(default=None, max_length=_MAX_QUERY),
                attack_type: str | None = Query(default=None, max_length=_MAX_QUERY),
                severity: str | None = Query(default=None, max_length=_MAX_QUERY),
                histogram: bool = Query(default=False)) -> dict:
        if repo is None:
            return {"attacks": [], "total": 0, "persisted": False}
        try:
            result = repo.search(limit=limit, offset=offset, hours=hours, q=q, domain=domain,
                                 attack_type=attack_type, severity=severity, histogram=histogram)
            return {**result, "count": repo.count(), "persisted": True}
        except Exception as exc:
            log.error("attack history query failed", error=str(exc))
            return {"attacks": [], "total": 0, "persisted": False}

    @app.get("/dashboard/overview")
    def overview(hours: int = Query(default=_DEFAULT_HOURS, ge=1, le=stats_mod.MAX_WINDOW_HOURS)) -> dict:
        empty = {"window": {"hours": hours}, "persisted": False}
        if stats is None:
            return empty
        try:
            return stats.overview(hours)
        except Exception as exc:
            log.error("overview query failed", error=str(exc))
            return empty

    @app.get("/dashboard/stats/domains")
    def domain_stats(hours: int = Query(default=_DEFAULT_HOURS, ge=1, le=stats_mod.MAX_WINDOW_HOURS)) -> dict:
        return _stats_list("domains", lambda: stats.domains(hours))

    @app.get("/dashboard/stats/rules")
    def rule_stats(hours: int = Query(default=_DEFAULT_HOURS, ge=1, le=stats_mod.MAX_WINDOW_HOURS)) -> dict:
        return _stats_list("rules", lambda: stats.rules(hours))

    def _stats_list(key: str, query) -> dict:
        if stats is None:
            return {key: [], "persisted": False}
        try:
            return {key: query(), "persisted": True}
        except Exception as exc:
            log.error("stats query failed", key=key, error=str(exc))
            return {key: [], "persisted": False}

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
