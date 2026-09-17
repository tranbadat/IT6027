"""Lưu lịch sử cảnh báo vào Postgres (schema alerting). An toàn đa luồng qua pool.

Consumer (thread nền) ghi; HTTP (threadpool) đọc. Idempotent theo event_id.
"""
from common import logging as log

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS alerting.alerts (
        event_id text PRIMARY KEY,
        triggered_at timestamptz,
        domain text, severity text,
        attack_types text[], main_attack_type text,
        src_ip text, host text, url text,
        score double precision, threshold double precision,
        rule_ids text[], reason text,
        request_id text, session_id text,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS alerts_triggered_idx ON alerting.alerts (triggered_at DESC)",
    "CREATE INDEX IF NOT EXISTS alerts_domain_idx ON alerting.alerts (domain)",
)

_COLS = ("event_id", "triggered_at", "domain", "severity", "attack_types",
         "main_attack_type", "src_ip", "host", "url", "score", "threshold",
         "rule_ids", "reason", "request_id", "session_id")


class AlertRepo:
    def __init__(self, cpool):
        self._pool = cpool

    def ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            for stmt in _DDL:
                conn.execute(stmt)

    def insert(self, alert: dict) -> None:
        row = [alert.get(c) for c in _COLS]
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO alerting.alerts (event_id,triggered_at,domain,severity,"
                "attack_types,main_attack_type,src_ip,host,url,score,threshold,rule_ids,"
                "reason,request_id,session_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                row,
            )

    def recent(self, limit: int = 100, domain: str | None = None) -> list[dict]:
        sql = ("SELECT event_id,triggered_at,domain,severity,attack_types,main_attack_type,"
               "src_ip,host,url,score,threshold,rule_ids,reason,request_id,session_id "
               "FROM alerting.alerts")
        params: list = []
        if domain:
            sql += " WHERE domain = %s"
            params.append(domain)
        sql += " ORDER BY triggered_at DESC NULLS LAST LIMIT %s"
        params.append(limit)
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            item = dict(zip(_COLS, r))
            if item.get("triggered_at") is not None:
                item["triggered_at"] = item["triggered_at"].isoformat()
            result.append(item)
        return result

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM alerting.alerts").fetchone()[0]
