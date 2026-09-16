"""Current treasure quota and settlement evidence; never search history."""

from copy import deepcopy
import math
import re


TREASURE_GAIN_KEYS = {
    "expgain": "\u7ecf\u9a8c", "experiencegain": "\u7ecf\u9a8c",
    "cultivationgain": "\u4fee\u4e3a", "xiuweigain": "\u4fee\u4e3a",
    "lingshigain": "\u7075\u77f3", "spiritstonegain": "\u7075\u77f3", "stonegain": "\u7075\u77f3",
    "contribution": "\u8d21\u732e",
}
_REWARD_KEYS = frozenset({"loot", "rewards", "reward", "bonusloot", "drops", "items", "materials", "gains"})
_TEXT_KEYS = frozenset({"log", "logs", "text", "message", "rawmessage", "resulttext", "statustext"})
_NAME_KEYS = ("name", "itemName", "item_name", "title", "label")
_QUANTITY_KEYS = ("qty", "count", "quantity", "amount")
_TERMINAL_FLAGS = ("ok", "success", "completed", "finished", "settled", "settledInApp", "settled_in_app")
_TERMINAL_STATUSES = frozenset({"settled", "completed", "done", "success"})
_ITEM_TEXT_RE = re.compile(
    r"(?:\u83b7\u5f97|\u5956\u52b1|\u6536\u83b7|\u6389\u843d|\u6218\u5229\u54c1|\u6750\u6599)?\s*"
    r"(?:\u3010(?P<bracket>[^\u3011]+)\u3011|(?P<plain>[\u4e00-\u9fffA-Za-z0-9_\u00b7-]{2,24}))"
    r"\s*[xX\u00d7]\s*(?P<count>[0-9]+(?:,[0-9]{3})*)(?![0-9A-Za-z_]|[.,][0-9])"
)
_GAIN_TEXT_RE = re.compile(
    r"(?P<name>\u4fee\u4e3a|\u7ecf\u9a8c|\u7075\u77f3|\u5929\u673a\u6b8b\u75d5)"
    r"\s*[+\uff0b]\s*(?P<count>[0-9]+(?:,[0-9]{3})*)(?![0-9A-Za-z_]|[.,][0-9])"
)


def _key(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).lower()


def treasure_integer(value):
    if type(value) is int:
        number = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and len(value.strip()) <= 16 and re.fullmatch(r"[0-9]+", value.strip()):
        number = int(value.strip())
    else:
        return None
    return number if 0 <= number <= 2**53 - 1 else None


def treasure_session_id(value):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 256 else ""


def treasure_response_body(data):
    if not isinstance(data, dict):
        return {}, "hunt_response_missing"
    if "data" not in data:
        return data, ""
    if not isinstance(data["data"], dict):
        return {}, "hunt_response_invalid_envelope"
    if any(key in data for key in ("dwelling", "hunt", "huntRun", "huntResult")):
        return {}, "hunt_response_ambiguous_envelope"
    return data["data"], ""


def parse_treasure_quota(body, *, error_panel=False):
    dwelling = body.get("dwelling") if isinstance(body.get("dwelling"), dict) else {}
    present = "hunt" in dwelling or (error_panel and "hunt" in body)
    panel = dwelling.get("hunt", body.get("hunt") if error_panel else None)
    state = {"quota_present": present, "quota_verified": False, "quota_known": False,
             "quota_error": "hunt_quota_missing", "games_used": 0, "games_limit": 0, "games_remaining": 0}
    if not present:
        return state
    if not isinstance(panel, dict):
        return {**state, "quota_error": "hunt_quota_invalid_panel"}
    counts = {key: treasure_integer(panel[key]) for key in ("used", "limit", "remaining") if key in panel}
    if any(value is None for value in counts.values()):
        return {**state, "quota_error": "hunt_quota_invalid_count"}
    used, limit, remaining = (counts.get(key) for key in ("used", "limit", "remaining"))
    if limit is None or (used is None and remaining is None):
        return state
    if limit <= 0 or any(value is not None and value > limit for value in (used, remaining)):
        return {**state, "quota_error": "hunt_quota_invalid_bounds"}
    if used is None:
        used = limit - remaining
    if remaining is not None and used + remaining != limit:
        return {**state, "quota_error": "hunt_quota_inconsistent"}
    if error_panel and "hunt" in body and "hunt" in dwelling and body["hunt"] != dwelling["hunt"]:
        return {**state, "quota_error": "hunt_quota_conflicting_panels"}
    return {**state, "quota_verified": True, "quota_known": True, "quota_error": "",
            "games_used": used, "games_limit": limit, "games_remaining": limit - used}


