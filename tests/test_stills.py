"""The stills ffmpeg invocation, and the settings that reach it.

This is here because a setting stopped reaching it: `preview_quality` was added
to the options, threaded through the manager's signature, and never assigned to
the attribute the source reads. Every test passed, the add-on started, and the
first request for a picture failed with a 500 — the one path nothing covered.

`bridge.stills` needs no vendor SDK, so its wiring can simply be built and
inspected. The command line is checked rather than mocked: it is the entire
contract between this add-on and ffmpeg, and getting an argument wrong there is
invisible until a picture does not appear.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

import pytest
from bridge import stills
from bridge.stills import (
    _HEIGHT,
    _JPEG_QUALITY,
    Stills,
    StillsError,
    _Source,
    keyframes_suffice,
)
from conftest import NeverReportsExit


@pytest.fixture
def wedged_ffmpeg(monkeypatch: pytest.MonkeyPatch):
    """Every source spawned during the test gets a process that never exits."""

    async def spawn(*args: object, **kwargs: object) -> NeverReportsExit:
        return NeverReportsExit()

    monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", spawn)


def url_for(did: str) -> str:
    return f"rtsp://127.0.0.1:8554/camera_{did}"


def argv(
    fps: int = 0,
    quality: str = "medium",
    keyframes_only: bool = False,
) -> list[str]:
    """The command a source would run, without running it."""
    captured: list[str] = []

    async def fake_exec(*args: str, **kwargs: object):
        captured.extend(args)
        raise AssertionError("not reached")

    height = None if quality is None else _HEIGHT[quality]
    source = _Source("42", url_for("42"), fps, height, keyframes_only)
    # The arguments are assembled inline, so they are read back from a stand-in
    # for the spawn rather than from a separate builder that could drift from
    # what actually runs.
    import asyncio

    from bridge import stills

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = fake_exec
    stills.asyncio.create_subprocess_exec = fake_exec
    try:
        with pytest.raises(AssertionError):
            asyncio.run(source.async_start())
    finally:
        asyncio.create_subprocess_exec = original
        stills.asyncio.create_subprocess_exec = original
    return captured


def filters(command: list[str]) -> str:
    """The one `-vf` argument, insisting there is exactly one.

    ffmpeg does not merge repeated `-vf` flags -- it silently keeps the last
    and discards the rest. Everything the preview asks of the picture has to
    arrive in a single comma-separated chain, so this refuses to read a
    command where a second one could have thrown the first away.
    """
    assert command.count("-vf") == 1, f"-vf appears {command.count('-vf')}x"
    return command[command.index("-vf") + 1]


class TestSettingsReachFfmpeg:
    def test_two_viewers_asking_for_different_things_get_their_own(self) -> None:
        """Settings arrive per request, so they cannot leak between viewers.

        A single source shared by everyone would hand whoever arrived second
        the settings the first one chose, silently.
        """
        manager = Stills(url_for)
        assert manager._sources == {}

    @pytest.mark.parametrize("quality", sorted(_HEIGHT))
    def test_each_quality_becomes_a_height(self, quality: str) -> None:
        assert f"min({_HEIGHT[quality]}," in filters(argv(quality=quality)).replace(
            " ", ""
        )

    def test_a_frame_rate_becomes_a_filter(self) -> None:
        assert "fps=12" in filters(argv(fps=12))

    def test_zero_leaves_the_frame_rate_alone(self) -> None:
        # Not `fps=0`, which ffmpeg rejects: the rate is left out so the
        # camera's own passes through. The size is still asked for -- no rate
        # limit is not the same as no filtering.
        value = filters(argv(fps=0))
        assert "fps=" not in value
        assert "scale=" in value


class TestThePictureIsScaledDown:
    """What the camera sends is not what a card on the page can use.

    At `video_quality: high` these cameras send 3840x2160. Encoding that
    straight to JPEG measured 193 Mbps for a card a few hundred pixels wide,
    and nothing downstream could keep up -- the page stuttered while the
    encoder was producing a picture far finer than it could ever display.
    Scaling to 360 brought the same stream to 9.1 Mbps and cut a core's worth
    of work to a third of it.
    """

    def test_a_frame_rate_and_a_size_share_one_filter(self) -> None:
        """Both requests have to arrive as one argument.

        The rate limit was here first. Adding the size as a second `-vf` would
        drop it without a word, and the preview would quietly go back to
        encoding at the camera's full rate.
        """
        value = filters(argv(fps=12, quality="low"))
        assert "fps=12" in value
        assert "scale=" in value

    def test_it_never_enlarges_a_smaller_camera(self) -> None:
        """`video_quality: low` sends 848x480; asking for 720 must not stretch.

        The height is bounded by the source's own, so a camera that already
        sends less than was asked for is passed through at its own size rather
        than blown up into a bigger, blurrier, more expensive picture.
        """
        value = filters(argv(quality="high")).replace(" ", "")
        assert "ih" in value, "the height must be bounded by the source's own"
        assert "min(720,ih)" in value

    def test_the_width_is_left_even(self) -> None:
        """`yuv420p` needs both dimensions even; `-1` can produce an odd one."""
        assert "scale=-2:" in filters(argv()).replace(" ", "")


class TestJpegCompression:
    @pytest.mark.parametrize("quality", sorted(_HEIGHT))
    def test_compression_no_longer_follows_the_knob(self, quality: str) -> None:
        """The knob moves resolution now, because that is what a viewer sees.

        Measured on a 4K source: `-q:v 2` against `-q:v 12` is 2.6x the bytes
        for a difference invisible at the size a card draws. Resolution is
        what decides whether the picture looks sharp, so that is what the
        setting was given.
        """
        command = argv(quality=quality)
        assert command[command.index("-q:v") + 1] == str(_JPEG_QUALITY)

    def test_it_is_within_ffmpeg_s_range(self) -> None:
        assert 2 <= _JPEG_QUALITY <= 31

    def test_every_height_is_even(self) -> None:
        """`yuv420p` again: an odd target height cannot be encoded."""
        assert all(height % 2 == 0 for height in _HEIGHT.values())

    def test_higher_quality_is_a_taller_picture(self) -> None:
        assert _HEIGHT["low"] < _HEIGHT["medium"] < _HEIGHT["high"]


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

    async def _quiesce(self, manager: Stills, *tasks: asyncio.Task) -> None:
        """Drop the tasks a wedged manager cannot be asked to shut down.

        `async_shutdown` waits on teardown, and teardown is the thing wedged,
        so it cannot be used to clean up after these tests. Everything the
        manager started is cancelled directly instead -- the reaper, the drop
        in flight, and the reader tasks of whichever sources are still open.
        """
        pending = [*tasks, manager._reaper, *manager._closing]
        for source in list(manager._sources.values()):
            pending.extend(source._tasks)
        for task in pending:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def test_another_camera_is_still_watchable(self, wedged_ffmpeg) -> None:
        manager = Stills(url_for)
        await manager.async_frame("A", 0, "medium")
        await manager.async_frame("B", 0, "medium")

        # A has left the device list. Its teardown will wedge, in the
        # background, where dropping does not wait for it.
        manager.drop({"B"})
        await asyncio.sleep(0.05)

        try:
            _, frame = await asyncio.wait_for(
                manager.async_frame("B", 0, "medium"), timeout=1
            )
            assert frame.startswith(b"\xff\xd8")
        finally:
            await self._quiesce(manager)

    async def test_a_different_camera_can_still_be_started(self, wedged_ffmpeg) -> None:
        """Not only served from an existing source -- a new one must open.

        The failure in the field was that no preview could be started at all,
        including for cameras that had never been watched, because starting
        one needed the same lock that teardown was holding.
        """
        manager = Stills(url_for)
        await manager.async_frame("A", 0, "medium")

        manager.drop({"C"})
        await asyncio.sleep(0.05)

        try:
            _, frame = await asyncio.wait_for(
                manager.async_frame("C", 0, "medium"), timeout=1
            )
            assert frame.startswith(b"\xff\xd8")
        finally:
            await self._quiesce(manager)


class TestDroppingCannotBeBlocked:
    """Dropping departed cameras must not be able to wait on anything.

    The refresh loop ends by calling it, and that loop is how the add-on
    notices cameras appearing, disappearing and being switched off. When it
    stopped running, the add-on went on answering `/api/cameras` from a
    different path and so went on looking healthy while it had in fact gone
    blind -- for the lifetime of the process, with nothing logged.

    Guarding the source table with a lock put every one of those things
    behind whatever the table's slowest operation happened to be. The table
    is a dict, mutated from a single thread, and needs no lock at all; what
    actually needed guarding was only that one key cannot be opened twice,
    which is settled by putting the source in the table before starting it.

    That `drop` is now synchronous is what makes this class's premise
    structural rather than something to keep testing for -- a signature with
    nothing to await cannot stall. What is left worth asserting is that it
    still empties the table when the sources in it are wedged, in either of
    the two slow things a source does: starting, and stopping.
    """

    async def _quiesce(self, manager: Stills, *tasks: asyncio.Task) -> None:
        pending = [*tasks, manager._reaper, *manager._closing]
        for source in list(manager._sources.values()):
            pending.extend(source._tasks)
        for task in pending:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def test_it_empties_the_table_when_every_teardown_wedges(
        self, wedged_ffmpeg
    ) -> None:
        """The shape of the failure that shipped: all of them leaving at once."""
        manager = Stills(url_for)
        await manager.async_frame("A", 0, "medium")
        await manager.async_frame("B", 0, "medium")

        try:
            manager.drop(set())
            assert manager._sources == {}
        finally:
            await self._quiesce(manager)

    async def test_it_empties_the_table_when_a_start_never_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A source still being opened is in the table, and so can be dropped.

        Seating it before the spawn rather than after is what puts it within
        reach: opened-after-the-fact, a camera departing mid-spawn left a
        source that no later pass would look for, because the camera it
        belonged to was no longer in any list.
        """
        spawning = asyncio.Event()

        async def never_finishes(*args: object, **kwargs: object) -> object:
            spawning.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")  # pragma: no cover

        monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", never_finishes)

        manager = Stills(url_for)
        watching = asyncio.create_task(manager.async_frame("A", 0, "medium"))
        await asyncio.wait_for(spawning.wait(), timeout=1)

        try:
            manager.drop({"B"})
            assert manager._sources == {}
        finally:
            await self._quiesce(manager, watching)


