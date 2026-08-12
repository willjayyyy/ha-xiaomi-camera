"""go2rtc's Xiaomi endpoint, and nothing else of go2rtc's API.

`POST /api/xiaomi` signs in; `GET /api/xiaomi` lists signed-in accounts and
their devices. Both are reached over loopback. go2rtc's wider API exposes
`exec` and stream management, so nothing here is generic -- there is no
"call go2rtc" helper to grow into one.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlparse

import aiohttp

from .redact import safe_error

_BASE = "http://127.0.0.1:1984/api/xiaomi"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: go2rtc keeps the half-finished sign-in in a package-level variable, so two
#: flows at once overwrite each other and both fail in ways neither person
#: can interpret. One at a time, refused rather than queued: a queued second
#: attempt would sit behind a captcha nobody is looking at.
_sign_in_lock = asyncio.Lock()


class SignInBusy(RuntimeError):
    """Another sign-in is already in progress."""


class SignInResult(NamedTuple):
    ok: bool
    captcha: bytes | None = None
    verify_phone: str | None = None
    verify_email: str | None = None


_FIELDS = {
    "password": ("username", "password"),
    "captcha": ("captcha",),
    "verify": ("verify",),
}


async def sign_in(step: str, **fields: str) -> SignInResult:
    """One step of go2rtc's three-step Xiaomi sign-in.

    Which step comes next is decided by the 401 body, never guessed here --
    go2rtc owns that protocol and this only carries it.
    """
    if _sign_in_lock.locked():
        raise SignInBusy
    async with _sign_in_lock:
        form = {name: fields[name] for name in _FIELDS[step] if name in fields}
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.post(_BASE, data=form) as response,
        ):
            if response.status == 200:
                return SignInResult(ok=True)
            if response.status != 401:
                raise RuntimeError(safe_error(RuntimeError(await response.text())))
            body: dict[str, Any] = await response.json()
            captcha = body.get("Captcha")
            return SignInResult(
                ok=False,
                captcha=base64.b64decode(captcha) if captcha else None,
                verify_phone=body.get("VerifyPhone"),
                verify_email=body.get("VerifyEmail"),
            )


async def signed_in_users() -> list[str]:
    """The Xiaomi accounts go2rtc currently holds a credential for."""
    async with (
        aiohttp.ClientSession(timeout=_TIMEOUT) as session,
        session.get(_BASE) as response,
    ):
        response.raise_for_status()
        return list(await response.json())


async def _device_urls(user: str, region: str) -> dict[str, str]:
    """Every device one signed-in account can reach, keyed by did.

    The URL is taken whole. go2rtc fills the camera's LAN address in from the
    cloud's `localip`, and composing our own here would put that address --
    and the account id, and the region -- in a second place.
    """
    async with (
        aiohttp.ClientSession(timeout=_TIMEOUT) as session,
        session.get(_BASE, params={"id": user, "region": region}) as response,
    ):
        response.raise_for_status()
        body = await response.json()
    urls: dict[str, str] = {}
    for source in body.get("sources", body if isinstance(body, list) else []):
        url = source.get("url") if isinstance(source, dict) else None
        if not url:
            continue
        did = parse_qs(urlparse(url).query).get("did", [None])[0]
        if did:
            urls[did] = url
    return urls


async def all_device_urls(region: str) -> dict[str, str]:
    """Every device reachable through any signed-in account, keyed by did.

    go2rtc supports several Xiaomi accounts; this add-on has exactly one
    OAuth identity, and the two need not match. Rather than ask which
    account to use, every signed-in account is queried and the results are
    merged by did -- a camera this add-on knows from OAuth but that no
    signed-in account can reach is simply absent from the result, which the
    caller reads as "compatibility mode not signed in".
    """
    users = await signed_in_users()
    urls: dict[str, str] = {}
    for user in users:
        urls.update(await _device_urls(user, region))
    return urls
