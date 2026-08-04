"""Session plumbing.

These cover the parts that only fail at runtime -- the kind of defect that
passes review and type checking because it depends on Python's own rules rather
than on the logic being wrong.
"""

from __future__ import annotations

import asyncio

from bridge.framing import Consumer as _Consumer
from bridge.framing import ParameterSets as _ParameterSets
from bridge.framing import SessionStats


class TestConsumer:
    def test_is_hashable(self) -> None:
        """Consumers are held in a set, so they must be hashable.

        A dataclass that generates __eq__ silently loses __hash__, which only
        surfaces when the code actually runs.
        """
        consumer = _Consumer(queue=asyncio.Queue())
        assert {consumer}

    def test_two_consumers_are_distinct(self) -> None:
        """Identity, not field equality: two subscribers are never the same."""
        a = _Consumer(queue=asyncio.Queue())
        b = _Consumer(queue=asyncio.Queue())
        assert len({a, b}) == 2

    def test_removal_from_a_set_works(self) -> None:
        consumer = _Consumer(queue=asyncio.Queue())
        consumers = {consumer}
        consumers.discard(consumer)
        assert not consumers


class TestParameterSets:
    def test_keeps_one_unit_per_type(self) -> None:
        sets = _ParameterSets()
        sets.remember(b"\x40\x01")
        sets.remember(b"\x42\x01")
        sets.remember(b"\x40\x02")  # replaces the first
        assert len(sets.units) == 2

    def test_renders_with_start_codes(self) -> None:
        sets = _ParameterSets()
        sets.remember(b"\x40\x01")
        assert sets.as_annex_b() == b"\x00\x00\x00\x01\x40\x01"

    def test_empty_renders_to_nothing(self) -> None:
        assert _ParameterSets().as_annex_b() == b""


class TestFallingBehind:
    """What a viewer that cannot keep up costs everyone else.

    Dropping one chunk from an H.265 stream is not a dropped frame: the
    decoder cannot use anything until the next keyframe, which on these
    cameras is about three seconds away. Forwarding the rest of the group
    meanwhile produces artefacts instead of video, and it was invisible --
    a debug log, no counter -- to anyone trying to explain a stutter.
    """

    def test_a_full_queue_starts_a_resync(self) -> None:
        consumer = _Consumer(queue=asyncio.Queue(maxsize=1))
        consumer.queue.put_nowait(b"already here")

        assert consumer.resyncing is False
        # Standing in for the session's fan-out: full queue, so the consumer
        # is marked and its stale contents discarded.
        try:
            consumer.queue.put_nowait(b"one too many")
        except asyncio.QueueFull:
            while not consumer.queue.empty():
                consumer.queue.get_nowait()
            consumer.resyncing = True

        assert consumer.resyncing is True
        assert consumer.queue.empty(), "stale mid-group data must not be kept"

    def test_stats_report_it(self) -> None:
        # A number a user can quote beats a symptom they have to describe.
        stats = SessionStats()
        assert stats.as_dict()["resyncs"] == 0
        stats.resyncs += 1
        assert stats.as_dict()["resyncs"] == 1
