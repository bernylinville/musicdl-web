from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from musicdl_web.core import PipelineProcessor, PipelineResult
from musicdl_web.db import Repository
from musicdl_web.domain import (
    Delivery,
    JobRequest,
    PlatformTrack,
    Quality,
)
from musicdl_web.download import DownloadError
from musicdl_web.models import Source
from musicdl_web.platforms.quality import DownloadGrant


def request() -> JobRequest:
    return JobRequest(
        track=PlatformTrack(
            source=Source.NETEASE, track_id="42", title="Song", artists=("Artist",)
        ),
        quality=Quality.LOSSLESS,
        quality_id="lossless",
        quality_snapshot_id="snapshot-1234567890123456",
        delivery=Delivery.SERVER,
    )


class MismatchGateway:
    async def revalidate(self, request: JobRequest) -> DownloadGrant:
        return DownloadGrant(
            source=request.track.source,
            track_id=request.track.track_id,
            quality_id=request.quality_id,
            quality_rank=20,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            allowed_hosts=frozenset({"audio.example"}),
            _source_url="https://audio.example/song",
        )


class MustNotDownload:
    async def download(self, grant: DownloadGrant, target: Path) -> int:
        raise AssertionError("download must not start after an exact-quality mismatch")


class MustNotDeliver:
    def to_server(self, downloaded: Path, request: JobRequest) -> Path:
        raise AssertionError("delivery must not start")

    def to_browser(self, job_id: str, downloaded: Path, request: JobRequest) -> Path:
        raise AssertionError("delivery must not start")


class SuccessfulGateway:
    def __init__(self, cover: bytes | None) -> None:
        self.cover = cover

    async def revalidate(self, request: JobRequest) -> DownloadGrant:
        return DownloadGrant(
            source=request.track.source,
            track_id=request.track.track_id,
            quality_id=request.quality_id,
            quality_rank=int(request.quality),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            allowed_hosts=frozenset({"audio.example"}),
            _source_url="https://audio.example/song",
        )

    async def artwork(self, request: JobRequest) -> bytes | None:
        assert request.track.track_id == "42"
        return self.cover


class SuccessfulDownloader:
    async def download(self, grant: DownloadGrant, target: Path) -> int:
        await asyncio.to_thread(target.write_bytes, b"audio")
        return 5


class CoverCapturingDelivery:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.cover: bytes | None = None

    def to_server(
        self,
        downloaded: Path,
        request: JobRequest,
        *,
        cover: bytes | None = None,
    ) -> Path:
        assert downloaded.read_bytes() == b"audio"
        self.cover = cover
        return self.output

    def to_browser(
        self,
        job_id: str,
        downloaded: Path,
        request: JobRequest,
        *,
        cover: bytes | None = None,
    ) -> Path:
        raise AssertionError("browser delivery is outside this test")


async def test_pipeline_revalidates_exact_quality_before_download(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    job = repository.create_job(request())
    processor = PipelineProcessor(
        repository,
        MismatchGateway(),
        MustNotDownload(),  # type: ignore[arg-type]
        MustNotDeliver(),  # type: ignore[arg-type]
        tmp_path / "work",
    )

    with pytest.raises(DownloadError) as caught:
        await processor.process(job)

    assert caught.value.code == "quality_mismatch"


@pytest.mark.parametrize(
    ("cover", "warning"),
    [(b"jpeg-cover", None), (None, "音频成功，封面缺失")],
)
async def test_pipeline_passes_search_cover_to_delivery_and_warns_when_missing(
    tmp_path: Path,
    cover: bytes | None,
    warning: str | None,
) -> None:
    repository = Repository(tmp_path / "app.db")
    job = repository.create_job(request())
    delivery = CoverCapturingDelivery(tmp_path / "published.flac")
    processor = PipelineProcessor(
        repository,
        SuccessfulGateway(cover),
        SuccessfulDownloader(),  # type: ignore[arg-type]
        delivery,  # type: ignore[arg-type]
        tmp_path / "work",
    )

    result = await processor.process(job)

    assert result == PipelineResult(str(tmp_path / "published.flac"), warning)
    assert delivery.cover == cover
