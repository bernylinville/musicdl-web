#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
status=0

run() {
  local name="$1"
  shift
  if "$@"; then
    echo "PASS $name"
  else
    echo "FAIL $name" >&2
    status=1
  fi
}

run "repository-security" "$ROOT/scripts/verify_repository_security.sh"
run "runtime-dependencies" "$ROOT/scripts/verify_runtime_dependencies.sh"
run "backend-tests" "$ROOT/.venv/bin/pytest" -q
run "backend-lint" "$ROOT/.venv/bin/ruff" check backend tests
run "backend-types" "$ROOT/.venv/bin/mypy" backend/src
run "root-compose-contract" "$ROOT/scripts/verify_compose_contract.sh" "$ROOT/compose.yaml"
run "nas-compose-contract" "$ROOT/scripts/verify_compose_contract.sh" "$ROOT/deploy/nas/compose.yaml"

if [[ -f "$ROOT/frontend/package.json" ]]; then
  if command -v pnpm >/dev/null 2>&1; then
    run "frontend-tests" pnpm --dir frontend test
    run "frontend-types" pnpm --dir frontend typecheck
    run "frontend-lint" pnpm --dir frontend lint
    run "frontend-build" pnpm --dir frontend build
  else
    echo "BLOCKED frontend-tests: pnpm is unavailable" >&2
    echo "BLOCKED frontend-types: pnpm is unavailable" >&2
    echo "BLOCKED frontend-lint: pnpm is unavailable" >&2
    echo "BLOCKED frontend-build: pnpm is unavailable" >&2
    status=1
  fi
else
  echo "BLOCKED frontend-contract: frontend/package.json does not exist" >&2
  status=1
fi

exit "$status"
