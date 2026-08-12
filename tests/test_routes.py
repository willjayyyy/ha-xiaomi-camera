"""Every address the page requests must exist on the listener serving it.

This shipped broken: the page was changed to request `/api/preview/<did>` while
the route it replaced was still the one registered, and the preview answered
404 for two releases. Nothing caught it -- the page is not exercised by any
test, `bridge.api` cannot be imported without the vendor SDK, and both halves
looked right in isolation.

Both sides are read as text for that reason. It is a coarse check, and it
catches exactly the mistake that occurred: the two halves of a request
disagreeing about its address.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bridge import go2rtc_xiaomi
from bridge.api import BridgeApi
from bridge.config import AccessMode, Options
from bridge.go2rtc_xiaomi import SignInBusy
from bridge.paths import VideoPath
from bridge.settings import SettingsStore

if importlib.util.find_spec("pytest_socket") is not None:
    # `TestServer` below opens a real loopback socket, which
    # `pytest-homeassistant-custom-component`'s `pytest-socket` blocks by
    # default on Python >= 3.14. Only present there, so only opted into
    # there -- see `bridge/../tests/test_webauth.py` for the same pattern.
    pytestmark = pytest.mark.usefixtures("socket_enabled")

_APP = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app"
_API = (_APP / "bridge" / "api.py").read_text(encoding="utf-8")
# The markup and the behaviour that requests things live in separate files
# since the page was split; a request address can drift in either one.
_PAGE = (_APP / "web" / "index.html").read_text(encoding="utf-8") + (
    _APP / "web" / "app.js"
).read_text(encoding="utf-8")

#: `web.get("/api/health", ...)` and friends, per listener. `put` joined the
#: settings endpoints in a later task; missing it here would make this file
#: blind to exactly the mismatch it exists to catch, for any route that
#: happens to use that method.
_ROUTE = re.compile(r'web\.(get|post|put)\(\s*"(?P<path>[^"]+)"')

#: Every address the page names, whether through `api("/api/…")` or as an
#: element's source. Both forms have shipped pointing at a route that did not
#: exist, so both are checked.
#:
#: Matches paths in backticks or quotes anywhere in the page source,
#: including comments. Do not backtick-quote an endpoint path in a comment
#: -- it reads as a request and this test will reject it.
_REQUEST = re.compile(r'[`"]\.?(?P<path>/api/[^`"?\s]*)')


def _routes(app: str) -> set[str]:
    """Paths registered by one `build_*_app`, with placeholders normalised."""
    body = _API.split(f"def build_{app}_app")[1].split("\n    def ")[0]
    return {
        re.sub(r"\{[^}]+\}", "*", match.group("path"))
        for match in _ROUTE.finditer(body)
    }


def _requested() -> set[str]:
    """Paths the page asks for, with interpolations normalised."""
    return {
        re.sub(r"\$\{[^}]+\}", "*", match.group("path")).rstrip("/")
        for match in _REQUEST.finditer(_PAGE)
    }


@pytest.mark.parametrize("path", sorted(_requested()))
def test_the_page_only_requests_routes_that_exist(path: str) -> None:
    assert path in _routes("ingress"), (
        f"the page requests {path}, which the ingress listener does not serve"
    )


def test_the_page_requests_something() -> None:
    """Guard the guard.

    A pattern that quietly matches nothing would make every assertion above
    vacuous, which is a worse failure than the one this file exists to catch.
    """
    assert len(_requested()) >= 5


def test_the_control_plane_is_not_reachable_through_the_page() -> None:
    """The two listeners exist to have different exposure.

    The control plane has no authentication of its own; it is bound to loopback
    and that is the whole of its protection. Serving one of its routes from the
    page's listener would hand a stream and camera power control to anything
    that gets past the page's guard.
    """
    control_only = _routes("control") - _routes("ingress")
    assert "/api/stream/*" in control_only
    assert "/api/cameras/*/power" in control_only


# ----------------------------------------------------------------------
# The settings endpoints
#
# Unlike the checks above, these drive a real `BridgeApi` over a real
# `aiohttp` request/response -- the route table alone cannot say whether an
# unknown value is rejected, whether `null` clears an override rather than
# setting it, or whether a password ends up in a response body.
# ----------------------------------------------------------------------


class _Camera:
    """Stands in for `CameraDescription` -- only what `_cameras` reads.

    `support` defaults to `"full"`, matching every existing test's
    assumption of a working camera; `publishable` is derived from `path`
    exactly as the real `CameraDescription.publishable` is, so a double built
    with `support="unsupported"` behaves like a refused camera on both the
    filtering path and the JSON body.

    `path` is separable from `support` because the real thing separates them:
    a refused model the user put on compatibility mode is `unsupported` and
    publishable at once, which is the whole point of that mode.
    """

    _NO_PATH = object()

    def __init__(self, did: str, support: str = "full", path=_NO_PATH) -> None:
        self.did = did
        self.support = support
        self.path = (
            (VideoPath.OFFICIAL if support == "full" else None)
            if path is self._NO_PATH
            else path
        )

    @property
    def publishable(self) -> bool:
        return self.path is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "did": self.did,
            "name": "Cam",
            "model": "chuangmi.camera.81ac1",
            "manufacturer": "Xiaomi",
            "channel_count": 1,
            "online": True,
            "lan_online": True,
            "powered_on": True,
            "requires_pin": False,
            "support": self.support,
            "publishable": self.publishable,
        }


class _Registry:
    """One known camera, `aaa`. Just enough of `CameraRegistry` to answer
    `/api/cameras` and the power-state questions the settings handlers ask.

    `extra` lets a test add cameras -- typically a refused one -- without
    disturbing every other test built on this fixture, which assumes `aaa`
    is the only, and first, camera in the list.
    """

    def __init__(self, extra: list[_Camera] | None = None) -> None:
        self._cameras = {"aaa": _Camera("aaa"), **{c.did: c for c in extra or []}}

    async def async_refresh(self) -> list[_Camera]:
        return list(self._cameras.values())

    def get(self, did: str) -> _Camera | None:
        return self._cameras.get(did)

    def is_publishable(self, did: str) -> bool:
        camera = self._cameras.get(did)
        return camera is not None and camera.publishable

    def power_state(self, did: str) -> bool | None:
        return True

    async def async_read_power_state(self, did: str) -> bool | None:
        return True

    async def async_set_power(self, did: str, value: bool) -> None:
        return None


class _Sessions:
    """Records which camera's session was reloaded, without a real one."""

    def __init__(self) -> None:
        self.reloaded: list[str] = []

    def stats(self) -> dict[str, dict[str, object]]:
        return {}

    async def async_reload(self, did: str) -> None:
        self.reloaded.append(did)


