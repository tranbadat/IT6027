"""Test tích hợp C1 -> C2 -> C3 trên InMemoryBus, ghi log LIVE vào file (không nạp file tĩnh)."""
import json
import threading
import time

import pytest

from wafcollect.c1_ingestion.config import C1Config, SourceConfig, load_config
from wafcollect.c1_ingestion.service import IngestionService
from wafcollect.c1_ingestion.tailer import StateStore
from wafcollect.c2_parsing.service import ParsingService
from wafcollect.c3_enrichment.geoip import GeoResolver
from wafcollect.c3_enrichment.service import EnrichmentService
from wafcollect.c3_enrichment.sessions import SessionTracker
from wafcollect.common import events
from wafcollect.common.bus import BusUnavailable, InMemoryBus
from wafcollect.common.scope import Decision, ScopeChecker, StaticScope


def nginx_line(ip="203.0.113.9", path="/", status=200, ck="-", ts="21/Sep/2026:10:15:30 +0700"):
    return (f'{ip} - - [{ts}] "GET {path} HTTP/1.1" {status} 512 "-" "Mozilla/5.0" rt=0.005 ck="{ck}"\n')


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Harness:
    def __init__(self, tmp_path, scope, sources, bus=None, state_path=None):
        self.bus = bus or InMemoryBus()
        cfg = C1Config(sources=sources, poll_interval=0.02)
        self.c1 = IngestionService(cfg, self.bus, scope, StateStore(state_path))
        self.c2 = ParsingService(self.bus, scope)
        self.c3 = EnrichmentService(self.bus, scope, GeoResolver(), SessionTracker())
        self.c2.register(); self.c3.register()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.c1.run, args=(self.stop,), daemon=True)

    def start(self):
        self.c1.start(self.stop)   # mở file đồng bộ: chốt vị trí "end" trước khi test ghi log
        self.thread.start(); return self

    def shutdown(self):
        self.stop.set(); self.thread.join(5)


def src(tmp_path, name="shop", domain="shop.example.com", start_at="end", **kw):
    p = tmp_path / f"{name}.log"
    p.touch()
    return SourceConfig(name=name, type="file", path=str(p), domain=domain, server="nginx", start_at=start_at, **kw), p


def write(p, text):
    with open(p, "a") as f:
        f.write(text)


def test_live_pipeline_all_events_match_schema(tmp_path, validate):
    s, p = src(tmp_path)
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s]).start()
    try:
        write(p, nginx_line(path="/", ck="PHPSESSID=abc"))
        write(p, nginx_line(path="/item?id=1%27%20OR%20%271%27%3D%271", ck="PHPSESSID=abc", status=500))
        write(p, nginx_line(ip="10.0.0.7", path="/x", status=404))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 3)
    finally:
        h.shutdown()

    raw, norm, enr = (h.bus.of(e) for e in (events.LOG_RAW_INGESTED, events.LOG_NORMALIZED, events.LOG_ENRICHED))
    assert len(raw) == len(norm) == len(enr) == 3
    for e in raw: validate(e, "log_raw_ingested")
    for e in norm: validate(e, "log_normalized")
    for e in enr: validate(e, "log_enriched")

    # request_id giữ nguyên xuyên suốt và khớp từng cặp
    assert [e["request_id"] for e in raw] == [e["request_id"] for e in norm] == [e["request_id"] for e in enr]

    sqli = enr[1]["data"]
    assert sqli["query_string"] == "id=1' OR '1'='1"
    assert sqli["status"] == 500
    # hai request đầu cùng IP + cookie -> cùng phiên; request thứ 3 khác IP -> phiên khác
    assert enr[0]["session_id"] == enr[1]["session_id"] != enr[2]["session_id"]
    assert enr[1]["data"]["session"]["request_count"] == 2
    assert enr[1]["data"]["frequency"]["requests_60s"] == 2
    assert enr[2]["data"]["geo"]["status"] == "non_public"
    # cookie thô không được lọt xuống downstream ở BẤT KỲ trường nào (kể cả raw_line)
    assert all("cookie" not in e["data"]["headers"] for e in enr)
    assert "PHPSESSID=abc" not in json.dumps(enr)
    assert 'ck="[redacted]"' in enr[0]["data"]["raw_line"]
    assert enr[0]["data"]["session"]["cookie_present"] is True


