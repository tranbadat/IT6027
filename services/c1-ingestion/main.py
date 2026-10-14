"""C1 — Log Ingestion (worker, KHÔNG có HTTP).

Tail liên tục các file access log; mỗi dòng mới publish log.raw.ingested. Các dòng của một
vòng poll được gửi liền nhau và chờ broker xác nhận một lần cho cả vòng.
"""
import ipaddress
import time
import uuid

from common import bus as bus_mod
from common import config
from common import envelope as envelope_mod
from common import health
from common import logging as log
from common import scope as scope_mod

import tailer as tailer_mod

SOURCE = "c1-ingestion"
_POLL_INTERVAL_SECONDS = 0.5
_SKIP_LOG_INTERVAL_SECONDS = 60.0


class _SkipTracker:
    """Đếm dòng ngoài scope và log info thưa (mặc định mỗi 60s)."""

    def __init__(self, interval: float = _SKIP_LOG_INTERVAL_SECONDS):
        self._interval = interval
        self._count = 0
        self._last_log = time.time()

    def record(self, domain: str) -> None:
        self._count += 1
        now = time.time()
        if now - self._last_log >= self._interval:
            log.info("skipped out-of-scope lines", count=self._count, last_domain=domain)
            self._count = 0
            self._last_log = now


def partition_key(domain: str, line: str) -> str | None:
    """Key "domain|IP client" lấy từ trường đầu dòng (Nginx $remote_addr, Apache %h).

    Log của một domain nhờ vậy chia đều các partition, nhiều instance C2 xử lý song song được,
    trong khi mọi dòng của cùng một client vẫn vào một partition (giữ thứ tự). Trường đầu không
    phải IP (log JSON, %h là hostname) -> None: bus dùng key = domain như trước.
    """
    fields = line.split(maxsplit=1)
    try:
        client = str(ipaddress.ip_address(fields[0])) if fields else None
    except ValueError:
        client = None
    return bus_mod.client_key(domain, client) if client else None


def _publish_record(record: tailer_mod.LineRecord, bus: bus_mod.EventBus,
                    scope: scope_mod.ScopeClient, skips: _SkipTracker) -> None:
    if not scope.allowed(record.domain):
        skips.record(record.domain)
        return
    data = {
        "raw": record.line,
        "log_source": record.log_source,
        "log_path": record.log_path,
        "collected_at": envelope_mod.now_rfc3339(),
    }
    try:
        bus.publish("log.raw.ingested", domain=record.domain, data=data,
                    request_id=str(uuid.uuid4()), key=partition_key(record.domain, record.line))
    except Exception as exc:  # broker trục trặc: log tường minh, không dừng worker
        log.error("publish failed", domain=record.domain, error=str(exc))


def publish_cycle(records: list[tailer_mod.LineRecord], bus: bus_mod.EventBus,
                  scope: scope_mod.ScopeClient, skips: _SkipTracker) -> None:
    """Gửi mọi dòng mới của một vòng poll, chờ broker xác nhận một lần thay vì sau từng dòng.
    Broker không xác nhận: ghi log lỗi kèm số dòng của vòng, worker chạy tiếp như trước."""
    if not records:
        return
    try:
        with bus.deferred_delivery("log.raw.ingested"):
            for record in records:
                _publish_record(record, bus, scope, skips)
    except bus_mod.DeliveryError as exc:
        log.error("publish failed", lines=len(records), error=str(exc))


def run_loop(tailer: tailer_mod.LogTailer, bus: bus_mod.EventBus,
             scope: scope_mod.ScopeClient) -> None:
    skips = _SkipTracker()
    while True:
        try:
            publish_cycle(tailer.poll(), bus, scope, skips)
        except Exception as exc:  # lỗi bất ngờ trong một vòng: log rồi tiếp tục
            log.error("poll cycle failed", error=str(exc))
        # Vừa chờ giữa hai vòng poll, vừa phục vụ heartbeat để broker không đóng kết nối.
        bus.process_events(_POLL_INTERVAL_SECONDS)


def main() -> None:
    log.set_service(SOURCE)
    health.start_heartbeat()
    log_dir = config.env("LOG_DIR", "/var/log/waf")
    read_existing = (config.env("C1_READ_EXISTING", "false") or "").lower() == "true"
    domain_override = config.env("C1_DOMAIN", "") or ""
    bus = bus_mod.EventBus(config.env("EVENT_BUS_URL", required=True), source=SOURCE).connect()
    scope = scope_mod.ScopeClient(config.env("SCOPE_SERVICE_URL", required=True))
    tailer = tailer_mod.LogTailer(log_dir, read_existing=read_existing,
                                  domain_override=domain_override)
    log.info("c1-ingestion started", log_dir=log_dir, read_existing=read_existing,
             domain_override=domain_override or "(from filename)")
    run_loop(tailer, bus, scope)


if __name__ == "__main__":
    main()
