#!/usr/bin/env bash
# Dựng toàn bộ hệ thống trên máy chủ Ubuntu thật bằng một lệnh: make server-up
#   1. cài gói còn thiếu (Nginx, Docker Engine + Compose plugin...);
#   2. dựng 3 stack Docker với ENV=production (+ observability nếu OBSERVABILITY=true);
#   3. cấu hình Nginx của máy chủ: site demo được giám sát, WAF_DOMAIN -> dashboard, MAIL_DOMAIN -> Mailpit,
#      IP thật của client qua Cloudflare, logrotate cho log của site demo;
#   4. thêm site demo vào scope và kiểm tra nhanh.
# Chạy lại an toàn (cập nhật cấu hình, build lại image đã đổi). Cấu hình: deploy/server/server.env
# (tạo từ server.env.example ở lần đầu). Hướng dẫn: docs/server-deployment.md
#
# Dùng: scripts/server-up.sh                 dựng/cập nhật toàn bộ
#       scripts/server-up.sh --print-nginx   chỉ in cấu hình Nginx sẽ cài (không thay đổi máy)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="$ROOT/deploy/server"
SERVER_ENV="$SERVER_DIR/server.env"
PLATFORM_ENV="$ROOT/deploy/platform/.env"

# Phải khớp với SENSOR_NGINX_LOG_DIR trong deploy/sensor/env/production.env.
SITE_LOG_DIR=/var/log/nginx/waf
SITE_WEB_ROOT=/var/www/waf-demo
NGINX_SITE=/etc/nginx/sites-available/waf.conf
MAIL_HTPASSWD=/etc/nginx/waf-mailpit.htpasswd
CLOUDFLARE_IPS_URL=https://www.cloudflare.com/ips
DOMAIN_RE='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'

SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"
CURRENT_USER="$(id -un)"

step() { printf '\n== %s\n' "$*"; }
die() { printf 'LỖI: %s\n' "$*" >&2; exit 1; }
random_secret() {
  local secret
  secret="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9')"
  printf '%s' "${secret:0:20}"
}

# Giá trị KEY trong file env (dòng cuối cùng), hoặc mặc định.
env_value() {
  local value
  value="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || true
  printf '%s' "${value:-$3}"
}

