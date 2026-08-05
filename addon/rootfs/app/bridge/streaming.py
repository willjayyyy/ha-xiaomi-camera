"""Camera sessions and fan-out of the camera's media units, video and audio.

One peer-to-peer session per camera is shared by every consumer -- the RTSP
restreamer, snapshot requests, anything else that subscribes. Sessions start
when the first consumer arrives and stop after the last one leaves, so an idle
camera costs neither bandwidth nor a held-open connection.

The vendor SDK exposes two callbacks with very different costs: raw video is a
zero-copy passthrough of the encoded elementary stream, while decoded JPEG runs
a full software decode per frame. Live viewing therefore uses the raw stream and
only snapshots pay for decoding.

Threading note: the SDK invokes its callbacks from its own thread but hands them
to the event loop with ``asyncio.run_coroutine_threadsafe``, so the coroutine
bodies below run *on the loop*. They contain no ``await`` and therefore execute
atomically within a single loop step, which is what makes the unsynchronised
access to the consumer set and the statistics safe. Introducing an ``await``
into a callback would silently break that.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from miot.types import MIoTCameraCodec, MIoTCameraStatus, MIoTCameraVideoQuality

from .const import (
    CONNECT_TIMEOUT_SECONDS,
    PARAMETER_SET_TIMEOUT_SECONDS,
    SNAPSHOT_TIMEOUT_SECONDS,
)
from .framing import Consumer as _Consumer
from .framing import MediaKind, MediaUnit, SessionStats
from .framing import ParameterSets as _ParameterSets
from .mux import AUDIO_CODEC_OPUS
from .nal import (
    Codec,
    detect_codec,
    is_keyframe,
    is_parameter_set,
    iter_nal_units,
    nal_type,
)
from .redact import safe_error

if TYPE_CHECKING:
    from miot.client import MIoTClient
    from miot.types import MIoTCameraInfo

_LOGGER = logging.getLogger(__name__)

#: Bounded so a stalled consumer drops frames instead of growing without limit.
#: A unit is one video frame or one audio packet -- the queue carries both since
#: this branch added audio. Video runs about 20 units/s; Opus adds one packet
#: every 20ms, about 50 units/s more, when a camera sends audio at all. Sized
#: for the worst case (audio flowing) to keep the original one second of
#: headroom: 20 + 50 = 70. A video-only session gets more headroom than that,
#: not less.
_CONSUMER_QUEUE_SIZE = 70

#: Grace period before tearing down a session whose last user left, so a client
#: reconnecting (a browser reloading a stream) reuses the live session rather
#: than paying the connect cost again.
_LINGER_SECONDS = 20.0

#: A cached still older than this is not returned. A switched-off camera keeps
#: its session CONNECTED while delivering nothing, so without an age check a
#: dashboard would show the frame from before power-off indefinitely.
_SNAPSHOT_MAX_AGE_SECONDS = 3.0


def audio_codec_for(codec_id: MIoTCameraCodec) -> str | None:
    """The container codec for an SDK audio codec, or None if unpublishable.

    Only Opus can be carried. MPEG-TS has no stream type that identifies A-law
    or mu-law: the muxer writes them without complaint and they demux back as a
    `data` stream with no codec context, which is a declared track that carries
    nothing usable -- the very defect being fixed here.

    Refusing rather than transcoding is deliberate. No camera in scope is known
    to send G.711, and the log line this produces names the codec, so the
    decision can be revisited with evidence instead of pre-empted without any.
    """
    return AUDIO_CODEC_OPUS if codec_id is MIoTCameraCodec.AUDIO_OPUS else None


class StreamError(RuntimeError):
    """Raised when a camera session cannot be established."""


class CameraOffError(StreamError):
    """Raised when the camera is switched off.

    Distinct from a connection failure because the remedy is different: the
    peer-to-peer session succeeds and simply carries no frames, so this must be
    reported as "the camera is off" rather than "the camera is unreachable".
    """


class CameraSession:
    """A single camera's peer-to-peer session, shared by all consumers."""

    def __init__(
        self,
        client: MIoTClient,
        info: MIoTCameraInfo,
        quality: MIoTCameraVideoQuality,
        enable_audio: bool,
        channel: int = 0,
    ) -> None:
        self._client = client
        self._info = info
        self._quality = quality
        self._enable_audio = enable_audio
        self._channel = channel
        self._instance = None
        self._consumers: set[_Consumer] = set()
        #: Counts everything keeping the session alive, including snapshot
        #: requests that never register a consumer. Without this a single
        #: snapshot would start a session nothing ever stops, leaving the camera
        #: streaming and the SDK decoding frames indefinitely.
        self._users = 0
        self._lock = asyncio.Lock()
        self._linger_task: asyncio.Task[None] | None = None
        self._codec: Codec | None = None
        self._parameter_sets = _ParameterSets()
        #: The container codec for this camera's audio, once a usable packet
        #: has arrived. The single source of truth for whether a container
        #: gets an audio track -- evidence, not configuration, because a track
        #: that is declared and never fed costs 11 to 25 extra seconds of cold
        #: start and is discarded anyway.
        self._audio_codec: str | None = None
        #: Guards the log line below, so an unsupported codec is explained once
        #: rather than fifty times a second.
        self._audio_codec_reported = False
        self._latest_jpeg: bytes | None = None
        self._latest_jpeg_at: float | None = None
        #: Replaced, never cleared, on each decoded frame. A waiter takes a
        #: reference before it waits, so two viewers cannot clear the event out
        #: from under each other -- with one shared event, a preview clearing it
        #: between a snapshot's clear and its wait made the snapshot miss the
        #: very frame it woke for.
        self._jpeg_ready = asyncio.Event()
        #: Set once the parameter sets a decoder needs have arrived. Replaced
        #: rather than cleared on teardown, for the same reason as
        #: ``_jpeg_ready``: a waiter holds its own reference.
        self._parameters_ready = asyncio.Event()
        self.stats = SessionStats()

    @property
    def did(self) -> str:
        return self._info.did

    @property
    def running(self) -> bool:
        return self._instance is not None

    @property
    def audio_codec(self) -> str | None:
        return self._audio_codec

    @property
    def codec(self) -> Codec:
        """The video encoding, known by the time a consumer is handed out.

        `subscribe` waits for the parameter sets, and detection happens on the
        first payload, so this cannot be unset by the time it is read. H.265 is
        the fallback rather than an error: every camera in scope sends it, and
        refusing to serve a stream over a detection that has not landed yet
        would be worse than serving the overwhelmingly likely one.
        """
        return self._codec or Codec.H265

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    async def _async_ensure_started(self) -> None:
        """Start the session if it is not already running."""
        async with self._lock:
            if self._linger_task is not None:
                self._linger_task.cancel()
                self._linger_task = None
            if self._instance is not None:
                # Holding an instance is not the same as holding a working
                # one. The peer-to-peer link drops on its own, and the SDK
                # reports that only through its status -- the object stays
                # exactly as it was. Trusting the object alone once left a
                # camera dead for two and a half hours: every request reused a
                # disconnected session, nothing reconnected, and because this
                # branch returned before the log line below, the add-on never
                # said a word about it.
                #
                # Asked on every entry rather than cached behind an interval.
                # The call is one crossing into the vendor library and the
                # callers are not hot -- snapshots come from dashboard tiles,
                # and the preview reads the published RTSP stream rather than
                # this path. A cache here would buy nothing and would put a
                # window back in, during which a dead link still looks alive.
                if await self._current_status() == MIoTCameraStatus.CONNECTED:
                    return
                _LOGGER.warning(
                    "%s lost its peer-to-peer link; reconnecting", self._info.name
                )
                await self._teardown()

            _LOGGER.info("Starting session for %s", self._info.name)
            instance = await self._client.create_camera_instance_async(
                self._info, enable_hw_accel=True
            )
            await instance.start_async(
                qualities=self._quality,
                pin_code=None,
                enable_audio=self._enable_audio,
                enable_reconnect=True,
            )

            deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if await instance.get_status_async() == MIoTCameraStatus.CONNECTED:
                    break
                await asyncio.sleep(0.5)
            else:
                with contextlib.suppress(Exception):
                    await instance.stop_async()
                raise StreamError(
                    f"{self._info.name} did not connect within "
                    f"{CONNECT_TIMEOUT_SECONDS}s"
                )

            # Callbacks are registered after the session is up because the SDK
            # creates its decoders in start_async.
            await instance.register_raw_video_async(
                self._on_video, channel=self._channel
            )
            await instance.register_decode_jpg_async(
                self._on_jpeg, channel=self._channel
            )
            if self._enable_audio:
                # Requested from the camera by start_async above; without this
                # registration the SDK receives every audio frame and discards
                # it, which is what the option did for its entire existence.
                await instance.register_raw_audio_async(
                    self._on_audio, channel=self._channel
                )

            self._instance = instance
            self.stats = SessionStats(started_at=time.monotonic())
            _LOGGER.info("Session established for %s", self._info.name)

    async def _current_status(self) -> MIoTCameraStatus | None:
        """The peer-to-peer link's status, or ``None`` if it cannot be read.

        An unreadable status is treated as a dead link by the caller rather
        than as a reason to give up: the remedy for both is the same
        reconnect, and the alternative -- keeping a session whose health is
        unknown -- is exactly the state this guards against.
        """
        assert self._instance is not None
        try:
            return await self._instance.get_status_async()
        except Exception as err:
            _LOGGER.warning(
                "Could not read %s's session status: %s",
                self._info.name,
                safe_error(err),
            )
            return None

    async def _teardown(self) -> None:
        """Stop the instance and reset everything derived from it.

        Assumes ``self._lock`` is already held. Separate from
        :meth:`async_stop` because reconnecting has to tear down from inside
        the critical section it is already in; calling the public method there
        would deadlock on the same lock.
        """
        if self._instance is None:
            return
        _LOGGER.info("Stopping session for %s", self._info.name)
        with contextlib.suppress(Exception):
            await self._instance.stop_async()
        self._instance = None
        self._codec = None
        self._parameter_sets = _ParameterSets()
        self._audio_codec = None
        self._audio_codec_reported = False
        self._latest_jpeg = None
        self._latest_jpeg_at = None
        # Released rather than discarded, so anything waiting for a frame
        # wakes up now and finds the session gone instead of waiting out
        # its own timeout.
        self._jpeg_ready.set()
        self._jpeg_ready = asyncio.Event()
        self._parameters_ready.set()
        self._parameters_ready = asyncio.Event()
        self.stats = SessionStats()
        # Signal through an event rather than a queue sentinel -- see
        # the note on _Consumer.closed.
        for consumer in list(self._consumers):
            consumer.closed.set()
        self._consumers.clear()

    async def async_stop(self) -> None:
        async with self._lock:
            if self._linger_task is not None:
                self._linger_task.cancel()
                self._linger_task = None
            await self._teardown()

    def _schedule_linger_stop(self) -> None:
        if self._linger_task is not None:
            self._linger_task.cancel()

        async def _stop_later() -> None:
            try:
                await asyncio.sleep(_LINGER_SECONDS)
            except asyncio.CancelledError:
                return
            if self._users == 0:
                try:
                    await self.async_stop()
                except Exception as err:  # pragma: no cover - defensive
                    # Nothing awaits this task, so an escaping exception would
                    # be invisible and would leave the session running forever.
                    _LOGGER.error(
                        "Stopping the session for %s failed: %s",
                        self._info.name,
                        safe_error(err),
                    )

        self._linger_task = asyncio.create_task(_stop_later())

    @contextlib.asynccontextmanager
    async def _hold(self) -> AsyncIterator[None]:
        """Keep the session alive for the duration of the context.

        Every entry point that needs frames goes through here, so a session's
        lifetime follows actual use rather than whether a particular API happens
        to register a consumer.
        """
        await self._async_ensure_started()
        self._users += 1
        try:
            yield
        finally:
            self._users -= 1
            if self._users == 0:
                self._schedule_linger_stop()

    # ------------------------------------------------------------------
    # Consumers
    # ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[_Consumer]:
        """Yield a consumer of stamped media units for the context's lifetime."""
        async with self._hold():
            await self._async_wait_for_parameter_sets()
            consumer = _Consumer(queue=asyncio.Queue(maxsize=_CONSUMER_QUEUE_SIZE))
            self._consumers.add(consumer)
            self.stats.consumers = len(self._consumers)
            try:
                yield consumer
            finally:
                self._consumers.discard(consumer)
                self.stats.consumers = len(self._consumers)

    async def _async_wait_for_parameter_sets(self) -> None:
        """Block until a joining consumer would have something decodable.

        An H.265 decoder cannot start without the parameter sets, and a camera
        sends them only ahead of a keyframe -- roughly every three seconds
        here. Handing over a consumer before then produces a stream that reads
        as corrupt rather than as empty, and the reader gives up long before
        the next keyframe explains why.

        Only the raw video path waits. Snapshots come from the SDK's own
        decoder and never touch these.
        """
        if self._parameter_sets.units:
            return
        ready = self._parameters_ready
        try:
            await asyncio.wait_for(ready.wait(), timeout=PARAMETER_SET_TIMEOUT_SECONDS)
        except TimeoutError as err:
            raise CameraOffError(
                f"{self._info.name} is connected but sent no video within "
                f"{PARAMETER_SET_TIMEOUT_SECONDS}s. The camera is most likely "
                f"switched off."
            ) from err

    async def async_snapshot(self, max_age: float | None = None) -> bytes:
        """Return a JPEG still, starting the session if needed.

        Snapshots use the SDK's decoded-JPEG callback rather than decoding the
        video stream, so a dashboard thumbnail does not require a decoder in
        this process.

        ``max_age`` is how old a held frame may be before a new one is waited
        for. The default suits a dashboard tile; a live preview refreshing
        twice a second passes something shorter, or every second request would
        return the picture it already has.
        """
        async with self._hold():
            if self._is_snapshot_fresh(max_age):
                assert self._latest_jpeg is not None
                return self._latest_jpeg

            # Either nothing has arrived yet, or what we hold is stale because
            # the camera stopped sending. Wait for a genuinely new frame rather
            # than serving a picture of a room that no longer matches reality.
            ready = self._jpeg_ready
            try:
                await asyncio.wait_for(ready.wait(), timeout=SNAPSHOT_TIMEOUT_SECONDS)
            except TimeoutError as err:
                raise CameraOffError(
                    f"{self._info.name} is connected but sent no video within "
                    f"{SNAPSHOT_TIMEOUT_SECONDS}s. The camera is most likely "
                    f"switched off."
                ) from err
            assert self._latest_jpeg is not None
            return self._latest_jpeg

    def _is_snapshot_fresh(self, max_age: float | None = None) -> bool:
        if self._latest_jpeg is None or self._latest_jpeg_at is None:
            return False
        limit = _SNAPSHOT_MAX_AGE_SECONDS if max_age is None else max_age
        return (time.monotonic() - self._latest_jpeg_at) <= limit

    # ------------------------------------------------------------------
    # SDK callbacks
    # ------------------------------------------------------------------

    async def _on_video(
        self, did: str, data: bytes, ts: int, seq: int, channel: int
    ) -> None:
        # The SDK discards the future returned by run_coroutine_threadsafe, so
        # an exception escaping here would produce no log output at all while
        # frames silently stopped reaching consumers.
        try:
            self._handle_video(bytes(data), ts)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.error(
                "Dropping a frame from %s: %s", self._info.name, safe_error(err)
            )

    def _handle_video(self, payload: bytes, ts: int) -> None:
        self.stats.frames += 1
        self.stats.bytes_total += len(payload)
        self.stats.last_frame_at = time.monotonic()

        if self._codec is None:
            self._codec = detect_codec(payload)
            if self._codec is not None:
                self.stats.codec = self._codec.value
                _LOGGER.info("%s stream codec: %s", self._info.name, self._codec.value)

        if self._codec is not None:
            for unit in iter_nal_units(payload):
                unit_type = nal_type(unit, self._codec)
                if is_parameter_set(unit_type, self._codec):
                    self._parameter_sets.remember(unit)
                    self._parameters_ready.set()
                elif is_keyframe(unit_type, self._codec):
                    self.stats.keyframes += 1

        if self.stats.frames <= 5:
            _LOGGER.warning(
                "TSDUMP %s frame%d len=%d ts=%d head=%s",
                self._info.name,
                self.stats.frames,
                len(payload),
                ts,
                payload[:24].hex(),
            )

        self._fan_out(MediaUnit(MediaKind.VIDEO, ts, payload))

    async def _on_audio(
        self,
        did: str,
        data: bytes,
        ts: int,
        seq: int,
        channel: int,
        codec_id: MIoTCameraCodec,
    ) -> None:
        try:
            self._handle_audio(bytes(data), ts, codec_id)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.error(
                "Dropping audio from %s: %s", self._info.name, safe_error(err)
            )

    def _handle_audio(self, payload: bytes, ts: int, codec_id: MIoTCameraCodec) -> None:
        codec = audio_codec_for(codec_id)
        if codec is None:
            if not self._audio_codec_reported:
                self._audio_codec_reported = True
                _LOGGER.info(
                    "%s sends %s audio, which cannot be published; its stream "
                    "will carry video only",
                    self._info.name,
                    codec_id.name,
                )
            return
        if self._audio_codec is None:
            self._audio_codec = codec
            self.stats.audio_codec = codec
            _LOGGER.info("%s audio codec: %s", self._info.name, codec)
        self.stats.audio_frames += 1
        self.stats.audio_bytes += len(payload)
        self._fan_out(MediaUnit(MediaKind.AUDIO, ts, payload))

    def _fan_out(self, unit: MediaUnit) -> None:
        """Hand a unit to every subscriber, dropping in whole groups.

        A consumer that falls behind loses data, and from that moment its
        decoder cannot use anything until the next keyframe. Sending it the
        rest of the group anyway produces artefacts rather than video, so the
        remainder is skipped and delivery resumes cleanly at the next keyframe
        -- the same amount of lost time, without the mess in between.
        """
        is_video = unit.kind is MediaKind.VIDEO
        starts_group = is_video and self._starts_group(unit.payload)
        for consumer in list(self._consumers):
            if consumer.resyncing:
                if not starts_group:
                    continue
                consumer.resyncing = False
                # A decoder joining at this keyframe needs the parameter sets,
                # and they were in the part that was skipped.
                consumer.needs_parameters = True
            payload = unit.payload
            if is_video and consumer.needs_parameters and self._parameter_sets.units:
                # Prepended to a real, stamped unit rather than sent as a chunk
                # of its own: a container has nowhere to put a payload that has
                # no time, and inventing one would be inventing sync.
                payload = self._parameter_sets.as_annex_b() + payload
                consumer.needs_parameters = False
            try:
                consumer.queue.put_nowait(
                    unit
                    if payload is unit.payload
                    else MediaUnit(unit.kind, unit.ts_ms, payload)
                )
            except asyncio.QueueFull:
                # Emptied rather than trimmed: what is in there is now the
                # middle of a group whose start this consumer will never see.
                while not consumer.queue.empty():
                    consumer.queue.get_nowait()
                consumer.resyncing = True
                self.stats.resyncs += 1
                _LOGGER.info(
                    "%s: a viewer fell behind and lost the picture until the "
                    "next keyframe (%d so far)",
                    self._info.name,
                    self.stats.resyncs,
                )

    def _starts_group(self, payload: bytes) -> bool:
        """Whether a decoder could begin here."""
        if self._codec is None:
            return False
        return any(
            is_keyframe(nal_type(unit, self._codec), self._codec)
            for unit in iter_nal_units(payload)
        )

    async def _on_jpeg(self, did: str, data: bytes, ts: int, channel: int) -> None:
        try:
            self._latest_jpeg = bytes(data)
            self._latest_jpeg_at = time.monotonic()
            # Wake everyone waiting on this frame, then hand out a fresh event
            # for the next one. No `await` runs between the two, so no waiter
            # can arrive in between and miss a wake-up.
            self._jpeg_ready.set()
            self._jpeg_ready = asyncio.Event()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.error(
                "Dropping a still from %s: %s", self._info.name, safe_error(err)
            )


