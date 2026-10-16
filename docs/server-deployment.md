# Triển khai trên server thật

Dựng toàn bộ hệ thống trên một máy chủ Ubuntu bằng một lệnh `make server-up`. Nginx chạy trực tiếp trên máy chủ (không Docker), mọi service còn lại chạy Docker. DNS có thể đi qua Cloudflare hoặc trỏ thẳng về máy chủ, chọn bằng biến `ACCESS_MODE` (mục 1).

```
Người dùng ─HTTPS─► Cloudflare ─HTTP:80─► Nginx (Ubuntu)        ACCESS_MODE=cloudflare
Người dùng ─HTTPS:443 (Let's Encrypt)───► Nginx (Ubuntu)        ACCESS_MODE=direct-https
Người dùng ─HTTP:80─────────────────────► Nginx (Ubuntu)        ACCESS_MODE=direct-http
                                            ├─ service1/2/3.tranbadat.vn  site demo, log -> /var/log/nginx/waf/<domain>.access.log
                                            ├─ waf.tranbadat.vn           -> 127.0.0.1:8080  gateway (dashboard realtime)
                                            └─ mailpit.tranbadat.vn       -> 127.0.0.1:8025  Mailpit (có đăng nhập)

Docker (ENV=production):
  platform  Kafka, Postgres, P1–P5, gateway, Mailpit
  pipeline  C2, C3, detector, rule-api
  sensor    C1, đọc /var/log/nginx/waf (chỉ đọc)
```

Request tới site demo được Nginx ghi log; C1 đọc log, pipeline phát hiện tấn công, dashboard `waf.tranbadat.vn` hiển thị realtime và P4 gửi email cảnh báo vào Mailpit (`mailpit.tranbadat.vn`).

## Yêu cầu

- Ubuntu 22.04 hoặc 24.04 (Debian cũng được), user có quyền sudo. Script tự cài Nginx, Docker Engine và Compose plugin (kho apt chính thức của Docker) nếu chưa có.
- Tài nguyên (ước lượng, chưa đo trên server): khi tắt observability, tối thiểu 2 vCPU và 4 GB RAM; Kafka, Postgres và hai JVM (detector, rule-api) chiếm phần lớn bộ nhớ. Lần đầu build image Java (Maven) tốn thêm bộ nhớ, nên thêm swap nếu máy chỉ có 4 GB.
- Mở cổng 22 (SSH), 80 và (với `direct-https`) 443. Không cần mở cổng nào khác.

## 1. DNS và cách truy cập (`ACCESS_MODE`)

Trong cả ba chế độ, tạo bản ghi A cho `service1`, `service2`, `service3`, `waf`, `mailpit` trỏ về IP máy chủ, rồi đặt `ACCESS_MODE` trong `deploy/server/server.env`. Script tự làm phần còn lại.

| `ACCESS_MODE` | DNS | HTTPS | Script tự làm |
|---|---|---|---|
| `cloudflare` (mặc định) | Proxied qua Cloudflare (đám mây cam) | Cloudflare phục vụ, nối về máy chủ bằng HTTP | Lấy IP thật của client từ header `CF-Connecting-IP` (chỉ tin dải IP của Cloudflare) |
| `direct-https` | Trỏ thẳng về máy chủ (ở Cloudflare thì để DNS only, đám mây xám) | Chứng chỉ Let's Encrypt trên máy chủ | Cài certbot, lấy một chứng chỉ cho cả 5 domain (xác minh HTTP-01 qua thư mục `/var/www/letsencrypt`), bật cổng 443, chuyển HTTP sang HTTPS, tự gia hạn (`certbot.timer`) và nạp lại Nginx sau khi gia hạn |
| `direct-http` | Trỏ thẳng về máy chủ | Không có | Chỉ HTTP, không mã hoá: chỉ nên dùng thử |

**Cloudflare (`cloudflare`):**

1. SSL/TLS → Overview chọn Flexible; Edge Certificates bật Always Use HTTPS.
2. Lưu ý khi demo tấn công:
   - Cloudflare có thể chặn hoặc bắt xác minh (challenge) một số request trước khi tới máy chủ (managed ruleset, Bot Fight Mode, Browser Integrity Check). Request bị chặn ở Cloudflare không có trong log nên hệ thống không thấy. Khi demo, tắt Bot Fight Mode hoặc tạo WAF custom rule với hành động Skip cho hostname của các site demo.
   - Cloudflare cache tệp tĩnh (`.css`, `.js`, ảnh): request trúng cache không tới máy chủ. Trang HTML và `/api/` mặc định không bị cache.

