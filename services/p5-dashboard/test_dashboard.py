"""Unit test P5: tổng hợp AggregateStore (traffic/top attack/top ip/alerts/stages).

Chạy: python /app/p5-dashboard/test_dashboard.py  (PYTHONPATH=/app để import common).
"""
import store as store_mod


def _enriched(domain, ts):
    return {"event": "log.enriched", "domain": domain, "event_id": "e", "timestamp": ts,
            "request_id": "r", "data": {"request_ts": ts, "src_ip": "1.1.1.1"}}


def _attack(domain, attack_type, src_ip):
    return {"event": "attack.detected", "domain": domain, "event_id": "e",
            "timestamp": "2026-09-16T09:00:00+00:00", "request_id": "r",
            "data": {"attack_type": attack_type, "src_ip": src_ip, "url": "/x"}}


def test_traffic_buckets_counted_per_minute():
    store = store_mod.AggregateStore()
    store.record(_enriched("shop.local", "2026-09-16T09:00:10+00:00"))
    store.record(_enriched("shop.local", "2026-09-16T09:00:40+00:00"))
    store.record(_enriched("shop.local", "2026-09-16T09:01:05+00:00"))
    traffic = {(t["domain"], t["minute"]): t["count"] for t in store.summary()["traffic"]}
    assert traffic[("shop.local", "2026-09-16T09:00")] == 2
    assert traffic[("shop.local", "2026-09-16T09:01")] == 1


def test_traffic_pruned_to_window():
    store = store_mod.AggregateStore()
    for i in range(store_mod.TRAFFIC_MINUTES + 10):
        store.record(_enriched("shop.local", f"2026-09-16T10:{i:02d}:00+00:00"))
    minutes = [t for t in store.summary()["traffic"] if t["domain"] == "shop.local"]
    assert len(minutes) == store_mod.TRAFFIC_MINUTES


def test_top_attacks_and_ips_ranked():
    store = store_mod.AggregateStore()
    for _ in range(3):
        store.record(_attack("shop.local", "sqli", "1.2.3.4"))
    store.record(_attack("shop.local", "xss", "9.9.9.9"))
    summary = store.summary()
    assert summary["top_attacks"][0] == {"attack_type": "sqli", "count": 3}
    assert summary["top_ips"][0] == {"ip": "1.2.3.4", "count": 3}


def test_alerts_history_newest_first_and_capped():
    store = store_mod.AggregateStore()
    for i in range(store_mod.ALERTS_CAP + 5):
        store.record({"event": "alert.triggered", "domain": "shop.local", "event_id": str(i),
                      "timestamp": f"2026-09-16T11:00:{i % 60:02d}+00:00", "request_id": "r",
                      "data": {"src_ip": "1.2.3.4", "attack_types": ["sqli"], "severity": "high",
                               "reason": f"r{i}"}})
    alerts = store.summary()["alerts"]
    assert len(alerts) == store_mod.ALERTS_CAP
    assert alerts[0]["reason"] == f"r{store_mod.ALERTS_CAP + 4}"  # mới nhất trước


def test_stage_completed_recorded():
    store = store_mod.AggregateStore()
    store.record({"event": "stage.completed", "domain": "shop.local", "event_id": "s1",
                  "timestamp": "2026-09-16T09:00:00+00:00",
                  "data": {"pipeline": "default", "stage": "enrich", "processed": 10,
                           "window_seconds": 60}})
    stages = store.summary()["stages"]
    assert stages[0]["stage"] == "enrich"
    assert stages[0]["processed"] == 10


def test_recent_feed_only_realtime_events():
    store = store_mod.AggregateStore()
    store.record(_attack("shop.local", "sqli", "1.2.3.4"))
    store.record({"event": "stage.completed", "domain": "shop.local", "event_id": "s1",
                  "timestamp": "2026-09-16T09:00:00+00:00", "data": {"stage": "x"}})
    events = [item["event"] for item in store.summary()["recent"]]
    assert "attack.detected" in events
    assert "stage.completed" not in events  # stage không vào feed realtime


def test_stream_item_keeps_event_and_request_ids():
    import consumer as consumer_mod

    class Hub:
        items = []

        def publish_threadsafe(self, item):
            self.items.append(item)

    hub = Hub()
    consumer_mod.build_handler(store_mod.AggregateStore(), hub)(_attack("shop.local", "sqli", "1.2.3.4"), "k")
    # Khung chi tiết realtime hiển thị mã yêu cầu: SSE phải mang event_id và request_id.
    assert hub.items[0]["event_id"] == "e" and hub.items[0]["request_id"] == "r", hub.items
    assert hub.items[0]["data"]["src_ip"] == "1.2.3.4"


# ---------- Liên kết ở trang Cài đặt (DASHBOARD_LINKS) ----------

