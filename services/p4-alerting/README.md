# p4-alerting (P4, Người 3)

Service cảnh báo: đọc cảnh báo do detector phát ra, gửi email và webhook có chống trùng theo cửa sổ thời gian, lưu lịch sử vào Postgres và cung cấp API tra cứu. P4 chỉ cảnh báo, không tự chặn IP hay thay đổi cấu hình.

## Vai trò

Đọc `TOPICS = ["anomaly.scored", "alert.triggered"]` ([`consumer.py`](consumer.py)) bằng consumer group `EVENT_GROUP` (mặc định `p4-alerting`). Consumer chạy ở thread nền qua `start_consumer_thread` của [`common/bus.py`](../common/bus.py), HTTP chạy ở thread chính. Khi consumer dừng vì lỗi (broker không xác nhận, commit lỗi, lỗi nghiêm trọng), process thoát với mã 1 để Docker khởi động lại.

`alert.triggered` do `detector` và `ddos-detector` phát; `anomaly.scored` do `detector` phát (khi có rule khớp hoặc điểm từ 40).

Ngưỡng cảnh báo riêng theo domain `ALERT_THRESHOLDS` có dạng `domain:điểm,domain2:điểm`; khoá `*` hoặc `default` là ngưỡng cho domain không liệt kê ([`thresholds.py`](thresholds.py)). Quy tắc gửi ([`consumer.py`](consumer.py)):

| Event | Domain không có ngưỡng riêng | Domain có ngưỡng riêng `T` |
|---|---|---|
| `alert.triggered` (detector đã vượt ngưỡng chung, mặc định 70) | Gửi | Gửi nếu `score >= T`; ngược lại bỏ qua và tăng `alerts_below_threshold` |
| `anomaly.scored` | Chỉ đếm `anomalies` | Gửi nếu `score >= T` (tăng `anomalies_flagged`, lý do ghi ngưỡng domain) |

Như vậy domain nhạy cảm đặt `T` thấp hơn 70 để cảnh báo sớm hơn, domain ồn đặt `T` cao hơn để giảm cảnh báo. Khi cùng một request sinh cả `anomaly.scored` và `alert.triggered`, bước chống trùng bên dưới chỉ để một email đi qua. Với cảnh báo chỉ do hành vi (không rule khớp), detector chỉ phát `anomaly.scored` khi điểm từ 40, nên `T` dưới 40 không có thêm tác dụng.

Mỗi cảnh báo được gửi đi qua các bước sau:

1. Dựng bản tóm tắt cảnh báo ([`alerts.py`](alerts.py)) gồm `event_id`, `domain`, `src_ip`, `host`, `url`, `attack_types`, `main_attack_type`, `rule_ids`, `severity`, `score`, `threshold`, `reason`, `triggered_at` (lấy `timestamp` của envelope), `request_id`, `session_id`. `main_attack_type` là phần tử đầu của `attack_types`; thiếu `src_ip`, `severity` hoặc `attack_types` thì nhận `unknown`.
2. Chống trùng ([`cooldown.py`](cooldown.py)) theo khoá `(domain, src_ip, main_attack_type)`: cùng khoá chỉ được xử lý lại sau `ALERT_COOLDOWN_SECONDS` (mặc định 60, giá trị `<= 0` tắt chống trùng). Cảnh báo bị chặn chỉ tăng bộ đếm `alerts_suppressed`, không lưu và không gửi. Cảnh báo DDoS có `src_ip = "distributed"` nên được gom theo domain.
3. Lưu vào lịch sử trong bộ nhớ (tối đa 500 bản ghi, [`store.py`](store.py)) và vào Postgres nếu có.
4. Gửi email qua SMTP (không TLS, không xác thực, timeout 10 giây), tiêu đề `[<SEVERITY>] <attack_types> on <domain>`, nội dung gồm domain, loại tấn công, URL, IP nguồn, mức độ, điểm/ngưỡng, rule, lý do và thời điểm ([`notify.py`](notify.py)).
5. Nếu `ALERT_WEBHOOK_URL` khác rỗng: `POST` bản tóm tắt ở bước 1 dưới dạng JSON (timeout 5 giây).

Hai kênh lỗi độc lập với nhau: lỗi chỉ được ghi log và tăng `emails_failed` hoặc `webhooks_failed`, không chuyển sang dead-letter và không làm dừng consumer.

### Lưu trữ

Bảng `alerting.alerts` thuộc schema `alerting` (tạo sẵn bởi [`01-schemas.sql`](../../infra/postgres/init/01-schemas.sql)). Khi khởi động, service tự tạo bảng và index theo `triggered_at DESC`, `domain` ([`repo.py`](repo.py)). Khoá chính là `event_id`, ghi bằng `ON CONFLICT (event_id) DO NOTHING` nên đọc lại cùng một event không sinh bản trùng.

- Không có `DATABASE_URL`, hoặc không mở được pool sau 30 lần thử: service ghi log lỗi và chỉ dùng lịch sử trong bộ nhớ.
- Lỗi ghi một bản ghi: tăng `db_errors`, consumer vẫn chạy tiếp.
- Grafana đọc bảng này bằng role chỉ đọc `grafana_ro` ([`02-grafana-readonly.sh`](../../infra/postgres/init/02-grafana-readonly.sh), xem [`docs/observability.md`](../../docs/observability.md)).

## API HTTP

OpenAPI: [`contracts/openapi/alerting.yaml`](../../contracts/openapi/alerting.yaml).

