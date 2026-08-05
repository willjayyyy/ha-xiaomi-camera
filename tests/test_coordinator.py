"""The repair that flags an add-on too old to declare streams.

The integration deliberately does not synthesise a stream list from the two
legacy URL fields -- that would mean maintaining two ways of finding streams
forever. Instead it tells the user their add-on needs an update, and takes
the warning back the moment a poll shows streams again.
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
    from homeassistant.helpers import issue_registry as ir
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.xiaomi_camera.api import BridgeCamera, CameraStream
    from custom_components.xiaomi_camera.const import DOMAIN

_ISSUE_ID = "addon_outdated"


def _camera(did: str = "42", *, with_streams: bool) -> BridgeCamera:
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
        streams=(
            (
                CameraStream(
                    key="h265",
                    codec="h265",
                    height=None,
                    url=f"rtsp://127.0.0.1:8554/camera_{did}",
                ),
            )
            if with_streams
            else ()
        ),
    )


async def _loaded_entry(hass, cameras: list[BridgeCamera]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options={"cameras": [camera.did for camera in cameras]},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(return_value=cameras)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_an_addon_that_declares_streams_raises_no_repair(hass) -> None:
    await _loaded_entry(hass, [_camera(with_streams=True)])

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _ISSUE_ID) is None


async def test_an_addon_that_declares_no_streams_raises_the_repair(hass) -> None:
    await _loaded_entry(hass, [_camera(with_streams=False)])

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _ISSUE_ID) is not None


async def test_an_empty_camera_list_raises_no_repair(hass) -> None:
    """No cameras yet is not the same thing as an outdated add-on."""
    await _loaded_entry(hass, [])

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _ISSUE_ID) is None


async def test_the_repair_clears_once_the_addon_starts_declaring_streams(
    hass,
) -> None:
    """The one that matters: the warning withdraws itself, unprompted."""
    entry = await _loaded_entry(hass, [_camera(with_streams=False)])

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _ISSUE_ID) is not None

    with patch("custom_components.xiaomi_camera.BridgeClient") as client:
        client.return_value.async_health = AsyncMock(return_value={"status": "ok"})
        client.return_value.async_cameras = AsyncMock(
            return_value=[_camera(with_streams=True)]
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = entry.runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, _ISSUE_ID) is None
