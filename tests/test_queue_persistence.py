from __future__ import annotations

import asyncio
from pathlib import Path

from musicdl_web.core import PipelineResult
from musicdl_web.db import Repository
from musicdl_web.domain import (
    Delivery,
    Job,
    JobRequest,
    JobStatus,
    PlatformTrack,
    Quality,
)
from musicdl_web.models import Source
from musicdl_web.queue import QueueService


def request(track_id: str, delivery: Delivery = Delivery.SERVER) -> JobRequest:
    return JobRequest(
        track=PlatformTrack(source=Source.NETEASE, track_id=track_id, title="Song", artists=("A",)),
        quality=Quality.LOSSLESS,
        quality_id="lossless",
        quality_snapshot_id="snapshot-1234567890123456",
        delivery=delivery,
    )


class ControlledProcessor:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def process(self, job: Job) -> str:
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return f"/output/{job.id}"
        finally:
            self.active -= 1


class WarningProcessor:
    async def process(self, job: Job) -> PipelineResult:
        return PipelineResult(f"/output/{job.id}", "音频成功，封面缺失")


async def wait_for(predicate: object, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


async def test_queue_enforces_concurrency_and_allows_partial_success(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    processor = ControlledProcessor()
    queue = QueueService(repository, processor, concurrency=2)
    jobs = [repository.create_job(request(str(index))) for index in range(3)]

    await queue.start()
    await wait_for(lambda: processor.active == 2)
    assert processor.maximum == 2
    processor.release.set()
    await wait_for(lambda: all(repository.get_job(job.id).status.terminal for job in jobs))  # type: ignore[union-attr]
    await queue.stop()

    assert all(repository.get_job(job.id).status == JobStatus.COMPLETED for job in jobs)  # type: ignore[union-attr]


async def test_cancel_retry_and_crash_recovery_are_persistent(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    queued = repository.create_job(request("cancel"))
    assert repository.cancel(queued.id)
    failed = repository.create_job(request("retry"))
    repository.transition(failed.id, JobStatus.FAILED, error_code="x", error_message="no")
    assert repository.retry(failed.id).status == JobStatus.QUEUED
    interrupted = repository.claim_next()
    assert interrupted is not None
    assert interrupted.status == JobStatus.RESOLVING

    reopened = Repository(tmp_path / "app.db")
    assert reopened.recover_interrupted() == 1
    assert reopened.get_job(interrupted.id).status == JobStatus.QUEUED  # type: ignore[union-attr]
    assert reopened.get_job(queued.id).status == JobStatus.CANCELLED  # type: ignore[union-attr]


async def test_completed_task_preserves_nonfatal_cover_warning(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    job = repository.create_job(request("warning"))
    queue = QueueService(repository, WarningProcessor())

    await queue.start()
    await wait_for(lambda: repository.get_job(job.id).status == JobStatus.COMPLETED)  # type: ignore[union-attr]
    await queue.stop()

    completed = repository.get_job(job.id)
    assert completed is not None
    assert completed.output_path == f"/output/{job.id}"
    assert completed.error_message == "音频成功，封面缺失"
