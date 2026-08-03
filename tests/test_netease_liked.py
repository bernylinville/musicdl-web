from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from musicdl_web.adapters.netease_liked import NeteaseLikedCatalog
from musicdl_web.models import Source
from musicdl_web.sessions.netease_eapi import eapi_params


def _song(track_id: int, title: str) -> dict:
    return {
        "id": track_id,
        "name": title,
        "ar": [{"name": "Artist"}],
        "al": {
            "name": "Album",
            "picUrl": f"https://p1.music.126.net/cover-{track_id}.jpg",
        },
        "dt": 200000,
    }


def _eapi_path(url: httpx.URL) -> str:
    # https://interface.music.163.com/eapi/user/playlist -> /api/user/playlist
    path = url.path
    assert path.startswith("/eapi/")
    return "/api/" + path.removeprefix("/eapi/")


def test_liked_catalog_maps_special_type_playlist_and_pages(tmp_path: Path) -> None:
    del tmp_path
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api = _eapi_path(request.url)
        calls.append(api)
        assert request.method == "POST"
        assert "music_u" in request.headers.get("cookie", "").lower() or "MUSIC_U" in (
            request.headers.get("cookie") or ""
        )
        if api == "/api/nuser/account/get":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 42},
                    "profile": {"userId": 42, "nickname": "op"},
                },
            )
        if api == "/api/user/playlist":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "playlist": [
                        {
                            "id": 9001,
                            "name": "op喜欢的音乐",
                            "specialType": 5,
                            "userId": 42,
                            "trackCount": 3,
                        },
                        {
                            "id": 9002,
                            "name": "other",
                            "specialType": 0,
                            "userId": 42,
                            "trackCount": 1,
                        },
                    ],
                },
            )
        if api == "/api/v6/playlist/detail":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "playlist": {
                        "id": 9001,
                        "tracks": [
                            _song(1, "One"),
                            _song(2, "Two"),
                            _song(3, "Three"),
                        ],
                    },
                },
            )
        return httpx.Response(500, json={"code": 500})

    catalog = NeteaseLikedCatalog(transport=httpx.MockTransport(handler))
    page1 = catalog.list_liked({"MUSIC_U": "token"}, page=1, limit=2)
    page2 = catalog.list_liked({"MUSIC_U": "token"}, page=2, limit=2)

    assert page1.source is Source.NETEASE
    assert [t.track_id for t in page1.tracks] == ["1", "2"]
    assert page1.has_more is True
    assert [t.track_id for t in page2.tracks] == ["3"]
    assert page2.has_more is False
    assert page1.tracks[0].cover_url and "music.126.net" in page1.tracks[0].cover_url
    assert "/api/nuser/account/get" in calls
    assert "/api/user/playlist" in calls
    assert "/api/v6/playlist/detail" in calls


def test_liked_catalog_requires_cookies() -> None:
    catalog = NeteaseLikedCatalog(
        transport=httpx.MockTransport(lambda request: pytest.fail("no request"))
    )
    with pytest.raises(PermissionError):
        catalog.list_liked({})


def test_liked_catalog_rejects_missing_liked_playlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        api = _eapi_path(request.url)
        if api == "/api/nuser/account/get":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 7},
                    "profile": {"userId": 7, "nickname": "x"},
                },
            )
        if api == "/api/user/playlist":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "playlist": [
                        {
                            "id": 1,
                            "name": "not liked",
                            "specialType": 0,
                            "userId": 7,
                        }
                    ],
                },
            )
        return httpx.Response(500, json={"code": 500})

    catalog = NeteaseLikedCatalog(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception):
        catalog.fetch_all({"MUSIC_U": "token"})


def test_eapi_params_still_stable_for_liked_paths() -> None:
    # Guard that liked endpoints use the same eapi wire format as QR login.
    cipher = eapi_params("/api/song/like/get", {"uid": 1})
    assert cipher.isupper()
    assert all(c in "0123456789ABCDEF" for c in cipher)
    assert len(cipher) % 32 == 0


def test_fetch_liked_ids_and_set_like() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        api = _eapi_path(request.url)
        calls.append(api)
        if api == "/api/nuser/account/get":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 42},
                    "profile": {"userId": 42, "nickname": "op"},
                },
            )
        if api == "/api/song/like/get":
            return httpx.Response(200, json={"code": 200, "ids": [101, 202]})
        if api == "/api/song/like":
            return httpx.Response(200, json={"code": 200, "playlistId": 1})
        return httpx.Response(500, json={"code": 500})

    catalog = NeteaseLikedCatalog(transport=httpx.MockTransport(handler))
    assert catalog.fetch_liked_ids({"MUSIC_U": "token"}) == frozenset({"101", "202"})
    catalog.set_like({"MUSIC_U": "token"}, "101", liked=False)
    assert "/api/song/like/get" in calls
    assert "/api/song/like" in calls
