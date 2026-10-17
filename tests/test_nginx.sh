#!/usr/bin/env bash
# Proxy test through the installed NGINX site (needs: sudo DEPLOY_ENV=dev bash install.sh).
set -euo pipefail

NGINX_PORT="${NGINX_PORT:-8088}"
BASE="${BKMRKL_TEST_BASE:-http://127.0.0.1:${NGINX_PORT}}"
SERVICE_NAME="bkmrklparsesave.service"

fail() {
  echo "test_nginx.sh: FAIL: $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl not found"

# Data dir of the installed service — used to reset the test rule's file.
DATA_DIR=$(systemctl show -p Environment "$SERVICE_NAME" 2>/dev/null \
  | sed -n 's/.*BKMRKLPARSESAVE_DATA=\([^ "]*\).*/\1/p' || true)
if [ -z "$DATA_DIR" ]; then
  fail "service $SERVICE_NAME not installed (run install.sh first)"
fi
TEST_FILE="${DATA_DIR}/nginxtest.md"

cleanup() {
  rm -f "$TEST_FILE"
}
trap cleanup EXIT
cleanup

json_field() {
  # json_field <field> <<< "$response"
  sed -n 's/.*"'"$1"'": *\([0-9a-zA-Z"]*\).*/\1/p'
}

echo "1. GET /health through the proxy"
HEALTH=$(curl -fsS --max-time 5 "${BASE}/health") || fail "proxy did not answer /health"
printf '%s' "$HEALTH" | grep -q '"ok": *true' || fail "unexpected /health body: $HEALTH"

echo "2. POST /save through the proxy"
RESP=$(curl -fsS --max-time 5 -X POST "${BASE}/save" \
  -H 'Content-Type: application/json' \
  -d '{"rule":"nginxtest","items":[{"fields":{"Title":"Proxy Test Item","Price":1}}]}') \
  || fail "save through proxy failed"
printf '%s' "$RESP" | grep -q '"ok": *true' || fail "save not ok: $RESP"
SAVED=$(printf '%s' "$RESP" | json_field saved)
[ "$SAVED" = "1" ] || fail "expected saved=1, got: $RESP"
[ -f "$TEST_FILE" ] || fail "$TEST_FILE was not created"

echo "3. duplicate POST is skipped"
RESP2=$(curl -fsS --max-time 5 -X POST "${BASE}/save" \
  -H 'Content-Type: application/json' \
  -d '{"rule":"nginxtest","items":[{"fields":{"Title":"Proxy Test Item","Price":1}}]}') \
  || fail "duplicate save through proxy failed"
printf '%s' "$RESP2" | grep -q '"ok": *true' || fail "duplicate save not ok: $RESP2"
SAVED2=$(printf '%s' "$RESP2" | json_field saved)
SKIPPED2=$(printf '%s' "$RESP2" | json_field skipped)
[ "$SAVED2" = "0" ] || fail "expected saved=0, got: $RESP2"
[ "$SKIPPED2" = "1" ] || fail "expected skipped=1, got: $RESP2"

echo "test_nginx.sh: OK (health, save, dedupe through NGINX :${NGINX_PORT})"
