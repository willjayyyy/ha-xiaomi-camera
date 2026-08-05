"""Entities created from the selected streams, through real Home Assistant.

`streams.py` decides *which* streams and *what identity*; this covers the
wiring that turns those decisions into entities -- the part unit tests cannot
reach.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

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

_KEYS = ("h265", "h264", "h264_360")


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
                codec="h265" if key.startswith("h265") else "h264",
                height=360 if key.endswith("360") else None,
                url=f"rtsp://127.0.0.1:8554/camera_{did}_{key}",
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
    """Variant entities carry the device name in their own name.

    The primary can rely on Home Assistant's device-name prefixing. Variants
    cannot: HomeKit and voice assistants show only the entity name, so it has
    to say which camera it is by itself.
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

    assert hass.states.get(primary).attributes["friendly_name"] == "Living room H.264"
    assert (
        hass.states.get(variant).attributes["friendly_name"] == "Living room H.264 360p"
    )


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
    """An entry migrated from `original` has h265 as its primary."""
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
