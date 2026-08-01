from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from musicdl_web.api.platform_router import create_platform_router
from musicdl_web.domain import Delivery, Quality
from musicdl_web.models import Source
from musicdl_web.runtime import (
    ProductionPlatformRuntime,
    RuntimeSettings,
    build_application_runtime,
)
from musicdl_web.sessions import AESGCMCipher, EncryptedSessionStore, FileCiphertextRepository


def make_runtime(
    tmp_path: Path,
    *,
    cover_body: bytes = b"\xff\xd8\xff\xe0runtime-jpeg\xff\xd9",
    cover_type: str = "image/jpeg",
) -> tuple[ProductionPlatformRuntime, list[httpx.Request]]:
    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"k" * 32)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/cloudsearch/pc":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "result": {
                        "songCount": 1,
                        "songs": [
                            {
                                "id": 101,
                                "name": "Runtime Track",
                                "ar": [{"name": "Runtime Artist"}],
                                "al": {
                                    "name": "Runtime Album",
                                    "picUrl": "https://p1.music.126.net/runtime-cover.jpg?token=hidden",
                                },
                                "dt": 180000,
                            }
                        ],
                    },
                },
            )
        if request.url.path == "/runtime-cover.jpg":
            return httpx.Response(
                200,
                headers={"content-type": cover_type},
                content=cover_body,
            )
        if request.url.path == "/api/song/enhance/player/url/v1":
            assert request.headers.get("cookie") == "MUSIC_U=local-session-value"
            form = parse_qs(request.content.decode())
            requested = form["level"][0]
            available = requested in {"standard", "lossless"}
            actual = requested if available else "standard"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "id": 101,
                            "level": actual,
                            "url": f"https://m10.music.126.net/media/{actual}",
                            "size": 4096,
                            "type": "flac" if actual == "lossless" else "mp3",
                            "freeTrialInfo": None,
                        }
                    ],
                },
            )
        if request.url.path == "/api/w/nuser/account/get":
            assert request.headers.get("cookie") == "MUSIC_U=local-session-value"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 101},
                    "profile": {"userId": 101, "nickname": "Runtime User"},
                },
            )
        raise AssertionError(f"unexpected platform request: {request.url.host}")

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    return runtime, seen


def test_production_session_store_is_ciphertext_only_and_restarts(tmp_path: Path) -> None:
    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"z" * 32)
    repository = FileCiphertextRepository(tmp_path / "sessions")
    store = EncryptedSessionStore(repository, AESGCMCipher.from_key_file(key_file))

    from musicdl_web.sessions import import_cookie_header

    store.save(
        import_cookie_header(Source.NETEASE, "MUSIC_U=credential-value"), version=7
    )
    encrypted = (tmp_path / "sessions" / "netease.bin").read_bytes()

    assert b"credential-value" not in encrypted
    assert os.stat(tmp_path / "sessions" / "netease.bin").st_mode & 0o777 == 0o600
    loaded = EncryptedSessionStore(
        FileCiphertextRepository(tmp_path / "sessions"),
        AESGCMCipher.from_key_file(key_file),
    ).load(Source.NETEASE)
    assert loaded is not None and loaded[1] == 7


async def test_runtime_matches_frontend_api_and_revalidates_exact_tier(
    tmp_path: Path,
) -> None:
    runtime, seen = make_runtime(tmp_path)
    runtime.import_session(Source.NETEASE, "MUSIC_U=local-session-value")
    search = runtime.search("runtime", Source.NETEASE, 1)
    assert search.groups[0].tracks[0].track_id == "101"

    snapshot = runtime.quality_snapshot(Source.NETEASE, "101")
    assert [option.id for option in snapshot.options] == ["standard", "lossless"]
    request = await runtime.prepare_request(
        Source.NETEASE,
        "101",
        "lossless",
        snapshot.snapshot_id,
        Delivery.SERVER,
    )
    grant = await runtime.revalidate_exact(request)

    assert request.quality is Quality.LOSSLESS
    assert grant.quality_id == "lossless"
    assert grant.allowed_hosts == frozenset({"m10.music.126.net"})
    assert "https://" not in repr(grant)
    assert all(request.url.scheme == "https" for request in seen)
    runtime.close()


