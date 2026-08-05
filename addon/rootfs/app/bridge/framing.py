"""Buffering primitives for the raw video fan-out.

Kept separate from :mod:`bridge.streaming` because these carry no dependency on
the vendor SDK: that keeps them directly testable, and keeps the test suite's
claim of being SDK-free true rather than aspirational.
"""

from __future__ import annotations

import asyncio
import time
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


@dataclass
class SessionStats:
    """Diagnostics for a live session, surfaced through the API."""

    codec: str | None = None
    frames: int = 0
    bytes_total: int = 0
    keyframes: int = 0
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
    started_at: float | None = None
    last_frame_at: float | None = None
    consumers: int = 0

    def as_dict(self) -> dict[str, object]:
        uptime = time.monotonic() - self.started_at if self.started_at else 0.0
        return {
            "codec": self.codec,
            "frames": self.frames,
            "bytes": self.bytes_total,
            "keyframes": self.keyframes,
            "uptime_seconds": round(uptime, 1),
            # Measured rather than declared: the camera announces nothing, and
            # what it actually sends is what a viewer can be offered.
            "fps": round(self.frames / uptime, 1) if uptime > 1 else None,
            "consumers": self.consumers,
            "resyncs": self.resyncs,
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
