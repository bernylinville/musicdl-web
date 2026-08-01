from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from musicdl_web.api.platform_router import create_platform_router
from musicdl_web.errors import NetworkPolicyError, NetworkRequestError
from musicdl_web.models import Source
from musicdl_web.network import PlatformCookieJarClient
from musicdl_web.sessions.errors import QrLoginError, QrLoginUnavailable
from musicdl_web.sessions.importer import import_cookie_header
from musicdl_web.sessions.netease_qr import NeteaseQrLoginFlow
from musicdl_web.sessions.qr import QrFlowResult, QrLoginController, QrLoginState


class StubFlow:
    def __init__(self, observations: list[QrFlowResult | Exception]) -> None:
        self.observations = deque(observations)
        self.discarded: list[object] = []
        self.token = object()

    def begin(self, source: Source) -> tuple[object, timedelta]:
        return self.token, timedelta(minutes=1)

    def image(self, source: Source, temporary_token: object) -> bytes:
        return b"<svg>image-canary-secret</svg>"

    def poll(self, source: Source, temporary_token: object) -> QrFlowResult:
        observation = self.observations.popleft()
        if isinstance(observation, Exception):
            raise observation
        return observation

    def discard(self, source: Source, temporary_token: object) -> None:
        self.discarded.append(temporary_token)


def test_qr_is_disabled_by_default() -> None:
    with pytest.raises(QrLoginUnavailable, match="disabled"):
        QrLoginController(StubFlow([])).start(Source.NETEASE)


def test_qq_qr_remains_unavailable_when_only_netease_is_enabled() -> None:
    controller = QrLoginController(
        StubFlow([]), enabled_sources=frozenset({Source.NETEASE})
    )
    with pytest.raises(QrLoginUnavailable, match="disabled"):
        controller.start(Source.QQ)


def test_qr_scanned_then_waiting_stays_active() -> None:
    """Netease may re-emit waiting after scanned; that must not kill the challenge."""

    flow = StubFlow(
        [
            QrFlowResult(QrLoginState.SCANNED),
            QrFlowResult(QrLoginState.WAITING),
            QrFlowResult(QrLoginState.SCANNED),
        ]
    )
    controller = QrLoginController(flow, enabled_sources=frozenset({Source.NETEASE}))
    challenge = controller.start(Source.NETEASE)

    assert controller.poll(challenge.challenge_id).state is QrLoginState.SCANNED
    assert controller.poll(challenge.challenge_id).state is QrLoginState.WAITING
    assert controller.poll(challenge.challenge_id).state is QrLoginState.SCANNED
    assert controller.has_temporary_token(challenge.challenge_id)
    assert flow.discarded == []


def test_qr_wait_scan_confirm_success_erases_temporary_token() -> None:
    material = import_cookie_header(Source.NETEASE, "MUSIC_U=redacted")
    flow = StubFlow(
        [
            QrFlowResult(QrLoginState.SCANNED),
            QrFlowResult(QrLoginState.AWAITING_CONFIRMATION),
            QrFlowResult(QrLoginState.SUCCEEDED, material),
        ]
    )
    saved = []
    controller = QrLoginController(
        flow, enabled_sources=frozenset({Source.NETEASE}), on_success=saved.append
    )
    challenge = controller.start(Source.NETEASE)

    assert "image-canary-secret" not in repr(challenge)
    assert controller.image(challenge.challenge_id) == (
        b"<svg>image-canary-secret</svg>"
    )

    assert controller.poll(challenge.challenge_id).state is QrLoginState.SCANNED
    assert controller.poll(challenge.challenge_id).state is QrLoginState.AWAITING_CONFIRMATION
    result = controller.poll(challenge.challenge_id)

    assert result.state is QrLoginState.SUCCEEDED
    assert "image-canary-secret" not in repr(result)
    assert saved == [material]
    assert not controller.has_temporary_token(challenge.challenge_id)
    assert flow.discarded == [flow.token]
    with pytest.raises(QrLoginError, match="no longer active"):
        controller.poll(challenge.challenge_id)


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (QrFlowResult(QrLoginState.REJECTED), QrLoginState.REJECTED),
        (RuntimeError("network detail"), QrLoginState.NETWORK_ERROR),
    ],
)
def test_qr_terminal_outcomes_erase_token(
    observation: QrFlowResult | Exception, expected: QrLoginState
) -> None:
    flow = StubFlow([observation])
    controller = QrLoginController(flow, enabled_sources=frozenset({Source.NETEASE}))
    challenge = controller.start(Source.NETEASE)

    assert controller.poll(challenge.challenge_id).state is expected
    assert flow.discarded == [flow.token]


