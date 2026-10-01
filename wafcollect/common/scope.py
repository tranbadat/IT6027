"""Client gọi Scope Service (P1 của Người 3): GET /scope/check?domain=...

Giả định contract (cần Người 3 xác nhận, xem docs/event-schema.md mục "Giả định"):
    GET {SCOPE_URL}/scope/check?domain=<domain>   ->  200 {"domain": "...", "allowed": true|false}
    Header tuỳ chọn:  Authorization: Bearer <SCOPE_TOKEN>

Nguyên tắc fail-closed: chỉ xử lý log khi Scope Service xác nhận rõ ràng allowed=true.
  - allowed=false            -> DENIED: bỏ dòng log, không xử lý
  - lỗi mạng/5xx/timeout     -> UNAVAILABLE: KHÔNG xử lý và KHÔNG bỏ dữ liệu; caller thử lại sau
"""
from __future__ import annotations

import enum
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

log = logging.getLogger("scope")


class Decision(enum.Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


class ScopeChecker:
    def check(self, domain: str) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


class ScopeClient(ScopeChecker):
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = 3.0,
        allow_ttl: float = 60.0,
        deny_ttl: float = 30.0,
        error_ttl: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.allow_ttl, self.deny_ttl, self.error_ttl = allow_ttl, deny_ttl, error_ttl
        self._clock = clock
        self._cache: dict[str, tuple[Decision, float]] = {}
        self._lock = threading.Lock()

    def _fetch(self, domain: str) -> Decision:
        url = f"{self.base_url}/scope/check?{urllib.parse.urlencode({'domain': domain})}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):  # domain không thuộc phạm vi
                return Decision.DENIED
            log.warning("scope check %s lỗi HTTP %s", domain, e.code)
            return Decision.UNAVAILABLE
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            log.warning("scope check %s lỗi: %s", domain, e)
            return Decision.UNAVAILABLE
        if body.get("allowed") is True:
            return Decision.ALLOWED
        if body.get("allowed") is False:
            return Decision.DENIED
        log.warning("scope check %s: phản hồi không hợp lệ %r", domain, body)
        return Decision.UNAVAILABLE

    def check(self, domain: str) -> Decision:
        if not domain:
            return Decision.DENIED
        now = self._clock()
        with self._lock:
            hit = self._cache.get(domain)
            if hit and hit[1] > now:
                return hit[0]
        decision = self._fetch(domain)
        ttl = {Decision.ALLOWED: self.allow_ttl, Decision.DENIED: self.deny_ttl}.get(
            decision, self.error_ttl
        )
        with self._lock:
            self._cache[domain] = (decision, now + ttl)
        return decision


class StaticScope(ScopeChecker):
    """Dùng cho test, hoặc dev cục bộ khi SCOPE_DISABLED=true (không dùng ở production)."""

    def __init__(self, allowed: set[str] | None = None, allow_all: bool = False) -> None:
        self.allowed = allowed or set()
        self.allow_all = allow_all

    def check(self, domain: str) -> Decision:
        if self.allow_all or domain in self.allowed:
            return Decision.ALLOWED
        return Decision.DENIED


def create_scope(url: str, token: str, disabled: bool) -> ScopeChecker:
    if disabled:
        log.warning("SCOPE_DISABLED=true: BỎ QUA kiểm tra scope. Chỉ dùng khi phát triển cục bộ!")
        return StaticScope(allow_all=True)
    if not url:
        raise SystemExit(
            "Thiếu SCOPE_URL. Đặt SCOPE_URL tới Scope Service (hoặc mock), "
            "hoặc SCOPE_DISABLED=true khi phát triển cục bộ."
        )
    return ScopeClient(url, token=token)
