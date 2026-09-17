#!/usr/bin/env bash
# Kiểm thử end-to-end theo "định nghĩa hoàn thành": gửi request thật (gồm SQLi) tới web
# mục tiêu -> pipeline tự đọc log -> chuẩn hoá -> khớp rule -> chấm điểm -> cảnh báo + dashboard.
# Yêu cầu: đã `make up` với đủ service (profile app) và P1/P2 thật. Cần docker, curl, jq.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo "Chưa có .env — chạy 'make up' trước." >&2; exit 2; }
while IFS='=' read -r key value; do
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
  printenv "$key" >/dev/null && continue
  export "$key=$value"
done < .env

GATEWAY="http://127.0.0.1:${GATEWAY_PORT:-8080}"
MAILPIT="http://127.0.0.1:${MAILPIT_UI_PORT:-8025}"
RABBIT_API="http://127.0.0.1:${RABBITMQ_UI_PORT:-15672}/api"
MARK="e2e-$(date +%s)"
DEADLINE_SECONDS=45

passed=0; failed=0
pass() { passed=$((passed + 1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { failed=$((failed + 1)); printf '  \033[31mFAIL\033[0m %s%s\n' "$1" "${2:+ — $2}"; }
section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

token() {
  curl -s --max-time 5 -X POST -H 'content-type: application/json' \
    -d '{"client_id":"e2e","client_secret":"e2e"}' "$GATEWAY/api/auth/token" | jq -r '.access_token // empty'
}

# wait_for "<mô tả>" "<lệnh trả về số>" "<giá trị tối thiểu>"
wait_for() {
  local desc="$1" cmd="$2" min="$3" waited=0 value=0
  while (( waited < DEADLINE_SECONDS )); do
    value="$(eval "$cmd" 2>/dev/null || echo 0)"
    [[ "$value" =~ ^[0-9]+$ ]] || value=0
    if (( value >= min )); then pass "$desc ($value)"; return 0; fi
    sleep 2; waited=$((waited + 2))
  done
  fail "$desc" "sau ${DEADLINE_SECONDS}s vẫn = $value (cần >= $min)"
  return 1
}

TOKEN="$(token)"
[[ -n "$TOKEN" ]] || { echo "Không lấy được token từ $GATEWAY/api/auth/token" >&2; exit 2; }
summary() { curl -s --max-time 5 -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/dashboard/summary"; }

section "1. Gửi traffic thật (bình thường + tấn công) tới web mục tiêu"
make demo >/dev/null 2>&1 && pass "đã gửi request demo tới shop.local" || fail "gửi request demo"

section "2. Pipeline xử lý (theo dashboard realtime của P5)"
wait_for "P5 ghi nhận tấn công (top_attacks)" \
  "summary | jq '[.top_attacks[].count] | add // 0'" 1
wait_for "phát hiện SQL Injection" \
  "summary | jq '[.top_attacks[] | select(.attack_type==\"sql_injection\") | .count] | add // 0'" 1
wait_for "P5 ghi nhận IP nguồn tấn công (top_ips)" \
  "summary | jq '[.top_ips[].count] | add // 0'" 1

section "3. Cảnh báo (P4 -> mailpit + webhook)"
wait_for "mailpit nhận email cảnh báo" \
  "curl -s '$MAILPIT/api/v1/messages' | jq '.total // 0'" 1
wait_for "P5 có lịch sử cảnh báo" \
  "summary | jq '(.alerts | length) // 0'" 1

section "4. Ràng buộc scope: log ngoài phạm vi không bị xử lý"
curl -s -o /dev/null -H 'Host: internal.local' \
  "http://127.0.0.1:${WEB_NGINX_PORT:-8081}/api/x?q=1%27%20OR%201=1--${MARK}"
sleep 6
ndl="$(curl -s -u "$RABBITMQ_USER:$RABBITMQ_PASSWORD" "$RABBIT_API/queues/%2F/q.deadletter" | jq '.messages // 0')"
if [[ "$ndl" == "0" ]]; then pass "không có message lỗi trong dead-letter"; else fail "dead-letter có $ndl message"; fi
if summary | jq -e '[.top_ips[].ip] | index("internal.local")' >/dev/null 2>&1; then
  fail "internal.local bị xử lý (không được phép)"
else
  pass "internal.local (ngoài scope) không xuất hiện trong kết quả phát hiện"
fi

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
