"""Netease QR-login flow via eapi (SPlayer / NeteaseCloudMusicApi-compatible).

Plain HTTPS form posts to music.163.com often return business code 8821 without
MUSIC_U after phone confirm. Desktop clients (SPlayer-Next) default empty crypto
to eapi against interface.music.163.com — that path is what we drive here.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..models import Source
from .errors import QrLoginError, QrLoginUnavailable
from .models import SessionMaterial
from .netease_eapi import NeteaseEapiClient
from .qr import QrFlowResult, QrLoginState
from .qr_image import qr_svg_data_url

_UNIKEY_API = "/api/login/qrcode/unikey"
_POLL_API = "/api/login/qrcode/client/login"
_ACCOUNT_URL = "https://music.163.com/api/w/nuser/account/get"
_LOGIN_URL_PREFIX = "https://music.163.com/login?codekey="
_QR_TYPE = 3
_CONFIRM_REDIRECT_CODE = 8821
_TTL = timedelta(minutes=5)
_DEBUG_LOG = Path("/app/tmp/qr-debug.log")
_LOG = logging.getLogger("musicdl_web.sessions.netease_qr")
_SECURITY_GATE_MESSAGE = (
    "网易云确认后要求额外安全验证，扫码无法在本服务内完成；"
    "请改用「导入登录 Cookie」"
)


@dataclass(slots=True)
class _NeteaseQrToken:
    client: NeteaseEapiClient | None = field(repr=False)
    key: str | None = field(repr=False)
    image: bytes | None = field(repr=False)
    payload: str | None = field(repr=False)


class NeteaseQrLoginFlow:
    """Drive Netease QR login with one isolated eapi client per challenge."""

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
        client = NeteaseEapiClient(transport=self._transport, timeout=self._timeout)
        try:
            response = client.post_eapi(_UNIKEY_API, {"type": _QR_TYPE})
            root = _json_mapping(response)
            key = root.get("unikey")
            if root.get("code") != 200 or not isinstance(key, str) or not key:
                _debug(
                    {
                        "event": "unikey_failed",
                        "code": root.get("code"),
                        "body_keys": sorted(root.keys()),
                    }
                )
                raise QrLoginError("Netease QR login is unavailable")
            payload = f"{_LOGIN_URL_PREFIX}{key}&chainId={_short_chain_id()}"
            image_data_url = qr_svg_data_url(payload)
            image = _decode_svg_data_url(image_data_url)
            return _NeteaseQrToken(client, key, image, payload), _TTL
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
            response = client.post_eapi(_POLL_API, {"key": key, "type": _QR_TYPE})
            root = _json_mapping(response)
            code = root.get("code")
            if code == 801:
                return QrFlowResult(QrLoginState.WAITING)
            if code == 802:
                return QrFlowResult(QrLoginState.SCANNED)
            if code == 800:
                return QrFlowResult(QrLoginState.EXPIRED)
            if code == _CONFIRM_REDIRECT_CODE:
                return self._handle_confirm_redirect(client, response, root)
            if code == 803:
                return self._complete_authenticated_session(client, response, root)

            _debug(
                {
                    "event": "poll_unexpected_code",
                    "code": code,
                    "message": _safe_message(root.get("message")),
                    "redirect": _redact_url(root.get("redirectUrl")),
                    "body_keys": sorted(root.keys()),
                    "set_cookie_names": _set_cookie_names(response),
                    "jar_names": sorted(client.cookie_mapping().keys()),
                    "transport": "eapi",
                }
            )
            raise QrLoginError("Netease QR login returned an invalid state")
        except QrLoginError as exc:
            _debug({"event": "poll_qr_login_error", "error": str(exc)})
            raise
        except Exception:
            _debug({"event": "poll_other_error", "error_type": "Exception"})
            raise QrLoginError("Netease QR login request failed") from None

    def _handle_confirm_redirect(
        self,
        client: NeteaseEapiClient,
        response: httpx.Response,
        root: Mapping[str, Any],
    ) -> QrFlowResult:
        _debug(
            {
                "event": "poll_8821",
                "message": _safe_message(root.get("message")),
                "redirect": _redact_url(root.get("redirectUrl")),
                "set_cookie_names": _set_cookie_names(response),
                "jar_names": sorted(client.cookie_mapping().keys()),
                "transport": "eapi",
            }
        )
        cookies = _merge_session_cookies(client, response, root)
        if not {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
            return self._complete_authenticated_session(client, response, root)

        redirect = root.get("redirectUrl")
        if isinstance(redirect, str) and redirect.startswith("https://music.163.com/"):
            try:
                finalize = client.get_web(redirect)
            except Exception:
                raise QrLoginError(_SECURITY_GATE_MESSAGE) from None
            try:
                finalize_root = _json_mapping(finalize)
            except QrLoginError:
                finalize_root = {}
            cookies = _merge_session_cookies(client, finalize, finalize_root)
            _debug(
                {
                    "event": "poll_8821_finalize",
                    "set_cookie_names": _set_cookie_names(finalize),
                    "jar_names": sorted(cookies.keys()),
                    "has_auth_cookie": not {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies),
                }
            )
            if not {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
                return self._complete_authenticated_session(
                    client, finalize, finalize_root
                )

        raise QrLoginError(_SECURITY_GATE_MESSAGE)

    def _complete_authenticated_session(
        self,
        client: NeteaseEapiClient,
        response: httpx.Response,
        root: Mapping[str, Any],
    ) -> QrFlowResult:
        cookies = _merge_session_cookies(client, response, root)
        _debug(
            {
                "event": "poll_session_complete",
                "body_keys": sorted(root.keys()),
                "set_cookie_names": _set_cookie_names(response),
                "jar_names": sorted(cookies.keys()),
                "has_auth_cookie": not {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies),
                "transport": "eapi",
            }
        )
        if {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
            raise QrLoginError("Netease QR login did not establish a session cookie")

        client.merge_cookies(cookies)
        account_response = client.get_web(_ACCOUNT_URL)
        account = _json_mapping(account_response)
        _debug(
            {
                "event": "account_after_session",
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


def _parse_body_cookies(root: Mapping[str, Any]) -> dict[str, str]:
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
    client: NeteaseEapiClient,
    response: httpx.Response,
    root: Mapping[str, Any],
) -> dict[str, str]:
    cookies = dict(client.cookie_mapping())
    from .netease_eapi import parse_set_cookie_headers

    cookies.update(parse_set_cookie_headers(response))
    cookies.update(_parse_body_cookies(root))
    return {name: value for name, value in cookies.items() if value}


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
    record = {"ts": datetime.now(UTC).isoformat(), **dict(payload)}
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    try:
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        _LOG.warning("qr debug log unavailable")
    _LOG.warning("qr %s", line)


def _short_chain_id() -> str:
    return f"v1_w{secrets.token_hex(2)}_{int(time.time()) % 100_000_000}"


def _safe_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:120] if text else None


def _redact_url(value: object) -> dict[str, object] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    return {
        "scheme": parsed.scheme,
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path[:80],
        "has_query": bool(parsed.query),
        "query_keys": sorted(
            {
                part.split("=", 1)[0]
                for part in parsed.query.split("&")
                if part and "=" in part
            }
        )[:20],
    }
