"""Who may reach the account page.

The page can view live video, switch cameras off and unlink the Xiaomi
account, so what guards it is the security boundary of the whole add-on.

The project's rule everywhere else is that the bind address does the work:
``host_network`` removes Docker's port isolation, so a listener on loopback is
genuinely unreachable and needs nothing further. This page is the one surface
that cannot always follow that rule, because Supervisor's ingress proxy runs in
its own container and reaches a host-network add-on over the Docker bridge, not
over loopback. So where it cannot be unreachable, it is guarded. Every check
that applies must pass:

* **Supervisor's ingress**, whenever this runs as an add-on. The request has to
  carry the headers Supervisor injects *and* come from Supervisor's own
  network.
* **The configured password**, whenever one is set. Set means checked -- in
  every deployment, including through the panel. A password that is configured
  and then not asked for is worse than no password at all, because the user
  believes they have one.

The password is entered on a page of this add-on's own, not through the
browser's built-in prompt. That prompt cannot be styled, appears before the
page it guards, and insists on a username this add-on does not have. It is
exchanged for a cookie, so it is typed once rather than attached to every
picture the preview fetches.

Neither is needed while the page is bound to loopback with no password set:
nothing outside the host can open a connection to it. There is no case where it
is reachable and unguarded -- :func:`build_guards` raises rather than returning
nothing, and ``Options.validate`` refuses a configuration that would get
there.

This lives apart from :mod:`bridge.api` so it can be tested: importing that
module pulls in the closed-source vendor SDK, which is not present in CI, and
an untested authentication check is not worth much.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets

from aiohttp import web

#: Injected by Supervisor's ingress proxy on every forwarded request.
INGRESS_HEADER = "X-Ingress-Path"

#: The network Supervisor's own containers sit on, fixed by Supervisor itself
#: (`DOCKER_IPV4_NETWORK_MASK` in its source). The ingress header is not a
#: secret and nothing signs it, so on its own it would let any machine on the
#: local network read the page by setting one header. Where the connection came
#: from is the part an attacker on another host cannot forge.
SUPERVISOR_NETWORK = ipaddress.ip_network("172.30.32.0/23")

#: Holds proof that the password was entered. `SameSite=Strict` is what makes
#: this safe from a page on another site: the browser will not attach it to a
#: request that site starts, so there is nothing for a forged form to ride on.
SESSION_COOKIE = "xiaomi_camera_session"

#: Reachable without the password, because they are how it gets entered: the
#: page itself, what it is built from, and the endpoint that checks it. None of
#: them reveal anything about the cameras.
_PUBLIC_PATHS = frozenset({"/", "/api/login"})
_PUBLIC_PREFIXES = ("/static/",)

_UNGUARDED = (
    "The web page would be reachable from the network with nothing guarding "
    "it. Set web_password, or switch access_mode back to 'local' to keep "
    "everything on this machine."
)


def build_guards(*, supervised: bool, published: bool, web_password: str) -> list:
    """The middlewares the account page runs behind.

    Each applicable check must pass, not merely one of them. Accepting a
    request that satisfied any single check meant a password configured on an
    add-on was never asked for: ingress vouched for every panel request on its
    own, and the setting silently did nothing.

    Empty is a valid answer, and only when the listener itself is the
    protection: the page bound to loopback with no password set, where nothing
    outside the host can open a connection to it. Reachable-and-unguarded is
    not a case this can return.
    """
    checks = []
    if supervised:
        checks.append(_from_ingress)
    if web_password:
        checks.append(_with_password(web_password))
    if checks:
        return [_guard(check) for check in checks]
    if published:
        raise ValueError(_UNGUARDED)
    return []


def _guard(check):
    """Turn a check into middleware that refuses whatever it rejects."""

    @web.middleware
    async def middleware(request: web.Request, handler):
        check(request)
        return await handler(request)

    return middleware


def _from_ingress(request: web.Request) -> None:
    """Accept what Supervisor's ingress proxy forwarded, and nothing else.

    Both halves matter. The header says the request went through ingress, which
    means Home Assistant authenticated whoever sent it; the peer address says
    the claim came from Supervisor rather than from someone on the local
    network who read the header name in this file.
    """
    if INGRESS_HEADER not in request.headers or not _from_supervisor(request):
        raise web.HTTPForbidden(
            text="This page is only reachable through Home Assistant."
        )


def _from_supervisor(request: web.Request) -> bool:
    """Whether the connection was opened from Supervisor's own network.

    The peer address, never a forwarded-for header: the point of the check is
    to establish something the sender cannot choose.
    """
    peer = request.remote
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return address in SUPERVISOR_NETWORK or address.is_loopback


def session_token(password: str) -> str:
    """The cookie value that stands for having entered ``password``.

    Derived from the password rather than random, so it survives a restart --
    a viewer should not be signed out because the add-on was updated -- and so
    nothing has to be stored server-side. It is a hash: the cookie never
    carries the password itself, which matters because cookies are written to
    disk and turn up in browser tooling.
    """
    return hashlib.sha256(f"xiaomi-camera:{password}".encode()).hexdigest()


def _with_password(password: str):
    """Accept a request that has been signed in.

    Signing in happens on this add-on's own page and is exchanged for a
    cookie. The browser's built-in prompt was the obvious alternative and is
    worse in every way that matters here: it cannot be styled, it interrupts
    before the page it guards has drawn anything, and it demands a username
    that does not exist.

    Over plain HTTP the password is visible to anything that can watch the
    traffic, the same exposure the RTSP credentials already carry. Put a
    reverse proxy with TLS in front where that matters.
    """
    expected = session_token(password)

    def check(request: web.Request) -> None:
        # `request.path` as received, never adjusted by anything the sender
        # controls. Supervisor strips its ingress prefix before forwarding --
        # the routes registered here are the plain ones, and they match --
        # so there is nothing to strip, and trying to do so was a way in: the
        # prefix was taken from a header, and a long enough forged one left an
        # empty path that read as the public root.
        path = request.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return
        supplied = request.cookies.get(SESSION_COOKIE, "")
        # Constant time: a plain comparison leaks the value one character at a
        # time to anyone who can measure the response.
        if not secrets.compare_digest(supplied, expected):
            raise web.HTTPUnauthorized(text="Sign in on the add-on page.")

    return check
