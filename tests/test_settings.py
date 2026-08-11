"""Per-camera settings and the defaults they fall back to.

`follow default` is the absence of a value, never a copy of one. A copy made
when a camera is first seen looks identical on the day it is written and
drifts the first time a default changes -- which is the failure this whole
module exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

from bridge.config import TranscodeQuality, VideoQuality
from bridge.settings import (
    CameraOverride,
    Defaults,
    SettingsStore,
    resolve,
)

DEFAULTS = Defaults(
    quality=VideoQuality.LOW,
    audio=False,
    transcode_quality=TranscodeQuality.STANDARD,
)


def test_absent_override_resolves_to_defaults():
    result = resolve(DEFAULTS, None)
    assert result.quality is VideoQuality.LOW
    assert result.audio is False
    assert result.transcode_quality is TranscodeQuality.STANDARD


def test_partial_override_resolves_field_by_field():
    override = CameraOverride(
        quality=VideoQuality.HIGH, audio=None, transcode_quality=None
    )
    result = resolve(DEFAULTS, override)
    assert result.quality is VideoQuality.HIGH
    assert result.audio is False


def test_audio_false_is_an_override_not_an_absence():
    """`False` must not be mistaken for "unset" -- the classic falsy bug."""
    louder = Defaults(
        quality=VideoQuality.LOW,
        audio=True,
        transcode_quality=TranscodeQuality.STANDARD,
    )
    override = CameraOverride(quality=None, audio=False, transcode_quality=None)
    assert resolve(louder, override).audio is False


def test_changing_a_default_moves_every_camera_that_did_not_override(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_override("aaa", quality=VideoQuality.HIGH)
    store.set_defaults(quality=VideoQuality.HIGH)
    store.set_defaults(quality=VideoQuality.LOW)

    # "bbb" never overrode anything, so it follows the default wherever it goes.
    assert store.resolved_for("bbb").quality is VideoQuality.LOW
    # "aaa" pinned its own value and must not have been dragged along.
    assert store.resolved_for("aaa").quality is VideoQuality.HIGH


def test_clearing_an_override_returns_the_camera_to_the_default(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_override("aaa", quality=VideoQuality.HIGH)
    store.set_override("aaa", quality=None)
    assert store.override_for("aaa").quality is None
    assert store.resolved_for("aaa").quality is DEFAULTS.quality


def test_settings_survive_a_restart(tmp_path: Path):
    path = tmp_path / "settings.json"
    first = SettingsStore(path)
    first.set_defaults(transcode_quality=TranscodeQuality.SHARP)
    first.set_override("aaa", audio=True)

    second = SettingsStore(path)
    assert second.defaults.transcode_quality is TranscodeQuality.SHARP
    assert second.resolved_for("aaa").audio is True


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


def test_pruning_forgets_cameras_that_no_longer_exist(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set_override("gone", audio=True)
    store.set_override("kept", audio=True)
    store.prune({"kept"})
    assert store.override_for("gone") == CameraOverride()
    assert store.override_for("kept").audio is True
