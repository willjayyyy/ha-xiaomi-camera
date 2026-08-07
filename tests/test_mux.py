"""Packaging the published stream.

PyAV is an ordinary dependency, so these mux real payloads and demux the
result rather than asserting against a mock. Every hazard covered here was
reproduced against go2rtc before it was written down: each one either refuses
a packet outright or takes the whole HTTP response down mid-stream.

**Timestamps are asserted on the audio track.** Opus packets carry their own
framing in MPEG-TS and come back out one for one, so what went in can be
compared with what came out. Video cannot be used that way here: ffmpeg's H.265
parser looks for real frame boundaries, and synthetic payloads are coalesced
into a single packet however many were muxed. The timestamp rules are per-track
and take no interest in which track they are applied to, so testing them on the
one that round-trips faithfully tests all of them. Do not "fix" this by
asserting video packet counts -- any such assertion passes by accident.
"""

from __future__ import annotations

import io

import av
import pytest
from bridge.framing import MediaKind, MediaUnit, SessionStats
from bridge.mux import AUDIO_CODEC_OPUS, StreamMuxer
from bridge.nal import Codec

#: The value the SDK reports when the device PTS is unknown, which Miloco's
#: own source documents for the first frames after a reconnect.
SENTINEL_TS = 0xFFFFFFFFFFFFFFFF


def _video(ts_ms: int) -> MediaUnit:
    # The muxer packages bytes; it never parses them, so a plausible NAL header
    # and some filler is all a payload has to be.
    return MediaUnit(MediaKind.VIDEO, ts_ms, b"\x00\x00\x00\x01\x26\x01" + bytes(64))


def _audio(ts_ms: int) -> MediaUnit:
    return MediaUnit(MediaKind.AUDIO, ts_ms, bytes(80))


def _drain(muxer: StreamMuxer, units: list[MediaUnit]) -> bytes:
    body = b"".join(muxer.write(unit) for unit in units)
    return body + muxer.close()


def _demux(body: bytes) -> tuple[list[str], dict[str, list[int]]]:
    """Track types, and each track's timestamps in milliseconds."""
    container = av.open(io.BytesIO(body), format="mpegts")
    kinds = [stream.type for stream in container.streams]
    times: dict[str, list[int]] = {}
    for packet in container.demux():
        if packet.size == 0 or packet.pts is None:
            continue
        times.setdefault(packet.stream.type, []).append(
            round(float(packet.pts * packet.time_base) * 1000)
        )
    container.close()
    return kinds, times


class TestTracks:
    def test_both_tracks_are_published(self) -> None:
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [_video(0), _audio(0), _video(50), _audio(20)]
        kinds, _ = _demux(_drain(muxer, units))
        assert sorted(kinds) == ["audio", "video"]

    def test_without_audio_only_one_track_is_declared(self) -> None:
        """A track that never carries data costs 11 to 25 extra seconds of
        cold start and is discarded anyway, so it must not be declared."""
        muxer = StreamMuxer(Codec.H265, None)
        kinds, _ = _demux(_drain(muxer, [_video(0), _video(50)]))
        assert kinds == ["video"]
        assert muxer.has_audio is False

    def test_h265_is_named_the_way_pyav_names_it(self) -> None:
        """This project names the format, PyAV names the decoder.

        `add_mux_stream("h265")` raises `ValueError: Unknown codec`, so the
        translation has to happen somewhere -- and only here.
        """
        muxer = StreamMuxer(Codec.H265, None)
        kinds, _ = _demux(_drain(muxer, [_video(0)]))
        assert kinds == ["video"]

    def test_the_audio_stream_is_given_a_rate(self) -> None:
        """Without one the muxer refuses the stream before writing a byte.

        Opus always codes at 48 kHz whatever its encoder was fed, so this is
        the rate rather than a guess at what the camera captured.
        """
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        body = _drain(muxer, [_video(0), _audio(0)])
        container = av.open(io.BytesIO(body), format="mpegts")
        assert container.streams.audio[0].codec_context.sample_rate == 48000
        container.close()


class TestSynchronisation:
    def test_the_offset_between_tracks_survives(self) -> None:
        """Both tracks share one rebasing offset, which is what keeps them
        together -- the whole reason a single container was chosen over a
        second, audio-only endpoint."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [_video(1000), _audio(1120), _video(1050), _audio(1140)]
        _, times = _demux(_drain(muxer, units))
        # Video rebases the container to zero, so audio keeps the offset the
        # camera reported: 120 ms behind the first picture.
        assert times["audio"] == [120, 140]

    def test_the_container_starts_near_zero(self) -> None:
        """The device clock's epoch is arbitrary and can be very large."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [_video(5_000_000), _audio(5_000_020), _audio(5_000_040)]
        _, times = _demux(_drain(muxer, units))
        assert times["audio"] == [20, 40]


