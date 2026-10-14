"""Unit test cho key phân vùng và gửi theo vòng poll của log.raw.ingested (không cần Kafka).
Chạy: python test_ingest.py"""
import contextlib

from common import bus as bus_mod

import main as main_mod
import tailer as tailer_mod

_NGINX_LINE = ('203.0.113.7 - - [14/Oct/2026:08:00:00 +0000] "GET / HTTP/1.1" 200 15 "-" "Mozilla/5.0" '
               '"lab.local" rt=0.001 sid="-"')


class _FakeBus:
    def __init__(self, fail_delivery=False):
        self.published = []
        self.journal = []
        self._fail_delivery = fail_delivery

    def publish(self, event, domain, data, request_id=None, session_id=None, key=None):
        self.published.append({"event": event, "domain": domain, "key": key})
        self.journal.append("publish")

    @contextlib.contextmanager
    def deferred_delivery(self, target):
        self.journal.append("begin")
        yield
        self.journal.append("wait-for-broker")
        if self._fail_delivery:
            raise bus_mod.DeliveryError(f"publish {target} failed: broker down")


def _record(ip="203.0.113.7"):
    return tailer_mod.LineRecord("lab.local", "nginx", "/var/log/waf/nginx/lab.local.access.log",
                                 _NGINX_LINE.replace("203.0.113.7", ip))


class _AllowAll:
    def allowed(self, domain):
        return True


def test_key_uses_client_ip_at_start_of_line():
    assert main_mod.partition_key("Lab.Local", _NGINX_LINE) == "lab.local|203.0.113.7"


def test_key_accepts_ipv6_client():
    line = '2001:db8::1 - - [14/Oct/2026:08:00:00 +0000] "GET / HTTP/1.1" 200 15'
    assert main_mod.partition_key("lab.local", line) == "lab.local|2001:db8::1"


def test_key_is_none_when_line_does_not_start_with_ip():
    # Bus quay về key = domain như trước: vẫn giữ thứ tự, chỉ không chia partition theo client.
    for line in ('{"remote_addr": "203.0.113.7"}', "crawler.example.com - - [x]", "", "   "):
        assert main_mod.partition_key("lab.local", line) is None, line


def test_publish_record_sends_client_key():
    bus = _FakeBus()
    record = tailer_mod.LineRecord("lab.local", "nginx", "/var/log/waf/nginx/lab.local.access.log", _NGINX_LINE)
    main_mod._publish_record(record, bus, _AllowAll(), main_mod._SkipTracker())
    assert bus.published == [{"event": "log.raw.ingested", "domain": "lab.local", "key": "lab.local|203.0.113.7"}], \
        bus.published


def test_publish_cycle_waits_for_broker_once_for_all_lines():
    bus = _FakeBus()
    main_mod.publish_cycle([_record(f"203.0.113.{i}") for i in range(1, 4)], bus, _AllowAll(),
                           main_mod._SkipTracker())
    assert bus.journal == ["begin", "publish", "publish", "publish", "wait-for-broker"], bus.journal


def test_publish_cycle_survives_delivery_failure():
    # Như trước: broker trục trặc thì ghi log lỗi, worker không dừng.
    bus = _FakeBus(fail_delivery=True)
    main_mod.publish_cycle([_record()], bus, _AllowAll(), main_mod._SkipTracker())
    assert bus.journal[-1] == "wait-for-broker", bus.journal


def test_publish_cycle_skips_empty_cycle():
    bus = _FakeBus()
    main_mod.publish_cycle([], bus, _AllowAll(), main_mod._SkipTracker())
    assert bus.journal == [], bus.journal


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("PASS", _name)
