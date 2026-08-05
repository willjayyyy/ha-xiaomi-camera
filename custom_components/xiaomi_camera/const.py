"""Constants for the Xiaomi Camera integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "xiaomi_camera"

#: Slug of the companion add-on. The integration can install and start it on
#: Home Assistant OS so the user never has to set it up separately.
ADDON_SLUG: Final = "xiaomi_camera_bridge"
ADDON_NAME: Final = "Xiaomi Camera Bridge"

#: Which cameras to bring into Home Assistant, by device id. Absent means all
#: of them -- an installation made before signing in has no list to store, and
#: should not end up with nothing once the cameras appear.
CONF_CAMERAS: Final = "cameras"

#: The cameras deliberately left out. Kept alongside the list above rather than
#: derived from it: which one applies depends on `CONF_AUTO_ADD`, and a camera
#: that appears later is only knowable as "not excluded".
CONF_EXCLUDED: Final = "excluded_cameras"

#: Whether a camera that shows up later joins on its own. On by default: the
#: common case is wanting the cameras you own, and noticing a new one is
#: missing is harder than noticing one you did not want.
CONF_AUTO_ADD: Final = "auto_add"

#: Per-camera stream selection: {did: [stream key, ...]}. Replaces the
#: entry-wide `stream_codec` option, which could only say one thing for every
#: camera at once.
CONF_CAMERA_STREAMS: Final = "camera_streams"

#: Which stream key the bare `<did>` entity is bound to, per entry. Set once
#: by the migration and never changed: it is what keeps existing entities
#: identical across the upgrade.
CONF_PRIMARY_STREAM: Final = "primary_stream"

CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8099

#: Cameras rarely appear or disappear, and each poll costs a cloud round trip
#: inside the bridge. Live state arrives with the stream itself, not from here.
UPDATE_INTERVAL: Final = timedelta(seconds=60)

#: A switched-off camera completes a session and then sends nothing, so the
#: bridge answers snapshot requests for it with a conflict rather than a
#: timeout. Surfacing that distinction is what tells a user why the picture is
#: missing.
HTTP_CAMERA_OFF: Final = 409

ATTR_MODEL: Final = "model"
ATTR_POWERED_ON: Final = "powered_on"
ATTR_LAN_ONLINE: Final = "lan_online"
