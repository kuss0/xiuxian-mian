"""Public-entry commands with explicit HTTP receipts, never Telegram IDs."""

import time
import math
from datetime import datetime

from ..state import get_miniapp_auto_config, is_cave_public_identity_available


def configured_entries():
    config = get_miniapp_auto_config()
    values = config.get("cave_public_entry_urls") or config.get("cave_public_entry_url") or ()
    if isinstance(values, str):
        values = [values]
    return tuple(value.strip() for value in values if isinstance(value, str) and value.strip())


def available(identity_id):
    from .cave_treasure_runtime import _public_entry_allowed

    return bool(configured_entries() and is_cave_public_identity_available(identity_id) and _public_entry_allowed())


def pavilion_available(identity_id):
    if not available(identity_id):
        return False
    config = get_miniapp_auto_config() or {}
    if config.get("cave_public_concubine_enabled") is not True:
        return False
    raw_ids = config.get("cave_public_concubine_identity_ids") or ()
    if not isinstance(raw_ids, (list, tuple, set)):
        return False
    try:
        allowed_ids = {int(value) for value in raw_ids if type(value) in (int, str)}
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return identity_id > 0 and identity_id in allowed_ids


def receipt_matches(receipt, *, identity_id, account_id, op_id, command, started_at):
    from .cave_treasure_miniapp import _require_cave_action_player_id

    if type(started_at) not in (int, float) or not math.isfinite(started_at):
        return False
    if not isinstance(op_id, str) or not op_id or not isinstance(command, str) or not command:
        return False
    if not isinstance(receipt, dict) or receipt.keys() != {
        "transport", "op_id", "command", "send_as_id", "account_id", "player_id", "started_at", "received_at",
    }:
        return False
    if any(type(receipt[key]) not in (int, float) or not math.isfinite(receipt[key])
           for key in ("started_at", "received_at")):
        return False
    if any(type(receipt[key]) is not int for key in ("send_as_id", "account_id", "player_id")):
        return False
    try:
        _require_cave_action_player_id(receipt["player_id"], identity_id=identity_id)
    except (TypeError, ValueError):
        return False
    return bool(
        receipt["transport"] == "miniapp" and receipt["op_id"] == op_id and receipt["command"] == command
        and receipt["send_as_id"] == identity_id and receipt["account_id"] == account_id > 0
        and 0 < started_at <= receipt["started_at"] <= receipt["received_at"]
    )


def pavilion_receipt_matches(receipt, *, identity_id, account_id, op_id, started_at):
    from .cave_treasure_miniapp import _require_cave_action_player_id

    if type(started_at) not in (int, float) or not math.isfinite(started_at):
        return False
    if not isinstance(op_id, str) or not op_id:
        return False
    if not isinstance(receipt, dict) or receipt.keys() != {
        "transport", "op_id", "section", "send_as_id", "account_id", "player_id", "started_at", "received_at",
    }:
        return False
    if any(type(receipt[key]) not in (int, float) or not math.isfinite(receipt[key])
           for key in ("started_at", "received_at")):
        return False
    if any(type(receipt[key]) is not int for key in ("send_as_id", "account_id", "player_id")):
        return False
    try:
        _require_cave_action_player_id(receipt["player_id"], identity_id=identity_id)
    except (TypeError, ValueError):
        return False
    return bool(
        receipt["transport"] == "miniapp" and receipt["op_id"] == op_id and receipt["section"] == "pavilion"
        and receipt["send_as_id"] == identity_id and receipt["account_id"] == account_id > 0
        and 0 < started_at <= receipt["started_at"] <= receipt["received_at"]
    )


def _pavilion_companion(data, *, identity_id, partner):
    from .cave_treasure_miniapp import cave_action_player_error

    if cave_action_player_error(data, identity_id):
        return None
    root = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    dwelling = root.get("dwelling") if isinstance(root, dict) else None
    if not isinstance(dwelling, dict):
        return None
    companions = dwelling.get("companions")
    if not isinstance(companions, list):
        single = dwelling.get("companion")
        companions = [single] if isinstance(single, dict) else []
    matches = [item for item in companions if isinstance(item, dict) and item.get("name") == partner]
    if len(matches) != 1:
        return None
    item = matches[0]
    if item.get("active") is not True or not isinstance(item.get("raw"), dict):
        return None
    return item


