from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
VERIFIER = ROOT / "scripts" / "verify_repository_security.sh"


def run_check(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable and arguments are repository constants
        [str(VERIFIER), "--check", name],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_repository_contains_no_tracked_secret_files() -> None:
    result = run_check("tracked-secret-files")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS tracked-secret-files" in result.stdout


def test_production_code_contains_no_tls_verification_bypass() -> None:
    result = run_check("tls-verification")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS tls-verification" in result.stdout


def test_production_code_contains_no_direct_uncontrolled_http_client() -> None:
    result = run_check("http-client-boundary")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS http-client-boundary" in result.stdout
