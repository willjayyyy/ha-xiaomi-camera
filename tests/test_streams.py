"""Which streams become entities.

`streams.py` itself never imports Home Assistant, but importing it by its real
package path (`custom_components.xiaomi_camera.streams`) runs the package's
`__init__.py` first, which does. That needs `homeassistant`, available under
`.venv314` but not under `.venv311` -- so this module is skipped there rather
than erroring at collection.

Gated on the interpreter version, not on whether the import happens to
succeed: a `.venv314` install that is present but broken should fail this
module loudly, not be indistinguishable from "not installed here on
purpose".
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="needs Python >= 3.14 and homeassistant; run under .venv314",
)

# Guarded the same way as the marker above, not with `importorskip`: below
# 3.14 the import is skipped on purpose (nothing to run); at or above 3.14 it
# is unconditional, so a broken `.venv314` install fails collection loudly
# instead of reporting a misleading skip.
if sys.version_info >= (3, 14):
    from custom_components.xiaomi_camera.streams import (
        ROOT_KEY,
        migrate_options,
        selected_streams,
        unique_id,
        wanted_unique_ids,
    )


class TestUniqueId:
    """The primary entity's identity is permanent.

    Home Assistant treats `unique_id` as the entity's identity. Changing it
    abandons the old entity and everything attached to it -- HomeKit pairings,
    automations, dashboard cards, history.
    """

    def test_the_primary_stream_keeps_the_bare_device_id(self) -> None:
        assert unique_id("42", "h264", primary_key="h264") == "42"

    def test_other_streams_are_suffixed(self) -> None:
        assert unique_id("42", "h264_360", primary_key="h264") == "42_h264_360"

    def test_which_stream_is_primary_depends_on_the_entry(self) -> None:
        """An entry migrated from `original` has h265 as its primary."""
        assert unique_id("42", "h265", primary_key="h265") == "42"
        assert unique_id("42", "h264", primary_key="h265") == "42_h264"


class TestSelection:
    def test_an_unconfigured_camera_gets_the_root_stream(self) -> None:
        """The camera's own encoding, nothing chosen on the user's behalf."""
        assert selected_streams({}, "42", available=["h265", "h264"]) == [ROOT_KEY]

    def test_a_configured_camera_gets_what_was_ticked(self) -> None:
        options = {"camera_streams": {"42": ["h264", "h264_360"]}}
        assert selected_streams(options, "42", ["h265", "h264", "h264_360"]) == [
            "h264",
            "h264_360",
        ]

    def test_streams_the_add_on_no_longer_publishes_are_dropped(self) -> None:
        """A downgraded add-on must not leave permanently broken entities."""
        options = {"camera_streams": {"42": ["h264", "h264_720"]}}
        assert selected_streams(options, "42", ["h264"]) == ["h264"]

    def test_order_follows_the_add_ons_order_not_the_users(self) -> None:
        """Entity creation order stays stable across reconfigurations."""
        options = {"camera_streams": {"42": ["h264_360", "h265"]}}
        assert selected_streams(options, "42", ["h265", "h264_360"]) == [
            "h265",
            "h264_360",
        ]


class TestMigration:
    """Upgrading must not change what a working entity plays."""

    def test_h264_entries_keep_playing_h264(self) -> None:
        migrated = migrate_options({"stream_codec": "h264"}, ["42"])
        assert migrated["primary_stream"] == "h264"
        assert migrated["camera_streams"]["42"] == ["h264"]

    def test_original_entries_keep_playing_h265(self) -> None:
        migrated = migrate_options({"stream_codec": "original"}, ["42"])
        assert migrated["primary_stream"] == "h265"
        assert migrated["camera_streams"]["42"] == ["h265"]

    def test_entries_predating_the_option_are_treated_as_h264(self) -> None:
        """`camera.py` has always defaulted to H.264 when the key is absent.

        Binding these to h265 because that is the new default would change
        what an already-working entity plays, on upgrade, unasked.
        """
        migrated = migrate_options({}, ["42"])
        assert migrated["primary_stream"] == "h264"

    def test_the_old_option_is_removed(self) -> None:
        """A field nobody reads is one every later reader has to think about."""
        migrated = migrate_options({"stream_codec": "h264"}, ["42"])
        assert "stream_codec" not in migrated

    def test_unrelated_options_survive(self) -> None:
        migrated = migrate_options(
            {"stream_codec": "h264", "auto_add": False, "excluded_cameras": ["7"]},
            ["42"],
        )
        assert migrated["auto_add"] is False
        assert migrated["excluded_cameras"] == ["7"]


class TestEntityCleanup:
    def test_it_lists_an_identity_per_selected_stream(self) -> None:
        options = {
            "camera_streams": {"42": ["h264", "h264_360"]},
            "primary_stream": "h264",
        }
        assert wanted_unique_ids(options, {"42": ["h264", "h264_360"]}) == {
            "42",
            "42_h264_360",
        }

    def test_an_unticked_stream_drops_out(self) -> None:
        options = {"camera_streams": {"42": ["h264"]}, "primary_stream": "h264"}
        assert wanted_unique_ids(options, {"42": ["h264", "h264_360"]}) == {"42"}

    def test_a_camera_with_no_entry_falls_back_to_the_root(self) -> None:
        options = {"primary_stream": "h265"}
        assert wanted_unique_ids(options, {"42": ["h265", "h264"]}) == {"42"}
