from __future__ import annotations

import logging

from ..common.bus import create_bus
from ..common.config import env_bool, env_str
from ..common.runtime import install_stop_handler, setup_logging
from ..common.scope import create_scope
from ..common.stats import Stats
from .service import ParsingService


def main() -> None:
    setup_logging()
    stop = install_stop_handler()
    bus = create_bus(env_str("BUS_BACKEND", "redis"), env_str("REDIS_URL"),
                     consumer=env_str("CONSUMER_NAME", "default"))
    scope = create_scope(env_str("SCOPE_URL"), env_str("SCOPE_TOKEN"), env_bool("SCOPE_DISABLED"))
    stats = Stats("c2")
    stats.start_reporter(stop)
    ParsingService(bus, scope, stats).register()
    logging.getLogger("c2").info("C2 Parsing sẵn sàng")
    bus.run(stop)
    bus.close()


if __name__ == "__main__":
    main()
