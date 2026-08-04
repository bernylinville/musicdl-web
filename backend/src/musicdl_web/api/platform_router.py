"""Secret-free platform/session API matching the browser workbench contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from musicdl_web.artwork import Artwork
from musicdl_web.models import Source
from musicdl_web.sessions.errors import SessionImportError


class PublicSessionState(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class PlatformSessionView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Source
    state: PublicSessionState
    display_name: str | None = Field(alias="displayName")
    qr_enabled: bool = Field(alias="qrEnabled")
    checked_at: str | None = Field(alias="checkedAt")
    message: str | None


class SessionImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=8192)


class QrChallengeView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    challenge_id: str = Field(alias="challengeId")
    state: Literal["waiting"]
    image_url: str = Field(alias="imageUrl")
    expires_at: str = Field(alias="expiresAt")


class QrObservationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["waiting", "scanned", "success", "expired"]
    session: PlatformSessionView | None = None


class LibraryStateView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    state: Literal["available", "missing"]
    quality_label: str = Field(alias="qualityLabel")


class TrackView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Source
    track_id: str = Field(alias="trackId")
    title: str
    artists: tuple[str, ...]
    album: str
    duration_ms: int = Field(alias="durationMs", ge=0)
    cover_url: str | None = Field(alias="coverUrl")
    library: LibraryStateView | None = None
    artist_ids: tuple[str, ...] = Field(default=(), alias="artistIds")
    album_id: str | None = Field(default=None, alias="albumId")
    # null = unknown / no session / non-Netease; true/false when session can tell.
    liked: bool | None = None
    liked_at: str | None = Field(default=None, alias="likedAt")
    play_count: int | None = Field(default=None, alias="playCount", ge=0)


class SearchGroupView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Source
    tracks: tuple[TrackView, ...]
    page: int = Field(ge=1)
    has_more: bool = Field(alias="hasMore")
    status: Literal["ready", "unavailable"]
    message: str | None


class SearchResponseView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    groups: tuple[SearchGroupView, ...]


class QualityOptionView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    label: str
    fidelity: Literal["standard", "high", "lossless", "hi_res", "master", "spatial"]
    codec: str | None
    estimated_size_bytes: int | None = Field(alias="estimatedSizeBytes", ge=1)
    requires_session: bool = Field(alias="requiresSession")
    upgrade: bool


class QualitySnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    snapshot_id: str = Field(alias="snapshotId")
    expires_at: str = Field(alias="expiresAt")
    session_version: str | None = Field(alias="sessionVersion")
    options: tuple[QualityOptionView, ...]


class TrackLikeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liked: bool


class TrackLikeView(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Source
    track_id: str = Field(alias="trackId")
    liked: bool


class PlatformService(Protocol):
    def session_statuses(self) -> tuple[PlatformSessionView, ...]: ...

    def import_session(self, source: Source, value: str) -> PlatformSessionView: ...

    def clear_session(self, source: Source) -> None: ...

    def search(self, query: str, source: Source | None, page: int) -> SearchResponseView: ...

    def liked_tracks(
        self,
        source: Source,
        page: int = 1,
        limit: int = 50,
        *,
        sort: str = "default",
    ) -> SearchResponseView: ...

    def play_record_tracks(
        self,
        source: Source,
        page: int = 1,
        limit: int = 50,
        *,
        window: str = "all",
    ) -> SearchResponseView: ...

    def artist_tracks(
        self,
        source: Source,
        artist_id: str,
        page: int = 1,
        limit: int = 50,
        *,
        title_hint: str | None = None,
    ) -> SearchResponseView: ...

    def album_tracks(
        self,
        source: Source,
        album_id: str,
        page: int = 1,
        limit: int = 50,
        *,
        title_hint: str | None = None,
    ) -> SearchResponseView: ...

    def set_track_liked(
        self, source: Source, track_id: str, *, liked: bool
    ) -> bool: ...

    def quality_snapshot(self, source: Source, track_id: str) -> QualitySnapshotView: ...

    def artwork(self, source: Source, track_id: str) -> Artwork: ...


@runtime_checkable
class _QrPlatformService(Protocol):
    def start_qr(self, source: Source) -> QrChallengeView: ...

    def poll_qr(self, source: Source, challenge_id: str) -> QrObservationView: ...

    def qr_image(self, source: Source, challenge_id: str) -> bytes: ...

    def cancel_qr(self, source: Source, challenge_id: str) -> None: ...


def create_platform_router(service: PlatformService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    qr_service = service if isinstance(service, _QrPlatformService) else None

    @router.get("/sessions", response_model=tuple[PlatformSessionView, ...])
    def sessions() -> tuple[PlatformSessionView, ...]:
        return service.session_statuses()

    @router.post("/sessions/{source}/import", response_model=PlatformSessionView)
    def import_session(source: Source, body: SessionImportBody) -> PlatformSessionView:
        try:
            return service.import_session(source, body.value)
        except (SessionImportError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="会话内容无效或不属于所选平台",
            ) from None

    @router.delete("/sessions/{source}", status_code=status.HTTP_204_NO_CONTENT)
    def clear_session(source: Source) -> Response:
        service.clear_session(source)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/sessions/{source}/qr",
        response_model=QrChallengeView,
    )
    def start_qr(source: Source) -> QrChallengeView:
        if qr_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="当前平台暂不支持二维码登录",
            )
        try:
            return qr_service.start_qr(source)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="当前平台暂不支持二维码登录",
            ) from None

    @router.get(
        "/sessions/{source}/qr/{challenge_id}/image",
        response_class=Response,
    )
    def qr_image(source: Source, challenge_id: str) -> Response:
        if qr_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="二维码登录暂时不可用",
            )
        try:
            image = qr_service.qr_image(source, challenge_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="二维码登录任务已结束") from None
        return Response(
            content=image,
            media_type="image/svg+xml",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get(
        "/sessions/{source}/qr/{challenge_id}",
        response_model=QrObservationView,
        response_model_exclude_none=True,
    )
    def poll_qr(source: Source, challenge_id: str) -> QrObservationView:
        if qr_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="二维码登录暂时不可用",
            )
        try:
            return qr_service.poll_qr(source, challenge_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="二维码登录任务已结束") from None
        except RuntimeError as exc:
            detail = str(exc).strip() or "二维码登录暂时不可用"
            # Never leak stack-like content; adapter messages are fixed Chinese/English phrases.
            if len(detail) > 160 or "\n" in detail:
                detail = "二维码登录暂时不可用"
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=detail,
            ) from None

    @router.delete(
        "/sessions/{source}/qr/{challenge_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def cancel_qr(source: Source, challenge_id: str) -> Response:
        if qr_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="二维码登录暂时不可用",
            )
        try:
            qr_service.cancel_qr(source, challenge_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="二维码登录任务已结束") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/search", response_model=SearchResponseView)
    def search(
        q: Annotated[str, Query(min_length=1, max_length=200)],
        source: Annotated[Literal["all", "netease", "qq"], Query()] = "all",
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
    ) -> SearchResponseView:
        selected = None if source == "all" else Source(source)
        try:
            return service.search(q, selected, page)
        except ValueError:
            raise HTTPException(status_code=422, detail="搜索参数无效") from None

    @router.get("/library/{source}/liked", response_model=SearchResponseView)
    def liked_tracks(
        source: Source,
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        sort: Annotated[
            Literal["default", "liked_at_desc", "liked_at_asc"], Query()
        ] = "default",
    ) -> SearchResponseView | JSONResponse:
        try:
            return service.liked_tracks(source, page=page, limit=limit, sort=sort)
        except PermissionError:
            return _error(
                status.HTTP_401_UNAUTHORIZED,
                "请先登录网易云音乐会话",
                "session_required",
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "当前平台暂不支持查看喜欢的音乐",
                "liked_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="分页或排序参数无效") from None

    @router.get("/library/{source}/play-record", response_model=SearchResponseView)
    def play_record_tracks(
        source: Source,
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        window: Annotated[Literal["all", "week"], Query()] = "all",
    ) -> SearchResponseView | JSONResponse:
        try:
            return service.play_record_tracks(
                source, page=page, limit=limit, window=window
            )
        except PermissionError:
            return _error(
                status.HTTP_401_UNAUTHORIZED,
                "请先登录网易云音乐会话",
                "session_required",
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "听歌排行暂时不可用",
                "play_record_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="分页或时间窗参数无效") from None

    @router.get(
        "/library/{source}/artists/{artist_id}/tracks",
        response_model=SearchResponseView,
    )
    def artist_tracks(
        source: Source,
        artist_id: str,
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        title: Annotated[str | None, Query(max_length=120)] = None,
    ) -> SearchResponseView | JSONResponse:
        try:
            return service.artist_tracks(
                source, artist_id, page=page, limit=limit, title_hint=title
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "歌手曲目暂时不可用",
                "artist_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="歌手或分页参数无效") from None

    @router.get(
        "/library/{source}/albums/{album_id}/tracks",
        response_model=SearchResponseView,
    )
    def album_tracks(
        source: Source,
        album_id: str,
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        title: Annotated[str | None, Query(max_length=120)] = None,
    ) -> SearchResponseView | JSONResponse:
        try:
            return service.album_tracks(
                source, album_id, page=page, limit=limit, title_hint=title
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "专辑曲目暂时不可用",
                "album_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="专辑或分页参数无效") from None

    @router.put(
        "/library/{source}/tracks/{track_id}/like",
        response_model=TrackLikeView,
    )
    def set_track_liked(
        source: Source, track_id: str, body: TrackLikeBody
    ) -> TrackLikeView | JSONResponse:
        try:
            liked = service.set_track_liked(source, track_id, liked=body.liked)
        except PermissionError:
            return _error(
                status.HTTP_401_UNAUTHORIZED,
                "请先登录网易云音乐会话",
                "session_required",
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "红心状态更新失败",
                "like_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="曲目参数无效") from None
        return TrackLikeView(source=source, trackId=track_id, liked=liked)

    @router.get("/covers/{source}/{track_id}", response_class=Response)
    def cover(source: Source, track_id: str) -> Response:
        try:
            artwork = service.artwork(source, track_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="封面暂不可用") from None
        return Response(
            content=artwork.body,
            media_type=artwork.content_type,
            headers={"Cache-Control": "private, max-age=600"},
        )

    @router.get(
        "/tracks/{source}/{track_id}/qualities", response_model=QualitySnapshotView
    )
    def qualities(
        source: Source, track_id: str
    ) -> QualitySnapshotView | JSONResponse:
        try:
            return service.quality_snapshot(source, track_id)
        except PermissionError:
            return _error(
                status.HTTP_401_UNAUTHORIZED,
                "请先导入本人平台会话",
                "session_required",
            )
        except LookupError:
            return _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "当前平台音质能力不可用",
                "quality_unavailable",
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="曲目或音质响应无效") from None

    @router.get("/tracks/{source}/{track_id}/preview", status_code=204)
    def preview_unavailable(source: Source, track_id: str) -> Response:
        del source, track_id
        # No platform has a proven legal short-preview lease yet. Never substitute a full-track
        # URL or expose a source location to the browser.
        return Response(status_code=204)

    return router


def _error(status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})
