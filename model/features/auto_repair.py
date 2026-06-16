"""Report-only auto repair gate.

This module does not fetch inventory data and is not connected to a scheduler.
It may only evaluate an already available snapshot as backup evidence. Live
automation must stay text-first unless API participation is explicitly approved.
"""

from dataclasses import dataclass
import json


CMD_AUTO_REPAIR = ".一键修理"
DURABILITY_SCOPE_EQUIPPED = "equipped"
DURABILITY_SCOPE_ALL = "all"
DEFAULT_DURABILITY_THRESHOLD = 0.5
LINGSHI_PER_DURABILITY = 50
XIUWEI_PER_DURABILITY = 200
SPIRIT_STONE_NAMES = ("灵石", "mat_001", "spirit_stone", "spirit_stones", "lingshi")


@dataclass(frozen=True)
class RepairCandidate:
    item_id: str
    name: str
    durability: int
    max_durability: int
    missing: int


@dataclass(frozen=True)
class AutoRepairDecision:
    should_send: bool
    command: str = ""
    reason: str = ""
    total_missing: int = 0
    cost_lingshi: int = 0
    cost_xiuwei: int = 0
    trigger_candidates: tuple = ()
    repair_candidates: tuple = ()


def _parse_int(value, default=0):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _parse_int_or_none(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _as_dict(value):
    if isinstance(value, dict):
        return value
    return {}


def _maybe_json(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def _iter_values(value):
    value = _maybe_json(value)
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return tuple(value.values())
    return ()


def _item_id(item):
    payload = _as_dict(item)
    for key in ("item_id", "id", "uid", "instance_id", "key"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _item_name(item):
    payload = _as_dict(item)
    for key in ("name", "item_name", "display_name"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _durability_pair(item):
    payload = _as_dict(item)
    durability = _parse_int_or_none(payload.get("durability", payload.get("dur")))
    max_durability = _parse_int_or_none(
        payload.get("max_durability", payload.get("maxDurability", payload.get("durability_max")))
    )
    return durability, max_durability


def _identity_snapshot(snapshot):
    return _as_dict(snapshot)


def _inventory(snapshot):
    payload = _identity_snapshot(snapshot)
    for key in ("inventory", "storage_bag", "bag"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def _iter_items(snapshot):
    inventory = _inventory(snapshot)
    for key in ("items", "equips", "equipment", "treasures", "artifacts"):
        value = inventory.get(key)
        items = _iter_values(value)
        if items:
            return items
    if isinstance(inventory, list):
        return tuple(inventory)
    return ()


def _equipped_ids(snapshot):
    payload = _identity_snapshot(snapshot)
    inventory = _inventory(snapshot)
    values = []
    for source in (payload, inventory):
        for key in (
            "equipped_treasure_id",
            "equipped_treasure_ids",
            "equipped_ids",
            "equipped",
            "equipment_ids",
        ):
            if key in source:
                values.append(source.get(key))
    equipped = set()
    for value in values:
        value = _maybe_json(value)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = str(item or "").strip()
                if text:
                    equipped.add(text)
        elif isinstance(value, dict):
            for item in value.values():
                text = str(item or "").strip()
                if text:
                    equipped.add(text)
        else:
            text = str(value or "").strip()
            if text:
                equipped.add(text)
    return equipped


def _candidate_from_item(item):
    durability, max_durability = _durability_pair(item)
    if durability is None or max_durability is None:
        return None
    if max_durability <= 0 or durability < 0 or durability >= max_durability:
        return None
    return RepairCandidate(
        item_id=_item_id(item),
        name=_item_name(item),
        durability=durability,
        max_durability=max_durability,
        missing=max_durability - durability,
    )


def repair_candidates(snapshot, scope=DURABILITY_SCOPE_EQUIPPED):
    selected_scope = str(scope or DURABILITY_SCOPE_EQUIPPED).strip() or DURABILITY_SCOPE_EQUIPPED
    equipped = _equipped_ids(snapshot)
    candidates = []
    for item in _iter_items(snapshot):
        item_id = _item_id(item)
        if selected_scope != DURABILITY_SCOPE_ALL and (not item_id or item_id not in equipped):
            continue
        candidate = _candidate_from_item(item)
        if candidate:
            candidates.append(candidate)
    return tuple(candidates)


def _below_threshold(candidate, threshold):
    try:
        limit = float(candidate.max_durability) * float(threshold)
    except (TypeError, ValueError):
        return False
    return candidate.durability < limit


def _material_amount(snapshot, names):
    wanted = {str(name or "").strip() for name in names if str(name or "").strip()}
    inventory = _inventory(snapshot)
    materials = inventory.get("materials", inventory.get("material", {}))
    total = 0
    if isinstance(materials, dict):
        for key, value in materials.items():
            if str(key or "").strip() in wanted:
                total += _parse_int(value)
            elif isinstance(value, dict) and str(value.get("name") or "").strip() in wanted:
                total += _parse_int(value.get("amount", value.get("count", value.get("num"))))
    elif isinstance(materials, list):
        for item in materials:
            payload = _as_dict(item)
            name = str(payload.get("name") or payload.get("item_name") or payload.get("item_id") or "").strip()
            if name in wanted:
                total += _parse_int(payload.get("amount", payload.get("count", payload.get("num"))))
    return total


def _cultivation_points(snapshot):
    payload = _identity_snapshot(snapshot)
    for key in ("cultivation_points", "points", "xiuwei", "修为"):
        if key in payload:
            return _parse_int(payload.get(key))
    return 0


def decide_auto_repair(
    snapshot,
    *,
    durability_threshold=DEFAULT_DURABILITY_THRESHOLD,
    scope=DURABILITY_SCOPE_EQUIPPED,
):
    try:
        threshold = float(durability_threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_DURABILITY_THRESHOLD
    if threshold <= 0 or threshold > 1:
        threshold = DEFAULT_DURABILITY_THRESHOLD

    candidates = repair_candidates(snapshot, scope=scope)
    trigger_candidates = tuple(candidate for candidate in candidates if _below_threshold(candidate, threshold))
    if not trigger_candidates:
        return AutoRepairDecision(False, reason="no_item_below_threshold", repair_candidates=candidates)

    total_missing = sum(candidate.missing for candidate in candidates)
    if total_missing <= 0:
        return AutoRepairDecision(
            False,
            reason="no_missing_durability",
            trigger_candidates=trigger_candidates,
            repair_candidates=candidates,
        )

    cost_lingshi = total_missing * LINGSHI_PER_DURABILITY
    cost_xiuwei = total_missing * XIUWEI_PER_DURABILITY
    available_lingshi = _material_amount(snapshot, SPIRIT_STONE_NAMES)
    if available_lingshi < cost_lingshi:
        return AutoRepairDecision(
            False,
            reason="lingshi_shortage",
            total_missing=total_missing,
            cost_lingshi=cost_lingshi,
            cost_xiuwei=cost_xiuwei,
            trigger_candidates=trigger_candidates,
            repair_candidates=candidates,
        )
    available_xiuwei = _cultivation_points(snapshot)
    if available_xiuwei < cost_xiuwei:
        return AutoRepairDecision(
            False,
            reason="xiuwei_shortage",
            total_missing=total_missing,
            cost_lingshi=cost_lingshi,
            cost_xiuwei=cost_xiuwei,
            trigger_candidates=trigger_candidates,
            repair_candidates=candidates,
        )

    return AutoRepairDecision(
        True,
        command=CMD_AUTO_REPAIR,
        reason="ready",
        total_missing=total_missing,
        cost_lingshi=cost_lingshi,
        cost_xiuwei=cost_xiuwei,
        trigger_candidates=trigger_candidates,
        repair_candidates=candidates,
    )


__all__ = [
    "AutoRepairDecision",
    "CMD_AUTO_REPAIR",
    "DEFAULT_DURABILITY_THRESHOLD",
    "DURABILITY_SCOPE_ALL",
    "DURABILITY_SCOPE_EQUIPPED",
    "LINGSHI_PER_DURABILITY",
    "RepairCandidate",
    "XIUWEI_PER_DURABILITY",
    "decide_auto_repair",
    "repair_candidates",
]
