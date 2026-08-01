"""Live platform session validation seam."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import SessionMaterial, SessionState


class SessionValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SessionState
    identity_hint: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=120)


class SessionValidator(Protocol):
    """Platform adapter seam; implementations must use a source-scoped HTTP client."""

    def validate_session(self, material: SessionMaterial) -> SessionValidation: ...
