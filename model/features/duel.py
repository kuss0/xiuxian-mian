import random
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from ..config import CD_BUFFER_SEC, CMD_DUEL, TZ_LOCAL
from ..message_log_recovery import (
    find_message_log_message,
    find_message_log_replies,
    iter_message_log_entries_between,
    sender_matches_identity,
)
from ..persistence import mark_dirty, save_state
from ..runtime import classify_game_send_block, console_log, send_audit_log, send_game_command
from ..state import (
    REALM_SORT_ORDER,
    get_current_identity_id,
    get_duel_target_cooldowns,
    get_identity_ids,
    get_send_as_profile,
    set_duel_target_cooldowns,
    state,
    update_send_as_profile,
    use_identity,
)
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time
from .tianxing import (
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    normalize_tianxing_auto_config,
    normalize_tianxing_observation,
    normalize_tianxing_timeline_state,
    run_tianxing_timeline_scheduler,
)


# Gate: 结丹后期可打；元婴须元婴后期及以上（元婴初/中期不可）。
# 修为保留默认 20 万地板，可在 UI 按身份覆盖（state.duel_reserve_xiuwei）。
DUEL_JIEDAN_MIN_REALM = "结丹后期"
DUEL_YUANYING_MIN_REALM = "元婴后期"
DUEL_MIN_REALM = DUEL_JIEDAN_MIN_REALM  # status/UI 展示用最低可参战境界
DUEL_RESERVE_XIUWEI = 200_000
DUEL_MAX_CONFIG_RESERVE_XIUWEI = 100_000_000
DUEL_MAX_LOSS_XIUWEI = 0
DUEL_MIN_XIUWEI = DUEL_RESERVE_XIUWEI  # 默认门槛；运行时以 get_duel_min_xiuwei() 为准
DUEL_PRESET_TOTAL_COUNT = 10
DUEL_PRESET_YUANYING_TARGET = "@ccahen"
# 默认关闭斗法的身份（吧唧 / WA）；其余结丹后、元婴后由预设打开。
DUEL_PRESET_EXCLUDED_IDENTITY_IDS = frozenset({301299112, 8659059191})
DUEL_PRESET_EXCLUDED_USERNAMES = frozenset(
    {
        "jfdffdddd",
        "jfdffdddd1",
        "walterwa2000",
        "wa2000",
    }
)
DUEL_PRESET_EXCLUDED_LABELS = frozenset({"吧唧", "wa2000", "walterwa2000"})
# Real final reports can arrive just after two minutes; keep the pending state
# alive long enough for the normal reply path before log recovery is needed.
DUEL_REPLY_TIMEOUT_SEC = 150
DUEL_NORMAL_COOLDOWN_MIN_SEC = 18 * 60
DUEL_NORMAL_COOLDOWN_MAX_SEC = 32 * 60
DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC = 30 * 60
DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC = 55 * 60
DUEL_NORMAL_COOLDOWN_SEC = DUEL_NORMAL_COOLDOWN_MIN_SEC
DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC = DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC
DUEL_RECOVERY_MIN_SEC = 60
DUEL_RECOVERY_MAX_SEC = 180
DUEL_RESULT_GRACE_SEC = 30
DUEL_BATCH_STAGGER_MIN_SEC = 3 * 60
DUEL_BATCH_STAGGER_MAX_SEC = 8 * 60
DUEL_SAME_TARGET_COOLDOWN_SEC = 10 * 60
DUEL_TARGET_CONTENTION_BUFFER_SEC = 5 * 60
DUEL_TIANXING_PREPARE_LEAD_SEC = 60
DUEL_TARGET_RESERVATION_SEC = max(
    DUEL_SAME_TARGET_COOLDOWN_SEC,
    DUEL_REPLY_TIMEOUT_SEC + DUEL_RESULT_GRACE_SEC,
)
DUEL_WAITING_PREFIX = "正在锁定对手天机，请稍候"
DUEL_READY_PREFIX = "⚔️ 法宝齐出！"
DUEL_REPORT_PREFIX = "【天道战报·文字版】"
DUEL_FINAL_PREFIX = "【斗法终局】"
DUEL_SETTLING_TEXT = "战斗结束，正在整理天道战报"
DUEL_PHASEFUL_INTERMEDIATE_MARKERS = (
    "元婴闭关结算",
    "元神归窍总结",
    "深度闭关总结",
)
DUEL_TERMINAL_ATTEMPT_KEYWORDS = (
    "凭借神通侥幸逃脱",
    "侥幸逃脱",
    "锁定目标时遭遇天机反噬",
    "出手次数过多",
    "神念不足",
    "神念已耗尽",
    "神念耗尽",
    "无法再次斗法",
    "元神尚未平复",
    "虚弱",
    "无法锁定对手",
    "尚未踏入仙途",
    "对方正在斗法",
    "你已在斗法",
    "小隐于野",
)
DUEL_TARGET_CONSUMING_TERMINAL_KEYWORDS = (
    "凭借神通侥幸逃脱",
    "侥幸逃脱",
)
RE_DUEL_WINNER = re.compile(r"(?:胜者[:：]\s*|胜者：)(@[^\s|]+)")
RE_DUEL_LOSER = re.compile(r"(?:败者[:：]\s*|败者：)(@[^\s|]+)")
RE_DUEL_WEAKNESS = re.compile(r"虚弱状态】?\s*(?P<wait>\d+\s*(?:天|小时|分钟|秒)(?:\d+\s*(?:小时|分钟|秒))*)")
RE_DUEL_XIUWEI_LOSS = re.compile(r"损失修为\s*-\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>万)?")
RE_DUEL_DAILY_LIMIT_TARGET = re.compile(r"今日对\s+(?P<target>@[^\s，。！？、；：:,.!?]+)\s+出手次数过多")
RE_DUEL_ATTACKER = re.compile(r"攻方[:：]\s*(?P<username>@[^\s·|]+)")
RE_DUEL_DEFENDER = re.compile(r"守方[:：]\s*(?P<username>@[^\s·|]+)")
RE_DUEL_MIND_REMAINING = re.compile(r"今日(?:剩余)?神念[:：]\s*(?P<remaining>\d+)\s*/\s*(?P<total>\d+)")
RE_DUEL_TARGET_WINS_REMAINING = re.compile(r"对此人剩余胜场[:：]\s*(?P<remaining>\d+)")
DUEL_LOG_REPLAY_LOOKBACK_SEC = 15 * 60
DUEL_LOG_REPLAY_LOOKAHEAD_SEC = 30
DUEL_TIANXING_RECONCILE_LOOKBACK_SEC = 24 * 3600
DUEL_LOADOUT_REPLY_TIMEOUT_SEC = 120
DUEL_LOADOUT_STEP_DELAY_SEC = 8
DUEL_LOADOUT_PHASE_PREFIX = "斗法配装:"
# 批次打完后，次日重开的散列偏移（兼容旧逻辑）。
DUEL_DAILY_WINDOW_START_MINUTE = 15
DUEL_DAILY_WINDOW_SPAN_SEC = 2 * 60 * 60
# 可配执行时间窗（分钟，本地时区）。默认全天，UI 可收窄（吸收上游 group_duel 窗口思路）。
DUEL_DEFAULT_WINDOW_START_MINUTE = 0
DUEL_DEFAULT_WINDOW_END_MINUTE = 23 * 60 + 59
# 容量预检：单身份最小间隔 ≈ 同目标 CD + 批次错峰下限；同目标全账号共享间隔更严。
DUEL_CAPACITY_SELF_INTERVAL_SEC = DUEL_SAME_TARGET_COOLDOWN_SEC + DUEL_BATCH_STAGGER_MIN_SEC
DUEL_CAPACITY_TARGET_INTERVAL_SEC = DUEL_SAME_TARGET_COOLDOWN_SEC + DUEL_TARGET_CONTENTION_BUFFER_SEC
DUEL_DEFAULT_LOADOUT = {
    "battle": (),
    "restore": (),
    "unequip_only": True,
    "keep_unequipped": True,
}
DUEL_CONTROLLED_LOADOUTS = {
    8659059191: {
        # WA 同样空装斗法；批次结束后恢复日常法宝。
        "battle": (),
        "restore": (
            "青竹蜂云剑（神雷版）",
            "乾蓝冰焰",
            "青竹蜂云剑（金雷竹·庚金相）",
            "元合五极山",
            "玄天斩灵剑",
        ),
        "unequip_only": True,
        "keep_unequipped": False,
    },
}
RE_CURRENT_EQUIPPED = re.compile(r"^当前祭出[：:]\s*(?P<items>.+)$", re.M)
_DUEL_DAY_LOG_CACHE = {"day": "", "refreshed_at": 0.0, "entries": []}