def treasure_quota_exhausted(state):
    if not isinstance(state, dict) or state.get("in_round") or state.get("outcome_unknown"):
        return False
    if state.get("daily_limit_confirmed") is True and not state.get("quota_present") and not state.get("quota_error"):
        return True
    if state.get("quota_verified") is not True or state.get("quota_error"):
        return False
    used, limit = (treasure_integer(state.get(key)) for key in ("games_used", "games_limit"))
    remaining = treasure_integer(state.get("games_remaining", 0))
    return used is not None and limit is not None and limit > 0 and used == limit and remaining == 0


def _reward_counts(value):
    if isinstance(value, list):
        entries = [(item, "") for item in value]
    elif isinstance(value, dict):
        entries = [(value, "")] if any(key in value for key in _NAME_KEYS) else [(item, name) for name, item in value.items()]
    else:
        return {}, set(), True, "hunt_reward_invalid_container"
    counts, blocked, unsafe_name, error = {}, set(), False, ""
    for item, fallback in entries:
        names = [item[key] for key in _NAME_KEYS if key in item] if isinstance(item, dict) else []
        if not names and fallback:
            names = [fallback]
        valid_names = {name.strip() for name in names if isinstance(name, str) and name.strip()}
        if not names or len(valid_names) != 1 or any(not isinstance(name, str) or not name.strip() for name in names):
            blocked.update(valid_names)
            unsafe_name = unsafe_name or not valid_names
            error = "hunt_reward_name_invalid"
            continue
        name = next(iter(valid_names))
        quantities = ([treasure_integer(item[key]) for key in _QUANTITY_KEYS if key in item]
                      if isinstance(item, dict) else [treasure_integer(item)])
        if not quantities or None in quantities or len(set(quantities)) != 1:
            blocked.add(name)
            error = "hunt_reward_quantity_invalid"
            continue
        counts[name] = counts.get(name, 0) + quantities[0]
        if treasure_integer(counts[name]) is None:
            blocked.add(name)
            error = "hunt_reward_quantity_invalid"
    return {name: count for name, count in counts.items() if name not in blocked}, blocked, unsafe_name, error


def _text_counts(lines):
    rewards, gains = {}, {}
    for line in lines:
        for match in _ITEM_TEXT_RE.finditer(line):
            name = (match.group("bracket") or match.group("plain")).strip(" \uff1a:\uff0c,\u3002")
            name = re.sub(r"^(?:\u83b7\u5f97|\u5956\u52b1|\u6536\u83b7|\u6389\u843d|\u6218\u5229\u54c1|\u6750\u6599)", "", name).strip()
            amount = treasure_integer(match.group("count").replace(",", ""))
            if name and amount and name not in {"\u795e\u8bc6", "\u6e38\u620f", "\u6b21\u6570"}:
                rewards[name] = rewards.get(name, 0) + amount
        for match in _GAIN_TEXT_RE.finditer(line):
            amount = treasure_integer(match.group("count").replace(",", ""))
            if amount:
                name = match.group("name")
                gains[name] = gains.get(name, 0) + amount
    return rewards, gains


def treasure_completion_error(value):
    if any(key in value and value[key] is not True for key in _TERMINAL_FLAGS):
        return "hunt_settlement_not_completed"
    if "status" in value and (not isinstance(value["status"], str) or value["status"].strip().lower() not in _TERMINAL_STATUSES):
        return "hunt_settlement_not_terminal"
    if value.get("error") not in (None, ""):
        return "hunt_settlement_error"
    return ""