class TestHazards:
    def test_a_sentinel_timestamp_is_dropped_not_muxed(self) -> None:
        """PyAV raises OverflowError on it, and an exception escaping here
        would end the response for every viewer of that camera."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        _, times = _demux(
            _drain(muxer, [_video(0), _audio(0), _audio(SENTINEL_TS), _audio(40)])
        )
        assert muxer.dropped == 1
        assert times["audio"] == [0, 40]

    def test_a_negative_timestamp_is_dropped_too(self) -> None:
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        _drain(muxer, [_video(0), _audio(0), _audio(-1)])
        assert muxer.dropped == 1

    def test_a_repeated_timestamp_does_not_break_the_stream(self) -> None:
        """One repeat is enough on its own: the muxer rejects the packet with
        `ArgumentError 22` and the whole response dies mid-stream. Confirmed by
        removing the backstop, which makes this raise."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        _, times = _demux(_drain(muxer, [_video(0), _audio(0), _audio(20), _audio(20)]))
        assert times["audio"] == [0, 20, 21]

    def test_a_backwards_jump_keeps_the_output_moving_forward(self) -> None:
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [_video(10_000), _audio(10_000), _audio(10_020), _audio(20)]
        _, times = _demux(_drain(muxer, units))
        assert len(times["audio"]) == 3
        assert times["audio"] == sorted(times["audio"])

    def test_a_clock_restart_keeps_the_tracks_together(self) -> None:
        """Both tracks jump on one clock, so re-anchoring the shared offset has
        to preserve their relative offset as well as monotonicity."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [
            _video(10_000),
            _audio(10_120),
            _video(30),  # the device clock restarted
            _audio(150),
        ]
        _, times = _demux(_drain(muxer, units))
        # The camera put 120 ms between the two audio units either side of the
        # restart -- 10120 to 150 on a clock that went back to 30 -- and the
        # container has to preserve that, not the raw difference.
        assert times["audio"][1] - times["audio"][0] == 150 - 30 + 1

    def test_a_forward_jump_is_treated_as_a_restart(self) -> None:
        """A gap larger than any real absence of media is a new clock, and
        carrying it through would put an hour of empty timeline in the
        container."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        _, times = _demux(_drain(muxer, [_video(0), _audio(0), _audio(3_600_000)]))
        assert times["audio"][-1] < 1000

    def test_a_clock_restart_is_counted(self) -> None:
        """The one number that would show a broken audio/video epoch
        assumption on real hardware, so it has to move on a real restart."""
        muxer = StreamMuxer(Codec.H265, AUDIO_CODEC_OPUS)
        units = [
            _video(10_000),
            _audio(10_120),
            _video(30),  # the device clock restarted
            _audio(150),
            _audio(3_600_150),  # and again, forward this time
        ]
        _drain(muxer, units)
        assert muxer.reanchors == 2

    def test_a_muxer_with_no_audio_still_counts_bad_video_stamps(self) -> None:
        muxer = StreamMuxer(Codec.H265, None)
        _drain(muxer, [_video(0), _video(SENTINEL_TS)])
        assert muxer.dropped == 1


@pytest.mark.parametrize("codec", [Codec.H264, Codec.H265])
def test_every_video_codec_the_project_detects_can_be_packaged(codec) -> None:
    """`nal.Codec` and PyAV's codec names are two vocabularies, and a member
    added to one without the other would fail only at runtime."""
    muxer = StreamMuxer(codec, AUDIO_CODEC_OPUS)
    kinds, _ = _demux(_drain(muxer, [_video(0), _audio(0)]))
    assert sorted(kinds) == ["audio", "video"]


class TestTheKeyframeIntervalIsMeasured:
    """How often the camera sends a keyframe, from what it actually sent.

    Nothing declares it. It is a property of whatever firmware is on the
    other end, it varies by model, and the add-on cannot ask -- so a still
    can only be as fresh as this allows, and any decision that trades
    freshness for work has to read it rather than assume it.
    """

    def test_it_is_unknown_before_two_keyframes_have_arrived(self) -> None:
        """One keyframe is a point, not an interval.

        Unknown has to be distinguishable from small: a caller that mistook
        "nothing measured yet" for "arrives constantly" would promise a
        freshness the stream has not been shown to support.
        """
        stats = SessionStats()
        assert stats.keyframe_interval is None
        stats.note_keyframe(at=100.0)
        assert stats.keyframe_interval is None

    def test_it_reports_the_longest_recent_gap_not_the_average(self) -> None:
        """The worst case is what a freshness promise has to be kept against.

        A camera that mostly sends one a second and occasionally takes eight
        would look comfortable on an average and leave a still eight seconds
        stale in practice.
        """
        stats = SessionStats()
        for at in (100.0, 101.0, 102.0, 110.0, 111.0):
            stats.note_keyframe(at=at)
        assert stats.keyframe_interval == 8.0

    def test_it_forgets_gaps_that_no_longer_describe_the_stream(self) -> None:
        """A camera whose rate changes must not be judged on its old one.

        Firmware switches keyframe interval when the resolution or the scene
        changes, and a single long-ago gap would otherwise hold the estimate
        pessimistic for the life of the session.
        """
        stats = SessionStats()
        stats.note_keyframe(at=0.0)
        stats.note_keyframe(at=30.0)
        for index in range(1, 40):
            stats.note_keyframe(at=30.0 + index)
        assert stats.keyframe_interval == 1.0
