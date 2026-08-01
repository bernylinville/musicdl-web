#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="all"

if [[ "${1:-}" == "--check" && -n "${2:-}" ]]; then
  CHECK="$2"
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--check tracked-secret-files|tls-verification|http-client-boundary]" >&2
  exit 2
fi

status=0

pass() {
  echo "PASS $1"
}

fail() {
  echo "FAIL $1: $2" >&2
  status=1
}

check_tracked_secret_files() {
  local matches
  matches="$({
    git -C "$ROOT" ls-files
    git -C "$ROOT" ls-files --others --exclude-standard
  } | sort -u | awk '
    /(^|\/)\.env($|\.)/ && !/\.env\.example$/ { print; next }
    /(^|\/)(session\.key|cookies?\.txt|credentials?\.json)$/ { print; next }
    /\.(sqlite|sqlite3|db|pem|p12|pfx)$/ { print }
  ')"
  if [[ -n "$matches" ]]; then
    fail "tracked-secret-files" "credential or runtime-state filename found"
    printf '%s\n' "$matches" >&2
    return
  fi
  pass "tracked-secret-files"
}

production_roots() {
  local path
  for path in "$ROOT/backend/src" "$ROOT/frontend/src"; do
    [[ -d "$path" ]] && printf '%s\n' "$path"
  done
}

check_tls_verification() {
  local roots matches
  mapfile -t roots < <(production_roots)
  if [[ ${#roots[@]} -eq 0 ]]; then
    fail "tls-verification" "no production source roots found"
    return
  fi
  matches="$(rg -n --glob '*.{py,js,jsx,ts,tsx,vue}' \
    'verify\s*=\s*False|NODE_TLS_REJECT_UNAUTHORIZED|rejectUnauthorized\s*:\s*false|CERT_NONE|check_hostname\s*=\s*False' \
    "${roots[@]}" 2>/dev/null || true)"
  if [[ -n "$matches" ]]; then
    fail "tls-verification" "TLS verification bypass found"
    printf '%s\n' "$matches" >&2
    return
  fi
  pass "tls-verification"
}

check_http_client_boundary() {
  local candidates matches file
  candidates="$(rg -l --glob '*.{py,js,jsx,ts,tsx,vue}' \
    'httpx\.(AsyncClient|Client|request|get|post|put|patch|delete|stream)|requests\.|aiohttp\.|urllib\.request' \
    "$ROOT/backend/src" "$ROOT/frontend/src" 2>/dev/null || true)"
  matches=""
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    case "$file" in
      "$ROOT/backend/src/musicdl_web/network.py") ;;
      "$ROOT/backend/src/musicdl_web/download/service.py") ;;
      "$ROOT/frontend/src/"*) ;;
      *) matches+="${file#"$ROOT/"}"$'\n' ;;
    esac
  done <<< "$candidates"
  matches="${matches%$'\n'}"
  if [[ -n "$matches" ]]; then
    fail "http-client-boundary" "direct production HTTP client found outside the controlled network boundary"
    printf '%s\n' "$matches" >&2
    return
  fi
  pass "http-client-boundary"
}

case "$CHECK" in
  all)
    check_tracked_secret_files
    check_tls_verification
    check_http_client_boundary
    ;;
  tracked-secret-files) check_tracked_secret_files ;;
  tls-verification) check_tls_verification ;;
  http-client-boundary) check_http_client_boundary ;;
  *)
    echo "unknown check: $CHECK" >&2
    exit 2
    ;;
esac

exit "$status"
