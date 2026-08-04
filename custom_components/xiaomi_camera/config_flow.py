"""Config flow.

The goal is that a user installs one thing -- this integration -- and gets a
working setup. On Home Assistant OS the flow installs and starts the companion
add-on itself, so the two-component architecture stays an implementation
detail. Where that is not possible (Home Assistant Container, or a bridge
running on another machine) the same flow accepts an address instead.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import BridgeClient, BridgeError, BridgeNotLinkedError
from .const import (
    ADDON_NAME,
    ADDON_SLUG,
    CONF_AUTO_ADD,
    CONF_CAMERAS,
    CONF_EXCLUDED,
    CONF_HOST,
    CONF_PORT,
    CONF_STREAM_CODEC,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    STREAM_CODEC_H264,
    STREAM_CODEC_ORIGINAL,
)
from .selection import selected

_LOGGER = logging.getLogger(__name__)

_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class XiaomiCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xiaomi Camera."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = DEFAULT_HOST
        self._port: int = DEFAULT_PORT
        self._addon_manager: Any = None

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> XiaomiCameraOptionsFlow:
        return XiaomiCameraOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start setup.

        On a supervised installation the add-on route is offered first, since
        it is the one that requires nothing further from the user.
        """
        if self._is_supervised():
            return await self.async_step_install_addon()
        return await self.async_step_manual()

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle the add-on announcing itself through Supervisor."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        config = discovery_info.config or {}
        self._host = str(config.get("host", DEFAULT_HOST))
        self._port = int(config.get("port", DEFAULT_PORT))
        self.context["title_placeholders"] = {"name": ADDON_NAME}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered bridge.

        Discovery never creates an entry on its own; the user always confirms.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="discovery_confirm",
                description_placeholders={"name": ADDON_NAME},
            )
        return await self._async_validate_and_create()

    # ------------------------------------------------------------------
    # Reauthentication
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Add-on managed setup
    # ------------------------------------------------------------------

    async def async_step_install_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Install and start the bridge add-on, then continue."""
        manager = self._get_addon_manager()
        if manager is None:
            return await self.async_step_manual()

        try:
            info = await manager.async_get_addon_info()
        except Exception as err:
            _LOGGER.debug("Add-on info unavailable (%s); falling back to manual", err)
            return await self.async_step_manual()

        from homeassistant.components.hassio import AddonState

        if info.state == AddonState.NOT_INSTALLED:
            if not info.available:
                # The add-on repository has not been added, so Supervisor has
                # nothing to install. Point the user at the manual path rather
                # than failing with an opaque error.
                return await self.async_step_addon_unavailable()
            if user_input is None:
                return self.async_show_form(
                    step_id="install_addon",
                    description_placeholders={"addon": ADDON_NAME},
                )
            try:
                await manager.async_install_addon()
            except Exception as err:
                # Without this the flow surfaces a bare "Unknown error"; the
                # sibling start path already reports failures properly.
                return self.async_abort(
                    reason="addon_install_failed",
                    description_placeholders={"error": str(err)},
                )
            return await self.async_step_start_addon()

        if info.state != AddonState.RUNNING:
            return await self.async_step_start_addon()

        self._host, self._port = DEFAULT_HOST, DEFAULT_PORT
        return await self._async_validate_and_create()

    async def async_step_start_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start the add-on and create the entry."""
        manager = self._get_addon_manager()
        if manager is None:
            return await self.async_step_manual()
        try:
            await manager.async_start_addon()
        except Exception as err:
            return self.async_abort(
                reason="addon_start_failed",
                description_placeholders={"error": str(err)},
            )
        self._host, self._port = DEFAULT_HOST, DEFAULT_PORT
        return await self._async_validate_and_create()

    async def async_step_addon_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that the add-on repository has to be added first."""
        if user_input is None:
            return self.async_show_form(
                step_id="addon_unavailable",
                description_placeholders={"addon": ADDON_NAME},
            )
        return await self.async_step_manual()

    # ------------------------------------------------------------------
    # Manual setup
    # ------------------------------------------------------------------

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a bridge address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            try:
                await self._async_probe()
            except BridgeNotLinkedError:
                # Reachable but not signed in: still a valid entry, since the
                # user completes sign-in on the add-on's own page.
                return await self._async_create_entry()
            except BridgeError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_create_entry()

        return self.async_show_form(
            step_id="manual", data_schema=_MANUAL_SCHEMA, errors=errors
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _async_validate_and_create(self) -> ConfigFlowResult:
        try:
            await self._async_probe()
        except BridgeNotLinkedError:
            return await self._async_create_entry()
        except BridgeError as err:
            _LOGGER.debug("Bridge probe failed: %s", err)
            return self.async_show_form(
                step_id="manual",
                data_schema=_MANUAL_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        return await self._async_create_entry()

    async def _async_probe(self) -> None:
        client = BridgeClient(
            async_get_clientsession(self.hass), host=self._host, port=self._port
        )
        await client.async_health()

    async def _async_create_entry(self) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        # Asked before finishing, while the user is already thinking about
        # this. Skipped when there is nothing to choose from -- an account
        # that has not been linked yet has no cameras, and an empty checklist
        # would only teach the user that this step does not matter.
        self._cameras = await self._async_fetch_cameras()
        if self._cameras:
            return await self.async_step_cameras()
        return self._create()

    def _create(self, options: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(
            title=ADDON_NAME,
            data={CONF_HOST: self._host, CONF_PORT: self._port},
            options=options or {},
        )

    async def async_step_cameras(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which cameras to bring into Home Assistant."""
        if user_input is not None:
            return self._create(_options_from(self._cameras, user_input))
        return self.async_show_form(
            step_id="cameras",
            data_schema=_cameras_schema(
                self._cameras, list(self._cameras), True, STREAM_CODEC_H264
            ),
        )

    async def _async_fetch_cameras(self) -> dict[str, str]:
        client = BridgeClient(
            async_get_clientsession(self.hass), host=self._host, port=self._port
        )
        try:
            return {camera.did: camera.name for camera in await client.async_cameras()}
        except BridgeError:
            # Not fatal: the entry is still valid, and the choice can be made
            # later from the integration's options.
            return {}

    def _is_supervised(self) -> bool:
        try:
            from homeassistant.components.hassio import is_hassio
        except ImportError:
            return False
        return bool(is_hassio(self.hass))

    def _get_addon_manager(self) -> Any:
        """Return Supervisor's add-on manager, or ``None`` when unavailable."""
        if self._addon_manager is not None:
            return self._addon_manager
        try:
            from homeassistant.components.hassio import AddonManager
        except ImportError:
            return None
        if not self._is_supervised():
            return None
        self._addon_manager = AddonManager(self.hass, _LOGGER, ADDON_NAME, ADDON_SLUG)
        return self._addon_manager


