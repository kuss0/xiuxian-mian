"""Current trial finish evidence, separate from transport and future challenges."""

import math
import re


TRIAL_GAIN_KEYS = {
    "expgain": "\u7ecf\u9a8c", "experiencegain": "\u7ecf\u9a8c",
    "tracegain": "\u5929\u673a\u6b8b\u75d5", "tianjitracegain": "\u5929\u673a\u6b8b\u75d5",
    "rewardtrace": "\u5929\u673a\u6b8b\u75d5", "rewardtracegain": "\u5929\u673a\u6b8b\u75d5",
    "cultivationgain": "\u4fee\u4e3a", "xiuweigain": "\u4fee\u4e3a",
    "lingshigain": "\u7075\u77f3", "spiritstonegain": "\u7075\u77f3",
}
TRIAL_REWARD_CONTAINER_KEYS = frozenset({"rewards", "reward", "bonusloot", "loot", "drops", "items", "materials"})
_RESULT_METADATA = frozenset({
    "score", "grade", "trial_title", "trialTitle", "title", "balance", "traceBalance", "trace_balance",
    "settled_in_app", "settledInApp", "daily_progress", "daily_limit",
})
_QUOTA_ALIASES = {
    "completed": ("completed", "done"),
    "limit": ("limit", "dailyLimit"),
    "remaining": ("remaining", "remainingToday", "dailyRemaining", "remaining_today", "remainingCount"),
}
_LEGACY_QUOTA_ALIASES = {
    "completed": ("daily_progress",), "limit": ("daily_limit",), "remaining": ("daily_remaining",),
}
_ITEM_NAME_KEYS = ("name", "itemName", "item_name", "title", "label")
_ITEM_QUANTITY_KEYS = ("qty", "count", "quantity", "amount")
_TERMINAL_STATUSES = frozenset({"settled", "completed", "done", "success"})


def normalize_trial_result_key(key):
    return re.sub(r"[^A-Za-z0-9]", "", str(key or "")).lower()


def _exact_integer(value, *, signed=False):
    if type(value) is int:
        number = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+" if signed else r"[0-9]+", value.strip()):
        text = value.strip()
        if len(text) > 17:
            return None
        number = int(text)
    else:
        return None
    if abs(number) > 2**53 - 1 or (not signed and number < 0):
        return None
    return number


def normalize_trial_player_id(value):
    if value is None:
        return None
    player_id = _exact_integer(value, signed=True)
    if player_id in (None, 0):
        raise ValueError("trial_player_id_invalid")
    return player_id


def normalize_trial_challenge_id(value):
    if type(value) is int and abs(value) <= 2**53 - 1:
        return str(value)
    return value.strip() if isinstance(value, str) else ""


def _reward_item(value, *, fallback_name=""):
    if isinstance(value, str) and not fallback_name:
        return ({"name": value.strip(), "qty": 1}, "") if value.strip() else ({}, "trial_reward_name_missing")
    if isinstance(value, dict):
        names = [value[key] for key in _ITEM_NAME_KEYS if key in value]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            return {}, "trial_reward_name_invalid"
        if names and len({name.strip() for name in names}) != 1:
            return {}, "trial_reward_name_conflict"
        name = names[0].strip() if names else fallback_name
        quantities = [_exact_integer(value[key]) for key in _ITEM_QUANTITY_KEYS if key in value]
        if not quantities or None in quantities:
            return {}, "trial_reward_quantity_invalid"
        if len(set(quantities)) != 1:
            return {}, "trial_reward_quantity_conflict"
        quantity = quantities[0]
    else:
        name, quantity = fallback_name, _exact_integer(value)
    if not name or quantity is None:
        return {}, "trial_reward_item_invalid"
    return ({"name": name, "qty": quantity}, "") if quantity else ({}, "")


def parse_trial_rewards(value):
    if isinstance(value, list):
        inputs = [(item, "") for item in value]
    elif isinstance(value, dict):
        if any(key in value for key in _ITEM_NAME_KEYS):
            inputs = [(value, "")]
        else:
            inputs = [(amount, name) for name, amount in value.items()]
    else:
        return [], "trial_rewards_invalid_container"
    rewards, error = [], ""
    for item, fallback in inputs:
        if not isinstance(fallback, str) or (fallback and not fallback.strip()):
            error = "trial_reward_name_invalid"
            continue
        reward, item_error = _reward_item(item, fallback_name=fallback.strip())
        error = item_error or error
        if reward:
            rewards.append(reward)
    return rewards, error


def _finish_body(data):
    if not isinstance(data, dict) or data.get("ok") is not True:
        return {}, "trial_finish_not_ok"
    nested = data.get("data")
    if isinstance(nested, dict) and "result" in nested:
        if "result" in data or "dailyProgress" in data or "nextTrial" in data:
            return {}, "trial_finish_ambiguous_envelope"
        return nested, ""
    return data, ""


