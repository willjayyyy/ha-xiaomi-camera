"""Session plumbing.

These cover the parts that only fail at runtime -- the kind of defect that
passes review and type checking because it depends on Python's own rules rather
than on the logic being wrong.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bridge.api import BridgeApi
from bridge.framing import Consumer as _Consumer
from bridge.framing import MediaKind, MediaUnit, SessionStats
from bridge.framing import ParameterSets as _ParameterSets
from bridge.mux import AUDIO_CODEC_OPUS
from bridge.nal import Codec
from bridge.streaming import CameraSession, audio_codec_for
from miot.types import MIoTCameraCodec, MIoTCameraVideoQuality

_SC = b"\x00\x00\x00\x01"
#: H.265 VPS (type 32) and SPS (type 33): (byte >> 1) & 0x3F recovers the type.
_VPS = b"\x40\x01"
_SPS = b"\x42\x01"
#: An IRAP slice (type 19, within the 16..23 keyframe range) and an ordinary
#: non-IRAP, non-parameter-set slice (type 1), both without a start code --
#: `_fan_out` is given whole payloads, which carry their own start codes.
_KEYFRAME_PAYLOAD = _SC + b"\x26\x01\x02\x03"
_ORDINARY_PAYLOAD = _SC + b"\x02\x01\x02\x03"


def _bare_session() -> CameraSession:
    """A session with no live camera behind it -- enough state for `_fan_out`.

    `_fan_out` neither touches the client nor awaits anything, so building a
    real `CameraSession` and calling it directly exercises the merged
    mechanism itself rather than a hand-rolled copy of its logic.
    """
    session = CameraSession(
        client=None,
        info=SimpleNamespace(name="Test camera", did="0"),
        quality=MIoTCameraVideoQuality.LOW,
        enable_audio=False,
    )
    session._codec = Codec.H265
    return session


class TestConsumer:
    def test_is_hashable(self) -> None:
        """Consumers are held in a set, so they must be hashable.

        A dataclass that generates __eq__ silently loses __hash__, which only
        surfaces when the code actually runs.
        """
        consumer = _Consumer(queue=asyncio.Queue())
        assert {consumer}

    def test_two_consumers_are_distinct(self) -> None:
        """Identity, not field equality: two subscribers are never the same."""
        a = _Consumer(queue=asyncio.Queue())
        b = _Consumer(queue=asyncio.Queue())
        assert len({a, b}) == 2

    def test_removal_from_a_set_works(self) -> None:
        consumer = _Consumer(queue=asyncio.Queue())
        consumers = {consumer}
        consumers.discard(consumer)
        assert not consumers


class TestParameterSets:
    def test_keeps_one_unit_per_type(self) -> None:
        sets = _ParameterSets()
        sets.remember(b"\x40\x01")
        sets.remember(b"\x42\x01")
        sets.remember(b"\x40\x02")  # replaces the first
        assert len(sets.units) == 2

    def test_renders_with_start_codes(self) -> None:
        sets = _ParameterSets()
        sets.remember(b"\x40\x01")
        assert sets.as_annex_b() == b"\x00\x00\x00\x01\x40\x01"

    def test_empty_renders_to_nothing(self) -> None:
        assert _ParameterSets().as_annex_b() == b""


class TestFallingBehind:
    """What a viewer that cannot keep up costs everyone else.

    Dropping one chunk from an H.265 stream is not a dropped frame: the
    decoder cannot use anything until the next keyframe, which on these
    cameras is about three seconds away. Forwarding the rest of the group
    meanwhile produces artefacts instead of video, and it was invisible --
    a debug log, no counter -- to anyone trying to explain a stutter.
    """

    def test_a_full_queue_starts_a_resync(self) -> None:
        consumer = _Consumer(queue=asyncio.Queue(maxsize=1))
        consumer.queue.put_nowait(b"already here")

        assert consumer.resyncing is False
        # Standing in for the session's fan-out: full queue, so the consumer
        # is marked and its stale contents discarded.
        try:
            consumer.queue.put_nowait(b"one too many")
        except asyncio.QueueFull:
            while not consumer.queue.empty():
                consumer.queue.get_nowait()
            consumer.resyncing = True

        assert consumer.resyncing is True
        assert consumer.queue.empty(), "stale mid-group data must not be kept"

    def test_stats_report_it(self) -> None:
        # A number a user can quote beats a symptom they have to describe.
        stats = SessionStats()
        assert stats.as_dict()["resyncs"] == 0
        stats.resyncs += 1
        assert stats.as_dict()["resyncs"] == 1


class TestParameterReplay:
    """One mechanism, not two, exercised through the real `_fan_out`.

    A joining consumer and a resyncing one both need the parameter sets, and
    the code used to answer that in two places -- a free-standing chunk pushed
    at subscribe time, and a re-injection after a resync. A free-standing chunk
    has no timestamp, and under a container that is a special case with nothing
    to fill it in with. These tests drive `CameraSession._fan_out` itself, not
    a copy of its logic, so a regression in the merged mechanism fails one of
    them.
    """

    def test_a_new_consumer_starts_needing_parameters(self) -> None:
        assert _Consumer(queue=asyncio.Queue()).needs_parameters is True

    def test_a_joining_consumer_gets_them_prepended_once(self) -> None:
        session = _bare_session()
        session._parameter_sets.remember(_VPS)
        session._parameter_sets.remember(_SPS)
        consumer = _Consumer(queue=asyncio.Queue())
        session._consumers.add(consumer)

        session._fan_out(MediaUnit(MediaKind.VIDEO, 1_000, _ORDINARY_PAYLOAD))

        unit = consumer.queue.get_nowait()
        assert unit.payload == session._parameter_sets.as_annex_b() + _ORDINARY_PAYLOAD
        assert consumer.needs_parameters is False

    def test_a_resynced_consumer_gets_them_again_at_the_next_keyframe(self) -> None:
        session = _bare_session()
        session._parameter_sets.remember(_VPS)
        consumer = _Consumer(queue=asyncio.Queue())
        consumer.needs_parameters = False  # already served once, before falling behind
        consumer.resyncing = True
        session._consumers.add(consumer)

        session._fan_out(MediaUnit(MediaKind.VIDEO, 2_000, _KEYFRAME_PAYLOAD))

        unit = consumer.queue.get_nowait()
        assert unit.payload == session._parameter_sets.as_annex_b() + _KEYFRAME_PAYLOAD
        assert consumer.resyncing is False
        assert consumer.needs_parameters is False

    def test_a_consumer_that_already_has_them_is_not_sent_them_again(self) -> None:
        session = _bare_session()
        session._parameter_sets.remember(_VPS)
        consumer = _Consumer(queue=asyncio.Queue())
        consumer.needs_parameters = False
        session._consumers.add(consumer)

        session._fan_out(MediaUnit(MediaKind.VIDEO, 3_000, _ORDINARY_PAYLOAD))

        unit = consumer.queue.get_nowait()
        assert unit.payload == _ORDINARY_PAYLOAD


class TestMediaUnits:
    def test_a_unit_carries_its_own_time(self) -> None:
        """Bare bytes had nowhere to put this, which is the whole reason
        audio could not travel this path."""
        unit = MediaUnit(MediaKind.AUDIO, 1234, b"\x01\x02")
        assert unit.ts_ms == 1234
        assert unit.kind is MediaKind.AUDIO

    def test_units_are_immutable(self) -> None:
        """They are handed to every consumer at once; one consumer must not be
        able to alter what the others receive."""
        unit = MediaUnit(MediaKind.VIDEO, 0, b"")
        with pytest.raises(FrozenInstanceError):
            unit.ts_ms = 1


class TestAudioCodec:
    """What the camera sends decides whether a track is declared at all."""

    def test_opus_is_published(self) -> None:
        assert audio_codec_for(MIoTCameraCodec.AUDIO_OPUS) == AUDIO_CODEC_OPUS

    @pytest.mark.parametrize(
        "codec_id",
        [
            MIoTCameraCodec.AUDIO_G711A,
            MIoTCameraCodec.AUDIO_G711U,
            MIoTCameraCodec.AUDIO_PCM,
        ],
    )
    def test_everything_else_is_refused(self, codec_id) -> None:
        """MPEG-TS has no stream type that identifies G.711: the muxer accepts
        it and it demuxes back as an unusable `data` stream. A video-only
        stream is the honest outcome."""
        assert audio_codec_for(codec_id) is None


class TestAudioStatistics:
    """A number a user can read beats a symptom they have to describe.

    The option this work repairs spent its whole existence claiming to carry
    audio while carrying none, and nothing in the add-on could have said
    otherwise.
    """

    def test_a_session_with_no_audio_says_so(self) -> None:
        assert SessionStats().as_dict()["audio_codec"] is None

    def test_audio_is_counted(self) -> None:
        stats = SessionStats()
        stats.audio_codec = "opus"
        stats.audio_frames += 1
        stats.audio_bytes += 80
        reported = stats.as_dict()
        assert reported["audio_codec"] == "opus"
        assert reported["audio_frames"] == 1
        assert reported["audio_bytes"] == 80

    def test_dropped_timestamps_are_visible(self) -> None:
        """Frames silently missing is what this project keeps having to
        explain after the fact."""
        stats = SessionStats()
        stats.dropped_timestamps += 2
        assert stats.as_dict()["dropped_timestamps"] == 2

    async def test_the_counter_has_a_writer(self) -> None:
        """A statistic nothing assigns is an indicator wired to nothing --
        which is the defect this change exists to remove, not to repeat.

        This drives the real ``BridgeApi._stream`` handler over a real
        ``aiohttp`` request/response, through ``TestClient``/``TestServer`` --
        the same seam ``tests/test_webauth.py`` uses -- rather than doing the
        muxer-to-stats arithmetic here. A version of this test that performed
        `stats.dropped_timestamps += muxer.dropped` itself would still pass
        with the production write in ``api.py`` deleted: it would be
        checking its own arithmetic, not the endpoint's.
        """
        closed = asyncio.Event()
        units = [
            MediaUnit(MediaKind.VIDEO, 0, b"\x00\x00\x00\x01\x26\x01"),
            # The documented undecodable sentinel: the muxer cannot use it
            # and must count it as dropped rather than send it.
            MediaUnit(MediaKind.VIDEO, 0xFFFFFFFFFFFFFFFF, b"\x00"),
        ]

        class _DrainingQueue:
            """Yields the fixed units, then ends the pump loop.

            `_pump` races `consumer.queue.get()` against `consumer.closed.wait()`
            on `asyncio.wait(..., FIRST_COMPLETED)`, and neither coroutine here
            ever suspends -- both complete on their very first step. Setting
            `closed` once the queue empties would therefore make both tasks
            finish within the same poll, and `_pump` checks the `closed` branch
            first: the still-unread final unit would be discarded along with
            it, and this test would never reach the muxer at all.

            Raising once exhausted avoids that tie: `_pump` calls
            `unit_task.result()`, which re-raises, unwinding through the same
            `finally` production disconnects take -- exercised elsewhere by
            `except (ConnectionResetError, asyncio.CancelledError): pass` in
            `_stream` -- so the loop ends deterministically, driven by how many
            units were provided rather than by timing.
            """

            def __init__(self, items: list[MediaUnit]) -> None:
                self._items = list(items)

            async def get(self) -> MediaUnit:
                if not self._items:
                    raise asyncio.CancelledError()
                return self._items.pop(0)

        class _FakeSession:
            def __init__(self) -> None:
                self.codec = Codec.H265
                self.audio_codec = None
                self.stats = SessionStats()

            @contextlib.asynccontextmanager
            async def subscribe(self):
                # Never set: closing the loop is entirely the queue's job
                # here, so this only needs to be a legitimate, always-pending
                # partner in `_pump`'s race.
                consumer = SimpleNamespace(queue=_DrainingQueue(units), closed=closed)
                yield consumer

        session = _FakeSession()
        registry = SimpleNamespace(get=lambda did: object())
        sessions = SimpleNamespace(session_for=lambda info: session)

        api = BridgeApi(
            account=None,
            registry_provider=lambda: registry,
            sessions_provider=lambda: sessions,
            restreamer=None,
            refresh_callback=None,
            options=None,
            previews=None,
        )
        app = web.Application()
        app.router.add_get("/api/stream/{did}", api._stream)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/stream/42")
            await resp.read()
        finally:
            await client.close()

        assert session.stats.dropped_timestamps == 1


class TestCamerasEndpointReachability:
    """`/api/cameras` is where the page learns whether the RTSP address it is
    about to show can be rewritten to something reachable off this host.
    """

    async def test_the_field_is_read_from_the_restreamer_not_recomputed(self) -> None:
        """Drives the real ``BridgeApi._cameras`` handler over a real
        ``aiohttp`` request/response, the same seam as ``test_the_counter_has_a_writer``
        above, rather than asserting against ``restreamer.rtsp_reachable_off_host``
        directly -- a test that did that would pass even if the handler never
        put the field in the response at all.

        The fake restreamer's two flags are set to disagree on purpose:
        ``requires_credentials`` is False while ``rtsp_reachable_off_host`` is
        True. If the handler read the wrong attribute -- the mistake this field
        exists to prevent -- this would observe False and fail.
        """
        description = SimpleNamespace(
            did="42",
            as_dict=lambda: {
                "did": "42",
                "name": "Cam",
                "model": "chuangmi.camera.81ac1",
                "manufacturer": "Xiaomi",
                "channel_count": 1,
                "online": True,
                "lan_online": True,
                "powered_on": True,
                "requires_pin": False,
            },
        )

        async def _async_refresh():
            return [description]

        registry = SimpleNamespace(async_refresh=_async_refresh)
        restreamer = SimpleNamespace(
            rtsp_url=lambda did: f"rtsp://127.0.0.1:8554/camera_{did}",
            rtsp_url_h264=lambda did: f"rtsp://127.0.0.1:8554/camera_{did}_h264",
            stream_descriptions=lambda did: [],
            requires_credentials=False,
            rtsp_reachable_off_host=True,
        )

        api = BridgeApi(
            account=None,
            registry_provider=lambda: registry,
            sessions_provider=lambda: None,
            restreamer=restreamer,
            refresh_callback=None,
            options=None,
            previews=None,
        )
        app = web.Application()
        app.router.add_get("/api/cameras", api._cameras)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/cameras")
            payload = await resp.json()
        finally:
            await client.close()

        assert payload["cameras"][0]["rtsp_reachable_off_host"] is True
