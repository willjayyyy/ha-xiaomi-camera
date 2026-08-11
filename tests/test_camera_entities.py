"""Entities created from the selected streams, through real Home Assistant.

`streams.py` decides *which* streams and *what identity*; this covers the
wiring that turns those decisions into entities -- the part unit tests cannot
reach.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bridge.cameras import CameraDescription, CameraRegistry

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="needs Python >= 3.14 and homeassistant; run under .venv314",
)

if sys.version_info >= (3, 14):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.xiaomi_camera.api import BridgeCamera, CameraStream
    from custom_components.xiaomi_camera.const import DOMAIN

_KEYS = ("original", "h265", "h264", "h264_360")


def _camera(did: str = "42", keys: tuple[str, ...] = _KEYS) -> BridgeCamera:
    return BridgeCamera(
        did=did,
        name="Living room",
        model="chuangmi.camera.81ac1",
        manufacturer="chuangmi",
        channel_count=1,
        online=True,
        lan_online=True,
        powered_on=True,
        rtsp_url=f"rtsp://127.0.0.1:8554/camera_{did}",
        rtsp_url_h264=f"rtsp://127.0.0.1:8554/camera_{did}_h264",
        streams=tuple(
            CameraStream(
                key=key,
                codec=(
                    key
                    if key == "original"
                    else ("h265" if key.startswith("h265") else "h264")
                ),
                height=360 if key.endswith("360") else None,
                url=(
                    f"rtsp://127.0.0.1:8554/camera_{did}"
                    if key == "original"
                    else f"rtsp://127.0.0.1:8554/camera_{did}_{key}"
                ),
            )
            for key in keys
        ),
    )


async def _setup(hass, options: dict) -> MockConfigEntry:
    """Load the integration with a stubbed bridge."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options=options,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(return_value=[_camera()])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_one_entity_is_created_per_selected_stream(hass) -> None:
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264", "h264_360"]},
        },
    )

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264_360") is not None


async def test_variant_entities_name_the_camera(hass) -> None:
    """Every stream entity's full name carries the device name.

    Home Assistant composes it from the device name and the label, so the
    integration never writes the device name into a label itself -- both the
    registry and the frontend strip such a prefix back off. This asserts the
    composed result that HomeKit, voice assistants and the entity picker read,
    which is where knowing *which* camera actually matters.
    """
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264", "h264_360"]},
        },
    )

    registry = er.async_get(hass)
    primary = registry.async_get_entity_id("camera", DOMAIN, "42")
    variant = registry.async_get_entity_id("camera", DOMAIN, "42_h264_360")
    assert primary is not None and variant is not None

    assert (
        hass.states.get(primary).attributes["friendly_name"]
        == "Living room H.264 full size"
    )
    assert (
        hass.states.get(variant).attributes["friendly_name"] == "Living room H.264 360p"
    )


async def test_the_original_entity_is_named_after_the_device(hass) -> None:
    """The root carries the camera's own encoding, so it states no codec."""
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "original",
            "camera_streams": {"42": ["original"]},
        },
    )

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("camera", DOMAIN, "42")
    assert entity_id is not None
    assert hass.states.get(entity_id).attributes["friendly_name"] == "Living room"


async def test_the_primary_entity_keeps_the_bare_device_id(hass) -> None:
    """The identity every HomeKit pairing and automation is bound to.

    Home Assistant treats `unique_id` as an entity's identity. If this ever
    became `42_h264`, Home Assistant would create a new entity and abandon the
    old one -- taking its pairings, automations, dashboard cards and history
    with it, silently, on upgrade.
    """
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264"]},
        },
    )

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264") is None


async def test_which_stream_is_primary_moves_the_bare_id(hass) -> None:
    """An entry migrated from v2 keeps the root as its primary."""
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h265",
            "camera_streams": {"42": ["h265", "h264"]},
        },
    )

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h265") is None


