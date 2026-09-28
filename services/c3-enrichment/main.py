"""C3 — Enrichment & Session.

consume log.normalized -> thêm geoIP, tần suất request, session -> publish log.enriched.
"""
from common import bus as bus_mod
from common import config
from common import health
from common import logging as log
from common import scope as scope_mod

import enrich as enrich_mod

SOURCE = "c3-enrichment"


def build_handler(bus: bus_mod.EventBus, scope: scope_mod.ScopeClient,
                  geo: enrich_mod.GeoResolver, rates: enrich_mod.RequestRateCounter):
    def handle(env: dict, routing_key: str) -> None:
        domain = env["domain"]
        if not scope.allowed(domain):
            log.info("skip out-of-scope event", domain=domain, event=env["event"])
            return

        data = env["data"]
        src_ip = data.get("src_ip", "")
        geo_info = geo.lookup(src_ip)
        req_count = rates.record(domain, src_ip)
        session_id = enrich_mod.resolve_session_id(data)

        # Immutable: tạo dict mới, không sửa data nhận từ envelope.
        enriched = enrich_mod.build_enriched_data(data, geo_info, req_count, session_id)
        bus.publish(
            "log.enriched",
            domain=domain,
            data=enriched,
            request_id=env.get("request_id"),
            session_id=session_id,
        )
        log.debug("enriched", domain=domain, src_ip=src_ip, req_count_1m=req_count)

    return handle


def main() -> None:
    log.set_service(SOURCE)
    health.start_heartbeat()
    queue = config.env("EVENT_QUEUE", "q.c3-enrichment")
    geo = enrich_mod.GeoResolver(config.env("GEOIP_DB_PATH"))
    rates = enrich_mod.RequestRateCounter()
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    log.info("c3-enrichment started", queue=queue)
    bus.consume(queue, build_handler(bus, scope, geo, rates))


if __name__ == "__main__":
    main()
