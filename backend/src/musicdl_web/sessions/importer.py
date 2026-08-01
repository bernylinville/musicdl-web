"""Strict parser for manually imported Cookie request headers."""

from __future__ import annotations

import re
from datetime import datetime

from ..models import Source
from .errors import SessionImportError
from .models import SessionMaterial

_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SET_COOKIE_ATTRIBUTES = frozenset(
    {"domain", "expires", "httponly", "max-age", "path", "samesite", "secure"}
)
_MAX_HEADER_BYTES = 8192
_MAX_COOKIES = 64
_AUTH_COOKIE_NAMES = {
    Source.NETEASE: frozenset({"MUSIC_A", "MUSIC_U"}),
    Source.QQ: frozenset({"lskey", "p_lskey", "p_skey", "qqmusic_key", "skey"}),
}


def import_cookie_header(
    source: Source,
    header: str,
    *,
    expires_at: datetime | None = None,
) -> SessionMaterial:
    """Parse one Cookie header and bind it permanently to one platform."""

    if not isinstance(header, str) or not header.strip():
        raise SessionImportError("cookie header must not be blank")
    if len(header.encode("utf-8")) > _MAX_HEADER_BYTES:
        raise SessionImportError("cookie header is too large")
    if "\r" in header or "\n" in header or "\0" in header:
        raise SessionImportError("cookie header contains forbidden control characters")

    cookies: dict[str, str] = {}
    parts = header.split(";")
    if len(parts) > _MAX_COOKIES:
        raise SessionImportError("cookie header contains too many entries")
    for part in parts:
        pair = part.strip()
        if not pair or "=" not in pair:
            raise SessionImportError("cookie header contains an invalid entry")
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not _COOKIE_NAME.fullmatch(name):
            raise SessionImportError("cookie header contains an invalid name")
        if name.lower() in _SET_COOKIE_ATTRIBUTES:
            raise SessionImportError("Set-Cookie attributes are not accepted")
        if name in cookies:
            raise SessionImportError("cookie header contains a duplicate name")
        if not value or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value):
            raise SessionImportError("cookie header contains an invalid value")
        if any(character in value for character in ('"', ',', ';', '\\')):
            raise SessionImportError("cookie header contains an invalid value")
        cookies[name] = value

    if _AUTH_COOKIE_NAMES[source].isdisjoint(cookies):
        raise SessionImportError("cookie header has no recognized session credential")

    return SessionMaterial(source=source, _cookies=cookies, expires_at=expires_at)