def test_domain_out_of_scope_is_never_processed(tmp_path):
    s_ok, p_ok = src(tmp_path, "ok", "shop.example.com")
    s_bad, p_bad = src(tmp_path, "bad", "secret.internal.com")
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s_ok, s_bad]).start()
    try:
        write(p_bad, nginx_line(path="/should-not-pass"))
        write(p_ok, nginx_line(path="/allowed"))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 1)
        time.sleep(0.3)
    finally:
        h.shutdown()
    every = json.dumps(h.bus.published)
    assert "should-not-pass" not in every and "secret.internal.com" not in every
    assert h.c1.stats.snapshot()["dropped_out_of_scope"] == 1


def test_c2_and_c3_also_enforce_scope():
    """Dù C1 lỡ cho lọt, C2/C3 vẫn tự kiểm tra scope (ràng buộc xuyên suốt)."""
    bus = InMemoryBus()
    scope = StaticScope({"good.com"})
    ParsingService(bus, scope).register()
    EnrichmentService(bus, scope, GeoResolver(), SessionTracker()).register()
    bad_raw = events.make_event(events.LOG_RAW_INGESTED, {
        "raw_line": nginx_line().strip(), "domain": "bad.com",
        "source": {"name": "s", "type": "file", "server": "nginx"},
        "collected_at": events.utc_now_iso(), "truncated": False, "line_count": 1}, "r1")
    bus.publish(bad_raw)
    assert len(bus.published) == 1                                # chỉ có event gốc
    bad_norm = events.make_event(events.LOG_NORMALIZED, {"domain": "bad.com", "client_ip": "1.1.1.1",
                                                          "status": 200}, "r2")
    bus.publish(bad_norm)
    assert bus.of(events.LOG_ENRICHED) == []


class FlakyScope(ScopeChecker):
    def __init__(self, fail_first): self.fail, self.calls = fail_first, 0
    def check(self, domain):
        self.calls += 1
        return Decision.UNAVAILABLE if self.calls <= self.fail else Decision.ALLOWED


def test_scope_outage_pauses_but_loses_nothing(tmp_path):
    s, p = src(tmp_path)
    h = Harness(tmp_path, FlakyScope(fail_first=4), [s]).start()
    try:
        write(p, "".join(nginx_line(path=f"/p{i}") for i in range(5)))
        assert wait_for(lambda: len(h.bus.of(events.LOG_RAW_INGESTED)) == 5, timeout=10)
    finally:
        h.shutdown()
    paths = [json.loads(json.dumps(e))["data"]["raw_line"].split()[6] for e in h.bus.of(events.LOG_RAW_INGESTED)]
    assert paths == [f"/p{i}" for i in range(5)]                  # đủ và đúng thứ tự


class FlakyBus(InMemoryBus):
    def __init__(self, fail_first): super().__init__(); self.fail = fail_first
    def publish(self, env):
        if env["event"] == events.LOG_RAW_INGESTED and self.fail > 0:
            self.fail -= 1
            raise BusUnavailable("down")
        super().publish(env)


def test_bus_outage_retries_without_duplicates_or_loss(tmp_path):
    s, p = src(tmp_path)
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s], bus=FlakyBus(3)).start()
    try:
        write(p, "".join(nginx_line(path=f"/q{i}") for i in range(4)))
        assert wait_for(lambda: len(h.bus.of(events.LOG_RAW_INGESTED)) == 4, timeout=10)
        time.sleep(0.3)
    finally:
        h.shutdown()
    assert len(h.bus.of(events.LOG_RAW_INGESTED)) == 4


def test_restart_resumes_without_loss_or_duplicates(tmp_path):
    s, p = src(tmp_path, start_at="beginning")
    state = str(tmp_path / "state.json")
    write(p, nginx_line(path="/a") + nginx_line(path="/b"))
    h1 = Harness(tmp_path, StaticScope({"shop.example.com"}), [s], state_path=state).start()
    assert wait_for(lambda: len(h1.bus.of(events.LOG_RAW_INGESTED)) == 2)
    h1.shutdown()

    write(p, nginx_line(path="/c"))          # ghi trong lúc collector đang tắt
    h2 = Harness(tmp_path, StaticScope({"shop.example.com"}), [s], state_path=state).start()
    try:
        assert wait_for(lambda: len(h2.bus.of(events.LOG_RAW_INGESTED)) == 1)
        time.sleep(0.3)
    finally:
        h2.shutdown()
    assert len(h2.bus.of(events.LOG_RAW_INGESTED)) == 1
    assert "/c" in h2.bus.of(events.LOG_RAW_INGESTED)[0]["data"]["raw_line"]


def test_rotation_during_live_collection(tmp_path):
    import os
    s, p = src(tmp_path)
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s]).start()
    try:
        write(p, nginx_line(path="/before"))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 1)
        write(p, nginx_line(path="/last-old"))
        os.rename(p, str(p) + ".1")
        write(p, nginx_line(path="/first-new"))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 3)
    finally:
        h.shutdown()
    got = [e["data"]["path"] for e in h.bus.of(events.LOG_ENRICHED)]
    assert got == ["/before", "/last-old", "/first-new"]


