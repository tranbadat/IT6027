# Triển khai tách 3 tầng (deploy độc lập)

Hệ thống chia thành **3 loại hạ tầng** deploy riêng, nối nhau qua một network dùng chung (`waf-net`), để có thể "cắm" phần thu thập vào Nginx/Apache của bất kỳ app nào mà không đụng phần trung tâm.

| Loại | Stack | Thư mục | Thành phần | Deploy ở đâu |
|---|---|---|---|---|
| **1** | Sensor (thu thập) | `deploy/sensor` | C1 | **Mỗi app cần giám sát** (cạnh Nginx/Apache của app) |
| **2** | Pipeline (xử lý) | `deploy/pipeline` | C2, C3, D1, D2, D3 | Trung tâm |
| **3** | Platform (quản trị) | `deploy/platform` | RabbitMQ, Postgres, P1–P5, gateway, mailpit | Trung tâm (hub) |

```
   App A ── Nginx ──┐                            ┌── Loại 3: PLATFORM (hub) ───────────┐
   [Sensor C1] ─────┼──► RabbitMQ (bus) ◄────────┤ RabbitMQ  Postgres  gateway         │
                    │        ▲                    │ P1 scope  P2 auth  P3  P4  P5       │
   App B ── Nginx ──┤        │                    └─────────────────────────────────────┘
   [Sensor C1] ─────┘        │
                             │
                    ┌── Loại 2: PIPELINE ──────────────────────┐
                    │ C2 ─► C3 ─► D1 ─► D3   (+ D2 quản lý rule) │
                    └───────────────────────────────────────────┘
```

Sensor chỉ cần với tới **bus** (RabbitMQ) và **Scope Service** (P1) của Platform. Muốn giám sát thêm app → dựng thêm một Sensor, không sửa gì ở trung tâm.

## Chạy nhanh (một máy — khuyến nghị để thử)

```bash
make deploy-up      # tạo waf-net, build image, sinh .env khớp nhau, dựng cả 3 stack
make deploy-ps      # trạng thái 3 stack
make e2e            # nghiệm thu toàn trình (gửi tấn công thật -> phát hiện -> cảnh báo -> dashboard)
make deploy-down    # dừng cả 3 (giữ dữ liệu)
```

- Dashboard: http://localhost:8080 · Web demo (để bắn thử): http://localhost:8081 · Mailpit: http://localhost:8025
- `make deploy-up` bật sẵn Sensor ở chế độ `demo` (kèm một Nginx mẫu). Đây chỉ để thử; production dùng Nginx thật của app (xem dưới).

## Cắm Sensor vào Nginx/Apache của một app thật

Trên máy/host của app cần giám sát:

1. Bảo đảm app-domain nằm trong **scope**: thêm vào `SCOPE_ALLOWLIST` của Platform, hoặc gọi
   `POST http://<hub>:8080/api/scope/allowlist` (cần JWT) với `{"target":"myapp.com"}`.
2. (Tuỳ chọn) Cho log giàu thông tin hơn: thêm `deploy/sensor/waf-logformat.conf` vào Nginx của app và
   trỏ `access_log ... waf_combined`. Bỏ qua cũng được — sensor parse được log **combined chuẩn**.
3. Cấu hình `deploy/sensor/.env`:
   ```
   EVENT_BUS_URL=amqp://waf:<mật khẩu>@<hub-host>:5672/
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

1. **Platform** trước (tạo bus + topology + DB + scope/auth).
2. **Pipeline** và **Sensor** sau; nếu bus chưa sẵn sàng, chúng tự thử lại kết nối (không cần đúng thứ tự tuyệt đối).

Các stack độc lập: dừng/khởi động lại Pipeline không ảnh hưởng Platform; thêm Sensor không cần đụng hai stack kia.

## Nhiều máy (production)

- Chỉ Platform mở cổng bus `5672` ra ngoài (mặc định `.env` đã publish). **Siết firewall / dùng VPN / bật TLS** cho RabbitMQ khi chạy thật; đổi mật khẩu mặc định.
- Sensor/Pipeline ở máy khác: sửa host trong `EVENT_BUS_URL` và `SCOPE_SERVICE_URL` trỏ tới địa chỉ Platform (Scope cần được cho phép truy cập từ mạng nội bộ).
- Image: build ở trung tâm rồi đẩy lên registry, hoặc `docker save/load` sang máy Sensor (image `waf/c1-ingestion:dev`).
- Secret RabbitMQ trong `EVENT_BUS_URL` của Sensor/Pipeline phải khớp `RABBITMQ_USER/PASSWORD` của Platform.

## Quan hệ với `docker-compose.yml` ở gốc

`docker-compose.yml` ở gốc repo là bản **tất-cả-trong-một** để dev/demo nhanh trên một máy (`make up`). Ba stack trong `deploy/` là bản **tách rời để triển khai**. Hai cách dùng chung image (`waf/*:dev`) và cùng cấu hình trong `infra/`, `config/`, `contracts/`. Đừng chạy đồng thời cả hai trên cùng máy vì trùng cổng (8080, 5672, …).
