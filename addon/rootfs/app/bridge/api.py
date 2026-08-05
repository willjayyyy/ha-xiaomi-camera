"""HTTP surfaces.

Two listeners with deliberately different exposure:

* **Control plane** -- machine-facing. Serves the raw elementary stream to the
  restreamer and answers the integration's queries. It has no authentication of
  its own, so it is bound to loopback unconditionally and never follows
  ``access_mode``: publishing it would hand out live video, camera power control
  and session teardown to anyone on the network.
* **Ingress UI** -- the account-linking page. Supervisor's ingress proxy runs in
  its own container and reaches a host-network add-on over the Docker bridge
  rather than loopback, so this listener cannot be bound to loopback. Instead
  every request must carry the headers Supervisor injects, which keeps a direct
  connection to the port from reaching anything.

  In a standalone deployment there is no Supervisor to authenticate the user,
  so the same page is guarded by a password instead. Both guards, and the rule
  that there is no unguarded third case, live in :mod:`bridge.webauth`.

Only go2rtc's RTSP and WebRTC listeners follow ``access_mode`` -- those are the
streams a user may deliberately publish, and go2rtc guards them with
credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .account import AccountManager, LinkFailedError
from .config import Options
from .const import ALL_INTERFACES, API_PORT, INGRESS_PORT, LOOPBACK
from .framing import MediaKind
from .mux import StreamMuxer
from .preview import QUALITIES, PreviewError, PreviewManager
from .redact import safe_error
from .streaming import CameraOffError, StreamError
from .webauth import SESSION_COOKIE, build_guards, session_token

if TYPE_CHECKING:
    from .cameras import CameraRegistry
    from .restream import Restreamer
    from .streaming import SessionManager

_LOGGER = logging.getLogger(__name__)

_STATIC_DIR = "/app/web"

#: Bounds on how stale a caller may accept a still being. The upper bound stops
#: a request pinning a session open indefinitely; the lower one stops a client
#: asking for frames faster than the vendor library produces them.
_MAX_AGE_LIMITS = (0.2, 10.0)

#: How long a sign-in lasts. A month: this guards a page on a home network, and
#: being asked again every day trains people to pick a shorter password rather
#: than a better one.
_SESSION_SECONDS = 30 * 24 * 3600

#: Supervisor's ingress path prefix. The session cookie is scoped to it so it
#: is not sent to other add-ons living behind the same proxy.
_INGRESS_HEADER = "X-Ingress-Path"


def _bounded(raw: str | None, name: str, low: int, high: int, default: int) -> int:
    """A whole number within range, or the default when nothing was asked for.

    Clamped rather than trusted: these arrive from a browser, and a request for
    a thousand frames a second should cost a sensible answer, not a busy host.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise web.HTTPBadRequest(text=f"{name} must be a whole number") from None
    return min(max(value, low), high)


def _max_age(request: web.Request) -> float | None:
    """How old a held frame may be, per the request.

    Absent means the caller has no opinion and gets the default, which suits a
    dashboard tile. The add-on page's preview asks for something short so that
    refreshing twice a second actually yields new pictures.
    """
    raw = request.query.get("max_age")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise web.HTTPBadRequest(text="max_age must be a number") from None
    low, high = _MAX_AGE_LIMITS
    return min(max(value, low), high)


#: Returned to clients instead of the vendor SDK's own message, which embeds the
#: raw upstream response body and can contain the authorization code or tokens.
_LINK_FAILED_MESSAGE = (
    "The authorization could not be completed. The code may have expired or "
    "already been used -- start the sign-in again from this page."
)


