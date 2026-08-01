from __future__ import annotations

import httpx
from musicdl_web.app import app


def runtime_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_default_root_serves_download_workbench_instead_of_spike_page() -> None:
    async with runtime_client() as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "下载工作台" in response.text
    assert "只读状态页" not in response.text
    assert "当前仅提供搜索 spike" not in response.text.lower()


async def test_default_runtime_returns_session_status_schema() -> None:
    async with runtime_client() as client:
        response = await client.get("/api/v1/sessions")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert {item["source"] for item in body} == {"netease", "qq"}
    assert all("state" in item and "qrEnabled" in item for item in body)


async def test_default_runtime_returns_grouped_search_schema() -> None:
    async with runtime_client() as client:
        response = await client.get("/api/v1/search?q=fixture&source=all&page=1")

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "fixture"
    assert {group["source"] for group in body["groups"]} == {"netease", "qq"}


async def test_netease_quality_without_session_returns_explicit_session_required() -> None:
    async with runtime_client() as client:
        response = await client.get("/api/v1/tracks/netease/track-1/qualities")

    assert 400 <= response.status_code < 500
    assert "session_required" in response.text.lower()


async def test_default_runtime_exposes_task_queue_api() -> None:
    async with runtime_client() as client:
        response = await client.get("/api/v1/tasks?scope=active")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
