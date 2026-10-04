# Detector service (Java 17)

Dịch vụ đảm nhiệm phần phát hiện của hệ thống phân tích WAF log: nhận request đã chuẩn hóa từ Kafka, chạy rule SQLi/XSS/path traversal, chấm điểm hành vi theo client và phát hiện DDoS theo domain.

## Cấu trúc mã nguồn

```text
src/main/java/com/example/waf/detector/
├── App.java                         # Điểm khởi động ứng dụng
├── config/
│   └── DetectorConfig.java          # Cấu hình và ngưỡng phát hiện
├── kafka/
│   └── KafkaPipeline.java           # Consumer, producer, DLQ và commit offset
├── model/
│   ├── Event.java                   # Request chuẩn hóa
│   ├── RuleMatch.java               # Rule đã khớp
│   └── DetectionResult.java         # Kết quả gửi cho dashboard/cảnh báo
├── service/
│   ├── RuleEngineService.java       # Khai báo API rule engine
│   ├── RequestDetectionService.java # Khai báo API chấm điểm request/client
│   ├── DdosDetectionService.java    # Khai báo API phát hiện DDoS
│   └── impl/
│       ├── RuleEngineServiceImpl.java
│       ├── RequestDetectionServiceImpl.java
│       └── DdosDetectionServiceImpl.java
└── util/
    ├── EnvironmentUtils.java        # Đọc và kiểm tra biến môi trường
    ├── EventMapper.java             # Kiểm tra JSON đầu vào và tạo Event
    └── RequestNormalizer.java       # URL/HTML decode và canonicalization
```

`service/impl` là cách đặt package Java thông dụng cho phần `ServiceImpl`: interface và logic triển khai được tách riêng nhưng vẫn nằm cùng một nhóm nghiệp vụ.

## Luồng Kafka

Một image hỗ trợ ba role:

- `rules`: đọc `web.requests.v1`, phân tích rule và hành vi theo `(domain, client_ip)`.
- `repartition`: đọc `web.requests.v1`, ghi `web.requests.by-domain.v1` với key là domain.
- `ddos`: đọc `web.requests.by-domain.v1`, phát hiện burst phân tán theo domain.

Event sai schema, sai Kafka key hoặc có timestamp vượt quá clock skew cho phép được đưa vào `security.invalid.v1`. Offset chỉ được commit sau khi toàn bộ output trong batch đã được Kafka xác nhận. Pipeline có đặc tính at-least-once, vì vậy phía nhận nên deduplicate theo `event_id`.

## Database

Phần detector hiện không cần relational database:

- Kafka giữ request và detection event.
- Rule là cấu hình trong mã nguồn.
- Cửa sổ 10/60 giây được giữ trong RAM và có giới hạn số client/domain để tránh tăng bộ nhớ vô hạn.
- Dịch vụ dashboard của người 3 có thể dùng PostgreSQL/Elasticsearch/ClickHouse để lưu lịch sử và truy vấn báo cáo.

Nếu triển khai nhiều detector instance và cần giữ nguyên cửa sổ qua restart/rebalance, nên chuyển state sang Kafka Streams với RocksDB state store và changelog topic. Đây là state phục vụ stream processing, không cần dựng một database dùng chung cho mỗi request.

## Các sửa đổi phát hiện chính

- Behavioral window dùng timestamp của event, nên replay log cũ không tạo request burst giả.
- Event quá xa trong tương lai bị đưa vào DLQ và không thể đẩy watermark DDoS.
- Rule chạy riêng trên `path`, `query`, `body`; output chỉ rõ field đã match.
- Percent decode chịu được đoạn encoding lỗi và hỗ trợ double encoding.
- Bổ sung SQL comment obfuscation, `pg_sleep`, `WAITFOR DELAY`, stacked query, XSS handler tổng quát, `srcdoc`, data URL và sensitive path.
- Alert request có cooldown; kết quả trong cooldown có `alert_suppressed=true`.
- State có giới hạn LRU qua `MAX_TRACKED_CLIENTS` và `MAX_TRACKED_DOMAINS`.
- Có thể đặt `EMIT_ONLY_ALERTS=true` để không gửi các kết quả không phải alert sang output topic.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `REQUEST_WINDOW_SECONDS` | 60 | Cửa sổ hành vi client |
| `REQUEST_THRESHOLD` | 20 | Ngưỡng request trong cửa sổ |
| `ERROR_THRESHOLD` | 8 | Ngưỡng response lỗi |
| `DISTINCT_PATH_THRESHOLD` | 12 | Ngưỡng path khác nhau |
| `ALERT_SCORE_THRESHOLD` | 70 | Điểm phát cảnh báo |
| `RULE_ALERT_COOLDOWN_SECONDS` | 15 | Cooldown cảnh báo client |
| `DDOS_WINDOW_SECONDS` | 10 | Cửa sổ DDoS |
| `DDOS_REQUESTS_THRESHOLD` | 300 | Ngưỡng request DDoS |
| `DDOS_UNIQUE_IPS_THRESHOLD` | 20 | Ngưỡng IP khác nhau |
| `DDOS_COOLDOWN_SECONDS` | 10 | Cooldown cảnh báo DDoS |
| `MAX_FUTURE_SKEW_SECONDS` | 300 | Clock skew tương lai cho phép |
| `MAX_TRACKED_CLIENTS` | 100000 | Giới hạn client giữ trong RAM |
| `MAX_TRACKED_DOMAINS` | 10000 | Giới hạn domain giữ trong RAM |
| `EMIT_ONLY_ALERTS` | false | Chỉ ghi event có `alert=true` |
| `KAFKA_UI_PORT` | 8080 | Cổng giao diện theo dõi Kafka |

## Build và kiểm thử

```bash
mvn test
mvn package
docker build -t waf-log-detector .
```

`mvn test` chạy JUnit cho rule encoding/obfuscation, false positive giữa các field, replay theo event time, timestamp poisoning, cooldown và DDoS threshold. File chạy được tạo tại `target/app.jar`.

## Chạy bằng Docker Compose

Toàn bộ biến Kafka, topic, batch và ngưỡng phát hiện được quản lý trong `.env`. `.env.example` là mẫu để khôi phục hoặc tạo cấu hình cho môi trường khác.

Khởi động Kafka, tạo topic và chạy đủ ba detector role:

```bash
docker compose up --build -d
docker compose ps
```

Chạy thêm log simulator ở profile riêng:

```bash
docker compose --profile simulation up --build -d
```

Scale rule worker theo số partition của `web.requests.v1`:

```bash
docker compose up -d --scale detector=3
```

Xem log và dừng hệ thống:

```bash
docker compose logs -f detector ddos-detector
docker compose down
```

Kafka trong container dùng `kafka:19092`; chương trình trên máy host kết nối qua `localhost:9092`. Volume `kafka-data` giữ dữ liệu sau `docker compose down`.

Mở Kafka UI tại [http://localhost:8080](http://localhost:8080). Hướng dẫn chạy, kiểm thử từng loại tấn công, đọc kết quả và sơ đồ class/method nằm trong [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Ví dụ chạy từng role với Kafka có sẵn:

```bash
docker run --rm -e KAFKA_BOOTSTRAP_SERVERS=broker.example:9092 waf-log-detector java -jar /app/app.jar rules
docker run --rm -e KAFKA_BOOTSTRAP_SERVERS=broker.example:9092 waf-log-detector java -jar /app/app.jar repartition
docker run --rm -e KAFKA_BOOTSTRAP_SERVERS=broker.example:9092 waf-log-detector java -jar /app/app.jar ddos
```
