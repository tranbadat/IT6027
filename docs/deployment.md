# Triển khai tách 3 tầng (deploy độc lập)

Hệ thống chia thành **3 loại hạ tầng** deploy riêng, nối nhau qua một network dùng chung (`waf-net`), để có thể "cắm" phần thu thập vào Nginx/Apache của bất kỳ app nào mà không đụng phần trung tâm.

| Loại | Stack | Thư mục | Thành phần | Deploy ở đâu |
|---|---|---|---|---|
| **1** | Sensor (thu thập) | `deploy/sensor` | C1 | **Mỗi app cần giám sát** (cạnh Nginx/Apache của app) |
| **2** | Pipeline (xử lý) | `deploy/pipeline` | C2, C3, detector (D1 + D3), rule-api (D2), nhánh DDoS (profile `ddos`) | Trung tâm |
| **3** | Platform (quản trị) | `deploy/platform` | Kafka, Postgres, P1–P5, gateway (+ Mailpit, webhook-sink khi chạy local) | Trung tâm (hub) |

```
   App A ── Nginx ──┐                            ┌── Loại 3: PLATFORM (hub) ───────────┐
   [Sensor C1] ─────┼──► Kafka (bus) ◄───────────┤ Kafka     Postgres  gateway         │
                    │        ▲                    │ P1 scope  P2 auth  P3  P4  P5       │
   App B ── Nginx ──┤        │                    └─────────────────────────────────────┘
   [Sensor C1] ─────┘        │
                             │
                    ┌── Loại 2: PIPELINE ────────────────────────────────┐
                    │ C2 ─► C3 ─► detector (Java)   (+ rule-api quản lý rule) │
                    └─────────────────────────────────────────────────────┘
```

Sensor chỉ cần với tới **bus** (Kafka) và **Scope Service** (P1) của Platform. Muốn giám sát thêm app → dựng thêm một Sensor, không sửa gì ở trung tâm.

## Chạy nhanh (một máy — khuyến nghị để thử)

```bash
make deploy-up      # tạo waf-net, build image, sinh .env khớp nhau, dựng cả 3 stack
make deploy-ps      # trạng thái 3 stack
make e2e            # nghiệm thu toàn trình (gửi tấn công thật -> phát hiện -> cảnh báo -> dashboard)
make deploy-down    # dừng cả 3 (giữ dữ liệu)
```

- Dashboard: http://localhost:8080 · Web demo (để bắn thử): http://localhost:8081 · Mailpit: http://localhost:8025
- `make deploy-up` bật sẵn Sensor ở chế độ `demo` (kèm một Nginx mẫu). Đây chỉ để thử; production dùng Nginx thật của app (xem dưới).

## Môi trường: local và production

Biến `ENV` của Makefile chọn môi trường (mặc định `local`):

```bash
make deploy-up                    # ENV=local
make deploy-up ENV=production     # cấu hình production; trên server thật dùng make server-up
make deploy-down ENV=production   # dùng cùng ENV khi dừng hoặc xem trạng thái (deploy-ps)
```

Mỗi stack đọc `deploy/<stack>/.env` (secret, cổng, giá trị riêng của máy) rồi `deploy/<stack>/env/<ENV>.env` nếu có; giá trị cùng tên trong file môi trường ghi đè `.env`. Secret chỉ đặt trong `.env` (không commit).

| | `local` | `production` |
|---|---|---|
| Platform ([`local.env`](../deploy/platform/env/local.env), [`production.env`](../deploy/platform/env/production.env)) | Mailpit, webhook-sink, Swagger UI; gateway nghe mọi địa chỉ; P2 cấp token cho mọi client | Chỉ Mailpit; gateway chỉ nghe `127.0.0.1`; webhook tắt; P2 chỉ cấp token cho tài khoản trong `AUTH_CLIENTS` |
| Sensor ([`production.env`](../deploy/sensor/env/production.env)) | Kèm web demo Nginx (profile `demo`) | Không có web demo; C1 đọc `/var/log/nginx/waf` của Nginx trên máy chủ |

- Đổi môi trường trên cùng một máy: `make deploy-down` rồi `make deploy-up ENV=<môi trường mới>`, để container của môi trường cũ được gỡ.
- `make e2e` dùng web demo và `admin/admin` nên chỉ chạy ở `local`.
- Dựng trên server thật (Nginx của máy chủ, Cloudflare, một lệnh `make server-up`): [server-deployment.md](server-deployment.md).

## Cắm Sensor vào Nginx/Apache của một app thật

Trên máy/host của app cần giám sát:

