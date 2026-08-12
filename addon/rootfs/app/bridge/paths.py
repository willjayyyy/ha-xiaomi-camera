"""Where a camera's video comes from, and what it can be asked for.

Two questions with exactly one answer each. The project has shipped the
alternative: during the multi-stream work "which stream is this camera's
default" was answered independently in four places, drifted, and destroyed
and recreated every entity on upgrade -- unpairing HomeKit and orphaning
history with nothing logged. A fact with two sources needs a test to hold it;
a fact with one source is held by the structure.
"""

from __future__ import annotations

from enum import StrEnum


class VideoPath(StrEnum):
    """The two implementations that can reach a camera.

    Named for what the user chooses between, not for who wrote the code --
    `paths.py` is also where the translation keys live, and the interface,
    the log and the docs all use these same two words.
    """

    OFFICIAL = "official"
    COMPAT = "compat"


def path_for(
    support: str, override: VideoPath | None, *, compat_ready: bool
) -> VideoPath | None:
    """This camera's video path, or ``None`` when it has none.

    ``compat_ready`` is deliberately not consulted when an override exists.
    A camera the user put on compatibility mode keeps that path when the
    credential is removed, which is what keeps it publishable and therefore
    keeps its Home Assistant entities alive -- see the test named for it.
    """
    if override is not None:
        return override
    if support == "full":
        return VideoPath.OFFICIAL
    # Never chosen automatically: compatibility mode costs the user a
    # password, and spending that on their behalf is not ours to do.
    return None


def available_paths(support: str, *, compat_ready: bool) -> dict[VideoPath, str | None]:
    """Both paths, each mapped to why it is unavailable or ``None`` if it is.

    Both keys are always present: the interface shows both options on every
    camera and disables the one that cannot be used, so the reason has to
    exist even when the answer is "not on this model".
    """
    return {
        VideoPath.OFFICIAL: None if support == "full" else "pathOfficialUnsupported",
        VideoPath.COMPAT: None if compat_ready else "pathCompatNoAuth",
    }
