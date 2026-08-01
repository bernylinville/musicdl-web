from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from musicdl_web.models import Source
from musicdl_web.sessions.errors import SessionImportError, SessionPersistenceError
from musicdl_web.sessions.importer import import_cookie_header
from musicdl_web.sessions.models import SessionState
from musicdl_web.sessions.service import SessionManager
from musicdl_web.sessions.store import EncryptedSessionStore, MemoryCiphertextRepository
from musicdl_web.sessions.validation import SessionValidation


class FakeCipher:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        body = bytes(
            value ^ self._key[index % len(self._key)] for index, value in enumerate(plaintext)
        )
        tag = hmac.new(self._key, associated_data + body, hashlib.sha256).digest()
        return tag + body

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        tag, body = ciphertext[:32], ciphertext[32:]
        expected = hmac.new(self._key, associated_data + body, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError
        return bytes(value ^ self._key[index % len(self._key)] for index, value in enumerate(body))


def test_manual_import_is_platform_bound_and_redacted() -> None:
    material = import_cookie_header(Source.NETEASE, "MUSIC_U=secret-value; __csrf=csrf-value")

    assert "secret-value" not in repr(material)
    assert material.cookie_header_for(Source.NETEASE) == (
        "MUSIC_U=secret-value; __csrf=csrf-value"
    )
    with pytest.raises(ValueError, match="different platform"):
        material.cookie_header_for(Source.QQ)


@pytest.mark.parametrize(
    "header",
    [
        "",
        "MUSIC_U",
        "MUSIC_U=a\r\nInjected=yes",
        "MUSIC_U=a; MUSIC_U=b",
        "MUSIC_U=a; Path=/",
        "bad name=value",
        "MUSIC_U=quoted\\value",
        "unrelated=value",
    ],
)
def test_manual_import_rejects_ambiguous_or_set_cookie_input(header: str) -> None:
    with pytest.raises(SessionImportError) as exc_info:
        import_cookie_header(Source.NETEASE, header)

    assert "secret" not in str(exc_info.value)


def test_encrypted_session_cross_restart_wrong_key_and_clear_version() -> None:
    repository = MemoryCiphertextRepository()
    store = EncryptedSessionStore(repository, FakeCipher(b"correct-key"))
    manager = SessionManager(store)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    material = import_cookie_header(
        Source.QQ, "uin=redacted; qqmusic_key=redacted", expires_at=expiry
    )

    first = manager.replace(material)
    assert first.state is SessionState.UNAVAILABLE
    assert first.version == 1
    ciphertext = repository.values[Source.QQ.value]
    assert b"redacted" not in ciphertext

    restarted = SessionManager(EncryptedSessionStore(repository, FakeCipher(b"correct-key")))
    assert restarted.status(Source.QQ).state is SessionState.UNAVAILABLE
    assert restarted.status(Source.QQ).version == 1
    assert restarted.material(Source.QQ) is not None

    class ValidSession:
        def validate_session(self, material) -> SessionValidation:
            return SessionValidation(state=SessionState.ACTIVE, identity_hint="账号 …1234")

    validated = restarted.status(Source.QQ, validator=ValidSession())
    assert validated.state is SessionState.ACTIVE
    assert validated.identity_hint == "账号 …1234"

    with pytest.raises(SessionPersistenceError, match="could not be opened"):
        SessionManager(
            EncryptedSessionStore(repository, FakeCipher(b"incorrect-key"))
        ).status(Source.QQ)

    cleared = restarted.clear(Source.QQ)
    assert cleared.version == 2
    assert restarted.material(Source.QQ) is None
    assert SessionManager(store).status(Source.QQ).version == 2
    assert b"redacted" not in repository.values[Source.QQ.value]


def test_concurrent_session_replacements_receive_distinct_monotonic_versions() -> None:
    manager = SessionManager(
        EncryptedSessionStore(
            MemoryCiphertextRepository(), FakeCipher(b"concurrency-key")
        )
    )
    barrier = Barrier(8)

    def replace(index: int) -> int:
        barrier.wait()
        material = import_cookie_header(
            Source.NETEASE, f"MUSIC_U=concurrent-{index}"
        )
        return manager.replace(material).version

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = tuple(executor.map(replace, range(8)))

    assert sorted(versions) == list(range(1, 9))
    loaded = manager.material(Source.NETEASE)
    assert loaded is not None
    assert loaded[1] == 8
