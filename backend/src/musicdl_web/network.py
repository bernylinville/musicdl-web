"""Strict HTTP boundary for platform-owned search endpoints."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from types import TracebackType
from typing import Any, Self
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import NetworkPolicyError, NetworkRequestError


class PlatformHttpClient:
    """HTTP client that validates the initial request and every redirect."""

    def __init__(
        self,
        *,
        allowed_hosts: Collection[str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
        max_redirects: int = 5,
    ) -> None:
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("only httpx.MockTransport may be injected for tests")
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._max_redirects = max_redirects
        self._accessed_hosts: list[str] = []
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
        )

    def post(
        self,
        url: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._request(
            "POST", url, data=data, json=json, headers=headers, credential_host=None
        )

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._request(
            "GET", url, data=None, json=None, headers=headers, credential_host=None
        )

    def get_limited(
        self,
        url: str,
        *,
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if max_bytes < 1:
            raise ValueError("response limit must be positive")
        return self._request(
            "GET",
            url,
            data=None,
            json=None,
            headers=headers,
            credential_host=None,
            max_bytes=max_bytes,
        )

    def post_authenticated(
        self,
        url: str,
        *,
        cookie_header: str,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """POST credentials only to the exact allowlisted control host.

        The credential is injected after caller headers are copied, so generic callers cannot
        smuggle or override it. Redirects to a different host fail before another request is sent.
        """

        host = urlsplit(url).hostname
        if not host or host.lower() not in self._allowed_hosts:
            raise NetworkPolicyError("credential destination host is not allowed")
        if not cookie_header or "\r" in cookie_header or "\n" in cookie_header:
            raise NetworkPolicyError("platform credential is invalid")
        return self._request(
            "POST",
            url,
            data=data,
            json=json,
            headers=headers,
            credential_host=host.lower(),
            cookie_header=cookie_header,
        )

    def get_authenticated(
        self,
        url: str,
        *,
        cookie_header: str,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        host = urlsplit(url).hostname
        if not host or host.lower() not in self._allowed_hosts:
            raise NetworkPolicyError("credential destination host is not allowed")
        if not cookie_header or "\r" in cookie_header or "\n" in cookie_header:
            raise NetworkPolicyError("platform credential is invalid")
        return self._request(
            "GET",
            url,
            data=None,
            json=None,
            headers=headers,
            credential_host=host.lower(),
            cookie_header=cookie_header,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, Any] | None,
        json: Any,
        headers: Mapping[str, str] | None,
        credential_host: str | None,
        cookie_header: str | None = None,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        request_data = data
        request_json = json
        current_url = url

        for redirect_count in range(self._max_redirects + 1):
            self._validate_url(current_url)
            host = urlsplit(current_url).hostname
            if host is not None:
                self._accessed_hosts.append(host.lower())
            try:
                request = self._client.build_request(
                    method,
                    current_url,
                    data=request_data,
                    json=request_json,
                    headers=headers,
                )
                request.headers.pop("cookie", None)
                if credential_host is not None:
                    current_host = urlsplit(current_url).hostname
                    if current_host is None or current_host.lower() != credential_host:
                        raise NetworkPolicyError(
                            "platform credentials cannot cross control hosts"
                        )
                    request.headers["cookie"] = cookie_header or ""
                response = self._client.send(
                    request,
                    follow_redirects=False,
                    stream=max_bytes is not None,
                )
            except httpx.HTTPError:
                raise NetworkRequestError("platform request failed") from None

            if not response.is_redirect:
                if response.is_error:
                    response.close()
                    raise NetworkRequestError(
                        f"platform request returned HTTP {response.status_code}"
                    )
                if max_bytes is not None:
                    return _read_limited(response, max_bytes)
                return response

            location = response.headers.get("location")
            if not location:
                response.close()
                raise NetworkRequestError("platform redirect omitted its destination")
            if redirect_count == self._max_redirects:
                response.close()
                raise NetworkRequestError("platform request exceeded redirect limit")

            response.close()
            current_url = urljoin(current_url, location)
            if response.status_code in (301, 302, 303):
                method = "GET"
                request_data = None
                request_json = None

        raise NetworkRequestError("platform request failed")

    @property
    def accessed_hosts(self) -> tuple[str, ...]:
        """Hosts reached by validated requests, without paths or query strings."""

        return tuple(self._accessed_hosts)

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.scheme != "https":
            raise NetworkPolicyError("platform requests require HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise NetworkPolicyError("platform request credentials are forbidden")
        if host not in self._allowed_hosts:
            raise NetworkPolicyError("platform request host is not allowed")
        if parsed.port not in (None, 443):
            raise NetworkPolicyError("platform request port is not allowed")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _read_limited(response: httpx.Response, max_bytes: int) -> httpx.Response:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError:
            response.close()
            raise NetworkRequestError("platform response length is invalid") from None
        if declared_bytes < 0 or declared_bytes > max_bytes:
            response.close()
            raise NetworkRequestError("platform response exceeds the size limit")
    body = bytearray()
    try:
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise NetworkRequestError("platform response exceeds the size limit")
    finally:
        response.close()
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=bytes(body),
        request=response.request,
    )


class PlatformCookieJarClient:
    """Per-flow cookie jar constrained to one exact HTTPS control host."""

    def __init__(
        self,
        *,
        allowed_host: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("only httpx.MockTransport may be injected for tests")
        normalized_host = allowed_host.strip().lower()
        if not normalized_host or ":" in normalized_host or "/" in normalized_host:
            raise ValueError("platform cookie-jar host is invalid")
        if headers is not None and any(name.lower() == "cookie" for name in headers):
            raise ValueError("caller cookie headers are forbidden")
        self._allowed_host = normalized_host
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
            headers=headers,
        )

    def post(
        self,
        url: str,
        *,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("POST", url, data=data, headers=headers)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("GET", url, data=None, headers=headers)

    def cookie_mapping(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            if not isinstance(cookie.value, str) or not cookie.value:
                raise NetworkRequestError("platform cookie jar is invalid")
            if cookie.name in cookies and cookies[cookie.name] != cookie.value:
                raise NetworkRequestError("platform cookie jar is ambiguous")
            cookies[cookie.name] = cookie.value
        return cookies

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        self._validate_url(url)
        if headers is not None and any(name.lower() == "cookie" for name in headers):
            raise NetworkPolicyError("caller cookie headers are forbidden")
        try:
            response = self._client.request(
                method,
                url,
                data=data,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise NetworkRequestError("platform request failed") from None
        if response.is_redirect:
            raise NetworkRequestError("platform redirects are forbidden")
        if response.is_error:
            raise NetworkRequestError(
                f"platform request returned HTTP {response.status_code}"
            )
        return response

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or host != self._allowed_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise NetworkPolicyError("platform request destination is not allowed")
