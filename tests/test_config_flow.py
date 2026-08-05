"""The config and options flows, through real Home Assistant.

`streams.py` decides which stream keys and identities are valid; these tests
cover the flow code that turns a form submission into the options dict
`streams.py` reads -- the part unit tests cannot reach.
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
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.xiaomi_camera.api import BridgeCamera, CameraStream
    from custom_components.xiaomi_camera.const import DOMAIN


def _camera(did: str, streams: tuple[CameraStream, ...]) -> BridgeCamera:
    return BridgeCamera(
        did=did,
        name=f"Camera {did}",
        model="chuangmi.camera.81ac1",
        manufacturer="chuangmi",
        channel_count=1,
        online=True,
        lan_online=True,
        powered_on=True,
        rtsp_url=f"rtsp://127.0.0.1:8554/camera_{did}",
        rtsp_url_h264=f"rtsp://127.0.0.1:8554/camera_{did}_h264",
        streams=streams,
    )


def _stream(did: str, key: str, codec: str = "h264") -> CameraStream:
    return CameraStream(
        key=key,
        codec=codec,
        height=None,
        url=f"rtsp://127.0.0.1:8554/camera_{did}_{key}",
    )


async def test_ticking_a_new_camera_defaults_it_to_the_primary_stream(hass) -> None:
    """C1b: a camera ticked in step one is offered in the stream step, where
    the primary stream is its default -- so ticking alone gives it a working
    stream instead of falling through to an unrecorded default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options={
            "cameras": ["42"],
            "auto_add": False,
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264"]},
        },
    )
    entry.add_to_hass(hass)

    cameras = [
        _camera("42", (_stream("42", "h264"),)),
        _camera("43", (_stream("43", "h264"),)),
    ]
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(return_value=cameras)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        # Camera "43" is newly ticked here. The stream step that follows is
        # built from what was ticked, so it carries a field for "43" whose
        # default is the primary stream.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"cameras": ["42", "43"], "auto_add": False},
        )
        assert result["step_id"] == "streams"
        assert "Camera 43 (43) · Codec" in result["data_schema"].schema

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "Camera 42 (42) · Codec": ["h264"],
                "Camera 42 (42) · Resolution": ["original"],
                "Camera 43 (43) · Codec": ["h264"],
                "Camera 43 (43) · Resolution": ["original"],
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["data"]["camera_streams"]["43"] == ["h264"]


async def test_setup_completes_when_a_camera_declares_no_streams(hass) -> None:
    """I2: an add-on predating `/api/cameras.streams` reports `streams: []`.

    Without special handling the selector for that camera is either submitted
    empty (rejected by the `no_streams` check) or with a default that is not
    among its own -- empty -- options (rejected by `SelectSelector` itself).
    Either way the flow could never be completed, and the repair that is
    supposed to explain why is only reachable once an entry exists.
    """
    with patch("custom_components.xiaomi_camera.config_flow.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(return_value=[_camera("42", ())])

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "manual"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "127.0.0.1", "port": 8099},
        )
        assert result["step_id"] == "cameras"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"cameras": ["42"], "auto_add": True},
        )
        assert result["step_id"] == "streams"

        # No key for "42": its selector was omitted since it declares no
        # streams, so the stream step submits empty and setup completes.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"


async def test_options_flow_completes_when_a_camera_declares_no_streams(
    hass,
) -> None:
    """The same trap exists in the options flow -- the repair's own advice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options={
            "cameras": ["42"],
            "auto_add": False,
            "primary_stream": "h264",
            "camera_streams": {"42": ["h264"]},
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        # The add-on has since been downgraded, or this entry never saw a
        # `streams`-reporting add-on for camera "42".
        client.return_value.async_cameras = AsyncMock(return_value=[_camera("42", ())])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"cameras": ["42"], "auto_add": False},
        )
        assert result["step_id"] == "streams"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"