# Ghi KEY=VALUE vào file env, thay dòng cũ nếu có; giữ nguyên quyền và chủ sở hữu của file.
set_env() {
  local file=$1 key=$2 value=$3 tmp
  tmp="$(mktemp)"
  grep -v -E "^${key}=" "$file" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# Cấu hình từ server.env (hoặc server.env.example khi chỉ xem trước, chưa có server.env).
load_config() {
  local source_file=$SERVER_ENV
  [[ -f $source_file ]] || source_file="$SERVER_DIR/server.env.example"
  set -a
  # shellcheck source=/dev/null
  . "$source_file"
  set +a
  local var domain
  for var in WAF_DOMAIN MAIL_DOMAIN DEMO_DOMAINS DASHBOARD_USER MAIL_UI_USER; do
    [[ -n ${!var:-} ]] || die "thiếu $var trong deploy/server/server.env"
  done
  # Domain được chèn vào cấu hình Nginx: chỉ nhận ký tự hợp lệ của tên miền.
  for domain in $WAF_DOMAIN $MAIL_DOMAIN $DEMO_DOMAINS; do
    [[ $domain =~ $DOMAIN_RE ]] || die "domain không hợp lệ: '$domain'"
  done
  [[ $DASHBOARD_USER =~ ^[A-Za-z0-9_.-]+$ ]] || die "DASHBOARD_USER chỉ gồm chữ, số, . _ -"
}

# Tạo server.env ở lần đầu và sinh mật khẩu còn trống, ghi lại vào server.env.
ensure_server_env() {
  if [[ ! -f $SERVER_ENV ]]; then
    install -m 600 "$SERVER_DIR/server.env.example" "$SERVER_ENV"
    echo "Đã tạo deploy/server/server.env từ server.env.example"
  fi
  if [[ -z ${DASHBOARD_PASSWORD:-} ]]; then
    DASHBOARD_PASSWORD="$(random_secret)"
    set_env "$SERVER_ENV" DASHBOARD_PASSWORD "$DASHBOARD_PASSWORD"
  fi
  if [[ -z ${MAIL_UI_PASSWORD:-} ]]; then
    MAIL_UI_PASSWORD="$(random_secret)"
    set_env "$SERVER_ENV" MAIL_UI_PASSWORD "$MAIL_UI_PASSWORD"
  fi
  [[ $DASHBOARD_PASSWORD =~ ^[^,:[:space:]]+$ ]] || die "DASHBOARD_PASSWORD không được chứa dấu phẩy, hai chấm, khoảng trắng"
}

install_docker() {
  step "Cài Docker Engine + Compose plugin (kho apt chính thức của Docker)"
  # shellcheck source=/dev/null
  . /etc/os-release
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
       "https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
  $SUDO apt-get update -q
  $SUDO apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  if [[ $EUID -ne 0 ]]; then
    $SUDO usermod -aG docker "$CURRENT_USER"
  fi
}

install_packages() {
  command -v apt-get > /dev/null || die "server-up dành cho Ubuntu/Debian (cần apt-get)"
  local missing=() tool
  for tool in nginx curl openssl make python3; do
    command -v "$tool" > /dev/null || missing+=("$tool")
  done
  if ((${#missing[@]})); then
    step "Cài gói: ${missing[*]}"
    $SUDO apt-get update -q
    $SUDO apt-get install -y -q "${missing[@]}"
  fi
  if ! command -v docker > /dev/null || ! docker compose version > /dev/null 2>&1; then
    install_docker
  fi
}

# User vừa được thêm vào nhóm docker chưa có quyền trong phiên hiện tại: chạy lại script qua `sg docker`.
ensure_docker_access() {
  docker info > /dev/null 2>&1 && return
  if [[ $EUID -ne 0 && -z ${WAF_SERVER_UP_SG:-} ]] && getent group docker | grep -qw "$CURRENT_USER"; then
    export WAF_SERVER_UP_SG=1
    exec sg docker -c "$(printf '%q ' "$0" "$@")"
  fi
  die "không truy cập được Docker daemon. Kiểm tra 'sudo systemctl status docker' và nhóm docker của user."
}

prepare_stack_env() {
  step "Chuẩn bị .env cho các stack Docker"
  make -C "$ROOT" --no-print-directory net deploy-init
  local subnet demo_list
  subnet="$(docker network inspect waf-net -f '{{(index .IPAM.Config 0).Subnet}}')"
  # shellcheck disable=SC2086 # tách DEMO_DOMAINS theo dấu cách là chủ ý
  demo_list="$(printf '%s,' $DEMO_DOMAINS)"
  # Giá trị riêng của máy chủ; env/production.env không đặt các khoá này nên giá trị trong .env được dùng.
  set_env "$PLATFORM_ENV" GATEWAY_TRUSTED_PROXY "$subnet"
  set_env "$PLATFORM_ENV" AUTH_CLIENTS "$DASHBOARD_USER:$DASHBOARD_PASSWORD"
  set_env "$PLATFORM_ENV" SCOPE_ALLOWLIST "${demo_list%,}"
  set_env "$PLATFORM_ENV" DASHBOARD_LINKS "\"Mailpit (email cảnh báo)|${PUBLIC_SCHEME:-https}://$MAIL_DOMAIN\""
}

start_containers() {
  step "Dựng các stack Docker (ENV=production; lần đầu build image mất vài phút)"
  make -C "$ROOT" --no-print-directory deploy-up ENV=production
  if [[ ${OBSERVABILITY:-false} == true ]]; then
    step "Dựng stack quan sát (OBSERVABILITY=true)"
    make -C "$ROOT" --no-print-directory obs-up
  else
    make -C "$ROOT" --no-print-directory obs-down > /dev/null 2>&1 || true
  fi
}

# Dải IP của Cloudflare: tải bản mới nhất, không được thì dùng bản dự phòng trong repo.
cloudflare_realip() {
  echo "# WAF Log Analyzer: sinh bởi scripts/server-up.sh. IP thật của client khi đi qua Cloudflare."
  if [[ ${BEHIND_CLOUDFLARE:-true} != true ]]; then
    echo "# BEHIND_CLOUDFLARE=false: không dùng."
    return
  fi
  local ranges
  ranges="$(curl -fsS --max-time 10 "$CLOUDFLARE_IPS_URL-v4" && echo && curl -fsS --max-time 10 "$CLOUDFLARE_IPS_URL-v6")" \
    || ranges=""
  if ! grep -qE '^[0-9a-fA-F:.]+/[0-9]+$' <<< "$ranges"; then
    echo "# Không tải được danh sách mới: dùng deploy/server/nginx/cloudflare-ips.txt"
    ranges="$(cat "$SERVER_DIR/nginx/cloudflare-ips.txt")"
  fi
  grep -E '^[0-9a-fA-F:.]+/[0-9]+$' <<< "$ranges" | sed 's/^/set_real_ip_from /; s/$/;/'
  echo "real_ip_header CF-Connecting-IP;"
}

render_sites() {
  local gateway_port mailpit_port domain
  gateway_port="$(env_value "$PLATFORM_ENV" GATEWAY_PORT 8080)"
  mailpit_port="$(env_value "$PLATFORM_ENV" MAILPIT_UI_PORT 8025)"
  [[ $gateway_port =~ ^[0-9]+$ && $mailpit_port =~ ^[0-9]+$ ]] || die "GATEWAY_PORT/MAILPIT_UI_PORT phải là số"
  sed -e "s#@@WAF_DOMAIN@@#$WAF_DOMAIN#g" -e "s#@@MAIL_DOMAIN@@#$MAIL_DOMAIN#g" \
      -e "s#@@GATEWAY_PORT@@#$gateway_port#g" -e "s#@@MAILPIT_PORT@@#$mailpit_port#g" \
      -e "s#@@MAIL_HTPASSWD@@#$MAIL_HTPASSWD#g" \
      "$SERVER_DIR/nginx/waf-platform.conf.template"
  for domain in $DEMO_DOMAINS; do
    sed -e "s#@@DOMAIN@@#$domain#g" -e "s#@@SITE_LOG_DIR@@#$SITE_LOG_DIR#g" \
        -e "s#@@SITE_WEB_ROOT@@#$SITE_WEB_ROOT#g" "$SERVER_DIR/nginx/waf-site.conf.template"
  done
}

print_nginx() {
  echo "# ---- /etc/nginx/conf.d/waf-logformat.conf"
  cat "$SERVER_DIR/nginx/waf-logformat.conf"
  echo "# ---- /etc/nginx/conf.d/waf-cloudflare-realip.conf"
  cloudflare_realip
  echo "# ---- $NGINX_SITE"
  render_sites
}

install_demo_sites() {
  local domain
  for domain in $DEMO_DOMAINS; do
    $SUDO install -d -m 0755 "$SITE_WEB_ROOT/$domain"
    $SUDO cp -r "$ROOT/infra/web-nginx/html/lab/." "$SITE_WEB_ROOT/$domain/"
    sed -e "s#Lab shop#$domain#g" -e "s#Trang thí nghiệm cho experiments/.#Site demo được WAF Log Analyzer giám sát.#" \
        "$ROOT/infra/web-nginx/html/lab/index.html" | $SUDO tee "$SITE_WEB_ROOT/$domain/index.html" > /dev/null
  done
}

configure_nginx() {
  step "Cấu hình Nginx của máy chủ"
  $SUDO install -d -m 0750 -o root -g adm "$SITE_LOG_DIR"
  $SUDO install -m 0644 "$SERVER_DIR/nginx/waf-logformat.conf" /etc/nginx/conf.d/waf-logformat.conf
  cloudflare_realip | $SUDO tee /etc/nginx/conf.d/waf-cloudflare-realip.conf > /dev/null
  render_sites | $SUDO tee "$NGINX_SITE" > /dev/null
  $SUDO ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/waf.conf

  printf '%s:%s\n' "$MAIL_UI_USER" "$(openssl passwd -6 "$MAIL_UI_PASSWORD")" | $SUDO tee "$MAIL_HTPASSWD" > /dev/null
  $SUDO chown root:www-data "$MAIL_HTPASSWD"
  $SUDO chmod 0640 "$MAIL_HTPASSWD"

  install_demo_sites
  sed "s#@@SITE_LOG_DIR@@#$SITE_LOG_DIR#g" "$SERVER_DIR/logrotate/waf-sites.template" \
    | $SUDO tee /etc/logrotate.d/waf-sites > /dev/null

  $SUDO nginx -t
  $SUDO systemctl enable --now nginx
  $SUDO systemctl reload nginx
}

gateway_url() { printf 'http://127.0.0.1:%s' "$(env_value "$PLATFORM_ENV" GATEWAY_PORT 8080)"; }

# Thêm site demo vào scope qua API (P1 upsert nên chạy lại không sao), kể cả khi DB đã có dữ liệu cũ.
ensure_scope() {
  step "Thêm site demo vào scope (P1)"
  local token domain credentials
  credentials="$(python3 -c 'import json,sys; print(json.dumps({"client_id": sys.argv[1], "client_secret": sys.argv[2]}))' \
    "$DASHBOARD_USER" "$DASHBOARD_PASSWORD")"
  token="$(curl -fsS -X POST -H 'content-type: application/json' -d "$credentials" "$(gateway_url)/api/auth/token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')" \
    || die "không lấy được token từ gateway $(gateway_url)"
  for domain in $DEMO_DOMAINS; do
    curl -fsS -o /dev/null -X POST -H "authorization: Bearer $token" -H 'content-type: application/json' \
      -d "{\"target\":\"$domain\",\"target_type\":\"domain\",\"note\":\"server-up\"}" \
      "$(gateway_url)/api/scope/allowlist" || die "không thêm được $domain vào scope"
    echo "  $domain: trong scope"
  done
}

check() {
  local label=$1 expected=$2 code
  shift 2
  code="$(curl -s -o /dev/null -w '%{http_code}' "$@")" || true
  if [[ $code == "$expected" ]]; then
    echo "  OK    $label (HTTP $code)"
  else
    echo "  LỖI   $label: HTTP $code, cần $expected"
  fi
}

verify() {
  step "Kiểm tra nhanh qua Nginx của máy chủ"
  local first
  read -r first _ <<< "$DEMO_DOMAINS"
  check "site demo $first" 200 -H "Host: $first" http://127.0.0.1/
  check "dashboard $WAF_DOMAIN" 200 -H "Host: $WAF_DOMAIN" http://127.0.0.1/healthz
  check "Mailpit $MAIL_DOMAIN yêu cầu đăng nhập" 401 -H "Host: $MAIL_DOMAIN" http://127.0.0.1/
  check "Mailpit $MAIL_DOMAIN với tài khoản" 200 -u "$MAIL_UI_USER:$MAIL_UI_PASSWORD" -H "Host: $MAIL_DOMAIN" http://127.0.0.1/
  if $SUDO test -s "$SITE_LOG_DIR/$first.access.log"; then
    echo "  OK    log của $first được ghi vào $SITE_LOG_DIR (C1 đọc thư mục này)"
  else
    echo "  LỖI   chưa thấy log $SITE_LOG_DIR/$first.access.log"
  fi
}

summary() {
  local scheme=${PUBLIC_SCHEME:-https} domain
  step "Xong"
  echo "Dashboard : $scheme://$WAF_DOMAIN   (đăng nhập: $DASHBOARD_USER / $DASHBOARD_PASSWORD)"
  echo "Mailpit   : $scheme://$MAIL_DOMAIN  (đăng nhập: $MAIL_UI_USER / $MAIL_UI_PASSWORD)"
  for domain in $DEMO_DOMAINS; do
    echo "Site demo : $scheme://$domain"
  done
  echo "Observability: ${OBSERVABILITY:-false}. Mật khẩu lưu trong deploy/server/server.env (không commit)."
  echo "DNS Cloudflare: bản ghi A (Proxied) cho các domain trên trỏ về IP máy chủ, SSL/TLS = Flexible."
  echo "Chi tiết: docs/server-deployment.md"
}

main() {
  load_config
  if [[ ${1:-} == --print-nginx ]]; then
    print_nginx
    return
  fi
  install_packages
  ensure_server_env
  ensure_docker_access "$@"
  prepare_stack_env
  # Nginx trước: thư mục log phải có đúng quyền trước khi container C1 bind-mount nó.
  configure_nginx
  start_containers
  ensure_scope
  verify
  summary
}

main "$@"
