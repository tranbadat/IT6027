# Event Bus contract

Event bus dùng **Kafka** (KRaft, một node khi chạy trên máy cá nhân). Danh sách topic khai báo tại [`infra/kafka/create-topics.sh`](../../infra/kafka/create-topics.sh) và được tạo tự động khi `docker compose up` (service `kafka-init`).

## Envelope

Mọi event dùng chung một vỏ, schema tại [`envelope.schema.json`](envelope.schema.json), ví dụ tại [`examples/`](examples/).

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `event` | ✔ | Tên event, đồng thời là tên topic |
| `event_id` | ✔ | UUID, consumer dùng để loại bản trùng |
| `request_id` / `session_id` | ✔ (một trong hai) | Không bắt buộc với `stage.completed` |
| `timestamp` | ✔ | RFC 3339 có múi giờ |
| `source` | ✔ | Tên service phát event, ví dụ `c1-ingestion`, `detector` |
| `domain` | ✔ | Domain đã được Scope Service xác nhận |
| `schema_version` | | Mặc định `1` |
| `data` | ✔ | Nội dung riêng của từng event |

So với kế hoạch (`event`, `request_id`/`session_id`, `timestamp`, `data`), envelope thêm ba trường: `event_id` để loại bản trùng, `domain` để kiểm tra scope ngay trên envelope, và `source` để truy vết.

## Topology

- **Topic = tên event** (`log.raw.ingested`, `log.normalized`, …). Topic sự kiện có 3 partition, giữ message 3 ngày.
- **Key message = `domain|src_ip`** (domain viết thường) nếu `data` có `src_ip`, ngược lại là `domain`. Mọi event của cùng một client vào cùng partition: giữ thứ tự và để detector giữ trạng thái cửa sổ theo client trên một instance. Detector kiểm tra key này và chuyển message sai key sang dead-letter.
- **Mỗi service đọc bằng một consumer group riêng** (tên group = tên service), tự khai báo danh sách topic cần đọc trong mã nguồn. Group mới đọc từ đầu topic (`auto.offset.reset=earliest`).
- **Dead-letter:** topic `waf.deadletter` (1 partition, giữ 7 ngày).
- **Snapshot rule:** topic `rules.changed` (compacted, key `active`) do `rule-api` phát, `detector` đọc để nạp lại rule mà không cần khởi động lại.
- **Nhánh DDoS:** topic `log.enriched.by-domain` do `repartitioner` ghi lại `log.enriched` với key = domain.
- Broker tắt `auto.create.topics.enable`: topic mới phải được thêm vào `create-topics.sh`.

| Event (topic) | Service phát | Consumer group đọc | Trường `data` chính | Người định nghĩa `data` |
|---|---|---|---|---|
| `log.raw.ingested` | c1-ingestion | c2-parser, p3-orchestrator, metrics-exporter | dòng log gốc, nguồn log, thời điểm thu thập | Người 1 |
| `log.normalized` | c2-parser | c3-enrichment, p3-orchestrator, metrics-exporter | `src_ip`, `method`, `path`, `query_string`, `status`, `user_agent`, `referer`, `host`, `request_ts`, … | Người 1 |
| `log.enriched` | c3-enrichment | detector, repartitioner, p3-orchestrator, p5-dashboard, metrics-exporter | normalized + `geo`, phiên, `req_count_1m` | Người 1 |
| `attack.detected` | detector | p3-orchestrator, p5-dashboard, metrics-exporter | `attack_type`, `severity`, `rule_id`, `rule_name`, `src_ip`, `method`, `url`, `host`, `field`, `matched_value`, `evidence` | Người 2 |
| `anomaly.scored` | detector | p3-orchestrator, p4-alerting, p5-dashboard, metrics-exporter | `score`, `threshold`, `severity`, `src_ip`, `url`, `host`, `attack_types`, `signals`, `reasons` | Người 2 |
| `alert.triggered` | detector, ddos-detector | p3-orchestrator, p4-alerting, p5-dashboard, metrics-exporter | `score`, `threshold`, `severity`, `src_ip`, `url`, `host`, `attack_types`, `rule_ids`, `signals`, `reason` | Người 2 |
| `stage.completed` | p3-orchestrator | p5-dashboard, metrics-exporter | `pipeline`, `stage`, `processed`, `window_seconds` | Người 3 |

`signals` gồm `rule_hits`, `req_count`, `error_count`, `distinct_paths`, `window_seconds` (nhánh DDoS thêm `unique_ips`). `severity` theo thang điểm: `critical` ≥ 90, `high` ≥ 70, `medium` ≥ 40, còn lại `low`. Cảnh báo chỉ do hành vi (không rule nào khớp) có `attack_types` là `traffic_flood`, `error_scan`, `path_scan` hoặc `ddos`; cảnh báo `ddos` có `src_ip = "distributed"` vì nguồn phân tán.

## Quy ước khi publish / consume

**Publish**

- Value là envelope JSON (UTF-8), key theo quy ước ở trên.
- Producer bật idempotence, `acks=all`, và chờ broker xác nhận trước khi coi là đã gửi.
- Header `event`, `event_id`, `source` để truy vết mà không cần parse value.

**Consume**

- Tắt auto-commit. Chỉ commit offset sau khi handler xong **và** mọi event handler phát ra đã được broker xác nhận (at-least-once).
- Message sai schema hoặc handler ném lỗi: gửi nguyên value sang `waf.deadletter` với header `error`, `source_topic`, `consumer_group` rồi commit. Không xử lý lại vô hạn.
- Lỗi giao nhận (broker không xác nhận): để process dừng, khởi động lại sẽ đọc lại message chưa commit.
- Event thuộc domain ngoài scope: commit rồi bỏ qua, ghi log ở mức info. Đây là hành vi đúng, không phải lỗi.
- Xử lý idempotent theo `event_id` (ví dụ P5 và P4 lưu DB với `ON CONFLICT (event_id) DO NOTHING`).

Bus Python dùng chung: [`services/common/bus.py`](../../services/common/bus.py). Detector Java: [`services/detector`](../../services/detector).

## Thêm event hoặc consumer mới

1. Thêm tên event vào `enum` trong `envelope.schema.json`, vào `EVENTS` trong `services/common/envelope.py` và vào bảng trên.
2. Thêm topic vào `infra/kafka/create-topics.sh` rồi chạy `make bus-topology`.
3. Service mới đọc event bằng `bus.consume(<tên-group>, handler, topics=[...])`.
