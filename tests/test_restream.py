"""go2rtc configuration.

These tests pin the exposure boundary. `host_network` removes Docker's port
isolation entirely, so which address each listener binds to is the only thing
separating the cameras from the rest of the network — and only the stream
listeners may ever follow `access_mode`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bridge.config import AccessMode, Options, VideoQuality
from bridge.const import GO2RTC_API_PORT, LOOPBACK, RTSP_PORT, SRTP_PORT, WEBRTC_PORT
from bridge.restream import (
    ROOT_CODEC,
    ROOT_KEY,
    STREAM_SPECS,
    Restreamer,
    StreamSpec,
    _audio_codecs,
    build_config,
    stream_name,
)

_STRINGS_JSON = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "xiaomi_camera"
    / "strings.json"
)


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
        assert config["streams"][stream_name("42")].startswith(f"http://{LOOPBACK}:")

    def test_the_root_runs_no_ffmpeg(self) -> None:
        """go2rtc demuxes MPEG-TS itself.

        Putting ffmpeg in front of it costs three seconds of cold start --
        ffmpeg probes MPEG-TS for five by default -- and a process per camera,
        to do a job go2rtc already does. Measured: 3.11s native against 7.24s
        through ffmpeg, where today's elementary stream took 4.20s.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        source = config["streams"][stream_name("42")]
        assert not source.startswith("ffmpeg:")
        assert "#video=" not in source

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
        assert config["streams"][stream_name("42", "h264")].endswith(
            "#video=h264#audio=copy#audio=aac"
        )
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
        """Against `h265/360`, not the bare `h265` template.

        `h265` names the root variant's codec, but the root is always
        `#video=copy` (see `build_config`) -- no `#video=` value ever names
        the bare template, so asserting against it would pass even if the
        template were missing the encoder entirely.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "libx265" in config["ffmpeg"]["h265/360"]
        assert "kvazaar" not in config["ffmpeg"]["h265/360"]

    def test_both_encoders_shorten_the_keyframe_interval(self) -> None:
        """go2rtc defaults to -g 50; HLS cannot start anywhere but a keyframe."""
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert "-g 25" in config["ffmpeg"]["h264"]
        # Not the bare `h265` template -- see
        # `test_h265_is_encoded_with_the_standard_encoder`.
        assert "-g 25" in config["ffmpeg"]["h265/360"]

    def test_the_compatibility_stream_reuses_the_original(self) -> None:
        # Naming the stream rather than repeating the URL keeps both on one
        # session against the camera.
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        assert config["streams"][stream_name("42", "h264")].startswith(
            f"ffmpeg:{stream_name('42')}#"
        )


class TestAudioFollowsVideo:
    """One rule: a variant's audio serves the same consumer its video does.

    Derived from `spec.codec` rather than declared per spec, so there is no
    second table to keep aligned with the first. This project has shipped that
    defect four times.
    """

    @pytest.fixture
    def sources(self) -> dict[str, str]:
        return build_config(make_options(AccessMode.LOCAL), ["42"])["streams"]

    def test_every_derived_stream_offers_the_cameras_own_audio(
        self, sources: dict[str, str]
    ) -> None:
        """Without `#audio=`, go2rtc passes `-an` and the sound is gone with
        no error anywhere."""
        for spec in STREAM_SPECS:
            if spec.key == ROOT_KEY:
                continue
            assert "#audio=copy" in sources[stream_name("42", spec.key)], spec.key

    def test_only_the_compatibility_family_also_offers_aac(
        self, sources: dict[str, str]
    ) -> None:
        """A consumer that needs H.264 because it cannot decode H.265 is
        overwhelmingly the same consumer that cannot decode Opus. Home
        Assistant's own HLS path is one: it accepts aac and mp3 only."""
        for spec in STREAM_SPECS:
            if spec.key == ROOT_KEY:
                continue
            source = sources[stream_name("42", spec.key)]
            assert ("#audio=aac" in source) is (spec.codec != ROOT_CODEC), spec.key

    def test_the_rule_is_derived_not_listed(self) -> None:
        """A spec that never existed when the rule was written still gets the
        right answer -- which is what makes the rule a guard rather than a
        table someone has to remember to update.
        """
        assert _audio_codecs(StreamSpec("h264_90", "h264", 90, "128k")) == (
            "copy",
            "aac",
        )
        assert _audio_codecs(StreamSpec("h265_90", "h265", 90, "128k")) == ("copy",)


class TestStreamCatalogue:
    """Eight streams per camera: four heights across two codecs."""

    def test_every_camera_gets_eight_streams(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        mine = [name for name in config["streams"] if name.startswith("camera_42")]
        assert len(mine) == 8, sorted(mine)

    def test_every_stream_name_states_its_codec(self) -> None:
        """No variant omits its codec, not even the camera's own encoding.

        The rule exists because a user comparing two 360p entities otherwise
        has nothing to tell them apart.
        """
        config = build_config(make_options(AccessMode.LOCAL), ["42"])
        for name in config["streams"]:
            assert name.startswith(("camera_42_h264", "camera_42_h265")), name

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
            "#video=h264/360#audio=copy#audio=aac"
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
        """Against the eight keys literally, not `[s.key for s in STREAM_SPECS]`.

        That comparison is a restatement of the implementation under test: it
        passes for any `STREAM_SPECS`, including an empty one, so it cannot
        catch a spec that was never added.
        """
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        described = restreamer.stream_descriptions("42")
        assert [d["key"] for d in described] == [
            "h265",
            "h265_720",
            "h265_360",
            "h265_180",
            "h264",
            "h264_720",
            "h264_360",
            "h264_180",
        ]

    def test_urls_carry_no_credentials(self) -> None:
        """These reach Home Assistant's config state, diagnostics and logs."""
        restreamer = Restreamer(make_options(AccessMode.LAN, "user", "pass"))
        for described in restreamer.stream_descriptions("42"):
            assert "@" not in described["url"]

    def test_heights_are_reported_for_scaled_streams_only(self) -> None:
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        by_key = {d["key"]: d for d in restreamer.stream_descriptions("42")}
        assert by_key["h265"]["height"] is None
        assert by_key["h264_360"]["height"] == 360


class TestPorts:
    def test_listeners_do_not_collide(self) -> None:
        ports = {GO2RTC_API_PORT, RTSP_PORT, WEBRTC_PORT, SRTP_PORT}
        assert len(ports) == 4


class TestStreamKeysMatchTranslationLabels:
    """A seam between two packages, with nothing else checking they agree.

    The add-on correctly never hardcodes the stream *list* on the integration
    side -- `/api/cameras` reports it (see `TestStreamDescriptions`). But the
    eight stream *labels* the integration shows a user are a hardcoded table
    in `strings.json` (and its translations). Adding a ninth `StreamSpec` here
    without a matching label would show a raw identifier in the UI again --
    the exact defect already found once on this branch.
    """

    def test_every_stream_key_has_a_translation_label(self) -> None:
        strings = json.loads(_STRINGS_JSON.read_text(encoding="utf-8"))
        labelled = set(strings["selector"]["stream_key"]["options"])
        assert {spec.key for spec in STREAM_SPECS} == labelled
