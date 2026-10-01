# Event schema của Log Collection (Người 1)

Tài liệu mô tả các event do C1, C2, C3 phát ra, để Người 2 (Detection) và Người 3 (Platform) tích hợp
sớm. Nguồn sự thật ở dạng máy đọc được: [`schemas/log-events.schema.json`](../schemas/log-events.schema.json)
(JSON Schema 2020-12). Test tự động kiểm tra mọi event thật do pipeline sinh ra đều khớp schema này.

## 1. Envelope chung

```json
{ "event": "log.enriched", "request_id": "…", "session_id": "…", "timestamp": "2026-09-21T03:15:30.415Z", "data": { } }
```

| Trường | Ý nghĩa |
| --- | --- |
| `event` | Tên event. |
| `request_id` | UUID (hex) sinh ở C1 cho mỗi dòng log, **giữ nguyên** qua `log.raw.ingested` → `log.normalized` → `log.enriched` để truy vết một request xuyên pipeline. |
| `session_id` | Chỉ có ở `log.enriched` (sau khi C3 gom phiên). Dạng `s-<16 hex>`. |
| `timestamp` | Thời điểm **phát event** (UTC, ISO 8601, hậu tố `Z`). Khác với `data.request_time` là thời điểm request xảy ra. |

## 2. Luồng event

```
log file / syslog ─► C1 ─► log.raw.ingested ─► C2 ─┬► log.normalized ─► C3 ─► log.enriched
                                                   └► log.parse_failed   (dòng không parse được)
```

Ba event chính do Người 1 phát ra. Người 2 consume **`log.enriched`** (chứa đủ mọi trường của `log.normalized`).

## 3. `log.raw.ingested` (C1)

| Trường `data` | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `raw_line` | string | Dòng log gốc, chưa xử lý. Nếu bật gộp nhiều dòng thì chứa `\n`. |
| `domain` | string | Domain của nguồn (cấu hình trong `c1.yaml`, hoặc ánh xạ từ tag/host syslog). Đã qua Scope Service. |
| `source` | object | `name`, `type` (`file`\|`syslog`), `server` (`nginx`\|`apache`\|`unknown`); `path` với file; `protocol`, `host`, `tag`, `peer` với syslog. |
| `collected_at` | ISO time | Lúc C1 đọc được dòng này. |
| `truncated` | bool | `true` nếu dòng vượt `max_line_bytes` và đã bị cắt. |
| `line_count` | int | Số dòng vật lý gộp lại (thường là 1). |

Ví dụ:

```json
{
  "event": "log.raw.ingested",
  "request_id": "3f2b9c1e5a7d4e0b8c6a1d2e3f405162",
  "timestamp": "2026-09-21T03:15:30.415Z",
  "data": {
    "raw_line": "81.2.69.142 - - [21/Sep/2026:10:15:30 +0700] \"GET /products?id=1%27%20OR%20%271%27%3D%271 HTTP/1.1\" 500 512 \"https://shop.example.com/\" \"sqlmap/1.7\" rt=0.012 ck=\"PHPSESSID=abc123\"",
    "domain": "shop.example.com",
    "source": {
      "name": "shop-nginx",
      "type": "file",
      "server": "nginx",
      "path": "/var/log/nginx/access.log"
    },
    "collected_at": "2026-09-21T03:15:30.412Z",
    "truncated": false,
    "line_count": 1
  }
}
```

## 4. `log.normalized` (C2)

Mọi trường dưới đây cũng có mặt trong `log.enriched`.

