"""Session lifetime: when a session is rebuilt, and when it is fit to hand out.

Both faults covered here were found in production and neither produced a single
line of log. They are lifetime faults rather than logic errors -- the code is
correct for the order it was written against and wrong for another -- which is
why they survived review and why the camera that worked kept working.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from bridge import streaming
from bridge.streaming import CameraOffError, CameraSession
from miot.types import MIoTCameraStatus, MIoTCameraVideoQuality

_SC = b"\x00\x00\x00\x01"

#: One H.265 access unit as a camera sends it: VPS, SPS and PPS, then an IDR
#: picture. Parameter sets precede every keyframe, which is the only moment a
#: decoder joining mid-stream can start.
_PARAMETER_SETS = _SC + b"\x40\x01" + _SC + b"\x42\x01" + _SC + b"\x44\x01"
_KEYFRAME = _SC + b"\x26\x01\x02\x03"


class _FakeInfo:
    did = "1140981922"
    name = "Living room"


class _FakeInstance:
    """Stands in for a peer-to-peer session held by the vendor SDK.

    The delay before the first frame is the point: a real camera takes a
    moment to start sending, and the parameter sets a decoder needs arrive
    with it rather than at connect time.
    """

    def __init__(self, first_frame_delay: float = 0.0) -> None:
        self.status = MIoTCameraStatus.CONNECTED
        self.stopped = False
        self._first_frame_delay = first_frame_delay
        self._on_video = None
        self._feeder: asyncio.Task[None] | None = None

    async def start_async(self, **_kwargs: object) -> None:
        return None

    async def get_status_async(self) -> MIoTCameraStatus:
        return self.status

    async def register_raw_video_async(self, callback, channel: int = 0) -> int:
        self._on_video = callback
        self._feeder = asyncio.create_task(self._feed())
        return 0

    async def register_decode_jpg_async(self, callback, channel: int = 0) -> int:
        return 0

    async def _feed(self) -> None:
        await asyncio.sleep(self._first_frame_delay)
        assert self._on_video is not None
        # Repeats, as a real camera resends its parameter sets ahead of every
        # keyframe: a consumer that joins after the first delivery still needs
        # a video unit to arrive before it sees anything, since the parameter
        # sets are now carried on a unit rather than pushed on their own.
        while True:
            await self._on_video(_FakeInfo.did, _PARAMETER_SETS + _KEYFRAME, 0, 0, 0)
            await asyncio.sleep(0.01)

    async def stop_async(self) -> None:
        self.stopped = True
        if self._feeder is not None:
            self._feeder.cancel()


class _FakeClient:
    def __init__(self, first_frame_delay: float = 0.0) -> None:
        self.instances: list[_FakeInstance] = []
        self._first_frame_delay = first_frame_delay

    async def create_camera_instance_async(self, info, enable_hw_accel: bool = True):
        instance = _FakeInstance(self._first_frame_delay)
        self.instances.append(instance)
        return instance


def _session(client: _FakeClient) -> CameraSession:
    return CameraSession(
        client,
        _FakeInfo(),
        MIoTCameraVideoQuality.LOW,
        enable_audio=False,
    )


class TestReconnecting:
    """A session whose peer-to-peer link died must not be handed out again.

    Observed in production: the link dropped, the SDK reported DISCONNECTED
    for two and a half hours, and the session object stayed in place the whole
    time. Every request reused it, nothing reconnected, and the add-on logged
    nothing at all -- the only sign was go2rtc timing out every thirty
    seconds. Restarting the add-on fixed it within one second, which is what
    proved the camera was never at fault.
    """

    async def test_a_dead_link_is_rebuilt_rather_than_reused(self) -> None:
        client = _FakeClient()
        session = _session(client)

        async with session.subscribe():
            pass
        assert len(client.instances) == 1

        client.instances[0].status = MIoTCameraStatus.DISCONNECTED

        async with session.subscribe():
            pass

        assert len(client.instances) == 2, (
            "the session held a disconnected instance and never reconnected"
        )
        assert client.instances[0].stopped, "the dead instance was left running"

        await session.async_stop()

    async def test_a_live_link_is_reused(self) -> None:
        """The check must not cost a reconnect on every request."""
        client = _FakeClient()
        session = _session(client)

        async with session.subscribe():
            pass
        async with session.subscribe():
            pass

        assert len(client.instances) == 1
        await session.async_stop()


class TestColdStart:
    """A consumer must never be handed a stream it cannot decode.

    An H.265 decoder cannot start without the parameter sets, and a camera
    sends them only ahead of a keyframe -- about every three seconds here. The
    session waits for a first delivery before handing out any consumer, and
    prepends the held sets to the next unit a joining consumer receives, but
    on a cold start it holds none yet, so whoever arrived first got an
    undecodable stream and gave up. go2rtc's transcode allows five seconds,
    the session took nearly two to produce anything, and what remained was a
    coin toss against the keyframe interval.

    The camera that worked was not healthier: its dashboard fetched a snapshot
    first, which warmed the session, so by the time the stream connected the
    parameter sets were already held.
    """

    async def test_subscribe_waits_for_parameter_sets(self) -> None:
        """`subscribe()` itself must not return before the camera has sent
        anything -- not merely "the first unit happens to carry a VPS", which
        the feeder guarantees on every delivery regardless of whether anyone
        waited for it."""
        client = _FakeClient(first_frame_delay=0.05)
        session = _session(client)

        started = time.monotonic()
        async with session.subscribe() as consumer:
            elapsed = time.monotonic() - started
            first = await asyncio.wait_for(consumer.queue.get(), timeout=1.0)

        assert elapsed >= 0.04, (
            "subscribe() returned before the camera sent its first frame -- "
            "the wait for parameter sets was skipped"
        )
        assert b"\x40\x01" in first.payload, "the first unit carries no VPS"
        await session.async_stop()

    async def test_a_warm_session_does_not_wait(self) -> None:
        """A consumer joining a running session starts immediately.

        The deadline is far shorter than the camera's first-frame delay, so
        this fails if the wait starts over rather than seeing what the session
        already holds. Waiting every time would put the cost of a cold start
        on every reconnecting viewer -- and a browser reloading a stream is
        exactly the case the linger period exists to make cheap.
        """
        client = _FakeClient(first_frame_delay=0.2)
        session = _session(client)

        async with session.subscribe():
            pass

        async with asyncio.timeout(0.05):
            async with session.subscribe() as consumer:
                await consumer.queue.get()

        await session.async_stop()


class TestColdStartGivesUp:
    """Waiting cannot be unbounded -- a switched-off camera never sends.

    A camera that is off still connects and still answers every call; it
    simply produces no video. Waiting forever would turn that into the same
    silent hang the wait was added to remove, so it ends in the error that
    names the likely cause.
    """

    async def test_a_camera_that_sends_nothing_reports_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Shortened rather than waited out: the behaviour under test is that
        # the deadline is enforced and named, not how long it is.
        monkeypatch.setattr(streaming, "PARAMETER_SET_TIMEOUT_SECONDS", 0.05)
        client = _FakeClient(first_frame_delay=3600.0)
        session = _session(client)

        with pytest.raises(CameraOffError):
            async with asyncio.timeout(2.0):
                async with session.subscribe():
                    pass

        await session.async_stop()
