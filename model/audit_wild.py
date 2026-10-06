"""Pure projection of one confirmed journey receipt, never account balances."""

import hashlib
import json


def _integer(value, *, positive=False):
    if type(value) is not int or abs(value) > 10**18 or (positive and value <= 0):
        raise ValueError("invalid wild receipt number")
    return value


def validate_wild_outcome(value, identity_id):
    if not isinstance(value, dict) or set(value) != {"identity_id", "reset_at", "run", "last_at", "gains"}:
        raise ValueError("invalid wild receipt")
    if _integer(value["identity_id"], positive=True) != identity_id or type(identity_id) is not int:
        raise ValueError("wild receipt owner mismatch")
    for key in ("reset_at", "run", "last_at"):
        _integer(value[key], positive=True)
    if value["last_at"] >= value["reset_at"]:
        raise ValueError("invalid wild receipt window")
    gains = value["gains"]
    if not isinstance(gains, dict) or len(gains) > 32:
        raise ValueError("invalid wild receipt gains")
    for name, amount in gains.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("invalid wild receipt item")
        _integer(amount)
    return value


def confirmed_wild_outcome(identity_id, response):
    """Called only by the identity-checked wild producer after business reduction."""
    if not isinstance(response, dict) or response.get("ok") is not True:
        return None
    extra = response.get("extra")
    if (not isinstance(extra, dict) or any(extra.get(key) is not True for key in
            ("acted", "completed", "transport_ok")) or extra.get("outcome_unknown") is not False
            or extra.get("phase") != "completed"):
        return None
    action, before, after = (extra.get(key) for key in ("action_result", "before_wild", "wild"))
    if not all(isinstance(part, dict) for part in (action, before, after)):
        return None
    if action.get("ok") is not True or action.get("completed") is not True or action.get("type") != "wild_experience":
        return None
    try:
        run = _integer(action.get("dailyCount"), positive=True)
        if (run != _integer(before.get("daily_count")) + 1 or run != _integer(after.get("daily_count"))
                or run > _integer(after.get("daily_limit"), positive=True)
                or before.get("reset_at") != after.get("reset_at")
                or action.get("lastAt") != after.get("last_at")):
            return None
        gains = {"修为": _integer(action.get("cultivationDelta"))}
        for key, name in (("tianjiGain", "天机"), ("contributionGain", "贡献")):
            if key in action:
                gains[name] = _integer(action[key])
        loot = action.get("loot")
        if not isinstance(loot, list) or len(loot) > 32:
            return None
        for item in loot:
            if not isinstance(item, dict):
                return None
            name = item.get("name") or item.get("itemId")
            amount = item.get("quantity", item.get("qty", item.get("count")))
            if not isinstance(name, str):
                return None
            gains[name] = gains.get(name, 0) + _integer(amount)
        return validate_wild_outcome({
            "identity_id": identity_id, "reset_at": after.get("reset_at"), "run": run,
            "last_at": action.get("lastAt"), "gains": gains,
        }, identity_id)
    except (ValueError, TypeError):
        return None


def wild_bucket_key(outcome):
    # Distinct versions remain inspectable; the renderer excludes contradictions.
    encoded = json.dumps(outcome, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return "wild_outcome:" + hashlib.sha256(encoded.encode("ascii")).hexdigest()
