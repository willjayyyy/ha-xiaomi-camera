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
        self, cameras, *, compat_urls=None, explicit: bool = False
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


async def test_settings_pruning_is_told_every_camera_on_the_account() -> None:
    """`prune` deletes stored overrides, so it must be asked "is this camera
    still on the account", never "is it publishable right now".

    A refused model's stored `path: compat` is the only reason it is
    publishable at all. Pruning by publishability makes the two questions
    circular: one cycle in which the camera is not publishable deletes the
    override that would have made it publishable again, and it never comes
    back -- taking its Home Assistant entities with it, since the integration
    deletes entities for any camera missing from the control plane's list.
    """
    settings = _FakeSettings()
    bridge = _bridge_with(_FakeRegistry(), _FakeRestreamer(), settings)

    await bridge.async_refresh()

    assert settings.pruned == {"aaa", "bbb"}


async def test_a_camera_that_is_not_publishable_keeps_its_override(tmp_path) -> None:
    """The end-to-end shape of the same rule, against the real store: a
    refused camera whose path could not be resolved this cycle still has its
    override row afterwards."""
    from bridge.settings import SettingsStore

    class _NoPathRegistry:
        async def async_refresh(self) -> list[CameraDescription]:
            return [_description("bbb", "unsupported")]

    store = SettingsStore(tmp_path / "settings.json")
    store.set_override("bbb", path=VideoPath.COMPAT)
    bridge = _bridge_with(_NoPathRegistry(), _FakeRestreamer(), store)

    await bridge.async_refresh()

    assert store.override_for("bbb").path is VideoPath.COMPAT


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


async def test_the_keyframe_hint_is_unknown_for_a_camera_off_the_official_path() -> (
    None
):
    """`Stills` reads this while opening a preview, so it must degrade rather
    than raise.

    `SessionManager.session_for` refuses anything not on the official path by
    raising -- there is no vendor session to measure a compatibility-mode
    camera's keyframe interval with, and there never will be. "Unknown" is a
    value this hint already has and callers already handle; an exception here
    would take down the preview it was being opened for.
    """

    class _RefusingSessions:
        def session_for(self, info):
            raise ValueError("not on the Xiaomi official path")

    bridge = _bridge_with(_FakeRegistry(), _FakeRestreamer(), _FakeSettings())
    bridge._sessions = _RefusingSessions()
    bridge._registry = SimpleNamespace(get=lambda did: SimpleNamespace(did=did))

    assert bridge._keyframe_interval("aaa") is None


async def test_a_go2rtc_failure_does_not_fail_the_refresh(monkeypatch) -> None:
    """`async_refresh` is the callback behind every settings write, and those
    writes commit before it runs.

    `all_device_urls` raises on any go2rtc error, so letting it out turns a
    committed change into a 500: a camera switched to compatibility mode
    during a go2rtc hiccup would be switched *and* reported as failed. With
    no addresses the affected cameras report `stream_error` instead, which is
    the state that exists for exactly this.
    """
    from bridge import go2rtc_xiaomi

    async def _boom(region: str) -> dict[str, str]:
        raise RuntimeError("go2rtc says no")

    monkeypatch.setattr(go2rtc_xiaomi, "all_device_urls", _boom)

    class _AllCompat(_FakeSettings):
        def resolved_for(self, did, *, support, compat_ready):
            self.compat_ready_seen.append(compat_ready)
            return SimpleNamespace(path=VideoPath.COMPAT)

    restreamer = _FakeRestreamer()
    bridge = _bridge_with(_FakeRegistry(), restreamer, _AllCompat())
    bridge._account.cloud_server = "cn"

    await bridge.async_refresh()

    # The publish still happened, with no addresses -- `build_config` turns
    # that into a per-camera `stream_error` rather than a lost refresh.
    assert restreamer.applied is not None
