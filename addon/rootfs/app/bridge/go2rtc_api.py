"""Changing a running go2rtc's streams without restarting it.

Restarting go2rtc disconnects every viewer, which is the wrong price for a
camera changing address in the background or for one setting on one camera.
Its HTTP API can change a stream in place.

This is a delivery mechanism, never a second source of truth. The generated
configuration file states what should be running; these calls make the
running process agree with it sooner than a restart would. When they fail
they say so, and the caller restarts -- which is always correct, only
costlier. go2rtc's own source annotates this API as one it is not sure about
and means to rewrite, so nothing here may become a dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlencode

import aiohttp

from .const import GO2RTC_API_PORT, LOOPBACK
from .redact import safe_error

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class Go2rtcApi:
    """Talks to go2rtc's HTTP API on loopback."""

    def __init__(self, session_factory: Callable[[], object] | None = None) -> None:
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(timeout=_TIMEOUT)
        )

    @property
    def _base(self) -> str:
        return f"http://{LOOPBACK}:{GO2RTC_API_PORT}/api/streams"

    async def set_stream(self, name: str, src: str) -> bool:
        """Point a stream at a source. ``False`` means: restart instead.

        `PATCH` first because its handler changes memory only. `PUT` persists
        through go2rtc's own config writer, which would put a second copy of
        this fact in a file we do not own -- harmless while it agrees, and a
        stale stream for a deleted camera when it does not.
        """
        query = urlencode({"name": name, "src": src})
        if await self._call("PATCH", f"{self._base}?{query}"):
            return True
        return await self._call("PUT", f"{self._base}?{query}")

    async def replace_stream(self, name: str, src: str) -> bool:
        """Delete and recreate a stream, so current viewers redial into it.

        `PATCH`/`PUT` above change the source a stream *resolves to*, but a
        viewer already connected keeps the connection it has: the new source
        only applies to the next dial. That reads as a setting doing nothing
        to anyone already watching. `DELETE` drops those consumers so they
        reconnect, and `PUT` gives the reconnect something to land on --
        `PUT` alone would leave every current viewer on the old source, since
        it does not touch an existing connection either.
        """
        if not await self._call("DELETE", self._delete_url(name)):
            return False
        query = urlencode({"name": name, "src": src})
        return await self._call("PUT", f"{self._base}?{query}")

    async def remove_stream(self, name: str) -> bool:
        return await self._call("DELETE", self._delete_url(name))

    def _delete_url(self, name: str) -> str:
        """The URL for deleting one stream by name.

        Not `?name=<name>`, unlike every other verb here: go2rtc's `DELETE
        /api/streams` handler is `delete(streams, src)` -- it reads the
        stream name out of the `src` query parameter, not `name`. This is
        not a typo carried over from `set_stream`/`replace_stream`; it is
        go2rtc's own inconsistency, in the same handler its author annotated
        "Not sure about all this API. Should be rewrited...". Fixing it here
        to `name=` would silently break stream removal.
        """
        return f"{self._base}?{urlencode({'src': name})}"

    async def _call(self, method: str, url: str) -> bool:
        try:
            async with (
                self._session_factory() as session,
                session.request(method, url) as response,
            ):
                if 200 <= response.status < 300:
                    return True
                _LOGGER.debug("go2rtc %s returned %s", method, response.status)
                return False
        except Exception as err:
            _LOGGER.debug("go2rtc %s failed: %s", method, safe_error(err))
            return False
