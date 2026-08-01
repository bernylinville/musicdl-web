"""Async bounded workers over a SQLite queue."""

from __future__ import annotations

import asyncio
from typing import Protocol

from musicdl_web.core.pipeline import PipelineResult
from musicdl_web.db import Repository
from musicdl_web.domain import Job, JobStatus
from musicdl_web.download import DownloadError
from musicdl_web.media import PublishError


class JobProcessor(Protocol):
    async def process(self, job: Job) -> str | PipelineResult: ...


class QueueService:
    def __init__(
        self, repository: Repository, processor: JobProcessor, *, concurrency: int = 2
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")
        self.repository = repository
        self.processor = processor
        self.concurrency = concurrency
        self._wake = asyncio.Event()
        self._workers: list[asyncio.Task[None]] = []
        self._running: dict[str, asyncio.Task[str | PipelineResult]] = {}
        self._stopping = False

    async def start(self) -> None:
        if self._workers:
            return
        self.repository.recover_interrupted()
        self._stopping = False
        self._workers = [
            asyncio.create_task(self._worker(), name=f"musicdl-worker-{index}")
            for index in range(self.concurrency)
        ]
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        for task in self._running.values():
            task.cancel()
        self._wake.set()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running.clear()

    def notify(self) -> None:
        self._wake.set()

    def cancel(self, job_id: str) -> bool:
        changed = self.repository.cancel(job_id)
        running = self._running.get(job_id)
        if running:
            running.cancel()
        return changed

    def retry(self, job_id: str) -> Job:
        job = self.repository.retry(job_id)
        self.notify()
        return job

    async def _worker(self) -> None:
        while not self._stopping:
            self._wake.clear()
            job = self.repository.claim_next()
            if job is None:
                await self._wake.wait()
                continue
            task = asyncio.create_task(self.processor.process(job))
            self._running[job.id] = task
            try:
                output = await task
            except asyncio.CancelledError:
                self.repository.cancel(job.id)
            except DownloadError as exc:
                self._fail_if_active(job.id, exc.code, str(exc))
            except PublishError as exc:
                self._fail_if_active(job.id, "publish_failed", str(exc))
            except Exception:
                self._fail_if_active(job.id, "internal_error", "task failed unexpectedly")
            else:
                current = self.repository.get_job(job.id)
                if current and current.status != JobStatus.CANCELLED:
                    output_path = (
                        output.output_path if isinstance(output, PipelineResult) else output
                    )
                    warning = output.warning if isinstance(output, PipelineResult) else None
                    self.repository.transition(
                        job.id,
                        JobStatus.COMPLETED,
                        progress=1,
                        output_path=output_path,
                        error_message=warning,
                    )
            finally:
                self._running.pop(job.id, None)

    def _fail_if_active(self, job_id: str, code: str, message: str) -> None:
        current = self.repository.get_job(job_id)
        if current and current.status != JobStatus.CANCELLED:
            self.repository.transition(
                job_id,
                JobStatus.FAILED,
                error_code=code,
                error_message=message[:500],
            )
