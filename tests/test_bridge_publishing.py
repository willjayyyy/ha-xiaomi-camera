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
from bridge.paths import VideoPath


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
        path=VideoPath.OFFICIAL if support == "full" else None,
    )


class _FakeRegistry:
    """One publishable camera, one the vendor library refuses."""

    async def async_refresh(self) -> list[CameraDescription]:
        return [_description("aaa", "full"), _description("bbb", "unsupported")]


class _FakeRestreamer:
    """Records exactly what `Bridge.async_refresh` asked it to publish."""

    def __init__(self) -> None:
        self.applied: dict[str, object] | None = None

    async def async_apply(
        self, cameras, *, channel_counts=None, compat_urls=None, explicit: bool = False
    ) -> None:
        self.applied = dict(cameras)


class _FakeSettings:
    def __init__(self) -> None:
        self.pruned: set[str] | None = None
        #: Every `compat_ready` value `async_refresh` passed through, so a
        #: test can pin that this is read from the real cached flag rather
        #: than hardcoded -- see `test_compat_ready.py`.
        self.compat_ready_seen: list[bool] = []

    def prune(self, dids: set[str]) -> None:
        self.pruned = set(dids)

    def resolved_for(self, did: str, *, support: str, compat_ready: bool):
        self.compat_ready_seen.append(compat_ready)
        # A minimal stand-in for `Resolved`: `Bridge.async_refresh` only
        # reads `.path` off this now, to decide whether it needs to ask
        # go2rtc for compatibility-mode addresses.
        return SimpleNamespace(path=VideoPath.OFFICIAL)


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


async def test_channel_counts_reach_the_restreamer() -> None:
    """Obligation E3: a dual-lens camera's `channel_count` has to reach
    `Restreamer.async_apply` for a compatibility-mode second lens to be
    built at all -- `Resolved` itself carries no such field."""

    class _DualLensRegistry:
        async def async_refresh(self) -> list[CameraDescription]:
            description = _description("aaa", "full")
            return [
                CameraDescription(
                    **{**description.__dict__, "channel_count": 2},
                )
            ]

    restreamer = _FakeRestreamer()
    captured: dict[str, object] = {}
    original = restreamer.async_apply

    async def _capture(cameras, *, channel_counts=None, compat_urls=None, **kwargs):
        captured["channel_counts"] = dict(channel_counts or {})
        await original(
            cameras, channel_counts=channel_counts, compat_urls=compat_urls, **kwargs
        )

    restreamer.async_apply = _capture
    bridge = _bridge_with(_DualLensRegistry(), restreamer, _FakeSettings())

    await bridge.async_refresh()

    assert captured["channel_counts"] == {"aaa": 2}


async def test_compat_urls_are_fetched_only_when_a_camera_needs_them(
    monkeypatch,
) -> None:
    """Obligation D: fetching go2rtc's device list has a real cost, so it is
    skipped entirely when nothing resolved to compatibility mode -- and it
    reads the region from the OAuth side's own cloud-server setting rather
    than a second option (R3)."""
    from bridge import go2rtc_xiaomi

    calls: list[str] = []

    async def _fake_all_device_urls(region: str) -> dict[str, str]:
        calls.append(region)
        return {"aaa": "xiaomi://1:cn@1.2.3.4?did=aaa&model=m"}

    monkeypatch.setattr(go2rtc_xiaomi, "all_device_urls", _fake_all_device_urls)

    class _AllOfficial(_FakeSettings):
        def resolved_for(self, did, *, support, compat_ready):
            self.compat_ready_seen.append(compat_ready)
            return SimpleNamespace(path=VideoPath.OFFICIAL)

    restreamer = _FakeRestreamer()
    bridge = _bridge_with(_FakeRegistry(), restreamer, _AllOfficial())
    bridge._account.cloud_server = "cn"

    await bridge.async_refresh()

    assert calls == []

    class _OneCompat(_FakeSettings):
        def resolved_for(self, did, *, support, compat_ready):
            self.compat_ready_seen.append(compat_ready)
            path = VideoPath.COMPAT if did == "aaa" else VideoPath.OFFICIAL
            return SimpleNamespace(path=path)

    restreamer2 = _FakeRestreamer()
    bridge2 = _bridge_with(_FakeRegistry(), restreamer2, _OneCompat())
    bridge2._account.cloud_server = "cn"

    await bridge2.async_refresh()

    assert calls == ["cn"]