**Trỏ thẳng (`direct-https`):**

1. DNS phải trỏ đúng và cổng 80 phải mở từ Internet trước khi chạy, vì Let's Encrypt kiểm tra qua HTTP. Lỗi thì script dừng ngay sau bước cấu hình Nginx (trước khi build image) và nêu cách kiểm tra.
2. Nên đặt `LETSENCRYPT_EMAIL` để nhận thông báo khi chứng chỉ sắp hết hạn. Khi thử nhiều lần, đặt `LETSENCRYPT_STAGING=true` để tránh giới hạn số lần cấp của Let's Encrypt (chứng chỉ thử không được trình duyệt tin), xong thì đặt lại `false` và chạy lại.
3. Thêm hoặc đổi domain rồi chạy lại `make server-up`: chứng chỉ được cấp lại cho danh sách domain mới.

## 2. Chạy

```bash
git clone <repo> waf && cd waf
sudo apt-get install -y make        # nếu máy chưa có make
make server-up
```

Muốn đổi domain hoặc tuỳ chọn trước lần chạy đầu: `cp deploy/server/server.env.example deploy/server/server.env` rồi sửa. Xem trước cấu hình Nginx sẽ cài (không thay đổi máy): `./scripts/server-up.sh --print-nginx`; biến môi trường ghi đè `server.env`, ví dụ `ACCESS_MODE=direct-https ./scripts/server-up.sh --print-nginx`.

`make server-up` ([`scripts/server-up.sh`](../scripts/server-up.sh)) làm lần lượt:

1. Cài gói còn thiếu (Nginx, Docker...). User vừa được thêm vào nhóm `docker` thì script tự chạy tiếp với nhóm đó, không cần đăng nhập lại.
2. Tạo `deploy/server/server.env` (lần đầu), sinh mật khẩu dashboard và Mailpit nếu còn trống.
3. Sinh `.env` cho các stack, ghi giá trị riêng của máy chủ vào `deploy/platform/.env` (`AUTH_CLIENTS`, `SCOPE_ALLOWLIST`, `DASHBOARD_LINKS`, `GATEWAY_TRUSTED_PROXY`), build image và dựng 3 stack với `ENV=production`. Observability chỉ được dựng khi `OBSERVABILITY=true`.
4. Cấu hình Nginx của máy chủ:
   - `/etc/nginx/conf.d/waf-logformat.conf`: định dạng log `waf_combined`;
   - `/etc/nginx/conf.d/waf-cloudflare-realip.conf`: với `cloudflare`, dải IP Cloudflare tải mới mỗi lần chạy (không tải được thì dùng [`cloudflare-ips.txt`](../deploy/server/nginx/cloudflare-ips.txt)), để `$remote_addr` là IP thật của client; chế độ khác để trống;
   - `/etc/nginx/sites-available/waf.conf` (liên kết vào `sites-enabled`): dashboard, Mailpit và từng site demo, cổng 80 hoặc 443 theo chế độ; `/etc/nginx/snippets/waf-server.conf`: phần chung của các server block (chứng chỉ TLS với `direct-https`);
   - mật khẩu Mailpit `/etc/nginx/waf-mailpit.htpasswd`, nội dung site demo `/var/www/waf-demo/<domain>`, logrotate `/etc/logrotate.d/waf-sites`.
5. Với `direct-https`: lấy chứng chỉ Let's Encrypt rồi chuyển Nginx sang HTTPS.
6. Thêm các site demo vào scope qua API (chạy lại không tạo trùng).
7. Kiểm tra nhanh qua Nginx của máy chủ (đúng scheme và cổng của chế độ) rồi in địa chỉ và tài khoản đăng nhập.

Chạy lại `make server-up` sau khi `git pull` hoặc sửa `server.env`: script cập nhật cấu hình, build lại image đã thay đổi và giữ nguyên dữ liệu.

## 3. Cấu hình (`deploy/server/server.env`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WAF_DOMAIN` | `waf.tranbadat.vn` | Dashboard realtime |
| `MAIL_DOMAIN` | `mailpit.tranbadat.vn` | Mailpit (email cảnh báo) |
| `DEMO_DOMAINS` | `service1/2/3.tranbadat.vn` | Các site demo được giám sát, cách nhau bằng dấu cách |
| `ACCESS_MODE` | `cloudflare` | `cloudflare`, `direct-https` hoặc `direct-http` (mục 1) |
| `LETSENCRYPT_EMAIL` | rỗng | `direct-https`: email nhận thông báo của Let's Encrypt |
| `LETSENCRYPT_STAGING` | `false` | `direct-https`: `true` = lấy chứng chỉ thử |
| `OBSERVABILITY` | `false` | Dựng Prometheus, Grafana, Loki và exporter |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `admin` / tự sinh | Tài khoản dashboard |
| `MAIL_UI_USER` / `MAIL_UI_PASSWORD` | `admin` / tự sinh | Tài khoản trang Mailpit |

