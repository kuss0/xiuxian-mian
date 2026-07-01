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
    CMD_EXPLORE_RIFT,
    CMD_REBIRTH_REQUEST,
    CMD_REBIRTH_SELECT_PREFIX,
    CMD_HEHUAN_DUAL,
    CMD_MULAN_COLLECT,
    CMD_MULAN_JUDGE,
    CMD_MULAN_PUBLISH,
    CMD_MULAN_SHADOW,
    CMD_MULAN_SUPPORT,
    CMD_MULAN_WAR_PANEL,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_PET_WARM,
    CMD_PET_TRIAL,
    CMD_PET_FORMATION,
    CMD_RANCH,
    CMD_SECOND_SOUL_TRAIN,
    CMD_WENDAO,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_BARRIER,
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
    CMD_CRAFT,
    CMD_NORMAL_RETREAT,
    CMD_DEEP_RETREAT_FORCE_EXIT,
    CMD_USE_HEQI_DAN,
    CMD_EXCHANGE_HEQI_DAN_PREFIX,
    CMD_SECT_DONATE_LINGSHI_PREFIX,
    CMD_TOWER,
    CMD_TREE_PULSE,
    CMD_TREE_PULSE_STATUS,
    CMD_WILD_TRAINING,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_CURSE,
    CMD_YINLUO_DAILY_SACRIFICE,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_POSSESS,
    CMD_YINLUO_REFINE,
    CMD_YINLUO_SOOTHE,
    CMD_YINDAO,
    CMD_YUANYING,
    CMD_YUANYING_SECT_RETREAT,
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
POST_CLOSE_REPEAT_GUARD_SEC = 95

