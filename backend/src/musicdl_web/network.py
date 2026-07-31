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
        method = "POST"
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
                response = self._client.send(request, follow_redirects=False)
            except httpx.HTTPError:
                raise NetworkRequestError("platform request failed") from None

            if not response.is_redirect:
                if response.is_error:
                    raise NetworkRequestError(
                        f"platform request returned HTTP {response.status_code}"
                    )
                return response

            location = response.headers.get("location")
            if not location:
                raise NetworkRequestError("platform redirect omitted its destination")
            if redirect_count == self._max_redirects:
                raise NetworkRequestError("platform request exceeded redirect limit")

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
