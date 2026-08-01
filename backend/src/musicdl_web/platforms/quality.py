"""Per-track quality snapshots and exact-resolution grants."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..models import Source
from .errors import QualitySnapshotExpired, QualitySnapshotMismatch


class FidelityFamily(StrEnum):
    LINEAR = "linear"
    DOLBY = "dolby"
    ATMOS = "atmos"
    IMMERSIVE = "immersive"


class QualityOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality_id: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    family: FidelityFamily
    rank: int | None = Field(default=None, ge=0, le=100)
    codec: str | None = Field(default=None, max_length=24)


class QualitySnapshot(BaseModel):
    """Short-lived public capability list bound to one platform session version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=20, max_length=80)
    source: Source
    track_id: str = Field(min_length=1)
    session_version: int = Field(ge=0)
    expires_at: datetime
    options: tuple[QualityOption, ...]


@dataclass(frozen=True, slots=True)
class QualityBinding:
    snapshot_id: str
    source: Source
    track_id: str
    session_version: int
    option: QualityOption


class QualitySnapshotStore:
    """In-memory TTL cache; snapshot IDs are opaque and never platform tokens."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=5)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("quality snapshot TTL must be positive")
        self._ttl = ttl
        self._snapshots: dict[str, QualitySnapshot] = {}

    def create(
        self,
        *,
        source: Source,
        track_id: str,
        session_version: int,
        options: tuple[QualityOption, ...],
        now: datetime | None = None,
    ) -> QualitySnapshot:
        if not track_id:
            raise ValueError("track id must not be blank")
        if session_version < 0:
            raise ValueError("session version must not be negative")
        if not options:
            raise ValueError("quality snapshot must contain at least one option")
        ids = [option.quality_id for option in options]
        if len(ids) != len(set(ids)):
            raise ValueError("quality IDs must be unique within a snapshot")
        current = now or datetime.now(UTC)
        snapshot = QualitySnapshot(
            snapshot_id=uuid4().hex,
            source=source,
            track_id=track_id,
            session_version=session_version,
            expires_at=current + self._ttl,
            options=options,
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def bind(
        self,
        *,
        snapshot_id: str,
        quality_id: str,
        source: Source,
        track_id: str,
        session_version: int,
        now: datetime | None = None,
    ) -> QualityBinding:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise QualitySnapshotMismatch("quality snapshot is unknown")
        current = now or datetime.now(UTC)
        if current >= snapshot.expires_at:
            self._snapshots.pop(snapshot_id, None)
            raise QualitySnapshotExpired("quality snapshot has expired")
        if (
            snapshot.source is not source
            or snapshot.track_id != track_id
            or snapshot.session_version != session_version
        ):
            raise QualitySnapshotMismatch("quality snapshot binding does not match")
        option = next(
            (candidate for candidate in snapshot.options if candidate.quality_id == quality_id),
            None,
        )
        if option is None:
            raise QualitySnapshotMismatch("quality was not offered by this snapshot")
        return QualityBinding(
            snapshot_id=snapshot_id,
            source=source,
            track_id=track_id,
            session_version=session_version,
            option=option,
        )


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    """Memory-only exact-quality result passed directly to the downloader."""

    source: Source
    track_id: str
    quality_id: str
    quality_rank: int | None
    expires_at: datetime
    allowed_hosts: frozenset[str]
    _source_url: str = field(repr=False)
    content_type: str | None = None
    expected_bytes: int | None = None

    def source_url_for_downloader(self, source: Source) -> str:
        if source is not self.source:
            raise ValueError("download grant belongs to a different platform")
        return self._source_url

    def __getstate__(self) -> None:
        raise TypeError("download grants are memory-only")
