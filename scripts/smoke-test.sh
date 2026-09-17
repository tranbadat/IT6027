#!/usr/bin/env bash
# Kiểm tra nhanh hạ tầng sau khi `make up`. Trả về mã lỗi khác 0 nếu có kiểm tra thất bại.
# Yêu cầu: docker, curl, jq.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

for tool in docker curl jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Thiếu công cụ: $tool" >&2; exit 2; }
done
[[ -f .env ]] || { echo "Chưa có .env — chạy 'make init' trước." >&2; exit 2; }

# Nạp .env nhưng giữ biến đã export sẵn trong shell (cùng thứ tự ưu tiên với docker compose).
while IFS='=' read -r key value; do
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
  printenv "$key" >/dev/null && continue
  export "$key=$value"
done < .env

GATEWAY="http://127.0.0.1:${GATEWAY_PORT:-8080}"
WEB_NGINX="http://127.0.0.1:${WEB_NGINX_PORT:-8081}"
WEB_APACHE="http://127.0.0.1:${WEB_APACHE_PORT:-8082}"
RABBIT_API="http://127.0.0.1:${RABBITMQ_UI_PORT:-15672}/api"
MAILPIT="http://127.0.0.1:${MAILPIT_UI_PORT:-8025}"
MOCK_API="http://127.0.0.1:${MOCK_API_PORT:-4010}"
WEBHOOK_SINK="http://127.0.0.1:${WEBHOOK_SINK_PORT:-4020}"
SWAGGER_UI="http://127.0.0.1:${SWAGGER_UI_PORT:-8090}"
MARK="smoke-$(date +%s)-$$"
LOG_FLUSH_WAIT_SECONDS=1

passed=0
failed=0
skipped=0

