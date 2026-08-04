"""H.264 / H.265 Annex-B NAL unit parsing.

The Xiaomi camera SDK delivers raw video as opaque byte chunks: its callback
signature is ``(did, data, ts, seq, channel)`` and carries no codec identifier
and no frame type. Both pieces of information are needed downstream -- the
codec to configure the restreamer, the frame type to know where a decodable
sequence begins -- so they are recovered here by parsing the bitstream itself.

In Annex-B framing every NAL unit is preceded by a start code of either
``00 00 01`` or ``00 00 00 01``, and the byte that follows encodes the unit
type. The two codecs place that field differently:

* H.264 -- low 5 bits of the first byte
* H.265 -- bits 1..6 of the first byte

A decoder cannot produce a picture until it has received the parameter sets
(SPS/PPS, plus VPS for H.265) followed by an IRAP picture. Callers use
:func:`is_parameter_set` and :func:`is_keyframe` to locate that point.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum

__all__ = [
    "Codec",
    "detect_codec",
    "is_keyframe",
    "is_parameter_set",
    "iter_nal_units",
    "nal_type",
]

_START_CODE_3 = b"\x00\x00\x01"
_START_CODE_4 = b"\x00\x00\x00\x01"

# H.264 (ITU-T H.264 Table 7-1)
_H264_NON_IDR = 1
_H264_IDR = 5
_H264_SPS = 7
_H264_PPS = 8

# H.265 (ITU-T H.265 Table 7-1)
_H265_IRAP_FIRST = 16  # BLA_W_LP
_H265_IRAP_LAST = 23  # RSV_IRAP_VCL23
_H265_VPS = 32
_H265_SPS = 33
_H265_PPS = 34

_INVALID_NAL_TYPE = -1


class Codec(StrEnum):
    """Video codec carried by a raw stream."""

    H264 = "h264"
    H265 = "h265"


def iter_nal_units(data: bytes) -> Iterator[bytes]:
    """Split an Annex-B buffer into NAL units, without their start codes.

    Handles three- and four-byte start codes, including a mix of both within
    the same buffer. Leading bytes that precede the first start code are
    discarded, and empty units (produced by adjacent start codes) are skipped.

    Args:
        data: Annex-B framed bitstream, typically one SDK callback payload.

    Yields:
        Each NAL unit's payload, start code stripped.
    """
    starts: list[tuple[int, int]] = []
    index = 0
    end = len(data)

    while index < end - 2:
        if data[index] == 0 and data[index + 1] == 0:
            if data[index + 2] == 1:
                starts.append((index, 3))
                index += 3
                continue
            if index < end - 3 and data[index + 2] == 0 and data[index + 3] == 1:
                starts.append((index, 4))
                index += 4
                continue
        index += 1

    for position, (offset, size) in enumerate(starts):
        payload_start = offset + size
        payload_end = starts[position + 1][0] if position + 1 < len(starts) else end
        unit = data[payload_start:payload_end]
        if unit:
            yield unit


def nal_type(unit: bytes, codec: Codec) -> int:
    """Return the NAL unit type, or ``-1`` for an empty unit.

    Real streams occasionally contain zero-length units; returning a sentinel
    rather than raising keeps a single malformed unit from aborting capture.
    """
    if not unit:
        return _INVALID_NAL_TYPE
    if codec is Codec.H264:
        return unit[0] & 0x1F
    return (unit[0] >> 1) & 0x3F


def is_parameter_set(unit_type: int, codec: Codec) -> bool:
    """Whether the unit is a parameter set (SPS/PPS, and VPS for H.265).

    Parameter sets must reach a decoder before any picture can be decoded. When
    a camera sends them only once at session start, a restreamer has to cache
    and replay them for every client that joins later.
    """
    if codec is Codec.H264:
        return unit_type in (_H264_SPS, _H264_PPS)
    return unit_type in (_H265_VPS, _H265_SPS, _H265_PPS)


def is_keyframe(unit_type: int, codec: Codec) -> bool:
    """Whether the unit starts an independently decodable picture.

    For H.264 this is an IDR slice; for H.265, any IRAP picture.
    """
    if codec is Codec.H264:
        return unit_type == _H264_IDR
    return _H265_IRAP_FIRST <= unit_type <= _H265_IRAP_LAST


def detect_codec(data: bytes) -> Codec | None:
    """Infer the codec from a buffer's parameter sets.

    Both interpretations are scored against the same units and the one whose
    parameter sets appear is chosen. Returns ``None`` when the buffer contains
    no parameter set, which happens for a mid-stream chunk; callers should keep
    feeding data until detection succeeds rather than guessing.
    """
    units = list(iter_nal_units(data))
    if not units:
        return None

    h264_hits = sum(
        1 for unit in units if is_parameter_set(nal_type(unit, Codec.H264), Codec.H264)
    )
    h265_hits = sum(
        1 for unit in units if is_parameter_set(nal_type(unit, Codec.H265), Codec.H265)
    )

    if h264_hits == h265_hits:
        return None
    return Codec.H264 if h264_hits > h265_hits else Codec.H265
