#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${1:-$ROOT/compose.yaml}"
status=0

pass() {
  echo "PASS $1"
}

fail() {
  echo "FAIL $1: $2" >&2
  status=1
}

if ! config="$(docker compose -f "$COMPOSE_FILE" config --format json 2>&1)"; then
  fail "compose-config" "docker compose config rejected ${COMPOSE_FILE#"$ROOT/"}"
  exit "$status"
fi
pass "compose-config"

service_count="$(jq '.services | length' <<< "$config")"
if [[ "$service_count" == "1" ]] && jq -e '.services["musicdl-web"]' <<< "$config" >/dev/null; then
  pass "single-container"
else
  fail "single-container" "expected exactly one musicdl-web service"
fi

service="$(jq '.services["musicdl-web"]' <<< "$config")"

assert_jq() {
  local name="$1"
  local expression="$2"
  local reason="$3"
  if jq -e "$expression" <<< "$service" >/dev/null; then
    pass "$name"
  else
    fail "$name" "$reason"
  fi
}

assert_jq "container-user" '.user == "1000:1000"' "container must run as UID/GID 1000:1000"
assert_jq "container-platform" '.platform == "linux/amd64"' "platform must be fixed to linux/amd64"
assert_jq "read-only-root" '.read_only == true' "root filesystem must be read-only"
assert_jq "restart-policy" '.restart == "unless-stopped"' "restart must be unless-stopped"
assert_jq "published-port" '[.ports[]? | select(.target == 4534 and .published == "4534")] | length == 1' "expected 4534:4534"
assert_jq "log-rotation" '.logging.driver == "json-file" and .logging.options["max-size"] == "10m" and .logging.options["max-file"] == "3"' "expected json-file 10m x3"
assert_jq "data-volume" '[.volumes[]? | select(.target == "/app/data" and .type == "bind" and .read_only != true)] | length == 1' "/app/data must be a writable bind mount"
assert_jq "temporary-volume" '[.volumes[]? | select(.target == "/app/tmp" and .type == "bind" and .read_only != true)] | length == 1' "/app/tmp must be a writable bind mount"
assert_jq "session-key-volume" '[.volumes[]? | select(.target == "/run/secrets/session.key" and .type == "bind" and .read_only == true)] | length == 1' "session key must be a read-only bind mount"
assert_jq "music-library-volume" '[.volumes[]? | select(.target == "/music" and .type == "bind" and .read_only != true)] | length == 1' "/music must be writable for atomic publication"
assert_jq "dropped-capabilities" '(.cap_drop // []) | index("ALL") != null' "all Linux capabilities must be dropped"
assert_jq "no-new-privileges" '(.security_opt // []) | index("no-new-privileges:true") != null' "no-new-privileges must be enabled"

if rg -q 'HEALTHCHECK.*' "$ROOT/Dockerfile" \
  && rg -q "127\.0\.0\.1:4534/healthz" "$ROOT/Dockerfile"; then
  pass "healthcheck-4534"
else
  fail "healthcheck-4534" "Dockerfile healthcheck must probe the container port 4534"
fi

exit "$status"
