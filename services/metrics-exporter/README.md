# metrics-exporter (quan sát, Người 3)

Chuyển event trên bus thành metric Prometheus và phơi tại `GET /metrics`. Đây là một consumer bình thường của bus, không sửa service nào khác; Prometheus của stack quan sát scrape nó mỗi 10 giây ([`prometheus.yml`](../../infra/observability/prometheus.yml)).

## Vai trò

Đọc `TOPICS` ([`main.py`](main.py)): `log.raw.ingested`, `log.normalized`, `log.enriched`, `attack.detected`, `anomaly.scored`, `alert.triggered`, `stage.completed`, bằng consumer group `EVENT_GROUP` (mặc định `metrics-exporter`).

Khác P4 và P5, vòng `bus.consume` chạy ở thread chính, còn `/metrics` do `prometheus_client.start_http_server` phục vụ ở thread nền. Consumer lỗi (không kết nối được broker, lỗi giao nhận, lỗi nghiêm trọng) thì process kết thúc và Docker khởi động lại (`restart: unless-stopped`). Service không lưu trữ gì: mọi metric nằm trong bộ nhớ process.

Mỗi event tăng `waf_events_total`, sau đó cập nhật metric riêng theo loại event ([`collectors.py`](collectors.py)):

| Metric | Kiểu | Nhãn | Cập nhật khi |
|---|---|---|---|
| `waf_events_total` | Counter | `event`, `domain` | Mọi event |
| `waf_log_ingested_total` | Counter | `domain`, `source` | `log.raw.ingested`; `source` = `data.log_source` |
| `waf_http_requests_total` | Counter | `domain`, `status_class` | `log.enriched`; `status_class` = `2xx`, `3xx`, `4xx`, `5xx`, ... hoặc `unknown` nếu `status` không phải số |
| `waf_attacks_detected_total` | Counter | `domain`, `attack_type`, `severity`, `rule_id` | `attack.detected` |
| `waf_anomalies_scored_total` | Counter | `domain`, `severity` | `anomaly.scored` |
| `waf_anomaly_score` | Histogram, bucket 10, 20, ..., 100 | `domain` | `anomaly.scored` có `score` là số |
| `waf_alerts_triggered_total` | Counter | `domain`, `severity`, `attack_type` | `alert.triggered`; `attack_type` = phần tử đầu của `attack_types` |
| `waf_stage_completed_total` | Counter | `domain`, `stage` | `stage.completed` |

Trường thiếu thì nhãn nhận `unknown` (kể cả `domain` rỗng); riêng `source` thiếu thì để chuỗi rỗng. `log.normalized` chỉ tăng `waf_events_total`. Ví dụ PromQL và dashboard Grafana dùng các metric này: [`docs/observability.md`](../../docs/observability.md).

## API HTTP

Chỉ có `GET /metrics` trên `HTTP_PORT` (mặc định 8000), trả định dạng text của Prometheus, không cần xác thực. Không có OpenAPI, không đi qua gateway và không có `/healthz`: `HEALTHCHECK` trong [`Dockerfile`](Dockerfile) gọi thẳng `/metrics`.

- [`docker-compose.yml`](../../docker-compose.yml) (profile `obs`) publish `127.0.0.1:${METRICS_PORT:-9101}` sang cổng 8000.
- [`deploy/observability/docker-compose.yml`](../../deploy/observability/docker-compose.yml) không publish ra host; Prometheus gọi `metrics-exporter:8000` trong network `waf-net`.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Bootstrap servers Kafka (stack quan sát mặc định `kafka:19092`) |
| `EVENT_GROUP` | `metrics-exporter` | Consumer group |
| `HTTP_PORT` | `8000` | Cổng phục vụ `/metrics` |
| `LOG_LEVEL` | `info` | Mức log ([`common/logging.py`](../common/logging.py)) |

## Chạy và kiểm thử

Thông thường service chạy trong stack quan sát [`deploy/observability`](../../deploy/observability/docker-compose.yml) cùng Prometheus, Grafana, Loki và kafka-exporter, sau khi Platform (Kafka) đã chạy:

```bash
make obs-up     # Grafana http://localhost:3000, Prometheus http://localhost:9090
make obs-down
```

Stack này dùng image `waf/metrics-exporter:dev`. Service thuộc profile `obs`, nên `make build` chỉ build image này khi `COMPOSE_PROFILES` có `obs`; build riêng bằng `docker compose --profile obs build metrics-exporter`.

Chạy cùng stack phát triển ở gốc repo: thêm `obs` vào `COMPOSE_PROFILES` trong `.env`, chạy `make up`, rồi xem `curl -s localhost:9101/metrics | grep '^waf_'`. Stack gốc không có Prometheus và Grafana.

```bash
make test-python   # toàn bộ test Python
docker run --rm -v "$PWD/services:/app:ro" -w /app/metrics-exporter \
  -e PYTHONPATH=/app:/app/metrics-exporter waf/base:dev python -B test_collectors.py
```

[`test_collectors.py`](test_collectors.py) có 3 hàm test: counter tấn công và cảnh báo tăng đúng nhãn, lớp mã trạng thái HTTP và `waf_events_total`, event thiếu trường vẫn cập nhật với nhãn `unknown`.

## Giới hạn

- Metric chỉ nằm trong bộ nhớ: khởi động lại thì counter về 0, và consumer đọc tiếp từ offset đã commit nên không đếm lại event cũ. Ngược lại, bus giao ít nhất một lần và exporter không loại trùng theo `event_id`, nên event được giao lại sau sự cố có thể bị đếm hai lần.
- Giá trị nhãn (`domain`, `rule_id`, `attack_type`, `source`) lấy nguyên từ event, không có giới hạn trong code; cardinality chỉ thấp khi upstream giữ các giá trị này trong tập hữu hạn. Service không gọi P1 `/scope/check`.
- Metric chỉ phản ánh event đã đọc: khi consumer tụt lag, `/metrics` vẫn trả bình thường nhưng số liệu bị trễ. Theo dõi lag của group `metrics-exporter` qua kafka-exporter (`kafka_consumergroup_lag`).
