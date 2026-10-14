# Auth Service (P2, Người 3)

Cấp và xác thực JWT (HS256, khoá `JWT_SECRET`) cho API nội bộ. API gateway gọi P2 qua `auth_request` cho mọi route cần xác thực.

## Vai trò

### Cấp token: `POST /auth/token`

- Body JSON `{"client_id": "...", "client_secret": "..."}`. Body sai định dạng được coi như rỗng; thiếu một trong hai trường trả 401.
- Xác thực client ([`clients.py`](clients.py)):
  - `AUTH_ALLOW_ANY=true` (mặc định): chấp nhận mọi cặp `client_id`/`client_secret` không rỗng.
  - Giá trị khác: chỉ chấp nhận client có trong `AUTH_CLIENTS`, so sánh secret bằng `hmac.compare_digest`.
- Thành công: 200 `{"access_token", "token_type": "Bearer", "expires_in"}` và đặt cookie `waf_token` (`Path=/`, `HttpOnly`, `SameSite=Strict`, `Max-Age = expires_in`) để trình duyệt dùng được `EventSource`/`WebSocket`.
- Thất bại: 401 `{"error":"unauthorized","message":...}`.

### Claims ([`tokens.py`](tokens.py))

| Claim | Giá trị |
|---|---|
| `sub` | `client_id` |
| `roles` | luôn `["admin"]` (`DEFAULT_ROLES` trong `clients.py`) |
| `iss` | `waf` |
| `iat` | thời điểm cấp (giây Unix) |
| `exp` | `iat + AUTH_TOKEN_TTL` (mặc định 3600 giây) |

### Xác thực: `GET /auth/verify`

- Lấy token từ header `Authorization: Bearer <jwt>`; không có header dạng này thì đọc cookie `waf_token`.
- Kiểm tra chữ ký (chỉ chấp nhận HS256), hạn `exp`, `iss = waf`, và bắt buộc có `exp`, `iat`, `sub`.
- Hợp lệ: 200 `{"sub", "roles"}` kèm header `X-Auth-Subject` (giá trị `sub`) và `X-Auth-Roles` (các role nối bằng dấu phẩy).
- Không hợp lệ: 401, `message` là `missing token`, `token expired` hoặc `invalid token`, kèm `WWW-Authenticate: Bearer realm="waf"`.

### Tích hợp với gateway

Cấu hình tại [`default.conf.template`](../../infra/gateway/templates/default.conf.template) và [`protected.conf`](../../infra/gateway/snippets/protected.conf):

1. Route cần JWT khai báo `auth_request /_auth`. Location nội bộ `/_auth` gửi subrequest không có body tới `AUTH_SERVICE_URL/auth/verify`, chuyển tiếp header `Authorization` (cookie của request gốc cũng đi kèm).
2. P2 trả 2xx: gateway đọc `X-Auth-Subject`, `X-Auth-Roles` bằng `auth_request_set` rồi đặt lại hai header này khi chuyển tiếp tới service đích, ghi đè mọi giá trị client tự gửi. P2 trả 401: gateway trả 401 JSON `{"error":"unauthorized","message":"missing or invalid bearer token"}`.
3. `/api/auth/token` là route công khai duy nhất, giới hạn 10 request/phút cho mỗi IP (burst 5) và xoá `X-Auth-Subject`/`X-Auth-Roles` trước khi chuyển tiếp.

## API HTTP

Contract: [`contracts/openapi/auth.yaml`](../../contracts/openapi/auth.yaml).

| Method | Path tại service | Qua gateway | Xác thực |
|---|---|---|---|
| GET | `/healthz` | không có route | không (healthcheck của container) |
| POST | `/auth/token` | `/api/auth/token` | không (công khai, giới hạn tần suất ở gateway) |
| GET | `/auth/verify` | gọi nội bộ qua `/_auth`; từ ngoài là `/api/auth/verify` | Bearer hoặc cookie `waf_token` |

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JWT_SECRET` | bắt buộc | khoá ký và kiểm tra HS256; thiếu thì service dừng khi khởi động |
| `AUTH_TOKEN_TTL` | `3600` | thời hạn token (giây), đồng thời là `expires_in` và `Max-Age` của cookie |
| `AUTH_ALLOW_ANY` | `true` | `true` (không phân biệt hoa thường): chấp nhận mọi client; giá trị khác: chỉ client trong `AUTH_CLIENTS` |
| `AUTH_CLIENTS` | rỗng | danh sách `id:secret` phân tách bằng dấu phẩy; mục thiếu `:`, thiếu id hoặc secret bị bỏ qua |
| `HTTP_PORT` | `8000` | cổng HTTP |
| `LOG_LEVEL` | `info` | mức log JSON ra stdout |

[`docker-compose.yml`](../../docker-compose.yml) và [`deploy/platform`](../../deploy/platform/docker-compose.yml) chỉ đặt `JWT_SECRET` cho P2 (bắt buộc có trong `.env`), không đặt `AUTH_ALLOW_ANY` hay `AUTH_CLIENTS`. P2 không dùng PostgreSQL hay Kafka, dù compose vẫn chờ `postgres` và `kafka-init` sẵn sàng trước khi khởi động.

## Chạy và kiểm thử

Service thuộc profile `p2`, `platform` và `app`. Bật một trong các profile đó trong `COMPOSE_PROFILES`, rồi:

```bash
make build-base                        # image nền waf/base:dev
docker compose up -d --build p2-auth
docker compose ps p2-auth              # cột STATUS phải là healthy
```

`HEALTHCHECK` trong [`Dockerfile`](Dockerfile) gọi `http://127.0.0.1:8000/healthz` mỗi 10 giây. Để gateway dùng P2 thật, đặt `AUTH_SERVICE_URL=http://p2-auth:8000` trong `.env` (giá trị của `.env.example`):

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/token -H 'content-type: application/json' \
  -d '{"client_id":"dev","client_secret":"dev"}' | jq -r .access_token)
curl -s -i localhost:8080/api/auth/verify -H "Authorization: Bearer $TOKEN"   # 200, có X-Auth-Subject
```

Unit test [`test_token.py`](test_token.py) gọi trực tiếp `tokens.py`, không cần server: token hợp lệ, sai chữ ký, hết hạn, thiếu token.

```bash
make test-python   # toàn bộ test Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/p2-auth \
  -e PYTHONPATH=/app:/app/p2-auth waf/base:dev python -B test_token.py
```

`make smoke` kiểm tra thêm qua gateway: cấp token, dùng token qua cookie `waf_token`, và `/api/auth/verify` không có token trả 401.

## Giới hạn

- Mặc định `AUTH_ALLOW_ANY=true`: mọi `client_id`/`client_secret` không rỗng đều nhận token, và cả hai file compose giữ nguyên chế độ này. Chỉ phù hợp cho demo.
- Mọi client nhận role `admin`; gateway chỉ kiểm tra token hợp lệ, không phân quyền theo role.
- Không có thu hồi token hay đăng xuất: token dùng được tới khi hết `exp`. Cookie không đặt cờ `Secure` vì gateway chạy HTTP.
- Unit test mới bao phủ `tokens.py`; `clients.py` và các handler HTTP chưa có test.