| Trường `data` | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `domain`, `source`, `collected_at` | | Chuyển tiếp từ `log.raw.ingested`. |
| `parse_status` | `ok`\|`partial` | `partial` = tách được phần khung (IP, giờ, status…) nhưng **request line hỏng** (`method`, `path`… là `null`). Vẫn được phát vì request sai chuẩn thường là dấu hiệu quét/tấn công. |
| `parse_warnings` | string[] | Ví dụ `invalid_method`, `empty_request_line`, `space_in_target`, `invalid_timestamp`, `query_params_truncated`, `multiline_record`. |
| `client_ip` | string | IP nguồn (IPv4/IPv6). Nếu server bật HostnameLookups thì có thể là hostname. |
| `remote_user`, `vhost` | string\|null | `vhost` chỉ có với định dạng Apache `vhost_combined`. |
| `request_time` | ISO time (UTC) | Thời điểm request, lấy từ log (đã đổi múi giờ về UTC). |
| `request_time_source` | `log`\|`collected_at` | `collected_at` nghĩa là timestamp trong log hỏng nên dùng giờ thu thập. |
| `request_line` | string | Request line đã bỏ lớp escape của server (`\xHH`, `\"`). |
| `method`, `http_version` | string\|null | |
| `url` | string\|null | Target như trong log (dạng thô, còn `%xx`). |
| `path_raw`, `query_string_raw` | string\|null | Path / query **chưa giải mã**. |
| `path`, `query_string` | string\|null | Path / query **đã giải mã % lặp lại** tới khi ổn định (tối đa 5 lượt) — dùng cái này để khớp rule. Trong query, `+` → dấu cách. |
| `query_params` | `{name,value}[]` | Tham số query đã giải mã, tối đa 200. |
| `decode_passes` | int | Số lượt giải mã có thay đổi. |
| `double_encoded` | bool | `true` nếu `decode_passes ≥ 2` (vd `%252e%252e%252f`). **Chỉ là tín hiệu, không phải kết luận tấn công.** |
| `status` | int | HTTP status. |
| `response_bytes` | int | Kích thước phản hồi (`-` → 0). |
| `response_time_ms` | number\|null | Có nếu log format có `rt=`/`rt_us=`. |
| `headers` | object | `referer`, `user_agent`, `cookie` (`null` nếu không có). |
| `raw_line` | string | Dòng gốc, dùng làm bằng chứng. |

> **Lưu ý riêng tư:** `log.normalized` mang cookie thô nếu web server được cấu hình log cookie. Khuyến nghị chỉ log **cookie phiên** (xem `web-server-setup.md`). C3 loại cookie khỏi `log.enriched`.

### `log.parse_failed` (C2, dead-letter)

Dòng hoàn toàn không nhận ra được định dạng (không khớp cả combined lẫn common). Không service nào bắt buộc consume.
`data`: `domain`, `source`, `error` (`empty_line`\|`unrecognized_format`), `raw_line` (tối đa 2048 ký tự), `collected_at`.

## 5. `log.enriched` (C3)

Toàn bộ trường của `log.normalized` (trừ `headers.cookie`, và giá trị cookie bị che thành `ck="[redacted]"` trong `raw_line`), cộng thêm:

| Trường `data` | Ý nghĩa |
| --- | --- |
| `geo.status` | `ok` \| `non_public` (IP private/loopback) \| `not_found` \| `invalid` \| `disabled` (chưa cấu hình GeoIP database) \| `error`. |
| `geo.country_code`, `country_name`, `city`, `latitude`, `longitude` | Có khi `status=ok` (`city`/toạ độ chỉ có với database City). |
| `session.id` | Trùng `session_id` ở envelope. |
| `session.key_basis` | `ip+cookie` nếu có cookie phiên, ngược lại `ip`. |
| `session.is_new` | `true` ở request đầu tiên của phiên. |
| `session.request_count`, `error_count` | Số request / số request có status ≥ 400 trong phiên tính tới hiện tại. |
| `session.first_seen`, `last_seen`, `duration_s` | Thời gian phiên (theo `request_time`). Phiên kết thúc sau 30 phút im lặng (`SESSION_TIMEOUT_S`). |
| `session.cookie_present` | Log có cookie hay không. |
| `frequency.requests_10s`, `requests_60s` | Số request của **IP** (trong domain) trong 10s / 60s gần nhất. |
| `frequency.errors_4xx_60s`, `errors_5xx_60s`, `error_ratio_60s` | Đầu vào thô cho tín hiệu thống kê của D3. |

Ví dụ đầy đủ (sinh ra bởi code, với database GeoIP2 City test của MaxMind):

