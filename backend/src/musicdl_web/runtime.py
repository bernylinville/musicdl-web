"""Production platform runtime with no secret-bearing public or persisted values."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx

from musicdl_web.adapters import (
    NeteaseAdapter,
    NeteaseCatalog,
    NeteaseLikedCatalog,
    QQAdapter,
)
from musicdl_web.adapters.netease_liked import _sort_liked_tracks
from musicdl_web.api.platform_router import (
    LibraryStateView,
    PlatformSessionView,
    PublicSessionState,
    QrChallengeView,
    QrObservationView,
    QualityOptionView,
    QualitySnapshotView,
    SearchGroupView,
    SearchResponseView,
    TrackView,
)
from musicdl_web.artwork import Artwork, ArtworkProxy, ArtworkUnavailable
from musicdl_web.core import CapabilityGateway, PipelineProcessor
from musicdl_web.db import Repository
from musicdl_web.domain import (
    Delivery,
    JobRequest,
    PlatformTrack,
    Quality,
    QualityFamily,
)
from musicdl_web.download import SafeDownloader, create_download_client
from musicdl_web.media import FFprobeMediaProbe, MediaPublisher, MutagenTagWriter
from musicdl_web.media.delivery import DeliveryService
from musicdl_web.models import SearchResults, Source, Track
from musicdl_web.network import PlatformHttpClient
from musicdl_web.platforms import (
    DownloadGrant,
    FidelityFamily,
    QualityOption,
    QualitySnapshotStore,
)
from musicdl_web.queue import QueueService
from musicdl_web.sessions import (
    AESGCMCipher,
    EncryptedSessionStore,
    FileCiphertextRepository,
    NeteaseQrLoginFlow,
    QrLoginController,
    QrLoginState,
    SessionManager,
    SessionMaterial,
    SessionState,
    SessionValidation,
    import_cookie_header,
)
from musicdl_web.sessions.errors import QrLoginError
from musicdl_web.sessions.netease_qr import verified_identity

_NETEASE_CONTROL_HOST = "music.163.com"
_NETEASE_EXACT_ENDPOINT = (
    "https://music.163.com/api/song/enhance/player/url/v1"
)
_NETEASE_ACCOUNT_ENDPOINT = "https://music.163.com/api/w/nuser/account/get"
_NETEASE_MEDIA_HOST = re.compile(r"^m[0-9]+\.music\.126\.net$")
_NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
# Cap short previews so the workbench never becomes a full streamer.
_PREVIEW_MAX_BYTES = 1_500_000


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    session_key_file: Path = Path("/run/secrets/session.key")
    session_root: Path = Path("/app/data/sessions")
    database_file: Path = Path("/app/data/musicdl.sqlite3")
    work_root: Path = Path("/app/tmp/work")
    browser_root: Path = Path("/app/tmp/browser")
    music_root: Path = Path("/music")
    staging_root: Path = Path("/app/tmp/staging")
    artwork_root: Path = Path("/app/data/artwork")
    quality_ttl: timedelta = timedelta(minutes=5)
    queue_concurrency: int = 2


@dataclass(frozen=True, slots=True)
class _NeteaseTier:
    option: QualityOption
    fidelity: Literal[
        "standard", "high", "lossless", "hi_res", "master", "spatial"
    ]


@dataclass(frozen=True, slots=True)
class _ExactResult:
    level: str
    size: int
    content_type: str | None
    host: str
    source_url: str = field(repr=False)


# Level ids match Netease player/url/v1. Only list a tier when the response
# echoes the same level (no silent downgrade). Labels follow the product glossary.
_NETEASE_TIERS = (
    _NeteaseTier(
        QualityOption(
            quality_id="standard",
            label="标准",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.STANDARD),
            codec="mp3",
        ),
        "standard",
    ),
    _NeteaseTier(
        QualityOption(
            quality_id="higher",
            label="较高",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.HIGH),
            codec="mp3",
        ),
        "high",
    ),
    _NeteaseTier(
        QualityOption(
            quality_id="exhigh",
            label="极高",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.HIGH),
            codec="mp3",
        ),
        "high",
    ),
    _NeteaseTier(
        QualityOption(
            quality_id="lossless",
            label="无损",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.LOSSLESS),
            codec="flac",
        ),
        "lossless",
    ),
    _NeteaseTier(
        QualityOption(
            quality_id="hires",
            label="Hi-Res",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.HI_RES),
            codec="flac",
        ),
        "hi_res",
    ),
    _NeteaseTier(
        QualityOption(
            quality_id="jymaster",
            label="超清母带",
            family=FidelityFamily.LINEAR,
            rank=int(Quality.MASTER),
            codec="flac",
        ),
        "master",
    ),
)


class ProductionPlatformRuntime:
    """Owns platform clients, encrypted sessions, snapshots, and exact revalidation."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        repository: Repository | None = None,
    ) -> None:
        cipher = AESGCMCipher.from_key_file(settings.session_key_file)
        store = EncryptedSessionStore(
            FileCiphertextRepository(settings.session_root), cipher
        )
        self._sessions = SessionManager(store)
        self._snapshots = QualitySnapshotStore(ttl=settings.quality_ttl)
        self._transport = transport
        self._netease = NeteaseAdapter(transport=transport)
        self._qq = QQAdapter(transport=transport)
        self._liked = NeteaseLikedCatalog(transport=transport)
        self._catalog = NeteaseCatalog(transport=transport)
        self._netease_control = PlatformHttpClient(
            allowed_hosts={_NETEASE_CONTROL_HOST}, transport=transport
        )
        self._qr = QrLoginController(
            NeteaseQrLoginFlow(transport=transport),
            enabled_sources=frozenset({Source.NETEASE}),
            on_success=self._accept_qr_material,
        )
        self._qr_lock = threading.Lock()
        self._artwork = ArtworkProxy(settings.artwork_root, transport=transport)
        self._repository = repository
        self._tracks: dict[tuple[Source, str], Track] = {}
        self._track_lock = threading.Lock()
        # Full liked-track list cache: (session_version) -> (expires_at, tracks)
        self._liked_cache: tuple[int, datetime, tuple[Track, ...]] | None = None
        # Lightweight red-heart id set: (session_version, expires_at, ids)
        self._liked_ids_cache: tuple[int, datetime, frozenset[str]] | None = None
        # Personal play ranking: (session_version, window) -> (expires_at, tracks)
        self._play_record_cache: dict[
            tuple[int, str], tuple[datetime, tuple[Track, ...]]
        ] = {}
        self._liked_lock = threading.Lock()

    def close(self) -> None:
        with self._qr_lock:
            self._qr.close()
        self._netease.close()
        self._qq.close()
        self._netease_control.close()

    def session_statuses(self) -> tuple[PlatformSessionView, ...]:
        return tuple(self._session_view(source) for source in Source)

    def import_session(self, source: Source, value: str) -> PlatformSessionView:
        material = import_cookie_header(source, value)
        with self._qr_lock:
            if source is Source.NETEASE:
                try:
                    validation = self.validate_session(material)
                except Exception:
                    raise ValueError("Netease session validation failed") from None
                if validation.state is not SessionState.ACTIVE:
                    raise ValueError("Netease session validation failed")
            self._qr.cancel_source(source)
            self._sessions.replace(material)
        return self._session_view(source)

    def clear_session(self, source: Source) -> None:
        with self._qr_lock:
            self._qr.cancel_source(source)
            self._sessions.clear(source)

    def start_qr(self, source: Source) -> QrChallengeView:
        try:
            with self._qr_lock:
                challenge = self._qr.start(source)
        except QrLoginError:
            raise ValueError("QR login is unavailable") from None
        return QrChallengeView(
            challengeId=challenge.challenge_id,
            state="waiting",
            imageUrl=(
                f"/api/v1/sessions/{source.value}/qr/{challenge.challenge_id}/image"
            ),
            expiresAt=challenge.expires_at.isoformat(),
        )

    def qr_image(self, source: Source, challenge_id: str) -> bytes:
        try:
            with self._qr_lock:
                return self._qr.image(challenge_id, source=source)
        except QrLoginError:
            raise LookupError("QR challenge is unavailable") from None

    def poll_qr(self, source: Source, challenge_id: str) -> QrObservationView:
        try:
            with self._qr_lock:
                observation = self._qr.poll(challenge_id, source=source)
        except QrLoginError:
            raise LookupError("QR challenge is unavailable") from None
        if observation.state is QrLoginState.WAITING:
            return QrObservationView(state="waiting")
        if observation.state in {
            QrLoginState.SCANNED,
            QrLoginState.AWAITING_CONFIRMATION,
        }:
            return QrObservationView(state="scanned")
        if observation.state is QrLoginState.EXPIRED:
            return QrObservationView(state="expired")
        if observation.state is QrLoginState.SUCCEEDED and isinstance(
            observation.success_result, PlatformSessionView
        ):
            return QrObservationView(
                state="success", session=observation.success_result
            )
        if observation.state is QrLoginState.NETWORK_ERROR and isinstance(
            observation.success_result, str
        ) and observation.success_result:
            raise RuntimeError(observation.success_result)
        raise RuntimeError("二维码登录暂时不可用")

    def cancel_qr(self, source: Source, challenge_id: str) -> None:
        try:
            with self._qr_lock:
                self._qr.cancel(challenge_id, source=source)
        except QrLoginError:
            raise LookupError("QR challenge is unavailable") from None

    def validate_session(self, material: SessionMaterial) -> SessionValidation:
        if material.source is not Source.NETEASE:
            return SessionValidation(
                state=SessionState.UNAVAILABLE,
                reason="live session validation is unavailable",
            )
        response = self._netease_control.get_authenticated(
            _NETEASE_ACCOUNT_ENDPOINT,
            cookie_header=material.cookie_header_for(Source.NETEASE),
            headers=_NETEASE_HEADERS,
        )
        try:
            root = response.json()
        except ValueError:
            return SessionValidation(
                state=SessionState.INVALID, reason="Netease session is invalid"
            )
        if not isinstance(root, Mapping):
            return SessionValidation(
                state=SessionState.INVALID, reason="Netease session is invalid"
            )
        try:
            identity = verified_identity(root)
        except QrLoginError:
            return SessionValidation(
                state=SessionState.INVALID, reason="Netease session is invalid"
            )
        return SessionValidation(
            state=SessionState.ACTIVE, identity_hint=identity, reason=None
        )

    def _accept_qr_material(self, material: SessionMaterial) -> PlatformSessionView:
        self._sessions.replace(material)
        return PlatformSessionView(
            source=Source.NETEASE,
            state=PublicSessionState.AUTHENTICATED,
            displayName=None,
            qrEnabled=True,
            checkedAt=datetime.now(UTC).isoformat(),
            message=None,
        )

    def search(
        self, query: str, source: Source | None, page: int
    ) -> SearchResponseView:
        query = query.strip()
        if not query or page < 1:
            raise ValueError("invalid search request")
        sources = tuple(Source) if source is None else (source,)
        groups: list[SearchGroupView] = []
        for current in sources:
            try:
                result = self._search_source(current, query, page)
            except Exception:
                groups.append(
                    SearchGroupView(
                        source=current,
                        tracks=(),
                        page=page,
                        hasMore=False,
                        status="unavailable",
                        message="平台搜索暂时不可用",
                    )
                )
                continue
            with self._track_lock:
                for track in result.tracks:
                    self._tracks[(track.source, track.track_id)] = track
            groups.append(
                SearchGroupView(
                    source=current,
                    tracks=tuple(self._track_view(track) for track in result.tracks),
                    page=result.page,
                    hasMore=result.has_more,
                    status="ready",
                    message=None,
                )
            )
        return SearchResponseView(query=query, groups=tuple(groups))

    def liked_tracks(
        self,
        source: Source,
        page: int = 1,
        limit: int = 50,
        *,
        sort: str = "default",
    ) -> SearchResponseView:
        """Return one page of the operator's Netease liked-music catalog."""

        if source is not Source.NETEASE:
            raise LookupError("liked music catalog is unavailable")
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid liked catalog request")
        if sort not in {"default", "liked_at_desc", "liked_at_asc"}:
            raise ValueError("invalid liked sort")
        loaded = self._sessions.material(source)
        if loaded is None:
            raise PermissionError("platform session is required")
        material, session_version = loaded
        try:
            tracks = self._liked_tracks_cached(material, session_version)
        except PermissionError:
            raise
        except Exception:
            raise LookupError("liked music catalog is unavailable") from None
        ordered = _sort_liked_tracks(tracks, sort)
        start = (page - 1) * limit
        page_tracks = ordered[start : start + limit]
        with self._track_lock:
            for track in page_tracks:
                self._tracks[(track.source, track.track_id)] = track
        label = {
            "default": "我喜欢的音乐",
            "liked_at_desc": "我喜欢的音乐 · 红心时间↓",
            "liked_at_asc": "我喜欢的音乐 · 红心时间↑",
        }[sort]
        return SearchResponseView(
            query=label,
            groups=(
                SearchGroupView(
                    source=Source.NETEASE,
                    tracks=tuple(self._track_view(track) for track in page_tracks),
                    page=page,
                    hasMore=start + limit < len(ordered),
                    status="ready",
                    message=None,
                ),
            ),
        )

    def play_record_tracks(
        self,
        source: Source,
        page: int = 1,
        limit: int = 50,
        *,
        window: str = "all",
    ) -> SearchResponseView:
        """Return one page of personal Netease play ranking (not limited to liked)."""

        if source is not Source.NETEASE:
            raise LookupError("play record catalog is unavailable")
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid play-record request")
        if window not in {"all", "week"}:
            raise ValueError("invalid play-record window")
        loaded = self._sessions.material(source)
        if loaded is None:
            raise PermissionError("platform session is required")
        material, session_version = loaded
        try:
            tracks = self._play_record_cached(material, session_version, window)
        except PermissionError:
            raise
        except Exception:
            raise LookupError("play record catalog is unavailable") from None
        start = (page - 1) * limit
        page_tracks = tracks[start : start + limit]
        with self._track_lock:
            for track in page_tracks:
                self._tracks[(track.source, track.track_id)] = track
        label = "听歌排行 · 全部" if window == "all" else "听歌排行 · 近一周"
        return SearchResponseView(
            query=label,
            groups=(
                SearchGroupView(
                    source=Source.NETEASE,
                    tracks=tuple(self._track_view(track) for track in page_tracks),
                    page=page,
                    hasMore=start + limit < len(tracks),
                    status="ready",
                    message=None,
                ),
            ),
        )

    def artist_tracks(
        self,
        source: Source,
        artist_id: str,
        page: int = 1,
        limit: int = 50,
        *,
        title_hint: str | None = None,
    ) -> SearchResponseView:
        if source is not Source.NETEASE:
            raise LookupError("artist catalog is unavailable")
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid catalog request")
        cookies = self._optional_netease_cookies()
        try:
            result, title = self._catalog.artist_tracks(
                artist_id,
                page=page,
                limit=limit,
                cookies=cookies,
                title_hint=title_hint,
            )
        except ValueError:
            raise
        except Exception:
            raise LookupError("artist catalog is unavailable") from None
        return self._catalog_response(f"歌手 · {title}", result)

    def album_tracks(
        self,
        source: Source,
        album_id: str,
        page: int = 1,
        limit: int = 50,
        *,
        title_hint: str | None = None,
    ) -> SearchResponseView:
        if source is not Source.NETEASE:
            raise LookupError("album catalog is unavailable")
        if page < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid catalog request")
        cookies = self._optional_netease_cookies()
        try:
            result, title = self._catalog.album_tracks(
                album_id,
                page=page,
                limit=limit,
                cookies=cookies,
                title_hint=title_hint,
            )
        except ValueError:
            raise
        except Exception:
            raise LookupError("album catalog is unavailable") from None
        return self._catalog_response(f"专辑 · {title}", result)

    def _optional_netease_cookies(self) -> dict[str, str] | None:
        loaded = self._sessions.material(Source.NETEASE)
        if loaded is None:
            return None
        material, _ = loaded
        return material.cookie_mapping_for(Source.NETEASE)

    def _catalog_response(
        self, query: str, result: SearchResults
    ) -> SearchResponseView:
        with self._track_lock:
            for track in result.tracks:
                self._tracks[(track.source, track.track_id)] = track
        return SearchResponseView(
            query=query,
            groups=(
                SearchGroupView(
                    source=result.source,
                    tracks=tuple(self._track_view(track) for track in result.tracks),
                    page=result.page,
                    hasMore=result.has_more,
                    status="ready",
                    message=None,
                ),
            ),
        )

    def set_track_liked(
        self, source: Source, track_id: str, *, liked: bool
    ) -> bool:
        """Toggle Netease red-heart state; returns the confirmed liked flag."""

        if source is not Source.NETEASE:
            raise LookupError("like control is unavailable")
        if not track_id or not track_id.isdigit():
            raise ValueError("invalid track id")
        loaded = self._sessions.material(source)
        if loaded is None:
            raise PermissionError("platform session is required")
        material, session_version = loaded
        try:
            self._liked.set_like(
                material.cookie_mapping_for(Source.NETEASE),
                track_id,
                liked=liked,
            )
        except PermissionError:
            raise
        except ValueError:
            raise
        except Exception:
            raise LookupError("like update failed") from None
        self._apply_liked_id_change(session_version, track_id, liked=liked)
        return liked

    def _liked_tracks_cached(
        self, material: SessionMaterial, session_version: int
    ) -> tuple[Track, ...]:
        now = datetime.now(UTC)
        with self._liked_lock:
            cached = self._liked_cache
            if (
                cached is not None
                and cached[0] == session_version
                and cached[1] > now
            ):
                return cached[2]
        full = self._liked.fetch_all(material.cookie_mapping_for(Source.NETEASE))
        ids = frozenset(track.track_id for track in full)
        with self._liked_lock:
            expires = now + timedelta(minutes=5)
            self._liked_cache = (session_version, expires, full)
            self._liked_ids_cache = (session_version, expires, ids)
        return full

    def _play_record_cached(
        self,
        material: SessionMaterial,
        session_version: int,
        window: str,
    ) -> tuple[Track, ...]:
        now = datetime.now(UTC)
        key = (session_version, window)
        with self._liked_lock:
            cached = self._play_record_cache.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        full = self._liked.fetch_play_record(
            material.cookie_mapping_for(Source.NETEASE), window=window
        )
        with self._liked_lock:
            self._play_record_cache[key] = (now + timedelta(minutes=5), full)
        return full

    def _liked_ids_cached(
        self, material: SessionMaterial, session_version: int
    ) -> frozenset[str]:
        now = datetime.now(UTC)
        with self._liked_lock:
            cached = self._liked_ids_cache
            if (
                cached is not None
                and cached[0] == session_version
                and cached[1] > now
            ):
                return cached[2]
        ids = self._liked.fetch_liked_ids(material.cookie_mapping_for(Source.NETEASE))
        with self._liked_lock:
            self._liked_ids_cache = (
                session_version,
                now + timedelta(minutes=5),
                ids,
            )
        return ids

    def _optional_liked_ids(self) -> frozenset[str] | None:
        loaded = self._sessions.material(Source.NETEASE)
        if loaded is None:
            return None
        material, session_version = loaded
        try:
            return self._liked_ids_cached(material, session_version)
        except Exception:
            return None

    def _apply_liked_id_change(
        self, session_version: int, track_id: str, *, liked: bool
    ) -> None:
        with self._liked_lock:
            ids_cache = self._liked_ids_cache
            if ids_cache is not None and ids_cache[0] == session_version:
                current = set(ids_cache[2])
                if liked:
                    current.add(track_id)
                else:
                    current.discard(track_id)
                self._liked_ids_cache = (
                    session_version,
                    ids_cache[1],
                    frozenset(current),
                )
            # Full playlist cache may be stale after like/unlike.
            if self._liked_cache is not None and self._liked_cache[0] == session_version:
                self._liked_cache = None

    def artwork(self, source: Source, track_id: str) -> Artwork:
        cached = self._artwork.cached(source, track_id)
        if cached is not None:
            return cached
        with self._track_lock:
            track = self._tracks.get((source, track_id))
        if track is None or track.cover_url is None:
            raise LookupError("artwork is unavailable")
        try:
            return self._artwork.fetch(source, track_id, track.cover_url)
        except ArtworkUnavailable:
            raise LookupError("artwork is unavailable") from None

    def quality_snapshot(self, source: Source, track_id: str) -> QualitySnapshotView:
        if source is Source.QQ:
            raise LookupError("qq exact quality capability is unavailable")
        loaded = self._sessions.material(source)
        if loaded is None:
            raise PermissionError("platform session is required")
        material, session_version = loaded
        cookie = material.cookie_header_for(source)
        available: list[tuple[_NeteaseTier, _ExactResult]] = []
        for tier in _NETEASE_TIERS:
            try:
                resolved = self._resolve_netease_exact(track_id, tier.option.quality_id, cookie)
            except (LookupError, ValueError):
                continue
            available.append((tier, resolved))
        if not available:
            raise LookupError("netease quality capability is unavailable")
        snapshot = self._snapshots.create(
            source=source,
            track_id=track_id,
            session_version=session_version,
            options=tuple(tier.option for tier, _ in available),
        )
        return QualitySnapshotView(
            snapshotId=snapshot.snapshot_id,
            expiresAt=snapshot.expires_at.isoformat(),
            sessionVersion=str(snapshot.session_version),
            options=tuple(
                QualityOptionView(
                    id=tier.option.quality_id,
                    label=tier.option.label,
                    fidelity=tier.fidelity,
                    codec=exact.content_type,
                    estimatedSizeBytes=exact.size,
                    requiresSession=True,
                    upgrade=self._is_upgrade(
                        source, track_id, Quality(tier.option.rank or 0)
                    ),
                )
                for tier, exact in available
            ),
        )

    async def prepare_request(
        self,
        source: Source,
        track_id: str,
        quality_id: str,
        quality_snapshot_id: str,
        delivery: Delivery,
    ) -> JobRequest:
        loaded = self._sessions.material(source)
        if loaded is None:
            raise ValueError("session is unavailable")
        _, session_version = loaded
        binding = self._snapshots.bind(
            snapshot_id=quality_snapshot_id,
            quality_id=quality_id,
            source=source,
            track_id=track_id,
            session_version=session_version,
        )
        with self._track_lock:
            track = self._tracks.get((source, track_id))
        if track is None:
            raise ValueError("track metadata is unavailable")
        if track.cover_url is not None:
            try:
                await asyncio.to_thread(
                    self._artwork.fetch, source, track_id, track.cover_url
                )
            except ArtworkUnavailable:
                pass
        rank = binding.option.rank
        if rank is None:
            raise ValueError("selected fidelity cannot be linearly compared")
        return JobRequest(
            track=PlatformTrack(
                source=track.source,
                track_id=track.track_id,
                title=track.title,
                artists=track.artists,
                album=track.album or None,
            ),
            quality=Quality(rank),
            quality_id=binding.option.quality_id,
            quality_label=binding.option.label,
            quality_family=QualityFamily(binding.option.family.value),
            quality_snapshot_id=binding.snapshot_id,
            session_version=binding.session_version,
            delivery=delivery,
        )

    async def revalidate_exact(self, request: JobRequest) -> DownloadGrant:
        if request.track.source is Source.QQ:
            raise ValueError("qq exact quality capability is unavailable")
        loaded = self._sessions.material(request.track.source)
        if loaded is None:
            raise ValueError("session is unavailable")
        material, session_version = loaded
        binding = self._snapshots.bind(
            snapshot_id=request.quality_snapshot_id,
            quality_id=request.quality_id,
            source=request.track.source,
            track_id=request.track.track_id,
            session_version=session_version,
        )
        if (
            request.session_version != session_version
            or binding.option.rank != int(request.quality)
            or binding.option.family.value != request.quality_family.value
        ):
            raise ValueError("quality binding changed")
        cookie = material.cookie_header_for(request.track.source)
        exact = await asyncio.to_thread(
            self._resolve_netease_exact,
            request.track.track_id,
            request.quality_id,
            cookie,
        )
        return DownloadGrant(
            source=request.track.source,
            track_id=request.track.track_id,
            quality_id=exact.level,
            quality_rank=binding.option.rank,
            expires_at=datetime.now(UTC) + timedelta(seconds=90),
            allowed_hosts=frozenset({exact.host}),
            _source_url=exact.source_url,
            content_type=exact.content_type,
            expected_bytes=exact.size,
        )

    async def artwork_for_request(self, request: JobRequest) -> bytes | None:
        source = request.track.source
        track_id = request.track.track_id
        artwork = await asyncio.to_thread(self._artwork.cached, source, track_id)
        if artwork is not None:
            return artwork.body
        with self._track_lock:
            track = self._tracks.get((source, track_id))
        if track is None or track.cover_url is None:
            return None
        try:
            fetched = await asyncio.to_thread(
                self._artwork.fetch, source, track_id, track.cover_url
            )
        except ArtworkUnavailable:
            return None
        return fetched.body

    def open_preview(self, source: Source, track_id: str) -> tuple[bytes, str]:
        """Return a short, proxied Netease clip; never expose the platform URL."""

        if source is not Source.NETEASE:
            raise LookupError("preview is unavailable")
        if not track_id or not track_id.isdigit():
            raise ValueError("invalid track id")
        loaded = self._sessions.material(source)
        if loaded is None:
            raise PermissionError("platform session is required")
        material, _ = loaded
        cookie = material.cookie_header_for(source)
        resolved: _ExactResult | None = None
        for level in ("standard", "higher", "exhigh"):
            try:
                resolved = self._resolve_netease_preview(track_id, level, cookie)
                break
            except (LookupError, ValueError):
                continue
        if resolved is None:
            raise LookupError("preview is unavailable")
        client = PlatformHttpClient(
            allowed_hosts={resolved.host}, transport=self._transport
        )
        try:
            response = client.get_limited(
                resolved.source_url,
                max_bytes=_PREVIEW_MAX_BYTES,
                allow_partial=True,
                headers={
                    "Referer": "https://music.163.com/",
                    "User-Agent": _NETEASE_HEADERS["User-Agent"],
                },
            )
        except Exception:
            raise LookupError("preview is unavailable") from None
        finally:
            client.close()
        body = response.content
        if not body or body.lstrip()[:9].lower().startswith(
            (b"<!doctype", b"<html")
        ):
            raise LookupError("preview is unavailable")
        media_type = resolved.content_type or "audio/mpeg"
        return body, media_type

    def _session_view(self, source: Source) -> PlatformSessionView:
        status = self._sessions.status(
            source, validator=self if source is Source.NETEASE else None
        )
        state = {
            SessionState.DISCONNECTED: PublicSessionState.ANONYMOUS,
            SessionState.ACTIVE: PublicSessionState.AUTHENTICATED,
            SessionState.INVALID: PublicSessionState.EXPIRED,
            SessionState.UNAVAILABLE: PublicSessionState.UNAVAILABLE,
        }[status.state]
        return PlatformSessionView(
            source=source,
            state=state,
            displayName=status.identity_hint,
            qrEnabled=source is Source.NETEASE,
            checkedAt=(status.updated_at or datetime.now(UTC)).isoformat(),
            message=(
                "QQ 二维码登录尚未支持"
                if source is Source.QQ
                else status.reason
            ),
        )

    def _search_source(self, source: Source, query: str, page: int) -> SearchResults:
        if source is Source.NETEASE:
            return self._netease.search(query, page=page)
        return self._qq.search(query, page=page)

    def _resolve_netease_exact(
        self, track_id: str, quality_id: str, cookie_header: str
    ) -> _ExactResult:
        if quality_id not in {tier.option.quality_id for tier in _NETEASE_TIERS}:
            raise ValueError("unknown Netease quality")
        return self._resolve_netease_media(
            track_id,
            quality_id,
            cookie_header,
            encode_type="flac",
            allow_trial=False,
        )

    def _resolve_netease_preview(
        self, track_id: str, quality_id: str, cookie_header: str
    ) -> _ExactResult:
        """Like exact media resolve, but trial clips are allowed and mp3 is preferred."""

        return self._resolve_netease_media(
            track_id,
            quality_id,
            cookie_header,
            encode_type="mp3",
            allow_trial=True,
        )

    def _resolve_netease_media(
        self,
        track_id: str,
        quality_id: str,
        cookie_header: str,
        *,
        encode_type: str,
        allow_trial: bool,
    ) -> _ExactResult:
        response = self._netease_control.post_authenticated(
            _NETEASE_EXACT_ENDPOINT,
            cookie_header=cookie_header,
            data={
                "ids": f"[{track_id}]",
                "level": quality_id,
                "encodeType": encode_type,
            },
            headers=_NETEASE_HEADERS,
        )
        try:
            root = response.json()
        except ValueError:
            raise LookupError("invalid Netease media response") from None
        if not isinstance(root, Mapping) or root.get("code") != 200:
            raise LookupError("Netease media is unavailable")
        data = root.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
            raise LookupError("Netease media is unavailable")
        entry: Mapping[str, Any] = data[0]
        if str(entry.get("id")) != track_id:
            raise LookupError("Netease media track mismatch")
        if not allow_trial and entry.get("level") != quality_id:
            raise LookupError("Netease exact quality changed")
        if not allow_trial and entry.get("freeTrialInfo") is not None:
            raise LookupError("trial media is not an exact entitlement")
        source_url = entry.get("url")
        size = entry.get("size")
        if not isinstance(source_url, str) or not source_url:
            raise LookupError("Netease media is unavailable")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            # Preview trial clips sometimes omit size; allow a synthetic bound.
            if allow_trial:
                size = _PREVIEW_MAX_BYTES
            else:
                raise LookupError("Netease media is unavailable")
        if source_url.startswith("http://"):
            source_url = "https://" + source_url.removeprefix("http://")
        parsed = urlsplit(source_url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or _NETEASE_MEDIA_HOST.fullmatch(host) is None
        ):
            raise ValueError("Netease media location is outside the allowlist")
        media_type = entry.get("type")
        content_type = (
            "audio/flac"
            if media_type == "flac"
            else "audio/mpeg" if media_type in {"mp3", "m4a", None} else "audio/mpeg"
        )
        if media_type not in {"flac", "mp3", "m4a", None}:
            content_type = "audio/mpeg"
        return _ExactResult(
            level=str(entry.get("level") or quality_id),
            size=size,
            content_type=content_type,
            host=host,
            source_url=source_url,
        )

    def _track_view(self, track: Track) -> TrackView:
        library: LibraryStateView | None = None
        if self._repository is not None:
            managed = self._repository.managed_media(track.source, track.track_id)
            if managed is not None:
                quality, path = managed
                library = LibraryStateView(
                    state="available" if path.is_file() else "missing",
                    qualityLabel=_quality_label(quality),
                )
        liked: bool | None = None
        if track.source is Source.NETEASE:
            liked_ids = self._optional_liked_ids()
            if liked_ids is not None:
                liked = track.track_id in liked_ids
        return TrackView(
            source=track.source,
            trackId=track.track_id,
            title=track.title,
            artists=track.artists,
            album=track.album,
            durationMs=track.duration_ms,
            coverUrl=(
                f"/api/v1/covers/{track.source.value}/{quote(track.track_id, safe='')}"
                if track.cover_url is not None
                else None
            ),
            library=library,
            artistIds=track.artist_ids,
            albumId=track.album_id,
            liked=liked,
            likedAt=(
                datetime.fromtimestamp(track.liked_at_ms / 1000, UTC).isoformat()
                if track.liked_at_ms is not None
                else None
            ),
            playCount=track.play_count,
        )

    def _is_upgrade(self, source: Source, track_id: str, quality: Quality) -> bool:
        if self._repository is None:
            return False
        managed = self._repository.managed_media(source, track_id)
        return managed is not None and managed[1].is_file() and quality > managed[0]


