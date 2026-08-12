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
import yaml
from bridge import config
from bridge.config import AccessMode, Options, load_options

_ADDON_DIR = Path(__file__).resolve().parent.parent / "addon"


def write_options(tmp_path: Path, **overrides: object) -> Path:
    payload = {
        "access_mode": "local",
        "rtsp_username": "",
        "rtsp_password": "",
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
            log_level="info",
        )
        assert options.bind_address == "127.0.0.1"

    def test_lan_mode_binds_to_all_interfaces(self) -> None:
        options = Options(
            access_mode=AccessMode.LAN,
            rtsp_username="user",
            rtsp_password="secret",
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
        monkeypatch.setenv("XIAOMI_CAMERA_RTSP_USERNAME", "from-env")
        path = write_options(tmp_path, rtsp_username="from-file")

        assert load_options(path, supervised=False).rtsp_username == "from-env"


class TestTheImageSaysWhatItWasBuiltFrom:
    """A version number cannot identify a build, and was relied on to.

    Add-on versions are bumped per release, but an image is rebuilt every
    time the branch moves -- so one version number can, and did, cover two
    different builds. The add-on's own build check compares version numbers
    and never hashes the source, which is what makes "the change silently
    did not reach the image, and CI was green" possible.

    A reference the running process prints is the thing that settles it, and
    it has to be printed: one that nothing logs is one nobody can check,
    which is precisely the situation it exists to end.
    """

    def test_it_reports_the_commit_it_was_built_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUILD_REF", "0838020")
        assert config.build_ref() == "0838020"

    def test_it_says_so_when_the_image_carries_no_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A local build has none, and must not look like it has one."""
        monkeypatch.delenv("BUILD_REF", raising=False)
        assert config.build_ref() == "unknown"

    def test_the_startup_line_prints_it(self) -> None:
        import re

        source = (
            Path(__file__).resolve().parent.parent
            / "addon"
            / "rootfs"
            / "app"
            / "bridge"
            / "__main__.py"
        ).read_text(encoding="utf-8")
        banner = re.search(r'"Starting bridge[^"]*"', source)
        assert banner is not None, "the startup line moved or was renamed"
        assert "build=%s" in banner.group()
        assert "build_ref()" in source


class TestTheAddOnDeclaresEverySetting:
    """A setting has to exist in three places, and nothing links them.

    `_DEFAULTS` is what the bridge reads, `config.yaml`'s `options` is what a
    fresh install starts with, and its `schema` is what Supervisor's form will
    accept. Missing from any one of them the setting fails differently and
    quietly: unschema'd it cannot be typed, undefaulted it is dropped on read,
    unoffered it never appears. This project has shipped a setting that was
    threaded everywhere except the one place that assigned it.
    """

    @staticmethod
    def _addon() -> dict:
        return yaml.safe_load((_ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))

    def test_every_setting_has_a_starting_value(self) -> None:
        assert set(config._DEFAULTS) == set(self._addon()["options"])

    def test_every_setting_can_be_typed_into_the_form(self) -> None:
        assert set(config._DEFAULTS) == set(self._addon()["schema"])


def test_the_moved_options_are_gone() -> None:
    """A major version does not carry compatibility settings. There is no
    defaults record to seed any more either, so the migration they existed
    for has nothing left to do."""
    addon = TestTheAddOnDeclaresEverySetting._addon()
    for key in ("video_quality", "transcode_quality", "enable_audio"):
        assert key not in addon["options"]
        assert key not in addon["schema"]
        assert key not in config._DEFAULTS


def test_no_translation_still_says_moved() -> None:
    for name in ("en.yaml", "zh-Hans.yaml"):
        text = (_ADDON_DIR / "translations" / name).read_text(encoding="utf-8")
        assert "(moved)" not in text
        assert "已迁移" not in text


def test_an_options_file_carrying_the_retired_keys_still_loads(
    tmp_path: Path,
) -> None:
    """An upgrading 1.4.0 install still has these in `options.json` until

    Supervisor rewrites it. Whatever Supervisor's own tolerance for keys
    outside the schema turns out to be, the add-on's own reader must not
    choke on them: they load cleanly and are simply not consulted.
    """
    path = write_options(
        tmp_path,
        video_quality="high",
        transcode_quality="sharp",
        enable_audio=True,
    )
    options = load_options(path, supervised=True)
    assert options.access_mode is AccessMode.LOCAL
    assert not hasattr(options, "video_quality")
    assert not hasattr(options, "transcode_quality")
    assert not hasattr(options, "enable_audio")
