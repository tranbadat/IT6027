"""Lưu lịch sử tấn công phát hiện vào Postgres (schema detection). Idempotent theo event_id.

Consumer (thread nền) ghi trên attack.detected; HTTP đọc lại /dashboard/attacks.
"""
from common import logging as log

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
    "CREATE INDEX IF NOT EXISTS attacks_detected_idx ON detection.attacks (detected_at DESC)",
    "CREATE INDEX IF NOT EXISTS attacks_domain_idx ON detection.attacks (domain)",
    "CREATE INDEX IF NOT EXISTS attacks_type_idx ON detection.attacks (attack_type)",
)

_COLS = ("event_id", "detected_at", "domain", "attack_type", "severity", "rule_id",
         "rule_name", "src_ip", "method", "url", "host", "field", "matched_value",
         "evidence", "request_id", "session_id")


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
            (data.get("matched_value") or "")[:500], data.get("evidence"),
            env.get("request_id"), env.get("session_id"),
        ]
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO detection.attacks (event_id,detected_at,domain,attack_type,"
                "severity,rule_id,rule_name,src_ip,method,url,host,field,matched_value,"
                "evidence,request_id,session_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                row,
            )

    def recent(self, limit: int = 100, domain: str | None = None,
               attack_type: str | None = None) -> list[dict]:
        sql = ("SELECT event_id,detected_at,domain,attack_type,severity,rule_id,rule_name,"
               "src_ip,method,url,host,field,matched_value,evidence,request_id,session_id "
               "FROM detection.attacks")
        clauses, params = [], []
        if domain:
            clauses.append("domain = %s"); params.append(domain)
        if attack_type:
            clauses.append("attack_type = %s"); params.append(attack_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY detected_at DESC NULLS LAST LIMIT %s"
        params.append(limit)
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            item = dict(zip(_COLS, r))
            if item.get("detected_at") is not None:
                item["detected_at"] = item["detected_at"].isoformat()
            result.append(item)
        return result

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM detection.attacks").fetchone()[0]
