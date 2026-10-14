# Pipeline Orchestrator (P3, Người 3)

Theo dõi tiến độ xử lý log của toàn pipeline qua event bus Kafka, định kỳ phát event `stage.completed` và trả trạng thái hiện tại qua HTTP.

## Vai trò

Process chạy ba thread: thread consumer (client Kafka riêng) đếm event theo cặp (domain, stage); thread publisher (client Kafka riêng) mỗi `STAGE_WINDOW_SECONDS` giây phát `stage.completed`; thread chính chạy FastAPI/uvicorn phục vụ `/healthz` và `/pipelines`.

### Cấu hình pipeline (YAML)

P3 đọc mọi file `*.yaml`/`*.yml` trong `PIPELINES_DIR` theo thứ tự tên file, một lần khi khởi động. Thư mục này mount chỉ đọc từ [`config/pipelines/`](../../config/pipelines/), ví dụ [`shop.local.yaml`](../../config/pipelines/shop.local.yaml):

```yaml
pipeline: shop.local
domain: shop.local
stages: [ingestion, normalize, enrich, detect, score, alert]
```

| Trường | Mặc định | Ý nghĩa |
|---|---|---|
| `pipeline` | tên file bỏ đuôi | tên pipeline, gắn vào `data.pipeline` của `stage.completed` |
| `domain` | giá trị `pipeline` | domain mà pipeline theo dõi |
| `stages` | `[]` | danh sách stage, chỉ để hiển thị trong `GET /pipelines` |

File không đọc được hoặc sai cú pháp YAML bị bỏ qua (ghi log `error`); thư mục không tồn tại thì danh sách pipeline rỗng.

### Topic đọc và cách đếm

Danh sách `TOPICS` trong [`main.py`](main.py), đọc bằng consumer group `EVENT_GROUP` (mặc định `p3-orchestrator`) qua [`common/bus.py`](../common/bus.py). Mỗi topic ứng với một stage (`EVENT_STAGE`): `log.raw.ingested` - `ingestion`, `log.normalized` - `normalize`, `log.enriched` - `enrich`, `attack.detected` - `detect`, `anomaly.scored` - `score`, `alert.triggered` - `alert`. P3 không đọc `stage.completed` do chính nó phát.

Mỗi envelope hợp lệ làm tăng bộ đếm của cặp (`domain` của envelope, stage) và lưu `timestamp` của envelope làm `last_event_at`. P3 đếm mọi domain xuất hiện trên bus, kể cả domain không có file cấu hình, và không đối chiếu với `stages` trong YAML. Message sai envelope được chuyển sang `waf.deadletter` theo quy ước chung ([`contracts/events`](../../contracts/events/README.md)). Consumer lỗi (broker không xác nhận, commit lỗi) thì process thoát với mã 1 để Docker khởi động lại.

### Phát `stage.completed`

Cứ mỗi `STAGE_WINDOW_SECONDS` giây, publisher lấy các cặp (domain, stage) có event mới kể từ lần phát trước và phát một `stage.completed` cho mỗi cặp; cặp không có event mới thì không phát. Envelope có `domain` của cặp, `source = p3-orchestrator`, không có `request_id`/`session_id` (contract cho phép với event này), key Kafka là domain. Consumer: `p5-dashboard`, `metrics-exporter`.

| Trường `data` | Ý nghĩa |
|---|---|
| `pipeline` | tên pipeline có `domain` tương ứng; không có cấu hình thì bằng chính domain |
| `stage` | tên stage |
| `processed` | số event của stage nhận được kể từ lần phát trước |
| `window_seconds` | giá trị `STAGE_WINDOW_SECONDS` |

### Kết quả `GET /pipelines`

```json
{"pipelines": [{"pipeline": "shop.local", "domain": "shop.local", "stages": ["ingestion", "normalize", "..."]}],
 "runtime": [{"domain": "shop.local", "stage": "detect", "count": 28, "last_event_at": "2026-10-14T04:12:36.314696545Z"}]}
```

