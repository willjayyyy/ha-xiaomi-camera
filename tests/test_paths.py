"""Who answers "where does this camera's video come from", and with what."""

import pytest
from bridge.paths import (
    VideoPath,
    available_paths,
    path_for,
)


def test_supported_camera_defaults_to_official():
    assert path_for("full", None, compat_ready=False) is VideoPath.OFFICIAL


def test_refused_camera_has_no_path_until_the_user_picks_one():
    """Never auto-selects compatibility mode.

    Choosing it for the user would commit them to handing over a password
    they have not been asked for.
    """
    assert path_for("limited", None, compat_ready=True) is None
    assert path_for("unsupported", None, compat_ready=True) is None


def test_a_stored_override_wins():
    assert path_for("full", VideoPath.COMPAT, compat_ready=True) is VideoPath.COMPAT
    assert path_for("limited", VideoPath.COMPAT, compat_ready=True) is VideoPath.COMPAT


def test_an_override_survives_the_credential_being_removed():
    """C2: deleting the credential must not destroy entities.

    The stored path is what keeps the camera publishable, so the entity stays
    and reports unavailable rather than disappearing.
    """
    assert path_for("limited", VideoPath.COMPAT, compat_ready=False) is VideoPath.COMPAT


@pytest.mark.parametrize(
    ("support", "compat_ready", "official_reason", "compat_reason"),
    [
        ("full", False, None, "pathCompatNoAuth"),
        ("full", True, None, None),
        ("limited", True, "pathOfficialUnsupported", None),
        ("unsupported", False, "pathOfficialUnsupported", "pathCompatNoAuth"),
    ],
)
def test_available_paths_names_why_each_is_unavailable(
    support, compat_ready, official_reason, compat_reason
):
    reasons = available_paths(support, compat_ready=compat_ready)
    assert reasons[VideoPath.OFFICIAL] == official_reason
    assert reasons[VideoPath.COMPAT] == compat_reason


def test_no_other_module_answers_these_two_questions():
    """The guard the four-way drift taught us to write."""
    import pathlib

    root = pathlib.Path("addon/rootfs/app/bridge")
    offenders = []
    # Task B3 removes cameras.py from this allowlist
    allowed_offenders = {"cameras.py: decides a path from support"}
    for source in root.glob("*.py"):
        if source.name == "paths.py":
            continue
        text = source.read_text()
        if 'support == "full"' in text:
            offenders.append(f"{source.name}: decides a path from support")
        if '"low", "high"' in text or "'low', 'high'" in text:
            offenders.append(f"{source.name}: hardcodes a quality ladder")
    offenders = [o for o in offenders if o not in allowed_offenders]
    assert not offenders, offenders
