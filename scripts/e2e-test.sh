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

# Số message trong topic dead-letter (tổng end offset). Tìm container Kafka theo label compose
# nên dùng được cho cả bản tất-cả-trong-một lẫn stack deploy platform.
dlq_count() {
  local kafka
  kafka="$(docker ps -q --filter label=com.docker.compose.service=kafka | head -1)"
  [[ -n "$kafka" ]] || { echo 0; return; }
  docker exec "$kafka" /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server localhost:19092 \
    --topic waf.deadletter 2>/dev/null | awk -F: '{s += $3} END {print s + 0}'
}
DLQ_BEFORE="$(dlq_count)"

TOKEN="$(token)"
[[ -n "$TOKEN" ]] || { echo "Không lấy được token từ $GATEWAY/api/auth/token" >&2; exit 2; }
summary() { curl -s --max-time 5 -H "Authorization: Bearer $TOKEN" "$GATEWAY/api/dashboard/summary"; }

# Số bản ghi P5 có cho một domain: traffic từ log.enriched (bộ nhớ) + tấn công đã lưu DB.
# In ra rỗng nếu không đọc được, để phép so sánh trước/sau không âm thầm coi lỗi là 0.
domain_records() {
  local traffic attacks
  traffic="$(summary | jq -e --arg d "$1" '[.traffic[] | select(.domain == $d) | .count] | add // 0')" || return
  attacks="$(curl -s --max-time 5 -H "Authorization: Bearer $TOKEN" \
    "$GATEWAY/api/dashboard/attacks?domain=$1&limit=1000" | jq -e '
      if .persisted then .attacks | length else error("P5 chưa lưu DB") end')" || return
  echo $((traffic + attacks))
}
OUT_OF_SCOPE="internal.local"
SCOPE_BEFORE="$(domain_records "$OUT_OF_SCOPE")"

section "1. Gửi traffic thật (bình thường + tấn công) tới web mục tiêu"
if demo_out="$(make demo 2>&1)"; then
  pass "đã gửi request demo tới shop.local"
else
  fail "gửi request demo" "$(tail -n 1 <<<"$demo_out")"
fi

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
curl -s -o /dev/null -H "Host: $OUT_OF_SCOPE" \
  "http://127.0.0.1:${WEB_NGINX_PORT:-8081}/api/x?q=1%27%20OR%201=1--${MARK}"
sleep 6
ndl=$(( $(dlq_count) - DLQ_BEFORE ))
if (( ndl == 0 )); then pass "không có message lỗi mới trong dead-letter (waf.deadletter)"; else fail "dead-letter có thêm $ndl message"; fi
scope_after="$(domain_records "$OUT_OF_SCOPE")"
if [[ ! "$SCOPE_BEFORE" =~ ^[0-9]+$ || ! "$scope_after" =~ ^[0-9]+$ ]]; then
  fail "kiểm tra $OUT_OF_SCOPE" "không đọc được traffic/tấn công từ P5"
elif (( scope_after > SCOPE_BEFORE )); then
  fail "$OUT_OF_SCOPE (ngoài scope) bị xử lý" "P5 có thêm $((scope_after - SCOPE_BEFORE)) bản ghi traffic/tấn công"
else
  pass "$OUT_OF_SCOPE (ngoài scope) không có traffic hay tấn công mới trong P5"
fi

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
