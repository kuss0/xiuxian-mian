import asyncio
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_PULSE,
    CMD_TREE_PULSE_STATUS,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    FREEZE_CD,
    GUARD_INTERVAL_MAX,
    GUARD_INTERVAL_MIN,
    IRR_INTERVAL_MAX,
    IRR_INTERVAL_MIN,
    RE_TREE_REMAINING,
    RETRY_MAX_SEC,
    TREE_GUARD_ACTIVE_COUNT,
    TREE_GUARD_INITIAL_DELAY_MAX_SEC,
    TREE_GUARD_INITIAL_DELAY_MIN_SEC,
    TREE_GUARD_SELECTION_WINDOW_SEC,
    is_account_offline,
)
from ..persistence import save_state
from ..runtime import _fire_and_forget, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_account, get_identity_enabled, get_identity_ids, get_identity_state, get_pending_command, get_send_as_tags, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage
from .storage_bag import apply_storage_bag_item_deltas


TREE_MATURING_FIRST_STATUS_MIN_SEC = 10 * 60
TREE_MATURING_FIRST_STATUS_MAX_SEC = 20 * 60
TREE_HARVEST_FOLLOWUP_DELAY_SEC = 30 * 60
TREE_IRRIGATION_INSUFFICIENT_POWER_KEYWORDS = ("修为不足", "无法调动天地灵气")
TREE_GUARD_FREEZE_THRESHOLD_SEC = FREEZE_CD / 2
TREE_STATUS_DEDUPE_SEC = 4 * 60
TREE_STARTUP_STATUS_RECENT_SEC = 30 * 60
TREE_HARVEST_INFLIGHT_SEC = RETRY_MAX_SEC
TREE_BOOTSTRAP_CHECK_DELAY_MIN_SEC = 10 * 60
TREE_BOOTSTRAP_CHECK_DELAY_MAX_SEC = 45 * 60
TREE_BOOTSTRAP_CHECK_RETRY_MIN_SEC = 30 * 60
TREE_BOOTSTRAP_CHECK_RETRY_MAX_SEC = 60 * 60
TREE_MATURE_CONFIRM_DELAY_MIN_SEC = 10
TREE_MATURE_CONFIRM_DELAY_MAX_SEC = 30
TREE_HARVEST_ABNORMAL_CHECK_MIN_SEC = 60
TREE_HARVEST_ABNORMAL_CHECK_MAX_SEC = 180
TREE_HARVEST_RETRY_LIMIT = 1
TREE_IRRIGATION_RETRY_LIMIT = 1
TREE_IRRIGATION_REPLY_TIMEOUT_SEC = 30
TREE_PULSE_REPLY_TIMEOUT_SEC = 45
TREE_PULSE_PANEL_RECENT_SEC = 12 * 3600
TREE_PULSE_STATUS_SPREAD_MIN_SEC = 10 * 60
TREE_PULSE_STATUS_SPREAD_MAX_SEC = 45 * 60
TREE_PULSE_ACTION_DELAY_MIN_SEC = 35
TREE_PULSE_ACTION_DELAY_MAX_SEC = 90
TREE_PULSE_ACTION_FOLLOWUP_MIN_SEC = 75
TREE_PULSE_ACTION_FOLLOWUP_MAX_SEC = 150
TREE_PULSE_BLOCKED_CHECK_MIN_SEC = 30 * 60
TREE_PULSE_BLOCKED_CHECK_MAX_SEC = 60 * 60
TREE_PULSE_LOW_STABILITY_THRESHOLD = 45
TREE_NORMAL_PANEL_RECOVERY_SPREAD_MIN_SEC = 45 * 60
TREE_NORMAL_PANEL_RECOVERY_SPREAD_MAX_SEC = 75 * 60
TREE_IRRIGATION_RESOURCE_KEY = "tree_irrigation"
TREE_GUARD_RESOURCE_KEY = "tree_guard"
TREE_PULSE_RESOURCE_KEY = "tree_pulse"
RE_TREE_HARVEST_FRUIT = re.compile(r"你摘下一枚【([^】]+)】")
RE_TREE_HARVEST_XIUWEI = re.compile(r"修为增长[:：]\s*\+?\s*([\d,]+)")
RE_TREE_HARVEST_LINGWEN = re.compile(r"灵纹回馈[:：].*?\+\s*([\d,]+)\s*点修为")
RE_TREE_HARVEST_REWARD_ITEM = re.compile(r"【([^】]+)】\s*(?:[xX×]\s*([\d,]+))?")
TREE_HARVEST_REWARD_KEYWORDS = ("获得【", "分得【", "稳定分得【")
RE_TREE_PULSE_PROGRESS = re.compile(r"([0-9]+(?:\.[0-9]+)?)%")
RE_TREE_PULSE_ELEMENTS = re.compile(r"主脉【([^】]+)】\s*/\s*辅脉【([^】]+)】\s*/\s*逆脉【([^】]+)】\s*/\s*平脉【([^】]+)】")
RE_TREE_PULSE_MAIN = re.compile(r"主脉\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_AUX = re.compile(r"辅脉\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_REVERSE = re.compile(r"逆脉\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_NEUTRAL = re.compile(r"(?:平脉|中脉)\s*(?:[:：]\s*)?【?([金木水火土/、,，\s]+)】?")
RE_TREE_PULSE_STABILITY = re.compile(r"脉稳[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_STABILITY_CURRENT = re.compile(r"(?:脉稳|稳固|稳定)[^\n]*[（(]当前\s*(\d+)")
RE_TREE_PULSE_TURBIDITY = re.compile(r"(?:浊息|浊气)/紊乱[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_TURBIDITY_CURRENT = re.compile(r"(?:浊息|浊气)[^\n]*[（(]当前\s*(\d+)")
RE_TREE_PULSE_DAILY = re.compile(r"今日定脉令[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_RUSH = re.compile(r"冲脉\s*(\d+)\s*/\s*(\d+)")


def _parse_tree_int(text):
    return int(str(text or "0").replace(",", "") or 0)


def _parse_tree_harvest_items(raw_text):
    items = {}
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not any(keyword in line for keyword in TREE_HARVEST_REWARD_KEYWORDS):
            continue
        for item_name, raw_count in RE_TREE_HARVEST_REWARD_ITEM.findall(line):
            item_name = item_name.strip()
            count = _parse_tree_int(raw_count or 1)
            if item_name and count > 0:
                items[item_name] = items.get(item_name, 0) + count
    return items


def _parse_tree_harvest_result(text):
    raw_text = str(text or "")
    fruit_match = RE_TREE_HARVEST_FRUIT.search(raw_text)
    xiuwei_match = RE_TREE_HARVEST_XIUWEI.search(raw_text)
    lingwen_match = RE_TREE_HARVEST_LINGWEN.search(raw_text)
    return {
        "fruit": fruit_match.group(1).strip() if fruit_match else "",
        "xiuwei": _parse_tree_int(xiuwei_match.group(1)) if xiuwei_match else 0,
        "lingwen_xiuwei": _parse_tree_int(lingwen_match.group(1)) if lingwen_match else 0,
        "items": _parse_tree_harvest_items(raw_text),
    }


def _format_tree_harvest_items(items):
    return "、".join(f"{name}x{count}" for name, count in (items or {}).items())


def _format_tree_harvest_audit(parsed, storage_changed):
    parsed = parsed if isinstance(parsed, dict) else {}
    parts = ["🍒 灵果采摘已确认"]
    fruit = str(parsed.get("fruit") or "").strip()
    if fruit:
        parts[0] = f"🍒 灵果采摘已确认：{fruit}"
    xiuwei = int(parsed.get("xiuwei") or 0)
    lingwen_xiuwei = int(parsed.get("lingwen_xiuwei") or 0)
    if xiuwei > 0:
        parts.append(f"修为 +{xiuwei}")
    if lingwen_xiuwei > 0:
        parts.append(f"灵纹 +{lingwen_xiuwei}")
    items = parsed.get("items") or {}
    if items:
        sync_text = "已同步" if storage_changed else "未变更"
        parts.append(f"储物袋 +{_format_tree_harvest_items(items)}（{sync_text}）")
    return "｜".join(parts)


def _is_tree_pulse_panel(text):
    raw_text = str(text or "")
    return (
        "【落云宗 · 灵树玩法】" in raw_text
        or "云梦灵眼定脉" in raw_text
        or "今日脉象:" in raw_text
        or "今日脉象：" in raw_text
    ) and ("今日定脉令" in raw_text or "定脉玩法" in raw_text)


def _split_tree_elements(raw_value):
    return [part.strip() for part in re.split(r"[/、,，\s]+", str(raw_value or "")) if part.strip()]


def _tree_regex_text(pattern, raw_text):
    match = pattern.search(str(raw_text or ""))
    return match.group(1).strip() if match else ""


def _tree_regex_int_pair(pattern, raw_text):
    match = pattern.search(str(raw_text or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def parse_tree_pulse_panel(text):
    raw_text = str(text or "")
    if not _is_tree_pulse_panel(raw_text):
        return None

    progress_match = RE_TREE_PULSE_PROGRESS.search(raw_text)
    element_match = RE_TREE_PULSE_ELEMENTS.search(raw_text)
    daily_match = RE_TREE_PULSE_DAILY.search(raw_text)
    rush_match = RE_TREE_PULSE_RUSH.search(raw_text)
    main = element_match.group(1).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_MAIN, raw_text)
    aux = element_match.group(2).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_AUX, raw_text)
    reverse = element_match.group(3).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_REVERSE, raw_text)
    neutral_raw = element_match.group(4).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_NEUTRAL, raw_text)
    stability, stability_max = _tree_regex_int_pair(RE_TREE_PULSE_STABILITY, raw_text)
    if stability <= 0:
        current_stability = _tree_regex_text(RE_TREE_PULSE_STABILITY_CURRENT, raw_text)
        if current_stability:
            stability = int(current_stability)
            stability_max = 100
    turbidity, turbidity_max = _tree_regex_int_pair(RE_TREE_PULSE_TURBIDITY, raw_text)
    if turbidity <= 0:
        current_turbidity = _tree_regex_text(RE_TREE_PULSE_TURBIDITY_CURRENT, raw_text)
        if current_turbidity:
            turbidity = int(current_turbidity)
            turbidity_max = 100
    return {
        "progress": float(progress_match.group(1)) if progress_match else 0.0,
        "main": main,
        "aux": aux,
        "reverse": reverse,
        "neutral": neutral_raw,
        "neutral_elements": _split_tree_elements(neutral_raw),
        "stability": stability,
        "stability_max": stability_max,
        "turbidity": turbidity,
        "turbidity_max": turbidity_max,
        "daily_used": int(daily_match.group(1)) if daily_match else 0,
        "daily_limit": int(daily_match.group(2)) if daily_match else 0,
        "rush_used": int(rush_match.group(1)) if rush_match else 0,
        "rush_limit": int(rush_match.group(2)) if rush_match else 0,
        "blocked": "已成熟" in raw_text or "正遭劫难" in raw_text or "不可定脉" in raw_text,
    }


def _apply_tree_pulse_panel(parsed, now):
    if not isinstance(parsed, dict):
        return False
    state["tree_pulse_mode_seen"] = True
    state["tree_pulse_last_panel_at"] = float(now or time.time())
    state["tree_pulse_progress"] = float(parsed.get("progress", 0.0) or 0.0)
    state["tree_pulse_main"] = str(parsed.get("main") or "")
    state["tree_pulse_aux"] = str(parsed.get("aux") or "")
    state["tree_pulse_reverse"] = str(parsed.get("reverse") or "")
    state["tree_pulse_neutral"] = str(parsed.get("neutral") or "")
    state["tree_pulse_stability"] = int(parsed.get("stability", 0) or 0)
    state["tree_pulse_stability_max"] = int(parsed.get("stability_max", 0) or 0)
    state["tree_pulse_turbidity"] = int(parsed.get("turbidity", 0) or 0)
    state["tree_pulse_turbidity_max"] = int(parsed.get("turbidity_max", 0) or 0)
    state["tree_pulse_daily_used"] = int(parsed.get("daily_used", 0) or 0)
    state["tree_pulse_daily_limit"] = int(parsed.get("daily_limit", 0) or 0)
    state["tree_pulse_rush_used"] = int(parsed.get("rush_used", 0) or 0)
    state["tree_pulse_rush_limit"] = int(parsed.get("rush_limit", 0) or 0)
    return True


def _format_tree_pulse_command(action, element=""):
    action = str(action or "").strip()
    element = str(element or "").strip()
    if not action:
        return ""
    return f"{CMD_TREE_PULSE} {action} {element}".strip()


def _tree_pulse_next_day_delay(now):
    local_now = time.localtime(float(now or time.time()))
    next_midnight = time.mktime((
        local_now.tm_year,
        local_now.tm_mon,
        local_now.tm_mday + 1,
        0,
        0,
        0,
        local_now.tm_wday,
        local_now.tm_yday,
        local_now.tm_isdst,
    ))
    return max(30 * 60, next_midnight - float(now or time.time()) + random.uniform(10 * 60, 45 * 60))


def _schedule_tree_pulse_status_spread(now=None):
    now = float(now if now is not None else time.time())
    delay = random.uniform(TREE_PULSE_STATUS_SPREAD_MIN_SEC, TREE_PULSE_STATUS_SPREAD_MAX_SEC)
    state["next_irr_time"] = now + delay
    return delay


def _schedule_tree_pulse_blocked_check(now=None):
    now = float(now if now is not None else time.time())
    delay = random.uniform(TREE_PULSE_BLOCKED_CHECK_MIN_SEC, TREE_PULSE_BLOCKED_CHECK_MAX_SEC)
    state["tree_pulse_blocked_until"] = now + delay
    state["next_irr_time"] = now + delay
    return delay


def _choose_tree_pulse_command(parsed):
    if not isinstance(parsed, dict):
        return "", "未读到定脉面板"
    progress = float(parsed.get("progress", 0.0) or 0.0)
    daily_limit = int(parsed.get("daily_limit", 0) or 0)
    daily_used = int(parsed.get("daily_used", 0) or 0)
    main = str(parsed.get("main") or "").strip()
    neutral_elements = list(parsed.get("neutral_elements") or [])
    stability = int(parsed.get("stability", 0) or 0)
    turbidity = int(parsed.get("turbidity", 0) or 0)

    if parsed.get("blocked") or progress >= 99.9:
        return "", "灵树已成熟或遭劫难"
    if daily_limit > 0 and daily_used >= daily_limit:
        return "", "今日定脉令已满"
    if not main:
        return "", "未识别主脉"
    if turbidity > 0:
        return _format_tree_pulse_command("净浊"), "浊息优先净化"
    if stability > 0 and stability < TREE_PULSE_LOW_STABILITY_THRESHOLD:
        element = "土" if "土" in neutral_elements else (neutral_elements[0] if neutral_elements else main)
        return _format_tree_pulse_command("固脉", element), "脉稳偏低"
    return _format_tree_pulse_command("注灵", main), "主脉注灵"


def _tree_pulse_snapshot_from_state():
    neutral = str(state.get("tree_pulse_neutral") or "")
    return {
        "progress": float(state.get("tree_pulse_progress", 0.0) or 0.0),
        "main": str(state.get("tree_pulse_main") or ""),
        "aux": str(state.get("tree_pulse_aux") or ""),
        "reverse": str(state.get("tree_pulse_reverse") or ""),
        "neutral": neutral,
        "neutral_elements": _split_tree_elements(neutral),
        "stability": int(state.get("tree_pulse_stability", 0) or 0),
        "stability_max": int(state.get("tree_pulse_stability_max", 0) or 0),
        "turbidity": int(state.get("tree_pulse_turbidity", 0) or 0),
        "turbidity_max": int(state.get("tree_pulse_turbidity_max", 0) or 0),
        "daily_used": int(state.get("tree_pulse_daily_used", 0) or 0),
        "daily_limit": int(state.get("tree_pulse_daily_limit", 0) or 0),
        "rush_used": int(state.get("tree_pulse_rush_used", 0) or 0),
        "rush_limit": int(state.get("tree_pulse_rush_limit", 0) or 0),
        "blocked": False,
    }


def _tree_pulse_panel_is_recent(now=None):
    now = float(now if now is not None else time.time())
    last_panel_at = float(state.get("tree_pulse_last_panel_at", 0) or 0)
    return last_panel_at > 0 and now - last_panel_at < TREE_PULSE_PANEL_RECENT_SEC


def _is_tree_legacy_disabled_prompt(text):
    raw_text = str(text or "")
    return "当前管理员已关闭旧版【灵树灌溉】玩法" in raw_text or "请改用【云梦灵眼定脉】" in raw_text


def _is_tree_pulse_blocked_prompt(text):
    return "灵眼之树已成熟或正遭劫难，此刻不可定脉" in str(text or "")


def _is_tree_pulse_action_success(text):
    raw_text = str(text or "")
    if _is_tree_pulse_blocked_prompt(raw_text) or _is_tree_legacy_disabled_prompt(raw_text):
        return False
    if any(keyword in raw_text for keyword in ("冷却", "等待", "不足", "不可", "不能", "失败")):
        return False
    return any(keyword in raw_text for keyword in ("定脉", "注灵", "固脉", "净浊", "冲脉", "脉稳", "浊息", "进度", "宗门贡献"))


def _is_duplicate_tree_harvest_result(result_msg_id, reply_to_msg_id):
    result_msg_id = int(result_msg_id or 0)
    reply_to_msg_id = int(reply_to_msg_id or 0)
    return (
        result_msg_id > 0
        and result_msg_id == int(state.get("tree_last_harvest_result_msg_id", 0) or 0)
    ) or (
        reply_to_msg_id > 0
        and reply_to_msg_id == int(state.get("tree_last_harvest_reply_to_msg_id", 0) or 0)
    )


def _is_tree_irrigation_insufficient_power(text):
    return all(keyword in str(text or "") for keyword in TREE_IRRIGATION_INSUFFICIENT_POWER_KEYWORDS)


def _is_tree_guard_insufficient_power(text):
    raw_text = str(text or "")
    return "修为不足" in raw_text and "大阵注入灵力" in raw_text


def _is_tree_irrigation_success(text):
    raw_text = str(text or "")
    return "灵树灌溉" in raw_text and ("【💧" in raw_text or "【🌿" in raw_text or "【⛰️" in raw_text or "【🔥" in raw_text)


def _is_tree_guard_success(text):
    raw_text = str(text or "")
    return "【守山成功】" in raw_text or "【守护成功！】" in raw_text or "攻势已被成功击退" in raw_text


def _next_irrigation_delay():
    return random.uniform(IRR_INTERVAL_MIN, IRR_INTERVAL_MAX)


def _normalize_tree_identity_text(text):
    return "".join(str(text or "").strip().lstrip("@").split()).casefold()


def _tree_panel_matches_current_identity(text):
    for line in str(text or "").splitlines():
        if "(你)" not in line and "（你）" not in line:
            continue
        compact_line = _normalize_tree_identity_text(line)
        for tag in get_send_as_tags():
            normalized_tag = _normalize_tree_identity_text(tag)
            if len(normalized_tag) >= 3 and normalized_tag in compact_line:
                return True
    return False


def _line_matches_tree_identity(line, identity_id):
    compact_line = _normalize_tree_identity_text(line)
    for tag in get_send_as_tags(identity_id):
        normalized_tag = _normalize_tree_identity_text(tag)
        if len(normalized_tag) >= 3 and normalized_tag in compact_line:
            return True
    return False


def _tree_final_board_unclaimed_identity_ids(text):
    raw_text = str(text or "")
    if not (
        "本轮最终贡献榜" in raw_text
        or "本轮最终分枝榜" in raw_text
        or "天道榜单已定格" in raw_text
        or "天道快照" in raw_text
    ):
        return []

    matched_ids = []
    seen_ids = set()
    for line in raw_text.splitlines():
        if "已领" in line:
            continue
        if "未领" not in line and "⏳" not in line:
            continue
        for identity_id in _iter_tree_enabled_identity_ids():
            if int(identity_id or 0) in seen_ids:
                continue
            if _line_matches_tree_identity(line, identity_id):
                seen_ids.add(int(identity_id))
                matched_ids.append(int(identity_id))
    return matched_ids


def _has_pending_tree_command(*commands):
    command_set = {str(command or "").strip() for command in commands if str(command or "").strip()}
    for pending in state.get("pending_tasks", {}).values():
        if get_pending_command(pending) in command_set:
            return True
    return False


def _clear_pending_tree_status(*, persist=False):
    remove_ids = [
        msg_id for msg_id, pending in state.get("pending_tasks", {}).items()
        if get_pending_command(pending) in {CMD_TREE_STATUS, CMD_TREE_PULSE_STATUS}
    ]
    for msg_id in remove_ids:
        state["pending_tasks"].pop(msg_id, None)
    if remove_ids and persist:
        save_state()
    return bool(remove_ids)


def _has_tree_harvest_inflight(now=None):
    now = float(now if now is not None else time.time())
    if _has_pending_tree_command(CMD_TREE_HARVEST):
        return True
    return float(state.get("tree_harvest_inflight_until", 0) or 0) > now


def _tree_bootstrap_check_is_useful():
    if state["is_maturing"] and state["is_harvested"] and not state["is_invading"] and not state["pending_irrigation"]:
        return False
    return True


def _tree_status_probe_is_urgent():
    return bool(state["is_maturing"] or state["is_invading"] or state["pending_irrigation"])


def _tree_status_probe_is_recent(now=None):
    now = float(now if now is not None else time.time())
    last_sent_at = float(state.get("last_tree_status_sent_at", 0) or 0)
    return last_sent_at > 0 and now - last_sent_at < TREE_STARTUP_STATUS_RECENT_SEC


def _clear_redundant_tree_bootstrap_checks_after_normal_panel(now=None):
    now = float(now if now is not None else time.time())
    changed = False
    for identity_id in _iter_tree_enabled_identity_ids():
        if int(identity_id or 0) == int(get_current_identity_id() or 0):
            continue
        with use_identity(identity_id):
            if not state.get("tree_bootstrap_check_needed") and not state.get("tree_bootstrap_check_due_at"):
                continue
            if state["is_maturing"] or state["is_invading"] or state["pending_irrigation"]:
                continue
            state["tree_bootstrap_check_needed"] = False
            state["tree_bootstrap_check_due_at"] = 0
            state["last_tree_status_sent_at"] = now
            save_state()
            changed = True
    return changed


def _iter_tree_enabled_identity_ids():
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except Exception:
            continue
        if not get_identity_enabled(identity_id):
            continue
        account_id = int(get_identity_account(identity_id) or 0)
        if account_id and is_account_offline(account_id):
            continue
        if not identity_state.get("tree_enabled"):
            continue
        yield int(identity_id)


def _tree_mature_confirmation_identity_id(now):
    candidates = sorted(_iter_tree_enabled_identity_ids())
    if not candidates:
        return 0
    bucket = int(float(now or time.time()) // 3600)
    rng = random.Random(f"tree-mature-confirm:{bucket}")
    return int(rng.choice(candidates))


async def _send_tree_harvest(now=None):
    now = float(now if now is not None else time.time())
    if not state["tree_enabled"] or not state["is_maturing"] or state["is_harvested"]:
        return False
    if _has_tree_harvest_inflight(now):
        return False

    state["tree_harvest_inflight_until"] = now + TREE_HARVEST_INFLIGHT_SEC
    save_state()
    msg = await send_game_command(CMD_TREE_HARVEST, max_retry=TREE_HARVEST_RETRY_LIMIT)
    if not msg:
        state["tree_harvest_inflight_until"] = 0
        save_state()
        await send_audit_log("❌ 采摘发送失败，等待下轮调度。")
        return False
    return True


def _mark_tree_maturing_from_trusted_signal(now, *, reset_harvest=False):
    state["is_maturing"] = True
    if reset_harvest:
        state["is_harvested"] = False
        state["tree_harvest_inflight_until"] = 0
    state["next_irr_time"] = now + FREEZE_CD
    state["pending_irrigation"] = False
    state["tree_bootstrap_check_needed"] = False
    state["tree_bootstrap_check_due_at"] = 0
    state["tree_harvest_followup_due_at"] = 0
    state["tree_maturing_logged"] = True


async def queue_tree_harvest_for_identity_ids(identity_ids, now=None, *, reason="成熟采摘期"):
    now = float(now if now is not None else time.time())
    queued = 0
    skipped = 0
    target_ids = []
    seen_ids = set()

    for identity_id in identity_ids or []:
        identity_id = int(identity_id or 0)
        if identity_id <= 0 or identity_id in seen_ids:
            continue
        seen_ids.add(identity_id)
        with use_identity(identity_id):
            if not state["tree_enabled"] or not get_identity_enabled(identity_id):
                skipped += 1
                continue
            _mark_tree_maturing_from_trusted_signal(now)
            if state["is_harvested"] or _has_tree_harvest_inflight(now):
                skipped += 1
                continue
            state["tree_harvest_inflight_until"] = now + TREE_HARVEST_INFLIGHT_SEC
            save_state()
            target_ids.append(int(identity_id))

    async def harvest_batch(send_as_ids):
        for idx, send_as_id in enumerate(send_as_ids):
            if idx > 0:
                await asyncio.sleep(random.uniform(12.5, 16.0))
            with use_identity(send_as_id):
                if not state["tree_enabled"] or not state["is_maturing"] or state["is_harvested"]:
                    state["tree_harvest_inflight_until"] = 0
                    save_state()
                    continue
                msg = await send_game_command(CMD_TREE_HARVEST, max_retry=TREE_HARVEST_RETRY_LIMIT)
                if not msg:
                    state["tree_harvest_inflight_until"] = 0
                    save_state()

    queued = len(target_ids)
    if target_ids:
        _fire_and_forget(harvest_batch(target_ids))

    if queued > 0:
        await send_audit_log(
            f"🍒 {reason}确认，已为 {queued} 个灵树身份串行排队采摘（跳过 {skipped} 个已采/在途）。",
            scope="global",
            limit=220,
        )
    return queued


async def queue_tree_harvest_for_all_enabled(now=None, *, reason="成熟采摘期"):
    return await queue_tree_harvest_for_identity_ids(
        list(_iter_tree_enabled_identity_ids()),
        now,
        reason=reason,
    )


async def recover_tree_normal_round_for_all_enabled(now=None, *, reason="普通灵树面板"):
    now = float(now if now is not None else time.time())
    changed_ids = []
    target_ids = list(_iter_tree_enabled_identity_ids())
    if not target_ids:
        return 0

    ordered_ids = sorted(target_ids)
    for index, identity_id in enumerate(ordered_ids):
        with use_identity(identity_id):
            should_recover = (
                bool(state.get("is_maturing"))
                or bool(state.get("is_harvested"))
                or bool(state.get("pending_irrigation"))
                or float(state.get("next_irr_time", 0) or 0) > now + 24 * 3600
            )
            if not should_recover:
                continue
            rng = random.Random(f"tree-normal-recover:{int(now // 300)}:{identity_id}")
            spread = rng.uniform(TREE_NORMAL_PANEL_RECOVERY_SPREAD_MIN_SEC, TREE_NORMAL_PANEL_RECOVERY_SPREAD_MAX_SEC)
            state["is_maturing"] = False
            state["is_harvested"] = False
            state["pending_irrigation"] = False
            state["tree_maturing_logged"] = False
            state["tree_harvest_followup_due_at"] = 0
            state["tree_harvest_inflight_until"] = 0
            state["tree_bootstrap_check_needed"] = False
            state["tree_bootstrap_check_due_at"] = 0
            state["next_irr_time"] = now + spread
            save_state()
            changed_ids.append(identity_id)

    if changed_ids:
        await send_audit_log(
            f"🌳 {reason}确认当前不是成熟期，已释放 {len(changed_ids)} 个卡住的灵树状态，定脉错峰 45-75 分钟恢复。",
            scope="global",
            limit=240,
        )
    return len(changed_ids)


def _schedule_tree_bootstrap_check(now=None, *, retry=False, min_sec=None, max_sec=None):
    now = float(now if now is not None else time.time())
    if min_sec is not None or max_sec is not None:
        low = float(min_sec if min_sec is not None else max_sec)
        high = float(max_sec if max_sec is not None else min_sec)
        delay = random.uniform(max(0.0, min(low, high)), max(0.0, max(low, high)))
    elif retry:
        delay = random.uniform(TREE_BOOTSTRAP_CHECK_RETRY_MIN_SEC, TREE_BOOTSTRAP_CHECK_RETRY_MAX_SEC)
    else:
        delay = random.uniform(TREE_BOOTSTRAP_CHECK_DELAY_MIN_SEC, TREE_BOOTSTRAP_CHECK_DELAY_MAX_SEC)
    state["tree_bootstrap_check_needed"] = True
    state["tree_bootstrap_check_due_at"] = now + delay
    return delay


def _schedule_tree_mature_confirmation(now=None):
    return _schedule_tree_bootstrap_check(
        now,
        min_sec=TREE_MATURE_CONFIRM_DELAY_MIN_SEC,
        max_sec=TREE_MATURE_CONFIRM_DELAY_MAX_SEC,
    )


def _schedule_tree_abnormal_confirmation(now=None):
    return _schedule_tree_bootstrap_check(
        now,
        min_sec=TREE_HARVEST_ABNORMAL_CHECK_MIN_SEC,
        max_sec=TREE_HARVEST_ABNORMAL_CHECK_MAX_SEC,
    )


def request_tree_bootstrap_check(now=None, *, min_sec=None, max_sec=None):
    if not state["tree_enabled"]:
        return False

    # Status probes are observational. Never let old tracked probes retry after a restart.
    _clear_pending_tree_status(persist=True)

    if not _tree_bootstrap_check_is_useful():
        changed = bool(state.get("tree_bootstrap_check_needed") or state.get("tree_bootstrap_check_due_at"))
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        if changed:
            save_state()
        return False

    now = float(now if now is not None else time.time())
    if not _tree_status_probe_is_urgent() and _tree_status_probe_is_recent(now):
        changed = bool(state.get("tree_bootstrap_check_needed") or state.get("tree_bootstrap_check_due_at"))
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        if changed:
            save_state()
        return False

    if (
        state["is_maturing"]
        and not state["is_harvested"]
        and not state.get("tree_bootstrap_check_needed")
        and int(get_current_identity_id() or 0) != _tree_mature_confirmation_identity_id(now)
    ):
        state["tree_bootstrap_check_due_at"] = 0
        return False

    due_at = float(state.get("tree_bootstrap_check_due_at", 0) or 0)
    if state.get("tree_bootstrap_check_needed") and due_at > now:
        return False
    _schedule_tree_bootstrap_check(now, min_sec=min_sec, max_sec=max_sec)
    return True


async def _send_tree_status(now=None, *, force=False):
    now = float(now if now is not None else time.time())
    _clear_pending_tree_status(persist=True)
    last_sent_at = float(state.get("last_tree_status_sent_at", 0) or 0)
    if not force and last_sent_at > 0 and now - last_sent_at < TREE_STATUS_DEDUPE_SEC:
        return False

    msg = await send_game_command(
        CMD_TREE_PULSE_STATUS,
        track=False,
        source_module="灵树",
    )
    if not msg:
        return False
    state["last_tree_status_sent_at"] = float(getattr(msg, "sent_at", 0) or time.time())
    return True


def _tree_guard_selection_bucket(now):
    window = max(int(TREE_GUARD_SELECTION_WINDOW_SEC or 0), 3600)
    return int(float(now or time.time()) // window)


def _tree_guard_selected_ids(now):
    candidates = []
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except Exception:
            continue
        if not get_identity_enabled(identity_id):
            continue
        account_id = int(get_identity_account(identity_id) or 0)
        if account_id and is_account_offline(account_id):
            continue
        if not identity_state.get("tree_enabled"):
            continue
        candidates.append(int(identity_id))

    if not candidates:
        return set()

    limit = max(0, min(int(TREE_GUARD_ACTIVE_COUNT or 0), len(candidates)))
    if limit <= 0:
        return set()

    bucket = _tree_guard_selection_bucket(now)
    ordered = sorted(candidates)
    rng = random.Random(f"tree-guard:{bucket}")
    rng.shuffle(ordered)
    return set(ordered[:limit])


def _tree_guard_initial_delay(now, identity_id):
    low = max(0, float(TREE_GUARD_INITIAL_DELAY_MIN_SEC or 0))
    high = max(low, float(TREE_GUARD_INITIAL_DELAY_MAX_SEC or 0))
    bucket = _tree_guard_selection_bucket(now)
    rng = random.Random(f"tree-guard-delay:{bucket}:{int(identity_id or 0)}")
    return rng.uniform(low, high)


def get_tree_status_text():
    lines = ["🌳 灵树"]
    if state['is_invading']:
        lines.extend([
            "- 当前状态：入侵中",
            (
                "- 本轮守山：非主动守山号"
                if state['next_guard_time'] > time.time() + TREE_GUARD_FREEZE_THRESHOLD_SEC
                else f"- 下次守山：{fmt_abs_ts(state['next_guard_time'])}（{fmt_remaining(state['next_guard_time'])}）"
            ),
            f"- 待补偿定脉：{'是' if state['pending_irrigation'] else '否'}",
        ])
    elif state['is_maturing']:
        lines.extend([
            "- 当前状态：成熟采摘期",
            f"- 已采摘：{'是' if state['is_harvested'] else '否'}",
            f"- 待补偿定脉：{'是' if state['pending_irrigation'] else '否'}",
        ])
    else:
        if state.get("tree_pulse_mode_seen") or float(state.get("tree_pulse_last_panel_at", 0) or 0) > 0:
            daily_used = int(state.get("tree_pulse_daily_used", 0) or 0)
            daily_limit = int(state.get("tree_pulse_daily_limit", 0) or 0)
            progress = float(state.get("tree_pulse_progress", 0.0) or 0.0)
            main = str(state.get("tree_pulse_main") or "?")
            aux = str(state.get("tree_pulse_aux") or "?")
            reverse = str(state.get("tree_pulse_reverse") or "?")
            stability = int(state.get("tree_pulse_stability", 0) or 0)
            stability_max = int(state.get("tree_pulse_stability_max", 0) or 0)
            turbidity = int(state.get("tree_pulse_turbidity", 0) or 0)
            turbidity_max = int(state.get("tree_pulse_turbidity_max", 0) or 0)
            lines.extend([
                "- 当前玩法：云梦灵眼定脉",
                f"- 进度：{progress:.2f}%",
                f"- 今日定脉：{daily_used}/{daily_limit or '?'}",
                f"- 脉象：主 {main} / 辅 {aux} / 逆 {reverse}",
                f"- 脉稳/浊息：{stability}/{stability_max or '?'} | {turbidity}/{turbidity_max or '?'}",
                f"- 下次定脉：{fmt_abs_ts(state['next_irr_time'])}（{fmt_remaining(state['next_irr_time'])}）",
            ])
            last_error = str(state.get("tree_pulse_last_error") or "").strip()
            if last_error:
                lines.append(f"- 最近状态：{last_error}")
        else:
            lines.extend([
                "- 当前状态：等待定脉面板",
                f"- 下次校准：{fmt_abs_ts(state['next_irr_time'])}（{fmt_remaining(state['next_irr_time'])}）",
            ])
        if state['tree_bootstrap_check_needed']:
            due_at = float(state.get("tree_bootstrap_check_due_at", 0) or 0)
            if due_at > time.time():
                lines.append(f"- 启动校验：{fmt_abs_ts(due_at)}（{fmt_remaining(due_at)}）")
            else:
                lines.append("- 启动校验待执行：是")
        return "\n".join(lines)

    return "\n".join(lines)


async def handle_tree_invasion_start(text, now):
    if not state["tree_enabled"]:
        return

    if "古剑门来袭" in text or "古剑门入侵中" in text:
        if not state["is_invading"]:
            current_identity_id = get_current_identity_id()
            selected_ids = _tree_guard_selected_ids(now)
            is_selected = int(current_identity_id or 0) in selected_ids
            state["is_invading"] = True
            if is_selected:
                delay = _tree_guard_initial_delay(now, current_identity_id)
                state["next_guard_time"] = now + delay
                audit_text = f"🚨 检测到入侵，本轮主动守山，{delay:.0f}s 后执行。"
            else:
                state["next_guard_time"] = now + FREEZE_CD
                audit_text = "🚨 检测到入侵，本轮不主动守山，仅暂停定脉。"
            save_state()
            await send_audit_log(audit_text)


async def handle_tree_invasion_end(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return

    is_guard_success_broadcast = "【守护成功！】" in text or "攻势已被成功击退" in text
    is_no_invasion = "当前并无外敌入侵，无需加固大阵。" in text or (is_reply_to_me and "无需加固大阵" in text)
    if is_guard_success_broadcast or is_no_invasion:
        if state["is_invading"]:
            state["is_invading"] = False
            state["next_guard_time"] = now + FREEZE_CD
            if state["pending_irrigation"]:
                state["pending_irrigation"] = False
                state["next_irr_time"] = now
                save_state()
                await send_audit_log("🛡️ 入侵结束，立即恢复定脉。")
            else:
                save_state()
                await send_audit_log("🛡️ 入侵结束，恢复常规监控。")


async def handle_tree_rebirth_reset(text, now):
    if not state["tree_enabled"]:
        return

    if "灵眼之树" in text and ("新的轮回开始" in text or "贡献度已重置" in text):
        state["is_harvested"] = False
        state["tree_harvest_inflight_until"] = 0
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        if state["is_maturing"]:
            state["is_maturing"] = False
            delay = random.uniform(10, 20)
            state["next_irr_time"] = now + delay
            save_state()
            target_time = fmt_time_after(delay)
            await send_audit_log(f"🌳 轮回重置，{delay:.1f}s 后恢复定脉（{target_time}）。")
        else:
            save_state()


async def handle_tree_cd_fix(text, now, reply_to, matched_family=None):
    if not state["tree_enabled"]:
        return False

    orig_cmd = reply_to.raw_text if reply_to else ""
    is_irrigation_reply = matched_family == "tree_panel" or CMD_TREE_WATER in orig_cmd or CMD_TREE_PULSE_STATUS in orig_cmd
    is_pulse_reply = matched_family == "tree_pulse" or str(orig_cmd or "").strip().startswith(CMD_TREE_PULSE)
    is_guard_reply = (
        matched_family == "tree_guard"
        or CMD_TREE_GUARD in orig_cmd
        or "守山" in str(text or "")
        or "协同" in str(text or "")
        or "大阵注入灵力" in str(text or "")
    )

    if _is_tree_legacy_disabled_prompt(text) and is_irrigation_reply:
        state["tree_pulse_mode_seen"] = True
        state["pending_irrigation"] = False
        state["tree_pulse_last_error"] = "旧灌溉已关闭，切换定脉"
        _schedule_tree_pulse_status_spread(now)
        save_state()
        return True

    if _is_tree_pulse_blocked_prompt(text) and is_pulse_reply:
        state["is_maturing"] = True
        state["pending_irrigation"] = False
        state["next_irr_time"] = now + FREEZE_CD
        state["tree_pulse_last_error"] = "灵树已成熟或遭劫难，定脉停止"
        delay = _schedule_tree_abnormal_confirmation(now)
        save_state()
        await send_audit_log(f"🌳 定脉被拒：灵树已成熟或遭劫难，{delay:.0f}s 后复查面板。")
        return True

    if is_pulse_reply and _is_tree_pulse_action_success(text):
        reset_resource_shortage(TREE_PULSE_RESOURCE_KEY)
        used = int(state.get("tree_pulse_daily_used", 0) or 0)
        limit = int(state.get("tree_pulse_daily_limit", 0) or 0)
        if limit <= 0 or used < limit:
            state["tree_pulse_daily_used"] = used + 1
        next_used = int(state.get("tree_pulse_daily_used", 0) or 0)
        if limit > 0 and next_used >= limit:
            delay = _tree_pulse_next_day_delay(now)
        else:
            delay = random.uniform(TREE_PULSE_ACTION_FOLLOWUP_MIN_SEC, TREE_PULSE_ACTION_FOLLOWUP_MAX_SEC)
        state["next_irr_time"] = now + delay
        state["tree_pulse_last_error"] = "定脉回执已确认"
        save_state()
        return True

    if _is_tree_irrigation_success(text) and is_irrigation_reply:
        delay = _next_irrigation_delay()
        state["next_irr_time"] = float(now) + delay
        if reset_resource_shortage(TREE_IRRIGATION_RESOURCE_KEY):
            save_state()
        else:
            save_state()
        await send_audit_log(f"🚀 灌溉已确认→{fmt_time_after(delay)}")
        return True

    if _is_tree_guard_success(text) and is_guard_reply:
        if reset_resource_shortage(TREE_GUARD_RESOURCE_KEY):
            save_state()
        return True

    if "经脉尚需调息" in str(text or "") and is_guard_reply:
        if reset_resource_shortage(TREE_GUARD_RESOURCE_KEY):
            save_state()
        return True

    if _is_tree_irrigation_insufficient_power(text) and is_irrigation_reply:
        backoff = record_resource_shortage(TREE_IRRIGATION_RESOURCE_KEY, now, reason=text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["next_irr_time"] = due_at
        save_state()
        await send_audit_log(
            f"⚠️ 灌溉修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    if _is_tree_guard_insufficient_power(text) and is_guard_reply:
        backoff = record_resource_shortage(TREE_GUARD_RESOURCE_KEY, now, reason=text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["next_guard_time"] = due_at
        save_state()
        await send_audit_log(
            f"⚠️ 守山修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    if is_pulse_reply and "修为不足" in str(text or ""):
        backoff = record_resource_shortage(TREE_PULSE_RESOURCE_KEY, now, reason=text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["next_irr_time"] = due_at
        state["tree_pulse_last_error"] = "定脉修为不足"
        save_state()
        await send_audit_log(
            f"⚠️ 定脉修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    if not any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息", "调息"]):
        if is_pulse_reply:
            delay = _schedule_tree_pulse_blocked_check(now)
            state["tree_pulse_last_error"] = "定脉未知回执，已阻塞"
            save_state()
            await send_audit_log(f"⚠️ 灵树定脉未知回执，已停止动作并 {delay / 60:.0f} 分钟后复查。")
            return True
        return False

    wait_sec = parse_wait_time(text)
    if not has_wait_time(text):
        return False

    target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)

    if matched_family == "tree_guard" or "守山" in text or "协同" in text or CMD_TREE_GUARD in orig_cmd:
        reset_resource_shortage(TREE_GUARD_RESOURCE_KEY)
        state["next_guard_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 守山 CD→{target_time}")
        return True
    if (matched_family == "tree_panel" and CMD_TREE_PULSE_STATUS not in orig_cmd) or "灌溉" in text or CMD_TREE_WATER in orig_cmd:
        reset_resource_shortage(TREE_IRRIGATION_RESOURCE_KEY)
        state["next_irr_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 灌溉 CD→{target_time}")
        return True
    if is_pulse_reply or "定脉" in text:
        reset_resource_shortage(TREE_PULSE_RESOURCE_KEY)
        state["next_irr_time"] = now + wait_sec + CD_BUFFER_SEC
        state["tree_pulse_last_error"] = f"定脉 CD→{target_time}"
        save_state()
        await send_audit_log(f"⏳ 定脉 CD→{target_time}")
        return True
    return False


async def handle_tree_exception_prompt(text, now=None):
    if not state["tree_enabled"]:
        return False

    if "已然成熟或正遭劫难" not in text and not _is_tree_pulse_blocked_prompt(text):
        return False

    state["is_maturing"] = True
    state["pending_irrigation"] = False
    state["next_irr_time"] = float(now if now is not None else time.time()) + FREEZE_CD
    state["tree_pulse_last_error"] = "灵树已成熟或遭劫难，等待复查"
    delay = _schedule_tree_abnormal_confirmation(now)
    save_state()
    console_log(f"🔍 灵树异常，{delay}s 后查状态。")
    return True


async def handle_tree_panel(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return False

    pulse_panel = parse_tree_pulse_panel(text)
    if pulse_panel:
        if not is_reply_to_me:
            return False

        was_maturing = bool(state.get("is_maturing"))
        _apply_tree_pulse_panel(pulse_panel, now)
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        state["last_tree_status_sent_at"] = now

        progress = float(pulse_panel.get("progress", 0.0) or 0.0)
        daily_used = int(pulse_panel.get("daily_used", 0) or 0)
        daily_limit = int(pulse_panel.get("daily_limit", 0) or 0)
        command, reason = _choose_tree_pulse_command(pulse_panel)

        if progress >= 99.9 or pulse_panel.get("blocked"):
            state["is_maturing"] = True
            state["pending_irrigation"] = False
            state["next_irr_time"] = now + FREEZE_CD
            state["tree_pulse_last_error"] = "灵树已成熟或遭劫难，停止定脉"
            if not state.get("tree_bootstrap_check_needed") and float(state.get("tree_harvest_followup_due_at", 0) or 0) <= now:
                next_followup_at = now + TREE_HARVEST_FOLLOWUP_DELAY_SEC
                state["tree_harvest_followup_due_at"] = next_followup_at
                _schedule_tree_bootstrap_check(
                    now,
                    min_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
                    max_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
                )
            save_state()
            if not was_maturing:
                await send_audit_log("🌳 定脉进度已满，停止定脉，等待采摘/劫难后续文案。")
            return True

        if state.get("is_maturing"):
            state["is_maturing"] = False
            state["is_harvested"] = False
            state["tree_maturing_logged"] = False
            state["tree_harvest_followup_due_at"] = 0
            state["tree_harvest_inflight_until"] = 0

        if daily_limit > 0 and daily_used >= daily_limit:
            delay = _tree_pulse_next_day_delay(now)
            state["next_irr_time"] = now + delay
            state["tree_pulse_last_error"] = "今日定脉令已满"
            save_state()
            return True

        if command:
            delay = random.uniform(TREE_PULSE_ACTION_DELAY_MIN_SEC, TREE_PULSE_ACTION_DELAY_MAX_SEC)
            state["next_irr_time"] = now + delay
            state["tree_pulse_last_action"] = command
            state["tree_pulse_last_error"] = reason
            state["tree_pulse_blocked_until"] = 0
            save_state()
            return True

        state["tree_pulse_last_error"] = reason
        _schedule_tree_pulse_blocked_check(now)
        save_state()
        await send_audit_log(f"⚠️ 灵树定脉暂不动作：{reason}，稍后复查。")
        return True

    is_tree_panel = "【落云宗 · 灵眼之树】" in text or "落云宗·灵眼之树" in text
    is_maturing_broadcast = "🍎 灵果已完全成熟！ 采摘期开启！" in text and "📊 天道榜单已定格！" in text
    if not is_tree_panel and not is_maturing_broadcast:
        return False

    current_status_snapshot = "你的当前状态:" in text or "你的当前状态：" in text
    personal_panel_owned = is_reply_to_me or _tree_panel_matches_current_identity(text)
    confirmed_final_board = (
        "本轮最终贡献榜" in text
        or "本轮最终分枝榜" in text
        or "天道榜单已定格" in text
        or "天道快照" in text
    )
    final_board_unclaimed_ids = _tree_final_board_unclaimed_identity_ids(text) if confirmed_final_board else []
    if is_tree_panel and current_status_snapshot and not personal_panel_owned:
        if confirmed_final_board and final_board_unclaimed_ids:
            await queue_tree_harvest_for_identity_ids(
                final_board_unclaimed_ids,
                now,
                reason="灵树最终榜",
            )
            return True
        return False
    if is_tree_panel and current_status_snapshot and "成熟采摘期" not in text:
        await recover_tree_normal_round_for_all_enabled(now, reason="灵树状态")
    clear_status_pending = is_tree_panel and current_status_snapshot and not is_reply_to_me and personal_panel_owned

    if is_maturing_broadcast and not is_tree_panel:
        remove_ids = [
            msg_id for msg_id, pending in state["pending_tasks"].items()
            if get_pending_command(pending) in {CMD_TREE_WATER, CMD_TREE_STATUS, CMD_TREE_PULSE_STATUS}
        ]
        for msg_id in remove_ids:
            state["pending_tasks"].pop(msg_id, None)

        _mark_tree_maturing_from_trusted_signal(now, reset_harvest=True)
        confirm_identity_id = _tree_mature_confirmation_identity_id(now)
        if int(get_current_identity_id() or 0) == confirm_identity_id:
            delay = _schedule_tree_mature_confirmation(now)
            save_state()
            await send_audit_log(
                f"🌳 收到成熟广播，{delay:.0f}s 后先查 .灵树定脉确认，确认成熟后再全员采摘。",
                scope="global",
                limit=220,
            )
        else:
            save_state()
        return True

    state["tree_bootstrap_check_needed"] = False
    state["tree_bootstrap_check_due_at"] = 0
    if is_tree_panel and current_status_snapshot and personal_panel_owned:
        state["last_tree_status_sent_at"] = now
    if clear_status_pending:
        _clear_pending_tree_status()

    if "成熟采摘期" in text or is_maturing_broadcast:
        was_maturing = state["is_maturing"]
        state["is_maturing"] = True
        has_pending_status = any(get_pending_command(p) in {CMD_TREE_STATUS, CMD_TREE_PULSE_STATUS} for p in state["pending_tasks"].values())

        remaining_match = RE_TREE_REMAINING.search(text)
        remain_sec = parse_wait_time(remaining_match.group(1)) if remaining_match else 0
        if remain_sec > 0:
            state["next_irr_time"] = now + FREEZE_CD

        no_contribution_snapshot = "(暂无弟子贡献)" in text
        if no_contribution_snapshot and remain_sec > TREE_HARVEST_FOLLOWUP_DELAY_SEC:
            next_followup_at = now + TREE_HARVEST_FOLLOWUP_DELAY_SEC
            previous_followup_at = float(state.get("tree_harvest_followup_due_at", 0) or 0)
            state["tree_harvest_followup_due_at"] = next_followup_at
            should_schedule_followup = (not has_pending_status) and previous_followup_at <= now
            if should_schedule_followup:
                _schedule_tree_bootstrap_check(
                    now,
                    min_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
                    max_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
                )
            save_state()

            if previous_followup_at <= now:
                state["tree_maturing_logged"] = True
                await send_audit_log(f"🌳 榜单未定格，30 分钟后复查（{fmt_abs_ts(next_followup_at)}）。")
                save_state()
            return True

        state["tree_harvest_followup_due_at"] = 0
        already_harvested_snapshot = "你的当前状态: 已采摘" in text or "你的当前状态：已采摘" in text
        if current_status_snapshot:
            if already_harvested_snapshot:
                if not state["is_harvested"]:
                    state["is_harvested"] = True
                state["tree_harvest_inflight_until"] = 0
                save_state()
            elif state["is_harvested"]:
                state["is_harvested"] = False
                save_state()
                console_log("🌳 成熟期快照显示未采摘，已清除本地采摘标记。")

        if personal_panel_owned and confirmed_final_board:
            save_state()
            await queue_tree_harvest_for_all_enabled(now, reason="灵树成熟")
        elif personal_panel_owned and not has_pending_status and not state.get("tree_bootstrap_check_needed"):
            next_followup_at = now + TREE_HARVEST_FOLLOWUP_DELAY_SEC
            state["tree_harvest_followup_due_at"] = next_followup_at
            delay = _schedule_tree_bootstrap_check(
                now,
                min_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
                max_sec=TREE_HARVEST_FOLLOWUP_DELAY_SEC,
            )
            save_state()
            await send_audit_log(f"🌳 成熟期未见最终榜，{delay / 60:.0f} 分钟后复查状态。")

        if remain_sec > 0:
            end_t = fmt_time_after(remain_sec)
            if not was_maturing or not state.get("tree_maturing_logged", False):
                state["tree_maturing_logged"] = True
                await send_audit_log(f"🌳 成熟期至 {end_t}，等待采摘结果或重置广播。")
            save_state()
        return True
    if is_tree_panel:
        state_changed = False
        if current_status_snapshot and personal_panel_owned and not state["is_maturing"] and not state["is_invading"]:
            if _clear_redundant_tree_bootstrap_checks_after_normal_panel(now):
                console_log("🌳 已用本次灵树状态清理其他账号启动校验。", scope="global")
        if state["is_maturing"]:
            state["is_maturing"] = False
            state["tree_harvest_followup_due_at"] = 0
            # 退出成熟期时，next_irr_time 之前被推到 FREEZE_CD，必须重置
            # 否则会永久卡在 115 天后
            if state["next_irr_time"] > now + 24 * 3600:
                state["next_irr_time"] = now
            state_changed = True
            if state.get("tree_maturing_logged", False):
                state["tree_maturing_logged"] = False
                await send_audit_log("🌳 成熟期结束，恢复定脉。")
        if state["is_harvested"]:
            state["is_harvested"] = False
            state["tree_harvest_inflight_until"] = 0
            state_changed = True
            console_log('🌳 已清除本地采摘标记。')
        if state["pending_irrigation"] and not state["is_invading"]:
            state["pending_irrigation"] = False
            state["next_irr_time"] = now
            state_changed = True
            console_log("🌳 已释放补偿定脉，恢复调度。")
        if state_changed:
            save_state()
        return True
    return False


async def handle_tree_harvest_reply(text, now, reply_to, matched_family=None, current_msg_id=0):
    if not state["tree_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "tree_harvest" and CMD_TREE_HARVEST not in orig_cmd:
        return False

    raw_text = str(text or "")
    if "你来到灵眼之树下" in raw_text and "核对天道榜单" in raw_text:
        return False

    is_success = "【灵果入腹" in raw_text or "你摘下一枚" in raw_text
    is_already_done = "已经采摘过灵果" in raw_text or "不可贪得无厌" in raw_text
    if is_success or is_already_done:
        reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
        if _is_duplicate_tree_harvest_result(current_msg_id, reply_to_msg_id):
            return True
        parsed = _parse_tree_harvest_result(raw_text) if is_success else {}
        items = parsed.get("items") or {}
        storage_changed = apply_storage_bag_item_deltas(get_current_identity_id(), items) if items else False
        state["is_harvested"] = True
        state["tree_harvest_inflight_until"] = 0
        state["tree_last_harvest_result_msg_id"] = int(current_msg_id or 0)
        state["tree_last_harvest_reply_to_msg_id"] = reply_to_msg_id
        save_state()
        await send_audit_log(_format_tree_harvest_audit(parsed, storage_changed))
        return True

    if "尚未成熟" in raw_text or "无法采摘" in raw_text or "不能采摘" in raw_text:
        state["tree_harvest_inflight_until"] = 0
        delay = _schedule_tree_abnormal_confirmation(now)
        save_state()
        await send_audit_log(f"⚠️ 采摘未完成，{delay:.0f}s 后查 .灵树定脉确认阶段。")
        return True

    return False


async def run_tree_bootstrap_check(now):
    if not state["tree_enabled"]:
        return
    if not state["tree_bootstrap_check_needed"]:
        return
    if not _tree_bootstrap_check_is_useful():
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        save_state()
        return

    due_at = float(state.get("tree_bootstrap_check_due_at", 0) or 0)
    if due_at <= 0:
        delay = _schedule_tree_bootstrap_check(now)
        save_state()
        console_log(f"🌳 启动校验已错峰，{delay / 60:.1f} 分钟后查询灵树定脉。")
        return
    if now < due_at:
        return
    current_identity_id = int(get_current_identity_id() or 0)
    selected_identity_id = _tree_mature_confirmation_identity_id(now)
    if not state["is_maturing"] and selected_identity_id and current_identity_id != selected_identity_id:
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        save_state()
        return

    urgent_probe = _tree_status_probe_is_urgent()
    if not urgent_probe and _tree_status_probe_is_recent(now):
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        save_state()
        return

    console_log("🌳 启动校验：查询灵树定脉（无补发）。")
    sent = await _send_tree_status(now, force=urgent_probe)
    if sent:
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
    else:
        delay = _schedule_tree_bootstrap_check(now, retry=True)
        console_log(f"🌳 启动校验暂未发送，{delay / 60:.1f} 分钟后重试。")
    save_state()


async def run_tree_scheduler(now):
    if not state["tree_enabled"]:
        return

    has_pending_tree_action = _has_pending_tree_command(
        CMD_TREE_WATER,
        CMD_TREE_GUARD,
        CMD_TREE_STATUS,
        CMD_TREE_PULSE_STATUS,
        CMD_TREE_PULSE,
        CMD_TREE_HARVEST,
    )

    if not state["is_maturing"] and now >= state["next_irr_time"]:
        if has_pending_tree_action:
            return
        if state["is_invading"]:
            if not state["pending_irrigation"]:
                state["pending_irrigation"] = True
                state["next_irr_time"] = now + FREEZE_CD
                save_state()
                await send_audit_log("⏳ 入侵中，灵树定脉已暂停。")
        else:
            if not _tree_pulse_panel_is_recent(now):
                msg = await send_game_command(
                    CMD_TREE_PULSE_STATUS,
                    track=False,
                    max_retry=0,
                    source_module="灵树",
                )
                sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
                if not msg:
                    state["next_irr_time"] = sent_at + RETRY_MAX_SEC
                    save_state()
                else:
                    state["last_tree_status_sent_at"] = sent_at
                    _schedule_tree_pulse_status_spread(sent_at)
                    save_state()
            else:
                snapshot = _tree_pulse_snapshot_from_state()
                command, reason = _choose_tree_pulse_command(snapshot)
                if not command:
                    if reason == "今日定脉令已满":
                        delay = _tree_pulse_next_day_delay(now)
                        state["next_irr_time"] = now + delay
                    elif reason == "灵树已成熟或遭劫难":
                        state["is_maturing"] = True
                        state["next_irr_time"] = now + FREEZE_CD
                    else:
                        _schedule_tree_pulse_blocked_check(now)
                    state["tree_pulse_last_error"] = reason
                    save_state()
                else:
                    msg = await send_game_command(
                        command,
                        max_retry=0,
                        reply_timeout=TREE_PULSE_REPLY_TIMEOUT_SEC,
                        source_module="灵树",
                    )
                    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
                    if not msg:
                        state["next_irr_time"] = sent_at + RETRY_MAX_SEC
                        save_state()
                    else:
                        state["tree_pulse_last_action"] = command
                        state["tree_pulse_last_error"] = reason
                        state["next_irr_time"] = sent_at + 10 * 60
                        save_state()

    if state["is_invading"] and now >= state["next_guard_time"]:
        if has_pending_tree_action:
            return
        g_delay = random.uniform(GUARD_INTERVAL_MIN, GUARD_INTERVAL_MAX)
        msg = await send_game_command(CMD_TREE_GUARD, max_retry=0)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            state["next_guard_time"] = sent_at + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 守山发送失败，稍后重试。")
        else:
            state["next_guard_time"] = sent_at + g_delay
            save_state()
            g_next_t = fmt_time_after(g_delay)
            await send_audit_log(f"🛡️ 守山→{g_next_t}")


__all__ = [
    "get_tree_status_text",
    "handle_tree_cd_fix",
    "handle_tree_exception_prompt",
    "handle_tree_invasion_end",
    "handle_tree_invasion_start",
    "handle_tree_harvest_reply",
    "handle_tree_panel",
    "handle_tree_rebirth_reset",
    "request_tree_bootstrap_check",
    "run_tree_bootstrap_check",
    "run_tree_scheduler",
]
