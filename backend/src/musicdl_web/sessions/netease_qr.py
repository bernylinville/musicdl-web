"""Official Netease QR-login flow with one isolated cookie jar per challenge."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
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
_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": "musicdl-web/0.1",
}
_TTL = timedelta(minutes=5)


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
            response = client.post(_UNIKEY_ENDPOINT, data={"type": "1"})
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
            response = client.post(_POLL_ENDPOINT, data={"key": key, "type": "1"})
            root = _json_mapping(response)
            code = root.get("code")
            if code == 801:
                return QrFlowResult(QrLoginState.WAITING)
            if code == 802:
                return QrFlowResult(QrLoginState.SCANNED)
            if code == 800:
                return QrFlowResult(QrLoginState.EXPIRED)
            if code != 803:
                raise QrLoginError("Netease QR login returned an invalid state")

            account_response = client.get(_ACCOUNT_ENDPOINT)
            account = _json_mapping(account_response)
            _verify_account(account)
            cookies = client.cookie_mapping()
            if not cookies or {"MUSIC_A", "MUSIC_U"}.isdisjoint(cookies):
                raise QrLoginError("Netease account verification failed")
            return QrFlowResult(
                QrLoginState.SUCCEEDED,
                SessionMaterial(source=Source.NETEASE, _cookies=cookies),
            )
        except QrLoginError:
            raise
        except Exception:
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


def _verify_account(root: Mapping[str, Any]) -> None:
    account = root.get("account")
    profile = root.get("profile")
    if root.get("code") != 200 or not isinstance(account, Mapping) or not isinstance(
        profile, Mapping
    ):
        raise QrLoginError("Netease account verification failed")
    account_id = account.get("id")
    user_id = profile.get("userId")
    if (
        not isinstance(account_id, int)
        or isinstance(account_id, bool)
        or account_id <= 0
        or not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
        or account_id != user_id
    ):
        raise QrLoginError("Netease account verification failed")
