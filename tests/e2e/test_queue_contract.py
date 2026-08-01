from __future__ import annotations

import asyncio

from musicdl_web.domain import Job, JobRequest, JobStatus
from musicdl_web.queue import QueueService


async def wait_for_status(repository, job_id: str, status: JobStatus) -> Job:
    async def poll() -> Job:
        while True:
            job = repository.get_job(job_id)
            assert job is not None
            if job.status is status:
                return job
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), timeout=2)


class BlockingProcessor:
    def __init__(self, expected_parallel: int) -> None:
        self.expected_parallel = expected_parallel
        self.release = asyncio.Event()
        self.capacity_reached = asyncio.Event()
        self.active = 0
        self.maximum_active = 0

    async def process(self, job: Job) -> str:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == self.expected_parallel:
            self.capacity_reached.set()
        try:
            await self.release.wait()
            return f"/music/{job.id}.flac"
        finally:
            self.active -= 1


async def test_queue_never_exceeds_configured_concurrency(
    repository, job_request: JobRequest
) -> None:
    jobs = [repository.create_job(job_request) for _ in range(3)]
    processor = BlockingProcessor(expected_parallel=2)
    queue = QueueService(repository, processor, concurrency=2)
    await queue.start()
    try:
        await asyncio.wait_for(processor.capacity_reached.wait(), timeout=2)

        assert processor.maximum_active == 2
        queued = [repository.get_job(job.id).status for job in jobs]  # type: ignore[union-attr]
        assert queued.count(JobStatus.QUEUED) == 1
    finally:
        processor.release.set()
        await queue.stop()


async def test_cancelling_a_running_job_reaches_cancelled_state(
    repository, job_request: JobRequest
) -> None:
    job = repository.create_job(job_request)
    processor = BlockingProcessor(expected_parallel=1)
    queue = QueueService(repository, processor, concurrency=1)
    await queue.start()
    try:
        await asyncio.wait_for(processor.capacity_reached.wait(), timeout=2)

        changed = queue.cancel(job.id)
        cancelled = await wait_for_status(repository, job.id, JobStatus.CANCELLED)

        assert changed is True
        assert cancelled.status is JobStatus.CANCELLED
    finally:
        processor.release.set()
        await queue.stop()


def test_repository_recovers_interrupted_job_to_safe_retry(
    repository, job_request: JobRequest
) -> None:
    job = repository.create_job(job_request)
    repository.transition(job.id, JobStatus.DOWNLOADING, progress=0.5)

    recovered_count = repository.recover_interrupted()

    recovered = repository.get_job(job.id)
    assert recovered_count == 1
    assert recovered is not None
    assert recovered.status is JobStatus.QUEUED
    assert recovered.progress == 0


def test_retry_resets_only_a_failed_job(repository, job_request: JobRequest) -> None:
    job = repository.create_job(job_request)
    repository.transition(
        job.id,
        JobStatus.FAILED,
        progress=0.7,
        error_code="truncated",
        error_message="audio was truncated",
    )

    retried = repository.retry(job.id)

    assert retried.status is JobStatus.QUEUED
    assert retried.progress == 0
    assert retried.error_code is None
    assert retried.error_message is None
