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
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from .config import Options
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
    bitrate: str

    @property
    def template(self) -> str:
        """The go2rtc encoder template this variant asks for.

        go2rtc looks the name up in its `ffmpeg` map, so a variant needs its
        own entry there -- `h264/360` and friends, generated below. Passing
        scale and bitrate through `#raw=` instead does not work: go2rtc
        appends the `#video=` template's arguments afterwards, and ffmpeg
        takes the last `-b:v` it is given.
        """
        if self.height is None:
            return self.codec
        return f"{self.codec}/{self.height}"


#: Heights above the source are not suppressed. This configuration is written
#: when the camera list changes, and the source resolution is unknown until a
#: peer-to-peer session runs -- so there is no moment at which a height could
#: be filtered out. An upscale wastes nothing that is not already idle: no
#: producer starts until a consumer connects.
STREAM_SPECS: tuple[StreamSpec, ...] = (
    # The root's bitrate is documentary only: the root carries no `#video=`
    # argument at all (see `build_config`), so no template is ever generated
    # for it and this value is never read.
    StreamSpec("h265", "h265", None, "2M"),
    StreamSpec("h265_720", "h265", 720, "2M"),
    StreamSpec("h265_360", "h265", 360, "512k"),
    StreamSpec("h265_180", "h265", 180, "256k"),
    StreamSpec("h264", "h264", None, "2M"),
    StreamSpec("h264_720", "h264", 720, "2M"),
    StreamSpec("h264_360", "h264", 360, "512k"),
    StreamSpec("h264_180", "h264", 180, "256k"),
)

#: The variant every other one is derived from: the camera's own encoding at
#: its own resolution, repackaged without re-encoding.
ROOT_KEY = "h265"

#: The codec the root publishes, read from the root's own spec rather than
#: written out again. A second copy is a second thing to drift.
ROOT_CODEC = next(spec.codec for spec in STREAM_SPECS if spec.key == ROOT_KEY)


def _audio_codecs(spec: StreamSpec) -> tuple[str, ...]:
    """The audio a variant offers, negotiated per consumer by go2rtc.

    A variant's audio serves the same consumer its video codec serves. The
    H.264 family exists for consumers that cannot decode H.265, which is
    overwhelmingly the same population that cannot decode Opus -- Home
    Assistant's own HLS path among them, which accepts aac and mp3 only. An
    H.264 variant carrying Opus alone would be half-compatible, and silently.

    Nothing is transcoded that was not already: those variants re-encode the
    picture regardless, so the second encoding rides along on a process that
    is running anyway. `copy` is listed first, so a consumer that asks for
    nothing gets the camera's own encoding untouched.
    """
    return ("copy",) if spec.codec == ROOT_CODEC else ("copy", "aac")


def stream_name(did: str, key: str = ROOT_KEY) -> str:
    """Stable go2rtc stream name for one variant of a camera.

    `camera_<did>_<codec>` or `camera_<did>_<codec>_<height>`. The codec is
    never omitted, including for the camera's own encoding: a name that
    depends on which codec happens to be the default is a rule with an
    exception, and the exception is exactly what makes two 360p streams
    indistinguishable.
    """
    return f"camera_{did}_{key}"


def _encoder_templates() -> dict[str, str]:
    """One go2rtc encoder template per published variant.

    Merged into go2rtc's own table, so these names -- `h264`, `h264/360` and
    so on -- become valid `#video=` values.
    """
    base = {"h264": _H264_ENCODER, "h265": _H265_ENCODER}
    templates = dict(base)
    for spec in STREAM_SPECS:
        if spec.key == ROOT_KEY:
            # The root is served directly from the endpoint and carries no
            # `#video=` argument, so it keeps no template here.
            continue
        template = f"{base[spec.codec]} -b:v {spec.bitrate}"
        if spec.height is not None:
            template += f" -vf scale=-2:{spec.height}"
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
        "ffmpeg": _encoder_templates(),
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
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
        self._process = None
