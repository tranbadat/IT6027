"""C2 — Parsing & chuẩn hoá.

consume log.raw.ingested -> parse -> publish log.normalized.
"""
from common import bus as bus_mod
from common import config
from common import health
from common import logging as log
from common import scope as scope_mod

import parser as parser_mod

SOURCE = "c2-parser"
TOPICS = ["log.raw.ingested"]


def build_handler(bus: bus_mod.EventBus, scope: scope_mod.ScopeClient):
    def handle(env: dict, routing_key: str) -> None:
        domain = env["domain"]
        if not scope.allowed(domain):
            log.info("skip out-of-scope event", domain=domain, event=env["event"])
            return

        raw = env["data"]["raw"]
        log_source = env["data"].get("log_source", "nginx")
        # Parse hết mọi dòng trước khi publish: không dòng nào hợp lệ -> ValueError -> dead-letter;
        # có dòng hỏng lẫn dòng hợp lệ -> publish dòng hợp lệ, ghi log số dòng hỏng.
        lines, invalid = parser_mod.parse_raw(raw, log_source)
        if invalid:
            log.warning("skipped malformed log lines", domain=domain, invalid=invalid,
                        valid=len(lines))
        for fields in lines:
            # Log combined chuẩn không có "$host" -> lấy domain đã xác nhận từ envelope.
            if not fields["host"]:
                fields = {**fields, "host": domain}
            bus.publish(
                "log.normalized",
                domain=domain,
                data=fields,
                request_id=env.get("request_id"),
                session_id=fields.get("session_id") or env.get("session_id"),
            )
            log.debug("normalized", domain=domain, path=fields["path"], status=fields["status"])

    return handle


def main() -> None:
    log.set_service(SOURCE)
    health.start_heartbeat()
    group = config.env("EVENT_GROUP", SOURCE)
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    log.info("c2-parser started", group=group, topics=TOPICS)
    bus.consume(group, build_handler(bus, scope), topics=TOPICS)


if __name__ == "__main__":
    main()
