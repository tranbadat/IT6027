"""Lưu lịch sử tấn công phát hiện vào Postgres (schema detection). Idempotent theo event_id.

Consumer (thread nền) ghi trên attack.detected; HTTP đọc lại /dashboard/attacks.
"""
import stats as stats_mod

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS detection.attacks (
        event_id text PRIMARY KEY,
        detected_at timestamptz,
        domain text, attack_type text, severity text,
        rule_id text, rule_name text,
        src_ip text, method text, url text, host text,
        field text, matched_value text, evidence text,
        request_id text, session_id text,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Bổ sung cho dashboard: mã HTTP, User-Agent và điểm của rule (detector phát từ phiên bản này).
    "ALTER TABLE detection.attacks ADD COLUMN IF NOT EXISTS status int",
    "ALTER TABLE detection.attacks ADD COLUMN IF NOT EXISTS user_agent text",
    "ALTER TABLE detection.attacks ADD COLUMN IF NOT EXISTS score int",
    "CREATE INDEX IF NOT EXISTS attacks_detected_idx ON detection.attacks (detected_at DESC)",
    "CREATE INDEX IF NOT EXISTS attacks_domain_idx ON detection.attacks (domain)",
    "CREATE INDEX IF NOT EXISTS attacks_type_idx ON detection.attacks (attack_type)",
    "CREATE INDEX IF NOT EXISTS attacks_src_ip_idx ON detection.attacks (src_ip)",
)

_COLS = ("event_id", "detected_at", "domain", "attack_type", "severity", "rule_id",
         "rule_name", "src_ip", "method", "url", "host", "field", "matched_value",
         "evidence", "request_id", "session_id", "status", "user_agent", "score")
_SELECT = "SELECT " + ",".join(_COLS) + " FROM detection.attacks"
_MAX_TEXT = 500


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class AttackRepo:
    def __init__(self, cpool):
        self._pool = cpool

    def ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            for stmt in _DDL:
                conn.execute(stmt)

    def insert_from_event(self, env: dict) -> None:
        data = env.get("data") or {}
        row = [
            env.get("event_id"), env.get("timestamp"), env.get("domain"),
            data.get("attack_type"), data.get("severity"), data.get("rule_id"),
            data.get("rule_name"), data.get("src_ip"), data.get("method"),
            data.get("url"), data.get("host"), data.get("field"),
            (data.get("matched_value") or "")[:_MAX_TEXT], data.get("evidence"),
            env.get("request_id"), env.get("session_id"),
            _int_or_none(data.get("status")), (data.get("user_agent") or "")[:_MAX_TEXT] or None,
            _int_or_none(data.get("score")),
        ]
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO detection.attacks (" + ",".join(_COLS) + ") VALUES ("
                + ",".join(["%s"] * len(_COLS)) + ") ON CONFLICT (event_id) DO NOTHING",
                row,
            )

    def recent(self, limit: int = 100, domain: str | None = None,
               attack_type: str | None = None) -> list[dict]:
        return self.search(limit=limit, domain=domain, attack_type=attack_type)["attacks"]

    def search(self, limit: int = 100, offset: int = 0, hours: int | None = None, q: str | None = None,
               domain: str | None = None, attack_type: str | None = None, severity: str | None = None,
               histogram: bool = False) -> dict:
        """Lịch sử có lọc và phân trang. q tìm trong IP, URL, mã rule, domain (không phân biệt hoa thường).
        hours giới hạn cửa sổ thời gian; histogram=True trả thêm số sự kiện theo khoảng thời gian."""
        clauses, params = [], {}
        w = stats_mod.window(hours) if hours else None
        if w:
            clauses.append("detected_at >= %(start)s AND detected_at < %(end)s")
            params.update(start=w["start"], end=w["end"], tl=w["timeline_minutes"] * 60)
        for column, value in (("domain", domain), ("attack_type", attack_type), ("severity", severity)):
            if value:
                clauses.append(f"{column} = %({column})s")
                params[column] = value
        if q:
            clauses.append("(src_ip ILIKE %(q)s ESCAPE '\\' OR url ILIKE %(q)s ESCAPE '\\'"
                           " OR rule_id ILIKE %(q)s ESCAPE '\\' OR domain ILIKE %(q)s ESCAPE '\\')")
            params["q"] = f"%{stats_mod.escape_like(q)}%"
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._pool.connection() as conn:
            rows = conn.execute(f"{_SELECT}{where} ORDER BY detected_at DESC NULLS LAST"
                                f" LIMIT %(limit)s OFFSET %(offset)s",
                                {**params, "limit": limit, "offset": offset}).fetchall()
            total, unique_ips = conn.execute(
                f"SELECT count(*), count(DISTINCT src_ip) FROM detection.attacks{where}", params).fetchone()
            buckets = None
            if histogram and w:
                buckets = stats_mod.fill(conn.execute(
                    "SELECT floor(extract(epoch from detected_at - %(start)s) / %(tl)s)::int, count(*)"
                    f" FROM detection.attacks{where} GROUP BY 1", params).fetchall(), w["timeline_buckets"])
        result = {"attacks": [self._row(r) for r in rows], "total": total, "unique_ips": unique_ips}
        if buckets is not None:
            result["histogram"] = {"start": w["start"].isoformat(), "bucket_minutes": w["timeline_minutes"],
                                   "buckets": buckets}
        return result

    @staticmethod
    def _row(r) -> dict:
        item = dict(zip(_COLS, r))
        if item.get("detected_at") is not None:
            item["detected_at"] = item["detected_at"].isoformat()
        return item

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM detection.attacks").fetchone()[0]