`pipelines` là cấu hình đã nạp. `runtime` sắp xếp theo (domain, stage); `count` là tổng cộng dồn từ khi process khởi động.

## API HTTP

Contract: [`contracts/openapi/pipelines.yaml`](../../contracts/openapi/pipelines.yaml).

| Method | Path tại service | Qua gateway | Xác thực |
|---|---|---|---|
| GET | `/healthz` | không có route | không (healthcheck của container) |
| GET | `/pipelines` | `/api/pipelines/` | JWT khi đi qua gateway |

Gateway chuyển `/api/pipelines/` tới `PIPELINE_SERVICE_URL` (mặc định `http://p3-orchestrator:8000`) qua [`protected.conf`](../../infra/gateway/snippets/protected.conf), bỏ tiền tố `/api` và dấu `/` ở cuối.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EVENT_BUS_URL` | bắt buộc | Kafka bootstrap servers (`kafka:19092` trong compose) |
| `EVENT_GROUP` | `p3-orchestrator` | consumer group; đổi khi cần chạy song song hai bản khác nhau |
| `PIPELINES_DIR` | `/config/pipelines` | thư mục chứa file YAML cấu hình pipeline |
| `STAGE_WINDOW_SECONDS` | `10` | chu kỳ phát `stage.completed` (giây) |
| `HTTP_PORT` | `8000` | cổng HTTP |
| `LOG_LEVEL` | `info` | mức log JSON ra stdout; `debug` ghi log từng event |

P3 không dùng PostgreSQL. Trong triển khai tách stack, P3 chạy trong [`deploy/platform`](../../deploy/platform/docker-compose.yml) với cùng biến môi trường và mount `config/pipelines`.

## Chạy và kiểm thử

Service thuộc profile `p3`, `platform` và `app`. Bật một trong các profile đó trong `COMPOSE_PROFILES`, rồi:

```bash
make build-base                                # image nền waf/base:dev
docker compose up -d --build p3-orchestrator   # kéo theo kafka-init và postgres
docker compose ps p3-orchestrator              # cột STATUS phải là healthy
```

`HEALTHCHECK` trong [`Dockerfile`](Dockerfile) gọi `http://127.0.0.1:8000/healthz` mỗi 10 giây. Xem trạng thái và event phát ra (cần `TOKEN` lấy từ `POST /api/auth/token`):

```bash
curl -s localhost:8080/api/pipelines/ -H "Authorization: Bearer $TOKEN" | jq '.runtime'
make bus-peek t=stage.completed
```

Unit test [`test_orchestrator.py`](test_orchestrator.py) có 6 test: nạp cấu hình YAML (chuẩn hoá, bỏ file lỗi, thư mục không tồn tại), ánh xạ event sang stage, event giao lại chỉ đếm một lần, phần thay đổi giữa hai lần publish, nội dung `stage.completed`. `make test-python` chạy toàn bộ test Python; chạy riêng bằng:

```bash
docker run --rm -v "$PWD/services:/app:ro" -w /app/p3-orchestrator \
  -e PYTHONPATH=/app:/app/p3-orchestrator waf/base:dev python -B test_orchestrator.py
```

## Giới hạn

- Trạng thái chỉ nằm trong bộ nhớ: khởi động lại thì bộ đếm và `last_event_at` về rỗng, còn offset đã commit nên event cũ không được đếm lại. Group mới (hoặc đổi `EVENT_GROUP`) đọc từ đầu topic và cộng cả event cũ còn trong retention.
- Loại bản trùng theo `event_id` trong bộ nhớ với 10.000 id gần nhất: message giao lại sau khi P3 khởi động lại vẫn có thể bị đếm thêm.
- Mốc đã phát được cập nhật trước khi publish; publish lỗi chỉ ghi log, số event của cửa sổ đó không được phát lại. Nếu thread publisher không kết nối được Kafka, thread dừng nhưng `/healthz` vẫn trả 200.
- P3 không gọi `/scope/check` và không tự kiểm tra xác thực; `GET /pipelines` chỉ được bảo vệ khi đi qua gateway.
