# Log Collection (Người 1): C1 · C2 · C3

Thu thập access log của Nginx/Apache **theo thời gian thực**, chuẩn hoá và làm giàu, rồi publish lên event bus
cho Detection Engine (Người 2) tiêu thụ.

```
Nginx/Apache log file ─┐
                       ├─► C1 Ingestion ─► log.raw.ingested ─► C2 Parsing ─┬► log.normalized ─► C3 Enrichment ─► log.enriched
syslog (UDP/TCP) ──────┘                                                   └► log.parse_failed (dead-letter)
```

| Module | Việc làm | Consume → Publish |
| --- | --- | --- |
| **C1** Log Ingestion | Tail file liên tục (xử lý rotation/truncate, resume sau restart) hoặc nhận syslog; kiểm tra Scope trước khi publish | — → `log.raw.ingested` |
| **C2** Parsing | Parse combined/common/vhost_combined; giải mã URL (kể cả double-encoding); chịu được dòng hỏng | `log.raw.ingested` → `log.normalized` |
| **C3** Enrichment | GeoIP, gom phiên theo IP + cookie, tần suất và tỷ lệ lỗi theo IP (cửa sổ trượt 10s/60s), bộ nhớ có giới hạn | `log.normalized` → `log.enriched` |

Schema các event: [`docs/event-schema.md`](docs/event-schema.md) · [`schemas/log-events.schema.json`](schemas/log-events.schema.json).
Cấu hình web server: [`docs/web-server-setup.md`](docs/web-server-setup.md).

> Ba module này không có HTTP API (chỉ đọc/ghi event và gọi Scope Service), nên tài liệu giao tiếp là event schema ở trên chứ không phải OpenAPI.

## Chạy thử

### Cách 1 — Docker Compose (Nginx + Redis + C1–C3 + mock Scope)

```bash
docker compose up --build
curl -H 'Host: shop.example.com' "http://localhost:8080/products?id=1'%20OR%20'1'='1"
docker compose exec redis redis-cli XRANGE waf:events:log.enriched - +
```

### Cách 2 — Chạy trực tiếp

```bash
pip install -r requirements.txt
export BUS_BACKEND=redis REDIS_URL=redis://localhost:6379/0 SCOPE_URL=http://localhost:8081
python -m wafcollect.tools.mock_scope --allow shop.example.com --port 8081 &   # thay bằng P1 thật khi có
C1_CONFIG=config/c1.example.yaml python -m wafcollect.c1_ingestion &
python -m wafcollect.c2_parsing &
python -m wafcollect.c3_enrichment &
```

`BUS_BACKEND=memory` chỉ dùng trong test (bus trong một process).
`SCOPE_DISABLED=true` bỏ qua kiểm tra scope, **chỉ dùng khi phát triển cục bộ**; mặc định service từ chối khởi động nếu thiếu `SCOPE_URL`.

## Cấu hình (biến môi trường)

| Biến | Service | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| `BUS_BACKEND` | C1–C3 | `redis` | `redis` \| `memory` |
| `REDIS_URL` | C1–C3 | `redis://localhost:6379/0` | |
| `SCOPE_URL`, `SCOPE_TOKEN` | C1–C3 | — | Scope Service (P1) và JWT tuỳ chọn |
| `SCOPE_DISABLED` | C1–C3 | `false` | Chỉ để dev |
| `C1_CONFIG` | C1 | `/etc/wafcollect/c1.yaml` | Danh sách nguồn log, xem `config/c1.example.yaml` |
| `C1_STATE_PATH` | C1 | `/var/lib/wafcollect/c1-state.json` | Lưu `(inode, offset)` để resume |
| `CONSUMER_NAME` | C2, C3 | `default` | Tên consumer trong Redis consumer group; giữ ổn định giữa các lần restart |
| `GEOIP_DB_PATH` | C3 | — | File `.mmdb` GeoLite2 City hoặc Country; bỏ trống thì `geo.status=disabled` |
| `SESSION_TIMEOUT_S` | C3 | `1800` | Phiên kết thúc sau chừng này giây im lặng |
| `MAX_SESSIONS`, `MAX_TRACKED_IPS` | C3 | `50000`, `100000` | Trần bộ nhớ (LRU) |
| `REDACT_COOKIE` | C3 | `true` | Che giá trị cookie trong `log.enriched` |
| `LOG_LEVEL` | C1–C3 | `INFO` | |

### GeoIP database

Database **không** nằm trong repo (giấy phép MaxMind). Đăng ký miễn phí tại MaxMind, tải **GeoLite2-City** (hoặc Country) dạng `.mmdb`, đặt vào `data/geoip/` và trỏ `GEOIP_DB_PATH`. Chưa có database thì hệ thống vẫn chạy bình thường, chỉ thiếu thông tin quốc gia/thành phố.

## Kiểm thử

```bash
pip install -r requirements-dev.txt
pytest                       # 96 test, ~25s
./dev/e2e_local.sh           # cần nginx + redis-server cài sẵn trên máy
./dev/e2e_vulnshop.sh        # + pip install flask (chỉ cho script demo này)
```

`dev/e2e_local.sh` chạy đúng kịch bản "Định nghĩa hoàn thành" cho phần thu thập log: **Nginx thật** ghi log liên tục, ba service chạy như process riêng qua **Redis thật**, gửi request thật (có SQL Injection và path traversal double-encode), log rotation giữa chừng (`mv` + `nginx -s reopen`), một nguồn syslog UDP do Nginx gửi, và một domain ngoài phạm vi, rồi kiểm tra event ở đầu ra.

