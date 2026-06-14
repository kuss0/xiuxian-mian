import asyncio
import html
import json
import os
import random
import secrets
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote

import requests
from telethon import functions, types
from telethon.errors import FloodWaitError

from .config import (
    CMD_BATTLE_POWER,
    CMD_CHECKIN,
    CMD_CONCUBINE_DAILY_GREET,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_GIFT_STONE,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_HEART_STEADY,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
    CMD_FORMATION_ASSIST,
    CMD_FORMATION_START,
    CMD_HEHUAN_CONTRACT,
    CMD_HEHUAN_DUAL,
    CMD_HEHUAN_ESCAPE,
    CMD_HEHUAN_RETREAT,
    CMD_HEHUAN_SEAL,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_HELP,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_DIVINATION,
    CMD_DIVINATION_EXCHANGE,
    CMD_EXPLORE_RIFT,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_IDENTITY_INFO,
    CMD_NANLONG_EXCHANGE_FABAO,
    CMD_NANLONG_EXCHANGE_GONGFA,
    CMD_NANLONG_REJECT,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_PET,
    CMD_PET_WARM,
    CMD_PET_TRIAL,
    CMD_QUIZ_ANSWER,
    CMD_RANCH,
    CMD_REPLICA_CANGKUN_JOIN,
    CMD_REPLICA_HUANGLONG_JOIN,
    CMD_REPLICA_JOIN,
    CMD_REPLICA_KUNWU_JOIN,
    CMD_REPLICA_LUOYUN_JOIN,
    CMD_REPLICA_ZHUIMO_JOIN,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
    CMD_SECT_TEACH,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_RELIEF,
    CMD_SMALL_WORLD_REFINE,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TIANDAO_JUDGEMENT_PROVE,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_PULSE,
    CMD_TREE_PULSE_STATUS,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_WILD_TRAINING,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_CURSE,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_GUIDE,
    CMD_YINLUO_POSSESS,
    CMD_YINLUO_REFINE,
    CMD_QINGYUANZI_ATTACK,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_WORLD_BOSS_STATUS,
    CMD_YINDAO,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    CMD_WENDAO,
    LOG_BOT_TOKEN,
    LOG_GROUP_ID,
    LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC,
    LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS,
    LOG_SEND_MODE,
    TG_REQUESTS_PROXIES,
    ADMIN_IDS,
    MESSAGES_DIR,
    MY_MSG_MAX,
    MY_MSG_TTL,
    RETRY_LIMIT,
    RETRY_MAX_SEC,
    RETRY_MIN_SEC,
    SCRIPT_COMMANDS,
    TZ_LOCAL,
    STATE_DIR,
    UI_AUTH_IDLE_TIMEOUT_SEC,
    UI_AUTH_SESSION_TIMEOUT_SEC,
    UI_PUBLIC_BASE_URL,
    client,
    get_account_offline_reason,
    get_all_clients,
    get_registered_client,
    is_account_offline,
    is_identity_refresh_command_text,
    mark_account_offline,
)
from .persistence import mark_dirty
from .log_retention import cleanup_message_logs
from .timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time
from .action_guard import (
    before_send as action_guard_before_send,
    get_next_allowed_at as action_guard_next_allowed_at,
    is_guarded_command as action_guard_is_guarded_command,
    note_sent as action_guard_note_sent,
    should_log_block as action_guard_should_log_block,
)
from .features.dungeon_quiet import (
    format_dungeon_quiet_until,
    get_dungeon_quiet_reason,
    is_dungeon_quiet_active,
    should_log_dungeon_quiet_block,
)
from .module_manifest import get_module_name_for_reply_family
from .state import (
    get_active_identity_id,
    get_current_identity_id,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_global_enabled,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_pending_command,
    get_send_as_label,
    has_active_identity_context,
    has_identity,
    is_auto_delete_sent_messages_enabled,
    state,
    use_identity,
)


def _get_any_authed_client():
    """返回任意一个已认证的 client（优先账号 client，回退主 client）"""
    for account_id, tc in get_all_clients().items():
        if not is_account_offline(account_id):
            return tc
    return client


def _get_identity_client(send_as_id=None):
    """根据 identity 返回对应的已认证 client"""
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    account_id = get_identity_account(send_as_id)
    if account_id and not is_account_offline(account_id):
        tc = get_registered_client(account_id)
        if tc is not None:
            return tc
    return _get_any_authed_client()


_background_tasks = set()


# ============== 全局发送通道（防多号同步特征 + GM 检测） ==============
# 所有游戏群指令共用一条发送通道；P0/chain 只缩短间隔，不绕开通道。
SEND_PRIORITY_P0 = "p0"
SEND_PRIORITY_CHAIN = "chain"
SEND_PRIORITY_REACTIVE = "reactive"
SEND_PRIORITY_URGENT_REACTIVE = "urgent_reactive"
SEND_PRIORITY_EVENT_BURST = "event_burst"
SEND_PRIORITY_RETRY = "retry"
SEND_PRIORITY_PROBE = "probe"
SEND_PRIORITY_NORMAL = "normal"

P0_COMMAND_PREFIXES = (".验证", CMD_TIANDAO_JUDGEMENT_PROVE, CMD_QUIZ_ANSWER)

P0_SEND_GAP_MIN_SEC = 20.0
P0_SEND_GAP_MAX_SEC = 30.0
CHAIN_SEND_GAP_MIN_SEC = 20.0
CHAIN_SEND_GAP_MAX_SEC = 35.0
REACTIVE_SEND_GAP_MIN_SEC = 14.0
REACTIVE_SEND_GAP_MAX_SEC = 22.0
URGENT_REACTIVE_SEND_GAP_MIN_SEC = 1.0
URGENT_REACTIVE_SEND_GAP_MAX_SEC = 3.0
EVENT_BURST_SEND_GAP_MIN_SEC = 0.55
EVENT_BURST_SEND_GAP_MAX_SEC = 0.95
RETRY_SEND_GAP_MIN_SEC = 1.0
RETRY_SEND_GAP_MAX_SEC = 3.0
NORMAL_SEND_GAP_MIN_SEC = 20.0
NORMAL_SEND_GAP_MAX_SEC = 40.0

_GAME_SEND_LOCK = asyncio.Lock()
_GAME_LAST_SEND_AT = 0.0
_GAME_SEND_QUEUE_SEQ = 0
_GAME_SEND_QUEUE_ITEMS = {}
_GAME_COMMAND_SENT_OBSERVERS = []
_LOG_BOT_UPDATE_OFFSET = None
LOG_BOT_CONNECT_TIMEOUT_SEC = 3
LOG_BOT_READ_TIMEOUT_SEC = 8
LOG_BOT_TOTAL_TIMEOUT_SEC = 12
LOG_BOT_POLL_READ_TIMEOUT_SEC = 35
LOG_BOT_POLL_INTERVAL_SEC = 1.0
LOG_ACCOUNT_SEND_TIMEOUT_SEC = 10
_ACCOUNT_OFFLINE_AUDIT_LAST = {}
ACCOUNT_OFFLINE_AUDIT_INTERVAL_SEC = 30 * 60
WEAKNESS_BLOCK_AUDIT_INTERVAL_SEC = 5 * 60
WEAKNESS_DEFAULT_SEC = 30 * 60
WEAKNESS_BUFFER_SEC = 60
WEAKNESS_ALLOWED_PREFIXES = (
    ".储物袋",
    CMD_TREE_STATUS,
    CMD_TREE_PULSE_STATUS,
    CMD_TREE_HARVEST,
    ".上架",
    ".购买",
    ".赠送",
    ".修理法宝",
    ".验证",
    ".自证",
    ".作答",
)
BUSY_CRITICAL_ALLOWED_PREFIXES = (
    ".中断悟道",
    ".验证",
    ".自证",
    ".作答",
)
DUNGEON_QUIET_ALLOWED_PREFIXES = (
    ".进入虚天殿",
    ".进入坠魔谷",
    ".进入黄龙山",
    ".进入苍坤洞府",
    ".进入昆吾山",
    ".进入落云秘圃",
    ".选择道路",
    ".阵策",
    ".争鼎",
    ".后殿抉择",
    ".后殿阵策",
    ".坠魔抉择",
    ".黄龙抉择",
    ".苍坤抉择",
    ".落云抉择",
    CMD_WORLD_BOSS_STATUS,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_ATTACK,
)


def register_game_command_sent_observer(observer):
    if callable(observer) and observer not in _GAME_COMMAND_SENT_OBSERVERS:
        _GAME_COMMAND_SENT_OBSERVERS.append(observer)


def _notify_game_command_sent_observers(command, send_as_id, sent_at, msg_id, **metadata):
    for observer in list(_GAME_COMMAND_SENT_OBSERVERS):
        try:
            observer(int(send_as_id or 0), command, now=sent_at, msg_id=msg_id, **metadata)
        except TypeError:
            observer(int(send_as_id or 0), command, now=sent_at, msg_id=msg_id)
        except Exception:
            traceback.print_exc()


# ============== 天尊健康状态 ==============
BOT_HEALTH_HEALTHY = "healthy"
BOT_HEALTH_SUSPECT = "suspect"
BOT_HEALTH_PAUSED = "paused"
BOT_HEALTH_PROBING = "probing"
BOT_HEALTH_RECOVERING = "recovering"

_bot_health_state = BOT_HEALTH_HEALTHY
_bot_health_reason = ""
_bot_health_changed_at = 0.0
_bot_waiting_since = 0.0
_bot_last_seen_at = 0.0
_bot_probe_sent_at = 0.0
_bot_last_block_log_at = 0.0


def _now_ts(now=None):
    return float(now if now is not None else time.time())


def _set_bot_health_state(new_state, reason="", now=None):
    global _bot_health_state, _bot_health_reason, _bot_health_changed_at
    now = _now_ts(now)
    new_state = str(new_state or BOT_HEALTH_HEALTHY)
    reason = str(reason or "").strip()
    if _bot_health_state == new_state and _bot_health_reason == reason:
        return False
    old_state = _bot_health_state
    _bot_health_state = new_state
    _bot_health_reason = reason
    _bot_health_changed_at = now
    try:
        console_log(f"🩺 天尊状态 {old_state} -> {new_state}：{reason or '无'}", scope="global")
    except Exception:
        pass
    return True


def get_bot_health_snapshot():
    return {
        "state": _bot_health_state,
        "reason": _bot_health_reason,
        "changed_at": _bot_health_changed_at,
        "waiting_since": _bot_waiting_since,
        "last_seen_at": _bot_last_seen_at,
        "probe_sent_at": _bot_probe_sent_at,
    }


def get_bot_last_seen_at():
    return float(_bot_last_seen_at or 0)


def note_game_command_observed(command, now=None):
    """看到群内有人发出游戏指令后，开始观察 bot 是否静默。"""
    global _bot_waiting_since
    cmd = str(command or "").strip()
    if not cmd.startswith("."):
        return
    now = _now_ts(now)
    if _bot_health_state in {BOT_HEALTH_PAUSED, BOT_HEALTH_PROBING}:
        return
    if _bot_waiting_since <= 0:
        _bot_waiting_since = now


def note_game_command_sent(command, sent_at=None, priority=SEND_PRIORITY_NORMAL):
    global _bot_waiting_since, _bot_probe_sent_at
    sent_at = _now_ts(sent_at)
    priority = _normalize_send_priority(command, priority=priority)
    if priority == SEND_PRIORITY_PROBE:
        _bot_probe_sent_at = sent_at
        return
    if _bot_health_state not in {BOT_HEALTH_PAUSED, BOT_HEALTH_PROBING}:
        _bot_waiting_since = sent_at


def note_game_bot_message(now=None):
    """返回 probe/recover/None，交给 app 层决定是否发探测或恢复全局。"""
    global _bot_last_seen_at, _bot_waiting_since, _bot_probe_sent_at
    now = _now_ts(now)
    previous_state = _bot_health_state
    _bot_last_seen_at = now
    _bot_waiting_since = 0.0
    if previous_state == BOT_HEALTH_PAUSED:
        _set_bot_health_state(BOT_HEALTH_PROBING, "bot 有发言，先探测确认", now)
        return "probe"
    if previous_state == BOT_HEALTH_PROBING:
        if _bot_probe_sent_at > 0 and now >= _bot_probe_sent_at:
            _bot_probe_sent_at = 0.0
            _set_bot_health_state(BOT_HEALTH_RECOVERING, "探测后已看到 bot 回复", now)
            return "recover"
        return None
    if previous_state == BOT_HEALTH_SUSPECT:
        _set_bot_health_state(BOT_HEALTH_RECOVERING, "疑似静默后已看到 bot 回复", now)
        return "recover"
    return None


def mark_bot_health_recovered(reason="恢复普通调度", now=None):
    global _bot_waiting_since, _bot_probe_sent_at
    now = _now_ts(now)
    _bot_waiting_since = 0.0
    _bot_probe_sent_at = 0.0
    return _set_bot_health_state(BOT_HEALTH_HEALTHY, reason, now)


def mark_bot_health_suspect(reason, reference_at=None, now=None):
    now = _now_ts(now)
    reference_at = float(reference_at or now)
    if _bot_last_seen_at >= reference_at:
        return False
    return _set_bot_health_state(BOT_HEALTH_SUSPECT, reason, now)


