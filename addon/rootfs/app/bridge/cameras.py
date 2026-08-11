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


# ---------------------------------------------------------------------------
# Support level for models the vendor library refuses.
#
# ``is_camera_model`` (in the vendor SDK's ``camera.py``) rejects 53 models
# outright via a bundled deny list -- see the module docstring above. That
# list is "the vendor confirmed these don't work", not "nothing else can be
# done": go2rtc's native Xiaomi client (github.com/AlexxIT/go2rtc) implements
# the camera's P2P protocol itself, independent of this vendor library, and
# its users have reported success with some of those same refused models.
# This add-on does not speak that protocol yet -- that is later work -- but a
# user who owns one of these models deserves to be told an alternative exists
# at all, rather than seeing an empty camera list with no explanation.
#
# The alternative is not equally trustworthy for every model, and that
# difference matters enough to keep the two lists apart rather than folding
# them into one "there's a workaround" bucket. go2rtc speaks two protocols to
# reach these cameras: "cs2", which its maintainer treats as reliable, and
# "tutk", which the same maintainer has called "the worst thing that's ever
# happened to the P2P world". Recommending the tutk path to someone who would
# have to hand their Xiaomi account password to a second piece of software to
# try it is not a favour if it barely works -- so cs2 models are marked
# "limited" (a real path exists, just not built into this add-on yet) and
# tutk models fall back to "unsupported", the same bucket as every refused
# model with no alternative reported at all.
#
# Source: github.com/AlexxIT/go2rtc issue #1982 (community-reported Xiaomi
# camera compatibility), as compiled in
# docs/superpowers/specs/2026-08-07-v2-design-notes.md section 1.4. Read
# 2026-08-07. These lists go stale if that issue gains or loses reports, or
# once a future release wires this add-on's own alternative path -- at which
# point cs2 models stop being "limited" and become "full" for real.
_GO2RTC_CS2_RELIABLE_MODELS = frozenset(
    {
        "chuangmi.camera.021a04",
        "chuangmi.camera.026c02",
        "chuangmi.camera.029a02",
        "chuangmi.camera.ip029a",
        "chuangmi.camera.ipc019",
        "isa.camera.hlc6",
    }
)

_GO2RTC_TUTK_RISKY_MODELS = frozenset(
    {
        "chuangmi.camera.ipc019e",
        "chuangmi.camera.v2",
        "chuangmi.camera.v6",
        "chuangmi.camera.xiaobai",
        "isa.camera.df3",
        "isa.camera.isc5",
        "isa.camera.isc5c1",
        "lumi.camera.gwagl01",
        "mijia.camera.v1",
        "mijia.camera.v3",
    }
)


def _device_class(model: str) -> str:
    """The device class in a MIoT model string, or ``""`` if it has none.

    Models read ``<vendor>.<class>.<variant>``, and every device the cloud has
    returned so far does. But this list is whatever is on the user's account,
    and one device with an unexpected model string must not be able to take
    the whole camera list down with an ``IndexError`` -- ``/api/cameras``
    would answer 500 and every camera would disappear at once, which reads as
    the add-on being broken rather than as one odd device. An unrecognisable
    model is simply not a camera as far as this is concerned.
    """
    parts = model.split(".")
    return parts[1] if len(parts) > 1 else ""


