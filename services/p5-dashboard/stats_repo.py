"""Truy vấn tổng hợp cho dashboard trên detection.attacks (và alerting.alerts của P4 nếu có, cùng DB).

Mọi truy vấn chỉ đọc và giới hạn trong cửa sổ thời gian; phần ghép kết quả nằm ở stats.py.
"""
import stats as stats_mod

_TOP = 6
_TOP_IPS = 5


def _bucket(column: str, step_param: str) -> str:
    """Biểu thức chỉ số khoảng thời gian của `column` tính từ %(start)s, mỗi khoảng %(step_param)s giây."""
    return f"floor(extract(epoch from {column} - %(start)s) / %({step_param})s)::int"


_WINDOW = "detected_at >= %(start)s AND detected_at < %(end)s"


class StatsRepo:
    def __init__(self, cpool, tz: str = "Asia/Ho_Chi_Minh"):
        self._pool = cpool
        self._tz = tz

    def _params(self, hours: int) -> tuple[dict, dict]:
        w = stats_mod.window(hours)
        params = {"start": w["start"], "end": w["end"], "prev": w["prev_start"],
                  "trend": w["trend_seconds"], "tl": w["timeline_minutes"] * 60, "tz": self._tz}
        return w, params

    def overview(self, hours: int) -> dict:
        w, p = self._params(hours)
        n = stats_mod.TREND_BUCKETS
        with self._pool.connection() as conn:
            current, previous, ips, prev_ips = conn.execute(
                "SELECT count(*) FILTER (WHERE detected_at >= %(start)s),"
                " count(*) FILTER (WHERE detected_at < %(start)s),"
                " count(DISTINCT src_ip) FILTER (WHERE detected_at >= %(start)s),"
                " count(DISTINCT src_ip) FILTER (WHERE detected_at < %(start)s)"
                " FROM detection.attacks WHERE detected_at >= %(prev)s AND detected_at < %(end)s", p).fetchone()
            trend = conn.execute(
                f"SELECT {_bucket('detected_at', 'trend')}, count(*), count(DISTINCT src_ip),"
                f" count(DISTINCT domain), count(DISTINCT rule_id)"
                f" FROM detection.attacks WHERE {_WINDOW} GROUP BY 1", p).fetchall()
            timeline = conn.execute(
                f"SELECT {_bucket('detected_at', 'tl')}, severity, count(*)"
                f" FROM detection.attacks WHERE {_WINDOW} GROUP BY 1, 2", p).fetchall()
            types = conn.execute(
                f"SELECT attack_type, count(*) FROM detection.attacks WHERE {_WINDOW}"
                f" GROUP BY 1 ORDER BY 2 DESC", p).fetchall()
            domains = conn.execute(
                f"SELECT domain, count(*) FROM detection.attacks WHERE {_WINDOW}"
                f" GROUP BY 1 ORDER BY 2 DESC LIMIT {_TOP}", p).fetchall()
            rules = conn.execute(
                f"SELECT rule_id, max(rule_name), max(attack_type), count(*) FROM detection.attacks"
                f" WHERE {_WINDOW} GROUP BY 1 ORDER BY 4 DESC LIMIT {_TOP}", p).fetchall()
            heat_rows, today = self._heatmap(conn, p)
            top_ips = self._top_ips(conn, p, n)
            alerts = self._alerts(conn, p, n)
        return {
            "window": {"hours": w["hours"], "start": w["start"].isoformat(), "end": w["end"].isoformat(),
                       "trend_minutes": round(w["trend_seconds"] / 60, 2),
                       "timeline_minutes": w["timeline_minutes"]},
            "kpi": {
                "attacks": {"value": current, "previous": previous,
                            "change_pct": stats_mod.change_pct(current, previous),
                            "trend": stats_mod.fill([(r[0], r[1]) for r in trend], n)},
                "unique_ips": {"value": ips, "previous": prev_ips,
                               "change_pct": stats_mod.change_pct(ips, prev_ips),
                               "trend": stats_mod.fill([(r[0], r[2]) for r in trend], n)},
                "domains_attacked": {"trend": stats_mod.fill([(r[0], r[3]) for r in trend], n)},
                "rules_matched": {"trend": stats_mod.fill([(r[0], r[4]) for r in trend], n)},
                "alerts": alerts,
            },
            "timeline": stats_mod.timeline(timeline, w["timeline_buckets"]),
            "types": [{"attack_type": t, "count": c} for t, c in types],
            "top_domains": [{"domain": d, "count": c} for d, c in domains],
            "top_rules": [{"rule_id": r, "rule_name": name, "attack_type": t, "count": c}
                          for r, name, t, c in rules],
            "heatmap": stats_mod.heatmap(heat_rows, today),
            "top_ips": top_ips,
            "persisted": True,
        }

    def _heatmap(self, conn, p: dict):
        """Số tấn công theo (ngày, giờ) địa phương trong 7 ngày gần nhất, tính cả hôm nay."""
        today = conn.execute("SELECT (now() AT TIME ZONE %(tz)s)::date", p).fetchone()[0]
        rows = conn.execute(
            "SELECT (detected_at AT TIME ZONE %(tz)s)::date,"
            " extract(hour from detected_at AT TIME ZONE %(tz)s)::int, count(*)"
            " FROM detection.attacks"
            " WHERE detected_at >= ((date_trunc('day', now() AT TIME ZONE %(tz)s) - interval '6 days')"
            " AT TIME ZONE %(tz)s) GROUP BY 1, 2", p).fetchall()
        return rows, today

    def _top_ips(self, conn, p: dict, n: int) -> list[dict]:
        top = conn.execute(
            f"SELECT src_ip, count(*) FROM detection.attacks WHERE {_WINDOW}"
            f" GROUP BY 1 ORDER BY 2 DESC LIMIT {_TOP_IPS}", p).fetchall()
        ips = [ip for ip, _ in top]
        if not ips:
            return []
        q = {**p, "ips": ips}
        main = dict(conn.execute(
            f"SELECT DISTINCT ON (src_ip) src_ip, attack_type FROM ("
            f" SELECT src_ip, attack_type, count(*) AS c FROM detection.attacks"
            f" WHERE {_WINDOW} AND src_ip = ANY(%(ips)s) GROUP BY 1, 2) x"
            f" ORDER BY src_ip, c DESC", q).fetchall())
        trend: dict[str, list] = {ip: [] for ip in ips}
        for ip, index, count in conn.execute(
                f"SELECT src_ip, {_bucket('detected_at', 'trend')}, count(*) FROM detection.attacks"
                f" WHERE {_WINDOW} AND src_ip = ANY(%(ips)s) GROUP BY 1, 2", q).fetchall():
            trend[ip].append((index, count))
        return [{"ip": ip, "count": count, "main_type": main.get(ip), "trend": stats_mod.fill(trend[ip], n)}
                for ip, count in top]

    def _alerts(self, conn, p: dict, n: int) -> dict:
        """Cảnh báo của P4 (bảng alerting.alerts); chưa có bảng (P4 không dùng DB) thì trả rỗng."""
        if not conn.execute("SELECT to_regclass('alerting.alerts') IS NOT NULL").fetchone()[0]:
            return {"value": None, "previous": None, "change_pct": None, "trend": [0] * n}
        current, previous = conn.execute(
            "SELECT count(*) FILTER (WHERE triggered_at >= %(start)s),"
            " count(*) FILTER (WHERE triggered_at < %(start)s)"
            " FROM alerting.alerts WHERE triggered_at >= %(prev)s AND triggered_at < %(end)s", p).fetchone()
        trend = conn.execute(
            f"SELECT {_bucket('triggered_at', 'trend')}, count(*) FROM alerting.alerts"
            f" WHERE triggered_at >= %(start)s AND triggered_at < %(end)s GROUP BY 1", p).fetchall()
        return {"value": current, "previous": previous, "change_pct": stats_mod.change_pct(current, previous),
                "trend": stats_mod.fill(trend, n)}

    def domains(self, hours: int) -> list[dict]:
        """Theo domain: số tấn công và xu hướng trong cửa sổ, lần bị tấn công gần nhất (mọi thời điểm)."""
        _, p = self._params(hours)
        n = stats_mod.TREND_BUCKETS
        with self._pool.connection() as conn:
            last = dict(conn.execute("SELECT domain, max(detected_at) FROM detection.attacks GROUP BY 1").fetchall())
            counts = dict(conn.execute(
                f"SELECT domain, count(*) FROM detection.attacks WHERE {_WINDOW} GROUP BY 1", p).fetchall())
            trend: dict[str, list] = {}
            for domain, index, count in conn.execute(
                    f"SELECT domain, {_bucket('detected_at', 'trend')}, count(*) FROM detection.attacks"
                    f" WHERE {_WINDOW} GROUP BY 1, 2", p).fetchall():
                trend.setdefault(domain, []).append((index, count))
        return [{"domain": domain, "count": counts.get(domain, 0),
                 "trend": stats_mod.fill(trend.get(domain, []), n),
                 "last_attack_at": seen.isoformat() if seen else None}
                for domain, seen in sorted(last.items(), key=lambda item: -counts.get(item[0], 0))]

    def rules(self, hours: int) -> list[dict]:
        """Theo rule: số lần khớp và xu hướng trong cửa sổ."""
        _, p = self._params(hours)
        n = stats_mod.TREND_BUCKETS
        with self._pool.connection() as conn:
            counts = conn.execute(
                f"SELECT rule_id, count(*) FROM detection.attacks WHERE {_WINDOW} GROUP BY 1 ORDER BY 2 DESC",
                p).fetchall()
            trend: dict[str, list] = {}
            for rule_id, index, count in conn.execute(
                    f"SELECT rule_id, {_bucket('detected_at', 'trend')}, count(*) FROM detection.attacks"
                    f" WHERE {_WINDOW} GROUP BY 1, 2", p).fetchall():
                trend.setdefault(rule_id, []).append((index, count))
        return [{"rule_id": rule_id, "count": count, "trend": stats_mod.fill(trend.get(rule_id, []), n)}
                for rule_id, count in counts]
