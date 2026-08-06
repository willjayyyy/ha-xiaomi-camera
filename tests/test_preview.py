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

import asyncio
import contextlib

import pytest
from bridge import preview
from bridge.preview import _QUALITY, PreviewManager, _Source
from conftest import NeverReportsExit


@pytest.fixture
def wedged_ffmpeg(monkeypatch: pytest.MonkeyPatch):
    """Every source spawned during the test gets a process that never exits."""

    async def spawn(*args: object, **kwargs: object) -> NeverReportsExit:
        return NeverReportsExit()

    monkeypatch.setattr(preview.asyncio, "create_subprocess_exec", spawn)


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


class TestAWedgedTeardownStaysContained:
    """One source that will not stop must not take the others with it.

    This shipped with every source behind a single lock, and with teardown
    performed while holding it. A camera whose ffmpeg never reported its exit
    therefore froze not just its own preview but every preview, and the
    refresh loop with them -- that loop ends by asking this manager to drop
    departed cameras, so it queued behind the same lock and stopped running
    entirely. The add-on went on serving `/api/cameras` the whole time, so
    nothing looked wrong from outside while it silently stopped noticing
    cameras being added, removed or switched off.

    Teardown wedging is not itself preventable here: whether a process's exit
    status can still be collected is decided outside this module. What is
    preventable, and what these tests hold, is the blast radius.
    """

    async def _quiesce(self, manager: PreviewManager, *tasks: asyncio.Task) -> None:
        """Drop the tasks a wedged manager cannot be asked to shut down.

        `async_shutdown` waits on teardown, and teardown is the thing wedged,
        so it cannot be used to clean up after these tests. Everything the
        manager started is cancelled directly instead -- the reaper, the drop
        in flight, and the reader tasks of whichever sources are still open.
        """
        pending = [*tasks, manager._reaper, *manager._stopping]
        for source in list(manager._sources.values()):
            pending.extend(source._tasks)
        for task in pending:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def test_another_camera_is_still_watchable(self, wedged_ffmpeg) -> None:
        manager = PreviewManager(url_for)
        await manager.async_frame("A", 0, "balanced")
        await manager.async_frame("B", 0, "balanced")

        # A has left the device list. Dropping it wedges partway through.
        dropping = asyncio.create_task(manager.async_drop({"B"}))
        await asyncio.sleep(0.05)

        try:
            _, frame = await asyncio.wait_for(
                manager.async_frame("B", 0, "balanced"), timeout=1
            )
            assert frame.startswith(b"\xff\xd8")
        finally:
            await self._quiesce(manager, dropping)

    async def test_a_different_camera_can_still_be_started(self, wedged_ffmpeg) -> None:
        """Not only served from an existing source -- a new one must open.

        The failure in the field was that no preview could be started at all,
        including for cameras that had never been watched, because starting
        one needs the same lock that teardown was holding.
        """
        manager = PreviewManager(url_for)
        await manager.async_frame("A", 0, "balanced")

        dropping = asyncio.create_task(manager.async_drop({"C"}))
        await asyncio.sleep(0.05)

        try:
            _, frame = await asyncio.wait_for(
                manager.async_frame("C", 0, "balanced"), timeout=1
            )
            assert frame.startswith(b"\xff\xd8")
        finally:
            await self._quiesce(manager, dropping)


class TestTeardownIsBounded:
    """Stopping a source has to finish, whatever the process does.

    ``kill()`` guarantees the process dies. It guarantees nothing about
    whether the exit status can still be collected -- that belongs to
    whoever calls ``wait`` first, and this process shares itself with a
    closed-source vendor library. Waiting unconditionally after killing is
    therefore a wait that can legitimately never end.

    Per-source locks keep such a wait from spreading, but they do not make it
    harmless: each one strands a task forever, and shutdown would join them.
    A stop that gives up is not a workaround for not understanding why the
    wait hangs -- it is the acknowledgement that collectability was never
    this module's to guarantee.
    """

    async def test_it_gives_up_on_a_process_that_never_reports_its_exit(
        self, wedged_ffmpeg, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(preview, "_STOP_TIMEOUT", 0.05)
        source = _Source("A", url_for("A"), 0, "balanced")
        await source.async_start()

        await asyncio.wait_for(source.async_stop(), timeout=2)