def test_parse_links_reads_name_and_url_pairs():
    import app as app_mod
    links = app_mod.parse_links("Mailpit|https://mail.tranbadat.vn; Grafana | http://10.0.0.5:3000 ")
    assert links == [{"name": "Mailpit", "url": "https://mail.tranbadat.vn"},
                     {"name": "Grafana", "url": "http://10.0.0.5:3000"}], links


def test_parse_links_skips_malformed_and_non_http_urls():
    import app as app_mod
    links = app_mod.parse_links("no-separator;Bad|javascript:alert(1);Empty|;|https://x.y;Ok|https://ok.example")
    assert links == [{"name": "Ok", "url": "https://ok.example"}], links


def test_links_route_returns_configured_links():
    from fastapi.testclient import TestClient
    import app as app_mod
    from stream import StreamHub
    client = TestClient(app_mod.create_app(store_mod.AggregateStore(), StreamHub(),
                                           links=app_mod.parse_links("Mailpit|https://mail.tranbadat.vn")))
    assert client.get("/dashboard/links").json() == {"links": [{"name": "Mailpit", "url": "https://mail.tranbadat.vn"}]}


def test_links_route_is_empty_without_configuration():
    from fastapi.testclient import TestClient
    import app as app_mod
    from stream import StreamHub
    client = TestClient(app_mod.create_app(store_mod.AggregateStore(), StreamHub()))
    assert client.get("/dashboard/links").json() == {"links": []}


# ---------- API thống kê cho giao diện mới (overview, lịch sử có lọc, theo domain/rule) ----------

class _FakeRepo:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"attacks": [{"event_id": "e1", "src_ip": "1.2.3.4"}], "total": 1, "unique_ips": 1}

    def count(self):
        return 42


class _FakeStats:
    def __init__(self):
        self.calls = []

    def overview(self, hours):
        self.calls.append(("overview", hours))
        return {"kpi": {"attacks": {"value": 3}}, "persisted": True}

    def domains(self, hours):
        self.calls.append(("domains", hours))
        return [{"domain": "shop.local", "count": 3, "trend": [0] * 24, "last_attack_at": None}]

    def rules(self, hours):
        self.calls.append(("rules", hours))
        return [{"rule_id": "SQLI_UNION_SELECT", "count": 3, "trend": [0] * 24}]


def _client(repo=None, stats=None):
    from fastapi.testclient import TestClient
    import app as app_mod
    from stream import StreamHub
    return TestClient(app_mod.create_app(store_mod.AggregateStore(), StreamHub(), repo=repo, stats=stats))


def test_overview_route_uses_requested_window():
    stats = _FakeStats()
    body = _client(stats=stats).get("/dashboard/overview?hours=6").json()
    assert body["kpi"]["attacks"]["value"] == 3 and stats.calls == [("overview", 6)], (body, stats.calls)


def test_overview_without_database_is_not_persisted():
    body = _client().get("/dashboard/overview").json()
    assert body["persisted"] is False and body["window"]["hours"] == 24, body


def test_overview_rejects_out_of_range_window():
    assert _client(stats=_FakeStats()).get("/dashboard/overview?hours=100000").status_code == 422


def test_attacks_route_passes_filters_and_paging_to_repo():
    repo = _FakeRepo()
    body = _client(repo=repo).get(
        "/dashboard/attacks?limit=10&offset=20&hours=24&q=1.2.3&domain=shop.local"
        "&attack_type=xss&severity=high&histogram=true").json()
    assert body["total"] == 1 and body["count"] == 42 and body["persisted"] is True, body
    assert repo.calls == [{"limit": 10, "offset": 20, "hours": 24, "q": "1.2.3", "domain": "shop.local",
                           "attack_type": "xss", "severity": "high", "histogram": True}], repo.calls


def test_attacks_route_rejects_oversized_search_text():
    assert _client(repo=_FakeRepo()).get("/dashboard/attacks?q=" + "a" * 201).status_code == 422


def test_domain_and_rule_stats_routes():
    stats = _FakeStats()
    client = _client(stats=stats)
    assert client.get("/dashboard/stats/domains?hours=24").json()["domains"][0]["domain"] == "shop.local"
    assert client.get("/dashboard/stats/rules").json()["rules"][0]["rule_id"] == "SQLI_UNION_SELECT"
    assert stats.calls == [("domains", 24), ("rules", 24)], stats.calls


def test_stats_routes_are_empty_without_database():
    client = _client()
    assert client.get("/dashboard/stats/domains").json() == {"domains": [], "persisted": False}
    assert client.get("/dashboard/stats/rules").json() == {"rules": [], "persisted": False}


def test_static_assets_are_served():
    response = _client().get("/static/icons/search.svg")
    assert response.status_code == 200 and "svg" in response.headers["content-type"], response.headers
    # JS/CSS không có hash phiên bản: buộc trình duyệt hỏi lại (ETag -> 304) để không trộn bản cũ và mới.
    assert response.headers["cache-control"] == "no-cache", response.headers


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failures += 1
                print("FAIL", name, exc)
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")