def parse_pavilion_companion(data, *, identity_id, partner):
    item = _pavilion_companion(data, identity_id=identity_id, partner=partner)
    if item is None:
        return None
    raw = item.get("raw")
    affection = raw.get("affection") if isinstance(raw, dict) else None
    if (item.get("isStarPalace") is not True or type(item.get("greetedToday")) is not bool
            or type(affection) is not int or not 0 <= affection < 2 ** 63):
        return None
    return {"partner": partner, "greeted_today": item["greetedToday"], "affinity": affection}


_FRAGMENT_BAGS = {
    "xutian": {"xutian_chart_east", "xutian_chart_north", "xutian_chart_south", "xutian_chart_west"},
    "cangkun": {"cangkun_chart_gate", "cangkun_chart_jade", "cangkun_chart_mulan", "cangkun_chart_taimiao"},
}


def _pavilion_time(value):
    if value in (None, ""):
        return 0
    if not isinstance(value, str) or value != value.strip() or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        timestamp = parsed.timestamp()
    except (OverflowError, TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) and timestamp >= 0 else None


def fragment_snapshot_valid(value, *, partner=None):
    if not isinstance(value, dict) or value.keys() != {
        "partner", "fragments", "puzzle_completed", "last_dream_at", "last_puzzle_at",
    }:
        return False
    if (not isinstance(value["partner"], str) or not value["partner"]
            or partner is not None and value["partner"] != partner):
        return False
    if (not isinstance(value["fragments"], dict) or value["fragments"].keys() != _FRAGMENT_BAGS.keys()
            or any(type(parts) is not list or len(parts) != 2 or type(parts[0]) is not int
                   or not 0 <= parts[0] <= 4 or parts[1] != 4 for parts in value["fragments"].values())):
        return False
    if (not isinstance(value["puzzle_completed"], dict)
            or value["puzzle_completed"].keys() != _FRAGMENT_BAGS.keys()
            or any(type(count) is not int or not 0 <= count < 2 ** 63
                   for count in value["puzzle_completed"].values())):
        return False
    if (type(value["last_dream_at"]) not in {int, float} or not math.isfinite(value["last_dream_at"])
            or not 0 <= value["last_dream_at"] < 2 ** 63
            or not isinstance(value["last_puzzle_at"], dict)
            or value["last_puzzle_at"].keys() != _FRAGMENT_BAGS.keys()
            or any(type(at) not in {int, float} or not math.isfinite(at) or not 0 <= at < 2 ** 63
                   for at in value["last_puzzle_at"].values())):
        return False
    return True


def parse_pavilion_fragments(data, *, identity_id, partner):
    item = _pavilion_companion(data, identity_id=identity_id, partner=partner)
    if item is None:
        return None
    raw = item["raw"]
    fragments = {}
    for kind, expected in _FRAGMENT_BAGS.items():
        bag = raw.get(f"{kind}_fragment_bag")
        if (not isinstance(bag, dict) or bag.keys() != expected
                or any(type(count) is not int or not 0 <= count < 2 ** 63 for count in bag.values())):
            return None
        fragments[kind] = [sum(count > 0 for count in bag.values()), 4]
    completed = {kind: raw.get(f"{kind}_puzzle_completed") for kind in _FRAGMENT_BAGS}
    last_dream_at = _pavilion_time(raw.get("last_dream_map_seek_time"))
    last_puzzle_at = {kind: _pavilion_time(raw.get(f"last_{kind}_puzzle_time")) for kind in _FRAGMENT_BAGS}
    value = {
        "partner": partner,
        "fragments": fragments,
        "puzzle_completed": completed,
        "last_dream_at": last_dream_at,
        "last_puzzle_at": last_puzzle_at,
    }
    return value if fragment_snapshot_valid(value, partner=partner) else None


