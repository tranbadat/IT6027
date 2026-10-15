# deploy/ — Triển khai tách 3 tầng

Ba stack Docker Compose deploy độc lập, nối nhau qua network `waf-net`:

- **`sensor/`** — Loại 1: C1 thu thập log, **cắm vào Nginx/Apache của một app**. Deploy mỗi app một bản.
- **`pipeline/`** — Loại 2: C2, C3, detector Java (D1 + D3), rule-api (D2) — chuẩn hoá, enrichment, phát hiện, chấm điểm.
- **`platform/`** — Loại 3: Kafka + Postgres + P1–P5 + gateway — backbone & quản trị kết quả.

Xem hướng dẫn đầy đủ tại [`../docs/deployment.md`](../docs/deployment.md).

Nhanh (một máy):

```bash
make deploy-up                  # dựng cả 3 stack, môi trường local
make deploy-up ENV=production   # cấu hình production (server thật dùng `make server-up`, xem dưới)
make deploy-ps                  # trạng thái
make deploy-down                # dừng
```

Cấu hình theo môi trường của từng stack: `<stack>/env/<ENV>.env`, xem mục "Môi trường" trong [`../docs/deployment.md`](../docs/deployment.md).

Server thật (Ubuntu, Nginx chạy trên máy chủ, còn lại chạy Docker): `make server-up`, cấu hình trong [`server/`](server/), hướng dẫn ở [`../docs/server-deployment.md`](../docs/server-deployment.md).

Từng stack chạy riêng: `cd deploy/<stack> && cp .env.example .env && docker compose up -d`
(cần `docker network create waf-net` trước, hoặc `make net`).
