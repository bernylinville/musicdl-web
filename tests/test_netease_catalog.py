from __future__ import annotations

import httpx
from musicdl_web.adapters.netease_catalog import NeteaseCatalog
from musicdl_web.models import Source


def _song(track_id: int, title: str, *, artist_id: int = 9, album_id: int = 8) -> dict:
    return {
        "id": track_id,
        "name": title,
        "ar": [{"id": artist_id, "name": "Fixture Artist"}],
        "al": {
            "id": album_id,
            "name": "Fixture Album",
            "picUrl": f"https://p1.music.126.net/cover-{track_id}.jpg",
        },
        "dt": 180000,
    }


def _eapi_path(url: httpx.URL) -> str:
    path = url.path
    assert path.startswith("/eapi/")
    return "/api/" + path.removeprefix("/eapi/")


def test_artist_tracks_page_and_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _eapi_path(request.url) == "/api/v1/artist/songs"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "more": True,
                "total": 100,
                "songs": [_song(1, "A"), _song(2, "B")],
            },
        )

    catalog = NeteaseCatalog(transport=httpx.MockTransport(handler))
    result, title = catalog.artist_tracks("9", page=1, limit=2, title_hint="伍佰")

    assert title == "伍佰"
    assert result.source is Source.NETEASE
    assert [t.track_id for t in result.tracks] == ["1", "2"]
    assert result.has_more is True
    assert result.tracks[0].artist_ids == ("9",)
    assert result.tracks[0].album_id == "8"


def test_album_tracks_paginates_full_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _eapi_path(request.url) == "/api/v1/album/77"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "album": {"id": 77, "name": "泪桥"},
                "songs": [_song(1, "A", album_id=77), _song(2, "B", album_id=77), _song(3, "C", album_id=77)],
            },
        )

    catalog = NeteaseCatalog(transport=httpx.MockTransport(handler))
    page1, title = catalog.album_tracks("77", page=1, limit=2)
    page2, _ = catalog.album_tracks("77", page=2, limit=2)

    assert title == "泪桥"
    assert [t.track_id for t in page1.tracks] == ["1", "2"]
    assert page1.has_more is True
    assert [t.track_id for t in page2.tracks] == ["3"]
    assert page2.has_more is False
