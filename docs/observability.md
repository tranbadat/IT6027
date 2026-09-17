# Giám sát với Prometheus + Grafana

Stack add-on `deploy/observability` biến **Grafana thành trung tâm giám sát** (metrics + logs + realtime), cắm vào `waf-net`. Gồm:

- **metrics-exporter** — consume `q.metrics` (bind mọi event), phơi `/metrics`. Nguồn số liệu **nghiệp vụ/bảo mật**.
- **RabbitMQ Prometheus plugin** (cổng 15692) — số liệu **hạ tầng** (queue depth, message rate).
- **Prometheus** — scrape hai nguồn trên (10s/lần).
- **Loki + Promtail** — **logs tập trung**: Promtail gom log stdout của *mọi* container (qua Docker socket), gắn nhãn `service`/`project`, đẩy vào Loki.
- **Grafana** — 3 datasource provisioned (Prometheus, Loki, **Postgres**) + 3 dashboard provisioned.

## 3 dashboard (đều tự nạp)

| Dashboard | Nguồn | Nội dung |
|---|---|---|
| **WAF — Tổng quan** | Prometheus | Metrics: tấn công theo loại, top rule, HTTP theo mã, cảnh báo, queue depth |
| **WAF — Realtime / SOC** | Postgres | **Thay dashboard realtime P5**: bảng cảnh báo & tấn công gần đây (URL/payload/IP/rule), top IP, top loại — refresh 5s |
| **WAF — Logs tập trung** | Loki | Log trực tiếp mọi service (lọc theo `service`), số dòng/phút, log lỗi/phút |

> **Realtime qua Grafana:** dashboard SOC đọc thẳng `detection.attacks` + `alerting.alerts` (dữ liệu P5/P4 lưu bền), refresh 5s — hiển thị chi tiết từng request y như P5. **P5 vẫn chạy làm backend** (consume event + ghi `detection.attacks`), chỉ là giao diện xem chuyển sang Grafana; UI HTML của P5 (`/`, `/rules`) vẫn còn, không bắt buộc mở.

### Datasource Postgres dùng role CHỈ-ĐỌC

Grafana **không** dùng tài khoản `waf` (quyền ghi) mà dùng role riêng **`grafana_ro`**: chỉ `SELECT` trên hai schema `detection`, `alerting` — không ghi/xoá được, không đọc được `scope`/`auth`/…

- Deploy mới: role tạo tự động bởi `infra/postgres/init/02-grafana-readonly.sh` (dùng `GRAFANA_DB_PASSWORD` của Platform).
- Mật khẩu do `make deploy-init` sinh, khớp giữa `deploy/platform/.env` (`GRAFANA_DB_PASSWORD`) và `deploy/observability/.env` (`GRAFANA_DB_USER=grafana_ro` + `GRAFANA_DB_PASSWORD`).
- DB đã chạy sẵn: áp dụng cùng SQL trong script trên bằng `psql` (một lần).

## Chạy

```bash
make obs-up      # build metrics-exporter, sinh .env, dựng Prometheus + Grafana + exporter
make obs-down    # dừng
```

Yêu cầu: đã có Platform (RabbitMQ) chạy; nên có Pipeline/Sensor để sinh event.

- **Grafana:** http://localhost:3000 (xem được không cần đăng nhập — anonymous Viewer; quản trị: `admin`/`admin`, đổi qua `deploy/observability/.env`).
- **Prometheus:** http://localhost:9090

## Metric do exporter phơi ra

| Metric | Nhãn | Ý nghĩa |
|---|---|---|
| `waf_events_total` | `event`, `domain` | Tổng event theo loại/domain |
| `waf_log_ingested_total` | `domain`, `source` | Số dòng log thu thập (C1) |
| `waf_http_requests_total` | `domain`, `status_class` | Request theo lớp mã (2xx/4xx/5xx…) |
| `waf_attacks_detected_total` | `domain`, `attack_type`, `severity`, `rule_id` | **Lỗi/tấn công phát hiện** (D1) |
| `waf_anomalies_scored_total` | `domain`, `severity` | Lần chấm điểm bất thường (D3) |
| `waf_anomaly_score` | `domain` | Histogram phân bố điểm |
| `waf_alerts_triggered_total` | `domain`, `severity`, `attack_type` | Cảnh báo kích hoạt |
| `waf_stage_completed_total` | `domain`, `stage` | Tiến độ pipeline (P3) |

Cộng thêm toàn bộ metric RabbitMQ (`rabbitmq_queue_messages_ready`, `rabbitmq_queue_messages`, …).

## Vài truy vấn ví dụ (PromQL)

```promql
# Tấn công phát hiện theo loại (tốc độ)
sum by (attack_type) (rate(waf_attacks_detected_total[1m]))
# Top rule khớp nhiều nhất
topk(10, sum by (rule_id) (waf_attacks_detected_total))
# Tỉ lệ lỗi 4xx/5xx theo domain
sum by (domain) (rate(waf_http_requests_total{status_class=~"4xx|5xx"}[5m]))
# Độ sâu hàng đợi (phát hiện nghẽn pipeline)
sum by (queue) (rabbitmq_queue_messages_ready)
```

## Dùng Prometheus/Grafana sẵn có của bạn

Nếu đã có sẵn hệ Prometheus/Grafana, chỉ cần:
1. Bật plugin RabbitMQ (đã bật sẵn ở Platform) và cho Prometheus của bạn scrape `«hub»:15692`.
2. Dựng riêng `metrics-exporter` (image `waf/metrics-exporter:dev`) trỏ `EVENT_BUS_URL` về bus, rồi scrape nó ở cổng 8000.
3. Import dashboard mẫu tại `infra/observability/grafana/dashboards/waf-overview.json`.

## Ghi chú

- `metrics-exporter` là consumer bình thường của bus: dừng nó thì `q.metrics` tích message (giới hạn 50k, drop-head) — không ảnh hưởng phần còn lại.
- Cardinality được giữ thấp (domain/attack_type/severity/rule_id/stage đều bounded).
