TREE_MINIAPP_MODES = {"jump", "fly"}
TREE_MINIAPP_DEFAULT_TARGET_SCORE = {
    "jump": (30, 36),
    "fly": (8, 12),
}
TREE_MINIAPP_MIN_TARGET_SCORE = {
    "jump": 4,
    "fly": 4,
}
TREE_MINIAPP_MAX_TARGET_SCORE = {
    "jump": 45,
    "fly": 20,
}
TREE_MINIAPP_MIN_TARGET_SPREAD = {
    "jump": 6,
    "fly": 4,
}


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _clamp_int(value, lower, upper):
    return max(int(lower), min(int(upper), int(value)))


def _expand_target_range(mode, low, high):
    floor = int(TREE_MINIAPP_MIN_TARGET_SCORE.get(mode, 20))
    cap = int(TREE_MINIAPP_MAX_TARGET_SCORE.get(mode, 45))
    low = _clamp_int(low, floor, cap)
    high = _clamp_int(high, floor, cap)
    if high < low:
        low, high = high, low

    min_spread = min(max(0, cap - floor), int(TREE_MINIAPP_MIN_TARGET_SPREAD.get(mode, 8)))
    if min_spread <= 0 or high - low >= min_spread:
        return low, high

    center = int(round((low + high) / 2))
    low = center - min_spread // 2
    high = low + min_spread
    if low < floor:
        low = floor
        high = floor + min_spread
    if high > cap:
        high = cap
        low = cap - min_spread
    return int(low), int(high)


def normalize_tree_score_profile(mode, profile=None):
    mode = str(mode or "").strip().lower()
    if mode not in TREE_MINIAPP_MODES:
        raise ValueError("tree miniapp mode must be jump or fly")
    source = dict(profile or {})
    default_low, default_high = TREE_MINIAPP_DEFAULT_TARGET_SCORE[mode]
    low = default_low
    high = default_high

    if "target_score_range" in source:
        try:
            raw_low, raw_high = source.get("target_score_range") or ()
            low = _int_or(raw_low, default_low)
            high = _int_or(raw_high, default_high)
        except (TypeError, ValueError):
            low, high = default_low, default_high
    if "target_score" in source:
        score = _int_or(source.get("target_score"), default_low)
        low = high = score

    low, high = _expand_target_range(mode, low, high)
    normalized = dict(source)
    normalized["target_score_range"] = (low, high)
    normalized.pop("target_score", None)
    return normalized


def normalize_tree_score_records(records):
    if not isinstance(records, dict):
        return {}
    normalized = {}
    for raw_identity_id, raw_config in records.items():
        if not isinstance(raw_config, dict):
            continue
        identity_key = str(raw_identity_id or "").strip()
        if not identity_key:
            continue
        item = {}
        for mode in ("jump", "fly"):
            if mode in raw_config:
                item[mode] = normalize_tree_score_profile(mode, raw_config.get(mode))
        if item:
            normalized[identity_key] = item
    return normalized
