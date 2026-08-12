"""The client for go2rtc's one Xiaomi endpoint.

Only `POST /api/xiaomi` and `GET /api/xiaomi` are ever called. go2rtc's wider
API exposes `exec` and stream management, and proxying any of it would put
those behind this add-on's own password.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bridge import go2rtc_xiaomi as _module
from bridge.go2rtc_xiaomi import (
    SignInBusy,
    all_device_urls,
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
        self._gate = asyncio.Event()
        self._gate.set()

    def reply(self, status: int, body: object) -> None:
        """Sets the POST reply, and the GET reply when no `id` is given."""
        self._status = status
        self._body = body

    def reply_devices(self, user: str, body: object) -> None:
        """Sets the GET reply for `?id=<user>`."""
        self._device_bodies[user] = body

    def hang(self) -> None:
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    async def _post(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.last_request = _Recorded(request.content_type, form)
        await self._gate.wait()
        return web.json_response(self._body, status=self._status)

    async def _get(self, request: web.Request) -> web.Response:
        user = request.query.get("id")
        if user is not None:
            return web.json_response(self._device_bodies.get(user, {"sources": []}))
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
    go2rtc.reply(401, {"VerifyPhone": "138****5678"})
    result = await sign_in("password", username="u", password="p")
    assert result.ok is False
    assert result.verify_phone == "138****5678"
    assert result.captcha is None


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
