from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from musicdl_web.models import Source
from musicdl_web.sessions import AESGCMCipher, FileCiphertextRepository
from musicdl_web.sessions.errors import SessionPersistenceError
from musicdl_web.sessions.models import SessionMaterial
from musicdl_web.sessions.store import EncryptedSessionStore


def write_key(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


def test_production_encrypted_session_restores_without_plaintext(tmp_path: Path) -> None:
    key_file = tmp_path / "session.key"
    write_key(key_file, b"k" * 32)
    session_root = tmp_path / "sessions"
    store = EncryptedSessionStore(
        FileCiphertextRepository(session_root), AESGCMCipher.from_key_file(key_file)
    )
    material = SessionMaterial(Source.NETEASE, {"MUSIC_U": "production-canary-secret"})
    store.save(material, version=3)

    restored = EncryptedSessionStore(
        FileCiphertextRepository(session_root), AESGCMCipher.from_key_file(key_file)
    ).load(Source.NETEASE)

    assert restored is not None
    assert restored[1] == 3
    assert restored[0].cookie_header_for(Source.NETEASE) == (
        "MUSIC_U=production-canary-secret"
    )
    ciphertext = (session_root / "netease.bin").read_bytes()
    assert b"production-canary-secret" not in ciphertext


def test_production_session_files_use_restrictive_permissions(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    repository = FileCiphertextRepository(root)

    repository.write("netease", b"opaque")

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "netease.bin").stat().st_mode) == 0o600


def test_production_session_fails_closed_with_wrong_key(tmp_path: Path) -> None:
    first_key = tmp_path / "first.key"
    second_key = tmp_path / "second.key"
    write_key(first_key, os.urandom(32))
    write_key(second_key, os.urandom(32))
    repository = FileCiphertextRepository(tmp_path / "sessions")
    EncryptedSessionStore(repository, AESGCMCipher.from_key_file(first_key)).save(
        SessionMaterial(Source.NETEASE, {"MUSIC_U": "canary"}), version=1
    )

    with pytest.raises(SessionPersistenceError, match="could not be opened"):
        EncryptedSessionStore(repository, AESGCMCipher.from_key_file(second_key)).load(
            Source.NETEASE
        )


@pytest.mark.parametrize("trailing_byte", [b"\x00", b"\t", b"\n", b"\r", b" "])
def test_binary_session_key_preserves_trailing_whitespace_bytes(
    tmp_path: Path, trailing_byte: bytes
) -> None:
    key_file = tmp_path / "session.key"
    key = b"k" * 31 + trailing_byte
    write_key(key_file, key)

    cipher = AESGCMCipher.from_key_file(key_file)
    sealed = cipher.seal(b"session", associated_data=b"netease")

    assert cipher.open(sealed, associated_data=b"netease") == b"session"
