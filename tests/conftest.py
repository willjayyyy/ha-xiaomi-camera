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
import importlib.util
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
    for stub in (MIoTCameraStatus, MIoTCameraVideoQuality):
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
_miot = types.ModuleType("miot")
_miot.types = _types
sys.modules["miot"] = _miot
sys.modules["miot.types"] = _types


# `pytest-homeassistant-custom-component` lives under `.venv314`, not
# `.venv311` (it requires Python >= 3.14). The fixture is only defined -- and
# so only autoused -- when the plugin is present, or every test collected
# under `.venv311` would fail setup looking for `enable_custom_integrations`.
if importlib.util.find_spec("pytest_homeassistant_custom_component") is not None:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Let Home Assistant load this repository's integration.

        Required by the plugin for anything under `custom_components` since HA
        2021.6; without it a config entry set-up fails with a bare "integration
        not found".
        """
        yield