def _support_for_refused_model(model: str) -> str:
    """Support level for a camera model the vendor library refuses.

    Never returns ``"full"`` -- that value is reserved for models this add-on
    actually streams, which ``async_refresh`` derives separately and never
    routes through here.
    """
    if model in _GO2RTC_CS2_RELIABLE_MODELS:
        return "limited"
    return "unsupported"


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
    #: ``"full"`` for a model this add-on actually streams; ``"limited"`` or
    #: ``"unsupported"`` for one the vendor library refuses -- see
    #: ``_support_for_refused_model`` for how the latter two are told apart.
    support: str

    @property
    def publishable(self) -> bool:
        """Whether this camera may be published, streamed, or offered as an
        entity at all.

        The one predicate every consumer that touches a camera's stream must
        ask -- go2rtc's stream table, the session manager, the control
        plane's camera list -- rather than each re-deriving "does this camera
        work" from :attr:`support` on its own. A refused camera was once
        simply absent from this list; now that it is present so it can be
        explained, every one of those consumers would otherwise have to learn
        that fact independently, and independent answers to the same
        question are exactly what has drifted apart on this project before
        (see the multi-stream default-selection incident in CLAUDE.md).
        Routing every check through this property instead means a fourth
        support level, if one is ever added, cannot silently become
        publishable by accident -- it has to be added here, once.
        """
        return self.support == "full"

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
            "support": self.support,
            # Sent as its own field rather than left for the reader to derive
            # from `support`. The property above exists so that one function
            # answers "may this camera be published"; dropping it from the
            # wire would hand the same question back to every consumer on the
            # other side, and a fourth support level would then have to be
            # remembered in each of them.
            "publishable": self.publishable,
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
        """Re-read the camera list and their power state.

        Also looks past the vendor library's own filtering. ``get_cameras_async``
        silently drops any model on the 53-model deny list ``is_camera_model``
        consults, which is indistinguishable, to a user who owns one, from the
        add-on simply not working. ``get_devices_async`` returns every device
        on the account with no such filtering -- confirmed by reading the SDK
        source at the commit this add-on pins, since the SDK's Python layer is
        open even though the P2P library underneath it is not. The refused
        models are exactly the camera-class devices present in that unfiltered
        list but absent from ``self._cameras``, and are reported alongside the
        supported ones so this reads as "your camera is on a list" rather than
        as a broken add-on.

        ``get_cameras_async`` itself runs the same unguarded
        ``model.split(".")[1]`` that ``_device_class`` below guards against,
        over every device on the account in one loop with no per-device
        isolation -- confirmed by reading the same pinned SDK source. One
        dotless model string there raises ``IndexError`` before any of this
        method's own guarding runs, which is exactly the crash this method
        exists to prevent, just one call earlier. Caught here rather than
        left to reach ``/api/cameras`` as a 500 that empties the entire
        camera list over one unrelated device.
        """
        all_devices = await self._client.get_devices_async()
        try:
            self._cameras = await self._client.get_cameras_async()
        except IndexError:
            culprits = [
                did
                for did, info in all_devices.items()
                if _device_class(info.model) == ""
            ]
            _LOGGER.error(
                "get_cameras_async() raised on malformed model string(s) for "
                "device(s) %s; camera list for this refresh falls back to the "
                "unfiltered device list below",
                ", ".join(culprits) if culprits else "<undetermined>",
            )
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
                    support="full",
                )
            )

        refused_count = 0
        for did, info in all_devices.items():
            if did in self._cameras:
                continue
            # ``is_camera_model`` keys its allow/deny lookup on this same
            # split (device class is the second dot-separated segment of the
            # model string). Devices of other classes -- lights, speakers,
            # whatever else shares the account -- do not belong on a camera
            # list even though they too were dropped by ``get_cameras_async``.
            if _device_class(info.model) != "camera":
                continue
            refused_count += 1
            descriptions.append(
                CameraDescription(
                    did=did,
                    name=info.name,
                    model=info.model,
                    manufacturer=info.manufacturer,
                    channel_count=1,
                    # Always False, never the cloud's own answer for this
                    # device: there is no camera-control channel to a refused
                    # model at all, so nothing here can ever open a stream for
                    # it regardless of what the cloud reports about it being
                    # reachable.
                    online=False,
                    lan_online=bool(getattr(info, "lan_online", False)),
                    powered_on=None,
                    requires_pin=False,
                    support=_support_for_refused_model(info.model),
                )
            )

        _LOGGER.info(
            "Discovered %d supported camera(s), %d refused by the vendor library",
            len(self._cameras),
            refused_count,
        )
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
