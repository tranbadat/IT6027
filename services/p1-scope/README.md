# Scope Service (P1, Người 3)

Xác nhận một domain hoặc ứng dụng có nằm trong phạm vi được phép xử lý log hay không. Danh sách phạm vi (allowlist) lưu trong PostgreSQL và được quản trị qua API HTTP.

## Vai trò

P1 là điểm kiểm tra phạm vi của toàn hệ thống. C1, C2, C3 (qua [`common/scope.py`](../common/scope.py)) và detector Java (`HttpScopeService`) gọi `GET $SCOPE_SERVICE_URL/scope/check?domain=...` trước khi xử lý log của một domain, cache theo `cache_ttl_seconds` và fail closed khi không gọi được P1.

Contract `GET /scope/check`:

- Truyền một trong hai tham số `domain` hoặc `app`; có cả hai thì `domain` được ưu tiên và `target_type = domain`.
- Giá trị phải khớp `^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$`. Thiếu hoặc sai trả 400 `{"error":"invalid_request","message":...}`.
- Tra cứu theo cột `target` sau khi chuyển chữ thường (không xét `target_type`). `allowed = true` khi có bản ghi và `enabled = true`.
- Luôn trả 200 khi tham số hợp lệ, kể cả khi bị từ chối. `cache_ttl_seconds` cố định 60 giây.

```json
{"target": "shop-app", "target_type": "app", "allowed": true,
 "reason": "in scope allowlist", "cache_ttl_seconds": 60}
```

### Bảng `scope.allowlist`

Schema `scope` do [`01-schemas.sql`](../../infra/postgres/init/01-schemas.sql) tạo (chỉ chạy khi volume Postgres còn trống). Khi khởi động, P1 tạo bảng nếu chưa có:

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `target` | `text` | khoá chính, lưu chữ thường |
| `target_type` | `text` | `domain` hoặc `app`, mặc định `domain` |
| `enabled` | `boolean` | mặc định `true` |
| `note` | `text` | ghi chú, bản ghi seed có `note = seeded` |
| `created_at` | `timestamptz` | mặc định `now()` |

Seed: nếu bảng trống, P1 chèn các giá trị trong `SCOPE_ALLOWLIST` (chữ thường; không chứa dấu `.` thì là `app`, ngược lại là `domain`). Khi bảng đã có dữ liệu, `SCOPE_ALLOWLIST` bị bỏ qua, mọi thay đổi phải đi qua API bên dưới.

## API HTTP

Contract: [`contracts/openapi/scope.yaml`](../../contracts/openapi/scope.yaml).

| Method | Path tại service | Qua gateway | Xác thực |
|---|---|---|---|
| GET | `/healthz` | không có route | không (healthcheck của container) |
| GET | `/scope/check?domain=` hoặc `?app=` | `/api/scope/check` | JWT khi đi qua gateway |
| GET | `/scope/allowlist` | `/api/scope/allowlist` | JWT khi đi qua gateway |
| POST | `/scope/allowlist` | `/api/scope/allowlist` | JWT khi đi qua gateway |
| DELETE | `/scope/allowlist/{target}` | `/api/scope/allowlist/{target}` | JWT khi đi qua gateway |

- `GET /scope/allowlist` trả `{"items": [{"target", "target_type", "enabled", "note"}]}`, sắp xếp theo `target`.
- `POST /scope/allowlist` nhận JSON `{"target", "target_type"?, "enabled"?, "note"?}`. `target` được chuyển chữ thường và kiểm tra cùng biểu thức như trên (sai thì 400 `{"detail":"invalid target"}`); `target_type` mặc định suy từ dấu `.`; `enabled` mặc định `true`. Target đã tồn tại thì bị ghi đè `target_type`, `enabled`, `note` (upsert). Trả `{"target", "target_type", "enabled"}`.
- `DELETE /scope/allowlist/{target}` xoá theo chữ thường và luôn trả 200 `{"deleted": "<target>"}`, kể cả khi target không tồn tại.

