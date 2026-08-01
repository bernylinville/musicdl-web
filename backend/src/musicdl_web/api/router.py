"""Queue and delivery API; all returned models are secret-free."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from musicdl_web.db import Repository
from musicdl_web.domain import Delivery, Job, JobRequest, JobStatus
from musicdl_web.models import Source
from musicdl_web.queue import QueueService


class BatchSubmitter(Protocol):
    async def prepare_request(
        self,
        source: Source,
        track_id: str,
        quality_id: str,
        quality_snapshot_id: str,
        delivery: Delivery,
    ) -> JobRequest: ...


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    track_id: str = Field(alias="trackId", min_length=1, max_length=256)
    quality_id: str = Field(alias="qualityId", min_length=1, max_length=128)
    quality_snapshot_id: str = Field(
        alias="qualitySnapshotId", min_length=1, max_length=128
    )


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery: Delivery
    items: list[BatchItem] = Field(min_length=1, max_length=100)


class TaskTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    track_id: str = Field(alias="trackId")
    title: str
    artists: tuple[str, ...]


class TaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    track: TaskTrackView
    quality_label: str = Field(alias="qualityLabel")
    delivery: Delivery
    stage: JobStatus
    progress: float | None
    error: str | None
    warning: str | None
    browser_file_url: str | None = Field(alias="browserFileUrl")
    created_at: str = Field(alias="createdAt")

    @classmethod
    def from_job(cls, job: Job, repository: Repository) -> TaskView:
        downloadable = (
            job.status == JobStatus.COMPLETED
            and job.request.delivery == Delivery.BROWSER
            and repository.browser_artifact(job.id) is not None
        )
        track = job.request.track
        return cls(
            id=job.id,
            track=TaskTrackView(
                source=track.source,
                trackId=track.track_id,
                title=track.title,
                artists=track.artists,
            ),
            qualityLabel=job.request.quality_label or job.request.quality_id,
            delivery=job.request.delivery,
            stage=job.status,
            progress=job.progress,
            error=_safe_error_message(job.error_code),
            warning=(job.error_message if job.status == JobStatus.COMPLETED else None),
            browserFileUrl=f"/api/v1/tasks/{job.id}/file" if downloadable else None,
            createdAt=job.created_at.isoformat(),
        )


def create_api_router(
    repository: Repository,
    queue: QueueService,
    submitter: BatchSubmitter,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/batches", response_model=list[TaskView], status_code=status.HTTP_202_ACCEPTED)
    async def create_batch(batch: BatchCreate) -> list[TaskView]:
        jobs: list[Job] = []
        for item in batch.items:
            try:
                request = await submitter.prepare_request(
                    item.source,
                    item.track_id,
                    item.quality_id,
                    item.quality_snapshot_id,
                    batch.delivery,
                )
            except ValueError as exc:
                # Keep each item independent without echoing exception text, which can contain
                # platform credentials or source URLs.
                del exc
                jobs.append(
                    repository.create_rejected_job(
                        source=item.source,
                        track_id=item.track_id,
                        quality_id=item.quality_id,
                        quality_snapshot_id=item.quality_snapshot_id,
                        delivery=batch.delivery,
                        error_code="quality_snapshot_invalid",
                    )
                )
                continue
            if batch.delivery == Delivery.SERVER:
                decision = repository.managed_decision(
                    request.track.source, request.track.track_id, request.quality
                )
                if not decision.allowed:
                    failed = repository.create_job(request)
                    repository.transition(
                        failed.id,
                        JobStatus.FAILED,
                        error_code="already_managed",
                        error_message="曲库已有相同或更高音质，无需重复下载",
                    )
                    jobs.append(repository.get_job(failed.id) or failed)
                    continue
            jobs.append(repository.create_job(request))
        queue.notify()
        return [TaskView.from_job(job, repository) for job in jobs]

    @router.get("/tasks", response_model=list[TaskView])
    def list_tasks(
        scope: Annotated[Literal["active", "history"], Query()] = "active",
    ) -> list[TaskView]:
        jobs = repository.list_jobs()
        if scope == "active":
            jobs = [job for job in jobs if not job.status.terminal]
        else:
            jobs = [job for job in jobs if job.status.terminal]
        return [TaskView.from_job(job, repository) for job in jobs]

    @router.post("/tasks/{task_id}/cancel", response_model=TaskView)
    def cancel_task(task_id: str) -> TaskView:
        if not queue.cancel(task_id):
            raise HTTPException(status_code=409, detail="task cannot be cancelled")
        job = repository.get_job(task_id)
        if job is None:
            raise HTTPException(status_code=404, detail="task not found")
        return TaskView.from_job(job, repository)

    @router.post("/tasks/{task_id}/retry", response_model=TaskView)
    def retry_task(task_id: str) -> TaskView:
        try:
            job = queue.retry(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TaskView.from_job(job, repository)

    @router.get("/tasks/{task_id}/file", response_class=FileResponse)
    def retrieve_file(task_id: str) -> FileResponse:
        artifact = repository.browser_artifact(task_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="browser artifact unavailable")
        path, filename = artifact
        if not path.is_file():
            raise HTTPException(status_code=410, detail="browser artifact expired or missing")
        return FileResponse(
            path,
            filename=filename,
            media_type="application/octet-stream",
            background=BackgroundTask(repository.mark_delivered, task_id),
        )

    @router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
    def clear_history() -> Response:
        repository.clear_terminal_history()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


_SAFE_ERROR_MESSAGES = {
    "already_managed": "曲库已有相同或更高音质，无需重复下载",
    "authorization_rejected": "platform authorization was rejected",
    "empty_body": "platform returned an empty audio file",
    "grant_expired": "download authorization expired",
    "host_not_allowed": "download host is not allowed",
    "http_error": "platform download failed",
    "internal_error": "task failed unexpectedly",
    "invalid_length": "platform returned an invalid media length",
    "length_mismatch": "platform media length changed",
    "publish_failed": "media validation or publication failed",
    "quality_snapshot_invalid": "quality selection is invalid or expired",
    "quality_mismatch": "platform did not authorize the selected quality",
    "redirect_limit": "platform returned too many redirects",
    "truncated": "downloaded media was truncated",
    "unexpected_content": "platform returned non-audio content",
}


def _safe_error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return _SAFE_ERROR_MESSAGES.get(code, "task failed")
