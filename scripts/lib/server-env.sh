# shellcheck shell=bash
# Dùng chung cho scripts/server-up.sh và scripts/e2e-server.sh: đọc deploy/server/server.env.
# Biến đã có giá trị trong môi trường được ưu tiên, ví dụ xem trước cấu hình của chế độ khác:
#   ACCESS_MODE=direct-https scripts/server-up.sh --print-nginx

# load_server_env <file>: gán KEY=VALUE (bỏ dấu " bao ngoài), rồi suy ra PUBLIC_SCHEME từ ACCESS_MODE.
# shellcheck disable=SC2034 # PUBLIC_SCHEME dùng ở script gọi
load_server_env() {
  local file=$1 key value
  while IFS='=' read -r key value || [[ -n $key ]]; do
    [[ $key =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    [[ -n ${!key:-} ]] && continue
    value="${value%\"}"
    value="${value#\"}"
    printf -v "$key" '%s' "$value"
  done < "$file"
  ACCESS_MODE=${ACCESS_MODE:-cloudflare}
  case $ACCESS_MODE in
    cloudflare | direct-https) PUBLIC_SCHEME=https ;;
    direct-http) PUBLIC_SCHEME=http ;;
    *)
      printf 'LỖI: ACCESS_MODE=%s không hợp lệ (cloudflare | direct-https | direct-http)\n' "$ACCESS_MODE" >&2
      exit 2
      ;;
  esac
}
