#!/usr/bin/env bash
# Kịch bản kiểm thử end-to-end trên server thật (docs/server-deployment.md, mục 5):
#   traffic thật -> (Cloudflare, tuỳ ACCESS_MODE) -> Nginx của máy chủ -> C1 -> C2 -> C3 -> detector
#   -> dashboard (P5) + cảnh báo (P4) + email (Mailpit).
# Mỗi lượt chạy có một mã đánh dấu riêng gắn vào URL, nên chỉ kiểm tra đúng request của lượt đó
# (dữ liệu cũ không làm kết quả đạt giả) và đo độ trễ từ lúc gửi request tới lúc kết quả xuất hiện.
#
# Dùng: scripts/e2e-server.sh [--via-localhost] [--with-scope] [--report FILE]
#   (mặc định)       chạy từ máy bất kỳ có Internet, đi qua domain công khai theo ACCESS_MODE
#   --via-localhost  chạy trên chính server, gọi Nginx tại 127.0.0.1 (bỏ qua DNS và Cloudflare;
#                    cổng khác 80 thì đặt E2E_NGINX_PORT)
#   --with-scope     thêm kịch bản vận hành: tắt giám sát một domain rồi bật lại (thêm khoảng 3 phút)
#   --report FILE    ghi bảng kết quả ra file Markdown
# Cấu hình: deploy/server/server.env (domain, tài khoản). Biến môi trường cùng tên được ưu tiên, ví dụ
# chạy từ máy cá nhân: DASHBOARD_PASSWORD=... MAIL_UI_PASSWORD=... scripts/e2e-server.sh
# Cần: bash, curl, python3.
# shellcheck disable=SC2015 # pass() luôn trả 0 nên "điều kiện && pass || fail" tương đương if/else
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="$ROOT/deploy/server"
DEADLINE_SECONDS=60
SCOPE_CACHE_SECONDS=${E2E_SCOPE_WAIT:-70}   # các service cache kết quả kiểm tra scope 60 giây
SCAN_REQUESTS=25           # đủ vượt ngưỡng request (20), lỗi (8), path khác nhau (12) trong 60 giây
RERUN_GAP_SECONDS=65       # P4 chống gửi trùng 60 giây theo (domain, IP, loại tấn công)
LAST_RUN_FILE="${TMPDIR:-/tmp}/waf-e2e-server.last"

VIA_LOCALHOST=false
WITH_SCOPE=false
REPORT_FILE=""
while (($#)); do
  case $1 in
    --via-localhost) VIA_LOCALHOST=true ;;
    --with-scope) WITH_SCOPE=true ;;
    --report) REPORT_FILE=${2:?--report cần tên file}; shift ;;
    *) echo "Tham số không hợp lệ: $1" >&2; exit 2 ;;
  esac
  shift
done

