# Service ứng dụng

Mỗi module là một thư mục `services/<tên-service>/`, có `Dockerfile` riêng. Các service đã được khai báo sẵn trong [`docker-compose.yml`](../docker-compose.yml), mỗi service ứng với một profile.

## Danh sách service

| Thư mục | Module | Người | Profile | Loại | Queue nhận | Mount |
|---|---|---|---|---|---|---|
| `c1-ingestion` | C1 | 1 | `c1`, `collection` | worker (tail file) | — | `/var/log/waf/{nginx,apache}` (ro) |
| `c2-parser` | C2 | 1 | `c2`, `collection` | consumer | `q.c2-parser` | — |
| `c3-enrichment` | C3 | 1 | `c3`, `collection` | consumer | `q.c3-enrichment` | `/data/geoip` (ro) |
| `d1-rule-engine` | D1 | 2 | `d1`, `detection` | consumer | `q.d1-rule-engine` | `/data/rules` (ro) |
| `d2-rule-manager` | D2 | 2 | `d2`, `detection` | HTTP | — | `/data/rules` (rw) |
| `d3-anomaly-scorer` | D3 | 2 | `d3`, `detection` | consumer | `q.d3-anomaly-scorer` | — |
| `p1-scope` | P1 | 3 | `p1`, `platform` | HTTP | — | — |
| `p2-auth` | P2 | 3 | `p2`, `platform` | HTTP | — | — |
| `p3-orchestrator` | P3 | 3 | `p3`, `platform` | consumer + HTTP | `q.p3-orchestrator` | `/config/pipelines` (ro) |
| `p4-alerting` | P4 | 3 | `p4`, `platform` | consumer + HTTP | `q.p4-alerting` | — |
| `p5-dashboard` | P5 | 3 | `p5`, `platform` | consumer + HTTP | `q.p5-dashboard` | — |

Profile `app` bật toàn bộ 11 service.

## Quy ước

- **Ngôn ngữ tự chọn.** Chỉ cần `Dockerfile` build được từ chính thư mục service.
- **HTTP** lắng nghe cổng `8000` (biến `HTTP_PORT`) và có `GET /healthz` trả 200. Khai báo `HEALTHCHECK` trong Dockerfile.
- **Chạy bằng user không phải root** (`USER` trong Dockerfile).
- **Cấu hình chỉ đọc từ biến môi trường**, không hardcode địa chỉ hay mật khẩu.
- **Log ra stdout**, xem bằng `make logs s=<tên-service>`.
- **Phải gọi `GET $SCOPE_SERVICE_URL/scope/check`** trước khi xử lý log của một domain mới. Được phép cache theo `cache_ttl_seconds`.
- **Event theo [`contracts/events`](../contracts/events/README.md).**
- **P5:** API đặt dưới `/dashboard/`; gateway chỉ cho gọi qua `/api/dashboard/` (có JWT). Giao diện không dùng tiền tố `/dashboard`. API phải trả 401 nếu thiếu header `X-Auth-Subject`.

## Biến môi trường có sẵn

| Biến | Ví dụ | Dùng cho |
|---|---|---|
| `EVENT_BUS_URL` | `amqp://waf:***@rabbitmq:5672/` | mọi service |
| `EVENT_EXCHANGE` | `waf.events` | mọi service |
| `EVENT_QUEUE` | `q.c2-parser` | consumer |
| `DATABASE_URL` | `postgresql://waf:***@postgres:5432/waf` | service cần lưu trữ, mỗi module một schema riêng |
| `SCOPE_SERVICE_URL` | `http://mock-api` → `http://p1-scope:8000` | mọi service |
| `AUTH_SERVICE_URL` | `http://mock-api` → `http://p2-auth:8000` | service cần gọi Auth |
| `HTTP_PORT` | `8000` | service HTTP |
| `LOG_LEVEL` | `info` | mọi service |
| `LOG_DIR` | `/var/log/waf` | c1 |
| `GEOIP_DB_PATH` | `/data/geoip/dbip-city-lite.mmdb` | c3 (tải bằng `make geoip`) |
| `RULES_DIR` | `/data/rules` → `config/rules/` | d1, d2 |
| `PIPELINES_DIR` | `/config/pipelines` → `config/pipelines/` | p3 |
| `JWT_SECRET` | — | p2 |
| `SMTP_HOST`, `SMTP_PORT`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ALERT_WEBHOOK_URL` | `mailpit`, `1025`, …, `http://webhook-sink:8080/alerts` | p4 |

## Thêm một service vào hệ thống

1. Tạo `services/<tên>/Dockerfile` và mã nguồn.
2. Thêm profile vào `COMPOSE_PROFILES` trong `.env`, ví dụ `COMPOSE_PROFILES=mock,docs,c1`.
3. Chạy `make up`. Chỉ build lại service đó: `docker compose up -d --build c1-ingestion`.
4. Nếu là P1 hoặc P2 bản thật: đổi `SCOPE_SERVICE_URL` / `AUTH_SERVICE_URL` trong `.env` rồi chạy `make up`. Gateway sẽ tự trỏ sang service mới.