class _Restreamer:
    """Enough of `Restreamer` for `_cameras`'s per-camera fields."""

    requires_credentials = False
    rtsp_reachable_off_host = True

    def rtsp_url(self, did: str) -> str:
        return f"rtsp://127.0.0.1:8554/camera_{did}"

    def rtsp_url_h264(self, did: str) -> str:
        return f"rtsp://127.0.0.1:8554/camera_{did}_h264"

    def stream_descriptions(self, did: str) -> list[dict[str, object]]:
        return []

    def stream_error(self, did: str) -> str | None:
        return None


def _build_bridge(
    tmp_path: Path,
    extra: list[_Camera] | None = None,
    *,
    slug: str | None = None,
    web_password: str = "",
) -> BridgeApi:
    """A `BridgeApi` wired to camera `aaa` (plus `extra`) and its own settings
    file.

    `access_mode=local` and `supervised=False` together mean the ingress
    guards let every request through unauthenticated -- the same combination
    `test_webauth.py` uses for "a loopback page needs no password". What is
    under test here is the settings endpoints, not the guards in front of
    them.

    Factored out from the `bridge` fixture so a test that needs a second,
    refused camera can build its own instance without changing what every
    other test in this file sees.
    """
    options = Options(
        access_mode=AccessMode.LOCAL,
        rtsp_username="",
        rtsp_password="",
        log_level="info",
        web_password=web_password,
        supervised=False,
    )
    settings_store = SettingsStore(tmp_path / "settings.json")
    registry = _Registry(extra)
    sessions = _Sessions()

    async def refresh_callback(*, explicit: bool = False) -> None:
        return None

    return BridgeApi(
        account=None,
        registry_provider=lambda: registry,
        sessions_provider=lambda: sessions,
        restreamer=_Restreamer(),
        refresh_callback=refresh_callback,
        options=options,
        previews=None,
        settings_store=settings_store,
        slug=slug,
    )