def test_qr_expiry_and_cancel_erase_token_without_polling_platform() -> None:
    now = datetime.now(UTC)
    flow = StubFlow([])
    controller = QrLoginController(flow, enabled_sources=frozenset({Source.NETEASE}))
    expired = controller.start(Source.NETEASE, now=now)
    with pytest.raises(QrLoginError, match="no longer active"):
        controller.image(expired.challenge_id, now=now + timedelta(minutes=2))
    assert not controller.has_temporary_token(expired.challenge_id)

    cancelled = controller.start(Source.NETEASE, now=now)
    assert controller.cancel(cancelled.challenge_id).state is QrLoginState.CANCELLED
    assert flow.discarded == [flow.token, flow.token]


def test_netease_flow_uses_official_forms_same_cookie_jar_and_verified_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        form = parse_qs(request.content.decode()) if request.content else {}
        if request.url.path == "/api/login/qrcode/unikey":
            assert form == {"type": ["3"]}
            return httpx.Response(200, json={"code": 200, "unikey": "qr-key-secret"})
        if request.url.path == "/api/login/qrcode/client/login":
            assert form == {"key": ["qr-key-secret"], "type": ["3"]}
            return httpx.Response(
                200,
                headers={"set-cookie": "MUSIC_U=session-secret; Path=/; Secure"},
                json={"code": 803},
            )
        if request.url.path == "/api/w/nuser/account/get":
            assert request.headers["cookie"] == "MUSIC_U=session-secret"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 42},
                    "profile": {"userId": 42, "nickname": "安全昵称"},
                },
            )
        raise AssertionError("unexpected request")

    encoded_payloads: list[str] = []
    monkeypatch.setattr(
        "musicdl_web.sessions.netease_qr.qr_svg_data_url",
        lambda payload: encoded_payloads.append(payload)
        or "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    )
    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, ttl = flow.begin(Source.NETEASE)

    assert len(encoded_payloads) == 1
    assert encoded_payloads[0].startswith(
        "https://music.163.com/login?codekey=qr-key-secret&chainId=v1_w"
    )
    assert ttl == timedelta(minutes=5)
    assert flow.image(Source.NETEASE, token) == b"<svg></svg>"
    representation = repr(token)
    assert "qr-key-secret" not in representation
    assert "session-secret" not in representation
    result = flow.poll(Source.NETEASE, token)
    assert result.state is QrLoginState.SUCCEEDED
    assert result.material is not None
    assert result.material.cookie_header_for(Source.NETEASE) == "MUSIC_U=session-secret"
    assert [request.url.scheme for request in requests] == ["https", "https", "https"]

    flow.discard(Source.NETEASE, token)
    assert "qr-key-secret" not in repr(token)


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (801, QrLoginState.WAITING),
        (802, QrLoginState.SCANNED),
        (800, QrLoginState.EXPIRED),
    ],
)
def test_netease_flow_maps_poll_states(code: int, state: QrLoginState) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unikey"):
            return httpx.Response(200, json={"code": 200, "unikey": "short-key"})
        return httpx.Response(200, json={"code": code})

    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, _ = flow.begin(Source.NETEASE)
    assert flow.poll(Source.NETEASE, token).state is state
    flow.discard(Source.NETEASE, token)


