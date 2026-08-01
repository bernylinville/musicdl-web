"""Same-origin artwork proxy with strict platform-host and response limits."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from musicdl_web.models import Source
from musicdl_web.network import PlatformHttpClient


class ArtworkUnavailable(Exception):
    """Safe artwork failure that never contains a platform URL."""


@dataclass(frozen=True, slots=True)
class Artwork:
    body: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class _MemoryEntry:
    artwork: Artwork
    expires_at: datetime


class ArtworkProxy:
    # Netease album art is often multi-megabyte PNG; keep a firm upper bound.
    _MAX_BYTES = 5 * 1024 * 1024
    _HOST_SUFFIXES = {
        Source.NETEASE: ("music.126.net",),
        Source.QQ: ("y.gtimg.cn",),
    }
    _HEADERS = {
        Source.NETEASE: {"Referer": "https://music.163.com/", "User-Agent": "musicdl-web/0.2"},
        Source.QQ: {"Referer": "https://y.qq.com/", "User-Agent": "musicdl-web/0.2"},
    }
    _ALLOWED_TYPES = frozenset({"image/jpeg", "image/png"})

    def __init__(
        self,
        root: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("artwork cache lifetime must be positive")
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)
        self._transport = transport
        self._ttl = ttl
        self._memory: dict[tuple[Source, str], _MemoryEntry] = {}
        self._lock = threading.Lock()

    def fetch(self, source: Source, track_id: str, source_url: str) -> Artwork:
        key = (source, track_id)
        cached = self.cached(source, track_id)
        if cached is not None:
            return cached
        host = self._validate_source_url(source, source_url)
        client = PlatformHttpClient(allowed_hosts={host}, transport=self._transport)
        try:
            response = client.get_limited(
                source_url,
                max_bytes=self._MAX_BYTES,
                headers=self._HEADERS[source],
            )
        except Exception:
            raise ArtworkUnavailable("artwork is unavailable") from None
        finally:
            client.close()
        body = response.content
        content_type = detect_image_type(body)
        if content_type is None:
            raise ArtworkUnavailable("artwork response is not a valid JPEG or PNG")
        artwork = Artwork(body=body, content_type=content_type)
        try:
            self._store(key, artwork)
        except Exception:
            raise ArtworkUnavailable("artwork could not be cached") from None
        return artwork

    def cached(self, source: Source, track_id: str) -> Artwork | None:
        key = (source, track_id)
        now = datetime.now(UTC)
        with self._lock:
            entry = self._memory.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.artwork
            if entry is not None:
                self._memory.pop(key, None)
        path = self._path(source, track_id)
        meta = path.with_suffix(".type")
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if modified_at + self._ttl <= now:
                self._delete_pair(path, meta)
                return None
            body = path.read_bytes()
            content_type = meta.read_text(encoding="ascii").strip()
        except OSError:
            return None
        detected = detect_image_type(body)
        if (
            len(body) > self._MAX_BYTES
            or detected is None
            or content_type not in self._ALLOWED_TYPES
            or detected != content_type
        ):
            self._delete_pair(path, meta)
            return None
        artwork = Artwork(body=body, content_type=content_type)
        with self._lock:
            self._memory[key] = _MemoryEntry(artwork, now + self._ttl)
        return artwork

    def _store(self, key: tuple[Source, str], artwork: Artwork) -> None:
        path = self._path(*key)
        self._atomic_write(path, artwork.body)
        self._atomic_write(path.with_suffix(".type"), artwork.content_type.encode("ascii"))
        with self._lock:
            self._memory[key] = _MemoryEntry(artwork, datetime.now(UTC) + self._ttl)

    def _path(self, source: Source, track_id: str) -> Path:
        digest = hashlib.sha256(f"{source.value}:{track_id}".encode()).hexdigest()
        return self._root / f"{digest}.image"

    def _atomic_write(self, path: Path, value: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".artwork-", dir=self._root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = ""
            os.chmod(path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _delete_pair(path: Path, meta: Path) -> None:
        path.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)

    def _validate_source_url(self, source: Source, value: str) -> str:
        parsed = urlsplit(value)
        host = parsed.hostname.lower() if parsed.hostname else ""
        suffixes = self._HOST_SUFFIXES[source]
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or not any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)
        ):
            raise ArtworkUnavailable("artwork location is outside the allowlist")
        return host


def detect_image_type(body: bytes) -> str | None:
    """Return image/jpeg or image/png from magic bytes; ignore declared Content-Type."""

    if is_jpeg(body):
        return "image/jpeg"
    if is_png(body):
        return "image/png"
    return None


def is_jpeg(body: bytes) -> bool:
    return len(body) >= 4 and body.startswith(b"\xff\xd8\xff") and body.endswith(b"\xff\xd9")


def is_png(body: bytes) -> bool:
    # Signature + IEND chunk trailer is enough for proxy validation.
    return (
        len(body) >= 24
        and body.startswith(b"\x89PNG\r\n\x1a\n")
        and b"IEND" in body[-12:]
    )


def cover_sidecar_name(content_type: str) -> str:
    if content_type == "image/png":
        return "cover.png"
    return "cover.jpg"
