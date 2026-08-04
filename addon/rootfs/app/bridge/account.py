"""Xiaomi account linking and session lifetime.

Access tokens are valid for roughly three days, so an unattended bridge has to
refresh them on its own; a bridge that only refreshed on failure would drop
streams every couple of days until someone noticed.

Linking deliberately goes through :class:`MIoTClient` rather than the lower
level OAuth client. ``MIoTClient.get_access_token_async`` additionally fetches
the user profile and brings up the MQTT session that carries device events --
skipping it leaves the session working but silent, with only a
``mips setup skipped: no user uid`` line to explain why.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from miot.client import MIoTClient

from .const import (
    CACHE_DIR,
    DEFAULT_CLOUD_SERVER,
    OAUTH_REDIRECT_URI,
    TOKEN_REFRESH_MARGIN_SECONDS,
    TOKEN_REFRESH_RETRY_SECONDS,
)
from .redact import safe_error
from .store import Credentials, CredentialStore

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


class NotLinkedError(RuntimeError):
    """Raised when an operation needs an account that has not been linked."""


class LinkFailedError(RuntimeError):
    """Raised when the authorization code could not be exchanged."""


class AccountManager:
    """Owns the authenticated :class:`MIoTClient` and keeps its token fresh."""

    def __init__(
        self,
        store: CredentialStore,
        cloud_server: str = DEFAULT_CLOUD_SERVER,
        on_credentials_changed: Callable[[Credentials], None] | None = None,
    ) -> None:
        self._store = store
        self._cloud_server = cloud_server
        self._on_credentials_changed = on_credentials_changed
        self._client: MIoTClient | None = None
        self._credentials: Credentials | None = None
        self._pending_uuid: str | None = None
        self._pending_client: MIoTClient | None = None
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def is_linked(self) -> bool:
        return self._store.exists()

    @property
    def client(self) -> MIoTClient:
        """The authenticated client.

        Raises:
            NotLinkedError: if :meth:`async_setup` has not produced a session.
        """
        if self._client is None:
            raise NotLinkedError("Xiaomi account is not linked")
        return self._client

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------

    async def async_begin_link(self) -> str:
        """Start account linking and return the URL the user must visit.

        A client is constructed without credentials purely to obtain the
        authorization URL and the ``state`` value that must accompany the code.
        It is held until :meth:`async_complete_link` consumes it, because the
        state is derived from that instance's device identifier.
        """
        await self._async_discard_pending()

        device_uuid = CredentialStore.new_device_uuid()
        client = MIoTClient(
            uuid=device_uuid,
            redirect_uri=OAUTH_REDIRECT_URI,
            cache_path=CACHE_DIR,
            cloud_server=self._cloud_server,
        )
        await client.init_async()

        self._pending_uuid = device_uuid
        self._pending_client = client
        # gen_oauth_url_async, not gen_auth_url: the latter is the method this
        # one delegates to on the lower-level OAuth client, and is not part of
        # MIoTClient's own surface.
        return await client.gen_oauth_url_async()

    async def async_complete_link(self, code: str, state: str) -> None:
        """Exchange an authorization code for a session and persist it."""
        if self._pending_client is None or self._pending_uuid is None:
            raise LinkFailedError(
                "No linking attempt is in progress. Start over from the add-on page."
            )

        client = self._pending_client
        device_uuid = self._pending_uuid
        try:
            oauth_info = await client.get_access_token_async(code=code, state=state)
        except Exception as err:
            await self._async_discard_pending()
            raise LinkFailedError(str(err)) from err

        credentials = Credentials(
            device_uuid=device_uuid,
            access_token=oauth_info.access_token,
            refresh_token=oauth_info.refresh_token,
            expires_ts=int(oauth_info.expires_ts),
            user_info=(
                oauth_info.user_info.model_dump()
                if oauth_info.user_info is not None
                else None
            ),
        )
        self._store.save(credentials)

        # The pending client already holds a live session -- promote it rather
        # than tearing it down and reconnecting.
        self._credentials = credentials
        self._client = client
        self._pending_client = None
        self._pending_uuid = None
        self._notify(credentials)
        self._start_refresh_loop()
        _LOGGER.info("Xiaomi account linked")

    async def async_unlink(self) -> None:
        """Drop the stored session and shut the client down."""
        await self.async_shutdown()
        self._store.clear()
        self._credentials = None
        _LOGGER.info("Xiaomi account unlinked")

    # ------------------------------------------------------------------
    # Session lifetime
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Restore a stored session. Returns ``False`` when none exists."""
        credentials = self._store.load()
        if credentials is None:
            return False

        client = MIoTClient(
            uuid=credentials.device_uuid,
            redirect_uri=OAUTH_REDIRECT_URI,
            cache_path=CACHE_DIR,
            oauth_info=credentials.to_oauth_info(),
            cloud_server=self._cloud_server,
        )
        await client.init_async()

        # Older credential files predate storing the profile. Fetch it so the
        # next save carries it and the MQTT channel comes up on the next start.
        if credentials.user_info is None:
            try:
                info = await client.get_user_info_async()
                credentials.user_info = info.model_dump()
                self._store.save(credentials)
                _LOGGER.info("Fetched and stored the user profile")
            except Exception as err:
                _LOGGER.warning(
                    "Could not fetch the user profile; device events may be "
                    "delayed: %s",
                    safe_error(err),
                )

        self._client = client
        self._credentials = credentials
        self._start_refresh_loop()
        return True

    async def async_shutdown(self) -> None:
        await self._async_cancel_refresh()
        await self._async_discard_pending()
        if self._client is not None:
            try:
                await self._client.deinit_async()
            except Exception as err:
                _LOGGER.debug("client shutdown raised: %s", safe_error(err))
            self._client = None

    async def async_refresh_token(self) -> None:
        """Renew the access token and persist the new one."""
        async with self._lock:
            if self._client is None or self._credentials is None:
                raise NotLinkedError("Xiaomi account is not linked")
            oauth_info = await self._client.refresh_access_token_async(
                self._credentials.refresh_token
            )
            credentials = Credentials(
                device_uuid=self._credentials.device_uuid,
                access_token=oauth_info.access_token,
                refresh_token=oauth_info.refresh_token,
                expires_ts=int(oauth_info.expires_ts),
                user_info=self._credentials.user_info,
            )
            self._store.save(credentials)
            self._credentials = credentials
            self._notify(credentials)
            _LOGGER.info("Access token refreshed")

    def seconds_until_refresh(self) -> float:
        """Delay before the next refresh attempt."""
        if self._credentials is None:
            return TOKEN_REFRESH_RETRY_SECONDS
        remaining = self._credentials.expires_ts - time.time()
        due_in = remaining - TOKEN_REFRESH_MARGIN_SECONDS
        # A token restored from disk after a long shutdown is already due; the
        # loop must refresh it now rather than serve a dead token for a minute
        # while every camera reports itself unavailable.
        if due_in <= 0:
            return 0.0
        return max(60.0, due_in)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _start_refresh_loop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def _async_cancel_refresh(self) -> None:
        """Cancel the refresh loop and wait for it to unwind.

        Cancelling without awaiting lets a refresh already in flight resume
        into a half-torn-down client.
        """
        task = self._refresh_task
        self._refresh_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _refresh_loop(self) -> None:
        """Refresh ahead of expiry, retrying on failure.

        Retries are deliberately frequent relative to the refresh margin: a
        failure hours before expiry is recoverable, and giving up would strand
        the bridge until someone re-authorized by hand.
        """
        while True:
            try:
                delay = self.seconds_until_refresh()
                if delay > 0:
                    await asyncio.sleep(delay)
                await self.async_refresh_token()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # str(err) from the SDK embeds the request body, which for the
                # refresh endpoint contains the refresh token itself.
                _LOGGER.warning(
                    "Token refresh failed, retrying in %ss: %s",
                    TOKEN_REFRESH_RETRY_SECONDS,
                    safe_error(err),
                )
                await asyncio.sleep(TOKEN_REFRESH_RETRY_SECONDS)

    async def _async_discard_pending(self) -> None:
        if self._pending_client is not None:
            try:
                await self._pending_client.deinit_async()
            except Exception as err:
                _LOGGER.debug("discarding pending client raised: %s", safe_error(err))
        self._pending_client = None
        self._pending_uuid = None

    def _notify(self, credentials: Credentials) -> None:
        if self._on_credentials_changed is not None:
            self._on_credentials_changed(credentials)
