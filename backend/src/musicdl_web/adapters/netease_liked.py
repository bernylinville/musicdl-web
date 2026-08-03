"""Netease account liked-music catalog (红心 / 我喜欢的音乐).

Uses the authenticated eapi surface already proven for QR login. The operator's
session cookies identify the account; no playlist URL scraping or third-party
endpoints are involved.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from musicdl_web.errors import PlatformResponseError
from musicdl_web.models import SearchResults, Source, Track
from musicdl_web.sessions.netease_eapi import NeteaseEapiClient

from ._shared import require_list, require_mapping
from .netease import _map_track

# Netease marks the personal "liked music" playlist with specialType=5.
_LIKED_SPECIAL_TYPE = 5
_ACCOUNT_API = "/api/nuser/account/get"
_USER_PLAYLIST_API = "/api/user/playlist"
_PLAYLIST_DETAIL_API = "/api/v6/playlist/detail"
_LIKELIST_API = "/api/song/like/get"
_LIKE_API = "/api/song/like"


class NeteaseLikedCatalog:
    """Fetch the operator's Netease liked tracks under an active session."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = transport

    def fetch_all(self, cookies: Mapping[str, str]) -> tuple[Track, ...]:
        """Return the full liked-music catalog for the session account."""

        if not cookies:
            raise PermissionError("platform session is required")
        client = NeteaseEapiClient(transport=self._transport)
        try:
            client.merge_cookies(cookies)
            uid = _resolve_uid(client)
            playlist_id = _resolve_liked_playlist_id(client, uid)
            return _playlist_tracks(client, playlist_id)
        finally:
            client.close()

    def list_liked(
        self,
        cookies: Mapping[str, str],
        *,
        page: int = 1,
        limit: int = 50,
    ) -> SearchResults:
        _validate_page(page, limit)
        tracks = self.fetch_all(cookies)
        start = (page - 1) * limit
        page_tracks = tracks[start : start + limit]
        return SearchResults(
            source=Source.NETEASE,
            tracks=page_tracks,
            page=page,
            has_more=start + limit < len(tracks),
        )

    def fetch_liked_ids(self, cookies: Mapping[str, str]) -> frozenset[str]:
        """Return the operator's red-heart track ids (lightweight vs full playlist)."""

        if not cookies:
            raise PermissionError("platform session is required")
        client = NeteaseEapiClient(transport=self._transport)
        try:
            client.merge_cookies(cookies)
            uid = _resolve_uid(client)
            root = _eapi_json(client, _LIKELIST_API, {"uid": uid})
        finally:
            client.close()
        if root.get("code") != 200:
            raise PlatformResponseError(Source.NETEASE, "liked id list failed")
        raw_ids = root.get("ids")
        if not isinstance(raw_ids, list):
            raise PlatformResponseError(Source.NETEASE, "invalid liked id list")
        ids: set[str] = set()
        for value in raw_ids:
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value > 0:
                ids.add(str(value))
            elif isinstance(value, str) and value.isdigit() and int(value) > 0:
                ids.add(value)
        return frozenset(ids)

    def set_like(
        self,
        cookies: Mapping[str, str],
        track_id: str,
        *,
        liked: bool,
    ) -> None:
        """Red-heart or un-heart a single Netease track for the session account."""

        if not cookies:
            raise PermissionError("platform session is required")
        if not track_id or not track_id.isdigit() or int(track_id) <= 0:
            raise ValueError("invalid track id")
        client = NeteaseEapiClient(transport=self._transport)
        try:
            client.merge_cookies(cookies)
            root = _eapi_json(
                client,
                _LIKE_API,
                {"trackId": int(track_id), "like": liked, "time": 3},
            )
        finally:
            client.close()
        if root.get("code") != 200:
            raise PlatformResponseError(Source.NETEASE, "like update failed")


def _resolve_uid(client: NeteaseEapiClient) -> int:
    root = _eapi_json(client, _ACCOUNT_API, {})
    if root.get("code") != 200:
        raise PlatformResponseError(Source.NETEASE, "account lookup failed")
    profile = root.get("profile")
    account = root.get("account")
    if not isinstance(profile, Mapping) or not isinstance(account, Mapping):
        raise PermissionError("platform session is required")
    user_id = profile.get("userId")
    account_id = account.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise PlatformResponseError(Source.NETEASE, "invalid account identity")
    if account_id != user_id:
        raise PlatformResponseError(Source.NETEASE, "invalid account identity")
    return user_id


def _resolve_liked_playlist_id(client: NeteaseEapiClient, uid: int) -> int:
    root = _eapi_json(
        client,
        _USER_PLAYLIST_API,
        {"uid": uid, "limit": 30, "offset": 0, "includeVideo": True},
    )
    if root.get("code") != 200:
        raise PlatformResponseError(Source.NETEASE, "playlist list failed")
    playlists = require_list(root.get("playlist", []), Source.NETEASE, "invalid playlists")
    for value in playlists:
        entry = require_mapping(value, Source.NETEASE, "invalid playlist")
        special = entry.get("specialType")
        owner = entry.get("userId")
        playlist_id = entry.get("id")
        if (
            special == _LIKED_SPECIAL_TYPE
            and owner == uid
            and isinstance(playlist_id, int)
            and not isinstance(playlist_id, bool)
            and playlist_id > 0
        ):
            return playlist_id
    raise PlatformResponseError(Source.NETEASE, "liked playlist is unavailable")


def _playlist_tracks(client: NeteaseEapiClient, playlist_id: int) -> tuple[Track, ...]:
    root = _eapi_json(
        client,
        _PLAYLIST_DETAIL_API,
        {"id": playlist_id, "n": 100000, "s": 8},
    )
    if root.get("code") != 200:
        raise PlatformResponseError(Source.NETEASE, "playlist detail failed")
    playlist = require_mapping(root.get("playlist"), Source.NETEASE, "missing playlist")
    songs = require_list(playlist.get("tracks", []), Source.NETEASE, "invalid tracks")
    tracks: list[Track] = []
    for song in songs:
        try:
            tracks.append(_map_track(song))
        except PlatformResponseError:
            # Skip unmappable rows rather than failing the whole liked catalog.
            continue
    return tuple(tracks)


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


def _validate_page(page: int, limit: int) -> None:
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
