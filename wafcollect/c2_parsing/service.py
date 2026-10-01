"""C2 - Parsing & Normalize: consume log.raw.ingested -> publish log.normalized."""
from __future__ import annotations

import logging
from typing import Any

from ..common import events
from ..common.bus import EventBus, RetryLater
from ..common.scope import Decision, ScopeChecker
from ..common.stats import Stats
from .parser import parse_line

log = logging.getLogger("c2")

GROUP = "c2-parsing"


class ParsingService:
    def __init__(self, bus: EventBus, scope: ScopeChecker, stats: Stats | None = None) -> None:
        self.bus = bus
        self.scope = scope
        self.stats = stats or Stats("c2")

    def register(self) -> None:
        self.bus.subscribe(events.LOG_RAW_INGESTED, GROUP, self.handle)

    def handle(self, envelope: dict[str, Any]) -> None:
        raw = envelope["data"]
        domain = raw.get("domain", "")

        # Ràng buộc xuyên suốt: chỉ xử lý log của domain đã được xác nhận qua Scope Service.
        decision = self.scope.check(domain)
        if decision is Decision.UNAVAILABLE:
            raise RetryLater(f"Scope Service không khả dụng khi kiểm tra {domain!r}")
        if decision is Decision.DENIED:
            self.stats.incr("dropped_out_of_scope")
            return

        result = parse_line(raw["raw_line"], fallback_time=raw.get("collected_at"))
        request_id = envelope["request_id"]

        if result.status == "failed":
            self.stats.incr("parse_failed")
            self.bus.publish(events.make_event(
                events.LOG_PARSE_FAILED,
                {
                    "domain": domain,
                    "source": raw.get("source"),
                    "error": result.error,
                    "raw_line": raw["raw_line"][:2048],
                    "collected_at": raw.get("collected_at"),
                },
                request_id=request_id,
            ))
            return

        data = dict(result.data or {})
        data["domain"] = domain
        data["source"] = raw.get("source")
        data["collected_at"] = raw.get("collected_at")
        self.stats.incr("parsed_" + result.status)
        self.bus.publish(events.make_event(events.LOG_NORMALIZED, data, request_id=request_id))