def check_bot_health_timeout(now=None, silence_timeout_sec=600):
    global _bot_probe_sent_at
    now = _now_ts(now)
    if (
        _bot_waiting_since > 0
        and _bot_last_seen_at < _bot_waiting_since
        and now - _bot_waiting_since >= float(silence_timeout_sec or 600)
    ):
        return _set_bot_health_state(BOT_HEALTH_PAUSED, f"{int(silence_timeout_sec)} 秒无 bot 回复", now)
    if (
        _bot_health_state == BOT_HEALTH_PROBING
        and _bot_probe_sent_at > 0
        and _bot_last_seen_at < _bot_probe_sent_at
        and now - _bot_probe_sent_at >= RETRY_MAX_SEC
    ):
        _bot_probe_sent_at = 0.0
        return _set_bot_health_state(BOT_HEALTH_PAUSED, "恢复探测超时，继续暂停", now)
    return False


def should_pause_for_bot_health():
    return _bot_health_state in {BOT_HEALTH_SUSPECT, BOT_HEALTH_PAUSED, BOT_HEALTH_PROBING}


def _normalize_send_priority(command, priority=None):
    explicit = str(priority or "").strip().lower()
    if explicit in {
        SEND_PRIORITY_P0,
        SEND_PRIORITY_CHAIN,
        SEND_PRIORITY_REACTIVE,
        SEND_PRIORITY_URGENT_REACTIVE,
        SEND_PRIORITY_EVENT_BURST,
        SEND_PRIORITY_RETRY,
        SEND_PRIORITY_PROBE,
        SEND_PRIORITY_NORMAL,
    }:
        return explicit
    cmd = str(command or "").strip()
    if any(cmd.startswith(prefix) for prefix in P0_COMMAND_PREFIXES):
        return SEND_PRIORITY_P0
    return SEND_PRIORITY_NORMAL


def _bot_health_blocks_send(priority):
    if priority in {SEND_PRIORITY_P0, SEND_PRIORITY_PROBE}:
        return False
    return _bot_health_state in {BOT_HEALTH_SUSPECT, BOT_HEALTH_PAUSED, BOT_HEALTH_PROBING}


def _refresh_bot_health_timeout_before_send():
    try:
        check_bot_health_timeout(time.time())
    except Exception:
        traceback.print_exc()


async def _log_bot_health_blocked_send(command, send_as_id=None):
    global _bot_last_block_log_at
    now = time.time()
    if now - _bot_last_block_log_at < 300:
        return
    _bot_last_block_log_at = now
    await send_audit_log(
        f"⏸ 天尊状态 {_bot_health_state}，普通指令暂缓：{_truncate_log_text(command, limit=32)}",
        scope="identity",
        send_as_id=send_as_id,
        limit=220,
    )


def _get_send_gap_range(priority):
    if priority in {SEND_PRIORITY_P0, SEND_PRIORITY_PROBE}:
        return P0_SEND_GAP_MIN_SEC, P0_SEND_GAP_MAX_SEC
    if priority == SEND_PRIORITY_URGENT_REACTIVE:
        return URGENT_REACTIVE_SEND_GAP_MIN_SEC, URGENT_REACTIVE_SEND_GAP_MAX_SEC
    if priority == SEND_PRIORITY_EVENT_BURST:
        return EVENT_BURST_SEND_GAP_MIN_SEC, EVENT_BURST_SEND_GAP_MAX_SEC
    if priority == SEND_PRIORITY_RETRY:
        return RETRY_SEND_GAP_MIN_SEC, RETRY_SEND_GAP_MAX_SEC
    if priority == SEND_PRIORITY_REACTIVE:
        return REACTIVE_SEND_GAP_MIN_SEC, REACTIVE_SEND_GAP_MAX_SEC
    if priority == SEND_PRIORITY_CHAIN:
        return CHAIN_SEND_GAP_MIN_SEC, CHAIN_SEND_GAP_MAX_SEC
    return NORMAL_SEND_GAP_MIN_SEC, NORMAL_SEND_GAP_MAX_SEC


def _build_send_not_before(priority):
    min_gap, max_gap = _get_send_gap_range(priority)
    now_mono = time.monotonic()
    not_before = now_mono + random.uniform(min_gap, max_gap)
    if _GAME_LAST_SEND_AT > 0:
        not_before = max(not_before, _GAME_LAST_SEND_AT + min_gap)
    return not_before


def get_game_send_queue_snapshot():
    now_wall = time.time()
    now_mono = time.monotonic()
    items = []
    for token, item in sorted(
        _GAME_SEND_QUEUE_ITEMS.items(),
        key=lambda pair: (float((pair[1] or {}).get("enqueued_at", 0) or 0), int(pair[0] or 0)),
    ):
        not_before_mono = float((item or {}).get("not_before_mono", 0) or 0)
        ready_in = max(0.0, not_before_mono - now_mono) if not_before_mono > 0 else 0.0
        items.append({
            "id": int(token or 0),
            "cmd": str((item or {}).get("cmd") or ""),
            "identity_id": int((item or {}).get("send_as_id") or 0),
            "identity_name": get_send_as_label((item or {}).get("send_as_id") or 0),
            "priority": str((item or {}).get("priority") or SEND_PRIORITY_NORMAL),
            "status": str((item or {}).get("status") or "waiting"),
            "enqueued_at": float((item or {}).get("enqueued_at") or 0),
            "not_before_at": now_wall + ready_in if ready_in > 0 else 0,
            "ready_in_sec": int(round(ready_in)),
        })
    return items


@asynccontextmanager
async def _send_slot(priority, command=None, send_as_id=None):
    global _GAME_LAST_SEND_AT, _GAME_SEND_QUEUE_SEQ
    min_gap, max_gap = _get_send_gap_range(priority)
    slot_anchor = None
    not_before = 0.0
    _GAME_SEND_QUEUE_SEQ += 1
    queue_token = _GAME_SEND_QUEUE_SEQ
    _GAME_SEND_QUEUE_ITEMS[queue_token] = {
        "cmd": command or "",
        "send_as_id": int(send_as_id or 0),
        "priority": priority or SEND_PRIORITY_NORMAL,
        "status": "waiting",
        "enqueued_at": time.time(),
        "not_before_mono": 0.0,
    }
    try:
        while True:
            await _GAME_SEND_LOCK.acquire()
            now_mono = time.monotonic()
            if not_before <= 0 or slot_anchor != _GAME_LAST_SEND_AT:
                slot_anchor = _GAME_LAST_SEND_AT
                not_before = max(now_mono, _GAME_LAST_SEND_AT) + random.uniform(min_gap, max_gap)
            ready_at = not_before
            _GAME_SEND_QUEUE_ITEMS.get(queue_token, {})["not_before_mono"] = ready_at
            wait = ready_at - now_mono
            if wait <= 0:
                _GAME_SEND_QUEUE_ITEMS.get(queue_token, {})["status"] = "sending"
                try:
                    yield
                finally:
                    _GAME_LAST_SEND_AT = time.monotonic()
                    _GAME_SEND_LOCK.release()
                return
            _GAME_SEND_LOCK.release()
            await asyncio.sleep(min(wait, 5.0))
    finally:
        _GAME_SEND_QUEUE_ITEMS.pop(queue_token, None)
_ui_login_tokens = {}
_reply_chain_tracker = {}
SEND_INTENT_FIELDS = ("source_module", "op_id", "chain_id", "delete_policy")


REPLY_FAMILY_COMMANDS = {
    "checkin": {CMD_CHECKIN},
    "sect_teach": {CMD_SECT_TEACH},
    "tower": {CMD_TOWER},
    "pet": {CMD_PET},
    "pet_warm": {CMD_PET_WARM},
    "pet_trial": {CMD_PET_TRIAL},
    "ranch": {CMD_RANCH},
    "wild_training": {CMD_WILD_TRAINING},
    "tree_panel": {CMD_TREE_WATER, CMD_TREE_STATUS, CMD_TREE_PULSE_STATUS},
    "tree_pulse": {CMD_TREE_PULSE},
    "tree_guard": {CMD_TREE_GUARD},
    "tree_harvest": {CMD_TREE_HARVEST},
    "stargazer_panel": {CMD_STARGAZER_PANEL},
    "stargazer_guide": {CMD_STARGAZER_GUIDE},
    "stargazer_soothe": {CMD_STARGAZER_SOOTHE},
    "stargazer_collect": {CMD_STARGAZER_COLLECT},
    "guanxing_query": {CMD_GUANXING},
    "guanxing_shift": {CMD_GUANXING_SHIFT},
    "formation_start": {CMD_FORMATION_START},
    "formation_assist": {CMD_FORMATION_ASSIST},
    "tianti_status": {CMD_TIANTI_STATUS},
    "tianti_wenxin": {CMD_TIANTI_WENXIN},
    "tianti_climb": {CMD_TIANTI_CLIMB},
    "tianti_gangfeng": {CMD_TIANTI_GANGFENG},
    "yuanying": {CMD_YUANYING, CMD_YUANYING_STATUS},
    "explore_rift": {CMD_EXPLORE_RIFT},
    "wendao": {CMD_WENDAO},
    "deep_retreat": {CMD_DEEP_RETREAT, CMD_DEEP_RETREAT_QUERY},
    "small_world_preach": {CMD_SMALL_WORLD_PREACH},
    "small_world_relief": {CMD_SMALL_WORLD_RELIEF},
    "small_world_query": {CMD_SMALL_WORLD_QUERY},
    "small_world_manifest": {CMD_SMALL_WORLD_MANIFEST},
    "small_world_harvest": {CMD_SMALL_WORLD_HARVEST},
    "small_world_refine": {CMD_SMALL_WORLD_REFINE},
    "divination": {CMD_DIVINATION},
    "divination_exchange": {CMD_DIVINATION_EXCHANGE},
    "concubine_status": {CMD_CONCUBINE_STATUS},
    "concubine_greet": {CMD_CONCUBINE_DAILY_GREET},
    "concubine_gift": {CMD_CONCUBINE_GIFT_STONE},
    "concubine_dream": {CMD_CONCUBINE_DREAM},
    "concubine_fragment": {CMD_CONCUBINE_FRAGMENT},
    "concubine_puzzle": {CMD_CONCUBINE_PUZZLE},
    "concubine_reacquire": {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE},
    "concubine_tianji": {CMD_CONCUBINE_TIANJI},
    "concubine_heart": {CMD_CONCUBINE_HEART, CMD_CONCUBINE_HEART_STEADY},
    "concubine_voyage": {CMD_CONCUBINE_VOYAGE, CMD_CONCUBINE_VOYAGE_RETURN, CMD_CONCUBINE_VOYAGE_STATUS},
    "hehuan_retreat": {CMD_HEHUAN_RETREAT},
    "hehuan_contract": {CMD_HEHUAN_CONTRACT},
    "hehuan_dual": {CMD_HEHUAN_DUAL},
    "hehuan_seal": {CMD_HEHUAN_SEAL},
    "hehuan_escape": {CMD_HEHUAN_ESCAPE},
    "tianxing_help": {CMD_TIANXING_HELP},
    "tianxing_panel": {CMD_TIANXING_PANEL},
    "tianxing_observe": {CMD_TIANXING_OBSERVE},
    "tianxing_set_star": {CMD_TIANXING_SET_STAR},
    "tianxing_predict": {CMD_TIANXING_PREDICT},
    "tianxing_change_fate": {CMD_TIANXING_CHANGE_FATE},
    "tianxing_clear_calamity": {CMD_TIANXING_CLEAR_CALAMITY},
    "yinluo_guide": {CMD_YINLUO_GUIDE},
    "yinluo_banner": {CMD_YINLUO_BANNER},
    "yinluo_blood_forest": {CMD_YINLUO_BLOOD_FOREST},
    "yinluo_demon_summon": {CMD_YINLUO_DEMON_SUMMON},
    "yinluo_convert": {CMD_YINLUO_CONVERT},
    "yinluo_collect": {CMD_YINLUO_COLLECT},
    "yinluo_refine": {CMD_YINLUO_REFINE},
    "yinluo_curse": {CMD_YINLUO_CURSE},
    "yinluo_possess": {CMD_YINLUO_POSSESS},
    "world_boss": {CMD_WORLD_BOSS_STATUS, CMD_QINGYUANZI_SUPPRESS, CMD_QINGYUANZI_GUARD, CMD_QINGYUANZI_ATTACK},
    "nanlong": {CMD_NANLONG_EXCHANGE_FABAO, CMD_NANLONG_EXCHANGE_GONGFA, CMD_NANLONG_REJECT},
    "second_soul_status": {CMD_SECOND_SOUL_STATUS},
    "second_soul_train": {CMD_SECOND_SOUL_TRAIN},
    "second_soul_choice": {CMD_SECOND_SOUL_CHOICE_BREAK, CMD_SECOND_SOUL_CHOICE_STABLE},
    "taiyi_yindao": {CMD_YINDAO},
    "taiyi_node_search": {CMD_NODE_SEARCH},
    "taiyi_node_define": {CMD_NODE_DEFINE},
    "storage_bag": {".储物袋"},
    "storage_bag_listing": {".上架"},
    "storage_bag_buy": {".购买"},
    "storage_bag_gift": {".赠送"},
    "replica_join": {CMD_REPLICA_JOIN, CMD_REPLICA_ZHUIMO_JOIN, CMD_REPLICA_HUANGLONG_JOIN, CMD_REPLICA_CANGKUN_JOIN, CMD_REPLICA_KUNWU_JOIN, CMD_REPLICA_LUOYUN_JOIN},
}
COMMAND_TO_REPLY_FAMILY = {
    command: family
    for family, commands in REPLY_FAMILY_COMMANDS.items()
    for command in commands
}


def _compact_send_intent(intent=None):
    compact = {}
    if not isinstance(intent, dict):
        return compact
    for key in SEND_INTENT_FIELDS:
        value = str(intent.get(key) or "").strip()
        if value:
            compact[key] = value
    return compact