`dev/e2e_vulnshop.sh` dùng khi **chưa có website thật** để lấy log: dựng một target CỐ Ý dễ tổn thương (`dev/vulnapp/app.py` — SQL Injection, XSS phản hồi, Path Traversal thật, chỉ chạy trên localhost), đặt Nginx thật trước nó, tự gửi traffic tấn công thật rồi chạy hết pipeline C1→C2→C3, kiểm tra event `log.enriched` sinh ra đúng (payload SQLi/XSS/traversal còn nguyên để Detection Engine phân tích, cookie phiên không bị lộ, session gom đúng theo attacker/người dùng thường). Tự chứa, không cần quyền root, không đụng cổng hệ thống hay `/etc/nginx`. **Không** dùng để tấn công website của người khác — chỉ target do script tự dựng trên máy bạn.

Phạm vi đã kiểm chứng và chưa kiểm chứng:

| Đã chạy thực tế | Chưa chạy thực tế |
| --- | --- |
| Nginx thật (file + syslog UDP + rotation) → Redis thật → C1–C3 | `docker compose`/`Dockerfile` (môi trường phát triển không có Docker; mới kiểm tra cú pháp YAML) |
| GeoIP với database test chính thức của MaxMind (City & Country, IPv4/IPv6) | GeoIP với GeoLite2 bản đầy đủ |
| Redis Streams: consumer group, ack, giao lại message chưa ack sau restart | Apache thật (mới test với dòng log mẫu theo đúng `LogFormat` trong tài liệu) |
| Restart C1 không mất/trùng dòng; Scope Service hoặc bus sập tạm thời không mất dòng | Tải lớn kéo dài |

Hiệu năng tham khảo (một core, sandbox, không phải benchmark chính thức): parser C2 ≈ 29 nghìn dòng/giây. Với 1 triệu request từ ~500 nghìn IP khác nhau, C3 giữ số phiên và số IP đúng ở trần cấu hình (50 nghìn / 100 nghìn), RAM đỉnh ≈ 150 MB. Thông lượng end-to-end phụ thuộc chủ yếu vào Redis, chưa đo.

## Quyết định thiết kế đáng chú ý

- **Không mất dữ liệu, không xử lý ngoài phạm vi.** Scope trả `denied` → bỏ dòng và đếm; Scope hoặc bus **không khả dụng** → tạm dừng và thử lại chứ không bỏ (fail-closed nhưng không mất log). C2 và C3 tự kiểm tra scope lại, không tin C1.
- **At-least-once.** C1 chỉ lưu offset *sau khi* publish thành công. Crash có thể phát lại vài dòng, không bao giờ bỏ sót; downstream dedupe theo `request_id` nếu cần.
- **Backpressure.** Hàng đợi có giới hạn: bus chậm thì C1 ngừng đọc file (dữ liệu vẫn nằm trên đĩa). UDP syslog không có backpressure nên datagram thừa bị bỏ và đếm (`dropped_udp_queue_full`).
- **Dòng log lỗi vẫn được giữ.** Request line hỏng (TLS handshake gửi nhầm vào cổng HTTP, `"-"` của 408, method rác…) vẫn phát dưới dạng `parse_status: partial`, vì đó thường chính là traffic của scanner. Chỉ dòng hoàn toàn không nhận ra mới vào `log.parse_failed`.
- **Giải mã URL lặp** tới khi ổn định (tối đa 5 lượt) và gắn cờ `double_encoded`, để `%252e%252e%252f` trở thành `../` trước khi tới rule engine. C2 chỉ giải mã và gắn tín hiệu, không kết luận tấn công (việc của Người 2).
- **Che cookie.** Token phiên chỉ dùng để gom phiên ở C3, sau đó bị che khỏi `log.enriched` (kể cả trong `raw_line`). Giá trị cookie vẫn còn trong `log.normalized`, xem `docs/event-schema.md`.
- **Thời gian theo sự kiện, không theo giờ hệ thống.** C3 dùng `request_time` trong log để tính cửa sổ và phiên, nên chạy lại log cũ vẫn cho kết quả đúng; `session_id` cũng xác định (deterministic).

## Giới hạn đã biết

- **Không có request body** (access log không ghi). Xem mục "Ràng buộc và giả định" trong `docs/event-schema.md`.
- **Không xử lý `X-Forwarded-For`.** Nếu web server đứng sau proxy/CDN, `client_ip` là IP của proxy; cần cấu hình `real_ip` (Nginx) / `mod_remoteip` (Apache) phía web server.
- **Chưa đọc error log.** Kế hoạch ghi là "có thể mở rộng"; hiện chỉ access log.
- **Trạng thái phiên/tần suất của C3 nằm trong RAM**, mất khi restart và không chia sẻ giữa nhiều instance. Muốn scale ngang C3 phải phân vùng theo IP.
- **Syslog TCP** chỉ hỗ trợ framing theo dòng (chưa có octet-counting RFC 6587).
- Xem thêm giới hạn của `copytruncate` và rotation lúc C1 đang tắt trong `docs/web-server-setup.md`.

## Cấu trúc thư mục

```
wafcollect/
  common/          events, bus (Redis Streams + in-memory), scope client, config, stats
  c1_ingestion/    tailer (rotation/resume), syslog_receiver, service
  c2_parsing/      parser (hàm thuần, không phụ thuộc bus), service
  c3_enrichment/   geoip, sessions (gom phiên + tần suất), service
  tools/           mock_scope
tests/             96 test: unit + tích hợp C1→C2→C3 (kiểm schema từng event)
schemas/ docs/ config/ dev/
```
