"""Which streams become entities, decided without importing Home Assistant.

This module exists so the decision is testable at all. CI installs no
`homeassistant`, and the integration modules that would otherwise hold this
logic all import it -- which is how the add-on's own session handling went
untested until two faults surfaced in production.
"""

from __future__ import annotations

from xiaomi_camera.streams import ROOT_KEY, selected_streams, unique_id


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
        """An entry migrated from `original` has hevc as its primary."""
        assert unique_id("42", "hevc", primary_key="hevc") == "42"
        assert unique_id("42", "h264", primary_key="hevc") == "42_h264"


class TestSelection:
    def test_an_unconfigured_camera_gets_the_root_stream(self) -> None:
        """The camera's own encoding, nothing chosen on the user's behalf."""
        assert selected_streams({}, "42", available=["hevc", "h264"]) == [ROOT_KEY]

    def test_a_configured_camera_gets_what_was_ticked(self) -> None:
        options = {"camera_streams": {"42": ["h264", "h264_360"]}}
        assert selected_streams(options, "42", ["hevc", "h264", "h264_360"]) == [
            "h264",
            "h264_360",
        ]

    def test_streams_the_add_on_no_longer_publishes_are_dropped(self) -> None:
        """A downgraded add-on must not leave permanently broken entities."""
        options = {"camera_streams": {"42": ["h264", "h264_720"]}}
        assert selected_streams(options, "42", ["h264"]) == ["h264"]

    def test_order_follows_the_add_ons_order_not_the_users(self) -> None:
        """Entity creation order stays stable across reconfigurations."""
        options = {"camera_streams": {"42": ["h264_360", "hevc"]}}
        assert selected_streams(options, "42", ["hevc", "h264_360"]) == [
            "hevc",
            "h264_360",
        ]