def test_garbage_lines_go_to_parse_failed_and_pipeline_continues(tmp_path, validate):
    s, p = src(tmp_path)
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s]).start()
    try:
        write(p, "this is not an access log line\n")
        write(p, '1.2.3.4 - - [21/Sep/2026:10:15:30 +0700] "\\x16\\x03\\x01" 400 0 "-" "-"\n')
        write(p, nginx_line(path="/fine"))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 2)
    finally:
        h.shutdown()
    (failed,) = h.bus.of(events.LOG_PARSE_FAILED)
    validate(failed, "log_parse_failed")
    assert failed["data"]["error"] == "unrecognized_format"
    partial = h.bus.of(events.LOG_ENRICHED)[0]
    validate(partial, "log_enriched")
    assert partial["data"]["parse_status"] == "partial" and partial["data"]["method"] is None


def test_multiline_source_is_joined(tmp_path):
    s, p = src(tmp_path, multiline={"enabled": True, "flush_after": 0.2})
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s]).start()
    try:
        write(p, nginx_line(path="/one").rstrip("\n") + "\n   extra continuation\n")
        write(p, nginx_line(path="/two"))
        assert wait_for(lambda: len(h.bus.of(events.LOG_RAW_INGESTED)) == 2)
    finally:
        h.shutdown()
    first = h.bus.of(events.LOG_RAW_INGESTED)[0]["data"]
    assert first["line_count"] == 2 and "extra continuation" in first["raw_line"]
    assert h.bus.of(events.LOG_NORMALIZED)[0]["data"]["path"] == "/one"


def test_syslog_source_maps_tag_to_domain(tmp_path, validate):
    import socket
    from wafcollect.c1_ingestion.syslog_receiver import UdpSyslogReceiver  # noqa: F401
    s = SourceConfig(name="sys", type="syslog", protocol="udp", listen="127.0.0.1:0", server="nginx",
                     tag_domains={"shop_example": "shop.example.com", "other": "secret.com"})
    h = Harness(tmp_path, StaticScope({"shop.example.com"}), [s]).start()
    try:
        assert wait_for(lambda: h.c1._syslog_receivers)
        port = h.c1._syslog_receivers[0].bound_port
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.sendto(f"<134>Sep 21 10:15:30 web01 other: {nginx_line(path='/leak').strip()}".encode(), ("127.0.0.1", port))
        u.sendto(f"<134>Sep 21 10:15:31 web01 shop_example: {nginx_line(path='/ok').strip()}".encode(), ("127.0.0.1", port))
        assert wait_for(lambda: len(h.bus.of(events.LOG_ENRICHED)) == 1)
    finally:
        h.shutdown()
    (e,) = h.bus.of(events.LOG_ENRICHED)
    validate(e, "log_enriched")
    assert e["data"]["domain"] == "shop.example.com" and e["data"]["path"] == "/ok"
    assert e["data"]["source"]["type"] == "syslog" and e["data"]["source"]["tag"] == "shop_example"


def test_config_loader_validation(tmp_path):
    good = tmp_path / "c.yaml"
    good.write_text("sources:\n  - {name: a, type: file, path: /x.log, domain: d.com, server: nginx}\n")
    assert load_config(str(good)).sources[0].name == "a"
    for bad in ["sources: []", "sources:\n  - {name: a, type: file, path: /x}",
                "sources:\n  - {name: a, type: ftp}",
                "sources:\n  - {name: a, type: file, path: /x, domain: d}\n  - {name: a, type: file, path: /y, domain: d}"]:
        f = tmp_path / "bad.yaml"
        f.write_text(bad)
        with pytest.raises(ValueError):
            load_config(str(f))


def test_redaction_can_be_disabled():
    bus = InMemoryBus()
    EnrichmentService(bus, StaticScope({"d.com"}), GeoResolver(), SessionTracker(), redact_cookie=False).register()
    norm = events.make_event(events.LOG_NORMALIZED, {
        "domain": "d.com", "client_ip": "1.1.1.1", "status": 200, "request_time": "2026-09-21T03:00:00Z",
        "headers": {"referer": None, "user_agent": None, "cookie": "PHPSESSID=zzz"},
        "raw_line": '... ck="PHPSESSID=zzz"'}, "r1")
    bus.publish(norm)
    out = bus.of(events.LOG_ENRICHED)[0]["data"]
    assert out["headers"]["cookie"] == "PHPSESSID=zzz" and "zzz" in out["raw_line"]
