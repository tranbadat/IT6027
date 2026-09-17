#!/bin/sh
# Nạp exchange/queue/binding từ definitions.json vào RabbitMQ. Chạy lại nhiều lần vẫn an toàn.
# Được gọi bởi post_start hook (với --boot) trong docker-compose.yml và bởi `make bus-topology`.
#
# Lưu ý: RabbitMQ từ chối đổi `arguments` của queue đã tồn tại (PRECONDITION_FAILED)
# -> xoá queue đó trên UI (hoặc `make clean`) rồi chạy lại.
set -eu

DEFINITIONS_FILE="${DEFINITIONS_FILE:-/etc/rabbitmq/waf/definitions.json}"
READY_MARKER=/tmp/topology-ready
MAX_ATTEMPTS=60
RETRY_DELAY_SECONDS=2

# Chỉ reset marker healthcheck khi container vừa khởi động. Khi nạp lại lúc đang chạy mà lỗi,
# topology cũ vẫn còn nguyên nên container phải tiếp tục healthy.
if [ "${1:-}" = "--boot" ]; then
  rm -f "$READY_MARKER"
fi

# await_startup thoát ngay (mã 69) nếu node chưa nhận kết nối CLI -> thử lại.
attempt=1
until rabbitmqctl -q await_startup --timeout 10 >/dev/null 2>&1; do
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "load-topology: RabbitMQ is not up after ${MAX_ATTEMPTS} attempts" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep "$RETRY_DELAY_SECONDS"
done

rabbitmqctl -q import_definitions "$DEFINITIONS_FILE"
touch "$READY_MARKER"
echo "load-topology: imported ${DEFINITIONS_FILE}"
