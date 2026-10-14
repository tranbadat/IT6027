"""Đọc audit log JSON của ModSecurity (stdout của container) -> mỗi request bị CRS ghi nhận một dòng.

  docker compose -f experiments/modsec/docker-compose.yml logs --no-log-prefix modsec \
      | python3 experiments/modsec_eval.py > experiments/results/modsec.jsonl

Một request được coi là CRS phát hiện (flagged) khi rule 949110 khớp, tức tổng điểm bất thường
inbound vượt ngưỡng (ANOMALY_INBOUND=5, paranoia 1). request_id lấy từ header X-Req-Id.
"""
import json
import re
import sys

TOTAL_SCORE_RE = re.compile(r"Total Score: (\d+)")
BLOCKING_RULE = "949110"


def parse(lines):
    for line in lines:
        line = line.strip()
        if not line.startswith('{"transaction"'):
            continue
        try:
            transaction = json.loads(line)["transaction"]
        except (ValueError, KeyError):
            continue
        headers = transaction.get("request", {}).get("headers", {})
        request_id = next((v for k, v in headers.items() if k.lower() == "x-req-id"), None)
        if not request_id:
            continue
        messages = transaction.get("messages") or []
        rule_ids = [m.get("details", {}).get("ruleId") for m in messages]
        score = 0
        for message in messages:
            if message.get("details", {}).get("ruleId") == BLOCKING_RULE:
                found = TOTAL_SCORE_RE.search(message.get("message", ""))
                score = int(found.group(1)) if found else 0
        yield {"request_id": request_id, "flagged": BLOCKING_RULE in rule_ids, "score": score,
               "rule_ids": [r for r in rule_ids if r and r != BLOCKING_RULE]}


if __name__ == "__main__":
    for row in parse(sys.stdin):
        print(json.dumps(row))
