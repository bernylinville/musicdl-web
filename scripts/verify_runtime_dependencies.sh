#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
status=0

check() {
  local name="$1"
  local pattern="$2"
  local file="$3"
  local reason="$4"
  if rg -q "$pattern" "$ROOT/$file"; then
    echo "PASS $name"
  else
    echo "FAIL $name: $reason" >&2
    status=1
  fi
}

check "authenticated-encryption-dependency" '"cryptography[><=]' pyproject.toml \
  "cryptography must be a production dependency"
check "media-tagging-dependency" '"mutagen[><=]' pyproject.toml \
  "mutagen must be a production dependency"
check "media-probe-runtime" 'ffmpeg|ffprobe' Dockerfile \
  "the runtime image must install ffprobe"

exit "$status"
