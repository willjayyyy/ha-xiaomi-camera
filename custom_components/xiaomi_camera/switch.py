"""Power switch entities.

A Xiaomi camera can be online yet switched off. In that state it accepts a
peer-to-peer session and then sends no video at all, which is otherwise
indistinguishable from a broken stream. Exposing the switch gives that state a
name, and lets automations turn a camera on before requesting a snapshot.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XiaomiCameraConfigEntry
from .api import BridgeError
from .coordinator import XiaomiCameraCoordinator
from .entity import XiaomiCameraEntity
from .selection import selected

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XiaomiCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        """Create entities for chosen cameras as the bridge reports them.

        Filtered rather than added and hidden: an entity the user did not ask
        for still shows up in searches, automations and voice assistants.
        """
        wanted = selected(entry, coordinator.data)
        new = [did for did in wanted if did not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(XiaomiCameraPowerSwitch(coordinator, did) for did in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class XiaomiCameraPowerSwitch(XiaomiCameraEntity, SwitchEntity):
    """Turns the camera's lens on and off."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "camera_power"

    def __init__(self, coordinator: XiaomiCameraCoordinator, did: str) -> None:
        super().__init__(coordinator, did)
        self._attr_unique_id = f"{did}_power"

    @property
    def is_on(self) -> bool | None:
        camera = self.camera
        return camera.powered_on if camera else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        try:
            await self.coordinator.client.async_set_power(self._did, value)
        except BridgeError as err:
            _LOGGER.error("Could not switch %s: %s", self.entity_id, err)
            raise
        # The cloud applies the change asynchronously; refreshing confirms the
        # new state rather than assuming it.
        await self.coordinator.async_request_refresh()
