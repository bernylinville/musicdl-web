"""Session models with a strict public/secret split."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from ..models import Source


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    ACTIVE = "active"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class SessionStatus(BaseModel):
    """Safe session status suitable for API serialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Source
    state: SessionState
    version: int = Field(ge=0)
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    identity_hint: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=120)


@dataclass(frozen=True, slots=True)
class SessionMaterial:
    """Secret platform material; repr and public model serialization expose no values."""

    source: Source
    _cookies: Mapping[str, str] = field(repr=False)
    _refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_cookies", MappingProxyType(dict(self._cookies)))

    def cookie_header_for(self, source: Source) -> str:
        """Return credentials only to an adapter bound to the same platform."""

        if source is not self.source:
            raise ValueError("session material belongs to a different platform")
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())

    def cookie_mapping_for(self, source: Source) -> dict[str, str]:
        """Return a cookie map only to an adapter bound to the same platform."""

        if source is not self.source:
            raise ValueError("session material belongs to a different platform")
        return dict(self._cookies)

    def secret_payload(self) -> dict[str, object]:
        """Return a persistence-only payload; callers must never log it."""

        return {
            "source": self.source.value,
            "cookies": dict(self._cookies),
            "refresh_token": self._refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
