from __future__ import annotations

import httpx
from musicdl_web.app import app


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_healthz_reports_healthy() -> None:
    async with make_client() as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_root_serves_the_built_workbench_instead_of_the_spike_page() -> None:
    async with make_client() as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="zh-CN">' in response.text
    assert '<div id="app"></div>' in response.text
    assert "下载工作台" in response.text
    assert "只读状态页" not in response.text
    assert "当前仅提供搜索 spike" not in response.text


async def test_service_exposes_no_download_or_schema_endpoints() -> None:
    async with make_client() as client:
        for path in ("/download", "/api/download", "/docs", "/redoc", "/openapi.json"):
            response = await client.get(path)

            assert response.status_code == 404


async def test_status_responses_expose_no_secret_material() -> None:
    forbidden = ("session.key", "/run/secrets", "cookie", "token=", "password")

    async with make_client() as client:
        for path in ("/", "/healthz"):
            body = (await client.get(path)).text.lower()

            assert all(value not in body for value in forbidden)
