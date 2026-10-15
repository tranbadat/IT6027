# p5-dashboard (P5, Người 3)

Dashboard realtime: tổng hợp event trong bộ nhớ, lưu lịch sử tấn công vào Postgres, đẩy event tới trình duyệt qua Server-Sent Events (SSE) và tự phục vụ giao diện quản trị một trang tại `/`.

## Vai trò

Đọc `TOPICS = ["log.enriched", "attack.detected", "anomaly.scored", "alert.triggered", "stage.completed"]` ([`consumer.py`](consumer.py)) bằng consumer group `EVENT_GROUP` (mặc định `p5-dashboard`). Consumer chạy ở thread nền qua `start_consumer_thread` của [`common/bus.py`](../common/bus.py) và chỉ được khởi động sau khi `StreamHub` đã gắn event loop của uvicorn (sự kiện `startup` trong [`main.py`](main.py)). Consumer dừng vì lỗi thì process thoát với mã 1 để Docker khởi động lại.

| Event | Tổng hợp trong bộ nhớ | Postgres | SSE |
|---|---|---|---|
| `log.enriched` | Đếm request theo domain và phút (`data.request_ts`, thiếu thì `timestamp`), giữ 60 phút gần nhất mỗi domain | không | có |
| `attack.detected` | Đếm theo `attack_type` và theo `src_ip` | `detection.attacks` | có |
| `anomaly.scored` | Chỉ vào feed `recent` | không | có |
| `alert.triggered` | 200 cảnh báo gần nhất (bản gọn) | không | có |
| `stage.completed` | 50 bản gần nhất (`pipeline`, `stage`, `processed`, `window_seconds`) | không | không |

**Bộ nhớ** (`AggregateStore`, [`store.py`](store.py)): bốn event có cột SSE = "có" cũng vào feed `recent` (100 bản gần nhất). Bộ đếm tấn công và IP giữ tối đa 5000 khoá, trả về top 10. Toàn bộ dữ liệu này phục vụ `/dashboard/summary` và mất khi khởi động lại.

**Postgres** (`AttackRepo`, [`repo.py`](repo.py)): mỗi `attack.detected` ghi một dòng vào `detection.attacks` (schema `detection` tạo sẵn bởi [`01-schemas.sql`](../../infra/postgres/init/01-schemas.sql); bảng và index theo `detected_at DESC`, `domain`, `attack_type` do service tự tạo khi khởi động). Khoá chính là `event_id`, ghi bằng `ON CONFLICT (event_id) DO NOTHING`; `matched_value` được cắt còn 500 ký tự. Lỗi ghi chỉ được log, không làm dừng consumer. Không có `DATABASE_URL` hoặc không mở được pool thì bỏ qua lưu DB. Dashboard SOC trên Grafana đọc bảng này bằng role chỉ đọc `grafana_ro` ([`docs/observability.md`](../../docs/observability.md)).

**SSE** ([`app.py`](app.py), [`stream.py`](stream.py)): khi mở kết nối, service gửi comment `: connected`; mỗi event là một named event `event: <tên event>` (`attack.detected`, `anomaly.scored`, `alert.triggered`, `log.enriched`) với `data` là JSON `{event, domain, timestamp, data}`. Nếu 15 giây không có event thì gửi comment `: keep-alive`. Mỗi client có hàng đợi 1000 item; client chậm bị bỏ item thay vì làm nghẽn các client khác. Gateway tắt buffering cho `/api/dashboard/` ([`streaming.conf`](../../infra/gateway/snippets/streaming.conf)).

### Giao diện

[`index.html`](index.html) là SPA một file, không dùng tiền tố `/dashboard`. Khi mở trang, SPA kiểm tra cookie `waf_token` còn hạn (`GET /api/dashboard/summary`); nếu không, gọi `POST /api/auth/token` với `admin`/`admin` (chạy local, P2 ở chế độ `AUTH_ALLOW_ANY`). P2 từ chối (production chỉ nhận tài khoản trong `AUTH_CLIENTS`) thì SPA hiện form đăng nhập. P2 đặt cookie `waf_token`, sau đó mọi `fetch` và `EventSource` đều gửi kèm cookie. Kết nối SSE `GET /api/dashboard/stream` được mở ngay khi tải trang.

| View | API gọi |
|---|---|
| Giám sát | `GET /api/dashboard/summary` (phân loại tấn công, số domain có traffic, feed gần đây), `GET /api/dashboard/attacks?limit=1` (tổng tấn công trong DB), `GET /api/alerts/?limit=1` (tổng cảnh báo của P4), `GET /api/rules/` (số rule đang bật). Tự làm mới mỗi 5 giây khi đang mở view |
| Sự kiện realtime | Dữ liệu từ SSE; bảng chỉ hiện `attack.detected` và `alert.triggered`, tối đa 100 dòng |
| Lịch sử tấn công | `GET /api/dashboard/attacks?limit=200` kèm `domain`, `attack_type` nếu nhập |
| Domain | P1: `GET /api/scope/allowlist`, `POST /api/scope/allowlist`, `DELETE /api/scope/allowlist/{target}` |
| Rule pattern | rule-api: `GET /api/rules/`, `POST /api/rules/`, `PUT /api/rules/{id}`, `DELETE /api/rules/{id}`, `POST /api/rules/validate` |
| Kết nối | `GET /api/dashboard/links`; danh sách rỗng thì dùng liên kết mặc định tới Grafana (:3000), Prometheus (:9090), Mailpit (:8025), Kafka UI (:8085), Swagger UI (:8090) theo hostname hiện tại |

