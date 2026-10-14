#!/usr/bin/env bash
# Chạy toàn bộ thực nghiệm trên hệ thống ĐANG CHẠY (bản 3 tầng `make deploy-up` hoặc `make up`):
#   1. nạp vhost lab.local cho Nginx mục tiêu, thêm lab.local vào scope (P1);
#   2. sinh lưu lượng có nhãn qua Nginx -> pipeline thật (detector cấu hình C xử lý trực tiếp);
#   3. xuất log.enriched + kết quả detector từ Kafka, phát lại offline 3 cấu hình A/B/C;
#   4. gửi cùng tập request qua ModSecurity + OWASP CRS (DetectionOnly) làm mốc so sánh;
#   5. tính chỉ số -> experiments/results/summary.{json,md}.
# Dùng: experiments/run.sh [--skip-modsec]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="$ROOT/experiments/results"
GATEWAY="http://127.0.0.1:${GATEWAY_PORT:-8080}"
TARGET="http://127.0.0.1:${WEB_NGINX_PORT:-8081}"
RUN="lab-$(date +%s)"
SEED="${SEED:-42}"
mkdir -p "$RESULTS"

container() { docker ps -q --filter "label=com.docker.compose.service=$1" | head -1; }
KAFKA="$(container kafka)"
NGINX="$(container web-nginx)"
[[ -n "$KAFKA" && -n "$NGINX" ]] || { echo "Cần hệ thống đang chạy (make deploy-up hoặc make up)." >&2; exit 2; }

echo "== 1. Chuẩn bị lab.local (run=$RUN)"
docker exec "$NGINX" nginx -s reload
token="$(curl -s -X POST -H 'content-type: application/json' -d '{"client_id":"lab","client_secret":"lab"}' \
  "$GATEWAY/api/auth/token" | jq -r '.access_token // empty')"
curl -s -o /dev/null -w "scope allowlist -> HTTP %{http_code}\n" -X POST -H "authorization: Bearer $token" \
  -H 'content-type: application/json' -d '{"target":"lab.local","note":"experiments"}' "$GATEWAY/api/scope/allowlist"

echo "== 2. Sinh lưu lượng có nhãn qua Nginx"
python3 "$ROOT/experiments/traffic.py" --target "$TARGET" --run "$RUN" --seed "$SEED" --labels "$RESULTS/labels.jsonl"
echo "chờ pipeline xử lý hết..."; sleep 20

echo "== 3. Xuất từ Kafka và phát lại offline A/B/C"
consume() {
  docker exec "$KAFKA" /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:19092 \
    --topic "$1" --from-beginning --timeout-ms 15000 2>/dev/null | grep -E "\"session_id\": ?\"$RUN-" || true
}
consume log.enriched > "$RESULTS/enriched.jsonl"
: > "$RESULTS/live.jsonl"
for topic in attack.detected anomaly.scored alert.triggered; do
  consume "$topic" | jq -c --arg t "$topic" '{config: "live", topic: $t, envelope: .}' >> "$RESULTS/live.jsonl"
done
echo "log.enriched của lượt chạy: $(wc -l < "$RESULTS/enriched.jsonl") envelope"
docker run --rm -v "$RESULTS:/data" waf/detector:dev \
  java -cp /app/app.jar com.example.waf.detector.eval.Evaluate /data/enriched.jsonl /data

if [[ "${1:-}" != "--skip-modsec" ]]; then
  echo "== 4. Mốc so sánh ModSecurity + OWASP CRS"
  compose=(docker compose -f "$ROOT/experiments/modsec/docker-compose.yml")
  "${compose[@]}" up -d >/dev/null
  for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8088/ && break; sleep 2; done
  python3 "$ROOT/experiments/traffic.py" --target http://127.0.0.1:8088 --run "$RUN" --seed "$SEED" \
    --req-id-header X-Req-Id --labels "$RESULTS/labels-modsec.jsonl"
  sleep 5
  "${compose[@]}" logs --no-log-prefix modsec 2>/dev/null | python3 "$ROOT/experiments/modsec_eval.py" > "$RESULTS/modsec.jsonl"
  "${compose[@]}" down >/dev/null
fi

echo "== 5. Chỉ số"
python3 "$ROOT/experiments/evaluate.py" "$RESULTS"
