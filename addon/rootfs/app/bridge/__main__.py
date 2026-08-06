"""Bridge entry point.

Wires the pieces together and keeps them consistent: the account session, the
camera inventory, the stream sessions, the RTSP restreamer and the HTTP control
plane. The bridge starts and serves its UI even without a linked account, so
the user has somewhere to authorize from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from miot.types import MIoTCameraVideoQuality

from .account import AccountManager, NotLinkedError
from .api import BridgeApi
from .cameras import CameraRegistry
from .config import (
    LOG_LEVELS,
    Options,
    VideoQuality,
    build_ref,
    data_is_ephemeral,
    load_options,
)
from .const import CACHE_DIR, DATA_DIR, DEFAULT_CLOUD_SERVER
from .discovery import async_announce, async_withdraw
from .preview import PreviewManager
from .redact import install as install_redaction
from .redact import safe_error
from .restream import Restreamer
from .store import CredentialStore
from .streaming import SessionManager

_LOGGER = logging.getLogger("bridge")

#: How often the camera list is re-read. Cameras are added and renamed rarely,
#: and each refresh costs a cloud round trip, so this is deliberately slow.
_REFRESH_INTERVAL_SECONDS = 300


_QUALITY_MAP = {
    VideoQuality.LOW: MIoTCameraVideoQuality.LOW,
    VideoQuality.HIGH: MIoTCameraVideoQuality.HIGH,
}


class Bridge:
    """Owns every long-lived component."""

    def __init__(self, options: Options) -> None:
        self._options = options
        #: The client the current registry and sessions were built against.
        #: Compared by identity so an unlink/relink cycle rebuilds them.
        self._bound_client = None
        self._store = CredentialStore()
        self._account = AccountManager(self._store, cloud_server=DEFAULT_CLOUD_SERVER)
        self._registry: CameraRegistry | None = None
        self._sessions: SessionManager | None = None
        self._restreamer = Restreamer(options)
        self._previews = PreviewManager(self._restreamer.internal_rtsp_url)
        self._api = BridgeApi(
            account=self._account,
            registry_provider=lambda: self._registry,
            sessions_provider=lambda: self._sessions,
            restreamer=self._restreamer,
            refresh_callback=self.async_refresh,
            options=options,
            previews=self._previews,
        )
        self._discovery_uuid: str | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    async def async_start(self) -> None:
        await self._api.async_start()
        self._discovery_uuid = await async_announce()

        # A failure here must not take the bridge down: the UI is exactly where
        # the user goes to fix a broken or missing account, and an add-on that
        # crash-loops on a boot without network never gets there. It would also
        # leave a stale discovery record pointing at a dead add-on.
        try:
            linked = await self._account.async_setup()
        except Exception as err:
            _LOGGER.error(
                "Could not restore the Xiaomi session; sign in again from the "
                "add-on page: %s",
                safe_error(err),
            )
            linked = False

        if linked:
            await self.async_refresh()
        else:
            _LOGGER.info(
                "No Xiaomi account linked yet. Open the add-on page to sign in."
            )

        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def async_refresh(self) -> None:
        """Re-read the camera list and republish the RTSP streams."""
        await self._async_sync_session_binding()
        if self._registry is None:
            return

        try:
            cameras = await self._registry.async_refresh()
        except Exception as err:
            _LOGGER.error("Could not refresh the camera list: %s", err)
            return

        offline = [c.name for c in cameras if not c.online]
        powered_off = [c.name for c in cameras if c.powered_on is False]
        if offline:
            _LOGGER.warning("Offline camera(s): %s", ", ".join(offline))
        if powered_off:
            # Worth stating plainly: a switched-off camera connects fine and
            # then sends nothing, which is otherwise indistinguishable from a
            # broken stream.
            _LOGGER.info(
                "Switched-off camera(s), they will not produce video until "
                "turned on: %s",
                ", ".join(powered_off),
            )

        if self._sessions is not None:
            # Drop sessions for cameras that no longer exist, so a removed
            # device does not keep its native instance alive for the lifetime
            # of the process.
            await self._sessions.async_prune({c.did for c in cameras})
        await self._restreamer.async_apply([c.did for c in cameras])
        self._previews.drop({c.did for c in cameras})

    async def async_stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
        if self._discovery_uuid:
            await async_withdraw(self._discovery_uuid)
        await self._previews.async_shutdown()
        await self._restreamer.async_stop()
        if self._sessions is not None:
            await self._sessions.async_shutdown()
        await self._api.async_stop()
        await self._account.async_shutdown()

    async def _async_sync_session_binding(self) -> None:
        """Attach, rebuild or drop the session objects to match the account.

        Unlinking makes the SDK free every native camera instance, so anything
        still holding one has to be torn down before it calls into freed memory.
        Relinking produces a new client, which the registry and sessions must be
        rebuilt against.
        """
        client = None
        if self._account.is_linked:
            with contextlib.suppress(NotLinkedError):
                client = self._account.client

        if client is self._bound_client:
            return

        if self._sessions is not None:
            await self._sessions.async_shutdown()
        self._sessions = None
        self._registry = None
        self._bound_client = client

        if client is None:
            await self._restreamer.async_apply([])
            return

        self._registry = CameraRegistry(client)
        self._sessions = SessionManager(
            client,
            quality=_QUALITY_MAP[self._options.video_quality],
            enable_audio=self._options.enable_audio,
        )

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)
                await self.async_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.error("Refresh loop error: %s", err)


async def async_main() -> int:
    try:
        options = load_options()
    except ValueError as err:
        # Configuration errors are the user's to fix, so they are reported
        # plainly rather than as a traceback.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        _LOGGER.error("%s", err)
        return 1

    logging.basicConfig(
        level=LOG_LEVELS[options.log_level],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # The SDK builds log lines and exception messages from raw request bodies
    # and headers, which on the OAuth endpoints contain the account tokens.
    install_redaction()
    # The SDK's LAN scanner logs every broadcast at debug level.
    logging.getLogger("miot.lan").setLevel(logging.WARNING)

    if not options.supervised and data_is_ephemeral():
        _LOGGER.warning(
            "%s is inside the container, so the Xiaomi account link will be "
            "lost the next time this container is replaced -- including on "
            "every update. Start the container with -v xiaomi-camera:%s to "
            "keep it.",
            DATA_DIR,
            DATA_DIR,
        )

    _LOGGER.info(
        "Starting bridge (access_mode=%s, quality=%s, supervised=%s, build=%s)",
        options.access_mode.value,
        options.video_quality.value,
        options.supervised,
        build_ref(),
    )
    import pathlib

    pathlib.Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

    bridge = Bridge(options)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await bridge.async_start()
        await stop_event.wait()
    finally:
        # Runs even when startup raised, so the discovery record is withdrawn
        # and go2rtc is not left orphaned holding the RTSP port.
        _LOGGER.info("Shutting down")
        await bridge.async_stop()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
