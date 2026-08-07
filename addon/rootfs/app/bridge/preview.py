"""Preview pictures, decoded from the RTSP stream this add-on publishes.

The obvious way to build a preview is to use the vendor library's own decoded
JPEG callback -- it is already there, it costs nothing extra, and it is what
snapshots use. It was built that way first, and it was wrong, because it makes
the page a witness to something nobody depends on.

The bridge has two paths out of a camera session:

    on_raw_video -> /api/stream -> go2rtc -> RTSP     what everything consumes
    on_decode_jpg -> snapshots                        a side channel

A preview drawn from the second one keeps producing pictures while the first is
broken. Every component that matters -- the MPEG-TS stream endpoint, go2rtc's
native reading of it, the RTSP server, the credentials guarding it -- can fail
without the page showing anything wrong. A green light wired to a different
circuit is worse than no light.

So the preview reads the published stream instead: one ffmpeg per camera being
watched, pulling RTSP exactly as an NVR would, decoding to JPEG. A picture on
the page is then proof of the whole chain, and its absence is a real failure
rather than a cosmetic one.

The cost is a decode per watched camera, which is why sources are started on
demand and stopped once nobody is looking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from .redact import redact, safe_error

_LOGGER = logging.getLogger(__name__)

#: JPEG markers. Frames arrive back to back on ffmpeg's stdout with nothing
#: framing them, so they are found by their own delimiters.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

#: The tallest picture each setting will produce, in pixels. Resolution is what
#: the setting moves, because resolution is what a viewer sees: on a 4K source
#: the JPEG compression could be loosened all the way and the difference stayed
#: invisible at the size a card draws, while costing 2.6x the bytes. Height
#: alone -- the width follows from the source's own shape, which is not this
#: module's to decide.
#:
#: Read as a ceiling, never a target. A camera sending less than was asked for
#: is passed through at its own size; enlarging it would produce a bigger,
#: blurrier, more expensive picture carrying nothing extra.
_HEIGHT = {"low": 360, "balanced": 480, "high": 720}

#: The compression every preview uses, on ffmpeg's scale where lower is better.
#: Fixed rather than exposed: once the picture is scaled to something a card can
#: show, this knob stops being visible and only moves the byte count.
_JPEG_QUALITY = 5

#: The names a caller may ask for.
QUALITIES = frozenset(_HEIGHT)


#: How long a source keeps running after the last request for it. Long enough
#: that a page refresh reuses it, short enough that a closed tab stops paying.
_IDLE_SECONDS = 15

#: How long to wait for the first picture. RTSP setup plus a keyframe; cameras
#: here send one about every three seconds.
_FIRST_FRAME_TIMEOUT = 20

#: How long a held picture stays servable once frames stop arriving. Past this
#: the preview reports a failure rather than showing a scene that may no longer
#: exist. Generous next to the frame rate: a camera sends a keyframe every few
#: seconds, and a brief gap is not the same as a stream that has stopped.
_MAX_AGE_SECONDS = 6

#: Kept from ffmpeg's diagnostics so a failure can be explained. Its last words
#: are the useful ones -- earlier lines are usually banner and stream details.
_ERROR_TAIL = 1200

#: How long to wait for a stopped ffmpeg to report its exit, applied after the
#: polite signal and again after the fatal one. The second one is the one that
#: matters and the one that was missing: killing a process guarantees it dies,
#: not that its exit status is still there to be collected -- that goes to
#: whoever calls `wait` first, and this process is shared with a closed-source
#: vendor library that runs threads of its own. Waiting without a limit after
#: `kill` is waiting for something that may already have been taken.
_STOP_TIMEOUT = 5


class PreviewError(Exception):
    """A preview could not be produced, with a reason worth showing."""


class _Source:
    """One ffmpeg reading one camera's published stream."""

    def __init__(self, did: str, url: str, fps: int, quality: str) -> None:
        self._did = did
        self._url = url
        self._fps = fps
        self._quality = quality
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._frame: bytes | None = None
        self._frame_at: float = 0.0
        #: Incremented per picture. A viewer names the last one it received so
        #: it can be held until a genuinely newer one exists, which is what
        #: keeps the page's pacing tied to the stream instead of to a timer of
        #: its own.
        self._seq = 0
        #: Replaced, never cleared, on each picture -- see the same pattern in
        #: `bridge.streaming`. With one shared event, a second viewer clearing
        #: it between the first viewer's clear and its wait makes the first
        #: miss the frame it woke for.
        self._ready = asyncio.Event()
        self._error: str = ""
        self._stderr: list[str] = []
        self._last_request = time.monotonic()
        #: Set once this source has been taken out of service. Spawning is not
        #: instant, and a camera can depart during it -- at which point this
        #: object is the only thing that knows a process is on its way. Nobody
        #: else can clean up after it, so it checks this itself.
        self._retired = False

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self._last_request

    def _filters(self) -> str:
        """Everything asked of the picture, as the one chain ffmpeg accepts.

        A second `-vf` does not add to the first: ffmpeg keeps the last one and
        discards the rest without saying so. Whatever is asked of the picture
        has to be joined here, or adding a request silently withdraws one
        already made.

        The size is not optional. A camera at `video_quality: high` sends
        3840x2160, and encoding that whole into JPEG measured 193 Mbps for a
        card a few hundred pixels wide -- a picture finer than anything
        downstream could carry, let alone display.
        """
        chain = []
        if self._fps:
            # Left out entirely at zero, which ffmpeg rejects as a rate. The
            # camera's own is the best available, and dropping frames saves
            # the encoding of them, never the decoding.
            chain.append(f"fps={self._fps}")
        # Quoted because `min` takes a comma, and an unquoted one would read as
        # the end of this filter. `-2` and not `-1` for the width: yuv420p
        # needs both dimensions even, and only the former guarantees it.
        # `ih` makes the height a ceiling rather than a target -- a camera
        # already sending less keeps its own size, since enlarging it would
        # cost more for a picture carrying nothing extra.
        chain.append(f"scale=-2:'min({_HEIGHT[self._quality]},ih)'")
        return ",".join(chain)

    async def async_start(self) -> None:
        # -rtsp_transport tcp: the stream is read over loopback, where TCP
        # costs nothing and removes UDP reordering as a source of artefacts.
        # -an: the preview has no use for audio, and decoding it would be
        # spent effort.
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._url,
            "-an",
            "-vf",
            self._filters(),
            "-q:v",
            str(_JPEG_QUALITY),
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self._retired:
            # Retired while this was spawning. Nothing will ever read from it,
            # and no one else has a reference -- so it either ends here or it
            # runs for as long as the add-on does.
            with contextlib.suppress(Exception):
                process.kill()
            return
        self._process = process
        self._tasks = [
            asyncio.create_task(self._read_frames()),
            asyncio.create_task(self._read_errors()),
        ]

    def fail(self, reason: str) -> None:
        """Wake anyone waiting on a picture that is not coming.

        Opening happens once and is shared by everyone who asks while it is
        in flight, so a failure is learnt by exactly one caller -- the one
        doing the opening. The rest are already waiting on the frame event,
        and nothing will ever set it, because the reader that would have set
        it was never started. Told here instead, they fail at once rather
        than sitting out the full first-frame timeout.

        Deliberately the same shape as the reader's own exit path: put the
        held picture aside, wake the waiters, hand out a fresh event.
        """
        self._error = reason
        self._frame = None
        self._ready.set()
        self._ready = asyncio.Event()

    async def async_stop(self) -> None:
        # First, and unconditionally: a spawn already in flight has no other
        # way to learn that it is no longer wanted.
        self._retired = True
        # Before the process is dealt with, not after: whoever is waiting on
        # a picture from this should hear at once that it has been taken out
        # of service, rather than after however long stopping takes.
        self.fail("This preview was stopped.")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_STOP_TIMEOUT)
        except TimeoutError:
            process.kill()
            # Bounded too. `TimeoutError` is an `Exception`, so a second
            # expiry is swallowed here along with everything else: by this
            # point the process has been sent SIGKILL and there is nothing
            # further this code can do about it either way.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=_STOP_TIMEOUT)

    async def async_frame(self, after: int = 0) -> tuple[int, bytes]:
        """The first picture newer than ``after``, waiting for one if needed.

        Holding the request until a new picture exists is what makes the page
        smooth. The alternative -- answer immediately with whatever is held,
        and let the page wait a fixed interval before asking again -- puts two
        unsynchronised timers in series, and the gap between pictures then
        swings between one frame period and two depending on the phase between
        them. That is visible, and it is what this replaces.
        """
        self._last_request = time.monotonic()

        fresh = (
            self._frame is not None
            and time.monotonic() - self._frame_at <= _MAX_AGE_SECONDS
        )
        if fresh and self._seq > after:
            assert self._frame is not None
            return self._seq, self._frame

        # Either nothing has arrived yet, or the caller already has the latest,
        # or what is held is old enough that showing it would misrepresent the
        # camera.
        ready = self._ready
        # Waiting for the first picture and waiting for the next one are not
        # the same wait. The first has to cover RTSP connecting and a keyframe
        # coming round, which these cameras send about every three seconds.
        # Once a picture has arrived both are settled, so silence after that
        # means something stopped -- and the bound for that is already
        # defined: the age past which a held picture may no longer stand in
        # for the present is the same instant it is fair to say there is
        # none. Using the longer one left a viewer watching a frozen frame
        # for twenty seconds after a camera was switched off.
        patience = _FIRST_FRAME_TIMEOUT if self._frame is None else _MAX_AGE_SECONDS
        try:
            await asyncio.wait_for(ready.wait(), timeout=patience)
        except TimeoutError:
            raise PreviewError(self._explain()) from None
        if self._frame is None:
            raise PreviewError(self._explain())
        return self._seq, self._frame

    def _explain(self) -> str:
        """Why no picture arrived, in terms the page can show."""
        if self._error:
            return self._error
        return (
            "No video arrived from the published stream within "
            f"{_FIRST_FRAME_TIMEOUT}s. The camera may be switched off, or the "
            "stream may not be reaching go2rtc."
        )

    async def _read_frames(self) -> None:
        """Split ffmpeg's output into whole JPEGs."""
        stream = self._process.stdout if self._process else None
        if stream is None:
            return
        buffer = bytearray()
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                # ffmpeg exited. Its diagnostics carry the reason, and are
                # already being collected.
                self._error = self._error or self._collected_stderr()
                self._frame = None
                self._ready.set()
                self._ready = asyncio.Event()
                return
            buffer += chunk
            while True:
                start = buffer.find(_SOI)
                if start < 0:
                    buffer.clear()
                    break
                end = buffer.find(_EOI, start + 2)
                if end < 0:
                    # Keep the partial frame; the rest is still coming.
                    del buffer[:start]
                    break
                self._frame = bytes(buffer[start : end + 2])
                self._frame_at = time.monotonic()
                self._seq += 1
                self._error = ""
                # Wake everyone waiting on this picture, then hand out a fresh
                # event for the next. No `await` runs in between, so nobody can
                # arrive between the two and miss a wake-up.
                self._ready.set()
                self._ready = asyncio.Event()
                del buffer[: end + 2]

    async def _read_errors(self) -> None:
        stream = self._process.stderr if self._process else None
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            # Scrubbed before it is stored, not before it is shown. ffmpeg
            # quotes the URL it was given -- which carries the RTSP password
            # when the streams are published -- in most of its diagnostics,
            # and these lines end up in the log and on the add-on page.
            text = redact(line.decode(errors="replace").strip())
            if not text:
                continue
            _LOGGER.debug("preview ffmpeg [%s]: %s", self._did, text)
            self._stderr.append(text)
            del self._stderr[:-10]

    def _collected_stderr(self) -> str:
        return " / ".join(self._stderr)[-_ERROR_TAIL:]


