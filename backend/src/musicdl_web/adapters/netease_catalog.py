"""Netease artist / album track catalogs via eapi (same wire format as QR login)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from musicdl_web.errors import PlatformResponseError
from musicdl_web.models import SearchResults, Source, Track
from musicdl_web.sessions.netease_eapi import NeteaseEapiClient

from ._shared import require_list, require_mapping
from .netease import _map_track

_ARTIST_SONGS_API = "/api/v1/artist/songs"
_ALBUM_API_PREFIX = "/api/v1/album/"


class NeteaseCatalog:
    """Browse Netease artist and album song lists as downloadable platform tracks."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = transport

    def artist_tracks(
        self,
        artist_id: str,
        *,
        page: int = 1,
        limit: int = 50,
        cookies: Mapping[str, str] | None = None,
        title_hint: str | None = None,
    ) -> tuple[SearchResults, str]:
        """Return one page of artist songs and a display title."""

        _validate_id(artist_id)
        _validate_page(page, limit)
        client = NeteaseEapiClient(transport=self._transport)
        try:
            if cookies:
                client.merge_cookies(cookies)
            root = _eapi_json(
                client,
                _ARTIST_SONGS_API,
                {
                    "id": int(artist_id),
                    "private_cloud": "true",
                    "work_type": 1,
                    "order": "hot",
                    "offset": (page - 1) * limit,
                    "limit": limit,
                },
            )
        finally:
            client.close()
        if root.get("code") != 200:
            raise PlatformResponseError(Source.NETEASE, "artist songs unavailable")
        songs = require_list(root.get("songs", []), Source.NETEASE, "invalid artist songs")
        tracks = _map_songs(songs)
        more = root.get("more") is True
        # Prefer the caller's hint; otherwise use the first song's matching artist name.
        title = title_hint or _artist_title_from_tracks(tracks, artist_id) or f"歌手 {artist_id}"
        return (
            SearchResults(
                source=Source.NETEASE,
                tracks=tracks,
                page=page,
                has_more=more,
            ),
            title,
        )

    def album_tracks(
        self,
        album_id: str,
        *,
        page: int = 1,
        limit: int = 50,
        cookies: Mapping[str, str] | None = None,
        title_hint: str | None = None,
    ) -> tuple[SearchResults, str]:
        """Return one page of album songs and the album display title."""

        _validate_id(album_id)
        _validate_page(page, limit)
        client = NeteaseEapiClient(transport=self._transport)
        try:
            if cookies:
                client.merge_cookies(cookies)
            root = _eapi_json(client, f"{_ALBUM_API_PREFIX}{album_id}", {})
        finally:
            client.close()
        if root.get("code") != 200:
            raise PlatformResponseError(Source.NETEASE, "album unavailable")
        album = require_mapping(root.get("album"), Source.NETEASE, "missing album")
        album_name = str(album.get("name") or title_hint or f"专辑 {album_id}")
        songs = require_list(root.get("songs", []), Source.NETEASE, "invalid album songs")
        tracks = _map_songs(songs)
        start = (page - 1) * limit
        page_tracks = tracks[start : start + limit]
        return (
            SearchResults(
                source=Source.NETEASE,
                tracks=page_tracks,
                page=page,
                has_more=start + limit < len(tracks),
            ),
            title_hint or album_name,
        )


def _map_songs(songs: list[Any]) -> tuple[Track, ...]:
    tracks: list[Track] = []
    for song in songs:
        try:
            tracks.append(_map_track(song))
        except PlatformResponseError:
            continue
    return tuple(tracks)


def _artist_title_from_tracks(tracks: tuple[Track, ...], artist_id: str) -> str | None:
    for track in tracks:
        for name, aid in zip(track.artists, track.artist_ids, strict=False):
            if aid == artist_id and name:
                return name
        if track.artists:
            return track.artists[0]
    return None


def _eapi_json(
    client: NeteaseEapiClient, api_path: str, data: Mapping[str, Any]
) -> dict[str, Any]:
    response = client.post_eapi(api_path, data)
    try:
        payload = response.json()
    except ValueError as exc:
        raise PlatformResponseError(Source.NETEASE, "invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PlatformResponseError(Source.NETEASE, "invalid JSON object")
    return payload


def _validate_id(value: str) -> None:
    if not value or not value.isdigit() or int(value) <= 0:
        raise ValueError("invalid catalog id")


def _validate_page(page: int, limit: int) -> None:
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