class SessionManager:
    """Owns one :class:`CameraSession` per camera."""

    def __init__(
        self,
        client: MIoTClient,
        quality: MIoTCameraVideoQuality,
        enable_audio: bool,
    ) -> None:
        self._client = client
        self._quality = quality
        self._enable_audio = enable_audio
        self._sessions: dict[str, CameraSession] = {}

    def session_for(self, info: MIoTCameraInfo) -> CameraSession:
        session = self._sessions.get(info.did)
        if session is None:
            session = CameraSession(
                self._client, info, self._quality, self._enable_audio
            )
            self._sessions[info.did] = session
        return session

    def stats(self) -> dict[str, dict[str, object]]:
        return {
            did: session.stats.as_dict()
            for did, session in self._sessions.items()
            if session.running
        }

    async def async_prune(self, known_dids: set[str]) -> None:
        """Drop sessions for cameras that no longer exist.

        Without this a removed camera's session object -- and the native
        instance it holds -- would live for the lifetime of the process.
        """
        for did in list(self._sessions):
            if did not in known_dids:
                await self._async_stop_one(did)

    async def async_shutdown(self) -> None:
        for did in list(self._sessions):
            await self._async_stop_one(did)

    async def _async_stop_one(self, did: str) -> None:
        session = self._sessions.pop(did, None)
        if session is None:
            return
        try:
            await session.async_stop()
        except Exception as err:  # pragma: no cover - defensive
            # One failing session must not prevent the rest from being torn
            # down, which would hang shutdown until Supervisor kills us.
            _LOGGER.error("Stopping session %s failed: %s", did, safe_error(err))
