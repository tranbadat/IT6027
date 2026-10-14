# c2-parser (C2, Người 1)

Consumer chuẩn hoá: đọc dòng log thô từ `log.raw.ingested`, tách thành các trường có kiểu và phát `log.normalized`. Service không mở cổng HTTP; healthcheck trong `Dockerfile` kiểm tra file heartbeat `/tmp/alive` (ghi mỗi 10 giây, coi là hỏng nếu cũ hơn 30 giây).

## Vai trò trong pipeline

- **Đầu vào:** topic `log.raw.ingested` do [C1](../c1-ingestion/README.md) phát, consumer group lấy từ `EVENT_GROUP`, mặc định `c2-parser`. Chỉ dùng `data.raw` và `data.log_source` (thiếu thì coi là `nginx`).
- **Đầu ra:** topic `log.normalized` theo [`contracts/events`](../../contracts/events/README.md), giữ `request_id` của event vào; `session_id` của envelope lấy từ `sid` trong log (hoặc `session_id` của event vào nếu có). `data` có `src_ip` nên key Kafka là `domain|src_ip`. Consumer chính là [C3](../c3-enrichment/README.md).

| Trường `data` | Kiểu | Nguồn, ghi chú |
|---|---|---|
| `src_ip` | string | Trường đầu dòng, giữ nguyên, không kiểm tra định dạng IP |
| `remote_user` | string | `-` thành rỗng |
| `method`, `protocol` | string | Tách từ dòng request; rỗng nếu request không đúng dạng |
| `path`, `query_string` | string | Phần trước và sau dấu `?` đầu tiên, giữ nguyên percent-encoding |
| `path_decoded`, `query_decoded` | string | `urllib.parse.unquote` một lớp của `path`, `query_string` |
| `status` | int hoặc null | null nếu log ghi `-` |
| `body_bytes` | int | `-` thành 0 |
| `referer`, `user_agent` | string | `-` thành rỗng |
| `host` | string | Trường `"$host"` mở rộng; không có thì điền `domain` của envelope |
| `request_time` | float hoặc null | Giây: `rt=` giữ nguyên, `rt_us=` chia 1000000; null nếu không có |
| `session_id` | string | Trường `sid="..."`; `-` hoặc không có thành rỗng |
| `request_ts` | string hoặc null | `time_local` chuyển sang RFC 3339; null nếu không parse được |
| `log_time_raw` | string | `time_local` gốc trong log |
| `log_source` | string | Lấy từ event vào |

**Kiểm tra scope:** trước khi parse, handler gọi `ScopeClient.allowed(domain)` ([`common/scope.py`](../common/scope.py)) với `domain` của envelope. Kết quả cache theo `cache_ttl_seconds`; gọi Scope Service lỗi thì từ chối (fail closed, cache 5 giây). Event ngoài scope được ghi log info `skip out-of-scope event` rồi bỏ qua, offset vẫn được commit.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Kafka bootstrap servers, ví dụ `kafka:19092` |
| `SCOPE_SERVICE_URL` | bắt buộc | Địa chỉ Scope Service, ví dụ `http://p1-scope:8000` hoặc `http://mock-api` |
| `EVENT_GROUP` | `c2-parser` | Consumer group; đổi khi cần chạy song song hai bản khác nhau |
| `LOG_LEVEL` | `info` | Mức log JSON ra stdout; `debug` thêm log `normalized` cho từng event |

## Xử lý đặc biệt

