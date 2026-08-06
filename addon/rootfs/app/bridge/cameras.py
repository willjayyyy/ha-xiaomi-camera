"""Camera inventory and power state.

Device filtering delegates to the vendor SDK's ``is_camera_model``, which
consults a bundled table of allowed device classes and an explicit deny list of
models the vendor does not support. Filtering by MIoT spec URN instead looks
equivalent but silently accepts denied models, leaving users with an entity
that can never connect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from miot.client import MIoTClient
from miot.types import MIoTCameraInfo, MIoTGetPropertyParam

from .const import POWER_PIID, POWER_SIID

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraDescription:
    """A camera as presented to Home Assistant."""

    did: str
    name: str
    model: str
    manufacturer: str
    channel_count: int
    online: bool
    lan_online: bool
    #: ``None`` when the power state could not be read. Distinguishing "off"
    #: from "unknown" matters: a camera that is on but unreachable is a network
    #: problem, while one that is off simply has nothing to send.
    powered_on: bool | None
    requires_pin: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "did": self.did,
            "name": self.name,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "channel_count": self.channel_count,
            "online": self.online,
            "lan_online": self.lan_online,
            "powered_on": self.powered_on,
            "requires_pin": self.requires_pin,
        }


class CameraRegistry:
    """Discovers cameras and tracks the state Home Assistant needs."""

    def __init__(self, client: MIoTClient) -> None:
        self._client = client
        self._cameras: dict[str, MIoTCameraInfo] = {}
        #: Lens switches as last read, alongside the cameras they belong to.
        #: Empty until the first refresh, which reads "not known" rather than
        #: "off" -- see :meth:`power_state`.
        self._power_states: dict[str, bool | None] = {}

    @property
    def raw(self) -> dict[str, MIoTCameraInfo]:
        """SDK objects, needed verbatim when creating a camera instance.

        ``create_camera_instance_async`` validates its argument against a model
        with eleven required fields, so a hand-built dictionary is rejected.
        """
        return self._cameras

    def get(self, did: str) -> MIoTCameraInfo | None:
        return self._cameras.get(did)

    def power_state(self, did: str) -> bool | None:
        """This camera's lens switch as of the last refresh.

        Kept so that asking is free. A camera that is off connects normally
        and simply sends nothing, so anything opening a stream wants to know
        first -- and every one of those doing its own cloud read would put a
        request behind each preview, for a value that was already fetched to
        answer `/api/cameras`.

        ``None`` where it has not been read or could not be: the distinction
        from ``False`` is the one :class:`CameraDescription` makes, and it
        matters for the same reason there.
        """
        return self._power_states.get(did)

    async def async_read_power_state(self, did: str) -> bool | None:
        """Read one camera's lens switch now, rather than recalling it.

        For the moment a stream stops: the case worth naming is a camera
        switched off *since* the list was last read, and that is precisely
        what :meth:`power_state` cannot report -- it would still be answering
        with the value that was true when the camera was still sending.

        One camera, not all of them, because this runs on a failure rather
        than on a schedule and should cost accordingly.
        """
        states = await self._async_read_power_states([did])
        self._power_states.update(states)
        return states.get(did)

    async def async_refresh(self) -> list[CameraDescription]:
        """Re-read the camera list and their power state."""
        self._cameras = await self._client.get_cameras_async()
        power_states = await self._async_read_power_states(list(self._cameras))
        self._power_states = power_states

        descriptions: list[CameraDescription] = []
        for did, info in self._cameras.items():
            descriptions.append(
                CameraDescription(
                    did=did,
                    name=info.name,
                    model=info.model,
                    manufacturer=info.manufacturer,
                    channel_count=info.channel_count or 1,
                    online=bool(info.online),
                    lan_online=bool(info.lan_online),
                    powered_on=power_states.get(did),
                    requires_pin=bool(getattr(info, "is_set_pincode", 0)),
                )
            )
        _LOGGER.info("Discovered %d supported camera(s)", len(descriptions))
        return descriptions

    async def async_set_power(self, did: str, value: bool) -> None:
        """Switch a camera on or off."""
        from miot.types import MIoTSetPropertyParam

        await self._client._http_client.set_prop_async(
            MIoTSetPropertyParam(did=did, siid=POWER_SIID, piid=POWER_PIID, value=value)
        )

    async def _async_read_power_states(self, dids: list[str]) -> dict[str, bool | None]:
        """Read the camera-control power switch for each device.

        Failures degrade to ``None`` rather than propagating: an unreadable
        power state should not prevent the rest of the camera list from loading.
        """
        if not dids:
            return {}

        params = [
            MIoTGetPropertyParam(did=did, siid=POWER_SIID, piid=POWER_PIID)
            for did in dids
        ]
        try:
            results = await self._client._http_client.get_props_async(params)
        except Exception as err:
            _LOGGER.warning("Could not read camera power state: %s", err)
            return {did: None for did in dids}

        states: dict[str, bool | None] = {did: None for did in dids}
        for item in results or []:
            did = str(item.get("did", ""))
            if did in states and item.get("code") == 0:
                states[did] = bool(item.get("value"))
        return states
