#!/usr/bin/env bash
# Kiểm thử end-to-end trên máy cục bộ với Nginx + Redis THẬT (không dùng Docker).
# Yêu cầu: nginx, redis-server, python3 (đã pip install -r requirements.txt).
# Kịch bản (đúng "Định nghĩa hoàn thành" của đề tài, phần thu thập log):
#   Nginx thật ghi log liên tục -> C1 tail -> C2 parse -> C3 enrich -> kiểm tra event trong Redis,
#   gồm 1 request SQL Injection, 1 lần log rotation giữa chừng, và 1 domain ngoài phạm vi.
set -euo pipefail
cd "$(dirname "$0")/.."
W=${WORKDIR:-/tmp/waf-e2e}; rm -rf "$W"; mkdir -p "$W/nginx/logs" "$W/state"
REDIS_PORT=6390; NGINX_PORT=8088; SCOPE_PORT=8091
export BUS_BACKEND=redis REDIS_URL="redis://127.0.0.1:$REDIS_PORT/0" SCOPE_URL="http://127.0.0.1:$SCOPE_PORT"
export C1_CONFIG="$W/c1.yaml" C1_STATE_PATH="$W/state/c1.json" LOG_LEVEL=INFO
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; nginx -c "$W/nginx/nginx.conf" -p "$W/nginx" -s stop 2>/dev/null || true; }
trap cleanup EXIT

cat > "$W/nginx/nginx.conf" <<CONF
pid $W/nginx/nginx.pid; error_log $W/nginx/logs/error.log;
events {}
http {
  access_log off;
  client_body_temp_path $W/nginx/tmp1; proxy_temp_path $W/nginx/tmp2; fastcgi_temp_path $W/nginx/tmp3;
  uwsgi_temp_path $W/nginx/tmp4; scgi_temp_path $W/nginx/tmp5;
  log_format waf '\$remote_addr - \$remote_user [\$time_local] "\$request" \$status \$body_bytes_sent "\$http_referer" "\$http_user_agent" rt=\$request_time ck="\$cookie_PHPSESSID"';
  server { listen $NGINX_PORT; server_name localhost; access_log $W/nginx/logs/access.log waf;
           location / { return 200 "ok\n"; add_header Content-Type text/plain; } }
  server { listen $((NGINX_PORT+1)); server_name localhost; access_log syslog:server=127.0.0.1:5514,tag=blog_example waf;
           location / { return 200 "ok\n"; add_header Content-Type text/plain; } }
}
CONF
cat > "$W/c1.yaml" <<CONF
sources:
  - {name: shop-nginx, type: file, path: $W/nginx/logs/access.log, domain: shop.example.com, server: nginx, start_at: end}
  - {name: other-nginx, type: file, path: $W/nginx/logs/other.log, domain: secret.internal.com, server: nginx, start_at: end}
  - name: blog-syslog
    type: syslog
    protocol: udp
    listen: 127.0.0.1:5514
    server: nginx
    tag_domains: {blog_example: blog.example.com}
poll_interval: 0.1
CONF
: > "$W/nginx/logs/other.log"

redis-server --port $REDIS_PORT --save "" --appendonly no --daemonize no >"$W/redis.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.tools.mock_scope --allow shop.example.com,blog.example.com --port $SCOPE_PORT >"$W/scope.log" 2>&1 & PIDS+=($!)
nginx -c "$W/nginx/nginx.conf" -p "$W/nginx"
sleep 1
python3 -m wafcollect.c2_parsing >"$W/c2.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.c3_enrichment >"$W/c3.log" 2>&1 & PIDS+=($!)
python3 -m wafcollect.c1_ingestion >"$W/c1.log" 2>&1 & PIDS+=($!)
sleep 1.5

