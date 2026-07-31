"""Shared, platform-neutral parsing helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..errors import PlatformResponseError
from ..models import Source


def require_mapping(value: Any, source: Source, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformResponseError(source, reason)
    return value


def require_list(value: Any, source: Source, reason: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlatformResponseError(source, reason)
    return value


def require_string(value: Any, source: Source, reason: str) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise PlatformResponseError(source, reason)
    return str(value)


def normalize_cover_url(
    value: Any,
    *,
    allowed_host_suffixes: tuple[str, ...],
) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed_host_suffixes):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.port not in (None, 80, 443):
        return None
    return urlunsplit(
        ("https", parsed.netloc.split("@")[-1].split(":")[0], parsed.path, parsed.query, "")
    )
