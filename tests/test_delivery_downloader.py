from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from musicdl_web.download import DownloadError, SafeDownloader
from musicdl_web.models import Source
from musicdl_web.platforms.quality import DownloadGrant


def grant(url: str = "https://audio.example/song") -> DownloadGrant:
    return DownloadGrant(
        source=Source.NETEASE,
        track_id="42",
        quality_id="lossless",
        quality_rank=30,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        allowed_hosts=frozenset({"audio.example"}),
        _source_url=url,
        expected_bytes=8,
    )


async def test_download_accepts_only_allowlisted_https_and_exact_length(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "audio/flac", "content-length": "8"}, content=b"fLaCdata"
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        size = await SafeDownloader(client).download(grant(), tmp_path / "song")

    assert size == 8
    assert (tmp_path / "song").read_bytes() == b"fLaCdata"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            httpx.Response(302, headers={"location": "https://evil.example/song"}),
            "host_not_allowed",
        ),
        (httpx.Response(403), "authorization_rejected"),
        (
            httpx.Response(
                200,
                headers={"content-type": "text/html", "content-length": "8"},
                content=b"<html />",
            ),
            "unexpected_content",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "audio/flac", "content-length": "4"},
                content=b"fLaC",
            ),
            "length_mismatch",
        ),
    ],
)
async def test_download_rejects_unsafe_or_inconsistent_responses(
    tmp_path: Path, response: httpx.Response, code: str
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(DownloadError) as caught:
            await SafeDownloader(client).download(grant(), tmp_path / "song")

    assert caught.value.code == code
    assert not (tmp_path / "song").exists()


async def test_download_rejects_expired_grant_before_network(tmp_path: Path) -> None:
    expired = DownloadGrant(
        source=Source.QQ,
        track_id="x",
        quality_id="high",
        quality_rank=20,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        allowed_hosts=frozenset({"audio.example"}),
        _source_url="https://audio.example/song",
    )
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DownloadError) as caught:
            await SafeDownloader(client).download(expired, tmp_path / "song")

    assert caught.value.code == "grant_expired"


async def test_download_rejects_non_standard_https_port_before_network(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError) as caught:
            await SafeDownloader(client).download(
                grant("https://audio.example:8443/song"), tmp_path / "song"
            )

    assert caught.value.code == "host_not_allowed"
    assert calls == []