@pytest.fixture
def bridge(tmp_path: Path) -> BridgeApi:
    return _build_bridge(tmp_path)


@pytest.fixture
async def client(bridge: BridgeApi, monkeypatch: pytest.MonkeyPatch):
    """The ingress listener, driven over a real socket.

    Not the control plane: the settings endpoints belong to the page, and a
    fixture built from `build_control_app` would make every test below pass
    for the wrong reason.
    """
    import bridge.api as api_module

    # `build_ingress_app` also mounts `/app/web` as static files, which only
    # exists inside the container image. Pointed at the real source directory
    # so the app builds at all outside one -- nothing here exercises the
    # static route itself.
    monkeypatch.setattr(api_module, "_STATIC_DIR", str(_APP / "web"))
    test_client = TestClient(TestServer(bridge.build_ingress_app()))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_settings_endpoint_reports_only_the_video_defaults(client):
    """The add-on-info half that used to live here moved to `/api/info`.

    One endpoint changes when a camera does, the other only when the add-on
    restarts -- see `_info`'s docstring for the split.
    """
    response = await client.get("/api/settings")
    assert response.status == 200
    body = await response.json()
    assert body["defaults"]["quality"] == "low"
    assert "addon" not in body


@pytest.mark.asyncio
async def test_info_reports_the_configured_slug(tmp_path: Path) -> None:
    api = _build_bridge(tmp_path, slug="a1b2c3d4_xiaomi_camera_bridge")
    body = json.loads((await api._info(None)).body)
    assert body["slug"] == "a1b2c3d4_xiaomi_camera_bridge"


@pytest.mark.asyncio
async def test_info_has_no_slug_when_none_was_configured(client) -> None:
    """Standalone deployments have no Supervisor to ask for a slug, and a
    link to a config page that cannot exist is worse than no link."""
    body = await (await client.get("/api/info")).json()
    assert body["slug"] is None


@pytest.mark.asyncio
async def test_info_reports_compat_ready(client) -> None:
    body = await (await client.get("/api/info")).json()
    # `go2rtc_xiaomi.compat_ready()`'s cache starts `False` and nothing in
    # this test signs in, so this is the real (empty) answer, not a stub.
    assert body["compat_ready"] is False


@pytest.mark.asyncio
async def test_setting_a_default_takes_effect_for_cameras_that_did_not_override(client):
    await client.put("/api/settings", json={"transcode_quality": "sharp"})
    cameras = await (await client.get("/api/cameras")).json()
    assert cameras["cameras"][0]["settings"]["transcode_quality"] == "sharp"


@pytest.mark.asyncio
async def test_a_camera_carries_both_paths_and_why_each_is_unavailable(client):
    """The page needs this to disable the path it cannot offer, and say why.

    `aaa` is `support="full"` with no compatibility-mode credential, so the
    official path is usable and the compat path names its reason.
    """
    body = await (await client.get("/api/cameras")).json()
    row = next(c for c in body["cameras"] if c["did"] == "aaa")
    assert row["paths"] == {"official": None, "compat": "pathCompatNoAuth"}


@pytest.mark.asyncio
async def test_a_cameras_resolved_path_reaches_the_page_undisguised(bridge, client):
    """`settings.path` must be the add-on's own answer from `path_for`, not
    something the page reconstructs from `override`. A camera with no
    override resolves to `official` (`aaa` is `support="full"`); pinning it
    to compatibility mode must change what this same field reports, not
    just what `override` separately says -- the page renders the connection
    row from `settings.path` alone.
    """
    body = await (await client.get("/api/cameras")).json()
    row = next(c for c in body["cameras"] if c["did"] == "aaa")
    assert row["settings"]["path"] == "official"

    bridge._settings_store.set_override("aaa", path=VideoPath.COMPAT)
    body = await (await client.get("/api/cameras")).json()
    row = next(c for c in body["cameras"] if c["did"] == "aaa")
    assert row["settings"]["path"] == "compat"


