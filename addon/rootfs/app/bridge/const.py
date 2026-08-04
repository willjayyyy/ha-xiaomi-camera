"""Constants shared across the bridge."""

from __future__ import annotations

from typing import Final

# Where Supervisor persists add-on state across restarts and updates.
DATA_DIR: Final = "/data"
OPTIONS_FILE: Final = f"{DATA_DIR}/options.json"
CREDENTIALS_FILE: Final = f"{DATA_DIR}/credentials.json"
CACHE_DIR: Final = f"{DATA_DIR}/cache"

# The control plane is machine-facing: it serves the raw video stream to the
# restreamer and answers the integration's queries. It is ALWAYS bound to
# loopback and never follows `access_mode` -- it has no authentication of its
# own, and publishing it would hand out live video, camera power control and
# session teardown to anyone on the network.
API_PORT: Final = 8099

# The ingress UI is served separately because Supervisor's ingress proxy runs
# in its own container and reaches a host-network add-on over the Docker bridge,
# not over loopback. Requests arriving here must carry Supervisor's ingress
# headers -- see `bridge.api`.
INGRESS_PORT: Final = 8098

# Only these follow `access_mode`: they are the streams a user may deliberately
# publish to other machines, and go2rtc protects them with credentials.
RTSP_PORT: Final = 8554
WEBRTC_PORT: Final = 8555
#: go2rtc's SRTP listener. Pinned rather than left to its default, which binds
#: every interface.
SRTP_PORT: Final = 8443

GO2RTC_API_PORT: Final = 1984

LOOPBACK: Final = "127.0.0.1"
ALL_INTERFACES: Final = "0.0.0.0"

# Xiaomi cloud region. Only "cn" has been verified; other regions use the same
# code path with a region-prefixed host.
DEFAULT_CLOUD_SERVER: Final = "cn"

# Registered with Xiaomi's OAuth2 service by the upstream SDK. Changing it
# requires registering an application of your own with Xiaomi.
OAUTH_REDIRECT_URI: Final = "https://127.0.0.1"

# Access tokens last roughly three days. Refresh well before expiry so a
# transient network failure has room for retries.
TOKEN_REFRESH_MARGIN_SECONDS: Final = 12 * 3600
TOKEN_REFRESH_RETRY_SECONDS: Final = 300

# Camera power switch, as defined by the MIoT spec for the camera-control
# service. A camera that is online but switched off completes a peer-to-peer
# session and then sends no frames at all, so this has to be surfaced.
POWER_SIID: Final = 2
POWER_PIID: Final = 1

# How long to wait for a peer-to-peer session before declaring failure. Local
# network sessions establish in about two seconds; this leaves room for a
# congested network without hanging a snapshot request indefinitely.
CONNECT_TIMEOUT_SECONDS: Final = 30

# Cameras deliver a keyframe roughly every three seconds, and a decoder cannot
# produce a picture before one arrives.
SNAPSHOT_TIMEOUT_SECONDS: Final = 15

# Discovery service name announced to Supervisor; must match the integration's
# domain so Home Assistant routes it to the right config flow.
DISCOVERY_SERVICE: Final = "xiaomi_camera"
