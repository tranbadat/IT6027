# IT6027 — WAF Log Analyzer

Hệ thống giám sát và phát hiện tấn công web: thu thập log truy cập từ Nginx/Apache theo thời gian thực, chuẩn hoá, phát hiện các mẫu tấn công bằng rule engine tự viết, chấm điểm bất thường và cảnh báo trên dashboard.

> Phạm vi: **chỉ phát hiện và cảnh báo**. Hệ thống không tự động chặn IP hay thay đổi cấu hình web server. Mọi tính năng phản ứng tự động (nếu có) bắt buộc qua phê duyệt thủ công và mặc định chạy ở chế độ dry run.

## Kiến trúc

Các service độc lập, giao tiếp qua event bus với một event schema thống nhất:

```
{ event, request_id | session_id, timestamp, data }
```

Luồng event chính:

```
Nginx / Apache access log
        │
        ▼
C1 Log Ingestion ──► log.raw.ingested
        │
        ▼
C2 Parsing & Normalize ──► log.normalized
        │
        ▼
C3 Enrichment & Session ──► log.enriched
        │
        ├──────────────────────────────┐
        ▼                              ▼
D1 Rule Engine ──► attack.detected   D3 Anomaly Scoring
        │                              │
        │                              ▼
        │                       anomaly.scored ──► alert.triggered
        ▼                                              │
   D2 Rule Management (CRUD API)                       ▼
                                              P4 Alerting (webhook / email)
                                                       │
P1 Scope Service ◄── mọi service gọi trước khi xử lý   ▼
P2 Auth Gateway (JWT)                          P5 Dashboard realtime
P3 Pipeline Orchestration ──► stage.completed
```

Ràng buộc xuyên suốt: mọi service **phải** gọi `GET /scope/check` (P1) trước khi xử lý log của một domain mới. Không service nào được xử lý log ngoài phạm vi đã xác nhận.

## Module

### Log Collection (Người 1)

| Module | Nhiệm vụ | Output |
|---|---|---|
| C1 | Đọc access log theo cơ chế tail liên tục (file/volume mount) hoặc nhận qua syslog; xử lý log rotation | `log.raw.ingested` |
| C2 | Parse combined log format của Nginx/Apache; xử lý dòng lỗi định dạng, URL encode | `log.normalized` |
| C3 | GeoIP theo IP, gom phiên theo IP + cookie, tính tần suất request | `log.enriched` |

### Detection Engine (Người 2)

| Module | Nhiệm vụ | Output |
|---|---|---|
| D1 | Rule engine **tự viết**: đọc rule YAML/JSON, matcher regex/pattern trên URL, query string, body, header. Tối thiểu 3 nhóm: SQL Injection, XSS, path traversal | `attack.detected` |
| D2 | API CRUD quản lý rule, validate schema, chặn regex gây backtracking không kiểm soát; kèm **UI quản trị rule** tại `/rules` | — |
| D3 | Chấm điểm kết hợp số rule khớp + tín hiệu thống kê (tần suất, tỷ lệ mã lỗi) | `anomaly.scored`, `alert.triggered` |

D1 là lõi kỹ thuật của đề tài — không chỉ nạp lại bộ rule có sẵn (OWASP CRS) mà phải tự xử lý logic khớp mẫu.

### Platform (Người 3)

| Module | Nhiệm vụ |
|---|---|
| P1 | Scope Management — `GET /scope/check`, làm đầu tiên |
| P2 | Auth Gateway — JWT cho API nội bộ, định tuyến giữa các service |
| P3 | Pipeline Orchestration — cấu hình pipeline YAML, theo dõi trạng thái xử lý |
| P4 | Alerting — webhook/email, ngưỡng cảnh báo riêng theo domain |
| P5 | Dashboard realtime — traffic theo domain, top loại tấn công, top IP nguồn, lịch sử cảnh báo |

## Chạy hệ thống

Yêu cầu: Docker Compose 2.30 trở lên, `make`, `curl`, `jq`, `openssl`.

```bash
make up      # tạo .env (mật khẩu ngẫu nhiên), build & khởi động TOÀN BỘ hệ thống
make demo    # gửi request thử (gồm SQLi/XSS/path traversal) tới web mục tiêu
make e2e     # nghiệm thu end-to-end toàn pipeline theo "định nghĩa hoàn thành"
make smoke   # kiểm tra hạ tầng
```

Toàn bộ 11 service (C1–C3, D1–D3, P1–P5) viết bằng Python, dùng chung image nền `waf/base:dev` và thư viện `services/common/`. Sau `make up`:

- **Dashboard realtime (P5):** http://localhost:8080 — lưu lượng theo domain, top loại tấn công, top IP nguồn, lịch sử cảnh báo, feed trực tiếp.
- **Email cảnh báo (P4):** http://localhost:8025 (Mailpit).
- **RabbitMQ UI:** http://localhost:15672 · **Swagger UI:** http://localhost:8090.

Luồng thật: gửi request (kể cả SQLi) tới Nginx mục tiêu → C1 tail log → C2 chuẩn hoá → C3 enrichment → D1 khớp rule tự viết → D3 chấm điểm → P4 cảnh báo (email + webhook) → P5 hiển thị gần như tức thời. Log ngoài phạm vi Scope Service không bị xử lý.

Chạy một phần (vd hạ tầng + mock khi service chưa xong): đặt `COMPOSE_PROFILES=mock,docs` và `SCOPE_SERVICE_URL=AUTH_SERVICE_URL=http://mock-api` trong `.env`. Chi tiết: [`docs/infrastructure.md`](docs/infrastructure.md) và [`services/README.md`](services/README.md).

