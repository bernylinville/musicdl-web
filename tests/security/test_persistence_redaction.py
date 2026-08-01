from __future__ import annotations

import sqlite3
from pathlib import Path

from musicdl_web.db import Repository
from musicdl_web.domain import JobRequest


def test_sqlite_job_payload_contains_no_url_cookie_or_token(
    tmp_path: Path, job_request: JobRequest
) -> None:
    database = tmp_path / "state.sqlite3"
    repository = Repository(database)
    repository.create_job(job_request)

    with sqlite3.connect(database) as connection:
        payload = connection.execute("SELECT request_json FROM jobs").fetchone()[0]

    lowered = payload.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "cookie" not in lowered
    assert "token" not in lowered
    assert "secret" not in lowered