def test_netease_confirm_tolerates_empty_sibling_cookies_and_string_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: phone-confirmed QR previously failed as「二维码不可用」when Netease
    emitted an empty Set-Cookie sibling, or returned account ids as strings.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unikey"):
            form = parse_qs(request.content.decode()) if request.content else {}
            assert form == {"type": ["3"]}
            return httpx.Response(200, json={"code": 200, "unikey": "confirm-key"})
        if request.url.path.endswith("/client/login"):
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "MUSIC_U=confirmed-session; Path=/; Secure"),
                    ("set-cookie", "PLACEHOLDER=; Path=/"),
                    ("set-cookie", "__csrf=csrf-token; Path=/"),
                ],
                json={"code": 803, "message": "授权成功"},
            )
        if request.url.path.endswith("/account/get"):
            assert "MUSIC_U=confirmed-session" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": "42"},
                    "profile": {"userId": "42", "nickname": "确认账号"},
                },
            )
        raise AssertionError(request.url.path)

    monkeypatch.setattr(
        "musicdl_web.sessions.netease_qr.qr_svg_data_url",
        lambda payload: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    )
    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, _ = flow.begin(Source.NETEASE)
    result = flow.poll(Source.NETEASE, token)
    assert result.state is QrLoginState.SUCCEEDED
    assert result.material is not None
    header = result.material.cookie_header_for(Source.NETEASE)
    assert "MUSIC_U=confirmed-session" in header
    assert "PLACEHOLDER=" not in header
    flow.discard(Source.NETEASE, token)


def test_cookie_jar_skips_empty_values_without_aborting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "MUSIC_U=keep; Path=/"),
                ("set-cookie", "EMPTY=; Path=/"),
            ],
            json={"code": 803},
        )

    client = PlatformCookieJarClient(
        allowed_host="music.163.com", transport=httpx.MockTransport(handler)
    )
    client.post("https://music.163.com/api/login/qrcode/client/login", data={"key": "k"})
    assert client.cookie_mapping() == {"MUSIC_U": "keep"}
    client.close()


def test_cookie_jar_prefers_root_path_when_same_name_conflicts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "MUSIC_U=deep; Path=/api"),
                ("set-cookie", "MUSIC_U=root; Path=/"),
            ],
            json={"code": 803},
        )

    client = PlatformCookieJarClient(
        allowed_host="music.163.com", transport=httpx.MockTransport(handler)
    )
    client.post("https://music.163.com/api/login/qrcode/client/login", data={"key": "k"})
    assert client.cookie_mapping()["MUSIC_U"] == "root"
    client.close()


def test_netease_confirm_8821_with_session_cookies_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unikey"):
            return httpx.Response(200, json={"code": 200, "unikey": "gate-key"})
        if request.url.path.endswith("/client/login"):
            return httpx.Response(
                200,
                headers=[("set-cookie", "MUSIC_U=gate-session; Path=/")],
                json={
                    "code": 8821,
                    "message": "需要安全验证",
                    "redirectUrl": "https://music.163.com/st/user/gate",
                },
            )
        if request.url.path.endswith("/account/get"):
            assert "MUSIC_U=gate-session" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 3},
                    "profile": {"userId": 3, "nickname": "Gate"},
                },
            )
        raise AssertionError(request.url.path)

    monkeypatch.setattr(
        "musicdl_web.sessions.netease_qr.qr_svg_data_url",
        lambda payload: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    )
    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, _ = flow.begin(Source.NETEASE)
    result = flow.poll(Source.NETEASE, token)
    assert result.state is QrLoginState.SUCCEEDED
    assert result.material is not None
    flow.discard(Source.NETEASE, token)