```json
{
  "event": "log.enriched",
  "request_id": "3f2b9c1e5a7d4e0b8c6a1d2e3f405162",
  "timestamp": "2026-09-21T08:35:29.137Z",
  "data": {
    "parse_status": "ok",
    "parse_warnings": [],
    "vhost": null,
    "client_ip": "81.2.69.142",
    "remote_user": null,
    "request_time": "2026-09-21T03:15:30Z",
    "request_time_source": "log",
    "request_line": "GET /products?id=1%27%20OR%20%271%27%3D%271 HTTP/1.1",
    "method": "GET",
    "url": "/products?id=1%27%20OR%20%271%27%3D%271",
    "path": "/products",
    "path_raw": "/products",
    "query_string": "id=1' OR '1'='1",
    "query_string_raw": "id=1%27%20OR%20%271%27%3D%271",
    "query_params": [
      {
        "name": "id",
        "value": "1' OR '1'='1"
      }
    ],
    "http_version": "HTTP/1.1",
    "status": 500,
    "response_bytes": 512,
    "response_time_ms": 12.0,
    "headers": {
      "referer": "https://shop.example.com/",
      "user_agent": "sqlmap/1.7"
    },
    "decode_passes": 1,
    "double_encoded": false,
    "raw_line": "81.2.69.142 - - [21/Sep/2026:10:15:30 +0700] \"GET /products?id=1%27%20OR%20%271%27%3D%271 HTTP/1.1\" 500 512 \"https://shop.example.com/\" \"sqlmap/1.7\" rt=0.012 ck=\"PHPSESSID=abc123\"",
    "domain": "shop.example.com",
    "source": {
      "name": "shop-nginx",
      "type": "file",
      "server": "nginx",
      "path": "/var/log/nginx/access.log"
    },
    "collected_at": "2026-09-21T03:15:30.412Z",
    "geo": {
      "status": "ok",
      "country_code": "GB",
      "country_name": "United Kingdom",
      "city": "London",
      "latitude": 51.5142,
      "longitude": -0.0931
    },
    "session": {
      "id": "s-c87a987a18084614",
      "is_new": true,
      "key_basis": "ip+cookie",
      "request_count": 1,
      "error_count": 1,
      "first_seen": "2026-09-21T03:15:30.000Z",
      "last_seen": "2026-09-21T03:15:30.000Z",
      "duration_s": 0.0,
      "cookie_present": true
    },
    "frequency": {
      "window_seconds": 60,
      "requests_10s": 1,
      "requests_60s": 1,
      "errors_4xx_60s": 0,
      "errors_5xx_60s": 1,
      "error_ratio_60s": 1.0
    }
  },
  "session_id": "s-c87a987a18084614"
}
```

## 6. Ràng buộc và giả định — **cần Người 2, Người 3 xác nhận**

1. **Scope Service (P1).** Repo chưa có OpenAPI của P1 nên C1–C3 giả định:
   `GET {SCOPE_URL}/scope/check?domain=<d>` → `200 {"domain":"<d>","allowed":true|false}`, tuỳ chọn `Authorization: Bearer <SCOPE_TOKEN>`. HTTP 403/404 cũng được hiểu là "ngoài phạm vi". Nếu contract khác chỉ cần sửa `wafcollect/common/scope.py`. `wafcollect.tools.mock_scope` cài đúng giả định này.
2. **Event Bus (P3).** Hiện dùng **Redis Streams**: mỗi event một stream `waf:events:<tên event>`, message có một field `payload` chứa envelope JSON, mỗi service một consumer group. Nếu Người 3 chọn broker khác chỉ cần thay `wafcollect/common/bus.py`.
3. **Access log không có body.** Mọi trường của request đến từ access log nên **không có request body**. Rule D1 nhắm vào body sẽ không có dữ liệu từ nguồn này; cần thống nhất (chỉ khớp URL/query/header, hoặc mở rộng thu thập sau).
4. **Trường mở rộng ở cuối dòng log** (`rt=`, `ck=`) cần cấu hình `log_format` phía web server. Không có thì `response_time_ms=null` và phiên chỉ gom theo IP.
5. **`request_id` xuyên suốt** và **`session_id` chỉ có từ `log.enriched`.** Nếu Người 3 muốn khoá khác, báo lại.
6. **Đảm bảo giao hàng: at-least-once.** Crash/restart có thể phát lại vài dòng gần nhất; downstream nên dedupe theo `request_id` nếu cần. Không bao giờ bỏ mất dòng vì lỗi tạm thời (bus hoặc Scope Service sập).
