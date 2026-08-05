"""Camera entities."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import XiaomiCameraConfigEntry
from .api import BridgeError, CameraOffError
from .const import ATTR_LAN_ONLINE, ATTR_MODEL, ATTR_POWERED_ON
from .coordinator import XiaomiCameraCoordinator
from .entity import XiaomiCameraEntity
from .selection import selected
from .streams import primary_stream, selected_streams, unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XiaomiCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one camera entity per selected stream."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        """Create entities for each camera's chosen streams.

        Filtered rather than added and hidden: an entity the user did not ask
        for still shows up in searches, automations and voice assistants.
        """
        primary = primary_stream(dict(entry.options))
        new: list[XiaomiCamera] = []
        for did in selected(entry, coordinator.data):
            camera = coordinator.data.get(did)
            if camera is None:
                continue
            available = [stream.key for stream in camera.streams]
            for key in selected_streams(dict(entry.options), did, available):
                if (did, key) in known:
                    continue
                known.add((did, key))
                new.append(XiaomiCamera(coordinator, did, key, primary))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class XiaomiCamera(XiaomiCameraEntity, Camera):
    """A Xiaomi camera exposed as a Home Assistant camera entity."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: XiaomiCameraCoordinator,
        did: str,
        stream_key: str,
        primary_key: str,
    ) -> None:
        XiaomiCameraEntity.__init__(self, coordinator, did)
        Camera.__init__(self)
        self._stream_key = stream_key
        self._primary_key = primary_key
        self._attr_unique_id = unique_id(did, stream_key, primary_key)
        # Every entity names the camera and its stream itself, rather than
        # relying on Home Assistant's device-name prefixing (`has_entity_name`),
        # so a consumer that shows only the entity name -- the device page, a
        # voice assistant -- still knows which camera and which stream it is.
        # With `has_entity_name` off Home Assistant ignores `translation_key`
        # for the name, so the label is read by hand in `_stream_label`.
        self._attr_has_entity_name = False
        self._attr_translation_key = stream_key

    @property
    def name(self) -> str:
        """The camera name plus the stream label, on every entity."""
        camera = self.camera
        device = camera.name if camera else self._did
        label = self._stream_label()
        return device if not label else f"{device} {label}"

    def _stream_label(self) -> str:
        """The translated stream name.

        Read from the same platform translations table `has_entity_name`
        entities use, so there is exactly one source of labels; the `{camera}`
        placeholder is dropped because the device name is already prepended.
        """
        if self.platform_data is None:
            return self._stream_key
        key = (
            f"component.{self.platform_data.platform_name}."
            f"entity.{self.platform_data.domain}.{self._stream_key}.name"
        )
        template = self.platform_data.platform_translations.get(key, self._stream_key)
        return template.replace("{camera}", "").strip()

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
        """The RTSP URL for this entity's stream.

        Home Assistant pulls from this URL itself; the integration never
        handles video data.
        """
        camera = self.camera
        if camera is None:
            return None
        return camera.stream_url(self._stream_key) or None

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
