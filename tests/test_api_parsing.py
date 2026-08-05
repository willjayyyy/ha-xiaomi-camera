"""Reading the add-on's camera payload.

`api.py` is one of two integration modules that avoid importing Home
Assistant, which is what makes this testable at all.
"""

from __future__ import annotations

import sys

import pytest

# `api.py` itself imports no Home Assistant, but its package's `__init__.py`
# does, and Python runs that first. The interpreter gate is what makes the
# skip loud when `.venv314` is broken rather than silently green.
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="needs Python >= 3.14 and homeassistant; run under .venv314",
)

# Guarded the same way as the marker above, not with `importorskip`: below
# 3.14 the import is skipped on purpose (nothing to run); at or above 3.14 it
# is unconditional, so a broken `.venv314` install fails collection loudly
# instead of reporting a misleading skip.
if sys.version_info >= (3, 14):
    from custom_components.xiaomi_camera.api import BridgeCamera

_BASE = {
    "did": "42",
    "name": "Living room",
    "model": "chuangmi.camera.81ac1",
    "online": True,
}


class TestStreamParsing:
    def test_declared_streams_are_read(self) -> None:
        camera = BridgeCamera.from_dict(
            {
                **_BASE,
                "streams": [
                    {"key": "h265", "codec": "h265", "height": None, "url": "rtsp://a"},
                    {
                        "key": "h264_360",
                        "codec": "h264",
                        "height": 360,
                        "url": "rtsp://b",
                    },
                ],
            }
        )
        assert [s.key for s in camera.streams] == ["h265", "h264_360"]
        assert camera.stream_url("h264_360") == "rtsp://b"

    def test_an_add_on_without_streams_reports_none(self) -> None:
        """Older add-on. Reported as absent rather than guessed at.

        Inventing a stream list from the two legacy URLs would mean carrying a
        second way of finding streams forever, and would leave the user
        wondering why the new options never appeared.
        """
        camera = BridgeCamera.from_dict(
            {**_BASE, "rtsp_url": "rtsp://a", "rtsp_url_h264": "rtsp://b"}
        )
        assert camera.streams == ()

    def test_an_unknown_stream_key_has_no_url(self) -> None:
        camera = BridgeCamera.from_dict({**_BASE, "streams": []})
        assert camera.stream_url("h264") == ""
