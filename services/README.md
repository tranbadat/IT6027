# Service ứng dụng

Mỗi module là một thư mục `services/<tên-service>/`, có `Dockerfile` riêng. Các service đã được khai báo sẵn trong [`docker-compose.yml`](../docker-compose.yml), mỗi service ứng với một profile.

## Danh sách service

| Thư mục | Module | Người | Ngôn ngữ | Profile | Loại | Topic đọc (consumer group = tên service) | Mount |
|---|---|---|---|---|---|---|---|
| `c1-ingestion` | C1 | 1 | Python | `c1`, `collection` | worker (tail file) | — | `/var/log/waf/{nginx,apache}` (ro) |
| `c2-parser` | C2 | 1 | Python | `c2`, `collection` | consumer | `log.raw.ingested` | — |
| `c3-enrichment` | C3 | 1 | Python | `c3`, `collection` | consumer | `log.normalized` | `/data/geoip` (ro) |
| `detector` | D1 + D3 | 2 | Java 17 | `detection` | consumer | `log.enriched` | — |
| `detector` (`rule-api`) | D2 | 2 | Java 17 | `detection` | HTTP (cổng 8090) | `rules.changed` (phát) | `/data` (rules.json) |
| `detector` (`repartitioner`, `ddos-detector`) | DDoS | 2 | Java 17 | `ddos` | consumer | `log.enriched`, `log.enriched.by-domain` | — |
| `p1-scope` | P1 | 3 | Python | `p1`, `platform` | HTTP | — | — |
| `p2-auth` | P2 | 3 | Python | `p2`, `platform` | HTTP | — | — |
| `p3-orchestrator` | P3 | 3 | Python | `p3`, `platform` | consumer + HTTP | `log.raw.ingested`, `log.normalized`, `log.enriched`, `attack.detected`, `anomaly.scored`, `alert.triggered` | `/config/pipelines` (ro) |
| `p4-alerting` | P4 | 3 | Python | `p4`, `platform` | consumer + HTTP | `anomaly.scored`, `alert.triggered` | — |
| `p5-dashboard` | P5 | 3 | Python | `p5`, `platform` | consumer + HTTP | `log.enriched`, `attack.detected`, `anomaly.scored`, `alert.triggered`, `stage.completed` | — |
| `metrics-exporter` | quan sát | 3 | Python | `obs` | consumer + HTTP | mọi topic sự kiện | — |

Profile `app` bật toàn bộ pipeline (C1–C3, detector + rule-api, P1–P5). Detector Java có tài liệu riêng: [`detector/README.md`](detector/README.md).

## Quy ước

- **Ngôn ngữ tự chọn.** Chỉ cần `Dockerfile` build được từ chính thư mục service.
- **HTTP** lắng nghe cổng `8000` (biến `HTTP_PORT`) và có `GET /healthz` trả 200, khai báo `HEALTHCHECK` trong Dockerfile. Ngoại lệ: metrics-exporter chỉ có `/metrics` (healthcheck gọi `/metrics`), rule-api có `/health` ở cổng 8090.
- **Chạy bằng user không phải root** (`USER` trong Dockerfile).
- **Cấu hình chỉ đọc từ biến môi trường**, không hardcode địa chỉ hay mật khẩu.
- **Log ra stdout**, xem bằng `make logs s=<tên-service>`.
- **Service xử lý nội dung log (C1–C3, detector) phải gọi `GET $SCOPE_SERVICE_URL/scope/check`** trước khi xử lý log của một domain mới. Được phép cache theo `cache_ttl_seconds`. P3–P5 và metrics-exporter chỉ đọc event đã được C1 xác nhận scope (trường `domain` của envelope).
- **Event theo [`contracts/events`](../contracts/events/README.md).** Service Python dùng [`common/bus.py`](common/bus.py): `bus.publish(...)` và `bus.consume(<group>, handler, topics=[...])`.
- **Xác thực:** service phía sau không tự kiểm tra JWT mà dựa vào gateway (`auth_request` tới P2, chỉ gateway publish cổng ra host). Gateway ghi đè `X-Auth-Subject`/`X-Auth-Roles` nên service chỉ tin hai header này khi request đi qua gateway.
- **P5:** API đặt dưới `/dashboard/`; gateway chỉ cho gọi qua `/api/dashboard/` (có JWT). Giao diện không dùng tiền tố `/dashboard`.

## Biến môi trường có sẵn

| Biến | Ví dụ | Dùng cho |
|---|---|---|
| `EVENT_BUS_URL` | `kafka:19092` (bootstrap servers) | service Python |
| `EVENT_GROUP` | mặc định = tên service | consumer Python (đổi khi cần chạy song song hai bản khác nhau) |
| `KAFKA_BOOTSTRAP_SERVERS`, `ALERT_SCORE_THRESHOLD` | `kafka:19092`, `70` | detector |
| `DATABASE_URL` | `postgresql://waf:***@postgres:5432/waf` | service cần lưu trữ, mỗi module một schema riêng |
| `SCOPE_SERVICE_URL` | `http://mock-api` → `http://p1-scope:8000` | mọi service |
| `AUTH_SERVICE_URL` | `http://mock-api` → `http://p2-auth:8000` | service cần gọi Auth |
| `HTTP_PORT` | `8000` | service HTTP |
| `LOG_LEVEL` | `info` | mọi service |
| `LOG_DIR` | `/var/log/waf` | c1 |
| `GEOIP_DB_PATH` | `/data/geoip/dbip-city-lite.mmdb` | c3 (tải bằng `make geoip`) |
| `PIPELINES_DIR` | `/config/pipelines` → `config/pipelines/` | p3 |
| `JWT_SECRET` | — | p2 |
| `AUTH_ALLOW_ANY` | `true` | p2: chế độ demo, cấp token cho mọi `client_id`/`client_secret` không rỗng. Khi chạy thật đặt `false` và khai báo `AUTH_CLIENTS` |
| `AUTH_CLIENTS`, `AUTH_TOKEN_TTL` | xem [`p2-auth/README.md`](p2-auth/README.md) | p2 |
| `STAGE_WINDOW_SECONDS` | `10` | p3: chu kỳ phát `stage.completed` |
| `ALERT_THRESHOLDS`, `ALERT_COOLDOWN_SECONDS` | rỗng, `60` | p4 (xem [`p4-alerting/README.md`](p4-alerting/README.md)) |
| `SMTP_HOST`, `SMTP_PORT`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`, `ALERT_WEBHOOK_URL` | `mailpit`, `1025`, …, `http://webhook-sink:8080/alerts` | p4 |

## Thêm một service vào hệ thống

1. Tạo `services/<tên>/Dockerfile` và mã nguồn.
2. Thêm profile vào `COMPOSE_PROFILES` trong `.env`, ví dụ `COMPOSE_PROFILES=mock,docs,c1`.
3. Chạy `make up`. Chỉ build lại service đó: `docker compose up -d --build c1-ingestion`.
4. Nếu là P1 hoặc P2 bản thật: đổi `SCOPE_SERVICE_URL` / `AUTH_SERVICE_URL` trong `.env` rồi chạy `make up`. Gateway sẽ tự trỏ sang service mới.
