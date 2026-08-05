# Architecture

Notes for anyone working on this codebase. The behaviour described here was
established by reading the vendor SDK and by measuring against real hardware;
several points are counter-intuitive enough that rediscovering them costs a day.

## Why two components

The vendor's streaming implementation is a closed-source shared library linked
against glibc (`libc.so.6`, `libstdc++.so.6`, symbols up to `GLIBC_2.25`). Home
Assistant's own container is built on Alpine and uses musl, which cannot load
it. That single fact forces the split:

```
Add-on (Debian/glibc, host network)     Integration (HA process, Alpine/musl)
  vendor SDK + native library             config flow, installs the add-on
    │ peer-to-peer                        camera entities → stream_source()
    ▼                                     switch entities (lens power)
  cameras                                 coordinator polls the bridge
    │ Annex-B elementary stream
    ▼
  go2rtc → RTSP / WebRTC ────────────────► Home Assistant's stream component
```

Video never enters the Home Assistant process. The integration only hands it a
URL.

## Network path

The transport is a PPPP-family peer-to-peer stack with three paths, tried in
order:

| Path | Mechanism | Consequence |
|---|---|---|
| LAN | UDP broadcast discovery, then a direct connection | Fast, private, no upstream bandwidth |
| WAN | STUN hole punching | Works, quality depends on NAT type |
| Relay | Proxied through the vendor's servers | High latency, reduced bitrate, video leaves the network |

Two design consequences:

- **`host_network: true` is mandatory.** Docker's bridge network does not
  forward broadcasts, and its extra NAT layer breaks hole punching. Both
  failures are silent: sessions simply degrade to relay.
- **The bridge must sit on the cameras' own network.** A routed VPN is not
  equivalent. Measured: on the same LAN a session reaches `CONNECTED` in about
  two seconds and the first frame arrives in ~0.13 s; across a VPN the same
  camera fails with `PPCS_Connect errorcode: -3` and never connects.

## Stream characteristics

Measured on current-generation hardware at the `low` quality setting:

- **Codec: H.265**, 848×480, ~21 fps
- Keyframe roughly every 2.8 seconds
- **VPS/SPS/PPS are re-sent before every keyframe**, so a restreamer does not
  need to cache parameter sets and replay them to late joiners. A client that
  attaches mid-stream can decode from the next keyframe.

Do not assume H.264 anywhere upstream of go2rtc: the camera itself never emits
it. Every H.264 variant this add-on publishes (see "Eight published streams")
is produced by re-encoding the camera's own H.265, not received from the
camera.

## Vendor SDK constraints

These are the parts most likely to cost time. All were verified against the SDK
source or by measurement.

**Argument order differs between the two start methods.**

```python
MIoTCamera.start_camera_async(did, pin_code=None, qualities=..., ...)
MIoTCameraInstance.start_async(qualities=..., pin_code=None, ...)
```

**Callbacks carry no frame metadata.**

```python
async def on_raw_video_async(did, data, ts, seq, channel)
async def on_decode_jpg_async(did, data, ts, channel)
```

The C frame header holding `codec_id` and `frame_type` is consumed inside the
SDK. Codec detection and keyframe classification therefore parse the bitstream
directly — see `bridge/nal.py`.

**Callback bodies run on the event loop**, not the SDK's thread: the SDK hands
them over with `asyncio.run_coroutine_threadsafe`. They contain no `await` and
so execute atomically within one loop step, which is what makes unsynchronised
access to shared state safe there. Adding an `await` to a callback breaks that
guarantee silently.

The SDK also discards the future it gets back, so an exception escaping a
callback produces no log output at all. Callbacks must catch their own errors.

**Register callbacks after `start_async`.** It creates the decoder threads.

**Never build a `camera_info` dict by hand.** `MIoTCameraInfo` has eleven
required fields; `create_camera_instance_async` validates against it. Pass the
object returned by `get_cameras_async()`.

**Device filtering must use the SDK's `is_camera_model()`.** It consults a
bundled table of allowed device classes plus an explicit deny list. Filtering by
MIoT spec URN looks equivalent but admits denied models. That deny list is not
merely cautious: an excluded model, given a session anyway, never completes the
connection.

**Complete OAuth through `MIoTClient.get_access_token_async`.** Using the lower
level OAuth client leaves `user_info` unset, which disables the MQTT event
channel with only a `mips setup skipped: no user uid` line to explain it. A
session restored from disk must call `get_user_info_async()` to reach parity.

**Access tokens last about three days.** Unattended refresh is mandatory, and a
token restored after a long shutdown is already due — refresh immediately rather
than waiting out a sleep interval.

**Exception messages contain credentials.** The SDK builds them from raw request
bodies and headers; for the OAuth endpoints that includes access and refresh
tokens. Everything that logs or returns a third-party error goes through
`bridge/redact.py`.

