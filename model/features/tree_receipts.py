"""Current tree allocation and submit receipts, without historical inference."""

import hashlib
import math
import re

from ..webapp_core import sanitize_webapp_secret_text


_ITEM_KEYS = {"reward", "rewards", "bonusloot", "loot", "items", "materials", "drops"}
_NAME_KEYS = ("name", "itemName", "label", "title")
_COUNT_KEYS = ("qty", "quantity", "count", "amount")
_GAIN_KEYS = {
    "expgain": "\u7ecf\u9a8c", "experiencegain": "\u7ecf\u9a8c",
    "cultivationgain": "\u4fee\u4e3a", "xiuweigain": "\u4fee\u4e3a",
    "lingshigain": "\u7075\u77f3", "stonegain": "\u7075\u77f3",
    "contributiongain": "\u8d21\u732e", "tracegain": "\u5929\u673a\u6b8b\u75d5",
}
_TEXT_BINDINGS = {"runToken", "seed", "seasonId", "playDate"}
_NUMBER_BINDINGS = {"runNo", "dayIndex"}
_BINDINGS = _TEXT_BINDINGS | _NUMBER_BINDINGS | {"mode", "playerId", "player_id", "player"}
_CURRENT_KEYS = {"run", "score", "verified", "council", "seasonState", "tree", "ranking"} | _BINDINGS


def tree_integer(value):
    if type(value) is int:
        number = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and len(value.strip()) <= 16 and re.fullmatch(r"[0-9]+", value.strip()):
        number = int(value.strip())
    else:
        return None
    return number if 0 <= number <= 2**53 - 1 else None


def normalize_tree_player_id(value):
    if isinstance(value, str) and len(value.strip()) <= 17 and re.fullmatch(r"-?[0-9]+", value.strip()):
        value = int(value.strip())
    if type(value) is float and math.isfinite(value) and value.is_integer():
        value = int(value)
    if type(value) is not int or value == 0 or abs(value) > 2**53 - 1:
        raise ValueError("tree_player_invalid")
    if value <= -1_000_000_000_000:
        value = -value - 1_000_000_000_000
    if value <= 0:
        raise ValueError("tree_player_invalid")
    return value


def tree_response_body(data):
    if not isinstance(data, dict):
        return {}, "tree_response_missing"
    if any(not isinstance(key, str) for key in data):
        return {}, "tree_response_invalid_key"
    if "ok" in data and data["ok"] is not True or data.get("error") not in (None, ""):
        return {}, "tree_response_not_ok"
    if "data" in data:
        if not isinstance(data["data"], dict) or "data" in data["data"]:
            return {}, "tree_response_invalid_envelope"
        if any(key in _CURRENT_KEYS or key.lower() in _ITEM_KEYS | _GAIN_KEYS.keys() for key in data):
            return {}, "tree_response_conflicting_envelopes"
        data = data["data"]
        if any(not isinstance(key, str) for key in data):
            return {}, "tree_response_invalid_key"
        if "ok" in data and data["ok"] is not True or data.get("error") not in (None, ""):
            return {}, "tree_response_not_ok"
    return data, ""


def _bind(scopes, expected):
    context = dict(expected)
    for scope in scopes:
        if (not isinstance(scope, dict) or "ok" in scope and scope["ok"] is not True
                or scope.get("error") not in (None, "")):
            return {}, "tree_binding_invalid"
        values = [(key, scope[key]) for key in _BINDINGS - {"player"} if key in scope]
        if "player" in scope:
            if not isinstance(scope["player"], dict):
                return {}, "tree_player_invalid"
            values.extend(("playerId", scope["player"][key]) for key in ("id", "playerId", "player_id")
                          if key in scope["player"])
        for key, value in values:
            if key in {"playerId", "player_id"}:
                key = "player_id"
                try:
                    value = normalize_tree_player_id(value)
                except ValueError:
                    return {}, "tree_player_invalid"
            elif key in _NUMBER_BINDINGS:
                value = tree_integer(value)
                if value is None or key == "runNo" and value == 0:
                    return {}, "tree_round_invalid"
            else:
                if not isinstance(value, str) or not value.strip() or len(value) > 512:
                    return {}, "tree_binding_invalid"
                value = value.strip()
                if key == "mode" and value not in {"jump", "fly"}:
                    return {}, "tree_mode_invalid"
            if key in context and context[key] != value:
                return {}, "tree_binding_mismatch"
            context[key] = value
    return context, ""


def tree_panel_context(data, *, player_id=None):
    body, error = tree_response_body(data)
    if error:
        return {}, error
    try:
        expected = {} if player_id is None else {"player_id": normalize_tree_player_id(player_id)}
    except ValueError as exc:
        return {}, str(exc)
    council = body.get("council")
    season = council.get("season") if isinstance(council, dict) else None
    fields = {"player", "playerId", "player_id", "seasonId", "playDate", "dayIndex", "ok", "error"}
    scopes = [body, season] if isinstance(season, dict) else [body]
    return _bind([{key: value for key, value in scope.items() if key in fields} for scope in scopes], expected)


