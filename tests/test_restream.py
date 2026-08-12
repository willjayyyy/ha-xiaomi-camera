"""go2rtc configuration.

These tests pin the exposure boundary. `host_network` removes Docker's port
isolation entirely, so which address each listener binds to is the only thing
separating the cameras from the rest of the network — and only the stream
listeners may ever follow `access_mode`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import yaml
from bridge.config import AccessMode, Options, TranscodeQuality, VideoQuality
from bridge.const import (
    ALL_INTERFACES,
    API_PORT,
    GO2RTC_API_PORT,
    LOOPBACK,
    RTSP_PORT,
    SRTP_PORT,
    WEBRTC_PORT,
)
from bridge.paths import VideoPath
from bridge.restream import (
    ROOT_KEY,
    STREAM_SPECS,
    Restreamer,
    StreamSpec,
    _audio_codecs,
    _encoder_templates,
    build_config,
    source_for,
    stream_name,
)
from bridge.settings import Resolved

_COMPONENT = (
    Path(__file__).resolve().parent.parent / "custom_components" / "xiaomi_camera"
)
#: Every file carrying stream labels. `strings.json` is what Home Assistant
#: validates, the two under `translations/` are what it actually serves --
#: and a user-visible string has to exist in both languages to count as done,
#: so all three are checked rather than only the English one.
_LABEL_FILES = (
    _COMPONENT / "strings.json",
    _COMPONENT / "translations" / "en.json",
    _COMPONENT / "translations" / "zh-Hans.json",
)


def make_options(
    mode: AccessMode,
    user: str = "",
    password: str = "",
    transcode_quality: TranscodeQuality = TranscodeQuality.STANDARD,
) -> Options:
    return Options(
        access_mode=mode,
        rtsp_username=user,
        rtsp_password=password,
        video_quality=VideoQuality.LOW,
        enable_audio=False,
        log_level="info",
        transcode_quality=transcode_quality,
    )


def make_cameras(
    dids: list[str],
    transcode_quality: TranscodeQuality = TranscodeQuality.STANDARD,
) -> dict[str, Resolved]:
    """A mapping of resolved settings, as `build_config` and `async_apply`
    now take, standing in for a real `SettingsStore` in tests that only care
    about which cameras are published or at what quality they transcode."""
    return {
        did: Resolved(VideoQuality.LOW, False, transcode_quality, VideoPath.OFFICIAL)
        for did in dids
    }


class TestLocalMode:
    @pytest.fixture
    def config(self) -> dict:
        return build_config(make_options(AccessMode.LOCAL), make_cameras(["1", "2"]))

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
        return build_config(
            make_options(AccessMode.LAN, "user", "secret"), make_cameras(["1"])
        )

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
        config = build_config(
            make_options(AccessMode.LAN, "u", "p"), make_cameras(["42"])
        )
        assert config["streams"][stream_name("42")].startswith(f"http://{LOOPBACK}:")

    def test_the_root_runs_no_ffmpeg(self) -> None:
        """go2rtc demuxes MPEG-TS itself.

        Putting ffmpeg in front of it costs three seconds of cold start --
        ffmpeg probes MPEG-TS for five by default -- and a process per camera,
        to do a job go2rtc already does. Measured: 3.11s native against 7.24s
        through ffmpeg, where today's elementary stream took 4.20s.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        source = config["streams"][stream_name("42")]
        assert not source.startswith("ffmpeg:")
        assert "#video=" not in source

    def test_thirteen_streams_per_camera(self) -> None:
        """Six heights across both codecs, plus the root."""
        config = build_config(
            make_options(AccessMode.LOCAL), make_cameras(["1", "2", "3"])
        )
        assert len(config["streams"]) == 39

    def test_the_heights_step_down_from_a_modern_sensor(self) -> None:
        """1440 and 1080 sit between 720 and what these cameras now send.

        The ladder was drawn when the source was 848x480, where 720 was
        already most of the picture. A camera at `video_quality: high` sends
        3840x2160, and dropping straight from that to 720 skipped the two
        sizes consumers ask for most. Variants cost nothing until something
        connects to one, so an unused rung is free.
        """
        offered = {spec.height for spec in STREAM_SPECS if spec.codec == "h264"}
        assert {1440, 1080} <= offered

    def test_the_compatibility_stream_has_its_own_name(self) -> None:
        """Two names rather than two codecs under one.

        Offering both under a single name leaves the choice to whatever
        connects, so an NVR that accepts either could record a re-encode of a
        stream it could have copied. Each URL says what it carries.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert config["streams"][stream_name("42", "h264")].endswith(
            "#video=h264/standard#audio=copy#audio=aac"
        )
        assert stream_name("42", "h264") != stream_name("42")

    def test_h264_is_encoded_with_the_standard_encoder(self) -> None:
        """libx264 rather than libopenh264.

        The image ships the GPL ffmpeg build, where libx264 is present and is
        the better encoder at a given bitrate.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert "libx264" in config["ffmpeg"]["h264/standard"]
        assert "libopenh264" not in config["ffmpeg"]["h264/standard"]

    def test_h265_is_encoded_with_the_standard_encoder(self) -> None:
        """libx265 rather than kvazaar, in the full-size and scaled forms."""
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert "libx265" in config["ffmpeg"]["h265/standard"]
        assert "kvazaar" not in config["ffmpeg"]["h265/standard"]
        assert "libx265" in config["ffmpeg"]["h265/360/standard"]
        assert "kvazaar" not in config["ffmpeg"]["h265/360/standard"]

    def test_both_encoders_shorten_the_keyframe_interval(self) -> None:
        """go2rtc defaults to -g 50; HLS cannot start anywhere but a keyframe."""
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert "-g 25" in config["ffmpeg"]["h264/standard"]
        assert "-g 25" in config["ffmpeg"]["h265/standard"]
        assert "-g 25" in config["ffmpeg"]["h265/360/standard"]

    def test_quality_is_targeted_and_bandwidth_only_capped(self) -> None:
        """Constant quality with a safety valve, not a bitrate to fill.

        A bitrate target spends its whole allowance on a still room at night,
        and runs out on the one second someone walks through it -- which is
        exactly backwards, because a viewer perceives quality, not bytes.
        `-crf` asks for a quality and spends what that costs; `-maxrate`
        exists only so an unusually busy picture cannot run away with the
        network.

        It also settles what a bitrate could not: the full-size variant's
        resolution is whatever the camera sends, and is unknown when this
        configuration is written. A quality target needs no such knowledge.
        """
        template = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))[
            "ffmpeg"
        ]["h264/720/standard"]
        assert "-crf 23" in template
        assert "-b:v" not in template, "a target would defeat the point of -crf"
        assert "-maxrate 6M" in template
        assert "-bufsize 12M" in template

    def test_the_full_size_variant_is_sized_too(self) -> None:
        """The rung a bitrate could never size, because its height is unknown."""
        template = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))[
            "ffmpeg"
        ]["h264/standard"]
        assert "-crf 23" in template
        assert "-maxrate 24M" in template

    def test_a_scaled_variant_never_enlarges_its_source(self) -> None:
        """The height is a ceiling, so any camera gets the best it can give.

        Without this the 1440 rung would stretch a camera that sends less --
        more bandwidth and more CPU for a softer picture than the source it
        came from. The preview reads the same way, and the two paths must not
        answer this differently.
        """
        template = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))[
            "ffmpeg"
        ]["h264/1440/standard"]
        assert "scale=-2:'min(1440,ih)'" in template

    def test_the_compatibility_stream_reuses_the_original(self) -> None:
        # Naming the stream rather than repeating the URL keeps both on one
        # session against the camera.
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert config["streams"][stream_name("42", "h264")].startswith(
            f"ffmpeg:{stream_name('42')}#"
        )


