# c1-ingestion (C1, Người 1)

Worker thu thập log: tail liên tục access log của Nginx/Apache mục tiêu và phát mỗi dòng mới thành một event `log.raw.ingested`. Service không mở cổng HTTP; healthcheck trong `Dockerfile` kiểm tra file heartbeat `/tmp/alive` (một thread ghi mỗi 10 giây, coi là hỏng nếu cũ hơn 30 giây).

## Vai trò trong pipeline

- **Đầu vào:** các file `$LOG_DIR/nginx/*access.log` và `$LOG_DIR/apache/*access.log`. Tên thư mục con (`nginx` hoặc `apache`) chính là `log_source`. C1 không đọc topic nào nên không có consumer group (không dùng `EVENT_GROUP`).
- **Đầu ra:** topic `log.raw.ingested`, envelope theo [`contracts/events`](../../contracts/events/README.md) với `source = c1-ingestion` và `request_id` là UUID mới cho mỗi dòng. `data` không có `src_ip` nên key Kafka chỉ là `domain` (viết thường): mọi dòng của một domain vào cùng một partition. Consumer chính là [C2](../c2-parser/README.md).

| Trường `data` | Kiểu | Ý nghĩa |
|---|---|---|
| `raw` | string | Nội dung dòng log, không gồm `\n`, giải mã UTF-8 (byte lỗi được thay bằng ký tự thay thế) |
| `log_source` | string | `nginx` hoặc `apache` |
| `log_path` | string | Đường dẫn file bên trong container |
| `collected_at` | string | Thời điểm C1 phát dòng, RFC 3339 (UTC) |

**Kiểm tra scope:** trước khi publish, mỗi dòng đi qua `ScopeClient.allowed(domain)` ([`common/scope.py`](../common/scope.py)), gọi `GET $SCOPE_SERVICE_URL/scope/check?domain=...` với timeout 3 giây. Kết quả được cache theo `cache_ttl_seconds` (mặc định 60 nếu phản hồi không có, tối thiểu 1 giây). Gọi lỗi thì từ chối (fail closed) và cache kết quả từ chối 5 giây. Dòng ngoài scope bị bỏ; C1 chỉ đếm và ghi log info `skipped out-of-scope lines` (kèm `count`, `last_domain`) tối đa một lần mỗi 60 giây.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Kafka bootstrap servers, ví dụ `kafka:19092` |
| `SCOPE_SERVICE_URL` | bắt buộc | Địa chỉ Scope Service, ví dụ `http://p1-scope:8000` hoặc `http://mock-api` |
| `LOG_DIR` | `/var/log/waf` | Thư mục gốc chứa hai thư mục con `nginx/` và `apache/` |
| `C1_DOMAIN` | rỗng | Nếu đặt, gán domain này cho mọi file thay vì suy từ tên file |
| `C1_READ_EXISTING` | `false` | `true`: lần quét đầu đọc file có sẵn từ đầu; ngược lại bắt đầu từ cuối file |
| `LOG_LEVEL` | `info` | Mức log JSON ra stdout: `debug`, `info`, `warning`, `error` |

Thiếu `EVENT_BUS_URL` hoặc `SCOPE_SERVICE_URL` thì service dừng ngay khi khởi động. Kết nối Kafka được thử tối đa 30 lần, mỗi lần cách 2 giây.

## Xử lý đặc biệt