async def test_runtime_upgrades_http_netease_media_urls_to_https(tmp_path: Path) -> None:
    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"h" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/w/nuser/account/get":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 9},
                    "profile": {"userId": 9, "nickname": "HTTP CDN"},
                },
            )
        if request.url.path == "/api/song/enhance/player/url/v1":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "id": 202,
                            "level": "standard",
                            "url": "http://m801.music.126.net/media/standard.mp3",
                            "size": 2048,
                            "type": "mp3",
                            "freeTrialInfo": None,
                        }
                    ],
                },
            )
        raise AssertionError(request.url)

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    runtime.import_session(Source.NETEASE, "MUSIC_U=http-cdn-session")
    exact = runtime._resolve_netease_exact("202", "standard", "MUSIC_U=http-cdn-session")
    assert exact.host == "m801.music.126.net"
    assert exact.source_url.startswith("https://m801.music.126.net/")
    runtime.close()


async def test_invalid_platform_cover_does_not_block_download_preparation(
    tmp_path: Path,
) -> None:
    runtime, _ = make_runtime(
        tmp_path,
        cover_body=b"\x89PNG\r\nunsupported",
        cover_type="image/png",
    )
    runtime.import_session(Source.NETEASE, "MUSIC_U=local-session-value")
    runtime.search("runtime", Source.NETEASE, 1)
    snapshot = runtime.quality_snapshot(Source.NETEASE, "101")

    request = await runtime.prepare_request(
        Source.NETEASE,
        "101",
        "lossless",
        snapshot.snapshot_id,
        Delivery.SERVER,
    )

    assert await runtime.artwork_for_request(request) is None
    runtime.close()


async def test_platform_router_is_secret_free_and_qq_capabilities_fail_closed(
    tmp_path: Path,
) -> None:
    runtime, _ = make_runtime(tmp_path)
    application = FastAPI()
    application.include_router(create_platform_router(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        search = await client.get("/api/v1/search?q=runtime&source=netease&page=1")
        cover_url = search.json()["groups"][0]["tracks"][0]["coverUrl"]
        cover = await client.get(cover_url)
        no_session = await client.get("/api/v1/tracks/netease/101/qualities")
        imported = await client.post(
            "/api/v1/sessions/netease/import",
            json={"value": "MUSIC_U=local-session-value"},
        )
        sessions = await client.get("/api/v1/sessions")
        qq_quality = await client.get("/api/v1/tracks/qq/track-mid/qualities")
        preview = await client.get("/api/v1/tracks/netease/101/preview")

    assert imported.status_code == 200
    assert cover_url == "/api/v1/covers/netease/101"
    assert "music.126.net" not in json.dumps(search.json())
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/jpeg"
    assert cover.content == b"\xff\xd8\xff\xe0runtime-jpeg\xff\xd9"
    assert set(imported.json()) == {
        "source",
        "state",
        "displayName",
        "qrEnabled",
        "checkedAt",
        "message",
    }
    assert "local-session-value" not in json.dumps(sessions.json())
    assert no_session.status_code == 401
    assert no_session.json()["code"] == "session_required"
    assert qq_quality.status_code == 503
    assert qq_quality.json()["code"] == "quality_unavailable"
    assert preview.status_code == 204
    runtime.close()


async def test_production_lifespan_replaces_import_safe_routes_and_starts_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import musicdl_web.runtime as runtime_module
    from musicdl_web.app import create_app

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"p" * 32)
    settings = RuntimeSettings(
        session_key_file=key_file,
        session_root=tmp_path / "sessions",
        database_file=tmp_path / "musicdl.sqlite3",
        work_root=tmp_path / "work",
        browser_root=tmp_path / "browser",
        music_root=tmp_path / "music",
        staging_root=tmp_path / "staging",
        artwork_root=tmp_path / "artwork",
    )
    runtime = build_application_runtime(settings)
    monkeypatch.setattr(runtime_module, "build_application_runtime", lambda: runtime)
    application = create_app(production=True)

    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            sessions = await client.get("/api/v1/sessions")
            tasks = await client.get("/api/v1/tasks?scope=active")

    assert sessions.status_code == 200
    assert sessions.json()[0]["message"] is None
    assert tasks.status_code == 200
    assert tasks.json() == []
