"""Camera entities."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from . import XiaomiCameraConfigEntry
from .api import BridgeError, CameraOffError
from .const import ATTR_LAN_ONLINE, ATTR_MODEL, ATTR_POWERED_ON, DOMAIN
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
    created: list[XiaomiCamera] = []

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
                entity = XiaomiCamera(coordinator, did, key, primary)
                new.append(entity)
                created.append(entity)
        if new:
            async_add_entities(new)
        # Registered asynchronously, so a prompt pass runs once they exist; the
        # coordinator listener below re-runs it on every later poll.
        _sync_registry_names(hass, created)
        hass.async_create_task(_sync_registry_names_soon(hass, list(created)))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


async def _sync_registry_names_soon(
    hass: HomeAssistant, entities: list[XiaomiCamera]
) -> None:
    """Set the registry display names once the entities are registered."""
    await asyncio.sleep(1)
    _sync_registry_names(hass, entities)


def _sync_registry_names(hass: HomeAssistant, entities: list[XiaomiCamera]) -> None:
    """Pin each entity's registry display name to the device name plus label.

    Home Assistant's device page shows the entity's stored name, and for
    `has_entity_name` entities that is the label alone -- so a sub-stream would
    appear as a bare codec under the device heading. The registry `name` is the
    documented way to customise an entity's display name (what a user rename
    writes); setting it to "device + label" makes every surface agree. Only set
    it while unset, so a user's own rename is never undone.
    """
    registry = er.async_get(hass)
    for entity in entities:
        camera = entity.camera
        if camera is None or not camera.name:
            # Name not known yet; a later poll re-runs this once it is.
            continue
        entity_id = registry.async_get_entity_id("camera", DOMAIN, entity.unique_id)
        if entity_id is None:
            # Not registered yet; the follow-up task catches it.
            continue
        entry = registry.async_get(entity_id)
        if entry is None or entry.name is not None:
            continue
        label = entity.name
        if label is UNDEFINED or label is None:
            label = ""
        registry.async_update_entity(
            entity_id, name=f"{camera.name} {label}".strip() if label else camera.name
        )


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
        # `has_entity_name` comes from the base class: Home Assistant composes
        # the entity name as the device name plus the translated stream label,
        # on every surface -- the device page, the entity list, the states --
        # from one source of truth. The labels are therefore label-only
        # ("H.265 720p"), never "{camera} ...", so the device name is not
        # doubled, and the registry never strips a device prefix it then has to
        # add back by hand.
        self._attr_translation_key = stream_key

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
