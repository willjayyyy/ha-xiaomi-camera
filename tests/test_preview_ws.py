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
from bridge.preview import PreviewError

pytestmark = pytest.mark.usefixtures("socket_enabled")

_JPEG = b"\xff\xd8preview\xff\xd9"


class _Previews:
    """Stands in for `PreviewManager`, handing out a fixed run of frames.

    Once they are gone it never returns, so the handler is left waiting the
    way it would be on a live camera between frames -- rather than seeing an
    end that a real preview never has.
    """

    def __init__(self, frames: list[bytes], *, endless: bool = False) -> None:
        self._frames = list(frames)
        self._endless = endless
        self.asked: list[tuple] = []

    async def async_frame(
        self, did: str, fps: int, quality: str, after: int = 0
    ) -> tuple[int, bytes]:
        self.asked.append((did, fps, quality, after))
        if self._endless:
            # A live camera never stops producing, so a handler that never
            # notices its viewer has gone will go on asking forever. Yielding
            # keeps that from starving the event loop in a test.
            await asyncio.sleep(0.001)
            return len(self.asked), self._frames[0]
        if not self._frames:
            await asyncio.Event().wait()
        return len(self.asked), self._frames.pop(0)


def _api(previews, *, powered_on: bool | None = True, switch=None) -> BridgeApi:
    """A bridge wired to one camera.

    ``switch`` is a mutable holder when a test needs the lens to change while
    the connection is open -- the case that matters is a camera switched off
    after the list was last read, which a cached value cannot report.
    """

    class _Camera:
        did = "42"

    class _Registry:
        def get(self, did: str) -> object | None:
            return _Camera() if did == "42" else None

        def power_state(self, did: str) -> bool | None:
            return switch["powered_on"] if switch else powered_on

        async def async_read_power_state(self, did: str) -> bool | None:
            return switch["powered_on"] if switch else powered_on

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


class TestAViewerLeavingStopsTheDecoder:
    """A closed connection has to be noticed, and only reading notices it.

    Over HTTP this was free: the viewer stopped asking, so the add-on stopped
    answering, and the decoder went idle and was reaped. "Is anyone still
    watching?" was answered by each request simply existing.

    A socket does not come with that. `ws.closed` reports what this end has
    processed, and a handler that only ever sends processes nothing -- so the
    close frame sits unread, the loop keeps pulling frames, and the decoder's
    idle timer is reset forever by a viewer who left. Observed in the field as
    a second ffmpeg per camera that never went away, CPU at 86%, and the
    picture stuttering because encoding could not keep up with itself.
    """

    async def test_it_stops_pulling_frames_once_the_socket_closes(self) -> None:
        previews = _Previews([_JPEG], endless=True)
        client = await _client(_api(previews))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                await asyncio.wait_for(ws.receive(), timeout=2)
            # The socket is closed here. Whatever the handler was in the
            # middle of, it must not still be asking for pictures.
            await asyncio.sleep(0.1)
            settled = len(previews.asked)
            await asyncio.sleep(0.2)
            assert len(previews.asked) == settled
        finally:
            await client.close()


class TestTheDecoderIsReallyReclaimed:
    """End to end, against the real manager rather than a stand-in.

    The stand-in above proves the handler stops asking. What it cannot prove
    is that the manager then lets the decoder go -- and those are different
    claims joined by an idle timer that the handler's asking keeps resetting.
    The fault in the field lived exactly in that join: the handler asked
    forever, so the timer never expired, so a camera nobody was watching kept
    an ffmpeg to itself.

    Wired to a process that never exits, because what happens to the decoder
    afterwards is beside the point here -- what matters is that the manager
    stops holding it.
    """

    async def test_leaving_lets_the_manager_reap_the_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bridge import preview
        from bridge.preview import PreviewManager
        from conftest import NeverReportsExit

        async def spawn(*args: object, **kwargs: object) -> NeverReportsExit:
            return NeverReportsExit()

        monkeypatch.setattr(preview.asyncio, "create_subprocess_exec", spawn)
        # The real interval is fifteen seconds, which is a long time to hold a
        # test open for a timer whose duration is not what is being checked.
        monkeypatch.setattr(preview, "_IDLE_SECONDS", 0.3)

        manager = PreviewManager(lambda did: f"rtsp://127.0.0.1:8554/camera_{did}")
        client = await _client(_api(manager))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.type is WSMsgType.BINARY
            assert manager._sources, "a source should have been opened"

            # Nothing is watching now. The reaper polls at a third of the idle
            # interval, so a second of it is several passes.
            await asyncio.sleep(1.0)
            assert manager._sources == {}, "the decoder outlived its viewer"
        finally:
            if manager._reaper is not None:
                manager._reaper.cancel()
            for source in list(manager._sources.values()):
                for task in source._tasks:
                    task.cancel()
            for task in [*manager._closing]:
                task.cancel()
            await asyncio.sleep(0)
            await client.close()


class TestACameraSwitchedOffWhileWatchedIsNamed:
    """Switched off after the connection opened, which is the common way.

    Checking the switch at connect time covers opening a preview for a camera
    already off. It does not cover the ordinary case of someone turning a
    camera off while it is being watched -- there the stream simply stops, the
    decoder times out, and what reached the viewer was the generic "could not
    load" rather than the one sentence that would tell them what to do.

    Re-read rather than taken from the last list refresh: the whole point is a
    camera switched off *since* then, which the cached value by definition
    cannot report.
    """

    async def test_it_says_switched_off_rather_than_a_generic_failure(self) -> None:
        switch = {"powered_on": True}

        class _StopsWhenSwitchedOff:
            async def async_frame(self, did, fps, quality, after=0):
                switch["powered_on"] = False
                raise PreviewError("No video arrived from the published stream.")

        client = await _client(_api(_StopsWhenSwitchedOff(), switch=switch))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.type is WSMsgType.TEXT
                assert message.json()["reason"] == "switched_off"
        finally:
            await client.close()

    async def test_a_failure_with_the_camera_still_on_stays_generic(self) -> None:
        """Only the switch explains itself. Everything else is still unknown."""
        switch = {"powered_on": True}

        class _JustFails:
            async def async_frame(self, did, fps, quality, after=0):
                raise PreviewError("No video arrived from the published stream.")

        client = await _client(_api(_JustFails(), switch=switch))
        try:
            async with client.ws_connect("/api/preview/42/ws") as ws:
                message = await asyncio.wait_for(ws.receive(), timeout=2)
                assert message.json()["reason"] == "no_video"
        finally:
            await client.close()