passed=0
failed=0
REPORT_ROWS=()
pass() { passed=$((passed + 1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { failed=$((failed + 1)); printf '  \033[31mFAIL\033[0m %s%s\n' "$1" "${2:+ - $2}"; }
section() { printf '\n\033[1m%s\033[0m\n' "$1"; }
die() { printf 'LỖI: %s\n' "$*" >&2; exit 2; }
now() { python3 -c 'import time; print(f"{time.time():.3f}")'; }
elapsed() { python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.1f}')" "$1" "$2"; }
report() { REPORT_ROWS+=("| $1 | $2 | $3 | $4 |"); }

# shellcheck source-path=SCRIPTDIR source=lib/server-env.sh
. "$ROOT/scripts/lib/server-env.sh"

# Đọc server.env; biến đã có trong môi trường được giữ nguyên.
load_config() {
  local file="$SERVER_DIR/server.env"
  [[ -f $file ]] || file="$SERVER_DIR/server.env.example"
  load_server_env "$file"
  local var
  for var in WAF_DOMAIN MAIL_DOMAIN DEMO_DOMAINS DASHBOARD_USER DASHBOARD_PASSWORD MAIL_UI_USER MAIL_UI_PASSWORD; do
    [[ -n ${!var:-} ]] || die "thiếu $var (đặt trong deploy/server/server.env hoặc biến môi trường)"
  done
  read -r -a DOMAINS <<< "$DEMO_DOMAINS"
  ((${#DOMAINS[@]} >= 2)) || die "cần ít nhất 2 domain trong DEMO_DOMAINS"
}

setup_http() {
  CURL=(curl -sS --max-time 15 -A "Mozilla/5.0 (X11; Linux x86_64) waf-e2e")
  if [[ $VIA_LOCALHOST == true ]]; then
    # Nginx của máy chủ: HTTPS khi direct-https, còn lại HTTP (với cloudflare, HTTPS do Cloudflare phục vụ).
    local domain port=80
    SCHEME=http
    if [[ $ACCESS_MODE == direct-https ]]; then
      SCHEME=https
      port=443
      [[ ${LETSENCRYPT_STAGING:-false} == true ]] && CURL+=(-k)
    fi
    for domain in "$WAF_DOMAIN" "$MAIL_DOMAIN" "${DOMAINS[@]}"; do
      CURL+=(--connect-to "$domain:$port:127.0.0.1:${E2E_NGINX_PORT:-$port}")
    done
  else
    SCHEME=$PUBLIC_SCHEME
    [[ $ACCESS_MODE == direct-https && ${LETSENCRYPT_STAGING:-false} == true ]] && CURL+=(-k)
  fi
  WAF_URL="$SCHEME://$WAF_DOMAIN"
  MAIL_URL="$SCHEME://$MAIL_DOMAIN"
}

http_code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

# Gửi một request tới site demo; in mã HTTP. Mã 403 kèm "server: cloudflare" = bị Cloudflare chặn.
send() {
  local headers code
  headers="$(mktemp)"
  code="$("${CURL[@]}" -o /dev/null -D "$headers" -w '%{http_code}' -b "sessionid=$MARK" "$1" 2>/dev/null)" || code=000
  if [[ $code == 403 ]] && grep -qi '^server: cloudflare' "$headers"; then
    code="403-cloudflare"
  fi
  rm -f "$headers"
  printf '%s' "$code"
}

api_get() { "${CURL[@]}" -H "authorization: Bearer $TOKEN" "$WAF_URL$1"; }

# Dòng trong JSON trả về (key là danh sách) có URL chứa marker: in "<số dòng> <giá trị field 1> <field 2>".
match_rows() {
  M="$2" python3 -c '
import json, os, sys
key, marker = sys.argv[1], os.environ["M"]
fields = sys.argv[2:]
try:
    rows = json.load(sys.stdin).get(key) or []
except ValueError:
    rows = []
hits = [r for r in rows if marker in (r.get("url") or "")]
def values(field):
    out = set()
    for r in hits:
        v = r.get(field)
        out.update(v if isinstance(v, list) else [v])
    return ",".join(sorted(str(v) for v in out if v)) or "-"
print(len(hits), *[values(f) for f in fields])
' "$1" "${@:3}"
}

# Chờ tới khi <lệnh> in ra dòng có số đầu >= 1; in "<giây chờ> <phần còn lại của dòng>" hoặc trả 1.
wait_match() {
  local sent=$1 line count
  shift
  local deadline=$(( $(date +%s) + DEADLINE_SECONDS ))
  while (( $(date +%s) < deadline )); do
    line="$("$@" 2>/dev/null)" || line="0"
    read -r count _ <<< "$line"
    if [[ $count =~ ^[0-9]+$ ]] && (( count >= 1 )); then
      printf '%s %s' "$(elapsed "$sent" "$(now)")" "$line"
      return 0
    fi
    sleep 1
  done
  return 1
}

attacks_of() { api_get "/api/dashboard/attacks?limit=500&domain=$1" | match_rows attacks "$2" attack_type src_ip; }
alerts_of() { api_get "/api/alerts/?limit=200&domain=$1" | match_rows alerts "$2" attack_types src_ip; }
emails_with() {
  "${CURL[@]}" -u "$MAIL_UI_USER:$MAIL_UI_PASSWORD" "$MAIL_URL/api/v1/search?query=$1" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("messages_count", 0))'
}

wait_before_rerun() {
  local last gap
  last="$(cat "$LAST_RUN_FILE" 2>/dev/null || echo 0)"
  gap=$(( $(date +%s) - last ))
  if (( gap < RERUN_GAP_SECONDS )); then
    echo "Lượt trước chạy cách đây ${gap}s: chờ $((RERUN_GAP_SECONDS - gap))s để P4 không chặn cảnh báo trùng..."
    sleep $((RERUN_GAP_SECONDS - gap))
  fi
  date +%s > "$LAST_RUN_FILE"
}

step_ready() {
  section "0. Hệ thống sẵn sàng"
  local code login
  code="$(http_code "$WAF_URL/healthz")"
  [[ $code == 200 ]] && pass "gateway qua $WAF_DOMAIN" || fail "gateway qua $WAF_DOMAIN" "HTTP $code"
  code="$(http_code -X POST -H 'content-type: application/json' \
    -d '{"client_id":"admin","client_secret":"wrong-password"}' "$WAF_URL/api/auth/token")"
  [[ $code == 401 ]] && pass "đăng nhập sai bị từ chối (HTTP 401)" || fail "đăng nhập sai bị từ chối" "HTTP $code"
  login="$(python3 -c 'import json,sys; print(json.dumps({"client_id": sys.argv[1], "client_secret": sys.argv[2]}))' \
    "$DASHBOARD_USER" "$DASHBOARD_PASSWORD")"
  TOKEN="$("${CURL[@]}" -X POST -H 'content-type: application/json' -d "$login" "$WAF_URL/api/auth/token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token", ""))' 2>/dev/null)"
  [[ -n $TOKEN ]] && pass "đăng nhập dashboard bằng tài khoản $DASHBOARD_USER" || { fail "đăng nhập dashboard"; summary; exit 1; }
  code="$(http_code "$MAIL_URL/api/v1/messages")"
  [[ $code == 401 ]] && pass "Mailpit yêu cầu đăng nhập (HTTP 401)" || fail "Mailpit yêu cầu đăng nhập" "HTTP $code"
  code="$(http_code -u "$MAIL_UI_USER:$MAIL_UI_PASSWORD" "$MAIL_URL/api/v1/messages")"
  [[ $code == 200 ]] && pass "đăng nhập Mailpit" || fail "đăng nhập Mailpit" "HTTP $code"

  # IP của máy chạy kiểm thử như Nginx của máy chủ thấy (đã lấy IP thật nếu đi qua Cloudflare).
  CLIENT_IP="$("${CURL[@]}" "$WAF_URL/_waf/client-ip" 2>/dev/null | tr -d '[:space:]')"
  [[ $CLIENT_IP =~ ^[0-9a-fA-F:.]+$ ]] || CLIENT_IP=""
  echo "  IP của máy chạy kiểm thử (theo Nginx của máy chủ): ${CLIENT_IP:-không xác định}"
}

step_benign() {
  section "1. Traffic bình thường (không được báo nhầm)"
  local domain path code bad=0 sent=0
  for domain in "${DOMAINS[@]}"; do
    for path in "/?src=$BMARK" "/p/42?src=$BMARK" "/search?q=laptop&src=$BMARK" "/api/products?id=7&src=$BMARK"; do
      code="$(send "$SCHEME://$domain$path")"
      sent=$((sent + 1))
      [[ $code == 200 ]] || { bad=$((bad + 1)); echo "    $domain$path -> HTTP $code"; }
    done
  done
  ((bad == 0)) && pass "$sent request bình thường tới ${#DOMAINS[@]} site trả 200" || fail "request bình thường" "$bad request lỗi"
}

step_scan() {
  SCAN_DOMAIN="${DOMAINS[${#DOMAINS[@]}-1]}"
  section "2. Dò quét từ một nguồn: $SCAN_REQUESTS đường dẫn quản trị không tồn tại trên $SCAN_DOMAIN"
  local i code blocked=0
  SCAN_SENT="$(now)"
  for ((i = 1; i <= SCAN_REQUESTS; i++)); do
    code="$(send "$SCHEME://$SCAN_DOMAIN/$SMARK/admin-$i.php")"
    [[ $code == 404 ]] || blocked=$((blocked + 1))
  done
  ((blocked == 0)) && pass "$SCAN_REQUESTS request dò quét tới được máy chủ (404)" \
    || fail "request dò quét" "$blocked request không trả 404 (Cloudflare chặn?)"
}

# Tấn công payload: mỗi site một loại; site dò quét ở bước 2 vừa có cảnh báo nên chỉ kiểm tra phát hiện
# (detector giãn cách cảnh báo 15 giây cho cùng một nguồn).
ATTACK_TYPES=(sql_injection xss path_traversal)
# @M@ được thay bằng mã đánh dấu của từng request.
ATTACK_PATHS=(
  '/api/products?id=1%20UNION%20SELECT%20username,password%20FROM%20users--&m=@M@'
  '/search?q=%3Cscript%3Ealert(%22@M@%22)%3C/script%3E'
  '/api/load?file=../../../../etc/passwd&m=@M@'
)
step_attacks() {
  section "3. Tấn công payload"
  local i domain path code
  ATTACK_DOMAINS=()
  ATTACK_SENT=()
  for i in "${!ATTACK_TYPES[@]}"; do
    domain="${DOMAINS[i % ${#DOMAINS[@]}]}"
    path="${ATTACK_PATHS[i]//@M@/${AMARK}$i}"
    ATTACK_DOMAINS+=("$domain")
    ATTACK_SENT+=("$(now)")
    code="$(send "$SCHEME://$domain$path")"
    [[ $code == 200 ]] && pass "${ATTACK_TYPES[i]} -> $domain (HTTP 200)" \
      || fail "${ATTACK_TYPES[i]} -> $domain" "HTTP $code$([[ $code == 403-cloudflare ]] && echo ', bị Cloudflare chặn trước khi tới máy chủ')"
  done
}

check_src_ip() {
  local label=$1 ips=$2
  [[ -z $CLIENT_IP ]] && return
  [[ $ips == "$CLIENT_IP" ]] && pass "$label ghi đúng IP của client ($ips)" \
    || fail "$label ghi IP $ips" "cần $CLIENT_IP (xem ACCESS_MODE)"
}

step_detection() {
  section "4. Phát hiện (lịch sử tấn công trên dashboard, P5)"
  local i result latency count types ips
  for i in "${!ATTACK_TYPES[@]}"; do
    if result="$(wait_match "${ATTACK_SENT[i]}" attacks_of "${ATTACK_DOMAINS[i]}" "${AMARK}$i")"; then
      read -r latency count types ips <<< "$result"
      if [[ ,$types, == *",${ATTACK_TYPES[i]},"* ]]; then
        pass "${ATTACK_TYPES[i]} trên ${ATTACK_DOMAINS[i]} được phát hiện sau ${latency}s"
        report "Phát hiện ${ATTACK_TYPES[i]}" "${ATTACK_DOMAINS[i]}" "đạt ($types)" "$latency"
      else
        fail "${ATTACK_TYPES[i]} trên ${ATTACK_DOMAINS[i]}" "phát hiện thành $types"
        report "Phát hiện ${ATTACK_TYPES[i]}" "${ATTACK_DOMAINS[i]}" "sai loại ($types)" "$latency"
      fi
      ((i == 0)) && check_src_ip "kết quả phát hiện" "$ips"
    else
      fail "${ATTACK_TYPES[i]} trên ${ATTACK_DOMAINS[i]}" "không xuất hiện sau ${DEADLINE_SECONDS}s"
      report "Phát hiện ${ATTACK_TYPES[i]}" "${ATTACK_DOMAINS[i]}" "không đạt" "-"
    fi
  done
  local domain hits
  count=0
  for domain in "${DOMAINS[@]}"; do
    read -r hits _ <<< "$(attacks_of "$domain" "$BMARK")"
    count=$((count + ${hits:-0}))
  done
  ((count == 0)) && pass "không có request bình thường nào bị báo là tấn công" || fail "báo nhầm" "$count request bình thường bị phát hiện"
  report "Traffic bình thường" "tất cả" "$count báo nhầm" "-"
}

step_alerts() {
  section "5. Cảnh báo (P4) và email (Mailpit)"
  local result latency count types ips i
  if result="$(wait_match "$SCAN_SENT" alerts_of "$SCAN_DOMAIN" "$SMARK")"; then
    read -r latency count types ips <<< "$result"
    [[ ,$types, == *",path_scan,"* ]] && pass "cảnh báo dò quét trên $SCAN_DOMAIN sau ${latency}s ($types)" \
      || fail "cảnh báo dò quét" "loại $types, cần có path_scan"
    report "Cảnh báo dò quét (hành vi)" "$SCAN_DOMAIN" "đạt ($types)" "$latency"
  else
    fail "cảnh báo dò quét trên $SCAN_DOMAIN" "không có sau ${DEADLINE_SECONDS}s"
    report "Cảnh báo dò quét (hành vi)" "$SCAN_DOMAIN" "không đạt" "-"
  fi
  for i in "${!ATTACK_TYPES[@]}"; do
    [[ ${ATTACK_DOMAINS[i]} == "$SCAN_DOMAIN" ]] && continue
    if result="$(wait_match "${ATTACK_SENT[i]}" alerts_of "${ATTACK_DOMAINS[i]}" "${AMARK}$i")"; then
      read -r latency _ <<< "$result"
      pass "cảnh báo ${ATTACK_TYPES[i]} trên ${ATTACK_DOMAINS[i]} sau ${latency}s"
      report "Cảnh báo ${ATTACK_TYPES[i]}" "${ATTACK_DOMAINS[i]}" "đạt" "$latency"
    else
      fail "cảnh báo ${ATTACK_TYPES[i]} trên ${ATTACK_DOMAINS[i]}" "không có sau ${DEADLINE_SECONDS}s"
      report "Cảnh báo ${ATTACK_TYPES[i]}" "${ATTACK_DOMAINS[i]}" "không đạt" "-"
    fi
  done
  if result="$(wait_match "$SCAN_SENT" emails_with "$MARK")"; then
    read -r latency count <<< "$result"
    pass "Mailpit nhận $count email cảnh báo của lượt chạy"
    report "Email cảnh báo" "$MAIL_DOMAIN" "$count email" "$latency"
  else
    fail "email cảnh báo trong Mailpit" "không có sau ${DEADLINE_SECONDS}s"
    report "Email cảnh báo" "$MAIL_DOMAIN" "không đạt" "-"
  fi
}

set_scope() {
  local body
  body="$(printf '{"target":"%s","target_type":"domain","enabled":%s,"note":"%s"}' "$1" "$2" "$3")"
  "${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST -H "authorization: Bearer $TOKEN" \
    -H 'content-type: application/json' -d "$body" "$WAF_URL/api/scope/allowlist"
}

step_scope() {
  local domain="${DOMAINS[0]}" sent result latency count code
  section "6. Vận hành: tắt giám sát $domain rồi bật lại (khoảng 3 phút)"
  code="$(set_scope "$domain" false "e2e: tạm tắt giám sát")"
  [[ $code == 200 ]] || { fail "tắt giám sát $domain" "HTTP $code"; return; }
  # Bị ngắt giữa chừng thì vẫn bật lại giám sát (domain đã được kiểm tra hợp lệ trong server.env).
  # shellcheck disable=SC2064 # cố ý thay giá trị domain ngay lúc đặt trap
  trap "set_scope '$domain' true server-up > /dev/null" EXIT
  pass "đã tắt giám sát $domain qua API (như thao tác ở mục Domain của dashboard)"
  echo "  chờ ${SCOPE_CACHE_SECONDS}s để các service hết cache kết quả scope..."
  sleep "$SCOPE_CACHE_SECONDS"
  send "$SCHEME://$domain/api/products?id=1%20UNION%20SELECT%20pw--&m=$OMARK" > /dev/null
  sleep 20
  read -r count _ <<< "$(attacks_of "$domain" "$OMARK")"
  ((${count:-0} == 0)) && pass "tấn công vào $domain khi bị tắt giám sát không được xử lý" \
    || fail "domain bị tắt giám sát" "vẫn có $count kết quả phát hiện"
  report "Tấn công khi domain bị tắt giám sát" "$domain" "$([[ ${count:-0} == 0 ]] && echo 'không xử lý (đúng)' || echo 'vẫn xử lý')" "-"

  code="$(set_scope "$domain" true "server-up")"
  trap - EXIT
  [[ $code == 200 ]] && pass "đã bật lại giám sát $domain" || fail "bật lại giám sát $domain" "HTTP $code"
  echo "  chờ ${SCOPE_CACHE_SECONDS}s..."
  sleep "$SCOPE_CACHE_SECONDS"
  sent="$(now)"
  send "$SCHEME://$domain/api/products?id=1%20UNION%20SELECT%20pw--&m=$NMARK" > /dev/null
  if result="$(wait_match "$sent" attacks_of "$domain" "$NMARK")"; then
    read -r latency _ <<< "$result"
    pass "bật lại thì tấn công được phát hiện sau ${latency}s"
    report "Tấn công sau khi bật lại giám sát" "$domain" "đạt" "$latency"
  else
    fail "phát hiện sau khi bật lại giám sát" "không có sau ${DEADLINE_SECONDS}s"
    report "Tấn công sau khi bật lại giám sát" "$domain" "không đạt" "-"
  fi
}

summary() {
  printf '\n%d passed, %d failed\n' "$passed" "$failed"
  ((${#REPORT_ROWS[@]})) || return 0
  local table
  table="$(printf '%s\n' "| Bước | Domain | Kết quả | Độ trễ (s) |" "|---|---|---|---|" "${REPORT_ROWS[@]}")"
  printf '\nKết quả đo (mã lượt chạy %s, độ trễ tính từ lúc gửi request tới lúc kết quả xuất hiện qua API, độ phân giải khoảng 1 giây):\n%s\n' \
    "$MARK" "$table"
  if [[ -n $REPORT_FILE ]]; then
    {
      echo "# Kiểm thử end-to-end trên server"
      echo
      echo "- Thời điểm: $(date '+%Y-%m-%d %H:%M:%S %z'); mã lượt chạy: \`$MARK\`"
      echo "- Đường đi: $([[ $VIA_LOCALHOST == true ]] && echo 'Nginx của máy chủ qua 127.0.0.1' || echo "Internet ($ACCESS_MODE, $SCHEME) -> Nginx của máy chủ")"
      echo "- Kết quả: $passed kiểm tra đạt, $failed không đạt"
      echo
      echo "$table"
    } > "$REPORT_FILE"
    echo "Đã ghi $REPORT_FILE"
  fi
}

main() {
  load_config
  setup_http
  wait_before_rerun
  MARK="e2e$(date +%s)$((RANDOM % 900 + 100))"
  BMARK="${MARK}ok"
  SMARK="${MARK}scan"
  AMARK="${MARK}atk"
  OMARK="${MARK}off"
  NMARK="${MARK}on"
  echo "Kiểm thử end-to-end, mã lượt chạy: $MARK ($([[ $VIA_LOCALHOST == true ]] && echo 'qua 127.0.0.1' || echo "ACCESS_MODE=$ACCESS_MODE, $SCHEME"))"
  step_ready
  step_benign
  step_scan
  step_attacks
  step_detection
  step_alerts
  [[ $WITH_SCOPE == true ]] && step_scope
  summary
  ((failed == 0))
}

main