def _normalize_send_intent(
    command,
    *,
    intent=None,
    source_module=None,
    op_id=None,
    chain_id=None,
    delete_policy=None,
):
    merged = _compact_send_intent(intent)
    explicit_values = {
        "source_module": source_module,
        "op_id": op_id,
        "chain_id": chain_id,
        "delete_policy": delete_policy,
    }
    for key, value in explicit_values.items():
        if value is not None:
            merged[key] = str(value or "").strip()

    if not merged.get("source_module"):
        family = resolve_reply_family(command) or ""
        source_module = get_module_name_for_reply_family(family)
        if source_module:
            merged["source_module"] = source_module
    if not merged.get("delete_policy"):
        merged["delete_policy"] = "auto_delete" if is_auto_delete_sent_messages_enabled() else "keep"
    return _compact_send_intent(merged)


def _pending_send_intent_kwargs(pending_item):
    intent = _compact_send_intent(pending_item)
    return {key: value for key, value in intent.items() if value}


def _append_sent_message_log(msg_id, command, send_as_id, reply_to_msg_id=0, *, priority="", track=None, intent=None):
    try:
        now = datetime.now(TZ_LOCAL)
        log_file = os.path.join(MESSAGES_DIR, f"{now.strftime('%Y-%m-%d')}.log")
        cleanup_message_logs()
        family = resolve_reply_family(command) or ""
        payload = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC+8"),
            "event_type": "sent",
            "message_id": int(msg_id or 0),
            "chat_id": get_game_group_id(),
            "sender_id": int(send_as_id or 0),
            "topic_id": get_game_topic_id(),
            "reply_to_msg_id": int(reply_to_msg_id or 0),
            "text": command or "",
        }
        if family:
            payload["family"] = family
        if priority:
            payload["priority"] = str(priority)
        if track is not None:
            payload["track"] = bool(track)
        for key, value in _compact_send_intent(intent).items():
            payload[key] = value
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        traceback.print_exc()
_ui_sessions = {}
_UI_AUTH_STATE_FILE = os.path.join(STATE_DIR, "ui_auth_state.json")
_UI_AUTH_STATE_LOADED = False
_UI_AUTH_STATE_LAST_SAVED_AT = 0.0
_UI_AUTH_STATE_SAVE_INTERVAL_SEC = 60.0
IDENTITY_INFO_REFRESH_ERROR_TEXT = "获取失败，请手动重新获取"


def _is_identity_refresh_command(command):
    return is_identity_refresh_command_text(command)


def _fire_and_forget(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    def _done(done_task):
        _background_tasks.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            traceback.print_exc()
            return
        if exc is not None:
            console_log(f"⚠️ 后台任务异常：{_truncate_log_text(exc, limit=120)}", limit=180)
            traceback.print_exception(type(exc), exc, exc.__traceback__)
    task.add_done_callback(_done)


def _secure_lookup(store, token):
    token = (token or "").strip()
    if not token:
        return None, None
    for stored_token, payload in store.items():
        if secrets.compare_digest(stored_token, token):
            return stored_token, payload
    return None, None


def _new_runtime_token(store):
    while True:
        token = secrets.token_urlsafe(32)
        if token not in store:
            return token


def _coerce_ui_auth_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def is_script_command_text(text):
    raw_text = (text or "").strip()
    if not raw_text:
        return False
    return any(raw_text == cmd or raw_text.startswith(f"{cmd} ") for cmd in SCRIPT_COMMANDS)


def _gc_reply_chain_tracker(now=None):
    now = float(now if now is not None else time.time())
    expired_msg_ids = [
        msg_id
        for msg_id, payload in _reply_chain_tracker.items()
        if now - float((payload or {}).get("tracked_at", 0) or 0) > MY_MSG_TTL
    ]
    for msg_id in expired_msg_ids:
        _reply_chain_tracker.pop(msg_id, None)



def clear_identity_runtime_tracking(send_as_id):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0 or not has_identity(send_as_id):
        return False
    changed = False
    tracked_msg_ids = [
        msg_id
        for msg_id, payload in _reply_chain_tracker.items()
        if int((payload or {}).get("send_as_id", 0) or 0) == send_as_id
    ]
    for msg_id in tracked_msg_ids:
        _reply_chain_tracker.pop(msg_id, None)
        changed = True
    with use_identity(send_as_id) as identity_state:
        if identity_state.get("pending_tasks"):
            identity_state["pending_tasks"] = {}
            changed = True
        if identity_state.get("my_msg_ids"):
            identity_state["my_msg_ids"] = {}
            changed = True
        runtime_reset_fields = {
            "identity_info_reply_msg_ids": [],
            "last_identity_info_msg_id": 0,
            "identity_info_last_error": "",
            "identity_info_last_requested_at": 0,
            "identity_info_followup_due_at": 0,
            "identity_info_primary_payload": {},
        }
        for key, value in runtime_reset_fields.items():
            if identity_state.get(key) != value:
                identity_state[key] = value
                changed = True
    if changed:
        mark_dirty()
    return changed


def clear_all_pending_tasks(reason=""):
    changed_count = 0
    affected_identity_ids = set()
    for identity_id in get_identity_ids():
        if not has_identity(identity_id):
            continue
        with use_identity(identity_id) as identity_state:
            pending_count = len(identity_state.get("pending_tasks", {}) or {})
            if pending_count <= 0:
                continue
            identity_state["pending_tasks"] = {}
            changed_count += pending_count
            affected_identity_ids.add(int(identity_id))
    if changed_count > 0:
        mark_dirty()
        suffix = f"：{reason}" if reason else ""
        console_log(
            f"🧹 已清理 {len(affected_identity_ids)} 个身份 / {changed_count} 条待补发指令{suffix}",
            scope="global",
            limit=220,
        )
    return changed_count


def resolve_reply_family(command):
    raw_command = str(command or "").strip()
    if not raw_command:
        return None
    for prefix, family in COMMAND_TO_REPLY_FAMILY.items():
        if raw_command == prefix or raw_command.startswith(f"{prefix} "):
            return family
    return None


def get_reply_family_commands(family):
    return set(REPLY_FAMILY_COMMANDS.get(str(family or "").strip(), set()))


def has_active_reply_dispatch(send_as_id=None, family=None):
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    family_text = str(family or "").strip()

    def family_matches(candidate):
        candidate = str(candidate or "").strip()
        if not family_text:
            return bool(candidate)
        if family_text == "formation":
            return candidate.startswith("formation_")
        return candidate == family_text

    for identity_id in target_ids:
        if not has_identity(identity_id):
            continue
        identity_state = get_identity_state(identity_id)
        for pending in (identity_state.get("pending_tasks") or {}).values():
            if family_matches(resolve_reply_family(get_pending_command(pending))):
                return True

        tracked_msg_ids = (
            "formation_pending_invite_msg_id",
            "formation_pending_assist_msg_id",
        ) if family_text == "formation" else ()
        for state_key in tracked_msg_ids:
            if int(identity_state.get(state_key, 0) or 0) > 0:
                return True
    return False


def _get_special_tracked_message_family(identity_state, msg_id):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None
    tracked_id_families = (
        ("last_checkin_msg_id", "checkin"),
        ("last_sect_teach_msg_id", "sect_teach"),
        ("last_tower_msg_id", "tower"),
        ("ranch_last_msg_id", "ranch"),
        ("wild_training_reply_to_msg_id", "wild_training"),
        ("wild_training_last_msg_id", "wild_training"),
        ("last_identity_info_msg_id", "identity_info"),
        ("stargazer_last_panel_msg_id", "stargazer_panel"),
        ("guanxing_last_query_msg_id", "guanxing_query"),
        ("guanxing_last_shift_msg_id", "guanxing_shift"),
        ("last_formation_msg_id", "formation_assist"),
        ("formation_pending_invite_msg_id", "formation_start"),
        ("formation_pending_assist_msg_id", "formation_assist"),
        ("tianti_status_reply_to_msg_id", "tianti_status"),
        ("tianti_last_status_msg_id", "tianti_status"),
        ("tianti_last_wenxin_msg_id", "tianti_wenxin"),
        ("tianti_last_climb_msg_id", "tianti_climb"),
        ("tianti_last_gangfeng_msg_id", "tianti_gangfeng"),
        ("last_yuanying_summary_msg_id", "yuanying"),
        ("wendao_reply_to_msg_id", "wendao"),
        ("wendao_pending_result_msg_id", "wendao"),
        ("wendao_last_msg_id", "wendao"),
        ("last_deep_retreat_summary_msg_id", "deep_retreat"),
        ("small_world_preach_reply_to_msg_id", "small_world_preach"),
        ("small_world_query_msg_id", "small_world_query"),
        ("small_world_manifest_msg_id", "small_world_manifest"),
        ("small_world_harvest_msg_id", "small_world_harvest"),
        ("small_world_refine_msg_id", "small_world_refine"),
        ("second_soul_status_msg_id", "second_soul_status"),
        ("second_soul_train_msg_id", "second_soul_train"),
        ("taiyi_yindao_msg_id", "taiyi_yindao"),
        ("taiyi_node_search_msg_id", "taiyi_node_search"),
        ("taiyi_node_define_msg_id", "taiyi_node_define"),
        ("concubine_status_msg_id", "concubine_status"),
        ("concubine_gift_status_msg_id", "concubine_status"),
        ("concubine_greet_msg_id", "concubine_greet"),
        ("concubine_gift_bag_msg_id", "storage_bag"),
        ("concubine_gift_msg_id", "concubine_gift"),
        ("concubine_dream_msg_id", "concubine_dream"),
        ("concubine_fragment_msg_id", "concubine_fragment"),
        ("concubine_puzzle_msg_id", "concubine_puzzle"),
        ("concubine_reacquire_msg_id", "concubine_reacquire"),
        ("concubine_tianji_msg_id", "concubine_tianji"),
        ("concubine_heart_msg_id", "concubine_heart"),
        ("concubine_heart_prompt_msg_id", "concubine_heart"),
        ("concubine_voyage_msg_id", "concubine_voyage"),
    )
    for state_key, family in tracked_id_families:
        tracked_msg_id = int(identity_state.get(state_key, 0) or 0)
        if family in {"yuanying", "deep_retreat", "tower"}:
            tracked_msg_id = abs(tracked_msg_id)
        if msg_id == tracked_msg_id:
            return family
    tracked_identity_info_ids = {
        int(tracked_msg_id or 0)
        for tracked_msg_id in identity_state.get("identity_info_reply_msg_ids", [])
    }
    tracked_identity_info_ids.discard(0)
    if msg_id in tracked_identity_info_ids:
        return "identity_info"
    return None


def _resolve_identity_message_owner(msg_id, send_as_id=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None, None

    _gc_reply_chain_tracker()
    tracker_payload = _reply_chain_tracker.get(msg_id)
    tracked_identity_id = int((tracker_payload or {}).get("send_as_id", 0) or 0)
    if tracked_identity_id > 0 and has_identity(tracked_identity_id) and (send_as_id is None or int(send_as_id) == tracked_identity_id):
        return tracked_identity_id, "reply_chain_tracker"

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    for identity_id in target_ids:
        if not has_identity(identity_id):
            continue
        identity_state = get_identity_state(identity_id)
        if msg_id in identity_state["my_msg_ids"]:
            return identity_id, "my_msg_ids"
        if _get_special_tracked_message_family(identity_state, msg_id):
            return identity_id, "tracked_ids"
    return None, None


def _resolve_identity_from_message_sender(message, send_as_id=None):
    sender_id = int(getattr(message, "sender_id", 0) or 0)
    if sender_id == 0:
        return None, None

    candidates = [sender_id]
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                candidates.append(int(sender_abs[3:]))
            except ValueError:
                pass

    target_ids = {int(send_as_id)} if send_as_id is not None else {int(identity_id) for identity_id in get_identity_ids()}
    for candidate in candidates:
        candidate = int(candidate or 0)
        if candidate in target_ids and has_identity(candidate):
            return candidate, "reply_sender"
    return None, None


def _resolve_identity_message_family(msg_id, send_as_id):
    msg_id = int(msg_id or 0)
    send_as_id = int(send_as_id or 0)
    if msg_id <= 0 or send_as_id <= 0 or not has_identity(send_as_id):
        return None, 0, ""

    _gc_reply_chain_tracker()
    tracker_payload = _reply_chain_tracker.get(msg_id)
    if tracker_payload and int(tracker_payload.get("send_as_id", 0) or 0) == send_as_id:
        return (
            tracker_payload.get("family") or None,
            int(tracker_payload.get("root_msg_id", 0) or msg_id),
            str(tracker_payload.get("source") or "").strip(),
        )

    identity_state = get_identity_state(send_as_id)
    pending_item = identity_state.get("pending_tasks", {}).get(msg_id)
    if pending_item:
        return resolve_reply_family(get_pending_command(pending_item)), msg_id, ""

    special_family = _get_special_tracked_message_family(identity_state, msg_id)
    if special_family:
        return special_family, msg_id, ""

    return None, msg_id, ""


def _recent_sent_message_log_paths(*, days=2):
    local_now = datetime.now(TZ_LOCAL)
    paths = []
    for offset in range(max(1, int(days or 1))):
        day = local_now - timedelta(days=offset)
        paths.append(os.path.join(MESSAGES_DIR, f"{day.strftime('%Y-%m-%d')}.log"))
    return paths


def _read_recent_message_log_tail(path, *, max_lines=5000, max_bytes=512 * 1024):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - int(max_bytes or 0))
            handle.seek(start)
            if start > 0:
                handle.readline()
            data = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return data.splitlines()[-max(1, int(max_lines or 1)):]


def _resolve_identity_from_sent_message_log(msg_id, send_as_id=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None, None, 0, None
    target_ids = {int(send_as_id)} if send_as_id is not None else {int(identity_id) for identity_id in get_identity_ids()}
    target_ids.discard(0)
    if not target_ids:
        return None, None, 0, None

    for path in _recent_sent_message_log_paths():
        if not os.path.exists(path):
            continue
        for line in reversed(_read_recent_message_log_tail(path)):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("event_type") or "") != "sent":
                continue
            try:
                payload_msg_id = int(payload.get("message_id") or 0)
            except (TypeError, ValueError):
                payload_msg_id = 0
            if payload_msg_id != msg_id:
                continue
            try:
                identity_id = int(payload.get("sender_id") or 0)
            except (TypeError, ValueError):
                identity_id = 0
            if identity_id not in target_ids or not has_identity(identity_id):
                return None, None, 0, None
            family = str(payload.get("family") or "").strip() or resolve_reply_family(payload.get("text") or "")
            return identity_id, family or None, msg_id, "sent_message_log"
    return None, None, 0, None


def get_reply_context(reply_to=None, *, reply_to_msg_id=None, send_as_id=None):
    resolved_reply_to_msg_id = int(reply_to_msg_id or getattr(reply_to, "id", 0) or 0)
    if resolved_reply_to_msg_id <= 0:
        return {
            "send_as_id": None,
            "family": None,
            "reply_to_msg_id": 0,
            "matched_via": "none",
            "root_msg_id": 0,
            "source": "",
        }

    resolved_send_as_id, matched_via = _resolve_identity_message_owner(resolved_reply_to_msg_id, send_as_id=send_as_id)
    if resolved_send_as_id is None and reply_to is not None:
        resolved_send_as_id, matched_via = _resolve_identity_from_message_sender(reply_to, send_as_id=send_as_id)
    family = None
    source = ""
    root_msg_id = resolved_reply_to_msg_id
    if resolved_send_as_id is None:
        resolved_send_as_id, family, root_msg_id, matched_via = _resolve_identity_from_sent_message_log(
            resolved_reply_to_msg_id,
            send_as_id=send_as_id,
        )
    if resolved_send_as_id is not None:
        resolved_family, resolved_root_msg_id, resolved_source = _resolve_identity_message_family(
            resolved_reply_to_msg_id,
            resolved_send_as_id,
        )
        family = family or resolved_family
        source = resolved_source or source
        root_msg_id = int(root_msg_id or resolved_root_msg_id or resolved_reply_to_msg_id)
    if family is None and reply_to is not None:
        family = resolve_reply_family(getattr(reply_to, "raw_text", ""))
    if family is None and resolved_send_as_id is not None and reply_to is not None:
        identity_state = get_identity_state(resolved_send_as_id)
        reply_text = str(getattr(reply_to, "raw_text", "") or "").strip()
        if reply_text:
            for pending in identity_state.get("pending_tasks", {}).values():
                pending_cmd = get_pending_command(pending)
                if pending_cmd and pending_cmd in reply_text:
                    family = resolve_reply_family(pending_cmd)
                    break

    return {
        "send_as_id": resolved_send_as_id,
        "family": family,
        "reply_to_msg_id": resolved_reply_to_msg_id,
        "matched_via": matched_via or ("reply_header" if reply_to is None else "reply_object"),
        "root_msg_id": int(root_msg_id or resolved_reply_to_msg_id),
        "source": source,
    }


def track_reply_chain_message(msg_id, send_as_id, family, *, root_msg_id=None, source=""):
    msg_id = int(msg_id or 0)
    send_as_id = int(send_as_id or 0)
    family = str(family or "").strip()
    root_msg_id = int(root_msg_id or 0) or msg_id
    source = str(source or "").strip()
    if msg_id <= 0 or send_as_id <= 0 or not family:
        return False
    _gc_reply_chain_tracker()
    _reply_chain_tracker[msg_id] = {
        "send_as_id": send_as_id,
        "family": family,
        "root_msg_id": root_msg_id,
        "source": source,
        "tracked_at": time.time(),
    }
    return True


def _clear_pending_tasks_by_commands_locked(commands):
    commands = {str(command or "").strip() for command in commands if str(command or "").strip()}
    if not commands:
        return []

    families = {resolve_reply_family(command) for command in commands}
    families.discard(None)
    remove_ids = []
    for msg_id, pending in state.get("pending_tasks", {}).items():
        pending_cmd = get_pending_command(pending)
        pending_family = resolve_reply_family(pending_cmd)
        if pending_cmd in commands or (pending_family and pending_family in families):
            remove_ids.append(msg_id)
    for msg_id in remove_ids:
        state["pending_tasks"].pop(msg_id, None)
    return remove_ids


def clear_pending_tasks_by_commands(commands, send_as_id=None):
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    removed_ids = []
    changed = False
    for identity_id in target_ids:
        if not has_identity(identity_id):
            continue
        with use_identity(identity_id):
            current_removed_ids = _clear_pending_tasks_by_commands_locked(commands)
            if current_removed_ids:
                changed = True
                removed_ids.extend(current_removed_ids)
    if changed:
        mark_dirty()
    return removed_ids


def _normalize_inline_keyboard_buttons(buttons):
    rows = []
    for raw_row in buttons or []:
        row_items = raw_row if isinstance(raw_row, (list, tuple)) else [raw_row]
        row = []
        for raw_item in row_items:
            item = raw_item if isinstance(raw_item, dict) else {}
            text = str(item.get("text") or "").strip()
            callback_data = str(item.get("callback_data") or item.get("data") or "").strip()
            if not text or not callback_data:
                continue
            row.append({"text": text[:64], "callback_data": callback_data[:64]})
        if row:
            rows.append(row)
    return rows


def _send_log_group_via_bot(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True, parse_mode=None, buttons=None):
    if not LOG_BOT_TOKEN:
        return False, "missing bot token"
    payload = {
        "chat_id": str(LOG_GROUP_ID),
        "text": text,
        "disable_web_page_preview": not link_preview,
    }
    keyboard = _normalize_inline_keyboard_buttons(buttons)
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if int(reply_to_msg_id or 0) > 0:
        payload["reply_to_message_id"] = int(reply_to_msg_id)
        payload["allow_sending_without_reply"] = True
    if int(message_thread_id or 0) > 0:
        payload["message_thread_id"] = int(message_thread_id)
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data=payload,
            timeout=(LOG_BOT_CONNECT_TIMEOUT_SEC, LOG_BOT_READ_TIMEOUT_SEC),
            proxies=TG_REQUESTS_PROXIES,
        )
    except requests.exceptions.Timeout as e:
        return False, f"timeout: {e}"
    except requests.exceptions.ProxyError as e:
        return False, f"proxy error: {e}"
    except requests.exceptions.RequestException as e:
        return False, str(e)
    body = response.text
    if not response.ok:
        return False, f"HTTP {response.status_code}: {body}"
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("ok") is True:
        return True, ""
    return False, body or "bot api returned non-ok response"