class BridgeApi:
    """Serves the control plane and the ingress UI on separate listeners."""

    def __init__(
        self,
        account: AccountManager,
        registry_provider,
        sessions_provider,
        restreamer: Restreamer,
        refresh_callback,
        options: Options,
        previews: PreviewManager,
    ) -> None:
        self._previews = previews
        self._options = options
        self._account = account
        self._registry_provider = registry_provider
        self._sessions_provider = sessions_provider
        self._restreamer = restreamer
        self._refresh_callback = refresh_callback
        self._runners: list[web.AppRunner] = []

    # ------------------------------------------------------------------
    # Server lifetime
    # ------------------------------------------------------------------

    def build_control_app(self) -> web.Application:
        """Machine-facing API. Loopback only."""
        app = web.Application()
        app.add_routes(
            [
                web.get("/api/health", self._health),
                web.get("/api/cameras", self._cameras),
                web.post("/api/cameras/refresh", self._refresh),
                web.get("/api/snapshot/{did}", self._snapshot),
                web.get("/api/stream/{did}", self._stream),
                web.post("/api/cameras/{did}/power", self._set_power),
            ]
        )
        return app

    def build_ingress_app(self) -> web.Application:
        """Account-linking UI, guarded according to how the bridge is deployed."""
        app = web.Application(
            middlewares=build_guards(
                supervised=self._options.supervised,
                published=self._page_is_published,
                web_password=self._options.web_password,
            )
        )
        app.add_routes(
            [
                web.get("/api/health", self._health),
                web.get("/api/cameras", self._cameras),
                web.post("/api/link/begin", self._link_begin),
                web.post("/api/link/complete", self._link_complete),
                web.post("/api/unlink", self._unlink),
                web.post("/api/login", self._login),
                web.post("/api/logout", self._logout),
                web.get("/api/preview/{did}", self._preview),
                web.get("/", self._index),
                web.static("/static", _STATIC_DIR, show_index=False),
            ]
        )
        return app

    @property
    def _page_is_published(self) -> bool:
        """Whether the account page will be reachable from off this machine.

        As an add-on it always is: Supervisor's proxy arrives over the Docker
        bridge, so the listener cannot be bound to loopback and the guard has
        to do the work instead. Standalone it follows `access_mode`, like every
        other service here -- with the streams kept local there is nothing to
        guard, because nothing outside the host can open the connection.
        """
        return self._options.supervised or self._options.bind_address != LOOPBACK

    async def async_start(self) -> None:
        await self._async_serve(self.build_control_app(), LOOPBACK, API_PORT)
        host = ALL_INTERFACES if self._page_is_published else LOOPBACK
        await self._async_serve(self.build_ingress_app(), host, INGRESS_PORT)

    async def _async_serve(self, app: web.Application, host: str, port: int) -> None:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        self._runners.append(runner)
        # Logged deliberately: a wrong bind address is the one failure mode of
        # this design that behaves normally while exposing the streams.
        _LOGGER.info("Listening on http://%s:%s", host, port)

    async def async_stop(self) -> None:
        for runner in self._runners:
            with contextlib.suppress(Exception):
                await runner.cleanup()
        self._runners.clear()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _health(self, request: web.Request) -> web.Response:
        sessions: SessionManager | None = self._sessions_provider()
        return web.json_response(
            {
                "status": "ok",
                "linked": self._account.is_linked,
                "sessions": sessions.stats() if sessions else {},
            }
        )

    async def _cameras(self, request: web.Request) -> web.Response:
        registry: CameraRegistry | None = self._registry_provider()
        if registry is None:
            return web.json_response(
                {"error": "not_linked", "message": "Xiaomi account is not linked"},
                status=503,
            )
        descriptions = await registry.async_refresh()
        sessions: SessionManager | None = self._sessions_provider()
        stats = sessions.stats() if sessions else {}
        return web.json_response(
            {
                "cameras": [
                    {
                        **description.as_dict(),
                        # What the camera is actually sending, once a session
                        # has run long enough to measure it. The page offers
                        # frame rates up to this rather than a list invented
                        # here, which would be wrong the moment a camera ships
                        # that sends more.
                        "stream_fps": (stats.get(description.did, {}).get("fps")),
                        # What the camera is actually sending, not what the
                        # configuration asked for. The two disagreed for this
                        # option's entire existence and nothing said so.
                        "stream_audio": (
                            stats.get(description.did, {}).get("audio_codec")
                        ),
                        # Credential-free by construction: a URL carrying
                        # user:password@ would be copied into Home Assistant
                        # config state, diagnostics and the UI.
                        "rtsp_url": self._restreamer.rtsp_url(description.did),
                        # The same pictures, re-encoded on demand for anything
                        # that cannot decode H.265 -- browsers and HomeKit,
                        # mostly. Nothing pays for it until something opens it.
                        "rtsp_url_h264": self._restreamer.rtsp_url_h264(
                            description.did
                        ),
                        # Read by the integration in place of the two fixed
                        # fields above, which stay for an integration older
                        # than this add-on.
                        "streams": self._restreamer.stream_descriptions(
                            description.did
                        ),
                        "rtsp_requires_credentials": (
                            self._restreamer.requires_credentials
                        ),
                        # Whether the address above is reachable from anything
                        # other than this host -- distinct from whether a
                        # password is required. The page needs this to decide
                        # whether rewriting the loopback hostname it was sent
                        # would produce a working address or a dead one.
                        "rtsp_reachable_off_host": (
                            self._restreamer.rtsp_reachable_off_host
                        ),
                    }
                    for description in descriptions
                ]
            }
        )

    async def _refresh(self, request: web.Request) -> web.Response:
        await self._refresh_callback()
        return web.json_response({"status": "ok"})

    async def _snapshot(self, request: web.Request) -> web.StreamResponse:
        did = request.match_info["did"]
        session = self._session_for(did)
        if session is None:
            raise web.HTTPNotFound(text=f"unknown camera {did}")
        try:
            image = await session.async_snapshot(max_age=_max_age(request))
        except CameraOffError as err:
            # 409 rather than 503: the bridge is healthy and the request is
            # well-formed; the camera is simply switched off.
            raise web.HTTPConflict(text=str(err)) from err
        except StreamError as err:
            raise web.HTTPServiceUnavailable(text=str(err)) from err
        return web.Response(
            body=image,
            content_type="image/jpeg",
            # A preview refreshes by requesting this again; a cached answer
            # would freeze the picture.
            headers={"Cache-Control": "no-store"},
        )

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        """Serve the camera as MPEG-TS, for go2rtc to demux."""
        did = request.match_info["did"]
        session = self._session_for(did)
        if session is None:
            raise web.HTTPNotFound(text=f"unknown camera {did}")

        # A container, and declared as one. An elementary stream could not say
        # what it was and had to be probed; this can, which is what lets go2rtc
        # read it without an ffmpeg process in between.
        response = web.StreamResponse(
            status=200, headers={"Content-Type": "video/mp2t"}
        )
        await response.prepare(request)

        try:
            async with session.subscribe() as consumer:
                # Read here rather than passed out of subscribe(): the fact has
                # one home, on the session. A container fixes its tracks before
                # its first byte, so this is the moment the decision is made.
                muxer = StreamMuxer(session.codec, session.audio_codec)
                try:
                    await self._pump(consumer, response, muxer)
                finally:
                    # Accumulated before the muxer is discarded. It is
                    # per-consumer and short-lived; the session's counter is
                    # the total across every reader it has served.
                    session.stats.dropped_timestamps += muxer.dropped
                    with contextlib.suppress(Exception):
                        await response.write(muxer.close())
        except StreamError as err:
            _LOGGER.warning("stream for %s failed: %s", did, safe_error(err))
        except (ConnectionResetError, asyncio.CancelledError):
            # go2rtc disconnecting is routine -- it reconnects on demand.
            pass
        finally:
            with contextlib.suppress(Exception):
                await response.write_eof()
        return response

    @staticmethod
    async def _pump(consumer, response: web.StreamResponse, muxer: StreamMuxer) -> None:
        """Package units into the response until the session or client ends.

        The close signal is an event rather than a queue sentinel because the
        queue is bounded: a stalled consumer can fill it, and a dropped sentinel
        would leave this loop waiting forever and block shutdown.
        """
        while True:
            unit_task = asyncio.ensure_future(consumer.queue.get())
            closed_task = asyncio.ensure_future(consumer.closed.wait())
            done, pending = await asyncio.wait(
                {unit_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if closed_task in done:
                unit_task.cancel()
                return
            unit = unit_task.result()
            if unit.kind is MediaKind.AUDIO and not muxer.has_audio:
                # Audio started after this container was built, and a container
                # cannot gain a track. Ending the response is the whole remedy:
                # go2rtc reconnects within about a second onto a session that
                # is already warm, and gets a container that has the track.
                return
            chunk = muxer.write(unit)
            if chunk:
                await response.write(chunk)

    async def _set_power(self, request: web.Request) -> web.Response:
        did = request.match_info["did"]
        registry: CameraRegistry | None = self._registry_provider()
        if registry is None:
            raise web.HTTPServiceUnavailable(text="Xiaomi account is not linked")
        payload = await _json_body(request)
        value = bool(payload.get("value"))
        await registry.async_set_power(did, value)
        return web.json_response({"status": "ok", "value": value})

    async def _link_begin(self, request: web.Request) -> web.Response:
        try:
            url = await self._account.async_begin_link()
        except Exception as err:
            # Without this the handler answers a bare 500, which tells the user
            # nothing and leaves the cause only in the log. The detail is
            # logged (redacted) and a usable message goes back to the page.
            _LOGGER.exception("Could not start account linking")
            raise web.HTTPBadGateway(
                text=(
                    "Could not reach Xiaomi to start sign-in. Check that this "
                    "machine has internet access, then try again. Details: "
                    f"{safe_error(err)}"
                )
            ) from err
        return web.json_response({"authorize_url": url})

    async def _link_complete(self, request: web.Request) -> web.Response:
        payload = await _json_body(request)
        code = str(payload.get("code", "")).strip()
        state = str(payload.get("state", "")).strip()
        if not code:
            raise web.HTTPBadRequest(text="code is required")
        try:
            await self._account.async_complete_link(code=code, state=state)
        except LinkFailedError as err:
            # The detail is logged (redacted) rather than returned: the SDK's
            # message interpolates the upstream response body.
            _LOGGER.warning("Account linking failed: %s", safe_error(err))
            raise web.HTTPBadRequest(text=_LINK_FAILED_MESSAGE) from err
        await self._refresh_callback()
        return web.json_response({"status": "ok"})

    async def _unlink(self, request: web.Request) -> web.Response:
        await self._account.async_unlink()
        await self._refresh_callback()
        return web.json_response({"status": "ok"})

    async def _preview(self, request: web.Request) -> web.StreamResponse:
        """One picture, decoded from the stream this add-on publishes.

        Deliberately not the snapshot endpoint, which reads the vendor
        library's own decoded frames. Those keep arriving while the published
        stream is broken, so a preview drawn from them would show a healthy
        picture for a camera nothing else can watch. This one fails when the
        thing it is reporting on fails -- see :mod:`bridge.preview`.

        One picture per request, and the request is held until there is a
        picture newer than the one the caller names. A single multipart
        connection carrying every frame is the textbook answer and was tried
        twice -- once relayed from go2rtc, once served from here. Both were
        reset by something between this process and the browser after about
        ten frames. A request per frame survives that, and on a local network
        the cost is one round trip of a few milliseconds against a frame period
        of fifty.

        Holding the request is what makes it smooth: answering immediately and
        letting the page wait a fixed interval puts two unsynchronised timers
        in series, and the gap between pictures then swings between one frame
        period and two.
        """
        did = request.match_info["did"]
        if self._session_for(did) is None:
            raise web.HTTPNotFound(text=f"unknown camera {did}")

        # Carried on the request rather than stored: how smooth and how sharp a
        # preview should be is a property of the person watching and the screen
        # they are watching on, not of the installation. Two viewers can ask
        # for different things at once, and nothing has to be saved, migrated
        # or kept in step.
        fps = _bounded(request.query.get("fps"), "fps", 0, 30, default=12)
        after = _bounded(request.query.get("after"), "after", 0, 2**31, default=0)
        quality = request.query.get("quality", "balanced")
        if quality not in QUALITIES:
            raise web.HTTPBadRequest(
                text=f"quality must be one of {', '.join(sorted(QUALITIES))}"
            )

        try:
            seq, image = await self._previews.async_frame(did, fps, quality, after)
        except PreviewError as err:
            raise web.HTTPServiceUnavailable(text=str(err)) from err

        return web.Response(
            body=image,
            content_type="image/jpeg",
            headers={
                # Names this picture, so the page can ask to be held until
                # there is a newer one instead of guessing when to ask again.
                "X-Frame-Seq": str(seq),
                "Cache-Control": "no-store",
            },
        )

    async def _login(self, request: web.Request) -> web.Response:
        """Exchange the configured password for a session cookie.

        Deliberately not the browser's built-in prompt: it cannot be styled,
        it interrupts before the page it guards has drawn anything, and it
        insists on a username this add-on does not have.
        """
        expected = self._options.web_password
        if not expected:
            # Nothing to sign in to. Saying so beats a rejection the page
            # cannot explain.
            raise web.HTTPBadRequest(text="No password is configured.")

        supplied = str((await _json_body(request)).get("password", ""))
        # Compared as bytes: `compare_digest` refuses non-ASCII strings
        # outright, and a password with a Chinese character in it would
        # otherwise raise on every attempt, including the correct one.
        if not secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            raise web.HTTPUnauthorized(text="That password is not right.")

        response = web.json_response({"status": "ok"})
        response.set_cookie(
            SESSION_COOKIE,
            session_token(expected),
            httponly=True,
            # Strict is what makes a forged request from another site
            # harmless: the browser will not attach this to anything that site
            # starts, so there is nothing for it to ride on.
            samesite="Strict",
            path=self._cookie_path(request),
            max_age=_SESSION_SECONDS,
        )
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        response = web.json_response({"status": "ok"})
        response.del_cookie(SESSION_COOKIE, path=self._cookie_path(request))
        return response

    @staticmethod
    def _cookie_path(request: web.Request) -> str:
        """Scope the cookie to this add-on's own path behind ingress."""
        return (request.headers.get(_INGRESS_HEADER, "") or "") + "/"

    async def _index(self, request: web.Request) -> web.StreamResponse:
        # Revalidated on every load. Without this a browser keeps serving the
        # page it cached before an update, so a fix that shipped never runs and
        # the add-on looks unchanged -- indistinguishable from a fix that did
        # not work.
        return web.FileResponse(
            f"{_STATIC_DIR}/index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _session_for(self, did: str):
        registry: CameraRegistry | None = self._registry_provider()
        sessions: SessionManager | None = self._sessions_provider()
        if registry is None or sessions is None:
            return None
        info = registry.get(did)
        if info is None:
            return None
        return sessions.session_for(info)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as err:
        raise web.HTTPBadRequest(text="expected a JSON body") from err
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return payload


__all__ = ["BridgeApi"]
