import asyncio
import hashlib
import json
import re
import time
import traceback
from datetime import datetime, timedelta
from html import escape, unescape

from .app_message_log import (
    _get_replica_dispatch_event_listener_account_id,
    _get_replica_event_listener_account_id,
    _is_replica_listener_self_event,
    _send_replica_group_message,
)
from .app_runtime import (
    _claim_runtime_event,
    _get_event_reply_header_msg_id,
    _has_runtime_message_consumed,
    _mark_runtime_message_consumed,
)
from .config import (
    CMD_REPLICA_CANGKUN_JOIN,
    CMD_REPLICA_HUANGLONG_JOIN,
    CMD_REPLICA_JOIN,
    CMD_REPLICA_ZHUIMO_JOIN,
    MESSAGES_DIR,
    REPLICA_ACTIVE_TTL_SEC,
    REPLICA_FAILURE_GRACE_SEC,
    REPLICA_SUCCESS_COOLDOWN_SEC,
    TZ_LOCAL,
    get_account_offline_reason,
    get_all_clients,
    get_registered_client,
    is_account_offline,
)
from .features.dungeon_quiet import format_dungeon_quiet_until, get_dungeon_quiet_reason, is_dungeon_quiet_active
from .features.storage_bag import apply_storage_bag_item_deltas
from .persistence import mark_dirty
from .replica_query_aggregator_client import (
    ReplicaQueryAggregatorError,
    submit_replica_query_result,
    submit_virtual_hall_recommendation,
)
from .runtime import (
    _fire_and_forget,
    console_log,
    get_reply_context,
    mono,
    send_audit_log,
    send_game_command,
    should_pause_for_bot_health,
)
from .state import (
    REALM_SORT_ORDER,
    get_global_enabled,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_replica_gold_dps_enabled,
    get_replica_dispatch_listener_account_map,
    get_replica_dispatch_participant_identity_ids,
    get_replica_listener_account_map,
    get_replica_participant_identity_ids,
    get_replica_query_aggregator_config,
    get_replica_run_state,
    get_send_as_profile,
    get_storage_bag_records,
    is_replica_virtual_hall_match_enabled,
    resolve_identity_selector,
    set_replica_run_state,
)
from .timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time

_REPLICA_KIND_VIRTUAL_HALL = "virtual_hall"
_REPLICA_KIND_ZHUIMO = "zhuimo"
_REPLICA_KIND_HUANGLONG = "huanglong"
_REPLICA_KIND_CANGKUN = "cangkun"
_REPLICA_KINDS = (_REPLICA_KIND_VIRTUAL_HALL, _REPLICA_KIND_ZHUIMO, _REPLICA_KIND_HUANGLONG, _REPLICA_KIND_CANGKUN)
_REPLICA_KIND_META = {
    _REPLICA_KIND_VIRTUAL_HALL: {"name": "虚天殿", "short": "虚", "dispatch_command": ".虚天殿", "join_command": CMD_REPLICA_JOIN, "enter_command": ".进入虚天殿"},
    _REPLICA_KIND_ZHUIMO: {"name": "坠魔谷", "short": "坠", "dispatch_command": ".坠魔谷", "join_command": CMD_REPLICA_ZHUIMO_JOIN, "enter_command": ".进入坠魔谷"},
    _REPLICA_KIND_HUANGLONG: {"name": "黄龙山", "short": "黄", "dispatch_command": ".黄龙山", "join_command": CMD_REPLICA_HUANGLONG_JOIN, "enter_command": ".进入黄龙山"},
    _REPLICA_KIND_CANGKUN: {"name": "苍坤洞府", "short": "苍", "dispatch_command": ".苍坤洞府", "join_command": CMD_REPLICA_CANGKUN_JOIN, "enter_command": ".进入苍坤洞府"},
}
_REPLICA_TICKET_META = {
    _REPLICA_KIND_VIRTUAL_HALL: {
        "ticket_items": ("虚天残图",),
        "open_command": ".开启虚天殿",
        "dissolve_command": ".解散副本",
        "aliases": ("虚天", "虚天殿", "virtual", "virtual_hall"),
    },
    _REPLICA_KIND_CANGKUN: {
        "ticket_items": ("苍坤残图",),
        "open_command": ".开启苍坤洞府",
        "dissolve_command": ".解散苍坤洞府",
        "aliases": ("苍坤", "苍坤洞府", "苍坤上人洞府", "cangkun"),
    },
    _REPLICA_KIND_ZHUIMO: {
        "ticket_items": ("坠魔谷禁制令",),
        "open_command": ".开启坠魔谷",
        "dissolve_command": ".解散坠魔谷",
        "aliases": ("坠魔", "坠魔谷", "zhuimo"),
    },
    _REPLICA_KIND_HUANGLONG: {
        "ticket_items": ("黄龙急援令", "黄龙急援令（宗门版）"),
        "open_command": ".开启黄龙山",
        "dissolve_command": ".解散黄龙山",
        "aliases": ("黄龙", "黄龙山", "huanglong"),
    },
}
_REPLICA_TICKET_ITEMS = tuple(
    item
    for meta in _REPLICA_TICKET_META.values()
    for item in meta.get("ticket_items", ())
)
_REPLICA_KIND_OPEN_PRIORITY = (_REPLICA_KIND_VIRTUAL_HALL, _REPLICA_KIND_CANGKUN, _REPLICA_KIND_ZHUIMO, _REPLICA_KIND_HUANGLONG)
_REPLICA_DISPATCH_COMMAND_RE = re.compile(
    rf"^(?P<command>{'|'.join(re.escape(meta['dispatch_command']) for meta in _REPLICA_KIND_META.values())})\s+(?P<room_id>\d+)(?:\s+(?P<rest>.+))?$"
)
_VIRTUAL_HALL_MATCH_COMMAND_RE = re.compile(r"^\.匹配虚天殿\s+(?P<room_id>\d+)\s*$")
_VIRTUAL_HALL_AUTO_OPEN_COMMAND_RE = re.compile(r"^\.开启虚天殿\s+(?P<selector>\S+)\s*$")
_REPLICA_LIGHTWEIGHT_OPEN_COMMAND_RE = re.compile(r"^\.开启副本(?:\s+(?P<rest>.+))?$")
_REPLICA_LIGHTWEIGHT_JOIN_COMMAND_RE = re.compile(r"^\.加入副本(?:\s+(?P<rest>.+))?$")
_REPLICA_ENTER_COMMAND_RE = re.compile(rf"^(?P<command>{'|'.join(re.escape(meta['enter_command']) for meta in _REPLICA_KIND_META.values())})\s*$")
_REPLICA_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{3,32}")
_REPLICA_ROOM_DISSOLVED_RE = re.compile(r"队长\s*(@[A-Za-z0-9_]{3,32})\s*已将副本房间\s*[（(]\s*ID\s*[:：]\s*(\d+)\s*[）)]\s*解散")
_REPLICA_KIND_ROOM_DISSOLVED_RE = re.compile(
    r"队长\s*(@[A-Za-z0-9_]{3,32})\s*已解散(?:坠魔谷|黄龙山大战?|苍坤(?:上人)?洞府)房间\s*[（(]\s*ID\s*[:：]\s*(\d+)\s*[）)]"
)
_REPLICA_TEAM_KICKED_RE = re.compile(r"【队员已请离】\s*队长\s*(@[A-Za-z0-9_]{3,32})\s*已将道友\s*(@[A-Za-z0-9_]{3,32})\s*请离队伍")
_REPLICA_OPENED_RE = re.compile(
    r"(?:【(?P<opened_kind_name>虚天殿)已开启】|【(?P<opened_zhuimo>坠魔谷)·集结】|【(?P<opened_huanglong>黄龙山)大战·集结】|【(?P<opened_cangkun>苍坤(?:上人)?洞府)(?:·集结|已开启)?】)"
    r"\s*(?:队长\s*)?(?P<leader>@[^\s，。！？、；：:,.!?()（）【】\[\]]+).*?(?:副本ID|房间ID)\s*[:：]\s*(?P<room_id>\d+)",
    re.S,
)
_REPLICA_JOINED_RE = re.compile(
    r"(@[^\s，。！？、；：:,.!?()（）【】\[\]]+)\s*已(?:成功)?加入"
    r"(?:副本\s*(\d+)|坠魔谷(?:\s*(\d+))?|黄龙山(?:队伍)?(?:\s*(\d+))?|苍坤(?:上人)?洞府(?:队伍)?(?:\s*(\d+))?)"
)
_REPLICA_ROOM_GUA_TTL_SEC = 6 * 60 * 60
_REPLICA_ROOM_GUA_MAX_PER_KIND = 100
_VIRTUAL_HALL_MATCH_QUERY_WAIT_SEC = 15
_VIRTUAL_HALL_MATCH_QUERY_POLL_SEC = 0.5
_VIRTUAL_HALL_MATCH_LOG_WINDOW_SEC = 20
_VIRTUAL_HALL_OPEN_COMMAND = ".开启虚天殿"
_VIRTUAL_HALL_KICK_COMMAND = ".请离"
_VIRTUAL_HALL_DISSOLVE_COMMAND = ".解散副本"
_VIRTUAL_HALL_ENTER_COMMAND = ".进入虚天殿"
_VIRTUAL_HALL_AUTO_OPEN_TIMEOUT_SEC = 5 * 60
_VIRTUAL_HALL_AUTO_OPEN_KICK_TIMEOUT_SEC = 60
_VIRTUAL_HALL_AUTO_KICK_COMMAND_INTERVAL_SEC = 2
_VIRTUAL_HALL_AUTO_KICK_TIMEOUT_RECHECK_DELAY_SEC = 0.2
_VIRTUAL_HALL_AUTO_OPEN_DONE_TTL_SEC = 0
_VIRTUAL_HALL_AUTO_MISSING_RETRY_COOLDOWN_SEC = 30
_VIRTUAL_HALL_AUTO_MISSING_AUTO_RETRY_MAX = 1
_VIRTUAL_HALL_AUTO_OPEN_ACTIVE_PHASES = {"opening", "waiting_dispatch", "monitoring", "dissolving"}
_LIGHTWEIGHT_NO_DPS_AUTO_DISSOLVE_DELAY_SEC = 6
_REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC = 10 * 60
_REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC = 3 * 60 * 60
_REPLICA_TICKET_EVENT_TTL_SEC = 24 * 60 * 60
_REPLICA_TICKET_EVENT_MAX = 1000
_REPLICA_EXTERNAL_DISPATCH_PENDING_SEC = 5 * 60
_REPLICA_EXTERNAL_DISPATCH_COMMAND_INTERVAL_SEC = 2
_REPLICA_EXTERNAL_DISPATCH_FAST_RETRY_DELAY_SEC = 2.5
_REPLICA_EXTERNAL_DISPATCH_FAST_RETRY_LIMIT = 1
_REPLICA_EXTERNAL_DISPATCH_PENDING_KEYS = (
    "dispatch_pending_room_id",
    "dispatch_pending_until",
    "dispatch_pending_msg_id",
    "dispatch_pending_source_chat_id",
    "dispatch_pending_source_msg_id",
    "dispatch_retry_count",
)
_REPLICA_SEND_SOURCE_MODULE = "自动副本"
_VIRTUAL_HALL_ELEMENT_ALIASES = {
    "金": {"金", "雷"},
    "木": {"木", "风"},
    "水": {"水", "冰"},
    "火": {"火", "暗"},
    "土": {"土"},
}


def _replica_send_intent(op_id="", chain_id=""):
    intent = {
        "source_module": _REPLICA_SEND_SOURCE_MODULE,
        "delete_policy": "keep",
    }
    op_id = str(op_id or "").strip()
    chain_id = str(chain_id or "").strip()
    if op_id:
        intent["op_id"] = op_id
    if chain_id:
        intent["chain_id"] = chain_id
    return intent


_VIRTUAL_HALL_GUA_ROLE_ORDER = {"阵骨": 0, "主锋": 1, "引灵": 2, "旁合": 3}
_REPLICA_QUERY_STATUS_RE = re.compile(
    r"虚\s*[:：]\s*(?P<virtual>[^|\n]+?)\s*\|\s*坠\s*[:：]\s*(?P<zhuimo>[^|\n]+?)\s*\|\s*黄\s*[:：]\s*(?P<huanglong>[^|\n]+)(?:\s*\|\s*苍\s*[:：]\s*(?P<cangkun>[^|\n]+))?"
)
_REPLICA_QUERY_ROOT_RE = re.compile(r"^[金木水火土风雷冰暗]+$")
_REPLICA_QUERY_PROFESSION_NAMES = ("破军", "御山", "灵医", "影刃", "咒师", "未匹配")
_CANGKUN_REQUIRED_PROFESSIONS = ("破军", "御山", "灵医", "影刃", "咒师")
_CANGKUN_MIN_REALM = "结丹初期"
_CANGKUN_MIN_REALM_INDEX = REALM_SORT_ORDER.index(_CANGKUN_MIN_REALM)
_CANGKUN_ROOT_GRADE_PRIORITY = ("天", "异", "真", "伪")
_CANGKUN_PREFERRED_SECT = "太一门"
_XUTIAN_ORACLE_EXPLICIT = {
    "乾天上坤地下 · 三爻争锋": ("火路", "压策", "#528 明示"),
    "震雷上艮山下 · 五爻乘时": ("火路", "势策", "#529 明示"),
    "巽风上坤地下 · 二爻守中": ("冰路", "稳策", "#530 明示"),
    "乾天上巽风下 · 三爻争锋": ("火路", "压策", "#531 明示"),
    "兑泽上坎水下 · 初爻潜机": ("冰路", "势策", "#532 明示"),
    "离火上艮山下 · 四爻转阵": ("冰路", "稳策", "#533 明示"),
    "震雷上乾天下 · 上爻游变": ("火路", "势策", "#534 明示"),
    "巽风上乾天下 · 二爻守中": ("火路", "势策", "#535 明示"),
    "坎水上兑泽下 · 上爻游变": ("冰路", "势策", "#536 明示"),
    "艮山上离火下 · 三爻争锋": ("火路", "压策", "#537 明示"),
    "坤地上离火下 · 初爻潜机": ("冰路", "稳策", "#538 明示"),
    "乾天上震雷下 · 二爻守中": ("火路", "压策", "#539 明示"),
    "兑泽上巽风下 · 初爻潜机": ("冰路", "势策", "#540 明示"),
    "离火上巽风下 · 三爻争锋": ("火路", "压策", "#541 明示"),
    "震雷上巽风下 · 三爻争锋": ("火路", "势策", "#542 明示"),
    "乾天上离火下 · 五爻乘时": ("火路", "压策", "#544 明示"),
    "坎水上坤地下 · 上爻游变": ("冰路", "稳策", "#546 明示"),
    "艮山上坤地下 · 三爻争锋": ("冰路", "稳策", "#547 明示"),
    "坤地上坤地下 · 二爻守中": ("冰路", "稳策", "#548 明示"),
    "乾天上乾天下 · 初爻潜机": ("火路", "压策", "#549 明示"),
    "兑泽上乾天下 · 三爻争锋": ("火路", "压策", "#550 明示"),
    "离火上离火下 · 初爻潜机": ("火路", "压策", "#551 明示"),
    "巽风上坎水下 · 上爻游变": ("火路", "势策", "#552 明示"),
    "坎水上巽风下 · 五爻乘时": ("火路", "稳策", "#553 明示"),
    "坤地上兑泽下 · 初爻潜机": ("冰路", "势策", "#554 明示"),
}
_XUTIAN_ORACLE_SUCCESS = {
    "乾天上坤地下 · 上爻游变": (("火路", "稳策", "#561 顺"), ("火路", "势策", "#619 顺")),
    "坎水上兑泽下 · 二爻守中": (("冰路", "势策", "#566 顺"),),
    "离火上震雷下 · 二爻守中": (("火路", "稳策", "#578 顺"),),
    "离火上乾天下 · 二爻守中": (("火路", "稳策", "#606 顺"),),
    "坎水上坤地下 · 二爻守中": (("冰路", "势策", "#608 顺"), ("冰路", "稳策", "#628 顺")),
    "兑泽上艮山下 · 三爻争锋": (("冰路", "压策", "#627 顺"),),
    "兑泽上坎水下 · 三爻争锋": (("冰路", "压策", "#640 顺"),),
    "震雷上兑泽下 · 初爻潜机": (("冰路", "稳策", "#642 顺"),),
    "艮山上巽风下 · 初爻潜机": (("冰路", "稳策", "#657 顺"),),
    "兑泽上离火下 · 四爻转阵": (("冰路", "稳策", "#659 顺"),),
}
_XUTIAN_ORACLE_FAILURE = {
    "乾天上巽风下 · 上爻游变": (("冰路", "势策", "#576 逆"),),
    "乾天上巽风下 · 四爻转阵": (("冰路", "稳策", "#662 逆"),),
    "震雷上艮山下 · 四爻转阵": (("火路", "稳策", "#579 逆"),),
    "艮山上震雷下 · 四爻转阵": (("火路", "势策", "#581 逆"),),
    "离火上离火下 · 三爻争锋": (("冰路", "稳策", "#620 逆"),),
    "离火上离火下 · 四爻转阵": (("冰路", "势策", "#629 逆"),),
    "震雷上坤地下 · 三爻争锋": (("冰路", "势策", "#630 逆"),),
    "乾天上艮山下 · 五爻乘时": (("冰路", "势策", "#632 逆"),),
    "离火上震雷下 · 四爻转阵": (("冰路", "稳策", "#633 逆"),),
    "兑泽上震雷下 · 三爻争锋": (("冰路", "势策", "#635 逆"),),
    "震雷上坤地下 · 二爻守中": (("火路", "稳策", "#648 逆"),),
    "乾天上离火下 · 三爻争锋": (("冰路", "压策", "#651 逆"),),
    "兑泽上坎水下 · 初爻潜机": (("火路", "势策", "#654 逆"),),
    "震雷上离火下 · 四爻转阵": (("冰路", "势策", "#969 逆"),),
}


def _get_replica_run_state_dict():
    run_state = get_replica_run_state()
    return dict(run_state) if isinstance(run_state, dict) else {}


def _save_replica_run_state_dict(run_state):
    set_replica_run_state(run_state if isinstance(run_state, dict) else {})
    mark_dirty()


def _get_lightweight_dungeon_state():
    run_state = _get_replica_run_state_dict()
    state_item = run_state.get("lightweight_dungeon")
    if not isinstance(state_item, dict):
        state_item = {}
    pending = state_item.get("pending_open")
    if not isinstance(pending, dict):
        pending = {}
    rooms = state_item.get("last_room_by_chat")
    if not isinstance(rooms, dict):
        rooms = {}
    state_item["pending_open"] = pending
    state_item["last_room_by_chat"] = rooms
    return state_item


def _save_lightweight_dungeon_state(state_item):
    run_state = _get_replica_run_state_dict()
    run_state["lightweight_dungeon"] = state_item if isinstance(state_item, dict) else {}
    _save_replica_run_state_dict(run_state)


def _normalize_lightweight_open_flow(flow_id, flow):
    if not isinstance(flow, dict):
        return None
    flow_id = str(flow.get("flow_id") or flow_id or "").strip()
    replica_kind = flow.get("replica_kind")
    if not flow_id or replica_kind not in _REPLICA_KINDS:
        return None
    try:
        replica_chat_id = int(flow.get("replica_chat_id") or 0)
        leader_identity_id = int(flow.get("leader_identity_id") or 0)
    except (TypeError, ValueError):
        return None
    if replica_chat_id == 0 or leader_identity_id <= 0:
        return None
    normalized = dict(flow)
    normalized["flow_id"] = flow_id
    normalized["replica_chat_id"] = replica_chat_id
    normalized["leader_identity_id"] = leader_identity_id
    return normalized


def _normalize_lightweight_room(chat_id, room):
    if not isinstance(room, dict):
        return None
    replica_kind = room.get("replica_kind")
    room_id = str(room.get("room_id") or "").strip()
    if not room_id or replica_kind not in _REPLICA_KINDS:
        return None
    try:
        replica_chat_id = int(room.get("replica_chat_id") or chat_id or 0)
    except (TypeError, ValueError):
        return None
    if replica_chat_id == 0:
        return None
    normalized = dict(room)
    normalized["room_id"] = room_id
    normalized["replica_chat_id"] = replica_chat_id
    return normalized


def _cleanup_lightweight_dungeon_state(now=None):
    now = float(now or time.time())
    state_item = _get_lightweight_dungeon_state()
    pending = state_item.get("pending_open") if isinstance(state_item.get("pending_open"), dict) else {}
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    changed = False
    for flow_id, flow in list(pending.items()):
        normalized_flow = _normalize_lightweight_open_flow(flow_id, flow)
        if normalized_flow is None:
            pending.pop(flow_id, None)
            changed = True
            continue
        if normalized_flow != flow:
            pending[flow_id] = normalized_flow
            changed = True
        expires_at = float(normalized_flow.get("expires_at") or 0)
        if expires_at > 0 and now >= expires_at:
            pending.pop(flow_id, None)
            changed = True
    for chat_id, room in list(rooms.items()):
        normalized_room = _normalize_lightweight_room(chat_id, room)
        if normalized_room is None:
            rooms.pop(chat_id, None)
            changed = True
            continue
        if normalized_room != room:
            rooms[chat_id] = normalized_room
            changed = True
        expires_at = float(normalized_room.get("expires_at") or 0)
        if expires_at > 0 and now >= expires_at:
            rooms.pop(chat_id, None)
            changed = True
    state_item["pending_open"] = pending
    state_item["last_room_by_chat"] = rooms
    if changed:
        _save_lightweight_dungeon_state(state_item)
    return state_item


def _make_lightweight_flow_id(chat_id, identity_id, now):
    return f"{int(chat_id or 0)}:{int(identity_id or 0)}:{int(float(now or 0) * 1000)}"


def _upsert_lightweight_open_flow(flow):
    if not isinstance(flow, dict):
        return False
    flow_id = str(flow.get("flow_id") or "").strip()
    if not flow_id:
        return False
    state_item = _cleanup_lightweight_dungeon_state()
    pending = state_item.get("pending_open")
    pending[flow_id] = flow
    state_item["pending_open"] = pending
    _save_lightweight_dungeon_state(state_item)
    return True


def _remove_lightweight_open_flow(flow_id):
    flow_id = str(flow_id or "").strip()
    if not flow_id:
        return False
    state_item = _get_lightweight_dungeon_state()
    pending = state_item.get("pending_open")
    if flow_id not in pending:
        return False
    pending.pop(flow_id, None)
    state_item["pending_open"] = pending
    _save_lightweight_dungeon_state(state_item)
    return True


def _find_lightweight_open_flow(reply_to_msg_id=0, send_as_id=0, leader_username="", replica_kind="", now=None):
    state_item = _cleanup_lightweight_dungeon_state(now)
    pending = state_item.get("pending_open") if isinstance(state_item.get("pending_open"), dict) else {}
    reply_to_msg_id = int(reply_to_msg_id or 0)
    send_as_id = int(send_as_id or 0)
    leader_username = _normalize_replica_username(leader_username)
    matches = []
    for flow in pending.values():
        if not isinstance(flow, dict):
            continue
        if replica_kind and flow.get("replica_kind") != replica_kind:
            continue
        if reply_to_msg_id > 0 and int(flow.get("open_command_msg_id") or 0) == reply_to_msg_id:
            return flow
        if send_as_id > 0 and int(flow.get("leader_identity_id") or 0) == send_as_id:
            matches.append(flow)
            continue
        if leader_username and _normalize_replica_username(flow.get("leader_username") or "") == leader_username:
            matches.append(flow)
    if not matches:
        return None
    matches.sort(key=lambda item: float(item.get("updated_at") or item.get("open_requested_at") or 0), reverse=True)
    return matches[0]


def _find_active_lightweight_open_flow(replica_chat_id=0, replica_kind="", leader_identity_id=0, now=None):
    state_item = _cleanup_lightweight_dungeon_state(now)
    pending = state_item.get("pending_open") if isinstance(state_item.get("pending_open"), dict) else {}
    replica_chat_id = int(replica_chat_id or 0)
    leader_identity_id = int(leader_identity_id or 0)
    matches = []
    for flow in pending.values():
        if not isinstance(flow, dict):
            continue
        if replica_chat_id and int(flow.get("replica_chat_id") or 0) != replica_chat_id:
            continue
        if replica_kind and flow.get("replica_kind") != replica_kind:
            continue
        if leader_identity_id and int(flow.get("leader_identity_id") or 0) != leader_identity_id:
            continue
        if flow.get("phase") not in {"opening"}:
            continue
        matches.append(flow)
    if not matches:
        return None
    matches.sort(key=lambda item: float(item.get("updated_at") or item.get("open_requested_at") or 0), reverse=True)
    return matches[0]


def _set_lightweight_last_room(room):
    if not isinstance(room, dict):
        return False
    chat_id = int(room.get("replica_chat_id") or 0)
    room_id = str(room.get("room_id") or "").strip()
    replica_kind = room.get("replica_kind")
    if chat_id == 0 or not room_id or replica_kind not in _REPLICA_KINDS:
        return False
    state_item = _cleanup_lightweight_dungeon_state()
    rooms = state_item.get("last_room_by_chat")
    existing = rooms.get(str(chat_id)) if isinstance(rooms, dict) else None
    if (
        isinstance(existing, dict)
        and str(existing.get("room_id") or "").strip() == room_id
        and existing.get("replica_kind") == replica_kind
    ):
        for key in ("recommendation_sent_opened_msg_id", "recommendation_sent_at"):
            if key in existing and key not in room:
                room[key] = existing.get(key)
    rooms[str(chat_id)] = room
    state_item["last_room_by_chat"] = rooms
    _save_lightweight_dungeon_state(state_item)
    return True


def _get_lightweight_last_room(replica_chat_id=0, now=None):
    state_item = _cleanup_lightweight_dungeon_state(now)
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    replica_chat_id = int(replica_chat_id or 0)
    if replica_chat_id:
        room = rooms.get(str(replica_chat_id))
        return room if isinstance(room, dict) else None
    candidates = [room for room in rooms.values() if isinstance(room, dict)]
    if not candidates:
        return None
    candidates.sort(key=lambda item: float(item.get("updated_at") or item.get("opened_at") or 0), reverse=True)
    return candidates[0]


def _get_active_lightweight_room(replica_chat_id=0, now=None):
    room = _get_lightweight_last_room(replica_chat_id, now=now)
    if not isinstance(room, dict):
        return None
    if room.get("phase") not in {"opened", "dissolve_requested"}:
        return None
    return room


def _format_lightweight_existing_open_notice(flow, *, html=False):
    flow = flow if isinstance(flow, dict) else {}
    replica_kind = flow.get("replica_kind")
    replica_name = (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or "副本"
    leader = flow.get("leader_username") or flow.get("selector") or ""
    lines = [
        f"已有{replica_name}开房请求：{leader or '未知队长'}，未重复发送开房命令。",
        "等开房广播出现后再加入；如果确认开房指令被吞，可先解散副本取消等待。",
        _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=html),
    ]
    return "\n".join(line for line in lines if line)


