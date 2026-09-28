# IT6027 — Nhánh `c1`: Log Collection (Người 1)

Nhánh này chứa **phần Người 1** của WAF Log Analyzer: thu thập và chuẩn hoá log truy cập web, rồi publish lên event bus cho Người 2 (Detection) tiêu thụ.

```
Nginx/Apache access log ─► C1 ingestion ─► log.raw.ingested
                                            │
                              C2 parser ────► log.normalized
                                            │
                          C3 enrichment ────► log.enriched   ◄── Người 2 tiêu thụ từ đây
```

Kèm hạ tầng tối thiểu để **Người 2 chạy được và lấy dữ liệu**: web mục tiêu (Nginx), RabbitMQ (bus), và **mock Scope Service** (thay P1 của Người 3 — chưa cần).

## Chạy

Yêu cầu: Docker + Docker Compose (2.30+), `make`, `curl`, `jq`, `openssl`.

```bash
make up            # dựng web-nginx + rabbitmq + mock-api + C1 + C2 + C3
make demo          # gửi request thử (bình thường + SQLi/XSS/path traversal) -> sinh log
make peek-enriched # xem vài bản ghi log.enriched trên bus
```

- Web mục tiêu: http://localhost:8081 (`shop.local`, `internal.local`)
- RabbitMQ UI: http://localhost:15672 (tài khoản trong `.env`)

## Người 2 lấy dữ liệu thế nào

C3 publish event **`log.enriched`** lên exchange topic **`waf.events`**. Có sẵn queue **`q.enriched`** đã bind `log.enriched` để tiêu thụ ngay:

- Kết nối bus: `amqp://<user>:<pass>@localhost:5672/` (creds trong `.env`).
- Consume queue `q.enriched`, hoặc tự bind queue riêng của bạn vào `waf.events` với routing key `log.enriched` / `log.normalized` / `log.raw.ingested`.
- Xem nhanh không tiêu thụ mất: `make peek-enriched`.

Cấu trúc event (vỏ chung + `data`): xem [`contracts/events/README.md`](contracts/events/README.md) và [`contracts/events/envelope.schema.json`](contracts/events/envelope.schema.json). Trường của `log.enriched.data`: `src_ip, method, path, query_string, status, host, user_agent, request_ts, path_decoded, query_decoded, session_id, req_count_1m, geo`.

## Module (Người 1)

| Module | Nhiệm vụ | Output |
|---|---|---|
| **C1** `services/c1-ingestion` | Tail access log (Nginx/Apache), xử lý log rotation, suy domain từ tên file, kiểm tra scope | `log.raw.ingested` |
| **C2** `services/c2-parser` | Parse combined log (kể cả combined chuẩn không có host/rt/sid), giải mã URL | `log.normalized` |
| **C3** `services/c3-enrichment` | GeoIP (tuỳ chọn), gom phiên, tần suất request theo IP | `log.enriched` |

Chạy unit test: `make test` (parser, enrichment, tailer).

GeoIP là tuỳ chọn — thiếu file DB thì `geo=null` (không lỗi). Đặt file tại `data/geoip/dbip-city-lite.mmdb` nếu muốn.

## Phạm vi (scope)

Mọi service chỉ xử lý log của domain đã được Scope Service xác nhận. Nhánh này dùng **mock** (`infra/mock-api`, cho phép `shop.local`, `blog.local`, `shop-app`). Khi Người 3 có P1 thật: đổi `SCOPE_SERVICE_URL` trong `.env` sang `http://p1-scope:8000`.

> Toàn hệ thống (Detection của Người 2, Platform của Người 3, observability) nằm ở nhánh `dev`.
