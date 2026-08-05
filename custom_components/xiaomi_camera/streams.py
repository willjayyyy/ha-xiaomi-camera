"""Which published streams become entities.

Deliberately free of any Home Assistant import. The rest of the integration
cannot be imported without Home Assistant installed, and CI does not install
it -- so logic that lives there cannot be tested at all. This module holds the
parts worth testing: which streams a camera gets, and what identity each
entity carries.

The same split exists on the add-on side in `bridge.framing`, and for the same
reason.
"""

from __future__ import annotations

from .const import CONF_CAMERA_STREAMS

#: The camera's own encoding at its own resolution. What a camera gets when
#: nothing has been chosen for it: the unmodified stream, no transcode.
ROOT_KEY = "hevc"


def unique_id(did: str, key: str, primary_key: str) -> str:
    """The permanent identity of the entity for one stream.

    The primary stream keeps the bare device id, which is what entities
    created before this feature already use. Anything else would make Home
    Assistant create a new entity and abandon the old one, taking its HomeKit
    pairing, automations, dashboard cards and history with it.

    Which stream is primary is a property of the entry, decided once at
    migration and never revisited -- see :func:`migrate_options`.
    """
    return did if key == primary_key else f"{did}_{key}"


def selected_streams(options: dict, did: str, available: list[str]) -> list[str]:
    """The stream keys to create entities for, in the add-on's own order.

    Ordering follows what the add-on published rather than what the user
    ticked, so reconfiguring does not reshuffle entity creation.

    A key the add-on no longer publishes is dropped rather than kept: an
    entity pointing at a stream that does not exist is permanently
    unavailable, which reads as a fault rather than as a downgrade.
    """
    chosen = (options.get(CONF_CAMERA_STREAMS) or {}).get(did)
    if not chosen:
        return [ROOT_KEY] if ROOT_KEY in available else available[:1]
    wanted = set(chosen)
    return [key for key in available if key in wanted]