def _call_log_bot_api(method, payload=None, *, read_timeout=LOG_BOT_READ_TIMEOUT_SEC):
    if not LOG_BOT_TOKEN:
        return False, None, "missing bot token"
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/{method}"
    try:
        response = requests.post(
            url,
            data=payload or {},
            timeout=(LOG_BOT_CONNECT_TIMEOUT_SEC, read_timeout),
            proxies=TG_REQUESTS_PROXIES,
        )
    except requests.exceptions.Timeout as e:
        return False, None, f"timeout: {e}"
    except requests.exceptions.ProxyError as e:
        return False, None, f"proxy error: {e}"
    except requests.exceptions.RequestException as e:
        return False, None, str(e)
    body = response.text
    if not response.ok:
        return False, None, f"HTTP {response.status_code}: {body}"
    try:
        data = response.json()
    except Exception:
        return False, None, body or "bot api returned non-json response"
    if isinstance(data, dict) and data.get("ok") is True:
        return True, data.get("result"), ""
    return False, data, body or "bot api returned non-ok response"


async def answer_log_bot_callback(callback_query_id, text="", *, show_alert=False):
    callback_query_id = str(callback_query_id or "").strip()
    if not callback_query_id:
        return False
    payload = {
        "callback_query_id": callback_query_id,
        "text": str(text or "")[:200],
        "show_alert": bool(show_alert),
    }
    ok, _result, error_text = await asyncio.to_thread(_call_log_bot_api, "answerCallbackQuery", payload)
    if not ok:
        print(f"answerCallbackQuery failed: {error_text}")
    return bool(ok)


async def run_log_bot_callback_poller(callback_handler, stop_event=None):
    global _LOG_BOT_UPDATE_OFFSET
    if not LOG_BOT_TOKEN:
        return
    while stop_event is None or not stop_event.is_set():
        payload = {
            "timeout": 30,
            "allowed_updates": json.dumps(["callback_query"]),
        }
        if _LOG_BOT_UPDATE_OFFSET is not None:
            payload["offset"] = int(_LOG_BOT_UPDATE_OFFSET)
        ok, result, error_text = await asyncio.to_thread(
            _call_log_bot_api,
            "getUpdates",
            payload,
            read_timeout=LOG_BOT_POLL_READ_TIMEOUT_SEC,
        )
        if not ok:
            print(f"log bot callback poll failed: {error_text}")
            await asyncio.sleep(LOG_BOT_POLL_INTERVAL_SEC)
            continue
        updates = result if isinstance(result, list) else []
        for update in updates:
            try:
                update_id = int((update or {}).get("update_id") or 0)
            except (TypeError, ValueError):
                update_id = 0
            if update_id:
                next_offset = update_id + 1
                if _LOG_BOT_UPDATE_OFFSET is None or next_offset > _LOG_BOT_UPDATE_OFFSET:
                    _LOG_BOT_UPDATE_OFFSET = next_offset
            callback_query = (update or {}).get("callback_query")
            if not isinstance(callback_query, dict):
                continue
            try:
                await callback_handler(callback_query)
            except Exception:
                traceback.print_exc()
        if not updates:
            await asyncio.sleep(0)


async def _send_log_group_message(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True, parse_mode=None, buttons=None):
    if LOG_SEND_MODE == "bot":
        try:
            ok, error_text = await asyncio.wait_for(
                asyncio.to_thread(
                    _send_log_group_via_bot,
                    text,
                    reply_to_msg_id=reply_to_msg_id,
                    message_thread_id=message_thread_id,
                    link_preview=link_preview,
                    parse_mode=parse_mode,
                    buttons=buttons,
                ),
                timeout=LOG_BOT_TOTAL_TIMEOUT_SEC,
            )
            if ok:
                return True
            print(f"_send_log_group_message bot fallback: {error_text} | text={text}")
        except asyncio.TimeoutError:
            print(f"_send_log_group_message bot timeout | text={text}")
        except Exception as e:
            print(f"_send_log_group_message bot failed: {e} | text={text}")
    try:
        _fb = _get_any_authed_client()
        send_kwargs = {}
        keyboard = _normalize_inline_keyboard_buttons(buttons)
        if keyboard:
            try:
                from telethon import Button
                send_kwargs["buttons"] = [
                    [Button.inline(item["text"], data=item["callback_data"].encode("utf-8")) for item in row]
                    for row in keyboard
                ]
            except Exception:
                traceback.print_exc()
        await asyncio.wait_for(
            _fb.send_message(
                LOG_GROUP_ID,
                text,
                reply_to=int(reply_to_msg_id or 0) or None,
                link_preview=link_preview,
                parse_mode=parse_mode or None,
                **send_kwargs,
            ),
            timeout=LOG_ACCOUNT_SEND_TIMEOUT_SEC,
        )
        return True
    except asyncio.TimeoutError:
        print(f"_send_log_group_message account timeout | text={text}")
        return False
    except Exception as e:
        print(f"_send_log_group_message account failed: {e} | text={text}")
        return False


def mono(text):
    """将文本包裹为 HTML monospace 格式，防止 Telegram @提及"""
    from html import escape
    return f"<code>{escape(str(text))}</code>"


def _truncate_log_text(text, limit=220):
    raw = str(text or "").strip()
    if not raw or len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _resolve_log_identity(scope="auto", send_as_id=None):
    resolved_scope = (scope or "auto").strip().lower()
    if resolved_scope == "global":
        return None
    if send_as_id is not None:
        try:
            return int(send_as_id)
        except (TypeError, ValueError):
            return None
    if resolved_scope == "identity":
        active_identity_id = get_active_identity_id()
        if active_identity_id is not None:
            return active_identity_id
        current_identity_id = int(get_current_identity_id() or 0)
        return current_identity_id or None
    if has_active_identity_context():
        return get_active_identity_id()
    return None


def _format_log_identity_prefix(send_as_id, *, html=False):
    if send_as_id is None:
        return ""
    label = _truncate_log_text(get_send_as_label(send_as_id), limit=32)
    if not label:
        return ""
    return f"[{mono(label) if html else label}] "


def _format_log_message(content, *, scope="auto", send_as_id=None, html=False, limit=220):
    text = _truncate_log_text(content, limit=limit)
    identity_id = _resolve_log_identity(scope=scope, send_as_id=send_as_id)
    prefix = _format_log_identity_prefix(identity_id, html=html)
    return f"{prefix}{text}" if prefix else text


