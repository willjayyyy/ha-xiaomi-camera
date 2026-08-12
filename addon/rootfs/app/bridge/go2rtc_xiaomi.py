"""go2rtc's Xiaomi endpoint, and nothing else of go2rtc's API.

`POST /api/xiaomi` signs in; `GET /api/xiaomi` lists signed-in accounts and
their devices. Both are reached over loopback. go2rtc's wider API exposes
`exec` and stream management, so nothing here is generic -- there is no
"call go2rtc" helper to grow into one.

Also owns `compat_ready` -- the cached answer to "does go2rtc hold at least
one Xiaomi credential" that every other module reads instead of asking
go2rtc itself. See `refresh_compat_ready` for why it is cached rather than
read live.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlparse

import aiohttp

from .redact import safe_error

_LOGGER = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:1984/api/xiaomi"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: go2rtc keeps the half-finished sign-in in a package-level variable, so two
#: flows at once overwrite each other and both fail in ways neither person
#: can interpret. One at a time, refused rather than queued.
#:
#: This is a plain flag, not an `asyncio.Lock`, on purpose: a lock's name
#: says "queue the second caller", which is the opposite of what happens
#: here. The check-then-set below is atomic only because nothing between
#: them awaits -- true of a flag exactly as it was true of a lock's
#: uncontended fast path, but a flag does not depend on a reader having
#: traced that path to believe it.
_signing_in = False


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
    go2rtc owns that protocol and this only carries it. The caller is
    rendering this to someone part-way through logging in, so this always
    returns a `SignInResult`: an unreadable 401 body is reported the same as
    one naming no next step, rather than raised.
    """
    global _signing_in
    if _signing_in:
        raise SignInBusy
    _signing_in = True
    try:
        form = {name: fields[name] for name in _FIELDS[step] if name in fields}
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.post(_BASE, data=form) as response,
        ):
            if response.status == 200:
                return SignInResult(ok=True)
            if response.status != 401:
                raise RuntimeError(safe_error(RuntimeError(await response.text())))
            return await _parse_sign_in_failure(response)
    except aiohttp.ClientError as err:
        raise RuntimeError(safe_error(err)) from err
    finally:
        _signing_in = False


async def _parse_sign_in_failure(response: aiohttp.ClientResponse) -> SignInResult:
    """The next-step fields from a 401 body, or a bare failure if it cannot
    be read. go2rtc's own bug, a proxy in between, or a future format change
    could all hand back something that is not the JSON object documented --
    none of that should turn into an exception this deep into a login."""
    try:
        body: Any = await response.json(content_type=None)
        if not isinstance(body, dict):
            raise TypeError(f"expected an object, got {type(body).__name__}")
        captcha = body.get("Captcha")
        return SignInResult(
            ok=False,
            captcha=base64.b64decode(captcha) if captcha else None,
            verify_phone=body.get("VerifyPhone"),
            verify_email=body.get("VerifyEmail"),
        )
    except (ValueError, TypeError) as err:
        # Covers a non-JSON body (`json.JSONDecodeError`, a `ValueError`
        # subclass), a malformed `Captcha` field (`binascii.Error`, also a
        # `ValueError` subclass), and a body that parsed but was not the
        # object shape expected (`TypeError`, raised explicitly above).
        _LOGGER.warning("go2rtc sign-in: unreadable 401 body: %s", safe_error(err))
        return SignInResult(ok=False)


async def signed_in_users() -> list[str]:
    """The Xiaomi accounts go2rtc currently holds a credential for."""
    try:
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.get(_BASE) as response,
        ):
            response.raise_for_status()
            return list(await response.json())
    except (aiohttp.ClientError, ValueError, TypeError) as err:
        raise RuntimeError(safe_error(err)) from err


async def _device_urls(user: str, region: str) -> dict[str, str]:
    """Every device one signed-in account can reach, keyed by did.

    The URL is taken whole. go2rtc fills the camera's LAN address in from the
    cloud's `localip`, and composing our own here would put that address --
    and the account id, and the region -- in a second place.
    """
    try:
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.get(_BASE, params={"id": user, "region": region}) as response,
        ):
            response.raise_for_status()
            body = await response.json()
    except (aiohttp.ClientError, ValueError, TypeError) as err:
        raise RuntimeError(safe_error(err)) from err
    if isinstance(body, dict):
        sources = body.get("sources", [])
    elif isinstance(body, list):
        sources = body
    else:
        sources = []
    urls: dict[str, str] = {}
    for source in sources:
        url = source.get("url") if isinstance(source, dict) else None
        if not url:
            continue
        did = parse_qs(urlparse(url).query).get("did", [None])[0]
        if did:
            urls[did] = url
    return urls


#: Whether go2rtc holds at least one Xiaomi credential, as of the last
#: `refresh_compat_ready()` call. Cached rather than asked fresh on every
#: read: `path_for` consults it during every camera-list refresh and every
#: `/api/cameras`, both of which run far more often than the credential set
#: changes, and a network round trip inside a pure resolution function would
#: be wrong even though it is only loopback.
_ready = False


def compat_ready() -> bool:
    """Whether compatibility mode has a credential to use, as of the last
    refresh. Never makes a network call -- see `_ready` above."""
    return _ready


async def refresh_compat_ready() -> None:
    """Update the cached `compat_ready` flag from go2rtc's own account list.

    Call this at start-up, right after a sign-in succeeds, and on every
    camera-list refresh -- the moments the four readers of `compat_ready`
    (`BridgeApi._compat_ready`, `CameraRegistry._compat_ready`, and both call
    sites in `bridge.__main__`) need a current answer for. A failure to
    reach go2rtc -- it not being up yet at start-up, a transient error --
    degrades to "not ready" rather than raising, the same choice every other
    function in this module makes rather than taking its caller down with it.
    """
    global _ready
    try:
        _ready = bool(await signed_in_users())
    except RuntimeError as err:
        _LOGGER.warning("could not refresh compat_ready: %s", safe_error(err))
        _ready = False


async def all_device_urls(region: str) -> dict[str, str]:
    """Every device reachable through any signed-in account, keyed by did.

    go2rtc supports several Xiaomi accounts; this add-on has exactly one
    OAuth identity, and the two need not match. Rather than ask which
    account to use, every signed-in account is queried and the results are
    merged by did -- a camera this add-on knows from OAuth but that no
    signed-in account can reach is simply absent from the result, which the
    caller reads as "compatibility mode not signed in". If two accounts
    somehow report the same did, the one queried last wins, in the order
    `signed_in_users()` returned.

    One account failing -- an expired session, a transient error, a body
    that does not parse -- does not take the others down with it: a camera
    reachable through a healthy account must not be reported unreachable
    because some other account had a bad moment. The failure is logged and
    that account's devices are simply missing from the result, same as if it
    were not signed in at all.
    """
    users = await signed_in_users()
    urls: dict[str, str] = {}
    for user in users:
        try:
            urls.update(await _device_urls(user, region))
        except RuntimeError as err:
            _LOGGER.warning(
                "go2rtc: could not list devices for account %s: %s", user, err
            )
    return urls
