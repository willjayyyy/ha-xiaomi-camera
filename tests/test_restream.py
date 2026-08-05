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
from bridge.restream import STREAM_SPECS, Restreamer, build_config, stream_name


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

    def test_eight_streams_per_camera(self) -> None:
        """Four heights across both codecs."""
        config = build_config(make_options(AccessMode.LOCAL), ["1", "2", "3"])
        assert len(config["streams"]) == 24

    def test_the_compatibility_stream_has_its_own_name(self) -> None:
        """Two names rather than two codecs under one.

        Offering both under a single name leaves the choice to whatever
        connects, so an NVR that accepts either could record a re-encode of a
        stream it could have copied. Each URL says what it carries.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][stream_name("42", "h264")].endswith("#video=h264")
        assert stream_name("42", "h264") != stream_name("42")

    def test_h264_is_encoded_with_the_standard_encoder(self) -> None:
        """libx264 rather than libopenh264.

        The image ships the GPL ffmpeg build, where libx264 is present and is
        the better encoder at a given bitrate.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "libx264" in config["ffmpeg"]["h264"]
        assert "libopenh264" not in config["ffmpeg"]["h264"]

    def test_h265_is_encoded_with_the_standard_encoder(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "libx265" in config["ffmpeg"]["h265"]
        assert "kvazaar" not in config["ffmpeg"]["h265"]

    def test_both_encoders_shorten_the_keyframe_interval(self) -> None:
        """go2rtc defaults to -g 50; HLS cannot start anywhere but a keyframe."""
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "-g 25" in config["ffmpeg"]["h264"]
        assert "-g 25" in config["ffmpeg"]["h265"]

    def test_the_compatibility_stream_reuses_the_original(self) -> None:
        # Naming the stream rather than repeating the URL keeps both on one
        # session against the camera.
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][stream_name("42", "h264")].startswith(
            f"ffmpeg:{stream_name('42')}#"
        )


class TestStreamCatalogue:
    """Eight streams per camera: four heights across two codecs."""

    def test_every_camera_gets_eight_streams(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        mine = [name for name in config["streams"] if name.startswith("camera_42")]
        assert len(mine) == 8, sorted(mine)

    def test_derived_streams_source_the_root_not_the_http_endpoint(self) -> None:
        """One peer-to-peer session per camera, however many streams are open.

        Pointing a derived stream at the add-on's HTTP endpoint would open a
        second session on the camera.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        root = stream_name("42")
        for name, source in config["streams"].items():
            if name.startswith("camera_42") and name != root:
                assert source.startswith(f"ffmpeg:{root}"), (name, source)

    def test_the_root_is_the_only_stream_reading_http(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        readers = [n for n, s in config["streams"].items() if "http://" in s]
        assert readers == [stream_name("42")]

    def test_scaled_streams_name_a_variant_template(self) -> None:
        """Scale and bitrate live in the ffmpeg template, not in the source.

        go2rtc appends the `#video=` template's arguments *after* any `#raw=`
        ones, so a bitrate passed through `#raw=` is overridden by the
        template's own. Naming a variant template is the only way to vary it.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][stream_name("42", "h264_360")].endswith(
            "#video=h264/360"
        )

    def test_each_variant_template_sets_its_own_scale_and_bitrate(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        template = config["ffmpeg"]["h264/360"]
        assert "scale=-2:360" in template
        assert "-b:v 512k" in template

    def test_scaling_keeps_the_width_even(self) -> None:
        """`-2` derives an even width; `yuv420p` requires both dimensions even.

        go2rtc's own `#height=` parameter emits `-1`, which can produce an odd
        width, so it is deliberately not used.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        for name, template in config["ffmpeg"].items():
            if "/" in name:
                assert "scale=-2:" in template, name

    def test_the_source_resolution_streams_do_not_scale(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "scale" not in config["ffmpeg"]["h264"]
        assert "scale" not in config["streams"][stream_name("42")]

    def test_the_source_resolution_h264_stream_is_bitrate_capped(self) -> None:
        """The full-resolution H.264 variant is a real transcode, not a copy.

        Unlike the H.265 root, it is not `#video=copy`, so leaving it without
        `-b:v` would let libx264 fall back to CRF-based rate control instead
        of the documented 2M ceiling.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "-b:v 2M" in config["ffmpeg"]["h264"]
        assert "scale" not in config["ffmpeg"]["h264"]


class TestStreamDescriptions:
    """What the integration reads instead of hardcoding the list."""

    def test_it_describes_every_published_stream(self) -> None:
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        described = restreamer.stream_descriptions("42")
        assert [d["key"] for d in described] == [s.key for s in STREAM_SPECS]

    def test_urls_carry_no_credentials(self) -> None:
        """These reach Home Assistant's config state, diagnostics and logs."""
        restreamer = Restreamer(make_options(AccessMode.LAN, "user", "pass"))
        for described in restreamer.stream_descriptions("42"):
            assert "@" not in described["url"]

    def test_heights_are_reported_for_scaled_streams_only(self) -> None:
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        by_key = {d["key"]: d for d in restreamer.stream_descriptions("42")}
        assert by_key["hevc"]["height"] is None
        assert by_key["h264_360"]["height"] == 360


class TestPorts:
    def test_listeners_do_not_collide(self) -> None:
        ports = {GO2RTC_API_PORT, RTSP_PORT, WEBRTC_PORT, SRTP_PORT}
        assert len(ports) == 4
