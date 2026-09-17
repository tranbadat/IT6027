"""P2 — Auth Service (HTTP, khớp contracts/openapi/auth.yaml).

POST /auth/token  -> cấp JWT trong body + đặt cookie waf_token.
GET  /auth/verify -> xác thực JWT (header Authorization HOẶC cookie waf_token);
                     khi hợp lệ đặt X-Auth-Subject / X-Auth-Roles cho API gateway.
GET  /healthz     -> 200.
"""
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common import config
from common import logging as log

import clients as clients_mod
import tokens as tokens_mod

SOURCE = "p2-auth"
COOKIE_NAME = "waf_token"
BEARER_PREFIX = "Bearer "
WWW_AUTHENTICATE = 'Bearer realm="waf"'

app = FastAPI(title="P2 Auth Service", version="0.1.0")

# Cấu hình runtime, nạp một lần khi startup (immutable sau đó).
_config = {"secret": "", "ttl": 3600, "allow_any": True, "clients": {}}


@app.on_event("startup")
def startup() -> None:
    log.set_service(SOURCE)
    allow_any = (config.env("AUTH_ALLOW_ANY", "true") or "").strip().lower() == "true"
    _config.update({
        "secret": config.env("JWT_SECRET", required=True),
        "ttl": config.env_int("AUTH_TOKEN_TTL", 3600),
        "allow_any": allow_any,
        "clients": clients_mod.load_clients(),
    })
    log.info("p2-auth started", allow_any=allow_any, clients=len(_config["clients"]))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/auth/token")
async def issue_token(request: Request):
    body = await _read_json(request)
    client_id = (body.get("client_id") or "").strip()
    client_secret = body.get("client_secret") or ""
    try:
        subject = clients_mod.authenticate(
            client_id, client_secret, _config["allow_any"], _config["clients"])
    except clients_mod.ClientError as exc:
        return _error(401, "unauthorized", str(exc))

    ttl = _config["ttl"]
    token, expires_in = tokens_mod.create_token(
        _config["secret"], subject, clients_mod.roles_for(subject), ttl)
    response = JSONResponse(
        {"access_token": token, "token_type": "Bearer", "expires_in": expires_in})
    # Cookie cho trình duyệt (EventSource/WebSocket không đặt được header Authorization).
    response.set_cookie(
        COOKIE_NAME, token, max_age=expires_in, path="/",
        httponly=True, samesite="Strict")
    log.info("token issued", sub=subject, expires_in=expires_in)
    return response


@app.get("/auth/verify")
def verify(request: Request):
    token = _extract_token(request)
    try:
        claims = tokens_mod.verify_token(_config["secret"], token)
    except tokens_mod.TokenError as exc:
        return _unauthorized(str(exc))
    # Gateway đọc 2 header này qua auth_request_set rồi chuyển tiếp cho service phía sau.
    return JSONResponse(
        {"sub": claims["sub"], "roles": claims["roles"]},
        headers={
            "X-Auth-Subject": claims["sub"] or "",
            "X-Auth-Roles": ",".join(claims["roles"]),
        },
    )


def _extract_token(request: Request):
    """Lấy token từ 'Authorization: Bearer <jwt>' hoặc cookie waf_token."""
    header = request.headers.get("authorization") or ""
    if header.startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX):].strip()
    return request.cookies.get(COOKIE_NAME)


async def _read_json(request: Request) -> dict:
    """Đọc body JSON, luôn trả dict (body sai định dạng -> dict rỗng)."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _error(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "message": message})


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "message": message},
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.env_int("HTTP_PORT", 8000), access_log=False)
