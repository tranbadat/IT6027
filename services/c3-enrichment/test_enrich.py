"""Unit test cho enrich (không cần RabbitMQ hay GeoIP DB thật). Chạy: python test_enrich.py"""
import geoip2.errors

import enrich as enrich_mod


# ---- Rate counter -----------------------------------------------------------

def test_counts_within_window():
    counter = enrich_mod.RequestRateCounter()
    assert counter.record("shop.local", "1.2.3.4", now=1000.0) == 1
    assert counter.record("shop.local", "1.2.3.4", now=1001.0) == 2
    assert counter.record("shop.local", "1.2.3.4", now=1002.0) == 3


def test_evicts_outside_window():
    counter = enrich_mod.RequestRateCounter(window_seconds=60)
    counter.record("shop.local", "1.2.3.4", now=1000.0)
    counter.record("shop.local", "1.2.3.4", now=1030.0)
    # Tại t=1091: cả mốc 1000 (91s) và 1030 (61s) đều quá 60s -> chỉ còn mốc mới.
    assert counter.record("shop.local", "1.2.3.4", now=1091.0) == 1


def test_separates_by_domain_and_ip():
    counter = enrich_mod.RequestRateCounter()
    counter.record("a.local", "1.1.1.1", now=1.0)
    counter.record("a.local", "1.1.1.1", now=2.0)
    assert counter.record("b.local", "1.1.1.1", now=3.0) == 1
    assert counter.record("a.local", "2.2.2.2", now=3.0) == 1


def test_capacity_eviction_lru():
    counter = enrich_mod.RequestRateCounter(max_ips=2)
    counter.record("d", "1", now=1.0)
    counter.record("d", "2", now=2.0)
    counter.record("d", "3", now=3.0)  # vượt sức chứa -> loại IP cũ nhất ("1")
    assert counter.record("d", "1", now=4.0) == 1


def test_per_ip_timestamp_limit():
    counter = enrich_mod.RequestRateCounter(max_per_ip=5)
    count = 0
    for i in range(20):
        count = counter.record("d", "9.9.9.9", now=1000.0 + i)
    assert count == 5


# ---- GeoIP ------------------------------------------------------------------

class _FakeCountry:
    def __init__(self, iso, name):
        self.iso_code = iso
        self.name = name


class _FakeCity:
    def __init__(self, name):
        self.name = name


class _FakeCityResponse:
    def __init__(self, iso, country, city):
        self.country = _FakeCountry(iso, country)
        self.city = _FakeCity(city)


class _FakeReader:
    def city(self, ip):
        if ip == "8.8.8.8":
            return _FakeCityResponse("US", "United States", "Mountain View")
        raise geoip2.errors.AddressNotFoundError(ip)


def test_geo_maps_fields():
    resolver = enrich_mod.GeoResolver("fake", reader=_FakeReader())
    assert resolver.lookup("8.8.8.8") == {
        "country_iso": "US",
        "country": "United States",
        "city": "Mountain View",
    }


def test_geo_private_ip_returns_none():
    resolver = enrich_mod.GeoResolver("fake", reader=_FakeReader())
    assert resolver.lookup("10.0.0.5") is None
    assert resolver.lookup("127.0.0.1") is None
    assert resolver.lookup("172.23.0.1") is None


def test_geo_unknown_ip_returns_none():
    resolver = enrich_mod.GeoResolver("fake", reader=_FakeReader())
    assert resolver.lookup("9.9.9.9") is None


def test_geo_missing_db_returns_none():
    resolver = enrich_mod.GeoResolver("/no/such/file.mmdb")
    assert resolver.lookup("8.8.8.8") is None


# ---- Session & build --------------------------------------------------------

def test_resolve_session_from_data():
    assert enrich_mod.resolve_session_id({"session_id": "abc", "src_ip": "1.2.3.4"}) == "abc"


def test_resolve_session_falls_back_to_ip():
    assert enrich_mod.resolve_session_id({"session_id": "", "src_ip": "1.2.3.4"}) == "ip-1.2.3.4"


def test_build_enriched_does_not_mutate_input():
    normalized = {"src_ip": "1.2.3.4", "path": "/x", "session_id": ""}
    out = enrich_mod.build_enriched_data(normalized, None, 5, "ip-1.2.3.4")
    assert out["geo"] is None
    assert out["req_count_1m"] == 5
    assert out["session_id"] == "ip-1.2.3.4"
    assert out["src_ip"] == "1.2.3.4"
    # dict gốc phải nguyên vẹn
    assert "geo" not in normalized
    assert normalized["session_id"] == ""


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("PASS", _name)
