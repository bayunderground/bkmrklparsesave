#!/usr/bin/env bash
# bkmrklparsesave installer — idempotent, Ubuntu/Debian, rerun-safe.
set -euo pipefail

# --- settings (environment variables override every default) ---
DEPLOY_ENV="${DEPLOY_ENV:-}"          # required: dev | live (no default on purpose)
APP_DIR="${APP_DIR:-}"
DATA_DIR="${DATA_DIR:-}"
APP_HOST="127.0.0.1"
APP_PORT="${APP_PORT:-8765}"
NGINX_PORT="${NGINX_PORT:-8088}"
NGINX_BIND="${NGINX_BIND:-0.0.0.0}"
SERVICE_USER="${SERVICE_USER:-root}"
SERVER_NAME="${SERVER_NAME:-_}"
SERVICE_NAME="bkmrklparsesave.service"
SITE_CONF_REL="sites-available/bkmrklparsesave"   # or conf.d/bkmrklparsesave.conf
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

die() {
  echo "install.sh: ERROR: $*" >&2
  exit 1
}

step() {
  echo "==> $*"
}

require_root() {
  [ "$(id -u)" -eq 0 ] || die "must run as root (sudo)"
}

require_deploy_env() {
  case "${DEPLOY_ENV}" in
    dev)
      APP_DIR="${APP_DIR:-/var/www/mini/bkmrklparsesave}" ;;
    live)
      APP_DIR="${APP_DIR:-/root/bkmrklparsesave}" ;;
    "")
      die "DEPLOY_ENV is required: DEPLOY_ENV=dev or DEPLOY_ENV=live (refusing to guess an install dir)" ;;
    *)
      die "DEPLOY_ENV must be 'dev' or 'live', got '${DEPLOY_ENV}'" ;;
  esac
  DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
  # Resolve to absolute paths (APP_DIR may not exist yet).
  APP_DIR="$(mkdir -p "$APP_DIR" && cd "$APP_DIR" && pwd)"
  mkdir -p "$DATA_DIR"
  DATA_DIR="$(cd "$DATA_DIR" && pwd)"
}

ensure_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    step "installing python3"
    apt-get update -q
    apt-get install -y -q python3
  fi
  command -v python3 >/dev/null 2>&1 || die "python3 unavailable"
}

ensure_nginx() {
  if ! command -v nginx >/dev/null 2>&1; then
    step "installing nginx"
    apt-get update -q
    apt-get install -y -q nginx
  fi
  command -v nginx >/dev/null 2>&1 || die "nginx unavailable"
}

ensure_service_user() {
  [ "$SERVICE_USER" = "root" ] && return 0
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    step "creating service user ${SERVICE_USER}"
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
}

ensure_curl() {
  command -v curl >/dev/null 2>&1 || {
    step "installing curl"
    apt-get update -q
    apt-get install -y -q curl
  }
}

install_app() {
  step "installing server.py"
  local src="$SCRIPT_DIR/server.py" dst="$APP_DIR/server.py"
  [ -f "$src" ] || die "server.py not found next to install.sh"
  if [ "$(readlink -f "$src")" != "$(readlink -f "$dst" 2>/dev/null || echo "$dst")" ]; then
    install -m 644 "$src" "$dst"
  fi
  mkdir -p "$DATA_DIR"
  chmod 755 "$DATA_DIR"
}

install_systemd() {
  step "writing systemd unit ${SERVICE_NAME}"
  local unit="/etc/systemd/system/${SERVICE_NAME}"
  cat > "$unit" <<EOF
[Unit]
Description=bkmrklparsesave HTTP save server
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
Environment=BKMRKLPARSESAVE_HOST=${APP_HOST}
Environment=BKMRKLPARSESAVE_PORT=${APP_PORT}
Environment=BKMRKLPARSESAVE_DATA=${DATA_DIR}
ExecStart=$(command -v python3) ${APP_DIR}/server.py
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=${DATA_DIR}
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
  chmod 644 "$unit"
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME"
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
    die "service failed to start"
  }
}

check_local_health() {
  step "checking http://127.0.0.1:${APP_PORT}/health"
  local i
  for i in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
  die "server did not answer /health"
}

main() {
  require_root
  require_deploy_env
  ensure_python
  ensure_nginx
  ensure_curl
  ensure_service_user
  install_app
  install_systemd
  check_local_health
  echo "preflight OK: APP_DIR=$APP_DIR DATA_DIR=$DATA_DIR"
}

main "$@"