| Method | Path (service) | Qua gateway | Xác thực |
|---|---|---|---|
| `GET` | `/healthz` | không (gateway có `/healthz` riêng) | không |
| `GET` | `/alerts` | `/api/alerts/` | JWT, gateway kiểm tra qua P2 |

`GET /alerts` nhận `limit` (1-500, mặc định 100) và `domain` (khớp chính xác). Phản hồi:

- `alerts`: cảnh báo mới nhất trước.
- `count`: tổng số bản ghi trong `alerting.alerts` (không áp bộ lọc `domain`, không bị đặt lại khi restart). Khi chạy bằng bộ nhớ là bộ đếm `alerts` từ lúc khởi động.
- `counters`: bộ đếm trong bộ nhớ từ lúc khởi động, ví dụ `alerts`, `alerts_suppressed`, `emails_sent`, `emails_failed`, `webhooks_sent`, `webhooks_failed`, `db_errors` (danh sách đầy đủ trong `_COUNTER_NAMES` của [`store.py`](store.py)).
- `persisted`: `true` nếu đọc từ Postgres; `false` nếu không có DB hoặc truy vấn lỗi (khi đó tự quay về lịch sử trong bộ nhớ).

```bash
# TOKEN lấy như trong services/detector/README.md
curl -s 'localhost:8080/api/alerts/?limit=5&domain=shop.local' -H "Authorization: Bearer $TOKEN" | jq '.count, .counters'
```

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Bootstrap servers Kafka |
| `EVENT_GROUP` | `p4-alerting` | Consumer group |
| `DATABASE_URL` | rỗng | DSN Postgres; rỗng thì chỉ lưu lịch sử trong bộ nhớ |
| `SMTP_HOST` | `mailpit` | Máy chủ SMTP |
| `SMTP_PORT` | `1025` | Cổng SMTP |
| `ALERT_EMAIL_FROM` | bắt buộc | Địa chỉ gửi |
| `ALERT_EMAIL_TO` | bắt buộc | Địa chỉ nhận (một địa chỉ) |
| `ALERT_WEBHOOK_URL` | rỗng | URL nhận webhook; rỗng thì tắt webhook |
| `ALERT_COOLDOWN_SECONDS` | `60` | Cửa sổ chống trùng (giây); `<= 0` để tắt |
| `ALERT_THRESHOLDS` | rỗng | Ngưỡng cảnh báo riêng theo domain (`domain:điểm,...`, khoá `*` mặc định), xem bảng ở mục Vai trò |
| `HTTP_PORT` | `8000` | Cổng HTTP |
| `LOG_LEVEL` | `info` | Mức log ([`common/logging.py`](../common/logging.py)) |

Thiếu biến bắt buộc thì service dừng ngay khi khởi động. [`docker-compose.yml`](../../docker-compose.yml) và [`deploy/platform/docker-compose.yml`](../../deploy/platform/docker-compose.yml) đặt `SMTP_HOST=mailpit`, `SMTP_PORT=1025`, `ALERT_EMAIL_FROM=waf-alerts@waf.local`, `ALERT_EMAIL_TO=soc@waf.local`, `ALERT_WEBHOOK_URL` mặc định `http://webhook-sink:8080/alerts`; `ALERT_COOLDOWN_SECONDS` chưa được khai báo nên dùng mặc định.

## Chạy và kiểm thử

Thêm profile `p4` (đã nằm trong `platform` và `app`) vào `COMPOSE_PROFILES` trong `.env`, ví dụ `COMPOSE_PROFILES=mock,p4`, rồi chạy `make up`. Kafka, Postgres, Mailpit và gateway luôn chạy; `webhook-sink` thuộc profile `mock`. Build lại riêng P4: `docker compose up -d --build p4-alerting`.

- Log: `make logs s=p4-alerting`.
- Email: Mailpit http://localhost:8025. Webhook: `make logs s=webhook-sink`.
- Để có cảnh báo thật cần cả pipeline (profile `app`), sau đó `make demo`.

Unit test không cần SMTP, Kafka hay Postgres thật:

```bash
make test-python   # toàn bộ test Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/p4-alerting \
  -e PYTHONPATH=/app:/app/p4-alerting waf/base:dev python -B test_alerting.py
```

[`test_alerting.py`](test_alerting.py) có 17 hàm test: phân tích và phân giải `ALERT_THRESHOLDS`, ngưỡng riêng theo domain trên handler, khoá chống trùng và giá trị mặc định của `build_alert`, cooldown (trong và sau cửa sổ, độc lập theo khoá, tắt khi bằng 0), `GET /alerts` lấy `count` từ DB hoặc từ bộ nhớ.

## Giới hạn

- Cooldown nằm trong bộ nhớ của một process: mất khi khởi động lại và không chia sẻ giữa nhiều instance. Bus giao ít nhất một lần, nên một event được đọc lại sau sự cố có thể gửi email và webhook thêm lần nữa; chỉ bảng `alerting.alerts` là idempotent theo `event_id`.
- Email và webhook được gửi đồng bộ trong thread consumer và không thử lại: SMTP hoặc webhook chậm làm chậm việc tiêu thụ topic, lần gửi lỗi chỉ được ghi log và đếm.
- Webhook không kiểm tra mã trạng thái HTTP, phản hồi 4xx/5xx vẫn được đếm vào `webhooks_sent`.
- Service không tự kiểm tra `X-Auth-Subject` và không gọi P1 `/scope/check`: xác thực dựa vào gateway, phạm vi domain dựa vào việc detector đã lọc trước. Cổng 8000 không publish ra host, nhưng trong network nội bộ có thể gọi thẳng mà không cần JWT.
