"""MPEG-TS packaging for the published stream.

The vendor SDK hands over pre-encoded media -- video and audio both -- stamped
from one device clock in milliseconds. Video alone could be forwarded as a bare
elementary stream because H.26x start codes carry their own frame boundaries.
Opus carries nothing of the kind, so publishing audio at all means publishing a
container.

MPEG-TS is that container. Its parameter sets stay in band, so nothing has to
be supplied out of band, and go2rtc demuxes it natively -- which is what lets
the root stream drop its ffmpeg process rather than gain one. Matroska was
measured and rejected: it requires CodecPrivate, and a stream created for
muxing alone has nowhere to put it.

Nothing here encodes or decodes. `add_mux_stream` builds an output stream with
no codec context at all, so every payload is packaged exactly as the camera
sent it.
"""

from __future__ import annotations

import fractions
import logging

import av

from .framing import MediaKind, MediaUnit
from .nal import Codec

_LOGGER = logging.getLogger(__name__)

#: Container time base. The SDK stamps in milliseconds, so this carries the
#: device's own numbers with neither rescaling nor rounding.
_TIME_BASE = fractions.Fraction(1, 1000)

#: A timestamp at or above this cannot be handed to the muxer at all: PyAV
#: raises OverflowError converting it. The SDK reports 0xFFFFFFFFFFFFFFFF when
#: the device PTS is unknown, which Miloco's source documents for the first
#: frames after a peer-to-peer reconnect.
_MAX_TS_MS = 2**62

#: A jump beyond this, in either direction, is a restarted device clock rather
#: than a gap in delivery. Comfortably longer than the three seconds between
#: this camera's keyframes, and far shorter than any real absence of media.
_DISCONTINUITY_MS = 5_000

#: This project names the format; PyAV names the decoder. `h265` is not a name
#: PyAV accepts -- it raises `ValueError: Unknown codec: 'h265'` -- and this is
#: the only place the two vocabularies meet.
_PYAV_VIDEO_CODECS: dict[Codec, str] = {Codec.H264: "h264", Codec.H265: "hevc"}

#: The only audio encoding MPEG-TS carries identifiably. G.711 is accepted by
#: the muxer without complaint and demuxes back as a `data` stream with no
#: codec context -- a track that is declared and useless, which is the exact
#: defect this work exists to remove.
AUDIO_CODEC_OPUS = "opus"

#: Opus always codes at 48 kHz whatever rate its encoder was fed, and the
#: container reports 48000 back whatever is passed here. It is passed because
#: the muxer refuses an audio stream with no rate at all -- `ArgumentError 22`,
#: before a single byte is written -- not because the camera's capture rate is
#: known or needed.
_OPUS_SAMPLE_RATE = 48_000


class _Sink:
    """Collects what the muxer writes, for the caller to send onward.

    PyAV writes synchronously from the calling thread, and an HTTP response
    cannot be awaited from inside that call. Buffering here and draining
    between packets keeps the write on the event loop where it belongs.
    """

    def __init__(self) -> None:
        self._pending = bytearray()

    def write(self, data: bytes) -> int:
        self._pending += data
        return len(data)

    def flush(self) -> None:
        """Required by the file protocol PyAV writes through."""

    def take(self) -> bytes:
        data = bytes(self._pending)
        self._pending.clear()
        return data


class StreamMuxer:
    """Packages one consumer's media units as MPEG-TS.

    One instance per consumer, never shared: a container fixes its tracks
    before its first byte, and each reader is entitled to a container that
    starts where it joined.
    """

    def __init__(self, video_codec: Codec, audio_codec: str | None) -> None:
        self._sink = _Sink()
        self._container = av.open(self._sink, mode="w", format="mpegts")
        self._streams = {
            MediaKind.VIDEO: self._container.add_mux_stream(
                _PYAV_VIDEO_CODECS[video_codec]
            )
        }
        if audio_codec is not None:
            self._streams[MediaKind.AUDIO] = self._container.add_mux_stream(
                audio_codec, rate=_OPUS_SAMPLE_RATE
            )
        for stream in self._streams.values():
            stream.time_base = _TIME_BASE

        #: Subtracted from every device timestamp. Shared by both tracks,
        #: which is precisely what keeps them in sync.
        self._offset: int | None = None
        #: One value shared by both tracks rather than one per track. That is
        #: only correct because the SDK dispatches every callback through
        #: `asyncio.run_coroutine_threadsafe` (see streaming.py's threading
        #: note), which preserves the order units were produced in across
        #: audio and video alike -- so a single "last seen" timestamp is a
        #: valid discontinuity check for both. Queueing either track's
        #: callbacks separately, or dispatching audio through a different
        #: path, would silently break that ordering guarantee.
        self._last_input: int | None = None
        self._last_output: dict[MediaKind, int] = {}
        #: Units whose timestamp could not be used. Reported, because a stream
        #: quietly missing frames is what this project keeps having to explain.
        self.dropped = 0
        #: Times the device clock was judged to have restarted and the shared
        #: offset re-anchored. The one assumption this design cannot verify in
        #: CI is that the camera's audio and video timestamps share an epoch;
        #: if they do not, the symptom is desynchronised audio with otherwise
        #: clean statistics, so this is counted rather than only logged.
        self.reanchors = 0

    @property
    def has_audio(self) -> bool:
        return MediaKind.AUDIO in self._streams

    def write(self, unit: MediaUnit) -> bytes:
        """Package one unit and return the bytes to send, possibly none."""
        stamp = self._stamp(unit.ts_ms, unit.kind)
        if stamp is None:
            self.dropped += 1
            return b""
        packet = av.Packet(unit.payload)
        packet.stream = self._streams[unit.kind]
        packet.pts = packet.dts = stamp
        packet.time_base = _TIME_BASE
        self._container.mux(packet)
        return self._sink.take()

    def close(self) -> bytes:
        """Finish the container and return whatever it wrote on the way out."""
        self._container.close()
        return self._sink.take()

    def _stamp(self, ts_ms: int, kind: MediaKind) -> int | None:
        """The output timestamp for a unit, or None if it cannot be used.

        Four rules, all of them for behaviour observed against a real muxer
        rather than anticipated.
        """
        if not 0 <= ts_ms < _MAX_TS_MS:
            # Unrepairable without inventing a value. Dropping costs little:
            # these arrive after a reconnect, and a reconnect is followed by a
            # fresh keyframe anyway.
            return None

        if self._offset is None or self._last_input is None:
            self._offset = ts_ms
        elif abs(ts_ms - self._last_input) > _DISCONTINUITY_MS:
            # The device clock restarted. Both tracks jump together, so
            # re-anchoring the one shared offset keeps them in sync as well as
            # keeping the output moving forward.
            self._offset = ts_ms - (max(self._last_output.values(), default=0) + 1)
            self.reanchors += 1
            _LOGGER.debug(
                "clock re-anchored after a %sms jump (kind=%s, reanchors=%d)",
                ts_ms - self._last_input,
                kind,
                self.reanchors,
            )
        self._last_input = ts_ms

        stamp = ts_ms - self._offset
        last = self._last_output.get(kind)
        if last is not None and stamp <= last:
            # A single repeat is enough to make the muxer reject the packet
            # with `ArgumentError 22`, which ends the whole response.
            stamp = last + 1
        self._last_output[kind] = stamp
        return stamp
