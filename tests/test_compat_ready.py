"""One owner for `compat_ready`, four readers.

Before this, `False` was hardcoded at four production call sites --
`BridgeApi._compat_ready`, `CameraRegistry._compat_ready`, and both call
sites in `bridge.__main__` -- each with a comment saying a later task would
wire the real state in. This file pins that all four now read the same
cached flag (`go2rtc_xiaomi.compat_ready()`), rather than re-deriving or
independently hardcoding an answer: the multi-stream incident in CLAUDE.md
is exactly what a second source for one fact costs.

`test_bridge_publishing.py` carries the fourth reader, `Bridge.async_refresh`
in `bridge.__main__` -- it already has the fixtures for exercising that
method without a real account.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bridge import go2rtc_xiaomi
from bridge.api import BridgeApi
from bridge.cameras import CameraRegistry
from bridge.settings import SettingsStore


@pytest.fixture(autouse=True)
def _reset_cache():
    go2rtc_xiaomi._ready = False
    yield
    go2rtc_xiaomi._ready = False


def test_api_compat_ready_reads_the_shared_cache() -> None:
    api = BridgeApi.__new__(BridgeApi)
    assert api._compat_ready() is False
    go2rtc_xiaomi._ready = True
    assert api._compat_ready() is True


def test_cameras_compat_ready_reads_the_shared_cache() -> None:
    registry = CameraRegistry.__new__(CameraRegistry)
    assert registry._compat_ready() is False
    go2rtc_xiaomi._ready = True
    assert registry._compat_ready() is True


class _FakeClient:
    """Just enough of `MIoTClient` for `CameraRegistry.async_refresh`."""

    async def get_devices_async(self) -> dict[str, object]:
        return {}

    async def get_cameras_async(self) -> dict[str, object]:
        return {}


async def test_a_camera_list_refresh_refreshes_compat_ready(
    tmp_path: Path, monkeypatch
) -> None:
    """One of the four moments `compat_ready` must be kept current: the
    camera list refreshing, whether from the control plane, the page, or
    the background loop -- all of them go through this one method.
    """
    called = SimpleNamespace(count=0)

    async def fake_refresh() -> None:
        called.count += 1
        go2rtc_xiaomi._ready = True

    # `cameras.py` does `from . import go2rtc_xiaomi` and calls
    # `go2rtc_xiaomi.refresh_compat_ready()`, so patching the module's own
    # attribute is enough -- there is no separate bound name to chase.
    monkeypatch.setattr(go2rtc_xiaomi, "refresh_compat_ready", fake_refresh)
    registry = CameraRegistry(_FakeClient(), SettingsStore(tmp_path / "settings.json"))

    await registry.async_refresh()

    assert called.count == 1
    assert registry._compat_ready() is True
