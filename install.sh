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

main() {
  require_root
  require_deploy_env
  ensure_python
  ensure_nginx
  ensure_service_user
  echo "preflight OK: APP_DIR=$APP_DIR DATA_DIR=$DATA_DIR"
}

main "$@"