@pytest.mark.asyncio
async def test_the_defaults_payload_carries_no_path(client):
    """`Defaults` has no path to follow -- `/api/settings` must not invent
    one, and must not error trying to serialise a field that is not there.
    """
    body = await (await client.get("/api/settings")).json()
    assert "path" not in body["defaults"]


@pytest.mark.asyncio
async def test_null_clears_an_override_rather_than_setting_it(client):
    await client.put("/api/cameras/aaa/settings", json={"quality": "high"})
    await client.put("/api/cameras/aaa/settings", json={"quality": None})
    cameras = await (await client.get("/api/cameras")).json()
    row = next(c for c in cameras["cameras"] if c["did"] == "aaa")
    assert row["override"] == {}


@pytest.mark.asyncio
async def test_a_camera_can_switch_its_connection_path(bridge, client):
    """The bug this covers: `PUT .../settings {"path": "compat"}` used to
    return 200 and write nothing -- the connection row offered a choice
    that silently did not take.
    """
    response = await client.put("/api/cameras/aaa/settings", json={"path": "compat"})
    assert response.status == 200
    body = await (await client.get("/api/cameras")).json()
    row = next(c for c in body["cameras"] if c["did"] == "aaa")
    assert row["override"]["path"] == "compat"
    assert row["settings"]["path"] == "compat"
    # Which implementation serves a camera is only decided when its session
    # opens (`SessionManager.session_for`), so a path change must reopen the
    # session the same way a quality or audio change already does -- there
    # is no separate go2rtc stream URL to touch.
    assert bridge._sessions_provider().reloaded == ["aaa"]


@pytest.mark.asyncio
async def test_a_global_default_cannot_name_a_path(client):
    """A default for `path` would be meaningless -- which paths a camera can
    use depends on its own model and on whether the compatibility-mode
    credential exists. Refused by name, not silently dropped.
    """
    response = await client.put("/api/settings", json={"path": "compat"})
    assert response.status == 400
    assert "path" in (await response.text())


@pytest.mark.asyncio
async def test_an_unknown_value_is_rejected_by_name(client):
    response = await client.put("/api/settings", json={"quality": "ultra"})
    assert response.status == 400
    assert "quality" in (await response.text())


@pytest.mark.asyncio
async def test_the_settings_endpoints_are_not_on_the_control_plane(bridge):
    """The page owns these. The control plane is for the integration."""
    paths = {
        route.resource.canonical for route in bridge.build_control_app().router.routes()
    }
    assert "/api/settings" not in paths


# ----------------------------------------------------------------------
# A refused camera on `/api/cameras`.
#
# The two listeners answer different questions and must keep disagreeing:
# an older integration has never heard of `support` and cannot be expected to
# filter a refused camera out itself, so the control plane's answer is
# already filtered when it arrives -- see `CameraDescription.publishable`
# and `BridgeApi._cameras_for_control`. The page asks "what is on this
# account" and needs the refused camera, with `support`, to explain it.
# ----------------------------------------------------------------------


@pytest.fixture
async def _refused_camera_apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Both listeners of one bridge that knows about a refused camera `bbb`,
    driven directly through `TestClient` rather than through the shared
    `bridge`/`client` fixtures, which assume `aaa` is the only camera.
    """
    import bridge.api as api_module

    monkeypatch.setattr(api_module, "_STATIC_DIR", str(_APP / "web"))
    app = _build_bridge(tmp_path, extra=[_Camera("bbb", support="unsupported")])

    control = TestClient(TestServer(app.build_control_app()))
    page = TestClient(TestServer(app.build_ingress_app()))
    await control.start_server()
    await page.start_server()
    try:
        yield control, page
    finally:
        await control.close()
        await page.close()


@pytest.mark.asyncio
async def test_a_refused_camera_never_reaches_the_control_plane(_refused_camera_apps):
    """The one thing this bug did: an integration that cannot read `support`
    would otherwise build an entity for `bbb` that can never show a picture.
    """
    control, _ = _refused_camera_apps
    body = await (await control.get("/api/cameras")).json()
    dids = {c["did"] for c in body["cameras"]}
    assert dids == {"aaa"}


@pytest.mark.asyncio
async def test_a_refused_camera_reaches_the_page_with_its_support_level(
    _refused_camera_apps,
):
    """The page is where a refused camera's `support` has a reader at all."""
    _, page = _refused_camera_apps
    body = await (await page.get("/api/cameras")).json()
    dids = {c["did"] for c in body["cameras"]}
    assert dids == {"aaa", "bbb"}
    refused = next(c for c in body["cameras"] if c["did"] == "bbb")
    assert refused["support"] == "unsupported"
    # The page asks this rather than re-deriving it from `support`, so it has
    # to arrive on the wire.
    assert refused["publishable"] is False