- **Định dạng chấp nhận:** một regex trong [`parser.py`](parser.py) khớp phần **combined** chuẩn (`remote ident user [time] "request" status bytes "referer" "user_agent"`), theo sau là ba trường mở rộng của `waf_combined`, đều tuỳ chọn và theo đúng thứ tự: `"host"`, `rt=<giây>` hoặc `rt_us=<micro giây>`, `sid="..."`. Vì vậy log `waf_combined` của Nginx/Apache (xem [`docs/infrastructure.md`](../../docs/infrastructure.md)) và log combined chuẩn của web server bất kỳ đều parse được. Referer và User-Agent chấp nhận chuỗi có escape dạng `\"`.
- **Nhiều dòng trong một event:** `data.raw` có thể chứa nhiều dòng (gửi theo lô, CRLF). `parse_raw` tách theo dòng, bỏ dòng trống và parse hết trước khi publish; mỗi dòng hợp lệ thành một `log.normalized` (cùng `request_id` của event gốc).
- **Dòng sai định dạng:** dòng hỏng nằm lẫn với dòng hợp lệ chỉ bị bỏ qua và ghi log warning `skipped malformed log lines` (không dead-letter, để khi xử lý lại không publish trùng các dòng hợp lệ). Khi không dòng nào hợp lệ, `parse_raw` ném `ValueError`; `bus.consume` ([`common/bus.py`](../common/bus.py)) gửi nguyên value sang `waf.deadletter` (header `error`, `source_topic`, `consumer_group`, `dead_lettered_at`) rồi commit. Event sai envelope hoặc thiếu `data.raw` cũng vào dead-letter như vậy.
- **Request rác trong dòng hợp lệ:** nếu chuỗi request không có dạng `METHOD target HTTP/x` (ví dụ client gửi byte TLS vào cổng HTTP), dòng vẫn được phát với `method`, `protocol` rỗng và toàn bộ chuỗi request nằm trong `path`, để bước phát hiện vẫn phân tích được.
- **Giải mã URL:** `path_decoded`, `query_decoded` giải mã percent-encoding đúng một lớp (`%2527` thành `%27`) và không đổi `+` thành dấu cách. Bản gốc giữ trong `path`, `query_string`.
- **Giao nhận:** publish chờ broker xác nhận; nếu không được xác nhận, process dừng để Docker khởi động lại và đọc lại message chưa commit (at-least-once).

## Chạy và kiểm thử

C2 thuộc profile `c2`, `collection`, `app`; thường bật cùng C1 để có dữ liệu. Trong triển khai tách tầng, C2 nằm ở stack [`deploy/pipeline`](../../deploy/pipeline/docker-compose.yml) cùng C3 và detector.

```bash
# .env: COMPOSE_PROFILES=mock,docs,c1,c2 và SCOPE_SERVICE_URL=http://mock-api
make up
docker compose up -d --build c2-parser   # chỉ build lại C2
make logs s=c2-parser
make bus-peek t=log.normalized
make bus-peek t=waf.deadletter           # dòng không parse được
```

Unit test [`test_parser.py`](test_parser.py) có 9 test cho `parser.py` (dòng Nginx `rt=`, Apache `rt_us=`, trường `-`, log combined chuẩn, dòng sai định dạng, khối nhiều dòng, CRLF và dòng trống, dòng hỏng lẫn dòng hợp lệ, khối không có dòng hợp lệ), không cần Kafka; `main.py` không có unit test.

```bash
make test-python   # chạy test của mọi service Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/c2-parser \
  -e PYTHONPATH=/app:/app/c2-parser waf/base:dev python -B test_parser.py
```

## Giới hạn

- Chuỗi trong ngoặc kép thứ ba sau User-Agent luôn được hiểu là `host`. Với format `main` mặc định của Nginx (kết thúc bằng `"$http_x_forwarded_for"`), giá trị X-Forwarded-For hoặc `-` sẽ nằm trong `host` và không được thay bằng domain.
- Escape `\xHH` mà Nginx ghi cho ký tự đặc biệt không được giải mã. Format khác (log JSON, combined có thêm trường ở vị trí khác) đều vào dead-letter.
- `host` trong log thường đến từ header `Host` do client đặt nên có thể khác `domain` của envelope; scope và key Kafka luôn theo `domain` do C1 suy từ tên file.
- Xử lý lại sau khi khởi động lại sinh `event_id` mới (UUID ngẫu nhiên), nên consumer phía sau không loại được bản trùng bằng `event_id`.
