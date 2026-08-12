"""Per-camera video settings, and the defaults they fall back to.

Every setting here is about video: which path the picture comes from, how
large a picture the camera is asked for, whether it sends sound, and how
finely the add-on re-encodes it. Settings about *access* -- which address the
streams bind to, the passwords that guard them -- stay in the add-on's own
configuration, because they are not about video and because one of them is
the lock on the page that would otherwise contain them.

`follow default` is represented by the absence of a value. Copying a default
into a camera's record instead would produce a second source for the same
fact: identical on the day it was written, and silently divergent the first
time the default moved. Resolution therefore happens on read.

``path`` is the one field with no default: which paths a camera can even
reach depends on its own model and on whether the compatibility-mode
credential exists, so a global "follow the default" value for it would not
mean anything. A camera with no usable path resolves to ``None`` rather than
to some path it cannot actually use.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import Options, TranscodeQuality, VideoQuality
from .paths import VideoPath, path_for

_LOGGER = logging.getLogger(__name__)

_OWNER_READ_WRITE = 0o600

#: Sentinel distinguishing "leave this field alone" from "clear this field".
#: `None` cannot serve: it is the value that *means* follow-the-default, so a
#: setter using it for both could never clear an override.
_UNSET: Any = object()


@dataclass(frozen=True)
class CameraOverride:
    """What one camera says for itself. ``None`` means: follow the default.

    ``path`` is the exception: it has no default to follow (see the module
    docstring), so ``None`` here means "this camera has not chosen a path
    yet", not "use the shared value".
    """

    quality: VideoQuality | None = None
    audio: bool | None = None
    transcode_quality: TranscodeQuality | None = None
    path: VideoPath | None = None

    def as_dict(self) -> dict[str, object]:
        """Only the fields actually overridden, so the file states no defaults."""

        def scalar(value: object) -> object:
            return value.value if isinstance(value, StrEnum) else value

        return {
            key: scalar(value)
            for key, value in asdict(self).items()
            if value is not None
        }


@dataclass(frozen=True)
class Defaults:
    """What a camera gets when it says nothing for itself."""

    quality: VideoQuality
    audio: bool
    transcode_quality: TranscodeQuality


@dataclass(frozen=True)
class Resolved:
    """One camera's effective settings. Derived, never stored.

    ``path`` defaults to :attr:`VideoPath.OFFICIAL` purely for callers
    outside this module that construct a ``Resolved`` directly (mostly
    tests written before the path field existed) -- ``resolve`` itself always
    passes it explicitly and never relies on the default.
    """

    quality: VideoQuality
    audio: bool
    transcode_quality: TranscodeQuality
    path: VideoPath = VideoPath.OFFICIAL


def resolve(
    defaults: Defaults,
    override: CameraOverride | None,
    *,
    support: str,
    compat_ready: bool,
) -> Resolved | None:
    """Effective settings for one camera, or ``None`` when it has no path.

    A pure function on purpose: this is the rule the whole module exists to
    state, and it should be testable without a filesystem.
    """
    override = override or CameraOverride()
    path = path_for(support, override.path, compat_ready=compat_ready)
    if path is None:
        return None
    return Resolved(
        quality=defaults.quality if override.quality is None else override.quality,
        # `is None` rather than a truth test: `False` is a legitimate override
        # meaning "no sound on this camera", and a truth test would read it as
        # "nothing said" and hand back a default of `True`.
        audio=defaults.audio if override.audio is None else override.audio,
        transcode_quality=(
            defaults.transcode_quality
            if override.transcode_quality is None
            else override.transcode_quality
        ),
        path=path,
    )


_FACTORY = Defaults(
    quality=VideoQuality.LOW,
    audio=False,
    transcode_quality=TranscodeQuality.STANDARD,
)


class SettingsStore:
    """Defaults and per-camera overrides, persisted as JSON."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._defaults = _FACTORY
        self._cameras: dict[str, CameraOverride] = {}
        self._load()

    @property
    def defaults(self) -> Defaults:
        return self._defaults

    def override_for(self, did: str) -> CameraOverride:
        return self._cameras.get(did, CameraOverride())

    def resolved_for(
        self, did: str, *, support: str, compat_ready: bool
    ) -> Resolved | None:
        return resolve(
            self._defaults,
            self._cameras.get(did),
            support=support,
            compat_ready=compat_ready,
        )

    def set_defaults(
        self,
        *,
        quality: VideoQuality = _UNSET,
        audio: bool = _UNSET,
        transcode_quality: TranscodeQuality = _UNSET,
    ) -> None:
        changes = {
            key: value
            for key, value in (
                ("quality", quality),
                ("audio", audio),
                ("transcode_quality", transcode_quality),
            )
            if value is not _UNSET
        }
        self._defaults = replace(self._defaults, **changes)
        self._save()

    def set_override(
        self,
        did: str,
        *,
        quality: VideoQuality | None = _UNSET,
        audio: bool | None = _UNSET,
        transcode_quality: TranscodeQuality | None = _UNSET,
        path: VideoPath | None = _UNSET,
    ) -> None:
        """Set or clear fields on one camera. ``None`` clears (follow default).

        Clearing ``path`` returns the camera to no chosen path at all, not to
        a default -- see the module docstring.
        """
        changes = {
            key: value
            for key, value in (
                ("quality", quality),
                ("audio", audio),
                ("transcode_quality", transcode_quality),
                ("path", path),
            )
            if value is not _UNSET
        }
        current = self._cameras.get(did, CameraOverride())
        updated = replace(current, **changes)
        if updated == CameraOverride():
            self._cameras.pop(did, None)
        else:
            self._cameras[did] = updated
        self._save()

    def prune(self, known_dids: set[str]) -> None:
        """Forget cameras that are no longer on the account."""
        removed = [did for did in self._cameras if did not in known_dids]
        if not removed:
            return
        for did in removed:
            del self._cameras[did]
        self._save()

    def seed_from_options(self, options: Options) -> bool:
        """Adopt the pre-2.0 global values, once.

        Returns whether anything was written. Only runs when no file exists:
        after that the page owns these values and the add-on configuration is
        no longer consulted for them.

        The values are named in the log line rather than merely counted. This
        read happens exactly once per install and cannot be repeated, so it is
        the only chance to see whether the user's own settings were adopted or
        whether factory values were -- and a line that reads the same either
        way is precisely the silent failure this project keeps meeting.
        """
        if self._path.exists():
            return False
        self._defaults = Defaults(
            quality=options.video_quality,
            audio=options.enable_audio,
            transcode_quality=options.transcode_quality,
        )
        self._save()
        _LOGGER.info(
            "Adopted video settings from the add-on configuration: "
            "quality=%s, audio=%s, transcode_quality=%s",
            self._defaults.quality.value,
            self._defaults.audio,
            self._defaults.transcode_quality.value,
        )
        return True

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as err:
            # A corrupt file must not stop the add-on: factory settings publish
            # working streams, while refusing to start publishes none.
            _LOGGER.warning("Could not read %s, using defaults: %s", self._path, err)
            return
        self._defaults = _defaults_from(raw.get("defaults") or {})
        self._cameras = {
            str(did): _override_from(value or {})
            for did, value in (raw.get("cameras") or {}).items()
        }

    def _save(self) -> None:
        payload = {
            "defaults": {
                "quality": self._defaults.quality.value,
                "audio": self._defaults.audio,
                "transcode_quality": self._defaults.transcode_quality.value,
            },
            "cameras": {
                did: override.as_dict() for did, override in self._cameras.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        # Opened with the restrictive mode rather than chmod'ed afterwards:
        # the latter leaves a window in which the file exists world-readable.
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_READ_WRITE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        # Atomic replace keeps a crash mid-write from leaving a half-written
        # file that would read as corrupt and silently discard every
        # camera's settings back to factory defaults.
        temporary.replace(self._path)


def _defaults_from(raw: dict) -> Defaults:
    try:
        return Defaults(
            quality=VideoQuality(raw["quality"]),
            audio=bool(raw["audio"]),
            transcode_quality=TranscodeQuality(raw["transcode_quality"]),
        )
    except Exception as err:
        # Same choice as `_load`'s handler -- carry on with factory values
        # rather than refuse to start -- and it is said out loud for the same
        # reason. A `defaults` section that no longer parses reverts every
        # camera that follows the default to factory settings, which looks to
        # the user like the add-on quietly changed its mind.
        _LOGGER.warning("Unreadable default video settings, using factory: %s", err)
        return _FACTORY


def _override_from(raw: dict) -> CameraOverride:
    def enum(cls, key):
        try:
            return cls(raw[key]) if key in raw else None
        except ValueError:
            return None

    return CameraOverride(
        quality=enum(VideoQuality, "quality"),
        audio=bool(raw["audio"]) if "audio" in raw else None,
        transcode_quality=enum(TranscodeQuality, "transcode_quality"),
        path=enum(VideoPath, "path"),
    )
