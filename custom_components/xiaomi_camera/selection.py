"""Which cameras the user chose to bring into Home Assistant.

An account's cameras are not all wanted as entities. Someone with a camera in a
bedroom, or one belonging to a family member, should be able to leave it out --
and change their mind later without removing the integration. The choice is
stored on the config entry rather than in the add-on, because it is about this
Home Assistant, not about the bridge: the streams stay published either way.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_AUTO_ADD, CONF_CAMERAS, CONF_EXCLUDED, DOMAIN


def selected(entry: ConfigEntry, available: Iterable[str]) -> list[str]:
    """The device ids to create entities for, in the order the bridge gave.

    Two ways of saying it, because they answer different questions about a
    camera that did not exist when the choice was made:

    * With new cameras joining on their own, the stored fact is which ones were
      turned *down*. Anything else, including something bought tomorrow, is
      wanted.
    * With that turned off, the stored fact is which ones were picked. A camera
      that appears later stays out until it is picked too.

    Nothing stored at all means everything: an entry created before the Xiaomi
    account was linked has no list to store, and must not leave the user with
    an integration that finds cameras and then ignores them.
    """
    available = list(available)
    if entry.options.get(CONF_AUTO_ADD, True):
        excluded = set(entry.options.get(CONF_EXCLUDED, []))
        return [did for did in available if did not in excluded]

    chosen = entry.options.get(CONF_CAMERAS)
    if chosen is None:
        return available
    wanted = set(chosen)
    return [did for did in available if did in wanted]


def async_remove_unselected(
    hass: HomeAssistant, entry: ConfigEntry, keep: Iterable[str]
) -> None:
    """Delete devices for cameras that are no longer wanted.

    Leaving them behind would fill the device page with entities that are
    permanently unavailable, which reads as a fault rather than as a choice the
    user made. Removing the device takes its entities with it.
    """
    registry = dr.async_get(hass)
    kept = set(keep)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        dids = {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
        if dids and not dids & kept:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