- **Chọn file:** chỉ nhận file khớp `*access.log` trong `nginx/` và `apache/`. `shop.local.access.log` và `access.log` được theo dõi; bản đã rotate (`*.access.log.1`) và `error.log` bị bỏ qua. Danh sách file được quét lại ở mỗi vòng poll (0,5 giây).
- **Suy domain từ tên file:** `<domain>.access.log` cho domain `<domain>` (`shop.local.access.log` thành `shop.local`). File tên `access.log` sẽ cho domain `access`, nên khi cắm vào Nginx của một app cụ thể cần đặt `C1_DOMAIN`. Domain không lấy từ header `Host` vì client tự đặt được giá trị này (xem [`docs/infrastructure.md`](../../docs/infrastructure.md)).
- **Theo dõi bằng inode + offset:** mỗi file giữ `(inode, offset)` và một buffer byte cho phần dòng chưa xong. Mỗi vòng đọc từ offset đến EOF và chỉ phát các dòng đã kết thúc bằng `\n`; phần dở dang chờ vòng sau. Dòng rỗng bị bỏ.
- **Rotation và truncation:** inode đổi (file bị đổi tên rồi tạo mới) hoặc kích thước nhỏ hơn offset (file bị truncate) thì C1 đọc lại từ đầu file và xoá buffer.
- **Vị trí bắt đầu:** file có sẵn ở lần quét đầu bắt đầu từ cuối (trừ khi `C1_READ_EXISTING=true`); file xuất hiện sau đó luôn được đọc từ đầu. File biến mất thì ngừng theo dõi, không làm dừng worker.
- **Lỗi:** publish thất bại chỉ ghi log error `publish failed` rồi tiếp tục; lỗi bất ngờ trong một vòng ghi `poll cycle failed`. Giữa hai vòng, `bus.process_events(0.5)` vừa chờ vừa phục vụ callback của producer.

## Chạy và kiểm thử

Trong stack phát triển ([`docker-compose.yml`](../../docker-compose.yml)), C1 thuộc profile `c1`, `collection`, `app` và mount volume `nginx-logs`, `apache-logs` ở chế độ chỉ đọc. C1 cần Scope Service (P1 hoặc mock).

```bash
# .env: COMPOSE_PROFILES=mock,docs,c1 và SCOPE_SERVICE_URL=http://mock-api
make up
docker compose up -d --build c1-ingestion   # chỉ build lại C1
make demo                                    # sinh traffic tới web-nginx
make logs s=c1-ingestion
make bus-peek t=log.raw.ingested
make rotate-logs                             # thử xử lý log rotation
```

Bản sensor tách riêng ([`deploy/sensor/docker-compose.yml`](../../deploy/sensor/docker-compose.yml)) chỉ chứa C1: đặt `SENSOR_NGINX_LOG_DIR` (thư mục log Nginx của app) và `C1_DOMAIN` trong `.env`, chạy `make net` một lần rồi `docker compose up -d c1-ingestion`. Format `waf_combined` (tuỳ chọn) cho Nginx của app nằm ở [`deploy/sensor/waf-logformat.conf`](../../deploy/sensor/waf-logformat.conf).

Unit test [`test_tailer.py`](test_tailer.py) có 6 test cho `tailer.py` (chọn file, dòng dở dang, bắt đầu từ cuối, truncation, file mới, file biến mất), không cần Kafka; `main.py` không có unit test.

```bash
make test-python   # chạy test của mọi service Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/c1-ingestion \
  -e PYTHONPATH=/app:/app/c1-ingestion waf/base:dev python -B test_tailer.py
```

## Giới hạn

- Offset chỉ nằm trong bộ nhớ. Khởi động lại thì bỏ qua phần ghi trong lúc dừng (mặc định đọc từ cuối), hoặc gửi lại toàn bộ file nếu `C1_READ_EXISTING=true`, sinh bản trùng với `event_id` mới.
- Khi rotation, các dòng ghi vào file cũ sau vòng poll cuối sẽ mất vì C1 không đọc `*.access.log.1`; phần dòng dở dang trong buffer cũng bị xoá.
- Publish thất bại thì dòng đó mất (offset đã tiến), không có retry hay hàng đợi cục bộ.
- `C1_DOMAIN` áp cho mọi file trong cả `nginx/` và `apache/`, nên một sensor dùng biến này chỉ phục vụ một domain. Mỗi lần đọc nạp toàn bộ phần mới của file vào bộ nhớ, nên file lớn kèm `C1_READ_EXISTING=true` tốn RAM.
