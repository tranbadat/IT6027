#!/usr/bin/env bash
# Sinh secret dùng chung và ghi .env cho cả 3 stack (platform/pipeline/sensor) sao cho
# user/mật khẩu RabbitMQ KHỚP NHAU. Không ghi đè .env đã tồn tại.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"

if ! command -v openssl >/dev/null 2>&1; then
  echo "Cần openssl để sinh secret. Hoặc tự copy từng .env.example thành .env rồi sửa cho khớp." >&2
  exit 1
fi

# Nếu platform/.env đã có, TÁI SỬ DỤNG secret trong đó để pipeline/sensor khớp.
platform_env="$DEPLOY/platform/.env"
if [[ -f "$platform_env" ]]; then
  rabbit_user="$(grep -E '^RABBITMQ_USER=' "$platform_env" | cut -d= -f2-)"
  rabbit_pass="$(grep -E '^RABBITMQ_PASSWORD=' "$platform_env" | cut -d= -f2-)"
  echo "platform/.env đã có — dùng lại secret RabbitMQ hiện tại."
else
  rabbit_user="waf"
  rabbit_pass="$(openssl rand -hex 16)"
  postgres_pass="$(openssl rand -hex 16)"
  jwt_secret="$(openssl rand -hex 32)"
  umask 077
  sed -e "s/^RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${rabbit_pass}/" \
      -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${postgres_pass}/" \
      -e "s/^JWT_SECRET=.*/JWT_SECRET=${jwt_secret}/" \
      "$DEPLOY/platform/.env.example" > "$platform_env"
  echo "Đã tạo deploy/platform/.env"
fi

bus_url="amqp://${rabbit_user}:${rabbit_pass}@rabbitmq:5672/"

for stack in pipeline sensor observability; do
  target="$DEPLOY/$stack/.env"
  if [[ -f "$target" ]]; then
    echo "deploy/$stack/.env đã tồn tại — giữ nguyên."
    continue
  fi
  umask 077
  sed -e "s#^EVENT_BUS_URL=.*#EVENT_BUS_URL=${bus_url}#" \
      "$DEPLOY/$stack/.env.example" > "$target"
  echo "Đã tạo deploy/$stack/.env (trỏ về bus của Platform)"
done

echo "Xong. Nhớ: nếu chạy khác máy, sửa host trong EVENT_BUS_URL/SCOPE_SERVICE_URL của pipeline & sensor."
