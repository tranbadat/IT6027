"""HTTP API cho P4 (FastAPI). Gateway map /api/alerts -> /alerts (cần JWT).

Khi có kho DB (repo), /alerts đọc lịch sử BỀN từ Postgres (sống sót restart);
nếu không, đọc lịch sử gần đây trong bộ nhớ.
"""
from fastapi import FastAPI, Query

# Giới hạn số cảnh báo trả về mỗi lần gọi /alerts.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def create_app(store, repo=None) -> FastAPI:
    app = FastAPI(title="P4 Alerting Service", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/alerts")
    def alerts(limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
               domain: str | None = Query(default=None)) -> dict:
        counters = store.snapshot_counters()
        if repo is not None:
            try:
                return {"alerts": repo.recent(limit, domain), "counters": counters,
                        "persisted": True}
            except Exception:
                pass  # DB tạm lỗi -> quay về bộ nhớ
        items = store.recent_alerts(limit)
        if domain:
            items = [a for a in items if a.get("domain") == domain]
        return {"alerts": items, "counters": counters, "persisted": False}

    return app
