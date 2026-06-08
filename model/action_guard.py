import random
import time

from .config import (
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_TIANJI,
    CMD_DEEP_RETREAT,
    CMD_DIVINATION,
    CMD_DIVINATION_EXCHANGE,
    CMD_HEHUAN_DUAL,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_PET_WARM,
    CMD_PET_TRIAL,
    CMD_RANCH,
    CMD_SECOND_SOUL_TRAIN,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_RELIEF,
    CMD_SMALL_WORLD_REFINE,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
    CMD_TOWER,
    CMD_WILD_TRAINING,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_CURSE,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_POSSESS,
    CMD_YINDAO,
    CMD_YUANYING,
)
from .persistence import mark_dirty
from .state import has_identity, state, use_identity


ACTION_KIND_HIGH_RISK = "high_risk"
ACTION_KIND_REFRESH = "refresh"
ACTION_KIND_STATUS = "status"
ACTION_KIND_CHAIN = "chain"

RETRY_DELAY_RANGES_SEC = (
    (2 * 60, 3 * 60),
    (3 * 60, 5 * 60),
    (10 * 60, 30 * 60),
)
SESSION_MAX_ATTEMPTS = 1 + len(RETRY_DELAY_RANGES_SEC)
SESSION_TTL_SEC = 8 * 3600
BLOCK_LOG_INTERVAL_SEC = 10 * 60


