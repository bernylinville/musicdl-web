"""Conservative signature probe used before a richer ffprobe integration is configured."""

from __future__ import annotations

from pathlib import Path

from musicdl_web.domain import MediaMetadata, Quality


class SignatureProbe:
    """Reject obvious HTML/truncated files; callers provide authoritative expected duration."""

    def __init__(self, duration_seconds: float, quality: Quality) -> None:
        self.duration_seconds = duration_seconds
        self.quality = quality

    def probe(self, path: Path) -> MediaMetadata:
        size = path.stat().st_size
        if size < 16:
            raise ValueError("media file is truncated")
        header = path.read_bytes()[:16]
        lowered = header.lower()
        if b"<html" in lowered or b"<!doctype" in lowered:
            raise ValueError("media response is HTML")
        extension, codec = _identify(header)
        return MediaMetadata(
            extension=extension,
            codec=codec,
            duration_seconds=self.duration_seconds,
            quality=self.quality,
        )


def _identify(header: bytes) -> tuple[str, str]:
    if header.startswith(b"fLaC"):
        return "flac", "flac"
    if header.startswith(b"OggS"):
        return "ogg", "vorbis"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF):
        return "mp3", "mp3"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "m4a", "aac"
    raise ValueError("unsupported or invalid media container")
