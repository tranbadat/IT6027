# deploy/ — Triển khai tách 3 tầng

Ba stack Docker Compose deploy độc lập, nối nhau qua network `waf-net`:

- **`sensor/`** — Loại 1: C1 thu thập log, **cắm vào Nginx/Apache của một app**. Deploy mỗi app một bản.
- **`pipeline/`** — Loại 2: C2, C3, D1, D2, D3 — chuẩn hoá, enrichment, phát hiện, chấm điểm.
- **`platform/`** — Loại 3: RabbitMQ + Postgres + P1–P5 + gateway — backbone & quản trị kết quả.

Xem hướng dẫn đầy đủ tại [`../docs/deployment.md`](../docs/deployment.md).

Nhanh (một máy):

```bash
make deploy-up     # dựng cả 3 stack
make deploy-ps     # trạng thái
make deploy-down   # dừng
```

Từng stack chạy riêng: `cd deploy/<stack> && cp .env.example .env && docker compose up -d`
(cần `docker network create waf-net` trước, hoặc `make net`).
