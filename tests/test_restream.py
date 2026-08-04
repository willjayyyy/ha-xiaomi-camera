"""go2rtc configuration.

These tests pin the exposure boundary. `host_network` removes Docker's port
isolation entirely, so which address each listener binds to is the only thing
separating the cameras from the rest of the network — and only the stream
listeners may ever follow `access_mode`.
"""

from __future__ import annotations

import pytest
from bridge.config import AccessMode, Options, VideoQuality
from bridge.const import GO2RTC_API_PORT, LOOPBACK, RTSP_PORT, SRTP_PORT, WEBRTC_PORT
from bridge.restream import build_config, h264_stream_name, stream_name


def make_options(mode: AccessMode, user: str = "", password: str = "") -> Options:
    return Options(
        access_mode=mode,
        rtsp_username=user,
        rtsp_password=password,
        video_quality=VideoQuality.LOW,
        enable_audio=False,
        log_level="info",
    )


class TestLocalMode:
    @pytest.fixture
    def config(self) -> dict:
        return build_config(make_options(AccessMode.LOCAL), ["1", "2"])

    @pytest.mark.parametrize("module", ["api", "rtsp", "webrtc", "srtp"])
    def test_every_listener_is_loopback_only(self, config: dict, module: str) -> None:
        assert config[module]["listen"].startswith(f"{LOOPBACK}:")

    def test_all_of_go2rtcs_listeners_are_pinned(self, config: dict) -> None:
        """Modules omitted from the config fall back to go2rtc's defaults.

        Several of those defaults bind every interface, so leaving a module out
        would quietly contradict the loopback-only guarantee.
        """
        assert {"api", "rtsp", "webrtc", "srtp"} <= set(config)

    def test_no_credentials_are_written(self, config: dict) -> None:
        assert "username" not in config["rtsp"]
        assert "password" not in config["rtsp"]


class TestLanMode:
    @pytest.fixture
    def config(self) -> dict:
        return build_config(make_options(AccessMode.LAN, "user", "secret"), ["1"])

    @pytest.mark.parametrize("module", ["rtsp", "webrtc"])
    def test_stream_listeners_are_published(self, config: dict, module: str) -> None:
        assert config[module]["listen"].startswith("0.0.0.0:")

    @pytest.mark.parametrize("module", ["api", "srtp"])
    def test_non_stream_listeners_stay_local(self, config: dict, module: str) -> None:
        """`access_mode` must move only the streams a user asked to publish.

        go2rtc's own API can add streams and read state; publishing it would
        hand that to the network alongside the video.
        """
        assert config[module]["listen"].startswith(f"{LOOPBACK}:")

    def test_credentials_guard_the_published_streams(self, config: dict) -> None:
        assert config["rtsp"]["username"] == "user"
        assert config["rtsp"]["password"] == "secret"


class TestStreamSources:
    def test_the_source_is_pulled_from_loopback(self) -> None:
        """The bridge's own control plane is never reachable off-box."""
        config = build_config(make_options(AccessMode.LAN, "u", "p"), ["42"])
        assert config["streams"][stream_name("42")].startswith(
            f"ffmpeg:http://{LOOPBACK}:"
        )

    def test_streams_are_copied_not_transcoded(self) -> None:
        """Nothing here may re-encode.

        Every consumer would pay for it, on a host that generally cannot
        afford it. The add-on page's preview is served from the vendor
        library's own decoded stills instead -- see `BridgeApi._preview`.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][stream_name("42")].endswith("#video=copy")

    def test_two_streams_per_camera(self) -> None:
        """The original, and one re-encoded for whatever cannot play it."""
        config = build_config(make_options(AccessMode.LOCAL), ["1", "2", "3"])
        assert len(config["streams"]) == 6

    def test_the_compatibility_stream_has_its_own_name(self) -> None:
        """Two names rather than two codecs under one.

        Offering both under a single name leaves the choice to whatever
        connects, so an NVR that accepts either could record a re-encode of a
        stream it could have copied. Each URL says what it carries.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][h264_stream_name("42")].endswith("#video=h264")
        assert h264_stream_name("42") != stream_name("42")

    def test_h264_is_encoded_with_an_encoder_this_image_has(self) -> None:
        """go2rtc defaults to libx264, which is GPL and not in this build.

        Left to the default, every H.264 request would fail at the point a
        user opens a camera -- with the reason buried in go2rtc's output.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "libopenh264" in config["ffmpeg"]["h264"]
        assert "libx264" not in config["ffmpeg"]["h264"]

    def test_the_compatibility_stream_reuses_the_original(self) -> None:
        # Naming the stream rather than repeating the URL keeps both on one
        # session against the camera.
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][h264_stream_name("42")].startswith(
            f"ffmpeg:{stream_name('42')}#"
        )


class TestPorts:
    def test_listeners_do_not_collide(self) -> None:
        ports = {GO2RTC_API_PORT, RTSP_PORT, WEBRTC_PORT, SRTP_PORT}
        assert len(ports) == 4
