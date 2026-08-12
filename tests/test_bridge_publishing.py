"""A refused camera must never reach the publishing path.

`bridge.cameras.CameraRegistry.async_refresh` now reports cameras the vendor
library refuses alongside the ones it accepts, so a user learns why one is
missing instead of seeing an empty list. Before that change, nothing
downstream of the registry had ever needed to filter -- the registry itself
was the filter. Once refused cameras started flowing out of it, every
consumer that used to receive an already-clean list started receiving one
that was not, unchanged: `Bridge.async_refresh` handed the whole thing
straight to `Restreamer.async_apply`, which would have published thirteen
go2rtc streams per refused camera, every one of them pointing at a source
that answers 404 forever.

This is the one place that matters most: `Bridge.async_refresh` is what
decides what go2rtc is told to serve. `CameraDescription.publishable` is the
single predicate meant to guard every such consumer (see its docstring);
this test pins that the publishing path actually asks it, rather than
re-deriving "does this camera work" on its own.
"""

from __future__ import annotations

from types import SimpleNamespace

from bridge.__main__ import Bridge
from bridge.cameras import CameraDescription


def _description(did: str, support: str) -> CameraDescription:
    return CameraDescription(
        did=did,
        name=did,
        model="chuangmi.camera.81ac1",
        manufacturer="Xiaomi",
        channel_count=1,
        online=(support == "full"),
        lan_online=True,
        powered_on=True if support == "full" else None,
        requires_pin=False,
        support=support,
    )


class _FakeRegistry:
    """One publishable camera, one the vendor library refuses."""

    async def async_refresh(self) -> list[CameraDescription]:
        return [_description("aaa", "full"), _description("bbb", "unsupported")]


class _FakeRestreamer:
    """Records exactly what `Bridge.async_refresh` asked it to publish."""

    def __init__(self) -> None:
        self.applied: dict[str, object] | None = None

    async def async_apply(self, cameras, *, explicit: bool = False) -> None:
        self.applied = dict(cameras)


class _FakeSettings:
    def __init__(self) -> None:
        self.pruned: set[str] | None = None

    def prune(self, dids: set[str]) -> None:
        self.pruned = set(dids)

    def resolved_for(self, did: str, *, support: str, compat_ready: bool) -> str:
        return f"resolved-{did}"


def _bridge_with(registry, restreamer, settings) -> Bridge:
    """A `Bridge` with only the attributes `async_refresh` touches set.

    Built by bypassing `__init__`, which wires a real account, credential
    store and go2rtc supervisor -- none of which this test needs or can
    afford to stand up. `_bound_client` is set equal to the fake account's
    own client so `_async_sync_session_binding` (which `async_refresh` calls
    first) sees nothing has changed and returns immediately, without trying
    to rebuild a session manager this test never provided.
    """
    fake_client = object()
    bridge = Bridge.__new__(Bridge)
    bridge._account = SimpleNamespace(is_linked=True, client=fake_client)
    bridge._bound_client = fake_client
    bridge._registry = registry
    bridge._sessions = None
    bridge._restreamer = restreamer
    bridge._settings = settings
    bridge._previews = SimpleNamespace(drop=lambda dids: None)
    return bridge


async def test_a_refused_camera_never_reaches_the_restreamer() -> None:
    restreamer = _FakeRestreamer()
    bridge = _bridge_with(_FakeRegistry(), restreamer, _FakeSettings())

    await bridge.async_refresh()

    assert restreamer.applied is not None
    assert set(restreamer.applied) == {"aaa"}


async def test_a_refused_camera_is_not_kept_alive_by_settings_pruning() -> None:
    """`prune` is told which cameras exist so it can drop the rest. A refused
    camera was never given settings to prune in the first place -- passing its
    did through here would keep that predicate answered in two places instead
    of one.
    """
    settings = _FakeSettings()
    bridge = _bridge_with(_FakeRegistry(), _FakeRestreamer(), settings)

    await bridge.async_refresh()

    assert settings.pruned == {"aaa"}
