"""The client for go2rtc's one Xiaomi endpoint.

Only `POST /api/xiaomi` and `GET /api/xiaomi` are ever called. go2rtc's wider
API exposes `exec` and stream management, and proxying any of it would put
those behind this add-on's own password.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bridge import go2rtc_xiaomi as _module
from bridge.go2rtc_xiaomi import (
    SignInBusy,
    all_device_urls,
    compat_ready,
    refresh_compat_ready,
    sign_in,
    signed_in_users,
)


@pytest.fixture(autouse=True)
def _allow_loopback_servers(socket_enabled):
    """Opt back into real sockets under the Home Assistant plugin.

    This file drives a real HTTP handler over `TestClient`/`TestServer` --
    the only way to prove the request this module sends is the one go2rtc
    actually reads (a form, not JSON) -- while `pytest-socket`, brought in by
    the Home Assistant plugin, blocks socket use everywhere else. `conftest`
    supplies a no-op `socket_enabled` where the plugin is absent.
    """
    yield


class _Recorded:
    def __init__(self, content_type: str, form: dict[str, str]) -> None:
        self.content_type = content_type
        self.form = form


class _FakeGo2rtc:
    """A stand-in for go2rtc's `/api/xiaomi` endpoint.

    `hang()`/`release()` let a test hold a POST open, so the single-flight
    guard is proven against a login that has not finished -- not just one
    that resolves instantly.
    """

    def __init__(self) -> None:
        self.last_request: _Recorded | None = None
        self._status = 200
        self._body: object = []
        self._device_bodies: dict[str, object] = {}
        self._device_statuses: dict[str, int] = {}
        self._device_raw_text: dict[str, str] = {}
        self._raw_text: str | None = None
        self._gate = asyncio.Event()
        self._gate.set()

    def reply(self, status: int, body: object) -> None:
        """Sets the POST reply, and the GET reply when no `id` is given."""
        self._status = status
        self._body = body
        self._raw_text = None

    def reply_raw(self, status: int, text: str) -> None:
        """Sets a non-JSON POST reply, for a 401 body that cannot even be
        parsed rather than one that parses to the wrong shape."""
        self._status = status
        self._raw_text = text

    def reply_devices(self, user: str, body: object, status: int = 200) -> None:
        """Sets the GET reply for `?id=<user>`."""
        self._device_bodies[user] = body
        self._device_statuses[user] = status

    def reply_devices_raw(self, user: str, text: str, status: int = 200) -> None:
        """Sets a non-JSON GET reply for `?id=<user>`, for a body that
        cannot even be parsed rather than one that parses to the wrong
        shape."""
        self._device_raw_text[user] = text
        self._device_statuses[user] = status

    def hang(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    async def _post(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.last_request = _Recorded(request.content_type, form)
        await self._gate.wait()
        if self._raw_text is not None:
            return web.Response(
                text=self._raw_text, status=self._status, content_type="text/plain"
            )
        return web.json_response(self._body, status=self._status)

    async def _get(self, request: web.Request) -> web.Response:
        user = request.query.get("id")
        if user is not None:
            status = self._device_statuses.get(user, 200)
            if user in self._device_raw_text:
                return web.Response(
                    text=self._device_raw_text[user],
                    status=status,
                    content_type="text/plain",
                )
            body = self._device_bodies.get(user, {"sources": []})
            return web.json_response(body, status=status)
        return web.json_response(self._body, status=self._status)


@pytest.fixture
async def go2rtc(monkeypatch):
    fake = _FakeGo2rtc()
    app = web.Application()
    app.router.add_post("/api/xiaomi", fake._post)
    app.router.add_get("/api/xiaomi", fake._get)
    client = TestClient(TestServer(app))
    await client.start_server()
    server = client.server
    assert server.port is not None
    monkeypatch.setattr(_module, "_BASE", f"http://127.0.0.1:{server.port}/api/xiaomi")
    try:
        yield fake
    finally:
        await client.close()


async def test_password_step_posts_a_form_not_json(go2rtc):
    await sign_in("password", username="u", password="p")
    request = go2rtc.last_request
    assert request.content_type == "application/x-www-form-urlencoded"
    assert request.form == {"username": "u", "password": "p"}


async def test_a_401_names_the_next_step(go2rtc):
    """The keys are lowercase -- go2rtc's `LoginError` JSON tags in
    `pkg/xiaomi/cloud.go`, not the Go struct's capitalized field names. The
    capitalized spelling once read every captcha and verification target as
    absent, and a captcha-gated login came back to the page as a bare
    `{"captcha": null, ...}`."""
    go2rtc.reply(401, {"verify_phone": "138****5678"})
    result = await sign_in("password", username="u", password="p")
    assert result.ok is False
    assert result.verify_phone == "138****5678"
    assert result.captcha is None


async def test_a_captcha_comes_back_decoded(go2rtc):
    go2rtc.reply(401, {"captcha": base64.b64encode(b"png-bytes").decode()})
    result = await sign_in("password", username="u", password="p")
    assert result.ok is False
    assert result.captcha == b"png-bytes"
    assert result.verify_phone is None
    assert result.verify_email is None


async def test_two_sign_ins_at_once_are_refused(go2rtc):
    """go2rtc holds the half-finished login in a package-level variable, so a
    second flow overwrites the first and both fail in ways neither user can
    make sense of. Serialised here rather than discovered there."""
    go2rtc.hang()
    first = asyncio.create_task(sign_in("password", username="u", password="p"))
    await asyncio.sleep(0)
    with pytest.raises(SignInBusy):
        await sign_in("password", username="other", password="p")
    go2rtc.release()
    await first


async def test_device_urls_are_taken_whole(go2rtc):
    """We never compose the URL. go2rtc fills in the LAN address from the
    cloud, and a second composer here would be a second source for it."""
    go2rtc.reply(200, ["123"])
    go2rtc.reply_devices(
        "123",
        {
            "sources": [
                {"url": "xiaomi://123:cn@192.168.1.9?did=99&model=isa.camera.hlc7"},
            ]
        },
    )
    urls = await all_device_urls("cn")
    assert urls == {"99": "xiaomi://123:cn@192.168.1.9?did=99&model=isa.camera.hlc7"}


async def test_device_urls_merge_across_every_signed_in_account(go2rtc):
    """R3: the user never picks a Xiaomi account. go2rtc may hold several,
    and every one of them is queried and merged by did."""
    go2rtc.reply(200, ["123", "456"])
    go2rtc.reply_devices(
        "123",
        {"sources": [{"url": "xiaomi://123:cn@192.168.1.9?did=99&model=a"}]},
    )
    go2rtc.reply_devices(
        "456",
        {"sources": [{"url": "xiaomi://456:cn@192.168.1.10?did=100&model=b"}]},
    )
    urls = await all_device_urls("cn")
    assert urls == {
        "99": "xiaomi://123:cn@192.168.1.9?did=99&model=a",
        "100": "xiaomi://456:cn@192.168.1.10?did=100&model=b",
    }


async def test_signed_in_users_reads_the_bare_endpoint(go2rtc):
    go2rtc.reply(200, ["123", "456"])
    assert await signed_in_users() == ["123", "456"]


async def test_one_bad_account_does_not_take_down_the_others(go2rtc):
    """A camera reachable through a healthy account must not be reported
    unreachable just because some other signed-in account had a bad moment
    -- an expired session or a transient error, here a plain 500."""
    go2rtc.reply(200, ["123", "456"])
    go2rtc.reply_devices("123", {}, status=500)
    go2rtc.reply_devices(
        "456",
        {"sources": [{"url": "xiaomi://456:cn@192.168.1.10?did=100&model=b"}]},
    )
    urls = await all_device_urls("cn")
    assert urls == {"100": "xiaomi://456:cn@192.168.1.10?did=100&model=b"}


async def test_one_account_with_an_unreadable_body_does_not_take_down_the_others(
    go2rtc,
):
    """Same isolation, for a body that cannot be parsed rather than a bad
    status code."""
    go2rtc.reply(200, ["123", "456"])
    go2rtc.reply_devices_raw("123", "not json")
    go2rtc.reply_devices(
        "456",
        {"sources": [{"url": "xiaomi://456:cn@192.168.1.10?did=100&model=b"}]},
    )
    urls = await all_device_urls("cn")
    assert urls == {"100": "xiaomi://456:cn@192.168.1.10?did=100&model=b"}


async def test_a_non_json_401_body_fails_without_raising(go2rtc):
    """The caller is rendering this to someone part-way through a login, so
    an unreadable body must come back as a plain failure, never an
    exception."""
    go2rtc.reply_raw(401, "not json")
    result = await sign_in("password", username="u", password="p")
    assert result == (False, None, None, None)


async def test_a_401_body_that_is_a_list_fails_without_raising(go2rtc):
    """`body.get(...)` would raise `AttributeError` on a list; this must not
    surface as one."""
    go2rtc.reply(401, ["unexpected"])
    result = await sign_in("password", username="u", password="p")
    assert result == (False, None, None, None)


async def test_a_401_body_with_a_malformed_captcha_fails_without_raising(go2rtc):
    """Bad base64 padding in `captcha` must not surface as `binascii.Error`."""
    go2rtc.reply(401, {"captcha": "!!!not-base64!!!"})
    result = await sign_in("password", username="u", password="p")
    assert result == (False, None, None, None)


# ----------------------------------------------------------------------
# `compat_ready` -- cached, and refreshed only on request.
#
# `path_for` consults this on every camera refresh and every `/api/cameras`;
# a network round trip inside that pure resolution function would be wrong
# even over loopback. So the cache never calls go2rtc on a bare read, only
# when `refresh_compat_ready()` is asked to.
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    _module._ready = False
    yield
    _module._ready = False


async def test_compat_ready_starts_false_and_never_calls_go2rtc():
    """No fixture patching `_BASE` here on purpose: if a bare read called
    go2rtc, this would fail with a connection error rather than return
    `False`."""
    assert compat_ready() is False


async def test_refresh_turns_it_on_when_an_account_is_signed_in(go2rtc):
    go2rtc.reply(200, ["123"])
    await refresh_compat_ready()
    assert compat_ready() is True


async def test_refresh_turns_it_off_when_no_account_is_signed_in(go2rtc):
    go2rtc.reply(200, [])
    await refresh_compat_ready()
    assert compat_ready() is False


async def test_refresh_degrades_to_false_rather_than_raising(go2rtc):
    """go2rtc may not be up yet (add-on start-up) or may answer badly for a
    moment -- neither should take down whatever is refreshing this flag."""
    go2rtc.reply(500, {})
    _module._ready = True
    await refresh_compat_ready()
    assert compat_ready() is False
