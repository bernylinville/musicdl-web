"""Authenticated-encryption persistence seam for platform sessions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from ..models import Source
from .errors import SessionPersistenceError
from .models import SessionMaterial


class AuthenticatedCipher(Protocol):
    """Production wiring must provide authenticated encryption with an external key."""

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes: ...

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes: ...


class CiphertextRepository(Protocol):
    """Opaque ciphertext persistence, normally backed by the dedicated session volume."""

    def read(self, key: str) -> bytes | None: ...

    def write(self, key: str, ciphertext: bytes) -> None: ...

    def delete(self, key: str) -> None: ...


class EncryptedSessionStore:
    """Persist only ciphertext; malformed data and wrong keys fail closed."""

    _FORMAT_VERSION = 1

    def __init__(self, repository: CiphertextRepository, cipher: AuthenticatedCipher) -> None:
        self._repository = repository
        self._cipher = cipher

    def load(self, source: Source) -> tuple[SessionMaterial | None, int] | None:
        ciphertext = self._repository.read(source.value)
        if ciphertext is None:
            return None
        try:
            plaintext = self._cipher.open(ciphertext, associated_data=self._aad(source))
            payload = json.loads(plaintext)
            if (
                not isinstance(payload, dict)
                or payload.get("format_version") != self._FORMAT_VERSION
                or payload.get("source") != source.value
            ):
                raise ValueError
            version = payload["session_version"]
            cookies = payload["cookies"]
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ValueError
            if cookies is not None:
                if not isinstance(cookies, dict) or not cookies:
                    raise ValueError
                if not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in cookies.items()
                ):
                    raise ValueError
            expires_raw = payload.get("expires_at")
            expires_at = (
                datetime.fromisoformat(expires_raw) if isinstance(expires_raw, str) else None
            )
            refresh = payload.get("refresh_token")
            if refresh is not None and not isinstance(refresh, str):
                raise ValueError
            material = (
                SessionMaterial(
                    source=source,
                    _cookies=cookies,
                    _refresh_token=refresh,
                    expires_at=expires_at,
                )
                if cookies is not None
                else None
            )
            return material, version
        except Exception:
            raise SessionPersistenceError("encrypted session could not be opened") from None

    def save(self, material: SessionMaterial, *, version: int) -> None:
        if version < 1:
            raise ValueError("session version must be positive")
        payload = material.secret_payload()
        payload.update(format_version=self._FORMAT_VERSION, session_version=version)
        try:
            plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            ciphertext = self._cipher.seal(plaintext, associated_data=self._aad(material.source))
            self._repository.write(material.source.value, ciphertext)
        except Exception:
            raise SessionPersistenceError("encrypted session could not be saved") from None

    def delete(self, source: Source) -> None:
        try:
            self._repository.delete(source.value)
        except Exception:
            raise SessionPersistenceError("encrypted session could not be deleted") from None

    def clear(self, source: Source, *, version: int) -> None:
        """Erase credentials while preserving the monotonic invalidation version."""

        if version < 1:
            raise ValueError("session version must be positive")
        payload = {
            "format_version": self._FORMAT_VERSION,
            "session_version": version,
            "source": source.value,
            "cookies": None,
            "refresh_token": None,
            "expires_at": None,
        }
        try:
            plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            ciphertext = self._cipher.seal(plaintext, associated_data=self._aad(source))
            self._repository.write(source.value, ciphertext)
        except Exception:
            raise SessionPersistenceError("encrypted session could not be cleared") from None

    @classmethod
    def _aad(cls, source: Source) -> bytes:
        return f"musicdl-web:session:{cls._FORMAT_VERSION}:{source.value}".encode()


class MemoryCiphertextRepository:
    """Small test/development repository that still stores ciphertext only."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def read(self, key: str) -> bytes | None:
        return self.values.get(key)

    def write(self, key: str, ciphertext: bytes) -> None:
        self.values[key] = bytes(ciphertext)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
