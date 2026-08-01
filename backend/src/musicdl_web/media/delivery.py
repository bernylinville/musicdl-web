"""Mutually exclusive server-library and browser delivery."""

from __future__ import annotations

import os
import shutil
from datetime import timedelta
from pathlib import Path

from musicdl_web.db import Repository
from musicdl_web.domain import JobRequest, MediaMetadata
from musicdl_web.domain.models import utc_now
from musicdl_web.media.publisher import MediaProbe, MediaPublisher, PublishError, TagWriter


class DeliveryService:
    def __init__(
        self,
        repository: Repository,
        publisher: MediaPublisher,
        browser_root: Path,
        probe: MediaProbe,
        tag_writer: TagWriter,
        *,
        browser_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.browser_root = browser_root
        self.probe = probe
        self.tag_writer = tag_writer
        self.browser_ttl = browser_ttl

    def to_server(
        self,
        downloaded: Path,
        request: JobRequest,
        *,
        cover: bytes | None = None,
        lyrics: str | None = None,
    ) -> Path:
        return self.publisher.publish(downloaded, request, cover=cover, lyrics=lyrics)

    def to_browser(
        self,
        job_id: str,
        downloaded: Path,
        request: JobRequest,
        *,
        cover: bytes | None = None,
        lyrics: str | None = None,
    ) -> Path:
        metadata = self.probe.probe(downloaded)
        if metadata.quality != request.quality:
            raise PublishError("downloaded media quality does not match the selected tier")
        directory = self.browser_root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        stem = self.publisher.relative_path(request, metadata.extension).stem
        filename = f"{stem}.{metadata.extension}"
        staged = directory / f".{filename}.part"
        output = directory / filename
        shutil.copyfile(downloaded, staged)
        self.tag_writer.write(staged, request, cover=cover, lyrics=lyrics)
        after = self.probe.probe(staged)
        if (after.extension, after.codec) != (metadata.extension, metadata.codec):
            shutil.rmtree(directory, ignore_errors=True)
            raise PublishError("tagging changed the original media encoding")
        os.replace(staged, output)
        if lyrics:
            output.with_suffix(".lrc").write_text(lyrics, encoding="utf-8")
        self.repository.register_browser_artifact(
            job_id, output, filename, utc_now() + self.browser_ttl
        )
        return output

    def cleanup_expired(self) -> int:
        artifacts = self.repository.expired_artifacts()
        for _, path in artifacts:
            shutil.rmtree(path.parent, ignore_errors=True)
        self.repository.delete_artifacts(job_id for job_id, _ in artifacts)
        return len(artifacts)


def validate_media(metadata: MediaMetadata, request: JobRequest) -> None:
    if metadata.quality != request.quality:
        raise PublishError("media tier mismatch")