U="http://127.0.0.1:$NGINX_PORT"
echo "== gửi request thật tới Nginx"
curl -s -o /dev/null -b "PHPSESSID=sess-1" "$U/"
curl -s -o /dev/null -b "PHPSESSID=sess-1" "$U/products?id=1"
curl -s -o /dev/null -b "PHPSESSID=sess-1" -A "sqlmap/1.7" "$U/products?id=1%27%20OR%20%271%27%3D%271%27--%20"
curl -s -o /dev/null "$U/..%252f..%252fetc/passwd"
echo "== log rotation giữa chừng (rename + nginx -s reopen)"
curl -s -o /dev/null "$U/last-before-rotate"
mv "$W/nginx/logs/access.log" "$W/nginx/logs/access.log.1"
nginx -c "$W/nginx/nginx.conf" -p "$W/nginx" -s reopen
sleep 0.5
curl -s -o /dev/null "$U/first-after-rotate"
echo "== request tới server block ghi log qua syslog UDP (Nginx -> C1)"
curl -s -o /dev/null -b "PHPSESSID=blog-1" "http://127.0.0.1:$((NGINX_PORT+1))/blog/post?id=7"
echo "== dòng log của domain ngoài phạm vi (không được xử lý)"
echo '9.9.9.9 - - [21/Sep/2026:10:00:00 +0000] "GET /should-not-pass HTTP/1.1" 200 1 "-" "-"' >> "$W/nginx/logs/other.log"
sleep 2

python3 - <<PY
import json, redis, sys
r = redis.Redis.from_url("$REDIS_URL", decode_responses=True)
def evs(name): return [json.loads(f["payload"]) for _, f in r.xrange(f"waf:events:{name}")]
raw, norm, enr = evs("log.raw.ingested"), evs("log.normalized"), evs("log.enriched")
print(f"raw={len(raw)} normalized={len(norm)} enriched={len(enr)}")
blog = [e for e in enr if e["data"]["domain"] == "blog.example.com"]
enr = [e for e in enr if e["data"]["domain"] == "shop.example.com"]
raw = [e for e in raw if e["data"]["domain"] == "shop.example.com"]
norm = [e for e in norm if e["data"]["domain"] == "shop.example.com"]
paths = [e["data"]["path"] for e in enr]
print("paths:", paths)
ok = True
def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg); ok &= bool(cond)
check(len(raw) == len(norm) == len(enr) == 6, "6 request đi hết pipeline C1->C2->C3")
check(paths == ["/", "/products", "/products", "/../../etc/passwd", "/last-before-rotate", "/first-after-rotate"], "đủ request, đúng thứ tự, không mất dòng quanh rotation")
sqli = enr[2]["data"]
check(sqli["query_string"] == "id=1' OR '1'='1'-- ", "SQLi được giải mã đúng: " + repr(sqli["query_string"]))
check(sqli["headers"]["user_agent"] == "sqlmap/1.7", "user-agent được tách")
trav = enr[3]["data"]
check(trav["double_encoded"] and "../" in trav["path"], "double-encoding path traversal được phát hiện: " + repr(trav["path"]))
check(enr[0]["session_id"] == enr[1]["session_id"] == enr[2]["session_id"] != enr[3]["session_id"], "3 request cùng cookie+IP -> 1 phiên; request không cookie -> phiên khác")
check(enr[2]["data"]["session"]["request_count"] == 3 and enr[2]["data"]["frequency"]["requests_60s"] == 3, "đếm phiên/tần suất")
check("sess-1" not in json.dumps(enr), "giá trị cookie phiên không lọt xuống log.enriched ở bất kỳ trường nào")
check("should-not-pass" not in json.dumps(raw + norm + enr) and "secret.internal.com" not in json.dumps(raw + norm + enr), "domain ngoài phạm vi không được xử lý")
check(len(blog) == 1 and blog[0]["data"]["path"] == "/blog/post" and blog[0]["data"]["source"]["type"] == "syslog"
      and blog[0]["data"]["source"]["tag"] == "blog_example" and blog[0]["data"]["query_string"] == "id=7",
      "Nginx thật gửi syslog UDP -> C1 -> C2 -> C3, domain ánh xạ từ tag: " + (json.dumps(blog[0]["data"]["source"]) if blog else "KHÔNG CÓ EVENT"))
check("blog-1" not in json.dumps(blog), "cookie phiên của nguồn syslog cũng bị che")
print("XLEN failed:", r.xlen("waf:events:log.parse_failed"))
if len(sys.argv) > 1: print(json.dumps(enr[2], ensure_ascii=False, indent=2))
sys.exit(0 if ok else 1)
PY
