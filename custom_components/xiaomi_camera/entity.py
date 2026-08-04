"""Shared entity behaviour."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BridgeCamera
from .const import DOMAIN
from .coordinator import XiaomiCameraCoordinator


class XiaomiCameraEntity(CoordinatorEntity[XiaomiCameraCoordinator]):
    """Base entity bound to one physical camera."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XiaomiCameraCoordinator, did: str) -> None:
        super().__init__(coordinator)
        self._did = did

    @property
    def camera(self) -> BridgeCamera | None:
        return self.coordinator.data.get(self._did)

    @property
    def available(self) -> bool:
        camera = self.camera
        return super().available and camera is not None and camera.online

    @property
    def device_info(self) -> DeviceInfo:
        camera = self.camera
        return DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=camera.name if camera else self._did,
            manufacturer=camera.manufacturer if camera else "Xiaomi",
            model=camera.model if camera else None,
        )