def test_netease_confirm_8821_without_cookies_fails_with_cookie_import_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unikey"):
            return httpx.Response(200, json={"code": 200, "unikey": "gate-key-2"})
        if request.url.path.endswith("/client/login"):
            return httpx.Response(
                200,
                json={
                    "code": 8821,
                    "message": "需要安全验证",
                    "redirectUrl": "https://st.music.163.com/encrypt-pages?qrCode=redacted",
                },
            )
        if request.url.path.startswith("/st/") or "encrypt" in request.url.path:
            raise AssertionError("cross-host security page must not be fetched")
        raise AssertionError(request.url.path)

    monkeypatch.setattr(
        "musicdl_web.sessions.netease_qr.qr_svg_data_url",
        lambda payload: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    )
    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, _ = flow.begin(Source.NETEASE)
    with pytest.raises(QrLoginError, match="导入登录 Cookie"):
        flow.poll(Source.NETEASE, token)
    flow.discard(Source.NETEASE, token)


def test_netease_confirm_accepts_cookie_field_in_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/unikey"):
            return httpx.Response(200, json={"code": 200, "unikey": "body-cookie-key"})
        if request.url.path.endswith("/client/login"):
            # No Set-Cookie; session only in body — observed on some Netease edges.
            return httpx.Response(
                200,
                json={
                    "code": 803,
                    "message": "授权成功",
                    "cookie": "MUSIC_U=body-session; __csrf=csrf-token",
                },
            )
        if request.url.path.endswith("/account/get"):
            assert "MUSIC_U=body-session" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 7},
                    "profile": {"userId": 7, "nickname": "BodyCookie"},
                },
            )
        raise AssertionError(request.url.path)

    monkeypatch.setattr(
        "musicdl_web.sessions.netease_qr.qr_svg_data_url",
        lambda payload: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    )
    flow = NeteaseQrLoginFlow(transport=httpx.MockTransport(handler))
    token, _ = flow.begin(Source.NETEASE)
    result = flow.poll(Source.NETEASE, token)
    assert result.state is QrLoginState.SUCCEEDED
    assert result.material is not None
    assert "MUSIC_U=body-session" in result.material.cookie_header_for(Source.NETEASE)
    flow.discard(Source.NETEASE, token)


