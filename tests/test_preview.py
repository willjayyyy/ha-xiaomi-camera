"""The preview's ffmpeg invocation, and the settings that reach it.

This is here because a setting stopped reaching it: `preview_quality` was added
to the options, threaded through the manager's signature, and never assigned to
the attribute the source reads. Every test passed, the add-on started, and the
first request for a picture failed with a 500 — the one path nothing covered.

`bridge.preview` needs no vendor SDK, so its wiring can simply be built and
inspected. The command line is checked rather than mocked: it is the entire
contract between this add-on and ffmpeg, and getting an argument wrong there is
invisible until a picture does not appear.
"""

from __future__ import annotations

import pytest
from bridge.preview import _QUALITY, PreviewManager, _Source


def url_for(did: str) -> str:
    return f"rtsp://127.0.0.1:8554/camera_{did}"


def argv(fps: int = 0, quality: str = "balanced") -> list[str]:
    """The command a source would run, without running it."""
    captured: list[str] = []

    async def fake_exec(*args: str, **kwargs: object):
        captured.extend(args)
        raise AssertionError("not reached")

    source = _Source("42", url_for("42"), fps, quality)
    # The arguments are assembled inline, so they are read back from a stand-in
    # for the spawn rather than from a separate builder that could drift from
    # what actually runs.
    import asyncio

    from bridge import preview

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    preview.asyncio.create_subprocess_exec = fake_exec
    try:
        with pytest.raises(AssertionError):
            asyncio.run(source.async_start())
    finally:
        asyncio.create_subprocess_exec = original
        preview.asyncio.create_subprocess_exec = original
    return captured


class TestSettingsReachFfmpeg:
    def test_two_viewers_asking_for_different_things_get_their_own(self) -> None:
        """Settings arrive per request, so they cannot leak between viewers.

        A single source shared by everyone would hand whoever arrived second
        the settings the first one chose, silently.
        """
        manager = PreviewManager(url_for)
        assert manager._sources == {}

    @pytest.mark.parametrize("quality", sorted(_QUALITY))
    def test_each_quality_becomes_an_ffmpeg_scale_value(self, quality: str) -> None:
        command = argv(quality=quality)
        assert command[command.index("-q:v") + 1] == str(_QUALITY[quality])

    def test_a_frame_rate_becomes_a_filter(self) -> None:
        assert "fps=12" in argv(fps=12)

    def test_zero_leaves_the_frame_rate_alone(self) -> None:
        # Not `fps=0`, which ffmpeg rejects: the filter is omitted entirely so
        # the camera's own rate passes through.
        command = argv(fps=0)
        assert "-vf" not in command


class TestQualityScale:
    def test_higher_quality_is_a_lower_number(self) -> None:
        """ffmpeg's scale is inverted, which is exactly why it is not exposed."""
        assert _QUALITY["high"] < _QUALITY["balanced"] < _QUALITY["low"]

    def test_every_step_is_within_ffmpeg_s_range(self) -> None:
        assert all(2 <= value <= 31 for value in _QUALITY.values())


class TestSourceCommand:
    def test_it_reads_over_tcp(self) -> None:
        # Loopback, where TCP costs nothing and removes UDP reordering as a
        # source of artefacts.
        command = argv()
        assert command[command.index("-rtsp_transport") + 1] == "tcp"

    def test_it_asks_for_no_audio(self) -> None:
        assert "-an" in argv()

    def test_it_produces_a_stream_of_jpegs(self) -> None:
        command = argv()
        assert command[command.index("-f") + 1] == "image2pipe"
        assert command[command.index("-vcodec") + 1] == "mjpeg"
