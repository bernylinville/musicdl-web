"""Netease Cloud Music search metadata adapter."""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import PlatformResponseError
from ..models import SearchResults, Source, Track
from ..network import PlatformHttpClient
from ._shared import normalize_cover_url, require_list, require_mapping, require_string


class NeteaseAdapter:
    """Experimental search spike for Netease's private Web endpoint."""

    ENDPOINT = "https://music.163.com/api/cloudsearch/pc"
    HEADERS = {
        "Referer": "https://music.163.com/",
        "User-Agent": "musicdl-web-search-spike/0.1",
    }

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = PlatformHttpClient(
            allowed_hosts={"music.163.com"},
            transport=transport,
        )

    def search(self, query: str, *, page: int = 1, limit: int = 20) -> SearchResults:
        _validate_search_input(query, page, limit)
        response = self._http.post(
            self.ENDPOINT,
            data={
                "s": query,
                "type": "1",
                "limit": str(limit),
                "offset": str((page - 1) * limit),
            },
            headers=self.HEADERS,
        )
        payload = _read_json(response)
        if payload.get("code") != 200:
            raise PlatformResponseError(Source.NETEASE, "unsuccessful platform code")
        result = require_mapping(payload.get("result"), Source.NETEASE, "missing result")
        songs = require_list(result.get("songs", []), Source.NETEASE, "invalid songs")
        tracks = tuple(_map_track(song) for song in songs)
        total = result.get("songCount")
        has_more = isinstance(total, int) and not isinstance(total, bool) and page * limit < total
        return SearchResults(
            source=Source.NETEASE,
            tracks=tracks,
            page=page,
            has_more=has_more,
        )

    def close(self) -> None:
        self._http.close()

    @property
    def accessed_hosts(self) -> tuple[str, ...]:
        return self._http.accessed_hosts


def _map_track(value: Any) -> Track:
    song = require_mapping(value, Source.NETEASE, "invalid song")
    artists_value = require_list(song.get("ar"), Source.NETEASE, "invalid artists")
    artists = tuple(
        require_string(
            require_mapping(artist, Source.NETEASE, "invalid artist").get("name"),
            Source.NETEASE,
            "invalid artist name",
        )
        for artist in artists_value
    )
    album = require_mapping(song.get("al"), Source.NETEASE, "invalid album")
    duration = song.get("dt")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        raise PlatformResponseError(Source.NETEASE, "invalid duration")
    return Track(
        source=Source.NETEASE,
        track_id=require_string(song.get("id"), Source.NETEASE, "invalid track id"),
        title=require_string(song.get("name"), Source.NETEASE, "invalid title"),
        artists=artists,
        album=require_string(album.get("name", ""), Source.NETEASE, "invalid album name"),
        duration_ms=duration,
        cover_url=normalize_cover_url(
            album.get("picUrl"), allowed_host_suffixes=("music.126.net",)
        ),
    )


def _read_json(response: httpx.Response) -> dict[str, Any]:
    try:
        return dict(require_mapping(response.json(), Source.NETEASE, "invalid JSON object"))
    except (ValueError, TypeError):
        raise PlatformResponseError(Source.NETEASE, "invalid JSON") from None


def _validate_search_input(query: str, page: int, limit: int) -> None:
    if not query.strip():
        raise ValueError("query must not be blank")
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
