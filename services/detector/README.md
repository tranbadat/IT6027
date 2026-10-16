# Detector (Người 2, Java 17)

Khối phát hiện của hệ thống: khớp rule tự viết (thay D1), chấm điểm bất thường (thay D3) và quản lý rule (thay D2). Mã nguồn gốc do Người 2 phát triển trên nhánh `lienntp`, được tích hợp vào event bus Kafka chung của nhóm và contract [`contracts/events`](../../contracts/events/README.md).

Một image `waf/detector:dev`, chạy theo role (tham số đầu tiên hoặc biến `DETECTOR_ROLE`):

| Role | Service compose | Đọc | Ghi |
|---|---|---|---|
| `detection` | `detector` | `log.enriched` | `attack.detected` (mỗi rule khớp một message), `anomaly.scored` (khi điểm > 0), `alert.triggered` |
| `rule-management` | `rule-api` | HTTP `/rules` | `rules.json` (volume) và snapshot lên `rules.changed` |
| `repartition` | `repartitioner` (profile `ddos`) | `log.enriched` | `log.enriched.by-domain` (key = domain) |
| `ddos` | `ddos-detector` (profile `ddos`) | `log.enriched.by-domain` | `alert.triggered` (`attack_types=["ddos"]`) |

## Luồng xử lý một request (role `detection`)

1. `KafkaPipeline` poll theo batch và giao từng record cho `RecordProcessor`.
2. `EventMapper` đọc envelope `log.enriched` (hoặc JSON phẳng của simulator), kiểm tra contract, timestamp không vượt quá độ lệch cho phép.
3. Kiểm tra key Kafka = `domain|src_ip` và gọi P1 `GET /scope/check` (`HttpScopeService`, cache theo TTL, lỗi thì từ chối).
4. `RuleEngineServiceImpl` giải mã URL/HTML tối đa hai lớp, bỏ comment SQL nếu rule yêu cầu, rồi khớp RE2 trên `url`, `query`, header (`user_agent`, `referer`).
5. `RequestDetectionServiceImpl` cập nhật cửa sổ trượt theo client và tính điểm.
6. `EnvelopeFactory` dựng envelope đầu ra; offset chỉ được commit sau khi broker xác nhận mọi output (at-least-once). Record lỗi vào `waf.deadletter`.

## Công thức điểm

Điểm của một request, tính cho cặp `domain|src_ip` trong cửa sổ `REQUEST_WINDOW_SECONDS` (60 giây):

| Thành phần | Điểm |
|---|---|
| Rule khớp | trọng số lớn nhất trong các rule khớp (critical 95, high 85, medium 65) |
| Nhiều rule cùng khớp | +10 cho mỗi rule khớp thêm |
| Số request ≥ `REQUEST_THRESHOLD` (20) | +30 (`request_burst`, cảnh báo dạng `traffic_flood`) |
| Số request ≥ `FLOOD_THRESHOLD` (100) | thêm +40 (`request_flood`, dạng `traffic_flood`) |
| Số phản hồi lỗi ≥ `ERROR_THRESHOLD` (8) | +25 (`http_error_burst`, dạng `error_scan`) |
| Số path khác nhau ≥ `DISTINCT_PATH_THRESHOLD` (12) | +25 (`path_scan`) |

Tổng chặn ở 100. Cảnh báo khi điểm ≥ `ALERT_SCORE_THRESHOLD` (70); cùng một client cách nhau tối thiểu `RULE_ALERT_COOLDOWN_SECONDS` (15 giây). Tệp tĩnh (`.css`, `.js`, ảnh, `favicon.ico`...) vẫn tính vào số request nhưng không tính vào số lỗi và số path khác nhau, để một lần tải trang có ảnh lỗi 404 không bị coi là dò quét.

Nhờ tín hiệu hành vi, nguồn không mang payload vẫn có thể đạt ngưỡng: dò quét 30 + 25 + 25 = 80, flood từ một nguồn 30 + 40 = 70. Tín hiệu `request_flood` được bổ sung sau lượt thực nghiệm đầu ([experiments/](../../experiments/README.md)): trước đó một nguồn gửi 150-200 request/phút không lỗi, cùng một path, chỉ đạt 30 điểm và không bao giờ bị cảnh báo. Flood phân tán từ nhiều IP do vai trò `ddos` xử lý riêng.