Gateway: route `/api/scope/` dùng [`protected.conf`](../../infra/gateway/snippets/protected.conf), tức là xác thực JWT qua P2 (`auth_request`), bỏ tiền tố `/api` rồi chuyển tới `SCOPE_SERVICE_URL`. Service nội bộ gọi thẳng `http://p1-scope:8000`, không cần token.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DATABASE_URL` | bắt buộc | DSN PostgreSQL; thiếu thì service dừng khi khởi động. Kết nối thử lại tối đa 30 lần, cách nhau 2 giây |
| `SCOPE_ALLOWLIST` | `shop.local,blog.local,shop-app` | danh sách seed, phân tách bằng dấu phẩy, chỉ dùng khi bảng trống |
| `HTTP_PORT` | `8000` | cổng HTTP |
| `LOG_LEVEL` | `info` | mức log JSON ra stdout (`debug`, `info`, `warning`, `error`) |

P1 không dùng Kafka. Trong triển khai tách stack, P1 chạy trong [`deploy/platform`](../../deploy/platform/docker-compose.yml) với cùng biến môi trường.

## Chạy và kiểm thử

Service thuộc profile `p1`, `platform` và `app` trong [`docker-compose.yml`](../../docker-compose.yml). Bật một trong các profile đó trong `COMPOSE_PROFILES` (file `.env`), rồi build riêng service:

```bash
make build-base                         # image nền waf/base:dev
docker compose up -d --build p1-scope   # kéo theo postgres và kafka-init
docker compose ps p1-scope              # cột STATUS phải là healthy
```

`HEALTHCHECK` trong [`Dockerfile`](Dockerfile) gọi `http://127.0.0.1:8000/healthz` mỗi 10 giây. P1 không publish cổng ra host; từ máy host, gọi qua gateway (cần `SCOPE_SERVICE_URL=http://p1-scope:8000` trong `.env`, là giá trị của `.env.example`):

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/token -H 'content-type: application/json' \
  -d '{"client_id":"dev","client_secret":"dev"}' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" 'localhost:8080/api/scope/check?domain=shop.local'
curl -s -X POST localhost:8080/api/scope/allowlist -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"target":"myapp.com","note":"app moi"}'
```

Unit test [`test_scope.py`](test_scope.py) có 8 test, gọi API qua FastAPI `TestClient` với kết nối DB giả (không cần Postgres): domain được phép, bị từ chối hoặc đã tắt, domain ưu tiên hơn app, kiểm tra theo app, tham số thiếu/sai, thêm allowlist (chữ thường, suy `target_type`), từ chối `target` hoặc `target_type` sai, xoá idempotent. `make test-python` chạy toàn bộ test Python; chạy riêng bằng:

```bash
docker run --rm -v "$PWD/services:/app:ro" -w /app/p1-scope \
  -e PYTHONPATH=/app:/app/p1-scope waf/base:dev python -B test_scope.py
```

`make smoke` có kiểm tra `/api/scope/check` qua gateway: thiếu token trả 401, `shop.local` được phép, `internal.local` bị từ chối, thiếu tham số trả 400.

## Giới hạn

- P1 không tự kiểm tra xác thực, JWT chỉ được áp ở gateway. Container nào trong cùng network Docker cũng gọi được `POST`/`DELETE /scope/allowlist` mà không cần token.
- Chỉ mở một kết nối PostgreSQL lúc khởi động và không tự kết nối lại. `/healthz` không kiểm tra DB, nên container vẫn `healthy` khi đã mất kết nối.
- `SCOPE_ALLOWLIST` chỉ có tác dụng khi bảng trống. `cache_ttl_seconds` là hằng số 60, nên client có thể dùng kết quả cũ tối đa 60 giây sau khi allowlist thay đổi.
- `POST` không kiểm tra giá trị `target_type`; lỗi 400 của `POST` dùng định dạng mặc định của FastAPI (`detail`) thay vì `{error, message}` như `/scope/check`.
