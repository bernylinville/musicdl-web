#!/usr/bin/env bash
set -uo pipefail

NAS_HOST="${1:-192.168.50.10}"
APP_DIR="${MUSICDL_WEB_NAS_DIR:-/data/docker/musicdl-web}"
status=0

pass() {
  echo "PASS $1"
}

fail() {
  echo "FAIL $1: $2" >&2
  status=1
}

blocked() {
  echo "BLOCKED $1: $2" >&2
  status=1
}

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$NAS_HOST" "$1"
}

if ! remote "true" >/dev/null 2>&1; then
  blocked "nas-connection" "non-interactive SSH is unavailable"
  exit "$status"
fi
pass "nas-connection"

if [[ "$(remote "curl -fsS --max-time 5 http://127.0.0.1:4534/healthz >/dev/null && printf healthy" 2>/dev/null)" == "healthy" ]]; then
  pass "nas-healthz"
else
  fail "nas-healthz" "container health endpoint is unavailable on port 4534"
fi

inspect="$(remote "docker inspect --format '{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{.Config.Image}}' musicdl-web" 2>/dev/null || true)"
IFS='|' read -r run_user read_only health_status image_name <<< "$inspect"
[[ "$run_user" == "1000:1000" ]] && pass "nas-container-user" || fail "nas-container-user" "expected UID/GID 1000:1000"
[[ "$read_only" == "true" ]] && pass "nas-read-only-root" || fail "nas-read-only-root" "root filesystem is not read-only"
[[ "$health_status" == "healthy" ]] && pass "nas-container-health" || fail "nas-container-health" "Docker health status is not healthy"
[[ -n "$image_name" ]] && pass "nas-image-present" || fail "nas-image-present" "container image could not be identified"

service_count="$(remote "cd '$APP_DIR' && docker compose config --services | wc -l" 2>/dev/null || true)"
[[ "$service_count" == "1" ]] && pass "nas-single-container" || fail "nas-single-container" "Compose must define one service"

music_rw="$(remote "docker inspect --format '{{range .Mounts}}{{if eq .Destination \"/music\"}}{{.RW}}{{end}}{{end}}' musicdl-web" 2>/dev/null || true)"
[[ "$music_rw" == "true" ]] && pass "nas-music-library-writable" || fail "nas-music-library-writable" "/music is not writable"

secret_hits="$(remote "docker logs --tail 500 musicdl-web 2>&1 | grep -Eic 'cookie[[:space:]]*[:=]|authorization[[:space:]]*[:=]|token[[:space:]]*[:=]|data:image|https?://[^[:space:]]*[?&](token|key|auth)=' || true" 2>/dev/null || true)"
[[ "$secret_hits" == "0" ]] && pass "nas-log-redaction" || fail "nas-log-redaction" "recent logs contain secret-shaped material"

release_count="$(remote "find '$APP_DIR/releases' -maxdepth 1 -type f -name 'musicdl-web_*_linux-amd64.tar' 2>/dev/null | wc -l" 2>/dev/null || true)"
if [[ "$release_count" =~ ^[0-9]+$ ]] && (( release_count >= 1 )); then
  pass "nas-rollback-artifact"
else
  fail "nas-rollback-artifact" "no linux/amd64 image archive is retained"
fi

blocked "nas-restart-recovery" "requires an explicit restart drill with sessions and queued tasks present"
blocked "navidrome-discovery" "requires a legal account download and watcher/scan observation"

exit "$status"