class TestASourceStartingWhenItsCameraLeaves:
    """A camera can depart while its preview is still being opened.

    Starting is not instant -- a process is spawned -- and a refresh can land
    in that window. Whichever order the two arrive in, what must not survive
    is a source for a camera that is gone: it holds an ffmpeg reading a stream
    nobody will ever look at, and nothing later goes looking for it because
    the camera it belonged to is no longer in any list.
    """

    @pytest.fixture
    def held_spawn(self, monkeypatch: pytest.MonkeyPatch):
        """A spawn that hangs until released, then yields a live process."""
        entered = asyncio.Event()
        release = asyncio.Event()
        process = NeverReportsExit()

        async def spawn(*args: object, **kwargs: object) -> NeverReportsExit:
            entered.set()
            await release.wait()
            return process

        monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", spawn)
        return SimpleNamespace(entered=entered, release=release, process=process)

    async def _run(self, held_spawn) -> tuple[Stills, asyncio.Task]:
        manager = Stills(url_for)
        watching = asyncio.create_task(manager.async_frame("A", 0, "medium"))
        await asyncio.wait_for(held_spawn.entered.wait(), timeout=1)
        manager.drop({"B"})  # A departs, mid-spawn
        held_spawn.release.set()  # the spawn now completes
        await asyncio.sleep(0.05)
        return manager, watching

    async def _quiesce(self, manager: Stills, watching: asyncio.Task) -> None:
        pending = [watching, manager._reaper, *manager._closing]
        for source in list(manager._sources.values()):
            pending.extend(source._tasks)
        for task in pending:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def test_the_departed_camera_leaves_no_source_behind(
        self, held_spawn
    ) -> None:
        manager, watching = await self._run(held_spawn)
        try:
            assert manager._sources == {}
        finally:
            await self._quiesce(manager, watching)

    async def test_the_abandoned_process_is_not_left_running(self, held_spawn) -> None:
        manager, watching = await self._run(held_spawn)
        try:
            assert held_spawn.process.killed
        finally:
            await self._quiesce(manager, watching)


