from types import SimpleNamespace as NS

from wafcollect.c3_enrichment.geoip import GeoResolver
from wafcollect.c3_enrichment.sessions import SessionTracker, extract_session_cookie

T0 = 1_800_000_000.0


# ---------- cookie
def test_extract_session_cookie_variants():
    assert extract_session_cookie(None) is None
    assert extract_session_cookie("abc123") == "abc123"                      # server chỉ log $cookie_sessionid
    assert extract_session_cookie("_ga=GA1; PHPSESSID=xyz; theme=dark") == "xyz"
    assert extract_session_cookie("_ga=GA1; theme=dark") is None              # cookie theo dõi không dùng làm phiên
    assert extract_session_cookie("SessionId=q") == "q"                       # không phân biệt hoa thường


# ---------- phiên
def test_same_ip_same_cookie_same_session():
    t = SessionTracker()
    a = t.observe("d.com", "1.1.1.1", "ck", T0, 200)
    b = t.observe("d.com", "1.1.1.1", "ck", T0 + 5, 200)
    assert a["session"]["id"] == b["session"]["id"]
    assert (a["session"]["is_new"], b["session"]["is_new"]) == (True, False)
    assert b["session"]["request_count"] == 2 and b["session"]["duration_s"] == 5.0
    assert b["session"]["key_basis"] == "ip+cookie"


def test_different_cookie_or_ip_or_domain_gives_different_session():
    t = SessionTracker()
    ids = {
        t.observe("d.com", "1.1.1.1", "c1", T0, 200)["session"]["id"],
        t.observe("d.com", "1.1.1.1", "c2", T0, 200)["session"]["id"],
        t.observe("d.com", "2.2.2.2", "c1", T0, 200)["session"]["id"],
        t.observe("e.com", "1.1.1.1", "c1", T0, 200)["session"]["id"],
        t.observe("d.com", "1.1.1.1", None, T0, 200)["session"]["id"],
    }
    assert len(ids) == 5


def test_session_expires_after_inactivity():
    t = SessionTracker(session_timeout=60)
    a = t.observe("d.com", "1.1.1.1", None, T0, 200)
    b = t.observe("d.com", "1.1.1.1", None, T0 + 61, 200)
    assert a["session"]["id"] != b["session"]["id"] and b["session"]["is_new"]
    assert b["session"]["request_count"] == 1
    assert b["session"]["key_basis"] == "ip"


def test_session_ids_are_deterministic_for_replay():
    a = SessionTracker().observe("d.com", "1.1.1.1", "c", T0, 200)["session"]["id"]
    b = SessionTracker().observe("d.com", "1.1.1.1", "c", T0, 200)["session"]["id"]
    assert a == b


# ---------- tần suất
def test_frequency_windows_and_error_ratio():
    t = SessionTracker()
    for i in range(30):                         # 30 request trong 30 giây, 10 cái lỗi
        f = t.observe("d.com", "9.9.9.9", None, T0 + i, 404 if i % 3 == 0 else 200)["frequency"]
    assert f["requests_60s"] == 30
    assert f["requests_10s"] == 10
    assert f["errors_4xx_60s"] == 10 and f["errors_5xx_60s"] == 0
    assert f["error_ratio_60s"] == round(10 / 30, 4)


def test_window_slides_and_old_requests_fall_out():
    t = SessionTracker()
    for i in range(20):
        t.observe("d.com", "9.9.9.9", None, T0 + i, 500)
    f = t.observe("d.com", "9.9.9.9", None, T0 + 200, 200)["frequency"]
    assert f["requests_60s"] == 1 and f["errors_5xx_60s"] == 0


def test_frequency_is_per_ip_and_domain():
    t = SessionTracker()
    for i in range(5):
        t.observe("d.com", "1.1.1.1", None, T0 + i, 200)
    f = t.observe("d.com", "2.2.2.2", None, T0 + 6, 200)["frequency"]
    assert f["requests_60s"] == 1


def test_out_of_order_events_are_counted_and_too_late_ignored():
    t = SessionTracker()
    t.observe("d.com", "1.1.1.1", None, T0 + 30, 200)
    f = t.observe("d.com", "1.1.1.1", None, T0 + 10, 200)["frequency"]       # muộn 20s, vẫn trong cửa sổ
    assert f["requests_60s"] == 2
    f = t.observe("d.com", "1.1.1.1", None, T0 - 500, 200)["frequency"]      # muộn quá cửa sổ: không tính
    assert f["requests_60s"] == 2


# ---------- giới hạn bộ nhớ
def test_memory_is_bounded():
    t = SessionTracker(max_sessions=100, max_ips=50, session_timeout=1e9)
    for i in range(5000):
        t.observe("d.com", f"10.0.{i // 250}.{i % 250}", f"c{i}", T0 + i, 200)
    sz = t.sizes()
    assert sz["sessions"] <= 100 and sz["ips"] <= 50


def test_idle_sessions_are_evicted_by_time():
    t = SessionTracker(session_timeout=10, max_sessions=10_000)
    for i in range(100):
        t.observe("d.com", f"1.1.1.{i}", None, T0, 200)
    t.observe("d.com", "8.8.8.8", None, T0 + 100, 200)
    assert t.sizes()["sessions"] == 1


# ---------- GeoIP
class FakeReader:
    kind = "city"

    def city(self, ip):
        if ip == "8.8.4.4":
            raise type("AddressNotFoundError", (Exception,), {})()
        return NS(country=NS(iso_code="VN", name="Vietnam"), city=NS(name="Hanoi"),
                  location=NS(latitude=21.03, longitude=105.85))


def test_geo_ok_and_cache():
    g = GeoResolver(reader=FakeReader())
    r = g.lookup("113.160.0.1")
    assert r == {"status": "ok", "country_code": "VN", "country_name": "Vietnam",
                 "city": "Hanoi", "latitude": 21.03, "longitude": 105.85}
    assert g.lookup("113.160.0.1") is g._cache["113.160.0.1"]


def test_geo_special_cases():
    g = GeoResolver(reader=FakeReader())
    assert g.lookup("10.0.0.5")["status"] == "non_public"
    assert g.lookup("127.0.0.1")["status"] == "non_public"
    assert g.lookup("not-an-ip")["status"] == "invalid"
    assert g.lookup("8.8.4.4")["status"] == "not_found"


def test_geo_disabled_without_database_and_bad_path():
    assert GeoResolver().lookup("8.8.8.8") == {"status": "disabled"}
    g = GeoResolver(db_path="/does/not/exist.mmdb")   # không được crash
    assert not g.enabled and g.lookup("8.8.8.8")["status"] == "disabled"


def test_geo_cache_is_bounded():
    g = GeoResolver(reader=FakeReader(), cache_size=10)
    for i in range(1, 100):
        g.lookup(f"113.160.{i}.1")
    assert len(g._cache) == 10