## Rule

20 rule mặc định thuộc 10 nhóm ([`default-rules.yaml`](src/main/resources/rules/default-rules.yaml), chỉ dùng để tạo `rules.json` lần đầu; sau đó rule do quản trị viên sửa nằm trong volume và không bị ghi đè khi build image mới — muốn áp bộ mặc định mới thì sửa qua API/dashboard hoặc xoá volume `rules-data`): `sql_injection`, `xss`, `path_traversal`, `command_injection`, `lfi`, `rfi`, `sensitive_file`, `log4shell`, `ssti`, `scanner`. Rule dùng cú pháp RE2 (thời gian khớp tuyến tính, không lookaround và backreference), nên không có rủi ro quay lui mất kiểm soát.

Quản trị rule qua dashboard P5 (mục **Rule pattern**) hoặc API qua gateway (cần JWT):

```bash
TOKEN=$(curl -s -X POST localhost:8080/api/auth/token -H 'content-type: application/json' \
  -d '{"client_id":"admin","client_secret":"admin"}' | jq -r .access_token)
curl -s localhost:8080/api/rules/ -H "Authorization: Bearer $TOKEN" | jq '.rules | length'
curl -s -X POST localhost:8080/api/rules/validate -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"id":"XSS_IFRAME","group":"xss","description":"iframe injection","enabled":true,"targets":["query"],"pattern":"(?i)<\\s*iframe","score":75,"normalizers":["url_decode","html_decode"]}'
```

`POST /api/rules/` thêm, `PUT /api/rules/{id}` sửa, `DELETE /api/rules/{id}` xoá (404 nếu không tồn tại; không xoá được rule cuối cùng). `POST /api/rules/test` chạy thử một rule chưa lưu trên request mẫu `{"rule": {...}, "sample": "/path?query"}` (tối đa 8192 ký tự; chỉ trường `url` và `query`, theo `targets` và `normalizers` của rule) và trả `{matched, field, value, start, end, elapsed_ms}`; dashboard dùng cho nút "Chạy kiểm thử" trong ngăn kéo rule. `rule-api` không publish cổng ra host; chỉ đi qua gateway. Khi khởi động, detector đọc hết snapshot trên `rules.changed` (tối đa 30 giây) rồi mới xử lý log.

Lưu ý bảo mật: detector tin mọi snapshot trên `rules.changed`. Kafka dùng PLAINTEXT nên chỉ để broker trong mạng tin cậy; khi mở ra ngoài cần SASL + ACL để chỉ `rule-api` được ghi topic này.

## Thay đổi khi tích hợp so với nhánh `lienntp`

- Đọc envelope `log.enriched` và phát `attack.detected`, `anomaly.scored`, `alert.triggered` đúng contract chung (thêm `rule_name`, `url`, `matched_value`, `evidence`, `signals`).
- Thêm kiểm tra scope qua P1 (fail closed).
- Sửa rule `XSS_HANDLER` báo nhầm trên tham số hợp lệ (`content=`, `config=`, cookie `consent=`).
- Sửa bypass SQLi bằng tab, xuống dòng (`[ ]+` → `\s+`) và path traversal dạng `..\`.
- Role `ddos` chuyển message lỗi sang dead-letter thay vì `alert.triggered`.
- `anomaly.scored` chỉ phát khi có rule khớp hoặc điểm ≥ 40; rule-api ghi rule và phát snapshot trong cùng một khoá để detector luôn nhận snapshot mới nhất.
- Trường quá dài bị cắt bớt thay vì đẩy cả request vào dead-letter (tránh né phát hiện bằng cách đệm User-Agent); dòng request C2 không parse được vẫn được phân tích.
- Lỗi 404 của tệp tĩnh (favicon, CSS, JS, ảnh) không tính vào tín hiệu lỗi và số path khác nhau.
- `event_id` đầu ra xác định theo input (chạy lại không sinh bản trùng); cảnh báo DDoS có `src_ip = distributed` để P4 gom theo domain.
- Thay 7 rule ban đầu bằng 20 rule (giữ độ phủ của bộ rule trước đó), thêm target `user_agent`, `referer`.

## Kiểm thử

```bash
make test-java   # 33 test: rule, hồi quy lỗi, scope, xử lý record Kafka
```

Docker build cũng chạy toàn bộ test; test fail thì image không được tạo.
