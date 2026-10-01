#!/usr/bin/env bash
# Demo "phương án 2": dựng một target CỐ Ý dễ tổn thương (dev/vulnapp/app.py) sau Nginx thật,
# tự sinh traffic tấn công thật (SQL Injection, XSS, Path Traversal), và chạy toàn bộ pipeline
# C1 (thu log) -> C2 (parse) -> C3 (enrich) trên đó — dùng khi bạn CHƯA có website thật để test.
#
# Tự chứa, không cần quyền root, không đụng /etc/nginx hay cổng hệ thống:
# toàn bộ Nginx/Redis/Scope Service/target đều chạy với cấu hình & cổng riêng trong $WORKDIR.
#
# Yêu cầu: nginx, redis-server, python3 (+ pip install -r requirements.txt -r requirements-dev.txt),
#          pip install flask  (chỉ để chạy target demo, KHÔNG phải phụ thuộc của module).
#
# Chạy:  bash dev/e2e_vulnshop.sh
set -euo pipefail
cd "$(dirname "$0")/.."

W=${WORKDIR:-/tmp/waf-vulnshop}; rm -rf "$W"; mkdir -p "$W/nginx/logs" "$W/state" "$W/app-files"
REDIS_PORT=6391; NGINX_PORT=8089; SCOPE_PORT=8092; APP_PORT=8093
export BUS_BACKEND=redis REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0" SCOPE_URL="http://127.0.0.1:$SCOPE_PORT"
export C1_CONFIG="$W/c1.yaml" C1_STATE_PATH="$W/state/c1.json" LOG_LEVEL=INFO
DOMAIN=vulnshop.local

PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  nginx -c "$W/nginx/nginx.conf" -p "$W/nginx" -s stop 2>/dev/null || true
}
trap cleanup EXIT

cat > "$W/nginx/nginx.conf" <<CONF
pid $W/nginx/nginx.pid; error_log $W/nginx/logs/error.log;
events {}
http {
  client_body_temp_path $W/nginx/tmp1; proxy_temp_path $W/nginx/tmp2; fastcgi_temp_path $W/nginx/tmp3;
  uwsgi_temp_path $W/nginx/tmp4; scgi_temp_path $W/nginx/tmp5;
  log_format waf '\$remote_addr - \$remote_user [\$time_local] "\$request" \$status \$body_bytes_sent "\$http_referer" "\$http_user_agent" rt=\$request_time ck="\$cookie_PHPSESSID"';
  server {
    listen $NGINX_PORT;
    server_name $DOMAIN;
    access_log $W/nginx/logs/access.log waf;
    location / {
      proxy_pass http://127.0.0.1:$APP_PORT;
      proxy_set_header Host \$host;
      proxy_set_header X-Real-IP \$remote_addr;
    }
  }
}
CONF

cat > "$W/c1.yaml" <<CONF
sources:
  - {name: vulnshop-nginx, type: file, path: $W/nginx/logs/access.log, domain: $DOMAIN, server: nginx, start_at: end}
poll_interval: 0.1
CONF

echo "== khởi động target dễ tổn thương, Nginx, Redis, mock Scope Service"
APP_PORT=$APP_PORT APP_FILES_DIR="$W/app-files" APP_DB_PATH="$W/app.db" \
  python3 dev/vulnapp/app.py >"$W/app.log" 2>&1 & PIDS+=($!)
redis-server --port $REDIS_PORT --save "" --appendonly no --daemonize no >"$W/redis.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.tools.mock_scope --allow "$DOMAIN" --port $SCOPE_PORT >"$W/scope.log" 2>&1 & PIDS+=($!)
sleep 1
nginx -c "$W/nginx/nginx.conf" -p "$W/nginx"
sleep 0.5

echo "== khởi động C1 -> C2 -> C3"
python3 -m wafcollect.c2_parsing >"$W/c2.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.c3_enrichment >"$W/c3.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.c1_ingestion >"$W/c1.log" 2>&1 & PIDS+=($!)
sleep 1.5

U="http://127.0.0.1:$NGINX_PORT"
echo "== traffic bình thường (người dùng)"
curl -s -o /dev/null -b "PHPSESSID=user-sess-1" "$U/"
curl -s -o /dev/null -b "PHPSESSID=user-sess-1" "$U/product?id=2"

echo "== SQL Injection thật (UNION SELECT)"
curl -s -o /dev/null -b "PHPSESSID=atk-sess-1" -A "sqlmap/1.7" \
  "$U/product?id=1%20UNION%20SELECT%201%2Cname%2Cprice%20FROM%20sqlite_master--%20-"

echo "== XSS phản hồi thật"
curl -s -o /dev/null -b "PHPSESSID=atk-sess-1" -A "sqlmap/1.7" \
  --data-urlencode "q=<script>alert(1)</script>" -G "$U/search"

echo "== Path Traversal thật (đọc file ngoài thư mục cho phép)"
curl -s -o /dev/null -b "PHPSESSID=atk-sess-1" -A "sqlmap/1.7" \
  --data-urlencode "name=../secret.txt" -G "$U/file"

sleep 1.5
echo "== kiểm tra event log.enriched trong Redis"
python3 - <<PY
import json, redis, sys
r = redis.Redis.from_url("$REDIS_URL", decode_responses=True)
def evs(name): return [json.loads(f["payload"]) for _, f in r.xrange(f"waf:events:{name}")]
raw, norm, enr, failed = evs("log.raw.ingested"), evs("log.normalized"), evs("log.enriched"), evs("log.parse_failed")
print(f"raw={len(raw)} normalized={len(norm)} enriched={len(enr)} parse_failed={len(failed)}")
ok = True
def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg); ok &= bool(cond)

check(len(raw) == len(norm) == len(enr) == 5, "5 request đi hết pipeline C1->C2->C3")
sqli = next((e["data"] for e in enr if e["data"]["path"] == "/product" and "UNION" in e["data"]["query_string"]), None)
check(bool(sqli) and "UNION SELECT" in sqli["query_string"], "SQLi UNION SELECT được giải mã đúng: " + repr(sqli["query_string"] if sqli else None))
xss = next((e["data"] for e in enr if e["data"]["path"] == "/search"), None)
check(bool(xss) and "<script>alert(1)</script>" in xss["query_string"], "payload XSS còn nguyên trong dữ liệu enriched")
trav = next((e["data"] for e in enr if e["data"]["path"] == "/file"), None)
check(bool(trav) and "../secret.txt" in trav["query_string"], "path traversal ../secret.txt xuất hiện đúng: " + repr(trav["query_string"] if trav else None))
check("atk-sess-1" not in json.dumps(enr) and "user-sess-1" not in json.dumps(enr), "cookie phiên thật không lộ ra log.enriched (kể cả raw_line)")
sessions = {e["session_id"] for e in enr}
check(len(sessions) == 2, f"gom đúng 2 session (người dùng thường + attacker), thấy {len(sessions)}")
sys.exit(0 if ok else 1)
PY
echo "== log Nginx thật vừa ghi ra: $W/nginx/logs/access.log"