AUDIT_PRIORITY_LOW = "low"
AUDIT_PRIORITY_MEDIUM = "medium"
AUDIT_PRIORITY_HIGH = "high"
_AUDIT_PRIORITY_ALIASES = {
    "debug": AUDIT_PRIORITY_LOW,
    "low": AUDIT_PRIORITY_LOW,
    "info": AUDIT_PRIORITY_LOW,
    "normal": AUDIT_PRIORITY_MEDIUM,
    "medium": AUDIT_PRIORITY_MEDIUM,
    "notice": AUDIT_PRIORITY_MEDIUM,
    "warn": AUDIT_PRIORITY_MEDIUM,
    "warning": AUDIT_PRIORITY_MEDIUM,
    "high": AUDIT_PRIORITY_HIGH,
    "error": AUDIT_PRIORITY_HIGH,
    "critical": AUDIT_PRIORITY_HIGH,
}
_AUDIT_HIGH_MARKERS = (
    "🚨",
    "🆘",
    "已被封禁",
    "封禁",
    "全局暂停",
    "维持全局暂停",
    "需人工",
    "需要人工",
    "人工抉择",
    "人工处理",
    "请手动",
    "待处理",
    "心魔试炼",
    "账号离线熔断",
    "天尊状态",
    "后台任务异常",
)
_AUDIT_MEDIUM_MARKERS = (
    "⚠️",
    "⏸",
    "暂停",
    "熔断",
    "超时",
    "异常",
    "失败",
    "未匹配",
    "多个身份",
    "不足",
    "冻结",
    "重发",
    "补发",
    "吞回",
    "节流",
    "静场令",
    "启动成功",
    "UI 已启动",
    "状态恢复",
)
_AUDIT_LOW_SELF_HEAL_MARKERS = (
    "回复超时，准备补发一次",
    "已出发但未收到最终结果编辑，进入下一轮",
    "补发后仍无回复，进入下一轮",
    "launching 超时，已回退",
    "总结命中多个身份，已跳过",
    "归窍总结命中多个身份，已跳过",
    "题库内超时未作答",
    "CD 到期，等待灵兽归来广播",
    "启动成功",
    "UI 已启动",
    "状态恢复",
)
_low_priority_audit_bucket = {}
_low_priority_audit_order = []
_low_priority_audit_flush_task = None
_low_priority_audit_seq = 0


def _stateful_no_retry_timeout_is_module_managed(item, family=""):
    source_module = str((item or {}).get("source_module") or "").strip()
    return source_module in {"卜筮问天", "真仙试锋"} or str(family or "").strip() in {"divination", "world_boss"}
_DUNGEON_QUIET_FAILURE_SUPPRESS_WINDOW_SEC = 8
_recent_dungeon_quiet_send_blocks = {}


def _resolve_audit_priority(content, priority=None):
    explicit = str(priority or "").strip().lower()
    if explicit and explicit != "auto":
        return _AUDIT_PRIORITY_ALIASES.get(explicit, AUDIT_PRIORITY_MEDIUM)
    raw_text = str(content or "")
    if any(marker in raw_text for marker in _AUDIT_HIGH_MARKERS):
        return AUDIT_PRIORITY_HIGH
    if any(marker in raw_text for marker in _AUDIT_LOW_SELF_HEAL_MARKERS):
        return AUDIT_PRIORITY_LOW
    if any(marker in raw_text for marker in _AUDIT_MEDIUM_MARKERS):
        return AUDIT_PRIORITY_MEDIUM
    return AUDIT_PRIORITY_LOW


def _note_dungeon_quiet_send_block(command, *, send_as_id=None):
    try:
        identity_id = int(send_as_id if send_as_id is not None else (get_current_identity_id() or 0))
    except (TypeError, ValueError):
        identity_id = 0
    if identity_id <= 0:
        return
    _recent_dungeon_quiet_send_blocks[identity_id] = {
        "at": time.time(),
        "command": str(command or ""),
    }


def _should_suppress_dungeon_quiet_failure_audit(content, *, scope="auto", send_as_id=None):
    raw_text = str(content or "")
    if "发送失败" not in raw_text:
        return False
    identity_id = _resolve_log_identity(scope=scope, send_as_id=send_as_id)
    if identity_id is None:
        return False
    recent = _recent_dungeon_quiet_send_blocks.get(int(identity_id))
    if not recent:
        return False
    now = time.time()
    blocked_at = float(recent.get("at") or 0)
    if now - blocked_at > _DUNGEON_QUIET_FAILURE_SUPPRESS_WINDOW_SEC:
        _recent_dungeon_quiet_send_blocks.pop(int(identity_id), None)
        return False
    return True


def _format_admin_mentions_html():
    mentions = []
    for admin_id in sorted(int(admin_id) for admin_id in ADMIN_IDS if int(admin_id or 0) > 0):
        mentions.append(f'<a href="tg://user?id={admin_id}">@管理员</a>')
    return " ".join(mentions)


def _schedule_low_priority_audit_flush(*, force=False):
    global _low_priority_audit_flush_task
    if not _low_priority_audit_bucket:
        return
    if not force and _low_priority_audit_flush_task is not None and not _low_priority_audit_flush_task.done():
        return
    _low_priority_audit_flush_task = asyncio.create_task(_flush_low_priority_audit_after_delay())
    _background_tasks.add(_low_priority_audit_flush_task)
    _low_priority_audit_flush_task.add_done_callback(_handle_low_priority_audit_flush_done)


def _queue_low_priority_audit(message_body, plain_body):
    global _low_priority_audit_seq
    now = time.time()
    now_text = datetime.fromtimestamp(now, TZ_LOCAL).strftime("%H:%M:%S")
    key = str(plain_body or "").strip() or "-"
    row = _low_priority_audit_bucket.get(key)
    if row is None:
        _low_priority_audit_seq += 1
        row = {
            "count": 0,
            "first_ts": now_text,
            "last_ts": now_text,
            "plain": key,
            "html": message_body,
            "seq": _low_priority_audit_seq,
        }
        _low_priority_audit_bucket[key] = row
        _low_priority_audit_order.append(key)
    row["count"] += 1
    row["last_ts"] = now_text
    _schedule_low_priority_audit_flush()


def _handle_low_priority_audit_flush_done(done_task):
    global _low_priority_audit_flush_task
    _background_tasks.discard(done_task)
    if _low_priority_audit_flush_task is done_task:
        _low_priority_audit_flush_task = None
    try:
        exc = done_task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        traceback.print_exc()
        return
    if exc is not None:
        console_log(f"⚠️ 低优先级日志汇总任务异常：{_truncate_log_text(exc, limit=120)}", limit=180)
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def _snapshot_low_priority_audit_bucket():
    rows = []
    for key in list(_low_priority_audit_order):
        row = _low_priority_audit_bucket.get(key)
        if row:
            rows.append(dict(row))
    _low_priority_audit_bucket.clear()
    _low_priority_audit_order.clear()
    return rows


def _restore_low_priority_audit_rows(rows):
    global _low_priority_audit_seq
    for row in rows:
        key = str(row.get("plain") or "").strip() or "-"
        existing = _low_priority_audit_bucket.get(key)
        if existing is None:
            if int(row.get("seq") or 0) <= 0:
                _low_priority_audit_seq += 1
                row["seq"] = _low_priority_audit_seq
            _low_priority_audit_bucket[key] = dict(row)
            _low_priority_audit_order.append(key)
            continue
        existing["count"] += int(row.get("count") or 0)
        existing["last_ts"] = row.get("last_ts") or existing.get("last_ts")


def _format_low_priority_audit_summary(rows):
    total = sum(int(row.get("count") or 0) for row in rows)
    details = sorted(rows, key=lambda row: (-int(row.get("count") or 0), int(row.get("seq") or 0)))
    max_details = int(LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS or 20)
    detail_lines = []
    for row in details[:max_details]:
        count = int(row.get("count") or 0)
        last_ts = row.get("last_ts") or "?"
        text = _truncate_log_text(row.get("plain") or "-", limit=140)
        detail_lines.append(f"{last_ts} x{count} {text}")
    omitted = max(0, len(details) - len(detail_lines))
    if omitted:
        detail_lines.append(f"... 另 {omitted} 类低优先级日志未展开")
    now_text = datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
    body = "\n".join(detail_lines) if detail_lines else "无明细"
    return (
        f"<b>【🍃 低优先级日志汇总 {now_text}】</b>\n"
        f"累计 {total} 条，{len(rows)} 类。明细：\n"
        f"<pre>{html.escape(body)}</pre>"
    )


def get_low_priority_audit_pending_counts():
    total = sum(int(row.get("count") or 0) for row in _low_priority_audit_bucket.values())
    return total, len(_low_priority_audit_bucket)


def get_audit_push_status_text():
    total, kind_count = get_low_priority_audit_pending_counts()
    is_scheduled = _low_priority_audit_flush_task is not None and not _low_priority_audit_flush_task.done()
    lines = [
        "日志推送策略",
        "低优先级: 进入定时汇总，汇总里保留明细和次数。",
        "中优先级: 实时发送日志群，不 @。",
        "高优先级: 实时发送日志群，并 @ 管理员。",
        "",
        f"低优先级汇总间隔: {LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC} 秒",
        f"汇总明细上限: {LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS} 类",
        f"待汇总: {total} 条 / {kind_count} 类",
        f"定时任务: {'已排程' if is_scheduled else '未排程'}",
    ]
    rows = sorted(
        _low_priority_audit_bucket.values(),
        key=lambda row: (-int(row.get("count") or 0), int(row.get("seq") or 0)),
    )
    if rows:
        lines.extend(["", "待汇总 Top:"])
        for row in rows[:10]:
            lines.append(
                f"- {row.get('last_ts') or '?'} x{int(row.get('count') or 0)} "
                f"{_truncate_log_text(row.get('plain') or '-', limit=120)}"
            )
    return "\n".join(lines)


async def flush_low_priority_audit_summary():
    rows = _snapshot_low_priority_audit_bucket()
    if not rows:
        return True
    message = _format_low_priority_audit_summary(rows)
    try:
        ok = await _send_log_group_message(message, link_preview=False, parse_mode="HTML")
    except Exception:
        ok = False
        traceback.print_exc()
    if not ok:
        _restore_low_priority_audit_rows(rows)
        _schedule_low_priority_audit_flush(force=True)
        print("low priority audit summary failed")
    return ok


async def _flush_low_priority_audit_after_delay():
    await asyncio.sleep(LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC)
    await flush_low_priority_audit_summary()


async def send_audit_log(content, *, scope="auto", send_as_id=None, limit=220, priority="auto", buttons=None):
    if _should_suppress_dungeon_quiet_failure_audit(content, scope=scope, send_as_id=send_as_id):
        return True
    now = datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
    audit_priority = _resolve_audit_priority(content, priority)
    message_body = _format_log_message(content, scope=scope, send_as_id=send_as_id, html=True, limit=limit)
    plain_body = _format_log_message(content, scope=scope, send_as_id=send_as_id, html=False, limit=limit)
    console_log(content, scope=scope, send_as_id=send_as_id, limit=min(limit, 180))
    if audit_priority == AUDIT_PRIORITY_LOW:
        _queue_low_priority_audit(message_body, plain_body)
        return True
    attention_line = ""
    if audit_priority == AUDIT_PRIORITY_HIGH:
        mentions = _format_admin_mentions_html()
        if mentions:
            attention_line = f"\n关注：{mentions}"
    message = f"【🍃 监控日志 {now}】\n{message_body}{attention_line}"
    ok = await _send_log_group_message(message, link_preview=False, parse_mode="HTML", buttons=buttons)
    if not ok:
        print(f"send_audit_log failed | content={_truncate_log_text(content, limit=240)}")
    return ok


def console_log(content, *, scope="auto", send_as_id=None, limit=180):
    ts = datetime.now(TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")
    message = _format_log_message(content, scope=scope, send_as_id=send_as_id, limit=limit)
    print(f"[{ts}] {message}")


async def reply_log_group_message(
    event,
    text,
    *,
    audit_on_error=True,
    error_prefix="❌ 日志群回复失败",
    link_preview=True,
    scope="global",
    send_as_id=None,
    limit=350,
    parse_mode=None,
    preformatted=False,
    buttons=None,
):
    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    # forum 群需要 message_thread_id 才能回复
    reply_header = getattr(event, "reply_to", None)
    thread_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0) or int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    message = str(text or "") if preformatted else _format_log_message(text, scope=scope, send_as_id=send_as_id, limit=limit)
    ok = await _send_log_group_message(
        message,
        reply_to_msg_id=reply_to_msg_id,
        message_thread_id=thread_id,
        link_preview=link_preview,
        parse_mode=parse_mode,
        buttons=buttons,
    )
    if not ok and reply_to_msg_id:
        ok = await _send_log_group_message(message, link_preview=link_preview, parse_mode=parse_mode, buttons=buttons)
    if ok:
        return True
    print(f"reply_log_group_message failed | text={_truncate_log_text(text, limit=240)}")
    if audit_on_error:
        await send_audit_log(error_prefix, scope="global")
    return False


def _map_forum_topics_error(error):
    error_text = str(error or "").strip()
    error_code = error_text.upper()
    if "CHANNEL_FORUM_MISSING" in error_code or ("FORUM" in error_code and "MISSING" in error_code):
        return "该群未开启话题功能"
    if any(code in error_code for code in {"CHANNEL_INVALID", "CHANNEL_PRIVATE", "CHAT_ID_INVALID", "PEER_ID_INVALID"}):
        return "游戏群聊不存在或当前账号无权访问"
    if "TOPIC" in error_code and "INVALID" in error_code:
        return "话题接口返回无效结果"
    return error_text or "读取话题列表失败"