def _parse_int(value):
    try:
        return int(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0


def normalize_duel_target(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    if raw.startswith("@"):
        return "@" + raw.lstrip("@").split()[0]
    if re.fullmatch(r"\d+", raw):
        return raw
    return "@" + raw.split()[0]


def normalize_duel_targets(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s,，;；|/]+", raw)
    targets = []
    seen = set()
    for part in parts:
        target = normalize_duel_target(part)
        key = target.lower()
        if not target or key in seen:
            continue
        targets.append(target)
        seen.add(key)
    return targets


def _duel_day_key(now):
    return datetime.fromtimestamp(float(now), TZ_LOCAL).date().isoformat()


def _daily_limited_targets(now):
    day_key = _duel_day_key(now)
    if str(state.get("duel_daily_limit_day") or "") != day_key:
        state["duel_daily_limit_day"] = day_key
        state["duel_daily_limited_targets"] = []
        return set()
    raw = state.get("duel_daily_limited_targets") or []
    if not isinstance(raw, (list, tuple, set)):
        raw = normalize_duel_targets(raw)
    return {_target_cooldown_key(target) for target in raw if normalize_duel_target(target)}


def _mark_target_daily_limited(target, now):
    limited = _daily_limited_targets(now)
    normalized = normalize_duel_target(target)
    key = _target_cooldown_key(normalized)
    if key:
        limited.add(key)
    state["duel_daily_limited_targets"] = sorted(limited)
    return limited


def _target_token(now=None):
    targets = _target_tokens()
    if not targets:
        return ""
    now = float(now if now is not None else time.time())
    limited = _daily_limited_targets(now)
    completed = max(0, int(state.get("duel_completed_count", 0) or 0))
    for offset in range(len(targets)):
        target = targets[(completed + offset) % len(targets)]
        if _target_cooldown_key(target) not in limited:
            return target
    return ""


def _target_tokens():
    return normalize_duel_targets(state.get("duel_target", ""))


def build_duel_command(target=None):
    target = normalize_duel_target(_target_token() if target is None else target)
    return f"{CMD_DUEL} {target}" if target else ""


def _target_cooldown_key(target):
    return normalize_duel_target(target).casefold()


def _get_target_cooldown_record(target):
    record = get_duel_target_cooldowns().get(_target_cooldown_key(target)) or {}
    return record if isinstance(record, dict) else {"until": float(record or 0)}


def _target_cooldown_until(target):
    return float(_get_target_cooldown_record(target).get("until", 0) or 0)


def _set_target_cooldown(target, until, *, confirmed, command_msg_id=0):
    key = _target_cooldown_key(target)
    if not key:
        return 0
    records = dict(get_duel_target_cooldowns())
    current = records.get(key) or {}
    current_until = float(current.get("until", 0) or 0) if isinstance(current, dict) else float(current or 0)
    records[key] = {
        "until": max(current_until, float(until or 0)),
        "confirmed": bool(confirmed),
        "owner_identity_id": int(get_current_identity_id() or 0),
        "command_msg_id": int(command_msg_id or 0),
    }
    set_duel_target_cooldowns(records)
    return float(records[key]["until"])


def _clear_target_reservation(target, command_msg_id=0):
    key = _target_cooldown_key(target)
    records = dict(get_duel_target_cooldowns())
    record = records.get(key)
    if not isinstance(record, dict) or record.get("confirmed"):
        return False
    if command_msg_id and int(record.get("command_msg_id", 0) or 0) != int(command_msg_id):
        return False
    records.pop(key, None)
    set_duel_target_cooldowns(records)
    return True


def _username_alias_keys(value):
    key = _username_key(value)
    aliases = {key} if key else set()
    for identity_id in get_identity_ids():
        profile_keys = _profile_username_keys(identity_id)
        if key in profile_keys:
            aliases.update(profile_keys)
    return aliases


def _active_duel_participant_block(target, now):
    current_id = int(get_current_identity_id() or 0)
    self_aliases = _profile_username_keys(current_id)
    target_aliases = _username_alias_keys(target)
    for active_target, record in get_duel_target_cooldowns().items():
        if not isinstance(record, dict) or record.get("confirmed"):
            continue
        if float(record.get("until", 0) or 0) <= float(now):
            continue
        owner_id = int(record.get("owner_identity_id", 0) or 0)
        owner_aliases = _profile_username_keys(owner_id)
        active_target_aliases = _username_alias_keys(active_target)
        if current_id != owner_id and self_aliases.intersection(active_target_aliases):
            return f"当前身份正被 {next(iter(owner_aliases), owner_id)} 发起斗法"
        if target_aliases.intersection(owner_aliases):
            return f"目标 {normalize_duel_target(target)} 正在发起另一场斗法"
        if current_id != owner_id and target_aliases.intersection(active_target_aliases):
            return f"目标 {normalize_duel_target(target)} 正在被其他身份斗法"
    return ""


def _schedule_next_duel(now, delay_sec):
    state["next_duel_time"] = float(now + max(1, delay_sec))
    return state["next_duel_time"]


def normalize_duel_window_minute(value, default):
    try:
        minute = int(value)
    except (TypeError, ValueError):
        minute = int(default)
    return max(0, min(23 * 60 + 59, minute))


def get_duel_window_start_minute():
    raw = state.get("duel_window_start_minute", None)
    if raw in (None, ""):
        return int(DUEL_DEFAULT_WINDOW_START_MINUTE)
    return normalize_duel_window_minute(raw, DUEL_DEFAULT_WINDOW_START_MINUTE)


def get_duel_window_end_minute():
    raw = state.get("duel_window_end_minute", None)
    if raw in (None, ""):
        return int(DUEL_DEFAULT_WINDOW_END_MINUTE)
    return normalize_duel_window_minute(raw, DUEL_DEFAULT_WINDOW_END_MINUTE)


def get_duel_window_label(*, start_minute=None, end_minute=None):
    start_minute = normalize_duel_window_minute(
        start_minute if start_minute is not None else get_duel_window_start_minute(),
        DUEL_DEFAULT_WINDOW_START_MINUTE,
    )
    end_minute = normalize_duel_window_minute(
        end_minute if end_minute is not None else get_duel_window_end_minute(),
        DUEL_DEFAULT_WINDOW_END_MINUTE,
    )
    return f"{start_minute // 60:02d}:{start_minute % 60:02d}-{end_minute // 60:02d}:{end_minute % 60:02d}"


def get_duel_window_bounds(now=None, *, start_minute=None, end_minute=None):
    """Return (window_start_ts, window_end_ts) for the local calendar day of now."""
    local_now = datetime.fromtimestamp(float(now if now is not None else time.time()), TZ_LOCAL)
    start_minute = normalize_duel_window_minute(
        start_minute if start_minute is not None else get_duel_window_start_minute(),
        DUEL_DEFAULT_WINDOW_START_MINUTE,
    )
    end_minute = normalize_duel_window_minute(
        end_minute if end_minute is not None else get_duel_window_end_minute(),
        DUEL_DEFAULT_WINDOW_END_MINUTE,
    )
    if end_minute < start_minute:
        end_minute = start_minute
    start = local_now.replace(hour=start_minute // 60, minute=start_minute % 60, second=0, microsecond=0)
    end = local_now.replace(hour=end_minute // 60, minute=end_minute % 60, second=0, microsecond=0)
    return float(start.timestamp()), float(end.timestamp())


def is_within_duel_exec_window(now=None, *, start_minute=None, end_minute=None):
    now = float(now if now is not None else time.time())
    window_start, window_end = get_duel_window_bounds(now, start_minute=start_minute, end_minute=end_minute)
    return window_start <= now <= window_end


def next_duel_exec_window_open(now=None, *, start_minute=None, end_minute=None):
    """Next timestamp when the execution window opens (today or tomorrow)."""
    now = float(now if now is not None else time.time())
    window_start, window_end = get_duel_window_bounds(now, start_minute=start_minute, end_minute=end_minute)
    if now < window_start:
        return window_start
    if now <= window_end:
        return now
    # 已过今日窗口：跳到次日开始。
    return window_start + 24 * 3600


def estimate_duel_capacity(
    *,
    total_count,
    start_minute=None,
    end_minute=None,
    target_hits=None,
    self_interval_sec=None,
    target_interval_sec=None,
):
    """Offline capacity estimate for a same-day batch (absorb upstream group_duel idea).

    total_count: fights this identity wants today.
    target_hits: optional total fights (all identities) planned against the same target.

    稳妥语义：**纯提示，不拦截发送/不改配置**。
    只按斗法 CD 粗算，不含天星推命/改命耗时；开了 duel_route 时数字可能偏乐观。
    """
    start_minute = normalize_duel_window_minute(
        start_minute if start_minute is not None else DUEL_DEFAULT_WINDOW_START_MINUTE,
        DUEL_DEFAULT_WINDOW_START_MINUTE,
    )
    end_minute = normalize_duel_window_minute(
        end_minute if end_minute is not None else DUEL_DEFAULT_WINDOW_END_MINUTE,
        DUEL_DEFAULT_WINDOW_END_MINUTE,
    )
    if end_minute < start_minute:
        end_minute = start_minute
    span_sec = max(0, (end_minute - start_minute) * 60)
    self_interval = max(1, int(self_interval_sec or DUEL_CAPACITY_SELF_INTERVAL_SEC))
    target_interval = max(1, int(target_interval_sec or DUEL_CAPACITY_TARGET_INTERVAL_SEC))
    self_max = (span_sec // self_interval) + 1 if span_sec > 0 or total_count <= 1 else 0
    if span_sec == 0:
        self_max = 1 if int(total_count or 0) <= 1 else 0
    target_max = (span_sec // target_interval) + 1 if span_sec > 0 else (1 if int(target_hits or 0) <= 1 else 0)
    if span_sec == 0 and int(target_hits or 0) <= 1:
        target_max = 1
    needed = max(0, int(total_count or 0))
    hits = max(0, int(target_hits if target_hits is not None else needed))
    self_ok = needed <= self_max
    target_ok = hits <= target_max
    reasons = []
    if not self_ok:
        reasons.append(
            f"身份次数 {needed} 超过窗口容量约 {self_max}（间隔≥{self_interval // 60}分，窗口 {get_duel_window_label(start_minute=start_minute, end_minute=end_minute)}）"
        )
    if target_hits is not None and not target_ok:
        reasons.append(
            f"同目标合计 {hits} 场超过共享 CD 容量约 {target_max}（间隔≥{target_interval // 60}分）"
        )
    return {
        "ok": bool(self_ok and target_ok),
        "window_label": get_duel_window_label(start_minute=start_minute, end_minute=end_minute),
        "window_span_sec": span_sec,
        "total_count": needed,
        "self_max": int(self_max),
        "self_interval_sec": self_interval,
        "target_hits": hits,
        "target_max": int(target_max),
        "target_interval_sec": target_interval,
        "reason": "；".join(reasons),
    }


def _next_daily_duel_time(now):
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    start_minute = get_duel_window_start_minute()
    # 次日批次：落在执行窗口起点附近，再加身份散列，避免全员同一秒。
    next_day = (local_now + timedelta(days=1)).replace(
        hour=start_minute // 60,
        minute=start_minute % 60,
        second=0,
        microsecond=0,
    )
    identity_id = int(get_current_identity_id() or 0)
    span = max(1, min(DUEL_DAILY_WINDOW_SPAN_SEC, max(60, (get_duel_window_end_minute() - start_minute) * 60)))
    offset = identity_id % (span + 1)
    return float(next_day.timestamp() + offset)


def _complete_duel_batch(now):
    completed_count = int(state.get("duel_completed_count", 0) or 0)
    total_count = int(state.get("duel_total_count", 0) or 0)
    loadout_config = _controlled_loadout_config(now)
    keep_unequipped = bool(loadout_config and loadout_config.get("keep_unequipped"))
    loadout_prepared = bool(loadout_config and state.get("duel_unequip_prepared"))
    restore_items = tuple((loadout_config or {}).get("restore") or ())
    restoring = bool(loadout_config and restore_items and not keep_unequipped)
    if restoring:
        _clear_loadout_pending()
        _set_loadout_phase("restore_needed")
        state["next_duel_time"] = float(now + DUEL_LOADOUT_STEP_DELAY_SEC)
    else:
        if loadout_prepared:
            _clear_loadout_pending()
            state["duel_unequip_prepared"] = False
            _set_loadout_phase("restored")
        state["duel_completed_count"] = 0
        state["next_duel_time"] = _next_daily_duel_time(now) if state.get("duel_enabled") else 0
    return {
        "completed_count": completed_count,
        "total_count": total_count,
        "daily": bool(state.get("duel_enabled")),
        "restoring": restoring,
        "next_duel_time": float(state.get("next_duel_time", 0) or 0),
    }


def _clear_duel_pending():
    state["duel_reply_to_msg_id"] = 0
    state["duel_reply_due_at"] = 0
    state["duel_open_msg_id"] = 0
    state["duel_magic_due_at"] = 0
    state["duel_magic_sent_at"] = 0
    state["duel_started_at"] = 0


def _controlled_loadout_config(now=None):
    config = DUEL_CONTROLLED_LOADOUTS.get(int(get_current_identity_id() or 0), DUEL_DEFAULT_LOADOUT)
    if not _loadout_phase() and not state.get("duel_unequip_prepared"):
        if not state.get("duel_enabled") or now is None:
            return None
        next_duel_time = float(state.get("next_duel_time", 0) or 0)
        if next_duel_time > float(now) + DUEL_TIANXING_PREPARE_LEAD_SEC:
            return None
    return config


def seed_controlled_duel_loadout_prepare(now=None):
    """Arm loadout preparation after an explicit enable/configuration change."""
    if not state.get("duel_enabled"):
        return False
    now = float(now if now is not None else time.time())
    if float(state.get("next_duel_time", 0) or 0) > now + DUEL_TIANXING_PREPARE_LEAD_SEC:
        return False
    if _parse_int(state.get("duel_reply_to_msg_id", 0)) > 0 or _parse_int(state.get("duel_open_msg_id", 0)) > 0:
        return False
    phase = _loadout_phase()
    if state.get("duel_unequip_prepared") or phase.endswith("battle_ready"):
        return False
    if phase.startswith(f"{DUEL_LOADOUT_PHASE_PREFIX}restore"):
        return False
    _clear_loadout_pending()
    state["duel_unequip_prepared"] = False
    _set_loadout_phase("prepare")
    return True


def _loadout_phase():
    result = str(state.get("duel_last_result") or "")
    return result if result.startswith(DUEL_LOADOUT_PHASE_PREFIX) else ""


def _set_loadout_phase(phase):
    state["duel_last_result"] = f"{DUEL_LOADOUT_PHASE_PREFIX}{phase}"


def _clear_loadout_pending():
    state["duel_magic_sent_at"] = 0
    state["duel_magic_due_at"] = 0


def _parse_current_equipment(text):
    match = RE_CURRENT_EQUIPPED.search(str(text or ""))
    if not match:
        return []
    return re.findall(r"【([^】]+)】", match.group("items"))


def _loadout_reply_matches(text, expected):
    current = _parse_current_equipment(text)
    return bool(current) and set(current) == set(expected) and len(current) == len(expected)


def _loadout_unequip_reply(text):
    raw = str(text or "")
    return (
        "你已收回当前祭出的所有法宝" in raw
        or "你当前并未祭出任何法宝" in raw
        or "当前祭出法宝: 无祭出法宝" in raw
    )


def _username_key(value):
    return str(value or "").strip().lstrip("@").casefold()


def _profile_username_keys(identity_id):
    profile = get_send_as_profile(identity_id) or {}
    values = [profile.get("username")]
    aliases = profile.get("username_aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    values.extend(aliases)
    return {_username_key(value) for value in values if _username_key(value)}


def _configured_target_aliases():
    mapping = {}
    profiles = []
    for identity_id in get_identity_ids():
        keys = _profile_username_keys(identity_id)
        if keys:
            profiles.append(keys)
    for target in _target_tokens():
        canonical = normalize_duel_target(target)
        key = _username_key(canonical)
        aliases = {key} if key else set()
        for profile_keys in profiles:
            if key in profile_keys:
                aliases.update(profile_keys)
        for alias in aliases:
            mapping[alias] = canonical
    return mapping


def _duel_day_log_entries(now):
    day_key = _duel_day_key(now)
    if (
        _DUEL_DAY_LOG_CACHE.get("day") == day_key
        and float(now) - float(_DUEL_DAY_LOG_CACHE.get("refreshed_at") or 0) < 15
    ):
        return list(_DUEL_DAY_LOG_CACHE.get("entries") or [])
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    entries = []
    for entry, entry_ts in iter_message_log_entries_between(start, float(now) + DUEL_LOG_REPLAY_LOOKAHEAD_SEC):
        text = str((entry or {}).get("text") or "").strip()
        if not text:
            continue
        if not (
            text.startswith(CMD_DUEL)
            or text.startswith(".卸下法宝")
            or text.startswith(".装备 ")
            or _is_duel_report_text(text)
            or _has_duel_terminal_attempt_keyword(text)
            or _loadout_unequip_reply(text)
            or _parse_current_equipment(text)
        ):
            continue
        item = dict(entry)
        item["ts_epoch"] = float(entry_ts)
        entries.append(item)
    _DUEL_DAY_LOG_CACHE.update(day=day_key, refreshed_at=float(now), entries=entries)
    return list(entries)


def _derive_duel_log_evidence(entries, identity_id, *, now=None):
    identity_id = int(identity_id or 0)
    now = float(now if now is not None else time.time())
    self_aliases = _profile_username_keys(identity_id)
    target_aliases = _configured_target_aliases()
    commands = {}
    replies = {}
    for entry in entries or []:
        msg_id = _parse_int((entry or {}).get("message_id"))
        reply_to = _parse_int((entry or {}).get("reply_to_msg_id"))
        text = str((entry or {}).get("text") or "").strip()
        if msg_id > 0 and sender_matches_identity((entry or {}).get("sender_id"), identity_id) and (
            text.startswith(CMD_DUEL) or text.startswith(".卸下法宝") or text.startswith(".装备 ")
        ):
            item = commands.setdefault(msg_id, {"entries": [], "text": text, "ts_epoch": 0.0})
            item["entries"].append(entry)
            item["text"] = text or item["text"]
            item["ts_epoch"] = max(float(item.get("ts_epoch") or 0), float((entry or {}).get("ts_epoch") or 0))
        if reply_to > 0:
            replies.setdefault(reply_to, []).append(entry)

    completed = 0
    manual_completed = 0
    mind_remaining = -1
    last_command_msg_id = 0
    last_report = None
    limited_targets = set()
    open_duels = []
    confirmed_loadout = None
    for msg_id, command in sorted(commands.items(), key=lambda item: (item[1]["ts_epoch"], item[0])):
        text = str(command.get("text") or "")
        command_replies = sorted(
            replies.get(msg_id) or [],
            key=lambda item: (float((item or {}).get("ts_epoch") or 0), _parse_int((item or {}).get("message_id"))),
        )
        is_automatic = any(str((entry or {}).get("event_type") or "") == "sent" for entry in command.get("entries") or [])
        if text.startswith(".卸下法宝"):
            if any(_loadout_unequip_reply((entry or {}).get("text")) for entry in command_replies):
                confirmed_loadout = {"kind": "unequipped", "ts_epoch": command["ts_epoch"], "msg_id": msg_id}
            continue
        if text.startswith(".装备 "):
            if any(_parse_current_equipment((entry or {}).get("text")) for entry in command_replies):
                confirmed_loadout = {"kind": "equipped", "ts_epoch": command["ts_epoch"], "msg_id": msg_id}
            continue

        command_target = normalize_duel_target(text[len(CMD_DUEL):].strip())
        canonical_target = target_aliases.get(_username_key(command_target), command_target)
        terminal = None
        for entry in command_replies:
            reply_text = str((entry or {}).get("text") or "")
            if _is_duel_report_text(reply_text) or _has_duel_terminal_attempt_keyword(reply_text):
                terminal = entry
        if terminal is None:
            if float(command.get("ts_epoch") or 0) + DUEL_REPLY_TIMEOUT_SEC + DUEL_RESULT_GRACE_SEC >= now:
                open_duels.append({"target": canonical_target, "msg_id": msg_id, "ts_epoch": command["ts_epoch"]})
            continue
        terminal_text = str((terminal or {}).get("text") or "")
        last_command_msg_id = max(last_command_msg_id, msg_id)
        if "出手次数过多" in terminal_text:
            limit_match = RE_DUEL_DAILY_LIMIT_TARGET.search(terminal_text)
            limited = normalize_duel_target(limit_match.group("target") if limit_match else canonical_target)
            limited_targets.add(target_aliases.get(_username_key(limited), limited))
            continue
        defender_key = ""
        if _is_duel_report_text(terminal_text):
            attacker = RE_DUEL_ATTACKER.search(terminal_text)
            defender = RE_DUEL_DEFENDER.search(terminal_text)
            attacker_key = _username_key(attacker.group("username") if attacker else "")
            defender_key = _username_key(defender.group("username") if defender else "")
            if attacker_key not in self_aliases or defender_key not in target_aliases:
                continue
        elif _username_key(canonical_target) not in target_aliases or not _duel_counts_as_attempt(terminal_text):
            continue
        completed += 1
        if not is_automatic:
            manual_completed += 1
        last_report = terminal
        mind_match = RE_DUEL_MIND_REMAINING.search(terminal_text)
        if mind_match:
            mind_remaining = int(mind_match.group("remaining"))
        wins_match = RE_DUEL_TARGET_WINS_REMAINING.search(terminal_text)
        if defender_key and wins_match and int(wins_match.group("remaining")) <= 0:
            limited_targets.add(target_aliases[defender_key])

    return {
        "completed": completed,
        "manual_completed": manual_completed,
        "mind_remaining": mind_remaining,
        "last_command_msg_id": last_command_msg_id,
        "last_report": last_report,
        "limited_targets": sorted(target for target in limited_targets if target),
        "open_duels": open_duels,
        "confirmed_loadout": confirmed_loadout,
    }


def reconcile_duel_from_message_log(now, *, force=False):
    now = float(now or time.time())
    day_key = _duel_day_key(now)
    if (
        not force
        and str(state.get("duel_log_reconcile_day") or "") == day_key
        and now - float(state.get("duel_log_reconcile_at", 0) or 0) < 30
    ):
        return False
    evidence = _derive_duel_log_evidence(_duel_day_log_entries(now), get_current_identity_id(), now=now)
    changed = False
    observed_values = {
        "duel_log_reconcile_day": day_key,
        "duel_log_reconcile_at": now,
        "duel_observed_completed_count": int(evidence.get("completed") or 0),
        "duel_observed_manual_count": int(evidence.get("manual_completed") or 0),
        "duel_observed_mind_remaining": int(evidence.get("mind_remaining", -1)),
        "duel_observed_last_command_msg_id": int(evidence.get("last_command_msg_id") or 0),
    }
    for key, value in observed_values.items():
        if state.get(key) != value:
            state[key] = value
            changed = True
    observed_completed = int(evidence.get("completed") or 0)
    if observed_completed > int(state.get("duel_completed_count", 0) or 0):
        state["duel_completed_count"] = observed_completed
        changed = True
    for target in evidence.get("limited_targets") or []:
        before = set(_daily_limited_targets(now))
        after = _mark_target_daily_limited(target, now)
        changed = changed or before != after
    report = evidence.get("last_report") or {}
    report_text = str(report.get("text") or "")
    report_msg_id = _parse_int(report.get("message_id"))
    if report_msg_id > int(state.get("duel_last_msg_id", 0) or 0):
        state["duel_last_msg_id"] = report_msg_id
        state["duel_last_result"] = parse_duel_result_summary(report_text)
        state["duel_last_error"] = ""
        changed = True
    defender = RE_DUEL_DEFENDER.search(report_text)
    if defender:
        aliases = _configured_target_aliases()
        target = aliases.get(_username_key(defender.group("username")), normalize_duel_target(defender.group("username")))
        until = float(report.get("ts_epoch") or 0) + DUEL_SAME_TARGET_COOLDOWN_SEC
        if until > now:
            before = _get_target_cooldown_record(target)
            _set_target_cooldown(target, until, confirmed=True, command_msg_id=0)
            if float(before.get("until", 0) or 0) < until or not before.get("confirmed"):
                changed = True
    for open_duel in evidence.get("open_duels") or []:
        until = float(open_duel.get("ts_epoch") or 0) + DUEL_REPLY_TIMEOUT_SEC + DUEL_RESULT_GRACE_SEC
        if until > now:
            before = _get_target_cooldown_record(open_duel.get("target"))
            _set_target_cooldown(
                open_duel.get("target"),
                until,
                confirmed=False,
                command_msg_id=open_duel.get("msg_id"),
            )
            if (
                float(before.get("until", 0) or 0) < until
                or before.get("confirmed")
                or int(before.get("command_msg_id", 0) or 0) != int(open_duel.get("msg_id") or 0)
            ):
                changed = True
    loadout = evidence.get("confirmed_loadout") or {}
    if loadout.get("kind") == "unequipped" and not _loadout_phase().startswith(f"{DUEL_LOADOUT_PHASE_PREFIX}restore"):
        if not state.get("duel_unequip_prepared") or _loadout_phase() != f"{DUEL_LOADOUT_PHASE_PREFIX}battle_ready":
            state["duel_unequip_prepared"] = True
            _set_loadout_phase("battle_ready")
            changed = True
    elif loadout.get("kind") == "equipped" and state.get("duel_enabled") and not _has_active_duel_window(now):
        desired_phase = (
            "prepare"
            if float(state.get("next_duel_time", 0) or 0) <= now + DUEL_TIANXING_PREPARE_LEAD_SEC
            else "restored"
        )
        if state.get("duel_unequip_prepared") or _loadout_phase() != f"{DUEL_LOADOUT_PHASE_PREFIX}{desired_phase}":
            state["duel_unequip_prepared"] = False
            _set_loadout_phase(desired_phase)
            changed = True
    if changed:
        mark_dirty()
    return changed


def _find_loadout_reply(now, predicate):
    reply_to_msg_id = _parse_int(state.get("duel_magic_sent_at", 0))
    if reply_to_msg_id <= 0:
        return None
    replies = find_message_log_replies(
        reply_to_msg_id,
        now,
        lookback_sec=DUEL_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=DUEL_LOG_REPLAY_LOOKAHEAD_SEC,
        predicate=lambda entry: predicate(str((entry or {}).get("text") or "")),
    )
    return replies[-1] if replies else None


async def _send_loadout_command(command, now, waiting_phase):
    msg = await send_game_command(command, track=False, max_retry=0, source_module="斗法配装")
    if not msg:
        send_block = classify_game_send_block(get_current_identity_id(), command)
        if send_block.get("status") == "unsent":
            state["duel_last_error"] = f"斗法配装未发送: {send_block.get('code') or 'runtime_block'}"
            _schedule_next_duel(now, DUEL_RECOVERY_MIN_SEC)
            save_state()
            return False
        state["duel_enabled"] = False
        state["duel_last_error"] = "斗法配装发送状态未知，已停止批次"
        _set_loadout_phase("error")
        save_state()
        await send_audit_log("⛔ 斗法配装发送状态未知，已停止受控斗法批次。", scope="identity", limit=200)
        return False
    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["duel_magic_sent_at"] = int(getattr(msg, "id", 0) or 0)
    state["duel_magic_due_at"] = sent_at + DUEL_LOADOUT_REPLY_TIMEOUT_SEC
    state["duel_last_error"] = ""
    _set_loadout_phase(waiting_phase)
    _schedule_next_duel(sent_at, DUEL_LOADOUT_STEP_DELAY_SEC)
    save_state()
    return True


async def _stop_loadout_batch(message, *, restore=False):
    state["duel_enabled"] = False
    state["duel_unequip_prepared"] = False
    state["duel_last_error"] = message
    _clear_loadout_pending()
    _set_loadout_phase("restore_error" if restore else "error")
    save_state()
    await send_audit_log(f"⛔ {message}", scope="identity", limit=220)


async def _run_controlled_loadout_prepare(now, config):
    phase = _loadout_phase()
    battle_items = tuple(config.get("battle") or ())
    unequip_only = bool(config.get("unequip_only"))
    if not battle_items and not unequip_only:
        return True
    if state.get("duel_unequip_prepared"):
        return True
    if not phase or phase in {
        f"{DUEL_LOADOUT_PHASE_PREFIX}prepare",
        f"{DUEL_LOADOUT_PHASE_PREFIX}restored",
        f"{DUEL_LOADOUT_PHASE_PREFIX}error",
    }:
        return await _send_loadout_command(".卸下法宝", now, "prepare_unequip_wait") and False
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}prepare_unequip_wait":
        reply = _find_loadout_reply(now, _loadout_unequip_reply)
        if reply:
            _clear_loadout_pending()
            if unequip_only:
                state["duel_unequip_prepared"] = True
                state["duel_last_error"] = ""
                _set_loadout_phase("battle_ready")
            else:
                _set_loadout_phase("prepare_equip")
            _schedule_next_duel(now, DUEL_LOADOUT_STEP_DELAY_SEC)
            save_state()
            if unequip_only:
                await send_audit_log("🗡️ 斗法配装已确认：当前未祭出法宝。", scope="identity", limit=180)
            return False
        if float(state.get("duel_magic_due_at", 0) or 0) <= now:
            await _stop_loadout_batch("斗法配装卸装回包超时，未进入斗法")
        return False
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}prepare_equip":
        return await _send_loadout_command(f".装备 {battle_items[0]}", now, "prepare_equip_wait") and False
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}prepare_equip_wait":
        reply = _find_loadout_reply(now, lambda text: _loadout_reply_matches(text, battle_items))
        if reply:
            _clear_loadout_pending()
            state["duel_unequip_prepared"] = True
            state["duel_last_error"] = ""
            _set_loadout_phase("battle_ready")
            _schedule_next_duel(now, DUEL_LOADOUT_STEP_DELAY_SEC)
            save_state()
            await send_audit_log("🗡️ 斗法配装已确认：仅祭出玄铁剑。", scope="identity", limit=180)
            return False
        if float(state.get("duel_magic_due_at", 0) or 0) <= now:
            await _stop_loadout_batch("斗法配装未确认仅祭出玄铁剑，已停止批次")
        return False
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}battle_ready":
        state["duel_unequip_prepared"] = True
        return True
    return False


async def _run_controlled_loadout_restore(now, config):
    phase = _loadout_phase()
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}restored":
        return False
    if not phase.startswith(f"{DUEL_LOADOUT_PHASE_PREFIX}restore"):
        return False
    restore_items = tuple(config.get("restore") or ())
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}restore_needed":
        await _send_loadout_command(".卸下法宝", now, "restore_unequip_wait")
        return True
    if phase == f"{DUEL_LOADOUT_PHASE_PREFIX}restore_unequip_wait":
        reply = _find_loadout_reply(now, _loadout_unequip_reply)
        if reply:
            _clear_loadout_pending()
            _set_loadout_phase("restore_equip:0")
            _schedule_next_duel(now, DUEL_LOADOUT_STEP_DELAY_SEC)
            save_state()
        elif float(state.get("duel_magic_due_at", 0) or 0) <= now:
            await _stop_loadout_batch("斗法结束后卸装回包超时，恢复已停止", restore=True)
        return True
    equip_match = re.fullmatch(rf"{re.escape(DUEL_LOADOUT_PHASE_PREFIX)}restore_equip:(\d+)", phase)
    if equip_match:
        index = int(equip_match.group(1))
        if index >= len(restore_items):
            completed = int(state.get("duel_completed_count", 0) or 0)
            total = int(state.get("duel_total_count", 0) or 0)
            state["duel_unequip_prepared"] = False
            state["duel_last_error"] = ""
            _set_loadout_phase("restored")
            state["duel_completed_count"] = 0
            if state.get("duel_enabled"):
                state["next_duel_time"] = _next_daily_duel_time(now)
            else:
                state["next_duel_time"] = 0
            save_state()
            if state.get("duel_enabled"):
                message = (
                    f"✅ WA 今日斗法完成（{completed}/{total}），原法宝配装已恢复，"
                    f"次日批次→{fmt_abs_ts(state['next_duel_time'])}。"
                )
            else:
                message = f"✅ WA 斗法已关闭（{completed}/{total}），原法宝配装已恢复。"
            await send_audit_log(
                message,
                scope="identity",
                limit=220,
            )
            return True
        await _send_loadout_command(f".装备 {restore_items[index]}", now, f"restore_equip_wait:{index}")
        return True
    wait_match = re.fullmatch(rf"{re.escape(DUEL_LOADOUT_PHASE_PREFIX)}restore_equip_wait:(\d+)", phase)
    if wait_match:
        index = int(wait_match.group(1))
        expected = restore_items[: index + 1]
        reply = _find_loadout_reply(now, lambda text: _loadout_reply_matches(text, expected))
        if reply:
            _clear_loadout_pending()
            _set_loadout_phase(f"restore_equip:{index + 1}")
            _schedule_next_duel(now, DUEL_LOADOUT_STEP_DELAY_SEC)
            save_state()
        elif float(state.get("duel_magic_due_at", 0) or 0) <= now:
            await _stop_loadout_batch(f"斗法结束后恢复法宝失败：{restore_items[index]}", restore=True)
        return True
    return True


def _set_duel_error(message, *, next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=None, persist=True):
    state["duel_last_error"] = str(message or "").strip()
    if now is None:
        now = time.time()
    _schedule_next_duel(now, next_delay)
    if persist:
        save_state()
    else:
        mark_dirty()


def _realm_gate_reason(realm):
    realm = str(realm or "").strip()
    if realm not in REALM_SORT_ORDER:
        return f"境界至少需为{DUEL_JIEDAN_MIN_REALM}（元婴须{DUEL_YUANYING_MIN_REALM}），当前={realm or '未知'}"
    realm_index = REALM_SORT_ORDER.index(realm)
    jiedan_index = REALM_SORT_ORDER.index(DUEL_JIEDAN_MIN_REALM)
    yuanying_index = REALM_SORT_ORDER.index(DUEL_YUANYING_MIN_REALM)
    if realm_index == jiedan_index or realm_index >= yuanying_index:
        return ""
    if jiedan_index < realm_index < yuanying_index:
        return f"元婴须达到{DUEL_YUANYING_MIN_REALM}，当前={realm}"
    return f"境界至少需为{DUEL_JIEDAN_MIN_REALM}，当前={realm}"


def normalize_duel_reserve_xiuwei(value, *, default=None):
    """Normalize UI/state reserve; blank/None falls back to default (module 20万)."""
    if value is None:
        return int(default if default is not None else DUEL_RESERVE_XIUWEI)
    if isinstance(value, str) and not str(value).strip():
        return int(default if default is not None else DUEL_RESERVE_XIUWEI)
    amount = max(0, _parse_int(value))
    return min(amount, DUEL_MAX_CONFIG_RESERVE_XIUWEI)


def get_duel_reserve_xiuwei():
    """Effective reserve floor for current identity (UI-configurable)."""
    raw = state.get("duel_reserve_xiuwei", 0)
    # 0 / missing = 使用模块默认，避免老身份迁移后变成「无保留」。
    if raw in (None, "", 0, "0"):
        return int(DUEL_RESERVE_XIUWEI)
    return normalize_duel_reserve_xiuwei(raw)


def get_duel_min_xiuwei():
    """Effective min xiuwei gate (= reserve under current reserve-only policy)."""
    return get_duel_reserve_xiuwei()


def _profile_gate_reason():
    profile = get_send_as_profile(get_current_identity_id()) or {}
    realm = str(profile.get("realm") or "").strip()
    xiuwei_current = _parse_int(profile.get("xiuwei_current", 0))
    realm_reason = _realm_gate_reason(realm)
    if realm_reason:
        return realm_reason
    min_xiuwei = get_duel_min_xiuwei()
    reserve = get_duel_reserve_xiuwei()
    if xiuwei_current < min_xiuwei:
        current_text = xiuwei_current if xiuwei_current > 0 else "未知"
        return f"斗法前需至少 {min_xiuwei} 修为（保留 {reserve}），当前={current_text}"
    return ""


def _normalize_identity_token(value):
    return str(value or "").strip().lstrip("@").casefold()


def is_duel_preset_excluded_identity(send_as_id=None, *, username="", label="", daohao=""):
    identity_id = int(send_as_id or 0)
    if identity_id > 0 and identity_id in DUEL_PRESET_EXCLUDED_IDENTITY_IDS:
        return True
    tokens = {
        _normalize_identity_token(username),
        _normalize_identity_token(label),
        _normalize_identity_token(daohao),
    }
    tokens.discard("")
    if tokens & DUEL_PRESET_EXCLUDED_USERNAMES:
        return True
    if tokens & {_normalize_identity_token(item) for item in DUEL_PRESET_EXCLUDED_LABELS}:
        return True
    return False


def classify_duel_preset_band(realm):
    """Return preset band: jiedan | yuanying | none."""
    realm = str(realm or "").strip()
    if realm not in REALM_SORT_ORDER:
        return "none"
    realm_index = REALM_SORT_ORDER.index(realm)
    jiedan_index = REALM_SORT_ORDER.index(DUEL_JIEDAN_MIN_REALM)
    yuanying_index = REALM_SORT_ORDER.index(DUEL_YUANYING_MIN_REALM)
    if realm_index == jiedan_index:
        return "jiedan"
    if realm_index >= yuanying_index:
        return "yuanying"
    return "none"


def _identity_duel_username(profile):
    profile = profile or {}
    username = str(profile.get("username") or "").strip().lstrip("@")
    if username:
        return username
    label = str(profile.get("label") or "").strip().lstrip("@")
    return label


def plan_duel_presets(identity_rows):
    """Build offline/lab preset rows. Does not write state.

    identity_rows: iterable of dict-like with send_as_id, realm, username, label, daohao.
    """
    rows = []
    for raw in identity_rows or ():
        item = dict(raw or {})
        send_as_id = int(item.get("send_as_id") or 0)
        if send_as_id <= 0:
            continue
        rows.append(
            {
                "send_as_id": send_as_id,
                "realm": str(item.get("realm") or "").strip(),
                "username": str(item.get("username") or "").strip(),
                "label": str(item.get("label") or "").strip(),
                "daohao": str(item.get("daohao") or "").strip(),
            }
        )
    rows.sort(key=lambda item: (item["send_as_id"], item["username"]))

    yuanying_targets = []
    jiedan_ids = []
    plan = []
    for item in rows:
        excluded = is_duel_preset_excluded_identity(
            item["send_as_id"],
            username=item["username"],
            label=item["label"],
            daohao=item["daohao"],
        )
        band = classify_duel_preset_band(item["realm"])
        if excluded:
            plan.append(
                {
                    "send_as_id": item["send_as_id"],
                    "band": band,
                    "role": "excluded",
                    "duel_enabled": False,
                    "duel_target": "",
                    "duel_total_count": 0,
                    "reason": "吧唧/WA 预设关闭",
                }
            )
            continue
        if band == "yuanying":
            token = _identity_duel_username(item)
            if token:
                yuanying_targets.append(normalize_duel_target(token))
            plan.append(
                {
                    "send_as_id": item["send_as_id"],
                    "band": band,
                    "role": "yuanying",
                    "duel_enabled": True,
                    "duel_target": DUEL_PRESET_YUANYING_TARGET,
                    "duel_total_count": DUEL_PRESET_TOTAL_COUNT,
                    "reason": f"元婴后预设打 {DUEL_PRESET_YUANYING_TARGET} ×{DUEL_PRESET_TOTAL_COUNT}",
                }
            )
            continue
        if band == "jiedan":
            jiedan_ids.append(item["send_as_id"])
            plan.append(
                {
                    "send_as_id": item["send_as_id"],
                    "band": band,
                    "role": "jiedan",
                    "duel_enabled": True,
                    "duel_target": "",  # filled below
                    "duel_total_count": DUEL_PRESET_TOTAL_COUNT,
                    "reason": "结丹后预设均分打元婴号",
                }
            )
            continue
        plan.append(
            {
                "send_as_id": item["send_as_id"],
                "band": band,
                "role": "none",
                "duel_enabled": False,
                "duel_target": "",
                "duel_total_count": 0,
                "reason": "境界不在结丹后/元婴后预设带",
            }
        )

    # 稳定去重后的元婴目标池，供结丹均分。
    seen_targets = set()
    unique_yuanying_targets = []
    for target in yuanying_targets:
        key = target.casefold()
        if not target or key in seen_targets:
            continue
        seen_targets.add(key)
        unique_yuanying_targets.append(target)

    jiedan_ids = sorted(jiedan_ids)
    by_id = {item["send_as_id"]: item for item in plan}
    if unique_yuanying_targets:
        for index, send_as_id in enumerate(jiedan_ids):
            target = unique_yuanying_targets[index % len(unique_yuanying_targets)]
            row = by_id[send_as_id]
            row["duel_target"] = target
            row["reason"] = (
                f"结丹后预设打元婴 {target} ×{DUEL_PRESET_TOTAL_COUNT}"
                f"（{index + 1}/{len(jiedan_ids)} → 池 {len(unique_yuanying_targets)}）"
            )
    else:
        for send_as_id in jiedan_ids:
            row = by_id[send_as_id]
            row["duel_enabled"] = False
            row["duel_total_count"] = 0
            row["reason"] = "结丹后预设需要至少一个元婴目标，当前池为空"

    # 同目标负载 + 日容量预检（吸收上游 group_duel：排不下要提示）。
    target_hits = {}
    for row in plan:
        if not row.get("duel_enabled"):
            continue
        key = normalize_duel_target(row.get("duel_target") or "").casefold()
        if not key:
            continue
        target_hits[key] = target_hits.get(key, 0) + max(0, int(row.get("duel_total_count") or 0))

    for row in plan:
        if not row.get("duel_enabled"):
            row["capacity"] = {"ok": True, "reason": "", "skipped": True}
            continue
        target_key = normalize_duel_target(row.get("duel_target") or "").casefold()
        capacity = estimate_duel_capacity(
            total_count=row.get("duel_total_count") or 0,
            start_minute=DUEL_DEFAULT_WINDOW_START_MINUTE,
            end_minute=DUEL_DEFAULT_WINDOW_END_MINUTE,
            target_hits=target_hits.get(target_key) if target_key else None,
        )
        row["capacity"] = capacity
        if not capacity.get("ok"):
            row["reason"] = f"{row.get('reason') or ''}｜容量警告：{capacity.get('reason') or '不足'}".lstrip("｜")

    # 可视化分组摘要（上游 group 卡思路：发起带 / 目标池）。
    groups = {
        "yuanying_sources": [
            {
                "send_as_id": row["send_as_id"],
                "target": row.get("duel_target") or "",
                "count": int(row.get("duel_total_count") or 0),
            }
            for row in plan
            if row.get("role") == "yuanying" and row.get("duel_enabled")
        ],
        "jiedan_sources": [
            {
                "send_as_id": row["send_as_id"],
                "target": row.get("duel_target") or "",
                "count": int(row.get("duel_total_count") or 0),
            }
            for row in plan
            if row.get("role") == "jiedan" and row.get("duel_enabled")
        ],
        "excluded": [row["send_as_id"] for row in plan if row.get("role") == "excluded"],
        "disabled": [row["send_as_id"] for row in plan if row.get("role") == "none" or not row.get("duel_enabled")],
        "target_hits": dict(sorted(target_hits.items())),
    }

    return {
        "yuanying_targets": list(unique_yuanying_targets),
        "jiedan_count": len(jiedan_ids),
        "groups": groups,
        "rows": [by_id[item["send_as_id"]] for item in rows if item["send_as_id"] in by_id],
    }


def collect_identity_rows_for_duel_presets():
    """Snapshot current registered identities for preset planning (read-only)."""
    rows = []
    for send_as_id in get_identity_ids():
        profile = get_send_as_profile(send_as_id) or {}
        rows.append(
            {
                "send_as_id": int(send_as_id),
                "realm": str(profile.get("realm") or "").strip(),
                "username": str(profile.get("username") or "").strip(),
                "label": str(profile.get("label") or "").strip(),
                "daohao": str(profile.get("daohao") or "").strip(),
            }
        )
    return rows


def apply_duel_preset_row(row, *, now=None, persist=True, force=False):
    """Apply one planned preset row onto the current identity context.

    稳妥：只写斗法开关/目标/次数（及进度重置），**绝不**改
    ``tianxing_auto_config.duel_route_enabled`` 或任何天星推命/时间线状态。
    天星斗法线仍按原版：默认关，需人在天星配置里显式打开。
    """
    now = float(now if now is not None else time.time())
    row = dict(row or {})
    enabled = bool(row.get("duel_enabled"))
    target = str(row.get("duel_target") or "").strip()
    total_count = max(0, _parse_int(row.get("duel_total_count")))
    if not force:
        has_custom = bool(str(state.get("duel_target") or "").strip()) or int(
            state.get("duel_total_count", 0) or 0
        ) > 0
        if has_custom and state.get("duel_enabled"):
            return {
                "applied": False,
                "reason": "已有斗法配置，跳过预设（force=False）",
                "row": row,
            }
    state["duel_enabled"] = enabled
    if enabled:
        apply_duel_config(
            target=target,
            total_count=total_count,
            reset_progress=True,
            now=now,
            persist=False,
        )
        if float(state.get("next_duel_time", 0) or 0) <= now:
            state["next_duel_time"] = float(now + random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
        state["duel_last_error"] = ""
        state["duel_last_result"] = str(row.get("reason") or "已应用斗法预设")
    else:
        # 只关斗法模块开关与配置；不走 cancel_duel_tianxing_route，
        # 避免「套用排除预设」误关用户已开的天星斗法线（关模块仍走 control 原路径）。
        state["duel_enabled"] = False
        state["duel_target"] = target
        state["duel_total_count"] = total_count
        state["duel_completed_count"] = 0
        state["next_duel_time"] = 0
        _clear_duel_pending()
        state["duel_last_result"] = str(row.get("reason") or "预设关闭斗法")
    if enabled:
        seed_controlled_duel_loadout_prepare(now)
    if persist:
        save_state()
    else:
        mark_dirty()
    return {"applied": True, "reason": row.get("reason") or "", "row": row}


def apply_duel_presets_for_all_identities(*, now=None, persist=True, force=False):
    """Apply planned presets to every registered identity. Lab/main-AI tool path."""
    now = float(now if now is not None else time.time())
    plan = plan_duel_presets(collect_identity_rows_for_duel_presets())
    results = []
    for row in plan.get("rows") or ():
        with use_identity(int(row["send_as_id"])):
            results.append(apply_duel_preset_row(row, now=now, persist=False, force=force))
    if persist:
        save_state()
    else:
        mark_dirty()
    return {"plan": plan, "results": results}


def _target_gate_reason(target):
    target = normalize_duel_target(target)
    if not target:
        return "斗法目标未配置"
    profile = get_send_as_profile(get_current_identity_id()) or {}
    username = str(profile.get("username") or "").strip().lstrip("@").lower()
    target_name = target.lstrip("@").lower()
    if username and target_name == username:
        return f"斗法目标不能是自己：{target}"
    current_id = str(get_current_identity_id() or "").strip()
    if current_id and target == current_id:
        return f"斗法目标不能是自己：{target}"
    return ""


def _is_duel_reply(reply_to=None, matched_family=None):
    if matched_family == "duel":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd == CMD_DUEL or orig_cmd.startswith(f"{CMD_DUEL} ")


def _active_duel_anchor_ids():
    ids = set()
    for key in ("duel_reply_to_msg_id", "duel_open_msg_id", "duel_last_msg_id"):
        msg_id = _parse_int(state.get(key, 0))
        if msg_id > 0:
            ids.add(msg_id)
    return ids


def _has_active_duel_window(now):
    if _parse_int(state.get("duel_reply_to_msg_id", 0)) <= 0:
        return False
    reply_due_at = float(state.get("duel_reply_due_at", 0) or 0)
    if reply_due_at <= 0:
        return False
    return float(now) <= reply_due_at + DUEL_RESULT_GRACE_SEC


def _tag_in_text(text, tag):
    normalized = str(tag or "").strip().lstrip("@")
    if not normalized:
        return False
    pattern = rf"@{re.escape(normalized)}(?=$|[\s|，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`])"
    return re.search(pattern, str(text or ""), re.I) is not None


def is_duel_reply_text(text):
    raw = str(text or "").strip()
    return (
        raw.startswith(DUEL_READY_PREFIX)
        or raw.startswith(DUEL_WAITING_PREFIX)
        or raw.startswith(DUEL_REPORT_PREFIX)
        or raw.startswith(DUEL_FINAL_PREFIX)
        or raw.startswith(DUEL_SETTLING_TEXT)
        or _has_duel_terminal_attempt_keyword(raw)
    )


def _is_phaseful_settlement_text(text):
    compact = re.sub(r"\s+", "", str(text or ""))
    if any(marker in compact for marker in DUEL_PHASEFUL_INTERMEDIATE_MARKERS):
        return True
    return "天道感应：检测到" in compact and "功成圆满，神魂正在归位" in compact


def _first_line(text):
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _is_weak_or_unknown_result(text):
    raw = str(text or "")
    if raw.startswith(DUEL_REPORT_PREFIX) or raw.startswith(DUEL_FINAL_PREFIX):
        profile = get_send_as_profile(get_current_identity_id()) or {}
        username = str(profile.get("username") or "").strip().lstrip("@").lower()
        winner_match = RE_DUEL_WINNER.search(raw)
        loser_match = RE_DUEL_LOSER.search(raw)
        if username and winner_match:
            return winner_match.group(1).strip().lstrip("@").lower() != username
        if username and loser_match:
            return loser_match.group(1).strip().lstrip("@").lower() == username
        return not winner_match
    return any(
        keyword in raw
        for keyword in (
            "虚弱",
            "逃跑",
            "逃脱",
            "天机反噬",
            "出手次数过多",
            "神念不足",
            "神念已耗尽",
            "神念耗尽",
            "无法再次斗法",
            "元神尚未平复",
            "无法锁定对手",
            "尚未踏入仙途",
            "对方正在斗法",
            "你已在斗法",
            "小隐于野",
        )
    )


def _is_duel_report_text(text):
    raw = str(text or "").strip()
    return raw.startswith(DUEL_REPORT_PREFIX) or raw.startswith(DUEL_FINAL_PREFIX)


def _has_duel_terminal_attempt_keyword(text):
    raw = str(text or "")
    return any(keyword in raw for keyword in DUEL_TERMINAL_ATTEMPT_KEYWORDS)


def _duel_counts_as_attempt(text):
    raw = str(text or "").strip()
    if _is_target_named_cooldown(raw) or "出手次数过多" in raw:
        return False
    return _is_duel_report_text(raw) or _has_duel_terminal_attempt_keyword(raw)


def _is_duel_prediction_consuming_result(text):
    raw = str(text or "").strip()
    return _is_duel_report_text(raw) or (
        _duel_counts_as_attempt(raw)
        and any(keyword in raw for keyword in ("【推命命中】", "【推命落空】"))
    )


def _is_target_named_cooldown(text):
    raw = str(text or "")
    target = _target_token().lstrip("@")
    return bool(
        target
        and _tag_in_text(raw, target)
        and ("元神尚未平复" in raw or "无法再次斗法" in raw)
    )


def _target_cooldown_confirmed_by_text(text):
    raw = str(text or "")
    return (
        _is_duel_report_text(raw)
        or any(keyword in raw for keyword in DUEL_TARGET_CONSUMING_TERMINAL_KEYWORDS)
        or "对方正在斗法" in raw
        or _is_target_named_cooldown(raw)
    )


def _target_cooldown_delay_from_text(text):
    raw = str(text or "")
    parsed = parse_wait_time(raw) + CD_BUFFER_SEC if has_wait_time(raw) else 0
    floor = DUEL_SAME_TARGET_COOLDOWN_SEC
    if _is_target_named_cooldown(raw):
        floor += DUEL_TARGET_CONTENTION_BUFFER_SEC
    return max(floor, parsed)


def parse_duel_result_summary(text):
    raw = str(text or "").strip()
    if not raw:
        return "未知"
    if _is_duel_report_text(raw):
        match = RE_DUEL_WINNER.search(raw)
        return f"斗法结束，胜者 {match.group(1)}" if match else "斗法结束"
    if raw.startswith(DUEL_READY_PREFIX):
        return "法宝齐出，等待战报"
    if raw.startswith(DUEL_SETTLING_TEXT):
        return "战斗结束，等待战报"
    if raw.startswith(DUEL_WAITING_PREFIX):
        return "正在锁定对手"
    if "锁定目标时遭遇天机反噬" in raw:
        return "目标锁定失败：天机反噬"
    if "出手次数过多" in raw:
        return _first_line(raw)[:80] or "斗法出手次数受限"
    if "凭借神通侥幸逃脱" in raw or "侥幸逃脱" in raw:
        return _first_line(raw)[:80] or "目标侥幸逃脱"
    if "神念不足" in raw or "神念已耗尽" in raw or "神念耗尽" in raw:
        return _first_line(raw)[:80] or "神念不足"
    return _first_line(raw)[:80] or "未知"


def _duel_batch_stagger_sec():
    total = max(0, int(state.get("duel_total_count", 0) or 0))
    completed = max(0, int(state.get("duel_completed_count", 0) or 0))
    if len(_target_tokens()) <= 1 and total - completed <= 0:
        return 0
    return random.uniform(DUEL_BATCH_STAGGER_MIN_SEC, DUEL_BATCH_STAGGER_MAX_SEC)


def _duel_result_cooldown_sec(weak_or_unknown):
    if weak_or_unknown:
        return random.uniform(DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MIN_SEC, DUEL_WEAK_OR_UNKNOWN_COOLDOWN_MAX_SEC)
    return random.uniform(DUEL_NORMAL_COOLDOWN_MIN_SEC, DUEL_NORMAL_COOLDOWN_MAX_SEC)


def _duel_next_delay_from_result(text, weak_or_unknown):
    raw = str(text or "")
    stagger = _duel_batch_stagger_sec()
    if _is_duel_report_text(raw):
        weakness = RE_DUEL_WEAKNESS.search(raw)
        if weakness and weak_or_unknown:
            return parse_wait_time(weakness.group("wait")) + CD_BUFFER_SEC + stagger
        if not weak_or_unknown:
            return DUEL_SAME_TARGET_COOLDOWN_SEC + CD_BUFFER_SEC + stagger
    if has_wait_time(raw):
        return parse_wait_time(raw) + CD_BUFFER_SEC
    return _duel_result_cooldown_sec(weak_or_unknown) + CD_BUFFER_SEC


def _apply_duel_xiuwei_loss(text):
    raw = str(text or "")
    loser = RE_DUEL_LOSER.search(raw)
    loss = RE_DUEL_XIUWEI_LOSS.search(raw)
    if not loser or not loss:
        return 0
    profile = get_send_as_profile(get_current_identity_id()) or {}
    username = str(profile.get("username") or "").strip().lstrip("@").casefold()
    if not username or loser.group(1).strip().lstrip("@").casefold() != username:
        return 0
    amount = float(loss.group("amount") or 0)
    if loss.group("unit"):
        amount *= 10_000
    amount = max(0, int(round(amount)))
    current = _parse_int(profile.get("xiuwei_current", 0))
    if amount <= 0 or current <= 0:
        return 0
    update_send_as_profile(get_current_identity_id(), xiuwei_current=max(0, current - amount))
    return amount


def _duel_next_time_blocks(now):
    return cd_blocks(state.get("next_duel_time", 0), now, 0)


def _reconcile_consumed_duel_prediction_from_last_report(now):
    if not state.get("tianxing_enabled"):
        return False
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if str(observed.get("current_prediction") or "").strip() != "斗法":
        return False
    set_at = float(observed.get("current_prediction_set_at", 0) or 0)
    last_msg_id = int(state.get("duel_last_msg_id", 0) or 0)
    if set_at <= 0 or last_msg_id <= 0:
        return False
    report = find_message_log_message(
        last_msg_id,
        now,
        lookback_sec=DUEL_TIANXING_RECONCILE_LOOKBACK_SEC,
        lookahead_sec=DUEL_LOG_REPLAY_LOOKAHEAD_SEC,
        predicate=lambda entry: _is_duel_prediction_consuming_result(str((entry or {}).get("text") or "")),
    )
    report_at = float((report or {}).get("ts_epoch", 0) or 0)
    if report_at + 0.001 < set_at:
        return False
    observed["current_prediction"] = ""
    observed["current_prediction_until"] = 0
    observed["prediction_consumed_route"] = "斗法"
    observed["prediction_consumed_at"] = report_at or float(now)
    observed["last_error"] = ""
    state["tianxing_observation"] = observed

    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    released = dict(timeline.get("released_routes") or {})
    released.pop("斗法", None)
    timeline["released_routes"] = released
    active_step = dict(timeline.get("active_step") or {})
    if str(active_step.get("route") or active_step.get("arg") or "").strip() == "斗法":
        timeline["phase"] = "blocked_replan"
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["blocked_until"] = float(now)
        timeline["last_error"] = "上一场斗法已消费推命，需重新准备。"
    timeline["updated_at"] = float(now)
    state["tianxing_timeline_state"] = timeline
    save_state()
    console_log("🌌 已按最后一场真实战报清理已消费的斗法推命。", scope="identity")
    return True


def cancel_duel_tianxing_route(*, now=None, persist=False):
    now = float(now or time.time())
    changed = False

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if str(observed.get("current_prediction") or "").strip() == "斗法":
        observed["current_prediction"] = ""
        observed["current_prediction_until"] = 0
        observed["current_prediction_set_at"] = 0
        observed["prediction_cancelled_route"] = "斗法"
        observed["prediction_cancelled_at"] = now
        observed["last_error"] = "斗法已关闭，未消费的斗法推命已撤销。"
        state["tianxing_observation"] = observed
        changed = True

    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    if config.get("duel_route_enabled"):
        config["duel_route_enabled"] = False
        state["tianxing_auto_config"] = config
        changed = True

    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_step = dict(timeline.get("active_step") or {})
    active_route = str(active_step.get("route") or active_step.get("arg") or timeline.get("route") or "").strip()
    released = dict(timeline.get("released_routes") or {})
    if active_route == "斗法" or "斗法" in released:
        released.pop("斗法", None)
        timeline["phase"] = "blocked_replan"
        timeline["route"] = ""
        timeline["active_step_index"] = -1
        timeline["active_step"] = {}
        timeline["released_routes"] = released
        timeline["blocked_until"] = now
        timeline["last_error"] = "斗法模块已关闭，斗法时间线已撤销。"
        timeline["updated_at"] = now
        state["tianxing_timeline_state"] = timeline
        changed = True

    if changed:
        if persist:
            save_state()
        else:
            mark_dirty()
    return changed


async def _prepare_duel_tianxing_route(now, *, due_at=0):
    _reconcile_consumed_duel_prediction_from_last_report(now)
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan("斗法", reason="斗法", now=now)
    if preflight.get("route_allowed"):
        return True

    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        state["next_duel_time"] = min(
            blocked_until + CD_BUFFER_SEC,
            now + random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC),
        )
        state["duel_last_error"] = str(preflight.get("reason") or "斗法天星预检阻断")
        save_state()
        return False

    if preflight.get("timeline_required"):
        config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
        if not config.get("duel_route_enabled"):
            return True
        duel_config = dict(config)
        duel_config["route_prepare_lead_sec"] = DUEL_TIANXING_PREPARE_LEAD_SEC
        windows = build_tianxing_consume_window(
            "斗法",
            now=now,
            due_at=max(due_at, now),
            config=duel_config,
            reason="斗法",
        )
        if not windows:
            state["duel_last_error"] = str(preflight.get("reason") or "斗法等待天星时间线准备窗口")
            save_state()
            return False
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows, config=duel_config)
        state["duel_last_result"] = f"天星时间线：{timeline_result.get('phase') or 'waiting'}"
        state["duel_last_error"] = "" if timeline_result.get("changed") else str(preflight.get("reason") or "")
        if due_at <= now:
            _schedule_next_duel(now, random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
        save_state()
        return False

    state["duel_last_error"] = str(preflight.get("reason") or "斗法天星预检阻断")
    if due_at <= now:
        _schedule_next_duel(now, random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
    save_state()
    return False


def clear_duel_state(*, persist=False, keep_last_error=False, keep_config=True):
    last_error = state.get("duel_last_error") if keep_last_error else ""
    target = state.get("duel_target", "") if keep_config else ""
    total_count = int(state.get("duel_total_count", 0) or 0) if keep_config else 0
    reserve_xiuwei = int(state.get("duel_reserve_xiuwei", 0) or 0) if keep_config else 0
    window_start = int(state.get("duel_window_start_minute", DUEL_DEFAULT_WINDOW_START_MINUTE) or 0) if keep_config else DUEL_DEFAULT_WINDOW_START_MINUTE
    window_end = int(state.get("duel_window_end_minute", DUEL_DEFAULT_WINDOW_END_MINUTE) or 0) if keep_config else DUEL_DEFAULT_WINDOW_END_MINUTE
    state["next_duel_time"] = 0
    state["duel_target"] = target
    state["duel_total_count"] = total_count
    state["duel_reserve_xiuwei"] = reserve_xiuwei
    state["duel_window_start_minute"] = window_start
    state["duel_window_end_minute"] = window_end
    state["duel_completed_count"] = 0
    _clear_duel_pending()
    state["duel_last_msg_id"] = 0
    state["duel_last_result"] = ""
    state["duel_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def apply_duel_config(
    target=None,
    total_count=None,
    *,
    reserve_xiuwei=None,
    window_start_minute=None,
    window_end_minute=None,
    reset_progress=False,
    now=None,
    persist=True,
):
    if target is not None:
        state["duel_target"] = " ".join(normalize_duel_targets(target))
    if total_count is not None:
        state["duel_total_count"] = max(0, _parse_int(total_count))
    if reserve_xiuwei is not None:
        # 显式写入；空串回落默认并落库为 0（表示用默认）
        if isinstance(reserve_xiuwei, str) and not str(reserve_xiuwei).strip():
            state["duel_reserve_xiuwei"] = 0
        else:
            amount = normalize_duel_reserve_xiuwei(reserve_xiuwei)
            # 与默认相同也写具体值，方便 UI 回显一致；0 仍表示「跟随默认」仅在未配置时
            state["duel_reserve_xiuwei"] = int(amount)
    if window_start_minute is not None or window_end_minute is not None:
        start = normalize_duel_window_minute(
            window_start_minute if window_start_minute is not None else get_duel_window_start_minute(),
            DUEL_DEFAULT_WINDOW_START_MINUTE,
        )
        end = normalize_duel_window_minute(
            window_end_minute if window_end_minute is not None else get_duel_window_end_minute(),
            DUEL_DEFAULT_WINDOW_END_MINUTE,
        )
        if end < start:
            end = start
        state["duel_window_start_minute"] = start
        state["duel_window_end_minute"] = end
    if reset_progress:
        state["duel_completed_count"] = 0
    if now is not None and state.get("duel_enabled") and not _duel_next_time_blocks(now):
        state["next_duel_time"] = float(now + 1)
    seed_controlled_duel_loadout_prepare(now)
    capacity = estimate_duel_capacity(
        total_count=state.get("duel_total_count", 0) or 0,
        start_minute=get_duel_window_start_minute(),
        end_minute=get_duel_window_end_minute(),
        target_hits=None,
    )
    if persist:
        save_state()
    else:
        mark_dirty()
    return {
        "target": state.get("duel_target", ""),
        "targets": _target_tokens(),
        "total_count": int(state.get("duel_total_count", 0) or 0),
        "completed_count": int(state.get("duel_completed_count", 0) or 0),
        "reserve_xiuwei": get_duel_reserve_xiuwei(),
        "reserve_xiuwei_configured": int(state.get("duel_reserve_xiuwei", 0) or 0),
        "window_start_minute": get_duel_window_start_minute(),
        "window_end_minute": get_duel_window_end_minute(),
        "window_label": get_duel_window_label(),
        "capacity": capacity,
    }


def get_duel_status_text():
    targets = _target_tokens()
    target = _target_token() or "未配置"
    target_display = "、".join(targets) if targets else "未配置"
    total_count = int(state.get("duel_total_count", 0) or 0)
    completed_count = int(state.get("duel_completed_count", 0) or 0)
    profile = get_send_as_profile(get_current_identity_id()) or {}
    lines = [
        "🗡️ 斗法",
        f"- 已启用：{'是' if state.get('duel_enabled') else '否'}",
        f"- 当前目标：{target}",
        f"- 目标池：{target_display}",
        f"- 次数：{completed_count}/{total_count if total_count > 0 else '未配置'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_duel_time', 0))}（{fmt_remaining(state.get('next_duel_time', 0))}）",
        f"- 执行窗口：{get_duel_window_label()}（本地时区，UI 可改）",
        f"- 境界门槛：{DUEL_JIEDAN_MIN_REALM}可打；元婴须{DUEL_YUANYING_MIN_REALM}及以上",
        f"- 修为门槛：保留 {get_duel_reserve_xiuwei()}（当前需 ≥ {get_duel_min_xiuwei()}；默认 {DUEL_RESERVE_XIUWEI}，UI 可改）",
        f"- 预设：元婴→{DUEL_PRESET_YUANYING_TARGET}×{DUEL_PRESET_TOTAL_COUNT}；结丹后均分打元婴号×{DUEL_PRESET_TOTAL_COUNT}；吧唧/WA 默认关",
        f"- 当前境界：{profile.get('realm') or '未知'}",
        f"- 当前修为：{_parse_int(profile.get('xiuwei_current', 0)) or '未知'}",
        f"- 待回复命令ID：{int(state.get('duel_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 斗法消息ID：{int(state.get('duel_open_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('duel_reply_due_at', 0))}（{fmt_remaining(state.get('duel_reply_due_at', 0))}）",
        "- 冷却：同一目标全账号共享至少10分钟；批次额外错峰3-8分钟；真实虚弱/CD更长时按回包",
        f"- 最近结果：{state.get('duel_last_result') or '无'}",
    ]
    if state.get("duel_last_error"):
        lines.append(f"- 最近异常：{state['duel_last_error']}")
    return "\n".join(lines)


def get_duel_current_target(now=None):
    return _target_token(now)


def get_duel_daily_limited_targets(now=None):
    now = float(now if now is not None else time.time())
    limited = _daily_limited_targets(now)
    return [target for target in _target_tokens() if _target_cooldown_key(target) in limited]


async def handle_duel_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("duel_enabled"):
        return False
    if not _has_active_duel_window(now):
        return False
    if not _is_duel_reply(reply_to, matched_family=matched_family):
        return False
    reply_to_msg_id = _parse_int(getattr(reply_to, "id", 0))
    if reply_to_msg_id not in _active_duel_anchor_ids():
        return False
    return await _handle_duel_text(text, now, result_msg_id=result_msg_id or int(getattr(reply_to, "id", 0) or 0))


async def handle_duel_broadcast(text, now, event=None, result_msg_id=0):
    if not state.get("duel_enabled") or not _has_active_duel_window(now):
        return False
    raw = str(text or "")
    if not (raw.startswith(DUEL_REPORT_PREFIX) or raw.startswith(DUEL_FINAL_PREFIX)):
        return False
    profile = get_send_as_profile(get_current_identity_id()) or {}
    username = str(profile.get("username") or "").strip().lstrip("@")
    target = _target_token().lstrip("@")
    if username and not _tag_in_text(raw, username):
        return False
    if target and not _tag_in_text(raw, target):
        return False
    if result_msg_id <= 0 and event is not None:
        result_msg_id = int(getattr(event, "id", 0) or 0)
    return await _handle_duel_text(raw, now, result_msg_id=result_msg_id)


async def handle_duel_target_observation(text, now, event=None):
    if not state.get("duel_enabled"):
        return False
    raw = str(text or "")
    if not (raw.startswith(DUEL_REPORT_PREFIX) or raw.startswith(DUEL_FINAL_PREFIX)):
        return False
    target = _target_token(now)
    if not target or not _tag_in_text(raw, target.lstrip("@")):
        return False
    until = _set_target_cooldown(
        target,
        float(now) + DUEL_SAME_TARGET_COOLDOWN_SEC,
        confirmed=True,
        command_msg_id=0,
    )
    save_state()
    console_log(
        f"🗡️ 被动采集到目标 {target} 的斗法战报，共享目标CD→{fmt_abs_ts(until)}",
        scope="global",
        limit=180,
    )
    return True


async def _handle_duel_text(text, now, *, result_msg_id=0):
    raw_text = str(text or "").strip()
    if not raw_text:
        return False

    # A due Yuanying/deep-retreat settlement may be emitted as the first reply
    # to the duel command root. The game can then continue the same duel chain,
    # so this is intermediate evidence, not a duel terminal result.
    if _is_phaseful_settlement_text(raw_text):
        return False

    if raw_text.startswith(DUEL_WAITING_PREFIX):
        state["duel_last_result"] = "正在锁定对手"
        state["duel_last_error"] = ""
        if result_msg_id:
            state["duel_open_msg_id"] = int(result_msg_id)
        save_state()
        return True

    if raw_text.startswith(DUEL_READY_PREFIX) or raw_text.startswith(DUEL_SETTLING_TEXT):
        state["duel_open_msg_id"] = int(result_msg_id or state.get("duel_open_msg_id", 0) or 0)
        state["duel_reply_due_at"] = float(now + DUEL_REPLY_TIMEOUT_SEC)
        state["duel_last_msg_id"] = int(result_msg_id or 0)
        state["duel_last_result"] = parse_duel_result_summary(raw_text)
        state["duel_last_error"] = ""
        save_state()
        return True

    target = _target_token(now)
    pending_command_msg_id = _parse_int(state.get("duel_reply_to_msg_id", 0))
    summary = parse_duel_result_summary(raw_text)
    weak_or_unknown = _is_weak_or_unknown_result(raw_text)
    xiuwei_loss = _apply_duel_xiuwei_loss(raw_text)
    _clear_duel_pending()
    state["duel_last_msg_id"] = int(result_msg_id or 0)
    state["duel_last_result"] = summary
    state["duel_phaseful_retry_count"] = 0
    if xiuwei_loss > 0:
        state["duel_last_result"] = f"{summary}｜修为-{xiuwei_loss}"
    normal_target_cooldown = _is_target_named_cooldown(raw_text)
    state["duel_last_error"] = "" if (not weak_or_unknown or normal_target_cooldown) else summary
    if _target_cooldown_confirmed_by_text(raw_text):
        _set_target_cooldown(
            target,
            now + _target_cooldown_delay_from_text(raw_text),
            confirmed=True,
            command_msg_id=pending_command_msg_id,
        )
    else:
        _clear_target_reservation(target, pending_command_msg_id)
    if "出手次数过多" in raw_text:
        limit_match = RE_DUEL_DAILY_LIMIT_TARGET.search(raw_text)
        limited_target = normalize_duel_target(limit_match.group("target") if limit_match else target)
        _mark_target_daily_limited(limited_target, now)
        next_target = _target_token(now)
        state["duel_last_error"] = ""
        if next_target:
            _schedule_next_duel(now, random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
            save_state()
            await send_audit_log(
                f"🗡️ 目标 {limited_target} 今日出手额度已满，切换至 {next_target}。",
                scope="identity",
                limit=220,
            )
            return True
        completion = _complete_duel_batch(now)
        save_state()
        if completion["restoring"]:
            await send_audit_log(
                f"✅ 今日可用斗法目标均已封顶（完成 {completion['completed_count']} 场），开始恢复原法宝配装。",
                scope="identity",
                limit=240,
            )
            return True
        await send_audit_log(
            f"✅ 今日可用斗法目标均已封顶（完成 {completion['completed_count']} 场），"
            f"次日批次→{fmt_abs_ts(completion['next_duel_time'])}。",
            scope="identity",
            limit=240,
        )
        return True
    if _duel_counts_as_attempt(raw_text):
        state["duel_completed_count"] = int(state.get("duel_completed_count", 0) or 0) + 1
        total_count = int(state.get("duel_total_count", 0) or 0)
        if total_count > 0 and int(state.get("duel_completed_count", 0) or 0) >= total_count:
            completion = _complete_duel_batch(now)
            save_state()
            if completion["restoring"]:
                await send_audit_log(
                    f"✅ 今日斗法完成：{completion['completed_count']}/{total_count}，开始恢复原法宝配装",
                    scope="identity",
                    limit=220,
                )
                return True
            if completion["daily"]:
                await send_audit_log(
                    f"✅ 今日斗法完成：{completion['completed_count']}/{total_count}，"
                    f"次日批次→{fmt_abs_ts(completion['next_duel_time'])}",
                    scope="identity",
                    limit=220,
                )
                return True
            await send_audit_log(
                f"✅ 斗法已关闭：{completion['completed_count']}/{total_count}",
                scope="identity",
                limit=180,
            )
            return True
    _schedule_next_duel(now, _duel_next_delay_from_result(raw_text, weak_or_unknown))
    save_state()
    await send_audit_log(f"🗡️ 斗法结果：{summary}", scope="identity", limit=220)
    return True


def _is_duel_reply_log_entry(entry):
    return is_duel_reply_text(str((entry or {}).get("text") or "").strip())


async def _recover_duel_pending_from_message_log(now, reply_to_msg_id):
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id <= 0:
        return False
    command = build_duel_command()
    reply_to = SimpleNamespace(id=reply_to_msg_id, raw_text=command)
    replies = find_message_log_replies(
        reply_to_msg_id,
        now,
        lookback_sec=DUEL_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=DUEL_LOG_REPLAY_LOOKAHEAD_SEC,
        predicate=_is_duel_reply_log_entry,
    )
    handled_any = False
    for entry in replies:
        handled = await handle_duel_reply(
            entry.get("text") or "",
            float(entry.get("ts_epoch") or now),
            reply_to=reply_to,
            matched_family="duel",
            result_msg_id=int(entry.get("message_id") or 0),
        )
        handled_any = handled_any or handled
    return handled_any


async def run_duel_scheduler(now):
    # Reconcile real battle evidence before scheduling the next batch so a
    # consumed duel prediction cannot remain leased and block another route.
    _reconcile_consumed_duel_prediction_from_last_report(now)
    reconcile_duel_from_message_log(now)
    loadout_config = _controlled_loadout_config(now)
    if loadout_config and await _run_controlled_loadout_restore(now, loadout_config):
        return
    if not state.get("duel_enabled"):
        return

    targets = _target_tokens()
    if not targets:
        if not _duel_next_time_blocks(now):
            _set_duel_error("斗法目标未配置", next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return
    target = _target_token(now)
    if not target:
        if not _duel_next_time_blocks(now):
            state["next_duel_time"] = _next_daily_duel_time(now)
            state["duel_last_error"] = ""
            state["duel_last_result"] = f"今日可用斗法目标均已封顶；次日批次→{fmt_abs_ts(state['next_duel_time'])}"
            save_state()
        return
    target_gate_reason = _target_gate_reason(target)
    if target_gate_reason:
        if not _duel_next_time_blocks(now):
            _set_duel_error(target_gate_reason, next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    gate_reason = _profile_gate_reason()
    if gate_reason:
        if not _duel_next_time_blocks(now):
            _set_duel_error(gate_reason, next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    total_count = int(state.get("duel_total_count", 0) or 0)
    completed_count = int(state.get("duel_completed_count", 0) or 0)
    if total_count > 0 and completed_count >= total_count:
        completion = _complete_duel_batch(now)
        if completion["restoring"]:
            pass
        elif completion["daily"]:
            state["duel_last_result"] = (
                f"今日任务完成：{completed_count}/{total_count}；"
                f"次日批次→{fmt_abs_ts(completion['next_duel_time'])}"
            )
        else:
            state["duel_last_result"] = f"斗法已关闭：{completed_count}/{total_count}"
        save_state()
        return
    if (
        str(state.get("duel_log_reconcile_day") or "") == _duel_day_key(now)
        and int(state.get("duel_observed_mind_remaining", -1)) == 0
    ):
        completion = _complete_duel_batch(now)
        state["duel_last_result"] = "今日神念已耗尽"
        state["duel_last_error"] = ""
        save_state()
        if completion["restoring"]:
            await send_audit_log("✅ 今日神念已耗尽，开始恢复原法宝配装。", scope="identity", limit=200)
        return
    if total_count <= 0:
        if not _duel_next_time_blocks(now):
            _set_duel_error("斗法次数未配置", next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    loadout_prepare_due = float(state.get("next_duel_time", 0) or 0) <= float(now) + DUEL_TIANXING_PREPARE_LEAD_SEC
    if loadout_config and loadout_prepare_due and not await _run_controlled_loadout_prepare(now, loadout_config):
        return

    reply_to_msg_id = int(state.get("duel_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("duel_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        if await _recover_duel_pending_from_message_log(now, reply_to_msg_id):
            save_state()
            await send_audit_log(f"🗡️ 斗法日志补偿：已采纳超时回包，消息ID={reply_to_msg_id}", scope="identity", limit=220)
            return
        _clear_duel_pending()
        state["duel_last_error"] = "斗法回复超时"
        _schedule_next_duel(now, _duel_result_cooldown_sec(True) + CD_BUFFER_SEC)
        save_state()
        await send_audit_log(f"⚠️ 斗法回复超时，消息ID={reply_to_msg_id}，进入长冷却。", scope="identity", limit=220)
        return

    next_duel_time = float(state.get("next_duel_time", 0) or 0)
    if next_duel_time > now:
        windows = build_tianxing_consume_window("斗法", now=now, due_at=next_duel_time, reason="斗法")
        if windows and not await _prepare_duel_tianxing_route(now, due_at=next_duel_time):
            return
    if _duel_next_time_blocks(now):
        return

    # 可配执行时间窗外不发起新斗法（进行中 pending 仍由上面超时/补偿路径处理）。
    # 默认全天窗时本分支不触发，行为与原版一致。
    # 稳妥：改期到开窗后，若已进入 lead，按原版 future-due 路径提前备天星，本 tick 仍不发送。
    if not is_within_duel_exec_window(now):
        open_at = next_duel_exec_window_open(now)
        delay = max(1.0, open_at - now)
        _schedule_next_duel(now, delay)
        state["duel_last_error"] = f"不在斗法执行窗口 {get_duel_window_label()}，下次 {fmt_abs_ts(open_at)}"
        if open_at > now:
            windows = build_tianxing_consume_window("斗法", now=now, due_at=open_at, reason="斗法")
            if windows:
                prepared = await _prepare_duel_tianxing_route(now, due_at=open_at)
                if not prepared:
                    return
        save_state()
        return

    participant_block = _active_duel_participant_block(target, now)
    if participant_block:
        _schedule_next_duel(now, random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
        state["duel_last_error"] = participant_block
        save_state()
        return

    target_cooldown_until = _target_cooldown_until(target)
    if target_cooldown_until > now:
        _schedule_next_duel(now, (target_cooldown_until - now) + _duel_batch_stagger_sec())
        state["duel_last_error"] = f"目标 {target} 仍在斗法冷却"
        save_state()
        return

    if not await _prepare_duel_tianxing_route(now, due_at=now):
        return

    command = build_duel_command(target)
    msg = await send_game_command(command, track=False, max_retry=0, source_module="斗法")
    if not msg:
        send_block = classify_game_send_block(get_current_identity_id(), command)
        if send_block.get("status") == "unsent":
            blocked_until = float(send_block.get("blocked_until", 0) or 0)
            next_delay = DUEL_RECOVERY_MIN_SEC
            if blocked_until > now:
                next_delay = max(next_delay, blocked_until - now + random.uniform(10, 60))
            block_code = str(send_block.get("code") or "runtime_block")
            _set_duel_error(f"斗法未发送: {block_code}", next_delay=next_delay, now=now)
            console_log(f"🗡️ 斗法未发送：{block_code}，延后至 {fmt_abs_ts(state['next_duel_time'])}", scope="identity")
            return
        _set_duel_error("斗法发送失败", next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        await send_audit_log("❌ 斗法发送失败，稍后重试。", scope="identity", limit=180)
        return

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["duel_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["duel_reply_due_at"] = sent_at + DUEL_REPLY_TIMEOUT_SEC
    state["duel_open_msg_id"] = 0
    state["duel_magic_due_at"] = 0
    state["duel_magic_sent_at"] = 0
    state["duel_started_at"] = sent_at
    state["duel_phaseful_retry_count"] = 0
    state["duel_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["duel_last_result"] = "已发送"
    state["duel_last_error"] = ""
    state["next_duel_time"] = state["duel_reply_due_at"]
    _set_target_cooldown(
        target,
        sent_at + DUEL_TARGET_RESERVATION_SEC,
        confirmed=False,
        command_msg_id=state["duel_reply_to_msg_id"],
    )
    save_state()
    console_log(f"🗡️ 斗法已发送：{command}，等待战报→{fmt_abs_ts(state['duel_reply_due_at'])}", scope="identity", limit=180)


def schedule_duel_initial_check(now, *, persist=False, keep_last_error=True):
    last_error = state.get("duel_last_error") if keep_last_error else ""
    _clear_duel_pending()
    state["duel_last_error"] = last_error or ""
    state["next_duel_time"] = float(now + random.uniform(DUEL_RECOVERY_MIN_SEC, DUEL_RECOVERY_MAX_SEC))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_duel_time"]


__all__ = [
    "CMD_DUEL",
    "DUEL_CAPACITY_SELF_INTERVAL_SEC",
    "DUEL_CAPACITY_TARGET_INTERVAL_SEC",
    "DUEL_DEFAULT_WINDOW_END_MINUTE",
    "DUEL_DEFAULT_WINDOW_START_MINUTE",
    "DUEL_JIEDAN_MIN_REALM",
    "DUEL_MAX_CONFIG_RESERVE_XIUWEI",
    "DUEL_MIN_REALM",
    "DUEL_MIN_XIUWEI",
    "DUEL_NORMAL_COOLDOWN_SEC",
    "DUEL_PRESET_TOTAL_COUNT",
    "DUEL_PRESET_YUANYING_TARGET",
    "DUEL_RESERVE_XIUWEI",
    "DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC",
    "DUEL_YUANYING_MIN_REALM",
    "apply_duel_config",
    "apply_duel_preset_row",
    "apply_duel_presets_for_all_identities",
    "build_duel_command",
    "cancel_duel_tianxing_route",
    "classify_duel_preset_band",
    "clear_duel_state",
    "collect_identity_rows_for_duel_presets",
    "estimate_duel_capacity",
    "get_duel_min_xiuwei",
    "get_duel_current_target",
    "get_duel_daily_limited_targets",
    "get_duel_reserve_xiuwei",
    "get_duel_status_text",
    "get_duel_window_bounds",
    "get_duel_window_end_minute",
    "get_duel_window_label",
    "get_duel_window_start_minute",
    "handle_duel_broadcast",
    "handle_duel_target_observation",
    "handle_duel_reply",
    "is_duel_preset_excluded_identity",
    "is_duel_reply_text",
    "is_within_duel_exec_window",
    "next_duel_exec_window_open",
    "normalize_duel_reserve_xiuwei",
    "normalize_duel_target",
    "normalize_duel_targets",
    "normalize_duel_window_minute",
    "parse_duel_result_summary",
    "plan_duel_presets",
    "run_duel_scheduler",
    "schedule_duel_initial_check",
    "seed_controlled_duel_loadout_prepare",
]
