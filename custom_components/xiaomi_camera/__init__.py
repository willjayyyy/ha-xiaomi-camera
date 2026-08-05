"""The Xiaomi Camera integration.

Video never passes through Home Assistant's process: the companion add-on holds
the peer-to-peer session with each camera and republishes it as RTSP, and this
integration only registers entities and points the stream component at those
URLs. The split exists because the vendor's streaming library is a glibc binary
that Home Assistant's own Alpine-based container cannot load.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BridgeClient, BridgeError
from .const import CONF_CAMERAS, CONF_HOST, CONF_PORT
from .coordinator import XiaomiCameraCoordinator
from .selection import async_remove_unselected, selected
from .streams import migrate_options

#: Typed alias so platforms can read `entry.runtime_data` without a cast.
XiaomiCameraConfigEntry = ConfigEntry[XiaomiCameraCoordinator]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SWITCH]


async def async_migrate_entry(
    hass: HomeAssistant, entry: XiaomiCameraConfigEntry
) -> bool:
    """Bring an entry up to the current options shape.

    Version 1 chose one codec for every camera at once. Version 2 chooses per
    camera, and records which stream the pre-existing entity is bound to so
    its identity survives.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        dids = list(entry.options.get(CONF_CAMERAS) or [])
        hass.config_entries.async_update_entry(
            entry, options=migrate_options(dict(entry.options), dids), version=2
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: XiaomiCameraConfigEntry
) -> bool:
    """Set up a bridge from a config entry."""
    client = BridgeClient(
        async_get_clientsession(hass),
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
    )

    try:
        await client.async_health()
    except BridgeError as err:
        raise ConfigEntryNotReady(
            f"Bridge at {client.base_url} is not reachable: {err}"
        ) from err

    coordinator = XiaomiCameraCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # Devices dropped from the selection are removed before the platforms run,
    # so a deselected camera disappears instead of lingering as permanently
    # unavailable -- which reads as a fault rather than as a choice.
    async_remove_unselected(hass, entry, selected(entry, coordinator.data))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: XiaomiCameraConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, entry: XiaomiCameraConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
