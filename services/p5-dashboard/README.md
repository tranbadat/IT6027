# p5-dashboard (P5, Người 3)

Dashboard realtime: tổng hợp event trong bộ nhớ, lưu lịch sử tấn công vào Postgres, tính thống kê theo cửa sổ thời gian, đẩy event tới trình duyệt qua Server-Sent Events (SSE) và tự phục vụ giao diện quản trị một trang tại `/` (thiết kế Figma `BmKGPfztGJKW7oltQAonuE`).

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

**Postgres** (`AttackRepo`, [`repo.py`](repo.py)): mỗi `attack.detected` ghi một dòng vào `detection.attacks` (schema `detection` tạo sẵn bởi [`01-schemas.sql`](../../infra/postgres/init/01-schemas.sql); bảng và index theo `detected_at DESC`, `domain`, `attack_type`, `src_ip` do service tự tạo khi khởi động; các cột `status`, `user_agent`, `score` thêm bằng `ADD COLUMN IF NOT EXISTS` nên bảng cũ được nâng cấp tại chỗ, dòng cũ để `NULL`). Khoá chính là `event_id`, ghi bằng `ON CONFLICT (event_id) DO NOTHING`; `matched_value` được cắt còn 500 ký tự. Lỗi ghi chỉ được log, không làm dừng consumer. Không có `DATABASE_URL` hoặc không mở được pool thì bỏ qua lưu DB. Dashboard SOC trên Grafana đọc bảng này bằng role chỉ đọc `grafana_ro` ([`docs/observability.md`](../../docs/observability.md)).

**Thống kê** (`StatsRepo`, [`stats_repo.py`](stats_repo.py); hàm thuần trong [`stats.py`](stats.py)): đọc `detection.attacks` trong cửa sổ `hours` giờ gần nhất và so với `hours` giờ liền trước. Xu hướng chia cửa sổ thành 24 khoảng, diễn biến theo mức độ chia 48 khoảng, mật độ tính 7 ngày × 24 giờ theo múi giờ `DASHBOARD_TZ`. Số cảnh báo đọc `alerting.alerts` của P4 nếu bảng tồn tại (kiểm tra bằng `to_regclass`), không có thì trả `null`.

**SSE** ([`app.py`](app.py), [`stream.py`](stream.py)): khi mở kết nối, service gửi comment `: connected`; mỗi event là một named event `event: <tên event>` (`attack.detected`, `anomaly.scored`, `alert.triggered`, `log.enriched`) với `data` là JSON `{event, event_id, request_id, domain, timestamp, data}` (`compact_event` trong [`store.py`](store.py), dùng chung cho feed `recent`). Nếu 15 giây không có event thì gửi comment `: keep-alive`. Mỗi client có hàng đợi 1000 item; client chậm bị bỏ item thay vì làm nghẽn các client khác. Gateway tắt buffering cho `/api/dashboard/` ([`streaming.conf`](../../infra/gateway/snippets/streaming.conf)).

### Giao diện

[`index.html`](index.html) chỉ là khung trang (sidebar, thanh công cụ, các `section` view, form đăng nhập); phần còn lại nằm trong [`static/`](static/), service phục vụ ở `/static/...` với `Cache-Control: no-cache` (tên file không có hash phiên bản, trình duyệt hỏi lại bằng ETag và nhận 304). Không có bước build: JS là ES module chạy thẳng trên trình duyệt.

| Thư mục / file | Nội dung |
|---|---|
| [`static/css/app.css`](static/css/app.css) | Token màu sáng/tối theo thiết kế (`:root`, `[data-theme="dark"]`), bố cục, thẻ, bảng, biểu đồ, hộp thoại |
| [`static/js/core.js`](static/js/core.js) | Trạng thái chung, gọi API (cookie `waf_token`), định dạng số/giờ (vi-VN, UTC+7), nhãn mức độ và loại tấn công, icon |
| [`static/js/charts.js`](static/js/charts.js) | Sparkline, vùng xếp chồng theo mức độ, donut, cột, bản đồ nhiệt (SVG/HTML, không dùng thư viện) |
| [`static/js/dialogs.js`](static/js/dialogs.js) | Hộp thoại thêm/sửa tên miền, ngăn kéo thêm/sửa rule (kiểm tra RE2, chạy thử trên request mẫu) |
| [`static/js/views/`](static/js/views/) | Sáu màn hình, mỗi file `render` (dựng khung một lần) và `load` (nạp dữ liệu) |
| [`static/js/app.js`](static/js/app.js) | Đăng nhập, điều hướng theo hash (`#overview`, `#realtime`, ...), kết nối SSE, chuông cảnh báo, đổi giao diện sáng/tối, chọn khoảng thời gian |
| [`static/icons/`](static/icons/) | Icon SVG tải từ thiết kế, hiển thị bằng CSS `mask` nên đổi màu theo giao diện |