async def fetch_forum_topics(group_id):
    raw_group_id = str(group_id or "").strip()
    if not raw_group_id:
        return False, "游戏群聊 ID 不能为空", []
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        return False, "游戏群聊 ID 必须是整数", []
    if group_id == 0:
        return False, "游戏群聊 ID 不能为 0", []

    try:
        _tc = _get_any_authed_client()
        peer = await _tc.get_input_entity(group_id)
        entity = await _tc.get_entity(peer)
    except Exception:
        return False, "游戏群聊不存在或当前账号无权访问", []

    request_cls = getattr(getattr(functions, "channels", None), "GetForumTopicsRequest", None)
    if request_cls is None:
        request_cls = getattr(getattr(functions, "messages", None), "GetForumTopicsRequest", None)
    if request_cls is None:
        return False, "当前 Telethon 版本不支持自动读取话题列表", []

    group_title = str(getattr(entity, "title", "") or "").strip() or str(group_id)
    if not bool(getattr(entity, "forum", False)):
        return False, f"群聊[{group_title}]未开启话题功能", []

    request_kwargs = {
        "q": "",
        "offset_date": None,
        "offset_id": 0,
        "offset_topic": 0,
        "limit": 100,
    }
    request = None
    for peer_key in ("channel", "peer"):
        try:
            request = request_cls(**{peer_key: peer, **request_kwargs})
            break
        except TypeError as e:
            if f"unexpected keyword argument '{peer_key}'" not in str(e):
                return False, _map_forum_topics_error(e), []
    if request is None:
        return False, "当前 Telethon 版本不支持自动读取话题列表", []

    try:
        result = await _tc(request)
    except Exception as e:
        return False, _map_forum_topics_error(e), []

    topics = []
    seen_topic_ids = set()
    for topic in getattr(result, "topics", None) or []:
        topic_id = int(getattr(topic, "id", 0) or 0)
        if topic_id <= 0 or topic_id in seen_topic_ids:
            continue
        seen_topic_ids.add(topic_id)
        title = str(getattr(topic, "title", "") or "").strip() or f"话题 {topic_id}"
        if bool(getattr(topic, "hidden", False)):
            title = f"{title}（已隐藏）"
        elif bool(getattr(topic, "closed", False)):
            title = f"{title}（已关闭）"
        topics.append({
            "id": topic_id,
            "title": title,
            "top_message": int(getattr(topic, "top_message", 0) or 0),
        })

    topics.sort(key=lambda item: item["id"])
    return True, f"已读取群聊[{group_title}]的话题列表，共 {len(topics)} 个", topics


def _coerce_ui_admin_sender_id(sender_id):
    try:
        value = int(sender_id or 0)
    except (TypeError, ValueError):
        return 0
    return value if value in ADMIN_IDS else 0


