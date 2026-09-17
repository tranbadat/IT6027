"""HTTP API cho P4 (FastAPI). Gateway map /api/alerts -> /alerts (cần JWT)."""
from fastapi import FastAPI, Query

# Giới hạn số cảnh báo trả về mỗi lần gọi /alerts.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def create_app(store) -> FastAPI:
    app = FastAPI(title="P4 Alerting Service", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/alerts")
    def alerts(limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT)) -> dict:
        return {
            "alerts": store.recent_alerts(limit),
            "counters": store.snapshot_counters(),
        }

    return app
