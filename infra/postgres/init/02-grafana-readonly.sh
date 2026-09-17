#!/bin/sh
# Tạo role CHỈ-ĐỌC cho Grafana đọc lịch sử (schema detection, alerting).
# Chạy khi khởi tạo volume Postgres lần đầu (sau 01-schemas.sql). Bỏ qua nếu chưa đặt mật khẩu.
# Với DB đã chạy sẵn, áp dụng thủ công bằng cùng nội dung SQL (xem docs/observability.md).
set -e

if [ -z "${GRAFANA_DB_PASSWORD:-}" ]; then
  echo "02-grafana-readonly: GRAFANA_DB_PASSWORD trống — bỏ qua tạo role grafana_ro"
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-waf}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
  ELSE
    ALTER ROLE grafana_ro LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
  END IF;
END
\$\$;

-- Chỉ quyền đọc trên hai schema lịch sử.
GRANT USAGE ON SCHEMA detection, alerting TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA detection, alerting TO grafana_ro;
-- Bảng do owner (${POSTGRES_USER}) tạo về sau cũng tự được SELECT.
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA detection GRANT SELECT ON TABLES TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA alerting  GRANT SELECT ON TABLES TO grafana_ro;
SQL

echo "02-grafana-readonly: role grafana_ro sẵn sàng (chỉ SELECT trên detection, alerting)"
