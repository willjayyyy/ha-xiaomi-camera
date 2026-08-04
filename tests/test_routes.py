"""Every address the page requests must exist on the listener serving it.

This shipped broken: the page was changed to request `/api/preview/<did>` while
the route it replaced was still the one registered, and the preview answered
404 for two releases. Nothing caught it -- the page is not exercised by any
test, `bridge.api` cannot be imported without the vendor SDK, and both halves
looked right in isolation.

Both sides are read as text for that reason. It is a coarse check, and it
catches exactly the mistake that occurred: the two halves of a request
disagreeing about its address.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app"
_API = (_APP / "bridge" / "api.py").read_text(encoding="utf-8")
_PAGE = (_APP / "web" / "index.html").read_text(encoding="utf-8")

#: `web.get("/api/health", ...)` and friends, per listener.
_ROUTE = re.compile(r'web\.(get|post)\(\s*"(?P<path>[^"]+)"')

#: Every address the page names, whether through `api("/api/…")` or as an
#: element's source. Both forms have shipped pointing at a route that did not
#: exist, so both are checked.
_REQUEST = re.compile(r'[`"]\.?(?P<path>/api/[^`"?\s]*)')


def _routes(app: str) -> set[str]:
    """Paths registered by one `build_*_app`, with placeholders normalised."""
    body = _API.split(f"def build_{app}_app")[1].split("\n    def ")[0]
    return {
        re.sub(r"\{[^}]+\}", "*", match.group("path"))
        for match in _ROUTE.finditer(body)
    }


def _requested() -> set[str]:
    """Paths the page asks for, with interpolations normalised."""
    return {
        re.sub(r"\$\{[^}]+\}", "*", match.group("path")).rstrip("/")
        for match in _REQUEST.finditer(_PAGE)
    }


@pytest.mark.parametrize("path", sorted(_requested()))
def test_the_page_only_requests_routes_that_exist(path: str) -> None:
    assert path in _routes("ingress"), (
        f"the page requests {path}, which the ingress listener does not serve"
    )


def test_the_page_requests_something() -> None:
    """Guard the guard.

    A pattern that quietly matches nothing would make every assertion above
    vacuous, which is a worse failure than the one this file exists to catch.
    """
    assert len(_requested()) >= 5


def test_the_control_plane_is_not_reachable_through_the_page() -> None:
    """The two listeners exist to have different exposure.

    The control plane has no authentication of its own; it is bound to loopback
    and that is the whole of its protection. Serving one of its routes from the
    page's listener would hand a stream and camera power control to anything
    that gets past the page's guard.
    """
    control_only = _routes("control") - _routes("ingress")
    assert "/api/stream/*" in control_only
    assert "/api/cameras/*/power" in control_only
