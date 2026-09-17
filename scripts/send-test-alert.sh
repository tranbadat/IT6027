#!/usr/bin/env bash
# Gửi một cảnh báo MẪU (không phải phát hiện thật) theo đúng đường P4 Alerting sẽ dùng:
# từ bên trong network backend -> SMTP mailpit:1025 và webhook-sink:8080/alerts.
# Dùng để kiểm tra hạ tầng cảnh báo khi P4 chưa có. Xem kết quả tại http://localhost:8025
#
#   TEST_ALERT_ID=<chuỗi>  gắn vào tiêu đề để tìm lại (mặc định: thời điểm hiện tại)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CURL_IMAGE="curlimages/curl:8.15.0"
ALERT_ID="${TEST_ALERT_ID:-$(date +%s)}"
MAIL_FROM="waf-alerts@waf.local"
MAIL_TO="soc@waf.local"
NOW_RFC2822="$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S +0000')"
NOW_RFC3339="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

network="$(docker compose config --format json | jq -r '.networks.backend.name')"
if [[ -z "$(docker compose ps -q --status running mailpit 2>/dev/null)" ]]; then
  echo "mailpit chưa chạy — chạy 'make up' trước." >&2
  exit 1
fi

email="$(cat <<EOF
From: WAF Log Analyzer <${MAIL_FROM}>
To: <${MAIL_TO}>
Subject: [TEST ${ALERT_ID}][HIGH] SQL Injection detected on shop.local
Date: ${NOW_RFC2822}
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit

Đây là cảnh báo MẪU do scripts/send-test-alert.sh gửi để kiểm tra đường SMTP.
Không phải một phát hiện thật.

Domain       : shop.local
Loại tấn công: SQL Injection
Mức độ       : HIGH
Điểm         : 87 (ngưỡng 70)
URL          : /api/products?id=1' OR '1'='1
IP nguồn     : 172.23.0.1
Rule khớp    : sqli-001, sqli-003
Thời điểm    : ${NOW_RFC3339}
EOF
)"

webhook_body="$(jq -nc --arg id "$ALERT_ID" --arg ts "$NOW_RFC3339" '{
  event: "alert.triggered",
  event_id: "00000000-0000-4000-8000-000000000000",
  session_id: ("test-" + $id),
  timestamp: $ts,
  source: "send-test-alert",
  domain: "shop.local",
  data: {
    test: true,
    severity: "high",
    score: 87,
    threshold: 70,
    attack_type: "sql_injection",
    url: "/api/products?id=1%27%20OR%20%271%27%3D%271",
    src_ip: "172.23.0.1",
    rule_ids: ["sqli-001", "sqli-003"]
  }
}')"

printf '%s\n' "$email" | docker run --rm -i --network "$network" \
  -e WEBHOOK_BODY="$webhook_body" -e MAIL_FROM="$MAIL_FROM" -e MAIL_TO="$MAIL_TO" \
  --entrypoint /bin/sh "$CURL_IMAGE" -c '
    set -e
    curl -sS --url smtp://mailpit:1025 --mail-from "$MAIL_FROM" --mail-rcpt "$MAIL_TO" --upload-file -
    echo "SMTP    -> mailpit:1025 OK"
    if curl -sS -o /dev/null --max-time 5 -X POST -H "content-type: application/json" \
         -d "$WEBHOOK_BODY" http://webhook-sink:8080/alerts; then
      echo "Webhook -> webhook-sink:8080/alerts OK (xem: make logs s=webhook-sink)"
    else
      echo "Webhook -> bỏ qua (webhook-sink chưa chạy, cần profile mock)"
    fi
  '

echo "Đã gửi cảnh báo mẫu [TEST ${ALERT_ID}] — mở http://localhost:${MAILPIT_UI_PORT:-8025} để xem."