def test_qr_cookie_jar_rejects_cross_host_redirects_and_caller_cookie_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    client = PlatformCookieJarClient(
        allowed_host="music.163.com", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(NetworkPolicyError, match="destination"):
        client.get("https://evil.example/account")
    with pytest.raises(NetworkPolicyError, match="cookie headers"):
        client.get(
            "https://music.163.com/api/w/nuser/account/get",
            headers={"Cookie": "MUSIC_U=caller-secret"},
        )
    with pytest.raises(NetworkRequestError, match="redirects are forbidden") as error:
        client.get("https://music.163.com/api/w/nuser/account/get")
    assert "evil.example" not in str(error.value)
    assert "caller-secret" not in str(error.value)
    client.close()


async def test_runtime_qr_api_is_secret_free_and_retains_old_session_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from musicdl_web.runtime import ProductionPlatformRuntime, RuntimeSettings

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"q" * 32)
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.url.path == "/api/w/nuser/account/get":
            cookie = request.headers.get("cookie", "")
            if cookie == "MUSIC_U=old-session":
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "account": {"id": 7},
                        "profile": {"userId": 7, "nickname": "旧会话"},
                    },
                )
            return httpx.Response(200, json={"code": 200, "account": None, "profile": None})
        if request.url.path == "/api/login/qrcode/unikey":
            return httpx.Response(200, json={"code": 200, "unikey": "api-secret-key"})
        if request.url.path == "/api/login/qrcode/client/login":
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(200, json={"code": 802})
            return httpx.Response(
                200,
                headers={"set-cookie": "MUSIC_U=new-session; Path=/; Secure"},
                json={"code": 803},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    runtime.import_session(Source.NETEASE, "MUSIC_U=old-session")
    with pytest.raises(ValueError, match="validation failed"):
        runtime.import_session(Source.NETEASE, "MUSIC_U=unverified-session")
    retained = runtime._sessions.material(Source.NETEASE)
    assert retained is not None
    assert retained[0].cookie_header_for(Source.NETEASE) == "MUSIC_U=old-session"
    app = FastAPI()
    app.include_router(create_platform_router(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post("/api/v1/sessions/netease/qr")
        challenge = started.json()
        image = await client.get(challenge["imageUrl"])
        scanned = await client.get(
            f"/api/v1/sessions/netease/qr/{challenge['challengeId']}"
        )
        failed = await client.get(
            f"/api/v1/sessions/netease/qr/{challenge['challengeId']}"
        )
        cancellable = (
            await client.post("/api/v1/sessions/netease/qr")
        ).json()
        cancelled = await client.delete(
            f"/api/v1/sessions/netease/qr/{cancellable['challengeId']}"
        )
        missing = await client.get(
            f"/api/v1/sessions/netease/qr/{cancellable['challengeId']}"
        )
        missing_image = await client.get(cancellable["imageUrl"])
        expiring = (await client.post("/api/v1/sessions/netease/qr")).json()

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr("musicdl_web.sessions.qr.datetime", ExpiredClock)
        expired_image = await client.get(expiring["imageUrl"])

    assert started.status_code == 200
    assert set(challenge) == {"challengeId", "state", "imageUrl", "expiresAt"}
    assert challenge["state"] == "waiting"
    assert challenge["imageUrl"].startswith("/api/v1/sessions/netease/qr/")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/svg+xml"
    assert image.headers["cache-control"] == "no-store"
    assert image.headers["referrer-policy"] == "no-referrer"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.content.startswith(b"<svg")
    assert "data:image" not in str(challenge)
    assert "api-secret-key" not in str(challenge)
    assert scanned.json() == {"state": "scanned"}
    assert failed.status_code == 502
    assert "new-session" not in failed.text
    assert cancelled.status_code == 204
    assert missing.status_code == 404
    assert missing_image.status_code == 404
    assert expired_image.status_code == 404
    loaded = runtime._sessions.material(Source.NETEASE)
    assert loaded is not None
    assert loaded[0].cookie_header_for(Source.NETEASE) == "MUSIC_U=old-session"
    runtime.close()


async def test_runtime_qr_success_atomically_replaces_session_and_returns_safe_view(
    tmp_path: Path,
) -> None:
    from musicdl_web.runtime import ProductionPlatformRuntime, RuntimeSettings

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"s" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login/qrcode/unikey":
            return httpx.Response(200, json={"code": 200, "unikey": "success-key"})
        if request.url.path == "/api/login/qrcode/client/login":
            return httpx.Response(
                200,
                headers={"set-cookie": "MUSIC_U=verified-session; Path=/; Secure"},
                json={"code": 803},
            )
        if request.url.path == "/api/w/nuser/account/get":
            assert request.headers.get("cookie") == "MUSIC_U=verified-session"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 99},
                    "profile": {"userId": 99, "nickname": "已验证"},
                },
            )
        raise AssertionError("unexpected request")

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    app = FastAPI()
    app.include_router(create_platform_router(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        challenge = (await client.post("/api/v1/sessions/netease/qr")).json()
        succeeded = await client.get(
            f"/api/v1/sessions/netease/qr/{challenge['challengeId']}"
        )

    body = succeeded.json()
    assert succeeded.status_code == 200
    assert body["state"] == "success"
    assert set(body["session"]) == {
        "source",
        "state",
        "qrEnabled",
        "checkedAt",
    }
    assert body["session"]["state"] == "authenticated"
    assert "success-key" not in str(body)
    assert "verified-session" not in str(body)
    loaded = runtime._sessions.material(Source.NETEASE)
    assert loaded is not None
    assert loaded[0].cookie_header_for(Source.NETEASE) == "MUSIC_U=verified-session"
    runtime.close()


def test_runtime_serializes_concurrent_qr_start_and_cancel(tmp_path: Path) -> None:
    from musicdl_web.runtime import ProductionPlatformRuntime, RuntimeSettings

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"t" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/login/qrcode/unikey"
        return httpx.Response(200, json={"code": 200, "unikey": "thread-key"})

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        challenges = tuple(
            executor.map(runtime.start_qr, (Source.NETEASE for _ in range(8)))
        )
        tuple(
            executor.map(
                lambda challenge: runtime.cancel_qr(
                    Source.NETEASE, challenge.challenge_id
                ),
                challenges,
            )
        )

    assert len({challenge.challenge_id for challenge in challenges}) == 8
    runtime.close()


def test_manual_import_and_clear_cancel_older_qr_challenges(tmp_path: Path) -> None:
    from musicdl_web.runtime import ProductionPlatformRuntime, RuntimeSettings

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"u" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login/qrcode/unikey":
            return httpx.Response(200, json={"code": 200, "unikey": "older-key"})
        if request.url.path == "/api/w/nuser/account/get":
            assert request.headers.get("cookie") == "MUSIC_U=manual-session"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "account": {"id": 5},
                    "profile": {"userId": 5, "nickname": "手工会话"},
                },
            )
        raise AssertionError("an older QR challenge must not be polled")

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )
    replaced = runtime.start_qr(Source.NETEASE)
    runtime.import_session(Source.NETEASE, "MUSIC_U=manual-session")
    with pytest.raises(LookupError):
        runtime.poll_qr(Source.NETEASE, replaced.challenge_id)
    with pytest.raises(LookupError):
        runtime.qr_image(Source.NETEASE, replaced.challenge_id)
    loaded = runtime._sessions.material(Source.NETEASE)
    assert loaded is not None
    assert loaded[0].cookie_header_for(Source.NETEASE) == "MUSIC_U=manual-session"

    cleared = runtime.start_qr(Source.NETEASE)
    runtime.clear_session(Source.NETEASE)
    with pytest.raises(LookupError):
        runtime.poll_qr(Source.NETEASE, cleared.challenge_id)
    with pytest.raises(LookupError):
        runtime.qr_image(Source.NETEASE, cleared.challenge_id)
    assert runtime._sessions.material(Source.NETEASE) is None
    runtime.close()