async def test_each_entity_points_at_its_own_stream(hass) -> None:
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264", "h264_360"]},
        },
    )

    registry = er.async_get(hass)
    primary = registry.async_get_entity_id("camera", DOMAIN, "42")
    scaled = registry.async_get_entity_id("camera", DOMAIN, "42_h264_360")

    from homeassistant.components.camera import async_get_stream_source

    assert await async_get_stream_source(hass, primary) == (
        "rtsp://127.0.0.1:8554/camera_42_h264"
    )
    assert await async_get_stream_source(hass, scaled) == (
        "rtsp://127.0.0.1:8554/camera_42_h264_360"
    )


async def test_unticking_a_stream_removes_its_entity(hass) -> None:
    """Home Assistant keeps an entity nothing re-adds -- forever, unavailable.

    Left behind, it is indistinguishable from a broken camera.
    """
    entry = await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264", "h264_360"]},
        },
    )
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264_360") is not None

    # An options update triggers a full config-entry reload, which builds a
    # fresh `BridgeClient` outside the patch `_setup` installed -- so the
    # mock has to be reinstated for this window too, or the reload hits a
    # real (and in tests, blocked) socket.
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(return_value=[_camera()])
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, "camera_streams": {"42": ["h264"]}},
        )
        await hass.async_block_till_done()

    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264_360") is None
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None


async def test_a_poll_reporting_fewer_streams_does_not_remove_an_entity(hass) -> None:
    """I3: entity removal must follow the stored selection, not a live poll.

    go2rtc restarting, or the add-on reporting a shortened stream list on one
    refresh, is not the user deselecting anything. The spec's own error
    handling calls for the entity to become unavailable, not gone -- gone
    means the HomeKit pairing, automations, dashboard cards and history
    attached to it are gone too, and nothing brings them back.
    """
    entry = await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264", "h264_360"]},
        },
    )
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264_360") is not None

    # A reload where the add-on now reports only "h264" for this camera --
    # nothing the user did, and the stored selection is unchanged.
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(
            return_value=[_camera(keys=("h265", "h264"))]
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get_entity_id("camera", DOMAIN, "42_h264_360") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None


async def test_all_of_a_cameras_entities_share_one_device(hass) -> None:
    """One physical camera is one device, however many streams it has.

    Nothing in `identifiers` may vary per stream. Adding the stream key there
    would split a single camera into one device per stream -- breaking the
    device page, device-scoped automations, and how the HomeKit bridge groups
    accessories.
    """
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h265", "h264", "h264_360"]},
        },
    )

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entities = [
        entry for entry in entity_registry.entities.values() if entry.platform == DOMAIN
    ]
    assert len(entities) >= 3

    device_ids = {entry.device_id for entry in entities}
    assert len(device_ids) == 1, "one camera must be one device"

    device = device_registry.async_get(device_ids.pop())
    assert device is not None
    assert device.identifiers == {(DOMAIN, "42")}


async def test_a_camera_absent_from_camera_streams_keeps_its_bare_id(hass) -> None:
    """A migrated entry with a non-default primary and no stored selection.

    Two real populations land here: entries created before the Xiaomi account
    was linked, and cameras added later by `auto_add`, which is on by default
    while `camera_streams` is only written when the checklist is saved. Either
    way, the entity that already exists for this camera carries the bare
    `<did>` unique_id, bound to `primary_stream`. If the stream selection
    disagreed with that, `wanted_unique_ids` would compute a different
    identity for the very entity it is supposed to keep -- deleting it.
    """
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            # No "camera_streams" key for "42" at all.
        },
    )

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None
    assert registry.async_get_entity_id("camera", DOMAIN, "42_h265") is None


async def test_an_entry_with_no_stream_options_still_has_a_primary(hass) -> None:
    """A fresh entry that predates the per-camera options.

    Nothing has written `primary_stream` or `camera_streams` yet, so both the
    selection and the identity fall back to their defaults. If those defaults
    disagree, the only entity created is not the primary one and the bare
    device id -- the identity everything else is bound to -- belongs to
    nothing.
    """
    await _setup(hass, {"cameras": ["42"]})

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("camera", DOMAIN, "42") is not None


