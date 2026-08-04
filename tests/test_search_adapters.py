from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from musicdl_web.adapters import NeteaseAdapter, QQAdapter
from musicdl_web.errors import PlatformResponseError
from musicdl_web.models import Source


def test_netease_search_maps_stable_model_and_pagination(fixture_json) -> None:
    payload = fixture_json("netease_search.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(NeteaseAdapter.ENDPOINT)
        assert request.method == "POST"
        assert "cookie" not in request.headers
        assert request.headers["referer"] == "https://music.163.com/"
        assert request.headers["user-agent"] == "musicdl-web-search-spike/0.1"
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert request.content.decode() == "s=test&type=1&limit=2&offset=0"
        return httpx.Response(200, json=payload)

    adapter = NeteaseAdapter(transport=httpx.MockTransport(handler))
    result = adapter.search("test", page=1, limit=2)

    assert result.source is Source.NETEASE
    assert result.page == 1
    assert result.has_more is True
    assert result.tracks[0].model_dump(mode="json") == {
        "source": "netease",
        "track_id": "101",
        "title": "Fixture Song",
        "artists": ["Fixture Artist"],
        "album": "Fixture Album",
        "duration_ms": 213456,
        "cover_url": "https://p1.music.126.net/redacted/cover.jpg",
        "artist_ids": ["1"],
        "album_id": "10",
        "liked_at_ms": None,
        "play_count": None,
    }
    _assert_no_raw_platform_data(result.model_dump(mode="json"))


def test_qq_search_maps_source_specific_model_and_endpoint(fixture_json) -> None:
    payload = fixture_json("qq_search.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(QQAdapter.ENDPOINT)
        assert request.method == "POST"
        assert "cookie" not in request.headers
        assert request.headers["referer"] == "https://y.qq.com/"
        assert request.headers["user-agent"] == "musicdl-web-search-spike/0.1"
        body = json.loads(request.content)
        assert body == {
            "req_1": {
                "module": "music.search.SearchCgiService",
                "method": "DoSearchForQQMusicDesktop",
                "param": {
                    "query": "fixture",
                    "search_type": 0,
                    "num_per_page": 1,
                    "page_num": 2,
                },
            }
        }
        return httpx.Response(200, json=payload)

    adapter = QQAdapter(transport=httpx.MockTransport(handler))
    result = adapter.search("fixture", page=2, limit=1)

    assert result.source is Source.QQ
    assert result.page == 2
    assert result.has_more is True
    assert result.tracks[0].model_dump(mode="json") == {
        "source": "qq",
        "track_id": "fixture-track-mid",
        "title": "QQ Fixture Song",
        "artists": ["QQ Fixture Artist"],
        "album": "QQ Fixture Album",
        "duration_ms": 187000,
        "cover_url": ("https://y.gtimg.cn/music/photo_new/T002R300x300M000fixture-album-mid.jpg"),
        "artist_ids": [""],
        "album_id": None,
        "liked_at_ms": None,
        "play_count": None,
    }
    _assert_no_raw_platform_data(result.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("adapter_factory", "fixture_name", "expected_source"),
    [
        (NeteaseAdapter, "qq_search.json", Source.NETEASE),
        (QQAdapter, "netease_search.json", Source.QQ),
    ],
)
def test_platform_fixtures_cannot_cross_adapter_boundaries(
    adapter_factory: Callable[..., Any],
    fixture_name: str,
    expected_source: Source,
    fixture_json,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=fixture_json(fixture_name))
    )

    with pytest.raises(PlatformResponseError) as exc_info:
        adapter_factory(transport=transport).search("fixture")

    assert exc_info.value.source is expected_source
    assert "http" not in str(exc_info.value).lower()


def test_untrusted_cover_host_is_discarded(fixture_json) -> None:
    payload = fixture_json("netease_search.json")
    payload["result"]["songs"][0]["al"]["picUrl"] = "https://evil.example/cover.jpg"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    result = NeteaseAdapter(transport=transport).search("fixture")

    assert result.tracks[0].cover_url is None


def test_qq_pagination_falls_back_to_next_page(fixture_json) -> None:
    payload = fixture_json("qq_search.json")
    del payload["req_1"]["data"]["meta"]["sum"]
    payload["req_1"]["data"]["meta"]["nextpage"] = 2
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    result = QQAdapter(transport=transport).search("fixture", page=1, limit=20)

    assert result.has_more is True


@pytest.mark.parametrize(
    ("adapter_factory", "fixture_name", "path", "bad_code"),
    [
        (NeteaseAdapter, "netease_search.json", ("code",), 500),
        (QQAdapter, "qq_search.json", ("code",), 1),
        (QQAdapter, "qq_search.json", ("req_1", "code"), 1),
    ],
)
def test_unsuccessful_platform_code_is_normalized(
    adapter_factory: Callable[..., Any],
    fixture_name: str,
    path: tuple[str, ...],
    bad_code: int,
    fixture_json,
) -> None:
    payload = fixture_json(fixture_name)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad_code
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(PlatformResponseError):
        adapter_factory(transport=transport).search("fixture")


def _assert_no_raw_platform_data(value: Any) -> None:
    forbidden = {"download_url", "downloadUrl", "raw_data", "file", "response", "url"}
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for nested in value.values():
            _assert_no_raw_platform_data(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_raw_platform_data(nested)
