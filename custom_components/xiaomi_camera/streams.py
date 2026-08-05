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

from .const import CONF_CAMERA_STREAMS, CONF_PRIMARY_STREAM

#: The camera's own encoding at its own resolution. What a camera gets when
#: nothing has been chosen for it: the unmodified stream, no transcode.
ROOT_KEY = "h265"


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


def primary_stream(options: dict) -> str:
    """Which stream the bare `<did>` entity is bound to.

    One definition on purpose. This value decides an entity's permanent
    identity, and two call sites answering it differently moves that identity
    without anything failing -- the entity is simply created under a different
    id, and whatever was bound to the old one is orphaned.
    """
    return options.get(CONF_PRIMARY_STREAM, ROOT_KEY)


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
        # Falls back to the entry's primary stream, not a hardcoded key: the
        # bare `<did>` entity's identity (see `unique_id`) is always bound to
        # `primary_stream`, and this default must agree with it by
        # construction. A camera absent here -- never configured, or added
        # later by `auto_add` -- must still select the stream its existing
        # entity is bound to, or `wanted_unique_ids` computes an identity
        # that does not match and the entity is deleted.
        primary = primary_stream(options)
        return [primary] if primary in available else available[:1]
    wanted = set(chosen)
    return [key for key in available if key in wanted]


#: What the entry-wide option's values mean in the per-camera model. Read only
#: by the migration; delete both this and `migrate_options` once no entry can
#: still be at version 1.
_LEGACY_CODECS = {"h264": "h264", "original": ROOT_KEY}

#: What an entry with no `stream_codec` key has been playing all along --
#: `camera.py` defaults to H.264. Not the new default, which would silently
#: change a working entity on upgrade.
_LEGACY_DEFAULT = "h264"


def wanted_unique_ids(options: dict, available: dict[str, list[str]]) -> set[str]:
    """Every entity identity the current selection should produce.

    Anything in the registry outside this set was deselected. Home Assistant
    keeps such entities forever otherwise -- present, permanently unavailable,
    and indistinguishable from a broken one.
    """
    primary = primary_stream(options)
    return {
        unique_id(did, key, primary)
        for did, keys in available.items()
        for key in selected_streams(options, did, keys)
    }


def migrate_options(options: dict, dids: list[str]) -> dict:
    """Turn the entry-wide codec choice into a per-camera selection.

    One-shot, driven by the config entry version. Everything it reads is gone
    afterwards, so this function and `_LEGACY_*` above can be deleted whole
    once no entry remains at version 1 -- which is the point of doing it here
    rather than leaving the old key in place and branching on it forever.
    """
    migrated = {key: value for key, value in options.items() if key != "stream_codec"}
    primary = _LEGACY_CODECS.get(options.get("stream_codec", _LEGACY_DEFAULT), "h264")
    migrated[CONF_PRIMARY_STREAM] = primary
    migrated[CONF_CAMERA_STREAMS] = {did: [primary] for did in dids}
    return migrated
