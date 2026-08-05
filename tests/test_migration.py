"""The config entry migration, through real Home Assistant machinery.

`streams.migrate_options` is unit-tested in `test_streams.py`. This covers the
part that unit test cannot: that Home Assistant recognises the migration hook,
calls it for a version 1 entry, and stores the result.

Needs `homeassistant`, available under `.venv314` but not under `.venv311` --
so this module is skipped there rather than erroring at collection.
"""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xiaomi_camera.const import DOMAIN


async def test_a_version_one_entry_is_migrated(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"host": "127.0.0.1", "port": 8099},
        options={"cameras": ["42"], "stream_codec": "h264"},
    )
    entry.add_to_hass(hass)

    from custom_components.xiaomi_camera import async_migrate_entry

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 2
    assert entry.options["primary_stream"] == "h264"
    assert entry.options["camera_streams"] == {"42": ["h264"]}
    assert "stream_codec" not in entry.options
