"""Make the add-on's bridge package importable, and stub the vendor SDK.

The vendor SDK is closed source, glibc-only and around 28MB of prebuilt
libraries, so CI does not install it and most of the bridge is never imported
during a test run. That left ``bridge.streaming`` -- where a session's whole
lifetime is decided -- untested, and two faults lived there unnoticed: a
session that never reconnected after its peer-to-peer link died, and a cold
start that handed consumers a stream they could not yet decode.

The only names that module needs from the SDK at runtime are two enums; its
client and camera info arrive as constructor arguments and can be supplied by
the tests. Standing those enums up here makes the module importable without
the SDK, so the code that actually breaks can be exercised.

The stub is checked against the real enums whenever they happen to be
installed. Without that, a value drifting in the SDK would leave these tests
passing against a definition the add-on no longer uses -- an indicator wired
to a different circuit.

``custom_components.xiaomi_camera`` needs no such stub: with ``homeassistant``
installed via ``requirements-test.txt``, the real ``__init__.py`` imports
cleanly, and ``pythonpath = .`` in ``pytest.ini`` makes the package importable
by its real path.
"""

import enum
import sys
import types
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app"
sys.path.insert(0, str(_APP))


class MIoTCameraStatus(int, enum.Enum):
    """Mirrors ``miot.types.MIoTCameraStatus``."""

    DISCONNECTED = 1
    CONNECTING = 2
    RE_CONNECTING = 3
    CONNECTED = 4
    ERROR = 5


class MIoTCameraVideoQuality(int, enum.Enum):
    """Mirrors ``miot.types.MIoTCameraVideoQuality``."""

    LOW = 1
    HIGH = 3


class MIoTCameraCodec(int, enum.Enum):
    """Mirrors ``miot.types.MIoTCameraCodec``.

    ``VIDEO_H265`` is an alias of ``VIDEO_HEVC`` upstream and is kept as one
    here: an alias is skipped when an enum is iterated, so defining it as a
    distinct member would make the drift check below compare six names against
    seven and fail against an SDK that had not changed.
    """

    VIDEO_H264 = 4
    VIDEO_HEVC = 5
    VIDEO_H265 = 5

    AUDIO_PCM = 1024
    AUDIO_G711U = 1026
    AUDIO_G711A = 1027
    AUDIO_OPUS = 1032


class MIoTClient:
    """Stands in for ``miot.client.MIoTClient``.

    ``bridge.account`` imports the real class unconditionally (not under
    ``TYPE_CHECKING``), and ``bridge.api`` imports ``bridge.account``, so
    nothing under ``bridge.api`` -- including ``BridgeApi`` itself -- can be
    imported without this name existing, even in a test that never
    constructs an ``AccountManager``. Unlike the enums above this carries no
    behaviour to drift, so there is nothing here to verify against the real
    SDK -- it exists only so the import machinery has a name to bind.
    """


def _verify_against_real_sdk() -> None:
    """Fail loudly if the stub and the installed SDK disagree.

    Only runs where the SDK is present -- a developer machine, never CI. The
    point is that a value changing upstream is caught by someone, rather than
    quietly turning every session test into a test of a definition that no
    longer exists.
    """
    try:
        import miot.types as real
    except Exception:
        return
    for stub in (MIoTCameraStatus, MIoTCameraVideoQuality, MIoTCameraCodec):
        actual = getattr(real, stub.__name__, None)
        if actual is None:  # pragma: no cover - upstream rename
            raise AssertionError(f"{stub.__name__} no longer exists in miot.types")
        expected = {member.name: member.value for member in stub}
        found = {member.name: member.value for member in actual}
        if expected != found:  # pragma: no cover - upstream change
            raise AssertionError(
                f"{stub.__name__} drifted: stub {expected}, SDK {found}"
            )


_verify_against_real_sdk()

# Registered unconditionally, so a run on a machine that happens to have the
# SDK exercises the same definitions as CI rather than a second, untested
# configuration.
_types = types.ModuleType("miot.types")
_types.MIoTCameraStatus = MIoTCameraStatus
_types.MIoTCameraVideoQuality = MIoTCameraVideoQuality
_types.MIoTCameraCodec = MIoTCameraCodec
_client = types.ModuleType("miot.client")
_client.MIoTClient = MIoTClient
_miot = types.ModuleType("miot")
_miot.types = _types
_miot.client = _client
sys.modules["miot"] = _miot
sys.modules["miot.types"] = _types
sys.modules["miot.client"] = _client


# `pytest-homeassistant-custom-component` requires Python >= 3.14, so it is
# absent on any older interpreter. The fixture is only
# defined -- and so only autoused -- above that version, gated on the
# interpreter rather than on whether importing the plugin happens to
# succeed: a `.venv314` install that is present but broken should fail
# collection loudly, not silently behave as if the plugin were absent on
# purpose. Below 3.14 there is nothing under `custom_components` for it to
# gate, since those tests are themselves version-gated the same way (see
# `test_streams.py`, `test_migration.py`).
if sys.version_info >= (3, 14):

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Let Home Assistant load this repository's integration.

        Required by the plugin for anything under `custom_components` since HA
        2021.6; without it a config entry set-up fails with a bare "integration
        not found".
        """
        yield


else:

    @pytest.fixture
    def socket_enabled():
        """A no-op stand-in for the fixture `pytest-socket` provides.

        On Python >= 3.14 the Home Assistant plugin brings `pytest-socket`,
        which blocks socket use so an integration test cannot quietly reach the
        network. The add-on's own tests do the opposite on purpose: they drive
        the real HTTP handler over a loopback server, which is the only way to
        prove the endpoint answers what go2rtc reads. Those tests therefore
        depend on `socket_enabled` to opt back in.

        Below that the plugin is absent, so that fixture does not exist
        and depending on it would be a collection error. Defining a no-op here
        lets one test file make sense in both interpreters, rather than
        splitting it or guarding every test with a skip.
        """
        yield
