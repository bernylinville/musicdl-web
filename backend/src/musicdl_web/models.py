"""Stable internal models for platform search results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Source(StrEnum):
    NETEASE = "netease"
    QQ = "qq"


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Source
    track_id: str = Field(min_length=1)
    title: str
    artists: tuple[str, ...]
    album: str
    duration_ms: int = Field(ge=0)
    cover_url: str | None = None


class SearchResults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Source
    tracks: tuple[Track, ...]
    page: int = Field(ge=1)
    has_more: bool


# Kept as a readable singular alias for callers that name one search page.
SearchResult = SearchResults
