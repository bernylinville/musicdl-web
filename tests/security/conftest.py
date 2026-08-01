from __future__ import annotations

import pytest
from musicdl_web.domain import Delivery, JobRequest, PlatformTrack, Quality
from musicdl_web.models import Source


@pytest.fixture
def job_request() -> JobRequest:
    return JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE,
            track_id="track-1",
            title="Song",
            artists=("Artist",),
        ),
        quality=Quality.LOSSLESS,
        quality_id="lossless",
        quality_snapshot_id="snapshot-1",
        delivery=Delivery.SERVER,
    )
