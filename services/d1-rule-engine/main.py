"""D1 — Rule Engine.

consume log.enriched (q.d1-rule-engine) -> khớp rule -> publish attack.detected.
Một event có thể khớp nhiều rule -> publish nhiều attack.detected.
"""
from common import bus as bus_mod
from common import config
from common import health
from common import logging as log
from common import scope as scope_mod

import matcher as matcher_mod

SOURCE = "d1-rule-engine"


def build_handler(bus, scope, store):
    def handle(env, routing_key):
        domain = env["domain"]
        if not scope.allowed(domain):
            log.info("skip out-of-scope event", domain=domain, event=env["event"])
            return

        rules = store.maybe_reload()
        data = env["data"]
        detections = matcher_mod.match_event(rules, data)
        for detection in detections:
            bus.publish(
                "attack.detected",
                domain=domain,
                data=detection,
                request_id=env.get("request_id"),
                session_id=env.get("session_id"),
            )
        if detections:
            log.info("attacks detected", domain=domain, count=len(detections),
                     src_ip=data.get("src_ip"))

    return handle


def main():
    log.set_service(SOURCE)
    health.start_heartbeat()
    queue = config.env("EVENT_QUEUE", "q.d1-rule-engine")
    rules_dir = config.env("RULES_DIR", "/data/rules")

    store = matcher_mod.RuleStore(rules_dir)
    store.load()
    log.info("loaded rules", count=len(store.rules), dir=rules_dir)

    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    log.info("d1-rule-engine started", queue=queue)
    bus.consume(queue, build_handler(bus, scope, store))


if __name__ == "__main__":
    main()