class PreviewManager:
    """Starts and stops preview sources as the page asks for them."""

    def __init__(self, url_for) -> None:
        #: Given a device id, returns the RTSP URL to read -- credentials
        #: included when the published stream requires them. That URL never
        #: leaves this process.
        self._url_for = url_for
        #: Keyed by what was asked for, not only by camera: two people looking
        #: at the same camera may want different frame rates, and neither
        #: should silently get the other's.
        self._sources: dict[tuple[str, int, str], _Source] = {}
        #: Teardowns still running. Referenced only so they are not garbage
        #: collected before they finish -- nothing waits on them.
        self._closing: set[asyncio.Task] = set()
        self._reaper: asyncio.Task | None = None

    def _retire(self, key: tuple[str, int, str]) -> None:
        """Take a source out of service. Cannot block, by construction.

        There is deliberately no lock here. The table is a dict mutated from
        a single thread, so its own operations are already indivisible; the
        only thing that ever needed guarding was that one key must not be
        opened twice, and that is settled in :meth:`async_frame` by seating
        the source in the table before starting it rather than after.

        Removing and shutting down are separated for the same reason. Removal
        is what every caller actually wants and is a dictionary operation, so
        it is synchronous and cannot fail to finish. Shutdown ends a process,
        and whether a process's exit can still be collected is decided
        outside this module -- so it runs in the background, where taking
        forever costs nothing. The add-on once queued the refresh loop and
        every preview behind exactly that wait.
        """
        source = self._sources.pop(key, None)
        if source is None:
            return
        task = asyncio.create_task(source.async_stop())
        self._closing.add(task)
        task.add_done_callback(self._forget_closing)

    def _forget_closing(self, task: asyncio.Task) -> None:
        """Release a finished teardown, and never let its failure go unseen.

        Nothing awaits these, and an asyncio task whose exception is never
        retrieved reports itself only when the garbage collector reaches it --
        late, and detached from whatever caused it. Moving teardown off the
        request path is what keeps previews working through it; it must not
        also be what makes it fail silently.
        """
        self._closing.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.warning("Could not stop a preview decoder: %s", safe_error(error))

    async def async_frame(
        self, did: str, fps: int, quality: str, after: int = 0
    ) -> tuple[int, bytes]:
        """A picture decoded from what this add-on publishes for ``did``."""
        key = (did, fps, quality)
        source = self._sources.get(key)
        if source is None:
            source = _Source(did, self._url_for(did), fps, quality)
            # Seated before the spawn, not after. This is what makes a second
            # caller share this source instead of opening a rival one -- the
            # job a lock used to do -- and it is what lets a departing camera
            # find the source at all while it is still being opened.
            self._sources[key] = source
            try:
                await source.async_start()
            except BaseException as err:
                # A source that failed to open must not stay in the table:
                # every later caller would wait out the timeout for frames
                # that can never arrive. Left alone if something has already
                # replaced or retired it, which is not ours to undo.
                if self._sources.get(key) is source:
                    del self._sources[key]
                # The raise tells only this caller. Anyone who joined while
                # the open was in flight is waiting on the source itself.
                source.fail(safe_error(err))
                raise
            if self._sources.get(key) is not source:
                # Retired while it was opening -- the camera left the account
                # in between. The source has already ended the process it
                # spawned, so there is nothing to read and nothing to
                # announce; saying otherwise would put a line in the log
                # claiming a stream is being read when none is.
                raise PreviewError(
                    "The camera was removed while its preview was opening."
                )
            _LOGGER.info(
                "Reading %s's published stream for a preview (%s fps, %s)",
                did,
                fps or "camera",
                quality,
            )
        # No lock needed: the check and the assignment have no await between
        # them, so under asyncio's single thread no second caller can
        # interleave and start a rival reaper.
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap())
        return await source.async_frame(after)

    async def async_shutdown(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        for key in list(self._sources):
            self._retire(key)
        if self._closing:
            # The one place that does wait for teardown, because on the way
            # out there is nothing left to protect the processes from being
            # orphaned. Bounded at what a teardown can take at worst -- two
            # waits of `_STOP_TIMEOUT` -- so a decoder that will not go still
            # cannot be the reason the add-on fails to exit on its own.
            await asyncio.wait(self._closing, timeout=_STOP_TIMEOUT * 2 + 1)

    def drop(self, keep: set[str]) -> None:
        """Stop sources for cameras that no longer exist.

        Synchronous, and that is the point rather than an implementation
        detail: the refresh loop ends by calling this, and a signature with
        nothing to await cannot become the thing that stops it running. When
        it could, the add-on quietly stopped noticing cameras being added,
        removed or switched off -- for the lifetime of the process, with
        `/api/cameras` answering normally from another path the whole time,
        so nothing looked wrong.
        """
        for key in [k for k in self._sources if k[0] not in keep]:
            self._retire(key)

    async def _reap(self) -> None:
        """Stop sources nobody is watching.

        A preview costs a decode for as long as it runs, so it has to end on
        its own: the page has no reliable way to say "I have gone away", and a
        closed tab would otherwise keep a camera decoding indefinitely.
        """
        while True:
            try:
                await asyncio.sleep(_IDLE_SECONDS / 3)
                for key in [
                    k
                    for k, source in self._sources.items()
                    if source.idle_for > _IDLE_SECONDS
                ]:
                    self._retire(key)
                    _LOGGER.info("Stopped reading %s's stream for preview", key[0])
            except asyncio.CancelledError:
                raise
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.error("Preview cleanup failed: %s", err)