_recent_closed_command_guards = {}


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
    "pet_formation": {
        "commands": (CMD_PET_FORMATION,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "布下剑阵",
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
    "deep_retreat_force_exit": {
        "commands": (CMD_DEEP_RETREAT_FORCE_EXIT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "强行出关",
    },
    "tianxing_retreat_farm": {
        "commands": (CMD_NORMAL_RETREAT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "天星普通闭关",
    },
    "tianxing_craft_farm": {
        "commands": (CMD_CRAFT,),
        "kind": ACTION_KIND_CHAIN,
        "label": "天星炼制攒点",
        "max_attempts": 1,
        "ttl_sec": 20 * 60,
    },
    "tianxing_heqi_dan": {
        "commands": (CMD_USE_HEQI_DAN,),
        "kind": ACTION_KIND_CHAIN,
        "label": "合气丹",
        "max_attempts": 1,
        "ttl_sec": 20 * 60,
    },
    "tianxing_heqi_exchange": {
        "commands": (CMD_EXCHANGE_HEQI_DAN_PREFIX,),
        "kind": ACTION_KIND_CHAIN,
        "label": "兑换合气丹",
        "max_attempts": 1,
        "ttl_sec": 20 * 60,
    },
    "tianxing_lingshi_donation": {
        "commands": (CMD_SECT_DONATE_LINGSHI_PREFIX,),
        "kind": ACTION_KIND_CHAIN,
        "label": "宗门捐献灵石",
        "max_attempts": 1,
        "ttl_sec": 20 * 60,
    },
    "yuanying_launch": {
        "commands": (CMD_YUANYING, CMD_YUANYING_SECT_RETREAT),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "元婴",
    },
    "tower": {
        "commands": (CMD_TOWER,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "闯塔",
    },
    "tree_pulse_status": {
        "commands": (CMD_TREE_PULSE_STATUS,),
        "kind": ACTION_KIND_STATUS,
        "label": "灵树定脉",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
    },
    "tree_pulse": {
        "commands": (CMD_TREE_PULSE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "灵树定脉动作",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
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
    "small_world_barrier": {
        "commands": (CMD_SMALL_WORLD_BARRIER,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "小世界护界禁制",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
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
    "wendao": {
        "commands": (CMD_WENDAO,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "问道",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "explore_rift": {
        "commands": (CMD_EXPLORE_RIFT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "探寻裂缝",
        "max_attempts": 1,
        "ttl_sec": 12 * 3600,
    },
    "hehuan_dual": {
        "commands": (CMD_HEHUAN_DUAL,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "合欢温养",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "mulan_collect": {
        "commands": (CMD_MULAN_COLLECT, CMD_MULAN_SHADOW, CMD_MULAN_WAR_PANEL),
        "kind": ACTION_KIND_CHAIN,
        "label": "慕兰烽烟校准",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "mulan_judge": {
        "commands": (CMD_MULAN_JUDGE,),
        "kind": ACTION_KIND_CHAIN,
        "label": "慕兰辨报",
        "max_attempts": 1,
        "ttl_sec": 10 * 60,
    },
    "mulan_publish": {
        "commands": (CMD_MULAN_PUBLISH,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "慕兰公开军报",
        "max_attempts": 1,
        "ttl_sec": 24 * 3600,
    },
    "mulan_support": {
        "commands": (CMD_MULAN_SUPPORT,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "慕兰支援",
        "max_attempts": 1,
        "ttl_sec": 24 * 3600,
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
    "yinluo_daily_sacrifice": {
        "commands": (CMD_YINLUO_DAILY_SACRIFICE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "每日献祭",
        "max_attempts": 1,
        "ttl_sec": 24 * 3600,
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
        "label": "收取精华",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "yinluo_refine": {
        "commands": (CMD_YINLUO_REFINE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "囚禁魂魄",
        "max_attempts": 1,
        "ttl_sec": 30 * 60,
    },
    "yinluo_soothe": {
        "commands": (CMD_YINLUO_SOOTHE,),
        "kind": ACTION_KIND_HIGH_RISK,
        "label": "安抚幡灵",
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
    "concubine_heart": ("concubine_heart",),
    "concubine_fragment": ("concubine_fragment",),
    "concubine_puzzle": ("concubine_puzzle",),
    "concubine_reacquire": ("concubine_reacquire",),
    "pet_trial": ("pet_trial",),
    "pet_warm": ("pet_warm",),
    "pet_formation": ("pet_formation",),
    "ranch": ("ranch",),
    "wild_training": ("wild_training",),
    "second_soul_train": ("second_soul_train",),
    "deep_retreat": ("deep_retreat", "deep_retreat_force_exit"),
    "tower": ("tower",),
    "tree_panel": ("tree_pulse_status",),
    "tree_pulse": ("tree_pulse",),
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
    "small_world_barrier": ("small_world_barrier",),
    "divination": ("divination",),
    "divination_exchange": ("divination_exchange",),
    "wendao": ("wendao",),
    "explore_rift": ("explore_rift",),
    "hehuan_dual": ("hehuan_dual",),
    "mulan_panel": ("mulan_collect",),
    "mulan_collect": ("mulan_collect",),
    "mulan_judge": ("mulan_judge",),
    "mulan_publish": ("mulan_publish",),
    "mulan_support": ("mulan_support",),
    "tianxing_panel": ("tianxing_panel",),
    "tianxing_observe": ("tianxing_observe",),
    "tianxing_set_star": ("tianxing_set_star",),
    "tianxing_predict": ("tianxing_predict",),
    "tianxing_change_fate": ("tianxing_change_fate",),
    "tianxing_clear_calamity": ("tianxing_clear_calamity",),
    "tianxing_retreat_farm": ("tianxing_retreat_farm", "tianxing_heqi_dan", "tianxing_heqi_exchange", "tianxing_lingshi_donation"),
    "tianxing_craft_farm": ("tianxing_craft_farm",),
    "yinluo_banner": ("yinluo_banner",),
    "yinluo_demon_summon": ("yinluo_demon_summon",),
    "yinluo_daily_sacrifice": ("yinluo_daily_sacrifice",),
    "yinluo_convert": ("yinluo_convert",),
    "yinluo_collect": ("yinluo_collect",),
    "yinluo_refine": ("yinluo_refine",),
    "yinluo_soothe": ("yinluo_soothe",),
    "yinluo_blood_forest": ("yinluo_blood_forest",),
    "yinluo_curse": ("yinluo_curse",),
    "yinluo_possess": ("yinluo_possess",),
}


def normalize_command(command):
    return str(command or "").strip()


def _command_matches_prefix(raw_command, prefix):
    raw_command = normalize_command(raw_command)
    prefix = normalize_command(prefix)
    if not raw_command or not prefix:
        return False
    if raw_command == prefix or raw_command.startswith(f"{prefix} "):
        return True
    if prefix.endswith("*") and raw_command.startswith(prefix):
        return True
    return False


def resolve_action_key(command):
    raw_command = normalize_command(command)
    if not raw_command:
        return ""
    for prefix, action_key in COMMAND_TO_ACTION_KEY.items():
        if _command_matches_prefix(raw_command, prefix):
            return action_key
    return ""


def resolve_action_key_for_family(family):
    keys = FAMILY_TO_ACTION_KEYS.get(str(family or "").strip(), ())
    return keys[0] if keys else ""


def resolve_action_keys_for_family(family):
    return tuple(FAMILY_TO_ACTION_KEYS.get(str(family or "").strip(), ()))


def resolve_action_keys_for_module(module_name):
    try:
        from .module_manifest import get_module_manifest
    except Exception:
        return ()
    manifest = get_module_manifest(module_name)
    if not manifest:
        return ()
    action_keys = []
    seen = set()
    for family in tuple(getattr(manifest, "reply_families", ()) or ()):
        for action_key in resolve_action_keys_for_family(family):
            if action_key in seen:
                continue
            seen.add(action_key)
            action_keys.append(action_key)
    return tuple(action_keys)


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
    if _has_remote_block(session, now):
        return False
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


def _dict_state(identity_state, key):
    value = identity_state.get(key) if isinstance(identity_state, dict) else None
    return value if isinstance(value, dict) else {}


def _int_dict_state(mapping, key):
    try:
        return int(mapping.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _float_dict_state(mapping, key):
    try:
        return float(mapping.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _phase_is(identity_state, key, phases):
    return str(identity_state.get(key) or "idle") in set(phases)


def _is_rebirth_recovery_command(command):
    raw_command = normalize_command(command)
    return (
        raw_command == CMD_REBIRTH_REQUEST
        or raw_command == CMD_REBIRTH_SELECT_PREFIX
        or raw_command.startswith(f"{CMD_REBIRTH_SELECT_PREFIX} ")
    )


def _rebirth_quiet_reason(identity_state, now):
    now = float(now or 0)
    fatal_due_at = _float_state(identity_state, "explore_rift_fatal_confirm_due_at")
    if _int_state(identity_state, "explore_rift_fatal_msg_id") > 0 and fatal_due_at > now:
        return "探寻裂缝大凶确认中"
    weak_until = _float_state(identity_state, "explore_rift_nascent_escape_weak_until")
    if weak_until > now:
        return "元婴虚弱等待夺舍"
    if bool(identity_state.get("explore_rift_rebirth_required")):
        phase = str(identity_state.get("explore_rift_rebirth_phase") or "idle")
        return f"夺舍恢复中({phase})"
    return ""


def _session_has_send_evidence(session):
    if not isinstance(session, dict):
        return False
    return (
        int(session.get("attempt", 0) or 0) > 0
        or float(session.get("last_sent_at", 0) or 0) > 0
        or float(session.get("first_sent_at", 0) or 0) > 0
        or int(session.get("last_msg_id", 0) or 0) > 0
    )


def _recent_guard_key(send_as_id, action_key, command):
    try:
        identity_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    return identity_id, str(action_key or "").strip(), normalize_command(command)


def _note_recent_closed_command_guard(send_as_id, action_key, session, now):
    command = normalize_command((session or {}).get("last_command") or "")
    last_sent_at = float((session or {}).get("last_sent_at", 0) or 0)
    if not command or last_sent_at <= 0:
        return
    guard_until = max(float(now or 0), last_sent_at + POST_CLOSE_REPEAT_GUARD_SEC)
    if guard_until <= float(now or 0):
        return
    _recent_closed_command_guards[_recent_guard_key(send_as_id, action_key, command)] = guard_until


def _recent_closed_command_guard_until(send_as_id, action_key, command, now):
    key = _recent_guard_key(send_as_id, action_key, command)
    guard_until = float(_recent_closed_command_guards.get(key, 0) or 0)
    if guard_until <= float(now or 0):
        _recent_closed_command_guards.pop(key, None)
        return 0.0
    return guard_until


def _has_remote_block(session, now):
    if not isinstance(session, dict):
        return False
    block_until = float(session.get("remote_block_until", 0) or 0)
    return block_until > float(now or 0)


def _remote_block_reason(session, action_key, now):
    spec = _spec(action_key)
    label = str(session.get("label") or spec.get("label") or action_key)
    reason = str(session.get("remote_block_reason") or "").strip()
    block_until = float(session.get("remote_block_until", 0) or 0)
    wait_sec = int(max(1, block_until - float(now or 0)))
    if reason:
        return f"{label} {reason}，剩余约 {wait_sec}s"
    return f"{label} 已有远端状态证据，剩余约 {wait_sec}s"


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
        return _phase_is(identity_state, "deep_retreat_phase", {"launching", "running", "observing_summary", "waiting_summary"})
    if action_key == "yuanying_launch":
        return _phase_is(identity_state, "yuanying_phase", {"launching", "running", "observing_summary", "waiting_summary"})
    if action_key == "second_soul_train":
        return _phase_is(identity_state, "second_soul_phase", {"train_pending"}) and _int_state(identity_state, "second_soul_train_msg_id") > 0
    if action_key == "small_world_preach":
        return _int_state(identity_state, "small_world_preach_reply_to_msg_id") > 0 and _float_state(identity_state, "small_world_preach_due_at") > now
    if action_key == "small_world_query":
        return _int_state(identity_state, "small_world_query_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"query_pending"})
    if action_key == "small_world_manifest":
        return _int_state(identity_state, "small_world_manifest_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"manifest_pending"})
    if action_key == "small_world_harvest":
        return _int_state(identity_state, "small_world_harvest_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"harvest_pending", "harvest_sent", "harvest_before_manifest_sent"})
    if action_key == "small_world_refine":
        return _int_state(identity_state, "small_world_refine_msg_id") > 0 and _phase_is(identity_state, "small_world_phase", {"refine_pending"})
    if action_key == "small_world_barrier":
        return _int_state(identity_state, "small_world_barrier_msg_id") > 0 and _float_state(identity_state, "small_world_barrier_due_at") > now
    if action_key == "wendao":
        return _int_state(identity_state, "wendao_reply_to_msg_id") > 0 and _float_state(identity_state, "wendao_reply_due_at") > now
    if action_key == "hehuan_dual":
        observed = _dict_state(identity_state, "hehuan_observation")
        pending_msg_id = _int_dict_state(observed, "auto_pending_msg_id")
        pending_sent_at = _float_dict_state(observed, "auto_pending_sent_at")
        pending_deadline_at = _float_dict_state(observed, "auto_pending_deadline_at")
        last_observed_at = _float_dict_state(observed, "last_observed_at")
        if pending_msg_id <= 0 or pending_sent_at <= 0 or pending_deadline_at <= 0:
            return False
        if last_observed_at >= pending_sent_at:
            return False
        return pending_deadline_at > now
    if action_key in {"mulan_collect", "mulan_judge", "mulan_publish", "mulan_support"}:
        phase_by_action = {
            "mulan_collect": {"collect_pending", "panel_pending"},
            "mulan_judge": {"judge_pending"},
            "mulan_publish": {"publish_pending"},
            "mulan_support": {"support_pending"},
        }
        return (
            _int_state(identity_state, "mulan_reply_to_msg_id") > 0
            and _float_state(identity_state, "mulan_reply_due_at") > now
            and _phase_is(identity_state, "mulan_phase", phase_by_action[action_key])
        )
    if action_key in {"tianxing_panel", "tianxing_observe", "tianxing_set_star", "tianxing_predict", "tianxing_change_fate", "tianxing_clear_calamity"}:
        observed = _dict_state(identity_state, "tianxing_observation")
        expected_pending = {
            "tianxing_panel": "panel",
            "tianxing_observe": "observe",
            "tianxing_set_star": "set_star",
            "tianxing_predict": "predict",
            "tianxing_change_fate": "change_fate",
            "tianxing_clear_calamity": "clear_calamity",
        }.get(action_key)
        pending_action = str(observed.get("auto_pending_action") or "").strip()
        pending_msg_id = _int_dict_state(observed, "auto_pending_msg_id")
        pending_sent_at = _float_dict_state(observed, "auto_pending_sent_at")
        pending_due_at = _float_dict_state(observed, "auto_pending_due_at")
        last_observed_at = _float_dict_state(observed, "last_observed_at")
        if pending_action != expected_pending or pending_msg_id <= 0 or pending_sent_at <= 0 or pending_due_at <= 0:
            return False
        if last_observed_at >= pending_sent_at:
            return False
        return pending_due_at > now
    if action_key == "explore_rift":
        if _int_state(identity_state, "explore_rift_reply_to_msg_id") > 0 and _float_state(identity_state, "explore_rift_reply_due_at") > now:
            return True
        if _int_state(identity_state, "explore_rift_rebirth_request_msg_id") > 0 and _float_state(identity_state, "explore_rift_rebirth_due_at") > now:
            return True
        if _int_state(identity_state, "explore_rift_rebirth_select_msg_id") > 0 and _float_state(identity_state, "explore_rift_rebirth_due_at") > now:
            return True
        if _int_state(identity_state, "explore_rift_fatal_msg_id") > 0 and _float_state(identity_state, "explore_rift_fatal_confirm_due_at") > now:
            return True
        return _int_state(identity_state, "explore_rift_pending_result_msg_id") > 0 and _float_state(identity_state, "next_explore_rift_time") > now
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
    if _has_remote_block(session, now):
        return False
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
    now = float(now if now is not None else time.time())
    if has_identity(send_as_id):
        with use_identity(send_as_id) as identity_state:
            quiet_reason = _rebirth_quiet_reason(identity_state, now)
            if quiet_reason and not _is_rebirth_recovery_command(command):
                return False, f"{quiet_reason}，普通指令静默"
    if not action_key:
        return True, ""
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

        if _has_remote_block(session, now):
            if changed:
                mark_dirty()
            return False, _remote_block_reason(session, action_key, now)

        recent_guard_until = _recent_closed_command_guard_until(send_as_id, action_key, command, now)
        if recent_guard_until > now:
            wait_sec = int(max(1, recent_guard_until - now))
            if changed:
                mark_dirty()
            return False, f"{session.get('label') or spec.get('label') or action_key} 同命令短窗保护，剩余约 {wait_sec}s"

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


def note_remote_block(action_key_or_command, send_as_id=None, *, block_until=0, reason="", kind="", now=None, command=None):
    action_key = resolve_action_key(action_key_or_command)
    if not action_key and str(action_key_or_command or "").strip() in ACTION_SPECS:
        action_key = str(action_key_or_command or "").strip()
    if not action_key or not has_identity(send_as_id):
        return False
    now = float(now if now is not None else time.time())
    block_until = float(block_until or 0)
    if block_until <= now:
        return False
    spec = _spec(action_key)
    command = normalize_command(command or action_key_or_command)
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if not isinstance(session, dict):
            session = _new_session(action_key, now, command)
            sessions[action_key] = session
        session["remote_block_until"] = block_until
        session["remote_block_reason"] = str(reason or "").strip()
        session["remote_block_kind"] = str(kind or "").strip()
        session["remote_observed_at"] = now
        session["label"] = spec.get("label") or session.get("label") or action_key
        if command:
            session["last_command"] = command
        mark_dirty()
    return True


def get_next_allowed_at(command, send_as_id=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return 0.0
    with use_identity(send_as_id) as identity_state:
        session = _get_sessions(identity_state).get(action_key)
        if not isinstance(session, dict):
            return 0.0
        return float(session.get("next_allowed_at", 0) or 0)


def get_blocked_until(command, send_as_id=None, now=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return 0.0, ""
    now = float(now if now is not None else time.time())
    spec = _spec(action_key)
    label = str(spec.get("label") or action_key)
    with use_identity(send_as_id) as identity_state:
        quiet_reason = _rebirth_quiet_reason(identity_state, now)
        if quiet_reason and not _is_rebirth_recovery_command(command):
            return now + 60, f"{quiet_reason}，普通指令静默"

        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if isinstance(session, dict) and not _is_expired(session, now, spec):
            if _has_remote_block(session, now):
                return float(session.get("remote_block_until", 0) or 0), _remote_block_reason(session, action_key, now)

        recent_guard_until = _recent_closed_command_guard_until(send_as_id, action_key, command, now)
        if recent_guard_until > now:
            wait_sec = int(max(1, recent_guard_until - now))
            return recent_guard_until, f"{label} 同命令短窗保护，剩余约 {wait_sec}s"

        if isinstance(session, dict) and not _is_expired(session, now, spec):
            if _runtime_has_inflight_action(action_key, identity_state, now):
                deadline = float(session.get("next_allowed_at", 0) or 0)
                return deadline, f"{label} 等待游戏回复/结算中，暂不补发"
            attempt = int(session.get("attempt", 0) or 0)
            next_allowed_at = float(session.get("next_allowed_at", 0) or 0)
            if attempt > 0 and next_allowed_at > now:
                wait_sec = int(max(1, next_allowed_at - now))
                return next_allowed_at, f"{label} 安全补发等待中，剩余约 {wait_sec}s"
    return 0.0, ""


def close_action(action_key, send_as_id=None, reason="reply", now=None):
    action_key = str(action_key or "").strip()
    if not action_key or not has_identity(send_as_id):
        return False
    now = float(now if now is not None else time.time())
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        session = sessions.get(action_key)
        if not isinstance(session, dict):
            return False
        if _has_remote_block(session, now) and str(session.get("remote_block_kind") or "") != "send_unknown":
            session["attempt"] = 0
            session["last_msg_id"] = 0
            session["next_allowed_at"] = 0
            session["closed_at"] = now
            session["close_reason"] = str(reason or "")
        else:
            _note_recent_closed_command_guard(send_as_id, action_key, session, now)
            sessions.pop(action_key, None)
        mark_dirty()
    return True


def close_actions(action_keys, send_as_id=None, reason="reply", now=None):
    if not has_identity(send_as_id):
        return 0
    now = float(now if now is not None else time.time())
    closed_count = 0
    with use_identity(send_as_id) as identity_state:
        sessions = _get_sessions(identity_state)
        for action_key in tuple(action_keys or ()):
            action_key = str(action_key or "").strip()
            if not action_key:
                continue
            session = sessions.get(action_key)
            if not isinstance(session, dict):
                continue
            if _has_remote_block(session, now) and str(session.get("remote_block_kind") or "") != "send_unknown":
                session["attempt"] = 0
                session["last_msg_id"] = 0
                session["next_allowed_at"] = 0
                session["closed_at"] = now
                session["close_reason"] = str(reason or "")
            else:
                _note_recent_closed_command_guard(send_as_id, action_key, session, now)
                sessions.pop(action_key, None)
            closed_count += 1
        if closed_count:
            mark_dirty()
    return closed_count


def close_by_family(family, send_as_id=None, reason="reply", now=None):
    closed = False
    for action_key in resolve_action_keys_for_family(family):
        closed = close_action(action_key, send_as_id=send_as_id, reason=reason, now=now) or closed
    return closed


def close_by_module(module_name, send_as_id=None, reason="module_disabled", now=None):
    return close_actions(resolve_action_keys_for_module(module_name), send_as_id=send_as_id, reason=reason, now=now)


def should_log_block(command, send_as_id=None, now=None):
    action_key = resolve_action_key(command)
    if not action_key or not has_identity(send_as_id):
        return False
    now = float(now if now is not None else time.time())
    with use_identity(send_as_id) as identity_state:
        quiet_reason = _rebirth_quiet_reason(identity_state, now)
        if quiet_reason and not _is_rebirth_recovery_command(command):
            return False
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
