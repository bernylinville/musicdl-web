"""Official Netease QR-login flow with one isolated cookie jar per challenge."""

from __future__ import annotations

import json
import logging
from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..models import Source
from ..network import PlatformCookieJarClient
from .errors import QrLoginError, QrLoginUnavailable
from .models import SessionMaterial
from .qr import QrFlowResult, QrLoginState
from .qr_image import qr_svg_data_url

_UNIKEY_ENDPOINT = "https://music.163.com/api/login/qrcode/unikey"
_POLL_ENDPOINT = "https://music.163.com/api/login/qrcode/client/login"
_ACCOUNT_ENDPOINT = "https://music.163.com/api/w/nuser/account/get"
_LOGIN_URL_PREFIX = "https://music.163.com/login?codekey="
# type=3 is the modern Web QR challenge used by current Netease web clients.
_QR_TYPE = "3"
_HEADERS = {
    "Referer": "https://music.163.com/",
    # Browser-like UA: some confirm paths omit session cookies for unknown agents.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
_TTL = timedelta(minutes=5)
_DEBUG_LOG = Path("/app/tmp/qr-debug.log")
_LOG = logging.getLogger("musicdl_web.sessions.netease_qr")


@dataclass(slots=True)
class _NeteaseQrToken:
    client: PlatformCookieJarClient | None = field(repr=False)
    key: str | None = field(repr=False)
    image: bytes | None = field(repr=False)
    payload: str | None = field(repr=False)


class NeteaseQrLoginFlow:
    """Drive the official Web QR endpoints without sharing cookies between challenges."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("only httpx.MockTransport may be injected for tests")
        self._transport = transport
        self._timeout = timeout

    def begin(self, source: Source) -> tuple[object, timedelta]:
        if source is not Source.NETEASE:
            raise QrLoginUnavailable("QR login is unavailable for this platform")
        client = PlatformCookieJarClient(
            allowed_host="music.163.com",
            transport=self._transport,
            timeout=self._timeout,
            headers=_HEADERS,
        )
        try:
            response = client.post(_UNIKEY_ENDPOINT, data={"type": _QR_TYPE})
            root = _json_mapping(response)
            key = root.get("unikey")
            if root.get("code") != 200 or not isinstance(key, str) or not key:
                raise QrLoginError("Netease QR login is unavailable")
            payload = f"{_LOGIN_URL_PREFIX}{key}"
            image_data_url = qr_svg_data_url(payload)
            image = _decode_svg_data_url(image_data_url)
            token = _NeteaseQrToken(client, key, image, payload)
            return token, _TTL
        except QrLoginError:
            client.close()
            raise
        except Exception:
            client.close()
            raise QrLoginError("Netease QR login request failed") from None

    def image(self, source: Source, temporary_token: object) -> bytes:
        if source is not Source.NETEASE or not isinstance(temporary_token, _NeteaseQrToken):
            raise QrLoginError("Netease QR challenge is invalid")
        if temporary_token.client is None or temporary_token.image is None:
            raise QrLoginError("Netease QR challenge is inactive")
        return temporary_token.image

    def poll(self, source: Source, temporary_token: object) -> QrFlowResult:
        if source is not Source.NETEASE or not isinstance(temporary_token, _NeteaseQrToken):
            raise QrLoginError("Netease QR challenge is invalid")
        client = temporary_token.client
        key = temporary_token.key
        if client is None or key is None:
            raise QrLoginError("Netease QR challenge is inactive")
        try:
            response = client.post(
                _POLL_ENDPOINT, data={"key": key, "type": _QR_TYPE}
            )
            root = _json_mapping(response)
            code = root.get("code")
            if code == 801:
                return QrFlowResult(QrLoginState.WAITING)
            if code == 802:
                return QrFlowResult(QrLoginState.SCANNED)
            if code == 800:
                return QrFlowResult(QrLoginState.EXPIRED)
            if code != 803:
                _debug(
                    {
                        "event": "poll_unexpected_code",
                        "code": code,
                        "body_keys": sorted(root.keys()),
                        "body_types": {k: type(v).__name__ for k, v in root.items()},
                        "set_cookie_names": _set_cookie_names(response),
                        "jar_names": sorted(client.cookie_mapping().keys()),
                    }
                )
                raise QrLoginError("Netease QR login returned an invalid state")

            cookies = _merge_session_cookies(client, response, root)
            _debug(
                {
                    "event": "poll_803",
                    "body_keys": sorted(root.keys()),
                    "body_types": {k: type(v).__name__ for k, v in root.items()},
                    "set_cookie_names": _set_cookie_names(response),
                    "jar_names": sorted(cookies.keys()),
                    "has_auth_cookie": not {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies),
                }
            )
            if {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
                raise QrLoginError("Netease QR login did not establish a session cookie")

            # Seed the client jar with any header/body cookies httpx did not store, so
            # the account probe and later adapters share the same session material.
            _seed_client_cookies(client, cookies)

            account_response = client.get(_ACCOUNT_ENDPOINT)
            account = _json_mapping(account_response)
            _debug(
                {
                    "event": "account_after_803",
                    "code": account.get("code"),
                    "body_types": {k: type(v).__name__ for k, v in account.items()},
                    "id_types": _account_id_types(account),
                }
            )
            _verify_account(account)
            cookies = _merge_session_cookies(client, account_response, account)
            if not cookies or {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
                raise QrLoginError("Netease account verification failed")
            return QrFlowResult(
                QrLoginState.SUCCEEDED,
                SessionMaterial(source=Source.NETEASE, _cookies=cookies),
            )
        except QrLoginError as exc:
            _debug({"event": "poll_qr_login_error", "error": str(exc)})
            raise
        except Exception as exc:
            _debug({"event": "poll_other_error", "error_type": type(exc).__name__})
            raise QrLoginError("Netease QR login request failed") from None

    def discard(self, source: Source, temporary_token: object) -> None:
        del source
        if not isinstance(temporary_token, _NeteaseQrToken):
            return
        client = temporary_token.client
        temporary_token.client = None
        temporary_token.key = None
        temporary_token.image = None
        temporary_token.payload = None
        if client is not None:
            client.close()


def _decode_svg_data_url(value: str) -> bytes:
    prefix = "data:image/svg+xml;base64,"
    if not value.startswith(prefix):
        raise QrLoginError("Netease QR image is unavailable")
    try:
        image = b64decode(value.removeprefix(prefix), validate=True)
    except (Base64Error, ValueError):
        raise QrLoginError("Netease QR image is unavailable") from None
    if not image.startswith(b"<svg") or b"</svg>" not in image:
        raise QrLoginError("Netease QR image is unavailable")
    return image


def verified_identity(root: Mapping[str, Any]) -> str:
    """Validate an account response and return a bounded display hint."""

    _verify_account(root)
    profile = root["profile"]
    if not isinstance(profile, Mapping):
        raise QrLoginError("Netease account verification failed")
    nickname = profile.get("nickname")
    if isinstance(nickname, str) and nickname.strip():
        return nickname.strip()[:80]
    user_id = profile.get("userId")
    return f"网易云账号 {user_id}"


def _json_mapping(response: httpx.Response) -> Mapping[str, Any]:
    try:
        root = response.json()
    except ValueError:
        raise QrLoginError("Netease QR login returned an invalid response") from None
    if not isinstance(root, Mapping):
        raise QrLoginError("Netease QR login returned an invalid response")
    return root


def _positive_id(value: object) -> int | None:
    """Coerce Netease account identifiers that may arrive as int or numeric string."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            parsed = int(text)
            return parsed if parsed > 0 else None
    return None


def _verify_account(root: Mapping[str, Any]) -> None:
    account = root.get("account")
    profile = root.get("profile")
    if root.get("code") != 200 or not isinstance(account, Mapping) or not isinstance(
        profile, Mapping
    ):
        raise QrLoginError("Netease account verification failed")
    account_id = _positive_id(account.get("id"))
    user_id = _positive_id(profile.get("userId"))
    if account_id is None or user_id is None or account_id != user_id:
        raise QrLoginError("Netease account verification failed")


def _set_cookie_names(response: httpx.Response) -> list[str]:
    names: list[str] = []
    for header in response.headers.get_list("set-cookie"):
        name = header.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def _parse_set_cookie_headers(response: httpx.Response) -> dict[str, str]:
    """Parse Set-Cookie name/value pairs without trusting jar domain policy."""

    cookies: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        name_value = header.split(";", 1)[0]
        name, sep, value = name_value.partition("=")
        if not sep or not name.strip() or not value:
            continue
        cookies[name.strip()] = value
    return cookies


def _parse_body_cookies(root: Mapping[str, Any]) -> dict[str, str]:
    """Absorb cookie material Netease sometimes places in the JSON body."""

    cookies: dict[str, str] = {}
    raw = root.get("cookie")
    if isinstance(raw, str) and raw.strip():
        for part in raw.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name and value:
                cookies[name] = value
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                continue
            name_value = item.split(";", 1)[0]
            name, sep, value = name_value.partition("=")
            if sep and name.strip() and value:
                cookies[name.strip()] = value
    return cookies


def _merge_session_cookies(
    client: PlatformCookieJarClient,
    response: httpx.Response,
    root: Mapping[str, Any],
) -> dict[str, str]:
    cookies = dict(client.cookie_mapping())
    cookies.update(_parse_set_cookie_headers(response))
    cookies.update(_parse_body_cookies(root))
    # Drop empties again after merges.
    return {name: value for name, value in cookies.items() if value}


def _seed_client_cookies(
    client: PlatformCookieJarClient, cookies: Mapping[str, str]
) -> None:
    """Ensure subsequent same-host requests send auth cookies we recovered manually."""

    jar = client._client.cookies  # noqa: SLF001 — intentional recovery seam
    for name, value in cookies.items():
        if name in {"MUSIC_U", "MUSIC_A", "__csrf", "NMTID"}:
            jar.set(name, value, domain="music.163.com", path="/")


def _account_id_types(root: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    account = root.get("account")
    profile = root.get("profile")
    if isinstance(account, Mapping):
        result["account.id"] = type(account.get("id")).__name__
    else:
        result["account"] = type(account).__name__
    if isinstance(profile, Mapping):
        result["profile.userId"] = type(profile.get("userId")).__name__
    else:
        result["profile"] = type(profile).__name__
    return result


def _debug(payload: Mapping[str, Any]) -> None:
    """Append redacted QR diagnostics for NAS operators (no cookie/token values)."""

    record = {
        "ts": datetime.now(UTC).isoformat(),
        **dict(payload),
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        _LOG.warning("qr debug log unavailable")
    _LOG.warning("qr %s", line)
