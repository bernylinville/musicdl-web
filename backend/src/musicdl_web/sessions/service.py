"""Versioned session lifecycle service."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock

from ..models import Source
from .models import SessionMaterial, SessionState, SessionStatus
from .store import EncryptedSessionStore
from .validation import SessionValidator


class SessionManager:
    def __init__(self, store: EncryptedSessionStore) -> None:
        self._store = store
        self._versions = {source: 0 for source in Source}
        self._locks = {source: RLock() for source in Source}

    def status(
        self,
        source: Source,
        *,
        now: datetime | None = None,
        validator: SessionValidator | None = None,
    ) -> SessionStatus:
        with self._locks[source]:
            return self._status_locked(source, now=now, validator=validator)

    def _status_locked(
        self,
        source: Source,
        *,
        now: datetime | None,
        validator: SessionValidator | None,
    ) -> SessionStatus:
        current = now or datetime.now(UTC)
        loaded = self._store.load(source)
        if loaded is None:
            return SessionStatus(
                source=source,
                state=SessionState.DISCONNECTED,
                version=self._versions[source],
            )
        material, version = loaded
        self._versions[source] = max(self._versions[source], version)
        if material is None:
            return SessionStatus(
                source=source,
                state=SessionState.DISCONNECTED,
                version=version,
            )
        state: SessionState
        reason: str | None
        identity_hint: str | None
        if material.expires_at is not None and material.expires_at <= current:
            state = SessionState.INVALID
            reason = "session expired"
            identity_hint = None
        elif validator is None:
            state = SessionState.UNAVAILABLE
            reason = "live session validation is unavailable"
            identity_hint = None
        else:
            try:
                validation = validator.validate_session(material)
                if validation.state not in {
                    SessionState.ACTIVE,
                    SessionState.INVALID,
                    SessionState.UNAVAILABLE,
                }:
                    raise ValueError
                state = validation.state
                reason = validation.reason
                identity_hint = validation.identity_hint
            except Exception:
                state = SessionState.UNAVAILABLE
                reason = "live session validation failed"
                identity_hint = None
        return SessionStatus(
            source=source,
            state=state,
            version=version,
            updated_at=None,
            expires_at=material.expires_at,
            identity_hint=identity_hint,
            reason=reason,
        )

    def replace(self, material: SessionMaterial) -> SessionStatus:
        with self._locks[material.source]:
            return self._replace_locked(material)

    def _replace_locked(self, material: SessionMaterial) -> SessionStatus:
        existing = self._store.load(material.source)
        previous = existing[1] if existing is not None else self._versions[material.source]
        version = previous + 1
        self._store.save(material, version=version)
        self._versions[material.source] = version
        return SessionStatus(
            source=material.source,
            state=SessionState.UNAVAILABLE,
            version=version,
            updated_at=datetime.now(UTC),
            expires_at=material.expires_at,
            reason="session stored; live validation is unavailable",
        )

    def material(self, source: Source) -> tuple[SessionMaterial, int] | None:
        with self._locks[source]:
            return self._material_locked(source)

    def _material_locked(self, source: Source) -> tuple[SessionMaterial, int] | None:
        loaded = self._store.load(source)
        if loaded is not None:
            self._versions[source] = max(self._versions[source], loaded[1])
        if loaded is None or loaded[0] is None:
            return None
        return loaded[0], loaded[1]

    def clear(self, source: Source) -> SessionStatus:
        with self._locks[source]:
            return self._clear_locked(source)

    def _clear_locked(self, source: Source) -> SessionStatus:
        existing = self._store.load(source)
        previous = existing[1] if existing is not None else self._versions[source]
        self._versions[source] = previous + 1
        self._store.clear(source, version=self._versions[source])
        return SessionStatus(
            source=source,
            state=SessionState.DISCONNECTED,
            version=self._versions[source],
            updated_at=datetime.now(UTC),
        )
