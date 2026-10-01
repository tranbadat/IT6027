"""C3 - Enrichment & Session: consume log.normalized -> publish log.enriched."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from ..common import events
from ..common.bus import EventBus, RetryLater
from ..common.scope import Decision, ScopeChecker
from ..common.stats import Stats
from .geoip import GeoResolver
from .sessions import SessionTracker, extract_session_cookie

log = logging.getLogger("c3")

GROUP = "c3-enrichment"

# `ck="..."` là trường cookie mở rộng trong dòng log gốc (xem docs/web-server-setup.md)
_CK_IN_RAW = re.compile(r'(\bck=)"(?:[^"\\]|\\.)*"')


def _to_epoch(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class EnrichmentService:
    def __init__(self, bus: EventBus, scope: ScopeChecker, geo: GeoResolver,
                 tracker: SessionTracker, stats: Stats | None = None, redact_cookie: bool = True) -> None:
        self.bus, self.scope, self.geo, self.tracker = bus, scope, geo, tracker
        # Token phiên là bí mật: mặc định C3 không chuyển giá trị cookie xuống downstream.
        self.redact_cookie = redact_cookie
        self.stats = stats or Stats("c3")

    def register(self) -> None:
        self.bus.subscribe(events.LOG_NORMALIZED, GROUP, self.handle)

    def handle(self, envelope: dict[str, Any]) -> None:
        data = envelope["data"]
        domain = data.get("domain", "")

        decision = self.scope.check(domain)
        if decision is Decision.UNAVAILABLE:
            raise RetryLater(f"Scope Service không khả dụng khi kiểm tra {domain!r}")
        if decision is Decision.DENIED:
            self.stats.incr("dropped_out_of_scope")
            return

        ip = data.get("client_ip") or ""
        ts = (_to_epoch(data.get("request_time")) or _to_epoch(data.get("collected_at"))
              or datetime.now(timezone.utc).timestamp())
        headers = dict(data.get("headers") or {})
        cookie_raw = headers.get("cookie")
        if self.redact_cookie:
            headers.pop("cookie", None)
        cookie_key = extract_session_cookie(cookie_raw)

        tracked = self.tracker.observe(domain, ip, cookie_key, ts, int(data.get("status") or 0))

        out = dict(data)
        out["headers"] = headers
        if self.redact_cookie and out.get("raw_line"):
            out["raw_line"] = _CK_IN_RAW.sub(r'\1"[redacted]"', out["raw_line"])
        out["geo"] = self.geo.lookup(ip)
        out["session"] = tracked["session"]
        out["session"]["cookie_present"] = cookie_raw is not None
        out["frequency"] = tracked["frequency"]

        self.stats.incr("enriched")
        self.bus.publish(events.make_event(
            events.LOG_ENRICHED, out,
            request_id=envelope["request_id"], session_id=tracked["session"]["id"],
        ))
