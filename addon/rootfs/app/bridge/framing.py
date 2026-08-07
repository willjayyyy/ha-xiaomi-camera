"""Buffering primitives for the raw video fan-out.

Kept separate from :mod:`bridge.streaming` because these carry no dependency on
the vendor SDK: that keeps them directly testable, and keeps the test suite's
claim of being SDK-free true rather than aspirational.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

_ANNEX_B_START_CODE = b"\x00\x00\x00\x01"


class MediaKind(StrEnum):
    """Which track a unit belongs to."""

    VIDEO = "video"
    AUDIO = "audio"


@dataclass(frozen=True)
class MediaUnit:
    """One encoded chunk, stamped on the camera's own clock.

    The timestamp is what makes audio possible at all. H.26x start codes carry
    their own frame boundaries, so video survived being passed around as bare
    bytes; Opus carries no such thing and needs a container, and a container
    needs the time. Both media arrive through one SDK callback stamped from one
    device clock, so carrying that number here is all synchronisation costs.
    """

    kind: MediaKind
    ts_ms: int
    payload: bytes


# eq=False keeps the default identity-based __eq__ and __hash__. A dataclass
# that generates __eq__ is unhashable, and consumers are held in a set --
# identity is also the semantics we want, since two subscribers are distinct
# even when their fields happen to match.
@dataclass(eq=False)
class Consumer:
    """A subscriber to the raw stream."""

    queue: asyncio.Queue[MediaUnit]
    #: Set when the session ends, so the reader stops waiting on the queue.
    #: A queue sentinel cannot serve this purpose: the queue is bounded and may
    #: be full exactly when the session is torn down, in which case the sentinel
    #: would be dropped and its reader would wait forever.
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    #: Set after this consumer fell behind and lost data. Everything up to the
    #: next keyframe is then skipped rather than sent: a decoder that has lost
    #: part of a group of pictures cannot use the rest of it, so forwarding it
    #: produces artefacts instead of video and delays the recovery it is
    #: waiting for.
    resyncing: bool = False
    #: Set while this consumer still needs the parameter sets prepended to the
    #: next video unit it is sent. True on arrival and true again after a
    #: resync, so a consumer joining and a consumer recovering take one path
    #: rather than two that have to be kept in step.
    needs_parameters: bool = True


@dataclass
class ParameterSets:
    """Most recent VPS/SPS/PPS, replayed to joining consumers."""

    units: list[bytes] = field(default_factory=list)

    def remember(self, unit: bytes) -> None:
        # Keep one of each type; cameras resend them before every keyframe.
        self.units = [u for u in self.units if u[:1] != unit[:1]]
        self.units.append(unit)

    def as_annex_b(self) -> bytes:
        return b"".join(_ANNEX_B_START_CODE + unit for unit in self.units)


#: How many keyframe gaps are kept. Long enough that one unlucky gap does not
#: swing the answer, short enough that a camera which changes its keyframe
#: interval -- firmware does this when the resolution or the scene changes --
#: is judged on what it sends now rather than on what it used to.
_KEYFRAME_SAMPLES = 20


@dataclass
class SessionStats:
    """Diagnostics for a live session, surfaced through the API."""

    codec: str | None = None
    frames: int = 0
    bytes_total: int = 0
    keyframes: int = 0
    #: When the last keyframe arrived, and the gaps before it. Kept because
    #: nothing declares how often a camera sends one: it is firmware's
    #: business, it differs by model, and there is no way to ask. Anything
    #: that trades freshness for work -- a still decoded from keyframes alone
    #: rather than from every frame -- can only be correct if it reads what
    #: this stream actually does instead of assuming what ours does.
    _last_keyframe_at: float | None = field(default=None, repr=False)
    _keyframe_gaps: deque[float] = field(
        default_factory=lambda: deque(maxlen=_KEYFRAME_SAMPLES), repr=False
    )
    #: Times a consumer fell behind far enough to lose data. Counted because a
    #: single dropped frame costs everything until the next keyframe -- about
    #: three seconds on these cameras -- and that is exactly what a viewer
    #: reports as "it stutters every few seconds". Left as a debug log, it was
    #: invisible to anyone trying to explain the stutter.
    resyncs: int = 0
    #: The container codec of the audio track, or None when the camera sends
    #: none. Not derived from the option: the option says what was asked for,
    #: this says what arrived.
    audio_codec: str | None = None
    audio_frames: int = 0
    audio_bytes: int = 0
    #: Units whose device timestamp could not be used -- the sentinel the SDK
    #: reports after a reconnect, mostly. Counted rather than merely logged, so
    #: a stream that is quietly short of frames can be recognised as one.
    dropped_timestamps: int = 0
    #: Times the muxer judged the device clock to have restarted and
    #: re-anchored its shared offset. Frequent re-anchoring on a camera that
    #: never actually reconnects would mean the audio and video timestamps do
    #: not share an epoch after all -- the one assumption this design could
    #: not verify in CI.
    clock_reanchors: int = 0
    #: Times a container without an audio track outlived the arrival of an
    #: audio unit, so the response was ended for go2rtc to reconnect onto a
    #: session that already has the track. The design treats this as a rare
    #: safety net rather than the normal path; if it fires on every connect
    #: instead of almost never, that assumption is wrong and cold start is
    #: paying for an extra reconnect cycle every time.
    late_audio_reconnects: int = 0
    started_at: float | None = None
    last_frame_at: float | None = None
    consumers: int = 0

    def note_keyframe(self, at: float) -> None:
        """Record a keyframe's arrival so the interval can be measured."""
        if self._last_keyframe_at is not None:
            self._keyframe_gaps.append(at - self._last_keyframe_at)
        self._last_keyframe_at = at

    @property
    def keyframe_interval(self) -> float | None:
        """The longest recent gap between keyframes, or None if unmeasured.

        The longest rather than the mean, because what this is read for is
        keeping a promise about freshness, and a promise is kept against the
        worst case. A camera that usually sends one a second and occasionally
        takes eight would look comfortable on an average while leaving a still
        eight seconds stale.

        `None` until a gap has actually been seen, and it must stay
        distinguishable from a small number: a caller that read "nothing
        measured yet" as "arrives constantly" would promise a freshness this
        stream has not been shown to support.
        """
        if not self._keyframe_gaps:
            return None
        return max(self._keyframe_gaps)

    def as_dict(self) -> dict[str, object]:
        uptime = time.monotonic() - self.started_at if self.started_at else 0.0
        return {
            "codec": self.codec,
            "frames": self.frames,
            "bytes": self.bytes_total,
            "keyframes": self.keyframes,
            "keyframe_interval": (
                round(self.keyframe_interval, 2)
                if self.keyframe_interval is not None
                else None
            ),
            "uptime_seconds": round(uptime, 1),
            # Measured rather than declared: the camera announces nothing, and
            # what it actually sends is what a viewer can be offered.
            "fps": round(self.frames / uptime, 1) if uptime > 1 else None,
            "consumers": self.consumers,
            "resyncs": self.resyncs,
            "audio_codec": self.audio_codec,
            "audio_frames": self.audio_frames,
            "audio_bytes": self.audio_bytes,
            "dropped_timestamps": self.dropped_timestamps,
            "clock_reanchors": self.clock_reanchors,
            "late_audio_reconnects": self.late_audio_reconnects,
            "stalled": self.is_stalled(),
        }

    def is_stalled(self) -> bool:
        """Whether the session is up but no longer receiving frames.

        A camera that is switched off keeps its session connected and simply
        stops sending, so "connected" alone says nothing about liveness.
        """
        if self.started_at is None:
            return False
        if self.last_frame_at is None:
            return True
        return (time.monotonic() - self.last_frame_at) > 10.0
