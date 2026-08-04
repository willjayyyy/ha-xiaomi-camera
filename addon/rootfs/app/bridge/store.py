"""Persistence for Xiaomi account credentials.

Credentials live in ``/data``, which Supervisor keeps across add-on restarts
and updates and never includes in a public backup by default. They are written
with owner-only permissions and are never logged.
"""

from __future__ import annotations

import json
import logging
import os
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .const import CREDENTIALS_FILE

_LOGGER = logging.getLogger(__name__)

_OWNER_READ_WRITE = 0o600


@dataclass
class Credentials:
    """A Xiaomi OAuth2 session.

    ``device_uuid`` must stay stable across restarts: the SDK derives the
    device identifier and the OAuth state parameter from it, and a changed
    value invalidates the existing session.
    """

    device_uuid: str
    access_token: str
    refresh_token: str
    expires_ts: int
    #: The full user profile as returned by the SDK, not just the id. The SDK
    #: brings up its MQTT event channel during client initialisation and needs
    #: ``user_info.uid`` at that moment; supplying only an id afterwards leaves
    #: the channel disabled with a single warning line to explain it.
    user_info: dict[str, Any] | None = None

    @property
    def user_id(self) -> str | None:
        return str((self.user_info or {}).get("uid") or "") or None

    def to_oauth_info(self) -> dict[str, Any]:
        """Shape expected by ``MIoTClient(oauth_info=...)``."""
        payload: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_ts": self.expires_ts,
        }
        if self.user_info:
            payload["user_info"] = self.user_info
        return payload


class CredentialStore:
    """Reads and writes the credential file."""

    def __init__(self, path: str | Path = CREDENTIALS_FILE) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> Credentials | None:
        """Return stored credentials, or ``None`` when not linked yet.

        A corrupt file is treated as "not linked" rather than a fatal error, so
        the user can re-authorize through the UI instead of having to reach into
        the filesystem.
        """
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return Credentials(
                device_uuid=raw["device_uuid"],
                access_token=raw["access_token"],
                refresh_token=raw["refresh_token"],
                expires_ts=int(raw["expires_ts"]),
                user_info=raw.get("user_info"),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            _LOGGER.warning(
                "credential file at %s is unreadable; treating the account as "
                "not linked. Re-authorize from the add-on page.",
                self._path,
            )
            return None

    def save(self, credentials: Credentials) -> None:
        """Persist credentials atomically with owner-only permissions."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        payload = {
            "device_uuid": credentials.device_uuid,
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "expires_ts": credentials.expires_ts,
            "user_info": credentials.user_info,
        }
        # Opened with the restrictive mode rather than chmod'ed afterwards:
        # the latter leaves a window in which the file exists world-readable.
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_READ_WRITE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        # Atomic replace keeps a crash mid-write from leaving a half-written
        # file that would read as "not linked" and silently drop the session.
        temporary.replace(self._path)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    @staticmethod
    def new_device_uuid() -> str:
        return uuid_module.uuid4().hex
