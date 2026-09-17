"""P2 — Logic JWT HS256 thuần (không phụ thuộc HTTP) để test trực tiếp được.

Tách khỏi main.py: create_token / verify_token có thể gọi thẳng trong test_token.py.
"""
import datetime

import jwt

ISSUER = "waf"
ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["exp", "iat", "sub"]


class TokenError(Exception):
    """Lỗi tạo/verify token. Thông điệp an toàn để trả cho client."""


def create_token(secret, subject, roles, ttl_seconds, now=None):
    """Tạo JWT HS256. Trả (token, expires_in). Không sửa tham số đầu vào."""
    if not secret:
        raise TokenError("missing signing secret")
    if not subject:
        raise TokenError("missing subject")
    issued_at = now or datetime.datetime.now(datetime.timezone.utc)
    expire_at = issued_at + datetime.timedelta(seconds=ttl_seconds)
    claims = {
        "sub": subject,
        "roles": list(roles),
        "iss": ISSUER,
        "iat": int(issued_at.timestamp()),
        "exp": int(expire_at.timestamp()),
    }
    token = jwt.encode(claims, secret, algorithm=ALGORITHM)
    return token, ttl_seconds


def verify_token(secret, token):
    """Verify chữ ký + exp + iss. Trả {sub, roles}. Ném TokenError nếu không hợp lệ."""
    if not token:
        raise TokenError("missing token")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    roles = claims.get("roles") or []
    return {"sub": claims.get("sub"), "roles": list(roles)}