# ----------------------------------------------------------------------
# Compatibility mode's sign-in, carried to go2rtc.
#
# Only two routes -- `POST /api/compat/signin` and `DELETE /api/compat` --
# on the ingress app, behind the same password guard as everything else on
# the page. go2rtc's wider API exposes `exec` and stream management, and
# there must be nothing generic here for a later task to grow into a proxy
# for either.
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_compat_ready_cache():
    """`go2rtc_xiaomi.compat_ready()` is cached module state -- see its own
    docstring for why. Reset around every test in this file so one test's
    sign-in does not leak into the next."""
    go2rtc_xiaomi._ready = False
    yield
    go2rtc_xiaomi._ready = False


@pytest.fixture
async def locked_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The ingress listener behind a configured password, with no session
    cookie supplied -- the same "not signed in" state a fresh browser is in.
    """
    import bridge.api as api_module

    monkeypatch.setattr(api_module, "_STATIC_DIR", str(_APP / "web"))
    bridge_api = _build_bridge(tmp_path, web_password="correct-horse-battery")
    test_client = TestClient(TestServer(bridge_api.build_ingress_app()))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_sign_in_is_behind_the_page_password(locked_client) -> None:
    response = await locked_client.post("/api/compat/signin", json={"step": "password"})
    assert response.status == 401


@pytest.mark.asyncio
async def test_forget_is_behind_the_page_password(locked_client) -> None:
    response = await locked_client.delete("/api/compat")
    assert response.status == 401


def test_only_the_two_compat_routes_exist(
    bridge: BridgeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing generic. go2rtc's wider API exposes `exec` and stream
    management, and a passthrough would put both behind our password."""
    import bridge.api as api_module

    monkeypatch.setattr(api_module, "_STATIC_DIR", str(_APP / "web"))
    paths = {
        route.resource.canonical for route in bridge.build_ingress_app().router.routes()
    }
    assert not any("xiaomi" in p for p in paths)
    assert "/api/compat/signin" in paths
    assert "/api/compat" in paths


@pytest.mark.asyncio
async def test_a_sign_in_is_carried_to_go2rtc(client, monkeypatch: pytest.MonkeyPatch):
    async def fake_sign_in(step, **fields):
        assert step == "password"
        assert fields == {"username": "u", "password": "p"}
        return go2rtc_xiaomi.SignInResult(ok=True)

    monkeypatch.setattr(go2rtc_xiaomi, "sign_in", fake_sign_in)
    response = await client.post(
        "/api/compat/signin",
        json={"step": "password", "username": "u", "password": "p"},
    )
    assert response.status == 200
    assert (await response.json())["ok"] is True


@pytest.mark.asyncio
async def test_a_successful_sign_in_updates_compat_ready(
    client, monkeypatch: pytest.MonkeyPatch
):
    async def fake_sign_in(step, **fields):
        return go2rtc_xiaomi.SignInResult(ok=True)

    async def fake_refresh():
        go2rtc_xiaomi._ready = True

    monkeypatch.setattr(go2rtc_xiaomi, "sign_in", fake_sign_in)
    monkeypatch.setattr(go2rtc_xiaomi, "refresh_compat_ready", fake_refresh)
    assert go2rtc_xiaomi.compat_ready() is False
    await client.post("/api/compat/signin", json={"step": "password"})
    assert go2rtc_xiaomi.compat_ready() is True


