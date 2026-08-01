"""Opaque, memory-only proxy seam for legal short previews."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..models import Source
from .errors import PreviewUnavailable


class PreviewLease(BaseModel):
    """Public preview handle; never includes the platform URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_id: str = Field(min_length=20, max_length=80)
    source: Source
    track_id: str = Field(min_length=1)
    duration_ms: int = Field(gt=0, le=120_000)
    expires_at: datetime


class PlatformPreviewResolver(Protocol):
    """Resolve only a platform-authorized short preview into an opaque lease."""

    def resolve_preview(
        self,
        source: Source,
        track_id: str,
        *,
        session_version: int,
    ) -> PreviewLease: ...


class UnavailablePreviewResolver:
    """Safe wiring until a live platform preview capability has been proven."""

    def resolve_preview(
        self,
        source: Source,
        track_id: str,
        *,
        session_version: int,
    ) -> PreviewLease:
        raise PreviewUnavailable("preview is unavailable")


@dataclass(frozen=True, slots=True)
class PreviewTarget:
    source: Source
    track_id: str
    expires_at: datetime
    allowed_hosts: frozenset[str]
    _source_url: str = field(repr=False)

    def source_url_for_proxy(self, source: Source) -> str:
        if source is not self.source:
            raise ValueError("preview target belongs to a different platform")
        return self._source_url


class PreviewRegistry:
    def __init__(self, *, ttl: timedelta = timedelta(minutes=2)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("preview TTL must be positive")
        self._ttl = ttl
        self._targets: dict[str, PreviewTarget] = {}

    def register(
        self,
        *,
        source: Source,
        track_id: str,
        duration_ms: int | None,
        source_url: str | None,
        allowed_hosts: frozenset[str],
        now: datetime | None = None,
    ) -> PreviewLease:
        if not source_url or duration_ms is None:
            raise PreviewUnavailable("preview is unavailable")
        if not 0 < duration_ms <= 120_000:
            raise PreviewUnavailable("preview duration is invalid")
        parsed = urlsplit(source_url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or host not in allowed_hosts
        ):
            raise PreviewUnavailable("preview location is not platform-owned")
        current = now or datetime.now(UTC)
        expires_at = current + self._ttl
        preview_id = uuid4().hex
        self._targets[preview_id] = PreviewTarget(
            source=source,
            track_id=track_id,
            expires_at=expires_at,
            allowed_hosts=allowed_hosts,
            _source_url=source_url,
        )
        return PreviewLease(
            preview_id=preview_id,
            source=source,
            track_id=track_id,
            duration_ms=duration_ms,
            expires_at=expires_at,
        )

    def claim(
        self,
        preview_id: str,
        *,
        source: Source,
        track_id: str,
        now: datetime | None = None,
    ) -> PreviewTarget:
        target = self._targets.pop(preview_id, None)
        if target is None:
            raise PreviewUnavailable("preview is unavailable")
        current = now or datetime.now(UTC)
        if current >= target.expires_at:
            raise PreviewUnavailable("preview has expired")
        if target.source is not source or target.track_id != track_id:
            raise PreviewUnavailable("preview binding does not match")
        return target