Khi mở trang, SPA kiểm tra cookie `waf_token` còn hạn (`GET /api/dashboard/summary`); nếu không, gọi `POST /api/auth/token` với `admin`/`admin` (chạy local, P2 ở chế độ `AUTH_ALLOW_ANY`). P2 từ chối (production chỉ nhận tài khoản trong `AUTH_CLIENTS`) thì SPA hiện form đăng nhập. P2 đặt cookie `waf_token`, sau đó mọi `fetch` và `EventSource` đều gửi kèm cookie. Kết nối SSE `GET /api/dashboard/stream` được mở ngay khi tải trang. Giao diện tối/sáng lưu trong `localStorage` (`waf-theme`), có thể ép bằng `?theme=dark|light`. Khoảng thời gian (1 giờ, 6 giờ, 24 giờ, 7 ngày) áp dụng cho các màn hình đọc thống kê.

| Màn hình | Dữ liệu |
|---|---|
| Tổng quan | `GET /api/dashboard/overview?hours=` (KPI so với kỳ trước, diễn biến theo mức độ, phân loại, top domain/rule, mật độ theo giờ, top IP), `GET /api/dashboard/attacks?limit=3` (sự kiện gần đây). Tự làm mới mỗi 15 giây khi đang mở |
| Thời gian thực | SSE: `log.enriched` đếm lưu lượng mỗi giây (biểu đồ 60 giây), `attack.detected` vào bảng (giữ trong bộ nhớ trình duyệt, hiện 10 dòng) và khung chi tiết; tạm dừng, xoá, lọc theo IP/đường dẫn, tên miền, mức độ, loại |
| Lịch sử | `GET /api/dashboard/attacks` với `hours`, `q`, `domain`, `attack_type`, `severity`, `limit`, `offset`, `histogram=true`; ẩn/hiện cột, phân trang, xuất CSV (tối đa 1000 dòng khớp bộ lọc), ngăn kéo chi tiết |
| Tên miền | P1: `GET/POST /api/scope/allowlist`, `DELETE /api/scope/allowlist/{target}`; số tấn công và xu hướng từ `GET /api/dashboard/stats/domains?hours=` |
| Quy tắc | rule-api: `GET/POST /api/rules/`, `PUT/DELETE /api/rules/{id}`, `POST /api/rules/validate`, `POST /api/rules/test`; lượt khớp và xu hướng từ `GET /api/dashboard/stats/rules?hours=` |
| Kết nối | `GET /api/dashboard/links`; danh sách rỗng thì dùng liên kết mặc định tới Grafana (:3000), Prometheus (:9090), Mailpit (:8025), Kafka UI (:8085), Swagger UI (:8090) theo hostname hiện tại. Trình duyệt thử mở từng liên kết (`fetch` chế độ `no-cors`) mỗi 30 giây để báo trạng thái |

Khác biệt có chủ đích so với thiết kế: hệ thống chỉ phát hiện (không chặn) nên trạng thái là "Đã ghi nhận · HTTP <mã>" thay cho "Đã chặn · 403"; không có dữ liệu quốc gia/ASN nên bảng top IP hiện cột "Tỉ trọng"; P1 không lưu upstream của tên miền; trường kiểm tra của rule là các `targets` và `normalizers` thật của detector.

## API HTTP

OpenAPI: [`contracts/openapi/dashboard.yaml`](../../contracts/openapi/dashboard.yaml).

| Method | Path (service) | Qua gateway | Xác thực |
|---|---|---|---|
| `GET` | `/` | `/` | không |
| `GET` | `/rules` | `/rules` | không; chuyển hướng 307 tới `/#rules` |
| `GET` | `/dashboard/summary` | `/api/dashboard/summary` | JWT |
| `GET` | `/dashboard/attacks` | `/api/dashboard/attacks` | JWT |
| `GET` | `/dashboard/overview` | `/api/dashboard/overview` | JWT |
| `GET` | `/dashboard/stats/domains` | `/api/dashboard/stats/domains` | JWT |
| `GET` | `/dashboard/stats/rules` | `/api/dashboard/stats/rules` | JWT |
| `GET` | `/dashboard/stream` | `/api/dashboard/stream` | JWT (trình duyệt dùng cookie `waf_token`) |
| `GET` | `/dashboard/links` | `/api/dashboard/links` | JWT |
| `GET` | `/static/...` | `/static/...` (vùng rate limit riêng `static_rl`) | không |
| `GET` | `/healthz` | không (gateway có `/healthz` riêng) | không |

