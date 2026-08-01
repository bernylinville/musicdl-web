from __future__ import annotations

from pathlib import Path

import pytest
from musicdl_web.db import Repository
from musicdl_web.domain import Delivery, JobRequest, PlatformTrack, Quality
from musicdl_web.models import Source


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "state.sqlite3")


@pytest.fixture
def job_request() -> JobRequest:
    return JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE,
            track_id="track-1",
            title="Song",
            artists=("Artist",),
            album="Album",
            album_artist="Album Artist",
            year=2026,
            disc=1,
            track_number=2,
        ),
        quality=Quality.LOSSLESS,
        quality_id="lossless",
        quality_snapshot_id="snapshot-1",
        session_version=7,
        delivery=Delivery.SERVER,
    )
