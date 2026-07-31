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