`server.env` chứa mật khẩu nên không được commit (đã có trong `.gitignore`). Cấu hình môi trường của các stack nằm ở `deploy/<stack>/env/production.env`.

## 4. Demo thủ công

1. Mở `https://waf.tranbadat.vn`, đăng nhập bằng tài khoản dashboard.
2. Gửi vài request tấn công tới site demo:
   ```bash
   curl "https://service1.tranbadat.vn/search?q=%3Cscript%3Ealert(1)%3C/script%3E"
   curl "https://service2.tranbadat.vn/api/products?id=1%20UNION%20SELECT%20password%20FROM%20users--"
   curl "https://service3.tranbadat.vn/api/load?file=../../../../etc/passwd"
   ```
3. Sự kiện xuất hiện ở mục Sự kiện realtime; cảnh báo có email trong `https://mailpit.tranbadat.vn`.

## 5. Kịch bản kiểm thử end-to-end và đo độ trễ

[`scripts/e2e-server.sh`](../scripts/e2e-server.sh) gửi traffic thật tới hệ thống đã triển khai, kiểm tra từng khâu qua API công khai (dashboard, cảnh báo, Mailpit) và đo độ trễ. Mỗi lượt chạy có một mã đánh dấu riêng (`e2e<thời điểm><số ngẫu nhiên>`) gắn vào URL và cookie `sessionid`, nên chỉ đếm đúng request của lượt đó; dữ liệu của các lượt trước không làm kết quả đạt giả.

```bash
# Trên server (đọc domain, tài khoản và ACCESS_MODE từ deploy/server/server.env), đi qua domain công khai:
make e2e-server
# Thêm kịch bản tắt/bật giám sát một domain và ghi bảng kết quả ra file:
make e2e-server ARGS="--with-scope --report e2e-server-report.md"
# Từ máy cá nhân (cần repo, curl, python3), truyền tài khoản qua biến môi trường:
DASHBOARD_PASSWORD=... MAIL_UI_PASSWORD=... ./scripts/e2e-server.sh
# Trên server, gọi thẳng Nginx tại 127.0.0.1 (khi DNS chưa sẵn sàng):
make e2e-server ARGS=--via-localhost
```

| Bước | Thao tác | Kết quả mong đợi |
|---|---|---|
| 0. Sẵn sàng | Gọi `/healthz` của gateway; đăng nhập dashboard bằng mật khẩu sai rồi đúng; gọi API Mailpit không có và có tài khoản | 200; 401 rồi nhận token; 401 rồi 200 |
| 1. Traffic bình thường | 4 request bình thường tới mỗi site demo (trang chủ, sản phẩm, tìm kiếm, API) | 200, không request nào bị ghi nhận là tấn công |
| 2. Dò quét | 25 request tới các đường dẫn quản trị không tồn tại trên một site, từ cùng một nguồn | 404; dashboard và P4 có cảnh báo hành vi có `path_scan` (không cần rule nào khớp) |
| 3. Tấn công payload | SQL injection, XSS, path traversal, mỗi loại vào một site | 200 (request tới được máy chủ; `403-cloudflare` nghĩa là Cloudflare đã chặn) |
| 4. Phát hiện | Theo dõi lịch sử tấn công (P5) | Mỗi tấn công được phát hiện đúng loại, đúng domain, `src_ip` là IP thật của máy chạy kiểm thử (Nginx của máy chủ trả qua `/_waf/client-ip` trên domain dashboard) |
| 5. Cảnh báo và email | Theo dõi P4 và Mailpit | Có cảnh báo cho dò quét, SQL injection, XSS; Mailpit có email tương ứng |
| 6. Vận hành (`--with-scope`) | Tắt giám sát site đầu tiên qua API, chờ hết cache scope, tấn công; bật lại, tấn công lần nữa | Khi tắt: không có kết quả phát hiện; khi bật lại: phát hiện bình thường |

