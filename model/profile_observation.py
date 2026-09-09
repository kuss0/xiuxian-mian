"""Chronological profile observations; never registers identities or sends reads."""

import math

from .persistence import mark_dirty
from .state import get_identity_state, get_send_as_profile, has_identity, update_send_as_profile


FIELD_GROUPS = {
    "daohao": ("daohao",),
    "realm": ("realm",),
    "spiritual_root": ("spiritual_root_type", "spiritual_root_attrs", "replica_professions"),
    "sect_name": ("sect_name",),
    "xiuwei": ("xiuwei_current", "xiuwei_max"),
    "battle_power": ("battle_power_text", "battle_power_value"),
    "username": ("username",),
    "label": ("label",),
    "sect_contribution": ("sect_contribution",),
}
NUMBER_FIELDS = {"xiuwei_current", "xiuwei_max", "battle_power_value", "sect_contribution"}
PROFILE_FIELDS = {field for fields in FIELD_GROUPS.values() for field in fields}


def timestamp(value):
    if type(value) not in {int, float}:
        return 0.0
    try:
        return float(value) if math.isfinite(value) and value >= 0 else 0.0
    except (ValueError, OverflowError):
        return 0.0


def field_clocks(identity_id):
    if not has_identity(identity_id):
        return None
    clocks = get_identity_state(identity_id).get("identity_profile_observed_at", {})
    if (
        not isinstance(clocks, dict) or clocks.keys() - FIELD_GROUPS.keys() - {"_evidence"}
        or any(type(value) not in {int, float} or value < 0 or timestamp(value) != value for key, value in clocks.items() if key != "_evidence")
    ):
        return None
    evidence = clocks.get("_evidence", {})
    if (
        not isinstance(evidence, dict) or evidence.keys() - FIELD_GROUPS.keys()
        or any(key not in clocks or not valid_evidence(value, clocks[key]) for key, value in evidence.items())
    ):
        return None
    return {**clocks, "_evidence": {key: dict(value) for key, value in evidence.items()}}


def valid_evidence(evidence, observed_at):
    if not isinstance(evidence, dict) or timestamp(observed_at) <= 0:
        return False
    if evidence.get("source") == "api":
        requested_at = timestamp(evidence.get("requested_at"))
        return evidence.keys() == {"source", "requested_at"} and requested_at > 0 and int(requested_at) == observed_at
    return (
        evidence.keys() == {"source", "chat_id", "msg_id", "edited"}
        and evidence["source"] == "telegram"
        and type(evidence["chat_id"]) is int and evidence["chat_id"] != 0
        and type(evidence["msg_id"]) is int and evidence["msg_id"] > 0
        and type(evidence["edited"]) is bool
    )


def observation_is_newer(previous_at, previous, observed_at, evidence=None):
    if observed_at != previous_at:
        return observed_at > previous_at
    if not evidence or not previous:
        return False
    if evidence["source"] != previous["source"]:
        return evidence["source"] == "telegram"
    if evidence["source"] == "api":
        return evidence["requested_at"] > previous["requested_at"]
    if evidence["chat_id"] != previous["chat_id"]:
        return False
    if evidence["msg_id"] == previous["msg_id"]:
        return evidence["edited"] and not previous["edited"]
    return not evidence["edited"] and not previous["edited"] and evidence["msg_id"] > previous["msg_id"]


def telegram_profile_evidence(event):
    return {
        "source": "telegram", "chat_id": getattr(event, "chat_id", 0),
        "msg_id": getattr(event, "msg_id", getattr(event, "id", 0)),
        "edited": str(getattr(event, "event_type", "message")).lower() in {"edit", "edited", "message_edited"},
    }


def apply_profile_observation(identity_id, changes, observed_at, *, evidence=None):
    """Apply newer field groups, returning accepted fields or None on invalid evidence."""
    clocks = field_clocks(identity_id)
    observed_at = timestamp(observed_at)
    if (
        clocks is None or observed_at <= 0 or not isinstance(changes, dict)
        or (evidence is not None and not valid_evidence(evidence, observed_at))
        or changes.keys() - PROFILE_FIELDS
        or any(
            (type(value) is not int or value < 0) if field in NUMBER_FIELDS
            else (not isinstance(value, str) or len(value) > 512)
            for field, value in changes.items()
        )
    ):
        return None
    accepted = {}
    for group, fields in FIELD_GROUPS.items():
        present = {field: changes[field] for field in fields if field in changes}
        if present and observation_is_newer(clocks.get(group, 0), clocks["_evidence"].get(group), observed_at, evidence):
            accepted.update(present)
            clocks[group] = observed_at
            if evidence is None:
                clocks["_evidence"].pop(group, None)
            else:
                clocks["_evidence"][group] = dict(evidence)
    if accepted:
        metadata = {"sect_updated_at": max(observed_at, timestamp(get_send_as_profile(identity_id).get("sect_updated_at")))}
        if "sect_contribution" in accepted:
            metadata["sect_contribution_updated_at"] = observed_at
        update_send_as_profile(identity_id, **accepted, **metadata)
        get_identity_state(identity_id)["identity_profile_observed_at"] = clocks
        mark_dirty()
    return accepted