@dataclass(slots=True)
class ApplicationRuntime:
    platform: ProductionPlatformRuntime
    repository: Repository
    queue: QueueService
    _download_client: Any = field(repr=False)

    async def start(self) -> None:
        await self.queue.start()

    async def stop(self) -> None:
        await self.queue.stop()
        await self._download_client.aclose()
        self.platform.close()


def build_platform_runtime(
    settings: RuntimeSettings | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ProductionPlatformRuntime:
    return ProductionPlatformRuntime(settings or RuntimeSettings(), transport=transport)


def build_application_runtime(
    settings: RuntimeSettings | None = None,
) -> ApplicationRuntime:
    configured = settings or RuntimeSettings()
    repository = Repository(configured.database_file)
    platform = ProductionPlatformRuntime(configured, repository=repository)
    download_client = create_download_client()
    downloader = SafeDownloader(download_client)
    probe = FFprobeMediaProbe()
    tag_writer = MutagenTagWriter()
    publisher = MediaPublisher(
        configured.music_root,
        configured.staging_root,
        repository,
        probe,
        tag_writer,
    )
    delivery = DeliveryService(
        repository,
        publisher,
        configured.browser_root,
        probe,
        tag_writer,
    )
    processor = PipelineProcessor(
        repository,
        CapabilityGateway(platform),
        downloader,
        delivery,
        configured.work_root,
    )
    queue = QueueService(
        repository, processor, concurrency=configured.queue_concurrency
    )
    return ApplicationRuntime(
        platform=platform,
        repository=repository,
        queue=queue,
        _download_client=download_client,
    )


def _quality_label(quality: Quality) -> str:
    return {
        Quality.UNRESOLVED: "未知",
        Quality.STANDARD: "标准",
        Quality.HIGH: "高品质",
        Quality.LOSSLESS: "无损",
        Quality.HI_RES: "Hi-Res",
        Quality.MASTER: "母带",
    }[quality]
