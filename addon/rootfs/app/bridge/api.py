"""HTTP surfaces.

Two listeners with deliberately different exposure:

* **Control plane** -- machine-facing. Serves the camera streams, muxed as
  MPEG-TS, to the restreamer and answers the integration's queries. It has no
  authentication of its own, so it is bound to loopback unconditionally and
  never follows ``access_mode``: publishing it would hand out live video,
  camera power control and session teardown to anyone on the network.
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
import base64
import contextlib
import ipaddress
import logging
import secrets
from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Any

import av.error
from aiohttp import web

from . import go2rtc_xiaomi
from .account import AccountManager, LinkFailedError
from .config import Options, TranscodeQuality, VideoQuality
from .const import ALL_INTERFACES, API_PORT, INGRESS_PORT, LOOPBACK
from .framing import MediaKind
from .mux import StreamMuxer
from .paths import VideoPath, available_paths
from .redact import safe_error
from .settings import Defaults, Resolved, SettingsStore
from .stills import QUALITIES, Stills, StillsError
from .streaming import StreamError
from .webauth import SESSION_COOKIE, build_guards, session_token

if TYPE_CHECKING:
    from .cameras import CameraDescription, CameraRegistry
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

#: Failures per source address, and the delay each earns. Five free attempts
#: covers a typo and a forgotten variant; after that the answer slows down,
#: doubling each time and capped so a very persistent attacker still gets an
#: answer eventually. Never a lockout -- that turns the page into a
#: denial-of-service switch anyone on the network can throw, with the owner
#: on the wrong side of it, standing in their own house, unable to see their
#: own cameras.
#:
#: Two limits of this, worth stating here rather than only in a design note,
#: because this comment is where the next person will look and a defence
#: whose limits live only in a report is a defence whose limits get lost.
#: First, this slows one *address*, not one attacker: anyone with a second
#: NIC, a spare device, or an IPv6 /64 gets a fresh budget and a fresh curve
#: per address they attack from. That is inherent to any per-address scheme,
#: not a defect of this one. Second, the password check runs in every
#: deployment including through Home Assistant's own panel (see
#: `webauth.py`'s module docstring), and behind Supervisor's ingress every
#: request arrives from Supervisor's single address -- so yes, one person
#: mistyping the password there slows the page for everyone else on that
#: instance too, capped at the same 30 seconds.
_MAX_FREE_ATTEMPTS = 5
_MAX_PENALTY_S = 30.0

#: Upper bound on how many source addresses are tracked at once. Only a
#: success removes an entry, so without a cap a sustained attack spread
#: across many addresses -- cheap over an IPv6 /64, though TCP's handshake
#: means it is never free -- would grow this dict for the life of the
#: process. Bounded by evicting the least-recently-touched entry once the
#: cap is hit: a defence that can be turned into a memory leak is a new
#: attack surface, not a fix.
_MAX_TRACKED_ADDRESSES = 10_000


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
        previews: Stills,
        settings_store: SettingsStore | None = None,
        slug: str | None = None,
    ) -> None:
        self._previews = previews
        self._options = options
        # Read once at start-up by `__main__.py` and handed in here, rather
        # than read on every `/api/info` request: it does not change while
        # the add-on runs, and a Supervisor round trip per page load would be
        # a cost with no matching benefit.
        self._slug = slug
        # Optional only so the many tests that build a `BridgeApi` to exercise
        # one unrelated handler (streaming, previews, linking) do not each
        # need a store of their own. Every handler that actually reads or
        # writes settings requires a real one, wired once in `__main__.py`.
        self._settings_store = settings_store
        self._account = account
        self._registry_provider = registry_provider
        self._sessions_provider = sessions_provider
        self._restreamer = restreamer
        self._refresh_callback = refresh_callback
        self._runners: list[web.AppRunner] = []
        # Keyed on the peer address (never a forwarded-for header -- see
        # `webauth._from_supervisor` for why that distinction matters), and
        # never persisted: a restart is an acceptable way to reset it, and
        # keeping it only in memory is what keeps the login page itself free
        # of anything worth calling storage. An `OrderedDict` so the least
        # recently touched address can be evicted once `_MAX_TRACKED_ADDRESSES`
        # is reached -- see that constant for why a cap exists at all.
        self._login_failures: OrderedDict[str, int] = OrderedDict()
        # Open preview sockets, keyed by the camera they show. Consulted only
        # by `_set_camera_settings`, which needs to reach every viewer of a
        # camera whose session it is about to tear down and reopen -- without
        # this they would just go quiet until the still decoder's own
        # twenty-second first-frame timeout turned the silence into
        # `no_video` (see `stills.py`'s `_FIRST_FRAME_TIMEOUT` and
        # `docs/superpowers/findings/2026-08-12-preview-restart.md`).
        self._preview_sockets: dict[str, set[web.WebSocketResponse]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Server lifetime
    # ------------------------------------------------------------------

    def build_control_app(self) -> web.Application:
        """Machine-facing API. Loopback only."""
        app = web.Application()
        app.add_routes(
            [
                web.get("/api/health", self._health),
                web.get("/api/cameras", self._cameras_for_control),
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
                web.get("/api/cameras", self._cameras_for_page),
                web.post("/api/link/begin", self._link_begin),
                web.post("/api/link/complete", self._link_complete),
                web.post("/api/unlink", self._unlink),
                web.post("/api/login", self._login),
                web.post("/api/logout", self._logout),
                web.get("/api/preview/{did}/ws", self._preview_ws),
                # These belong to the page, not the control plane: the page
                # is where a person changes them, and the integration has no
                # business writing video settings over the loopback API.
                web.get("/api/settings", self._settings),
                web.put("/api/settings", self._set_defaults),
                web.put("/api/cameras/{did}/settings", self._set_camera_settings),
                # Compatibility mode's own sign-in, carried to go2rtc.
                # Nothing generic: go2rtc's wider API exposes `exec` and
                # stream management, and a passthrough would put both
                # behind this add-on's password too.
                web.post("/api/compat/signin", self._compat_signin),
                web.delete("/api/compat", self._compat_forget),
                # Facts about the add-on itself, not any one camera: changes
                # only when the add-on restarts, unlike `/api/cameras`.
                web.get("/api/info", self._info),
                web.get("/", self._index),
                web.get("/app.css", self._asset),
                web.get("/app.js", self._asset),
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

    async def _cameras_for_control(self, request: web.Request) -> web.Response:
        """The integration's answer: only cameras this add-on can stream.

        A refused camera is real account data, not a bug, but an integration
        -- including one released before this field existed -- has no way to
        act on ``support`` and would otherwise build a `camera` entity for a
        device that can never show a picture, with nothing telling the user
        why. Filtering happens here, once, rather than trusting every caller
        of this route to check ``CameraDescription.publishable`` itself.
        """
        return await self._cameras(request, publishable_only=True)

    async def _cameras_for_page(self, request: web.Request) -> web.Response:
        """The page's answer: every camera on the account, refused or not.

        The page is where a refused camera's ``support`` value has somewhere
        to be read and explained; unlike the control plane it is not asking
        "which of these can I build an entity for".
        """
        return await self._cameras(request, publishable_only=False)

    async def _cameras(
        self, request: web.Request, *, publishable_only: bool
    ) -> web.Response:
        registry: CameraRegistry | None = self._registry_provider()
        if registry is None:
            return web.json_response(
                {"error": "not_linked", "message": "Xiaomi account is not linked"},
                status=503,
            )
        descriptions = await registry.async_refresh()
        if publishable_only:
            descriptions = [d for d in descriptions if d.publishable]
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
                        **self._stream_fields(description),
                        # A publishable camera whose root stream still could
                        # not be built -- most often compatibility mode
                        # unable to find this camera's address. `None` covers
                        # both "not publishable" and "built fine": neither
                        # has anything to report here. See
                        # `Restreamer.stream_error` and `restream.source_for`
                        # for who raises this.
                        "stream_error": self._restreamer.stream_error(description.did),
                        # Resolved is what the camera actually gets right now
                        # -- its own override where it has one, the shared
                        # default otherwise. Override is only what this
                        # camera says for itself, so the page can show which
                        # fields are following the default versus set here.
                        "settings": _settings_dict(
                            self._settings_store.resolved_for(
                                description.did,
                                support=description.support,
                                compat_ready=self._compat_ready(),
                            )
                        ),
                        "override": self._settings_store.override_for(
                            description.did
                        ).as_dict(),
                        # Sent rather than derived on the page: which path is
                        # usable depends on the account's compatibility-mode
                        # state, which only this process holds. A copy in
                        # JavaScript would be the second source `paths.py`'s
                        # own docstring exists to avoid.
                        "paths": {
                            path.value: reason
                            for path, reason in available_paths(
                                description.support,
                                compat_ready=self._compat_ready(),
                            ).items()
                        },
                    }
                    for description in descriptions
                ]
            }
        )

    def _stream_fields(self, description: CameraDescription) -> dict[str, object]:
        """RTSP addresses for a camera, or nothing for one that cannot stream.

        A refused camera's ``publishable`` is ``False`` -- go2rtc never builds
        a stream table entry for it, so any address handed out here would name
        a connection that can never succeed. Relying on the page to remember
        not to render it is weaker than not sending it: any consumer of this
        JSON, now or later, may reasonably show or dial a URL it was given.
        Gated on the same ``publishable`` property everything else that
        touches a camera's stream uses, rather than re-deriving it from
        ``support`` here -- see the property's own docstring for why that
        matters on this project specifically. Also gated on
        ``Restreamer.stream_error``: a publishable camera whose root source
        could not be built (see ``restream.source_for``) has no go2rtc stream
        behind these names either, and handing out a URL that can never
        connect is worse than not sending one -- see ``stream_error`` in
        ``_cameras`` for where that failure is reported instead.
        """
        if not description.publishable or self._restreamer.stream_error(
            description.did
        ):
            return {}
        return {
            # Credential-free by construction: a URL carrying user:password@
            # would be copied into Home Assistant config state, diagnostics
            # and the UI.
            "rtsp_url": self._restreamer.rtsp_url(description.did),
            # The same pictures, re-encoded on demand for anything that cannot
            # decode H.265 -- browsers and HomeKit, mostly. Nothing pays for
            # it until something opens it.
            "rtsp_url_h264": self._restreamer.rtsp_url_h264(description.did),
            # Read by the integration in place of the two fixed fields above,
            # which stay for an integration older than this add-on.
            "streams": self._restreamer.stream_descriptions(description.did),
            "rtsp_requires_credentials": self._restreamer.requires_credentials,
            # Whether the address above is reachable from anything other than
            # this host -- distinct from whether a password is required. The
            # page needs this to decide whether rewriting the loopback
            # hostname it was sent would produce a working address or a dead
            # one.
            "rtsp_reachable_off_host": self._restreamer.rtsp_reachable_off_host,
        }

    async def _refresh(self, request: web.Request) -> web.Response:
        await self._refresh_callback()
        return web.json_response({"status": "ok"})

    async def _snapshot(self, request: web.Request) -> web.StreamResponse:
        did = request.match_info["did"]
        session = self._session_for(did)
        if session is None:
            raise web.HTTPNotFound(text=f"unknown camera {did}")
        # Asked before anything is started, and only for a definite "off":
        # such a camera answers every call and sends nothing, so waiting for a
        # picture would spend the whole first-frame timeout rediscovering what
        # the power switch already said. The preview socket asks the same
        # question for the same reason.
        if self._power_state(did) is False:
            # 409 rather than 503: the bridge is healthy and the request is
            # well-formed; the camera is simply switched off.
            raise web.HTTPConflict(text=f"{did} is switched off")
        try:
            image = await self._previews.async_still(did, _max_age(request))
        except StillsError as err:
            if await self._read_power_state(did) is False:
                raise web.HTTPConflict(text=f"{did} is switched off") from err
            raise web.HTTPServiceUnavailable(text=safe_error(err)) from err
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
                    await self._pump(consumer, response, muxer, session.stats, did)
                finally:
                    # Accumulated before the muxer is discarded. They are
                    # per-consumer and short-lived; the session's counters are
                    # the totals across every reader it has served.
                    session.stats.dropped_timestamps += muxer.dropped
                    session.stats.clock_reanchors += muxer.reanchors
                    # Attempted independently: a failing write must not skip
                    # the close (it releases the underlying AVFormatContext),
                    # and a failing close is suppressed on its own rather than
                    # also swallowing a write that would otherwise succeed.
                    trailer = b""
                    with contextlib.suppress(Exception):
                        trailer = muxer.close()
                    if trailer:
                        with contextlib.suppress(Exception):
                            await response.write(trailer)
        except (StreamError, av.error.FFmpegError) as err:
            # The muxer's own exceptions land here too: a malformed unit (a
            # zero-length payload, observed from the SDK after a peer-to-peer
            # reconnect) makes PyAV raise, and letting that escape means an
            # unredacted traceback in the response instead of the safe_error
            # discipline every other failure on this path already gets.
            _LOGGER.warning("stream for %s failed: %s", did, safe_error(err))
        except (ConnectionResetError, asyncio.CancelledError):
            # go2rtc disconnecting is routine -- it reconnects on demand.
            pass
        finally:
            with contextlib.suppress(Exception):
                await response.write_eof()
        return response

    @staticmethod
    async def _pump(
        consumer,
        response: web.StreamResponse,
        muxer: StreamMuxer,
        stats,
        did: str,
    ) -> None:
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
                #
                # The spec treats this as a rare safety net rather than the
                # normal path, on the unverified assumption that a camera
                # sending audio at all sends some before subscribe()'s
                # parameter-set wait finishes. Counted and logged so release
                # verification can tell "fired once" from "fires on every
                # connect" without a packet capture.
                stats.late_audio_reconnects += 1
                _LOGGER.info(
                    "%s: audio arrived after the stream started without it; "
                    "ending the response so go2rtc reconnects with an audio "
                    "track (%d such reconnect(s) so far)",
                    did,
                    stats.late_audio_reconnects,
                )
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

    async def _settings(self, request: web.Request) -> web.Response:
        """The global video defaults every camera falls back to.

        Add-on-level facts used to be mirrored alongside these -- see
        `_info` for why they moved out.
        """
        defaults = self._settings_store.defaults
        return web.json_response({"defaults": _settings_dict(defaults)})

    async def _info(self, request: web.Request) -> web.Response:
        """What the page needs about the add-on itself, not any one camera.

        Split from `/api/settings` (which used to carry an `addon` block
        alongside the video defaults) because the two change on different
        schedules: this one only when the add-on restarts, `/api/cameras`
        whenever a camera does. Folding them together would mean re-sending
        facts that never changed every time one that does is polled.
        """
        return web.json_response(
            {
                "slug": self._slug,
                "compat_ready": self._compat_ready(),
            }
        )

    def _compat_ready(self) -> bool:
        """Delegated to the one owner of this fact -- see
        `go2rtc_xiaomi.compat_ready`'s docstring."""
        return go2rtc_xiaomi.compat_ready()

    async def _compat_signin(self, request: web.Request) -> web.Response:
        """One step of compatibility mode's sign-in, carried to go2rtc.

        Only this. go2rtc's API also exposes `exec` and stream management,
        and a general proxy would put both behind this add-on's password --
        which is a smaller door than either of those deserves.
        """
        body = await _json_body(request)
        step = body.get("step")
        if step not in {"password", "captcha", "verify"}:
            return web.json_response({"error": "unknown_step"}, status=400)
        try:
            result = await go2rtc_xiaomi.sign_in(
                step, **{k: v for k, v in body.items() if k != "step"}
            )
        except go2rtc_xiaomi.SignInBusy:
            return web.json_response({"error": "sign_in_busy"}, status=409)
        except Exception as err:
            # Credentials live in this exception text often enough that it
            # must never reach a response unredacted.
            return web.json_response({"error": safe_error(err)}, status=502)
        if result.ok:
            # Refreshed before the camera-list refresh below, which reads
            # this cache to decide whether the compat path is offered --
            # otherwise the page's own reload would show "not signed in"
            # for one more cycle.
            await go2rtc_xiaomi.refresh_compat_ready()
            await self._refresh_callback(explicit=True)
            return web.json_response({"ok": True})
        return web.json_response(
            {
                "captcha": (
                    base64.b64encode(result.captcha).decode()
                    if result.captcha
                    else None
                ),
                "verify_phone": result.verify_phone,
                "verify_email": result.verify_email,
            },
            status=401,
        )

    async def _compat_forget(self, request: web.Request) -> web.Response:
        """Remove compatibility mode's Xiaomi credential -- which go2rtc has
        no way to do.

        go2rtc's own `/api/xiaomi` handler (`internal/xiaomi/xiaomi.go` in
        its source) accepts a GET to list and a POST to sign in; there is no
        third case, and the in-memory token map that handler fills is only
        ever written to, never deleted from, anywhere in that file. go2rtc
        is also the sole writer of the state file it stores the token in
        (`app.PatchConfig`, called from that same POST handler), so editing
        that file from here would give it a second writer -- exactly what
        this add-on's configuration design avoids everywhere else. A control
        that says it is not supported is better than one that does nothing.
        """
        return web.json_response({"error": "not_supported"}, status=501)

    async def _set_defaults(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            changes = _settings_changes(body, per_camera=False)
        except ValueError as err:
            return web.json_response({"error": str(err)}, status=400)
        self._settings_store.set_defaults(**changes)
        # `explicit=True`: someone is looking at the page they just changed a
        # setting on. Without it a viewer keeps seeing the old picture until
        # they happen to reconnect, which reads as a setting that did nothing.
        await self._refresh_callback(explicit=True)
        return web.json_response({"ok": True})

    async def _set_camera_settings(self, request: web.Request) -> web.Response:
        did = request.match_info["did"]
        body = await _json_body(request)
        try:
            changes = _settings_changes(body, per_camera=True)
        except ValueError as err:
            return web.json_response({"error": str(err)}, status=400)
        # `path` belongs here too: `SessionManager.session_for` decides which
        # implementation serves a camera -- today only `VideoPath.OFFICIAL`,
        # by raising for anything else -- from the same `resolver` callback
        # quality and audio are read through, at the same moment: when a
        # session opens. Dropping the cached session is what forces that
        # decision to be remade, exactly as it already does for the other
        # two.
        #
        # As of this writing there is also no separate go2rtc stream URL to
        # touch: go2rtc always pulls every camera from this bridge's own
        # `/api/stream/{did}`, whichever path serves it. That holds only
        # because compatibility mode has no source URL of its own yet -- the
        # day it gets one, a path change will also need to change what
        # go2rtc sources from, and this paragraph will be wrong.
        session_affecting = {"quality", "audio", "path"} & set(changes)
        self._settings_store.set_override(did, **changes)
        sessions: SessionManager | None = self._sessions_provider()
        if session_affecting and sessions is not None:
            # Picture size, audio and which implementation serves the camera
            # are all only decided when its session opens, so any of them
            # changing means reopening that camera's session -- and only
            # this camera's.
            _LOGGER.info("reloading session %s for changed %s", did, sorted(changes))
            await sessions.async_reload(did)
            _LOGGER.info("session %s reloaded", did)
            # Told only once the old session is fully torn down, not while it
            # is still in flight: `SessionManager._async_stop_one` removes
            # the entry from its table before it awaits the vendor P2P
            # teardown, so `session_for()` would build a second, concurrent
            # session for the same physical camera if a viewer reconnected
            # into that window. Waiting for `async_reload` to return closes
            # the window entirely -- on real hardware that cost about 1.68s,
            # against the 20s `no_video` this replaces. See
            # `_notify_reloading`.
            await self._notify_reloading(did)
        await self._refresh_callback(explicit=True)
        return web.json_response({"ok": True})

    async def _notify_reloading(self, did: str) -> None:
        """Tell every open preview socket for *did* its session is reloading.

        A settings change that touches quality, audio or path drops and
        reopens the camera's session, which interrupts the RTSP stream that
        feeds a preview's decoder. Left alone, an open socket would just go
        quiet until the decoder's own first-frame timeout turned the silence
        into a generic `no_video` -- twenty seconds after a change the add-on
        itself caused, and a message the page used to treat as permanent.
        Saying `reloading` up front removes both problems: it is immediate,
        and the page knows it is temporary.

        Each socket is closed right after, rather than left to notice on its
        own -- the page reconnects on a transient reason, and a socket left
        open here would otherwise sit waiting for pictures a torn-down
        session cannot yet produce.
        """
        for ws in list(self._preview_sockets.get(did, ())):
            # Broad on purpose, matching every other best-effort socket
            # cleanup in this file (`async_stop` above, `_stream`'s
            # `response.write_eof()`): this runs inside `_set_camera_settings`,
            # so a narrower catch that let some other error through -- a
            # `RuntimeError` from writing to a transport that is mid-close,
            # say -- would abort the settings PUT before the session it is
            # announcing ever reloads. A best-effort notification must never
            # be able to fail the operation it is announcing.
            try:
                await ws.send_json({"type": "unavailable", "reason": "reloading"})
                await ws.close()
            except Exception:
                _LOGGER.debug(
                    "could not notify a preview socket for %s", did, exc_info=True
                )

    async def _preview_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Pictures from one camera, over one connection.

        Replaces a request per frame. The saving in round trips is the lesser
        half: a socket can also carry a message that is not a picture, which
        is what lets the add-on say why there is no picture instead of
        letting a request run out its timeout and handing the viewer
        ffmpeg's complaint about RTSP.

        The handshake is an ordinary HTTP request, so it passes through the
        same guards as everything else here -- the ingress headers, the peer
        address and the password cookie are all checked before it upgrades.
        What does change is that they are checked once: an established
        connection is not re-authorised, so a password set after one opens
        applies to it only when it next reconnects.
        """
        did = request.match_info["did"]
        if self._session_for(did) is None:
            _LOGGER.warning("preview ws refused: no session for %s", did)
            raise web.HTTPNotFound(text=f"unknown camera {did}")

        fps = _bounded(request.query.get("fps"), "fps", 0, 30, default=12)
        quality = request.query.get("quality", "medium")
        if quality not in QUALITIES:
            raise web.HTTPBadRequest(
                text=f"quality must be one of {', '.join(sorted(QUALITIES))}"
            )

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Asked before anything is started, and only for a definite "off":
        # such a camera connects, answers every call and sends nothing, so a
        # decoder opened for it would spend twenty seconds rediscovering --
        # in ffmpeg's words, about RTSP -- what the power switch already
        # said. `None` means the switch could not be read, which is not the
        # same as off; there the picture is the better evidence.
        if self._power_state(did) is False:
            await ws.send_json({"type": "unavailable", "reason": "switched_off"})
            await ws.close()
            return ws

        # Registered so `_notify_reloading` can reach this socket if a
        # settings change reloads this camera's session while it is open.
        # Removed unconditionally below -- both a clean close and one this
        # handler ends itself (`send_pictures`'s `finally`) must stop
        # tracking it, or a closed socket would linger here forever.
        sockets = self._preview_sockets[did]
        sockets.add(ws)

        async def send_pictures() -> None:
            """Each picture, named by the last one sent.

            The manager holds each call until a genuinely newer frame exists,
            so the pace follows the stream rather than a timer of this end's
            own -- the same arrangement the page used to drive over HTTP.
            """
            seq = 0
            try:
                while True:
                    seq, image = await self._previews.async_frame(
                        did, fps, quality, seq
                    )
                    await ws.send_bytes(image)
            except StillsError as err:
                _LOGGER.warning("preview %s ended: %s", did, safe_error(err))
                # Asked again rather than recalled: the case worth naming is a
                # camera switched off since the list was last read, and the
                # remembered value is by definition the one from before that.
                # Only the switch explains itself; everything else stays
                # "no video", which the page words generically.
                off = await self._read_power_state(did)
                await ws.send_json(
                    {
                        "type": "unavailable",
                        "reason": "switched_off" if off is False else "no_video",
                        "detail": str(err),
                    }
                )
            finally:
                # Ends the reading side too, which is what closes this handler
                # when the stream is the thing that stopped.
                await ws.close()

        sending = asyncio.create_task(send_pictures())
        try:
            # Read, purely to learn when the viewer goes away. The page sends
            # nothing, so there is nothing here to act on -- but the close
            # frame the browser sends is delivered to this process either way,
            # and `ws.closed` only becomes true once something has read it.
            #
            # Sending alone does not notice. It finds out when a write happens
            # to fail, which needs a picture to still be arriving and the
            # socket's buffer to have filled -- and a camera that has gone
            # quiet produces neither. Until then the decoder keeps running for
            # a viewer who left, and its idle timer keeps being reset by this
            # very loop, so nothing reaps it. That cost a second ffmpeg per
            # camera and took the add-on to 86% CPU.
            async for _ in ws:
                pass
        finally:
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending
            sockets.discard(ws)
            if not sockets:
                self._preview_sockets.pop(did, None)
        return ws

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

        peer = self._bucket_key(request.remote or "unknown")
        await self._login_penalty(peer)

        supplied = str((await _json_body(request)).get("password", ""))
        # Compared as bytes: `compare_digest` refuses non-ASCII strings
        # outright, and a password with a Chinese character in it would
        # otherwise raise on every attempt, including the correct one.
        if not secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            failures = self._record_login_failure(peer)
            # The source and the count, never the value: the attempted
            # password must not enter a log line, full stop -- see
            # `redact.py`'s reason for existing, which is that credentials
            # leaked through exception text before.
            _LOGGER.warning(
                "wrong web password from %s (%d failed attempt(s))",
                peer,
                failures,
            )
            raise web.HTTPUnauthorized(text="That password is not right.")

        # A success clears the slate completely -- this is a delay on
        # guessing, not a debt the address carries forward.
        self._login_failures.pop(peer, None)

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

    async def _login_penalty(self, peer: str) -> None:
        """Delay this address's login attempt, if it has earned one.

        A genuine `await asyncio.sleep` rather than anything that blocks the
        loop: this is a single-threaded server, and every camera's preview
        and every API call shares it. A sleep that blocked would let an
        attacker degrade the whole add-on just by failing to log in
        repeatedly. Several delayed attempts from different addresses run
        concurrently without interfering, because each is its own coroutine
        waiting on its own timer.
        """
        if peer in self._login_failures:
            # Touched: this address stays recently-used, so eviction under
            # `_MAX_TRACKED_ADDRESSES` takes an idle address first.
            self._login_failures.move_to_end(peer)
        failures = self._login_failures.get(peer, 0)
        if failures < _MAX_FREE_ATTEMPTS:
            return
        delay = min(2.0 ** (failures - _MAX_FREE_ATTEMPTS), _MAX_PENALTY_S)
        await asyncio.sleep(delay)

    @staticmethod
    def _bucket_key(peer: str) -> str:
        """Normalise a peer address before it becomes a dict key.

        `request.remote` can name the same host two ways -- plain
        `192.168.1.5` and its IPv4-mapped IPv6 form `::ffff:192.168.1.5` --
        and leaving both as their raw strings would hand a dual-stack
        attacker two separate budgets for one address. Parsed through
        `ipaddress`, the same normalisation `webauth._from_supervisor`
        already relies on to reason about a peer address in this file
        family. Falls back to the raw string when it does not parse as an
        address at all -- losing the backoff there is better than the
        request failing outright.
        """
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return peer
        mapped = getattr(address, "ipv4_mapped", None)
        return str(mapped) if mapped is not None else str(address)

    def _record_login_failure(self, peer: str) -> int:
        """Bump this address's failure count and return it.

        Also where the bound in `_MAX_TRACKED_ADDRESSES` is enforced: the
        address just touched is moved to the most-recently-used end, and if
        that pushes the map over the cap, the least-recently-touched address
        -- not necessarily this one -- is evicted. Split out from `_login`
        so this bookkeeping can be exercised directly rather than only
        through however many real requests it would take to fill the cap.
        """
        failures = self._login_failures.get(peer, 0) + 1
        self._login_failures[peer] = failures
        self._login_failures.move_to_end(peer)
        if len(self._login_failures) > _MAX_TRACKED_ADDRESSES:
            self._login_failures.popitem(last=False)
        return failures

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

    async def _asset(self, request: web.Request) -> web.StreamResponse:
        """The page's own stylesheet and script.

        Served beside the page rather than under `/static` so the page can
        reference them relatively -- ingress serves it under a prefix this
        file must not know or reconstruct.
        """
        name = request.path.lstrip("/")
        if name not in {"app.css", "app.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(
            f"{_STATIC_DIR}/{name}",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _read_power_state(self, did: str) -> bool | None:
        """This camera's lens switch, read now rather than recalled.

        Costs a request, so it is used where the answer is worth it: a stream
        that has stopped, where "switched off" is the one cause the viewer can
        act on and the remembered value is too old to tell them.
        """
        registry: CameraRegistry | None = self._registry_provider()
        if registry is None:
            return None
        return await registry.async_read_power_state(did)

    def _power_state(self, did: str) -> bool | None:
        """Whether this camera's lens is switched on, as last read.

        Read from the registry's own record rather than asked of the cloud
        again: the same value answers `/api/cameras`, which the page calls
        before it opens any preview, so what is held here is as fresh as what
        the viewer is looking at -- and opening a preview adds no request of
        its own.
        """
        registry: CameraRegistry | None = self._registry_provider()
        if registry is None:
            return None
        return registry.power_state(did)

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


def _settings_dict(settings: Defaults | Resolved | None) -> dict[str, object] | None:
    """`Defaults` and `Resolved` serialised the same way for the three fields
    they share -- the page has no reason to see those rendered differently.

    `path` exists only on `Resolved`: `Defaults` has no path to follow (see
    `settings.py`'s module docstring), so it is included only when present
    rather than reported as absent or invented for the defaults card. The
    page must read it from here, not reconstruct it -- `path_for` in
    `paths.py` is the one place that fact is decided; a second answer to it
    is exactly the bug the multi-stream incident in CLAUDE.md was.

    `None` passes straight through: a camera with no resolvable path (see
    `SettingsStore.resolved_for`) has no effective settings to show.
    """
    if settings is None:
        return None
    result: dict[str, object] = {
        "quality": settings.quality.value,
        "audio": settings.audio,
        "transcode_quality": settings.transcode_quality.value,
    }
    if isinstance(settings, Resolved):
        result["path"] = settings.path.value
    return result


def _settings_changes(body: dict, *, per_camera: bool = False) -> dict:
    """Validated settings from a request body.

    ``per_camera`` is the one distinction between this function's two
    callers, `/api/settings` (defaults) and `/api/cameras/{did}/settings`
    (one camera) -- not two independently-set flags, because "may this
    write clear a field back to following the default" and "may this write
    name a path" have always moved together: both are true for a per-camera
    write and false for a defaults write, and nothing about either question
    can be answered without knowing which endpoint is asking.

    `path` is refused outright, by name, on a defaults write -- never
    silently dropped. A global default for it would be meaningless: which
    paths a camera can use depends on its own model and on whether the
    compatibility-mode credential exists (see `settings.py`'s module
    docstring), and a dropped key here is exactly how the connection row
    in the settings sheet shipped as a control that accepted a choice and
    did nothing with it.

    Names the setting and the accepted values in the error. A bare enum
    ValueError names neither, which leaves someone who sent `"ultra"` with
    nothing to act on.
    """
    changes: dict[str, object] = {}
    parsers = {
        "quality": VideoQuality,
        "transcode_quality": TranscodeQuality,
    }
    for key, cls in parsers.items():
        if key not in body:
            continue
        raw = body[key]
        if raw is None and per_camera:
            changes[key] = None
            continue
        try:
            changes[key] = cls(raw)
        except ValueError:
            allowed = ", ".join(member.value for member in cls)
            raise ValueError(f"{key} must be one of {allowed}, not {raw!r}") from None
    if "audio" in body:
        raw = body["audio"]
        if raw is None and per_camera:
            changes["audio"] = None
        elif isinstance(raw, bool):
            changes["audio"] = raw
        else:
            raise ValueError(f"audio must be true or false, not {raw!r}")
    if "path" in body:
        if not per_camera:
            raise ValueError(
                "path has no default -- set it on the camera itself, "
                "not on /api/settings"
            )
        raw = body["path"]
        if raw is None:
            changes["path"] = None
        else:
            try:
                changes["path"] = VideoPath(raw)
            except ValueError:
                allowed = ", ".join(member.value for member in VideoPath)
                raise ValueError(
                    f"path must be one of {allowed}, not {raw!r}"
                ) from None
    return changes


__all__ = ["BridgeApi"]
