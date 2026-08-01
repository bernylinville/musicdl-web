"""QQ Music search metadata adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

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


class QQCapabilityParser:
    """Strict parser for QQ's observed per-track file capability shape."""

    _FILE_OPTIONS = (
        (
            "size_128mp3",
            QualityOption(
                quality_id="M500", label="标准", family=FidelityFamily.LINEAR, rank=10
            ),
        ),
        (
            "size_320mp3",
            QualityOption(
                quality_id="M800", label="高品质", family=FidelityFamily.LINEAR, rank=30
            ),
        ),
        (
            "size_flac",
            QualityOption(
                quality_id="F000", label="无损", family=FidelityFamily.LINEAR, rank=40
            ),
        ),
        (
            "size_hires",
            QualityOption(
                quality_id="RS01", label="Hi-Res", family=FidelityFamily.LINEAR, rank=50
            ),
        ),
        (
            "size_dolby",
            QualityOption(quality_id="D00A", label="杜比", family=FidelityFamily.DOLBY),
        ),
    )

    def __init__(
        self,
        snapshots: QualitySnapshotStore,
        *,
        capability_enabled: bool = False,
        approved_media_hosts: frozenset[str] = frozenset(),
    ) -> None:
        if any(
            re.fullmatch(r"[a-z0-9-]+\.stream\.qqmusic\.qq\.com", host) is None
            for host in approved_media_hosts
        ):
            raise ValueError("approved QQ media host is invalid")
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
            raise CapabilityUnavailable("qq quality capability is unavailable")
        root = require_mapping(payload, Source.QQ, "invalid quality response")
        if root.get("code") != 0:
            raise CapabilityUnavailable("qq quality capability is unavailable")
        data = require_mapping(root.get("data"), Source.QQ, "missing quality data")
        if require_string(data.get("mid"), Source.QQ, "missing track mid") != track_id:
            raise CapabilityUnavailable("qq track capability is unavailable")
        file_info = require_mapping(data.get("file"), Source.QQ, "missing file capability")
        options: list[QualityOption] = []
        for field_name, option in self._FILE_OPTIONS:
            size = file_info.get(field_name, 0)
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise PlatformResponseError(Source.QQ, "invalid quality size")
            if size > 0:
                options.append(option)
        if not options:
            raise CapabilityUnavailable("qq quality capability is unavailable")
        return self._snapshots.create(
            source=Source.QQ,
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
            raise CapabilityUnavailable("qq exact resolution is unavailable")
        if binding.source is not Source.QQ:
            raise ExactQualityMismatch("quality binding belongs to another platform")
        root = require_mapping(payload, Source.QQ, "invalid exact response")
        if root.get("code") != 0:
            raise CapabilityUnavailable("qq exact resolution is unavailable")
        data = require_mapping(root.get("data"), Source.QQ, "missing exact data")
        sip = require_list(data.get("sip"), Source.QQ, "missing exact host")
        infos = require_list(data.get("midurlinfo"), Source.QQ, "missing exact entry")
        if len(sip) != 1 or len(infos) != 1:
            raise PlatformResponseError(Source.QQ, "exact response must contain one location")
        base = require_string(sip[0], Source.QQ, "invalid exact host")
        info = require_mapping(infos[0], Source.QQ, "invalid exact entry")
        if require_string(info.get("songmid"), Source.QQ, "missing track mid") != binding.track_id:
            raise ExactQualityMismatch("qq returned a different track")
        filename = require_string(info.get("filename"), Source.QQ, "missing exact filename")
        actual = filename[:4]
        if actual != binding.option.quality_id:
            raise ExactQualityMismatch("qq returned a different quality")
        purl = require_string(info.get("purl"), Source.QQ, "missing exact location")
        if not purl or purl.startswith(("//", "/")):
            raise PlatformResponseError(Source.QQ, "invalid exact path")
        source_url = urljoin(base.rstrip("/") + "/", purl)
        _require_qq_https_url(source_url, self._approved_media_hosts)
        size = info.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PlatformResponseError(Source.QQ, "invalid exact size")
        current = now or datetime.now(UTC)
        return DownloadGrant(
            source=Source.QQ,
            track_id=binding.track_id,
            quality_id=actual,
            quality_rank=binding.option.rank,
            expires_at=current + timedelta(minutes=2),
            allowed_hosts=self._approved_media_hosts,
            _source_url=source_url,
            content_type="audio/flac" if actual in {"F000", "RS01"} else "audio/mpeg",
            expected_bytes=size,
        )


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


def _require_qq_https_url(value: str, allowed_hosts: frozenset[str]) -> None:
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
        raise PlatformResponseError(Source.QQ, "exact location is not platform-owned")