def project_treasure_settlement(value, *, session_id=None):
    outcome = {"confirmed": False, "error": "hunt_settlement_missing", "result": {},
               "rewards": {}, "gains": {}, "material_error": ""}
    if not isinstance(value, dict) or not value:
        return outcome
    session_ids = [treasure_session_id(value[key]) for key in ("sessionId", "session_id") if key in value]
    if session_ids and (not all(session_ids) or len(set(session_ids)) != 1
                        or (session_id is not None and session_ids[0] != session_id)):
        return {**outcome, "error": "hunt_settlement_session_mismatch"}
    error = treasure_completion_error(value)
    if error:
        return {**outcome, "error": error}

    confirmed = any(value.get(key) is True for key in _TERMINAL_FLAGS if key not in {"ok", "success"})
    confirmed = confirmed or str(value.get("status") or "").strip().lower() in _TERMINAL_STATUSES
    projected, rewards, gains, text_rewards, text_gains = {}, {}, {}, {}, {}
    blocked_rewards, blocked_gains, unsafe_name, material_error = set(), set(), False, ""
    for key, child in value.items():
        normalized = _key(key)
        if key in ("sessionId", "session_id", *_TERMINAL_FLAGS, "status"):
            projected[key] = child
        elif key in {"foundMain", "found_main"}:
            if type(child) is bool:
                projected[key] = child
        elif key in {"grade", "score"}:
            valid = isinstance(child, str) and bool(child.strip()) if key == "grade" else treasure_integer(child) is not None
            if valid:
                confirmed = True
                projected[key] = child
        elif normalized in _REWARD_KEYS:
            projected[key] = deepcopy(child)
            counts, blocked, unsafe, error = _reward_counts(child)
            confirmed = confirmed or isinstance(child, (list, dict))
            material_error = error or material_error
            blocked_rewards.update(blocked)
            unsafe_name = unsafe_name or unsafe
            for name, amount in counts.items():
                if name in rewards and amount != rewards[name]:
                    blocked_rewards.add(name)
                    material_error = "hunt_reward_alias_conflict"
                rewards[name] = amount
        elif normalized in TREASURE_GAIN_KEYS:
            projected[key] = deepcopy(child)
            name, amount = TREASURE_GAIN_KEYS[normalized], treasure_integer(child)
            if amount is None or (name in gains and gains[name] != amount):
                blocked_gains.add(name)
                material_error = "hunt_gain_invalid_or_conflicting"
            else:
                gains[name] = amount
                confirmed = True
        elif normalized in _TEXT_KEYS:
            lines = [child] if isinstance(child, str) else child
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                material_error = "hunt_reward_text_invalid"
                continue
            projected[key] = deepcopy(child)
            row_rewards, row_gains = _text_counts(lines)
            confirmed = confirmed or bool(row_rewards or row_gains)
            # Logs may mirror a summary, but repeated gains within one log are additive.
            for name, amount in row_rewards.items():
                text_rewards[name] = max(text_rewards.get(name, 0), amount)
            for name, amount in row_gains.items():
                text_gains[name] = max(text_gains.get(name, 0), amount)
    if not confirmed:
        return {**outcome, "error": "hunt_settlement_unrecognized"}
    main_flags = [value[key] for key in ("foundMain", "found_main") if key in value]
    if any(type(flag) is not bool for flag in main_flags) or (main_flags and any(flag != main_flags[0] for flag in main_flags)):
        projected.pop("foundMain", None)
        projected.pop("found_main", None)
        material_error = "hunt_result_main_flag_invalid"
    if not unsafe_name:
        rewards = {**text_rewards, **rewards}
    gains = {**text_gains, **gains}
    return {**outcome, "confirmed": True, "error": "", "result": projected, "material_error": material_error,
            "rewards": {name: amount for name, amount in rewards.items()
                        if name not in blocked_rewards and treasure_integer(amount) not in (None, 0)},
            "gains": {name: amount for name, amount in gains.items()
                      if name not in blocked_gains and treasure_integer(amount) not in (None, 0)}}