async def test_entities_never_strip_the_device_prefix(hass) -> None:
    """The device page shows the device name on every stream entity.

    With `has_entity_name` Home Assistant composes "device + label" for the
    name on every surface -- the device page, the entity list, the states --
    and does not strip a device prefix into `original_name_unprefixed`. A
    bare "H.264 360p" under the device heading would otherwise read as a
    fault rather than a choice.
    """
    await _setup(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "original",
            "camera_streams": {"42": ["original", "h264_360"]},
        },
    )

    registry = er.async_get(hass)
    for uid in ("42", "42_h264_360"):
        entry = registry.async_get(registry.async_get_entity_id("camera", DOMAIN, uid))
        assert entry is not None
        assert entry.has_entity_name is True
        assert entry.original_name_unprefixed is None

    original = registry.async_get_entity_id("camera", DOMAIN, "42")
    variant = registry.async_get_entity_id("camera", DOMAIN, "42_h264_360")
    assert hass.states.get(original).attributes["friendly_name"] == "Living room"
    assert (
        hass.states.get(variant).attributes["friendly_name"] == "Living room H.264 360p"
    )


# ----------------------------------------------------------------------
# Cameras the vendor library refuses.
#
# Unlike everything above, `CameraRegistry.async_refresh` (`bridge.cameras`)
# does not need Home Assistant at all -- only the `miot` stub `conftest.py`
# already provides. These tests live in this file because that is where the
# task that added them was pointed; the `pytestmark` skip above still applies
# to them, since a module-level mark covers every test in the module, so they
# only run on the same Python >= 3.14 interpreter as the rest of this file.
# ----------------------------------------------------------------------

#: The one model these tests need treated as denied. `get_cameras_async`
#: applies the vendor SDK's own filtering server-side; `_FakeCameraClient`
#: below stands in for that filtering, not for `CameraRegistry`'s own logic,
#: so it is free to hard-code just enough of the real 53-model denylist
#: (`xiaomi-miloco`'s `camera_extra_info.yaml`, read at the commit
#: `addon/requirements.txt` pins) to exercise the refused-model path.
_VENDOR_DENYLIST = {
    "chuangmi.camera.ipc019",
    "chuangmi.camera.v2",
    "chuangmi.camera.021a04",
    "chuangmi.camera.no-known-alternative",
}


def _device(did: str, model: str, *, online: bool = True) -> SimpleNamespace:
    """A raw device as `MIoTClient.get_devices_async` returns it, unfiltered."""
    return SimpleNamespace(
        did=did,
        name=did,
        model=model,
        manufacturer="Xiaomi",
        online=online,
        lan_online=True,
        is_set_pincode=0,
        channel_count=1,
    )


class _FakeCameraClient:
    """Stands in for `MIoTClient`, for `CameraRegistry.async_refresh`.

    Mimics the one distinction the refused-model path depends on:
    `get_cameras_async` applies the vendor's own filtering (`_VENDOR_DENYLIST`
    here), while `get_devices_async` -- confirmed unfiltered by reading the
    real SDK source at the pinned commit, since only the native library
    underneath it is closed -- returns every device regardless.
    """

    class _HttpClient:
        async def get_props_async(self, params):
            return [{"did": p.did, "code": 0, "value": True} for p in params]

    def __init__(self, devices: list[SimpleNamespace]) -> None:
        self._devices = {d.did: d for d in devices}
        self._http_client = self._HttpClient()

    async def get_devices_async(self) -> dict[str, SimpleNamespace]:
        return dict(self._devices)

    async def get_cameras_async(self) -> dict[str, SimpleNamespace]:
        # Mirrors `is_camera_model`'s own two checks: an allowed device class
        # first (real allow_classes is `camera`/`wifispeaker`/`controller`;
        # only `camera` matters to these tests), then the denylist. The index
        # is unguarded, deliberately -- `is_camera_model` itself does
        # `model.split(".")[1]` with no length check, so a device with no dot
        # in its model string raises `IndexError` here exactly as it would
        # against the real SDK, aborting this whole dict comprehension rather
        # than quietly excluding just that one device.
        return {
            did: d
            for did, d in self._devices.items()
            if d.model.split(".")[1] == "camera" and d.model not in _VENDOR_DENYLIST
        }