@pytest.mark.asyncio
async def test_a_next_step_reports_what_go2rtc_asked_for(
    client, monkeypatch: pytest.MonkeyPatch
):
    async def fake_sign_in(step, **fields):
        return go2rtc_xiaomi.SignInResult(ok=False, verify_phone="138****5678")

    monkeypatch.setattr(go2rtc_xiaomi, "sign_in", fake_sign_in)
    response = await client.post("/api/compat/signin", json={"step": "password"})
    assert response.status == 401
    body = await response.json()
    assert body["verify_phone"] == "138****5678"
    assert body["captcha"] is None


@pytest.mark.asyncio
async def test_an_unknown_step_is_rejected_by_name(client) -> None:
    response = await client.post("/api/compat/signin", json={"step": "carrier-pigeon"})
    assert response.status == 400


@pytest.mark.asyncio
async def test_a_second_sign_in_is_refused_with_a_reason(
    client, monkeypatch: pytest.MonkeyPatch
):
    async def fake_sign_in(step, **fields):
        raise SignInBusy

    monkeypatch.setattr(go2rtc_xiaomi, "sign_in", fake_sign_in)
    response = await client.post("/api/compat/signin", json={"step": "password"})
    assert response.status == 409
    assert (await response.json())["error"] == "sign_in_busy"


@pytest.mark.asyncio
async def test_a_go2rtc_failure_is_reported_without_its_raw_text(
    client, monkeypatch: pytest.MonkeyPatch
):
    """Whatever go2rtc's own error said, it must pass through `safe_error`
    rather than reach the response verbatim -- it can carry the account
    password in cleartext."""

    async def fake_sign_in(step, **fields):
        raise RuntimeError("upstream said: rtsp://admin:hunter2@host/failed")

    monkeypatch.setattr(go2rtc_xiaomi, "sign_in", fake_sign_in)
    response = await client.post("/api/compat/signin", json={"step": "password"})
    assert response.status == 502
    body = await response.json()
    assert "hunter2" not in body["error"]


@pytest.mark.asyncio
async def test_forgetting_the_credential_is_not_supported(client) -> None:
    """go2rtc's own `/api/xiaomi` (its `internal/xiaomi/xiaomi.go`) accepts
    GET and POST only -- there is no removal path to call, and hand-editing
    the state file go2rtc owns would give it a second writer. A clear
    "not supported" beats a button that does nothing.
    """
    response = await client.delete("/api/compat")
    assert response.status == 501
    assert (await response.json())["error"] == "not_supported"


# ----------------------------------------------------------------------
# Login backoff -- see bridge/api.py's `_login_penalty`.
# ----------------------------------------------------------------------


class _FakeClock:
    """A clock that only moves when something awaits `asyncio.sleep`.

    Standing in for wall time lets the backoff tests assert on delay without
    a slow test suite actually waiting out a 30-second cap.
    """

    def __init__(self) -> None:
        self._elapsed = 0.0

    def now(self) -> float:
        return self._elapsed

    async def sleep(self, seconds: float) -> None:
        self._elapsed += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    import bridge.api as api_module

    fake = _FakeClock()
    monkeypatch.setattr(api_module.asyncio, "sleep", fake.sleep)
    return fake


