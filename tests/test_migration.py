"""The config entry migration, through real Home Assistant machinery.

`streams.migrate_options` is unit-tested in `test_streams.py`. This covers the
part that unit test cannot: that Home Assistant recognises the migration hook,
calls it for a version 1 entry, and stores the result.

Needs `homeassistant`, which only installs on Python >= 3.14 -- so on an
older interpreter this module is skipped rather than erroring at collection.

Gated on the interpreter version, not on whether the import happens to
succeed: a `.venv314` install that is present but broken should fail this
module loudly, not be indistinguishable from "not installed here on
purpose".
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="needs Python >= 3.14 and homeassistant; run under .venv314",
)

# Guarded the same way as the marker above, not with `importorskip`: below
# 3.14 the import is skipped on purpose (nothing to run); at or above 3.14 it
# is unconditional, so a broken `.venv314` install fails collection loudly
# instead of reporting a misleading skip.
if sys.version_info >= (3, 14):
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

    # The migration chains in one call, so a v1 entry reaches the current
    # version (3), not the intermediate one.
    assert entry.version == 3
    assert entry.options["primary_stream"] == "h264"
    assert entry.options["camera_streams"] == {"42": ["h264"]}
    assert "stream_codec" not in entry.options


async def test_a_version_two_entry_is_migrated(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"host": "127.0.0.1", "port": 8099},
        options={
            "cameras": ["42"],
            "primary_stream": "h265",
            "camera_streams": {"42": ["h265", "h264"]},
        },
    )
    entry.add_to_hass(hass)

    from custom_components.xiaomi_camera import async_migrate_entry

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 3
    assert entry.options["primary_stream"] == "original"
    assert entry.options["camera_streams"] == {"42": ["original", "h264"]}
