"""Client gọi Scope Service (P1). Cache kết quả theo cache_ttl_seconds.

Fail closed: khi không gọi được Scope Service, coi như KHÔNG được phép xử lý,
đúng ràng buộc "không service nào xử lý log ngoài phạm vi đã xác nhận".
"""
import time

import httpx

from . import logging as log

_FAILURE_TTL_SECONDS = 5


class ScopeClient:
    def __init__(self, base_url: str, timeout: float = 3.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[str, tuple[bool, float]] = {}

    def allowed(self, domain: str) -> bool:
        now = time.time()
        cached = self._cache.get(domain)
        if cached is not None and cached[1] > now:
            return cached[0]

        try:
            resp = httpx.get(f"{self._base}/scope/check",
                             params={"domain": domain}, timeout=self._timeout)
            resp.raise_for_status()
            body = resp.json()
            allowed = bool(body.get("allowed"))
            ttl = int(body.get("cache_ttl_seconds", 60))
        except Exception as exc:
            log.warning("scope check failed, denying (fail closed)",
                        domain=domain, error=str(exc))
            self._cache[domain] = (False, now + _FAILURE_TTL_SECONDS)
            return False

        self._cache[domain] = (allowed, now + max(ttl, 1))
        return allowed