1. Bảo đảm app-domain nằm trong **scope**: thêm ở dashboard (mục **Domain**) hoặc gọi
   `POST http://<hub>:8080/api/scope/allowlist` (cần JWT) với `{"target":"myapp.com"}`.
   `SCOPE_ALLOWLIST` của Platform chỉ dùng để tạo dữ liệu ban đầu khi bảng allowlist còn trống; với DB đã có dữ liệu phải thêm qua dashboard hoặc API.
2. (Tuỳ chọn) Cho log giàu thông tin hơn: thêm `deploy/sensor/waf-logformat.conf` vào Nginx của app và
   trỏ `access_log ... waf_combined`. Bỏ qua cũng được — sensor parse được log **combined chuẩn**.
3. Cấu hình `deploy/sensor/.env`:
   ```
   EVENT_BUS_URL=<hub-host>:9092               # listener EXTERNAL của Kafka ở Platform
   SCOPE_SERVICE_URL=http://<hub-host-hoặc-p1-scope>:8000
   SENSOR_NGINX_LOG_DIR=/var/log/nginx        # thư mục log THẬT của app
   C1_DOMAIN=myapp.com                         # bắt buộc nếu file là access.log
   COMPOSE_PROFILES=                           # bỏ demo
   ```
4. Dựng riêng sensor:
   ```bash
   cd deploy/sensor && docker compose up -d c1-ingestion
   ```

C1 đọc log mới gần như tức thì (tail), kiểm tra scope, rồi đẩy `log.raw.ingested` về bus trung tâm. Từ đó Pipeline và Platform xử lý như bình thường.

### Nhận diện domain và định dạng log

- File `<domain>.access.log` → C1 tự suy domain từ tên file (không cần `C1_DOMAIN`).
- File `access.log` (mặc định của đa số app) → **phải** đặt `C1_DOMAIN=<domain>`.
- Hỗ trợ cả Nginx và Apache; đặt log Apache qua `SENSOR_APACHE_LOG_DIR`.
- Parser nhận cả `waf_combined` (có host/rt/sid) lẫn `combined` chuẩn (không có) — thiếu trường nào thì để trống, `host` lấy theo domain đã xác nhận.

## Thứ tự khởi động & phụ thuộc

1. **Platform** trước (Kafka + tạo topic + DB + scope/auth).
2. **Pipeline** và **Sensor** sau; nếu bus chưa sẵn sàng, chúng tự thử lại kết nối (không cần đúng thứ tự tuyệt đối).

Các stack độc lập: dừng/khởi động lại Pipeline không ảnh hưởng Platform; thêm Sensor không cần đụng hai stack kia.

## Nhiều máy (production)

- Kafka của Platform có listener `EXTERNAL` cho máy khác: đặt `KAFKA_EXTERNAL_HOST=<ip/hostname Platform>` và `KAFKA_EXTERNAL_BIND=0.0.0.0` trong `deploy/platform/.env` (mặc định chỉ bind `127.0.0.1`).
- Listener dùng PLAINTEXT, **không có xác thực**: chỉ mở ra mạng khi đã chặn firewall chỉ cho IP của Sensor/Pipeline, dùng VPN, hoặc bật SASL/TLS cho Kafka. Ai ghi được vào Kafka đều có thể chèn log giả hoặc ghi một snapshot rỗng lên `rules.changed` làm detector tắt hết rule; khi chạy thật nên bật SASL kèm ACL chỉ cho `rule-api` ghi topic này.
- Sensor/Pipeline ở máy khác: sửa host trong `EVENT_BUS_URL` và `SCOPE_SERVICE_URL` trỏ tới địa chỉ Platform (Scope cần được cho phép truy cập từ mạng nội bộ).
- Image: build ở trung tâm rồi đẩy lên registry, hoặc `docker save/load` sang máy Sensor (image `waf/c1-ingestion:dev`).
- `EVENT_BUS_URL` của Sensor/Pipeline ở máy khác là `<hub-host>:9092`; cùng máy thì dùng `kafka:19092` qua `waf-net`.

## Quan hệ với `docker-compose.yml` ở gốc

`docker-compose.yml` ở gốc repo là bản **tất-cả-trong-một** để dev/demo nhanh trên một máy (`make up`). Ba stack trong `deploy/` là bản **tách rời để triển khai**. Hai cách dùng chung image (`waf/*:dev`) và cùng cấu hình trong `infra/` (gồm `infra/kafka/create-topics.sh`), `config/`, `contracts/`. Đừng chạy đồng thời cả hai trên cùng máy vì trùng cổng (8080, 9092, …).