## A camera can be online and switched off

The lens has its own power property (`siid=2 piid=1`). When it is off, a session
still reaches `CONNECTED`, every API call still returns success, and **no frames
arrive at all** — indistinguishable from a broken stream unless the property is
read.

The bridge surfaces this as a distinct error, and the integration exposes the
switch as its own entity so the state is visible and automations can turn a
camera on before requesting a snapshot.

For the same reason, cached stills are age-checked: returning the last frame
from before power-off would show a live-looking picture of a room that no longer
matches reality.

## Security model

`host_network` disables Docker's port isolation entirely, so the **bind address
is the enforcement point**, not port mapping.

| Listener | Bind | Follows `access_mode` |
|---|---|---|
| Control plane (`bridge/api.py`) | loopback | **no** |
| go2rtc API, SRTP | loopback | no |
| go2rtc RTSP, WebRTC | per `access_mode` | yes |
| Account page | all interfaces as an add-on, otherwise per `access_mode` | standalone only |

The account page's live preview is served by the bridge itself, as multipart
JPEG built from the frames the vendor library already decodes for snapshots.

That is worth stating because the obvious design is wrong. Routing the preview
through go2rtc means asking ffmpeg to re-encode H.265 into MJPEG — go2rtc does
not transcode implicitly, so it needs a second source declared for it — and it
means relaying go2rtc's API through the page, which exposes `GET /api/config`:
the configuration file verbatim, RTSP credentials included, the one thing
`Restreamer.rtsp_url` deliberately refuses to disclose. All of that to deliver
frames that were already sitting in memory. It was built that way first, and it
never produced a picture.

The control plane has no authentication of its own and never follows
`access_mode`: it can serve live video, switch cameras off and unlink the
account. Only the streams a user deliberately publishes may move, and go2rtc
guards those with credentials — `access_mode: lan` refuses to start without
them, enforced in `Options.validate()` rather than in documentation.

Every go2rtc module is pinned explicitly. Modules left out of the configuration
fall back to go2rtc's defaults, several of which bind all interfaces.

The ingress UI cannot be bound to loopback because Supervisor's proxy reaches a
host-network add-on over the Docker bridge. It is gated on the headers
Supervisor injects instead.

## Two deployment modes

The image also runs standalone, under plain Docker, for users who want the RTSP
streams without Home Assistant. The integration was always the optional half --
nothing in the bridge calls into it -- but the account page was not reachable
outside Supervisor, which left the mode unusable in practice.

The mode is detected from `SUPERVISOR_TOKEN`, not configured. A user who sets
such a flag wrongly gets either a page they cannot open or one that is not
guarded at all, and neither failure announces itself.

| | Add-on | Standalone |
|---|---|---|
| Options source | `/data/options.json` | same, plus `XIAOMI_CAMERA_*` environment variables |
| Account page bind | all interfaces (forced) | per `access_mode` |
| Account page guard | ingress headers + peer address, or the password | none on loopback, password when published |
| Missing credentials | refused at startup, naming every missing field | the same |

The page is the only listener that cannot always follow the project's own rule
that the bind address does the work. Supervisor's ingress proxy reaches a
host-network add-on over the Docker bridge, so as an add-on it must accept
connections from off-loopback and be guarded instead. Standalone there is no
such constraint, so it follows `access_mode` like everything else — and with
the streams kept local it needs no password at all, for the same reason the
control plane never had one.

The ingress guard checks two things. The header says the request came through
ingress, which means Home Assistant authenticated whoever sent it. The peer
address says that claim came from Supervisor: the header is not a secret and
nothing signs it, so on its own it would let any machine on the local network
read the page by setting one header.

Add-on option translations must be named for Home Assistant's own language
codes: `translations/zh-Hans.yaml`, not `zh.yaml`. Supervisor keys them by
filename and the frontend looks them up by language code, so a mismatched name
is loaded, validated, and never shown.

Both guards live in `bridge/webauth.py`, apart from `bridge/api.py`, so they
can be tested: importing `api` pulls in the vendor SDK, which CI does not have,
and an authentication check nothing exercises is worth little.

A published standalone page needs a second check the add-on does not. Browsers
attach Basic credentials to cross-site requests on their own, so a page anywhere could POST
to `/api/unlink` and disconnect the account of anyone visiting it. Anything
that changes state therefore also has to carry a header, which a cross-site
form cannot set and a scripted request cannot obtain past the preflight.

`run.sh` must start with `#!/usr/bin/with-contenv bash`. s6-overlay keeps the
container's environment aside and exposes it only to processes started through
that wrapper; under a plain `env bash` every variable Supervisor sets is simply
absent. That shipped once: `SUPERVISOR_TOKEN` was invisible, so discovery never
announced the add-on and the bridge decided it was running standalone — inside
Home Assistant, asking for a password. Nothing logged an error, because every
one of those paths treats a missing token as a legitimate state.