class TestTranscodeQualityMovesOneKnob:
    """One setting, moving every figure that has to agree with the others.

    Twelve independently editable numbers would be twelve places for the
    relationship between the heights to drift, and drift between values that
    each looked right alone is a defect this project has shipped four times.
    The ratios stay in the table where they can be read side by side; the
    setting moves all of them together.
    """

    def test_the_two_encoders_do_not_share_a_scale(self) -> None:
        """x265's numbers are not x264's: the same figure is a softer picture.

        Giving both the same constant would have made every H.265 variant
        quietly worse than its H.264 twin while the configuration read as if
        they matched.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert "-crf 23" in config["ffmpeg"]["h264/720/standard"]
        assert "-crf 28" in config["ffmpeg"]["h265/720/standard"]

    def test_asking_for_sharper_lowers_the_quality_number(self) -> None:
        config = build_config(
            make_options(AccessMode.LOCAL),
            make_cameras(["42"], transcode_quality=TranscodeQuality.SHARP),
        )
        assert "-crf 20" in config["ffmpeg"]["h264/720/sharp"]
        assert "-crf 25" in config["ffmpeg"]["h265/720/sharp"]

    def test_the_valve_opens_with_the_quality(self) -> None:
        """A cap left where it was would bind before the new quality arrived.

        Then the setting would buy nothing on exactly the busy pictures that
        motivated raising it.
        """
        standard = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))[
            "ffmpeg"
        ]
        maximum = build_config(
            make_options(AccessMode.LOCAL),
            make_cameras(["42"], transcode_quality=TranscodeQuality.MAXIMUM),
        )["ffmpeg"]
        assert "-maxrate 6M" in standard["h264/720/standard"]
        assert "-maxrate 24M" in maximum["h264/720/maximum"]
        assert "-bufsize 48M" in maximum["h264/720/maximum"]


class TestAudioFollowsVideo:
    """One rule: a variant's audio serves the same consumer its video does.

    Derived from `spec.codec` rather than declared per spec, so there is no
    second table to keep aligned with the first. This project has shipped that
    defect four times.
    """

    @pytest.fixture
    def sources(self) -> dict[str, str]:
        return build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))[
            "streams"
        ]

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
            assert ("#audio=aac" in source) is (spec.codec == "h264"), spec.key

    def test_the_rule_is_derived_not_listed(self) -> None:
        """A spec that never existed when the rule was written still gets the
        right answer -- which is what makes the rule a guard rather than a
        table someone has to remember to update.
        """
        assert _audio_codecs(StreamSpec("original", "original", None, "2M")) == (
            "copy",
        )
        assert _audio_codecs(StreamSpec("h264_90", "h264", 90, "128k")) == (
            "copy",
            "aac",
        )
        assert _audio_codecs(StreamSpec("h265_90", "h265", 90, "128k")) == ("copy",)


class TestStreamCatalogue:
    """Nine streams per camera: the root plus four heights across two codecs."""

    def test_every_camera_gets_thirteen_streams(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        mine = [name for name in config["streams"] if name.startswith("camera_42")]
        assert len(mine) == 13, sorted(mine)

    def test_every_derived_stream_name_states_its_codec(self) -> None:
        """Derived variants never omit their codec; the root has none to state.

        A user comparing two 360p entities otherwise has nothing to tell them
        apart. The root is the camera's own encoding, so its name carries no
        codec at all -- it cannot lie about which one that is.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        root = stream_name("42")
        assert root == "camera_42"
        for name in config["streams"]:
            if name == root:
                continue
            assert name.startswith(("camera_42_h264", "camera_42_h265")), name

    def test_derived_streams_source_the_root_not_the_http_endpoint(self) -> None:
        """One peer-to-peer session per camera, however many streams are open.

        Pointing a derived stream at the add-on's HTTP endpoint would open a
        second session on the camera.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        root = stream_name("42")
        for name, source in config["streams"].items():
            if name.startswith("camera_42") and name != root:
                assert source.startswith(f"ffmpeg:{root}"), (name, source)

    def test_the_root_is_the_only_stream_reading_http(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        readers = [n for n, s in config["streams"].items() if "http://" in s]
        assert readers == [stream_name("42")]

    def test_scaled_streams_name_a_variant_template(self) -> None:
        """Scale and bitrate live in the ffmpeg template, not in the source.

        go2rtc appends the `#video=` template's arguments *after* any `#raw=`
        ones, so a bitrate passed through `#raw=` is overridden by the
        template's own. Naming a variant template is the only way to vary it.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert config["streams"][stream_name("42", "h264_360")].endswith(
            "#video=h264/360/standard#audio=copy#audio=aac"
        )

    def test_each_variant_template_sets_its_own_scale_and_ceiling(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        template = config["ffmpeg"]["h264/360/standard"]
        assert "scale=-2:'min(360,ih)'" in template
        assert "-maxrate 2M" in template

    def test_scaling_keeps_the_width_even(self) -> None:
        """`-2` derives an even width; `yuv420p` requires both dimensions even.

        go2rtc's own `#height=` parameter emits `-1`, which can produce an odd
        width, so it is deliberately not used.

        A scaled template's name carries a height as its middle segment --
        `h264/360/standard` -- while a full-size one does not, so the segment
        count is what tells the two apart now that quality names every entry.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        for name, template in config["ffmpeg"].items():
            if name.count("/") == 2:
                assert "scale=-2:" in template, name

    def test_the_source_resolution_streams_do_not_scale(self) -> None:
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        assert "scale" not in config["ffmpeg"]["h264/standard"]
        assert "scale" not in config["ffmpeg"]["h265/standard"]
        assert "scale" not in config["streams"][stream_name("42")]

    def test_the_source_resolution_codec_streams_are_capped_too(self) -> None:
        """The full-resolution transcodes are real transcodes, not copies.

        Unlike the original root they are not `#video=copy`, so they need
        both a quality to aim for and something to stop them. The ceiling is
        the loosest of any rung because what they are re-encoding is whatever
        the camera sends -- possibly 4K -- and a valve that binds in normal
        use would be a bitrate target wearing another name.
        """
        config = build_config(make_options(AccessMode.LOCAL), make_cameras(["42"]))
        for codec in ("h264", "h265"):
            template = config["ffmpeg"][f"{codec}/standard"]
            assert "-crf" in template
            assert "-maxrate 24M" in template
            assert "scale" not in template


class TestStreamDescriptions:
    """What the integration reads instead of hardcoding the list."""

    def test_it_describes_every_published_stream(self) -> None:
        """Against the thirteen keys literally, not `[s.key for s in STREAM_SPECS]`.

        That comparison is a restatement of the implementation under test: it
        passes for any `STREAM_SPECS`, including an empty one, so it cannot
        catch a spec that was never added.
        """
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        described = restreamer.stream_descriptions("42")
        assert [d["key"] for d in described] == [
            "original",
            "h265",
            "h265_1440",
            "h265_1080",
            "h265_720",
            "h265_360",
            "h265_180",
            "h264",
            "h264_1440",
            "h264_1080",
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
        assert by_key["original"]["height"] is None
        assert by_key["h265"]["height"] is None
        assert by_key["h264_360"]["height"] == 360


class TestRtspReachableOffHost:
    """Whether the published RTSP listener can be reached from another
    machine -- what the page needs before it may rewrite the loopback
    hostname it was sent into something else.

    This is a different fact from `requires_credentials`, even though the two
    happen to agree for every real `Options` today (`lan` mode is the only
    mode that both requires a password and binds beyond loopback). Pinned to
    a fake whose two flags disagree, so this fails if the property is ever
    re-pointed at `requires_credentials` instead of `bind_address`.
    """

    def test_real_options_agree_in_local_mode(self) -> None:
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        assert restreamer.rtsp_reachable_off_host is False

    def test_real_options_agree_in_lan_mode(self) -> None:
        restreamer = Restreamer(make_options(AccessMode.LAN, "user", "secret"))
        assert restreamer.rtsp_reachable_off_host is True

    def test_it_reads_bind_address_even_when_credentials_disagree(self) -> None:
        fake_options = SimpleNamespace(
            bind_address=ALL_INTERFACES, requires_credentials=False
        )
        assert Restreamer(fake_options).rtsp_reachable_off_host is True

    def test_it_is_false_on_loopback_even_when_credentials_are_required(self) -> None:
        fake_options = SimpleNamespace(bind_address=LOOPBACK, requires_credentials=True)
        assert Restreamer(fake_options).rtsp_reachable_off_host is False


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

    @pytest.mark.parametrize("path", _LABEL_FILES)
    def test_every_stream_key_has_a_dropdown_label(self, path: Path) -> None:
        """`selector.stream_key.options` names every stream, root included.

        This is the table the stream dropdown reads at runtime, so a spec
        missing from it shows a raw identifier in the form.
        """
        table = json.loads(path.read_text(encoding="utf-8"))["selector"]
        labelled = set(table["stream_key"]["options"])
        assert {spec.key for spec in STREAM_SPECS} == labelled

    @pytest.mark.parametrize("path", _LABEL_FILES)
    def test_entity_labels_cover_every_stream_but_the_root(self, path: Path) -> None:
        """`entity.camera` names every stream *except* the root one.

        The root stream's entity takes the device's own name -- it declares
        `_attr_name = None` (see `streams.takes_device_name`) -- so a label
        for it would never be read. An entry here would be dead text that
        looks live, which is how the empty `"original": {"name": ""}` string
        survived: it read as a deliberate label and was really a lookup miss.
        """
        table = json.loads(path.read_text(encoding="utf-8"))["entity"]["camera"]
        expected = {spec.key for spec in STREAM_SPECS} - {ROOT_KEY}
        assert set(table) == expected

    @pytest.mark.parametrize("path", _LABEL_FILES)
    def test_the_two_tables_say_the_same_words(self, path: Path) -> None:
        """A stream is called the same thing in the form and on the device page.

        Two tables exist because Home Assistant puts selector options and
        entity names in different places, and neither can reference the other
        -- so the agreement cannot be structural and has to be a test. Without
        it they drift: the dropdown once read "H.265 · Full size" for the
        stream whose entity was named "H.265".
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        options = data["selector"]["stream_key"]["options"]
        entities = data["entity"]["camera"]
        assert {key: options[key] for key in entities} == {
            key: entry["name"] for key, entry in entities.items()
        }


class TestTerminationIsBounded:
    """Stopping go2rtc has to finish, whatever the process does.

    The same shape as the preview's teardown, and reached on two paths that
    matter: restarting go2rtc when the camera list changes, and shutting the
    add-on down. A wait that never ends here stops the add-on from exiting on
    its own, leaving Supervisor to kill it after its grace period -- and on
    the restart path it would strand the refresh loop as well.
    """

    async def test_it_gives_up_on_a_process_that_never_reports_its_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from bridge import restream
        from conftest import NeverReportsExit

        monkeypatch.setattr(restream, "_STOP_TIMEOUT", 0.05)
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        restreamer._process = NeverReportsExit()

        await asyncio.wait_for(restreamer._async_terminate(), timeout=2)


class TestGo2rtcSpawnArguments:
    """go2rtc's `-config` order is load-bearing, not cosmetic.

    `PUT`/`DELETE` on its streams API persist into whichever `-config` path
    was given *first* -- measured, not assumed (see the module docstring on
    `_STATE_PATH`). The state file is passed first and the generated
    configuration second precisely so those writes land in the throwaway
    file rather than the one this module regenerates. Nothing else pins that
    order, so a future edit swapping the two arguments would silently
    reintroduce the corruption this branch fixed, with every test in this
    file still green.
    """

    async def test_the_state_file_is_passed_before_the_generated_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from bridge import restream
        from conftest import NeverReportsExit

        state_path = tmp_path / "go2rtc-state.yaml"
        config_path = tmp_path / "go2rtc.yaml"
        monkeypatch.setattr(restream, "_STATE_PATH", state_path)
        monkeypatch.setattr(restream, "_CONFIG_PATH", config_path)
        monkeypatch.setattr(restream, "_STOP_TIMEOUT", 0.05)

        captured: list[str] = []

        async def fake_exec(*args: str, **kwargs: object) -> NeverReportsExit:
            captured.extend(args)
            return NeverReportsExit()

        monkeypatch.setattr(restream.asyncio, "create_subprocess_exec", fake_exec)

        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        await restreamer.async_start()
        try:
            # `NeverReportsExit.stdout` never closes, so `_relay_output` stays
            # parked reading it and the spawn above never happens a second
            # time -- give the supervisor task a moment to reach it.
            await asyncio.sleep(0.05)
        finally:
            await restreamer.async_stop()

        positions = [i for i, arg in enumerate(captured) if arg == "-config"]
        assert len(positions) == 2, f"expected two -config flags, got {captured}"
        first_value, second_value = (captured[i + 1] for i in positions)
        assert first_value == str(state_path), "the state file must come first"
        assert second_value == str(config_path), "the generated config must come second"


def test_every_variant_has_a_template_at_every_quality():
    """A stream may name any quality at any time, so all of them exist up front."""
    templates = _encoder_templates()
    for spec in STREAM_SPECS:
        if spec.key == ROOT_KEY:
            continue
        for quality in TranscodeQuality:
            assert spec.template_for(quality) in templates


def test_template_names_carry_the_quality():
    spec = StreamSpec("h264_360", "h264", 360, "2M")
    assert spec.template_for(TranscodeQuality.SHARP) == "h264/360/sharp"
    full = StreamSpec("h264", "h264", None, "24M")
    assert full.template_for(TranscodeQuality.MAXIMUM) == "h264/maximum"


def test_quality_changes_the_crf_and_the_ceiling_together():
    """A finer picture with the old cap would bind before the detail arrived."""
    templates = _encoder_templates()
    standard = templates["h264/360/standard"]
    maximum = templates["h264/360/maximum"]
    assert "-crf 23" in standard
    assert "-crf 17" in maximum
    assert "-maxrate 2M" in standard
    assert "-maxrate 8M" in maximum


def test_h265_asks_for_a_higher_crf_than_h264_for_the_same_picture():
    templates = _encoder_templates()
    assert "-crf 23" in templates["h264/720/standard"]
    assert "-crf 28" in templates["h265/720/standard"]


def test_scaling_never_enlarges_a_smaller_source():
    templates = _encoder_templates()
    assert "scale=-2:'min(360,ih)'" in templates["h264/360/standard"]


def test_the_root_never_gets_a_template():
    templates = _encoder_templates()
    assert not any(key.startswith(ROOT_KEY) for key in templates)


def test_source_for_points_at_a_route_the_control_plane_actually_serves():
    """The URL `source_for` hands to go2rtc has to be a route this add-on
    actually answers.

    Those are two facts owned by two files with nothing else making them
    agree: renaming the route in `api.py` without a matching change here
    would leave go2rtc dialing a 404 -- and that failure surfaces only as
    "the camera never connects", with nothing in the log pointing back to a
    renamed route.

    `build_control_app` registers its routes from bound methods on `self`
    without calling any of them or touching account/session state, so a bare
    instance built without `__init__` is enough to read the route table back
    -- this does not stand up a server.
    """
    from bridge.api import BridgeApi

    app = BridgeApi.__new__(BridgeApi).build_control_app()
    routes = {resource.canonical for resource in app.router.resources()}
    assert "/api/stream/{did}" in routes

    settings = Resolved(
        VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
    )
    parsed = urlsplit(source_for("42", settings))
    assert parsed.port == API_PORT
    assert parsed.path == "/api/stream/{did}".replace("{did}", "42")


def test_source_for_is_the_only_producer_of_a_root_source_url():
    """A second producer is a second source of truth, and it will drift.

    This is the guard the whole design rests on: which path a camera's video
    comes from is answered once. During the multi-stream work the same kind of
    default was answered independently in four places, each correct alone, and
    the disagreement destroyed and recreated entities with nothing logged.
    """
    root = (
        Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app" / "bridge"
    )
    # Mentioning the path is legitimate and common: `api.py` registers the
    # route that serves it, and a log line elsewhere prints a host and port.
    # What no other module may do is *build* such a URL, so the pattern looked
    # for is an interpolated string -- the act of composing one -- rather than
    # the mere presence of the words, which would flag the route declaration
    # and teach the next person to work around the check instead of the rule.
    builder = re.compile(r"""f["'][^"']*(?:/api/stream/|xiaomi://)""")
    offenders = sorted(
        path.name
        for path in root.glob("*.py")
        if path.name != "restream.py"
        and builder.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == [], f"these build a root source URL themselves: {offenders}"


def test_each_camera_gets_the_template_matching_its_own_transcode_quality():
    cameras = {
        "aaa": Resolved(
            VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
        ),
        "bbb": Resolved(
            VideoQuality.LOW, False, TranscodeQuality.MAXIMUM, VideoPath.OFFICIAL
        ),
    }
    config = build_config(make_options(AccessMode.LOCAL), cameras)
    assert "#video=h264/360/standard" in config["streams"]["camera_aaa_h264_360"]
    assert "#video=h264/360/maximum" in config["streams"]["camera_bbb_h264_360"]


def test_stream_names_do_not_depend_on_any_setting():
    """Entity identity rides on these names.

    A name that moved when a setting changed would give the integration a new
    unique_id, destroying and recreating the entity: HomeKit unpairs, history
    is orphaned, automations break, and nothing is logged.
    """
    plain = {
        "aaa": Resolved(
            VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
        )
    }
    fancy = {
        "aaa": Resolved(
            VideoQuality.HIGH, True, TranscodeQuality.MAXIMUM, VideoPath.OFFICIAL
        )
    }
    options = make_options(AccessMode.LOCAL)
    assert set(build_config(options, plain)["streams"]) == set(
        build_config(options, fancy)["streams"]
    )


class TestApplyingWithoutRestarting:
    """`async_apply` reuses the running go2rtc instead of restarting it,
    whenever the change can be delivered through its API -- see the module
    docstring on `Restreamer._async_deliver` for why the two kinds of change
    it distinguishes are delivered differently.
    """

    @pytest.mark.asyncio
    async def test_changing_one_cameras_quality_does_not_restart_go2rtc(
        self, tmp_path, monkeypatch
    ):
        """A restart drops every viewer on every camera. One camera's setting
        must not cost the other seven their picture."""
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        restarts = []
        monkeypatch.setattr(restreamer, "async_restart", lambda: restarts.append(1))
        monkeypatch.setattr(restreamer, "_process", object())
        delivered: list[tuple[str, str]] = []

        class _Api:
            async def set_stream(self, name, src):
                delivered.append((name, src))
                return True

            async def remove_stream(self, name):
                return True

        restreamer._api = _Api()

        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.STANDARD,
                    VideoPath.OFFICIAL,
                )
            }
        )
        restarts.clear()
        delivered.clear()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW, False, TranscodeQuality.SHARP, VideoPath.OFFICIAL
                )
            }
        )

        assert restarts == []
        assert any(name == "camera_aaa_h264_360" for name, _ in delivered)

    @pytest.mark.asyncio
    async def test_a_failed_delivery_falls_back_to_a_restart(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        restarts = []

        async def _fake_restart() -> None:
            restarts.append(1)

        monkeypatch.setattr(restreamer, "async_restart", _fake_restart)
        monkeypatch.setattr(restreamer, "_process", object())

        class _Api:
            async def set_stream(self, name, src):
                return False

            async def remove_stream(self, name):
                return False

        restreamer._api = _Api()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.STANDARD,
                    VideoPath.OFFICIAL,
                )
            }
        )
        restarts.clear()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW, False, TranscodeQuality.SHARP, VideoPath.OFFICIAL
                )
            }
        )
        assert restarts == [1]

    @pytest.mark.asyncio
    async def test_the_file_is_rewritten_even_when_the_change_was_delivered_live(
        self, monkeypatch, tmp_path
    ):
        """The file is the truth. A live delivery that left it stale would be
        undone by the next restart, silently and hours later."""
        config_path = tmp_path / "go2rtc.yaml"
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", config_path)
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        monkeypatch.setattr(restreamer, "async_restart", lambda: None)
        monkeypatch.setattr(restreamer, "_process", object())

        class _Api:
            async def set_stream(self, name, src):
                return True

            async def remove_stream(self, name):
                return True

        restreamer._api = _Api()

        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.STANDARD,
                    VideoPath.OFFICIAL,
                )
            }
        )
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.MAXIMUM,
                    VideoPath.OFFICIAL,
                )
            }
        )

        # Read back what a restart would rebuild from, not what the mock API
        # happened to receive. `_encoder_templates()` deliberately emits every
        # quality's template regardless of what any camera currently asks
        # for -- see its own docstring -- so both "standard" and "maximum"
        # templates are present in `ffmpeg` either way; that is what makes
        # switching a stream to a quality live, without a restart, possible
        # at all. What the file must get right is which template *this
        # stream* names, so the check reads the `streams` entry rather than
        # scanning the whole document for a substring that the `ffmpeg` table
        # would always contain.
        written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        video_360 = written["streams"][stream_name("aaa", "h264_360")]
        assert "#video=h264/360/maximum" in video_360
        assert "#video=h264/360/standard" not in video_360

    @pytest.mark.asyncio
    async def test_a_background_refresh_does_not_drop_viewers(
        self, monkeypatch, tmp_path
    ):
        """A camera changing address in the background must not cost the
        other seven their picture -- or this one its viewer, whose source is
        already dead and will be repaired on the next dial."""
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        monkeypatch.setattr(restreamer, "async_restart", lambda: None)
        monkeypatch.setattr(restreamer, "_process", object())
        used: list[str] = []

        class _Api:
            async def set_stream(self, name, src):
                used.append("patch")
                return True

            async def replace_stream(self, name, src):
                used.append("replace")
                return True

            async def remove_stream(self, name):
                return True

        restreamer._api = _Api()
        settings = {
            "aaa": Resolved(
                VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
            )
        }
        await restreamer.async_apply(settings)
        used.clear()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW, False, TranscodeQuality.SHARP, VideoPath.OFFICIAL
                )
            }
        )
        assert set(used) == {"patch"}
        used.clear()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.MAXIMUM,
                    VideoPath.OFFICIAL,
                )
            },
            explicit=True,
        )
        assert set(used) == {"replace"}

    @pytest.mark.asyncio
    async def test_an_explicit_change_reaches_only_the_camera_it_was_made_on(
        self, monkeypatch, tmp_path
    ):
        """Two cameras, one changed setting. An explicit change drops the
        consumers of the streams it replaces, so delivering it to a camera
        nobody touched would take down that camera's Home Assistant entity,
        its HomeKit session and its preview for nothing. One camera is the
        blast radius the design pays for; the rest is not."""
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        monkeypatch.setattr(restreamer, "async_restart", lambda: None)
        monkeypatch.setattr(restreamer, "_process", object())
        touched: list[str] = []

        class _Api:
            async def set_stream(self, name, src):
                touched.append(name)
                return True

            async def replace_stream(self, name, src):
                touched.append(name)
                return True

            async def remove_stream(self, name):
                touched.append(name)
                return True

        restreamer._api = _Api()
        standard = Resolved(
            VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
        )
        await restreamer.async_apply({"aaa": standard, "bbb": standard})
        touched.clear()

        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW, False, TranscodeQuality.SHARP, VideoPath.OFFICIAL
                ),
                "bbb": standard,
            },
            explicit=True,
        )

        assert touched, "the changed camera's streams were never delivered"
        assert not [name for name in touched if name.startswith("camera_bbb")]

    @pytest.mark.asyncio
    async def test_a_setting_go2rtc_cannot_see_touches_no_stream_at_all(
        self, monkeypatch, tmp_path
    ):
        """Picture size and sound are settled with the camera when the
        peer-to-peer session opens and appear nowhere in go2rtc's
        configuration. Delivering them anyway would drop every viewer on
        every camera to hand go2rtc a stream table it already had."""
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        monkeypatch.setattr(restreamer, "async_restart", lambda: None)
        monkeypatch.setattr(restreamer, "_process", object())
        touched: list[str] = []

        class _Api:
            async def set_stream(self, name, src):
                touched.append(name)
                return True

            async def replace_stream(self, name, src):
                touched.append(name)
                return True

            async def remove_stream(self, name):
                touched.append(name)
                return True

        restreamer._api = _Api()
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.LOW,
                    False,
                    TranscodeQuality.STANDARD,
                    VideoPath.OFFICIAL,
                )
            }
        )
        touched.clear()

        # A bigger picture from the camera, and sound switched on: both are
        # real changes, and neither changes a single source string.
        await restreamer.async_apply(
            {
                "aaa": Resolved(
                    VideoQuality.HIGH,
                    True,
                    TranscodeQuality.STANDARD,
                    VideoPath.OFFICIAL,
                )
            },
            explicit=True,
        )

        assert touched == []
        # Distinguishes "nothing needed delivering" from "nothing happened
        # at all": `async_apply` must still have processed the change and
        # stored the new mapping, not short-circuited before comparing it.
        assert restreamer._cameras == {
            "aaa": Resolved(
                VideoQuality.HIGH, True, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
            )
        }

    @pytest.mark.asyncio
    async def test_a_camera_leaving_the_account_has_its_streams_removed(
        self, monkeypatch, tmp_path
    ):
        """A camera that disappears from the account -- sold, deleted,
        deauthorized -- must have its streams removed from the running
        go2rtc, and removing them must not touch a camera that is still
        there. `_async_deliver`'s removal branch was rewritten and had no
        test exercising it at all."""
        monkeypatch.setattr("bridge.restream._CONFIG_PATH", tmp_path / "go2rtc.yaml")
        restreamer = Restreamer(make_options(AccessMode.LOCAL))
        monkeypatch.setattr(restreamer, "async_restart", lambda: None)
        monkeypatch.setattr(restreamer, "_process", object())
        removed: list[str] = []
        touched: list[str] = []

        class _Api:
            async def set_stream(self, name, src):
                touched.append(name)
                return True

            async def replace_stream(self, name, src):
                touched.append(name)
                return True

            async def remove_stream(self, name):
                removed.append(name)
                return True

        restreamer._api = _Api()
        standard = Resolved(
            VideoQuality.LOW, False, TranscodeQuality.STANDARD, VideoPath.OFFICIAL
        )
        await restreamer.async_apply({"aaa": standard, "bbb": standard})
        removed.clear()
        touched.clear()

        await restreamer.async_apply({"bbb": standard})

        assert removed, "the departed camera's streams were never removed"
        assert all(name.startswith("camera_aaa") for name in removed)
        assert not touched, "the remaining camera's streams were touched for nothing"
