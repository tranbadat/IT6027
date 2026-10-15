# Triển khai trên server thật

Dựng toàn bộ hệ thống trên một máy chủ Ubuntu bằng một lệnh `make server-up`. Nginx chạy trực tiếp trên máy chủ (không Docker), mọi service còn lại chạy Docker. DNS đi qua Cloudflare.

```
Người dùng ─HTTPS─► Cloudflare ─HTTP:80─► Nginx (Ubuntu)
                                            ├─ service1/2/3.tranbadat.vn  site demo, log -> /var/log/nginx/waf/<domain>.access.log
                                            ├─ waf.tranbadat.vn           -> 127.0.0.1:8080  gateway (dashboard realtime)
                                            └─ mail.tranbadat.vn          -> 127.0.0.1:8025  Mailpit (có đăng nhập)

Docker (ENV=production):
  platform  Kafka, Postgres, P1–P5, gateway, Mailpit
  pipeline  C2, C3, detector, rule-api
  sensor    C1, đọc /var/log/nginx/waf (chỉ đọc)
```

Request tới site demo được Nginx ghi log; C1 đọc log, pipeline phát hiện tấn công, dashboard `waf.tranbadat.vn` hiển thị realtime và P4 gửi email cảnh báo vào Mailpit (`mail.tranbadat.vn`).

## Yêu cầu

- Ubuntu 22.04 hoặc 24.04 (Debian cũng được), user có quyền sudo. Script tự cài Nginx, Docker Engine và Compose plugin (kho apt chính thức của Docker) nếu chưa có.
- Tài nguyên (ước lượng, chưa đo trên server): khi tắt observability, tối thiểu 2 vCPU và 4 GB RAM; Kafka, Postgres và hai JVM (detector, rule-api) chiếm phần lớn bộ nhớ. Lần đầu build image Java (Maven) tốn thêm bộ nhớ, nên thêm swap nếu máy chỉ có 4 GB.
- Mở cổng 22 (SSH) và 80 (Cloudflare nối về máy chủ). Không cần mở cổng nào khác.

## 1. Cloudflare

1. **DNS:** tạo bản ghi A cho `service1`, `service2`, `service3`, `waf`, `mail` trỏ về IP máy chủ, Proxy status = Proxied (đám mây màu cam).
2. **SSL/TLS → Overview:** chọn Flexible (Cloudflare nối về máy chủ bằng HTTP). Ở Edge Certificates bật Always Use HTTPS.
3. **Lưu ý khi demo tấn công:**
   - Cloudflare có thể chặn hoặc bắt xác minh (challenge) một số request trước khi tới máy chủ (managed ruleset, Bot Fight Mode, Browser Integrity Check). Request bị chặn ở Cloudflare không có trong log nên hệ thống không thấy. Khi demo, tắt Bot Fight Mode hoặc tạo WAF custom rule với hành động Skip cho hostname của các site demo.
   - Cloudflare cache tệp tĩnh (`.css`, `.js`, ảnh): request trúng cache không tới máy chủ. Trang HTML và `/api/` mặc định không bị cache.

## 2. Chạy

```bash
git clone <repo> waf && cd waf
sudo apt-get install -y make        # nếu máy chưa có make
make server-up
```

Muốn đổi domain hoặc tuỳ chọn trước lần chạy đầu: `cp deploy/server/server.env.example deploy/server/server.env` rồi sửa. Xem trước cấu hình Nginx sẽ cài (không thay đổi máy): `./scripts/server-up.sh --print-nginx`.

`make server-up` ([`scripts/server-up.sh`](../scripts/server-up.sh)) làm lần lượt:

1. Cài gói còn thiếu (Nginx, Docker...). User vừa được thêm vào nhóm `docker` thì script tự chạy tiếp với nhóm đó, không cần đăng nhập lại.
2. Tạo `deploy/server/server.env` (lần đầu), sinh mật khẩu dashboard và Mailpit nếu còn trống.
3. Sinh `.env` cho các stack, ghi giá trị riêng của máy chủ vào `deploy/platform/.env` (`AUTH_CLIENTS`, `SCOPE_ALLOWLIST`, `DASHBOARD_LINKS`, `GATEWAY_TRUSTED_PROXY`), build image và dựng 3 stack với `ENV=production`. Observability chỉ được dựng khi `OBSERVABILITY=true`.
4. Cấu hình Nginx của máy chủ:
   - `/etc/nginx/conf.d/waf-logformat.conf`: định dạng log `waf_combined`;
   - `/etc/nginx/conf.d/waf-cloudflare-realip.conf`: dải IP Cloudflare tải mới mỗi lần chạy (không tải được thì dùng [`cloudflare-ips.txt`](../deploy/server/nginx/cloudflare-ips.txt)), để `$remote_addr` là IP thật của client;
   - `/etc/nginx/sites-available/waf.conf` (liên kết vào `sites-enabled`): dashboard, Mailpit và từng site demo;
   - mật khẩu Mailpit `/etc/nginx/waf-mailpit.htpasswd`, nội dung site demo `/var/www/waf-demo/<domain>`, logrotate `/etc/logrotate.d/waf-sites`.