ACTION_SPECS = {
    "concubine_dream": {
        "commands": (CMD_CONCUBINE_DREAM,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "入梦寻图",
    },
    "concubine_tianji": {
        "commands": (CMD_CONCUBINE_TIANJI,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天机代卜",
    },
    "concubine_heart": {
        "commands": (CMD_CONCUBINE_HEART,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "共历心劫",
        "max_attempts": 2,
        "retry_delay_ranges": ((10 * 60, 30 * 60),),
        "ttl_sec": 12 * 3600,
    },
    "concubine_fragment": {
        "commands": (CMD_CONCUBINE_FRAGMENT,),
        "kind": ACTION_KIND_CHAIN,
        "label": "残图确认",
    },
    "concubine_puzzle": {
        "commands": (CMD_CONCUBINE_PUZZLE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "虚天拼图",
    },
    "concubine_reacquire": {
        "commands": (CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "补领侍妾",
    },
    "pet_trial": {
        "commands": (CMD_PET_TRIAL,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "器灵试炼",
    },
    "pet_warm": {
        "commands": (CMD_PET_WARM,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "温养器灵",
    },
    "ranch": {
        "commands": (CMD_RANCH,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "一键放养",
        "max_attempts": 2,
        "retry_delay_ranges": ((2 * 60, 3 * 60),),
        "ttl_sec": 30 * 60,
    },
    "wild_training": {
        "commands": (CMD_WILD_TRAINING,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "野外历练",
        "max_attempts": 2,
        "retry_delay_ranges": ((2 * 60, 3 * 60),),
        "ttl_sec": 30 * 60,
    },
    "second_soul_train": {
        "commands": (CMD_SECOND_SOUL_TRAIN,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "第二元神修炼",
    },
    "deep_retreat": {
        "commands": (CMD_DEEP_RETREAT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "深度闭关",
    },
    "yuanying_launch": {
        "commands": (CMD_YUANYING,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "元婴出窍",
    },
    "tower": {
        "commands": (CMD_TOWER,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "闯塔",
    },
    "taiyi_yindao": {
        "commands": (CMD_YINDAO,),
        "kind": ACTION_KIND_CHAIN,
        "label": "太一引道",
    },
    "taiyi_node_search": {
        "commands": (CMD_NODE_SEARCH,),
        "kind": ACTION_KIND_CHAIN,
        "label": "搜寻节点",
    },
    "taiyi_node_define": {
        "commands": (CMD_NODE_DEFINE,),
        "kind": ACTION_KIND_CHAIN,
        "label": "定星",
    },
    "small_world_preach": {
        "commands": (CMD_SMALL_WORLD_PREACH, CMD_SMALL_WORLD_RELIEF),
        "kind": ACTION_KIND_REFRESH,
        "label": "小世界神迹维护",
        "max_attempts": 2,
        "retry_delay_ranges": ((2 * 60, 3 * 60),),
        "ttl_sec": 30 * 60,
    },
    "small_world_query": {
        "commands": (CMD_SMALL_WORLD_QUERY,),
        "kind": ACTION_KIND_REFRESH,
        "label": "小世界查询/刷新",
        "max_attempts": 10,
        "retry_delay_ranges": ((5 * 60, 8 * 60),) * 9,
        "ttl_sec": 90 * 60,
    },
    "small_world_manifest": {
        "commands": (CMD_SMALL_WORLD_MANIFEST,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "小世界显灵",
    },
    "small_world_harvest": {
        "commands": (CMD_SMALL_WORLD_HARVEST,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "收割香火",
    },
    "small_world_refine": {
        "commands": (CMD_SMALL_WORLD_REFINE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "神识淬炼",
    },
    "divination": {
        "commands": (CMD_DIVINATION,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "卜筮问天",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "divination_exchange": {
        "commands": (CMD_DIVINATION_EXCHANGE,),
        "kind": ACTION_KIND_CHAIN,
        "label": "卜筮换取",
        "max_attempts": 1,
        "ttl_sec": 5 * 60,
    },
    "hehuan_dual": {
        "commands": (CMD_HEHUAN_DUAL,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "合欢温养",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "tianxing_panel": {
        "commands": (CMD_TIANXING_PANEL,),
        "kind": ACTION_KIND_STATUS,
        "label": "天机盘",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
    },
    "tianxing_observe": {
        "commands": (CMD_TIANXING_OBSERVE,),
        "kind": ACTION_KIND_STATUS,
        "label": "观命",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
    },
    "tianxing_set_star": {
        "commands": (CMD_TIANXING_SET_STAR,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天星定命",
        "max_attempts": 1,
        "ttl_sec": 12 * 3600,
    },
    "tianxing_predict": {
        "commands": (CMD_TIANXING_PREDICT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天星推命",
        "max_attempts": 1,
        "ttl_sec": 8 * 3600,
    },
    "tianxing_change_fate": {
        "commands": (CMD_TIANXING_CHANGE_FATE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天星改命",
        "max_attempts": 1,
        "ttl_sec": 24 * 3600,
    },
    "tianxing_clear_calamity": {
        "commands": (CMD_TIANXING_CLEAR_CALAMITY,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天星消劫",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "yinluo_banner": {
        "commands": (CMD_YINLUO_BANNER,),
        "kind": ACTION_KIND_STATUS,
        "label": "阴罗幡查询",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
    },
    "yinluo_demon_summon": {
        "commands": (CMD_YINLUO_DEMON_SUMMON,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "召唤魔影",
        "max_attempts": 1,
        "ttl_sec": 4 * 3600,
    },
    "yinluo_convert": {
        "commands": (CMD_YINLUO_CONVERT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "化功为煞",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "yinluo_collect": {
        "commands": (CMD_YINLUO_COLLECT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "收取幡魂",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "yinluo_blood_forest": {
        "commands": (CMD_YINLUO_BLOOD_FOREST,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "血洗山林",
        "max_attempts": 1,
        "ttl_sec": 4 * 3600,
    },
    "yinluo_curse": {
        "commands": (CMD_YINLUO_CURSE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "阴罗下咒",
        "max_attempts": 1,
        "ttl_sec": 4 * 3600,
    },
    "yinluo_possess": {
        "commands": (CMD_YINLUO_POSSESS,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "阴罗夺舍",
        "max_attempts": 1,
        "ttl_sec": 24 * 3600,
    },
}

COMMAND_TO_ACTION_KEY = {
    command: action_key
    for action_key, spec in ACTION_SPECS.items()
    for command in spec.get("commands", ())
}

FAMILY_TO_ACTION_KEYS = {
    "concubine_dream": ("concubine_dream",),
    "concubine_tianji": ("concubine_tianji",),
    "concubine_fragment": ("concubine_fragment",),
    "concubine_puzzle": ("concubine_puzzle",),
    "concubine_reacquire": ("concubine_reacquire",),
    "pet_trial": ("pet_trial",),
    "pet_warm": ("pet_warm",),
    "ranch": ("ranch",),
    "wild_training": ("wild_training",),
    "second_soul_train": ("second_soul_train",),
    "deep_retreat": ("deep_retreat",),
    "tower": ("tower",),
    "yuanying": ("yuanying_launch",),
    "taiyi_yindao": ("taiyi_yindao",),
    "taiyi_node_search": ("taiyi_node_search",),
    "taiyi_node_define": ("taiyi_node_define",),
    "small_world_preach": ("small_world_preach",),
    "small_world_relief": ("small_world_preach",),
    "small_world_query": ("small_world_query",),
    "small_world_manifest": ("small_world_manifest",),
    "small_world_harvest": ("small_world_harvest",),
    "small_world_refine": ("small_world_refine",),
    "divination": ("divination",),
    "divination_exchange": ("divination_exchange",),
    "hehuan_dual": ("hehuan_dual",),
    "tianxing_panel": ("tianxing_panel",),
    "tianxing_observe": ("tianxing_observe",),
    "tianxing_set_star": ("tianxing_set_star",),
    "tianxing_predict": ("tianxing_predict",),
    "tianxing_change_fate": ("tianxing_change_fate",),
    "tianxing_clear_calamity": ("tianxing_clear_calamity",),
    "yinluo_banner": ("yinluo_banner",),
    "yinluo_demon_summon": ("yinluo_demon_summon",),
    "yinluo_convert": ("yinluo_convert",),
    "yinluo_collect": ("yinluo_collect",),
    "yinluo_blood_forest": ("yinluo_blood_forest",),
    "yinluo_curse": ("yinluo_curse",),
    "yinluo_possess": ("yinluo_possess",),
}


def normalize_command(command):
    return str(command or "").strip()


def resolve_action_key(command):
    raw_command = normalize_command(command)
    if not raw_command:
        return ""
    for prefix, action_key in COMMAND_TO_ACTION_KEY.items():
        if raw_command == prefix or raw_command.startswith(f"{prefix} "):
            return action_key
    return ""


def resolve_action_key_for_family(family):
    keys = FAMILY_TO_ACTION_KEYS.get(str(family or "").strip(), ())
    return keys[0] if keys else ""


def resolve_action_keys_for_family(family):
    return tuple(FAMILY_TO_ACTION_KEYS.get(str(family or "").strip(), ()))


def _get_sessions(identity_state=None):
    if identity_state is None:
        identity_state = state
    sessions = identity_state.get("action_guard_sessions")
    if not isinstance(sessions, dict):
        sessions = {}
        identity_state["action_guard_sessions"] = sessions
    return sessions


def _spec(action_key):
    return ACTION_SPECS.get(str(action_key or "").strip()) or {}


def _max_attempts(spec):
    return max(1, int(spec.get("max_attempts", SESSION_MAX_ATTEMPTS) or SESSION_MAX_ATTEMPTS))


def _retry_ranges(spec):
    ranges = spec.get("retry_delay_ranges") or RETRY_DELAY_RANGES_SEC
    return tuple(ranges)


def _ttl_sec(spec):
    return max(60, float(spec.get("ttl_sec", SESSION_TTL_SEC) or SESSION_TTL_SEC))


def _next_retry_delay(spec, retry_index):
    ranges = _retry_ranges(spec)
    if retry_index <= 0 or retry_index > len(ranges):
        return 0.0
    low, high = ranges[retry_index - 1]
    return random.uniform(float(low), float(high))


def _is_expired(session, now, spec):
    last_sent_at = float((session or {}).get("last_sent_at", 0) or 0)
    if last_sent_at <= 0:
        return True
    return now - last_sent_at >= _ttl_sec(spec)


def _int_state(identity_state, key):
    try:
        return int(identity_state.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _float_state(identity_state, key):
    try:
        return float(identity_state.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _phase_is(identity_state, key, phases):
    return str(identity_state.get(key) or "idle") in set(phases)


def _session_has_send_evidence(session):
    if not isinstance(session, dict):
        return False
    return (
        int(session.get("attempt", 0) or 0) > 0
        or float(session.get("last_sent_at", 0) or 0) > 0
        or float(session.get("first_sent_at", 0) or 0) > 0
        or int(session.get("last_msg_id", 0) or 0) > 0
    )


def _runtime_has_inflight_action(action_key, identity_state, now):
    """Return True only when local runtime still has concrete reply/phase evidence."""
    now = float(now or 0)
    action_key = str(action_key or "").strip()
    if action_key == "wild_training":
        return _int_state(identity_state, "wild_training_reply_to_msg_id") > 0 and _float_state(identity_state, "wild_training_reply_due_at") > now
    if action_key == "ranch":
        return _int_state(identity_state, "ranch_reply_to_msg_id") > 0 and _float_state(identity_state, "ranch_reply_due_at") > now
    if action_key == "tower":
        return _int_state(identity_state, "last_tower_msg_id") > 0 and _float_state(identity_state, "tower_reply_due_at") > now
    if action_key == "concubine_dream":
        return _phase_is(identity_state, "concubine_phase", {"dream_pending"}) and _int_state(identity_state, "concubine_dream_msg_id") > 0
    if action_key == "concubine_tianji":
        return _phase_is(identity_state, "concubine_phase", {"tianji_pending"}) and _int_state(identity_state, "concubine_tianji_msg_id") > 0
    if action_key == "concubine_fragment":
        return _phase_is(identity_state, "concubine_phase", {"fragment_pending"}) and _int_state(identity_state, "concubine_fragment_msg_id") > 0
    if action_key == "concubine_puzzle":
        return _phase_is(identity_state, "concubine_phase", {"puzzle_pending"}) and _int_state(identity_state, "concubine_puzzle_msg_id") > 0
    if action_key == "concubine_reacquire":
        return _phase_is(identity_state, "concubine_phase", {"reacquire_pending"}) and _int_state(identity_state, "concubine_reacquire_msg_id") > 0
    if action_key == "concubine_heart":
        return _phase_is(identity_state, "concubine_phase", {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}) or _int_state(identity_state, "concubine_heart_prompt_msg_id") > 0
    if action_key == "deep_retreat":
        return _phase_is(identity_state, "deep_retreat_phase", {"launching", "running"})
    if action_key == "yuanying_launch":
        return _phase_is(identity_state, "yuanying_phase", {"launching", "running"})
    if action_key == "second_soul_train":
        return _phase_is(identity_state, "second_soul_phase", {"train_pending"}) and _int_state(identity_state, "second_soul_train_msg_id") > 0
    if action_key == "small_world_preach":
        return _int_state(identity_state, "small_world_preach_reply_to_msg_id") > 0 and _float_state(identity_state, "small_world_preach_due_at") > now
    if action_key == "small_world_query":
        return _int_state(identity_state, "small_world_query_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"query_pending"})
    if action_key == "small_world_manifest":
        return _int_state(identity_state, "small_world_manifest_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"manifest_pending"})
    if action_key == "small_world_harvest":
        return _int_state(identity_state, "small_world_harvest_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"harvest_pending"})
    if action_key == "small_world_refine":
        return _int_state(identity_state, "small_world_refine_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"refine_pending"})
    if action_key == "nanlong":
        return _int_state(identity_state, "nanlong_reply_to_msg_id") > 0 and _float_state(identity_state, "nanlong_reply_due_at") > now
    if action_key in {"taiyi_yindao", "taiyi_node_search", "taiyi_node_define"}:
        phase = str(identity_state.get("taiyi_phase") or "idle")
        if action_key == "taiyi_yindao":
            return phase in {"yindao_pending", "search_pending", "define_pending"} and _int_state(identity_state, "taiyi_yindao_msg_id") > 0
        if action_key == "taiyi_node_search":
            return phase in {"search_pending", "define_pending"} and _int_state(identity_state, "taiyi_node_search_msg_id") > 0
        return phase == "define_pending" and _int_state(identity_state, "taiyi_node_define_msg_id") > 0
    return None


def _session_should_close(action_key, session, identity_state, now):
    spec = _spec(action_key)
    if not spec:
        return True
    if not _session_has_send_evidence(session):
        return True
    if _is_expired(session, now, spec):
        return True
    inflight = _runtime_has_inflight_action(action_key, identity_state, now)
    if inflight is False:
        return True
    return False


def _reconcile_action_session(action_key, identity_state, now):
    sessions = _get_sessions(identity_state)
    session = sessions.get(action_key)
    if not isinstance(session, dict):
        if action_key in sessions:
            sessions.pop(action_key, None)
            return True
        return False
    if _session_should_close(action_key, session, identity_state, now):
        sessions.pop(action_key, None)
        return True
    return False


def reconcile_identity_sessions(send_as_id=None, now=None):
    if not has_identity(send_as_id):
        return 0
    now = float(now if now is not None else time.time())
    changed = 0
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        for action_key in list(sessions.keys()):
            if _reconcile_action_session(action_key, identity_state, now):
                changed += 1
        if changed:
            mark_dirty()
    return changed


def _new_session(action_key, now, command):
    spec = _spec(action_key)
    return {
        "action_key": action_key,
        "kind": spec.get("kind") or ACTION_KIND_HIGH_RISK,
        "label": spec.get("label") or action_key,
        "attempt": 0,
        "first_sent_at": 0,
        "last_sent_at": 0,
        "next_allowed_at": 0,
        "last_msg_id": 0,
        "last_command": normalize_command(command),
        "last_block_log_at": 0,
        "closed_at": 0,
        "close_reason": "",
    }


def before_send(command, send_as_id=None, now=None):
    action_key = resolve_action_key(command)
    if not action_key:
        return True, ""
    now = float(now if now is not None else time.time())
    spec = _spec(action_key)
    max_attempts = _max_attempts(spec)
    with use_identity(send_as_id) as identity_state:
        changed = _reconcile_action_session(action_key, identity_state, now)
        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if not isinstance(session, dict) or _is_expired(session, now, spec):
            session = _new_session(action_key, now, command)
            sessions[action_key] = session
            changed = True

        if _runtime_has_inflight_action(action_key, identity_state, now):
            if changed:
                mark_dirty()
            return False, f"{session.get('label') or action_key} 等待游戏回复/结算中，暂不补发"

        attempt = int(session.get("attempt", 0) or 0)
        if attempt >= max_attempts:
            if changed:
                mark_dirty()
            return False, f"{session.get('label') or action_key} 本轮已发送 {attempt}/{max_attempts} 次，等待结果或人工处理"

        next_allowed_at = float(session.get("next_allowed_at", 0) or 0)
        if attempt > 0 and now < next_allowed_at:
            wait_sec = int(max(1, next_allowed_at - now))
            if changed:
                mark_dirty()
            return False, f"{session.get('label') or action_key} 安全补发等待中，剩余约 {wait_sec}s"

        if changed:
            mark_dirty()
        return True, ""


def is_guarded_command(command):
    return bool(resolve_action_key(command))


def note_sent(command, send_as_id, msg_id, sent_at=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return
    sent_at = float(sent_at if sent_at is not None else time.time())
    spec = _spec(action_key)
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if not isinstance(session, dict) or _is_expired(session, sent_at, spec):
            session = _new_session(action_key, sent_at, command)
            sessions[action_key] = session
        attempt = int(session.get("attempt", 0) or 0) + 1
        session["attempt"] = attempt
        session["last_command"] = normalize_command(command)
        session["last_msg_id"] = int(msg_id or 0)
        session["last_sent_at"] = sent_at
        if float(session.get("first_sent_at", 0) or 0) <= 0:
            session["first_sent_at"] = sent_at
        delay = _next_retry_delay(spec, attempt)
        session["next_allowed_at"] = sent_at + delay if delay > 0 else 0
        session["closed_at"] = 0
        session["close_reason"] = ""
        mark_dirty()


def get_next_allowed_at(command, send_as_id=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return 0.0
    with use_identity(send_as_id) as identity_state:
        session = _get_sessions(identity_state).get(action_key)
        if not isinstance(session, dict):
            return 0.0
        return float(session.get("next_allowed_at", 0) or 0)


def close_action(action_key, send_as_id=None, reason="reply", now=None):
    action_key = str(action_key or "").strip()
    if not action_key or not has_identity(send_as_id):
        return False
    now = float(now if now is not None else time.time())
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        if action_key not in sessions:
            return False
        sessions.pop(action_key, None)
        mark_dirty()
    return True


def close_by_family(family, send_as_id=None, reason="reply", now=None):
    closed = False
    for action_key in resolve_action_keys_for_family(family):
        closed = close_action(action_key, send_as_id=send_as_id, reason=reason, now=now) or closed
    return closed


def should_log_block(command, send_as_id=None, now=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return False
    now = float(now if now is not None else time.time())
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if not isinstance(session, dict):
            return True
        last = float(session.get("last_block_log_at", 0) or 0)
        if now - last < BLOCK_LOG_INTERVAL_SEC:
            return False
        session["last_block_log_at"] = now
        mark_dirty()
        return True


def get_action_guard_sessions(send_as_id=None):
    if not has_identity(send_as_id):
        return {}
    with use_identity(send_as_id) as identity_state:
        return dict(_get_sessions(identity_state))
