"""Netease eapi request helpers for QR login (client-compatible wire format).

Protocol primitives only: AES-ECB params encryption and device-header cookies.
Hosts are limited to official Netease control endpoints.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from ..errors import NetworkPolicyError, NetworkRequestError

_EAPI_KEY = b"e82ckenh8dichen8"
_API_HOST = "interface.music.163.com"
_WEB_HOST = "music.163.com"
_ALLOWED = frozenset({_API_HOST, _WEB_HOST})
_UA = (
    "NeteaseMusic/9.1.65.240927161425(9001065);"
    "Dalvik/2.1.0 (Linux; U; Android 14; 23013RK75C Build/UKQ1.230804.001)"
)


def eapi_params(api_path: str, payload: Mapping[str, Any]) -> str:
    """Encrypt *payload* for POST to ``/eapi/...`` (uppercase hex ciphertext)."""

    if not api_path.startswith("/api/"):
        raise ValueError("eapi path must start with /api/")
    text = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.md5(
        f"nobody{api_path}use{text}md5forencrypt".encode("utf-8")
    ).hexdigest()
    plain = f"{api_path}-36cd479b6b5-{text}-36cd479b6b5-{digest}".encode("utf-8")
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_EAPI_KEY), modes.ECB()).encryptor()
    return (encryptor.update(padded) + encryptor.finalize()).hex().upper()


def cookie_header(values: Mapping[str, str]) -> str:
    parts: list[str] = []
    for name, value in values.items():
        if not name or not value:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def parse_set_cookie_headers(response: httpx.Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        # Drop Domain= so values can be reused across music.163.com hosts.
        cleaned = header
        for part in header.split(";"):
            if part.strip().lower().startswith("domain="):
                cleaned = header.replace(part, "").replace(";;", ";")
                break
        name_value = cleaned.split(";", 1)[0]
        name, sep, value = name_value.partition("=")
        if sep and name.strip() and value:
            cookies[name.strip()] = value
    return cookies


class NeteaseEapiClient:
    """Per-QR-challenge client using eapi against interface.music.163.com."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("only httpx.MockTransport may be injected for tests")
        self._device_id = secrets.token_hex(16)
        self._cookies: dict[str, str] = {
            "os": "pc",
            "appver": "3.1.17.204416",
            "osver": "Microsoft-Windows-10-Professional-build-19045-64bit",
            "channel": "netease",
            "deviceId": self._device_id,
        }
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
        )

    def close(self) -> None:
        self._client.close()

    def cookie_mapping(self) -> dict[str, str]:
        return {k: v for k, v in self._cookies.items() if v}

    def merge_cookies(self, values: Mapping[str, str]) -> None:
        for name, value in values.items():
            if name and value:
                self._cookies[name] = value

    def post_eapi(self, api_path: str, data: Mapping[str, Any]) -> httpx.Response:
        """POST encrypted eapi body to interface.music.163.com."""

        header = self._request_header()
        payload = {**dict(data), "header": header}
        body = {"params": eapi_params(api_path, payload)}
        eapi_path = "/eapi/" + api_path.removeprefix("/api/")
        url = f"https://{_API_HOST}{eapi_path}"
        return self._send(
            "POST",
            url,
            data=body,
            headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie_header(header),
            },
        )

    def get_web(self, url: str) -> httpx.Response:
        """GET an official music.163.com HTTPS endpoint with current cookies."""

        return self._send(
            "GET",
            url,
            data=None,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://music.163.com/",
                "Cookie": cookie_header(self._cookies),
            },
        )

    def _request_header(self) -> dict[str, str]:
        header: dict[str, str] = {
            "osver": self._cookies.get(
                "osver", "Microsoft-Windows-10-Professional-build-19045-64bit"
            ),
            "deviceId": self._cookies.get("deviceId", self._device_id),
            "os": self._cookies.get("os", "pc"),
            "appver": self._cookies.get("appver", "3.1.17.204416"),
            "versioncode": self._cookies.get("versioncode", "140"),
            "mobilename": self._cookies.get("mobilename", ""),
            "buildver": self._cookies.get("buildver", str(int(time.time()))[:10]),
            "resolution": self._cookies.get("resolution", "1920x1080"),
            "__csrf": self._cookies.get("__csrf", ""),
            "channel": self._cookies.get("channel", "netease"),
            "requestId": f"{int(time.time() * 1000)}_{secrets.randbelow(1000):04d}",
        }
        if self._cookies.get("MUSIC_U"):
            header["MUSIC_U"] = self._cookies["MUSIC_U"]
        if self._cookies.get("MUSIC_A"):
            header["MUSIC_A"] = self._cookies["MUSIC_A"]
        return header

    def _send(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        self._validate_url(url)
        try:
            response = self._client.request(
                method, url, data=data, headers=headers, follow_redirects=False
            )
        except httpx.HTTPError:
            raise NetworkRequestError("platform request failed") from None
        if response.is_redirect:
            raise NetworkRequestError("platform redirects are forbidden")
        # Netease login codes 800-803 are business codes in a 200 body; still
        # reject hard HTTP errors.
        if response.status_code >= 400:
            raise NetworkRequestError(
                f"platform request returned HTTP {response.status_code}"
            )
        self.merge_cookies(parse_set_cookie_headers(response))
        return response

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "https"
            or host not in _ALLOWED
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise NetworkPolicyError("platform request destination is not allowed")
