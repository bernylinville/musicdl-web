"""Netease Cloud Music search metadata adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..errors import PlatformResponseError
from ..models import SearchResults, Source, Track
from ..network import PlatformHttpClient
from ..platforms.errors import CapabilityUnavailable, ExactQualityMismatch
from ..platforms.quality import (
    DownloadGrant,
    FidelityFamily,
    QualityBinding,
    QualityOption,
    QualitySnapshot,
    QualitySnapshotStore,
)
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


class NeteaseCapabilityParser:
    """Strict parser for redacted, platform-owned capability responses.

    Network acquisition intentionally remains outside this class until a credential-aware
    HTTP boundary is wired. Absence of an observed response is never treated as success.
    """

    _OPTIONS = {
        "standard": QualityOption(
            quality_id="standard", label="标准", family=FidelityFamily.LINEAR, rank=10
        ),
        "higher": QualityOption(
            quality_id="higher", label="较高", family=FidelityFamily.LINEAR, rank=20
        ),
        "exhigh": QualityOption(
            quality_id="exhigh", label="极高", family=FidelityFamily.LINEAR, rank=30
        ),
        "lossless": QualityOption(
            quality_id="lossless", label="无损", family=FidelityFamily.LINEAR, rank=40
        ),
        "hires": QualityOption(
            quality_id="hires", label="Hi-Res", family=FidelityFamily.LINEAR, rank=50
        ),
        "jyeffect": QualityOption(
            quality_id="jyeffect", label="高清臻音", family=FidelityFamily.LINEAR, rank=60
        ),
        "dolby": QualityOption(
            quality_id="dolby", label="杜比全景声", family=FidelityFamily.DOLBY
        ),
        "sky": QualityOption(
            quality_id="sky", label="沉浸环绕声", family=FidelityFamily.IMMERSIVE
        ),
    }

    def __init__(
        self,
        snapshots: QualitySnapshotStore,
        *,
        capability_enabled: bool = False,
        approved_media_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if any(
            re.fullmatch(r"m[0-9]+\.music\.126\.net", host) is None
            for host in approved_media_hosts
        ):
            raise ValueError("approved Netease media host is invalid")
        self._snapshots = snapshots
        self._capability_enabled = capability_enabled
        self._approved_media_hosts = approved_media_hosts

    def parse_quality_snapshot(
        self,
        payload: Any,
        *,
        track_id: str,
        session_version: int,
        now: datetime | None = None,
    ) -> QualitySnapshot:
        if not self._capability_enabled:
            raise CapabilityUnavailable("netease quality capability is unavailable")
        root = require_mapping(payload, Source.NETEASE, "invalid quality response")
        if root.get("code") != 200:
            raise CapabilityUnavailable("netease quality capability is unavailable")
        entries = require_list(root.get("data"), Source.NETEASE, "invalid quality entries")
        entry = _find_track(entries, Source.NETEASE, track_id)
        qualities = require_list(
            entry.get("qualities"), Source.NETEASE, "missing explicit quality capability"
        )
        options: list[QualityOption] = []
        for value in qualities:
            quality = require_mapping(value, Source.NETEASE, "invalid quality capability")
            quality_id = require_string(
                quality.get("level"), Source.NETEASE, "invalid quality level"
            )
            if quality.get("available") is not True:
                continue
            option = self._OPTIONS.get(quality_id)
            if option is None:
                raise PlatformResponseError(Source.NETEASE, "unknown quality level")
            options.append(option)
        if not options:
            raise CapabilityUnavailable("netease quality capability is unavailable")
        return self._snapshots.create(
            source=Source.NETEASE,
            track_id=track_id,
            session_version=session_version,
            options=tuple(options),
            now=now,
        )

    def parse_exact_response(
        self,
        payload: Any,
        binding: QualityBinding,
        *,
        now: datetime | None = None,
    ) -> DownloadGrant:
        if not self._capability_enabled or not self._approved_media_hosts:
            raise CapabilityUnavailable("netease exact resolution is unavailable")
        if binding.source is not Source.NETEASE:
            raise ExactQualityMismatch("quality binding belongs to another platform")
        root = require_mapping(payload, Source.NETEASE, "invalid exact response")
        if root.get("code") != 200:
            raise CapabilityUnavailable("netease exact resolution is unavailable")
        entries = require_list(root.get("data"), Source.NETEASE, "invalid exact entries")
        entry = _find_track(entries, Source.NETEASE, binding.track_id)
        actual = require_string(entry.get("level"), Source.NETEASE, "missing exact level")
        if actual != binding.option.quality_id:
            raise ExactQualityMismatch("netease returned a different quality")
        source_url = require_string(entry.get("url"), Source.NETEASE, "missing exact location")
        _require_platform_https_url(
            source_url, self._approved_media_hosts, Source.NETEASE
        )
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PlatformResponseError(Source.NETEASE, "invalid exact size")
        current = now or datetime.now(UTC)
        return DownloadGrant(
            source=Source.NETEASE,
            track_id=binding.track_id,
            quality_id=actual,
            quality_rank=binding.option.rank,
            expires_at=current + timedelta(minutes=2),
            allowed_hosts=self._approved_media_hosts,
            _source_url=source_url,
            content_type=_content_type(entry.get("type")),
            expected_bytes=size,
        )


def _map_track(value: Any) -> Track:
    song = require_mapping(value, Source.NETEASE, "invalid song")
    artists_value = require_list(song.get("ar"), Source.NETEASE, "invalid artists")
    artist_names: list[str] = []
    artist_ids: list[str] = []
    for raw_artist in artists_value:
        artist = require_mapping(raw_artist, Source.NETEASE, "invalid artist")
        artist_names.append(
            require_string(artist.get("name"), Source.NETEASE, "invalid artist name")
        )
        artist_ids.append(_optional_positive_id(artist.get("id")) or "")
    album = require_mapping(song.get("al"), Source.NETEASE, "invalid album")
    duration = song.get("dt")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        raise PlatformResponseError(Source.NETEASE, "invalid duration")
    return Track(
        source=Source.NETEASE,
        track_id=require_string(song.get("id"), Source.NETEASE, "invalid track id"),
        title=require_string(song.get("name"), Source.NETEASE, "invalid title"),
        artists=tuple(artist_names),
        album=require_string(album.get("name", ""), Source.NETEASE, "invalid album name"),
        duration_ms=duration,
        cover_url=normalize_cover_url(
            album.get("picUrl"), allowed_host_suffixes=("music.126.net",)
        ),
        artist_ids=tuple(artist_ids),
        album_id=_optional_positive_id(album.get("id")),
    )


def _optional_positive_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return value
    return None


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


def _find_track(entries: list[Any], source: Source, track_id: str) -> Mapping[str, Any]:
    for value in entries:
        entry = require_mapping(value, source, "invalid track capability")
        if str(entry.get("id")) == track_id:
            return entry
    raise CapabilityUnavailable(f"{source.value} track capability is unavailable")


def _require_platform_https_url(
    value: str, allowed_hosts: frozenset[str], source: Source
) -> None:
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or host not in allowed_hosts
    ):
        raise PlatformResponseError(source, "exact location is not platform-owned")


def _content_type(value: Any) -> str | None:
    if value == "flac":
        return "audio/flac"
    if value == "mp3":
        return "audio/mpeg"
    return None
