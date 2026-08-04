"""Announce this add-on to Home Assistant through Supervisor.

Publishing a discovery record is what turns "install the add-on, then go find
the integration and type in a host and port" into a notification the user
confirms with one click. Home Assistant routes the record to the config flow of
the integration whose domain matches the service name.

Discovery is best-effort by design: the bridge is fully usable without it (the
integration also accepts a manually entered address), so a Supervisor that is
unavailable or an older core that does not understand the service must never
prevent startup.
"""

from __future__ import annotations

import logging
import os

import aiohttp

from .const import API_PORT, DISCOVERY_SERVICE, LOOPBACK

_LOGGER = logging.getLogger(__name__)

_SUPERVISOR_URL = "http://supervisor"
_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def async_announce() -> str | None:
    """Publish a discovery record. Returns its uuid, or ``None`` on failure."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        _LOGGER.debug(
            "SUPERVISOR_TOKEN is not set; skipping discovery. This is expected "
            "when running outside Home Assistant OS."
        )
        return None

    payload = {
        "service": DISCOVERY_SERVICE,
        "config": {
            # Home Assistant shares the host network namespace with add-ons, so
            # loopback is reachable from the integration and keeps the control
            # plane off the LAN.
            "host": LOOPBACK,
            "port": API_PORT,
        },
    }

    try:
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.post(
                f"{_SUPERVISOR_URL}/discovery",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            ) as response,
        ):
            if response.status not in (200, 201):
                body = await response.text()
                _LOGGER.warning(
                    "Discovery announcement rejected (%s): %s",
                    response.status,
                    body.strip()[:200],
                )
                return None
            data = await response.json()
    except Exception as err:
        _LOGGER.warning("Could not announce to Supervisor: %s", err)
        return None

    uuid = (data.get("data") or {}).get("uuid") or data.get("uuid")
    _LOGGER.info("Announced to Home Assistant for automatic setup")
    return uuid


async def async_withdraw(uuid: str) -> None:
    """Remove a previously published discovery record."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or not uuid:
        return
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            await session.delete(
                f"{_SUPERVISOR_URL}/discovery/{uuid}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as err:
        _LOGGER.debug("Withdrawing discovery record failed: %s", err)