## Triển khai tách 3 tầng (deploy độc lập)

Ngoài bản tất-cả-trong-một ở trên, hệ thống còn tách thành **3 stack deploy riêng** (thư mục [`deploy/`](deploy/)), nối nhau qua network `waf-net`, để cắm phần thu thập vào Nginx/Apache của app bất kỳ mà không đụng phần trung tâm:

| Loại | Stack | Thành phần | Deploy ở đâu |
|---|---|---|---|
| 1 | Sensor | C1 | mỗi app cần giám sát (cạnh Nginx/Apache của app) |
| 2 | Pipeline | C2, C3, D1, D2, D3 | trung tâm |
| 3 | Platform | RabbitMQ, Postgres, P1–P5, gateway | trung tâm (hub) |

```bash
make deploy-up     # tạo waf-net, build image, sinh .env khớp nhau, dựng cả 3 stack
make deploy-ps     # trạng thái · make deploy-down để dừng
```

Cắm sensor vào Nginx thật của một app: đặt `SENSOR_NGINX_LOG_DIR` + `C1_DOMAIN` trong `deploy/sensor/.env`, thêm domain vào scope, rồi `docker compose up -d c1-ingestion`. Parser nhận cả log `combined` chuẩn lẫn `waf_combined`. Hướng dẫn đầy đủ: [`docs/deployment.md`](docs/deployment.md).

### Giám sát (Prometheus + Grafana)

Stack add-on `deploy/observability` biến Grafana thành trung tâm giám sát (không sửa service nào):

```bash
make obs-up      # Prometheus + Loki/Promtail + Grafana + metrics-exporter
```

**Grafana** (http://localhost:3000) tự nạp 3 dashboard:
- **Tổng quan** — metrics (Prometheus): tấn công theo loại, top rule, queue depth…
- **Realtime / SOC** — đọc thẳng Postgres (`detection.attacks`, `alerting.alerts`): bảng cảnh báo/tấn công chi tiết, top IP/loại, refresh 5s — **thay dashboard realtime P5** (P5 vẫn chạy làm backend ghi DB).
- **Logs tập trung** — Loki/Promtail gom log stdout của mọi service, lọc theo `service`.

Prometheus: http://localhost:9090. Chi tiết + PromQL/LogQL mẫu: [`docs/observability.md`](docs/observability.md).

## Nguyên tắc phát triển

- **Interface first**: Người 3 định nghĩa OpenAPI/JSON schema và dựng mock server cho Scope Service, Event Bus, Auth ngay tuần 1 để ba người phát triển song song.
- Mỗi service có `Dockerfile` riêng, một `docker-compose.yml` chung ở gốc repository để chạy thử toàn hệ thống trên máy cá nhân.
- Daily sync ~15 phút: báo cáo API nào đã thật, API nào còn mock.
- Mỗi service có README và tài liệu OpenAPI riêng.

## Mốc tích hợp

| Giai đoạn | Nội dung | Phụ thuộc |
|---|---|---|
| 0 | API contract + mock Scope/Auth/Event Bus (P3) | — |
| 1 | C1 Log Ingestion | mock Scope Service |
| 2 | C2, C3 Parsing & Enrichment | giai đoạn 1 |
| 3 | D1, D2 Rule Engine & quản lý rule | `log.enriched` thật hoặc dữ liệu mẫu |
| 4 | D3 Chấm điểm bất thường | `attack.detected` thật |
| 5 | P3 Pipeline Orchestration nối toàn bộ | giai đoạn 1–4, API thật |
| 6 | Alerting, Dashboard, kiểm thử toàn trình | giai đoạn 5 |

Tổng thời gian: 4 tuần. Lịch trình chi tiết theo tuần cho từng người: xem [`ke_hoach_waf_log_analyzer.md`](ke_hoach_waf_log_analyzer.md).

## Định nghĩa hoàn thành

Chạy được một kịch bản thật end-to-end theo hướng realtime, không nạp file log thủ công:

1. Trỏ Log Collector vào log đang được một Nginx/Apache thật ghi liên tục cho một domain đã khai báo.
2. Gửi một số request thử tới web server đó, trong đó có ít nhất một request dạng SQL Injection.
3. Hệ thống tự đọc log mới → chuẩn hoá → khớp rule tấn công tự viết → chấm điểm bất thường.
4. Kết quả hiển thị gần như tức thời trên dashboard kèm cảnh báo tương ứng.

Kèm theo:

- Không service nào xử lý log của domain ngoài phạm vi đã xác nhận qua Scope Service.
- Không có hành vi tự động chặn IP hay thay đổi hệ thống.
- Có tài liệu OpenAPI và README cho từng service để người ngoài nhóm chạy lại được.

## Tài liệu

- [`ke_hoach_waf_log_analyzer.md`](ke_hoach_waf_log_analyzer.md) — kế hoạch triển khai đầy đủ: phân công, chi tiết từng module, lịch trình 4 tuần.
- [`docs/infrastructure.md`](docs/infrastructure.md) — hạ tầng Docker Compose: thành phần, profile, log format, gateway, chuyển mock sang service thật, xử lý sự cố.
- [`services/README.md`](services/README.md) — quy ước và biến môi trường cho từng service.
- [`contracts/`](contracts/) — OpenAPI (Scope, Auth), event envelope schema, bảng event → queue.
