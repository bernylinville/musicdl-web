from __future__ import annotations

import httpx
from musicdl_web.app import create_app
from musicdl_web.domain import JobRequest, JobStatus


class FakeQueue:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.notifications = 0

    def notify(self) -> None:
        self.notifications += 1

    def cancel(self, job_id: str) -> bool:
        return self.repository.cancel(job_id)

    def retry(self, job_id: str):
        return self.repository.retry(job_id)


class SuccessfulSubmitter:
    def __init__(self, request: JobRequest) -> None:
        self.request = request

    async def prepare_request(self, source, track_id, quality_id, snapshot_id, delivery):
        return self.request.model_copy(update={"delivery": delivery})


class SecretFailingSubmitter:
    async def prepare_request(self, source, track_id, quality_id, snapshot_id, delivery):
        raise ValueError("platform rejected token=super-secret")


class PartialSubmitter:
    def __init__(self, request: JobRequest) -> None:
        self.request = request

    async def prepare_request(self, source, track_id, quality_id, snapshot_id, delivery):
        if track_id == "unavailable-track":
            raise ValueError("selected quality is unavailable")
        return self.request.model_copy(update={"delivery": delivery})


def client_for(repository, submitter) -> httpx.AsyncClient:
    app = create_app(repository, FakeQueue(repository), submitter)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def batch_payload() -> dict[str, object]:
    return {
        "delivery": "server",
        "items": [
            {
                "source": "netease",
                "trackId": "track-1",
                "qualityId": "lossless",
                "qualitySnapshotId": "snapshot-1",
            }
        ],
    }


async def test_batch_preserves_partial_success_as_independent_task_results(
    repository, job_request: JobRequest
) -> None:
    payload = batch_payload()
    payload["items"] = [
        payload["items"][0],  # type: ignore[index]
        {
            "source": "netease",
            "trackId": "unavailable-track",
            "qualityId": "lossless",
            "qualitySnapshotId": "snapshot-2",
        },
    ]
    async with client_for(repository, PartialSubmitter(job_request)) as client:
        response = await client.post("/api/v1/batches", json=payload)

    assert response.status_code == 202
    tasks = repository.list_jobs()
    assert len(tasks) == 2
    assert {task.status for task in tasks} == {JobStatus.QUEUED, JobStatus.FAILED}


async def test_batch_response_matches_frontend_download_task_contract(
    repository, job_request: JobRequest
) -> None:
    async with client_for(repository, SuccessfulSubmitter(job_request)) as client:
        response = await client.post("/api/v1/batches", json=batch_payload())

    assert response.status_code == 202
    body = response.json()
    assert isinstance(body, list)
    assert set(body[0]) >= {
        "id",
        "track",
        "qualityLabel",
        "delivery",
        "stage",
        "progress",
        "error",
        "warning",
        "browserFileUrl",
        "createdAt",
    }


async def test_batch_error_response_redacts_platform_token(repository) -> None:
    async with client_for(repository, SecretFailingSubmitter()) as client:
        response = await client.post("/api/v1/batches", json=batch_payload())

    assert response.status_code == 202
    assert "super-secret" not in response.text
    assert "token=" not in response.text.lower()


async def test_task_history_redacts_secret_shaped_persisted_error(
    repository, job_request: JobRequest
) -> None:
    job = repository.create_job(job_request)
    repository.transition(
        job.id,
        JobStatus.FAILED,
        error_code="platform_error",
        error_message="authorization token=super-secret",
    )

    async with client_for(repository, SuccessfulSubmitter(job_request)) as client:
        response = await client.get("/api/v1/tasks?scope=history")

    assert response.status_code == 200
    assert "super-secret" not in response.text
    assert "token=" not in response.text.lower()
