"""Disabled-by-default experimental QR login state machine."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from ..models import Source
from .errors import QrLoginError, QrLoginUnavailable
from .models import SessionMaterial


class QrLoginState(StrEnum):
    WAITING = "waiting"
    SCANNED = "scanned"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    NETWORK_ERROR = "network_error"


TERMINAL_QR_STATES = frozenset(
    {
        QrLoginState.SUCCEEDED,
        QrLoginState.REJECTED,
        QrLoginState.EXPIRED,
        QrLoginState.CANCELLED,
        QrLoginState.NETWORK_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class QrChallenge:
    source: Source
    challenge_id: str
    state: QrLoginState
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class QrObservation:
    state: QrLoginState
    success_result: object | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class QrFlowResult:
    """Adapter-only result; never returned by the controller's public methods."""

    state: QrLoginState
    material: SessionMaterial | None = field(default=None, repr=False)


class QrLoginFlow(Protocol):
    def begin(self, source: Source) -> tuple[object, timedelta]: ...

    def image(self, source: Source, temporary_token: object) -> bytes: ...

    def poll(self, source: Source, temporary_token: object) -> QrFlowResult: ...

    def discard(self, source: Source, temporary_token: object) -> None: ...


@dataclass(slots=True)
class _ActiveChallenge:
    public: QrChallenge
    token: object | None = field(repr=False)


class QrLoginController:
    """Own temporary tokens and erase them on every terminal transition."""

    _TRANSITIONS = {
        QrLoginState.WAITING: {
            QrLoginState.WAITING,
            QrLoginState.SCANNED,
            QrLoginState.AWAITING_CONFIRMATION,
            *TERMINAL_QR_STATES,
        },
        QrLoginState.SCANNED: {
            QrLoginState.SCANNED,
            QrLoginState.AWAITING_CONFIRMATION,
            *TERMINAL_QR_STATES,
        },
        QrLoginState.AWAITING_CONFIRMATION: {
            QrLoginState.AWAITING_CONFIRMATION,
            *TERMINAL_QR_STATES,
        },
    }

    def __init__(
        self,
        flow: QrLoginFlow,
        *,
        enabled_sources: frozenset[Source] = frozenset(),
        on_success: Callable[[SessionMaterial], object] | None = None,
    ) -> None:
        self._flow = flow
        self._enabled_sources = enabled_sources
        self._on_success = on_success
        self._challenges: dict[str, _ActiveChallenge] = {}

    def start(self, source: Source, *, now: datetime | None = None) -> QrChallenge:
        if source not in self._enabled_sources:
            raise QrLoginUnavailable("experimental QR login is disabled")
        token, ttl = self._flow.begin(source)
        if ttl <= timedelta(0):
            self._flow.discard(source, token)
            raise QrLoginError("QR login returned an invalid lifetime")
        current = now or datetime.now(UTC)
        public = QrChallenge(
            source=source,
            challenge_id=uuid4().hex,
            state=QrLoginState.WAITING,
            expires_at=current + ttl,
        )
        self._challenges[public.challenge_id] = _ActiveChallenge(public=public, token=token)
        return public

    def poll(
        self,
        challenge_id: str,
        *,
        source: Source | None = None,
        now: datetime | None = None,
    ) -> QrObservation:
        active = self._require_active(challenge_id, source=source)
        current = now or datetime.now(UTC)
        if current >= active.public.expires_at:
            return self._finish(active, QrLoginState.EXPIRED)
        if active.token is None:
            raise QrLoginError("QR challenge is no longer active")
        try:
            observation = self._flow.poll(active.public.source, active.token)
        except Exception:
            return self._finish(active, QrLoginState.NETWORK_ERROR)
        if observation.state not in self._TRANSITIONS.get(active.public.state, set()):
            return self._finish(active, QrLoginState.NETWORK_ERROR)
        if observation.state is QrLoginState.SUCCEEDED:
            if (
                observation.material is None
                or observation.material.source is not active.public.source
                or self._on_success is None
            ):
                return self._finish(active, QrLoginState.NETWORK_ERROR)
            try:
                success_result = self._on_success(observation.material)
            except Exception:
                return self._finish(active, QrLoginState.NETWORK_ERROR)
            return self._finish(
                active, QrLoginState.SUCCEEDED, success_result=success_result
            )
        elif observation.material is not None:
            return self._finish(active, QrLoginState.NETWORK_ERROR)
        if observation.state in TERMINAL_QR_STATES:
            return self._finish(active, observation.state)
        active.public = QrChallenge(
            source=active.public.source,
            challenge_id=active.public.challenge_id,
            state=observation.state,
            expires_at=active.public.expires_at,
        )
        return QrObservation(observation.state)

    def image(
        self,
        challenge_id: str,
        *,
        source: Source | None = None,
        now: datetime | None = None,
    ) -> bytes:
        active = self._require_active(challenge_id, source=source)
        current = now or datetime.now(UTC)
        if current >= active.public.expires_at:
            self._finish(active, QrLoginState.EXPIRED)
            raise QrLoginError("QR challenge is no longer active")
        if active.token is None:
            raise QrLoginError("QR challenge is no longer active")
        try:
            image = self._flow.image(active.public.source, active.token)
        except Exception:
            self._finish(active, QrLoginState.NETWORK_ERROR)
            raise QrLoginError("QR image is unavailable") from None
        if not isinstance(image, bytes) or not image:
            self._finish(active, QrLoginState.NETWORK_ERROR)
            raise QrLoginError("QR image is unavailable")
        return image

    def cancel(
        self, challenge_id: str, *, source: Source | None = None
    ) -> QrObservation:
        return self._finish(
            self._require_active(challenge_id, source=source), QrLoginState.CANCELLED
        )

    def cancel_source(self, source: Source) -> int:
        matching = tuple(
            active
            for active in self._challenges.values()
            if active.public.source is source
        )
        for active in matching:
            self._finish(active, QrLoginState.CANCELLED)
        return len(matching)

    def cancel_all(self) -> int:
        active = tuple(self._challenges.values())
        for challenge in active:
            self._finish(challenge, QrLoginState.CANCELLED)
        return len(active)

    def close(self) -> None:
        self.cancel_all()

    def has_temporary_token(self, challenge_id: str) -> bool:
        active = self._challenges.get(challenge_id)
        return active is not None and active.token is not None

    def _require_active(
        self, challenge_id: str, *, source: Source | None = None
    ) -> _ActiveChallenge:
        active = self._challenges.get(challenge_id)
        if (
            active is None
            or active.token is None
            or (source is not None and active.public.source is not source)
        ):
            raise QrLoginError("QR challenge is no longer active")
        return active

    def _finish(
        self,
        active: _ActiveChallenge,
        state: QrLoginState,
        *,
        success_result: object | None = None,
    ) -> QrObservation:
        token = active.token
        active.token = None
        self._challenges.pop(active.public.challenge_id, None)
        if token is not None:
            with suppress(Exception):
                self._flow.discard(active.public.source, token)
        return QrObservation(state, success_result)
