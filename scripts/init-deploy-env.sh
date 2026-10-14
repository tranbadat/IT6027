#!/usr/bin/env bash
# Sinh secret và ghi .env cho các stack deploy (platform/pipeline/sensor/observability).
# Pipeline/Sensor nối Kafka qua waf-net; Observability dùng lại mật khẩu role chỉ-đọc của
# Platform để Grafana đọc DB. Không ghi đè .env đã tồn tại.
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
  echo "platform/.env đã có — dùng lại secret hiện tại."
else
  postgres_pass="$(openssl rand -hex 16)"
  jwt_secret="$(openssl rand -hex 32)"
  grafana_db_pass="$(openssl rand -hex 16)"
  umask 077
  sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${postgres_pass}/" \
      -e "s/^JWT_SECRET=.*/JWT_SECRET=${jwt_secret}/" \
      -e "s/^GRAFANA_DB_PASSWORD=.*/GRAFANA_DB_PASSWORD=${grafana_db_pass}/" \
      "$DEPLOY/platform/.env.example" > "$platform_env"
  echo "Đã tạo deploy/platform/.env"
fi

bus_url="kafka:19092"

# Đọc creds Postgres từ platform/.env để observability (Grafana) khớp khi query DB.
pg_user="$(grep -E '^POSTGRES_USER=' "$platform_env" | cut -d= -f2-)"
pg_pass="$(grep -E '^POSTGRES_PASSWORD=' "$platform_env" | cut -d= -f2-)"
pg_db="$(grep -E '^POSTGRES_DB=' "$platform_env" | cut -d= -f2-)"
grafana_db_pass="$(grep -E '^GRAFANA_DB_PASSWORD=' "$platform_env" | cut -d= -f2-)"

for stack in pipeline sensor observability; do
  target="$DEPLOY/$stack/.env"
  if [[ -f "$target" ]]; then
    echo "deploy/$stack/.env đã tồn tại — giữ nguyên."
    continue
  fi
  umask 077
  # Các dòng POSTGRES_* chỉ có trong observability/.env.example -> no-op với pipeline/sensor.
  sed -e "s#^EVENT_BUS_URL=.*#EVENT_BUS_URL=${bus_url}#" \
      -e "s#^POSTGRES_DB=.*#POSTGRES_DB=${pg_db:-waf}#" \
      -e "s#^GRAFANA_DB_PASSWORD=.*#GRAFANA_DB_PASSWORD=${grafana_db_pass}#" \
      "$DEPLOY/$stack/.env.example" > "$target"
  echo "Đã tạo deploy/$stack/.env (trỏ về bus/DB của Platform)"
done

echo "Xong. Nhớ: nếu chạy khác máy, sửa host trong EVENT_BUS_URL/SCOPE_SERVICE_URL của pipeline & sensor."
