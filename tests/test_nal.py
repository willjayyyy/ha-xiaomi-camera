"""Annex-B NAL parsing.

Expected values are derived from the codec specifications rather than from the
implementation: H.264 puts the unit type in the low five bits of the first byte,
H.265 in bits 1..6 of it. Deriving them the other way round would let a
misreading of the spec pass unnoticed, since the implementation and the test
would agree with each other while both being wrong.
"""

from __future__ import annotations

import pytest
from bridge.nal import (
    Codec,
    detect_codec,
    is_keyframe,
    is_parameter_set,
    iter_nal_units,
    nal_type,
)

SC3 = b"\x00\x00\x01"
SC4 = b"\x00\x00\x00\x01"


class TestIterNalUnits:
    def test_splits_on_four_byte_start_code(self) -> None:
        data = SC4 + b"\x67\xaa" + SC4 + b"\x65\xbb\xcc"
        assert list(iter_nal_units(data)) == [b"\x67\xaa", b"\x65\xbb\xcc"]

    def test_splits_on_three_byte_start_code(self) -> None:
        data = SC3 + b"\x67\xaa" + SC3 + b"\x41\xbb"
        assert list(iter_nal_units(data)) == [b"\x67\xaa", b"\x41\xbb"]

    def test_handles_mixed_start_codes(self) -> None:
        data = SC4 + b"\x67" + SC3 + b"\x68" + SC4 + b"\x65"
        assert list(iter_nal_units(data)) == [b"\x67", b"\x68", b"\x65"]

    def test_four_byte_code_is_not_split_as_three_byte(self) -> None:
        """A four-byte start code must not be misread as three bytes.

        Its first three bytes are ``00 00 00``, which does not match the
        three-byte pattern; a scanner that checked the last three bytes instead
        would emit an extra empty unit here.
        """
        assert list(iter_nal_units(SC4 + b"\x65\x01")) == [b"\x65\x01"]

    def test_ignores_leading_bytes_before_first_start_code(self) -> None:
        assert list(iter_nal_units(b"\xde\xad" + SC4 + b"\x65")) == [b"\x65"]

    def test_returns_nothing_without_a_start_code(self) -> None:
        assert list(iter_nal_units(b"\xde\xad\xbe\xef")) == []

    def test_empty_input(self) -> None:
        assert list(iter_nal_units(b"")) == []

    def test_skips_empty_units_from_adjacent_start_codes(self) -> None:
        assert list(iter_nal_units(SC4 + SC4 + b"\x65")) == [b"\x65"]

    def test_last_unit_runs_to_end_of_buffer(self) -> None:
        assert list(iter_nal_units(SC4 + b"\x65\x01\x02\x03")) == [b"\x65\x01\x02\x03"]


class TestNalType:
    @pytest.mark.parametrize(
        ("byte", "expected"),
        [(b"\x65", 5), (b"\x41", 1), (b"\x67", 7), (b"\x68", 8)],
    )
    def test_h264_reads_low_five_bits(self, byte: bytes, expected: int) -> None:
        assert nal_type(byte, Codec.H264) == expected

    @pytest.mark.parametrize(
        ("byte", "expected"),
        [(b"\x40", 32), (b"\x42", 33), (b"\x44", 34), (b"\x26", 19)],
    )
    def test_h265_reads_bits_one_to_six(self, byte: bytes, expected: int) -> None:
        assert nal_type(byte, Codec.H265) == expected

    def test_empty_unit_returns_sentinel(self) -> None:
        """Real streams contain zero-length units; they must not raise."""
        assert nal_type(b"", Codec.H264) == -1
        assert nal_type(b"", Codec.H265) == -1


class TestClassification:
    def test_h264_parameter_sets(self) -> None:
        assert is_parameter_set(7, Codec.H264)
        assert is_parameter_set(8, Codec.H264)
        assert not is_parameter_set(5, Codec.H264)

    def test_h265_parameter_sets(self) -> None:
        for unit_type in (32, 33, 34):
            assert is_parameter_set(unit_type, Codec.H265)
        assert not is_parameter_set(19, Codec.H265)

    def test_h264_keyframe_is_idr_only(self) -> None:
        assert is_keyframe(5, Codec.H264)
        assert not is_keyframe(1, Codec.H264)

    def test_h265_keyframe_covers_the_irap_range(self) -> None:
        assert is_keyframe(19, Codec.H265)
        assert is_keyframe(20, Codec.H265)
        assert is_keyframe(16, Codec.H265)
        assert is_keyframe(23, Codec.H265)
        assert not is_keyframe(15, Codec.H265)
        assert not is_keyframe(24, Codec.H265)
        assert not is_keyframe(1, Codec.H265)


class TestDetectCodec:
    def test_detects_h264_from_its_parameter_sets(self) -> None:
        data = SC4 + b"\x67\x42" + SC4 + b"\x68\xce" + SC4 + b"\x65\x88"
        assert detect_codec(data) is Codec.H264

    def test_detects_h265_from_its_parameter_sets(self) -> None:
        data = SC4 + b"\x40\x01" + SC4 + b"\x42\x01" + SC4 + b"\x44\x01"
        assert detect_codec(data) is Codec.H265

    def test_returns_none_without_parameter_sets(self) -> None:
        """A mid-stream chunk carries no parameter sets.

        Returning ``None`` lets the caller keep feeding data instead of locking
        in a guess that would mislabel the whole session.
        """
        assert detect_codec(SC4 + b"\x01\x02\x03") is None

    def test_returns_none_for_empty_input(self) -> None:
        assert detect_codec(b"") is None