Kết quả in ra dạng PASS/FAIL cho từng kiểm tra, kèm bảng đo độ trễ từ lúc gửi request tới lúc kết quả xuất hiện qua API (độ phân giải khoảng 1 giây vì script hỏi API mỗi giây). Mã thoát khác 0 nếu có kiểm tra không đạt. Lưu ý:

- Hai lượt chạy cần cách nhau ít nhất 60 giây vì P4 không gửi lại cảnh báo cùng (domain, IP, loại tấn công) trong 60 giây; script tự chờ nếu lượt trước vừa chạy.
- Ở bước 3, site vừa bị dò quét chỉ được kiểm tra phát hiện, không kiểm tra cảnh báo, vì detector giãn cách cảnh báo 15 giây cho cùng một nguồn.
- Kiểm thử cục bộ (bản chạy toàn bộ trong Docker trên máy dev) dùng `make e2e`.

## 6. Vận hành

- Trạng thái: `make deploy-ps ENV=production`. Log một service: `docker logs -f waf-pipeline-detector-1`.
- Dừng toàn bộ container (giữ dữ liệu, giữ cấu hình Nginx): `make server-down`. Bật lại: `make server-up`.
- Observability: đặt `OBSERVABILITY=true` rồi chạy lại `make server-up`. Grafana chỉ nghe `127.0.0.1:3000`; xem qua SSH tunnel: `ssh -L 3000:127.0.0.1:3000 <user>@<server>` rồi mở http://localhost:3000.
- Gỡ cấu hình Nginx: `sudo rm /etc/nginx/sites-enabled/waf.conf /etc/nginx/conf.d/waf-*.conf && sudo systemctl reload nginx`.

## 7. Bảo mật

- Ở `production`, Docker chỉ publish cổng trên `127.0.0.1` (gateway, Mailpit, Kafka, Postgres). Cổng Docker publish ra `0.0.0.0` bỏ qua ufw nên không được mở thẳng.
- Tường lửa (ufw), luôn mở SSH trước khi bật:
  ```bash
  sudo ufw allow OpenSSH
  # cloudflare: chỉ cho Cloudflare vào cổng 80
  for ip in $(curl -s https://www.cloudflare.com/ips-v4) $(curl -s https://www.cloudflare.com/ips-v6); do
    sudo ufw allow from "$ip" to any port 80 proto tcp
  done
  # direct-https: sudo ufw allow 80,443/tcp      direct-http: sudo ufw allow 80/tcp
  sudo ufw enable
  ```
- Dashboard: P2 chỉ cấp token cho tài khoản trong `AUTH_CLIENTS` (`AUTH_ALLOW_ANY=false`). Mailpit: Nginx yêu cầu đăng nhập.
- Với `cloudflare`, SSL Flexible nghĩa là đoạn Cloudflare tới máy chủ không mã hoá, kể cả cookie đăng nhập dashboard. `direct-https` mã hoá suốt từ trình duyệt tới máy chủ. `direct-http` không mã hoá gì: mật khẩu dashboard và Mailpit đi dạng rõ, chỉ nên dùng thử.
- Cổng SMTP của Mailpit chỉ dùng trong mạng Docker. Muốn Mailpit nhận thư từ bên ngoài cho một domain thì cần publish cổng SMTP và tạo bản ghi MX; phần này chưa cấu hình.

## 8. Sự cố thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `waf` hoặc `mail` trả 502 | Container chưa chạy: `make deploy-ps ENV=production` |
| Dashboard không có sự kiện của site demo | Domain có trong mục Domain chưa; Cloudflare có chặn request không; C1 có đọc được log không: `docker logs waf-sensor-c1-ingestion-1` |
| Mọi IP trong log là IP của Cloudflare | DNS đang Proxied nhưng `ACCESS_MODE` không phải `cloudflare`: sửa rồi chạy lại `make server-up`; xem `/etc/nginx/conf.d/waf-cloudflare-realip.conf` |
| `direct-https` dừng ở bước chứng chỉ | Bản ghi A chưa trỏ về máy chủ (hoặc đang Proxied qua Cloudflare), cổng 80 bị chặn, hoặc vượt giới hạn cấp của Let's Encrypt (dùng `LETSENCRYPT_STAGING=true` khi thử). Chi tiết: `sudo less /var/log/letsencrypt/letsencrypt.log` |
| Đăng nhập báo 429 | Gateway giới hạn 10 lần lấy token mỗi phút cho một IP; đợi một phút |
| Build image bị dừng giữa chừng | Thiếu RAM: thêm swap rồi chạy lại `make server-up` |
