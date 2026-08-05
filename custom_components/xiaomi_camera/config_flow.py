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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.translation import async_get_translations

from .api import BridgeCamera, BridgeClient, BridgeError, BridgeNotLinkedError
from .const import (
    ADDON_NAME,
    ADDON_SLUG,
    CONF_AUTO_ADD,
    CONF_CAMERA_STREAMS,
    CONF_CAMERAS,
    CONF_EXCLUDED,
    CONF_HOST,
    CONF_PORT,
    CONF_PRIMARY_STREAM,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
)
from .selection import selected
from .streams import ROOT_KEY, primary_stream

_LOGGER = logging.getLogger(__name__)

_MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class XiaomiCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xiaomi Camera."""

    VERSION = 3

    def __init__(self) -> None:
        self._host: str = DEFAULT_HOST
        self._port: int = DEFAULT_PORT
        self._addon_manager: Any = None
        self._cameras: dict[str, str] = {}
        #: Stream keys each camera actually publishes, by device id. Comes
        #: from the add-on's own answer -- never a list hardcoded here, since
        #: the two components ship separately.
        self._available_streams: dict[str, list[str]] = {}
        #: Filled by the camera checklist step, read by the stream step that
        #: follows it.
        self._chosen_cameras: list[str] = []
        self._auto_add = True

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
        cameras = await self._async_fetch_cameras()
        self._cameras = {camera.did: camera.name for camera in cameras}
        self._available_streams = {
            camera.did: [stream.key for stream in camera.streams] for camera in cameras
        }
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
        """Choose which cameras to bring into Home Assistant.

        Step one of two: tick the cameras. The stream choice for each comes
        in the next step, so it only ever asks about cameras selected here --
        a camera ticked now appears in that form, one unticked does not.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            self._chosen_cameras = list(user_input[CONF_CAMERAS])
            self._auto_add = bool(user_input[CONF_AUTO_ADD])
            return await self.async_step_streams()
        return self.async_show_form(
            step_id="cameras",
            data_schema=_camera_checklist_schema(
                self._cameras, list(self._cameras), True
            ),
            errors=errors,
            # Not the last step: the stream choices follow, so the button
            # reads "Next" rather than "Submit".
            last_step=False,
        )

    async def async_step_streams(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which streams each selected camera publishes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen_streams = _with_defaulted_streams(
                self._chosen_cameras,
                _stream_labels_to_dids(user_input, self._cameras, self._chosen_cameras),
                self._available_streams,
                ROOT_KEY,
            )
            if any(not keys for keys in chosen_streams.values()):
                # The camera was ticked one step ago, so an empty stream list
                # is a contradiction rather than a choice. Saying so beats
                # silently dropping the camera, which would look like the
                # tick was ignored.
                errors["base"] = "no_streams"
            else:
                return self._create(
                    {
                        **_options_from(
                            self._cameras,
                            {
                                CONF_CAMERAS: self._chosen_cameras,
                                CONF_AUTO_ADD: self._auto_add,
                                CONF_CAMERA_STREAMS: chosen_streams,
                            },
                        ),
                        # Fixed at creation and never revisited: this is what
                        # binds the bare `<did>` entity to a stream.
                        # Recomputing it later would change an existing
                        # entity's identity.
                        CONF_PRIMARY_STREAM: ROOT_KEY,
                    }
                )
        return self.async_show_form(
            step_id="streams",
            data_schema=_streams_schema(
                self._cameras,
                self._chosen_cameras,
                self._available_streams,
                ROOT_KEY,
                await _stream_labels(self.hass),
            ),
            errors=errors,
            last_step=True,
        )

    async def _async_fetch_cameras(self) -> list[BridgeCamera]:
        client = BridgeClient(
            async_get_clientsession(self.hass), host=self._host, port=self._port
        )
        try:
            return await client.async_cameras()
        except BridgeError:
            # Not fatal: the entry is still valid, and the choice can be made
            # later from the integration's options.
            return []

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
        #: Stream keys each camera actually publishes, by device id. Filled
        #: alongside `_cameras` so a redisplay after a validation error does
        #: not need a second round trip to the bridge.
        self._available_streams: dict[str, list[str]] = {}
        #: Filled by the camera checklist step, read by the stream step that
        #: follows it.
        self._chosen_cameras: list[str] = []
        self._auto_add = True

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step one of two: tick the cameras to keep in Home Assistant."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._chosen_cameras = list(user_input[CONF_CAMERAS])
            self._auto_add = bool(user_input[CONF_AUTO_ADD])
            return await self.async_step_streams()

        if not self._cameras:
            # The coordinator already holds the current inventory -- polled
            # while the entry was set up -- so opening this form costs no
            # extra round trip to the bridge. `runtime_data` is only ever set
            # by a *successful* `async_setup_entry`; an entry stuck retrying
            # after a `ConfigEntryNotReady` may not have the attribute at
            # all, and the options flow can still be opened against it -- Home
            # Assistant does not gate on entry state here. Reading it with
            # `getattr` turns that into a clean, actionable abort instead of
            # an unhandled `AttributeError` surfacing as "Unknown error".
            coordinator = getattr(self.config_entry, "runtime_data", None)
            if coordinator is None:
                return self.async_abort(reason="cannot_connect")
            cameras = coordinator.data
            self._cameras = {did: camera.name for did, camera in cameras.items()}
            self._available_streams = {
                did: [stream.key for stream in camera.streams]
                for did, camera in cameras.items()
            }
            if not self._cameras:
                return self.async_abort(reason="no_cameras")

        # Everything the account has now, with what is currently imported
        # ticked. A camera bought since setup therefore appears here -- ticked
        # already if new ones join on their own, waiting to be ticked if not.
        auto_add = self.config_entry.options.get(CONF_AUTO_ADD, True)
        chosen = selected(self.config_entry, self._cameras)
        return self.async_show_form(
            step_id="init",
            data_schema=_camera_checklist_schema(self._cameras, chosen, auto_add),
            errors=errors,
            # Not the last step: the stream choices follow, so the button
            # reads "Next" rather than "Submit".
            last_step=False,
        )

    async def async_step_streams(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step two: choose the streams each selected camera publishes."""
        errors: dict[str, str] = {}
        primary = primary_stream(dict(self.config_entry.options))
        if user_input is not None:
            chosen_streams = _with_defaulted_streams(
                self._chosen_cameras,
                _stream_labels_to_dids(user_input, self._cameras, self._chosen_cameras),
                self._available_streams,
                primary,
            )
            if any(not keys for keys in chosen_streams.values()):
                # The camera was ticked one step ago, so an empty stream list
                # is a contradiction rather than a choice. Saying so beats
                # silently dropping the camera, which would look like the
                # tick was ignored.
                errors["base"] = "no_streams"
            else:
                return self.async_create_entry(
                    data={
                        **_options_from(
                            self._cameras,
                            {
                                CONF_CAMERAS: self._chosen_cameras,
                                CONF_AUTO_ADD: self._auto_add,
                                CONF_CAMERA_STREAMS: chosen_streams,
                            },
                        ),
                        # Carried forward, not recomputed: it fixes which
                        # stream the bare `<did>` entity is bound to, and
                        # this flow never revisits that decision.
                        CONF_PRIMARY_STREAM: primary,
                    }
                )
        return self.async_show_form(
            step_id="streams",
            data_schema=_streams_schema(
                self._cameras,
                self._chosen_cameras,
                self._available_streams,
                primary,
                await _stream_labels(self.hass),
                stream_options=self.config_entry.options.get(CONF_CAMERA_STREAMS, {}),
            ),
            errors=errors,
            last_step=True,
        )


def _camera_checklist_schema(
    cameras: dict[str, str],
    chosen: list[str],
    auto_add: bool,
) -> vol.Schema:
    """A checklist of cameras, labelled the way the Mi Home app labels them.

    `cv.multi_select` renders as a dropdown that opens into checkboxes -- the
    same control Home Assistant's own "pick a domain" selectors use, rather
    than a `SelectSelector`, which shows chosen values as chips. Each option
    carries the camera's name plus its id, so a person can tell which camera
    is which; the submitted value is the device id itself.
    """
    options = {did: f"{name} ({did})" for did, name in cameras.items()}
    return vol.Schema(
        {
            vol.Required(
                CONF_CAMERAS, default=[did for did in chosen if did in cameras]
            ): cv.multi_select(options),
            vol.Required(CONF_AUTO_ADD, default=auto_add): bool,
        }
    )


async def _stream_labels(hass: HomeAssistant) -> dict[str, str]:
    """Stream key -> dropdown label, in the user's own language.

    Read through Home Assistant's translation helper rather than by opening
    `en.json`: a Chinese user picking "H.265 720p" in the form and finding
    "H.265 720p" on the device page is the whole point, and reading one fixed
    file gives them an English form and a Chinese device page.

    `selector.stream_key.options` is the table, because it is the only one
    naming every stream. The entity table deliberately has no entry for the
    root stream -- that entity takes the device's own name (see
    `streams.takes_device_name`) -- but the dropdown still has to call it
    something. A test keeps the two tables saying the same words for every
    stream they share.

    The camera's name is not prefixed onto the options: the field these
    options belong to is already labelled with it, so prefixing made the form
    read "Living room (123)" over a list of "Living room H.264 360p".

    Resolved in the server's language, not each user's: `cv.multi_select`
    bakes its labels into the schema here, so there is no later point at which
    the frontend could translate them per viewer.
    """
    prefix = f"component.{DOMAIN}.selector.stream_key.options."
    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    return {
        key.removeprefix(prefix): label
        for key, label in translations.items()
        if key.startswith(prefix)
    }


def _default_selection(available: list[str], primary: str) -> list[str]:
    """The stream a camera defaults to when nothing has been chosen for it.

    Mirrors `selected_streams`'s fallback: the primary when the add-on
    publishes it, otherwise the first stream it does publish. A default naming
    a stream the dropdown does not offer would fail validation on submit -- an
    older add-on that never gained the `original` stream is exactly that case.
    """
    return [primary] if primary in available else available[:1]


def _streams_schema(
    cameras: dict[str, str],
    chosen_cameras: list[str],
    available: dict[str, list[str]],
    primary: str,
    labels: dict[str, str],
    stream_options: dict[str, list[str]] | None = None,
) -> vol.Schema:
    """One stream checklist per selected camera, opening from a dropdown.

    `cv.multi_select` renders as a dropdown that opens into checkboxes -- the
    same control Home Assistant's own "pick a domain" selectors use, unlike a
    `SelectSelector`, which shows chosen values as chips. A separate field per
    camera rather than a shared list, so one camera's choice can never be
    confused with another's. The field is keyed by the camera's name plus its
    device id, so the form reads like something a person recognises and the id
    keeps the key unique even when two cameras share a name. Defaults to the
    primary stream (see `streams.py`) -- or to the first published stream when
    the add-on does not publish it -- and in the options flow to the entry's
    current choice where one exists.

    A camera reporting no streams at all -- an add-on predating
    `/api/cameras.streams` -- gets no selector here.

    `labels` comes from `_stream_labels`; a key missing from it falls back to
    the raw key, which reads as an identifier but still lets the form work.
    """
    streams = {}
    for did in chosen_cameras:
        if did not in cameras or not available.get(did):
            continue
        current = stream_options.get(did) if stream_options else None
        default = (
            current
            if current is not None
            else _default_selection(available[did], primary)
        )
        streams[vol.Required(f"{cameras[did]} ({did})", default=default)] = (
            cv.multi_select({key: labels.get(key, key) for key in available[did]})
        )
    return vol.Schema(streams)


def _stream_labels_to_dids(
    user_input: dict[str, Any],
    cameras: dict[str, str],
    chosen_cameras: list[str],
) -> dict[str, list[str]]:
    """Turn the name+(id) form labels back into device ids.

    The stream step's schema is keyed by `"name (did)"` so the form reads
    like something a person recognises. Submission arrives keyed the same way,
    so the labels have to be resolved to the ids the options dict stores.
    """
    label_to_did = {f"{cameras[did]} ({did})": did for did in chosen_cameras}
    return {
        label_to_did[label]: value
        for label, value in user_input.items()
        if label in label_to_did
    }


def _with_defaulted_streams(
    ticked_cameras: list[str],
    chosen_streams: dict[str, list[str]],
    available: dict[str, list[str]],
    primary: str,
) -> dict[str, list[str]]:
    """Fill in a stream selection for every ticked camera the form omitted.

    `_cameras_schema` only builds a selector for a camera already ticked when
    the form was rendered, so ticking a *new* camera in the same submission
    leaves it with no key here at all -- not an empty one, which the
    `no_streams` check already catches. Left unfilled, the eventual entities
    would be decided by `selected_streams`'s own fallback instead of an
    explicit choice recorded alongside the others.

    A camera with nothing to choose from (`available` empty) is left out
    entirely: forcing a key that names no real stream would ask for an entity
    pointing at a stream that does not exist.
    """
    return {
        **chosen_streams,
        **{
            did: _default_selection(available[did], primary)
            for did in ticked_cameras
            if did not in chosen_streams and available.get(did)
        },
    }


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
        CONF_CAMERA_STREAMS: user_input[CONF_CAMERA_STREAMS],
    }
