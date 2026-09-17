"""P1 — Scope Management.

GET /scope/check?domain=|app=  -> domain/ứng dụng có được phép xử lý log không.
Danh sách phạm vi lưu ở bảng scope.allowlist, seed từ SCOPE_ALLOWLIST khi bảng trống.
"""
import re

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from common import config
from common import db as db_mod
from common import logging as log

SOURCE = "p1-scope"
CACHE_TTL_SECONDS = 60
NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")

app = FastAPI(title="P1 Scope Service", version="0.1.0")
_conn = None


def _init_db() -> None:
    global _conn
    _conn = db_mod.connect(config.env("DATABASE_URL", required=True))
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS scope.allowlist (
            target      text PRIMARY KEY,
            target_type text NOT NULL DEFAULT 'domain',
            enabled     boolean NOT NULL DEFAULT true,
            note        text,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
    """)
    row = _conn.execute("SELECT count(*) FROM scope.allowlist").fetchone()
    if row[0] == 0:
        seed = config.env_list("SCOPE_ALLOWLIST", "shop.local,blog.local,shop-app")
        for target in seed:
            target_type = "app" if "." not in target else "domain"
            _conn.execute(
                "INSERT INTO scope.allowlist (target, target_type, note) VALUES (%s, %s, %s)"
                " ON CONFLICT (target) DO NOTHING",
                (target.lower(), target_type, "seeded"),
            )
        log.info("seeded scope allowlist", count=len(seed))


def _lookup(target: str) -> bool:
    row = _conn.execute(
        "SELECT enabled FROM scope.allowlist WHERE target = %s", (target.lower(),)
    ).fetchone()
    return bool(row and row[0])


@app.on_event("startup")
def startup() -> None:
    log.set_service(SOURCE)
    _init_db()
    log.info("p1-scope started")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/scope/check")
def check(domain: str | None = Query(default=None), app_: str | None = Query(default=None, alias="app")):
    target = domain or app_
    target_type = "domain" if domain else "app"
    if not target or not NAME_RE.match(target):
        return JSONResponse(status_code=400, content={
            "error": "invalid_request",
            "message": "query parameter domain or app is required and must be a valid name",
        })
    allowed = _lookup(target)
    return {
        "target": target,
        "target_type": target_type,
        "allowed": allowed,
        "reason": "in scope allowlist" if allowed else "not in scope allowlist",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }


@app.get("/scope/allowlist")
def list_allowlist() -> dict:
    rows = _conn.execute(
        "SELECT target, target_type, enabled, note FROM scope.allowlist ORDER BY target"
    ).fetchall()
    return {"items": [
        {"target": r[0], "target_type": r[1], "enabled": r[2], "note": r[3]} for r in rows
    ]}


@app.post("/scope/allowlist")
def add_allowlist(body: dict) -> dict:
    target = (body.get("target") or "").lower()
    if not NAME_RE.match(target):
        raise HTTPException(status_code=400, detail="invalid target")
    target_type = body.get("target_type") or ("app" if "." not in target else "domain")
    enabled = bool(body.get("enabled", True))
    _conn.execute(
        "INSERT INTO scope.allowlist (target, target_type, enabled, note) VALUES (%s,%s,%s,%s)"
        " ON CONFLICT (target) DO UPDATE SET target_type = EXCLUDED.target_type,"
        " enabled = EXCLUDED.enabled, note = EXCLUDED.note",
        (target, target_type, enabled, body.get("note")),
    )
    return {"target": target, "target_type": target_type, "enabled": enabled}


@app.delete("/scope/allowlist/{target}")
def remove_allowlist(target: str) -> dict:
    _conn.execute("DELETE FROM scope.allowlist WHERE target = %s", (target.lower(),))
    return {"deleted": target.lower()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000), access_log=False)
