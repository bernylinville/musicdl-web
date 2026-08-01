from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest
from musicdl_web.models import Source
from musicdl_web.sessions.errors import QrLoginError, SessionPersistenceError
from musicdl_web.sessions.models import SessionMaterial
from musicdl_web.sessions.qr import (
    QrFlowResult,
    QrLoginController,
    QrLoginState,
)
from musicdl_web.sessions.store import EncryptedSessionStore, MemoryCiphertextRepository


class FakeAuthenticatedCipher:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def seal(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        tag = hmac.digest(self.key, associated_data + plaintext, hashlib.sha256)
        encrypted = bytes(
            value ^ self.key[index % len(self.key)] for index, value in enumerate(plaintext)
        )
        return tag + encrypted

    def open(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        tag, encrypted = ciphertext[:32], ciphertext[32:]
        plaintext = bytes(
            value ^ self.key[index % len(self.key)] for index, value in enumerate(encrypted)
        )
        expected = hmac.digest(self.key, associated_data + plaintext, hashlib.sha256)
        if not hmac.compare_digest(tag, expected):
            raise ValueError("authentication failed")
        return plaintext


def material() -> SessionMaterial:
    return SessionMaterial(
        source=Source.NETEASE,
        _cookies={"MUSIC_U": "cookie-secret"},
        _refresh_token="refresh-secret",  # noqa: S106 - synthetic canary secret
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_encrypted_session_restores_across_store_recreation_without_plaintext() -> None:
    repository = MemoryCiphertextRepository()
    first = EncryptedSessionStore(repository, FakeAuthenticatedCipher(b"correct-key"))
    first.save(material(), version=4)

    restored = EncryptedSessionStore(repository, FakeAuthenticatedCipher(b"correct-key")).load(
        Source.NETEASE
    )

    assert restored is not None
    assert restored[1] == 4
    assert restored[0].cookie_header_for(Source.NETEASE) == "MUSIC_U=cookie-secret"
    ciphertext = repository.values[Source.NETEASE]
    assert b"cookie-secret" not in ciphertext
    assert b"refresh-secret" not in ciphertext


def test_encrypted_session_fails_closed_with_the_wrong_key() -> None:
    repository = MemoryCiphertextRepository()
    EncryptedSessionStore(repository, FakeAuthenticatedCipher(b"correct-key")).save(
        material(), version=1
    )

    with pytest.raises(SessionPersistenceError, match="could not be opened"):
        EncryptedSessionStore(repository, FakeAuthenticatedCipher(b"wrong-key")).load(
            Source.NETEASE
        )


def test_session_repr_redacts_cookie_and_refresh_token() -> None:
    representation = repr(material())

    assert "cookie-secret" not in representation
    assert "refresh-secret" not in representation


class ScriptedQrFlow:
    def __init__(self, observations: Iterable[QrFlowResult | Exception]) -> None:
        self.observations = iter(observations)
        self.discarded: list[object] = []
        self.token = object()

    def begin(self, source: Source) -> tuple[object, timedelta]:
        return self.token, timedelta(minutes=2)

    def image(self, source: Source, temporary_token: object) -> bytes:
        return b"<svg></svg>"

    def poll(self, source: Source, temporary_token: object) -> QrFlowResult:
        observation = next(self.observations)
        if isinstance(observation, Exception):
            raise observation
        return observation

    def discard(self, source: Source, temporary_token: object) -> None:
        self.discarded.append(temporary_token)


@pytest.mark.parametrize(
    "observation",
    [
        QrFlowResult(QrLoginState.REJECTED),
        QrFlowResult(QrLoginState.EXPIRED),
        QrFlowResult(QrLoginState.NETWORK_ERROR),
        QrFlowResult(QrLoginState.SUCCEEDED, material()),
    ],
    ids=["rejected", "expired", "network-error", "succeeded"],
)
def test_qr_terminal_state_discards_temporary_token(observation: QrFlowResult) -> None:
    flow = ScriptedQrFlow([observation])
    controller = QrLoginController(
        flow,
        enabled_sources=frozenset({Source.NETEASE}),
        on_success=lambda value: None,
    )
    challenge = controller.start(Source.NETEASE)

    result = controller.poll(challenge.challenge_id)

    assert result.state is observation.state
    assert flow.discarded == [flow.token]
    assert not controller.has_temporary_token(challenge.challenge_id)


def test_qr_cancel_discards_temporary_token() -> None:
    flow = ScriptedQrFlow([])
    controller = QrLoginController(flow, enabled_sources=frozenset({Source.NETEASE}))
    challenge = controller.start(Source.NETEASE)

    result = controller.cancel(challenge.challenge_id)

    assert result.state is QrLoginState.CANCELLED
    assert flow.discarded == [flow.token]


def test_qr_network_exception_is_redacted_and_terminal() -> None:
    flow = ScriptedQrFlow([RuntimeError("token=network-secret")])
    controller = QrLoginController(flow, enabled_sources=frozenset({Source.NETEASE}))
    challenge = controller.start(Source.NETEASE)

    result = controller.poll(challenge.challenge_id)

    assert result.state is QrLoginState.NETWORK_ERROR
    with pytest.raises(QrLoginError) as error:
        controller.poll(challenge.challenge_id)
    assert "network-secret" not in str(error.value)
