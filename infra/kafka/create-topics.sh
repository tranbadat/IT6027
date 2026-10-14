#!/usr/bin/env bash
# Tạo topic cho event bus (idempotent). Chạy trong container kafka-init sau khi broker healthy.
# Quy ước: topic = tên event (contracts/events/README.md). Key message = "domain|src_ip".
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:19092}"
PARTITIONS="${KAFKA_PARTITIONS:-3}"
RETENTION_MS="${KAFKA_RETENTION_MS:-259200000}"        # 3 ngày cho topic sự kiện
DLQ_RETENTION_MS="${KAFKA_DLQ_RETENTION_MS:-604800000}" # 7 ngày cho dead-letter
TOPICS_BIN=/opt/kafka/bin/kafka-topics.sh

create() {
  local topic="$1"; shift
  "$TOPICS_BIN" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
    --topic "$topic" --replication-factor 1 "$@"
}

for topic in log.raw.ingested log.normalized log.enriched attack.detected \
             anomaly.scored alert.triggered stage.completed log.enriched.by-domain; do
  create "$topic" --partitions "$PARTITIONS" --config "retention.ms=$RETENTION_MS"
done
# Snapshot rule hiện hành (key "active"): compacted để detector mới khởi động đọc lại được.
create rules.changed --partitions 1 --config cleanup.policy=compact
create waf.deadletter --partitions 1 --config "retention.ms=$DLQ_RETENTION_MS"

"$TOPICS_BIN" --bootstrap-server "$BOOTSTRAP" --list
