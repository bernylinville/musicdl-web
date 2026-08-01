from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from musicdl_web.download import DownloadError, SafeDownloader
from musicdl_web.models import Source
from musicdl_web.platforms import DownloadGrant


def grant() -> DownloadGrant:
    return DownloadGrant(
        source=Source.NETEASE,
        track_id="track-1",
        quality_id="lossless",
        quality_rank=30,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        allowed_hosts=frozenset({"media.music.163.com"}),
        _source_url="https://media.music.163.com/audio",
    )


async def download_with(response: httpx.Response, target: Path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        await SafeDownloader(client).download(grant(), target)


async def test_download_rejects_redirect_to_a_non_allowlisted_host(tmp_path: Path) -> None:
    target = tmp_path / "audio.part"

    with pytest.raises(DownloadError) as error:
        await download_with(
            httpx.Response(302, headers={"location": "https://resolver.invalid/audio"}),
            target,
        )

    assert error.value.code == "host_not_allowed"
    assert not target.exists()


async def test_download_rejects_html_response(tmp_path: Path) -> None:
    target = tmp_path / "audio.part"

    with pytest.raises(DownloadError) as error:
        await download_with(
            httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
            target,
        )

    assert error.value.code == "unexpected_content"


async def test_download_removes_a_truncated_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "audio.part"

    with pytest.raises(DownloadError) as error:
        await download_with(
            httpx.Response(
                200,
                headers={"content-type": "audio/flac", "content-length": "100"},
                content=b"fLaC" + b"x" * 20,
            ),
            target,
        )

    assert error.value.code == "truncated"
    assert not target.exists()


@pytest.mark.parametrize("status", [401, 403])
async def test_download_reports_platform_authorization_rejection(
    status: int, tmp_path: Path
) -> None:
    with pytest.raises(DownloadError) as error:
        await download_with(httpx.Response(status), tmp_path / "audio.part")

    assert error.value.code == "authorization_rejected"
