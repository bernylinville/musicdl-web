from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from musicdl_web.core import PipelineProcessor
from musicdl_web.domain import JobRequest
from musicdl_web.models import Source
from musicdl_web.platforms import DownloadGrant


class CorrectGateway:
    async def revalidate(self, request: JobRequest) -> DownloadGrant:
        return DownloadGrant(
            source=request.track.source,
            track_id=request.track.track_id,
            quality_id=request.quality_id,
            quality_rank=int(request.quality),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            allowed_hosts=frozenset({"media.music.163.com"}),
            _source_url="https://media.music.163.com/audio",
        )


class CancellingDownloader:
    async def download(self, grant: DownloadGrant, target: Path) -> int:
        await asyncio.to_thread(target.write_bytes, b"partial media")
        raise asyncio.CancelledError


class NeverCalledDelivery:
    def to_server(self, downloaded: Path, request: JobRequest) -> Path:
        raise AssertionError("delivery must not run after cancellation")


async def test_pipeline_cancellation_removes_temporary_media(
    tmp_path: Path, repository, job_request: JobRequest
) -> None:
    assert job_request.track.source is Source.NETEASE
    job = repository.create_job(job_request)
    work_root = tmp_path / "work"
    processor = PipelineProcessor(
        repository,
        CorrectGateway(),  # type: ignore[arg-type]
        CancellingDownloader(),  # type: ignore[arg-type]
        NeverCalledDelivery(),  # type: ignore[arg-type]
        work_root,
    )

    with pytest.raises(asyncio.CancelledError):
        await processor.process(job)

    assert not (work_root / job.id).exists()
