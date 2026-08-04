"""Add-on options, with emphasis on the rule that keeps streams private.

``host_network`` removes Docker's port isolation, so the bind address is the
only thing separating the camera streams from the rest of the network. These
tests pin that behaviour: a published stream always has a password, whether
the user set one or the add-on had to supply it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bridge import config
from bridge.config import AccessMode, Options, VideoQuality, load_options


def write_options(tmp_path: Path, **overrides: object) -> Path:
    payload = {
        "access_mode": "local",
        "rtsp_username": "",
        "rtsp_password": "",
        "video_quality": "low",
        "enable_audio": False,
        "log_level": "info",
    }
    payload.update(overrides)
    path = tmp_path / "options.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestBindAddress:
    def test_local_mode_binds_to_loopback(self) -> None:
        options = Options(
            access_mode=AccessMode.LOCAL,
            rtsp_username="",
            rtsp_password="",
            video_quality=VideoQuality.LOW,
            enable_audio=False,
            log_level="info",
        )
        assert options.bind_address == "127.0.0.1"

    def test_lan_mode_binds_to_all_interfaces(self) -> None:
        options = Options(
            access_mode=AccessMode.LAN,
            rtsp_username="user",
            rtsp_password="secret",
            video_quality=VideoQuality.LOW,
            enable_audio=False,
            log_level="info",
        )
        assert options.bind_address == "0.0.0.0"


class TestValidation:
    """`lan` publishes this add-on, so it must not start without passwords.

    One rule for every deployment. Supervisor's options schema cannot express
    "required when access_mode is lan" -- a field is optional or it is not --
    so the screen lets the combination be saved and startup is the only place
    left to enforce it. That makes the message part of the contract: it has to
    name every field that is missing, because it is all the user gets.
    """

    def lan(self, tmp_path: Path, **overrides: object) -> Path:
        return write_options(tmp_path, access_mode="lan", **overrides)

    @pytest.mark.parametrize("supervised", [True, False])
    def test_lan_without_anything_is_rejected(
        self, tmp_path: Path, supervised: bool
    ) -> None:
        with pytest.raises(ValueError) as raised:
            load_options(self.lan(tmp_path), supervised=supervised)

        message = str(raised.value)
        # Both, in one message: reporting them one restart at a time makes a
        # user fix the same configuration twice.
        assert "rtsp_username" in message
        assert "web_password" in message

    def test_lan_without_a_web_password_is_rejected(self, tmp_path: Path) -> None:
        path = self.lan(tmp_path, rtsp_username="u", rtsp_password="p")
        with pytest.raises(ValueError, match="web_password"):
            load_options(path, supervised=True)

    def test_lan_without_rtsp_credentials_is_rejected(self, tmp_path: Path) -> None:
        path = self.lan(tmp_path, web_password="w")
        with pytest.raises(ValueError, match="rtsp_username"):
            load_options(path, supervised=True)

    def test_lan_with_only_a_username_is_rejected(self, tmp_path: Path) -> None:
        path = self.lan(tmp_path, rtsp_username="u", web_password="w")
        with pytest.raises(ValueError, match="rtsp_username"):
            load_options(path, supervised=True)

    @pytest.mark.parametrize("supervised", [True, False])
    def test_lan_with_everything_is_accepted(
        self, tmp_path: Path, supervised: bool
    ) -> None:
        path = self.lan(
            tmp_path, rtsp_username="u", rtsp_password="p", web_password="w"
        )
        assert load_options(path, supervised=supervised).access_mode is AccessMode.LAN

    @pytest.mark.parametrize("supervised", [True, False])
    def test_local_needs_no_passwords(self, tmp_path: Path, supervised: bool) -> None:
        # Nothing is published, so there is nothing to protect. Demanding
        # passwords here would be a step every user takes for no reason.
        options = load_options(write_options(tmp_path), supervised=supervised)
        assert options.access_mode is AccessMode.LOCAL
        assert options.rtsp_password == ""
        assert options.web_password == ""


class TestDeploymentDetection:
    """Which mode is chosen must not hinge on one stray variable.

    The standalone branch carries the weaker guard, and the supervised branch
    generates no password at all. Selecting the wrong one by accident is
    silent in both directions, so detection takes two signals.
    """

    def test_both_signals_present_means_supervised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
        assert config.is_supervised(write_options(tmp_path)) is True

    def test_an_inherited_token_alone_is_not_enough(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A compose file or exported shell variable shared with a Home
        # Assistant stack would otherwise start the bridge with no password
        # and no warning.
        monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
        assert config.is_supervised(tmp_path / "absent.json") is False

    def test_no_token_means_standalone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert config.is_supervised(write_options(tmp_path)) is False


class TestBadValues:
    """An error a user can act on, rather than one that names nothing."""

    def test_an_unknown_access_mode_names_the_setting(self, tmp_path: Path) -> None:
        path = write_options(tmp_path, access_mode="public")
        with pytest.raises(ValueError, match="access_mode must be one of"):
            load_options(path, supervised=True)

    def test_an_unknown_log_level_is_rejected(self, tmp_path: Path) -> None:
        # Previously this fell back to `info` in silence, so a user following
        # the troubleshooting instructions saw no extra output and no reason.
        path = write_options(tmp_path, log_level="verbose")
        with pytest.raises(ValueError, match="log_level must be one of"):
            load_options(path, supervised=True)

    def test_case_and_whitespace_are_forgiven(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `-e XIAOMI_CAMERA_ACCESS_MODE=LAN` is the natural thing to type.
        monkeypatch.setenv("XIAOMI_CAMERA_ACCESS_MODE", " LAN ")
        monkeypatch.setenv("XIAOMI_CAMERA_RTSP_USERNAME", "u")
        monkeypatch.setenv("XIAOMI_CAMERA_RTSP_PASSWORD", "p")
        monkeypatch.setenv("XIAOMI_CAMERA_WEB_PASSWORD", "w")
        options = load_options(write_options(tmp_path), supervised=True)
        assert options.access_mode is AccessMode.LAN


class TestEnvironmentOverrides:
    """`docker run -e ...` in place of an options file.

    Standalone users have no Supervisor writing `options.json`, and asking
    them to craft one before the first start would be its own configuration
    step.
    """

    def test_environment_supplies_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XIAOMI_CAMERA_ACCESS_MODE", "lan")
        monkeypatch.setenv("XIAOMI_CAMERA_RTSP_USERNAME", "nvr")
        monkeypatch.setenv("XIAOMI_CAMERA_RTSP_PASSWORD", "from-env")
        monkeypatch.setenv("XIAOMI_CAMERA_WEB_PASSWORD", "from-env-too")

        options = load_options(tmp_path / "absent.json", supervised=False)
        assert options.access_mode is AccessMode.LAN
        assert options.rtsp_password == "from-env"

    def test_environment_wins_over_the_options_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XIAOMI_CAMERA_VIDEO_QUALITY", "high")
        path = write_options(tmp_path, video_quality="low")

        assert load_options(path, supervised=False).video_quality is VideoQuality.HIGH

    def test_booleans_are_read_as_booleans(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every environment variable is a string, and a bare bool("false")
        # would silently be True.
        path = write_options(tmp_path)

        monkeypatch.setenv("XIAOMI_CAMERA_ENABLE_AUDIO", "false")
        assert load_options(path, supervised=False).enable_audio is False

        monkeypatch.setenv("XIAOMI_CAMERA_ENABLE_AUDIO", "true")
        assert load_options(path, supervised=False).enable_audio is True
