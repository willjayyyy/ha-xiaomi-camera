"""Add-on options, as provided by Supervisor in ``/data/options.json``.

The same options file drives standalone deployments, where the image is run
under plain Docker with no Supervisor around it. That case is detected rather
than configured -- see :func:`is_supervised` -- because getting it wrong in
either direction is a security question, not a preference.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Container
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from .const import ALL_INTERFACES, DATA_DIR, LOOPBACK, OPTIONS_FILE

_LOGGER = logging.getLogger(__name__)


class AccessMode(StrEnum):
    """Who may reach the published streams."""

    #: Bind to loopback. Home Assistant shares the host network namespace, so it
    #: still reaches the streams while nothing else on the LAN can.
    LOCAL = "local"

    #: Bind to every interface so other machines (Frigate, an NVR) can pull the
    #: streams. Requires credentials -- see :meth:`Options.validate`.
    LAN = "lan"


class VideoQuality(StrEnum):
    """Stream quality requested from the camera."""

    LOW = "low"
    HIGH = "high"


def build_ref() -> str:
    """The commit this image was built from, or ``unknown`` if it says none.

    A version number cannot answer this. Versions are bumped per release
    while the image is rebuilt every time the branch moves, so one version
    covers as many builds as were pushed under it -- and the build check
    compares versions without hashing any source, which is what lets a change
    quietly not reach the image while CI stays green.

    Baked in at build time rather than read from a file in the image: a file
    can be edited in a running container, and then it describes that
    container rather than the build it came from.
    """
    return os.environ.get("BUILD_REF") or "unknown"


def is_supervised(options_path: str | Path = OPTIONS_FILE) -> bool:
    """Whether Home Assistant's Supervisor is managing this container.

    Detected rather than configured: a user who sets such a flag wrongly gets
    either a page they cannot open or one that is not guarded at all, and
    neither failure announces itself.

    Two signals, because the mode with the weaker guard must not be selectable
    by accident. Supervisor injects ``SUPERVISOR_TOKEN`` *and* writes the
    options file; a standalone deployment that inherited the variable from a
    shared compose file or an exported shell has no options file, and would
    otherwise silently start with no password at all.
    """
    return bool(os.environ.get("SUPERVISOR_TOKEN")) and Path(options_path).exists()


@dataclass(frozen=True)
class Options:
    """Validated add-on options."""

    access_mode: AccessMode
    rtsp_username: str
    rtsp_password: str
    video_quality: VideoQuality
    enable_audio: bool
    log_level: str
    web_password: str = ""
    supervised: bool = True

    @property
    def bind_address(self) -> str:
        """Address the RTSP and control-plane listeners bind to.

        This is the only thing standing between the streams and the rest of the
        network: ``host_network`` disables Docker's port isolation entirely, so
        binding is the enforcement point, not port mapping.
        """
        return LOOPBACK if self.access_mode is AccessMode.LOCAL else ALL_INTERFACES

    @property
    def requires_credentials(self) -> bool:
        return self.access_mode is AccessMode.LAN

    def validate(self) -> None:
        """Reject a configuration that would publish something unprotected.

        One rule, and it does not depend on how the add-on is deployed:
        ``local`` keeps everything on this machine and needs no passwords,
        ``lan`` puts it on the network and requires them. Encoding it here
        rather than in documentation means a mistake fails loudly at startup
        instead of quietly publishing the cameras.

        Supervisor's options schema has no conditional -- a field is optional
        or it is not -- so this cannot be expressed as "required when
        access_mode is lan" and the screen will let the combination be saved.
        Startup is the only place left to enforce it, which is why the message
        has to say exactly what to do.
        """
        if not self.requires_credentials:
            return

        missing = []
        if not self.rtsp_username or not self.rtsp_password:
            missing.append("rtsp_username and rtsp_password (they protect the streams)")
        if not self.web_password:
            missing.append(
                "web_password (it protects the page that can view your cameras "
                "and unlink your account)"
            )
        if not missing:
            return
        raise ValueError(
            "access_mode 'lan' publishes this add-on to your whole local "
            "network, so it will not start without: "
            + "; ".join(missing)
            + ". Fill them in, or switch access_mode back to 'local'."
        )


#: Log levels the add-on accepts, mapped to Python's. Defined here rather than
#: at the point of use so an unknown value is rejected while the options are
#: being read, instead of silently falling back to `info` and leaving a user
#: who raised the level to debug wondering why nothing changed.
LOG_LEVELS: Final[dict[str, int]] = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


_DEFAULTS: dict[str, object] = {
    "access_mode": AccessMode.LOCAL.value,
    "rtsp_username": "",
    "rtsp_password": "",
    "video_quality": VideoQuality.LOW.value,
    "enable_audio": False,
    "log_level": "info",
    "web_password": "",
}


#: Environment overrides, for standalone deployments. Supervisor writes
#: ``options.json``; a plain ``docker run`` has no such file, and asking someone
#: to craft one before the container starts is a worse experience than
#: ``-e XIAOMI_CAMERA_ACCESS_MODE=lan``. Under Supervisor these are absent, so
#: the options file remains the only source there.
_ENV_PREFIX = "XIAOMI_CAMERA_"

_ACCESS_MODES: Final = frozenset(mode.value for mode in AccessMode)
_QUALITIES: Final = frozenset(quality.value for quality in VideoQuality)


def _env_overrides() -> dict[str, object]:
    """Option values supplied through the environment."""
    overrides: dict[str, object] = {}
    for key in _DEFAULTS:
        value = os.environ.get(f"{_ENV_PREFIX}{key.upper()}")
        if value is None:
            continue
        if isinstance(_DEFAULTS[key], bool):
            overrides[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        else:
            overrides[key] = value
    return overrides


def _choice(values: Container[str], raw: object, setting: str, allowed: str) -> str:
    """A value from a fixed set, or an error that says what was expected.

    The bare `ValueError` an enum raises names neither the setting nor the
    accepted values, which is little help to someone who typed
    `XIAOMI_CAMERA_ACCESS_MODE=LAN` and cannot see why the container exited.
    """
    value = str(raw).strip().lower()
    if value not in values:
        raise ValueError(f"{setting} must be one of {allowed}, not {raw!r}")
    return value


def load_options(
    path: str | Path = OPTIONS_FILE, *, supervised: bool | None = None
) -> Options:
    """Read and validate the add-on options.

    Missing keys fall back to defaults so an options file written by an older
    add-on version still starts. ``supervised`` is detected when not given;
    the parameter exists so the two deployment modes can be tested without
    manipulating the environment.
    """
    file_path = Path(path)
    raw: dict[str, object] = dict(_DEFAULTS)
    if file_path.exists():
        raw.update(json.loads(file_path.read_text(encoding="utf-8")))
    elif supervised or (supervised is None and is_supervised(file_path)):
        _LOGGER.warning("options file not found at %s, using defaults", file_path)
    raw.update(_env_overrides())

    options = Options(
        access_mode=AccessMode(
            _choice(_ACCESS_MODES, raw["access_mode"], "access_mode", "local or lan")
        ),
        rtsp_username=str(raw["rtsp_username"] or ""),
        rtsp_password=str(raw["rtsp_password"] or ""),
        video_quality=VideoQuality(
            _choice(_QUALITIES, raw["video_quality"], "video_quality", "low or high")
        ),
        enable_audio=bool(raw["enable_audio"]),
        log_level=_choice(
            LOG_LEVELS, raw["log_level"], "log_level", ", ".join(LOG_LEVELS)
        ),
        web_password=str(raw["web_password"] or ""),
        supervised=is_supervised(file_path) if supervised is None else supervised,
    )
    options.validate()
    return options


def data_is_ephemeral() -> bool:
    """Whether ``/data`` looks like it will not survive the container.

    Standalone users who omit ``-v ... :/data`` lose the Xiaomi account link
    the next time they pull a new image, and the symptom -- being asked to sign
    in again after an update -- gives no hint of the cause. A mounted volume or
    bind mount sits on a different device from the image's own filesystem, so
    comparing the two identifies the case without needing to parse mount
    tables.

    Answers ``False`` if anything about the check fails: a spurious warning
    about losing the account link is worse than none.
    """
    try:
        return os.stat(DATA_DIR).st_dev == os.stat("/").st_dev
    except OSError:
        return False
