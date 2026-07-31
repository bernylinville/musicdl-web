"""QQ Music search metadata adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from ..errors import PlatformResponseError
from ..models import SearchResults, Source, Track
from ..network import PlatformHttpClient
from ._shared import normalize_cover_url, require_list, require_mapping, require_string


class QQAdapter:
    """Experimental search spike for QQ Music's private Web endpoint."""

    ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    HEADERS = {
        "Referer": "https://y.qq.com/",
        "User-Agent": "musicdl-web-search-spike/0.1",
    }

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = PlatformHttpClient(
            allowed_hosts={"u.y.qq.com"},
            transport=transport,
        )

    def search(self, query: str, *, page: int = 1, limit: int = 20) -> SearchResults:
        _validate_search_input(query, page, limit)
        response = self._http.post(
            self.ENDPOINT,
            json={
                "req_1": {
                    "module": "music.search.SearchCgiService",
                    "method": "DoSearchForQQMusicDesktop",
                    "param": {
                        "query": query,
                        "search_type": 0,
                        "num_per_page": limit,
                        "page_num": page,
                    },
                }
            },
            headers=self.HEADERS,
        )
        payload = _read_json(response)
        if payload.get("code") != 0:
            raise PlatformResponseError(Source.QQ, "unsuccessful platform code")
        req = require_mapping(payload.get("req_1"), Source.QQ, "missing req_1")
        if req.get("code") != 0:
            raise PlatformResponseError(Source.QQ, "unsuccessful search code")
        data = require_mapping(req.get("data"), Source.QQ, "missing data")
        body = require_mapping(data.get("body"), Source.QQ, "missing body")
        song_data = require_mapping(body.get("song"), Source.QQ, "missing songs")
        songs = require_list(song_data.get("list", []), Source.QQ, "invalid songs")
        tracks = tuple(_map_track(song) for song in songs)
        meta = require_mapping(data.get("meta", {}), Source.QQ, "invalid pagination metadata")
        has_more = _has_more(meta, song_data, page, limit)
        return SearchResults(source=Source.QQ, tracks=tracks, page=page, has_more=has_more)

    def close(self) -> None:
        self._http.close()

    @property
    def accessed_hosts(self) -> tuple[str, ...]:
        return self._http.accessed_hosts


def _map_track(value: Any) -> Track:
    song = require_mapping(value, Source.QQ, "invalid song")
    singers_value = require_list(song.get("singer"), Source.QQ, "invalid artists")
    artists = tuple(
        require_string(
            require_mapping(singer, Source.QQ, "invalid artist").get("name"),
            Source.QQ,
            "invalid artist name",
        )
        for singer in singers_value
    )
    album = require_mapping(song.get("album", {}), Source.QQ, "invalid album")
    interval = song.get("interval")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 0:
        raise PlatformResponseError(Source.QQ, "invalid duration")
    album_mid = album.get("pmid") or album.get("mid")
    derived_cover = (
        f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg"
        if isinstance(album_mid, str) and album_mid
        else None
    )
    return Track(
        source=Source.QQ,
        track_id=require_string(song.get("mid") or song.get("id"), Source.QQ, "invalid track id"),
        title=require_string(song.get("title") or song.get("name"), Source.QQ, "invalid title"),
        artists=artists,
        album=require_string(album.get("name", ""), Source.QQ, "invalid album name"),
        duration_ms=interval * 1000,
        cover_url=normalize_cover_url(
            derived_cover or album.get("picUrl"), allowed_host_suffixes=("y.gtimg.cn",)
        ),
    )


def _read_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return dict(require_mapping(response.json(), Source.QQ, "invalid JSON object"))
    except (ValueError, TypeError):
        raise PlatformResponseError(Source.QQ, "invalid JSON") from None


def _validate_search_input(query: str, page: int, limit: int) -> None:
    if not query.strip():
        raise ValueError("query must not be blank")
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")


def _has_more(
    meta: Mapping[str, Any],
    song_data: Mapping[str, Any],
    page: int,
    limit: int,
) -> bool:
    total = meta.get("sum", song_data.get("totalnum"))
    if isinstance(total, int) and not isinstance(total, bool):
        return page * limit < total
    next_page = meta.get("nextpage")
    if isinstance(next_page, int) and not isinstance(next_page, bool):
        return next_page > page
    return False
