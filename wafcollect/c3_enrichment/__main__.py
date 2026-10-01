from __future__ import annotations

import logging

from ..common.bus import create_bus
from ..common.config import env_bool, env_float, env_int, env_str
from ..common.runtime import install_stop_handler, setup_logging
from ..common.scope import create_scope
from ..common.stats import Stats
from .geoip import GeoResolver
from .service import EnrichmentService
from .sessions import SessionTracker


def main() -> None:
    setup_logging()
    stop = install_stop_handler()
    bus = create_bus(env_str("BUS_BACKEND", "redis"), env_str("REDIS_URL"),
                     consumer=env_str("CONSUMER_NAME", "default"))
    scope = create_scope(env_str("SCOPE_URL"), env_str("SCOPE_TOKEN"), env_bool("SCOPE_DISABLED"))
    geo = GeoResolver(env_str("GEOIP_DB_PATH") or None)
    tracker = SessionTracker(
        session_timeout=env_float("SESSION_TIMEOUT_S", 1800.0),
        max_sessions=env_int("MAX_SESSIONS", 50_000),
        max_ips=env_int("MAX_TRACKED_IPS", 100_000),
    )
    stats = Stats("c3")
    stats.start_reporter(stop)
    EnrichmentService(bus, scope, geo, tracker, stats,
                      redact_cookie=env_bool("REDACT_COOKIE", True)).register()
    logging.getLogger("c3").info("C3 Enrichment sẵn sàng (geoip=%s)", "on" if geo.enabled else "off")
    bus.run(stop)
    bus.close()


if __name__ == "__main__":
    main()
