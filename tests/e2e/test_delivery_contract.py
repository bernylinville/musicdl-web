from __future__ import annotations

from pathlib import Path

from musicdl_web.db import Repository
from musicdl_web.domain import Delivery, JobRequest, MediaMetadata, Quality
from musicdl_web.media import MediaPublisher
from musicdl_web.media.delivery import DeliveryService


class StableProbe:
    def probe(self, path: Path) -> MediaMetadata:
        return MediaMetadata(
            extension="flac",
            codec="flac",
            duration_seconds=180,
            quality=Quality.LOSSLESS,
        )


class NoOpTagWriter:
    def write(
        self,
        audio: Path,
        request: JobRequest,
        *,
        cover: bytes | None,
        lyrics: str | None,
    ) -> None:
        return None


def build_delivery(tmp_path: Path, repository: Repository) -> DeliveryService:
    probe = StableProbe()
    writer = NoOpTagWriter()
    publisher = MediaPublisher(
        tmp_path / "music",
        tmp_path / "stage",
        repository,
        probe,
        writer,
    )
    return DeliveryService(
        repository,
        publisher,
        tmp_path / "browser",
        probe,
        writer,
    )


def downloaded_file(tmp_path: Path) -> Path:
    path = tmp_path / "download.flac"
    path.write_bytes(b"fLaC" + b"audio" * 10)
    return path


def test_browser_delivery_does_not_enter_managed_library_index(
    tmp_path: Path, repository: Repository, job_request: JobRequest
) -> None:
    request = job_request.model_copy(update={"delivery": Delivery.BROWSER})
    job = repository.create_job(request)
    delivery = build_delivery(tmp_path, repository)

    output = delivery.to_browser(job.id, downloaded_file(tmp_path), request)

    decision = repository.managed_decision(
        request.track.source, request.track.track_id, request.quality
    )
    assert output.is_relative_to(tmp_path / "browser")
    assert decision.reason == "new"


def test_server_delivery_registers_managed_media_only_after_publication(
    tmp_path: Path, repository: Repository, job_request: JobRequest
) -> None:
    delivery = build_delivery(tmp_path, repository)

    output = delivery.to_server(downloaded_file(tmp_path), job_request, lyrics="line")

    decision = repository.managed_decision(
        job_request.track.source, job_request.track.track_id, job_request.quality
    )
    assert output.is_relative_to(tmp_path / "music")
    assert decision.reason == "same_or_lower"
    assert output.with_suffix(".lrc").read_text(encoding="utf-8") == "line"


def test_nonmanaged_conflicting_file_is_preserved(
    tmp_path: Path, repository: Repository, job_request: JobRequest
) -> None:
    delivery = build_delivery(tmp_path, repository)
    relative = delivery.publisher.relative_path(job_request, "flac")
    existing = tmp_path / "music" / relative
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"nonmanaged-original")

    output = delivery.to_server(downloaded_file(tmp_path), job_request)

    assert existing.read_bytes() == b"nonmanaged-original"
    assert output != existing
    assert "[netease-track-1]" in output.name


def test_nonmanaged_lyrics_sidecar_is_never_overwritten(
    tmp_path: Path, repository: Repository, job_request: JobRequest
) -> None:
    delivery = build_delivery(tmp_path, repository)
    relative = delivery.publisher.relative_path(job_request, "flac")
    existing_lyrics = (tmp_path / "music" / relative).with_suffix(".lrc")
    existing_lyrics.parent.mkdir(parents=True)
    existing_lyrics.write_text("personal lyrics", encoding="utf-8")

    output = delivery.to_server(
        downloaded_file(tmp_path), job_request, lyrics="managed lyrics"
    )

    assert existing_lyrics.read_text(encoding="utf-8") == "personal lyrics"
    assert output.with_suffix(".lrc").read_text(encoding="utf-8") == "managed lyrics"


def test_quality_upgrade_removes_previous_managed_lyrics(
    tmp_path: Path, repository: Repository, job_request: JobRequest
) -> None:
    delivery = build_delivery(tmp_path, repository)
    previous = tmp_path / "music" / "Artist" / "Old.flac"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"old managed audio")
    previous.with_suffix(".lrc").write_text("old managed lyrics", encoding="utf-8")
    repository.register_media(
        job_request.track.source,
        job_request.track.track_id,
        Quality.HIGH,
        previous,
    )

    output = delivery.to_server(
        downloaded_file(tmp_path), job_request, lyrics="new managed lyrics"
    )

    assert output.exists()
    assert not previous.exists()
    assert not previous.with_suffix(".lrc").exists()
