"""Camera entities."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XiaomiCameraConfigEntry
from .api import BridgeError, CameraOffError
from .const import (
    ATTR_LAN_ONLINE,
    ATTR_MODEL,
    ATTR_POWERED_ON,
    CONF_STREAM_CODEC,
    STREAM_CODEC_H264,
    STREAM_CODEC_ORIGINAL,
)
from .coordinator import XiaomiCameraCoordinator
from .entity import XiaomiCameraEntity
from .selection import selected

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XiaomiCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one camera entity per discovered device."""
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
        async_add_entities(XiaomiCamera(coordinator, did) for did in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class XiaomiCamera(XiaomiCameraEntity, Camera):
    """A Xiaomi camera exposed as a Home Assistant camera entity."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: XiaomiCameraCoordinator, did: str) -> None:
        XiaomiCameraEntity.__init__(self, coordinator, did)
        Camera.__init__(self)
        self._attr_unique_id = did

    @property
    def is_streaming(self) -> bool:
        camera = self.camera
        return bool(camera and camera.online and camera.powered_on is not False)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        camera = self.camera
        if camera is None:
            return {}
        return {
            ATTR_MODEL: camera.model,
            ATTR_POWERED_ON: camera.powered_on,
            ATTR_LAN_ONLINE: camera.lan_online,
        }

    async def stream_source(self) -> str | None:
        """Return the RTSP URL for Home Assistant to pull.

        The H.264 one when the add-on offers it. These cameras send H.265,
        which browsers decode only where the hardware happens to support it and
        HomeKit does not accept at all -- so the picture would simply sit
        still, with nothing to explain why. The add-on re-encodes on demand,
        and the original H.265 stream stays published for anything that
        prefers it, such as an NVR.

        Home Assistant pulls from this URL itself; the integration never
        handles video data.
        """
        camera = self.camera
        if camera is None:
            return None
        options = self.coordinator.config_entry.options
        if options.get(CONF_STREAM_CODEC, STREAM_CODEC_H264) == STREAM_CODEC_ORIGINAL:
            return camera.rtsp_url
        # Falls back when the add-on is older than this option: an entry that
        # asks for a stream the bridge does not publish would play nothing.
        return camera.rtsp_url_h264 or camera.rtsp_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image.

        The bridge decodes the frame, so this stays a plain HTTP fetch. A
        switched-off camera is reported distinctly, because the alternative --
        an unexplained timeout -- gives the user nothing to act on.
        """
        try:
            return await self.coordinator.client.async_snapshot(self._did)
        except CameraOffError:
            _LOGGER.debug(
                "%s is switched off, so no image is available", self.entity_id
            )
            return None
        except BridgeError as err:
            _LOGGER.warning("Could not fetch an image for %s: %s", self.entity_id, err)
            return None