class TestOpeningIsSharedHonestly:
    """Seating a source before starting it is what removed the lock.

    It also means a second viewer can reach a source that is not open yet,
    and that whoever opens it may find, on finishing, that the camera has
    since departed. Both are new states the lock used to make unreachable,
    and neither may be answered with a wait that runs to the full
    first-frame timeout: nothing is coming, and the caller can be told so.
    """

    async def test_a_failed_open_does_not_leave_a_second_viewer_waiting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def failing_spawn(*args: object, **kwargs: object) -> object:
            entered.set()
            await release.wait()
            raise OSError("ffmpeg is not installed")

        monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", failing_spawn)

        manager = Stills(url_for)
        first = asyncio.create_task(manager.async_frame("A", 0, "medium"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = asyncio.create_task(manager.async_frame("A", 0, "medium"))
        await asyncio.sleep(0.05)  # let the second one reach the wait
        release.set()

        try:
            with pytest.raises(OSError):
                await first
            # Not the 20s first-frame timeout: the picture is already known
            # not to be coming.
            with pytest.raises(StillsError):
                await asyncio.wait_for(second, timeout=1)
        finally:
            for task in (first, second, manager._reaper):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def test_a_camera_leaving_mid_open_is_not_announced_as_readable(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_spawn(*args: object, **kwargs: object) -> NeverReportsExit:
            entered.set()
            await release.wait()
            return NeverReportsExit()

        monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", held_spawn)

        manager = Stills(url_for)
        watching = asyncio.create_task(manager.async_frame("A", 0, "medium"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        manager.drop({"B"})  # A departs while it is being opened
        release.set()

        try:
            with pytest.raises(StillsError):
                await asyncio.wait_for(watching, timeout=1)
            assert "Reading A's published stream" not in caplog.text
        finally:
            for task in (watching, manager._reaper, *manager._closing):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task


class TestATeardownFailureIsNotSwallowed:
    """Teardown runs unwatched, so its failures have to be gone looking for.

    Nothing awaits the background task, and an asyncio task whose exception
    is never retrieved reports itself only when the garbage collector reaches
    it -- late, detached from what caused it, and easy to never see at all.
    Moving teardown off the request path is what made previews survive it; it
    must not also be what makes its failures invisible.
    """

    async def test_it_says_so_when_a_decoder_cannot_be_stopped(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        class _RefusesToDie(NeverReportsExit):
            def terminate(self) -> None:
                raise ProcessLookupError("vanished between the check and the signal")

        async def spawn(*args: object, **kwargs: object) -> _RefusesToDie:
            return _RefusesToDie()

        monkeypatch.setattr(stills.asyncio, "create_subprocess_exec", spawn)

        manager = Stills(url_for)
        await manager.async_frame("A", 0, "medium")

        try:
            manager.drop(set())
            await asyncio.sleep(0.05)
            assert "vanished between the check and the signal" in caplog.text
        finally:
            if manager._reaper is not None:
                manager._reaper.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await manager._reaper


class TestShutdownCannotHangTheAddOn:
    """Shutting down has to finish even when a decoder will not stop.

    This is the one place that does wait for teardown, because on the way out
    there is nothing left to keep the processes from being orphaned. It is
    also the worst place to wait forever: the add-on would never exit on its
    own, and Supervisor would eventually kill it after its grace period --
    slower and noisier than stopping properly, and with no explanation.
    """

    async def test_it_gives_up_on_teardowns_that_will_not_finish(
        self, wedged_ffmpeg, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teardown is replaced wholesale, and that is the point.

        A wedged ffmpeg is not enough to exercise this: stopping one is
        already bounded from the inside, so shutdown would return on time
        whether or not it imposed a limit of its own -- a guard that cannot
        fail is not a guard. Standing in a teardown that never returns at
        all is what leaves only this method's own limit to end the wait.
        """

        async def never_stops(self: _Source) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(stills._Source, "async_stop", never_stops)
        monkeypatch.setattr(stills, "_STOP_TIMEOUT", 0.05)

        manager = Stills(url_for)
        await manager.async_frame("A", 0, "medium")
        await manager.async_frame("B", 0, "medium")
        # Held now, because shutting down empties the table -- and with the
        # real teardown replaced, nothing else will ever cancel the readers
        # these sources started.
        opened = list(manager._sources.values())

        try:
            await asyncio.wait_for(manager.async_shutdown(), timeout=2)
            assert manager._sources == {}
        finally:
            pending = list(manager._closing)
            for source in opened:
                pending.extend(source._tasks)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


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
        monkeypatch.setattr(stills, "_STOP_TIMEOUT", 0.05)
        source = _Source("A", url_for("A"), 0, 480)
        await source.async_start()

        await asyncio.wait_for(source.async_stop(), timeout=2)


class TestAStoppedStreamIsReportedSooner:
    """Waiting for the first picture and waiting for the next are different.

    Twenty seconds is right for the first: RTSP has to connect and a keyframe
    has to come round, and these cameras send one about every three seconds.
    It is wrong for the next one. A source that has already produced a picture
    has proved the connection and the keyframe; if nothing follows, something
    has stopped, and making the viewer watch a frozen frame for twenty seconds
    before saying so is the behaviour this replaces.

    The bound used is the one that already says how long a held picture may
    stand in for the present. Past it the picture is too old to show -- which
    is the same instant it is fair to say there is no picture.
    """

    async def test_a_source_that_has_shown_a_picture_gives_up_sooner(
        self, wedged_ffmpeg, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(stills, "_FIRST_FRAME_TIMEOUT", 10)
        monkeypatch.setattr(stills, "_MAX_AGE_SECONDS", 0.2)

        source = _Source("A", url_for("A"), 0, 480)
        await source.async_start()
        try:
            seq, frame = await asyncio.wait_for(source.async_frame(0), timeout=2)
            assert frame.startswith(b"\xff\xd8")

            # The stream has stopped: no frame will ever follow this one.
            with pytest.raises(StillsError):
                await asyncio.wait_for(source.async_frame(seq), timeout=3)
        finally:
            for task in source._tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


class TestAStillDecodesOnlyWhatItNeeds:
    """A dashboard thumbnail does not need every frame decoded for it.

    The camera sends around twenty frames a second; a still is asked for
    every ten. Decoding the ones in between produces pictures nobody reads,
    and measured against a 4K camera that was 66.6% of a core against 4.6%.

    An inter-frame codec offers no middle ground: a frame can only be decoded
    from the keyframe before it, so the choice is keyframes alone or all of
    them. Which is correct depends on how often this camera sends one, so it
    is measured rather than assumed -- a stream with a long keyframe interval
    still gets a full decode, because the freshness was promised.
    """

    def test_keyframes_only_skips_the_frames_between(self) -> None:
        command = argv(keyframes_only=True)
        assert command.count("-skip_frame") == 1
        assert command[command.index("-skip_frame") + 1] == "nokey"

    def test_the_skip_is_applied_to_the_input(self) -> None:
        """It is a decoder setting, so after `-i` it would configure nothing.

        ffmpeg accepts it in either position without complaint; in the wrong
        one it simply has no effect, and the only visible symptom is a cost
        that never went down.
        """
        command = argv(keyframes_only=True)
        assert command.index("-skip_frame") < command.index("-i")

    def test_the_frame_rate_is_left_to_the_stream(self) -> None:
        """`-vsync 0` keeps ffmpeg from padding the gaps back out.

        Without it ffmpeg pads a sparse input up to a constant rate by
        repeating pictures, which would undo the saving and emit the same
        still many times over.
        """
        command = argv(keyframes_only=True)
        assert command[command.index("-vsync") + 1] == "0"

    def test_a_preview_still_decodes_everything(self) -> None:
        """The saving belongs to stills; a preview needs every frame."""
        assert "-skip_frame" not in argv()


class TestWhichDecodeAStillCanAfford:
    """Measured, never assumed. This is the whole point of the change.

    Keyframes alone are cheap but only arrive as often as the camera sends
    them, which is firmware's business and differs by model. Deciding from
    the cameras that happened to be on the developer's desk would leave every
    other camera either paying for frames nobody reads or handing out stills
    older than it promised.
    """

    def test_a_stream_whose_keyframes_are_frequent_enough_takes_the_cheap_path(
        self,
    ) -> None:
        assert keyframes_suffice(interval=2.9, max_age=3.0) is True

    def test_the_boundary_counts_as_sufficient(self) -> None:
        """Equal meets the promise: a still is at most `max_age` old."""
        assert keyframes_suffice(interval=3.0, max_age=3.0) is True

    def test_a_long_interval_keeps_the_full_decode(self) -> None:
        """A ten-second keyframe cannot serve a three-second promise.

        This camera pays what it paid before rather than being handed a
        cheaper stale picture -- no camera ends up worse off than it was.
        """
        assert keyframes_suffice(interval=10.0, max_age=3.0) is False

    def test_an_unmeasured_stream_keeps_the_full_decode(self) -> None:
        """Nothing measured is not evidence of a short interval.

        The safe reading is the expensive one: it is what the add-on did
        before, so a session too young to have been measured behaves exactly
        as it used to and settles onto the cheap path once it has.
        """
        assert keyframes_suffice(interval=None, max_age=3.0) is False


class TestTheModeTravelsInTheSourceKey:
    async def test_two_modes_are_two_sources(self, wedged_ffmpeg) -> None:
        """So a stream that changes its rate is re-judged, with no state.

        The mode is derived per request and put in the key, which is the same
        mechanism that already keeps two viewers on different frame rates
        apart. A source whose mode no longer matches simply stops being asked
        for and the reaper collects it -- there is no escalation path to get
        wrong.
        """
        intervals = {"A": 1.0}
        manager = Stills(url_for, lambda did: intervals.get(did))
        with contextlib.suppress(StillsError, TimeoutError):
            await asyncio.wait_for(manager.async_still("A", max_age=3.0), 0.2)
        intervals["A"] = 30.0
        with contextlib.suppress(StillsError, TimeoutError):
            await asyncio.wait_for(manager.async_still("A", max_age=3.0), 0.2)
        modes = {key[-1] for key in manager._sources}
        await manager.async_shutdown()
        assert modes == {True, False}


class TestAStillIsAsFreshAsItWasAskedFor:
    """The age a caller asks for is the age it gets, not the module's own.

    A source holds its last picture and hands it straight back when it is
    fresh enough. What counts as fresh enough belongs to the caller: a
    dashboard tile accepts a few seconds, and the whole decision about
    decoding keyframes alone was made against that number. Checking a
    different one here would quietly hand back a picture older than the
    promise that justified the cheap decode.
    """

    async def test_a_held_picture_older_than_asked_for_is_not_returned(
        self, wedged_ffmpeg
    ) -> None:
        manager = Stills(url_for, lambda did: 1.0)
        source = _Source("A", url_for("A"), 0, None, True)
        manager._sources[("A", 0, None, True)] = source
        source._frame = b"\xff\xd8old\xff\xd9"
        source._seq = 1
        source._frame_at = time.monotonic() - 4.0

        with pytest.raises((StillsError, TimeoutError)):
            await asyncio.wait_for(manager.async_still("A", max_age=3.0), 0.3)
        await manager.async_shutdown()

    async def test_a_held_picture_within_the_age_is_returned_at_once(
        self, wedged_ffmpeg
    ) -> None:
        manager = Stills(url_for, lambda did: 1.0)
        source = _Source("A", url_for("A"), 0, None, True)
        manager._sources[("A", 0, None, True)] = source
        source._frame = b"\xff\xd8fresh\xff\xd9"
        source._seq = 1
        source._frame_at = time.monotonic() - 1.0

        assert await manager.async_still("A", max_age=3.0) == b"\xff\xd8fresh\xff\xd9"
        await manager.async_shutdown()
