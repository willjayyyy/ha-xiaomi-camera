"""Choosing streams per camera, through the real options flow.

The only part of this change a user operates directly. What each camera
offers has to come from what the add-on declared for that camera -- the two
components ship separately, so a list hardcoded here goes stale as soon as
either side moves.
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
    from homeassistant.data_entry_flow import FlowResultType
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.xiaomi_camera.api import BridgeCamera, CameraStream
    from custom_components.xiaomi_camera.const import DOMAIN

_KEYS = ("h265", "h264", "h264_360")


def _camera(did: str = "42") -> BridgeCamera:
    return BridgeCamera(
        did=did,
        name="Living room",
        model="chuangmi.camera.81ac1",
        manufacturer="chuangmi",
        channel_count=1,
        online=True,
        lan_online=True,
        powered_on=True,
        rtsp_url=f"rtsp://127.0.0.1:8554/camera_{did}_h265",
        rtsp_url_h264=f"rtsp://127.0.0.1:8554/camera_{did}_h264",
        streams=tuple(
            CameraStream(
                key=key,
                codec="h265" if key.startswith("h265") else "h264",
                height=360 if key.endswith("360") else None,
                url=f"rtsp://127.0.0.1:8554/camera_{did}_{key}",
            )
            for key in _KEYS
        ),
    )


async def _loaded_entry(hass, options: dict) -> MockConfigEntry:
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


async def _pick_cameras(hass, entry, cameras: list[str]) -> dict:
    """Open the options flow and complete step one (the camera checklist)."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"cameras": cameras, "auto_add": True},
    )


async def test_the_stream_choices_come_from_what_the_add_on_declared(hass) -> None:
    """Not from a list hardcoded in the integration.

    The add-on and the integration ship separately. A camera here publishes
    only three variants; the stream step must offer exactly those, so that an
    add-on which gains or loses a variant is reflected without changing this
    code.
    """
    entry = await _loaded_entry(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h265",
            "camera_streams": {"42": ["h265"]},
        },
    )

    result = await _pick_cameras(hass, entry, ["42"])

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "streams"
    # Each ticked camera is its own field, so the choices live directly on
    # the step's schema keyed by device id -- not inside a shared section.
    stream_selector = result["data_schema"].schema["42"]
    stream_choices = stream_selector.config["options"]
    assert set(stream_choices) == set(_KEYS)
    assert "h264_720" not in stream_choices

    # A plain `cv.multi_select` renders its labels verbatim -- raw identifiers
    # like "h264_360" -- because it has no notion of translation at all. Only
    # a selector with a `translation_key` resolves labels from
    # `selector.stream_key.options.*`, so asserting the key here is what
    # would catch a regression back to `cv.multi_select` before it ships.
    assert stream_selector.config["translation_key"] == "stream_key"


async def test_a_camera_with_no_streams_chosen_is_rejected(hass) -> None:
    """Ticking a camera and then choosing none of its streams is a
    contradiction, and saying so beats silently dropping the camera --
    which would read as the tick having been ignored."""
    entry = await _loaded_entry(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h265",
            "camera_streams": {"42": ["h265"]},
        },
    )

    result = await _pick_cameras(hass, entry, ["42"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"42": []},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]


async def test_choosing_streams_stores_them_per_camera(hass) -> None:
    entry = await _loaded_entry(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h265",
            "camera_streams": {"42": ["h265"]},
        },
    )

    result = await _pick_cameras(hass, entry, ["42"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"42": ["h265", "h264_360"]},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["camera_streams"] == {"42": ["h265", "h264_360"]}


async def test_the_primary_stream_is_not_rewritten_by_editing_options(hass) -> None:
    """It fixes an entity's identity, so it is decided once and left alone.

    Recomputing it here would move the bare `<did>` entity to a different
    stream the next time anyone opened this form.
    """
    entry = await _loaded_entry(
        hass,
        {
            "cameras": ["42"],
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264"]},
        },
    )

    result = await _pick_cameras(hass, entry, ["42"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"42": ["h264", "h264_360"]},
    )
    await hass.async_block_till_done()

    assert entry.options["primary_stream"] == "h264"


async def test_options_on_an_entry_that_never_loaded_aborts_cleanly(hass) -> None:
    """The bridge was unreachable at startup, so nothing was ever polled.

    Home Assistant still lets the options flow be opened on an entry stuck
    retrying, and this is precisely when a user goes looking at the
    configuration. "Cannot connect" is actionable; "Unknown error" is not.
    """
    from custom_components.xiaomi_camera.api import BridgeError

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options={"cameras": ["42"], "primary_stream": "h265"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(
            side_effect=BridgeError("bridge is down")
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
