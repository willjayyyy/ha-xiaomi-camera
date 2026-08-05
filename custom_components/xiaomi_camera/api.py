"""Client for the bridge add-on's control plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import HTTP_CAMERA_OFF


class BridgeError(Exception):
    """The bridge could not be reached or returned an error."""


class BridgeNotLinkedError(BridgeError):
    """The bridge is running but no Xiaomi account is linked to it."""


class CameraOffError(BridgeError):
    """The camera is switched off, so it produces no video."""


@dataclass(frozen=True)
class CameraStream:
    """One published variant of a camera's video, as the add-on describes it."""

    key: str
    codec: str
    height: int | None
    url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraStream:
        height = data.get("height")
        return cls(
            key=str(data.get("key") or ""),
            codec=str(data.get("codec") or ""),
            height=int(height) if height is not None else None,
            url=str(data.get("url") or ""),
        )


@dataclass(frozen=True)
class BridgeCamera:
    """A camera as reported by the bridge."""

    did: str
    name: str
    model: str
    manufacturer: str
    channel_count: int
    online: bool
    lan_online: bool
    powered_on: bool | None
    rtsp_url: str
    #: The same pictures re-encoded as H.264. Empty on an older add-on, which
    #: is why nothing may assume it is there.
    rtsp_url_h264: str = ""
    #: Every variant the add-on publishes. Empty on an add-on that predates
    #: this list, which the integration reports rather than working around --
    #: see `coordinator._async_report_outdated_addon`.
    streams: tuple[CameraStream, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BridgeCamera:
        return cls(
            did=str(data["did"]),
            name=str(data.get("name") or f"Camera {data['did']}"),
            model=str(data.get("model") or "unknown"),
            manufacturer=str(data.get("manufacturer") or "Xiaomi"),
            channel_count=int(data.get("channel_count") or 1),
            online=bool(data.get("online")),
            lan_online=bool(data.get("lan_online")),
            powered_on=data.get("powered_on"),
            rtsp_url=str(data.get("rtsp_url") or ""),
            rtsp_url_h264=str(data.get("rtsp_url_h264") or ""),
            streams=tuple(
                CameraStream.from_dict(item) for item in data.get("streams") or ()
            ),
        )

    def stream_url(self, key: str) -> str:
        """The URL for one variant, or empty if it is not published."""
        for stream in self.streams:
            if stream.key == key:
                return stream.url
        return ""


class BridgeClient:
    """Talks to the add-on over HTTP."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return self._base

    async def async_health(self) -> dict[str, Any]:
        return await self._get_json("/api/health")

    async def async_cameras(self) -> list[BridgeCamera]:
        payload = await self._get_json("/api/cameras")
        return [BridgeCamera.from_dict(item) for item in payload.get("cameras", [])]

    async def async_snapshot(self, did: str) -> bytes:
        """Fetch a still image.

        Snapshots are slower than a typical HTTP call because the bridge may
        need to establish a peer-to-peer session and wait for a keyframe.
        """
        url = f"{self._base}/api/snapshot/{did}"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=25)
            ) as response:
                if response.status == HTTP_CAMERA_OFF:
                    raise CameraOffError(await response.text())
                response.raise_for_status()
                return await response.read()
        except aiohttp.ClientError as err:
            raise BridgeError(f"snapshot request failed: {err}") from err

    async def async_set_power(self, did: str, value: bool) -> None:
        url = f"{self._base}/api/cameras/{did}/power"
        try:
            async with self._session.post(
                url, json={"value": value}, timeout=aiohttp.ClientTimeout(total=20)
            ) as response:
                response.raise_for_status()
        except aiohttp.ClientError as err:
            raise BridgeError(f"could not switch camera: {err}") from err

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{self._base}{path}", timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 503:
                    payload = await response.json()
                    raise BridgeNotLinkedError(
                        payload.get("message", "Xiaomi account is not linked")
                    )
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as err:
            raise BridgeError(f"request to {path} failed: {err}") from err
