"""D2 — Rule Management (HTTP).

CRUD rule lưu thành file YAML trong RULES_DIR (mount RW). Validate schema và chặn
regex nguy hiểm trước khi lưu. FastAPI + uvicorn, access_log=False.
"""
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from common import config
from common import logging as log

import store as store_mod
import validator as validator_mod

SOURCE = "d2-rule-manager"

app = FastAPI(title="D2 Rule Manager", version="0.1.0")


def _rules_dir():
    return config.env("RULES_DIR", "/data/rules")


def _error(status, message, errors=None):
    body = {"error": "invalid_rule" if status == 400 else "error", "message": message}
    if errors:
        body["errors"] = list(errors)
    return JSONResponse(status_code=status, content=body)


@app.on_event("startup")
def startup():
    log.set_service(SOURCE)
    log.info("d2-rule-manager started", rules_dir=_rules_dir())


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/rules")
def list_rules():
    rules = store_mod.list_rules(_rules_dir())
    return {"items": rules, "count": len(rules)}


@app.get("/rules/{rule_id}")
def get_rule(rule_id: str):
    rule = store_mod.get_rule(_rules_dir(), rule_id)
    if rule is None:
        return _error(404, f"rule {rule_id!r} not found")
    return rule


@app.post("/rules/validate")
def validate_only(body: dict):
    result = validator_mod.validate_rule(body)
    if not result.ok:
        return _error(400, "rule failed validation", result.errors)
    return {"valid": True, "rule": result.rule}


@app.post("/rules")
def create_rule(body: dict):
    result = validator_mod.validate_rule(body)
    if not result.ok:
        return _error(400, "rule failed validation", result.errors)
    if store_mod.rule_exists(_rules_dir(), result.rule["id"]):
        return _error(409, f"rule {result.rule['id']!r} already exists")
    saved = store_mod.save_rule(_rules_dir(), result.rule)
    log.info("rule created", id=saved["id"])
    return JSONResponse(status_code=201, content=saved)


@app.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: dict):
    if body.get("id") not in (None, rule_id):
        return _error(400, "id in body must match id in path")
    payload = {**body, "id": rule_id}
    result = validator_mod.validate_rule(payload)
    if not result.ok:
        return _error(400, "rule failed validation", result.errors)
    saved = store_mod.save_rule(_rules_dir(), result.rule)
    log.info("rule updated", id=saved["id"])
    return saved


@app.delete("/rules/{rule_id}")
def delete_rule(rule_id: str):
    if not store_mod.delete_rule(_rules_dir(), rule_id):
        return _error(404, f"rule {rule_id!r} not found")
    log.info("rule deleted", id=rule_id)
    return {"deleted": rule_id}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000),
                access_log=False)