Detection reads two signals, not one. `SUPERVISOR_TOKEN` alone would fail open:
a standalone deployment that inherited it from a shared compose file or an
exported shell would silently take the supervised branch, generate no password,
and serve the page to the network behind a header anyone can forge. Supervisor
also writes the options file, so both must be present.

`build_guards` has no unguarded branch: a reachable page with no credential to
check raises rather than being served. Two credentials are accepted wherever
they are available -- ingress, and the configured password -- rather than one
being selected by deployment, so the same configuration means the same thing
everywhere and a password set on an add-on does not lock anyone out of the
panel.

Supervisor's options schema has no conditional -- a field is optional or it is
not -- so "required only when `access_mode` is lan" cannot be stated there. The
screen will let that combination be saved, which leaves startup as the only
place to enforce it. `Options.validate` therefore names every missing field in
one message: reporting them one restart at a time makes a user fix the same
configuration twice.

An earlier version generated the missing credentials instead, to avoid the dead
end. It was dropped for uniformity. Generating a password the user must then
find in a log is worse than telling them to choose one, and one rule that holds
everywhere is easier to reason about than two that differ by deployment.

## Eight published streams

Each camera is published as eight variants: two codecs (H.265, H.264) at four
resolutions each (source, 720, 360, 180).

| Stream | Contents | For |
|---|---|---|
| `camera_<did>_h265` | the camera's own encoding, repackaged, never re-encoded | NVRs, Frigate, recording — anything that can decode H.265 and wants the source untouched |
| `camera_<did>_h264` | the same pictures re-encoded at source resolution | browsers, HomeKit, and anything else that cannot decode H.265 |
| `camera_<did>_h265_720` / `_h264_720` | re-encoded and scaled to 720p, 2M bitrate | consumers that want a smaller picture than the source but not a phone-sized one |
| `camera_<did>_h265_360` / `_h264_360` | scaled to 360p, 512k bitrate | bandwidth-constrained viewing |
| `camera_<did>_h265_180` / `_h264_180` | scaled to 180p, 256k bitrate | the most constrained consumers — thumbnails, slow links |

The H.264 variants exist because H.265 is where these cameras end and most
consumers begin. `VideoDecoder` is a SecureContext API and Home Assistant is
usually reached over plain HTTP; MSE decodes H.265 only where the hardware
happens to support it; HomeKit does not accept it at all. The symptom is not
an error but a picture that sits still, which is the hardest kind of failure
to explain.

The lower resolutions exist for the same underlying reason under a different
name: a viewer on a slow link or a small screen does not need the bitrate the
source was captured at, and asking it to decode that anyway wastes bandwidth
it may not have.

Distinct names rather than codecs or resolutions negotiated on one stream.
Offering more than one option on a single name leaves the choice to whatever
connects, so an NVR that accepts either codec could end up recording a
re-encode of a stream it could have copied untouched. The naming rule has no
exception: the codec is always part of the name, including for the camera's
own H.265 at source resolution (`camera_<did>_h265`, never a bare
`camera_<did>`). An earlier version omitted the codec for H.265 on the
grounds that it was the default, and that made the two 360p variants
indistinguishable in the entity picker — there was no way to tell
`camera_<did>_360` apart from `camera_<did>_h264_360` without opening it.

Every variant but the root is defined not against the add-on's HTTP endpoint
but against `camera_<did>_h265` itself — go2rtc reads one stream from
another by name. That means every derived stream, however many are open at
once, shares the one peer-to-peer session the root already holds with the
camera, instead of each variant opening its own.

Nothing is encoded until a consumer connects: go2rtc starts an `ffmpeg:`
producer for a variant on its first consumer and stops it after the last one
leaves. The root is the exception in the other direction — it is
`#video=copy`, repackaging only, so there is nothing to encode there at any
point.

The image ships ffmpeg's GPL build, and `libx264` and `libx265` are used
directly as go2rtc's encoder templates for the H.264 and H.265 variants —
they are the standard encoders for their formats. The Dockerfile greps the
built ffmpeg's encoder list for both after compiling it, so a build that loses
one fails there rather than when a user opens a camera.

The image shipped the LGPL build, which carries neither encoder, until this
branch. LGPL's weak copyleft (its library-linking exception) was enough for
that build to be acceptable, and that earlier choice rested on the add-on
only ever copying streams, never encoding them — a build without `libx264`
or `libx265` was enough to remux H.265 into RTSP. Publishing re-encoded and
rescaled variants stopped that being true: the standard encoders for both
formats are available only under GPL's strong copyleft, so the build had to
move with it.

The integration hands Home Assistant the URL for whichever variants a user
selects per camera; `Restreamer.rtsp_url` reports the root regardless of what
else is published.