## API HTTP

OpenAPI: [`contracts/openapi/dashboard.yaml`](../../contracts/openapi/dashboard.yaml).

| Method | Path (service) | Qua gateway | Xác thực |
|---|---|---|---|
| `GET` | `/` | `/` | không |
| `GET` | `/rules` | `/rules` | không; chuyển hướng 307 tới `/#rules` |
| `GET` | `/dashboard/summary` | `/api/dashboard/summary` | JWT |
| `GET` | `/dashboard/attacks` | `/api/dashboard/attacks` | JWT |
| `GET` | `/dashboard/stream` | `/api/dashboard/stream` | JWT (trình duyệt dùng cookie `waf_token`) |
| `GET` | `/dashboard/links` | `/api/dashboard/links` | JWT |
| `GET` | `/healthz` | không (gateway có `/healthz` riêng) | không |

JWT do gateway kiểm tra qua P2. Gateway trả 404 cho `/dashboard` và `/dashboard/...` gọi thẳng, buộc đi qua `/api/dashboard/`.

- `/dashboard/summary` trả `traffic` (`domain`, `minute`, `count`), `top_attacks`, `top_ips`, `alerts`, `stages`, `recent`; các danh sách mới nhất trước.
- `/dashboard/attacks` nhận `limit` (1-1000, mặc định 100), `domain`, `attack_type` (khớp chính xác). Trả `{attacks, count, persisted: true}`, trong đó `count` là tổng số dòng của bảng (không áp bộ lọc). Không có DB hoặc truy vấn lỗi thì trả `{attacks: [], persisted: false}`.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Bootstrap servers Kafka |
| `EVENT_GROUP` | `p5-dashboard` | Consumer group |
| `DATABASE_URL` | rỗng | DSN Postgres; rỗng thì không lưu lịch sử, `/dashboard/attacks` trả danh sách rỗng |
| `DASHBOARD_LINKS` | rỗng | Liên kết trang Kết nối trên server thật, dạng `Tên\|URL;Tên\|URL` (chỉ nhận URL http/https); `make server-up` đặt liên kết Mailpit |
| `HTTP_PORT` | `8000` | Cổng HTTP |
| `LOG_LEVEL` | `info` | Mức log ([`common/logging.py`](../common/logging.py)) |

## Chạy và kiểm thử

Thêm profile `p5` (đã nằm trong `platform` và `app`) vào `COMPOSE_PROFILES` trong `.env` rồi chạy `make up`. Build lại riêng P5: `docker compose up -d --build p5-dashboard`. Giao diện mở qua gateway tại http://localhost:8080/ (`DASHBOARD_SERVICE_URL` mặc định `http://p5-dashboard:8000`).

- Lấy token cần P2 hoặc `mock-api` (profile `mock`). View Rule pattern cần `rule-api` (profile `detection`), thẻ Cảnh báo cần P4, view Domain cần P1 hoặc `mock-api`.
- Để có dữ liệu realtime: bật profile `app` rồi chạy `make demo`.

```bash
make test-python   # toàn bộ test Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/p5-dashboard \
  -e PYTHONPATH=/app:/app/p5-dashboard waf/base:dev python -B test_dashboard.py
```

[`test_dashboard.py`](test_dashboard.py) có 6 hàm test cho `AggregateStore`: đếm traffic theo phút, cắt cửa sổ 60 phút, xếp hạng top tấn công và IP, lịch sử cảnh báo có giới hạn và mới nhất trước, ghi `stage.completed`, feed `recent` chỉ gồm event realtime. Chưa có test cho `repo.py`, SSE và các route HTTP.

## Giới hạn

- Tổng hợp trong bộ nhớ không bền và không idempotent: mất khi khởi động lại (consumer đọc tiếp từ offset đã commit, không nạp lại lịch sử), và một event được giao lại sẽ bị đếm hai lần. Chỉ `detection.attacks` bền và chống trùng theo `event_id`.
- Chỉ `attack.detected` được lưu DB; cảnh báo trong `/dashboard/summary` là 200 bản trong bộ nhớ (lịch sử cảnh báo bền nằm ở P4).
- API không tự kiểm tra `X-Auth-Subject`, dựa hoàn toàn vào gateway; SPA gửi cứng client `admin`/`admin` khi lấy token.
- Service không gọi P1 `/scope/check`, tin vào việc detector và các bước thu thập đã lọc scope. Client SSE chậm có thể mất event khi hàng đợi đầy.
