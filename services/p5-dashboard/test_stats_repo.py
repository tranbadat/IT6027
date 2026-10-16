"""Test tích hợp truy vấn lịch sử và thống kê của P5 trên Postgres thật.

Chỉ chạy khi có TEST_DATABASE_URL (database dùng riêng cho test: script tạo lại schema detection và
alerting). Không có biến này thì bỏ qua, để `make test` không cần Postgres.
Chạy: TEST_DATABASE_URL=postgresql://... python /app/p5-dashboard/test_stats_repo.py
"""
import os
from datetime import datetime, timedelta, timezone

from common import db as db_mod

import repo as repo_mod
import stats_repo as stats_repo_mod

DSN = os.environ.get("TEST_DATABASE_URL", "")
NOW = datetime.now(timezone.utc)


def _attack(i, minutes_ago, ip="10.0.0.1", domain="shop.local", attack_type="sql_injection",
            severity="critical", rule="SQLI_UNION_SELECT", url="/p?id=1%20UNION%20SELECT"):
    return {"event": "attack.detected", "event_id": f"e{i}", "domain": domain,
            "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "request_id": f"r{i}",
            "data": {"attack_type": attack_type, "severity": severity, "rule_id": rule, "rule_name": rule,
                     "src_ip": ip, "method": "GET", "url": url, "status": 200, "user_agent": "sqlmap/1.8",
                     "score": 95, "matched_value": "UNION SELECT", "field": "query"}}


def _setup():
    cpool = db_mod.pool(DSN)
    with cpool.connection() as conn:
        conn.execute("DROP SCHEMA IF EXISTS detection CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS alerting CASCADE")
        conn.execute("CREATE SCHEMA detection")
        conn.execute("CREATE SCHEMA alerting")
        conn.execute("CREATE TABLE alerting.alerts (event_id text PRIMARY KEY, triggered_at timestamptz)")
        conn.execute("INSERT INTO alerting.alerts VALUES ('a1', now() - interval '1 hour'),"
                     " ('a2', now() - interval '2 hours'), ('a3', now() - interval '30 hours')")
    attacks = repo_mod.AttackRepo(cpool)
    attacks.ensure_schema()
    attacks.ensure_schema()  # chạy lại không lỗi (ADD COLUMN IF NOT EXISTS)
    rows = [
        _attack(1, 5), _attack(2, 10), _attack(3, 70, ip="10.0.0.2", attack_type="xss", severity="high",
                                                 rule="XSS_SCRIPT_TAG", url="/s?q=%3Cscript%3E"),
        _attack(4, 120, ip="10.0.0.2", domain="blog.local", attack_type="xss", severity="high", rule="XSS_SCRIPT_TAG"),
        _attack(5, 60 * 30, ip="10.0.0.9"),  # kỳ trước (24-48 giờ)
        _attack(6, 60 * 30 + 5, ip="10.0.0.9", url="/a?x=50%_off"),
    ]
    for env in rows:
        attacks.insert_from_event(env)
    attacks.insert_from_event(rows[0])  # trùng event_id: bỏ qua
    return attacks, stats_repo_mod.StatsRepo(cpool, tz="Asia/Ho_Chi_Minh")


def test_search_filters_pages_and_histogram(attacks):
    result = attacks.search(limit=2, offset=0, hours=24, histogram=True)
    assert result["total"] == 4 and result["unique_ips"] == 2, result
    assert [a["event_id"] for a in result["attacks"]] == ["e1", "e2"], result["attacks"]
    assert result["attacks"][0]["status"] == 200 and result["attacks"][0]["score"] == 95, result["attacks"][0]
    assert sum(result["histogram"]["buckets"]) == 4 and result["histogram"]["bucket_minutes"] == 30, result["histogram"]
    assert attacks.search(hours=24, offset=2, limit=2)["attacks"][0]["event_id"] == "e3"
    assert attacks.search(hours=24, attack_type="xss", domain="blog.local")["total"] == 1
    assert attacks.search(hours=24, severity="critical")["total"] == 2
    assert attacks.search(q="10.0.0.2")["total"] == 2
    assert attacks.search(q="xss_script")["total"] == 2  # mã rule, không phân biệt hoa thường
    assert attacks.search(q="50%_")["total"] == 1  # % và _ là ký tự thường, không phải ký tự đại diện
    assert attacks.search(q="5_%")["total"] == 0  # nếu không thoát, "5_%" sẽ khớp "50%..."
    assert attacks.count() == 6


def test_overview_aggregates_window(stats):
    o = stats.overview(24)
    k = o["kpi"]
    assert k["attacks"]["value"] == 4 and k["attacks"]["previous"] == 2 and k["attacks"]["change_pct"] == 100.0, k["attacks"]
    assert k["unique_ips"]["value"] == 2 and k["unique_ips"]["previous"] == 1, k["unique_ips"]
    assert sum(k["attacks"]["trend"]) == 4 and len(k["attacks"]["trend"]) == 24, k["attacks"]
    assert k["alerts"]["value"] == 2 and k["alerts"]["previous"] == 1 and sum(k["alerts"]["trend"]) == 2, k["alerts"]
    assert sum(sum(b.values()) for b in o["timeline"]) == 4 and len(o["timeline"]) == 48, o["timeline"]
    assert o["types"] == [{"attack_type": "sql_injection", "count": 2}, {"attack_type": "xss", "count": 2}] or \
        sorted(t["attack_type"] for t in o["types"]) == ["sql_injection", "xss"], o["types"]
    assert o["top_domains"][0] == {"domain": "shop.local", "count": 3}, o["top_domains"]
    assert {r["rule_id"] for r in o["top_rules"]} == {"SQLI_UNION_SELECT", "XSS_SCRIPT_TAG"}, o["top_rules"]
    assert len(o["heatmap"]) == 7 and sum(sum(d["hours"]) for d in o["heatmap"]) == 6, o["heatmap"]
    top = {ip["ip"]: ip for ip in o["top_ips"]}
    assert top["10.0.0.1"]["count"] == 2 and top["10.0.0.1"]["main_type"] == "sql_injection", top
    assert sum(top["10.0.0.2"]["trend"]) == 2, top


def test_domain_and_rule_stats(stats):
    domains = {d["domain"]: d for d in stats.domains(24)}
    assert domains["shop.local"]["count"] == 3 and sum(domains["shop.local"]["trend"]) == 3, domains
    assert domains["blog.local"]["count"] == 1 and domains["blog.local"]["last_attack_at"], domains
    rules = {r["rule_id"]: r for r in stats.rules(24)}
    assert rules["SQLI_UNION_SELECT"]["count"] == 2 and rules["XSS_SCRIPT_TAG"]["count"] == 2, rules


def test_overview_without_alerting_table(stats):
    with stats._pool.connection() as conn:
        conn.execute("DROP TABLE alerting.alerts")
    assert stats.overview(24)["kpi"]["alerts"]["value"] is None


if __name__ == "__main__":
    if not DSN:
        print("SKIP test_stats_repo (đặt TEST_DATABASE_URL để chạy trên Postgres)")
        raise SystemExit(0)
    attacks_repo, stats_repo = _setup()
    for name, fn, arg in (("test_search_filters_pages_and_histogram", test_search_filters_pages_and_histogram, attacks_repo),
                          ("test_overview_aggregates_window", test_overview_aggregates_window, stats_repo),
                          ("test_domain_and_rule_stats", test_domain_and_rule_stats, stats_repo),
                          ("test_overview_without_alerting_table", test_overview_without_alerting_table, stats_repo)):
        fn(arg)
        print("PASS", name)
    print("ALL PASS")
