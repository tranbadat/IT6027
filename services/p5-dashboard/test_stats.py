"""Unit test các hàm tổng hợp số liệu cho dashboard (stats.py), không cần Postgres.

Chạy: python /app/p5-dashboard/test_stats.py  (PYTHONPATH=/app để import common).
"""
from datetime import date, datetime, timezone

import stats as stats_mod

NOW = datetime(2026, 10, 16, 7, 30, tzinfo=timezone.utc)


def test_window_covers_requested_hours_and_previous_period():
    w = stats_mod.window(24, now=NOW)
    assert w["end"] == NOW
    assert (w["end"] - w["start"]).total_seconds() == 24 * 3600
    assert (w["start"] - w["prev_start"]).total_seconds() == 24 * 3600
    assert w["trend_seconds"] == 3600, w
    assert w["timeline_minutes"] == 30 and w["timeline_buckets"] == 48, w


def test_window_widens_timeline_buckets_for_long_ranges():
    w = stats_mod.window(168, now=NOW)
    assert w["timeline_minutes"] == 210 and w["timeline_buckets"] == 48, w


def test_window_clamps_hours():
    assert stats_mod.window(0, now=NOW)["hours"] == 1
    assert stats_mod.window(100000, now=NOW)["hours"] == stats_mod.MAX_WINDOW_HOURS


def test_fill_places_counts_in_buckets_and_ignores_out_of_range():
    assert stats_mod.fill([(0, 2), (3, 5), (9, 1), (-1, 4), (None, 7)], 4) == [2, 0, 0, 5]


def test_change_pct():
    assert stats_mod.change_pct(110, 100) == 10.0
    assert stats_mod.change_pct(5, 0) is None
    assert stats_mod.change_pct(0, 8) == -100.0


def test_timeline_groups_severity_per_bucket():
    rows = [(0, "critical", 3), (0, "low", 1), (2, "high", 4), (2, "unknown", 9)]
    assert stats_mod.timeline(rows, 3) == [
        {"critical": 3, "high": 0, "medium": 0, "low": 1},
        {"critical": 0, "high": 0, "medium": 0, "low": 0},
        {"critical": 0, "high": 4, "medium": 0, "low": 0},
    ]


def test_heatmap_has_seven_local_days_oldest_first():
    rows = [(date(2026, 10, 16), 14, 5), (date(2026, 10, 10), 0, 2), (date(2026, 10, 1), 3, 9)]
    days = stats_mod.heatmap(rows, today=date(2026, 10, 16))
    assert [d["date"] for d in days] == ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13",
                                          "2026-10-14", "2026-10-15", "2026-10-16"]
    assert days[0]["hours"][0] == 2 and days[-1]["hours"][14] == 5
    assert sum(sum(d["hours"]) for d in days) == 7  # ngày ngoài 7 ngày bị bỏ
    assert days[-1]["weekday"] == 4  # 16/10/2026 là thứ Sáu (Monday = 0)


def test_escape_like_neutralises_wildcards():
    assert stats_mod.escape_like("50%_a\\b") == "50\\%\\_a\\\\b"


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
