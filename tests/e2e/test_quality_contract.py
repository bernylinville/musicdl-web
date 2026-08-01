from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from musicdl_web.core import CapabilityGateway, PipelineProcessor
from musicdl_web.domain import Job, JobRequest
from musicdl_web.download import DownloadError
from musicdl_web.models import Source
from musicdl_web.platforms import (
    DownloadGrant,
    FidelityFamily,
    QualityOption,
    QualitySnapshotExpired,
    QualitySnapshotMismatch,
    QualitySnapshotStore,
)

NOW = datetime(2026, 7, 31, 8, tzinfo=UTC)
LOSSLESS = QualityOption(
    quality_id="lossless",
    label="无损",
    family=FidelityFamily.LINEAR,
    rank=30,
    codec="flac",
)


def create_snapshot(store: QualitySnapshotStore):
    return store.create(
        source=Source.NETEASE,
        track_id="track-1",
        session_version=7,
        options=(LOSSLESS,),
        now=NOW,
    )


def test_quality_snapshot_rejects_a_different_session_version() -> None:
    store = QualitySnapshotStore()
    snapshot = create_snapshot(store)

    with pytest.raises(QualitySnapshotMismatch, match="binding does not match"):
        store.bind(
            snapshot_id=snapshot.snapshot_id,
            quality_id="lossless",
            source=Source.NETEASE,
            track_id="track-1",
            session_version=8,
            now=NOW,
        )


def test_quality_snapshot_rejects_the_exact_expiry_boundary() -> None:
    store = QualitySnapshotStore(ttl=timedelta(minutes=5))
    snapshot = create_snapshot(store)

    with pytest.raises(QualitySnapshotExpired, match="expired"):
        store.bind(
            snapshot_id=snapshot.snapshot_id,
            quality_id="lossless",
            source=Source.NETEASE,
            track_id="track-1",
            session_version=7,
            now=NOW + timedelta(minutes=5),
        )


class WrongQualityResolver:
    async def revalidate_exact(self, request: JobRequest) -> DownloadGrant:
        return DownloadGrant(
            source=request.track.source,
            track_id=request.track.track_id,
            quality_id="high",
            quality_rank=20,
            expires_at=NOW + timedelta(minutes=1),
            allowed_hosts=frozenset({"music.163.com"}),
            _source_url="https://music.163.com/audio",
        )


class NeverCalledDownloader:
    called = False

    async def download(self, grant: DownloadGrant, target: Path) -> int:
        self.called = True
        return 1


class NeverCalledDelivery:
    def to_server(self, downloaded: Path, request: JobRequest) -> Path:
        raise AssertionError("delivery must not run after an exact-quality mismatch")


async def test_exact_quality_revalidation_never_downgrades(
    tmp_path: Path, repository, job_request: JobRequest
) -> None:
    job: Job = repository.create_job(job_request)
    downloader = NeverCalledDownloader()
    processor = PipelineProcessor(
        repository,
        CapabilityGateway(WrongQualityResolver()),
        downloader,  # type: ignore[arg-type]
        NeverCalledDelivery(),  # type: ignore[arg-type]
        tmp_path / "work",
    )

    with pytest.raises(DownloadError) as error:
        await processor.process(job)

    assert error.value.code == "quality_mismatch"
    assert downloader.called is False


def test_download_grant_repr_redacts_source_url() -> None:
    grant = DownloadGrant(
        source=Source.NETEASE,
        track_id="track-1",
        quality_id="lossless",
        quality_rank=30,
        expires_at=NOW + timedelta(minutes=1),
        allowed_hosts=frozenset({"music.163.com"}),
        _source_url="https://music.163.com/audio?token=top-secret",
    )

    assert "top-secret" not in repr(grant)
