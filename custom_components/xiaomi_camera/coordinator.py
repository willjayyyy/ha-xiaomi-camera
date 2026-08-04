"""Polls the bridge for the camera inventory."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeCamera, BridgeClient, BridgeError, BridgeNotLinkedError
from .const import ADDON_NAME, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

#: How long the add-on may report "not signed in" before the user is asked to
#: do something about it. Comfortably longer than a restart, which takes a few
#: seconds, and short enough that a genuinely signed-out bridge does not sit
#: there unexplained.
_UNLINKED_POLLS_BEFORE_ASKING = 5

#: Identifies the repair raised when the add-on has no Xiaomi session.
_UNLINKED_ISSUE = "not_linked"


class XiaomiCameraCoordinator(DataUpdateCoordinator[dict[str, BridgeCamera]]):
    """Keeps the camera list and their reachability in sync with the bridge."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: BridgeClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        #: Consecutive polls that found no Xiaomi session. Counted so a restart
        #: is told apart from an account that really is signed out.
        self._unlinked_polls = 0

    async def _async_update_data(self) -> dict[str, BridgeCamera]:
        try:
            cameras = await self.client.async_cameras()
        except BridgeNotLinkedError as err:
            self._unlinked_polls += 1
            if self._unlinked_polls >= _UNLINKED_POLLS_BEFORE_ASKING:
                self._async_report_unlinked()
            # Always retryable. Reporting this as an authentication failure
            # stopped Home Assistant polling until someone opened a dialog, so
            # an add-on restart that happened to overlap a poll left the
            # cameras unavailable indefinitely -- and the dialog could do
            # nothing anyway: the Xiaomi account belongs to the add-on and
            # never enters Home Assistant.
            raise UpdateFailed(str(err)) from err
        except BridgeError as err:
            raise UpdateFailed(str(err)) from err
        if self._unlinked_polls:
            self._unlinked_polls = 0
            ir.async_delete_issue(self.hass, DOMAIN, _UNLINKED_ISSUE)
        return {camera.did: camera for camera in cameras}

    def _async_report_unlinked(self) -> None:
        """Put the missing sign-in where Home Assistant puts such things.

        A repair rather than a reauthentication prompt. There is nothing to
        re-authenticate here -- the credentials live in the add-on -- and a
        prompt whose only content is "go elsewhere, then come back and press
        this" costs the user a round trip to tell the integration something it
        can see for itself. This says the same thing without stopping
        anything, and withdraws itself once the add-on reports a session.
        """
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            _UNLINKED_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_UNLINKED_ISSUE,
            translation_placeholders={"addon": ADDON_NAME},
        )