def parse_tree_allocation(data, *, mode, context):
    body, error = tree_response_body(data)
    run = body.get("run")
    if error or not isinstance(run, dict):
        return {}, {}, error or "tree_run_missing"
    bound, error = _bind([body, run], {**context, "mode": mode})
    if error:
        return {}, {}, error
    if not all(key in run and key in bound for key in ("runToken", "seed")):
        return {}, {}, "tree_run_missing_credentials"
    for key in ("used", "limit"):
        if key in run and tree_integer(run[key]) is None:
            return {}, {}, "tree_run_invalid_count"
    if "used" in run and "limit" in run and tree_integer(run["used"]) > tree_integer(run["limit"]):
        return {}, {}, "tree_run_invalid_count"
    return dict(run), bound, ""


def tree_round_key(context):
    return hashlib.sha256(f"{context['mode']}\0{context['runToken']}".encode()).hexdigest()


def _item_counts(value):
    if isinstance(value, list):
        entries = [(item, "") for item in value]
    elif isinstance(value, dict):
        entries = [(value, "")] if any(key in value for key in _NAME_KEYS) else [(item, name) for name, item in value.items()]
    else:
        return {}, set(), "tree_reward_container_invalid"
    counts, blocked, error = {}, set(), ""
    for item, fallback in entries:
        names = [item[key] for key in _NAME_KEYS if key in item] if isinstance(item, dict) else []
        if not names and fallback:
            names = [fallback]
        valid_names = {name.strip() for name in names if isinstance(name, str) and name.strip()}
        if (len(valid_names) != 1 or not names or any(not isinstance(name, str) or not name.strip() for name in names)
                or any(len(name) > 80 or sanitize_webapp_secret_text(name, limit=81) != name for name in valid_names)):
            blocked.update(valid_names)
            error = "tree_reward_name_invalid"
            continue
        name = next(iter(valid_names))
        quantities = ([tree_integer(item[key]) for key in _COUNT_KEYS if key in item]
                      if isinstance(item, dict) else [tree_integer(item)])
        if not quantities or None in quantities or len(set(quantities)) != 1:
            blocked.add(name)
            error = "tree_reward_quantity_invalid"
            continue
        counts[name] = counts.get(name, 0) + quantities[0]
        if tree_integer(counts[name]) is None:
            blocked.add(name)
            error = "tree_reward_quantity_invalid"
    return {name: count for name, count in counts.items() if name not in blocked}, blocked, error


def parse_tree_rewards(data):
    body, error = tree_response_body(data)
    items, gains, blocked_items, blocked_gains = {}, {}, set(), set()
    if error:
        return {"items": items, "gains": gains}, error
    for key, value in body.items():
        key = key.lower()
        if key in _ITEM_KEYS:
            counts, blocked, item_error = _item_counts(value)
            blocked_items.update(blocked)
            error = item_error or error
            for name, amount in counts.items():
                if name in items and amount != items[name]:
                    blocked_items.add(name)
                    error = "tree_reward_alias_conflict"
                items[name] = amount
        elif key in _GAIN_KEYS:
            name, amount = _GAIN_KEYS[key], tree_integer(value)
            if amount is None or name in gains and amount != gains[name]:
                blocked_gains.add(name)
                error = "tree_gain_invalid"
            else:
                gains[name] = amount
    return {"items": {name: amount for name, amount in items.items() if amount and name not in blocked_items},
            "gains": {name: amount for name, amount in gains.items() if amount and name not in blocked_gains}}, error


def parse_tree_submit(data, *, context):
    receipt = {"confirmed": False, "error": "", "score": None, "verification": {},
               "rewards": {"items": {}, "gains": {}}, "material_error": ""}
    body, error = tree_response_body(data)
    scopes = [body, body["run"]] if "run" in body else [body]
    _, binding_error = _bind(scopes, context)
    error = error or binding_error
    if error:
        return {**receipt, "error": error}
    rewards, material_error = parse_tree_rewards(body)
    receipt.update(rewards=rewards, material_error=material_error, round_key=tree_round_key(context))
    score = tree_integer(body.get("score"))
    if score is None:
        return {**receipt, "error": "tree_score_missing_or_invalid"}
    verification = {}
    if "verified" in body:
        value = body["verified"]
        if not isinstance(value, dict):
            return {**receipt, "error": "tree_verification_invalid"}
        for key in ("ok", "hit", "gameOver"):
            if key in value:
                if type(value[key]) is not bool:
                    return {**receipt, "error": "tree_verification_invalid"}
                verification[key] = value[key]
        for key in ("score", "durationMs", "steps", "centerSteps", "exactCenters", "bestCenterCombo", "maxEvents"):
            if key in value:
                number = tree_integer(value[key])
                if number is None:
                    return {**receipt, "error": "tree_verification_invalid"}
                verification[key] = number
        if verification.get("ok") is False:
            score = 0
        elif "score" in verification and verification["score"] != score:
            return {**receipt, "error": "tree_verification_score_conflict"}
    return {**receipt, "confirmed": True, "score": score, "verification": verification,
            "rewards": rewards, "material_error": material_error}
