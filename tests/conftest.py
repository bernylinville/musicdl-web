from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json():
    def load(name: str) -> dict[str, Any]:
        with (FIXTURES / name).open(encoding="utf-8") as handle:
            return json.load(handle)

    return load
