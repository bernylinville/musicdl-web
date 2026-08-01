"""Exact quality download pipeline."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from musicdl_web.db import Repository
from musicdl_web.domain import Delivery, Job, JobRequest, JobStatus
from musicdl_web.download import DownloadError, SafeDownloader
from musicdl_web.media.delivery import DeliveryService
from musicdl_web.platforms.quality import DownloadGrant


class PlatformDownloadGateway(Protocol):
    async def revalidate(self, request: JobRequest) -> DownloadGrant: ...

    async def artwork(self, request: JobRequest) -> bytes | None: ...


class ExactGrantResolver(Protocol):
    async def revalidate_exact(self, request: JobRequest) -> DownloadGrant: ...

    async def artwork_for_request(self, request: JobRequest) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class PipelineResult:
    output_path: str
    warning: str | None = None


class CapabilityGateway:
    """Single adapter from the persisted safe request to platform exact resolution."""

    def __init__(self, resolver: ExactGrantResolver) -> None:
        self.resolver = resolver

    async def revalidate(self, request: JobRequest) -> DownloadGrant:
        return await self.resolver.revalidate_exact(request)

    async def artwork(self, request: JobRequest) -> bytes | None:
        return await self.resolver.artwork_for_request(request)


class PipelineProcessor:
    def __init__(
        self,
        repository: Repository,
        gateway: PlatformDownloadGateway,
        downloader: SafeDownloader,
        delivery: DeliveryService,
        work_root: Path,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.downloader = downloader
        self.delivery = delivery
        self.work_root = work_root

    async def process(self, job: Job) -> PipelineResult:
        work = self.work_root / job.id
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            self.repository.transition(job.id, JobStatus.RESOLVING, progress=0.05)
            grant = await self.gateway.revalidate(job.request)
            if (
                grant.quality_rank != int(job.request.quality)
                or grant.quality_id != job.request.quality_id
                or grant.source is not job.request.track.source
                or grant.track_id != job.request.track.track_id
            ):
                raise DownloadError(
                    "quality_mismatch", "platform did not authorize the exact selected quality"
                )
            self.repository.transition(job.id, JobStatus.DOWNLOADING, progress=0.15)
            raw = work / "download.part"
            await self.downloader.download(grant, raw)
            self.repository.transition(job.id, JobStatus.TAGGING, progress=0.75)
            cover = await self.gateway.artwork(job.request)
            if job.request.delivery == Delivery.SERVER:
                self.repository.transition(job.id, JobStatus.PUBLISHING, progress=0.9)
                output = self.delivery.to_server(raw, job.request, cover=cover)
            else:
                output = self.delivery.to_browser(job.id, raw, job.request, cover=cover)
            return PipelineResult(
                str(output), warning=None if cover is not None else "音频成功，封面缺失"
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
