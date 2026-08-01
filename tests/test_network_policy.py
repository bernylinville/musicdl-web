from __future__ import annotations

import httpx
import pytest
from musicdl_web.errors import NetworkPolicyError, NetworkRequestError
from musicdl_web.network import PlatformHttpClient


class NonVerifyingTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)


def test_non_mock_transport_cannot_bypass_tls_configuration() -> None:
    with pytest.raises(ValueError, match="MockTransport"):
        PlatformHttpClient(
            allowed_hosts={"music.163.com"},
            transport=NonVerifyingTransport(),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://music.163.com/api/cloudsearch/pc",
        "https://third-party.invalid/search",
        "https://music.163.com:8443/api/cloudsearch/pc",
        "https://user:secret@music.163.com/api/cloudsearch/pc",
    ],
)
def test_initial_request_must_use_allowed_https_origin(url: str) -> None:
    client = PlatformHttpClient(
        allowed_hosts={"music.163.com"},
        transport=httpx.MockTransport(
            lambda request: pytest.fail("policy violation reached transport")
        ),
    )

    with pytest.raises(NetworkPolicyError) as exc_info:
        client.post(url)

    assert url not in str(exc_info.value)


def test_cross_allowlist_redirect_is_rejected_before_following() -> None:
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(
            302,
            headers={"location": "https://u.y.qq.com/cgi-bin/musicu.fcg?token=secret"},
        )

    client = PlatformHttpClient(
        allowed_hosts={"music.163.com"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(NetworkPolicyError) as exc_info:
        client.post("https://music.163.com/api/cloudsearch/pc")

    assert seen_hosts == ["music.163.com"]
    assert "token" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_caller_supplied_cookie_header_is_removed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "cookie" not in request.headers
        return httpx.Response(200, json={})

    client = PlatformHttpClient(
        allowed_hosts={"music.163.com"}, transport=httpx.MockTransport(handler)
    )

    client.post(
        "https://music.163.com/api/cloudsearch/pc",
        headers={"Cookie": "shared=must-not-be-sent"},
    )


def test_get_uses_the_same_https_and_cookie_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "cookie" not in request.headers
        return httpx.Response(200, content=b"image")

    client = PlatformHttpClient(
        allowed_hosts={"p1.music.126.net"}, transport=httpx.MockTransport(handler)
    )

    response = client.get(
        "https://p1.music.126.net/cover.jpg",
        headers={"Cookie": "shared=must-not-be-sent"},
    )

    assert response.content == b"image"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, stream=httpx.ByteStream(b"123456")),
        httpx.Response(200, headers={"content-length": "6"}, content=b"123456"),
    ],
)
def test_limited_get_stops_oversized_responses_before_returning(
    response: httpx.Response,
) -> None:
    client = PlatformHttpClient(
        allowed_hosts={"p1.music.126.net"},
        transport=httpx.MockTransport(lambda request: response),
    )

    with pytest.raises(NetworkRequestError, match="size limit"):
        client.get_limited("https://p1.music.126.net/cover.jpg", max_bytes=5)


def test_http_failure_is_sanitized() -> None:
    client = PlatformHttpClient(
        allowed_hosts={"music.163.com"},
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
    )

    with pytest.raises(NetworkRequestError) as exc_info:
        client.post("https://music.163.com/api/cloudsearch/pc?token=secret")

    message = str(exc_info.value)
    assert message == "platform request returned HTTP 403"
    assert "secret" not in message
