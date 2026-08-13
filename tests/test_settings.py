"""Per-camera settings and the defaults they fall back to.

`follow default` is the absence of a value, never a copy of one. A copy made
when a camera is first seen looks identical on the day it is written and
drifts the first time a default changes -- which is the failure this whole
module exists to prevent.

``path`` is not part of that mechanism: which paths a camera can reach
depends on its own model and on whether the compatibility-mode credential
exists, so it has no default to follow -- see ``resolve``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bridge.config import TranscodeQuality, VideoQuality
from bridge.paths import VideoPath
from bridge.settings import (
    CameraOverride,
    Defaults,
    SettingsStore,
    resolve,
)

DEFAULTS = Defaults(
    quality=VideoQuality.LOW,
    audio=True,
    transcode_quality=TranscodeQuality.STANDARD,
)


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


def test_absent_override_resolves_to_defaults():
    result = resolve(DEFAULTS, None, support="full", compat_ready=False)
    assert result.quality is VideoQuality.LOW
    assert result.audio is True
    assert result.transcode_quality is TranscodeQuality.STANDARD
    assert result.path is VideoPath.OFFICIAL


def test_partial_override_resolves_field_by_field():
    override = CameraOverride(
        quality=VideoQuality.HIGH, audio=None, transcode_quality=None
    )
    result = resolve(DEFAULTS, override, support="full", compat_ready=False)
    assert result.quality is VideoQuality.HIGH
    assert result.audio is True


def test_audio_false_is_an_override_not_an_absence():
    """`False` must not be mistaken for "unset" -- the classic falsy bug."""
    louder = Defaults(
        quality=VideoQuality.LOW,
        audio=True,
        transcode_quality=TranscodeQuality.STANDARD,
    )
    override = CameraOverride(quality=None, audio=False, transcode_quality=None)
    assert resolve(louder, override, support="full", compat_ready=False).audio is False


def test_a_camera_with_no_path_resolves_to_nothing():
    """C2/R1: a refused camera with no chosen path cannot be published."""
    result = resolve(DEFAULTS, None, support="unsupported", compat_ready=False)
    assert result is None


def test_a_camera_on_compat_stays_resolved_when_the_credential_is_removed():
    """R2: `publishable` is the only fact that decides whether Home Assistant
    keeps or destroys a camera's entities. A camera the user put on
    compatibility mode must keep resolving to something even after the
    credential that made compatibility mode work is gone -- losing it here
    would unpair HomeKit and orphan history with nothing logged.
    """
    override = CameraOverride(path=VideoPath.COMPAT)
    result = resolve(DEFAULTS, override, support="limited", compat_ready=False)
    assert result is not None
    assert result.path is VideoPath.COMPAT


def test_changing_a_default_moves_every_camera_that_did_not_override(
    store: SettingsStore,
):
    store.set_override("aaa", quality=VideoQuality.HIGH)
    store.set_defaults(quality=VideoQuality.HIGH)
    store.set_defaults(quality=VideoQuality.LOW)

    # "bbb" never overrode anything, so it follows the default wherever it goes.
    assert store.resolved_for("bbb", support="full", compat_ready=False).quality is (
        VideoQuality.LOW
    )
    # "aaa" pinned its own value and must not have been dragged along.
    assert store.resolved_for("aaa", support="full", compat_ready=False).quality is (
        VideoQuality.HIGH
    )


def test_clearing_an_override_returns_the_camera_to_the_default(store: SettingsStore):
    store.set_override("aaa", quality=VideoQuality.HIGH)
    store.set_override("aaa", quality=None)
    assert store.override_for("aaa").quality is None
    resolved = store.resolved_for("aaa", support="full", compat_ready=False)
    assert resolved.quality is DEFAULTS.quality


def test_settings_survive_a_restart(tmp_path: Path):
    path = tmp_path / "settings.json"
    first = SettingsStore(path)
    first.set_defaults(transcode_quality=TranscodeQuality.SHARP)
    first.set_override("aaa", audio=True)

    second = SettingsStore(path)
    assert second.defaults.transcode_quality is TranscodeQuality.SHARP
    resolved = second.resolved_for("aaa", support="full", compat_ready=False)
    assert resolved.audio is True


def test_stored_file_holds_no_copied_defaults(tmp_path: Path):
    """The persisted override must record only what was actually overridden."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.set_override("aaa", quality=VideoQuality.HIGH)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["cameras"]["aaa"] == {"quality": "high"}


def test_unreadable_file_falls_back_to_defaults_rather_than_failing(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = SettingsStore(path)
    assert store.defaults.quality is VideoQuality.LOW


def test_pruning_forgets_cameras_that_no_longer_exist(store: SettingsStore):
    store.set_override("gone", audio=True)
    store.set_override("kept", audio=True)
    store.prune({"kept"})
    assert store.override_for("gone") == CameraOverride()
    assert store.override_for("kept").audio is True


def test_a_camera_can_pin_its_own_path(store: SettingsStore):
    store.set_override("aaa", path=VideoPath.COMPAT)
    assert store.override_for("aaa").path is VideoPath.COMPAT
    resolved = store.resolved_for("aaa", support="full", compat_ready=True)
    assert resolved.path is VideoPath.COMPAT


def test_a_camera_s_path_survives_the_credential_being_removed(store: SettingsStore):
    """R2, exercised through the store rather than the bare function: a
    camera parked on compatibility mode must stay in the publishable list
    even after `compat_ready` turns false, or Home Assistant deletes its
    entities.
    """
    store.set_override("aaa", path=VideoPath.COMPAT)
    resolved = store.resolved_for("aaa", support="limited", compat_ready=False)
    assert resolved is not None
    assert resolved.path is VideoPath.COMPAT


def test_a_path_override_persists_through_a_restart(tmp_path: Path):
    path = tmp_path / "settings.json"
    first = SettingsStore(path)
    first.set_override("aaa", path=VideoPath.COMPAT)

    second = SettingsStore(path)
    assert second.override_for("aaa").path is VideoPath.COMPAT


def test_path_has_no_place_in_the_defaults_record(store: SettingsStore):
    """R1b: path depends on the camera's own model and credentials, so it
    cannot be expressed as a shared default -- only as a per-camera choice.
    """
    assert not hasattr(store.defaults, "path")
