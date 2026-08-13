"""`_read_own_slug`'s failure modes.

This function reads `SUPERVISOR_TOKEN` and calls out to Supervisor -- exactly
the shape of thing this project has already shipped broken once, silently:
`SUPERVISOR_TOKEN` unreadable, three code paths treating "no token" as a
legal state, auto-discovery never working, and nothing logged (see
`CLAUDE.md`, hard fact 4). Every way this one can fail is covered here rather
than trusted to behave, on the strength of `discovery.py` doing something
similar next to it.

`bridge.__main__` is importable without the vendor SDK -- see
`tests/conftest.py`'s stub and `tests/test_bridge_publishing.py`, which
already imports `Bridge` from here.
"""

from __future__ import annotations

import importlib.util

import bridge.__main__ as main_module
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bridge.__main__ import _read_own_slug

if importlib.util.find_spec("pytest_socket") is not None:
    # `TestServer` below opens a real loopback socket -- same opt-in
    # `test_routes.py` needs for the same reason.
    pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.asyncio
async def test_no_token_returns_none_without_making_a_request():
    """The early return -- needs no server, mock or patch at all."""
    assert await _read_own_slug(None) is None


@pytest.mark.asyncio
async def test_the_real_slug_is_read_from_the_response(monkeypatch):
    async def info(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        return web.json_response({"data": {"slug": "a1b2c3d4_xiaomi_camera_bridge"}})

    app = web.Application()
    app.router.add_get("/addons/self/info", info)
    client = TestClient(TestServer(app))
    await client.start_server()
    monkeypatch.setattr(main_module, "_SUPERVISOR_URL", str(client.make_url("")))
    try:
        assert await _read_own_slug("tok") == "a1b2c3d4_xiaomi_camera_bridge"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_non_200_response_yields_none(monkeypatch):
    async def info(request: web.Request) -> web.Response:
        return web.Response(status=503, text="unavailable")

    app = web.Application()
    app.router.add_get("/addons/self/info", info)
    client = TestClient(TestServer(app))
    await client.start_server()
    monkeypatch.setattr(main_module, "_SUPERVISOR_URL", str(client.make_url("")))
    try:
        assert await _read_own_slug("tok") is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_malformed_body_yields_none_not_a_raise(monkeypatch, caplog):
    async def info(request: web.Request) -> web.Response:
        return web.Response(
            status=200, text="not json", content_type="application/json"
        )

    app = web.Application()
    app.router.add_get("/addons/self/info", info)
    client = TestClient(TestServer(app))
    await client.start_server()
    monkeypatch.setattr(main_module, "_SUPERVISOR_URL", str(client.make_url("")))
    try:
        with caplog.at_level("WARNING"):
            assert await _read_own_slug("tok") is None
        assert "Could not read this add-on's slug" in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_connection_failure_yields_none_and_is_logged(monkeypatch, caplog):
    # Nothing listens here -- exercises the `except Exception` branch, the
    # same one a Supervisor that is simply unreachable would hit.
    monkeypatch.setattr(main_module, "_SUPERVISOR_URL", "http://127.0.0.1:1")
    with caplog.at_level("WARNING"):
        assert await _read_own_slug("tok") is None
    assert "Could not read this add-on's slug" in caplog.text