JWT do gateway kiểm tra qua P2. Gateway trả 404 cho `/dashboard` và `/dashboard/...` gọi thẳng, buộc đi qua `/api/dashboard/`.

- `/dashboard/summary` trả `traffic` (`domain`, `minute`, `count`), `top_attacks`, `top_ips`, `alerts`, `stages`, `recent`; các danh sách mới nhất trước.
- `/dashboard/attacks` nhận `limit` (1-1000, mặc định 100), `offset`, `hours` (1-720, bỏ trống = mọi thời điểm), `q` (chuỗi con trong IP, URL, mã rule, domain; không phân biệt hoa thường; `%` và `_` là ký tự thường), `domain`, `attack_type`, `severity` (khớp chính xác), `histogram`. Trả `{attacks, total, unique_ips, histogram?, count, persisted: true}`: `total` và `unique_ips` tính trên bộ lọc, `count` là tổng số dòng của bảng. Chuỗi lọc dài quá 200 ký tự trả 422. Không có DB hoặc truy vấn lỗi thì trả `{attacks: [], total: 0, persisted: false}`.
- `/dashboard/overview`, `/dashboard/stats/domains`, `/dashboard/stats/rules` nhận `hours` (1-720, mặc định 24). Không có DB hoặc truy vấn lỗi thì trả `persisted: false` kèm dữ liệu rỗng. Chi tiết trường: OpenAPI.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Bootstrap servers Kafka |
| `EVENT_GROUP` | `p5-dashboard` | Consumer group |
| `DATABASE_URL` | rỗng | DSN Postgres; rỗng thì không lưu lịch sử, `/dashboard/attacks` trả danh sách rỗng |
| `DASHBOARD_TZ` | `Asia/Ho_Chi_Minh` | Múi giờ chia ngày/giờ cho bản đồ nhiệt. Giao diện luôn hiển thị theo UTC+7, đổi biến này thì hai bên lệch nhau |
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

- [`test_dashboard.py`](test_dashboard.py): `AggregateStore` (đếm traffic theo phút, cửa sổ 60 phút, top tấn công và IP, lịch sử cảnh báo, `stage.completed`, feed `recent`), payload SSE giữ `event_id`/`request_id`, `DASHBOARD_LINKS`, các route HTTP với repo giả (tham số lọc và giới hạn, 422, `persisted: false` khi không có DB) và file tĩnh (`Cache-Control: no-cache`).
- [`test_stats.py`](test_stats.py): hàm thuần của thống kê (cửa sổ thời gian, điền khoảng trống, % thay đổi, diễn biến, bản đồ nhiệt, thoát ký tự `ILIKE`).
- [`test_stats_repo.py`](test_stats_repo.py): chạy thật các câu SQL của `AttackRepo.search` và `StatsRepo` trên Postgres. Chỉ chạy khi có `TEST_DATABASE_URL` (database riêng cho test vì script xoá và tạo lại schema `detection`, `alerting`); không có thì in `SKIP`, nên `make test-python` không cần Postgres.

```bash
docker network create p5-test
docker run -d --name p5-testdb --network p5-test -e POSTGRES_PASSWORD=test postgres:17-alpine
until docker exec p5-testdb pg_isready -U postgres -q; do sleep 1; done
docker run --rm --network p5-test -v "$PWD/services:/app:ro" -w /app/p5-dashboard -e PYTHONPATH=/app:/app/p5-dashboard \
  -e TEST_DATABASE_URL=postgresql://postgres:test@p5-testdb:5432/postgres waf/base:dev python -B test_stats_repo.py
docker rm -f p5-testdb && docker network rm p5-test
```

Chưa có test tự động cho giao diện (JS) và cho luồng SSE thật.

## Giới hạn

- Tổng hợp trong bộ nhớ không bền và không idempotent: mất khi khởi động lại (consumer đọc tiếp từ offset đã commit, không nạp lại lịch sử), và một event được giao lại sẽ bị đếm hai lần. Chỉ `detection.attacks` bền và chống trùng theo `event_id`.
- Chỉ `attack.detected` được lưu DB; cảnh báo trong `/dashboard/summary` là 200 bản trong bộ nhớ (lịch sử cảnh báo bền nằm ở P4).
- API không tự kiểm tra `X-Auth-Subject`, dựa hoàn toàn vào gateway; SPA thử client `admin`/`admin` trước (tiện khi chạy local), bị từ chối mới hiện form đăng nhập.
- Service không gọi P1 `/scope/check`, tin vào việc detector và các bước thu thập đã lọc scope. Client SSE chậm có thể mất event khi hàng đợi đầy.
