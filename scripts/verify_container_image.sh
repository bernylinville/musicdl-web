#!/usr/bin/env bash
set -uo pipefail

IMAGE="${1:-}"
if [[ -z "$IMAGE" ]]; then
  echo "usage: $0 <image>" >&2
  exit 2
fi

status=0
name="musicdl-web-verify-$$"
secret_dir="$(mktemp -d)"

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$secret_dir"
}
trap cleanup EXIT

pass() {
  echo "PASS $1"
}

fail() {
  echo "FAIL $1: $2" >&2
  status=1
}

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  fail "container-image" "image is unavailable"
  exit "$status"
fi
pass "container-image"

architecture="$(docker image inspect --format '{{.Architecture}}' "$IMAGE")"
[[ "$architecture" == "amd64" ]] && pass "image-architecture" \
  || fail "image-architecture" "expected linux/amd64 image"

image_user="$(docker image inspect --format '{{.Config.User}}' "$IMAGE")"
[[ "$image_user" == "1000:1000" ]] && pass "image-user" \
  || fail "image-user" "image must default to UID/GID 1000:1000"

if docker image inspect --format '{{json .Config.Healthcheck.Test}}' "$IMAGE" \
  | rg -q '127\.0\.0\.1:4534/healthz'; then
  pass "image-healthcheck"
else
  fail "image-healthcheck" "image healthcheck must probe port 4534"
fi

umask 077
head -c 32 /dev/urandom > "$secret_dir/session.key"
chmod 0444 "$secret_dir/session.key"

if ! docker run -d --rm \
  --name "$name" \
  --read-only \
  --user 1000:1000 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:size=16m,mode=1777 \
  --tmpfs /app/data:uid=1000,gid=1000,mode=0700 \
  --tmpfs /app/tmp:uid=1000,gid=1000,mode=0700 \
  --tmpfs /music:uid=1000,gid=1000,mode=0755 \
  --mount "type=bind,src=$secret_dir/session.key,dst=/run/secrets/session.key,readonly" \
  -p 127.0.0.1::4534 \
  "$IMAGE" >/dev/null; then
  fail "container-start" "container could not start with the production security boundary"
  exit "$status"
fi
pass "container-start"

port="$(docker inspect --format '{{(index (index .NetworkSettings.Ports "4534/tcp") 0).HostPort}}' "$name")"
healthy=false
for _ in {1..40}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 0.25
done
[[ "$healthy" == "true" ]] && pass "container-healthz" \
  || fail "container-healthz" "health endpoint did not become ready"

root_body="$(curl -fsS --max-time 5 "http://127.0.0.1:$port/" 2>/dev/null || true)"
if [[ "$root_body" == *"下载工作台"* ]] \
  && [[ "$root_body" != *"当前仅提供搜索 spike"* ]]; then
  pass "container-workbench"
else
  fail "container-workbench" "root page is not the built download workbench"
fi

sessions_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  "http://127.0.0.1:$port/api/v1/sessions" 2>/dev/null || true)"
[[ "$sessions_status" == "200" ]] && pass "container-session-api" \
  || fail "container-session-api" "default session API did not return 200"

if docker exec "$name" sh -c 'command -v ffprobe >/dev/null && python -c "import mutagen"'; then
  pass "container-media-runtime"
else
  fail "container-media-runtime" "ffprobe or mutagen is unavailable"
fi

secret_hits="$(docker logs --tail 500 "$name" 2>&1 \
  | grep -Eic 'cookie[[:space:]]*[:=]|authorization[[:space:]]*[:=]|token[[:space:]]*[:=]|data:image|https?://[^[:space:]]*[?&](token|key|auth)=' \
  || true)"
[[ "$secret_hits" == "0" ]] && pass "container-log-redaction" \
  || fail "container-log-redaction" "container logs contain secret-shaped material"

exit "$status"
