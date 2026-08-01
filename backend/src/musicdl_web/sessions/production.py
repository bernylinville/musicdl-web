"""Production authenticated encryption and ciphertext-only file persistence."""

from __future__ import annotations

import base64
import binascii
import os
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import SessionPersistenceError


class AESGCMCipher:
    """AES-256-GCM envelope with a fresh nonce for every write."""

    _VERSION = b"\x01"
    _NONCE_BYTES = 12

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("session encryption key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_key_file(cls, path: Path) -> AESGCMCipher:
        try:
            raw = path.read_bytes()
        except OSError:
            raise SessionPersistenceError("session encryption key is unavailable") from None
        key = _decode_key(raw)
        try:
            return cls(key)
        finally:
            # The immutable bytes object cannot be reliably zeroed. Keep its lifetime local and
            # never retain or report the source representation.
            del key, raw

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        nonce = os.urandom(self._NONCE_BYTES)
        return self._VERSION + nonce + self._cipher.encrypt(nonce, plaintext, associated_data)

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        minimum = 1 + self._NONCE_BYTES + 16
        if len(ciphertext) < minimum or ciphertext[:1] != self._VERSION:
            raise ValueError("invalid encrypted session envelope")
        nonce = ciphertext[1 : 1 + self._NONCE_BYTES]
        body = ciphertext[1 + self._NONCE_BYTES :]
        try:
            return self._cipher.decrypt(nonce, body, associated_data)
        except InvalidTag:
            raise ValueError("invalid encrypted session envelope") from None


class FileCiphertextRepository:
    """One opaque file per platform, atomically replaced with restrictive permissions."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise SessionPersistenceError("encrypted session could not be read") from None

    def write(self, key: str, ciphertext: bytes) -> None:
        path = self._path(key)
        descriptor: int | None = None
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".session-", dir=self._root)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
            os.chmod(path, 0o600)
        except OSError:
            raise SessionPersistenceError("encrypted session could not be written") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            raise SessionPersistenceError("encrypted session could not be deleted") from None

    def _path(self, key: str) -> Path:
        safe = "abcdefghijklmnopqrstuvwxyz0123456789_-"
        if not key or any(character not in safe for character in key):
            raise SessionPersistenceError("invalid encrypted session key")
        return self._root / f"{key}.bin"


def _decode_key(raw: bytes) -> bytes:
    if len(raw) == 32:
        return raw
    encoded = raw.strip()
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise SessionPersistenceError("session encryption key is invalid") from None
    if len(decoded) != 32:
        raise SessionPersistenceError("session encryption key is invalid")
    return decoded