5. Thêm các site demo vào scope qua API (chạy lại không tạo trùng).
6. Kiểm tra nhanh qua Nginx của máy chủ rồi in địa chỉ và tài khoản đăng nhập.

Chạy lại `make server-up` sau khi `git pull` hoặc sửa `server.env`: script cập nhật cấu hình, build lại image đã thay đổi và giữ nguyên dữ liệu.

## 3. Cấu hình (`deploy/server/server.env`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WAF_DOMAIN` | `waf.tranbadat.vn` | Dashboard realtime |
| `MAIL_DOMAIN` | `mail.tranbadat.vn` | Mailpit (email cảnh báo) |
| `DEMO_DOMAINS` | `service1/2/3.tranbadat.vn` | Các site demo được giám sát, cách nhau bằng dấu cách |
| `PUBLIC_SCHEME` | `https` | Scheme người dùng thấy (dùng cho liên kết trong dashboard) |
| `BEHIND_CLOUDFLARE` | `true` | Tin header `CF-Connecting-IP` từ dải IP Cloudflare |
| `OBSERVABILITY` | `false` | Dựng Prometheus, Grafana, Loki và exporter |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `admin` / tự sinh | Tài khoản dashboard |
| `MAIL_UI_USER` / `MAIL_UI_PASSWORD` | `admin` / tự sinh | Tài khoản trang Mailpit |

`server.env` chứa mật khẩu nên không được commit (đã có trong `.gitignore`). Cấu hình môi trường của các stack nằm ở `deploy/<stack>/env/production.env`.

## 4. Demo

1. Mở `https://waf.tranbadat.vn`, đăng nhập bằng tài khoản dashboard.
2. Gửi vài request tấn công tới site demo:
   ```bash
   curl "https://service1.tranbadat.vn/search?q=%3Cscript%3Ealert(1)%3C/script%3E"
   curl "https://service2.tranbadat.vn/api/products?id=1%20UNION%20SELECT%20password%20FROM%20users--"
   curl "https://service3.tranbadat.vn/api/load?file=../../../../etc/passwd"
   ```
3. Sự kiện xuất hiện ở mục Sự kiện realtime; cảnh báo có email trong `https://mail.tranbadat.vn`.

## 5. Vận hành

- Trạng thái: `make deploy-ps ENV=production`. Log một service: `docker logs -f waf-pipeline-detector-1`.
- Dừng toàn bộ container (giữ dữ liệu, giữ cấu hình Nginx): `make server-down`. Bật lại: `make server-up`.
- Observability: đặt `OBSERVABILITY=true` rồi chạy lại `make server-up`. Grafana chỉ nghe `127.0.0.1:3000`; xem qua SSH tunnel: `ssh -L 3000:127.0.0.1:3000 <user>@<server>` rồi mở http://localhost:3000.
- Gỡ cấu hình Nginx: `sudo rm /etc/nginx/sites-enabled/waf.conf /etc/nginx/conf.d/waf-*.conf && sudo systemctl reload nginx`.

## 6. Bảo mật

- Ở `production`, Docker chỉ publish cổng trên `127.0.0.1` (gateway, Mailpit, Kafka, Postgres). Cổng Docker publish ra `0.0.0.0` bỏ qua ufw nên không được mở thẳng.
- Nên chỉ cho Cloudflare vào cổng 80:
  ```bash
  sudo ufw allow OpenSSH
  for ip in $(curl -s https://www.cloudflare.com/ips-v4) $(curl -s https://www.cloudflare.com/ips-v6); do
    sudo ufw allow from "$ip" to any port 80 proto tcp
  done
  sudo ufw enable
  ```
- Dashboard: P2 chỉ cấp token cho tài khoản trong `AUTH_CLIENTS` (`AUTH_ALLOW_ANY=false`). Mailpit: Nginx yêu cầu đăng nhập.
- SSL Flexible nghĩa là đoạn Cloudflare tới máy chủ không mã hoá, kể cả cookie đăng nhập dashboard. Muốn mã hoá: cài Cloudflare Origin Certificate, thêm `listen 443 ssl` cho các server block và chuyển SSL mode sang Full (strict). Cấu hình hiện tại chưa làm bước này.
- Cổng SMTP của Mailpit chỉ dùng trong mạng Docker. Muốn Mailpit nhận thư từ bên ngoài cho một domain thì cần publish cổng SMTP và tạo bản ghi MX; phần này chưa cấu hình.

## 7. Sự cố thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `waf` hoặc `mail` trả 502 | Container chưa chạy: `make deploy-ps ENV=production` |
| Dashboard không có sự kiện của site demo | Domain có trong mục Domain chưa; Cloudflare có chặn request không; C1 có đọc được log không: `docker logs waf-sensor-c1-ingestion-1` |
| Mọi IP trong log là IP của Cloudflare | `BEHIND_CLOUDFLARE=true` rồi chạy lại `make server-up`; xem `/etc/nginx/conf.d/waf-cloudflare-realip.conf` |
| Đăng nhập báo 429 | Gateway giới hạn 10 lần lấy token mỗi phút cho một IP; đợi một phút |
| Build image bị dừng giữa chừng | Thiếu RAM: thêm swap rồi chạy lại `make server-up` |