pass() { passed=$((passed + 1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { failed=$((failed + 1)); printf '  \033[31mFAIL\033[0m %s%s\n' "$1" "${2:+ — $2}"; }
skip() { skipped=$((skipped + 1)); printf '  \033[33mSKIP\033[0m %s%s\n' "$1" "${2:+ — $2}"; }
section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# expect <mô tả> <giá trị thực> <giá trị mong đợi>
expect() {
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1" "nhận '$2', mong đợi '$3'"; fi
}

is_running() {
  [[ -n "$(docker compose ps -q --status running "$1" 2>/dev/null)" ]]
}

container_state() {
  local id
  id="$(docker compose ps -aq "$1" 2>/dev/null)"
  [[ -n "$id" ]] || { echo "missing"; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}:{{.State.ExitCode}}{{end}}' "$id"
}

http_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@"
}

rabbit() {
  curl -sS --max-time 5 -u "${RABBITMQ_USER}:${RABBITMQ_PASSWORD}" -H 'content-type: application/json' "$@"
}

# log_contains <service> <file> <chuỗi>
log_contains() {
  docker compose exec -T "$1" grep -F -- "$3" "$2" 2>/dev/null
}

check_containers() {
  section "Container"
  local svc
  for svc in web-nginx rabbitmq postgres gateway mailpit; do
    expect "$svc healthy" "$(container_state "$svc")" "healthy"
  done
}

check_web_nginx() {
  section "Web mục tiêu Nginx → access log"
  expect "shop.local trả 200" "$(http_status -H 'Host: shop.local' "$WEB_NGINX/api/products?id=1")" "200"

  curl -s -o /dev/null --max-time 5 -H 'Host: shop.local' -H "Cookie: sessionid=${MARK}" \
    "$WEB_NGINX/api/products?id=1%27%20OR%20%271%27%3D%271&marker=${MARK}"
  curl -s -o /dev/null --max-time 5 -H 'Host: internal.local' "$WEB_NGINX/api/x?marker=${MARK}-internal"
  sleep "$LOG_FLUSH_WAIT_SECONDS"

  local line
  line="$(log_contains web-nginx /var/log/waf/nginx/shop.local.access.log "marker=${MARK}")"
  if [[ -n "$line" ]]; then pass "dòng log mới xuất hiện trong shop.local.access.log"; else fail "dòng log mới xuất hiện trong shop.local.access.log"; fi
  if [[ "$line" =~ \"shop\.local\"\ rt=[0-9.]+\ sid=\"${MARK}\"$ ]]; then
    pass "định dạng waf_combined (host, rt, sid)"
  else
    fail "định dạng waf_combined (host, rt, sid)" "$line"
  fi

  if [[ -n "$(log_contains web-nginx /var/log/waf/nginx/internal.local.access.log "marker=${MARK}-internal")" ]]; then
    pass "internal.local ghi vào file log riêng"
  else
    fail "internal.local ghi vào file log riêng"
  fi
  if [[ -z "$(log_contains web-nginx /var/log/waf/nginx/shop.local.access.log "marker=${MARK}-internal")" ]]; then
    pass "log internal.local không lẫn vào shop.local"
  else
    fail "log internal.local không lẫn vào shop.local"
  fi
}

check_web_apache() {
  section "Web mục tiêu Apache → access log"
  if ! is_running web-apache; then skip "web-apache" "profile apache chưa bật"; return; fi
  curl -s -o /dev/null --max-time 5 -H 'Host: blog.local' -H "Cookie: sessionid=${MARK}" "$WEB_APACHE/?marker=${MARK}"
  sleep "$LOG_FLUSH_WAIT_SECONDS"
  local line
  line="$(log_contains web-apache /var/log/waf/apache/blog.local.access.log "marker=${MARK}")"
  if [[ "$line" =~ \"blog\.local\"\ rt_us=[0-9]+\ sid=\"${MARK}\"$ ]]; then
    pass "dòng log mới trong blog.local.access.log đúng định dạng"
  else
    fail "dòng log mới trong blog.local.access.log đúng định dạng" "${line:-không thấy dòng log}"
  fi
}

check_event_bus() {
  section "Event bus (RabbitMQ)"
  expect "exchange waf.events là topic" \
    "$(rabbit "$RABBIT_API/exchanges/%2F/waf.events" | jq -r '.type')" "topic"

  local expected actual
  expected="$(jq -c '[.bindings[] | select(.source == "waf.events") | "\(.destination) \(.routing_key)"] | sort' infra/rabbitmq/definitions.json)"
  actual="$(rabbit "$RABBIT_API/exchanges/%2F/waf.events/bindings/source" | jq -c '[.[] | "\(.destination) \(.routing_key)"] | sort')"
  expect "binding khớp definitions.json" "$actual" "$expected"

  expect "queue q.deadletter tồn tại" \
    "$(rabbit "$RABBIT_API/queues/%2F/q.deadletter" | jq -r '.name')" "q.deadletter"

  # Publish qua một queue tạm với routing key riêng để không làm bẩn queue thật.
  local queue="smoke.${MARK}" routing_key="smoke.${MARK}" payload body
  rabbit -o /dev/null -X PUT "$RABBIT_API/queues/%2F/${queue}" \
    -d '{"durable":false,"auto_delete":false,"arguments":{"x-expires":60000}}'
  rabbit -o /dev/null -X POST "$RABBIT_API/bindings/%2F/e/waf.events/q/${queue}" \
    -d "{\"routing_key\":\"${routing_key}\"}"

  payload="$(jq -c --arg id "$MARK" '.request_id = $id' contracts/events/examples/log.raw.ingested.json)"
  body="$(jq -nc --arg rk "$routing_key" --arg p "$payload" \
    '{routing_key:$rk, payload:$p, payload_encoding:"string", properties:{content_type:"application/json", delivery_mode:2}}')"
  expect "publish được route" \
    "$(rabbit -X POST "$RABBIT_API/exchanges/%2F/waf.events/publish" -d "$body" | jq -r '.routed')" "true"

  expect "consume lại đúng event" \
    "$(rabbit -X POST "$RABBIT_API/queues/%2F/${queue}/get" \
        -d '{"count":1,"ackmode":"ack_requeue_false","encoding":"auto"}' \
      | jq -r '.[0].payload | fromjson | .request_id')" "$MARK"

  rabbit -o /dev/null -X DELETE "$RABBIT_API/queues/%2F/${queue}"
}

check_postgres() {
  section "PostgreSQL"
  local count
  count="$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "${POSTGRES_DB:-waf}" -tAc \
    "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ('scope','auth','pipeline','alerting','dashboard','rules','detection')" 2>/dev/null | tr -d '[:space:]')"
  expect "7 schema theo module" "$count" "7"
}

check_gateway() {
  section "API gateway"
  expect "healthz" "$(http_status "$GATEWAY/healthz")" "200"

  if ! is_running mock-api && [[ "${SCOPE_SERVICE_URL:-}" == *mock-api* ]]; then
    skip "route /api/scope, /api/auth" "SCOPE_SERVICE_URL trỏ tới mock-api nhưng profile mock chưa bật"
    return
  fi

  expect "không có token → 401" "$(http_status "$GATEWAY/api/scope/check?domain=shop.local")" "401"
  expect "401 trả JSON" \
    "$(curl -s --max-time 5 "$GATEWAY/api/scope/check?domain=shop.local" | jq -r '.error')" "unauthorized"

  local token
  token="$(curl -s --max-time 5 -X POST -H 'content-type: application/json' \
    -d "{\"client_id\":\"${SMOKE_CLIENT_ID:-smoke}\",\"client_secret\":\"${SMOKE_CLIENT_SECRET:-smoke}\"}" \
    "$GATEWAY/api/auth/token" | jq -r '.access_token // empty')"
  if [[ -n "$token" ]]; then pass "cấp token qua /api/auth/token"; else fail "cấp token qua /api/auth/token"; return; fi

  local auth=(-H "Authorization: Bearer ${token}")
  expect "token qua cookie waf_token" \
    "$(http_status -H "Cookie: waf_token=${token}" "$GATEWAY/api/scope/check?domain=shop.local")" "200"
  expect "endpoint /api/auth khác cần token" "$(http_status "$GATEWAY/api/auth/verify")" "401"
  expect "chặn đường tắt /dashboard/ → 404" "$(http_status "$GATEWAY/dashboard/stats")" "404"
  expect "scope shop.local → allowed" \
    "$(curl -s --max-time 5 "${auth[@]}" "$GATEWAY/api/scope/check?domain=shop.local" | jq -r '.allowed')" "true"
  expect "scope internal.local → denied" \
    "$(curl -s --max-time 5 "${auth[@]}" "$GATEWAY/api/scope/check?domain=internal.local" | jq -r '.allowed')" "false"
  expect "scope thiếu tham số → 400" \
    "$(http_status "${auth[@]}" "$GATEWAY/api/scope/check")" "400"
  expect "kết quả scope đủ trường theo contract" \
    "$(curl -s --max-time 5 "${auth[@]}" "$GATEWAY/api/scope/check?app=shop-app" \
      | jq -r '[has("target"), has("target_type"), has("allowed"), has("reason"), has("cache_ttl_seconds")] | all')" "true"

  if is_running d2-rule-manager; then
    skip "route tới service chưa chạy → 502" "d2-rule-manager đang chạy"
  else
    expect "route tới service chưa chạy → 502 JSON" \
      "$(curl -s --max-time 10 "${auth[@]}" "$GATEWAY/api/rules/" | jq -r '.error')" "upstream_unavailable"
  fi
}

check_dev_tools() {
  section "Công cụ phát triển"
  expect "mailpit API" "$(http_status "$MAILPIT/api/v1/info")" "200"

  # Gửi cảnh báo mẫu từ network backend (đường P4 sẽ dùng), kiểm tra mailpit nhận được rồi xoá đi.
  local mail_query="subject%3A%22TEST%20${MARK}%22"
  if TEST_ALERT_ID="$MARK" ./scripts/send-test-alert.sh >/dev/null 2>&1; then
    sleep "$LOG_FLUSH_WAIT_SECONDS"
    expect "mailpit nhận email cảnh báo qua SMTP từ network backend" \
      "$(curl -s --max-time 5 "$MAILPIT/api/v1/search?query=${mail_query}" | jq -r '.messages_count')" "1"
    curl -s -o /dev/null --max-time 5 -X DELETE "$MAILPIT/api/v1/search?query=${mail_query}"
  else
    fail "gửi email cảnh báo mẫu qua SMTP" "scripts/send-test-alert.sh lỗi"
  fi

  if is_running mock-api; then
    expect "mock-api trực tiếp" \
      "$(curl -s --max-time 5 "$MOCK_API/scope/check?domain=blog.local" | jq -r '.allowed')" "true"
  else
    skip "mock-api" "profile mock chưa bật"
  fi

  if is_running webhook-sink; then
    expect "webhook-sink nhận POST" \
      "$(http_status -X POST -H 'content-type: application/json' -d '{"smoke":true}' "$WEBHOOK_SINK/alerts")" "200"
  else
    skip "webhook-sink" "profile mock chưa bật"
  fi

  if is_running swagger-ui; then
    expect "swagger-ui" "$(http_status "$SWAGGER_UI/")" "200"
    expect "swagger-ui phục vụ scope.yaml" "$(http_status "$SWAGGER_UI/contracts/scope.yaml")" "200"
  else
    skip "swagger-ui" "profile docs chưa bật"
  fi
}

check_containers
check_web_nginx
check_web_apache
check_event_bus
check_postgres
check_gateway
check_dev_tools

printf '\n%d passed, %d failed, %d skipped\n' "$passed" "$failed" "$skipped"
[[ "$failed" -eq 0 ]]