async def _describe(devices: list[SimpleNamespace]) -> list[CameraDescription]:
    registry = CameraRegistry(_FakeCameraClient(devices))
    return await registry.async_refresh()


def _by_did(descriptions: list[CameraDescription], did: str) -> CameraDescription:
    matches = [d for d in descriptions if d.did == did]
    assert len(matches) == 1, f"expected exactly one description for {did!r}"
    return matches[0]


async def test_a_refused_model_is_listed_rather_than_silently_dropped() -> None:
    """A camera missing from the list with no explanation reads as a broken
    add-on. It is the vendor library refusing that model, and saying so is
    also where the second path is discovered."""
    descriptions = await _describe(
        [
            _device("aaa", "chuangmi.camera.81ac1"),
            _device("bbb", "chuangmi.camera.ipc019"),
        ]
    )
    assert {d.did for d in descriptions} == {"aaa", "bbb"}
    assert _by_did(descriptions, "aaa").support == "full"
    # `ipc019` is one of the 6 models go2rtc reaches reliably over `cs2`
    # (design notes 1.4), so this add-on marks it "limited" -- not "full",
    # which is reserved for a model it actually streams today, and not
    # "unsupported", which would understate that a real path exists.
    assert _by_did(descriptions, "bbb").support == "limited"


async def test_a_refused_model_is_not_offered_a_working_stream() -> None:
    """It is listed so it can be explained, not so it can appear to work."""
    descriptions = await _describe([_device("bbb", "chuangmi.camera.ipc019")])
    assert _by_did(descriptions, "bbb").online is False


async def test_one_device_with_an_odd_model_string_does_not_hide_the_cameras() -> None:
    """Whatever else is on the account is not this add-on's to validate.

    Every model seen so far reads `<vendor>.<class>.<variant>`, but the list
    comes from the user's account, and one device that does not must not turn
    `/api/cameras` into a 500 -- every camera would vanish at once, which
    reads as the add-on being broken rather than as one odd device.
    """
    descriptions = await _describe(
        [_device("aaa", "chuangmi.camera.81ac1"), _device("odd", "gateway")]
    )
    assert {d.did for d in descriptions} == {"aaa"}


async def test_the_wire_format_carries_the_publishing_decision() -> None:
    """`publishable` is one property for a reason -- see its docstring. A
    payload that stated only `support` would hand the same question back to
    the page, which is where the fourth support level would be forgotten."""
    descriptions = await _describe(
        [
            _device("aaa", "chuangmi.camera.81ac1"),
            _device("bbb", "chuangmi.camera.ipc019"),
        ]
    )
    assert _by_did(descriptions, "aaa").as_dict()["publishable"] is True
    assert _by_did(descriptions, "bbb").as_dict()["publishable"] is False


async def test_a_refused_models_support_level_follows_the_go2rtc_protocol() -> None:
    """The two paths go2rtc offers refused models are not equally safe.

    A model reachable over `cs2` gets "limited" -- go2rtc's own maintainer
    calls that protocol reliable. A model reachable only over `tutk` --
    called "the worst thing that's ever happened to the P2P world" by that
    same maintainer -- is not distinguished from a model with no reported
    alternative at all: both fall back to "unsupported", so this add-on never
    steers a user toward handing over their account password for a path this
    barely works.
    """
    descriptions = await _describe(
        [
            # cs2, reliable.
            _device("cs2", "chuangmi.camera.021a04"),
            # tutk, risky.
            _device("tutk", "chuangmi.camera.v2"),
            # On the vendor's denylist but not reported working by go2rtc at all.
            _device("neither", "chuangmi.camera.no-known-alternative"),
        ]
    )
    assert _by_did(descriptions, "cs2").support == "limited"
    assert _by_did(descriptions, "tutk").support == "unsupported"
    assert _by_did(descriptions, "neither").support == "unsupported"


async def test_a_non_camera_device_on_the_deny_list_is_not_listed_as_a_camera() -> None:
    """`is_camera_model` denies non-camera classes too; those never belong here."""
    descriptions = await _describe([_device("light", "yeelink.light.col")])
    assert descriptions == []