def _binding_error(containers, *, challenge_id, player_id):
    if not challenge_id:
        return "trial_finish_challenge_missing"
    try:
        expected_player = normalize_trial_player_id(player_id)
    except ValueError:
        return "trial_finish_player_mismatch"
    observed_player = expected_player
    for container in containers:
        for key in ("challengeId", "challenge_id"):
            if key in container:
                if normalize_trial_challenge_id(container[key]) != challenge_id:
                    return "trial_finish_challenge_mismatch"
        for key in ("playerId", "player_id"):
            if key in container:
                actual = _exact_integer(container[key], signed=True)
                if actual in (None, 0) or (observed_player is not None and actual != observed_player):
                    return "trial_finish_player_mismatch"
                observed_player = actual
    return ""


def _finish_quota(body, result):
    sources = []
    for key in ("dailyProgress", "nextTrial"):
        if key not in body or (key == "nextTrial" and body[key] is None):
            continue
        if not isinstance(body[key], dict):
            return {}, "trial_quota_invalid_container"
        sources.append((body[key], _QUOTA_ALIASES))
    sources.append((result, _LEGACY_QUOTA_ALIASES))
    quota = {}
    for source, aliases in sources:
        for field, keys in aliases.items():
            for key in keys:
                if key not in source:
                    continue
                value = _exact_integer(source[key])
                if value is None or (field == "limit" and value == 0):
                    return {}, "trial_quota_invalid_value"
                if field in quota and quota[field] != value:
                    return {}, "trial_quota_conflict"
                quota[field] = value
    completed, limit, remaining = (quota.get(key) for key in ("completed", "limit", "remaining"))
    if limit is not None:
        if any(value is not None and value > limit for value in (completed, remaining)):
            return {}, "trial_quota_invalid_bounds"
        if completed is not None:
            if remaining is not None and completed + remaining != limit:
                return {}, "trial_quota_inconsistent"
            quota["remaining"] = limit - completed
        elif remaining is not None:
            quota["completed"] = limit - remaining
    return quota, ""


def parse_trial_finish_receipt(data, *, challenge_id, player_id=None):
    body, error = _finish_body(data)
    outcome = {
        "confirmed": False, "error": error, "result": {}, "material_error": "",
        "quota": {}, "quota_error": "", "body": body,
    }
    if error:
        return outcome
    result = body.get("result")
    if not isinstance(result, dict) or not result:
        outcome["error"] = "trial_finish_result_missing"
        return outcome
    scopes = (data, body, result)
    error = _binding_error(scopes, challenge_id=normalize_trial_challenge_id(challenge_id), player_id=player_id)
    if error:
        outcome["error"] = error
        return outcome
    for scope in scopes:
        for key in ("ok", "settled_in_app", "settledInApp"):
            if key in scope and scope[key] is not True:
                outcome["error"] = "trial_finish_not_settled"
                return outcome
        if "status" in scope and (
            not isinstance(scope["status"], str)
            or scope["status"].strip().lower() not in _TERMINAL_STATUSES
        ):
            outcome["error"] = "trial_finish_not_terminal"
            return outcome
        if scope.get("error") not in (None, ""):
            outcome["error"] = "trial_finish_result_error"
            return outcome

    confirmed = (
        any(result.get(key) is True for key in ("settled_in_app", "settledInApp"))
        or str(result.get("status") or "").strip().lower() in _TERMINAL_STATUSES
    )
    projected = {}
    gain_fields, conflicting_gains = {}, set()
    material_error = ""
    for key, value in result.items():
        normalized = normalize_trial_result_key(key)
        if normalized == "reward" and not isinstance(value, (dict, list)):
            key, normalized = "reward_trace", "rewardtrace"
        if normalized in TRIAL_GAIN_KEYS:
            label = TRIAL_GAIN_KEYS[normalized]
            amount = _exact_integer(value)
            if amount is None:
                material_error = "trial_finish_invalid_gain"
                conflicting_gains.add(label)
            else:
                confirmed = True
            if label in gain_fields:
                previous_key, previous_amount = gain_fields[label]
                if amount != previous_amount:
                    conflicting_gains.add(label)
                    material_error = "trial_finish_gain_conflict"
                if label in conflicting_gains:
                    projected.pop(previous_key, None)
                continue
            gain_fields[label] = key, amount
            if label not in conflicting_gains:
                projected[key] = amount
        elif key == "score":
            if _exact_integer(value) is None:
                material_error = "trial_finish_invalid_score"
                continue
            confirmed = True
            projected[key] = value
        elif normalized in TRIAL_REWARD_CONTAINER_KEYS:
            rewards, reward_error = parse_trial_rewards(value)
            material_error = reward_error or material_error
            confirmed = confirmed or bool(rewards) or (bool(value) and not reward_error)
            projected[key] = rewards
        elif key in _RESULT_METADATA and type(value) in (str, int, float, bool):
            projected[key] = value
    if not confirmed:
        outcome["error"] = "trial_finish_result_unconfirmed"
        return outcome
    quota, quota_error = _finish_quota(body, result)
    outcome.update(confirmed=True, result=projected, material_error=material_error, quota=quota, quota_error=quota_error)
    return outcome
