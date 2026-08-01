from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from musicdl_web.app import create_app
from musicdl_web.db import Repository
from musicdl_web.domain import Delivery, Job, JobRequest, PlatformTrack, Quality
from musicdl_web.models import Source
from musicdl_web.queue import QueueService


class IdleProcessor:
    async def process(self, job: Job) -> str:
        return job.id


class Submitter:
    async def prepare_request(
        self,
        source: Source,
        track_id: str,
        quality_id: str,
        quality_snapshot_id: str,
        delivery: Delivery,
    ) -> JobRequest:
        return JobRequest(
            track=PlatformTrack(source=source, track_id=track_id, title="Song", artists=("A",)),
            quality=Quality.LOSSLESS,
            quality_id=quality_id,
            quality_snapshot_id=quality_snapshot_id,
            delivery=delivery,
        )


async def test_batch_task_cancel_retry_and_history_api(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    queue = QueueService(repository, IdleProcessor())
    app = create_app(repository, queue, Submitter())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/batches",
            json={
                "delivery": "browser",
                "items": [
                    {
                        "source": "netease",
                        "trackId": "42",
                        "qualityId": "lossless",
                        "qualitySnapshotId": "snapshot-1234567890123456",
                    }
                ],
            },
        )
        assert created.status_code == 202
        task_id = created.json()[0]["id"]
        active = await client.get("/api/v1/tasks", params={"scope": "active"})
        assert active.json()[0]["track"]["trackId"] == "42"
        assert "outputPath" not in active.text
        cancelled = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancelled.json()["stage"] == "cancelled"
        assert (await client.get("/api/v1/tasks", params={"scope": "history"})).json()
        assert (await client.delete("/api/v1/history")).status_code == 204


async def test_browser_file_is_one_time_and_server_job_has_no_file(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "app.db")
    queue = QueueService(repository, IdleProcessor())
    app = create_app(repository, queue, Submitter())
    browser = repository.create_job(
        await Submitter().prepare_request(
            Source.QQ, "x", "high", "snapshot-1234567890123456", Delivery.BROWSER
        )
    )
    artifact = tmp_path / "tmp/song.flac"
    artifact.parent.mkdir()
    artifact.write_bytes(b"audio")
    repository.register_browser_artifact(
        browser.id, artifact, "song.flac", datetime.now(UTC) + timedelta(minutes=5)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(f"/api/v1/tasks/{browser.id}/file")
        second = await client.get(f"/api/v1/tasks/{browser.id}/file")

    assert first.status_code == 200
    assert first.content == b"audio"
    assert second.status_code == 404
