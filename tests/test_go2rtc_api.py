"""Delivering stream changes to a running go2rtc.

The generated configuration file is the source of truth. This is a delivery
mechanism that avoids interrupting live viewers, never a second place where
state lives: killing go2rtc and letting it rebuild from the files must
produce exactly what these calls produced.
"""

from __future__ import annotations

import pytest
from bridge.go2rtc_api import Go2rtcApi


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self) -> str:
        return ""


class _FakeSession:
    def __init__(self, statuses: dict[str, int]) -> None:
        self._statuses = statuses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url))
        return _FakeResponse(self._statuses.get(method, 200))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_a_successful_patch_needs_no_put():
    session = _FakeSession({"PATCH": 200})
    api = Go2rtcApi(session_factory=lambda: session)
    assert await api.set_stream("camera_aaa", "http://x/") is True
    assert [method for method, _ in session.calls] == ["PATCH"]


@pytest.mark.asyncio
async def test_a_rejected_patch_falls_back_to_put():
    session = _FakeSession({"PATCH": 400, "PUT": 200})
    api = Go2rtcApi(session_factory=lambda: session)
    assert await api.set_stream("camera_aaa", "http://x/") is True
    assert [method for method, _ in session.calls] == ["PATCH", "PUT"]


@pytest.mark.asyncio
async def test_both_failing_reports_failure_rather_than_pretending():
    """The caller restarts go2rtc on False. Reporting success here would leave
    the running process disagreeing with the file that describes it."""
    session = _FakeSession({"PATCH": 400, "PUT": 500})
    api = Go2rtcApi(session_factory=lambda: session)
    assert await api.set_stream("camera_aaa", "http://x/") is False


@pytest.mark.asyncio
async def test_a_connection_failure_is_a_failure_not_an_exception():
    class _Broken(_FakeSession):
        def request(self, method: str, url: str, **kwargs):
            raise OSError("connection refused")

    api = Go2rtcApi(session_factory=lambda: _Broken({}))
    assert await api.set_stream("camera_aaa", "http://x/") is False


@pytest.mark.asyncio
async def test_the_stream_name_and_source_are_sent_as_query_parameters():
    session = _FakeSession({"PATCH": 200})
    api = Go2rtcApi(session_factory=lambda: session)
    await api.set_stream("camera_aaa", "http://host/api/stream/aaa")
    _, url = session.calls[0]
    assert "name=camera_aaa" in url
    assert "src=http%3A%2F%2Fhost%2Fapi%2Fstream%2Faaa" in url


@pytest.mark.asyncio
async def test_replacing_a_stream_deletes_before_putting():
    """DELETE drops the consumers; PUT gives them something to redial into.
    PUT alone would leave every current viewer on the old settings."""
    session = _FakeSession({"DELETE": 200, "PUT": 200})
    api = Go2rtcApi(session_factory=lambda: session)
    assert await api.replace_stream("camera_aaa", "http://x/") is True
    assert [method for method, _ in session.calls] == ["DELETE", "PUT"]
