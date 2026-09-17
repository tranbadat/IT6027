#!/usr/bin/env bash
# Tải cơ sở dữ liệu GeoIP miễn phí "IP to City Lite" của DB-IP (CC BY 4.0) vào data/geoip/ cho C3.
# Khi hiển thị dữ liệu vị trí cần ghi nguồn: "IP Geolocation by DB-IP" (https://db-ip.com).
# FORCE=1 để tải lại.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="$ROOT/data/geoip"
DEST="$DEST_DIR/dbip-city-lite.mmdb"
BASE_URL="https://download.db-ip.com/free"

if [[ -f "$DEST" && "${FORCE:-0}" != "1" ]]; then
  echo "Đã có $DEST (FORCE=1 để tải lại)."
  exit 0
fi

mkdir -p "$DEST_DIR"
tmp="$(mktemp "$DEST_DIR/.download.XXXXXX")"
trap 'rm -f "$tmp" "$DEST.partial"' EXIT

year="$(date -u +%Y)"
month="$(date -u +%m)"
month=$((10#$month))
prev_year="$year"
prev_month=$((month - 1))
if (( prev_month == 0 )); then
  prev_month=12
  prev_year=$((year - 1))
fi

# Bản của tháng hiện tại có thể chưa phát hành vào đầu tháng -> thử thêm tháng trước.
downloaded=""
for ym in "$(printf '%04d-%02d' "$year" "$month")" "$(printf '%04d-%02d' "$prev_year" "$prev_month")"; do
  url="${BASE_URL}/dbip-city-lite-${ym}.mmdb.gz"
  echo "Tải ${url} ..."
  if curl -fL --retry 2 --progress-bar -o "$tmp" "$url"; then
    downloaded="$ym"
    break
  fi
done

if [[ -z "$downloaded" ]]; then
  echo "Không tải được GeoIP DB. Có thể tự tải file .mmdb và đặt tại $DEST" >&2
  exit 1
fi

gunzip -c "$tmp" > "$DEST.partial"
mv "$DEST.partial" "$DEST"
echo "Đã lưu $DEST (bản ${downloaded})."