@pytest.fixture
async def bridge_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The ingress listener behind a configured password, used to exercise
    `/api/login` itself rather than what it guards."""
    import bridge.api as api_module

    monkeypatch.setattr(api_module, "_STATIC_DIR", str(_APP / "web"))
    bridge_api = _build_bridge(tmp_path, web_password="right")
    test_client = TestClient(TestServer(bridge_api.build_ingress_app()))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


def test_the_bucket_key_normalises_dual_stack_addresses() -> None:
    """`192.168.1.5` and its IPv4-mapped IPv6 form `::ffff:192.168.1.5` name
    the same host -- leaving them as distinct dict keys would hand a
    dual-stack attacker a second free budget for nothing."""
    assert BridgeApi._bucket_key("::ffff:192.168.1.5") == BridgeApi._bucket_key(
        "192.168.1.5"
    )


def test_the_bucket_key_falls_back_to_the_raw_string_when_unparseable() -> None:
    assert BridgeApi._bucket_key("unknown") == "unknown"


def test_the_tracked_address_map_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a success clears an address's entry -- without a cap, a
    sustained attack spread across many addresses would grow the map for
    the life of the process."""
    import bridge.api as api_module

    monkeypatch.setattr(api_module, "_MAX_TRACKED_ADDRESSES", 3)
    api = _build_bridge(tmp_path, web_password="right")
    for i in range(8):
        api._record_login_failure(f"10.0.0.{i}")
    assert len(api._login_failures) == 3
    # The addresses kept are the most recently touched, not an arbitrary
    # subset -- the earliest ones were the ones evicted.
    assert set(api._login_failures) == {"10.0.0.5", "10.0.0.6", "10.0.0.7"}


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_are_slowed_down(bridge_client, clock) -> None:
    """`access_mode: lan` puts this page on the network, and what it guards
    is the pictures. Unlimited guesses is not a posture."""
    for _ in range(5):
        await bridge_client.post("/api/login", json={"password": "wrong"})
    before = clock.now()
    await bridge_client.post("/api/login", json={"password": "wrong"})
    assert clock.now() - before >= 1


@pytest.mark.asyncio
async def test_a_correct_password_clears_the_penalty(bridge_client, clock) -> None:
    for _ in range(5):
        await bridge_client.post("/api/login", json={"password": "wrong"})
    await bridge_client.post("/api/login", json={"password": "right"})
    before = clock.now()
    await bridge_client.post("/api/login", json={"password": "wrong"})
    assert clock.now() - before < 1


@pytest.mark.asyncio
async def test_the_attempted_password_is_never_logged(
    bridge_client, caplog: pytest.LogCaptureFixture
) -> None:
    """A guard that only checks the password's absence cannot tell "nothing
    leaked" from "nothing happened" -- deleting the failure log entirely
    would pass it too. The positive half pins that the source and the count
    are logged, so there is something here besides silence."""
    caplog.set_level(logging.DEBUG)
    await bridge_client.post("/api/login", json={"password": "hunter2"})
    assert "hunter2" not in caplog.text
    failure_records = [
        r for r in caplog.records if "wrong web password" in r.getMessage()
    ]
    assert len(failure_records) == 1
    message = failure_records[0].getMessage()
    assert "127.0.0.1" in message
    assert "1 failed attempt" in message


# ----------------------------------------------------------------------
# The snapshot endpoint on a compatibility-mode camera.
#
# Home Assistant fetches `/api/snapshot/<did>` for the entity picture, so a
# camera that cannot answer it is permanently blank in the interface. The
# picture is decoded from the add-on's own published RTSP stream, which
# exists on either path -- but the endpoint used to demand a vendor session,
# which no compatibility-mode camera has: a refused model got a 404 and a
# supported one switched over got an uncaught 500.
# ----------------------------------------------------------------------


class _Previews:
    """Stands in for `Stills` -- one still, no decoder."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def async_still(self, did: str, max_age: float | None = None) -> bytes:
        self.asked.append(did)
        return b"\xff\xd8jpeg\xff\xd9"


@pytest.fixture
async def _snapshot_client(tmp_path: Path):
    """The control listener -- the one Home Assistant reads -- whose `bbb` is
    a refused model parked on compatibility mode: `support="unsupported"`,
    and publishable all the same."""
    api = _build_bridge(
        tmp_path,
        extra=[
            _Camera("bbb", support="unsupported", path=VideoPath.COMPAT),
            _Camera("ccc", support="unsupported"),
        ],
    )
    previews = _Previews()
    api._previews = previews
    client = TestClient(TestServer(api.build_control_app()))
    await client.start_server()
    try:
        yield client, previews
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_compatibility_mode_camera_has_a_snapshot(_snapshot_client):
    client, previews = _snapshot_client
    response = await client.get("/api/snapshot/bbb")
    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert previews.asked == ["bbb"]


@pytest.mark.asyncio
async def test_a_camera_with_no_path_has_no_snapshot(_snapshot_client):
    """Widening the gate to "publishable" must not widen it to everything:
    a camera with no resolved path has no published stream to decode."""
    client, previews = _snapshot_client
    response = await client.get("/api/snapshot/ccc")
    assert response.status == 404
    assert previews.asked == []
