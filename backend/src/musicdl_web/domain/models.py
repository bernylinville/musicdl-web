"""Stable, secret-free domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from musicdl_web.models import Source


def utc_now() -> datetime:
    return datetime.now(UTC)


class Quality(IntEnum):
    """Comparable stereo quality tiers; immersive variants are deliberately absent."""

    UNRESOLVED = 0
    STANDARD = 10
    HIGH = 20
    LOSSLESS = 30
    HI_RES = 40
    MASTER = 50


class Delivery(StrEnum):
    SERVER = "server"
    BROWSER = "browser"


class QualityFamily(StrEnum):
    LINEAR = "linear"
    DOLBY = "dolby"
    ATMOS = "atmos"
    IMMERSIVE = "immersive"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    TAGGING = "tagging"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


class PlatformTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Source
    track_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    artists: tuple[str, ...] = Field(min_length=1)
    album: str | None = Field(default=None, max_length=512)
    album_artist: str | None = Field(default=None, max_length=512)
    year: int | None = Field(default=None, ge=1000, le=9999)
    disc: int | None = Field(default=None, ge=1)
    track_number: int | None = Field(default=None, ge=1)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    track: PlatformTrack
    quality: Quality
    quality_id: str = Field(min_length=1, max_length=128)
    quality_label: str | None = Field(default=None, max_length=128)
    quality_family: QualityFamily = QualityFamily.LINEAR
    quality_snapshot_id: str = Field(min_length=1, max_length=128)
    session_version: int = Field(default=0, ge=0)
    delivery: Delivery


class MediaMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extension: str = Field(pattern=r"^[a-z0-9]{2,5}$")
    codec: str = Field(min_length=1, max_length=64)
    duration_seconds: float = Field(gt=0)
    quality: Quality


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    request: JobRequest
    status: JobStatus
    attempt: int = Field(ge=0)
    progress: float = Field(ge=0, le=1)
    error_code: str | None = None
    error_message: str | None = None
    output_path: str | None = None
    created_at: datetime
    updated_at: datetime


class ManagedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: str
    existing_path: Path | None = None
