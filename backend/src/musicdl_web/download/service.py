"""Streaming downloader that refuses redirects and responses outside the grant."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from musicdl_web.platforms.quality import DownloadGrant


class DownloadError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SafeDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_redirects: int = 5,
        max_bytes: int = 1_500_000_000,
    ) -> None:
        self._client = client
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes

    async def download(self, grant: DownloadGrant, target: Path) -> int:
        if grant.expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise DownloadError("grant_expired", "download authorization expired")
        url = grant.source_url_for_downloader(grant.source)
        self._validate_url(url, grant.allowed_hosts)
        for redirect_count in range(self._max_redirects + 1):
            async with self._client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count == self._max_redirects:
                        raise DownloadError("redirect_limit", "too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise DownloadError("invalid_redirect", "redirect has no location")
                    url = urljoin(url, location)
                    self._validate_url(url, grant.allowed_hosts)
                    continue
                if response.status_code in {401, 403}:
                    raise DownloadError("authorization_rejected", "platform rejected authorization")
                if response.status_code >= 400:
                    raise DownloadError(
                        "http_error", f"platform returned HTTP {response.status_code}"
                    )
                content_type = response.headers.get("content-type", "").lower()
                if "html" in content_type or content_type.startswith("text/"):
                    raise DownloadError("unexpected_content", "platform returned non-audio content")
                expected = _content_length(response.headers.get("content-length"))
                if grant.expected_bytes is not None and expected != grant.expected_bytes:
                    raise DownloadError(
                        "length_mismatch", "response size differs from platform authorization"
                    )
                if expected is not None and expected > self._max_bytes:
                    raise DownloadError("too_large", "audio exceeds configured size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                try:
                    with target.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self._max_bytes:
                                raise DownloadError(
                                    "too_large", "audio exceeds configured size limit"
                                )
                            output.write(chunk)
                except BaseException:
                    _unlink(target)
                    raise
                if size == 0:
                    _unlink(target)
                    raise DownloadError("empty_body", "platform returned an empty audio file")
                if expected is not None and size != expected:
                    _unlink(target)
                    raise DownloadError("truncated", "audio size does not match Content-Length")
                return size
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_url(url: str, allowed_hosts: frozenset[str]) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        normalized = {item.lower().rstrip(".") for item in allowed_hosts}
        if (
            parsed.scheme != "https"
            or not host
            or host not in normalized
            or parsed.port not in (None, 443)
        ):
            raise DownloadError(
                "host_not_allowed", "download URL is outside the platform allowlist"
            )
        if parsed.username or parsed.password:
            raise DownloadError("invalid_url", "download URL must not contain credentials")


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise DownloadError("invalid_length", "invalid Content-Length") from exc
    if result < 0:
        raise DownloadError("invalid_length", "invalid Content-Length")
    return result


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


def create_download_client(*, timeout: float = 60.0) -> httpx.AsyncClient:
    """Create the sole production async media client with certificate checks enabled."""

    return httpx.AsyncClient(
        verify=True,
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=False,
    )
