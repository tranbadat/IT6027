# Event Bus contract

Event bus dùng **RabbitMQ**. Topology khai báo tại [`infra/rabbitmq/definitions.json`](../../infra/rabbitmq/definitions.json) và được nạp tự động khi `docker compose up`.

## Envelope

Mọi event dùng chung một vỏ, schema tại [`envelope.schema.json`](envelope.schema.json), ví dụ tại [`examples/`](examples/).

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `event` | ✔ | Tên event, đồng thời là routing key |
| `event_id` | ✔ | UUID, consumer dùng để loại bản trùng |
| `request_id` / `session_id` | ✔ (một trong hai) | Không bắt buộc với `stage.completed` |
| `timestamp` | ✔ | RFC 3339 có múi giờ |
| `source` | ✔ | Tên service phát event, ví dụ `c1-ingestion` |
| `domain` | ✔ | Domain đã được Scope Service xác nhận |
| `schema_version` | | Mặc định `1` |
| `data` | ✔ | Nội dung riêng của từng event |

So với kế hoạch (`event`, `request_id`/`session_id`, `timestamp`, `data`), envelope thêm ba trường: `event_id` để loại bản trùng, `domain` để kiểm tra scope ngay trên envelope, và `source` để truy vết.

## Topology

- Exchange chính: `waf.events` (topic, durable). Publish với **routing key = tên event**.
- Dead-letter: `waf.events.dlx` (fanout) → queue `q.deadletter` (tối đa 10.000 message). Consumer **tự publish** message lỗi vào đây, xem quy ước bên dưới.
- Mỗi service consumer có **một queue riêng** tên `q.<tên-service>` và tự phân loại theo routing key.
- Queue giữ tối đa 50.000 message. Khi đầy, message cũ nhất bị bỏ (`drop-head`) để queue của service chưa chạy không phình mãi.
- Queue consumer **không** gắn `x-dead-letter-exchange`. Nếu gắn, message bị bỏ do queue đầy cũng bị chuyển sang `q.deadletter` và lấn hết message lỗi thật.

| Event (routing key) | Service phát | Queue nhận | Trường `data` theo kế hoạch | Người định nghĩa `data` |
|---|---|---|---|---|
| `log.raw.ingested` | c1-ingestion | `q.c2-parser`, `q.p3-orchestrator` | dòng log gốc, nguồn log, thời điểm thu thập | Người 1 |
| `log.normalized` | c2-parser | `q.c3-enrichment`, `q.p3-orchestrator` | method, URL, query string, header, user agent, IP nguồn, status, kích thước phản hồi, thời gian xử lý, thời điểm request | Người 1 |
| `log.enriched` | c3-enrichment | `q.d1-rule-engine`, `q.d3-anomaly-scorer`, `q.p3-orchestrator`, `q.p5-dashboard` | normalized + geoIP, session, tần suất request theo IP | Người 1 |
| `attack.detected` | d1-rule-engine | `q.d3-anomaly-scorer`, `q.p3-orchestrator`, `q.p5-dashboard` | URL, rule_id, loại tấn công, mức độ, bằng chứng khớp mẫu | Người 2 |
| `anomaly.scored` | d3-anomaly-scorer | `q.p3-orchestrator`, `q.p4-alerting`, `q.p5-dashboard` | điểm tổng hợp, các tín hiệu thành phần, mức độ đề xuất | Người 2 |
| `alert.triggered` | d3-anomaly-scorer | `q.p3-orchestrator`, `q.p4-alerting`, `q.p5-dashboard` | điểm, ngưỡng, loại tấn công, URL, mức độ | Người 2 |
| `stage.completed` | p3-orchestrator | `q.p5-dashboard` | pipeline, stage, số lượng đã xử lý, khoảng thời gian | Người 3 |

## Quy ước khi publish / consume

**Publish**

- `content_type=application/json`, `delivery_mode=2` (persistent).
- `message_id` = `event_id`, `type` = `event`, `app_id` = `source`.
- Bật publisher confirms để biết message đã vào broker.

**Consume**

- Ack thủ công, `prefetch` khoảng 10–100.
- Message sai schema hoặc không xử lý được: publish nguyên body sang exchange `waf.events.dlx`, giữ routing key gốc và thêm header `x-error` (lý do) cùng `x-source-queue`, rồi `ack`. Không requeue vô hạn.
- Event thuộc domain ngoài scope: `ack` rồi bỏ qua, ghi log ở mức info. Đây là hành vi đúng, không phải lỗi.
- Xử lý idempotent theo `event_id`.

## Thêm event hoặc queue mới

1. Thêm tên event vào `enum` trong `envelope.schema.json` và vào bảng trên.
2. Thêm queue/binding vào `infra/rabbitmq/definitions.json`.
3. Chạy `make bus-topology`. Muốn đổi `arguments` của queue đã tồn tại thì phải xoá queue đó trước.
