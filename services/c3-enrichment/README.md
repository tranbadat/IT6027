# c3-enrichment (C3, Người 1)

Consumer làm giàu log: đọc `log.normalized`, thêm vị trí địa lý (GeoIP), số request trong 60 giây của client và khoá phiên, rồi phát `log.enriched` cho khối phát hiện. Service không mở cổng HTTP; healthcheck trong `Dockerfile` kiểm tra file heartbeat `/tmp/alive` (ghi mỗi 10 giây, coi là hỏng nếu cũ hơn 30 giây).

## Vai trò trong pipeline

- **Đầu vào:** topic `log.normalized` do [C2](../c2-parser/README.md) phát, consumer group lấy từ `EVENT_GROUP`, mặc định `c3-enrichment`.
- **Đầu ra:** topic `log.enriched` theo [`contracts/events`](../../contracts/events/README.md), giữ `request_id` của event vào, `session_id` của envelope là khoá phiên đã tính. Key Kafka `domain|src_ip`. [Detector](../detector/README.md) đọc topic này để khớp rule và chấm điểm.

`data` là bản sao toàn bộ trường của `log.normalized` (xem bảng trong [C2](../c2-parser/README.md)), thêm hoặc ghi đè ba trường:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `geo` | object hoặc null | `{country_iso, country, city}`, mỗi giá trị là string hoặc null theo DB; null khi không tra cứu được |
| `req_count_1m` | int | Số request của cặp `(domain, src_ip)` trong 60 giây gần nhất, tính cả request hiện tại |
| `session_id` | string | Ghi đè: `session_id` từ C2 nếu khác rỗng, ngược lại `ip-<src_ip>` (hoặc `ip-unknown` khi không có IP) |

**Kiểm tra scope:** handler gọi `ScopeClient.allowed(domain)` ([`common/scope.py`](../common/scope.py)) với `domain` của envelope trước khi làm giàu. Kết quả cache theo `cache_ttl_seconds`; gọi Scope Service lỗi thì từ chối (fail closed, cache 5 giây). Event ngoài scope được ghi log info `skip out-of-scope event` rồi bỏ qua, offset vẫn được commit.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Kafka bootstrap servers, ví dụ `kafka:19092` |
| `SCOPE_SERVICE_URL` | bắt buộc | Địa chỉ Scope Service, ví dụ `http://p1-scope:8000` hoặc `http://mock-api` |
| `EVENT_GROUP` | `c3-enrichment` | Consumer group; đổi khi cần chạy song song hai bản khác nhau |
| `GEOIP_DB_PATH` | không đặt | File `.mmdb`; compose đặt `/data/geoip/dbip-city-lite.mmdb` và mount `./data/geoip` chỉ đọc |
| `LOG_LEVEL` | `info` | Mức log JSON ra stdout; `debug` thêm log `enriched` cho từng event |

Độ dài cửa sổ (`_WINDOW_SECONDS` = 60), số cặp tối đa (`_MAX_TRACKED_IPS` = 10000) và số mốc tối đa mỗi cặp (`_MAX_TIMESTAMPS_PER_IP` = 1000) là hằng số trong [`enrich.py`](enrich.py), không chỉnh qua biến môi trường.

## Xử lý đặc biệt

- **GeoIP:** `GeoResolver` mở Reader của `geoip2` một lần khi khởi động. Không đặt biến hoặc thiếu file thì service vẫn chạy, mọi `geo` là null và chỉ cảnh báo một lần `geoip database not available, geo=null`; file hỏng thì ghi thêm log error `cannot open geoip database`. IP rỗng, sai định dạng hoặc không định tuyến công cộng (private, loopback, link-local, reserved, multicast, unspecified) không được tra cứu và cho `geo` = null. IP không có trong DB cũng cho null; lỗi tra cứu khác ghi log warning `geoip lookup failed`.
- **Phiên:** khoá phiên là cookie `sessionid` mà C2 đọc từ trường `sid` của log. Khi log không có cookie (log combined chuẩn, client chưa có phiên), mọi request cùng `src_ip` gộp vào phiên `ip-<src_ip>`.
- **Đếm tần suất:** `RequestRateCounter` giữ một deque mốc thời gian cho mỗi `(domain, src_ip)` và loại mốc cũ hơn 60 giây trước khi đếm. Mốc là thời điểm C3 xử lý event (`time.time()`), không phải `request_ts` trong log.
- **Giới hạn bộ nhớ:** vượt 10000 cặp `(domain, src_ip)` thì loại cặp lâu không dùng nhất (LRU); mỗi cặp giữ tối đa 1000 mốc, nên `req_count_1m` bão hoà ở 1000.
- **Không sửa input:** `build_enriched_data` tạo dict mới thay vì sửa `data` của envelope vào.
- **Lỗi và giao nhận:** giống C2 ([`common/bus.py`](../common/bus.py)); handler ném lỗi thì value vào `waf.deadletter` rồi commit, broker không xác nhận thì process dừng để đọc lại message chưa commit.

## Chạy và kiểm thử

C3 thuộc profile `c3`, `collection`, `app`. Profile `collection` bật cả C1, C2, C3. Trong triển khai tách tầng, C3 nằm ở stack [`deploy/pipeline`](../../deploy/pipeline/docker-compose.yml).

```bash
make geoip   # tải DB-IP City Lite vào data/geoip/ (một lần)
# .env: COMPOSE_PROFILES=mock,docs,collection và SCOPE_SERVICE_URL=http://mock-api
make up
docker compose up -d --build c3-enrichment   # chỉ build lại C3
make logs s=c3-enrichment
make bus-peek t=log.enriched
```

Traffic từ `make demo` đi qua IP nội bộ của Docker nên `geo` thường là null; đó là hành vi đúng.

Unit test [`test_enrich.py`](test_enrich.py) có 12 test (5 cho bộ đếm, 4 cho GeoIP với Reader giả, 3 cho phiên và dựng `data`), không cần Kafka hay DB GeoIP thật; `main.py` không có unit test.

```bash
make test-python   # chạy test của mọi service Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/c3-enrichment \
  -e PYTHONPATH=/app:/app/c3-enrichment waf/base:dev python -B test_enrich.py
```

## Giới hạn

- Bộ đếm nằm trong bộ nhớ của từng instance: khởi động lại hoặc partition chuyển sang instance khác thì `req_count_1m` của client đếm lại từ 0.
- Đếm theo thời gian xử lý: khi đọc dồn (consumer lag, group mới đọc từ đầu topic, C1 đọc lại file cũ), nhiều request cũ rơi vào cùng một cửa sổ 60 giây và `req_count_1m` bị thổi phồng.
- Phiên không có thời hạn; phiên dự phòng `ip-<src_ip>` gộp mọi người dùng sau cùng một NAT hoặc proxy.
- DB GeoIP chỉ nạp lúc khởi động, cập nhật file thì phải khởi động lại. `event_id` đầu ra là UUID mới nên xử lý lại sinh bản trùng không loại được bằng `event_id`.
