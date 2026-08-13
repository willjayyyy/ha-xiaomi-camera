"""go2rtc supervision.

The add-on's HTTP endpoint serves MPEG-TS directly: a container carrying both
video and audio, with proper timestamps for seeking and timeline sync. go2rtc
demuxes the container, applies its own transport (RTSP, WebRTC, HLS), and
handles reconnection and client negotiation. The same session thus serves Home
Assistant, a browser, and an external NVR alike.

Streams are pulled from this bridge's own HTTP endpoint rather than pushed,
which keeps go2rtc's supervision (retry, backoff, client tracking) in charge of
the connection lifetime.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote

import yaml

from .config import Options, TranscodeQuality, VideoQuality
from .const import (
    API_PORT,
    GO2RTC_API_PORT,
    LOOPBACK,
    RTSP_PORT,
    SRTP_PORT,
    WEBRTC_PORT,
)
from .go2rtc_api import Go2rtcApi
from .paths import VideoPath
from .redact import safe_error
from .settings import Resolved


class SourceUnavailable(RuntimeError):
    """Compatibility mode has no go2rtc-supplied address for this camera.

    Raised rather than silently producing no stream: the caller can catch
    this, skip the camera, and -- this is the whole point of raising rather
    than returning `None` -- record *why*, so the page can report it on the
    camera's row instead of a stream that simply never appears with nothing
    to explain it.
    """


#: `subtype` is the one thing appended to a compatibility-mode source, and it
#: only ever names one of go2rtc's two accepted values. Upstream warns the
#: numeric levels mean different things on different camera models and break
#: the codec outright on some older ones -- named values only, ever.
_COMPAT_SUBTYPE: Final[dict[VideoQuality, str]] = {
    VideoQuality.LOW: "sd",
    VideoQuality.HIGH: "hd",
}

_LOGGER = logging.getLogger(__name__)

_CONFIG_PATH = Path("/data/go2rtc.yaml")

#: go2rtc's own scratch file, and the reason there are two.
#:
#: `PUT` and `DELETE` on go2rtc's streams API do not only change memory: they
#: persist through its config writer into whichever `-config` path it was
#: given *first* -- measured, not assumed (see the go2rtc API findings). With
#: a single `-config` that would be the file this module regenerates, so a
#: delivery landing while `_write_config` is rewriting it could leave YAML
#: neither side can parse, and the next restart would take every stream down
#: with only a parse error to explain it.
#:
#: Passing this file first gives those writes somewhere of their own. We
#: write it exactly once -- `_ensure_state_file` creates it if it is missing,
#: so go2rtc always has somewhere to read at startup -- and never again after
#: that; ours is passed second, so where both name the same stream ours is
#: the one that wins.
_STATE_PATH = Path("/data/go2rtc-state.yaml")

_BINARY = "/usr/local/bin/go2rtc"

#: The generated configuration carries the RTSP credentials, so it is written
#: no more readably than the credential file is.
_OWNER_READ_WRITE = 0o600

#: Restart delay if go2rtc exits unexpectedly. Long enough to avoid a hot loop,
#: short enough that a transient failure self-heals before anyone notices.
_RESTART_DELAY_SECONDS = 5.0

#: How long to wait for go2rtc to answer its API after starting it. It boots
#: in a fraction of a second; this is a margin, not a deadline the design
#: leans on -- a longer wait would only delay every start-up by its full
#: length. The start contract is "go2rtc is up", so callers that read from
#: it (compat_ready) never race the boot.
_STARTUP_READY_ATTEMPTS = 20
_STARTUP_READY_INTERVAL = 0.25

#: How long to wait for a stopped go2rtc to report its exit, applied after the
#: polite signal and again after the fatal one. See the same constant in
#: `bridge.stills`: killing a process guarantees it dies, not that its exit
#: status is still there to be collected, and waiting without a limit for
#: something another thread may already have taken never ends. Longer than the
#: preview's because go2rtc has live consumers to disconnect on the way out.
_STOP_TIMEOUT = 10


#: Our log levels mapped onto go2rtc's, which names them differently and has
#: no equivalent for the ones in between. Default `info` rather than `warn`:
#: go2rtc reports a source it cannot start there, and that is the failure this
#: add-on most needs explained.
_GO2RTC_LOG_LEVELS = {
    "trace": "trace",
    "debug": "debug",
    "info": "info",
    "notice": "info",
    "warning": "warn",
    "error": "error",
    "fatal": "fatal",
}


#: How go2rtc should encode. Its own defaults are close to this, but pin
#: `-g 25` rather than the default 50: a keyframe roughly every second at these
#: frame rates. Home Assistant's live view is HLS, which cannot begin at
#: anything else and buffers a segment or two first, so the keyframe interval
#: is most of the wait before a picture appears. Halving it halves that wait,
#: and more keyframes at a fixed bitrate costs a little detail -- worth it for
#: a view someone is waiting on.
#:
#: x264 and x265 are the standard encoders for their formats and are present
#: in the GPL build this image ships. The image previously carried the LGPL
#: build, where neither exists.
_H264_ENCODER = (
    "-c:v libx264 -g 25 -preset:v superfast -tune:v zerolatency "
    "-profile:v high -pix_fmt:v yuv420p"
)
_H265_ENCODER = (
    "-c:v libx265 -g 25 -preset:v superfast -tune:v zerolatency "
    "-profile:v main -pix_fmt:v yuv420p"
)

#: The quality each setting asks for, on the encoders' own scale where lower
#: is finer. Two tables because the scales are not the same one: x265 needs a
#: higher number for the picture x264 gives at a lower one, and sharing a
#: constant would have made every H.265 variant quietly worse than its H.264
#: twin while the configuration read as though they matched.
_CRF: Final[dict[str, dict[TranscodeQuality, int]]] = {
    "h264": {
        TranscodeQuality.STANDARD: 23,
        TranscodeQuality.SHARP: 20,
        TranscodeQuality.MAXIMUM: 17,
    },
    "h265": {
        TranscodeQuality.STANDARD: 28,
        TranscodeQuality.SHARP: 25,
        TranscodeQuality.MAXIMUM: 22,
    },
}

#: How far the safety valve opens as the quality rises. A cap left where it
#: was would bind before the finer picture arrived, so the setting would buy
#: nothing on exactly the busy scenes that motivated raising it.
_CEILING_MULTIPLIER: Final[dict[TranscodeQuality, int]] = {
    TranscodeQuality.STANDARD: 1,
    TranscodeQuality.SHARP: 2,
    TranscodeQuality.MAXIMUM: 4,
}


@dataclass(frozen=True)
class StreamSpec:
    """One published variant of a camera's video.

    Height is the only dimension given: width follows the source's aspect
    ratio, so a 16:9 camera yields 640x360 and a 4:3 one 480x360 -- both of
    which are resolutions consumers ask for. Fixing the width instead would
    letterbox one of them.
    """

    key: str
    codec: str
    height: int | None
    #: The most this variant may ever spend, not what it aims to spend. The
    #: quality is asked for with `-crf`; this is only what stops an unusually
    #: busy picture running away with the network.
    ceiling: str

    def template_for(self, quality: TranscodeQuality) -> str:
        """The go2rtc encoder template this variant asks for at this quality.

        go2rtc looks the name up in its `ffmpeg` map by whole string, without
        parsing it, so the quality can be part of the name. That is what makes
        a quality change a *stream* change: every combination already exists in
        the table, and the stream simply names a different one. The table
        itself is process-level and could not be changed without a restart.

        Passing scale and quality through `#raw=` instead does not work:
        go2rtc appends the `#video=` template's arguments afterwards, and
        ffmpeg takes the last of any argument it is given twice.
        """
        if self.height is None:
            return f"{self.codec}/{quality.value}"
        return f"{self.codec}/{self.height}/{quality.value}"


#: A height is a ceiling, never a target: a camera sending less than a rung
#: asks for is published at its own size. Rungs above the source therefore
#: cost nothing and lie about nothing -- which is what lets the ladder be the
#: same everywhere instead of being tuned to whatever any one vendor calls
#: "2K". The source resolution is unknown when this file is written, since it
#: is not settled until a peer-to-peer session delivers a frame, so the
#: decision has to be one ffmpeg makes per stream rather than one made here.
#:
#: The root's codec field is "original": the camera's own encoding is not
#: known until a session delivers its first frame, and the root never names
#: a codec anywhere -- neither in its key nor in its go2rtc name. Its ceiling
#: is documentary only (the root carries no `#video=` argument, so no template
#: is ever generated for it).
#:
#: The ceilings are a ladder, and their ratios are the part worth reading:
#: each rung may spend roughly in proportion to the pixels it carries.
#: `transcode_quality` moves the whole ladder rather than any single rung,
#: which is what keeps the ratios from drifting apart one plausible-looking
#: edit at a time. They are deliberately loose -- a valve, not a setting --
#: so that on ordinary scenes the quality target is what decides the bitrate.
STREAM_SPECS: tuple[StreamSpec, ...] = (
    StreamSpec("original", "original", None, "24M"),
    StreamSpec("h265", "h265", None, "24M"),
    StreamSpec("h265_1440", "h265", 1440, "16M"),
    StreamSpec("h265_1080", "h265", 1080, "10M"),
    StreamSpec("h265_720", "h265", 720, "6M"),
    StreamSpec("h265_360", "h265", 360, "2M"),
    StreamSpec("h265_180", "h265", 180, "1M"),
    StreamSpec("h264", "h264", None, "24M"),
    StreamSpec("h264_1440", "h264", 1440, "16M"),
    StreamSpec("h264_1080", "h264", 1080, "10M"),
    StreamSpec("h264_720", "h264", 720, "6M"),
    StreamSpec("h264_360", "h264", 360, "2M"),
    StreamSpec("h264_180", "h264", 180, "1M"),
)

#: The variant every other one is derived from: the camera's own encoding at
#: its own resolution, repackaged without re-encoding. Codec-neutral on
#: purpose -- its codec is unknown until a session runs, and naming it would
#: lie for every camera whose native codec is not the one named.
ROOT_KEY = "original"


def _audio_codecs(spec: StreamSpec) -> tuple[str, ...]:
    """The audio a variant offers, negotiated per consumer by go2rtc.

    The root carries the camera's own encoding, so its audio is passed
    through untouched. Among transcoded variants, the H.264 family exists
    for consumers that cannot decode H.265 -- overwhelmingly the same
    population that cannot decode Opus (Home Assistant's own HLS path
    accepts aac and mp3 only) -- so those variants additionally offer an aac
    conversion. H.265 variants stay copy-only: a consumer that accepts H.265
    can decode the camera's own audio.

    Nothing is transcoded that was not already: these variants re-encode the
    picture regardless, so the audio rides along on a process that is running
    anyway. `copy` is listed first, so a consumer that asks for nothing gets
    the camera's own encoding untouched.
    """
    if spec.key == ROOT_KEY:
        return ("copy",)
    return ("copy", "aac") if spec.codec == "h264" else ("copy",)


def stream_name(did: str, key: str = ROOT_KEY) -> str:
    """Stable go2rtc stream name for one variant of a camera.

    `camera_<did>` for the root -- the camera's own encoding, which has no
    codec of its own to state -- or `camera_<did>_<codec>` /
    `camera_<did>_<codec>_<height>` for derived variants. The codec is never
    omitted from a derived name: a name that depends on which codec happens
    to be the default is a rule with an exception, and the exception is
    exactly what makes two 360p streams indistinguishable.

    This never depends on which path serves the camera, and must not start:
    the go2rtc stream name is also the integration's unique_id suffix, so a
    name that moved when a camera switched path would destroy and recreate
    its entity -- unpairing HomeKit and orphaning history, the exact failure
    this project has already shipped once (see `paths.py`'s own docstring).

    There is deliberately no name here for a dual-lens camera's second lens.
    One was added on the strength of "the official path already does this",
    which was not true of the official path and never had been; nothing
    produced it (the refused-camera branch reports one channel) and nothing
    consumed it (`stream_descriptions` never named it). A real dual-lens
    camera gets this designed then, with evidence.
    """
    return f"camera_{did}" if key == ROOT_KEY else f"camera_{did}_{key}"


def _bits(rate: str) -> int:
    """`24M` or `512k` as a plain count of bits per second."""
    return int(rate[:-1]) * {"k": 1_000, "M": 1_000_000}[rate[-1]]


def _rate(bits: int) -> str:
    """The shortest form ffmpeg accepts, so the config stays readable."""
    if bits % 1_000_000 == 0:
        return f"{bits // 1_000_000}M"
    return f"{bits // 1_000}k"


def _encoder_templates() -> dict[str, str]:
    """One go2rtc encoder template per published variant, at every quality.

    Merged into go2rtc's own table, so these names -- `h264/360/sharp` and so
    on -- become valid `#video=` values. All of them are generated whether or
    not any camera currently asks for them: the table is read once at startup,
    so a name absent from it cannot be adopted later without restarting the
    process and dropping every live viewer.

    Quality is asked for, and bandwidth is only capped. A bitrate target
    spends its whole allowance on a still room at night and then runs out on
    the one second somebody walks through it, which is backwards: a viewer
    perceives the picture, not the byte count -- and a camera pointed at a
    room that rarely changes is the case this add-on exists for, so that
    still room is the ordinary state, not the edge case. `-crf` asks for a
    quality and spends what that costs; `-maxrate` is the valve that keeps an
    unusually busy picture off the network's back.

    It also settles what no bitrate could. The full-size variants re-encode
    whatever resolution the camera happens to send, and that is not known when
    this file is written -- a figure picked here would be far too small for a
    4K sensor and wasteful on a 480p one. A quality target needs no such
    knowledge.
    """
    base = {"h264": _H264_ENCODER, "h265": _H265_ENCODER}
    templates: dict[str, str] = {}
    for quality in TranscodeQuality:
        for spec in STREAM_SPECS:
            if spec.key == ROOT_KEY:
                # The root is served directly from the endpoint and carries no
                # `#video=` argument, so it keeps no template here.
                continue
            # The buffer holds two seconds at the ceiling: long enough to spend
            # on a keyframe without the frames after it paying for the burst.
            ceiling = _bits(spec.ceiling) * _CEILING_MULTIPLIER[quality]
            template = (
                f"{base[spec.codec]} -crf {_CRF[spec.codec][quality]} "
                f"-maxrate {_rate(ceiling)} -bufsize {_rate(ceiling * 2)}"
            )
            if spec.height is not None:
                # `min(..., ih)` rather than the height alone, so a camera that
                # already sends less is published at its own size instead of
                # being stretched -- more bandwidth and more work for a softer
                # picture than the source. Quoted because `min` takes a comma,
                # which would otherwise read as the end of this filter; go2rtc
                # splits its templates on whitespace and leaves a token that
                # does not begin with a quote alone, so ffmpeg receives this
                # exactly as written.
                template += f" -vf scale=-2:'min({spec.height},ih)'"
            templates[spec.template_for(quality)] = template
    return templates


def source_for(
    did: str, settings: Resolved, compat_urls: Mapping[str, str] | None = None
) -> str:
    """Where go2rtc reads this camera's root stream from.

    The single place in the codebase that answers this. Every consumer
    downstream -- the thirteen published variants, the camera and switch
    entities, HomeKit, the preview -- reads the published stream by name and
    never learns where it came from.

    Which branch runs is decided by `settings.path` alone, never by asking
    `go2rtc_xiaomi.compat_ready()` -- that flag degrades to `False` on any
    transient go2rtc error, and a stream generator that consulted it directly
    could erase a working compatibility stream from the config over a single
    hiccup. `settings.path` already carries the right answer: `path_for`
    (see `paths.py`) ignores `compat_ready` whenever a stored override
    exists, precisely so a camera the user put on compatibility mode keeps
    that path -- and therefore keeps its entity -- when the credential goes
    missing.

    Compatibility mode's URL is never composed here. go2rtc has already
    filled in the camera's LAN address, account id and region when it built
    `compat_urls` (see `go2rtc_xiaomi.all_device_urls`); rebuilding any of
    that from scratch would put it in a second place that drifts the first
    time any of it moves -- the same mistake the multi-stream default did.
    Only the picture quality is added, as go2rtc's own `subtype` parameter:
    `low` maps to `sd` and `high` to `hd`, the two named values go2rtc itself
    accepts. Never the numeric levels: upstream warns those mean different
    things on different camera models and break the codec outright on some
    older ones.
    """
    if settings.path is VideoPath.OFFICIAL:
        return f"http://{LOOPBACK}:{API_PORT}/api/stream/{did}"
    base = (compat_urls or {}).get(did)
    if base is None:
        raise SourceUnavailable(
            f"no compatibility-mode address for {did} -- "
            "not signed in, or the camera is unreachable from any signed-in account"
        )
    return f"{base}&subtype={_COMPAT_SUBTYPE[settings.quality]}"


def build_config(
    options: Options,
    cameras: Mapping[str, Resolved | None],
    *,
    compat_urls: Mapping[str, str] | None = None,
    errors: dict[str, str] | None = None,
) -> dict:
    """Render the go2rtc configuration.

    The root is read straight from this add-on's own endpoint on the official
    path, with no ffmpeg in between: it serves MPEG-TS, which go2rtc demuxes
    itself. On compatibility mode the root is go2rtc's own Xiaomi source
    instead (see `source_for`). Either way both of the camera's tracks reach
    RTSP exactly as the camera encoded them.

    Derived variants do re-encode the picture, which is what they are for.
    Clients that cannot decode H.265 take one of those rather than forcing a
    transcode on everyone.

    There is deliberately no MJPEG source here. An earlier version added one so
    the add-on page could show a preview, which meant ffmpeg re-encoding H.265
    for a picture the vendor SDK was already decoding to JPEG on its own. The
    page now reads those frames directly and go2rtc is left to the job it is
    good at.

    A camera's settings may resolve to `None` -- `SettingsStore.resolved_for`
    returns that when the camera has no usable path at all -- and such a
    camera is simply left out, exactly as if it were absent from `cameras`.

    `errors`, if given, is filled in as a side effect with one entry per
    camera whose root source could not be built (`SourceUnavailable`): a
    resolvable path that still cannot produce a stream, most often a
    compatibility-mode camera go2rtc cannot currently reach. Populating this
    is what lets the page report the failure on that camera's row instead of
    it simply having no working stream with nothing to explain why.
    """
    compat_urls = compat_urls or {}
    bind = options.bind_address
    streams: dict[str, str] = {}
    for did, settings in cameras.items():
        if settings is None:
            _LOGGER.warning(
                "%s has no resolved video settings; publishing nothing for it", did
            )
            continue
        root = stream_name(did)
        try:
            root_source = source_for(did, settings, compat_urls)
        except SourceUnavailable as err:
            if errors is not None:
                errors[did] = safe_error(err)
            _LOGGER.warning("skipping %s: %s", did, safe_error(err))
            continue
        # No ffmpeg in front of it: go2rtc demuxes the MPEG-TS this endpoint
        # serves and passes both tracks through untouched. An ffmpeg hop here
        # would spend three seconds of cold start probing a container it did
        # not need to, to do a job go2rtc already does.
        streams[root] = root_source
        for spec in STREAM_SPECS:
            if spec.key == ROOT_KEY:
                continue
            audio = "".join(f"#audio={codec}" for codec in _audio_codecs(spec))
            # Named after the root rather than repeating its URL, so all of
            # them share one session on the camera instead of opening several.
            streams[stream_name(did, spec.key)] = (
                f"ffmpeg:{root}#video={spec.template_for(settings.transcode_quality)}"
                f"{audio}"
            )

    config: dict = {
        # Quiet by default, but follows the add-on's own level when raised.
        # Diagnosing a stream that will not start means reading what go2rtc
        # says about it, and the troubleshooting instructions tell users to
        # turn the level up before reporting a problem.
        "log": {"level": _GO2RTC_LOG_LEVELS.get(options.log_level, "info")},
        "api": {"listen": f"{LOOPBACK}:{GO2RTC_API_PORT}"},
        "rtsp": {"listen": f"{bind}:{RTSP_PORT}"},
        "webrtc": {"listen": f"{bind}:{WEBRTC_PORT}"},
        # Every listener go2rtc offers is pinned explicitly. Modules left out of
        # the configuration fall back to go2rtc's own defaults, several of which
        # bind all interfaces -- srtp on :8443 among them -- which would quietly
        # contradict the loopback-only guarantee of `local` mode.
        "srtp": {"listen": f"{LOOPBACK}:{SRTP_PORT}"},
        "streams": streams,
        "ffmpeg": _encoder_templates(),
    }

    if options.requires_credentials:
        config["rtsp"]["username"] = options.rtsp_username
        config["rtsp"]["password"] = options.rtsp_password

    return config


def _write_config(config: dict) -> None:
    """Write the generated configuration, all of it or none of it.

    The same temp-then-replace idiom the credential store uses, for the same
    reason and with the same permissions: a crash -- or a go2rtc restart
    picking the file up -- part way through a plain write leaves a truncated
    document, and truncated YAML is not a smaller configuration but an
    unparseable one, which costs every stream rather than the changed ones.
    Written owner-only from the start rather than chmod'ed afterwards, so the
    RTSP password inside it is never briefly world-readable.
    """
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _CONFIG_PATH.with_suffix(".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_READ_WRITE
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    temporary.replace(_CONFIG_PATH)


def _ensure_state_file() -> None:
    """Give go2rtc's own writes a file to land in before it starts.

    go2rtc creates it on the first write regardless; creating it here means a
    reader who finds an unfamiliar file in `/data` finds an explanation with
    it, rather than a stream table that looks like a second configuration
    someone forgot about.
    """
    if _STATE_PATH.exists():
        return
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        "# Written by go2rtc itself when the add-on changes a stream without\n"
        "# restarting it. Not the add-on's configuration -- that is\n"
        f"# {_CONFIG_PATH}, which is loaded after this file and wins.\n",
        encoding="utf-8",
    )


class Restreamer:
    """Runs go2rtc and keeps its configuration in sync with the camera list."""

    def __init__(self, options: Options) -> None:
        self._options = options
        self._process: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._cameras: dict[str, Resolved] = {}
        #: What `async_apply` was last called with, alongside `_cameras`,
        #: purely so a later call can tell whether anything actually changed
        #: -- see the comparison in `async_apply`. Kept out of `_cameras`'s
        #: own shape: existing callers and tests read that attribute as a
        #: plain `dict[str, Resolved]`.
        self._compat_urls: dict[str, str] = {}
        #: The stream table as last written, which is also what the running
        #: go2rtc was last told. Kept so a change can be delivered per stream:
        #: a name whose source string is identical needs no call at all, and
        #: most settings changes leave most names identical.
        self._streams: dict[str, str] = {}
        #: Which cameras' root source could not be built on the last apply,
        #: and why -- see `build_config`'s `errors` parameter. Read by the
        #: control plane so a camera with a resolvable path but no working
        #: stream reports that on its own row instead of silently having no
        #: stream at all.
        self._stream_errors: dict[str, str] = {}
        self._api = Go2rtcApi()

    def stream_error(self, did: str) -> str | None:
        """Why this camera's root stream could not be built, as of the last
        `async_apply` -- or `None` if it was built, or nothing was ever
        applied for it."""
        return self._stream_errors.get(did)

    @property
    def requires_credentials(self) -> bool:
        return self._options.requires_credentials

    @property
    def rtsp_reachable_off_host(self) -> bool:
        """Whether the published RTSP listener can be reached from another machine.

        Derived from `bind_address` rather than `access_mode` directly, and
        kept separate from `requires_credentials`: the two happen to agree
        today -- `lan` mode is the only mode that both requires a password and
        binds beyond loopback -- but they answer different questions, and nothing
        forces them to keep agreeing. `requires_credentials` speaks for whether a
        password is mandatory; this speaks for whether a listener is reachable
        off-box, which is what a page rewriting a displayed hostname needs to know.
        """
        return self._options.bind_address != LOOPBACK

    def rtsp_url(self, did: str) -> str:
        """RTSP URL for a camera, always without embedded credentials.

        Home Assistant shares the host network namespace, so loopback reaches
        the listener even when it is bound to all interfaces.

        The password is deliberately not interpolated here. A URL carrying
        ``user:password@`` is copied into Home Assistant's config entry state,
        its diagnostics downloads and debug logs, and is rendered verbatim in
        this add-on's own UI -- it leaks by construction. Consumers that need
        credentials read them from the add-on configuration.
        """
        return f"rtsp://{LOOPBACK}:{RTSP_PORT}/{stream_name(did)}"

    def rtsp_url_h264(self, did: str) -> str:
        """RTSP URL for the H.264 version of a camera's stream."""
        return f"rtsp://{LOOPBACK}:{RTSP_PORT}/{stream_name(did, 'h264')}"

    def stream_descriptions(self, did: str) -> list[dict[str, object]]:
        """Every variant published for a camera, for the integration to read.

        Sent rather than hardcoded on the other side so the two components can
        be upgraded independently: an integration older than this add-on shows
        the variants it knows, and a newer one shows whatever arrives.

        Credentials are absent by construction, as in :meth:`rtsp_url`.
        """
        return [
            {
                "key": spec.key,
                "codec": spec.codec,
                "height": spec.height,
                "url": f"rtsp://{LOOPBACK}:{RTSP_PORT}/{stream_name(did, spec.key)}",
            }
            for spec in STREAM_SPECS
        ]

    def internal_rtsp_url(self, did: str) -> str:
        """RTSP URL for this process's own use, credentials included.

        Separate from :meth:`rtsp_url` on purpose. That one is handed to Home
        Assistant and rendered in this add-on's page, so it must never carry a
        password; this one is read by ffmpeg inside the container and never
        leaves it. Two callers with genuinely different requirements, rather
        than one URL compromising for both.
        """
        if not self.requires_credentials:
            return self.rtsp_url(did)
        credentials = (
            f"{quote(self._options.rtsp_username, safe='')}:"
            f"{quote(self._options.rtsp_password, safe='')}@"
        )
        return f"rtsp://{credentials}{LOOPBACK}:{RTSP_PORT}/{stream_name(did)}"

    async def async_apply(
        self,
        cameras: Mapping[str, Resolved | None],
        *,
        compat_urls: Mapping[str, str] | None = None,
        explicit: bool = False,
    ) -> None:
        """Make the running configuration match `cameras`.

        Compares the whole mapping, not just which cameras exist: a changed
        transcode quality changes which template a stream names, and a
        comparison on the camera set alone would leave the old one running.
        Equality on a dict ignores key order, which matters because the cloud
        does not guarantee a stable device order -- treating a reordering as
        a change would restart go2rtc, dropping every live viewer, on an
        unrelated refresh. `compat_urls` is compared alongside it for the
        same reason: a compatibility-mode camera's address can change in the
        background with its `Resolved` untouched, and missing that would
        leave go2rtc dialing a stale address until some unrelated setting
        happened to change too.

        A camera may map to `None` -- `SettingsStore.resolved_for` returns
        that for one with no usable path -- and is simply published as
        nothing, the same as if it were absent; see `build_config`.

        The file is rewritten first and always: it is what a restart rebuilds
        from, so a change delivered only to the running process would be
        undone the next time go2rtc restarted -- silently, and long after the
        change was made.

        With the file correct, the running process is brought up to date the
        cheapest way that works. Changing streams in place keeps every viewer
        connected; a restart drops all of them, and is used only when go2rtc
        is not running yet or refuses a change.

        `explicit` distinguishes why the mapping changed, and comes from the
        caller because the caller already knows: a settings write someone is
        watching for (`explicit=True`) against a background refresh -- a
        camera's address changing, a periodic re-read of the device list --
        whose viewers are left on their existing connection.
        """
        compat_urls = dict(compat_urls or {})
        if (
            cameras == self._cameras
            and compat_urls == self._compat_urls
            and self._process is not None
        ):
            return

        self._cameras = dict(cameras)
        self._compat_urls = compat_urls
        errors: dict[str, str] = {}
        config = build_config(
            self._options,
            self._cameras,
            compat_urls=self._compat_urls,
            errors=errors,
        )
        self._stream_errors = errors
        previous_streams = self._streams
        self._streams = dict(config["streams"])
        _write_config(config)

        if self._process is None:
            _LOGGER.info("Publishing %d camera stream(s) over RTSP", len(self._cameras))
            await self.async_restart()
            return

        delivered = await self._async_deliver(
            previous_streams, self._streams, explicit=explicit
        )
        if delivered is not None:
            _LOGGER.info("Updated %d go2rtc stream(s) in place", delivered)
            return

        _LOGGER.info("Could not update streams in place; restarting go2rtc")
        await self.async_restart()

    async def _async_deliver(
        self,
        previous: Mapping[str, str],
        streams: Mapping[str, str],
        *,
        explicit: bool,
    ) -> int | None:
        """Bring the running go2rtc's streams up to date, one stream at a time.

        Returns how many streams were touched, or ``None`` if any call failed.

        The comparison is per stream name, against the table last written,
        because that is the granularity the cost is paid at: dropping a
        stream drops its consumers -- a Home Assistant entity's live view, a
        HomeKit session, an open preview on this page. A stream whose source
        string is unchanged is left completely alone, so a settings change on
        one camera reaches that camera's streams and no other's.

        It also means a change go2rtc cannot see costs nothing. Picture size
        and sound are negotiated with the camera when the peer-to-peer
        session opens and appear nowhere in this configuration; only
        `transcode_quality` reaches it, by naming a different encoder
        template. Changing the other two rewrites a byte-identical stream
        table, finds nothing to deliver, and leaves every viewer where they
        were.

        All or nothing on failure: one failed call abandons the delivery and
        lets the caller restart, rather than leaving the process in a state no
        file describes.

        Two kinds of change, delivered differently, because `PATCH` changes
        what a stream *will* dial and never moves a viewer already connected
        to it -- measured, not assumed (see the go2rtc API findings).

        A camera that changed address is the background case: the source the
        current viewer holds is already dead, so leaving it alone costs
        nothing and the repair applies to every later dial. `PATCH` is right,
        and it writes to no file.

        A setting somebody just changed is the opposite. They are watching
        and waiting to see it, and a stream that keeps serving the old
        picture until they happen to reconnect reads as a setting that did
        nothing. Those streams are replaced, which drops their consumers so
        they redial.
        """
        changed = 0
        for name in previous:
            if name in streams:
                continue
            if not await self._api.remove_stream(name):
                return None
            changed += 1
        # Which kind of change this is comes from the caller, which knows:
        # a refresh of the camera list is background, a settings write is
        # not. The table comparison answers *which* streams changed, never
        # *why* -- an address drift and a quality change both read as a
        # different source string here.
        deliver = self._api.replace_stream if explicit else self._api.set_stream
        for name, src in streams.items():
            if previous.get(name) == src:
                continue
            if not await deliver(name, src):
                return None
            changed += 1
        return changed

    async def async_start(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())
        # The start contract is "go2rtc is up": wait until it answers its API,
        # so a caller that reads from it (compat_ready) never races the boot
        # and reads a credential that is actually there as absent.
        for _ in range(_STARTUP_READY_ATTEMPTS):
            if await self._api.ready():
                return
            await asyncio.sleep(_STARTUP_READY_INTERVAL)
        _LOGGER.error(
            "go2rtc did not answer its API after %.1fs; compatibility mode "
            "will read as not signed in until a later refresh",
            _STARTUP_READY_ATTEMPTS * _STARTUP_READY_INTERVAL,
        )

    async def async_restart(self) -> None:
        await self._async_terminate()
        await self.async_start()

    async def async_stop(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            self._supervisor = None
        await self._async_terminate()

    async def _supervise(self) -> None:
        """Keep go2rtc running, restarting it if it exits."""
        while True:
            try:
                # Shielded so a cancellation landing mid-spawn cannot leave a
                # process running that nothing holds a handle to -- it would
                # keep the RTSP port bound and make every later start fail with
                # "address already in use".
                _ensure_state_file()
                self._process = await asyncio.shield(
                    asyncio.create_subprocess_exec(
                        _BINARY,
                        # Order is load-bearing, not cosmetic: go2rtc writes
                        # its API changes back into the first `-config` path
                        # and reads both, later files winning. State first so
                        # its writes stay out of the file this module
                        # regenerates; ours second so it is the authority on
                        # what should be published.
                        "-config",
                        str(_STATE_PATH),
                        "-config",
                        str(_CONFIG_PATH),
                        # Merged rather than captured separately: go2rtc
                        # writes its log to stdout by default -- and says in
                        # its own source that it means to move it to stderr
                        # one day -- so reading either one alone is a bet on
                        # which. Taking both leaves nothing to lose.
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                )
                _LOGGER.info("go2rtc started (pid %s)", self._process.pid)
                await self._relay_output(self._process)
                code = await self._process.wait()
                if code != 0:
                    _LOGGER.error("go2rtc exited with code %s", code)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.error("Could not start go2rtc: %s", safe_error(err))
            self._process = None
            await asyncio.sleep(_RESTART_DELAY_SECONDS)

    @staticmethod
    async def _relay_output(process: asyncio.subprocess.Process) -> None:
        """Forward go2rtc's output into this add-on's log as it appears.

        Buffering it until the process exits hid every problem that does not
        also kill go2rtc -- a transcode that cannot start, a source it refuses
        -- which is exactly the class of failure worth seeing, and left the
        user looking at a preview that never appeared with nothing to explain
        it. It also risked filling the pipe and blocking go2rtc outright.

        Its own level lives in the generated configuration, so what arrives
        here is already filtered; it is logged as-is rather than re-parsed.
        """
        stream = process.stdout
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                _LOGGER.info("go2rtc: %s", text)

    async def _async_terminate(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=_STOP_TIMEOUT)
        except TimeoutError:
            process.kill()
            # Bounded, and its failure swallowed: SIGKILL has been delivered,
            # so there is nothing further to do here whether or not the exit
            # status ever arrives. Waiting indefinitely for it would leave
            # the add-on unable to shut down on its own.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=_STOP_TIMEOUT)
        self._process = None
