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


async def test_status_page_explains_current_gate_in_simplified_chinese() -> None:
    async with make_client() as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="zh-CN">' in response.text
    for statement in (
        "当前仅提供搜索 spike",
        "17+ 项测试",
        "网易云音乐",
        "QQ 音乐",
        "QQ live volatile",
        "实际音质清单",
        "精确单档解析",
        "合法短试听",
        "账号权益路径",
        "不能执行下载",
        "下一门槛",
    ):
        assert statement in response.text


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
