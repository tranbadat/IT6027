from __future__ import annotations

import logging

from ..common.bus import create_bus
from ..common.config import env_bool, env_str
from ..common.runtime import install_stop_handler, setup_logging
from ..common.scope import create_scope
from ..common.stats import Stats
from .config import load_config
from .service import IngestionService
from .tailer import StateStore


def main() -> None:
    setup_logging()
    stop = install_stop_handler()
    cfg = load_config(env_str("C1_CONFIG", "/etc/wafcollect/c1.yaml"))
    bus = create_bus(env_str("BUS_BACKEND", "redis"), env_str("REDIS_URL"))
    scope = create_scope(env_str("SCOPE_URL"), env_str("SCOPE_TOKEN"), env_bool("SCOPE_DISABLED"))
    state = StateStore(env_str("C1_STATE_PATH", "/var/lib/wafcollect/c1-state.json"))
    stats = Stats("c1")
    stats.start_reporter(stop)
    svc = IngestionService(cfg, bus, scope, state, stats)
    logging.getLogger("c1").info("C1 Ingestion chạy với %d nguồn", len(cfg.sources))
    svc.run(stop)
    bus.close()


if __name__ == "__main__":
    main()