def _format_lightweight_cancel_open_notice(flow, *, html=False):
    flow = flow if isinstance(flow, dict) else {}
    replica_kind = flow.get("replica_kind")
    replica_name = (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or "副本"
    leader = flow.get("leader_username") or flow.get("selector") or ""
    lines = [
        f"已取消等待中的{replica_name}开房请求" + (f"：{leader}" if leader else "") + "。",
        "未发送解散命令；当前还没有记录到可解散的房间。",
        _format_lightweight_next_commands(".查询副本", ".开启副本 @用户名 <虚天|苍坤|坠魔|黄龙>", html=html),
    ]
    return "\n".join(line for line in lines if line)


def _is_lightweight_open_flow_active(flow, now=None):
    if not isinstance(flow, dict):
        return False
    now = float(now or time.time())
    try:
        expires_at = float(flow.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return expires_at <= 0 or now < expires_at


def _format_lightweight_existing_room_notice(room, *, html=False):
    room = room if isinstance(room, dict) else {}
    replica_kind = room.get("replica_kind")
    replica_name = (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or "副本"
    room_id = str(room.get("room_id") or "-")
    leader = room.get("leader_username") or ""
    phase = str(room.get("phase") or "")
    if phase == "dissolve_requested":
        lines = [
            f"{replica_name}房间 {room_id} 已请求解散，未重复开房。",
            _format_lightweight_next_commands(".查询副本", html=html),
        ]
    else:
        lines = [
            f"已有{replica_name}房间 {room_id}" + (f"｜队长 {leader}" if leader else "") + "，未重复开房。",
            _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=html),
        ]
    return "\n".join(line for line in lines if line)


def _format_lightweight_dissolve_pending_notice(room, *, html=False):
    room = room if isinstance(room, dict) else {}
    replica_kind = room.get("replica_kind")
    replica_name = (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or "副本"
    room_id = str(room.get("room_id") or "-")
    lines = [
        f"{replica_name}房间 {room_id} 已请求解散，未重复发送解散命令。",
        _format_lightweight_next_commands(".查询副本", html=html),
    ]
    return "\n".join(line for line in lines if line)


def _reserve_lightweight_room_dissolve(room, now=None, *, source="", source_msg_id=0):
    room = room if isinstance(room, dict) else {}
    chat_id = int(room.get("replica_chat_id") or 0)
    room_id = str(room.get("room_id") or "").strip()
    replica_kind = room.get("replica_kind")
    leader_identity_id = int(room.get("leader_identity_id") or 0)
    if chat_id == 0 or not room_id or replica_kind not in _REPLICA_KINDS or leader_identity_id <= 0:
        return "missing", {}
    now = float(now or time.time())
    state_item = _cleanup_lightweight_dungeon_state(now)
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    current = rooms.get(str(chat_id))
    if not isinstance(current, dict):
        return "missing", {}
    if (
        str(current.get("room_id") or "").strip() != room_id
        or current.get("replica_kind") != replica_kind
        or int(current.get("leader_identity_id") or 0) != leader_identity_id
    ):
        return "missing", dict(current)
    phase = str(current.get("phase") or "")
    if phase == "dissolve_requested":
        return "pending", dict(current)
    if phase in {"dissolved", "entered"}:
        return "closed", dict(current)
    current.update({
        "phase": "dissolve_requested",
        "dissolve_previous_phase": phase or "opened",
        "dissolve_requested_at": now,
        "dissolve_source": str(source or ""),
        "dissolve_source_msg_id": int(source_msg_id or 0),
        "updated_at": now,
    })
    rooms[str(chat_id)] = current
    state_item["last_room_by_chat"] = rooms
    _save_lightweight_dungeon_state(state_item)
    room.update(current)
    return "reserved", dict(current)


def _finish_lightweight_room_dissolve_send(room, msg_id=0, now=None, *, error=""):
    room = room if isinstance(room, dict) else {}
    chat_id = int(room.get("replica_chat_id") or 0)
    room_id = str(room.get("room_id") or "").strip()
    replica_kind = room.get("replica_kind")
    if chat_id == 0 or not room_id or replica_kind not in _REPLICA_KINDS:
        return False
    now = float(now or time.time())
    state_item = _get_lightweight_dungeon_state()
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    current = rooms.get(str(chat_id))
    if not isinstance(current, dict):
        return False
    if str(current.get("room_id") or "").strip() != room_id or current.get("replica_kind") != replica_kind:
        return False
    msg_id = int(msg_id or 0)
    if msg_id > 0:
        current.update({
            "phase": "dissolve_requested",
            "dissolve_msg_id": msg_id,
            "updated_at": now,
            "last_error": "",
        })
    else:
        previous_phase = str(current.get("dissolve_previous_phase") or "opened")
        if previous_phase in {"dissolve_requested", "dissolved", "entered"}:
            previous_phase = "opened"
        current.update({
            "phase": previous_phase,
            "updated_at": now,
            "last_error": str(error or "解散命令发送失败"),
        })
    rooms[str(chat_id)] = current
    state_item["last_room_by_chat"] = rooms
    _save_lightweight_dungeon_state(state_item)
    room.update(current)
    return True


def _mark_lightweight_room_recommendation_sent(room, now=None):
    room = room if isinstance(room, dict) else {}
    chat_id = int(room.get("replica_chat_id") or 0)
    room_id = str(room.get("room_id") or "").strip()
    replica_kind = room.get("replica_kind")
    opened_msg_id = int(room.get("opened_msg_id") or 0)
    if chat_id == 0 or not room_id or replica_kind not in _REPLICA_KINDS:
        return False
    state_item = _cleanup_lightweight_dungeon_state(now)
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    current = rooms.get(str(chat_id))
    if not isinstance(current, dict):
        current = dict(room)
    if (
        str(current.get("room_id") or "").strip() == room_id
        and current.get("replica_kind") == replica_kind
        and int(current.get("recommendation_sent_opened_msg_id") or 0) == opened_msg_id
        and opened_msg_id > 0
    ):
        return False
    current.update(dict(room))
    current["recommendation_sent_opened_msg_id"] = opened_msg_id
    current["recommendation_sent_at"] = float(now or time.time())
    current["updated_at"] = float(now or time.time())
    rooms[str(chat_id)] = current
    state_item["last_room_by_chat"] = rooms
    _save_lightweight_dungeon_state(state_item)
    room.update(current)
    return True


def _parse_replica_entered_kind(text):
    raw_text = str(text or "")
    if "队伍已进入虚天殿" in raw_text:
        return _REPLICA_KIND_VIRTUAL_HALL
    if "队伍已进入坠魔谷" in raw_text:
        return _REPLICA_KIND_ZHUIMO
    if "队伍已进入黄龙山" in raw_text:
        return _REPLICA_KIND_HUANGLONG
    if "队伍已进入苍坤洞府" in raw_text or "队伍已进入苍坤上人洞府" in raw_text:
        return _REPLICA_KIND_CANGKUN
    return ""


def _mark_latest_lightweight_room_entered(replica_kind="", now=None):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else ""
    if not replica_kind:
        return {}
    now = float(now or time.time())
    state_item = _cleanup_lightweight_dungeon_state(now)
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    candidates = []
    for chat_id, room in rooms.items():
        if not isinstance(room, dict):
            continue
        if room.get("replica_kind") != replica_kind:
            continue
        if room.get("phase") not in {"opened", "dissolve_requested"}:
            continue
        candidates.append((str(chat_id), room))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: float(item[1].get("updated_at") or item[1].get("opened_at") or 0), reverse=True)
    chat_id, room = candidates[0]
    room.update({"phase": "entered", "entered_at": now, "updated_at": now, "expires_at": now + 60})
    rooms[chat_id] = room
    state_item["last_room_by_chat"] = rooms
    _save_lightweight_dungeon_state(state_item)
    return dict(room)


def _mark_lightweight_room_dissolved(room_id, leader_username="", replica_kind="", now=None):
    room_id = str(room_id or "").strip()
    if not room_id:
        return False
    leader_username = _normalize_replica_username(leader_username)
    now = float(now or time.time())
    state_item = _get_lightweight_dungeon_state()
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    changed = False
    for chat_id, room in list(rooms.items()):
        if not isinstance(room, dict) or str(room.get("room_id") or "").strip() != room_id:
            continue
        if replica_kind and room.get("replica_kind") != replica_kind:
            continue
        room_leader = _normalize_replica_username(room.get("leader_username") or "")
        if leader_username and room_leader and leader_username != room_leader:
            continue
        room.update({"phase": "dissolved", "dissolved_at": now, "updated_at": now, "expires_at": now + 60})
        rooms[chat_id] = room
        changed = True
    if changed:
        state_item["last_room_by_chat"] = rooms
        _save_lightweight_dungeon_state(state_item)
    return changed


def _find_lightweight_room_for_dissolve_notice(room_id, leader_username="", replica_kind="", now=None):
    room_id = str(room_id or "").strip()
    if not room_id:
        return {}
    leader_username = _normalize_replica_username(leader_username)
    state_item = _cleanup_lightweight_dungeon_state(now)
    rooms = state_item.get("last_room_by_chat") if isinstance(state_item.get("last_room_by_chat"), dict) else {}
    for room in rooms.values():
        if not isinstance(room, dict) or str(room.get("room_id") or "").strip() != room_id:
            continue
        if replica_kind and room.get("replica_kind") != replica_kind:
            continue
        room_leader = _normalize_replica_username(room.get("leader_username") or "")
        if leader_username and room_leader and leader_username != room_leader:
            continue
        return dict(room)
    return {}


def _format_lightweight_dissolve_confirm_notice(room_id, replica_kind="", leader_username="", raw_text="", *, html=False):
    room_id = str(room_id or "").strip() or "-"
    replica_name = (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or "副本"
    leader_username = _normalize_replica_username(leader_username)
    lines = [f"已确认解散{replica_name}房间 {room_id}" + (f"｜队长 {leader_username}" if leader_username else "")]
    raw_text = str(raw_text or "")
    if "归还" in raw_text:
        returned_lines = [line.strip() for line in raw_text.splitlines() if "归还" in line]
        if returned_lines:
            lines.append(returned_lines[0])
    lines.append(_format_lightweight_next_commands(".查询副本", html=html))
    text = "\n".join(lines)
    return escape(text) if not html else "\n".join(
        escape(line) if not line.startswith("可复制命令：") and "<code>" not in line else line
        for line in lines
    )


def _normalize_replica_ticket_item_name(item_name, *, sect_huanglong=False):
    item_name = str(item_name or "").strip()
    if item_name == "黄龙急援令" and sect_huanglong:
        return "黄龙急援令（宗门版）"
    return item_name if item_name in _REPLICA_TICKET_ITEMS else ""


def _get_storage_bag_item_count(identity_id, item_name):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        return 0
    record = (get_storage_bag_records() or {}).get(str(identity_id))
    if not isinstance(record, dict):
        return 0
    items = record.get("items")
    if not isinstance(items, dict):
        return 0
    try:
        return int(items.get(str(item_name or "").strip()) or 0)
    except (TypeError, ValueError):
        return 0


def _get_replica_ticket_kind_count(identity_id, replica_kind):
    meta = _REPLICA_TICKET_META.get(replica_kind) or {}
    return sum(_get_storage_bag_item_count(identity_id, item_name) for item_name in meta.get("ticket_items") or ())


def _get_replica_ticket_counts(identity_id):
    return {replica_kind: _get_replica_ticket_kind_count(identity_id, replica_kind) for replica_kind in _REPLICA_KINDS}


def _get_openable_replica_kinds(identity_id):
    openable_kinds = []
    for replica_kind in _REPLICA_KIND_OPEN_PRIORITY:
        if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
            continue
        if _get_replica_ticket_kind_count(identity_id, replica_kind) > 0:
            openable_kinds.append(replica_kind)
    return openable_kinds


def _has_openable_replica_ticket(identity_id):
    return bool(_get_openable_replica_kinds(identity_id))


def _resolve_replica_kind_alias(text):
    raw_text = str(text or "").strip().strip("，,。.;；:：")
    if not raw_text:
        return ""
    for replica_kind, meta in _REPLICA_TICKET_META.items():
        aliases = set(meta.get("aliases") or ())
        kind_meta = _REPLICA_KIND_META.get(replica_kind) or {}
        aliases.add(kind_meta.get("name") or "")
        aliases.add(kind_meta.get("short") or "")
        if raw_text in aliases:
            return replica_kind
    return ""


def _select_open_replica_kind(identity_id, requested_kind=""):
    requested_kind = requested_kind if requested_kind in _REPLICA_KINDS else ""
    if requested_kind:
        if _get_replica_ticket_kind_count(identity_id, requested_kind) <= 0:
            return ""
        if requested_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
            return ""
        return requested_kind
    openable_kinds = _get_openable_replica_kinds(identity_id)
    return openable_kinds[0] if len(openable_kinds) == 1 else ""


def _get_replica_listener_account_ids():
    listener_ids = set()
    listener_maps = (get_replica_listener_account_map() or {}, get_replica_dispatch_listener_account_map() or {})
    for listener_map in listener_maps:
        for account_id in listener_map.values():
            try:
                normalized_id = int(account_id or 0)
            except (TypeError, ValueError):
                normalized_id = 0
            if normalized_id > 0:
                listener_ids.add(normalized_id)
    return listener_ids


def _get_replica_candidate_identity_ids(*, require_username=False, require_ticket=False, participant_identity_ids=None, fallback_to_all=True):
    if participant_identity_ids is None:
        participant_ids = [int(identity_id) for identity_id in get_replica_participant_identity_ids()]
    else:
        participant_ids = []
        seen_participant_ids = set()
        for raw_identity_id in participant_identity_ids or []:
            try:
                identity_id = int(raw_identity_id)
            except (TypeError, ValueError):
                continue
            if identity_id <= 0 or identity_id in seen_participant_ids:
                continue
            seen_participant_ids.add(identity_id)
            participant_ids.append(identity_id)
    explicit_participants = bool(participant_ids)
    if participant_identity_ids is not None and not explicit_participants and not fallback_to_all:
        return []
    identity_ids = participant_ids if explicit_participants else [int(identity_id) for identity_id in get_identity_ids()]
    listener_account_ids = _get_replica_listener_account_ids()
    candidates = []
    seen = set()
    for identity_id in identity_ids:
        if identity_id in seen:
            continue
        seen.add(identity_id)
        if not get_identity_enabled(identity_id):
            continue
        if not explicit_participants and identity_id in listener_account_ids:
            continue
        if require_username and not _normalize_replica_username(get_send_as_profile(identity_id).get("username") or ""):
            continue
        if require_ticket and not _has_openable_replica_ticket(identity_id):
            continue
        candidates.append(identity_id)
    return candidates


def _get_identity_id_by_replica_username(username, *, include_disabled=True):
    username = _normalize_replica_username(username)
    if not username:
        return 0
    for identity_id in get_identity_ids():
        if not include_disabled and not get_identity_enabled(identity_id):
            continue
        profile_username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        if profile_username == username:
            return int(identity_id)
    return 0


def _resolve_replica_command_identity(selector):
    selector = str(selector or "").strip()
    if not selector:
        return 0
    identity_id = resolve_identity_selector(selector)
    if identity_id:
        return int(identity_id)
    if selector.startswith("@"):
        return _get_identity_id_by_replica_username(selector, include_disabled=False)
    return 0


def _format_replica_ticket_counts(identity_id):
    counts = _get_replica_ticket_counts(identity_id)
    parts = []
    for replica_kind in _REPLICA_KIND_OPEN_PRIORITY:
        if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
            continue
        count = int(counts.get(replica_kind) or 0)
        if count > 0:
            parts.append(f"{_REPLICA_KIND_META[replica_kind]['short']}x{count}")
    return " ".join(parts)


def _format_lightweight_command_lines(*commands, html=False):
    filtered = [str(command or "").strip() for command in commands if str(command or "").strip()]
    if not filtered:
        return ""
    return "\n".join(mono(command) if html else command for command in filtered)


def _format_lightweight_next_commands(*commands, html=False):
    command_lines = _format_lightweight_command_lines(*commands, html=html)
    if not command_lines:
        return ""
    return "可复制命令：\n" + command_lines


def _format_lightweight_reply_text(text, *, html=False):
    return escape(str(text or "")) if html else str(text or "")


def _get_replica_identity_block_reason(identity_id, *, now=None):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        return "身份无效"
    if identity_id <= 0:
        return "身份无效"
    if not get_identity_enabled(identity_id):
        return "身份未启用"
    if not get_global_enabled():
        return "全局暂停"
    if is_dungeon_quiet_active(now):
        quiet_reason = get_dungeon_quiet_reason() or "副本静场令"
        return f"{quiet_reason}生效中，恢复 {format_dungeon_quiet_until()}"
    account_id = int(get_identity_account(identity_id) or 0)
    if account_id and is_account_offline(account_id):
        offline_reason = get_account_offline_reason(account_id) or "账号离线"
        return f"账号离线:{offline_reason}"
    if account_id and get_registered_client(account_id) is None:
        return f"账号未连接:acc={account_id}"
    try:
        if should_pause_for_bot_health():
            return "天尊静默/暂停"
    except Exception:
        traceback.print_exc()
    try:
        identity_state = get_identity_state(identity_id)
    except Exception:
        return ""
    try:
        weak_until = float(identity_state.get("weak_until", 0) or 0)
    except (TypeError, ValueError):
        weak_until = 0.0
    now = float(now or time.time())
    if weak_until > now:
        weak_label = "静思悟道" if str(identity_state.get("weak_source") or "") == "jingsi" else "虚弱状态"
        return f"{weak_label}至 {fmt_abs_ts(weak_until)}（{fmt_remaining(weak_until)}）"
    return ""


def _format_replica_skipped_selector(selector, reason=""):
    selector = str(selector or "").strip() or "未知身份"
    reason = str(reason or "").strip()
    return f"{selector}({reason})" if reason else selector


def _format_lightweight_join_command_for_room(room, selectors="@用户名 @用户名"):
    if not room:
        return ""
    selectors = str(selectors or "").strip() or "@用户名 @用户名"
    return f".加入副本 {selectors}"


def _format_lightweight_open_command_for_identity(identity_id, replica_kind=""):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return ""
    username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
    selector = username or str(identity_id)
    kind_text = (_REPLICA_KIND_META.get(replica_kind) or {}).get("short") or (_REPLICA_KIND_META.get(replica_kind) or {}).get("name") or ""
    return f".开启副本 {selector}" + (f" {kind_text}" if kind_text else "")


def _format_lightweight_open_commands_for_identity(identity_id, *, html=False):
    return _format_lightweight_command_lines(
        *[
            _format_lightweight_open_command_for_identity(identity_id, replica_kind)
            for replica_kind in _get_openable_replica_kinds(identity_id)
        ],
        html=html,
    )


def _format_lightweight_open_command_sections(*, html=False, limit_per_kind=8, now=None, records=None):
    now = float(now or time.time())
    records = records if isinstance(records, dict) else _cleanup_replica_run_state(now)
    grouped = {replica_kind: [] for replica_kind in _REPLICA_KIND_OPEN_PRIORITY}
    for identity_id in _get_replica_candidate_identity_ids(require_username=True, require_ticket=True):
        for replica_kind in _REPLICA_KIND_OPEN_PRIORITY:
            if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
                continue
            if _get_replica_identity_kind_status(identity_id, replica_kind, now, records=records) != "可":
                continue
            if _get_replica_ticket_kind_count(identity_id, replica_kind) <= 0:
                continue
            command = _format_lightweight_open_command_for_identity(identity_id, replica_kind)
            if command:
                grouped.setdefault(replica_kind, []).append(command)
    lines = []
    for replica_kind in _REPLICA_KIND_OPEN_PRIORITY:
        commands = grouped.get(replica_kind) or []
        if not commands:
            continue
        lines.append(f"{_REPLICA_KIND_META[replica_kind]['name']}：")
        lines.append(_format_lightweight_command_lines(*commands[: int(limit_per_kind or 8)], html=html))
    if not lines:
        return ""
    return "可复制开房命令（按副本）：\n" + "\n".join(lines)


def _find_preferred_ticket_opener(replica_kind):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else ""
    if not replica_kind:
        return 0
    for identity_id in _get_replica_candidate_identity_ids(require_username=True, require_ticket=True):
        if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
            continue
        if _get_replica_ticket_kind_count(identity_id, replica_kind) > 0:
            return int(identity_id or 0)
    return 0


def _get_replica_profile_professions(identity_id):
    raw_text = str(get_send_as_profile(identity_id).get("replica_professions") or "")
    professions = []
    for item in re.split(r"[|/,，、\s]+", raw_text):
        item = str(item or "").strip()
        if item and item != "未匹配" and item not in professions:
            professions.append(item)
    return professions


def _get_cangkun_realm(identity_id):
    profile = get_send_as_profile(identity_id)
    return str(profile.get("realm") or "").strip()


def _is_cangkun_realm_available(identity_id):
    realm = _get_cangkun_realm(identity_id)
    if not realm or realm not in REALM_SORT_ORDER:
        return False
    return REALM_SORT_ORDER.index(realm) >= _CANGKUN_MIN_REALM_INDEX


def _get_cangkun_root_grade_rank(identity_id):
    profile = get_send_as_profile(identity_id)
    root_type = str(profile.get("spiritual_root_type") or "")
    for index, marker in enumerate(_CANGKUN_ROOT_GRADE_PRIORITY):
        if marker in root_type:
            return index
    return len(_CANGKUN_ROOT_GRADE_PRIORITY)


def _normalize_replica_sect_name(text):
    return str(text or "").strip().strip("【】[]")


def _is_cangkun_preferred_sect_identity(identity_id):
    profile = get_send_as_profile(identity_id)
    return _normalize_replica_sect_name(profile.get("sect_name") or "") == _CANGKUN_PREFERRED_SECT


def _best_cangkun_profession_assignment(identity_ids, *, leader_identity_id=0):
    leader_identity_id = int(leader_identity_id or 0)
    normalized_ids = []
    seen = set()
    for identity_id in identity_ids or []:
        identity_id = int(identity_id or 0)
        if identity_id <= 0 or identity_id in seen or not _is_cangkun_realm_available(identity_id):
            continue
        if not set(_get_replica_profile_professions(identity_id)).intersection(_CANGKUN_REQUIRED_PROFESSIONS):
            continue
        seen.add(identity_id)
        normalized_ids.append(identity_id)

    if not normalized_ids:
        return []

    leader_roles = set(_get_replica_profile_professions(leader_identity_id)).intersection(_CANGKUN_REQUIRED_PROFESSIONS)
    require_leader = leader_identity_id in seen and bool(leader_roles)
    role_candidates = {
        role: [
            identity_id
            for identity_id in normalized_ids
            if role in _get_replica_profile_professions(identity_id)
        ]
        for role in _CANGKUN_REQUIRED_PROFESSIONS
    }

    def role_identity_sort_key(identity_id):
        professions = _get_replica_profile_professions(identity_id)
        username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        return (
            0 if _is_cangkun_preferred_sect_identity(identity_id) else 1,
            _get_cangkun_root_grade_rank(identity_id),
            -sum(1 for role in _CANGKUN_REQUIRED_PROFESSIONS if role in professions),
            username,
        )

    best_assignments = []
    best_score = None
    best_key = ""

    def consider(assignments):
        nonlocal best_assignments, best_score, best_key
        if require_leader and not any(identity_id == leader_identity_id for _role, identity_id in assignments):
            return
        assigned_ids = [identity_id for _role, identity_id in assignments]
        coverage_count = len(assignments)
        preferred_count = sum(1 for identity_id in assigned_ids if _is_cangkun_preferred_sect_identity(identity_id))
        root_score = sum(_get_cangkun_root_grade_rank(identity_id) for identity_id in assigned_ids)
        role_count_score = sum(
            sum(1 for role in _CANGKUN_REQUIRED_PROFESSIONS if role in _get_replica_profile_professions(identity_id))
            for identity_id in assigned_ids
        )
        score = (coverage_count, preferred_count, -root_score, role_count_score)
        key = "|".join(
            f"{role}:{_normalize_replica_username(get_send_as_profile(identity_id).get('username') or identity_id)}"
            for role, identity_id in assignments
        )
        if best_score is None or score > best_score or (score == best_score and key < best_key):
            best_score = score
            best_key = key
            best_assignments = list(assignments)

    def dfs(index, used_ids, assignments):
        if index >= len(_CANGKUN_REQUIRED_PROFESSIONS):
            consider(assignments)
            return
        role = _CANGKUN_REQUIRED_PROFESSIONS[index]
        dfs(index + 1, used_ids, assignments)
        for identity_id in sorted(role_candidates.get(role) or [], key=role_identity_sort_key):
            if identity_id in used_ids:
                continue
            used_ids.add(identity_id)
            assignments.append((role, identity_id))
            dfs(index + 1, used_ids, assignments)
            assignments.pop()
            used_ids.remove(identity_id)

    dfs(0, set(), [])
    return best_assignments


def _get_cangkun_profession_coverage(identity_ids):
    assignments = _best_cangkun_profession_assignment(identity_ids)
    return {role for role, _identity_id in assignments}


def _format_cangkun_profession_coverage(identity_ids, *, leader_identity_id=0):
    assignments = _best_cangkun_profession_assignment(identity_ids, leader_identity_id=leader_identity_id)
    covered = {role for role, _identity_id in assignments}
    covered_text = "、".join(role for role in _CANGKUN_REQUIRED_PROFESSIONS if role in covered) or "无"
    missing_text = "、".join(role for role in _CANGKUN_REQUIRED_PROFESSIONS if role not in covered) or "无"
    return covered_text, missing_text


def _format_cangkun_realm_requirement(identity_id):
    realm = _get_cangkun_realm(identity_id) or "未知"
    return f"苍坤要求{_CANGKUN_MIN_REALM}及以上，当前境界：{realm}"


def _get_lightweight_available_identity_ids(replica_kind, now=None):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    now = float(now or time.time())
    records = _cleanup_replica_run_state(now)
    identity_ids = []
    for identity_id in _get_replica_candidate_identity_ids(require_username=True):
        if _get_replica_identity_kind_status(identity_id, replica_kind, now, records=records) == "可":
            identity_ids.append(int(identity_id or 0))
    return identity_ids


def _pick_lightweight_profession_team(replica_kind, leader_identity_id=0, *, limit=4, now=None):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_CANGKUN
    leader_identity_id = int(leader_identity_id or 0)
    limit = max(1, int(limit or 4))
    now = float(now or time.time())
    candidates = [
        identity_id
        for identity_id in _get_lightweight_available_identity_ids(replica_kind, now=now)
        if identity_id and identity_id != leader_identity_id
    ]
    if not candidates:
        return []
    if replica_kind == _REPLICA_KIND_CANGKUN:
        candidates = [identity_id for identity_id in candidates if _is_cangkun_realm_available(identity_id)]
        assignments = _best_cangkun_profession_assignment(
            ([leader_identity_id] if leader_identity_id else []) + candidates,
            leader_identity_id=leader_identity_id,
        )
        return [
            identity_id
            for _role, identity_id in assignments
            if identity_id != leader_identity_id
        ][:limit]
    else:
        role_priority = ["破军", "御山", "灵医", "影刃", "咒师"]
        covered = set(_get_replica_profile_professions(leader_identity_id)) if leader_identity_id else set()
    selected = []
    used = set()

    def candidate_score(identity_id, desired_role=""):
        professions = set(_get_replica_profile_professions(identity_id))
        username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        if replica_kind == _REPLICA_KIND_CANGKUN:
            desired_miss = 0 if not desired_role or desired_role in professions else 1
            role_count = sum(1 for role in role_priority if role in professions)
            return (desired_miss, _get_cangkun_root_grade_rank(identity_id), -role_count, username)
        score = 0
        if desired_role and desired_role in professions:
            score += 100
        score += sum(8 for role in role_priority if role in professions)
        root_attrs = str(get_send_as_profile(identity_id).get("spiritual_root_attrs") or "")
        if "金" in root_attrs:
            score += 4
        if root_attrs and root_attrs != "未获取":
            score += 2
        return (-score, username)

    roles_to_fill = role_priority if replica_kind == _REPLICA_KIND_CANGKUN else role_priority[:3]
    for role in roles_to_fill:
        if role in covered or len(selected) >= limit:
            continue
        matches = [identity_id for identity_id in candidates if identity_id not in used and role in _get_replica_profile_professions(identity_id)]
        if not matches:
            continue
        identity_id = sorted(matches, key=lambda item: candidate_score(item, role))[0]
        selected.append(identity_id)
        used.add(identity_id)
        covered.update(_get_replica_profile_professions(identity_id))
    if replica_kind == _REPLICA_KIND_CANGKUN:
        return selected
    for identity_id in sorted([item for item in candidates if item not in used], key=candidate_score):
        if len(selected) >= limit:
            break
        selected.append(identity_id)
        used.add(identity_id)
    return selected


def _format_lightweight_profession_recommendation_section(replica_kind, leader_identity_id=0, *, html=False):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_CANGKUN
    leader_identity_id = int(leader_identity_id or 0)
    leader_username = _normalize_replica_username(get_send_as_profile(leader_identity_id).get("username") or "") if leader_identity_id > 0 else ""
    team_ids = _pick_lightweight_profession_team(replica_kind, leader_identity_id=leader_identity_id, limit=4 if leader_identity_id else 5)
    usernames = [
        _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        for identity_id in team_ids
    ]
    usernames = [username for username in usernames if username]
    replica_name = _REPLICA_KIND_META[replica_kind]["name"]
    leader_text = f"（开房 {leader_username}）" if leader_username else ""
    lines = [f"推荐配置：{replica_name}｜职业补位{leader_text}"]
    if usernames:
        command = ".加入副本 " + " ".join(usernames)
        lines.append(_format_lightweight_command_lines(command, html=html))
    else:
        lines.append("暂未找到可参加且带 username 的补位身份。")
    if replica_kind == _REPLICA_KIND_CANGKUN:
        covered_text, missing_text = _format_cangkun_profession_coverage(
            ([leader_identity_id] if leader_identity_id else []) + team_ids,
            leader_identity_id=leader_identity_id,
        )
        lines.append(f"覆盖职业：{covered_text}")
        if missing_text == "无":
            lines.append("五职业已齐。")
        else:
            lines.append(f"缺职业：{missing_text}")
        if leader_identity_id > 0 and not _is_cangkun_realm_available(leader_identity_id):
            lines.append(f"开房身份未达要求，不计入职业覆盖：{_format_cangkun_realm_requirement(leader_identity_id)}。")
        lines.append("提示：苍坤要求结丹初期及以上且五职业齐全：破军/御山/灵医/影刃/咒师；入本默认 .苍坤抉择 1 / .苍坤抉择 1 / .苍坤抉择 2。")
    else:
        lines.append("提示：先按破军/御山/灵医补齐，若后续出现专属卦象再按卦象改配。")
    return "\n".join(lines)


def _format_replica_ticket_query_reply(*, html=False):
    lines = []
    now = time.time()
    records = _cleanup_replica_run_state(now)
    for identity_id in _get_replica_candidate_identity_ids(require_username=True, require_ticket=True):
        profile = get_send_as_profile(identity_id)
        username = _normalize_replica_username(profile.get("username") or "")
        ticket_text = _format_replica_ticket_counts(identity_id)
        if not ticket_text:
            continue
        root_attrs = str(profile.get("spiritual_root_attrs") or "").strip() or "未获取"
        display_root_attrs = _format_replica_query_root_attrs(root_attrs, get_replica_gold_dps_enabled(identity_id))
        professions = str(profile.get("replica_professions") or "").strip() or "未匹配"
        status_text = _format_replica_identity_statuses(identity_id, now, records=records)
        lines.append(f"{mono(username)} | {ticket_text} | {status_text} | {display_root_attrs} | {professions}")
    if lines:
        reply = "可开副本：\n" + "\n".join(lines)
        open_sections = _format_lightweight_open_command_sections(html=html, now=now, records=records)
        if open_sections:
            reply += "\n\n" + open_sections
        reply += "\n\n" + _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=html)
        return reply
    return "当前没有可开副本的参与身份；请先同步储物袋或等待门票入账。\n\n" + _format_lightweight_next_commands(".查询副本", html=html)


def _cleanup_replica_ticket_event_records(now=None):
    now = float(now or time.time())
    run_state = _get_replica_run_state_dict()
    records = run_state.get("ticket_events")
    if not isinstance(records, dict):
        records = {}
    changed = False
    for key, ts in list(records.items()):
        try:
            event_ts = float(ts or 0)
        except (TypeError, ValueError):
            event_ts = 0
        if event_ts <= 0 or now >= event_ts + _REPLICA_TICKET_EVENT_TTL_SEC:
            records.pop(key, None)
            changed = True
    if len(records) > _REPLICA_TICKET_EVENT_MAX:
        keep = {
            key
            for key, _ts in sorted(records.items(), key=lambda item: float(item[1] or 0), reverse=True)[:_REPLICA_TICKET_EVENT_MAX]
        }
        for key in list(records):
            if key not in keep:
                records.pop(key, None)
                changed = True
    run_state["ticket_events"] = records
    if changed:
        _save_replica_run_state_dict(run_state)
    return records


def _make_replica_ticket_event_key(event, text):
    try:
        msg_id = int(getattr(event, "id", 0) or 0)
    except (TypeError, ValueError):
        msg_id = 0
    try:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        chat_id = 0
    if msg_id > 0:
        return f"msg:{chat_id}:{msg_id}"
    digest = hashlib.sha1(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"text:{digest}"


def _mark_replica_ticket_event_processed(key, now):
    key = str(key or "").strip()
    if not key:
        return False
    run_state = _get_replica_run_state_dict()
    records = _cleanup_replica_ticket_event_records(now)
    if key in records:
        return False
    records[key] = float(now or time.time())
    run_state["ticket_events"] = records
    _save_replica_run_state_dict(run_state)
    return True


def _extract_replica_ticket_deltas_from_text(text, reply_context=None):
    raw_text = str(text or "")
    reply_context = reply_context if isinstance(reply_context, dict) else {}
    own_identity_id = int(reply_context.get("send_as_id") or 0)
    deltas_by_identity = {}

    def add_delta(identity_id, item_name, delta):
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        item_name = _normalize_replica_ticket_item_name(item_name)
        delta = int(delta or 0)
        if identity_id <= 0 or not item_name or delta == 0:
            return
        identity_deltas = deltas_by_identity.setdefault(identity_id, {})
        identity_deltas[item_name] = int(identity_deltas.get(item_name) or 0) + delta

    opened_match = _REPLICA_OPENED_RE.search(raw_text)
    if opened_match:
        opened_kind_name = opened_match.group("opened_kind_name") or opened_match.group("opened_zhuimo") or opened_match.group("opened_huanglong") or opened_match.group("opened_cangkun") or raw_text
        replica_kind = _infer_replica_kind_from_text(opened_kind_name)
        leader_identity_id = _get_identity_id_by_replica_username(opened_match.group("leader")) or own_identity_id
        if replica_kind == _REPLICA_KIND_VIRTUAL_HALL and "虚天残图" in raw_text:
            add_delta(leader_identity_id, "虚天残图", -1)
        elif replica_kind == _REPLICA_KIND_CANGKUN and "苍坤残图" in raw_text:
            add_delta(leader_identity_id, "苍坤残图", -1)
        elif replica_kind == _REPLICA_KIND_ZHUIMO and "坠魔谷禁制令" in raw_text:
            add_delta(leader_identity_id, "坠魔谷禁制令", -1)
        elif replica_kind == _REPLICA_KIND_HUANGLONG and "黄龙急援令" in raw_text:
            add_delta(leader_identity_id, "黄龙急援令（宗门版）" if "宗门版" in raw_text else "黄龙急援令", -1)

    if "归还" in raw_text:
        leader_username, _room_id = _parse_replica_room_dissolved(raw_text)
        if not leader_username:
            dissolved_match = _REPLICA_KIND_ROOM_DISSOLVED_RE.search(raw_text)
            leader_username = dissolved_match.group(1) if dissolved_match else ""
        leader_identity_id = _get_identity_id_by_replica_username(leader_username) or own_identity_id
        if "虚天残图" in raw_text:
            add_delta(leader_identity_id, "虚天残图", 1)
        if "苍坤残图" in raw_text:
            add_delta(leader_identity_id, "苍坤残图", 1)
        if "坠魔谷禁制令" in raw_text:
            add_delta(leader_identity_id, "坠魔谷禁制令", 1)
        if "黄龙急援令" in raw_text:
            add_delta(leader_identity_id, "黄龙急援令（宗门版）" if "宗门版" in raw_text else "黄龙急援令", 1)

    for item_name, count_text in re.findall(r"(虚天残图|苍坤残图|坠魔谷禁制令|黄龙急援令(?:（宗门版）)?)\s*[x×]\s*(\d+)", raw_text):
        if "你获得" not in raw_text and "获得：" not in raw_text:
            continue
        add_delta(own_identity_id, item_name, int(count_text or 0))

    if reply_context.get("family") != "storage_bag_gift":
        for match in re.finditer(
            r"道友\s*(?P<source>@[A-Za-z0-9_]{3,32})\s*向\s*(?P<target>@[A-Za-z0-9_]{3,32})\s*赠送了\s*【(?P<item>虚天残图|苍坤残图|坠魔谷禁制令|黄龙急援令)】\s*[x×]\s*(?P<count>\d+)",
            raw_text,
        ):
            item_name = _normalize_replica_ticket_item_name(match.group("item"))
            count = int(match.group("count") or 0)
            if not item_name or count <= 0:
                continue
            add_delta(_get_identity_id_by_replica_username(match.group("source")), item_name, -count)
            add_delta(_get_identity_id_by_replica_username(match.group("target")), item_name, count)

    for match in re.finditer(r"(?P<users>(?:@[A-Za-z0-9_]{3,32}[、,，\s]*)+)\s*获得\s*【(?P<item>虚天残图|苍坤残图|坠魔谷禁制令|黄龙急援令)】", raw_text):
        item_name = match.group("item")
        sect_huanglong = item_name == "黄龙急援令" and "宗门版" in raw_text[max(0, match.start() - 20):match.end() + 20]
        item_name = _normalize_replica_ticket_item_name(item_name, sect_huanglong=sect_huanglong)
        for username in _extract_replica_usernames(match.group("users") or ""):
            add_delta(_get_identity_id_by_replica_username(username), item_name, 1)

    sect_match = re.search(r"队长\s*(@[A-Za-z0-9_]{3,32})\s*已获得当日宗门版\s*【黄龙急援令】", raw_text)
    if sect_match:
        add_delta(_get_identity_id_by_replica_username(sect_match.group(1)), "黄龙急援令（宗门版）", 1)

    return deltas_by_identity


def apply_replica_ticket_text_deltas(event, text, now, reply_context=None):
    deltas_by_identity = _extract_replica_ticket_deltas_from_text(text, reply_context=reply_context)
    if not deltas_by_identity:
        return False
    key = _make_replica_ticket_event_key(event, text)
    if not _mark_replica_ticket_event_processed(key, now):
        return False
    changed = False
    for identity_id, item_deltas in deltas_by_identity.items():
        changed = apply_storage_bag_item_deltas(identity_id, item_deltas) or changed
    return changed


def _get_replica_run_records():
    run_state = _get_replica_run_state_dict()
    records = run_state.get("by_identity") if isinstance(run_state, dict) else {}
    return records if isinstance(records, dict) else {}


def _save_replica_run_records(records):
    run_state = _get_replica_run_state_dict()
    run_state["by_identity"] = records if isinstance(records, dict) else {}
    _save_replica_run_state_dict(run_state)


def _get_replica_room_gua_records():
    run_state = _get_replica_run_state_dict()
    room_gua = run_state.get("room_gua")
    return room_gua if isinstance(room_gua, dict) else {}


def _save_replica_room_gua_records(room_gua):
    run_state = _get_replica_run_state_dict()
    run_state["room_gua"] = room_gua if isinstance(room_gua, dict) else {}
    _save_replica_run_state_dict(run_state)


def _get_virtual_hall_auto_open_state():
    run_state = _get_replica_run_state_dict()
    auto_state = run_state.get("virtual_hall_auto_open")
    if not isinstance(auto_state, dict):
        auto_state = {}
    flows = auto_state.get("flows")
    if not isinstance(flows, dict):
        flows = {}
    auto_state["flows"] = flows
    return auto_state


def _save_virtual_hall_auto_open_state(auto_state):
    run_state = _get_replica_run_state_dict()
    run_state["virtual_hall_auto_open"] = auto_state if isinstance(auto_state, dict) else {"flows": {}}
    _save_replica_run_state_dict(run_state)


def _get_virtual_hall_auto_open_flows():
    auto_state = _get_virtual_hall_auto_open_state()
    flows = auto_state.get("flows")
    return flows if isinstance(flows, dict) else {}


def _save_virtual_hall_auto_open_flows(flows):
    auto_state = _get_virtual_hall_auto_open_state()
    auto_state["flows"] = flows if isinstance(flows, dict) else {}
    _save_virtual_hall_auto_open_state(auto_state)


def _schedule_virtual_hall_auto_audit(text):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        console_log(text, scope="global", limit=180)
        return
    _fire_and_forget(send_audit_log(text, scope="global", limit=180))


def _make_virtual_hall_auto_flow_id(chat_id, identity_id, now):
    return f"{int(chat_id or 0)}:{int(identity_id or 0)}:{int(float(now or 0) * 1000)}"


def _upsert_virtual_hall_auto_flow(flow):
    if not isinstance(flow, dict):
        return False
    flow_id = str(flow.get("flow_id") or "").strip()
    if not flow_id:
        return False
    flows = _get_virtual_hall_auto_open_flows()
    flows[flow_id] = flow
    _save_virtual_hall_auto_open_flows(flows)
    return True


def _normalize_virtual_hall_auto_phases(phases):
    if phases is None:
        return None
    if isinstance(phases, str):
        return {phases}
    return {str(phase or "") for phase in phases if str(phase or "")}


def _cleanup_virtual_hall_auto_open_flows(now=None):
    now = float(now or time.time())
    flows = _get_virtual_hall_auto_open_flows()
    changed = False
    audit_messages = []
    for flow_id, flow in list(flows.items()):
        if not isinstance(flow, dict):
            flows.pop(flow_id, None)
            changed = True
            continue
        expires_at = float(flow.get("expires_at") or 0)
        if expires_at > 0 and now >= expires_at:
            flows.pop(flow_id, None)
            changed = True
            continue
        pending = flow.get("kick_pending_usernames")
        if not isinstance(pending, dict):
            continue
        kick_results = flow.get("kick_results")
        if not isinstance(kick_results, dict):
            kick_results = {}
        flow_changed = False
        for username, item in list(pending.items()):
            sent_at = float((item or {}).get("sent_at") or 0) if isinstance(item, dict) else 0
            if sent_at <= 0 or now < sent_at + _VIRTUAL_HALL_AUTO_OPEN_KICK_TIMEOUT_SEC:
                continue
            normalized_username = _normalize_replica_username(username)
            pending.pop(username, None)
            attempts = int((item or {}).get("attempts") or 0) if isinstance(item, dict) else 0
            kick_results.pop(normalized_username, None)
            audit_messages.append(f"异常人员请离未确认：{normalized_username}")
            flow["last_kick_timeout_username"] = normalized_username
            flow["last_kick_timeout_attempts"] = attempts
            flow_changed = True
        if flow_changed:
            flow["kick_pending_usernames"] = pending
            flow["kick_results"] = kick_results
            flow["updated_at"] = now
            changed = True
            if str(flow.get("phase") or "") == "monitoring":
                _schedule_virtual_hall_auto_deferred_team_check(flow_id, _VIRTUAL_HALL_AUTO_KICK_TIMEOUT_RECHECK_DELAY_SEC)
    if changed:
        _save_virtual_hall_auto_open_flows(flows)
    for message in audit_messages:
        _schedule_virtual_hall_auto_audit(message)
    return flows


def _find_virtual_hall_auto_flow_by_room(room_id, replica_chat_id=0, phases=None, now=None):
    room_id = str(room_id or "").strip()
    if not room_id:
        return None
    target_phases = _normalize_virtual_hall_auto_phases(phases)
    flows = _cleanup_virtual_hall_auto_open_flows(now)
    matches = []
    for flow in flows.values():
        if not isinstance(flow, dict):
            continue
        if str(flow.get("room_id") or "").strip() != room_id:
            continue
        if int(replica_chat_id or 0) and int(flow.get("replica_chat_id") or 0) != int(replica_chat_id or 0):
            continue
        if target_phases is not None and str(flow.get("phase") or "") not in target_phases:
            continue
        matches.append(flow)
    if not matches:
        return None
    matches.sort(key=lambda item: float(item.get("updated_at") or item.get("opened_at") or 0), reverse=True)
    return matches[0]


def _find_virtual_hall_auto_opening_flow(reply_to_msg_id=0, send_as_id=0, leader_username="", now=None):
    now = float(now or time.time())
    leader_username = _normalize_replica_username(leader_username)
    flows = _cleanup_virtual_hall_auto_open_flows(now)
    opening_flows = [flow for flow in flows.values() if isinstance(flow, dict) and flow.get("phase") == "opening"]
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id > 0:
        for flow in opening_flows:
            if int(flow.get("open_command_msg_id") or 0) == reply_to_msg_id:
                return flow
    send_as_id = int(send_as_id or 0)
    if send_as_id > 0:
        matches = [flow for flow in opening_flows if int(flow.get("leader_identity_id") or 0) == send_as_id]
        if len(matches) == 1:
            return matches[0]
    if leader_username:
        matches = [flow for flow in opening_flows if _normalize_replica_username(flow.get("leader_username") or "") == leader_username]
        matches.sort(key=lambda item: float(item.get("open_requested_at") or 0), reverse=True)
        if matches:
            return matches[0]
    return None


def _has_active_virtual_hall_auto_flow(replica_chat_id, leader_identity_id, now=None):
    flows = _cleanup_virtual_hall_auto_open_flows(now)
    for flow in flows.values():
        if not isinstance(flow, dict):
            continue
        if str(flow.get("phase") or "") not in _VIRTUAL_HALL_AUTO_OPEN_ACTIVE_PHASES:
            continue
        if int(flow.get("replica_chat_id") or 0) != int(replica_chat_id or 0):
            continue
        if int(flow.get("leader_identity_id") or 0) != int(leader_identity_id or 0):
            continue
        return True
    return False


def _find_latest_virtual_hall_auto_flow(replica_chat_id=0, phases=None, now=None):
    target_phases = _normalize_virtual_hall_auto_phases(phases)
    flows = _cleanup_virtual_hall_auto_open_flows(now)
    matches = []
    for flow in flows.values():
        if not isinstance(flow, dict):
            continue
        if int(replica_chat_id or 0) and int(flow.get("replica_chat_id") or 0) != int(replica_chat_id or 0):
            continue
        if target_phases is not None and str(flow.get("phase") or "") not in target_phases:
            continue
        matches.append(flow)
    if not matches:
        return None
    matches.sort(key=lambda item: float(item.get("updated_at") or item.get("opened_at") or item.get("open_requested_at") or 0), reverse=True)
    return matches[0]


def _get_replica_room_gua_map(replica_kind):
    room_gua = _get_replica_room_gua_records()
    kind_map = room_gua.get(replica_kind)
    return kind_map if isinstance(kind_map, dict) else {}


def _get_replica_room_gua_record(replica_kind, room_id):
    return _get_replica_room_gua_map(replica_kind).get(str(room_id or "")) or {}


def _upsert_replica_room_gua_record(replica_kind, room_id, record):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    room_id = str(room_id or "").strip()
    if not room_id or not isinstance(record, dict):
        return False
    room_gua = _get_replica_room_gua_records()
    kind_map = room_gua.get(replica_kind)
    if not isinstance(kind_map, dict):
        kind_map = {}
    kind_map[room_id] = record
    room_gua[replica_kind] = kind_map
    _save_replica_room_gua_records(room_gua)
    return True


def _cleanup_replica_room_gua_records(now=None):
    now = float(now or time.time())
    room_gua = _get_replica_room_gua_records()
    changed = False
    for replica_kind, kind_map in list(room_gua.items()):
        if not isinstance(kind_map, dict):
            room_gua.pop(replica_kind, None)
            changed = True
            continue
        for room_id, record in list(kind_map.items()):
            expires_at = float((record or {}).get("expires_at") or 0) if isinstance(record, dict) else 0
            if expires_at > 0 and now >= expires_at:
                kind_map.pop(room_id, None)
                changed = True
        if len(kind_map) > _REPLICA_ROOM_GUA_MAX_PER_KIND:
            sorted_items = sorted(
                kind_map.items(),
                key=lambda item: float((item[1] or {}).get("updated_at") or 0) if isinstance(item[1], dict) else 0,
                reverse=True,
            )
            keep_ids = {room_id for room_id, _record in sorted_items[:_REPLICA_ROOM_GUA_MAX_PER_KIND]}
            for room_id in list(kind_map.keys()):
                if room_id not in keep_ids:
                    kind_map.pop(room_id, None)
                    changed = True
        if kind_map:
            room_gua[replica_kind] = kind_map
        else:
            room_gua.pop(replica_kind, None)
            changed = True
    if changed:
        _save_replica_room_gua_records(room_gua)
    return room_gua


def _normalize_replica_run_record(record):
    return record if isinstance(record, dict) else {}


def _get_replica_identity_record(records, identity_id):
    key = str(int(identity_id or 0))
    record = _normalize_replica_run_record(records.get(key))
    records[key] = record
    return record


def _extract_replica_usernames(text):
    usernames = []
    seen = set()
    for raw_username in _REPLICA_USERNAME_RE.findall(str(text or "")):
        username = _normalize_replica_username(raw_username)
        if username and username not in seen:
            seen.add(username)
            usernames.append(username)
    return usernames


def _get_replica_kind_by_join_command(command):
    normalized_command = str(command or "").strip()
    for kind, meta in _REPLICA_KIND_META.items():
        if normalized_command == meta["join_command"]:
            return kind
    return ""


def _get_replica_kind_by_dispatch_command(command):
    normalized_command = str(command or "").strip()
    for kind, meta in _REPLICA_KIND_META.items():
        if normalized_command == meta["dispatch_command"]:
            return kind
    return ""


def _get_replica_kind_by_enter_command(command):
    normalized_command = str(command or "").strip()
    for kind, meta in _REPLICA_KIND_META.items():
        if normalized_command == meta["enter_command"]:
            return kind
    return ""


def _parse_replica_join_command(text):
    raw_text = str(text or "").strip()
    for kind, meta in _REPLICA_KIND_META.items():
        match = re.match(rf"^{re.escape(meta['join_command'])}\s*(\d+)\s*$", raw_text)
        if match:
            return str(match.group(1)), kind
    return "", ""


def _parse_replica_join_room_id(text):
    room_id, _kind = _parse_replica_join_command(text)
    return room_id


def _get_replica_identity_ids_by_username():
    identity_ids_by_username = {}
    for identity_id in _get_replica_candidate_identity_ids(require_username=True):
        username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        identity_ids_by_username[username] = identity_id
    return identity_ids_by_username


def _map_replica_usernames_to_identity_ids(usernames):
    identity_ids_by_username = _get_replica_identity_ids_by_username()
    identity_ids = []
    seen = set()
    for username in usernames or []:
        identity_id = identity_ids_by_username.get(_normalize_replica_username(username))
        if identity_id and identity_id not in seen:
            seen.add(identity_id)
            identity_ids.append(identity_id)
    return identity_ids


def _normalize_replica_username_list(usernames):
    normalized = []
    seen = set()
    for username in usernames or []:
        normalized_username = _normalize_replica_username(username)
        if normalized_username and normalized_username not in seen:
            seen.add(normalized_username)
            normalized.append(normalized_username)
    return normalized


def _extract_replica_team_section(text):
    raw_text = str(text or "")
    if "当前队伍" not in raw_text:
        return ""
    team_section = raw_text.split("当前队伍", 1)[1]
    for marker in ("【卦象词条】", "【", "当前契合", "断术", "行运", "爻意"):
        if marker in team_section:
            team_section = team_section.split(marker, 1)[0]
    return team_section


def _extract_replica_team_usernames(text):
    team_section = _extract_replica_team_section(text)
    usernames = []
    for line in team_section.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        username_match = _REPLICA_USERNAME_RE.search(line)
        if username_match:
            usernames.append(username_match.group(0))
    return _normalize_replica_username_list(usernames)


def _extract_virtual_hall_gua_section(text):
    raw_text = str(text or "")
    marker_index = raw_text.find("【卦象词条】")
    if marker_index < 0:
        return ""
    return raw_text[marker_index:].strip()


def _parse_virtual_hall_gua_requirement_line(line):
    raw_line = str(line or "").strip()
    if raw_line.startswith("-"):
        raw_line = raw_line[1:].strip()
    match = re.match(r"^(阵骨|主锋|引灵|旁合)\s*[:：]\s*(.+)$", raw_line)
    if not match:
        return None
    role = match.group(1)
    body = match.group(2).strip()
    if role == "阵骨":
        element_match = re.search(r"([金木水火土])\s*必带", body)
        if not element_match:
            return None
        return {"role": role, "element": element_match.group(1), "count": 1, "required": True, "raw_line": str(line or "").strip()}
    if role == "主锋":
        element_match = re.search(r"([金木水火土])\s*[xX×]\s*(\d+)", body)
        if not element_match:
            element_match = re.search(r"([金木水火土])", body)
        if not element_match:
            return None
        count = int(element_match.group(2)) if element_match.lastindex and element_match.lastindex >= 2 and element_match.group(2) else 1
        return {"role": role, "element": element_match.group(1), "count": count, "required": True, "raw_line": str(line or "").strip()}
    if role == "引灵":
        element_match = re.search(r"([金木水火土])\s*位", body)
        if not element_match:
            return None
        fallback_match = re.search(r"可由\s*([金木水火土])\s*借生代行", body)
        item = {"role": role, "element": element_match.group(1), "count": 1, "required": True, "raw_line": str(line or "").strip()}
        if fallback_match:
            item["fallback_element"] = fallback_match.group(1)
            item["fallback_type"] = "借生"
        return item
    if role == "旁合":
        element_match = re.search(r"([金木水火土])\s*位更佳", body)
        if not element_match:
            return None
        fallback_match = re.search(r"若用\s*([金木水火土])\s*强顶只算偏配", body)
        item = {"role": role, "element": element_match.group(1), "count": 1, "required": False, "raw_line": str(line or "").strip()}
        if fallback_match:
            item["fallback_element"] = fallback_match.group(1)
            item["fallback_type"] = "偏配"
        return item
    return None


def _parse_virtual_hall_gua_requirements(gua_text):
    lines = [line.strip() for line in str(gua_text or "").splitlines() if line.strip()]
    if not lines or not lines[0].startswith("【卦象词条】"):
        return None
    title = lines[0].replace("【卦象词条】", "", 1).strip()
    requirements = []
    route = ""
    meaning = ""
    for line in lines[1:]:
        requirement = _parse_virtual_hall_gua_requirement_line(line)
        if requirement:
            requirements.append(requirement)
            continue
        clean_line = line[1:].strip() if line.startswith("-") else line
        if clean_line.startswith("行运：") or clean_line.startswith("行运:"):
            route = re.sub(r"^行运\s*[:：]\s*", "", clean_line).strip()
        elif clean_line.startswith("爻意：") or clean_line.startswith("爻意:"):
            meaning = re.sub(r"^爻意\s*[:：]\s*", "", clean_line).strip()
    roles = {item.get("role") for item in requirements}
    if not {"阵骨", "主锋", "引灵", "旁合"}.issubset(roles):
        return None
    return {"gua_title": title, "raw_gua_text": str(gua_text or "").strip(), "requirements": requirements, "route": route, "meaning": meaning}


def _mark_virtual_hall_gua_from_opened_text(text, now, room_id, leader_username="", msg_id=0):
    gua_section = _extract_virtual_hall_gua_section(text)
    parsed = _parse_virtual_hall_gua_requirements(gua_section)
    if not parsed:
        return False
    record = {
        "room_id": str(room_id or "").strip(),
        "replica_kind": _REPLICA_KIND_VIRTUAL_HALL,
        "source": "opened_message",
        "leader_username": _normalize_replica_username(leader_username),
        "source_msg_id": int(msg_id or 0),
        "opened_at": float(now or 0),
        "updated_at": float(now or 0),
        "expires_at": float(now or 0) + _REPLICA_ROOM_GUA_TTL_SEC,
        **parsed,
    }
    return _upsert_replica_room_gua_record(_REPLICA_KIND_VIRTUAL_HALL, record["room_id"], record)


def _normalize_replica_identity_ids(identity_ids):
    normalized = []
    seen = set()
    for identity_id in identity_ids or []:
        try:
            normalized_id = int(identity_id or 0)
        except (TypeError, ValueError):
            continue
        if normalized_id > 0 and normalized_id not in seen:
            seen.add(normalized_id)
            normalized.append(normalized_id)
    return normalized


def _strip_html_code_tags(text):
    return re.sub(r"</?code>", "", unescape(str(text or "")), flags=re.I)


def _get_root_elements(root_attrs):
    attrs_text = str(root_attrs or "")
    elements = set()
    for element, aliases in _VIRTUAL_HALL_ELEMENT_ALIASES.items():
        if any(alias in attrs_text for alias in aliases):
            elements.add(element)
    return elements


def _clean_replica_query_field(text):
    return str(text or "").replace("【", "").replace("】", "").strip()


def _parse_replica_query_root_attrs_field(field):
    raw_field = _clean_replica_query_field(field)
    gold_dps = raw_field.endswith("DPS")
    root_attrs = raw_field[:-3].strip() if gold_dps else raw_field
    if not _REPLICA_QUERY_ROOT_RE.fullmatch(root_attrs):
        return "", False
    return root_attrs, gold_dps and any(attr in root_attrs for attr in ("金", "雷"))


def _is_virtual_hall_status_available(status):
    return str(status or "").strip() == "可"


def _parse_replica_query_prefix(prefix):
    prefix = _strip_html_code_tags(prefix).strip()
    username_match = _REPLICA_USERNAME_RE.search(prefix)
    if not username_match:
        return None
    username = username_match.group(0)
    rest = prefix[username_match.end():].strip().strip("|").strip()
    fields = [_clean_replica_query_field(field) for field in re.split(r"\s*\|\s*", rest) if _clean_replica_query_field(field)]
    root_attrs = ""
    gold_dps = False
    professions = []
    for field in fields:
        parsed_root_attrs, parsed_gold_dps = _parse_replica_query_root_attrs_field(field)
        if not root_attrs and parsed_root_attrs:
            root_attrs = parsed_root_attrs
            gold_dps = parsed_gold_dps
            continue
        for profession in _REPLICA_QUERY_PROFESSION_NAMES:
            if profession in field and profession not in professions:
                professions.append(profession)
    return {"username": username, "username_key": _normalize_replica_username(username), "root_attrs": root_attrs, "gold_dps": gold_dps, "professions": professions}


def _parse_replica_query_reply_line(line):
    raw_line = _strip_html_code_tags(line).strip()
    if not raw_line or "@" not in raw_line:
        return None
    status_match = _REPLICA_QUERY_STATUS_RE.search(raw_line)
    if status_match:
        parsed_prefix = _parse_replica_query_prefix(raw_line[:status_match.start()])
        if not parsed_prefix:
            return None
        suffix = raw_line[status_match.end():].strip().strip("|").strip()
        suffix_fields = [_clean_replica_query_field(field) for field in re.split(r"\s*\|\s*", suffix) if _clean_replica_query_field(field)]
        for field in suffix_fields:
            parsed_root_attrs, parsed_gold_dps = _parse_replica_query_root_attrs_field(field)
            if not parsed_prefix.get("root_attrs") and parsed_root_attrs:
                parsed_prefix["root_attrs"] = parsed_root_attrs
                parsed_prefix["gold_dps"] = parsed_gold_dps
                continue
            for profession in _REPLICA_QUERY_PROFESSION_NAMES:
                if profession in field and profession not in parsed_prefix["professions"]:
                    parsed_prefix["professions"].append(profession)
        virtual_status = status_match.group("virtual").strip()
        parsed_prefix.update({
            "root_elements": _get_root_elements(parsed_prefix.get("root_attrs")),
            "virtual_status": virtual_status,
            "statuses": {
                _REPLICA_KIND_VIRTUAL_HALL: virtual_status,
                _REPLICA_KIND_ZHUIMO: status_match.group("zhuimo").strip(),
                _REPLICA_KIND_HUANGLONG: status_match.group("huanglong").strip(),
                **({_REPLICA_KIND_CANGKUN: status_match.group("cangkun").strip()} if status_match.group("cangkun") else {}),
            },
            "available": _is_virtual_hall_status_available(virtual_status),
            "raw_line": raw_line,
        })
        return parsed_prefix

    username_match = _REPLICA_USERNAME_RE.search(raw_line)
    if not username_match:
        return None
    username = username_match.group(0)
    rest = raw_line[username_match.end():].strip().strip("|").strip()
    fields = [_clean_replica_query_field(field) for field in re.split(r"\s*\|\s*", rest) if _clean_replica_query_field(field)]
    if not fields:
        return None
    status = fields[-1]
    root_attrs = ""
    gold_dps = False
    professions = []
    for field in fields[:-1]:
        parsed_root_attrs, parsed_gold_dps = _parse_replica_query_root_attrs_field(field)
        if not root_attrs and parsed_root_attrs:
            root_attrs = parsed_root_attrs
            gold_dps = parsed_gold_dps
            continue
        for profession in _REPLICA_QUERY_PROFESSION_NAMES:
            if profession in field and profession not in professions:
                professions.append(profession)
    return {
        "username": username,
        "username_key": _normalize_replica_username(username),
        "root_attrs": root_attrs,
        "root_elements": _get_root_elements(root_attrs),
        "gold_dps": gold_dps,
        "professions": professions,
        "virtual_status": status,
        "statuses": {_REPLICA_KIND_VIRTUAL_HALL: status},
        "available": status == "可参加",
        "raw_line": raw_line,
    }


def _enrich_replica_query_candidate_from_identity(candidate):
    candidate = dict(candidate or {})
    identity_id = _get_identity_id_by_replica_username(candidate.get("username") or "", include_disabled=False)
    if identity_id <= 0:
        return candidate
    profile = get_send_as_profile(identity_id)
    root_attrs = str(candidate.get("root_attrs") or "").strip()
    profile_root_attrs = str(profile.get("spiritual_root_attrs") or "").strip()
    if (not root_attrs or root_attrs == "未获取") and profile_root_attrs:
        root_attrs = profile_root_attrs
        candidate["root_attrs"] = root_attrs
    candidate["root_elements"] = _get_root_elements(root_attrs)
    if not candidate.get("professions"):
        professions = _get_replica_profile_professions(identity_id)
        if professions:
            candidate["professions"] = professions
    if get_replica_gold_dps_enabled(identity_id) and any(attr in root_attrs for attr in ("金", "雷")):
        candidate["gold_dps"] = True
    return candidate


def _parse_replica_query_reply_text(text):
    candidates = []
    seen = set()
    for line in str(text or "").splitlines():
        candidate = _parse_replica_query_reply_line(line)
        if not candidate:
            continue
        candidate = _enrich_replica_query_candidate_from_identity(candidate)
        username_key = candidate.get("username_key")
        if username_key in seen:
            continue
        seen.add(username_key)
        candidates.append(candidate)
    return candidates


def _parse_log_ts(ts_text):
    raw_ts = str(ts_text or "").replace(" UTC+8", "").strip()
    try:
        return datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0


def _iter_replica_message_log_entries_between(start_ts, end_ts, chat_id=0):
    start_dt = datetime.fromtimestamp(float(start_ts or 0), TZ_LOCAL) - timedelta(days=1)
    end_dt = datetime.fromtimestamp(float(end_ts or time.time()), TZ_LOCAL) + timedelta(days=1)
    target_chat_id = int(chat_id or 0)
    seen_paths = set()
    current = start_dt.date()
    while current <= end_dt.date():
        log_file = f"{MESSAGES_DIR}/replica-{current.strftime('%Y-%m-%d')}.log"
        current += timedelta(days=1)
        if log_file in seen_paths:
            continue
        seen_paths.add(log_file)
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if target_chat_id and int(entry.get("chat_id") or 0) != target_chat_id:
                        continue
                    entry_ts = _parse_log_ts(entry.get("ts"))
                    if entry_ts and (entry_ts < start_ts or entry_ts > end_ts):
                        continue
                    yield entry
        except FileNotFoundError:
            continue
        except Exception:
            print(traceback.format_exc())


def _dedupe_replica_query_candidates(candidates):
    deduped = {}
    order = []
    for candidate in candidates or []:
        username_key = candidate.get("username_key") or _normalize_replica_username(candidate.get("username") or "")
        if not username_key:
            continue
        if username_key not in deduped:
            order.append(username_key)
        deduped[username_key] = candidate
    return [deduped[username_key] for username_key in order]


def _find_replica_query_log_candidates(query_msg_id, query_sent_at, wait_sec=_VIRTUAL_HALL_MATCH_QUERY_WAIT_SEC, chat_id=0):
    start_ts = float(query_sent_at or 0) - 2
    end_ts = float(query_sent_at or time.time()) + float(wait_sec or 0) + _VIRTUAL_HALL_MATCH_LOG_WINDOW_SEC
    candidates = []
    for entry in _iter_replica_message_log_entries_between(start_ts, end_ts, chat_id=chat_id):
        text = str(entry.get("text") or "")
        parsed = _parse_replica_query_reply_text(text)
        if parsed:
            candidates.extend(parsed)
    return _dedupe_replica_query_candidates(candidates)


async def _wait_replica_query_log_candidates(query_msg_id, query_sent_at, timeout_sec, chat_id=0):
    deadline = time.time() + max(0.0, float(timeout_sec or 0))
    poll_sec = max(0.1, float(_VIRTUAL_HALL_MATCH_QUERY_POLL_SEC))
    while True:
        elapsed = max(0.0, time.time() - float(query_sent_at or 0))
        candidates = _find_replica_query_log_candidates(
            query_msg_id,
            query_sent_at,
            wait_sec=elapsed,
            chat_id=chat_id,
        )
        if candidates:
            return candidates
        remaining = deadline - time.time()
        if remaining <= 0:
            return []
        await asyncio.sleep(min(poll_sec, remaining))


def _expand_virtual_hall_gua_slots(requirements):
    slots = []
    for requirement in sorted(requirements or [], key=lambda item: _VIRTUAL_HALL_GUA_ROLE_ORDER.get((item or {}).get("role"), 99)):
        if not isinstance(requirement, dict):
            continue
        count = max(1, int(requirement.get("count") or 1))
        for index in range(count):
            slot = dict(requirement)
            slot["slot_index"] = index + 1
            slots.append(slot)
    return slots


def _candidate_slot_match(candidate, slot):
    elements = candidate.get("root_elements") or set()
    element = slot.get("element")
    if element and element in elements:
        return {"quality": "exact", "score": 100, "note": ""}
    fallback_element = slot.get("fallback_element")
    if fallback_element and fallback_element in elements:
        if slot.get("gold_fallback"):
            return {"quality": "fallback", "score": 90, "note": "旁合带金"}
        fallback_type = slot.get("fallback_type") or "偏配"
        score = 90 if fallback_element == "金" else (70 if slot.get("role") == "引灵" else 45)
        return {"quality": "fallback", "score": score, "note": f"{slot.get('role')}{element}用{fallback_element}{fallback_type}"}
    return None


def _slot_has_virtual_hall_gold(slot, include_fallback=True):
    if (slot or {}).get("element") == "金":
        return True
    return include_fallback and (slot or {}).get("fallback_element") == "金"


def _ensure_virtual_hall_gold_fallback(slots):
    slots = list(slots or [])
    if any(_slot_has_virtual_hall_gold(slot, include_fallback=True) for slot in slots):
        return slots
    for slot in slots:
        if slot.get("role") == "旁合":
            slot["fallback_element"] = "金"
            slot["fallback_type"] = "带金"
            slot["gold_fallback"] = True
            return slots
    slots.append({"role": "旁合", "element": "金", "count": 1, "required": False, "slot_index": 1, "gold_fallback": True})
    return slots


def _candidate_has_virtual_hall_gold(candidate):
    return "金" in (candidate.get("root_elements") or set())


def _candidate_has_virtual_hall_gold_dps(candidate):
    return _candidate_has_virtual_hall_gold(candidate) and bool((candidate or {}).get("gold_dps"))


def _assignments_have_virtual_hall_gold(assignments):
    return any(_candidate_has_virtual_hall_gold(assignment.get("candidate") or {}) for assignment in assignments or [])


def _assignments_have_virtual_hall_gold_dps(assignments):
    return any(_candidate_has_virtual_hall_gold_dps(assignment.get("candidate") or {}) for assignment in assignments or [])


def _has_available_virtual_hall_gold_dps(candidates):
    return any(candidate.get("available") and _candidate_has_virtual_hall_gold_dps(candidate) for candidate in candidates or [])


def _has_available_virtual_hall_gold_candidate(candidates):
    return any(candidate.get("available") and _candidate_has_virtual_hall_gold(candidate) for candidate in candidates or [])


def _has_virtual_hall_gold_fallback_slots(slots):
    return any(slot.get("fallback_element") == "金" for slot in slots or [])


def _assignments_use_virtual_hall_gold_fallback(assignments):
    return any(
        (assignment.get("slot") or {}).get("fallback_element") == "金"
        and _candidate_has_virtual_hall_gold(assignment.get("candidate") or {})
        for assignment in assignments or []
    )


def _format_virtual_hall_slot_name(slot):
    role = slot.get("role") or "未知"
    element = slot.get("element") or ""
    if role == "主锋":
        return f"{role}{element}{int(slot.get('slot_index') or 1)}"
    return f"{role}{element}"


def _virtual_hall_assignment_key(assignments):
    parts = []
    for assignment in assignments:
        slot = assignment.get("slot") or {}
        role = slot.get("role") or ""
        element = slot.get("element") or ""
        slot_index = int(slot.get("slot_index") or 1)
        username = _normalize_replica_username((assignment.get("candidate") or {}).get("username") or "")
        parts.append((role, element, slot_index, username))
    normalized_parts = []
    grouped = {}
    for role, element, slot_index, username in parts:
        key = (role, element)
        grouped.setdefault(key, []).append(username)
    for key in sorted(grouped):
        normalized_parts.append((key, tuple(sorted(grouped[key]))))
    return tuple(normalized_parts)


def _score_virtual_hall_assignments(slots, assignments):
    assigned_slot_ids = {id(assignment.get("slot")) for assignment in assignments}
    score = 0
    notes = []
    missing = []
    for assignment in assignments:
        slot = assignment.get("slot") or {}
        match = assignment.get("match") or {}
        score += int(match.get("score") or 0)
        if match.get("note"):
            notes.append(match["note"])
    for slot in slots:
        if id(slot) not in assigned_slot_ids:
            name = _format_virtual_hall_slot_name(slot)
            missing.append(name)
            score -= 300 if slot.get("required") else 80
    exact_count = sum(1 for assignment in assignments if (assignment.get("match") or {}).get("quality") == "exact")
    fallback_count = sum(1 for assignment in assignments if (assignment.get("match") or {}).get("quality") == "fallback")
    gold_fallback_count = sum(
        1
        for assignment in assignments
        if (assignment.get("slot") or {}).get("fallback_element") == "金"
        and _candidate_has_virtual_hall_gold(assignment.get("candidate") or {})
    )
    return score + exact_count * 10 - fallback_count * 5 + gold_fallback_count * 20, missing, notes


def _has_virtual_hall_core_slots_filled(slots, assignments):
    assigned_slot_ids = {id(assignment.get("slot")) for assignment in assignments}
    core_slots = [slot for slot in slots if slot.get("role") in {"阵骨", "主锋"}]
    return bool(core_slots) and all(id(slot) in assigned_slot_ids for slot in core_slots)


def _virtual_hall_recommendation_command_limit(leader_username=""):
    return 4 if _normalize_replica_username(leader_username) else 5


def _virtual_hall_recommendation_command_usernames(assignments, leader_username="", limit=None):
    leader_username = _normalize_replica_username(leader_username)
    usernames = []
    dps_usernames = []
    seen = set()
    for assignment in assignments or []:
        candidate = assignment.get("candidate") or {}
        username = _normalize_replica_username(candidate.get("username") or "")
        if not username or username == leader_username or username in seen:
            continue
        seen.add(username)
        usernames.append(username)
        if _candidate_has_virtual_hall_gold_dps(candidate):
            dps_usernames.append(username)
    if limit is None or len(usernames) <= int(limit or 0):
        return usernames
    limit = max(0, int(limit or 0))
    if limit <= 0:
        return []
    selected = set(dps_usernames[:limit])
    for username in usernames:
        if len(selected) >= limit:
            break
        selected.add(username)
    return [username for username in usernames if username in selected]


def _virtual_hall_recommendation_command_key(recommendation, leader_username=""):
    usernames = _virtual_hall_recommendation_command_usernames(
        recommendation.get("assignments") or [],
        leader_username,
        limit=_virtual_hall_recommendation_command_limit(leader_username),
    )
    return tuple(sorted(usernames))


def _virtual_hall_recommendation_dps_usernames(recommendation):
    usernames = []
    seen = set()
    for assignment in (recommendation or {}).get("assignments") or []:
        candidate = assignment.get("candidate") or {}
        if not _candidate_has_virtual_hall_gold_dps(candidate):
            continue
        username = _normalize_replica_username(candidate.get("username") or "")
        if username and username not in seen:
            seen.add(username)
            usernames.append(username)
    return usernames


def _normalize_xutian_oracle_title(gua_title):
    return re.sub(r"\s+", " ", str(gua_title or "").strip())


def _xutian_oracle_prefix(gua_title):
    return _normalize_xutian_oracle_title(gua_title).split(" · ", 1)[0].strip()


def _ordered_unique_text(values):
    seen = set()
    result = []
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _format_xutian_oracle_examples(examples):
    return [f"{route} / {strategy} ({source})" for route, strategy, source in examples]


def _get_xutian_oracle_same_prefix_advice(gua_title):
    prefix = _xutian_oracle_prefix(gua_title)
    if not prefix:
        return {}
    examples = []
    for title, item in _XUTIAN_ORACLE_EXPLICIT.items():
        if _xutian_oracle_prefix(title) == prefix:
            route, strategy, source = item
            examples.append((route, strategy, source))
    for title, items in _XUTIAN_ORACLE_SUCCESS.items():
        if _xutian_oracle_prefix(title) == prefix:
            examples.extend(items)
    negative_examples = []
    for title, items in _XUTIAN_ORACLE_FAILURE.items():
        if _xutian_oracle_prefix(title) == prefix:
            negative_examples.extend(items)
    routes = _ordered_unique_text(route for route, _strategy, _source in examples)
    strategies = _ordered_unique_text(strategy for _route, strategy, _source in examples)
    if len(routes) != 1 or len(strategies) != 1:
        return {}
    advice = {
        "route": routes[0],
        "strategy": strategies[0],
        "confidence": "同卦系推断",
        "basis": "同卦系正向样本唯一",
        "positive_examples": _format_xutian_oracle_examples(examples),
    }
    if negative_examples:
        advice["negative_examples"] = _format_xutian_oracle_examples(negative_examples)
    return advice


def _get_xutian_oracle_route_advice(gua_title):
    gua_title = _normalize_xutian_oracle_title(gua_title)
    if not gua_title:
        return {}
    if gua_title in _XUTIAN_ORACLE_EXPLICIT:
        route, strategy, source = _XUTIAN_ORACLE_EXPLICIT[gua_title]
        return {"route": route, "strategy": strategy, "confidence": "明示", "basis": f"历史明示 {source}"}
    if gua_title in _XUTIAN_ORACLE_SUCCESS:
        examples = _XUTIAN_ORACLE_SUCCESS[gua_title]
        routes = _ordered_unique_text(route for route, _strategy, _source in examples)
        strategies = _ordered_unique_text(strategy for _route, strategy, _source in examples)
        return {
            "route": "/".join(routes),
            "strategy": "/".join(strategies),
            "confidence": "实测顺合",
            "basis": "历史顺合样本",
            "positive_examples": _format_xutian_oracle_examples(examples),
        }
    if gua_title in _XUTIAN_ORACLE_FAILURE:
        return {
            "confidence": "反例",
            "basis": "仅有历史反例，暂不推荐具体路线",
            "negative_examples": _format_xutian_oracle_examples(_XUTIAN_ORACLE_FAILURE[gua_title]),
        }
    return _get_xutian_oracle_same_prefix_advice(gua_title)


def _xutian_oracle_route_commands(route_text):
    commands = []
    for route in re.split(r"[/／、,，\s]+", str(route_text or "")):
        if "冰" in route and ".选择道路 冰" not in commands:
            commands.append(".选择道路 冰")
        if "火" in route and ".选择道路 火" not in commands:
            commands.append(".选择道路 火")
    return commands


def _xutian_oracle_strategy_commands(strategy_text):
    commands = []
    for strategy in re.split(r"[/／、,，\s]+", str(strategy_text or "")):
        if "稳" in strategy and ".阵策 稳" not in commands:
            commands.append(".阵策 稳")
        if "压" in strategy and ".阵策 压" not in commands:
            commands.append(".阵策 压")
        if "势" in strategy and ".阵策 势" not in commands:
            commands.append(".阵策 势")
    return commands


def _format_xutian_oracle_advice_meta(advice):
    confidence = str((advice or {}).get("confidence") or "").strip()
    positive_count = len((advice or {}).get("positive_examples") or [])
    negative_count = len((advice or {}).get("negative_examples") or [])
    sample_parts = []
    if positive_count:
        sample_parts.append(f"正{positive_count}")
    if negative_count:
        sample_parts.append(f"反{negative_count}")
    return "，".join(part for part in (confidence, "，".join(sample_parts)) if part)


def _format_xutian_oracle_manual_command(command, *, html=False):
    return mono(command) if html else command


def _format_xutian_oracle_followup_section(*, html=False):
    return "\n".join([
        "后续：" + " / ".join([
            _format_xutian_oracle_manual_command(".争鼎 夺鼎", html=html),
            _format_xutian_oracle_manual_command(".后殿抉择 冲关", html=html),
        ]),
        "保守：" + " / ".join([
            _format_xutian_oracle_manual_command(".争鼎 求稳", html=html),
            _format_xutian_oracle_manual_command(".后殿抉择 收手", html=html),
        ]),
    ])


def _format_xutian_oracle_route_advice_section(gua_record, *, html=False):
    advice = _get_xutian_oracle_route_advice((gua_record or {}).get("gua_title") or "")
    if not advice:
        return ""
    route = str(advice.get("route") or "").strip()
    strategy = str(advice.get("strategy") or "").strip()
    basis = str(advice.get("basis") or "").strip()
    lines = []
    if route or strategy:
        route_text = " / ".join(part for part in (route, strategy) if part)
        meta_text = _format_xutian_oracle_advice_meta(advice)
        lines.append(f"路策：{route_text}" + (f"（{meta_text}）" if meta_text else ""))
        commands = _xutian_oracle_route_commands(route) + _xutian_oracle_strategy_commands(strategy)
        if commands:
            lines.extend(_format_xutian_oracle_manual_command(command, html=html) for command in commands)
        lines.append(_format_xutian_oracle_followup_section(html=html))
    else:
        meta_text = _format_xutian_oracle_advice_meta(advice)
        lines.append(f"路策：{basis or '暂无可用路线'}" + (f"（{meta_text}）" if meta_text else ""))
        negative_examples = advice.get("negative_examples") or []
        if negative_examples:
            lines.append("历史反例：" + "；".join(str(item) for item in negative_examples[:2]))
    return "\n".join(lines)


def _get_latest_replica_room_gua_record(replica_kind, now=None):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    room_gua = _cleanup_replica_room_gua_records(now)
    kind_map = room_gua.get(replica_kind)
    if not isinstance(kind_map, dict):
        return {}
    records = [record for record in kind_map.values() if isinstance(record, dict)]
    if not records:
        return {}
    records.sort(key=lambda item: float(item.get("updated_at") or item.get("opened_at") or 0), reverse=True)
    return records[0]


def _build_virtual_hall_recommendations(gua_record, candidates, limit=3):
    slots = _ensure_virtual_hall_gold_fallback(_expand_virtual_hall_gua_slots(gua_record.get("requirements") or []))
    has_exact_gold_slot = any(slot.get("element") == "金" for slot in slots)
    require_gold_fallback = not has_exact_gold_slot and _has_virtual_hall_gold_fallback_slots(slots)
    leader_username = _normalize_replica_username(gua_record.get("leader_username") or "")
    candidates = [
        candidate
        for candidate in candidates or []
        if candidate.get("root_elements") and (candidate.get("available") or candidate.get("username_key") == leader_username)
    ]
    has_available_gold_dps = _has_available_virtual_hall_gold_dps(candidates)
    if not has_available_gold_dps:
        return []
    slots_for_search = sorted(
        slots,
        key=lambda slot: (
            _VIRTUAL_HALL_GUA_ROLE_ORDER.get(slot.get("role"), 99),
            sum(1 for candidate in candidates if _candidate_slot_match(candidate, slot)),
        ),
    )
    results = []
    seen_keys = set()

    def search(index, used_usernames, assignments):
        if index >= len(slots_for_search):
            ordered_assignments = sorted(assignments, key=lambda item: slots.index(item["slot"]))
            command_usernames = _virtual_hall_recommendation_command_usernames(
                ordered_assignments,
                leader_username,
                limit=None,
            )
            if len(command_usernames) > _virtual_hall_recommendation_command_limit(leader_username):
                return
            if has_available_gold_dps and not _assignments_have_virtual_hall_gold_dps(ordered_assignments):
                return
            gold_fallback_used = _assignments_use_virtual_hall_gold_fallback(ordered_assignments)
            if require_gold_fallback and not gold_fallback_used:
                return
            key = _virtual_hall_assignment_key(ordered_assignments)
            if key in seen_keys:
                return
            seen_keys.add(key)
            score, missing, notes = _score_virtual_hall_assignments(slots, ordered_assignments)
            core_filled = _has_virtual_hall_core_slots_filled(slots, ordered_assignments)
            results.append({"assignments": ordered_assignments, "score": score, "missing": missing, "notes": notes, "core_filled": core_filled, "gold_fallback_used": gold_fallback_used})
            return
        slot = slots_for_search[index]
        matches = []
        for candidate in candidates:
            username_key = candidate.get("username_key")
            if username_key in used_usernames:
                continue
            match = _candidate_slot_match(candidate, slot)
            if match:
                matches.append((candidate, match))
        matches.sort(key=lambda item: (
            not ((slot.get("element") == "金" or slot.get("fallback_element") == "金") and _candidate_has_virtual_hall_gold_dps(item[0])),
            not ((slot.get("element") == "金" or slot.get("fallback_element") == "金") and _candidate_has_virtual_hall_gold(item[0])),
            item[1].get("quality") != "exact",
            not _candidate_has_virtual_hall_gold_dps(item[0]),
            not _candidate_has_virtual_hall_gold(item[0]),
            -int(item[1].get("score") or 0),
            item[0].get("username_key") or "",
        ))
        for candidate, match in matches[:12]:
            used_usernames.add(candidate.get("username_key"))
            assignments.append({"slot": slot, "candidate": candidate, "match": match})
            search(index + 1, used_usernames, assignments)
            assignments.pop()
            used_usernames.discard(candidate.get("username_key"))
        search(index + 1, used_usernames, assignments)

    search(0, set(), [])
    results.sort(key=lambda item: (not item.get("core_filled"), -item["score"], len(item.get("missing") or []), len(item.get("notes") or [])))
    deduped_results = []
    seen_command_keys = set()
    selected_command_sets = []
    for result in results:
        command_key = _virtual_hall_recommendation_command_key(result, leader_username=leader_username)
        command_set = set(command_key)
        if command_key in seen_command_keys:
            continue
        if any(command_set.issubset(selected_set) for selected_set in selected_command_sets):
            continue
        seen_command_keys.add(command_key)
        selected_command_sets.append(command_set)
        deduped_results.append(result)
        if len(deduped_results) >= int(limit or 0):
            break
    return deduped_results


def _format_virtual_hall_recommendation_line(room_id, recommendation, leader_username=""):
    assignments = recommendation.get("assignments") or []
    usernames = _virtual_hall_recommendation_command_usernames(
        assignments,
        leader_username,
        limit=_virtual_hall_recommendation_command_limit(leader_username),
    )
    command = f".虚天殿 {room_id}" + (" " + " ".join(usernames) if usernames else "")
    missing = recommendation.get("missing") or []
    notes = recommendation.get("notes") or []
    if not missing and not notes:
        prefix = "全匹配"
    elif missing and notes:
        prefix = "未全匹配（缺" + "、".join(missing) + "；" + "、".join(notes) + "）"
    elif missing:
        prefix = "未全匹配（缺" + "、".join(missing) + "）"
    else:
        prefix = "偏配（" + "、".join(notes) + "）"
    dps_usernames = _virtual_hall_recommendation_dps_usernames(recommendation)
    dps_text = "｜DPS：" + " ".join(mono(username) for username in dps_usernames) if dps_usernames else ""
    return f"{prefix} ： {mono(command)}{dps_text}"


def _format_virtual_hall_lightweight_recommendation_line(recommendation, leader_username="", *, html=False):
    usernames = _virtual_hall_recommendation_command_usernames(
        recommendation.get("assignments") or [],
        leader_username,
        limit=_virtual_hall_recommendation_command_limit(leader_username),
    )
    command = ".加入副本" + (" " + " ".join(usernames) if usernames else " @用户名 @用户名")
    missing = recommendation.get("missing") or []
    notes = recommendation.get("notes") or []
    if not missing and not notes:
        prefix = "全匹配"
    elif missing and notes:
        prefix = "未全匹配（缺" + "、".join(missing) + "；" + "、".join(notes) + "）"
    elif missing:
        prefix = "未全匹配（缺" + "、".join(missing) + "）"
    else:
        prefix = "偏配（" + "、".join(notes) + "）"
    dps_usernames = _virtual_hall_recommendation_dps_usernames(recommendation)
    dps_text = "｜DPS：" + " ".join(mono(username) if html else username for username in dps_usernames) if dps_usernames else ""
    return f"{prefix}：{mono(command) if html else command}{dps_text}"


def _format_virtual_hall_recommendations(room_id, gua_record, recommendations, candidates, *, lightweight=False, html=False):
    title = gua_record.get("gua_title") or "未知卦象"
    leader_username = _normalize_replica_username(gua_record.get("leader_username") or "")
    available_count = sum(1 for candidate in candidates if candidate.get("available"))
    known_root_count = sum(1 for candidate in candidates if candidate.get("available") and candidate.get("root_elements"))
    has_available_gold_dps = _has_available_virtual_hall_gold_dps(candidates)
    has_available_gold_candidate = _has_available_virtual_hall_gold_candidate(candidates)
    availability_line = f"可参加：{available_count}，可匹配灵根：{known_root_count}"
    if not has_available_gold_dps:
        availability_line += "，无DPS可用"
    title_prefix = "推荐配置：虚天殿" if lightweight else "虚天殿"
    lines = [f"{title_prefix} {room_id}｜{title}", availability_line]
    if available_count <= 0:
        lines.append("未找到虚天殿状态为可的人员。")
        return "\n".join(lines)
    if not has_available_gold_dps:
        lines.append("无DPS可用，当前不推荐入本。")
        if has_available_gold_candidate:
            lines.append("提示：存在金/雷候选，但未勾选金/雷 DPS。")
        if lightweight:
            lines.append(f"已安排 {_LIGHTWEIGHT_NO_DPS_AUTO_DISSOLVE_DELAY_SEC} 秒后自动解散。")
        return "\n".join(lines)
    if not recommendations:
        lines.append("未找到可推荐配置")
        return "\n".join(lines)
    visible_recommendations = recommendations[:1] if lightweight else recommendations
    for index, recommendation in enumerate(visible_recommendations):
        if lightweight:
            line = _format_virtual_hall_lightweight_recommendation_line(recommendation, leader_username=leader_username, html=html)
            lines.append("推荐加入：" + line)
        else:
            lines.append(_format_virtual_hall_recommendation_line(room_id, recommendation, leader_username=leader_username))
    route_advice = _format_xutian_oracle_route_advice_section(gua_record, html=html)
    if route_advice:
        lines.append(route_advice)
    missing_roots = [candidate.get("username") for candidate in candidates if candidate.get("available") and not candidate.get("root_elements")]
    if missing_roots:
        lines.append("未参与匹配（缺灵根）：" + " ".join(missing_roots[:10]))
    return "\n".join(lines)


def _format_latest_virtual_hall_recommendation_section(*, html=False):
    gua_record = _get_latest_replica_room_gua_record(_REPLICA_KIND_VIRTUAL_HALL, now=time.time())
    if not gua_record:
        return ""
    room_id = str(gua_record.get("room_id") or "").strip()
    if not room_id:
        return ""
    candidates = _parse_replica_query_reply_text(_format_replica_query_reply(""))
    recommendations = _build_virtual_hall_recommendations(gua_record, candidates, limit=1)
    return _format_virtual_hall_recommendations(
        room_id,
        gua_record,
        recommendations,
        candidates,
        lightweight=True,
        html=html,
    )


def _get_replica_kind_state(record, replica_kind, *, create=False):
    record = record if isinstance(record, dict) else {}
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    states = record.get("replica_states")
    if not isinstance(states, dict):
        states = {}
        if create:
            record["replica_states"] = states
    state_item = states.get(replica_kind)
    if not isinstance(state_item, dict):
        state_item = {}
        if create:
            states[replica_kind] = state_item
    if replica_kind == _REPLICA_KIND_VIRTUAL_HALL:
        for key in ("cooldown_until", "participating", "room_id", "team_usernames", "team_identity_ids", "joined_at", "active_until"):
            if key in record and key not in state_item:
                state_item[key] = record.get(key)
    return state_item


def _get_replica_active_until(record, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    state_item = _get_replica_kind_state(record, replica_kind)
    active_until = float(state_item.get("active_until") or 0)
    joined_at = float(state_item.get("joined_at") or 0)
    if joined_at > 0:
        joined_active_until = joined_at + REPLICA_ACTIVE_TTL_SEC
        return min(active_until, joined_active_until) if active_until > 0 else joined_active_until
    return active_until


def _get_replica_state_msg_id(record, state_item, key, replica_kind):
    try:
        msg_id = int((state_item or {}).get(key) or 0)
    except (TypeError, ValueError):
        msg_id = 0
    if msg_id > 0:
        return msg_id
    if (record or {}).get("replica_kind") != replica_kind:
        return 0
    try:
        return int((record or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _is_replica_join_obsolete_after_dissolve(record, state_item, replica_kind, msg_id, room_id=""):
    try:
        join_msg_id = int(msg_id or 0)
    except (TypeError, ValueError):
        join_msg_id = 0
    if join_msg_id <= 0:
        return False
    join_room_id = str(room_id or (state_item or {}).get("room_id") or "").strip()
    dissolved_room_id = str((state_item or {}).get("last_dissolved_room_id") or (record or {}).get("last_dissolved_room_id") or "").strip()
    current_room_id = str((state_item or {}).get("room_id") or "").strip()
    if dissolved_room_id:
        if join_room_id and dissolved_room_id != join_room_id:
            return False
    elif join_room_id and current_room_id and join_room_id != current_room_id:
        return False
    dissolve_msg_id = _get_replica_state_msg_id(record, state_item, "last_dissolve_source_msg_id", replica_kind)
    return dissolve_msg_id > join_msg_id


def _get_active_replica_identity_ids(now, replica_kind=None):
    records = _cleanup_replica_run_state(now)
    identity_ids = []
    target_kinds = [replica_kind] if replica_kind in _REPLICA_KINDS else list(_REPLICA_KINDS)
    for raw_identity_id, record in records.items():
        if not isinstance(record, dict):
            continue
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0:
            continue
        for kind in target_kinds:
            state_item = _get_replica_kind_state(record, kind)
            if state_item.get("participating") and _get_replica_active_until(record, kind) > float(now or 0):
                identity_ids.append(identity_id)
                break
    return identity_ids


def _get_active_replica_team_identity_ids_for_usernames(usernames, now, replica_kind=None):
    target_usernames = set(_normalize_replica_username_list(usernames))
    if not target_usernames:
        return []
    records = _cleanup_replica_run_state(now)
    identity_ids = []
    seen = set()
    target_kinds = [replica_kind] if replica_kind in _REPLICA_KINDS else list(_REPLICA_KINDS)
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for kind in target_kinds:
            state_item = _get_replica_kind_state(record, kind)
            if not state_item.get("participating") or _get_replica_active_until(record, kind) <= float(now or 0):
                continue
            team_usernames = set(_normalize_replica_username_list(state_item.get("team_usernames") or []))
            if not team_usernames or not target_usernames.intersection(team_usernames):
                continue
            team_identity_ids = _normalize_replica_identity_ids(state_item.get("team_identity_ids") or [])
            if not team_identity_ids:
                team_identity_ids = _map_replica_usernames_to_identity_ids(team_usernames)
            for identity_id in team_identity_ids:
                if identity_id not in seen:
                    seen.add(identity_id)
                    identity_ids.append(identity_id)
    return identity_ids


def _parse_replica_join_reply(text, reply_to=None):
    raw_text = str(text or "")
    room_id, replica_kind = _parse_replica_join_command(getattr(reply_to, "raw_text", "") or "")
    replica_kind = replica_kind or _infer_replica_kind_from_text(raw_text, default=_REPLICA_KIND_VIRTUAL_HALL)
    joined_match = _REPLICA_JOINED_RE.search(raw_text)
    if joined_match and not room_id:
        room_id = next((str(group or "").strip() for group in joined_match.groups()[1:] if str(group or "").strip()), "")
    team_usernames = _extract_replica_team_usernames(raw_text) or _extract_replica_usernames(raw_text)
    if joined_match or "你已在队伍中" in raw_text:
        return {"kind": "joined", "replica_kind": replica_kind, "room_id": room_id, "team_usernames": team_usernames, "wait_sec": 0, "reason": ""}
    if "此队伍已满员" in raw_text:
        return {"kind": "not_joined", "replica_kind": replica_kind, "room_id": room_id, "team_usernames": [], "wait_sec": 0, "reason": "full"}
    if "找不到此副本房间" in raw_text:
        return {"kind": "not_joined", "replica_kind": replica_kind, "room_id": room_id, "team_usernames": [], "wait_sec": 0, "reason": "not_found"}
    if "无法立即加入新副本" in raw_text and "请在" in raw_text and "后再试" in raw_text:
        return {"kind": "cooldown", "replica_kind": replica_kind, "room_id": room_id, "team_usernames": [], "wait_sec": parse_wait_time(raw_text), "reason": "cooldown"}
    return {"kind": "unknown", "replica_kind": replica_kind, "room_id": room_id, "team_usernames": team_usernames, "wait_sec": 0, "reason": ""}


def _cleanup_replica_run_state(now=None):
    now = float(now or time.time())
    _cleanup_replica_room_gua_records(now)
    _cleanup_virtual_hall_auto_open_flows(now)
    records = _get_replica_run_records()
    changed = False
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for replica_kind in _REPLICA_KINDS:
            state_item = _get_replica_kind_state(record, replica_kind, create=replica_kind == _REPLICA_KIND_VIRTUAL_HALL)
            active_until = _get_replica_active_until(record, replica_kind)
            last_join_msg_id = _get_replica_state_msg_id(record, state_item, "last_join_msg_id", replica_kind)
            if state_item.get("participating") and _is_replica_join_obsolete_after_dissolve(record, state_item, replica_kind, last_join_msg_id, state_item.get("room_id")):
                state_item["participating"] = False
                state_item["team_usernames"] = []
                state_item["team_identity_ids"] = []
                changed = True
            elif state_item.get("participating") and active_until > 0 and now >= active_until:
                state_item["participating"] = False
                state_item["team_usernames"] = []
                state_item["team_identity_ids"] = []
                changed = True
        has_kind_failure_pending = any(float(_get_replica_kind_state(record, kind).get("failure_pending_until") or 0) > 0 for kind in _REPLICA_KINDS)
        for replica_kind in _REPLICA_KINDS:
            state_item = _get_replica_kind_state(record, replica_kind)
            failure_pending_until = float(state_item.get("failure_pending_until") or 0)
            if failure_pending_until > 0 and now >= failure_pending_until:
                state_item["failure_pending_until"] = 0
                if state_item.get("participating"):
                    state_item["participating"] = False
                    state_item["team_usernames"] = []
                    state_item["team_identity_ids"] = []
                changed = True
            dispatch_pending_until = float(state_item.get("dispatch_pending_until") or 0)
            if dispatch_pending_until > 0 and now >= dispatch_pending_until:
                _clear_replica_dispatch_pending_fields(state_item)
                changed = True
        failure_pending_until = float(record.get("failure_pending_until") or 0)
        if failure_pending_until > 0 and now >= failure_pending_until:
            record["failure_pending_until"] = 0
            if not has_kind_failure_pending:
                for replica_kind in _REPLICA_KINDS:
                    state_item = _get_replica_kind_state(record, replica_kind)
                    if state_item.get("participating"):
                        state_item["participating"] = False
                        state_item["team_usernames"] = []
                        state_item["team_identity_ids"] = []
                        changed = True
            changed = True
    if changed:
        _save_replica_run_records(records)
    return records


def _clear_replica_dispatch_pending_fields(state_item):
    for key in _REPLICA_EXTERNAL_DISPATCH_PENDING_KEYS:
        state_item.pop(key, None)


def _update_replica_join_record(record, identity_id, room_id, team_usernames, now, msg_id=0, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    if _is_replica_join_obsolete_after_dissolve(record, state_item, replica_kind, msg_id, room_id):
        return False
    profile_username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
    normalized_usernames = _normalize_replica_username_list(team_usernames)
    if profile_username and profile_username not in normalized_usernames:
        normalized_usernames.append(profile_username)
    state_item.update({
        "participating": True,
        "room_id": str(room_id or state_item.get("room_id") or ""),
        "team_usernames": normalized_usernames,
        "team_identity_ids": _map_replica_usernames_to_identity_ids(normalized_usernames),
        "joined_at": float(now or 0),
        "active_until": float(now or 0) + REPLICA_ACTIVE_TTL_SEC,
        "last_join_msg_id": int(msg_id or 0),
        "failure_pending_until": 0,
    })
    _clear_replica_dispatch_pending_fields(state_item)
    record.update({
        "replica_kind": replica_kind,
        "last_join_msg_id": int(msg_id or 0),
        "last_join_result": "joined",
        "last_join_error": "",
        "last_failure_at": 0,
        "failure_pending_until": 0,
        "updated_at": float(now or 0),
    })
    return True


def _mark_replica_join_success(identity_id, room_id, team_usernames, now, msg_id=0, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    identity_id = int(identity_id or 0)
    if identity_id <= 0:
        return
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    if _update_replica_join_record(record, identity_id, room_id, team_usernames, now, msg_id=msg_id, replica_kind=replica_kind):
        _save_replica_run_records(records)


def _preserve_confirmed_replica_join(records, record, state_item, room_id, now, msg_id=0, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    if (
        state_item.get("participating")
        and str(state_item.get("room_id") or "") == str(room_id or state_item.get("room_id") or "")
        and _get_replica_active_until(record, replica_kind) > float(now or 0)
    ):
        _clear_replica_dispatch_pending_fields(state_item)
        record.update({
            "replica_kind": replica_kind,
            "last_join_result": "joined",
            "last_join_error": "",
            "updated_at": float(now or 0),
        })
        _save_replica_run_records(records)
        return True
    return False


def _mark_replica_join_not_joined(identity_id, room_id, reason, now, msg_id=0, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    if _preserve_confirmed_replica_join(records, record, state_item, room_id, now, msg_id=msg_id, replica_kind=replica_kind):
        return
    state_item.update({
        "participating": False,
        "room_id": str(room_id or state_item.get("room_id") or ""),
        "team_usernames": [],
        "team_identity_ids": [],
        "failure_pending_until": 0,
    })
    _clear_replica_dispatch_pending_fields(state_item)
    record.update({
        "replica_kind": replica_kind,
        "last_join_msg_id": int(msg_id or 0),
        "last_join_result": "not_joined",
        "last_join_error": str(reason or ""),
        "updated_at": float(now or 0),
    })
    _save_replica_run_records(records)


def _mark_replica_join_cooldown(identity_id, room_id, wait_sec, now, msg_id=0, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    if _preserve_confirmed_replica_join(records, record, state_item, room_id, now, msg_id=msg_id, replica_kind=replica_kind):
        return
    state_item.update({
        "participating": False,
        "room_id": str(room_id or state_item.get("room_id") or ""),
        "cooldown_until": float(now or 0) + max(0, int(wait_sec or 0)),
        "team_usernames": [],
        "team_identity_ids": [],
        "failure_pending_until": 0,
    })
    _clear_replica_dispatch_pending_fields(state_item)
    record.update({
        "replica_kind": replica_kind,
        "last_join_msg_id": int(msg_id or 0),
        "last_join_result": "cooldown",
        "last_join_error": "",
        "updated_at": float(now or 0),
    })
    _save_replica_run_records(records)


def _find_replica_identity_id_by_reply_sender(event):
    try:
        sender_id = int(getattr(event, "sender_id", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if sender_id <= 0:
        return 0
    participant_ids = []
    for identity_id in [*get_replica_participant_identity_ids(), *get_replica_dispatch_participant_identity_ids()]:
        if int(identity_id or 0) not in participant_ids:
            participant_ids.append(int(identity_id or 0))
    for identity_id in participant_ids:
        try:
            normalized_id = int(identity_id or 0)
        except (TypeError, ValueError):
            continue
        if sender_id == normalized_id:
            return normalized_id
        if sender_id == int(get_identity_account(normalized_id) or 0):
            return normalized_id
    return 0


async def _find_replica_identity_ids_by_sender(event):
    identity_id = _find_replica_identity_id_by_reply_sender(event)
    if identity_id > 0:
        return [identity_id]
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    username = _normalize_replica_username(getattr(sender, "username", "") or "")
    identity_id = _get_replica_identity_ids_by_username().get(username)
    return [identity_id] if identity_id else []


def _infer_replica_kind_from_text(text, default=""):
    raw_text = str(text or "")
    if "苍坤上人洞府" in raw_text:
        return _REPLICA_KIND_CANGKUN
    for kind, meta in _REPLICA_KIND_META.items():
        if meta["name"] in raw_text or meta["join_command"] in raw_text or meta["enter_command"] in raw_text:
            return kind
    return default if default in _REPLICA_KINDS else ""


def _infer_replica_kind_from_active_team_usernames(usernames, now, records=None):
    target_usernames = set(_normalize_replica_username_list(usernames))
    if not target_usernames:
        return ""
    records = records if isinstance(records, dict) else _cleanup_replica_run_state(now)
    matched_kinds = set()
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for kind in _REPLICA_KINDS:
            state_item = _get_replica_kind_state(record, kind)
            if not state_item.get("participating") or _get_replica_active_until(record, kind) <= float(now or 0):
                continue
            team_usernames = set(_normalize_replica_username_list(state_item.get("team_usernames") or []))
            if target_usernames.intersection(team_usernames):
                matched_kinds.add(kind)
    return next(iter(matched_kinds)) if len(matched_kinds) == 1 else ""


def _infer_replica_kind_from_active_room(room_id, now, records=None):
    room_id = str(room_id or "").strip()
    if not room_id:
        return ""
    records = records if isinstance(records, dict) else _cleanup_replica_run_state(now)
    matched_kinds = set()
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for kind in _REPLICA_KINDS:
            state_item = _get_replica_kind_state(record, kind)
            if str(state_item.get("room_id") or "") != room_id:
                continue
            if not state_item.get("participating") or _get_replica_active_until(record, kind) <= float(now or 0):
                continue
            matched_kinds.add(kind)
    return next(iter(matched_kinds)) if len(matched_kinds) == 1 else ""


def _infer_single_active_replica_kind(now, records=None):
    records = records if isinstance(records, dict) else _cleanup_replica_run_state(now)
    matched_kinds = set()
    for record in records.values():
        if not isinstance(record, dict):
            continue
        for kind in _REPLICA_KINDS:
            state_item = _get_replica_kind_state(record, kind)
            if state_item.get("participating") and _get_replica_active_until(record, kind) > float(now or 0):
                matched_kinds.add(kind)
    return next(iter(matched_kinds)) if len(matched_kinds) == 1 else ""


def _resolve_replica_kind_for_progress(text, now, usernames=None, room_id=""):
    replica_kind = _infer_replica_kind_from_text(text)
    if replica_kind:
        return replica_kind
    replica_kind = _infer_replica_kind_from_active_team_usernames(usernames or [], now)
    if replica_kind:
        return replica_kind
    replica_kind = _infer_replica_kind_from_active_room(room_id, now)
    if replica_kind:
        return replica_kind
    replica_kind = _infer_single_active_replica_kind(now)
    return replica_kind or _REPLICA_KIND_VIRTUAL_HALL


def _mark_replica_team_joined_from_text(text, now, msg_id=0):
    raw_text = str(text or "")
    opened_match = _REPLICA_OPENED_RE.search(raw_text)
    joined_match = _REPLICA_JOINED_RE.search(raw_text)
    room_id = ""
    replica_kind = _infer_replica_kind_from_text(raw_text)
    if opened_match:
        room_id = str(opened_match.group("room_id") or "").strip()
        leader_username = _normalize_replica_username(opened_match.group("leader"))
        team_usernames = [leader_username]
        opened_kind_name = opened_match.group("opened_kind_name") or opened_match.group("opened_zhuimo") or opened_match.group("opened_huanglong") or opened_match.group("opened_cangkun") or raw_text
        replica_kind = _infer_replica_kind_from_text(opened_kind_name)
        if replica_kind == _REPLICA_KIND_VIRTUAL_HALL:
            _mark_virtual_hall_gua_from_opened_text(raw_text, now, room_id, leader_username=leader_username, msg_id=msg_id)
    elif joined_match:
        room_id = next((str(group or "").strip() for group in joined_match.groups()[1:] if str(group or "").strip()), "")
        team_usernames = _extract_replica_team_usernames(raw_text)
        if not replica_kind:
            replica_kind = _infer_replica_kind_from_active_team_usernames(team_usernames, now)
    else:
        return False
    if not team_usernames or replica_kind not in _REPLICA_KINDS:
        return False
    identity_ids = _map_replica_usernames_to_identity_ids(team_usernames)
    if not identity_ids:
        return False
    records = _get_replica_run_records()
    changed = False
    for identity_id in identity_ids:
        record = _get_replica_identity_record(records, identity_id)
        changed = _update_replica_join_record(record, identity_id, room_id, team_usernames, now, msg_id=msg_id, replica_kind=replica_kind) or changed
    if changed:
        _save_replica_run_records(records)
    return changed


def _mark_replica_success_cooldown(identity_ids, now, source_msg_id=0, leader_username="", replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    records = _get_replica_run_records()
    changed = False
    cooldown_until = float(now or 0) + REPLICA_SUCCESS_COOLDOWN_SEC
    source_key = f"last_cooldown_source_msg_id_{replica_kind}"
    for identity_id in identity_ids or []:
        record = _get_replica_identity_record(records, identity_id)
        if int(record.get(source_key) or 0) == int(source_msg_id or 0) and int(source_msg_id or 0) > 0:
            continue
        state_item = _get_replica_kind_state(record, replica_kind, create=True)
        state_item.update({
            "participating": False,
            "cooldown_until": max(float(state_item.get("cooldown_until") or 0), cooldown_until),
            "team_usernames": [],
            "team_identity_ids": [],
        })
        state_item["failure_pending_until"] = 0
        record.update({
            "replica_kind": replica_kind,
            "leader_username": _normalize_replica_username(leader_username),
            "last_join_result": "success_cooldown",
            "last_join_error": "",
            "last_failure_at": 0,
            "failure_pending_until": 0,
            source_key: int(source_msg_id or 0),
            "updated_at": float(now or 0),
        })
        if replica_kind == _REPLICA_KIND_VIRTUAL_HALL:
            record["last_cooldown_source_msg_id"] = int(source_msg_id or 0)
        changed = True
    if changed:
        _save_replica_run_records(records)


def _mark_replica_failure_pending(identity_ids, now, replica_kind=_REPLICA_KIND_VIRTUAL_HALL):
    replica_kind = replica_kind if replica_kind in _REPLICA_KINDS else _REPLICA_KIND_VIRTUAL_HALL
    records = _get_replica_run_records()
    changed = False
    failure_pending_until = float(now or 0) + REPLICA_FAILURE_GRACE_SEC
    for identity_id in identity_ids or []:
        record = _get_replica_identity_record(records, identity_id)
        state_item = _get_replica_kind_state(record, replica_kind, create=True)
        state_item["failure_pending_until"] = failure_pending_until
        record["replica_kind"] = replica_kind
        record["last_failure_at"] = float(now or 0)
        record["failure_pending_until"] = failure_pending_until
        record["last_join_result"] = "failure_pending"
        record["updated_at"] = float(now or 0)
        changed = True
    if changed:
        _save_replica_run_records(records)


def _mark_replica_room_dissolved(room_id, now, source_msg_id=0, leader_username="", replica_kind=None):
    room_id = str(room_id or "").strip()
    if not room_id:
        return False
    records = _get_replica_run_records()
    changed = False
    target_kinds = [replica_kind] if replica_kind in _REPLICA_KINDS else list(_REPLICA_KINDS)
    for record in records.values():
        if not isinstance(record, dict):
            continue
        if int(record.get("last_dissolve_source_msg_id") or 0) == int(source_msg_id or 0):
            continue
        matched = False
        for kind in target_kinds:
            state_item = _get_replica_kind_state(record, kind)
            if str(state_item.get("room_id") or "") != room_id:
                continue
            state_item.update({
                "participating": False,
                "team_usernames": [],
                "team_identity_ids": [],
                "failure_pending_until": 0,
                "last_dissolve_source_msg_id": int(source_msg_id or 0),
                "last_dissolved_room_id": room_id,
            })
            matched = True
        if not matched:
            continue
        record.update({
            "leader_username": _normalize_replica_username(leader_username),
            "last_join_result": "dissolved",
            "last_join_error": "",
            "last_failure_at": 0,
            "failure_pending_until": 0,
            "last_dissolve_source_msg_id": int(source_msg_id or 0),
            "last_dissolved_room_id": room_id,
            "updated_at": float(now or 0),
        })
        changed = True
    if changed:
        _save_replica_run_records(records)
    return changed


def _mark_replica_team_kicked(leader_username, kicked_username, team_usernames, now, source_msg_id=0, replica_kind=None):
    leader_username = _normalize_replica_username(leader_username)
    kicked_username = _normalize_replica_username(kicked_username)
    normalized_team_usernames = [username for username in _normalize_replica_username_list(team_usernames) if username != kicked_username]
    if not leader_username or not kicked_username:
        return False
    records = _cleanup_replica_run_state(now)
    changed = False
    target_kinds = [replica_kind] if replica_kind in _REPLICA_KINDS else list(_REPLICA_KINDS)
    for raw_identity_id, record in records.items():
        if not isinstance(record, dict):
            continue
        if int(record.get("last_kick_source_msg_id") or 0) == int(source_msg_id or 0):
            continue
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        profile_username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        matched = False
        for kind in target_kinds:
            state_item = _get_replica_kind_state(record, kind)
            if not state_item.get("participating"):
                continue
            record_team_usernames = set(_normalize_replica_username_list(state_item.get("team_usernames") or []))
            if leader_username not in record_team_usernames and kicked_username not in record_team_usernames:
                continue
            if profile_username == kicked_username:
                state_item.update({
                    "participating": False,
                    "team_usernames": [],
                    "team_identity_ids": [],
                })
            elif normalized_team_usernames:
                state_item["team_usernames"] = normalized_team_usernames
                state_item["team_identity_ids"] = _map_replica_usernames_to_identity_ids(normalized_team_usernames)
            matched = True
        if not matched:
            continue
        record.update({
            "leader_username": leader_username,
            "last_join_result": "kicked" if profile_username == kicked_username else record.get("last_join_result", "joined"),
            "last_join_error": "",
            "last_failure_at": 0,
            "failure_pending_until": 0,
            "last_kick_source_msg_id": int(source_msg_id or 0),
            "updated_at": float(now or 0),
        })
        changed = True
    if changed:
        _save_replica_run_records(records)
    return changed


def _format_replica_remaining(until_ts, now):
    remain = max(0, int((float(until_ts or 0) - float(now or 0) + 59) // 60))
    hours, minutes = divmod(remain, 60)
    return f"{hours}:{minutes:02d}"


def _get_replica_identity_kind_status(identity_id, replica_kind, now, records=None):
    records = records if isinstance(records, dict) else _cleanup_replica_run_state(now)
    record = _normalize_replica_run_record(records.get(str(identity_id)))
    state_item = _get_replica_kind_state(record, replica_kind)
    cooldown_until = float(state_item.get("cooldown_until") or 0)
    if cooldown_until > now:
        return _format_replica_remaining(cooldown_until, now)
    active_until = _get_replica_active_until(record, replica_kind)
    if state_item.get("participating") and active_until > now:
        return "中"
    return "可"


def _format_replica_identity_statuses(identity_id, now, records=None):
    return " | ".join(
        f"{_REPLICA_KIND_META[replica_kind]['short']}:{_get_replica_identity_kind_status(identity_id, replica_kind, now, records=records)}"
        for replica_kind in _REPLICA_KINDS
    )


def _format_replica_query_root_attrs(root_attrs, dps_enabled=False):
    root_attrs = str(root_attrs or "").strip() or "未获取"
    if dps_enabled and any(attr in root_attrs for attr in ("金", "雷")):
        return f"{root_attrs}DPS"
    return root_attrs


def _format_replica_query_reply(filter_text="", participant_identity_ids=None, fallback_to_all=True):
    query = str(filter_text or "").strip()
    now = time.time()
    records = _cleanup_replica_run_state(now)
    lines = []
    for identity_id in _get_replica_candidate_identity_ids(
        require_username=True,
        participant_identity_ids=participant_identity_ids,
        fallback_to_all=fallback_to_all,
    ):
        profile = get_send_as_profile(identity_id)
        username = str(profile.get("username") or "").strip()
        if not username.startswith("@"):
            username = f"@{username}"
        root_attrs = str(profile.get("spiritual_root_attrs") or "").strip() or "未获取"
        professions = str(profile.get("replica_professions") or "").strip()
        if query and query not in professions.split("|"):
            continue
        display_root_attrs = _format_replica_query_root_attrs(root_attrs, get_replica_gold_dps_enabled(identity_id))
        status_text = _format_replica_identity_statuses(identity_id, now, records=records)
        lines.append(f"{mono(username)} | {display_root_attrs} | {status_text}")
    if lines:
        return "\n".join(lines)
    return f"未找到职业为 {mono(query)} 的参与身份" if query else "当前没有已勾选且带 username 的副本参与身份"


def _get_replica_query_aggregator_submit_config():
    config = get_replica_query_aggregator_config()
    if not config.get("base_url") or not config.get("client_id") or not config.get("secret"):
        return {}
    return config


def _coerce_replica_metadata_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default or 0)


async def _maybe_submit_replica_query_reply_to_aggregator(
    query_message_id,
    query_text,
    query_filter,
    reply_text,
    *,
    source_chat_id=0,
    source_message_id=0,
    listener_account_id=0,
    identity_id=0,
):
    config = _get_replica_query_aggregator_submit_config()
    if not config:
        return False
    try:
        query_message_id = int(query_message_id or 0)
    except (TypeError, ValueError):
        query_message_id = 0
    if query_message_id <= 0:
        console_log("副本查询汇聚提交失败：缺少 query_message_id", scope="global", limit=180)
        return False
    source_chat_id = _coerce_replica_metadata_int(source_chat_id)
    source_message_id = _coerce_replica_metadata_int(source_message_id or query_message_id)
    listener_account_id = _coerce_replica_metadata_int(listener_account_id)
    identity_id = _coerce_replica_metadata_int(identity_id or listener_account_id)
    try:
        response = await submit_replica_query_result(
            config=config,
            query_message_id=query_message_id,
            query_text=query_text,
            query_filter=query_filter,
            source_id=source_chat_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            identity_id=identity_id,
            send_as_id=identity_id,
            listener_account_id=listener_account_id,
            client_id=config.get("client_id") or "",
            reply_text=reply_text,
            generated_at=time.time(),
        )
        session_id = str(response.get("session_id") or f"msg:{query_message_id}")
        accepted_lines = int(response.get("accepted_lines") or 0)
        console_log(f"已提交副本查询结果到汇聚服务：{session_id} lines={accepted_lines}", scope="global", limit=180)
        return True
    except Exception as exc:
        error_text = str(exc) or exc.__class__.__name__
        if isinstance(exc, ReplicaQueryAggregatorError):
            console_log(f"副本查询汇聚提交失败：{error_text}", scope="global", limit=240)
        else:
            console_log(f"副本查询汇聚提交异常：{error_text}", scope="global", limit=240)
    return False


def _parse_replica_leader_username(text):
    match = re.search(r"队长\s*[:：]?\s*(@[A-Za-z0-9_]{3,32})", str(text or ""))
    return _normalize_replica_username(match.group(1)) if match else ""


def _parse_replica_enter_command(text):
    match = _REPLICA_ENTER_COMMAND_RE.match(str(text or "").strip())
    if not match:
        return ""
    return _get_replica_kind_by_enter_command(match.group("command"))

def _parse_replica_room_dissolved(text):
    match = _REPLICA_ROOM_DISSOLVED_RE.search(str(text or ""))
    if not match:
        match = _REPLICA_KIND_ROOM_DISSOLVED_RE.search(str(text or ""))
    if not match:
        return "", ""
    return _normalize_replica_username(match.group(1)), str(match.group(2) or "").strip()


def _parse_replica_team_kicked(text):
    text = str(text or "")
    match = _REPLICA_TEAM_KICKED_RE.search(text)
    if not match:
        return "", "", []
    team_usernames = []
    team_section = text.split("当前队伍", 1)[1] if "当前队伍" in text else ""
    for line in team_section.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        username_match = _REPLICA_USERNAME_RE.search(line)
        if username_match:
            team_usernames.append(username_match.group(0))
    return _normalize_replica_username(match.group(1)), _normalize_replica_username(match.group(2)), team_usernames


async def _handle_replica_progress_event(event, now, event_type="message"):
    text = str(getattr(event, "raw_text", "") or "")
    dissolved_leader, dissolved_room_id = _parse_replica_room_dissolved(text)
    if (
        "【鼎前抉择】" not in text
        and "挑战失败！" not in text
        and not dissolved_room_id
        and "【队员已请离】" not in text
        and not _parse_replica_enter_command(text)
    ):
        return False
    consumed_family = f"replica_progress_{str(event_type or 'message').strip() or 'message'}"
    if _has_runtime_message_consumed(event, consumed_family):
        return False
    _mark_runtime_message_consumed(event, consumed_family)
    enter_kind = _parse_replica_enter_command(text)
    if enter_kind and enter_kind != _REPLICA_KIND_VIRTUAL_HALL:
        leader_ids = await _find_replica_identity_ids_by_sender(event)
        leader_username = _normalize_replica_username(get_send_as_profile(leader_ids[0]).get("username") or "") if leader_ids else ""
        identity_ids = _get_active_replica_team_identity_ids_for_usernames([leader_username], now, replica_kind=enter_kind) if leader_username else []
        if not identity_ids:
            identity_ids = leader_ids
        if identity_ids:
            _mark_replica_success_cooldown(identity_ids, now, source_msg_id=getattr(event, "id", 0), leader_username=leader_username, replica_kind=enter_kind)
            return True
    if "【鼎前抉择】" in text:
        leader_username = _parse_replica_leader_username(text)
        if not leader_username:
            return False
        replica_kind = _resolve_replica_kind_for_progress(text, now, usernames=[leader_username])
        identity_ids = _get_active_replica_team_identity_ids_for_usernames([leader_username], now, replica_kind=replica_kind)
        if not identity_ids:
            identity_ids = _map_replica_usernames_to_identity_ids([leader_username])
        if not identity_ids:
            return False
        _mark_replica_success_cooldown(identity_ids, now, source_msg_id=getattr(event, "id", 0), leader_username=leader_username, replica_kind=replica_kind)
        return True
    if dissolved_room_id:
        replica_kind = _resolve_replica_kind_for_progress(text, now, usernames=[dissolved_leader], room_id=dissolved_room_id)
        if _mark_replica_room_dissolved(dissolved_room_id, now, source_msg_id=getattr(event, "id", 0), leader_username=dissolved_leader, replica_kind=replica_kind):
            return True
    if "【队员已请离】" in text:
        leader_username, kicked_username, team_usernames = _parse_replica_team_kicked(text)
        replica_kind = _resolve_replica_kind_for_progress(text, now, usernames=team_usernames or [leader_username, kicked_username])
        if _mark_replica_team_kicked(leader_username, kicked_username, team_usernames, now, source_msg_id=getattr(event, "id", 0), replica_kind=replica_kind):
            return True
    if "挑战失败！" in text:
        event_usernames = _extract_replica_usernames(text)
        replica_kind = _resolve_replica_kind_for_progress(text, now, usernames=event_usernames)
        identity_ids = _get_active_replica_team_identity_ids_for_usernames(event_usernames, now, replica_kind=replica_kind)
        if not identity_ids:
            identity_ids = _map_replica_usernames_to_identity_ids(event_usernames)
        if not identity_ids and not event_usernames:
            identity_ids = _get_active_replica_identity_ids(now, replica_kind=replica_kind)
        if identity_ids:
            _mark_replica_failure_pending(identity_ids, now, replica_kind=replica_kind)
            return True
    return False


def _parse_virtual_hall_open_failure(text):
    raw_text = str(text or "")
    if "你没有【虚天残图】" in raw_text or "无法开启虚天殿" in raw_text and "残图" in raw_text:
        return "缺少虚天残图"
    if "你已经开启了一个副本房间" in raw_text or "请勿重复操作" in raw_text:
        return "已有副本房间"
    if "无法立即开启新副本" in raw_text and "后再试" in raw_text:
        wait_sec = parse_wait_time(raw_text)
        wait_text = fmt_time_after(wait_sec) if wait_sec > 0 else "冷却中"
        return f"开房冷却中：{wait_text}"
    return ""


def _parse_replica_kick_failure(text):
    raw_text = str(text or "")
    if "你并非队长" in raw_text and "无法请离他人" in raw_text:
        return {"username": "", "reason": "不是队长或房间已解散"}
    match = re.search(r"道友\s*(@[A-Za-z0-9_]{3,32})\s*并不在你当前的队伍中", raw_text)
    if match:
        return {"username": _normalize_replica_username(match.group(1)), "reason": "不在当前队伍"}
    match = re.search(r"天机阁无法在天地间定位到\s*[“\"]?(@[A-Za-z0-9_]{3,32})[”\"]?", raw_text)
    if match:
        return {"username": _normalize_replica_username(match.group(1)), "reason": "无法定位用户名"}
    return None


def _extract_replica_team_member_entries(text):
    team_section = _extract_replica_team_section(text)
    entries = []
    for line in team_section.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        username_match = _REPLICA_USERNAME_RE.match(body)
        if body.startswith("@") and username_match:
            entries.append({"kind": "username", "username": _normalize_replica_username(username_match.group(0)), "raw": body})
            continue
        display_name = re.split(r"\s+", body, maxsplit=1)[0].strip()
        entries.append({"kind": "plain", "display_name": display_name, "raw": body})
    return entries


def _parse_virtual_hall_team_snapshot(text, reply_to=None):
    raw_text = str(text or "")
    if "当前队伍" not in raw_text:
        return None
    room_id = ""
    joined_match = _REPLICA_JOINED_RE.search(raw_text)
    if joined_match:
        room_id = next((str(group or "").strip() for group in joined_match.groups()[1:] if str(group or "").strip()), "")
    if not room_id:
        reply_room_id, reply_kind = _parse_replica_join_command(getattr(reply_to, "raw_text", "") or "")
        if reply_kind != _REPLICA_KIND_VIRTUAL_HALL:
            return None
        room_id = reply_room_id
    if not room_id:
        return None
    entries = _extract_replica_team_member_entries(raw_text)
    if not entries:
        return None
    return {
        "room_id": room_id,
        "entries": entries,
        "usernames": [item.get("username") for item in entries if item.get("kind") == "username" and item.get("username")],
        "plain_members": [item.get("display_name") for item in entries if item.get("kind") == "plain" and item.get("display_name")],
    }


def _resolve_virtual_hall_pending_kick(flow, username="", reply_to_msg_id=0, now=None):
    pending = flow.get("kick_pending_usernames")
    if not isinstance(pending, dict):
        return "", None
    username = _normalize_replica_username(username)
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id > 0:
        for pending_username, item in pending.items():
            if int((item or {}).get("msg_id") or 0) == reply_to_msg_id:
                return _normalize_replica_username(pending_username), item
    if username and username in pending:
        return username, pending.get(username)
    now = float(now or time.time())
    candidates = []
    for pending_username, item in pending.items():
        sent_at = float((item or {}).get("sent_at") or 0)
        if sent_at > 0 and now <= sent_at + _VIRTUAL_HALL_AUTO_OPEN_KICK_TIMEOUT_SEC:
            candidates.append((_normalize_replica_username(pending_username), item, sent_at))
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1]
    return "", None


def _record_virtual_hall_kick_result(flow, username, status, now, reason="", msg_id=0, team_usernames=None):
    username = _normalize_replica_username(username)
    if not username:
        return False
    pending = flow.get("kick_pending_usernames")
    if not isinstance(pending, dict):
        pending = {}
    pending.pop(username, None)
    kick_results = flow.get("kick_results")
    if not isinstance(kick_results, dict):
        kick_results = {}
    kick_results[username] = {"status": status, "reason": str(reason or ""), "msg_id": int(msg_id or 0), "updated_at": float(now or 0)}
    flow["kick_pending_usernames"] = pending
    flow["kick_results"] = kick_results
    if status == "success":
        kicked_usernames = _normalize_replica_username_list(flow.get("kicked_usernames") or [])
        if username not in kicked_usernames:
            kicked_usernames.append(username)
        flow["kicked_usernames"] = kicked_usernames
    if team_usernames is not None:
        team_usernames = _normalize_replica_username_list(team_usernames)
        flow["observed_team_usernames"] = team_usernames
        flow["last_team_snapshot_usernames"] = team_usernames
        flow["last_team_snapshot_at"] = float(now or 0)
    flow["updated_at"] = float(now or 0)
    return True


def _get_virtual_hall_auto_dispatch_usernames(flow):
    leader_username = _normalize_replica_username((flow or {}).get("leader_username") or "")
    source = (flow or {}).get("dispatch_usernames") or []
    if not source:
        source = [username for username in (flow or {}).get("allowed_usernames") or [] if _normalize_replica_username(username) != leader_username]
    usernames = []
    for username in _normalize_replica_username_list(source):
        if username == leader_username or username in usernames:
            continue
        usernames.append(username)
        if len(usernames) >= 4:
            break
    return usernames


def _merge_virtual_hall_auto_dispatch_usernames(flow, usernames, now, msg_id=0, sender_id=0):
    if not isinstance(flow, dict):
        return []
    leader_username = _normalize_replica_username(flow.get("leader_username") or "")
    dispatch_usernames = _get_virtual_hall_auto_dispatch_usernames(flow)
    added = []
    for username in _normalize_replica_username_list(usernames):
        if username == leader_username or username in dispatch_usernames:
            continue
        if len(dispatch_usernames) >= 4:
            break
        dispatch_usernames.append(username)
        added.append(username)
    required_usernames = _normalize_replica_username_list([leader_username, *dispatch_usernames])
    flow.update({
        "phase": "monitoring",
        "dispatch_msg_id": int(msg_id or 0),
        "dispatch_sender_id": int(sender_id or 0),
        "dispatch_seen_at": float(now or 0),
        "dispatch_usernames": dispatch_usernames,
        "required_usernames": required_usernames,
        "allowed_usernames": required_usernames,
        "expires_at": float(now or 0) + _VIRTUAL_HALL_AUTO_OPEN_TIMEOUT_SEC,
        "updated_at": float(now or 0),
    })
    if added:
        flow["dispatch_revision"] = int(flow.get("dispatch_revision") or 0) + 1
    else:
        flow["dispatch_revision"] = int(flow.get("dispatch_revision") or 0)
    if not isinstance(flow.get("missing_join_requests"), dict):
        flow["missing_join_requests"] = {}
    return added


def _virtual_hall_auto_missing_check_delay(user_count):
    return max(0, int(user_count or 0) - 1) * 2 + 2


def _set_virtual_hall_auto_missing_check_after(flow, now, user_count):
    delay = _virtual_hall_auto_missing_check_delay(user_count)
    check_after = float(now or 0) + delay
    current = float((flow or {}).get("missing_check_after") or 0)
    if current > check_after:
        check_after = current
    flow["missing_check_after"] = check_after
    return max(0, check_after - float(now or 0))


async def _run_virtual_hall_auto_deferred_team_check(flow_id, delay):
    await asyncio.sleep(max(0, float(delay or 0)))
    now = time.time()
    flows = _cleanup_virtual_hall_auto_open_flows(now)
    flow = flows.get(str(flow_id or ""))
    if not isinstance(flow, dict) or str(flow.get("phase") or "") != "monitoring":
        return False
    usernames = _normalize_replica_username_list(flow.get("last_team_snapshot_usernames") or flow.get("observed_team_usernames") or [])
    if not usernames:
        return False
    return await _apply_virtual_hall_auto_team_snapshot(flow, {"usernames": usernames, "plain_members": []}, now, allow_missing_actions=True)


def _schedule_virtual_hall_auto_deferred_team_check(flow_id, delay):
    flow_id = str(flow_id or "").strip()
    if not flow_id:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    _fire_and_forget(_run_virtual_hall_auto_deferred_team_check(flow_id, delay))
    return True


def _build_virtual_hall_auto_team_accounting(flow, usernames):
    leader_username = _normalize_replica_username((flow or {}).get("leader_username") or "")
    dispatch_usernames = _get_virtual_hall_auto_dispatch_usernames(flow)
    required_usernames = _normalize_replica_username_list([leader_username, *dispatch_usernames])
    observed_usernames = _normalize_replica_username_list(usernames)
    required = set(required_usernames)
    observed = set(observed_usernames)
    missing_dispatch = [username for username in dispatch_usernames if username not in observed]
    outsiders = [username for username in observed_usernames if username not in required]
    observed_count = len(observed_usernames)
    free_slots = max(0, 5 - observed_count)
    shortage = max(0, len(missing_dispatch) - free_slots)
    needed_evictions = max(shortage, max(0, observed_count - 5))
    return {
        "dispatch_usernames": dispatch_usernames,
        "required_usernames": required_usernames,
        "observed_usernames": observed_usernames,
        "observed_count": observed_count,
        "missing_dispatch": missing_dispatch,
        "outsiders": outsiders,
        "free_slots": free_slots,
        "shortage": shortage,
        "needed_evictions": needed_evictions,
    }


def _virtual_hall_auto_has_pending_kick(flow):
    pending = (flow or {}).get("kick_pending_usernames")
    return bool(pending) if isinstance(pending, dict) else False


async def _send_virtual_hall_auto_replica_notice(flow, text, parse_mode=None, log_text=None):
    listener_account_id = int((flow or {}).get("listener_account_id") or 0)
    client_obj = get_all_clients().get(listener_account_id)
    replica_chat_id = int((flow or {}).get("replica_chat_id") or 0)
    if client_obj is None or replica_chat_id == 0:
        return None
    return await _send_replica_group_message(client_obj, replica_chat_id, text, parse_mode=parse_mode, listener_account_id=listener_account_id, log_text=log_text if log_text is not None else text)


async def _maybe_send_virtual_hall_auto_manual_enter_notice(flow, accounting, now):
    if not isinstance(flow, dict) or flow.get("enter_requested_at"):
        return False
    if _virtual_hall_auto_has_pending_kick(flow) or accounting.get("missing_dispatch"):
        return False
    dispatch_usernames = list(accounting.get("dispatch_usernames") or [])
    observed_usernames = list(accounting.get("observed_usernames") or [])
    outsiders = list(accounting.get("outsiders") or [])
    observed_count = int(accounting.get("observed_count") or 0)
    if len(dispatch_usernames) >= 4 and observed_count >= 5 and not outsiders:
        return False
    key = f"{int(flow.get('dispatch_revision') or 0)}|{','.join(dispatch_usernames)}|{','.join(observed_usernames)}|{','.join(outsiders)}"
    if str(flow.get("manual_enter_notice_key") or "") == key:
        return False
    msg = await _send_virtual_hall_auto_replica_notice(
        flow,
        f"人员不足请手动 {mono(_VIRTUAL_HALL_ENTER_COMMAND)}",
        parse_mode="html",
        log_text=f"人员不足请手动 {_VIRTUAL_HALL_ENTER_COMMAND}",
    )
    if not msg:
        return False
    flow["manual_enter_notice_key"] = key
    flow["manual_enter_notice_at"] = float(now or 0)
    flow["updated_at"] = float(now or 0)
    _upsert_virtual_hall_auto_flow(flow)
    return True


async def _maybe_send_virtual_hall_auto_missing_dispatch_command(flow, accounting, now):
    if not isinstance(flow, dict) or _virtual_hall_auto_has_pending_kick(flow):
        return False
    missing_dispatch = list(accounting.get("missing_dispatch") or [])
    if not missing_dispatch or int(accounting.get("shortage") or 0) > 0:
        return False
    room_id = str(flow.get("room_id") or "").strip()
    if not room_id:
        return False
    requests = flow.get("missing_join_requests")
    if not isinstance(requests, dict):
        requests = {}
    eligible = []
    for username in missing_dispatch:
        item = requests.get(username)
        item = item if isinstance(item, dict) else {}
        count = int(item.get("count") or 0)
        last_sent_at = float(item.get("last_sent_at") or 0)
        if count >= _VIRTUAL_HALL_AUTO_MISSING_AUTO_RETRY_MAX:
            continue
        if last_sent_at > 0 and float(now or 0) < last_sent_at + _VIRTUAL_HALL_AUTO_MISSING_RETRY_COOLDOWN_SEC:
            continue
        eligible.append(username)
    if not eligible:
        return False
    command = f".虚天殿 {room_id} {' '.join(eligible)}"
    for username in eligible:
        item = requests.get(username)
        item = item if isinstance(item, dict) else {}
        requests[username] = {
            "count": int(item.get("count") or 0) + 1,
            "last_sent_at": float(now or 0),
            "pending": True,
        }
    flow["missing_join_requests"] = requests
    flow["updated_at"] = float(now or 0)
    _upsert_virtual_hall_auto_flow(flow)

    msg = await _send_virtual_hall_auto_replica_notice(flow, command)
    if not msg:
        for username in eligible:
            item = requests.get(username)
            if isinstance(item, dict):
                item["count"] = max(0, int(item.get("count") or 0) - 1)
                item["last_sent_at"] = 0
                item["pending"] = False
                item["send_failed_at"] = float(now or 0)
        flow["missing_join_requests"] = requests
        flow["updated_at"] = time.time()
        _upsert_virtual_hall_auto_flow(flow)
        return False
    for username in eligible:
        item = requests.get(username)
        if isinstance(item, dict):
            item["pending"] = False
            item["msg_id"] = int(getattr(msg, "id", 0) or 0)
    flow["missing_join_requests"] = requests
    delay = _set_virtual_hall_auto_missing_check_after(flow, now, len(eligible))
    flow["updated_at"] = float(now or 0)
    _upsert_virtual_hall_auto_flow(flow)
    _schedule_virtual_hall_auto_deferred_team_check(flow.get("flow_id"), delay)
    return True


async def _handle_virtual_hall_auto_open_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if not raw_text.startswith(_VIRTUAL_HALL_OPEN_COMMAND):
        return False
    if not _is_replica_listener_self_event(event, listener_account_id):
        return True
    match = _VIRTUAL_HALL_AUTO_OPEN_COMMAND_RE.match(raw_text)
    if not match:
        await _send_replica_group_message(event.client, event.chat_id, "用法：.开启虚天殿 <身份>", listener_account_id=listener_account_id, log_text="用法：.开启虚天殿 <身份>")
        return True
    selector = str(match.group("selector") or "").strip()
    identity_id = resolve_identity_selector(selector)
    if identity_id is None:
        await _send_replica_group_message(event.client, event.chat_id, f"未找到身份：{selector}", listener_account_id=listener_account_id, log_text=f"未找到身份：{selector}")
        return True
    now = time.time()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if _has_active_virtual_hall_auto_flow(chat_id, identity_id, now):
        await _send_replica_group_message(event.client, event.chat_id, f"身份 {selector} 已有自动虚天殿流程进行中", listener_account_id=listener_account_id, log_text=f"身份 {selector} 已有自动虚天殿流程进行中")
        return True
    leader_username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
    flow_id = _make_virtual_hall_auto_flow_id(chat_id, identity_id, now)
    flow = {
        "flow_id": flow_id,
        "phase": "opening",
        "replica_chat_id": chat_id,
        "listener_account_id": int(listener_account_id or 0),
        "leader_identity_id": int(identity_id or 0),
        "leader_username": leader_username,
        "selector": selector,
        "replica_command_msg_id": int(getattr(event, "id", 0) or 0),
        "open_command_msg_id": 0,
        "open_requested_at": now,
        "kick_pending_usernames": {},
        "kick_results": {},
        "kicked_usernames": [],
        "expires_at": now + _VIRTUAL_HALL_AUTO_OPEN_TIMEOUT_SEC,
        "updated_at": now,
        "last_error": "",
    }
    _upsert_virtual_hall_auto_flow(flow)
    msg = await send_game_command(
        _VIRTUAL_HALL_OPEN_COMMAND,
        track=False,
        send_as_id=identity_id,
        **_replica_send_intent(
            op_id=f"virtual_hall_auto_open:{chat_id}:{int(getattr(event, 'id', 0) or 0)}:{identity_id}",
            chain_id=f"virtual_hall_auto:{flow_id}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    if msg_id <= 0:
        flow.update({"phase": "failed", "last_error": "开房命令发送失败", "expires_at": now + _VIRTUAL_HALL_AUTO_OPEN_DONE_TTL_SEC, "updated_at": time.time()})
        _upsert_virtual_hall_auto_flow(flow)
        await _send_virtual_hall_auto_replica_notice(flow, f".开启虚天殿 发送失败：{selector}")
        return True
    flow.update({"open_command_msg_id": msg_id, "updated_at": time.time()})
    _upsert_virtual_hall_auto_flow(flow)
    return True


async def _handle_virtual_hall_auto_dissolve_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if raw_text != _VIRTUAL_HALL_DISSOLVE_COMMAND:
        return False
    if not _is_replica_listener_self_event(event, listener_account_id):
        return True
    now = time.time()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    flow = _find_latest_virtual_hall_auto_flow(replica_chat_id=chat_id, phases={"waiting_dispatch", "monitoring"}, now=now)
    if not flow:
        await _send_replica_group_message(event.client, event.chat_id, "没有可解散的自动虚天殿副本", listener_account_id=listener_account_id, log_text="没有可解散的自动虚天殿副本")
        return True
    if await _request_virtual_hall_auto_dissolve(flow, [], now, manual=True):
        await _send_virtual_hall_auto_replica_notice(flow, "已发送解散副本命令")
    else:
        await _send_virtual_hall_auto_replica_notice(flow, "解散副本命令发送失败")
    return True


async def _handle_virtual_hall_auto_dispatch_observer(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    replica_kind, room_id, usernames = _parse_replica_dispatch_command(getattr(event, "raw_text", "") or "")
    if replica_kind != _REPLICA_KIND_VIRTUAL_HALL or not room_id or not usernames:
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    flow = _find_virtual_hall_auto_flow_by_room(room_id, replica_chat_id=chat_id, phases={"waiting_dispatch", "monitoring"})
    if not flow:
        return False
    now = time.time()
    _merge_virtual_hall_auto_dispatch_usernames(
        flow,
        usernames,
        now,
        msg_id=getattr(event, "id", 0),
        sender_id=getattr(event, "sender_id", 0),
    )
    delay = _set_virtual_hall_auto_missing_check_after(flow, now, len(_normalize_replica_username_list(usernames)))
    _upsert_virtual_hall_auto_flow(flow)
    _schedule_virtual_hall_auto_deferred_team_check(flow.get("flow_id"), delay)
    return True


async def _request_virtual_hall_auto_enter(flow, now):
    if flow.get("enter_requested_at"):
        return False
    flow_id = str(flow.get("flow_id") or "").strip()
    msg = await send_game_command(
        _VIRTUAL_HALL_ENTER_COMMAND,
        track=False,
        send_as_id=int(flow.get("leader_identity_id") or 0),
        **_replica_send_intent(
            op_id=f"virtual_hall_auto_enter:{flow_id}",
            chain_id=f"virtual_hall_auto:{flow_id}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    if msg_id <= 0:
        flow["last_error"] = "进入命令发送失败"
        flow["updated_at"] = now
        _upsert_virtual_hall_auto_flow(flow)
        return False
    flow.update({
        "enter_requested_at": now,
        "enter_msg_id": msg_id,
        "updated_at": now,
    })
    _upsert_virtual_hall_auto_flow(flow)
    await _send_virtual_hall_auto_replica_notice(flow, "已组队完成自动进入副本")
    return True


async def _request_virtual_hall_auto_dissolve(flow, plain_members, now, manual=False):
    if flow.get("dissolve_requested_at"):
        return False
    flow_id = str(flow.get("flow_id") or "").strip()
    msg = await send_game_command(
        _VIRTUAL_HALL_DISSOLVE_COMMAND,
        track=False,
        send_as_id=int(flow.get("leader_identity_id") or 0),
        **_replica_send_intent(
            op_id=f"virtual_hall_auto_dissolve:{flow_id}",
            chain_id=f"virtual_hall_auto:{flow_id}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    if msg_id <= 0:
        flow["last_error"] = "解散命令发送失败"
        flow["updated_at"] = now
        _upsert_virtual_hall_auto_flow(flow)
        if not manual:
            _schedule_virtual_hall_auto_audit("异常人员加入 解散副本命令发送失败")
        return False
    flow.update({
        "phase": "dissolving",
        "plain_members": list(plain_members or []),
        "dissolve_requested_at": now,
        "dissolve_msg_id": msg_id,
        "dissolve_audit_sent": bool(flow.get("dissolve_audit_sent")) or bool(manual),
        "expires_at": now + _VIRTUAL_HALL_AUTO_OPEN_TIMEOUT_SEC,
        "updated_at": now,
    })
    _upsert_virtual_hall_auto_flow(flow)
    return True


async def _apply_virtual_hall_auto_team_snapshot(flow, snapshot, now, allow_missing_actions=True):
    if not flow or str(flow.get("phase") or "") != "monitoring":
        return False
    plain_members = list((snapshot or {}).get("plain_members") or [])
    if plain_members:
        await _request_virtual_hall_auto_dissolve(flow, plain_members, now)
        return True
    usernames = _normalize_replica_username_list((snapshot or {}).get("usernames") or [])
    flow["observed_team_usernames"] = usernames
    flow["last_team_snapshot_usernames"] = usernames
    flow["last_team_snapshot_at"] = float(now or 0)
    accounting = _build_virtual_hall_auto_team_accounting(flow, usernames)
    pending = flow.get("kick_pending_usernames")
    if not isinstance(pending, dict):
        pending = {}
    kick_results = flow.get("kick_results")
    if not isinstance(kick_results, dict):
        kick_results = {}
    outsiders = _normalize_replica_username_list(accounting.get("outsiders") or [])
    observed_set = set(usernames)
    outsiders_set = set(outsiders)
    for username in list(pending):
        normalized_username = _normalize_replica_username(username)
        if normalized_username not in observed_set or normalized_username not in outsiders_set:
            pending.pop(username, None)
    needed_evictions = int(accounting.get("needed_evictions") or 0)
    if needed_evictions <= 0:
        pending = {}
    pending_usernames = _normalize_replica_username_list(pending.keys())
    if needed_evictions > 0 and pending_usernames:
        kick_candidates = [username for username in pending_usernames if username in outsiders_set]
    elif needed_evictions > 0:
        kick_candidates = []
        for username in outsiders:
            result = kick_results.get(username)
            if isinstance(result, dict) and result.get("reason") not in {"发送失败", "timeout"}:
                continue
            kick_candidates.append(username)
            if len(kick_candidates) >= needed_evictions:
                break
    else:
        kick_candidates = []
    last_kick_sent_at = float(flow.get("last_kick_command_sent_at") or 0)
    can_send_kick = last_kick_sent_at <= 0 or float(now or 0) >= last_kick_sent_at + _VIRTUAL_HALL_AUTO_KICK_COMMAND_INTERVAL_SEC
    kick_wait_delay = 0
    if kick_candidates and not can_send_kick:
        kick_wait_delay = max(0.1, last_kick_sent_at + _VIRTUAL_HALL_AUTO_KICK_COMMAND_INTERVAL_SEC - float(now or 0))
    for username in kick_candidates[:1] if can_send_kick else []:
        previous = pending.pop(username, None)
        previous = previous if isinstance(previous, dict) else {}
        flow_id = str(flow.get("flow_id") or "").strip()
        msg = await send_game_command(
            f"{_VIRTUAL_HALL_KICK_COMMAND} {username}",
            track=False,
            send_as_id=int(flow.get("leader_identity_id") or 0),
            **_replica_send_intent(
                op_id=f"virtual_hall_auto_kick:{flow_id}:{username}",
                chain_id=f"virtual_hall_auto:{flow_id}",
            ),
        )
        msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
        flow["last_kick_command_sent_at"] = float(now or 0)
        if len(kick_candidates) > 1:
            kick_wait_delay = max(kick_wait_delay, _VIRTUAL_HALL_AUTO_KICK_COMMAND_INTERVAL_SEC)
        if msg_id <= 0:
            kick_results[username] = {"status": "failed", "reason": "发送失败", "msg_id": 0, "updated_at": now}
            _schedule_virtual_hall_auto_audit(f"异常人员请离失败：{username}｜发送失败")
            continue
        previous_attempts = int(previous.get("attempts") or 0)
        if username == _normalize_replica_username(flow.get("last_kick_timeout_username") or ""):
            previous_attempts = max(previous_attempts, int(flow.get("last_kick_timeout_attempts") or 0))
        pending[username] = {"msg_id": msg_id, "sent_at": now, "attempts": previous_attempts + 1, "status": "pending"}
        flow.pop("last_kick_timeout_username", None)
        flow.pop("last_kick_timeout_attempts", None)
    flow["kick_pending_usernames"] = pending
    flow["kick_results"] = kick_results
    flow["updated_at"] = now
    _upsert_virtual_hall_auto_flow(flow)
    if kick_wait_delay > 0:
        _schedule_virtual_hall_auto_deferred_team_check(flow.get("flow_id"), kick_wait_delay)
        return True
    if pending:
        _schedule_virtual_hall_auto_deferred_team_check(flow.get("flow_id"), _VIRTUAL_HALL_AUTO_KICK_COMMAND_INTERVAL_SEC)
        return True
    missing_check_after = float(flow.get("missing_check_after") or 0)
    if not allow_missing_actions or (missing_check_after > 0 and float(now or 0) < missing_check_after):
        return True
    if await _maybe_send_virtual_hall_auto_missing_dispatch_command(flow, accounting, now):
        return True
    dispatch_usernames = list(accounting.get("dispatch_usernames") or [])
    if (
        len(dispatch_usernames) >= 4
        and not accounting.get("missing_dispatch")
        and not accounting.get("outsiders")
        and int(accounting.get("observed_count") or 0) == 5
        and not flow.get("enter_requested_at")
    ):
        await _request_virtual_hall_auto_enter(flow, now)
        return True
    if not accounting.get("missing_dispatch"):
        await _maybe_send_virtual_hall_auto_manual_enter_notice(flow, accounting, now)
    return True


async def _handle_virtual_hall_auto_game_event(event, text, now, reply_to=None, reply_context=None, event_type="message"):
    text = str(text or "")
    now = float(now or time.time())
    reply_to_msg_id = int((reply_context or {}).get("reply_to_msg_id") or _get_event_reply_header_msg_id(event) or 0)
    send_as_id = int((reply_context or {}).get("send_as_id") or 0)
    apply_replica_ticket_text_deltas(event, text, now, reply_context=reply_context)
    opened_match = _REPLICA_OPENED_RE.search(text)
    if opened_match:
        opened_kind_name = opened_match.group("opened_kind_name") or opened_match.group("opened_zhuimo") or opened_match.group("opened_huanglong") or opened_match.group("opened_cangkun") or text
        replica_kind = _infer_replica_kind_from_text(opened_kind_name)
        leader_username = _normalize_replica_username(opened_match.group("leader"))
        flow = _find_lightweight_open_flow(
            reply_to_msg_id=reply_to_msg_id,
            send_as_id=send_as_id,
            leader_username=leader_username,
            replica_kind=replica_kind,
            now=now,
        )
        if flow:
            room_id = str(opened_match.group("room_id") or "").strip()
            room = {
                "phase": "opened",
                "room_id": room_id,
                "replica_kind": replica_kind,
                "replica_chat_id": int(flow.get("replica_chat_id") or 0),
                "listener_account_id": int(flow.get("listener_account_id") or 0),
                "leader_identity_id": int(flow.get("leader_identity_id") or 0),
                "leader_username": leader_username,
                "opened_msg_id": int(getattr(event, "id", 0) or 0),
                "opened_at": now,
                "updated_at": now,
                "expires_at": now + _REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
            }
            _set_lightweight_last_room(room)
            _remove_lightweight_open_flow(flow.get("flow_id"))
            if not _mark_lightweight_room_recommendation_sent(room, now):
                return True
            if replica_kind == _REPLICA_KIND_VIRTUAL_HALL:
                await _send_lightweight_virtual_hall_recommendation(room, text, now)
            else:
                recommendation_text = _format_lightweight_profession_recommendation_section(
                    replica_kind,
                    int(room.get("leader_identity_id") or 0),
                    html=True,
                )
                await _send_lightweight_replica_notice(
                    room,
                    f"已记录{escape(_REPLICA_KIND_META[replica_kind]['name'])}房间 {escape(room_id)}。\n\n"
                    + recommendation_text
                    + "\n\n"
                    + _format_lightweight_next_commands(
                        ".加入副本 @用户名 @用户名",
                        ".解散副本",
                        html=True,
                    ),
                    html=True,
                )
            return True
    open_failure = _parse_lightweight_replica_open_failure(text)
    if open_failure:
        flow = _find_lightweight_open_flow(reply_to_msg_id=reply_to_msg_id, send_as_id=send_as_id, now=now)
        if flow:
            flow.update({"phase": "failed", "last_error": open_failure, "expires_at": now + 60, "updated_at": now})
            _upsert_lightweight_open_flow(flow)
            retry_command = _format_lightweight_open_command_for_identity(
                int(flow.get("leader_identity_id") or 0),
                flow.get("replica_kind") or "",
            )
            await _send_lightweight_replica_notice(
                flow,
                f"开启{escape(_REPLICA_KIND_META.get(flow.get('replica_kind'), {}).get('name') or '副本')}失败：{escape(open_failure)}\n\n"
                + _format_lightweight_next_commands(".查询副本", retry_command or ".开启副本 @用户名 <虚天|苍坤|坠魔|黄龙>", html=True),
                html=True,
            )
            _remove_lightweight_open_flow(flow.get("flow_id"))
            return True
    entered_kind = _parse_replica_entered_kind(text)
    if entered_kind:
        return bool(_mark_latest_lightweight_room_entered(entered_kind, now=now))
    dissolved_leader, dissolved_room_id = _parse_replica_room_dissolved(text)
    if dissolved_room_id:
        replica_kind = _resolve_replica_kind_for_progress(text, now, usernames=[dissolved_leader], room_id=dissolved_room_id)
        room = _find_lightweight_room_for_dissolve_notice(dissolved_room_id, leader_username=dissolved_leader, replica_kind=replica_kind, now=now)
        changed = _mark_lightweight_room_dissolved(dissolved_room_id, leader_username=dissolved_leader, replica_kind=replica_kind, now=now)
        if changed and room and not room.get("dissolve_confirm_notice_sent_at"):
            room.update({"dissolve_confirm_notice_sent_at": now, "phase": "dissolved", "dissolved_at": now})
            _set_lightweight_last_room(room)
            await _send_lightweight_replica_notice(
                room,
                _format_lightweight_dissolve_confirm_notice(dissolved_room_id, replica_kind=replica_kind, leader_username=dissolved_leader, raw_text=text, html=True),
                html=True,
            )
        return changed
    return False


async def _handle_replica_query_command(
    event,
    listener_account_id=None,
    claim_scope="replica_query",
    participant_identity_ids=None,
    participant_fallback_to_all=True,
):
    if listener_account_id is None:
        listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if raw_text != ".查询" and not raw_text.startswith(".查询 "):
        return False
    if not _claim_runtime_event(event, scope=claim_scope):
        return True
    query = raw_text[len(".查询"):].strip()
    reply_text = _format_replica_query_reply(
        query,
        participant_identity_ids=participant_identity_ids,
        fallback_to_all=participant_fallback_to_all,
    )
    event_message_id = _coerce_replica_metadata_int(getattr(event, "id", 0))
    if await _maybe_submit_replica_query_reply_to_aggregator(
        event_message_id,
        raw_text,
        query,
        reply_text,
        source_chat_id=_coerce_replica_metadata_int(getattr(event, "chat_id", 0)),
        source_message_id=event_message_id,
        listener_account_id=listener_account_id,
        identity_id=listener_account_id,
    ):
        return True
    await _send_replica_group_message(
        event.client,
        event.chat_id,
        reply_text,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=_strip_html_code_tags(reply_text),
    )
    return True


async def _submit_virtual_hall_match_text_to_aggregator(
    room_id,
    text,
    query_message_id=0,
    html=False,
    *,
    source_chat_id=0,
    source_message_id=0,
    listener_account_id=0,
):
    config = _get_replica_query_aggregator_submit_config()
    if not config:
        return False
    source_chat_id = _coerce_replica_metadata_int(source_chat_id)
    source_message_id = _coerce_replica_metadata_int(source_message_id or query_message_id)
    listener_account_id = _coerce_replica_metadata_int(listener_account_id)
    try:
        response = await submit_virtual_hall_recommendation(
            config=config,
            room_id=room_id,
            text=str(text or "") if html else mono(str(text or "")),
            query_message_id=query_message_id,
            source_id=source_chat_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            identity_id=listener_account_id,
            send_as_id=listener_account_id,
            listener_account_id=listener_account_id,
            client_id=config.get("client_id") or "",
            generated_at=time.time(),
        )
        message_ids = response.get("message_ids") or []
        console_log(f"已提交虚天殿推荐到汇聚服务：room={room_id} messages={len(message_ids)}", scope="global", limit=180)
        return True
    except Exception as exc:
        error_text = str(exc) or exc.__class__.__name__
        if isinstance(exc, ReplicaQueryAggregatorError):
            console_log(f"虚天殿推荐汇聚提交失败：{error_text}", scope="global", limit=240)
        else:
            console_log(f"虚天殿推荐汇聚提交异常：{error_text}", scope="global", limit=240)
    return False


async def _send_virtual_hall_match_text(client_obj, chat_id, text, listener_account_id=0, html=False, room_id="", query_message_id=0):
    if room_id and await _submit_virtual_hall_match_text_to_aggregator(
        room_id,
        text,
        query_message_id=query_message_id,
        html=html,
        source_chat_id=chat_id,
        source_message_id=query_message_id,
        listener_account_id=listener_account_id,
    ):
        return
    await _send_replica_group_message(
        client_obj,
        chat_id,
        text if html else mono(text),
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=_strip_html_code_tags(text) if html else text,
    )


async def _run_virtual_hall_match(room_id, client_obj, chat_id, listener_account_id=0):
    _cleanup_replica_room_gua_records(time.time())
    gua_record = _get_replica_room_gua_record(_REPLICA_KIND_VIRTUAL_HALL, room_id)
    if not gua_record:
        await _send_virtual_hall_match_text(client_obj, chat_id, f"未找到虚天殿 {room_id} 的卦象记录，请先等待包含【卦象词条】的开启消息。", listener_account_id=listener_account_id)
        return
    query_msg = await _send_replica_group_message(client_obj, chat_id, ".查询", listener_account_id=listener_account_id, log_text=".查询")
    if not query_msg:
        await _send_virtual_hall_match_text(client_obj, chat_id, f".查询 发送失败，无法推荐虚天殿 {room_id}。", listener_account_id=listener_account_id)
        return
    query_sent_at = time.time()
    query_message_id = int(getattr(query_msg, "id", 0) or 0)
    reply_text = _format_replica_query_reply("")
    if not reply_text.startswith("未找到") and reply_text != "当前没有已勾选且带 username 的副本参与身份":
        await _maybe_submit_replica_query_reply_to_aggregator(
            query_message_id,
            ".查询",
            "",
            reply_text,
            source_chat_id=chat_id,
            source_message_id=query_message_id,
            listener_account_id=listener_account_id,
            identity_id=listener_account_id,
        )
    query_wait_sec = _VIRTUAL_HALL_MATCH_QUERY_WAIT_SEC
    candidates = await _wait_replica_query_log_candidates(
        query_message_id,
        query_sent_at,
        timeout_sec=query_wait_sec,
        chat_id=chat_id,
    )
    if not candidates:
        await _send_virtual_hall_match_text(client_obj, chat_id, f"未在 {int(query_wait_sec)} 秒内解析到 .查询 结果，无法推荐虚天殿 {room_id}。", listener_account_id=listener_account_id)
        return
    recommendations = _build_virtual_hall_recommendations(gua_record, candidates, limit=1)
    result_text = _format_virtual_hall_recommendations(room_id, gua_record, recommendations, candidates)
    await _send_virtual_hall_match_text(client_obj, chat_id, result_text, listener_account_id=listener_account_id, html=True, room_id=room_id, query_message_id=query_message_id)


async def _handle_virtual_hall_match_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    match = _VIRTUAL_HALL_MATCH_COMMAND_RE.match(raw_text)
    if not match:
        return False
    if not is_replica_virtual_hall_match_enabled(int(getattr(event, "chat_id", 0) or 0)):
        return True
    if not _claim_runtime_event(event, scope="virtual_hall_match_command"):
        return True
    room_id = str(match.group("room_id") or "").strip()
    _fire_and_forget(
        _run_virtual_hall_match(
            room_id,
            getattr(event, "client", None),
            int(getattr(event, "chat_id", 0) or 0),
            listener_account_id=listener_account_id,
        )
    )
    return True


async def _send_lightweight_replica_notice(flow_or_room, text, *, html=False):
    item = flow_or_room if isinstance(flow_or_room, dict) else {}
    listener_account_id = int(item.get("listener_account_id") or 0)
    replica_chat_id = int(item.get("replica_chat_id") or 0)
    if listener_account_id <= 0 or replica_chat_id == 0:
        return False
    client_obj = get_all_clients().get(listener_account_id)
    if client_obj is None:
        return False
    return await _send_replica_group_message(
        client_obj,
        replica_chat_id,
        str(text or "") if html else mono(str(text or "")),
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=_strip_html_code_tags(text) if html else str(text or ""),
    )


async def _run_lightweight_room_auto_dissolve(room_snapshot, delay):
    await asyncio.sleep(max(0, float(delay or 0)))
    room_snapshot = room_snapshot if isinstance(room_snapshot, dict) else {}
    room_id = str(room_snapshot.get("room_id") or "").strip()
    replica_kind = room_snapshot.get("replica_kind")
    chat_id = int(room_snapshot.get("replica_chat_id") or 0)
    leader_identity_id = int(room_snapshot.get("leader_identity_id") or 0)
    if not room_id or replica_kind not in _REPLICA_KINDS or chat_id == 0 or leader_identity_id <= 0:
        return False
    now = time.time()
    current = _get_lightweight_last_room(chat_id, now=now)
    if (
        not current
        or str(current.get("room_id") or "").strip() != room_id
        or current.get("replica_kind") != replica_kind
        or int(current.get("leader_identity_id") or 0) != leader_identity_id
        or current.get("phase") in {"dissolved", "dissolve_requested"}
    ):
        return False
    reserve_status, current = _reserve_lightweight_room_dissolve(
        current,
        now,
        source="auto",
        source_msg_id=int(room_snapshot.get("recommendation_sent_opened_msg_id") or room_snapshot.get("opened_msg_id") or 0),
    )
    if reserve_status != "reserved":
        return False
    command = (_REPLICA_TICKET_META.get(replica_kind) or {}).get("dissolve_command") or _VIRTUAL_HALL_DISSOLVE_COMMAND
    blocked_reason = _get_replica_identity_block_reason(leader_identity_id, now=now)
    if blocked_reason:
        _finish_lightweight_room_dissolve_send(
            current,
            0,
            now,
            error=blocked_reason,
        )
        current.update({
            "auto_dissolve_reason": room_snapshot.get("auto_dissolve_reason") or "no_dps",
            "auto_dissolve_requested_at": current.get("auto_dissolve_requested_at"),
        })
        _set_lightweight_last_room(current)
        await _send_lightweight_replica_notice(
            current,
            f"无DPS可用，自动解散命令未发送：{escape(blocked_reason)}\n\n" + _format_lightweight_next_commands(".解散副本", html=True),
            html=True,
        )
        return False
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=leader_identity_id,
        priority="urgent_reactive",
        **_replica_send_intent(
            op_id=f"replica_lightweight_auto_dissolve:{chat_id}:{room_id}:{leader_identity_id}",
            chain_id=f"replica_lightweight_room:{replica_kind}:{room_id}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    _finish_lightweight_room_dissolve_send(
        current,
        msg_id,
        now,
        error="无DPS自动解散命令发送失败",
    )
    current.update({
        "auto_dissolve_reason": room_snapshot.get("auto_dissolve_reason") or "no_dps",
        "auto_dissolve_requested_at": now if msg_id > 0 else current.get("auto_dissolve_requested_at"),
    })
    _set_lightweight_last_room(current)
    if msg_id > 0:
        await _send_lightweight_replica_notice(
            current,
            f"无DPS可用，已发送 {mono(command)}，等待游戏确认解散。",
            html=True,
        )
    else:
        blocked_reason = _get_replica_identity_block_reason(leader_identity_id) or "发送失败"
        await _send_lightweight_replica_notice(current, f"无DPS可用，自动解散命令发送失败：{escape(blocked_reason)}\n\n" + _format_lightweight_next_commands(".解散副本", html=True), html=True)
    return msg_id > 0


def _schedule_lightweight_room_auto_dissolve(room, delay=_LIGHTWEIGHT_NO_DPS_AUTO_DISSOLVE_DELAY_SEC):
    room = room if isinstance(room, dict) else {}
    if not room.get("room_id") or int(room.get("leader_identity_id") or 0) <= 0:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    _fire_and_forget(_run_lightweight_room_auto_dissolve(dict(room), delay))
    return True


def _parse_lightweight_replica_open_failure(text):
    raw_text = str(text or "")
    if "你没有【虚天残图】" in raw_text or ("无法开启虚天殿" in raw_text and "残图" in raw_text):
        return "缺少虚天残图"
    if "你没有【苍坤残图】" in raw_text or ("无法开启苍坤" in raw_text and "残图" in raw_text):
        return "缺少苍坤残图"
    if "你没有【坠魔谷禁制令】" in raw_text or ("无法开启坠魔谷" in raw_text and "禁制令" in raw_text):
        return "缺少坠魔谷禁制令"
    if "你没有【黄龙急援令】" in raw_text or ("无法调动前线阵纹" in raw_text and "黄龙急援令" in raw_text):
        return "缺少黄龙急援令"
    if "你已经开启了一个副本房间" in raw_text or "请勿重复操作" in raw_text:
        return "已有副本房间"
    if "无法立即开启新副本" in raw_text and "后再试" in raw_text:
        wait_sec = parse_wait_time(raw_text)
        return f"开房冷却中：{fmt_time_after(wait_sec)}" if wait_sec > 0 else "开房冷却中"
    return ""


async def _send_lightweight_virtual_hall_recommendation(room, opened_text, now):
    room = room if isinstance(room, dict) else {}
    room_id = str(room.get("room_id") or "").strip()
    leader_username = _normalize_replica_username(room.get("leader_username") or "")
    if not room_id:
        return False
    candidates = _parse_replica_query_reply_text(_format_replica_query_reply(""))
    has_available_gold_dps = _has_available_virtual_hall_gold_dps(candidates)
    _mark_virtual_hall_gua_from_opened_text(
        opened_text,
        now,
        room_id,
        leader_username=leader_username,
        msg_id=room.get("opened_msg_id") or 0,
    )
    gua_record = _get_replica_room_gua_record(_REPLICA_KIND_VIRTUAL_HALL, room_id)
    if not gua_record:
        if not has_available_gold_dps and not room.get("auto_dissolve_scheduled_at"):
            room["auto_dissolve_reason"] = "no_dps"
            room["auto_dissolve_scheduled_at"] = float(now or time.time())
            _set_lightweight_last_room(room)
            _schedule_lightweight_room_auto_dissolve(room)
        return await _send_lightweight_replica_notice(
            room,
            f"已记录虚天殿房间 {room_id}，但未解析到卦象词条。\n\n"
            + ("无DPS可用，已安排 6 秒后自动解散。\n\n" if not has_available_gold_dps else "")
            + (
                _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=True)
                if has_available_gold_dps
                else _format_lightweight_next_commands(".解散副本", html=True)
            ),
            html=True,
        )
    recommendations = _build_virtual_hall_recommendations(gua_record, candidates, limit=1)
    if not has_available_gold_dps and not room.get("auto_dissolve_scheduled_at"):
        room["auto_dissolve_reason"] = "no_dps"
        room["auto_dissolve_scheduled_at"] = float(now or time.time())
        _set_lightweight_last_room(room)
        _schedule_lightweight_room_auto_dissolve(room)
    result_text = _format_virtual_hall_recommendations(
        room_id,
        gua_record,
        recommendations,
        candidates,
        lightweight=True,
        html=True,
    )
    if has_available_gold_dps:
        result_text += "\n\n" + _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=True)
    else:
        result_text += "\n\n" + _format_lightweight_next_commands(".解散副本", html=True)
    return await _send_lightweight_replica_notice(room, result_text, html=True)


async def _handle_replica_ticket_query_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if raw_text != ".查询副本":
        return False
    if not _claim_runtime_event(event, scope="replica_ticket_query"):
        return True
    reply_text = _format_replica_ticket_query_reply(html=True)
    await _send_replica_group_message(
        event.client,
        event.chat_id,
        reply_text,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=_strip_html_code_tags(reply_text),
    )
    return True


def _parse_lightweight_open_command(raw_text):
    match = _REPLICA_LIGHTWEIGHT_OPEN_COMMAND_RE.match(str(raw_text or "").strip())
    if not match:
        return "", ""
    rest = str(match.group("rest") or "").strip()
    if not rest:
        return "", ""
    selector = ""
    replica_kind = ""
    for token in re.split(r"\s+", rest):
        if not token:
            continue
        token_kind = _resolve_replica_kind_alias(token)
        if token_kind and not replica_kind:
            replica_kind = token_kind
            continue
        if not selector:
            selector = token
    return selector, replica_kind


async def _handle_lightweight_open_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if not _REPLICA_LIGHTWEIGHT_OPEN_COMMAND_RE.match(raw_text):
        return False
    if not _claim_runtime_event(event, scope="replica_lightweight_open"):
        return True
    selector, requested_kind = _parse_lightweight_open_command(raw_text)
    if not selector:
        text = "用法：.开启副本 @用户名 <虚天|苍坤|坠魔|黄龙>\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    identity_id = _resolve_replica_command_identity(selector)
    if identity_id <= 0 or not get_identity_enabled(identity_id):
        text = f"未找到可用身份：{escape(selector)}\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    replica_kind = _select_open_replica_kind(identity_id, requested_kind=requested_kind)
    if not replica_kind:
        ticket_text = _format_replica_ticket_counts(identity_id) or "无可用门票"
        requested_text = _REPLICA_KIND_META.get(requested_kind, {}).get("name") if requested_kind else "副本"
        reason_text = ticket_text
        openable_kinds = _get_openable_replica_kinds(identity_id)
        if not requested_kind and len(openable_kinds) > 1:
            open_commands = _format_lightweight_open_commands_for_identity(identity_id, html=True)
            text = (
                f"{escape(selector)} 有多种可开副本（{escape(ticket_text)}），请指定类型，避免默认误开虚天殿。\n\n"
                "可复制开房命令：\n"
                f"{open_commands}\n\n"
                + _format_lightweight_next_commands(".查询副本", html=True)
            )
            await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
            return True
        if (
            (requested_kind == _REPLICA_KIND_CANGKUN or not requested_kind)
            and _get_replica_ticket_kind_count(identity_id, _REPLICA_KIND_CANGKUN) > 0
            and not _is_cangkun_realm_available(identity_id)
        ):
            reason_text = f"{ticket_text}；{_format_cangkun_realm_requirement(identity_id)}"
        text = f"{escape(selector)} 不能开启{escape(requested_text)}：{escape(reason_text)}\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    now = time.time()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    leader_username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
    active_room = _get_active_lightweight_room(chat_id, now=now)
    if active_room:
        text = _format_lightweight_existing_room_notice(active_room, html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    active_flow = _find_active_lightweight_open_flow(chat_id, now=now)
    if active_flow:
        if _is_lightweight_open_flow_active(active_flow, now=now):
            text = _format_lightweight_existing_open_notice(active_flow, html=True)
            await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
            return True
        _remove_lightweight_open_flow(active_flow.get("flow_id"))
    flow = {
        "flow_id": _make_lightweight_flow_id(chat_id, identity_id, now),
        "phase": "opening",
        "replica_chat_id": chat_id,
        "listener_account_id": int(listener_account_id or 0),
        "leader_identity_id": int(identity_id or 0),
        "leader_username": leader_username,
        "replica_kind": replica_kind,
        "selector": selector,
        "replica_command_msg_id": int(getattr(event, "id", 0) or 0),
        "open_command_msg_id": 0,
        "open_requested_at": now,
        "updated_at": now,
        "expires_at": now + _REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC,
        "last_error": "",
    }
    _upsert_lightweight_open_flow(flow)
    command = (_REPLICA_TICKET_META.get(replica_kind) or {}).get("open_command")
    blocked_reason = _get_replica_identity_block_reason(identity_id, now=now)
    if blocked_reason:
        _remove_lightweight_open_flow(flow.get("flow_id"))
        text = (
            f"{escape(command)} 未发送：{escape(selector)}（{escape(blocked_reason)}）\n\n"
            + _format_lightweight_next_commands(_format_lightweight_open_command_for_identity(identity_id, replica_kind), html=True)
        )
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=identity_id,
        priority="urgent_reactive",
        **_replica_send_intent(
            op_id=f"replica_lightweight_open:{chat_id}:{int(getattr(event, 'id', 0) or 0)}:{identity_id}",
            chain_id=f"replica_lightweight_open:{replica_kind}:{flow['flow_id']}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    if msg_id <= 0:
        _remove_lightweight_open_flow(flow.get("flow_id"))
        blocked_reason = _get_replica_identity_block_reason(identity_id) or "发送失败"
        text = f"{escape(command)} 发送失败：{escape(selector)}（{escape(blocked_reason)}）\n\n" + _format_lightweight_next_commands(_format_lightweight_open_command_for_identity(identity_id, replica_kind), html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    flow.update({"open_command_msg_id": msg_id, "updated_at": time.time()})
    _upsert_lightweight_open_flow(flow)
    text = (
        f"已用 {escape(leader_username or selector)} 发送 {escape(command)}，等待开房广播。\n\n"
        + _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=True)
    )
    await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
    return True


def _parse_lightweight_join_usernames(raw_text):
    match = _REPLICA_LIGHTWEIGHT_JOIN_COMMAND_RE.match(str(raw_text or "").strip())
    if not match:
        return []
    rest = str(match.group("rest") or "").strip()
    if not rest:
        return []
    selectors = []
    seen = set()
    tokens = [token for token in re.split(r"[\s,，、]+", rest) if token]
    for token in tokens:
        normalized = str(token or "").strip()
        key = normalized.lstrip("@").casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        selectors.append(normalized)
    return selectors


async def _handle_lightweight_join_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if not _REPLICA_LIGHTWEIGHT_JOIN_COMMAND_RE.match(raw_text):
        return False
    if not _claim_runtime_event(event, scope="replica_lightweight_join"):
        return True
    room = _get_lightweight_last_room(int(getattr(event, "chat_id", 0) or 0), now=time.time())
    if not room:
        text = "没有已记录的副本房间，请先开房。\n\n" + _format_lightweight_next_commands(".查询副本", ".开启副本 @用户名 <虚天|苍坤|坠魔|黄龙>", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    selectors = _parse_lightweight_join_usernames(raw_text)
    if not selectors:
        text = "用法：.加入副本 @用户名 @用户名\n\n" + _format_lightweight_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    replica_kind = room.get("replica_kind")
    room_id = str(room.get("room_id") or "").strip()
    command = f"{_REPLICA_KIND_META[replica_kind]['join_command']} {room_id}"
    leader_identity_id = int(room.get("leader_identity_id") or 0)
    sent_usernames = []
    skipped = []
    seen_identity_ids = set()
    for selector in selectors:
        identity_id = _resolve_replica_command_identity(selector)
        if identity_id <= 0:
            skipped.append(_format_replica_skipped_selector(selector, "未找到身份"))
            continue
        if not get_identity_enabled(identity_id):
            skipped.append(_format_replica_skipped_selector(selector, "身份未启用"))
            continue
        if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
            skipped.append(_format_replica_skipped_selector(selector, _format_cangkun_realm_requirement(identity_id)))
            continue
        if identity_id == leader_identity_id or identity_id in seen_identity_ids:
            continue
        seen_identity_ids.add(identity_id)
        blocked_reason = _get_replica_identity_block_reason(identity_id)
        if blocked_reason:
            skipped.append(_format_replica_skipped_selector(selector, blocked_reason))
            continue
        msg = await send_game_command(
            command,
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
            **_replica_send_intent(
                op_id=f"replica_lightweight_join:{int(getattr(event, 'chat_id', 0) or 0)}:{int(getattr(event, 'id', 0) or 0)}:{identity_id}",
                chain_id=f"replica_lightweight_room:{replica_kind}:{room_id}",
            ),
        )
        if msg:
            sent_usernames.append(_normalize_replica_username(get_send_as_profile(identity_id).get("username") or selector))
        else:
            blocked_reason = _get_replica_identity_block_reason(identity_id) or "发送失败"
            skipped.append(_format_replica_skipped_selector(selector, blocked_reason))
    room["join_requested_usernames"] = _normalize_replica_username_list((room.get("join_requested_usernames") or []) + sent_usernames)
    room["updated_at"] = time.time()
    _set_lightweight_last_room(room)
    action_label = "已发送加入" if sent_usernames else "未发送加入"
    summary = f"{action_label}{_REPLICA_KIND_META[replica_kind]['name']} {room_id}：{' '.join(sent_usernames) if sent_usernames else '无'}"
    if skipped:
        summary += f"\n未发送：{' '.join(skipped)}"
    summary = escape(summary)
    summary += "\n\n" + _format_lightweight_next_commands(".解散副本", html=True)
    await _send_replica_group_message(event.client, event.chat_id, summary, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(summary))
    return True


async def _handle_lightweight_dissolve_command(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if raw_text != ".解散副本":
        return False
    if not _claim_runtime_event(event, scope="replica_lightweight_dissolve"):
        return True
    now = time.time()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    room = _get_lightweight_last_room(chat_id, now=now)
    if not room:
        active_flow = _find_active_lightweight_open_flow(chat_id, now=now)
        if active_flow:
            _remove_lightweight_open_flow(active_flow.get("flow_id"))
            text = _format_lightweight_cancel_open_notice(active_flow, html=True)
            await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
            return True
        text = "没有已记录的副本房间可解散。\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    replica_kind = room.get("replica_kind")
    command = (_REPLICA_TICKET_META.get(replica_kind) or {}).get("dissolve_command")
    leader_identity_id = int(room.get("leader_identity_id") or 0)
    if leader_identity_id <= 0:
        text = "已记录副本房间，但缺少开房身份，不能自动解散。\n\n" + _format_lightweight_next_commands(".查询副本", ".开启副本 @用户名 <虚天|苍坤|坠魔|黄龙>", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    reserve_status, room = _reserve_lightweight_room_dissolve(
        room,
        now,
        source="manual",
        source_msg_id=int(getattr(event, "id", 0) or 0),
    )
    if reserve_status == "pending":
        text = _format_lightweight_dissolve_pending_notice(room, html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    if reserve_status == "closed":
        text = "该副本房间已结束，未重复发送解散命令。\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    if reserve_status != "reserved":
        text = "没有已记录的副本房间可解散。\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    blocked_reason = _get_replica_identity_block_reason(leader_identity_id, now=now)
    if blocked_reason:
        _finish_lightweight_room_dissolve_send(room, 0, time.time(), error=blocked_reason)
        text = f"{escape(command)} 未发送：{escape(blocked_reason)}\n\n" + _format_lightweight_next_commands(".解散副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=leader_identity_id,
        priority="urgent_reactive",
        **_replica_send_intent(
            op_id=f"replica_lightweight_dissolve:{int(getattr(event, 'chat_id', 0) or 0)}:{int(getattr(event, 'id', 0) or 0)}:{leader_identity_id}",
            chain_id=f"replica_lightweight_room:{replica_kind}:{str(room.get('room_id') or '').strip()}",
        ),
    )
    if not msg:
        blocked_reason = _get_replica_identity_block_reason(leader_identity_id) or "解散命令发送失败"
        _finish_lightweight_room_dissolve_send(room, 0, time.time(), error=blocked_reason)
        text = f"{escape(command)} 发送失败：{escape(blocked_reason)}\n\n" + _format_lightweight_next_commands(".解散副本", html=True)
        await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
        return True
    _finish_lightweight_room_dissolve_send(room, int(getattr(msg, "id", 0) or 0), time.time())
    leader_username = room.get("leader_username") or get_send_as_profile(leader_identity_id).get("username") or str(leader_identity_id)
    text = f"已用 {escape(str(leader_username))} 发送 {escape(str(command))}，房间 {escape(str(room.get('room_id') or '-'))}，等待游戏确认解散。\n\n" + _format_lightweight_next_commands(".查询副本", html=True)
    await _send_replica_group_message(event.client, event.chat_id, text, parse_mode="html", listener_account_id=listener_account_id, log_text=_strip_html_code_tags(text))
    return True


async def _handle_replica_join_reply(text, now, reply_to, matched_family=None, event=None):
    reply_command = str(getattr(reply_to, "raw_text", "") or "").strip()
    if matched_family != "replica_join" and not _parse_replica_join_room_id(reply_command):
        return False
    parsed = _parse_replica_join_reply(text, reply_to)
    kind = parsed.get("kind")
    if kind == "unknown":
        return False
    reply_context = get_reply_context(reply_to, send_as_id=None)
    identity_id = int((reply_context or {}).get("send_as_id") or 0)
    if identity_id <= 0:
        identity_id = _find_replica_identity_id_by_reply_sender(reply_to)
    if identity_id <= 0:
        identity_id = _find_replica_identity_id_by_reply_sender(event)
    if identity_id <= 0:
        return True
    msg_id = int(getattr(event, "id", 0) or 0)
    if kind == "joined":
        _mark_replica_join_success(identity_id, parsed.get("room_id"), parsed.get("team_usernames") or [], now, msg_id=msg_id, replica_kind=parsed.get("replica_kind") or _REPLICA_KIND_VIRTUAL_HALL)
    elif kind == "not_joined":
        _mark_replica_join_not_joined(identity_id, parsed.get("room_id"), parsed.get("reason"), now, msg_id=msg_id, replica_kind=parsed.get("replica_kind") or _REPLICA_KIND_VIRTUAL_HALL)
    elif kind == "cooldown":
        _mark_replica_join_cooldown(identity_id, parsed.get("room_id"), parsed.get("wait_sec") or 0, now, msg_id=msg_id, replica_kind=parsed.get("replica_kind") or _REPLICA_KIND_VIRTUAL_HALL)
    return True


def _normalize_replica_username(username):
    username = str(username or "").strip()
    if not username:
        return ""
    if not username.startswith("@"):
        username = f"@{username}"
    return username.lower()


def _get_enabled_replica_identity_ids_by_username(participant_identity_ids=None, fallback_to_all=True):
    identity_ids_by_username = {}
    for identity_id in _get_replica_candidate_identity_ids(
        require_username=True,
        participant_identity_ids=participant_identity_ids,
        fallback_to_all=fallback_to_all,
    ):
        username = _normalize_replica_username(get_send_as_profile(identity_id).get("username") or "")
        identity_ids_by_username[username] = identity_id
    return identity_ids_by_username


def _parse_replica_dispatch_command(raw_text):
    match = _REPLICA_DISPATCH_COMMAND_RE.match(str(raw_text or "").strip())
    if not match:
        return "", "", []
    replica_kind = _get_replica_kind_by_dispatch_command(match.group("command"))
    replica_id = str(match.group("room_id") or "").strip()
    usernames = []
    seen = set()
    for username in _REPLICA_USERNAME_RE.findall(match.group("rest") or ""):
        normalized_username = _normalize_replica_username(username)
        if normalized_username and normalized_username not in seen:
            seen.add(normalized_username)
            usernames.append(normalized_username)
    return replica_kind, replica_id, usernames


def _reserve_external_dispatch_join(identity_id, replica_kind, room_id, event, now):
    identity_id = int(identity_id or 0)
    room_id = str(room_id or "").strip()
    if identity_id <= 0 or replica_kind not in _REPLICA_KINDS or not room_id:
        return False, "invalid"
    if not get_identity_enabled(identity_id):
        return False, "disabled"
    if replica_kind == _REPLICA_KIND_CANGKUN and not _is_cangkun_realm_available(identity_id):
        return False, _format_cangkun_realm_requirement(identity_id)
    records = _cleanup_replica_run_state(now)
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    cooldown_until = float(state_item.get("cooldown_until") or 0)
    if cooldown_until > float(now or 0):
        return False, "cooldown"
    active_until = _get_replica_active_until(record, replica_kind)
    if state_item.get("participating") and active_until > float(now or 0):
        return False, "participating"
    dispatch_pending_until = float(state_item.get("dispatch_pending_until") or 0)
    if dispatch_pending_until > float(now or 0):
        return False, "pending"
    state_item.update({
        "dispatch_pending_room_id": room_id,
        "dispatch_pending_until": float(now or 0) + _REPLICA_EXTERNAL_DISPATCH_PENDING_SEC,
        "dispatch_pending_source_chat_id": int(getattr(event, "chat_id", 0) or 0),
        "dispatch_pending_source_msg_id": int(getattr(event, "id", 0) or 0),
        "dispatch_retry_count": 0,
    })
    record.update({
        "replica_kind": replica_kind,
        "last_join_result": "pending",
        "last_join_error": "",
        "updated_at": float(now or 0),
    })
    _save_replica_run_records(records)
    return True, ""


def _mark_external_dispatch_join_sent(identity_id, replica_kind, room_id, msg_id, now, retry_count=None):
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    if retry_count is None:
        retry_count = int(state_item.get("dispatch_retry_count") or 0)
    state_item.update({
        "room_id": str(room_id or state_item.get("room_id") or ""),
        "dispatch_pending_msg_id": int(msg_id or 0),
        "dispatch_pending_until": float(now or 0) + _REPLICA_EXTERNAL_DISPATCH_PENDING_SEC,
        "dispatch_retry_count": max(0, int(retry_count or 0)),
    })
    record.update({
        "replica_kind": replica_kind,
        "last_join_msg_id": int(msg_id or 0),
        "last_join_result": "pending",
        "last_join_error": "",
        "updated_at": float(now or 0),
    })
    _save_replica_run_records(records)


def _clear_external_dispatch_join_pending(identity_id, replica_kind, room_id="", source_msg_id=0):
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    if room_id and str(state_item.get("dispatch_pending_room_id") or "") != str(room_id):
        return
    if source_msg_id and int(state_item.get("dispatch_pending_source_msg_id") or 0) != int(source_msg_id or 0):
        return
    _clear_replica_dispatch_pending_fields(state_item)
    record["updated_at"] = time.time()
    _save_replica_run_records(records)


def _should_fast_retry_external_dispatch(identity_id, replica_kind, room_id, source_msg_id, sent_msg_id, now):
    records = _cleanup_replica_run_state(now)
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=False)
    if str(state_item.get("dispatch_pending_room_id") or "") != str(room_id or ""):
        return False
    if source_msg_id and int(state_item.get("dispatch_pending_source_msg_id") or 0) != int(source_msg_id or 0):
        return False
    if sent_msg_id and int(state_item.get("dispatch_pending_msg_id") or 0) != int(sent_msg_id or 0):
        return False
    if int(state_item.get("dispatch_retry_count") or 0) >= _REPLICA_EXTERNAL_DISPATCH_FAST_RETRY_LIMIT:
        return False
    if float(state_item.get("dispatch_pending_until") or 0) <= float(now or 0):
        return False
    if state_item.get("participating") and _get_replica_active_until(record, replica_kind) > float(now or 0):
        return False
    if float(state_item.get("cooldown_until") or 0) > float(now or 0):
        return False
    return True


def _mark_external_dispatch_retry_used(identity_id, replica_kind, retry_count, now):
    records = _get_replica_run_records()
    record = _get_replica_identity_record(records, identity_id)
    state_item = _get_replica_kind_state(record, replica_kind, create=True)
    state_item["dispatch_retry_count"] = max(int(state_item.get("dispatch_retry_count") or 0), int(retry_count or 0))
    record["updated_at"] = float(now or 0)
    _save_replica_run_records(records)


async def _retry_external_dispatch_join_once(identity_id, replica_kind, room_id, command, source_msg_id, first_msg_id, delay_sec=None):
    delay_sec = _REPLICA_EXTERNAL_DISPATCH_FAST_RETRY_DELAY_SEC if delay_sec is None else max(0, float(delay_sec or 0))
    await asyncio.sleep(delay_sec)
    now = time.time()
    if not _should_fast_retry_external_dispatch(identity_id, replica_kind, room_id, source_msg_id, first_msg_id, now):
        return False
    retry_count = 1
    _mark_external_dispatch_retry_used(identity_id, replica_kind, retry_count, now)
    msg = await send_game_command(
        command,
        track=False,
        send_as_id=identity_id,
        priority="urgent_reactive",
        source_module="自动副本",
        op_id=f"replica_external_dispatch_retry:{int(source_msg_id or 0)}:{identity_id}:{retry_count}",
        chain_id=f"replica_external_dispatch:{replica_kind}:{room_id}",
        delete_policy="keep",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if msg:
        _mark_external_dispatch_join_sent(
            identity_id,
            replica_kind,
            room_id,
            int(getattr(msg, "id", 0) or 0),
            sent_at,
            retry_count=retry_count,
        )
        await send_audit_log(
            f"🧩 主线拉人快补发：{command}｜retry={retry_count}",
            scope="identity",
            send_as_id=identity_id,
            limit=180,
        )
        return True
    return False


def _schedule_external_dispatch_fast_retry(identity_id, replica_kind, room_id, command, source_msg_id, first_msg_id):
    _fire_and_forget(
        _retry_external_dispatch_join_once(
            identity_id,
            replica_kind,
            room_id,
            command,
            source_msg_id,
            first_msg_id,
        )
    )


async def _handle_replica_external_dispatch_command(event, participant_identity_ids=None, participant_fallback_to_all=True):
    listener_account_id = _get_replica_dispatch_event_listener_account_id(event)
    if not listener_account_id:
        return False
    replica_kind, replica_id, usernames = _parse_replica_dispatch_command(getattr(event, "raw_text", "") or "")
    if not replica_kind or not replica_id:
        return False
    if not _claim_runtime_event(event, scope="replica_external_dispatch"):
        return True
    if not usernames:
        return True
    identity_ids_by_username = _get_enabled_replica_identity_ids_by_username(
        participant_identity_ids=participant_identity_ids,
        fallback_to_all=participant_fallback_to_all,
    )
    command = f"{_REPLICA_KIND_META[replica_kind]['join_command']} {replica_id}"
    seen_identity_ids = set()
    sent_usernames = []
    skipped = []
    now = time.time()
    started_at = time.monotonic()
    for username in usernames:
        identity_id = identity_ids_by_username.get(username)
        if not identity_id or identity_id in seen_identity_ids:
            skipped.append(_format_replica_skipped_selector(username, "未找到身份" if not identity_id else "重复身份"))
            continue
        seen_identity_ids.add(identity_id)
        blocked_reason = _get_replica_identity_block_reason(identity_id, now=now)
        if blocked_reason:
            skipped.append(_format_replica_skipped_selector(username, blocked_reason))
            continue
        allowed, reason = _reserve_external_dispatch_join(identity_id, replica_kind, replica_id, event, now)
        if not allowed:
            skipped.append(_format_replica_skipped_selector(username, reason))
            continue
        delay_sec = max(0, len(sent_usernames)) * _REPLICA_EXTERNAL_DISPATCH_COMMAND_INTERVAL_SEC
        wait_sec = delay_sec - (time.monotonic() - started_at)
        if wait_sec > 0:
            await asyncio.sleep(wait_sec)
        msg = await send_game_command(
            command,
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
            source_module="自动副本",
            op_id=f"replica_external_dispatch:{int(getattr(event, 'chat_id', 0) or 0)}:{int(getattr(event, 'id', 0) or 0)}:{identity_id}",
            chain_id=f"replica_external_dispatch:{replica_kind}:{replica_id}",
            delete_policy="keep",
        )
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if msg:
            sent_msg_id = int(getattr(msg, "id", 0) or 0)
            _mark_external_dispatch_join_sent(identity_id, replica_kind, replica_id, sent_msg_id, sent_at)
            _schedule_external_dispatch_fast_retry(
                identity_id,
                replica_kind,
                replica_id,
                command,
                int(getattr(event, "id", 0) or 0),
                sent_msg_id,
            )
            sent_usernames.append(username)
            await send_audit_log(
                (
                    f"🧩 主线拉人已发出：{command}｜{username}"
                    f"｜来源群 {int(getattr(event, 'chat_id', 0) or 0)} msg {int(getattr(event, 'id', 0) or 0)}"
                ),
                scope="identity",
                send_as_id=identity_id,
                limit=240,
            )
        else:
            _clear_external_dispatch_join_pending(
                identity_id,
                replica_kind,
                replica_id,
                source_msg_id=int(getattr(event, "id", 0) or 0),
            )
            blocked_reason = _get_replica_identity_block_reason(identity_id) or "发送失败"
            skipped.append(_format_replica_skipped_selector(username, blocked_reason))
    if skipped and not sent_usernames:
        skipped_text = " ".join(skipped[:8])
        if any("发送失败" in item for item in skipped):
            console_log(
                f"🧩 主线拉人发送失败：{_REPLICA_KIND_META[replica_kind]['name']} {replica_id}｜{skipped_text}",
                scope="global",
                limit=240,
            )
            return True
        console_log(
            f"🧩 主线拉人已跳过：{_REPLICA_KIND_META[replica_kind]['name']} {replica_id}｜{skipped_text}",
            scope="global",
            limit=240,
        )
    return True


async def _handle_replica_dispatch_group_command(event):
    listener_account_id = _get_replica_dispatch_event_listener_account_id(event)
    if not listener_account_id:
        return False
    participant_identity_ids = get_replica_dispatch_participant_identity_ids()
    handled = await _handle_replica_query_command(
        event,
        listener_account_id=listener_account_id,
        claim_scope="replica_dispatch_query",
        participant_identity_ids=participant_identity_ids,
        participant_fallback_to_all=False,
    )
    if handled:
        return True
    return await _handle_replica_external_dispatch_command(
        event,
        participant_identity_ids=participant_identity_ids,
        participant_fallback_to_all=False,
    )


async def _handle_replica_dispatch_command(event):
    if not _get_replica_event_listener_account_id(event):
        return False
    replica_kind, replica_id, usernames = _parse_replica_dispatch_command(getattr(event, "raw_text", "") or "")
    if not replica_kind or not replica_id:
        return False
    if not usernames:
        return True
    identity_ids_by_username = _get_enabled_replica_identity_ids_by_username()
    seen_identity_ids = set()
    command = f"{_REPLICA_KIND_META[replica_kind]['join_command']} {replica_id}"
    for username in usernames:
        identity_id = identity_ids_by_username.get(username)
        if not identity_id or identity_id in seen_identity_ids:
            continue
        seen_identity_ids.add(identity_id)
        await send_game_command(
            command,
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
            **_replica_send_intent(
                op_id=f"replica_dispatch:{int(getattr(event, 'chat_id', 0) or 0)}:{int(getattr(event, 'id', 0) or 0)}:{identity_id}",
                chain_id=f"replica_dispatch:{replica_kind}:{replica_id}",
            ),
        )
    return True


async def _handle_legacy_replica_dispatch_notice(event):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    replica_kind, replica_id, usernames = _parse_replica_dispatch_command(getattr(event, "raw_text", "") or "")
    if not replica_kind or not replica_id:
        return False
    if not _claim_runtime_event(event, scope="replica_legacy_dispatch_notice"):
        return True

    now = time.time()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    _set_lightweight_last_room({
        "phase": "legacy_dispatch_seen",
        "room_id": replica_id,
        "replica_kind": replica_kind,
        "replica_chat_id": chat_id,
        "listener_account_id": int(listener_account_id or 0),
        "leader_identity_id": 0,
        "leader_username": "",
        "legacy_dispatch_msg_id": int(getattr(event, "id", 0) or 0),
        "opened_at": now,
        "updated_at": now,
        "expires_at": now + _REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
    })

    replica_name = _REPLICA_KIND_META[replica_kind]["name"]
    join_command = ".加入副本 " + " ".join(usernames) if usernames else ".加入副本 @用户名 @用户名"
    text = (
        f"旧副本调度已关闭，未自动批量发送加入。\n"
        f"已记录{escape(replica_name)}房间 {escape(replica_id)}，请改用轻量流程：\n\n"
        + _format_lightweight_next_commands(".查询副本", join_command, ".解散副本", html=True)
        + "\n\n提示：.解散副本 需要已记录开房身份；旧调度只记录房间号，缺开房身份时会拒绝自动解散。"
    )
    await _send_replica_group_message(
        event.client,
        event.chat_id,
        text,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=_strip_html_code_tags(text),
    )
    return True


async def _handle_replica_group_command(event):
    handled = await _handle_replica_query_command(event)
    handled = await _handle_replica_ticket_query_command(event) or handled
    handled = await _handle_virtual_hall_match_command(event) or handled
    handled = await _handle_lightweight_open_command(event) or handled
    handled = await _handle_lightweight_join_command(event) or handled
    handled = await _handle_lightweight_dissolve_command(event) or handled
    handled = await _handle_legacy_replica_dispatch_notice(event) or handled
    return handled


__all__ = [
    "_handle_replica_dispatch_group_command",
    "_handle_replica_external_dispatch_command",
    "_handle_replica_group_command",
    "_handle_replica_join_reply",
    "_handle_replica_progress_event",
    "_handle_virtual_hall_auto_game_event",
    "_mark_replica_team_joined_from_text",
]
