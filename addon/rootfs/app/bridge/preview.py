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

from .redact import redact

_LOGGER = logging.getLogger(__name__)

#: JPEG markers. Frames arrive back to back on ffmpeg's stdout with nothing
#: framing them, so they are found by their own delimiters.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

#: The add-on's quality settings, on ffmpeg's scale where lower is better.
#: Named rather than exposed as a number: the scale is inverted, its useful
#: range is not obvious, and nobody should have to know either to make the
#: picture sharper.
_QUALITY = {"low": 12, "balanced": 6, "high": 2}

#: The names a caller may ask for.
QUALITIES = frozenset(_QUALITY)


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

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self._last_request

    async def async_start(self) -> None:
        # -rtsp_transport tcp: the stream is read over loopback, where TCP
        # costs nothing and removes UDP reordering as a source of artefacts.
        # -an: the preview has no use for audio, and decoding it would be
        # spent effort.
        self._process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._url,
            "-an",
            # No filter at all unless a limit was asked for. The camera's own
            # rate is the best available, ffmpeg decodes every frame either
            # way, and dropping some of them afterwards discards work already
            # done rather than saving any.
            *(["-vf", f"fps={self._fps}"] if self._fps else []),
            "-q:v",
            str(_QUALITY[self._quality]),
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._tasks = [
            asyncio.create_task(self._read_frames()),
            asyncio.create_task(self._read_errors()),
        ]

    async def async_stop(self) -> None:
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
        try:
            await asyncio.wait_for(ready.wait(), timeout=_FIRST_FRAME_TIMEOUT)
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
        #: One lock per source, never one for the manager. With a single lock
        #: whatever went wrong for one camera went wrong for all of them: a
        #: teardown that could not finish held it, and every other preview --
        #: plus the refresh loop, which ends by dropping departed cameras --
        #: queued behind it for the lifetime of the process. Locks are kept
        #: rather than reclaimed; there is one per distinct request shape,
        #: which is a small and slow-growing set.
        self._locks: dict[tuple[str, int, str], asyncio.Lock] = {}
        #: Teardowns still running. Referenced only so they are not garbage
        #: collected before they finish -- nothing waits on them.
        self._stopping: set[asyncio.Task] = set()
        self._reaper: asyncio.Task | None = None

    def _lock_for(self, key: tuple[str, int, str]) -> asyncio.Lock:
        """The lock guarding one source's place in the table.

        ``setdefault`` is atomic under asyncio's single thread, so the lookup
        needs no lock of its own.
        """
        return self._locks.setdefault(key, asyncio.Lock())

    async def _retire(self, key: tuple[str, int, str]) -> None:
        """Take a source out of service without waiting for it to stop.

        Removal and shutdown are deliberately separated. Removal is what
        callers care about and is a dictionary operation, so it cannot block.
        Shutdown ends a process, and whether that process's exit can still be
        collected is decided outside this module -- so it is started in the
        background, where taking forever harms nothing. Once removed the
        source is unreachable, and a request arriving afterwards opens a
        fresh one rather than waiting behind the old one's funeral.
        """
        async with self._lock_for(key):
            source = self._sources.pop(key, None)
        if source is None:
            return
        task = asyncio.create_task(source.async_stop())
        self._stopping.add(task)
        task.add_done_callback(self._stopping.discard)

    async def async_frame(
        self, did: str, fps: int, quality: str, after: int = 0
    ) -> tuple[int, bytes]:
        """A picture decoded from what this add-on publishes for ``did``."""
        key = (did, fps, quality)
        async with self._lock_for(key):
            source = self._sources.get(key)
            if source is None:
                source = _Source(did, self._url_for(did), fps, quality)
                await source.async_start()
                self._sources[key] = source
                _LOGGER.info(
                    "Reading %s's published stream for a preview (%s fps, %s)",
                    did,
                    fps or "camera",
                    quality,
                )
        # Outside the lock, and needing none: the check and the assignment
        # have no await between them, so under asyncio's single thread no
        # second caller can interleave and start a rival reaper.
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
            await self._retire(key)

    async def async_drop(self, keep: set[str]) -> None:
        """Stop sources for cameras that no longer exist.

        Called by the refresh loop on every pass, which is why it must not be
        able to block: when it did, the add-on stopped noticing cameras being
        added, removed or switched off, and said nothing about it.
        """
        for key in [k for k in self._sources if k[0] not in keep]:
            await self._retire(key)

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
                    await self._retire(key)
                    _LOGGER.info("Stopped reading %s's stream for preview", key[0])
            except asyncio.CancelledError:
                raise
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.error("Preview cleanup failed: %s", err)
