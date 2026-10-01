"""C1 - Log Ingestion: đọc log liên tục -> publish log.raw.ingested.

Luồng:
    [thread/nguồn] --RawItem--> queue có giới hạn --> [luồng chính] scope check -> publish -> lưu offset

Đảm bảo:
  - at-least-once cho nguồn file: offset chỉ được lưu SAU khi publish thành công, nên crash/restart
    có thể phát lại vài dòng nhưng không làm mất dòng.
  - Không xử lý domain ngoài phạm vi: DENIED -> bỏ dòng (đếm + cảnh báo); UNAVAILABLE -> giữ nguyên
    dòng và thử lại (không bỏ dữ liệu, không xử lý khi chưa được xác nhận).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

from ..common import events
from ..common.bus import EventBus, RetryLater
from ..common.scope import Decision, ScopeChecker
from ..common.stats import Stats
from .config import C1Config, SourceConfig
from .syslog_receiver import SyslogMessage, TcpSyslogReceiver, UdpSyslogReceiver
from .tailer import FileTailer, Line, MultilineJoiner, StateStore

log = logging.getLogger("c1")


@dataclass
class RawItem:
    source: SourceConfig
    text: str
    truncated: bool = False
    line_count: int = 1
    domain: str = ""
    # chỉ có ở nguồn file, để lưu state sau khi publish
    inode: Optional[int] = None
    offset_after: Optional[int] = None
    # chỉ có ở nguồn syslog
    syslog_host: Optional[str] = None
    syslog_tag: Optional[str] = None
    peer: Optional[str] = None


class IngestionService:
    def __init__(
        self,
        config: C1Config,
        bus: EventBus,
        scope: ScopeChecker,
        state: StateStore,
        stats: Optional[Stats] = None,
    ) -> None:
        self.cfg = config
        self.bus = bus
        self.scope = scope
        self.state = state
        self.stats = stats or Stats("c1")
        self.q: "queue.Queue[RawItem]" = queue.Queue(maxsize=config.queue_size)
        self._threads: list[threading.Thread] = []
        self._syslog_receivers: list = []
        self._warned: dict[str, float] = {}
        self._started = False

    # ------------------------------------------------------------ nguồn file
    def _file_worker(self, src: SourceConfig, stop: threading.Event, tailer: FileTailer) -> None:
        ml_cfg = src.multiline or {}
        joiner = None
        if ml_cfg.get("enabled"):
            kw = {k: ml_cfg[k] for k in ("start_pattern", "flush_after", "max_lines") if k in ml_cfg}
            joiner = MultilineJoiner(**kw)

        def emit(line: Line) -> bool:
            item = RawItem(
                source=src, text=line.text, truncated=line.truncated, line_count=line.line_count,
                domain=src.domain, inode=line.inode, offset_after=line.offset_after,
            )
            while not stop.is_set():
                try:
                    self.q.put(item, timeout=0.5)  # queue đầy -> dừng đọc file (backpressure)
                    return True
                except queue.Full:
                    continue
            return False

        try:
            while not stop.is_set():
                lines = tailer.poll()
                if joiner:
                    joined: list[Line] = []
                    for ln in lines:
                        joined.extend(joiner.feed(ln))
                    joined.extend(joiner.flush_if_stale())
                    lines = joined
                for ln in lines:
                    if not ln.text.strip():
                        continue
                    if not emit(ln):
                        return
                if not lines:
                    stop.wait(self.cfg.poll_interval)
        finally:
            if joiner:  # tắt êm: đẩy nốt bản ghi đang gộp dở (emit() đã dừng vì stop được set)
                for ln in joiner.flush():
                    try:
                        self.q.put_nowait(RawItem(
                            source=src, text=ln.text, truncated=ln.truncated, line_count=ln.line_count,
                            domain=src.domain, inode=ln.inode, offset_after=ln.offset_after))
                    except queue.Full:
                        pass
            tailer.close()

    # ------------------------------------------------------------ nguồn syslog
    def _syslog_worker(self, src: SourceConfig, stop: threading.Event) -> None:
        inbox: "queue.Queue[tuple[SyslogMessage, str]]" = queue.Queue(maxsize=self.cfg.queue_size)
        rx_cls = UdpSyslogReceiver if src.protocol == "udp" else TcpSyslogReceiver
        rx = rx_cls(src.listen, inbox, stop)
        self._syslog_receivers.append(rx)
        rx.start()
        reported_drops = 0
        while not stop.is_set():
            dropped = getattr(rx, "dropped", 0)  # UDP không có backpressure: hàng đợi đầy thì datagram bị bỏ
            if dropped > reported_drops:
                self.stats.incr("dropped_udp_queue_full", dropped - reported_drops)
                reported_drops = dropped
            try:
                msg, peer = inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            domain = (
                src.tag_domains.get(msg.tag or "")
                or src.host_domains.get(msg.host or "")
                or src.domain
            )
            item = RawItem(
                source=src, text=msg.message, domain=domain,
                syslog_host=msg.host, syslog_tag=msg.tag, peer=peer,
            )
            try:
                self.q.put(item, timeout=1.0)
            except queue.Full:
                self.stats.incr("dropped_queue_full")

    # ------------------------------------------------------------ luồng chính
    def start(self, stop: threading.Event) -> None:
        """Mở nguồn log và chạy thread đọc. Idempotent; run() tự gọi nếu chưa ai gọi."""
        if self._started:
            return
        self._started = True
        for src in self.cfg.sources:
            if src.type == "file":
                tailer = FileTailer(src.path, start_at=src.start_at, resume=self.state.get(src.name),
                                    max_line_bytes=src.max_line_bytes)
                tailer.open()  # chốt vị trí bắt đầu ở luồng chính, trước khi thread nguồn chạy
                target, args = self._file_worker, (src, stop, tailer)
            else:
                target, args = self._syslog_worker, (src, stop)
            t = threading.Thread(target=target, args=args, name=f"c1-{src.name}", daemon=True)
            t.start()
            self._threads.append(t)

    def _envelope(self, item: RawItem) -> dict:
        source = {"name": item.source.name, "type": item.source.type, "server": item.source.server}
        if item.source.type == "file":
            source["path"] = item.source.path
        else:
            source.update({"protocol": item.source.protocol, "host": item.syslog_host,
                           "tag": item.syslog_tag, "peer": item.peer})
        data = {
            "raw_line": item.text,
            "domain": item.domain,
            "source": source,
            "collected_at": events.utc_now_iso(),
            "truncated": item.truncated,
            "line_count": item.line_count,
        }
        return events.make_event(events.LOG_RAW_INGESTED, data, request_id=events.new_request_id())

    def _warn_once(self, key: str, msg: str, every: float = 60.0) -> None:
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= every:
            self._warned[key] = now
            log.warning(msg)

    def _process(self, item: RawItem, stop: threading.Event) -> None:
        backoff = 0.5
        while not stop.is_set():
            decision = self.scope.check(item.domain)
            if decision is Decision.DENIED:
                self.stats.incr("dropped_out_of_scope")
                self._warn_once(f"deny:{item.domain}", f"domain {item.domain!r} ngoài phạm vi: bỏ qua log")
                self._commit(item)
                return
            if decision is Decision.UNAVAILABLE:
                self._warn_once("scope-down", "Scope Service không khả dụng: tạm dừng xử lý, sẽ thử lại")
                stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            try:
                self.bus.publish(self._envelope(item))
            except RetryLater as e:
                self._warn_once("bus-down", f"không publish được ({e}); thử lại")
                self.stats.incr("publish_retry")
                stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            self.stats.incr("published")
            self._commit(item)
            return

    def _commit(self, item: RawItem) -> None:
        if item.inode is not None and item.offset_after is not None:
            self.state.update(item.source.name, item.inode, item.offset_after)

    def run(self, stop: threading.Event) -> None:
        self.start(stop)
        try:
            while not stop.is_set():
                try:
                    item = self.q.get(timeout=0.25)
                except queue.Empty:
                    self.state.flush()
                    continue
                self._process(item, stop)
                self.state.flush()
        finally:
            # tắt êm: xử lý nốt phần còn trong queue nếu có thể, rồi ghi state lần cuối
            self._drain_remaining()
            self.state.flush(force=True)

    def _drain_remaining(self) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            decision = self.scope.check(item.domain)
            if decision is Decision.ALLOWED:
                try:
                    self.bus.publish(self._envelope(item))
                    self._commit(item)
                except RetryLater:
                    return
            elif decision is Decision.DENIED:
                self._commit(item)
