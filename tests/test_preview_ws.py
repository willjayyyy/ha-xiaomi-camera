"""The preview's WebSocket endpoint.

The page used to fetch one picture per HTTP request, holding each request open
until a newer frame existed. That worked, and it cost a round trip per frame
and had no way back to the browser except a response body -- so a camera that
was simply switched off could only be reported by letting the request run out
its twenty-second timeout, and what the viewer saw was ffmpeg's own complaint
about RTSP.

One connection replaces it, carrying two kinds of message: binary frames are
whole JPEGs, and text frames are JSON telling the page what is going on. The
first is what removes the per-frame round trip; the second is what lets the
add-on say "that camera is switched off" using something it already knows.

These tests drive the real handler over `TestClient`/`TestServer`, because a
WebSocket handshake is exactly the part that cannot be checked by inspecting
the route table.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from bridge.api import BridgeApi

pytestmark = pytest.mark.usefixtures("socket_enabled")

_JPEG = b"\xff\xd8preview\xff\xd9"


class _Previews:
    """Stands in for `PreviewManager`, handing out a fixed run of frames.

    Once they are gone it never returns, so the handler is left waiting the
    way it would be on a live camera between frames -- rather than seeing an
    end that a real preview never has.
    """

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.asked: list[tuple] = []

    async def async_frame(
        self, did: str, fps: int, quality: str, after: int = 0
    ) -> tuple[int, bytes]:
        self.asked.append((did, fps, quality, after))
        if not self._frames:
            await asyncio.Event().wait()
        return len(self.asked), self._frames.pop(0)


def _api(previews: _Previews, *, powered_on: bool | None = True) -> BridgeApi:
    class _Camera:
        did = "42"

    class _Registry:
        def get(self, did: str) -> object | None:
            return _Camera() if did == "42" else None

        def power_state(self, did: str) -> bool | None:
            return powered_on

    class _Session:
        pass

    return BridgeApi(
        account=None,
        registry_provider=_Registry,
        sessions_provider=lambda: type(
            "_Sessions", (), {"session_for": lambda self, info: _Session()}
        )(),
        restreamer=None,
        refresh_callback=None,
        options=None,
        previews=previews,
    )


async def _client(api: BridgeApi) -> TestClient:
    app = web.Application()
    app.router.add_get("/api/preview/{did}/ws", api._preview_ws)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestFramesArriveAsBinaryMessages:
    async def test_a_whole_jpeg_arrives_in_one_message(self) -> None:
        """No delimiters to hunt for: a message boundary is a frame boundary.

        Over HTTP the page received one JPEG per response and the add-on had
        to find each one in ffmpeg's output by its own markers. That framing
        job does not go away inside the add-on, but it stops being something
        the wire has to redo.
        """
        previews = _Previews([_JPEG])
        client = await _client(_api(previews))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.type is WSMsgType.BINARY
                assert message.data == _JPEG
        finally:
            await client.close()


class TestASwitchedOffCameraIsNamedAsSuch:
    """A camera that is off is a known state, not a failure to be timed out.

    It connects, every call succeeds, and it sends nothing -- so the only way
    the old endpoint could report it was to wait out the first-frame timeout
    and pass on whatever ffmpeg had to say, which was a sentence about RTSP.
    The add-on has already read this camera's power switch to answer
    `/api/cameras`; saying so costs nothing and starting a decoder to
    rediscover it costs a process.

    The message names a reason rather than carrying a sentence: the page has
    both languages, this file has neither, and a machine-readable reason is
    what lets the viewer be told in the one they chose.
    """

    async def test_it_says_the_camera_is_switched_off(self) -> None:
        previews = _Previews([_JPEG])
        client = await _client(_api(previews, powered_on=False))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.type is WSMsgType.TEXT
                assert message.json() == {
                    "type": "unavailable",
                    "reason": "switched_off",
                }
        finally:
            await client.close()

    async def test_it_does_not_start_a_decoder_for_it(self) -> None:
        previews = _Previews([_JPEG])
        client = await _client(_api(previews, powered_on=False))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                await asyncio.wait_for(ws.receive(), timeout=2)
            assert previews.asked == []
        finally:
            await client.close()

    async def test_an_unreadable_power_state_is_not_treated_as_off(self) -> None:
        """`None` means the switch could not be read, which is not "off".

        Refusing to open a preview on that basis would turn a momentary cloud
        failure into a camera that appears switched off, and the picture is
        the better evidence anyway: if frames arrive, it is plainly on.
        """
        previews = _Previews([_JPEG])
        client = await _client(_api(previews, powered_on=None))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.type is WSMsgType.BINARY
        finally:
            await client.close()
