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
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote

import yaml

from .config import Options, TranscodeQuality
from .const import (
    API_PORT,
    GO2RTC_API_PORT,
    LOOPBACK,
    RTSP_PORT,
    SRTP_PORT,
    WEBRTC_PORT,
)
from .redact import safe_error

_LOGGER = logging.getLogger(__name__)

_CONFIG_PATH = Path("/data/go2rtc.yaml")
_BINARY = "/usr/local/bin/go2rtc"

#: Restart delay if go2rtc exits unexpectedly. Long enough to avoid a hot loop,
#: short enough that a transient failure self-heals before anyone notices.
_RESTART_DELAY_SECONDS = 5.0

#: How long to wait for a stopped go2rtc to report its exit, applied after the
#: polite signal and again after the fatal one. See the same constant in
#: `bridge.preview`: killing a process guarantees it dies, not that its exit
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

    @property
    def template(self) -> str:
        """The go2rtc encoder template this variant asks for.

        go2rtc looks the name up in its `ffmpeg` map, so a variant needs its
        own entry there -- `h264/360` and friends, generated below. Passing
        scale and quality through `#raw=` instead does not work: go2rtc
        appends the `#video=` template's arguments afterwards, and ffmpeg
        takes the last of any argument it is given twice.
        """
        if self.height is None:
            return self.codec
        return f"{self.codec}/{self.height}"


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
    """
    if key == ROOT_KEY:
        return f"camera_{did}"
    return f"camera_{did}_{key}"


def _bits(rate: str) -> int:
    """`24M` or `512k` as a plain count of bits per second."""
    return int(rate[:-1]) * {"k": 1_000, "M": 1_000_000}[rate[-1]]


def _rate(bits: int) -> str:
    """The shortest form ffmpeg accepts, so the config stays readable."""
    if bits % 1_000_000 == 0:
        return f"{bits // 1_000_000}M"
    return f"{bits // 1_000}k"


def _encoder_templates(quality: TranscodeQuality) -> dict[str, str]:
    """One go2rtc encoder template per published variant.

    Merged into go2rtc's own table, so these names -- `h264`, `h264/360` and
    so on -- become valid `#video=` values.

    Quality is asked for, and bandwidth is only capped. A bitrate target
    spends its whole allowance on a still room at night and then runs out on
    the one second somebody walks through it, which is backwards: a viewer
    perceives the picture, not the byte count, and a camera pointed at a
    room that rarely changes is the case this add-on exists for. `-crf` asks
    for a quality and spends what that costs; `-maxrate` is the valve that
    keeps an unusually busy picture off the network's back.

    It also settles what no bitrate could. The full-size variants re-encode
    whatever resolution the camera happens to send, and that is not known
    when this file is written -- a figure picked here would be far too small
    for a 4K sensor and wasteful on a 480p one. A quality target needs no
    such knowledge.
    """
    base = {"h264": _H264_ENCODER, "h265": _H265_ENCODER}
    templates = dict(base)
    for spec in STREAM_SPECS:
        if spec.key == ROOT_KEY:
            # The root is served directly from the endpoint and carries no
            # `#video=` argument, so it keeps no template here.
            continue
        # The buffer holds two seconds at the ceiling: long enough to spend on
        # a keyframe without the frames after it paying for the whole burst.
        ceiling = _bits(spec.ceiling) * _CEILING_MULTIPLIER[quality]
        template = (
            f"{base[spec.codec]} -crf {_CRF[spec.codec][quality]} "
            f"-maxrate {_rate(ceiling)} -bufsize {_rate(ceiling * 2)}"
        )
        if spec.height is not None:
            # `min(..., ih)` rather than the height alone, so a camera that
            # already sends less is published at its own size instead of being
            # stretched -- more bandwidth and more work for a softer picture
            # than the source. Quoted because `min` takes a comma, which would
            # otherwise read as the end of this filter; go2rtc splits its
            # templates on whitespace and leaves a token that does not begin
            # with a quote alone, so ffmpeg receives this exactly as written.
            template += f" -vf scale=-2:'min({spec.height},ih)'"
        templates[spec.template] = template
    return templates


def build_config(options: Options, dids: list[str]) -> dict:
    """Render the go2rtc configuration.

    The root is read straight from this add-on's own endpoint, with no ffmpeg
    in between: it serves MPEG-TS, which go2rtc demuxes itself. Both of the
    camera's tracks reach RTSP exactly as the camera encoded them.

    Derived variants do re-encode the picture, which is what they are for.
    Clients that cannot decode H.265 take one of those rather than forcing a
    transcode on everyone.

    There is deliberately no MJPEG source here. An earlier version added one so
    the add-on page could show a preview, which meant ffmpeg re-encoding H.265
    for a picture the vendor SDK was already decoding to JPEG on its own. The
    page now reads those frames directly and go2rtc is left to the job it is
    good at.
    """
    bind = options.bind_address
    streams: dict[str, str] = {}
    for did in dids:
        root = stream_name(did)
        # No ffmpeg in front of it: go2rtc demuxes the MPEG-TS this endpoint
        # serves and passes both tracks through untouched. An ffmpeg hop here
        # would spend three seconds of cold start probing a container it did
        # not need to, to do a job go2rtc already does.
        streams[root] = f"http://{LOOPBACK}:{API_PORT}/api/stream/{did}"
        for spec in STREAM_SPECS:
            if spec.key == ROOT_KEY:
                continue
            audio = "".join(f"#audio={codec}" for codec in _audio_codecs(spec))
            # Named after the root rather than repeating its URL, so all of
            # them share one session on the camera instead of opening several.
            streams[stream_name(did, spec.key)] = (
                f"ffmpeg:{root}#video={spec.template}{audio}"
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
        "ffmpeg": _encoder_templates(options.transcode_quality),
    }

    if options.requires_credentials:
        config["rtsp"]["username"] = options.rtsp_username
        config["rtsp"]["password"] = options.rtsp_password

    return config


class Restreamer:
    """Runs go2rtc and keeps its configuration in sync with the camera list."""

    def __init__(self, options: Options) -> None:
        self._options = options
        self._process: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._dids: list[str] = []

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

    async def async_apply(self, dids: list[str]) -> None:
        """Write the configuration and (re)start go2rtc if the set changed."""
        # Compare as a set: the cloud does not guarantee a stable device order,
        # and treating a reordering as a change would restart go2rtc -- dropping
        # every live viewer -- on an unrelated refresh.
        if set(dids) == set(self._dids) and self._process is not None:
            return
        self._dids = sorted(dids)
        _CONFIG_PATH.write_text(
            yaml.safe_dump(build_config(self._options, self._dids), sort_keys=False),
            encoding="utf-8",
        )
        _LOGGER.info("Publishing %d camera stream(s) over RTSP", len(self._dids))
        await self.async_restart()

    async def async_start(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())

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
                self._process = await asyncio.shield(
                    asyncio.create_subprocess_exec(
                        _BINARY,
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