async def execute(identity_id, command, *, op_id, operation_check):
    from . import cave_treasure_runtime as cave
    from .cave_treasure_miniapp import normalize_cave_tianjige_command
    from .miniapp_common import MiniAppIdentityOwner

    command = normalize_cave_tianjige_command(command)
    entries = configured_entries()
    owner = MiniAppIdentityOwner.capture(identity_id)
    unsent = {"ok": False, "status": "blocked", "action_dispatched": False, "outcome_unknown": False}
    if not op_id or owner is None or not entries:
        return unsent
    def current():
        return (owner.is_current() and entries == configured_entries() and available(identity_id)
                and not cave.get_cave_public_entry_gate(entries).get("blocked")
                and operation_check() is True)

    lock = cave._public_entry_lock(identity_id)
    if lock.locked() or not current():
        return unsent
    async with lock:
        session = {}
        # Only session establishment may try another entry; commands never replay.
        for entry in entries[:3]:
            if not current():
                return unsent
            token, webview_url, error = cave._parse_public_cave_entry_url(entry)
            if error:
                continue
            session = await cave._load_cave_public_identity_session(
                identity_id, token, webview_url, now=time.time(),
                capture_source=f"tianjige:{identity_id}:{op_id}", operation_check=current,
            )
            if session.get("ok"):
                break
            if (cave.miniapp_retry_after_sec(session) > 0
                    or not cave.is_cave_public_entry_token_failure(session.get("error"))):
                break
        if not current() or not session.get("ok"):
            return dict(unsent, error=session.get("error") or "session_unavailable")
        started_at = time.time()
        result = await cave.run_cave_tianjige_command_production_flow(
            identity_id, token=token, webview_url=webview_url, command=command,
            init_data=session.get("init_data") or "", player_id=session.get("player_id"),
            capture_sink=cave._capture_store(started_at), capture_source=f"tianjige:{identity_id}:{op_id}",
            operation_check=current,
        )
        result = dict(result)
        # A stale successful mutation is still dispatched, but cannot update the new plan.
        if not current():
            return dict(result, ok=False, status="stale", outcome_unknown=bool(result.get("action_dispatched")))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if result.get("ok") and cave.cave_action_player_error(data, identity_id):
            return dict(result, ok=False, status="identity_unverified", outcome_unknown=True, data={})
        action = data.get("actionResult") if isinstance(data.get("actionResult"), dict) else {}
        message = cave.extract_cave_tianjige_command_message(data)
        terminal = (result.get("ok") is True and data.get("ok") is True
                    and action.get("completed") is True and bool(message))
        result.update(terminal=terminal, message=message)
        if terminal:
            result["receipt"] = {
                "transport": "miniapp", "op_id": op_id, "command": command,
                "send_as_id": identity_id, "account_id": owner.account_id,
                "player_id": session["player_id"], "started_at": started_at,
                "received_at": time.time(),
            }
        elif result.get("action_dispatched"):
            result["outcome_unknown"] = True
        return result


async def read_pavilion(identity_id, *, partner, op_id, operation_check, projection="companion"):
    from . import cave_treasure_runtime as cave
    from .cave_treasure_miniapp import cave_action_player_error
    from .miniapp_common import MiniAppIdentityOwner

    entries = configured_entries()
    owner = MiniAppIdentityOwner.capture(identity_id)
    failed = {"ok": False, "status": "blocked"}
    if (not op_id or not isinstance(partner, str) or not partner.strip()
            or projection not in {"companion", "fragments"} or owner is None or not entries):
        return failed

    def current():
        return (owner.is_current() and entries == configured_entries() and available(identity_id)
                and not cave.get_cave_public_entry_gate(entries).get("blocked")
                and operation_check() is True)

    lock = cave._public_entry_lock(identity_id)
    if lock.locked() or not current():
        return failed
    async with lock:
        session = {}
        for entry in entries[:3]:
            if not current():
                return failed
            token, webview_url, error = cave._parse_public_cave_entry_url(entry)
            if error:
                continue
            session = await cave._load_cave_public_identity_session(
                identity_id, token, webview_url, now=time.time(),
                capture_source=f"pavilion:{identity_id}:{op_id}", operation_check=current,
            )
            if session.get("ok"):
                break
            if (cave.miniapp_retry_after_sec(session) > 0
                    or not cave.is_cave_public_entry_token_failure(session.get("error"))):
                break
        if not current() or not session.get("ok"):
            return dict(failed, error=session.get("error") or "session_unavailable")
        started_at = time.time()
        result = await cave.run_cave_dwelling_snapshot_production_flow(
            identity_id, token=token, webview_url=webview_url, endpoint="section", section="pavilion",
            init_data=session.get("init_data") or "", player_id=session.get("player_id"),
            capture_sink=cave._capture_store(started_at), capture_source=f"pavilion:{identity_id}:{op_id}",
            operation_check=current,
        )
        if not current():
            return dict(failed, status="stale")
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not result.get("ok") or cave_action_player_error(data, identity_id):
            return dict(result, ok=False, data={})
        parser = parse_pavilion_companion if projection == "companion" else parse_pavilion_fragments
        projected = parser(data, identity_id=identity_id, partner=partner)
        if projected is None:
            return dict(result, ok=False, status="unverified_companion", data={})
        return {
            "ok": True, "status": "ok", projection: projected,
            "receipt": {
                "transport": "miniapp", "op_id": op_id, "section": "pavilion",
                "send_as_id": identity_id, "account_id": owner.account_id,
                "player_id": session["player_id"], "started_at": started_at,
                "received_at": time.time(),
            },
        }