def test_clear_waiting_behind_import_validation_wins_by_lock_order(tmp_path: Path) -> None:
    from musicdl_web.runtime import ProductionPlatformRuntime, RuntimeSettings

    key_file = tmp_path / "session.key"
    key_file.write_bytes(b"v" * 32)
    validation_started = Event()
    release_validation = Event()
    clear_started = Event()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/w/nuser/account/get"
        validation_started.set()
        assert release_validation.wait(timeout=2)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "account": {"id": 6},
                "profile": {"userId": 6, "nickname": "串行会话"},
            },
        )

    runtime = ProductionPlatformRuntime(
        RuntimeSettings(
            session_key_file=key_file,
            session_root=tmp_path / "sessions",
            artwork_root=tmp_path / "artwork",
        ),
        transport=httpx.MockTransport(handler),
    )

    def clear() -> None:
        clear_started.set()
        runtime.clear_session(Source.NETEASE)

    with ThreadPoolExecutor(max_workers=2) as executor:
        imported = executor.submit(
            runtime.import_session, Source.NETEASE, "MUSIC_U=ordered-session"
        )
        assert validation_started.wait(timeout=2)
        cleared = executor.submit(clear)
        assert clear_started.wait(timeout=2)
        release_validation.set()
        imported.result(timeout=2)
        cleared.result(timeout=2)

    assert runtime._sessions.material(Source.NETEASE) is None
    runtime.close()