class XiaomiCameraOptionsFlow(OptionsFlow):
    """Change which cameras are in Home Assistant, at any time.

    The same list as during setup, so adding a camera later, or dropping one
    that turned out not to be wanted, is the same action in the same place
    rather than a reinstall.
    """

    def __init__(self) -> None:
        #: Filled by the form that offers them, so saving can work out which
        #: cameras were turned down as well as which were kept.
        self._cameras: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data=_options_from(self._cameras, user_input)
            )

        client = BridgeClient(
            async_get_clientsession(self.hass),
            host=self.config_entry.data[CONF_HOST],
            port=self.config_entry.data[CONF_PORT],
        )
        try:
            self._cameras = {c.did: c.name for c in await client.async_cameras()}
        except BridgeError:
            return self.async_abort(reason="cannot_connect")
        if not self._cameras:
            return self.async_abort(reason="no_cameras")

        # Everything the account has now, with what is currently imported
        # ticked. A camera bought since setup therefore appears here -- ticked
        # already if new ones join on their own, waiting to be ticked if not.
        auto_add = self.config_entry.options.get(CONF_AUTO_ADD, True)
        chosen = selected(self.config_entry, self._cameras)
        return self.async_show_form(
            step_id="init",
            data_schema=_cameras_schema(
                self._cameras,
                chosen,
                auto_add,
                self.config_entry.options.get(CONF_STREAM_CODEC, STREAM_CODEC_H264),
            ),
        )


def _cameras_schema(
    cameras: dict[str, str], chosen: list[str], auto_add: bool, codec: str
) -> vol.Schema:
    """A checklist of cameras, labelled the way the Mi Home app labels them."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CAMERAS, default=[did for did in chosen if did in cameras]
            ): cv.multi_select(cameras),
            vol.Required(CONF_AUTO_ADD, default=auto_add): bool,
            vol.Required(CONF_STREAM_CODEC, default=codec): SelectSelector(
                SelectSelectorConfig(
                    options=[STREAM_CODEC_H264, STREAM_CODEC_ORIGINAL],
                    translation_key=CONF_STREAM_CODEC,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _options_from(
    cameras: dict[str, str], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Turn one checklist into both ways of describing the same choice.

    Which one is consulted depends on the checkbox -- see `selection.selected`
    -- but both are recorded, so turning it off and on again does not lose what
    was ticked.
    """
    chosen = list(user_input[CONF_CAMERAS])
    return {
        CONF_CAMERAS: chosen,
        CONF_EXCLUDED: [did for did in cameras if did not in chosen],
        CONF_AUTO_ADD: bool(user_input[CONF_AUTO_ADD]),
        CONF_STREAM_CODEC: user_input[CONF_STREAM_CODEC],
    }