def _load_ui_auth_state(now=None):
    global _UI_AUTH_STATE_LOADED
    if _UI_AUTH_STATE_LOADED:
        return
    if now is None:
        now = time.time()
    _UI_AUTH_STATE_LOADED = True
    try:
        with open(_UI_AUTH_STATE_FILE, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return
    if not isinstance(payload, dict):
        return

    login_tokens = payload.get("login_tokens")
    if isinstance(login_tokens, dict):
        for token, item in login_tokens.items():
            if not isinstance(item, dict):
                continue
            sender_id = _coerce_ui_admin_sender_id(item.get("sender_id"))
            created_at = _coerce_ui_auth_float(item.get("created_at"), now)
            last_seen_at = _coerce_ui_auth_float(item.get("last_seen_at"), created_at)
            if sender_id <= 0:
                continue
            if now - last_seen_at > UI_AUTH_IDLE_TIMEOUT_SEC:
                continue
            token_text = str(token or "").strip()
            if not token_text:
                continue
            _ui_login_tokens[token_text] = {
                "sender_id": sender_id,
                "created_at": created_at,
                "last_seen_at": last_seen_at,
            }

    sessions = payload.get("sessions")
    if isinstance(sessions, dict):
        for token, item in sessions.items():
            if not isinstance(item, dict):
                continue
            sender_id = _coerce_ui_admin_sender_id(item.get("sender_id"))
            created_at = _coerce_ui_auth_float(item.get("created_at"), now)
            last_seen_at = _coerce_ui_auth_float(item.get("last_seen_at"), created_at)
            if sender_id <= 0:
                continue
            if now - last_seen_at > UI_AUTH_SESSION_TIMEOUT_SEC:
                continue
            token_text = str(token or "").strip()
            if not token_text:
                continue
            seen_keys = []
            for alert_key in item.get("seen_startup_alert_keys") or []:
                text = str(alert_key or "").strip()
                if text and text not in seen_keys:
                    seen_keys.append(text)
            _ui_sessions[token_text] = {
                "sender_id": sender_id,
                "created_at": created_at,
                "last_seen_at": last_seen_at,
                "seen_startup_alert_keys": seen_keys,
            }


def _save_ui_auth_state(now=None, *, force=False):
    global _UI_AUTH_STATE_LAST_SAVED_AT
    if now is None:
        now = time.time()
    if not force and now - float(_UI_AUTH_STATE_LAST_SAVED_AT or 0) < _UI_AUTH_STATE_SAVE_INTERVAL_SEC:
        return
    _UI_AUTH_STATE_LAST_SAVED_AT = float(now)
    tmp_path = ""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        payload = {
            "login_tokens": {
                token: {
                    "sender_id": int(item.get("sender_id") or 0),
                    "created_at": float(item.get("created_at") or 0),
                    "last_seen_at": float(item.get("last_seen_at") or 0),
                }
                for token, item in _ui_login_tokens.items()
                if int(item.get("sender_id") or 0) > 0
            },
            "sessions": {
                token: {
                    "sender_id": int(item.get("sender_id") or 0),
                    "created_at": float(item.get("created_at") or 0),
                    "last_seen_at": float(item.get("last_seen_at") or 0),
                    "seen_startup_alert_keys": [
                        str(alert_key or "").strip()
                        for alert_key in (item.get("seen_startup_alert_keys") or [])
                        if str(alert_key or "").strip()
                    ],
                }
                for token, item in _ui_sessions.items()
                if int(item.get("sender_id") or 0) > 0
            },
        }
        tmp_path = f"{_UI_AUTH_STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, _UI_AUTH_STATE_FILE)
        try:
            os.chmod(_UI_AUTH_STATE_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def issue_ui_login_token(sender_id, now=None):
    admin_id = _coerce_ui_admin_sender_id(sender_id)
    if admin_id <= 0:
        raise ValueError("Unauthorized UI login token requester")
    if now is None:
        now = time.time()
    _load_ui_auth_state(now)
    token = _new_runtime_token(_ui_login_tokens)
    _ui_login_tokens[token] = {
        "sender_id": admin_id,
        "created_at": now,
        "last_seen_at": now,
    }
    _save_ui_auth_state(now, force=True)
    return token


def build_ui_login_url(token):
    return f"{UI_PUBLIC_BASE_URL}/#token={quote((token or '').strip(), safe='')}"


def redeem_ui_login_token(token, now=None):
    if now is None:
        now = time.time()
    _load_ui_auth_state(now)
    stored_token, payload = _secure_lookup(_ui_login_tokens, token)
    if not stored_token or not payload:
        return None
    if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC:
        _ui_login_tokens.pop(stored_token, None)
        _save_ui_auth_state(now, force=True)
        return None

    sender_id = _coerce_ui_admin_sender_id(payload.get("sender_id"))
    if sender_id <= 0:
        _ui_login_tokens.pop(stored_token, None)
        _save_ui_auth_state(now, force=True)
        return None

    _ui_login_tokens.pop(stored_token, None)
    session_token = _new_runtime_token(_ui_sessions)
    _ui_sessions[session_token] = {
        "sender_id": sender_id,
        "created_at": now,
        "last_seen_at": now,
        "seen_startup_alert_keys": [],
    }
    _save_ui_auth_state(now, force=True)
    return session_token


def validate_ui_session(session_token, now=None):
    if now is None:
        now = time.time()
    _load_ui_auth_state(now)
    stored_token, payload = _secure_lookup(_ui_sessions, session_token)
    if not stored_token or not payload:
        return None
    if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_SESSION_TIMEOUT_SEC:
        _ui_sessions.pop(stored_token, None)
        _save_ui_auth_state(now, force=True)
        return None
    if _coerce_ui_admin_sender_id(payload.get("sender_id")) <= 0:
        _ui_sessions.pop(stored_token, None)
        _save_ui_auth_state(now, force=True)
        return None
    return {
        "session_token": stored_token,
        **payload,
    }


def touch_ui_session(session_token, now=None):
    if now is None:
        now = time.time()
    session = validate_ui_session(session_token, now)
    if not session:
        return None
    _ui_sessions[session["session_token"]]["last_seen_at"] = now
    session["last_seen_at"] = now
    _save_ui_auth_state(now)
    return session


def gc_ui_login_tokens(now=None):
    if now is None:
        now = time.time()
    _load_ui_auth_state(now)
    expired = [
        token
        for token, payload in _ui_login_tokens.items()
        if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC
        or _coerce_ui_admin_sender_id(payload.get("sender_id")) <= 0
    ]
    for token in expired:
        _ui_login_tokens.pop(token, None)
    if expired:
        _save_ui_auth_state(now, force=True)
    return len(expired)


def gc_ui_sessions(now=None):
    if now is None:
        now = time.time()
    _load_ui_auth_state(now)
    expired = [
        token
        for token, payload in _ui_sessions.items()
        if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_SESSION_TIMEOUT_SEC
        or _coerce_ui_admin_sender_id(payload.get("sender_id")) <= 0
    ]
    for token in expired:
        _ui_sessions.pop(token, None)
    if expired:
        _save_ui_auth_state(now, force=True)
    return len(expired)


def clear_ui_auth_state():
    global _UI_AUTH_STATE_LOADED, _UI_AUTH_STATE_LAST_SAVED_AT
    _ui_login_tokens.clear()
    _ui_sessions.clear()
    _UI_AUTH_STATE_LOADED = True
    _UI_AUTH_STATE_LAST_SAVED_AT = 0.0
    try:
        os.remove(_UI_AUTH_STATE_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def consume_unseen_startup_alerts(session_token, alerts):
    session = validate_ui_session(session_token)
    if not session:
        return []
    stored_session = _ui_sessions.get(session["session_token"], {})
    seen_keys = {
        str(alert_key)
        for alert_key in stored_session.get("seen_startup_alert_keys", [])
        if str(alert_key).strip()
    }
    unseen_alerts = []
    for alert in alerts or []:
        alert_key = str((alert or {}).get("key") or "").strip()
        if not alert_key or alert_key in seen_keys:
            continue
        unseen_alerts.append(alert)
        seen_keys.add(alert_key)
    stored_session["seen_startup_alert_keys"] = sorted(seen_keys)
    _save_ui_auth_state(force=True)
    return unseen_alerts


def _extract_sent_message_id(result):
    direct_msg_id = int(getattr(result, "id", 0) or 0)
    if direct_msg_id > 0:
        return direct_msg_id

    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id > 0:
            return message_id
        update_id = int(getattr(update, "id", 0) or 0)
        if update_id > 0:
            return update_id
    return 0


def _is_account_session_error(error):
    error_text = str(error or "")
    error_code = error_text.upper()
    markers = (
        "THE KEY IS NOT REGISTERED IN THE SYSTEM",
        "AUTH_KEY_UNREGISTERED",
        "AUTHKEYUNREGISTERED",
        "AUTH_KEY_DUPLICATED",
        "AUTHORIZATION HAS BEEN INVALIDATED",
        "UNAUTHORIZED",
        "SESSIONREVOKED",
        "SESSION_REVOKED",
        "USERDEACTIVATED",
        "USER_DEACTIVATED",
        "USER TERMINATING ALL SESSIONS",
        "PLEASE ENTER YOUR PHONE",
        "EOF WHEN READING A LINE",
    )
    return any(marker in error_code for marker in markers)


def is_account_session_error(error):
    return _is_account_session_error(error)


def _compact_account_error(error):
    return _truncate_log_text(str(error or "账号 session 不可用"), limit=72)


async def _ensure_account_client_ready(tc):
    is_connected = getattr(tc, "is_connected", None)
    if callable(is_connected) and not is_connected():
        await tc.connect()
    is_user_authorized = getattr(tc, "is_user_authorized", None)
    if callable(is_user_authorized) and not await is_user_authorized():
        raise RuntimeError("UNAUTHORIZED: 账号 session 未授权，请重新登录")


async def _log_account_offline_blocked(command, *, send_as_id, account_id, reason, force=False):
    now = time.time()
    audit_key = (int(account_id), int(send_as_id))
    last_at = float(_ACCOUNT_OFFLINE_AUDIT_LAST.get(audit_key, 0) or 0)
    if not force and now - last_at < ACCOUNT_OFFLINE_AUDIT_INTERVAL_SEC:
        return
    _ACCOUNT_OFFLINE_AUDIT_LAST[audit_key] = now
    label = get_send_as_label(send_as_id)
    await send_audit_log(
        (
            f"⏸ 账号离线熔断：{label}｜acc={int(account_id)}｜"
            f"跳过 {_truncate_log_text(command, limit=32)}｜{_truncate_log_text(reason, limit=72)}"
        ),
        scope="identity",
        send_as_id=send_as_id,
        limit=240,
    )


def _is_weakness_reply(text):
    raw = str(text or "")
    if "虚弱状态" not in raw:
        return False
    if "暂时无法运转灵力" in raw and "静养" in raw:
        return True
    if "陷入了" in raw and "【虚弱状态】" in raw and ("元气大伤" in raw or "修为损失" in raw):
        return True
    return False


def _is_jingsi_busy_reply(text):
    raw = str(text or "")
    return "静思崖面壁悟道" in raw and "无法进行大部分操作" in raw


def _is_jingsi_interrupt_reply(text):
    raw = str(text or "")
    return "心乱如麻" in raw and "强行中断了感悟" in raw and "离开了静思崖" in raw


def _weakness_until_from_text(text, now=None):
    if now is None:
        now = time.time()
    wait_sec = parse_wait_time(text) if has_wait_time(text) else 0
    if wait_sec <= 0:
        wait_sec = WEAKNESS_DEFAULT_SEC
    return float(now + wait_sec + WEAKNESS_BUFFER_SEC)


def note_identity_weakness(text, now=None, send_as_id=None, *, source="reply"):
    is_jingsi_interrupt = _is_jingsi_interrupt_reply(text)
    is_jingsi_busy = _is_jingsi_busy_reply(text)
    is_weakness = _is_weakness_reply(text)
    if not (is_weakness or is_jingsi_busy or is_jingsi_interrupt):
        return False
    if now is None:
        now = time.time()
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    try:
        send_as_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        send_as_id = 0
    if send_as_id <= 0 or not has_identity(send_as_id):
        return False

    identity_state = get_identity_state(send_as_id)
    if is_jingsi_interrupt:
        if str(identity_state.get("weak_source") or "") != "jingsi":
            return True
        identity_state["weak_until"] = 0
        identity_state["weak_reason"] = ""
        identity_state["weak_source"] = ""
        identity_state["weak_last_block_log_at"] = 0
        mark_dirty()
        _fire_and_forget(
            send_audit_log(
                "🚫 静思悟道已中断，恢复该身份自动指令。",
                scope="identity",
                send_as_id=send_as_id,
                limit=220,
            )
        )
        return True

    until = _weakness_until_from_text(text, now)
    if until <= float(identity_state.get("weak_until", 0) or 0):
        return True
    identity_state["weak_until"] = until
    identity_state["weak_reason"] = _truncate_log_text(text, limit=120)
    identity_state["weak_source"] = "jingsi" if is_jingsi_busy else str(source or "reply")
    identity_state["weak_last_block_log_at"] = 0
    mark_dirty()
    status_label = "静思悟道" if is_jingsi_busy else "虚弱状态"
    _fire_and_forget(
        send_audit_log(
            f"🚫 检测到{status_label}，暂停该身份自动指令至 {fmt_abs_ts(until)}（{fmt_remaining(until)}）。",
            scope="identity",
            send_as_id=send_as_id,
            limit=260,
        )
    )
    return True


def is_identity_weak(send_as_id=None, now=None):
    if now is None:
        now = time.time()
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    try:
        send_as_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        return False
    if send_as_id <= 0 or not has_identity(send_as_id):
        return False

    identity_state = get_identity_state(send_as_id)
    until = float(identity_state.get("weak_until", 0) or 0)
    if until <= 0:
        return False
    if now < until:
        return True
    identity_state["weak_until"] = 0
    identity_state["weak_reason"] = ""
    identity_state["weak_source"] = ""
    identity_state["weak_last_block_log_at"] = 0
    mark_dirty()
    return False


def _command_matches_prefixes(command, prefixes):
    raw = str(command or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in prefixes)


def _is_known_replica_choice_command(command):
    raw = str(command or "").strip()
    if raw in {".选择 强行摘取", ".选择 静待时机"}:
        return True
    suffix = raw.removeprefix(".选择 岔路")
    return suffix != raw and suffix.isdigit()


def _weakness_allows_command(command, send_as_id=None):
    if send_as_id is not None:
        identity_state = get_identity_state(send_as_id)
        if str(identity_state.get("weak_source") or "") == "jingsi":
            return _command_matches_prefixes(command, BUSY_CRITICAL_ALLOWED_PREFIXES)
    return _command_matches_prefixes(command, WEAKNESS_ALLOWED_PREFIXES)


async def _log_weakness_blocked(command, *, send_as_id):
    now = time.time()
    identity_state = get_identity_state(send_as_id)
    last_at = float(identity_state.get("weak_last_block_log_at", 0) or 0)
    if now - last_at < WEAKNESS_BLOCK_AUDIT_INTERVAL_SEC:
        return
    identity_state["weak_last_block_log_at"] = now
    mark_dirty()
    until = float(identity_state.get("weak_until", 0) or 0)
    status_label = "静思悟道暂停" if str(identity_state.get("weak_source") or "") == "jingsi" else "虚弱状态"
    await send_audit_log(
        f"🚫 {status_label}拦截：{_truncate_log_text(command, limit=32)}｜恢复 {fmt_abs_ts(until)}（{fmt_remaining(until)}）",
        scope="identity",
        send_as_id=send_as_id,
        limit=260,
    )


async def _dungeon_quiet_blocks_send(command, priority, send_as_id=None):
    if priority in {SEND_PRIORITY_P0, SEND_PRIORITY_PROBE}:
        return False
    if _command_matches_prefixes(command, DUNGEON_QUIET_ALLOWED_PREFIXES):
        return False
    if _is_known_replica_choice_command(command):
        return False
    if not is_dungeon_quiet_active():
        return False
    _note_dungeon_quiet_send_block(command, send_as_id=send_as_id)
    if should_log_dungeon_quiet_block():
        await send_audit_log(
            (
                f"🤫 {get_dungeon_quiet_reason() or '副本静场令'}生效中，暂缓普通指令："
                f"{_truncate_log_text(command, limit=32)}｜恢复 {format_dungeon_quiet_until()}"
            ),
            scope="identity",
            send_as_id=send_as_id,
            limit=260,
        )
    return True


async def send_game_command(
    command,
    track=True,
    reply_to=None,
    send_as_id=None,
    priority=None,
    max_retry=None,
    *,
    reply_timeout=None,
    intent=None,
    source_module=None,
    op_id=None,
    chain_id=None,
    delete_policy=None,
):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    topic_id = get_game_topic_id()
    send_priority = _normalize_send_priority(command, priority=priority)
    account_id = int(get_identity_account(send_as_id) or 0)
    send_intent = _normalize_send_intent(
        command,
        intent=intent,
        source_module=source_module,
        op_id=op_id,
        chain_id=chain_id,
        delete_policy=delete_policy,
    )

    try:
        if not get_global_enabled() and send_priority not in {SEND_PRIORITY_P0, SEND_PRIORITY_PROBE}:
            return None

        if await _dungeon_quiet_blocks_send(command, send_priority, send_as_id=send_as_id):
            return None

        if account_id and is_account_offline(account_id):
            await _log_account_offline_blocked(
                command,
                send_as_id=send_as_id,
                account_id=account_id,
                reason=get_account_offline_reason(account_id) or "账号离线",
            )
            return None

        if is_identity_weak(send_as_id) and not _weakness_allows_command(command, send_as_id=send_as_id):
            await _log_weakness_blocked(command, send_as_id=send_as_id)
            return None

        _refresh_bot_health_timeout_before_send()
        if _bot_health_blocks_send(send_priority):
            await _log_bot_health_blocked_send(command, send_as_id=send_as_id)
            return None

        guard_allowed, guard_reason = action_guard_before_send(command, send_as_id=send_as_id)
        if not guard_allowed:
            if action_guard_should_log_block(command, send_as_id=send_as_id):
                await send_audit_log(
                    f"🧯 安全锁拦截：{_truncate_log_text(command, limit=32)}｜{guard_reason}",
                    scope="identity",
                    send_as_id=send_as_id,
                    limit=260,
                )
            return None

        async with _send_slot(send_priority, command=command, send_as_id=send_as_id):
            if not get_global_enabled() and send_priority not in {SEND_PRIORITY_P0, SEND_PRIORITY_PROBE}:
                return None

            if await _dungeon_quiet_blocks_send(command, send_priority, send_as_id=send_as_id):
                return None

            _refresh_bot_health_timeout_before_send()
            if account_id and is_account_offline(account_id):
                await _log_account_offline_blocked(
                    command,
                    send_as_id=send_as_id,
                    account_id=account_id,
                    reason=get_account_offline_reason(account_id) or "账号离线",
                )
                return None
            if is_identity_weak(send_as_id) and not _weakness_allows_command(command, send_as_id=send_as_id):
                await _log_weakness_blocked(command, send_as_id=send_as_id)
                return None
            if _bot_health_blocks_send(send_priority):
                await _log_bot_health_blocked_send(command, send_as_id=send_as_id)
                return None
            guard_allowed, guard_reason = action_guard_before_send(command, send_as_id=send_as_id)
            if not guard_allowed:
                if action_guard_should_log_block(command, send_as_id=send_as_id):
                    await send_audit_log(
                        f"🧯 安全锁拦截：{_truncate_log_text(command, limit=32)}｜{guard_reason}",
                        scope="identity",
                        send_as_id=send_as_id,
                        limit=260,
                    )
                return None

            if account_id:
                active_client = get_registered_client(account_id)
                if active_client is None:
                    reason = "账号 client 未注册或启动失败"
                    mark_account_offline(account_id, reason)
                    await _log_account_offline_blocked(
                        command,
                        send_as_id=send_as_id,
                        account_id=account_id,
                        reason=reason,
                        force=True,
                    )
                    return None
                try:
                    await _ensure_account_client_ready(active_client)
                except Exception as e:
                    reason = _compact_account_error(e)
                    if _is_account_session_error(e):
                        mark_account_offline(account_id, reason)
                        await _log_account_offline_blocked(
                            command,
                            send_as_id=send_as_id,
                            account_id=account_id,
                            reason=reason,
                            force=True,
                        )
                    else:
                        await send_audit_log(
                            (
                                f"⚠️ 账号连接检查失败，稍后重试：acc={account_id}｜"
                                f"{reason}｜跳过 {_truncate_log_text(command, limit=32)}"
                            ),
                            scope="identity",
                            send_as_id=send_as_id,
                            limit=240,
                        )
                    return None
            else:
                active_client = _get_any_authed_client()
            game_group_id = get_game_group_id()
            if not game_group_id:
                raise ValueError("游戏群聊 ID 未配置，请在 UI 基础配置中设置")
            try:
                peer = await active_client.get_input_entity(game_group_id)
            except ValueError:
                await active_client.get_dialogs()
                peer = await active_client.get_input_entity(game_group_id)
            send_as_peer = await active_client.get_input_entity(send_as_id)
            reply_to_spec = None
            if reply_to:
                reply_to_spec = types.InputReplyToMessage(
                    reply_to_msg_id=int(reply_to),
                    top_msg_id=int(topic_id or 0) or None,
                )
            elif topic_id > 0:
                reply_to_spec = types.InputReplyToMessage(reply_to_msg_id=int(topic_id))
            try:
                result = await active_client(
                    functions.messages.SendMessageRequest(
                        peer=peer,
                        message=command,
                        reply_to=reply_to_spec,
                        send_as=send_as_peer,
                    )
                )
            except FloodWaitError as flood_err:
                mark_bot_health_suspect(
                    f"TG FloodWait {int(flood_err.seconds)}s",
                    reference_at=time.time(),
                )
                await send_audit_log(
                    f"⏸ TG FloodWait {int(flood_err.seconds)}s，普通指令已暂停等待恢复：{_truncate_log_text(command, limit=24)}",
                    scope="identity", send_as_id=send_as_id, limit=220,
                )
                return None
            msg_id = _extract_sent_message_id(result)
            if msg_id <= 0:
                raise ValueError("无法从发送结果中解析消息 ID")
            _append_sent_message_log(
                msg_id,
                command,
                send_as_id,
                reply_to_msg_id=int(reply_to or 0),
                priority=send_priority,
                track=track,
                intent=send_intent,
            )
            sent_at = time.time()
            msg = SimpleNamespace(id=msg_id, sent_at=sent_at)
            action_guard_note_sent(command, send_as_id, msg_id, sent_at=sent_at)
            with use_identity(send_as_id) as identity_state:
                identity_state["my_msg_ids"][msg_id] = sent_at
                if track:
                    if reply_timeout is not None:
                        try:
                            timeout = max(1, int(float(reply_timeout)))
                        except (TypeError, ValueError):
                            timeout = random.randint(RETRY_MIN_SEC, RETRY_MAX_SEC)
                    else:
                        timeout = random.randint(RETRY_MIN_SEC, RETRY_MAX_SEC)
                    retry_limit = max(0, int(max_retry if max_retry is not None else RETRY_LIMIT))
                    if action_guard_is_guarded_command(command):
                        next_allowed_at = action_guard_next_allowed_at(command, send_as_id=send_as_id)
                        if next_allowed_at > sent_at:
                            timeout = max(1, int(next_allowed_at - sent_at) + 1)
                    pending_item = {
                        "cmd": command,
                        "sent_at": sent_at,
                        "retry": 0,
                        "timeout": timeout,
                        "reply_to_msg_id": int(reply_to or 0),
                        "priority": send_priority,
                        "max_retry": retry_limit,
                    }
                    pending_item.update(send_intent)
                    identity_state["pending_tasks"][msg_id] = pending_item
                mark_dirty()
            note_game_command_sent(command, sent_at=sent_at, priority=send_priority)
            family = resolve_reply_family(command)
            if family:
                track_reply_chain_message(msg_id, send_as_id, family, root_msg_id=msg_id)
            _notify_game_command_sent_observers(
                command,
                send_as_id,
                sent_at,
                msg_id,
                track=track,
                reply_to=int(reply_to or 0),
                priority=send_priority,
                max_retry=max_retry,
                **send_intent,
            )
            return msg
    except Exception as e:
        if account_id and _is_account_session_error(e):
            reason = _compact_account_error(e)
            mark_account_offline(account_id, reason)
            await _log_account_offline_blocked(
                command,
                send_as_id=send_as_id,
                account_id=account_id,
                reason=reason,
                force=True,
            )
            return None
        await send_audit_log(
            (
                f"❌ 指令发送失败：{_truncate_log_text(command, limit=48)} | "
                f"{_truncate_log_text(e, limit=72)} | "
                f"acc={account_id} group={get_game_group_id()} topic={topic_id}"
            ),
            scope="identity",
            send_as_id=send_as_id,
            limit=240,
        )
        return None


def _get_tracked_identity_message_ids(identity_state):
    tracked_ids = {
        int(identity_state.get("last_checkin_msg_id", 0) or 0),
        int(identity_state.get("last_sect_teach_msg_id", 0) or 0),
        abs(int(identity_state.get("last_tower_msg_id", 0) or 0)),
        int(identity_state.get("last_identity_info_msg_id", 0) or 0),
        *(int(msg_id or 0) for msg_id in identity_state.get("identity_info_reply_msg_ids", [])),
    }
    tracked_ids.discard(0)
    return tracked_ids


def find_identity_by_msg_id(msg_id):
    resolved_send_as_id, _matched_via = _resolve_identity_message_owner(msg_id)
    return resolved_send_as_id


def is_reply_to_identity_message(reply_to, send_as_id):
    if not reply_to:
        return False
    resolved_send_as_id, _matched_via = _resolve_identity_message_owner(getattr(reply_to, "id", 0), send_as_id=send_as_id)
    return resolved_send_as_id == int(send_as_id or 0)


def gc_my_msg_ids(now=None, send_as_id=None):
    if now is None:
        now = time.time()

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    changed = False
    for identity_id in target_ids:
        if not has_identity(identity_id):
            continue
        with use_identity(identity_id) as identity_state:
            expired_ids = [msg_id for msg_id, sent_at in identity_state["my_msg_ids"].items() if now - sent_at > MY_MSG_TTL]
            if expired_ids:
                changed = True
                for msg_id in expired_ids:
                    identity_state["my_msg_ids"].pop(msg_id, None)

            if len(identity_state["my_msg_ids"]) > MY_MSG_MAX:
                sorted_items = sorted(identity_state["my_msg_ids"].items(), key=lambda x: x[1], reverse=True)
                trimmed_items = dict(sorted_items[:MY_MSG_MAX])
                if trimmed_items != identity_state["my_msg_ids"]:
                    identity_state["my_msg_ids"] = trimmed_items
                    changed = True
    _gc_reply_chain_tracker(now)
    if changed:
        mark_dirty()


def clear_pending_by_reply(reply_to=None, send_as_id=None, reply_context=None):
    if reply_context is None:
        reply_context = get_reply_context(reply_to, send_as_id=send_as_id)

    resolved_send_as_id = int((reply_context or {}).get("send_as_id") or 0)
    family = (reply_context or {}).get("family") or None
    reply_to_msg_id = int((reply_context or {}).get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0)
    if resolved_send_as_id <= 0 or reply_to_msg_id <= 0:
        return {"send_as_id": None, "family": family, "removed_ids": [], "matched": False}

    removed_ids = []
    with use_identity(resolved_send_as_id):
        if reply_to_msg_id in state["pending_tasks"]:
            state["pending_tasks"].pop(reply_to_msg_id, None)
            removed_ids.append(reply_to_msg_id)

        if family:
            family_commands = get_reply_family_commands(family)
            for msg_id, pending in list(state["pending_tasks"].items()):
                pending_cmd = get_pending_command(pending)
                if pending_cmd in family_commands or resolve_reply_family(pending_cmd) == family:
                    state["pending_tasks"].pop(msg_id, None)
                    removed_ids.append(msg_id)

        if removed_ids:
            mark_dirty()

    unique_removed_ids = sorted({int(msg_id) for msg_id in removed_ids if int(msg_id or 0) > 0})
    return {
        "send_as_id": resolved_send_as_id,
        "family": family,
        "removed_ids": unique_removed_ids,
        "matched": bool(unique_removed_ids or family),
    }


def _is_pending_consumed(identity_state, msg_id, family):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return True
    pending_tasks = identity_state.get("pending_tasks", {})
    if msg_id not in pending_tasks:
        return True
    if family:
        family_commands = get_reply_family_commands(family)
        same_family_items = [
            (pending_msg_id, pending)
            for pending_msg_id, pending in pending_tasks.items()
            if (get_pending_command(pending) in family_commands)
            or (resolve_reply_family(get_pending_command(pending)) == family)
        ]
        if not same_family_items:
            return True
        my_sent_at = float((pending_tasks.get(msg_id) or {}).get("sent_at", 0) or 0)
        if any(
            int(pending_msg_id or 0) != msg_id
            and float((pending or {}).get("sent_at", 0) or 0) > my_sent_at + 60
            for pending_msg_id, pending in same_family_items
        ):
            return True
    return False


def _refresh_identity_info_retry_tracking(identity_state, new_msg_id, now):
    if new_msg_id <= 0:
        return
    tracked_ids = {
        *(int(tracked_id or 0) for tracked_id in identity_state.get("identity_info_reply_msg_ids", [])),
        new_msg_id,
    }
    tracked_ids.discard(0)
    identity_state["last_identity_info_msg_id"] = new_msg_id
    identity_state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
    identity_state["identity_info_followup_due_at"] = 0
    identity_state["identity_info_last_requested_at"] = now


async def run_retry_scheduler(now, send_as_id=None):
    if should_pause_for_bot_health():
        return
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    for identity_id in target_ids:
        if not has_identity(identity_id) or not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id) as identity_state:
            retry_items = list(identity_state["pending_tasks"].items())
        for msg_id, item in retry_items:
            cmd = get_pending_command(item)
            if not cmd:
                with use_identity(identity_id) as identity_state:
                    if msg_id in identity_state["pending_tasks"]:
                        identity_state["pending_tasks"].pop(msg_id, None)
                        mark_dirty()
                continue
            send_time = float((item or {}).get("sent_at", 0) or 0)
            threshold = float((item or {}).get("timeout", 0) or 0)
            retry = int((item or {}).get("retry", 0) or 0)
            family = resolve_reply_family(cmd)

            if now - send_time <= threshold or not has_identity(identity_id):
                continue

            with use_identity(identity_id) as identity_state:
                current_item = identity_state["pending_tasks"].get(msg_id)
                if not current_item:
                    continue
                if _is_pending_consumed(identity_state, msg_id, family):
                    identity_state["pending_tasks"].pop(msg_id, None)
                    mark_dirty()
                    continue
                retry = int(current_item.get("retry", retry) or 0)
                cmd = get_pending_command(current_item) or cmd
                saved_priority = current_item.get("priority") or None

                if get_bot_last_seen_at() < float(send_time or 0):
                    changed = mark_bot_health_suspect(
                        f"指令 {_truncate_log_text(cmd, limit=32)} 超时且无 bot 发言",
                        reference_at=send_time,
                        now=now,
                    )
                    if changed:
                        await send_audit_log(
                            f"🩺 天尊疑似静默：{mono(_truncate_log_text(cmd, limit=40))} 超时 {threshold}s 后仍无 bot 发言，已暂停普通补发。",
                            scope="identity",
                            send_as_id=identity_id,
                            limit=260,
                        )
                    identity_state["pending_tasks"].pop(msg_id, None)
                    mark_dirty()
                    continue

                retry_limit = max(0, int(current_item.get("max_retry", RETRY_LIMIT) or 0))
                if retry >= retry_limit:
                    if family == "deep_retreat":
                        identity_state["pending_tasks"].pop(msg_id, None)
                        mark_dirty()
                        continue
                    if retry_limit <= 0:
                        if _stateful_no_retry_timeout_is_module_managed(current_item, family):
                            console_log(
                                f"🧯 指令 {_truncate_log_text(cmd, limit=40)} 超时无响应，交由模块状态机继续。",
                                scope="identity",
                                send_as_id=identity_id,
                            )
                        else:
                            await send_audit_log(
                                f"🧯 指令 {mono(_truncate_log_text(cmd, limit=40))} 超时无响应，已停补发。",
                                scope="identity",
                                send_as_id=identity_id,
                            )
                        identity_state["pending_tasks"].pop(msg_id, None)
                        mark_dirty()
                        continue
                    await send_audit_log(
                        f"🧯 指令 {mono(_truncate_log_text(cmd, limit=40))} 重试 {retry_limit} 次仍无响应，已停补发。",
                        scope="identity",
                        send_as_id=identity_id,
                    )
                    if _is_identity_refresh_command(cmd):
                        identity_state["last_identity_info_msg_id"] = 0
                        identity_state["identity_info_reply_msg_ids"] = []
                        identity_state["identity_info_followup_due_at"] = 0
                        identity_state["identity_info_last_error"] = IDENTITY_INFO_REFRESH_ERROR_TEXT
                    identity_state["pending_tasks"].pop(msg_id, None)
                    mark_dirty()
                    continue

            console_log(
                f"⚠️ 指令 {_truncate_log_text(cmd, limit=40)} 超时 {threshold}s，正在补发。",
                scope="identity",
                send_as_id=identity_id,
            )
            reply_to_kwargs = {}
            reply_to_msg_id = int((current_item or {}).get("reply_to_msg_id", 0) or 0)
            if reply_to_msg_id > 0:
                reply_to_kwargs["reply_to"] = reply_to_msg_id
            new_msg = await send_game_command(
                cmd,
                send_as_id=identity_id,
                priority=SEND_PRIORITY_RETRY,
                max_retry=retry_limit,
                reply_timeout=threshold,
                **reply_to_kwargs,
                **_pending_send_intent_kwargs(current_item),
            )
            if not has_identity(identity_id):
                continue
            with use_identity(identity_id) as identity_state:
                current_item = identity_state["pending_tasks"].get(msg_id)
                if current_item:
                    identity_state["pending_tasks"].pop(msg_id, None)
                if new_msg and new_msg.id in identity_state["pending_tasks"]:
                    identity_state["pending_tasks"][new_msg.id]["retry"] = retry + 1
                    identity_state["pending_tasks"][new_msg.id]["max_retry"] = retry_limit
                    identity_state["pending_tasks"][new_msg.id]["timeout"] = threshold
                    if _is_identity_refresh_command(cmd):
                        sent_at = float(getattr(new_msg, "sent_at", 0) or time.time())
                        _refresh_identity_info_retry_tracking(identity_state, int(new_msg.id), sent_at)
                mark_dirty()


async def schedule_cleanup(reply_to, send_as_id=None):
    if not reply_to or not is_auto_delete_sent_messages_enabled():
        return

    if send_as_id is None:
        send_as_id = find_identity_by_msg_id(reply_to.id)
    if send_as_id is None or not has_identity(send_as_id):
        return

    with use_identity(send_as_id) as identity_state:
        is_my_msg = reply_to.id in identity_state["my_msg_ids"]
        is_script_cmd = is_script_command_text(reply_to.raw_text)
        if not (is_my_msg and is_script_cmd):
            return

        msg_id = reply_to.id
        if msg_id in identity_state.get("checkin_cleanup_msg_ids", []):
            return
        if msg_id == identity_state.get("sect_teach_reply_to_msg_id") and identity_state.get("next_sect_teach_time", 0) > 0:
            return
        if (
            is_identity_refresh_command_text(reply_to.raw_text)
            and (
                any(_is_identity_refresh_command(get_pending_command(pending)) for pending in identity_state["pending_tasks"].values())
                or identity_state.get("identity_info_reply_msg_ids")
                or identity_state.get("last_identity_info_msg_id", 0)
                or float(identity_state.get("identity_info_followup_due_at", 0) or 0) > 0
            )
        ):
            return

    async def safe_delete():
        await asyncio.sleep(1)
        try:
            await reply_to.delete()
        except Exception as e:
            print(f"schedule_cleanup delete failed: {e} | msg_id={msg_id}")
        if not has_identity(send_as_id):
            return
        with use_identity(send_as_id) as identity_state:
            identity_state["my_msg_ids"].pop(msg_id, None)
            mark_dirty()

    _fire_and_forget(safe_delete())


__all__ = [
    "_fire_and_forget",
    "build_ui_login_url",
    "check_bot_health_timeout",
    "clear_all_pending_tasks",
    "clear_identity_runtime_tracking",
    "clear_pending_by_reply",
    "clear_pending_tasks_by_commands",
    "clear_ui_auth_state",
    "consume_unseen_startup_alerts",
    "fetch_forum_topics",
    "find_identity_by_msg_id",
    "gc_my_msg_ids",
    "gc_ui_login_tokens",
    "gc_ui_sessions",
    "flush_low_priority_audit_summary",
    "get_audit_push_status_text",
    "get_bot_health_snapshot",
    "get_bot_last_seen_at",
    "get_game_send_queue_snapshot",
    "has_active_reply_dispatch",
    "get_low_priority_audit_pending_counts",
    "get_reply_context",
    "get_reply_family_commands",
    "is_account_session_error",
    "is_identity_weak",
    "is_reply_to_identity_message",
    "is_script_command_text",
    "issue_ui_login_token",
    "mark_bot_health_recovered",
    "mark_bot_health_suspect",
    "note_identity_weakness",
    "note_game_bot_message",
    "note_game_command_observed",
    "note_game_command_sent",
    "redeem_ui_login_token",
    "register_game_command_sent_observer",
    "reply_log_group_message",
    "resolve_reply_family",
    "run_retry_scheduler",
    "schedule_cleanup",
    "send_audit_log",
    "send_game_command",
    "should_pause_for_bot_health",
    "touch_ui_session",
    "track_reply_chain_message",
    "validate_ui_session",
    "IDENTITY_INFO_REFRESH_ERROR_TEXT",
]
