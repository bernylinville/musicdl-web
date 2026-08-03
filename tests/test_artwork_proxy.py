from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from musicdl_web.artwork import ArtworkProxy, ArtworkUnavailable
from musicdl_web.models import Source

JPEG = b"\xff\xd8\xff\xe0safe-jpeg\xff\xd9"


def test_artwork_proxy_fetches_allowlisted_image_and_reuses_opaque_disk_cache(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=JPEG)

    proxy = ArtworkProxy(tmp_path, transport=httpx.MockTransport(handler))
    artwork = proxy.fetch(
        Source.NETEASE, "101", "https://p1.music.126.net/cover.jpg?token=source-secret"
    )
    restored = ArtworkProxy(tmp_path).cached(Source.NETEASE, "101")

    assert artwork.body == JPEG
    assert artwork.content_type == "image/jpeg"
    assert restored == artwork
    assert [request.url.host for request in seen] == ["p1.music.126.net"]
    assert not any(b"source-secret" in path.read_bytes() for path in tmp_path.iterdir())


def test_artwork_proxy_normalizes_platform_image_jpg_alias(tmp_path: Path) -> None:
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/jpg"}, content=JPEG
            )
        ),
    )

    artwork = proxy.fetch(
        Source.NETEASE, "jpg-alias", "https://p1.music.126.net/cover.jpg"
    )

    assert artwork == ArtworkProxy(tmp_path).cached(Source.NETEASE, "jpg-alias")
    assert artwork.content_type == "image/jpeg"


@pytest.mark.parametrize(
    "url",
    [
        "http://p1.music.126.net/cover.jpg",
        "https://third-party.invalid/cover.jpg",
        "https://music.126.net.evil.invalid/cover.jpg",
    ],
)
def test_artwork_proxy_rejects_non_platform_locations(tmp_path: Path, url: str) -> None:
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid artwork reached the transport")
        ),
    )

    with pytest.raises(ArtworkUnavailable) as exc_info:
        proxy.fetch(Source.NETEASE, "101", url)

    assert url not in str(exc_info.value)


def test_artwork_proxy_accepts_png_by_magic_bytes(tmp_path: Path) -> None:
    # Minimal valid-looking PNG: signature + IEND trailer (proxy checks magic only).
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/jpg"}, content=png
            )
        ),
    )
    artwork = proxy.fetch(Source.NETEASE, "png-1", "https://p1.music.126.net/cover.png")
    assert artwork.content_type == "image/png"
    assert artwork.body == png


def test_artwork_proxy_accepts_large_netease_png_under_10mib(tmp_path: Path) -> None:
    # Regression: 泪桥 full-res cover is ~5.9 MiB PNG (failed at previous 5 MiB cap).
    filler = b"\x00" * (6 * 1024 * 1024)
    png = b"\x89PNG\r\n\x1a\n" + filler + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/jpg"}, content=png
            )
        ),
    )

    artwork = proxy.fetch(
        Source.NETEASE, "156736", "https://p2.music.126.net/large-cover.jpg"
    )

    assert artwork.content_type == "image/png"
    assert len(artwork.body) == len(png)


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("text/html", b"<html>"),
        ("image/png", b"\x89PNG\r\n"),
        ("image/webp", b"RIFFxxxxWEBP"),
        ("image/jpeg", b"not-a-jpeg"),
        ("image/jpeg", b"\xff\xd8\xff" + b"x" * (10 * 1024 * 1024) + b"\xff\xd9"),
    ],
)
def test_artwork_proxy_rejects_invalid_responses(
    tmp_path: Path, content_type: str, body: bytes
) -> None:
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": content_type}, content=body
            )
        ),
    )

    with pytest.raises(ArtworkUnavailable):
        proxy.fetch(Source.QQ, "mid", "https://y.gtimg.cn/cover.jpg")


def test_artwork_proxy_treats_cache_write_failure_as_nonfatal_unavailability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy = ArtworkProxy(
        tmp_path,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/jpeg"}, content=JPEG
            )
        ),
    )

    def fail_store(key: object, artwork: object) -> None:
        del key, artwork
        raise OSError

    monkeypatch.setattr(proxy, "_store", fail_store)

    with pytest.raises(ArtworkUnavailable, match="cached"):
        proxy.fetch(Source.NETEASE, "write-failure", "https://p1.music.126.net/cover.jpg")


def test_artwork_disk_cache_expires_across_process_restart(tmp_path: Path) -> None:
    proxy = ArtworkProxy(
        tmp_path,
        ttl=timedelta(seconds=1),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "image/jpeg"}, content=JPEG
            )
        ),
    )
    proxy.fetch(Source.NETEASE, "expired", "https://p1.music.126.net/cover.jpg")
    for path in tmp_path.iterdir():
        os.utime(path, (0, 0))

    assert ArtworkProxy(tmp_path, ttl=timedelta(seconds=1)).cached(
        Source.NETEASE, "expired"
    ) is None
    assert tuple(tmp_path.iterdir()) == ()
