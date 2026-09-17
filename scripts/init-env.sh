#!/usr/bin/env bash
# Tạo .env từ .env.example với mật khẩu ngẫu nhiên. Không ghi đè .env đã tồn tại.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
TEMPLATE="$ROOT/.env.example"

if [[ -f "$ENV_FILE" ]]; then
  echo ".env đã tồn tại — giữ nguyên."
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "Cần openssl để sinh mật khẩu. Hoặc tự chạy: cp .env.example .env rồi sửa mật khẩu." >&2
  exit 1
fi

rabbitmq_password="$(openssl rand -hex 16)"
postgres_password="$(openssl rand -hex 16)"
jwt_secret="$(openssl rand -hex 32)"

umask 077
sed -e "s/^RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${rabbitmq_password}/" \
    -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${postgres_password}/" \
    -e "s/^JWT_SECRET=.*/JWT_SECRET=${jwt_secret}/" \
    "$TEMPLATE" > "$ENV_FILE"

echo "Đã tạo .env với mật khẩu ngẫu nhiên."
