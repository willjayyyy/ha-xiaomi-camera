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
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bridge.api import BridgeApi
from bridge.config import AccessMode, Options, TranscodeQuality, VideoQuality
from bridge.settings import SettingsStore

if importlib.util.find_spec("pytest_socket") is not None:
    # `TestServer` below opens a real loopback socket, which
    # `pytest-homeassistant-custom-component`'s `pytest-socket` blocks by
    # default on Python >= 3.14. Only present there, so only opted into
    # there -- see `bridge/../tests/test_webauth.py` for the same pattern.
    pytestmark = pytest.mark.usefixtures("socket_enabled")

_APP = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app"
_API = (_APP / "bridge" / "api.py").read_text(encoding="utf-8")
_PAGE = (_APP / "web" / "index.html").read_text(encoding="utf-8")

#: `web.get("/api/health", ...)` and friends, per listener.
_ROUTE = re.compile(r'web\.(get|post)\(\s*"(?P<path>[^"]+)"')

#: Every address the page names, whether through `api("/api/…")` or as an
#: element's source. Both forms have shipped pointing at a route that did not
#: exist, so both are checked.
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
    """Stands in for `CameraDescription` -- only what `_cameras` reads."""

    def __init__(self, did: str) -> None:
        self.did = did

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
        }


class _Registry:
    """One known camera, `aaa`. Just enough of `CameraRegistry` to answer
    `/api/cameras` and the power-state questions the settings handlers ask.
    """

    def __init__(self) -> None:
        self._cameras = {"aaa": _Camera("aaa")}

    async def async_refresh(self) -> list[_Camera]:
        return list(self._cameras.values())

    def get(self, did: str) -> _Camera | None:
        return self._cameras.get(did)

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


@pytest.fixture
def bridge(tmp_path: Path) -> BridgeApi:
    """A `BridgeApi` wired to one camera (`aaa`) and its own settings file.

    `access_mode=local` and `supervised=False` together mean the ingress
    guards let every request through unauthenticated -- the same combination
    `test_webauth.py` uses for "a loopback page needs no password". What is
    under test here is the settings endpoints, not the guards in front of
    them.
    """
    options = Options(
        access_mode=AccessMode.LOCAL,
        rtsp_username="",
        rtsp_password="",
        video_quality=VideoQuality.LOW,
        enable_audio=False,
        log_level="info",
        web_password="",
        transcode_quality=TranscodeQuality.STANDARD,
        supervised=False,
    )
    settings_store = SettingsStore(tmp_path / "settings.json")
    registry = _Registry()
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
    )


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
async def test_settings_endpoint_reports_defaults_and_the_read_only_mirror(client):
    response = await client.get("/api/settings")
    assert response.status == 200
    body = await response.json()
    assert body["defaults"]["quality"] == "low"
    # Mirrored, never editable here: these guard access, and one of them is the
    # lock on this very page.
    assert body["addon"]["access_mode"] == "local"
    assert "web_password_set" in body["addon"]


@pytest.mark.asyncio
async def test_the_settings_mirror_never_carries_the_actual_password(
    tmp_path: Path,
) -> None:
    """What the mirror must never do, checked against a real secret.

    The brief's version of this test asserted `"web_password" not in
    json.dumps(body)` against the `bridge` fixture, whose password is empty.
    That assertion can never pass regardless of what leaks: the mirror's own
    key is named `web_password_set`, which contains the substring being
    searched for, so the check fails on the key name before it ever gets to
    look for a value. Asserted here instead is what the key name only *looks*
    like it might be testing -- that the password's actual value is absent --
    against a fixture where that value is non-empty, so there is something
    real for a leak to be caught by.
    """
    options = Options(
        access_mode=AccessMode.LOCAL,
        rtsp_username="",
        rtsp_password="",
        video_quality=VideoQuality.LOW,
        enable_audio=False,
        log_level="info",
        web_password="hunter2-example-secret",
        transcode_quality=TranscodeQuality.STANDARD,
        supervised=False,
    )
    api = BridgeApi(
        account=None,
        registry_provider=lambda: None,
        sessions_provider=lambda: None,
        restreamer=None,
        refresh_callback=None,
        options=options,
        previews=None,
        settings_store=SettingsStore(tmp_path / "settings.json"),
    )
    # `_settings` never reads its `request` argument, so this drives the
    # handler directly rather than through a guarded HTTP round trip -- a
    # real password would otherwise need a guard fixture just to get past.
    response = await api._settings(None)
    body = json.loads(response.body)
    assert options.web_password not in json.dumps(body)
    assert body["addon"]["web_password_set"] is True


@pytest.mark.asyncio
async def test_setting_a_default_takes_effect_for_cameras_that_did_not_override(client):
    await client.put("/api/settings", json={"transcode_quality": "sharp"})
    cameras = await (await client.get("/api/cameras")).json()
    assert cameras["cameras"][0]["settings"]["transcode_quality"] == "sharp"


@pytest.mark.asyncio
async def test_null_clears_an_override_rather_than_setting_it(client):
    await client.put("/api/cameras/aaa/settings", json={"quality": "high"})
    await client.put("/api/cameras/aaa/settings", json={"quality": None})
    cameras = await (await client.get("/api/cameras")).json()
    row = next(c for c in cameras["cameras"] if c["did"] == "aaa")
    assert row["override"] == {}


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