## Preview

The add-on page shows a live picture, and where it comes from is a design
decision rather than an implementation detail.

The obvious source is the vendor library's own decoded-JPEG callback: it is
already there, it is what snapshots use, and it costs nothing extra. It is also
wrong, because it is a different circuit from the one anything depends on:

    on_raw_video -> /api/stream -> go2rtc -> RTSP     what consumers read
    on_decode_jpg -> snapshots                        a side channel

A preview drawn from the second keeps producing pictures while the first is
broken -- a green light wired to a different circuit. So the preview reads the
published stream instead: one ffmpeg per watched camera, pulling RTSP exactly
as an NVR would. A picture on the page is then evidence of the whole chain.

It is delivered as JPEG rather than as video because the browser usually
cannot decode H.265: `VideoDecoder` is a SecureContext API and Home Assistant
is commonly reached over plain HTTP, while the MSE fallback everyone uses does
not support H.265. That constrains the last hop only; the picture still comes
from the published stream.

It is delivered as multipart JPEG over one connection, at the camera's own
frame rate. With bandwidth not a constraint -- this runs on the same local
network as the cameras -- MJPEG wins on the things that are left: no round trip
or middleware pass per picture, no buffering latency, no decoder or codec
negotiation in the page, and intra-only frames with no motion artefacts. The
image now ships the encoders an H.264 fragment stream for MSE would need, so
that is no longer why MJPEG is used -- but MSE still buffers whole fragments
before playing them, which trades away the low latency a live preview exists
for, and WebCodecs still needs a secure context Home Assistant does not
reliably have. MJPEG remains the better fit on its own merits, not by default.

Its frame rate and quality are set on the page rather than in the add-on
options, and stored in `/data` (`bridge/preferences.py`). The split is between
settings that decide what the process binds to and who may reach it -- known
before a listener opens, changed only by restarting, and in one case protecting
the very page that would edit it -- and settings that are a viewing preference
with no bearing on exposure. Only the first kind belongs to the deployment.
Both deployments serve the same page, so this also removes the asymmetry where
a setting was edited on a screen under Supervisor and through an environment
variable under Docker.

Back-pressure is inherent. A slow viewer blocks the write; frames arriving
meanwhile replace each other, and the next write sends whatever is current. The
viewer sees a lower frame rate, never a growing backlog of stale pictures.

## Session lifetime

One peer-to-peer session per camera is shared by every user of it. Sessions are
reference-counted (`CameraSession._hold`) rather than keyed on whether a
consumer was registered — a snapshot needs frames without subscribing, and
tying lifetime to subscription leaks a session that nothing ever stops.

The session lingers briefly after the last user leaves so a reconnecting client
reuses it instead of paying the connect cost again.

Consumers are closed through an `asyncio.Event`, not a queue sentinel: the queue
is bounded, and a stalled consumer can fill it exactly when the session is torn
down, dropping the sentinel and stranding its reader.

## Testing

```
python -m pytest
```

The suite covers the pure logic — bitstream parsing, options validation and
credential redaction — and deliberately avoids mocking the SDK. A mock of a
closed-source library tests an assumption about it, not its behaviour.

The security-relevant invariants (which listener binds where, that `lan` mode
requires credentials, that credentials never survive redaction) are pinned by
tests so a regression fails the build rather than quietly widening exposure.

## Releasing

A release is one version for the whole repository, carried in three places
that different systems read:

| Where | Read by |
|---|---|
| `addon/config.yaml` | Supervisor, and the build's idempotence check |
| `custom_components/xiaomi_camera/manifest.json` | Home Assistant |
| the git tag `vX.Y.Z` and its GitHub release | HACS |

They drifted apart within a few releases when each was bumped where the work
happened to be, so `tests/test_versions.py` now fails the build if the first two
disagree.

The tag is what HACS actually uses. Without a release it installs from the
default branch, offers no version to choose, and never reports an update --
which is indistinguishable, from the user's side, from a project that has
stopped moving.

The release is created by the build workflow, after the image is published and
never before: a release names a version, and naming one whose image failed to
build points users at something that cannot run. Its notes are the matching
section of `addon/CHANGELOG.md`, verbatim — the same words, written for the
same reader, rather than a second set to keep in step. A version with no entry
there fails the job.

Two things must move together with any change under `addon/`:

1. **`version` in `addon/config.yaml`.** The build's idempotence check compares
   that version against what is already published; it does not hash the source.
   Leaving it unchanged means the build is skipped and the change silently never
   reaches an image, with a green CI run to suggest otherwise.
2. **`addon/CHANGELOG.md`.** Home Assistant shows it on the update screen, and
   its absence shows up there as "No changelog found".

Entries are for the person deciding whether to update, so they describe what
changed for them rather than which functions were touched.
