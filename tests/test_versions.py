"""The repository ships two things and they must claim the same version.

The add-on's `config.yaml` and the integration's `manifest.json` are read by
different systems -- Supervisor and HACS -- and drifted apart within a few
releases because each was bumped where it was being worked on. That is not
cosmetic: the release tag names one version, and a user reading "0.4.3" in the
add-on store has no way to tell which integration they got.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ADDON = _ROOT / "addon" / "config.yaml"
_MANIFEST = _ROOT / "custom_components" / "xiaomi_camera" / "manifest.json"


def addon_version() -> str:
    match = re.search(r'^version:\s*"([^"]+)"', _ADDON.read_text(), re.M)
    assert match, "addon/config.yaml has no version"
    return match.group(1)


def integration_version() -> str:
    return json.loads(_MANIFEST.read_text())["version"]


def test_the_two_halves_agree() -> None:
    assert addon_version() == integration_version(), (
        "addon/config.yaml and the integration manifest disagree; a release "
        "tag can only name one of them"
    )


def test_the_version_is_a_release_number() -> None:
    """Both Supervisor and HACS sort these, so they have to be sortable.

    A preview build carries a `-preview` suffix on a branch while it is being
    validated on real hardware; the final release is the plain form.
    """
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-preview)?", addon_version()), addon_version()


def test_the_changelog_mentions_it() -> None:
    """Home Assistant shows this on the update screen.

    A version with no entry appears there as "No changelog found", which is
    what a user sees at the moment they are deciding whether to update.
    """
    changelog = (_ROOT / "addon" / "CHANGELOG.md").read_text()
    assert f"## {addon_version()}" in changelog
