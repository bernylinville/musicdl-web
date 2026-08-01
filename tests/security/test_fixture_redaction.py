from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
FORBIDDEN_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "download_url",
    "downloadurl",
    "qr_content",
    "refresh_token",
    "source_url",
    "token",
}


def walk(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key).lower(), nested
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def test_platform_fixtures_contain_no_secret_or_download_url_fields() -> None:
    violations: list[str] = []
    for path in sorted(FIXTURE_ROOT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, _ in walk(payload):
            if key in FORBIDDEN_KEYS:
                violations.append(f"{path.name}:{key}")

    assert violations == []


def test_platform_fixture_urls_contain_no_query_credentials() -> None:
    violations: list[str] = []
    for path in sorted(FIXTURE_ROOT.glob("*.json")):
        text = path.read_text(encoding="utf-8").lower()
        for marker in ("token=", "key=", "auth=", "cookie="):
            if marker in text:
                violations.append(f"{path.name}:{marker}")

    assert violations == []
