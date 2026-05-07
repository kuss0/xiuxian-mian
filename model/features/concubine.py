import asyncio
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_TIANJI,
    CONCUBINE_CHAIN_DELAY_MAX_SEC,
    CONCUBINE_CHAIN_DELAY_MIN_SEC,
    CONCUBINE_DREAM_CD_SEC,
    CONCUBINE_NO_PARTNER_RETRY_SEC,
    CONCUBINE_PHASE_TIMEOUT_SEC,
    CONCUBINE_REACQUIRE_RETRY_SEC,
    CONCUBINE_STATUS_RECHECK_MAX_SEC,
    CONCUBINE_STATUS_RECHECK_MIN_SEC,
    CONCUBINE_STATUS_STALE_SEC,
    CONCUBINE_TIANJI_CD_SEC,
    RE_WHITESPACE,
)
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_send_as_profile, get_send_as_tags, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage


CONCUBINE_PENDING_COMMANDS = {
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_TIANJI,
}

_CONCUBINE_SCHEDULER_LOCK = asyncio.Lock()
CONCUBINE_MAIN_PENDING_COMMANDS = CONCUBINE_PENDING_COMMANDS - {CMD_CONCUBINE_TIANJI}
CONCUBINE_REACQUIRE_COMMANDS = {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE}
IDENTITY_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"

RE_CONCUBINE_HEAD = re.compile(r"你的(?P<kind>道心侍妾|红尘道侣)[：:]\s*【(?P<name>[^】]+)】\s*[(（]状态[：:]\s*(?P<location>[^)）\n]+)[)）]")
RE_CONCUBINE_AFFINITY = re.compile(r"情缘值[：:]\s*(\d+)")
RE_CONCUBINE_OATH = re.compile(r"当前誓约[：:]\s*([^\s(（\n]+)")
RE_DREAM_COOLDOWN = re.compile(r"入梦寻图冷却[：:]\s*([^\n]+)")
RE_TIANJI_COOLDOWN = re.compile(r"天机代卜冷却[：:]\s*([^\n]+)")
RE_TIANJI_CHAIN = re.compile(r"天机代卜链[：:]\s*([^\n]+)")
RE_TIANJI_CHAIN_REMAINING = re.compile(r"(?P<name>[^（(]+)[（(]\s*剩余\s*(?P<wait>[^）)]+)\s*[）)]")
RE_TIANJI_GUA = re.compile(r"得卦【(?P<name>[^】]+)】")
RE_TIANJI_XIUWEI_SHORTAGE = re.compile(r"修为不足[，,]\s*代卜天机需消耗\s*\d+\s*点?修为")
RE_DREAM_PARTNER = re.compile(r"你与侍妾【(?P<name>[^】]+)】")
RE_FRAGMENT_PROGRESS = re.compile(r"(?:虚天残图拼片|拼片进度|当前进度)\s*[：:]?\s*(\d+)\s*/\s*(\d+)")
RE_DREAM_BROADCAST_PROGRESS = re.compile(r"残图进度已至\s*(\d+)\s*/\s*(\d+)")
RE_PUZZLE_MISSING = re.compile(r"(?:仍缺|缺失残纹)[：:]\s*([^\n。]+)")
RE_NEW_SECT_PARTNER = re.compile(r"新的道心侍妾\s*【(?P<name>[^】]+)】\s*已被指派")
RE_NEW_ROMANCE_PARTNER = re.compile(r"名为\s*【(?P<name>[^】]+)】\s*的女子[\s\S]*成为你的侍妾")
RE_LOST_PARTNER_NAME = re.compile(r"侍妾【(?P<name>[^】]+)】(?:掳走|与南陇侯交换)")
RE_IDENTITY_TAG = re.compile(rf"@({IDENTITY_TAG_PATTERN})")

CONCUBINE_DREAM_RESOURCE_KEY = "concubine_dream"
CONCUBINE_TIANJI_RESOURCE_KEY = "concubine_tianji"


def _phase():
    return state.get("concubine_phase", "idle")


def _set_phase(new_phase):
    state["concubine_phase"] = str(new_phase or "idle")


def _set_availability(value):
    state["concubine_availability"] = str(value or "unknown")


def _clear_pending_msg_ids():
    state["concubine_status_msg_id"] = 0
    state["concubine_dream_msg_id"] = 0
    state["concubine_fragment_msg_id"] = 0
    state["concubine_puzzle_msg_id"] = 0
    state["concubine_reacquire_msg_id"] = 0
    state["concubine_tianji_msg_id"] = 0


def _schedule_after(now, min_sec, max_sec):
    state["next_concubine_time"] = float(now + random.uniform(min_sec, max_sec))
    return state["next_concubine_time"]


def _schedule_status_recheck(now):
    return _schedule_after(now, CONCUBINE_STATUS_RECHECK_MIN_SEC, CONCUBINE_STATUS_RECHECK_MAX_SEC)


def _schedule_chain_action(now):
    return _schedule_after(now, CONCUBINE_CHAIN_DELAY_MIN_SEC, CONCUBINE_CHAIN_DELAY_MAX_SEC)


def _schedule_at_due_or_chain(now, due_at):
    due_at = float(due_at or 0)
    if due_at <= now:
        return _schedule_chain_action(now)
    state["next_concubine_time"] = due_at + random.uniform(60, 600)
    return state["next_concubine_time"]


def _schedule_after_tianji(now):
    due_times = []
    tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
    if tianji_due_at > now:
        due_times.append(tianji_due_at)
    dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
    if state.get("concubine_enabled") and dream_due_at > now:
        due_times.append(dream_due_at)
    if not due_times:
        state["next_concubine_time"] = float(now + random.uniform(60, 600))
        return state["next_concubine_time"]
    state["next_concubine_time"] = min(due_times) + random.uniform(60, 600)
    return state["next_concubine_time"]


def _backoff_after_pending_timeout(now, phase):
    """Pending 超时后必须压住对应 due，避免下一轮因旧 due_at 立即重发。"""
    retry_at = _schedule_status_recheck(now)
    if phase == "status_pending":
        if state.get("concubine_enabled") and float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = retry_at
        if state.get("concubine_tianji_enabled") and float(state.get("concubine_tianji_due_at", 0) or 0) <= now:
            state["concubine_tianji_due_at"] = retry_at
    elif phase == "dream_pending":
        state["concubine_dream_due_at"] = retry_at
    elif phase == "tianji_pending":
        state["concubine_tianji_due_at"] = retry_at
    elif phase in {"fragment_pending", "puzzle_pending"}:
        total = max(1, int(state.get("concubine_fragment_total", 4) or 4))
        count = int(state.get("concubine_fragment_count", 0) or 0)
        if count >= total:
            state["concubine_fragment_count"] = max(0, total - 1)
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = retry_at
    elif phase == "reacquire_pending":
        state["concubine_reacquire_blocked_until"] = retry_at
    return retry_at


async def _apply_concubine_resource_backoff(now, action_key, due_key, error_key, label, raw_text):
    backoff = record_resource_shortage(action_key, now, reason=raw_text)
    due_at = float(backoff.get("next_at", 0) or 0)
    state[due_key] = due_at
    state[error_key] = f"{label}资源不足: {str(raw_text or '')[:80]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    if action_key == CONCUBINE_TIANJI_RESOURCE_KEY:
        _schedule_after_tianji(now)
    else:
        _schedule_at_due_or_chain(now, due_at)
    await send_audit_log(
        f"⚠️ {label}资源不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}",
        scope="identity",
        limit=220,
    )


def _is_current_reply(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if expected_msg_id <= 0 or reply_to_msg_id <= 0:
        return True
    return reply_to_msg_id == expected_msg_id


def _normalize_identity_text(text):
    return RE_WHITESPACE.sub("", str(text or "").strip().lstrip("@")).casefold()


def _text_matches_current_identity(text):
    raw_text = str(text or "")
    compact_text = _normalize_identity_text(raw_text)
    mentioned_tags = {
        _normalize_identity_text(tag)
        for tag in RE_IDENTITY_TAG.findall(raw_text)
        if str(tag or "").strip()
    }
    for raw_tag in get_send_as_tags():
        tag = str(raw_tag or "").strip().lstrip("@")
        if not tag:
            continue
        normalized_tag = _normalize_identity_text(tag)
        if mentioned_tags:
            if normalized_tag in mentioned_tags:
                return True
            continue
        if len(normalized_tag) >= 3 and normalized_tag in compact_text:
            return True
    return False


def _parse_wait_due_at(raw_text, now):
    text = str(raw_text or "").strip()
    if not text or "可施展" in text or "可用" in text:
        return 0.0
    if has_wait_time(text):
        return float(now + parse_wait_time(text) + CD_BUFFER_SEC)
    return 0.0


def _parse_tianji_chain(raw_text, now):
    text = str(raw_text or "").strip()
    if not text or text == "无":
        return "", 0.0
    matched = RE_TIANJI_CHAIN_REMAINING.search(text)
    if not matched:
        return text, 0.0
    name = matched.group("name").strip()
    wait_text = matched.group("wait").strip()
    due_at = float(now + parse_wait_time(wait_text) + CD_BUFFER_SEC) if has_wait_time(wait_text) else 0.0
    return name, due_at


def _parse_fragment_progress(text):
    raw_text = text or ""
    matched = RE_FRAGMENT_PROGRESS.search(raw_text) or RE_DREAM_BROADCAST_PROGRESS.search(raw_text)
    if not matched:
        return None
    try:
        count = max(0, int(matched.group(1)))
        total = max(1, int(matched.group(2)))
    except (TypeError, ValueError):
        return None
    return count, total


def _apply_dream_partner_hint(text):
    matched = RE_DREAM_PARTNER.search(str(text or ""))
    if not matched:
        return
    name = matched.group("name").strip()
    if name:
        state["concubine_name"] = name
        _set_availability("available")


def _is_no_partner_text(text):
    raw_text = str(text or "")
    return (
        "尚无红颜知己" in raw_text
        or "尚未被指派道心侍妾" in raw_text
        or "尚无侍妾" in raw_text
        or "没有侍妾" in raw_text
        or ("无侍妾" in raw_text and "无法共梦寻图" in raw_text)
    )


def _is_partner_not_eligible_text(text):
    raw_text = str(text or "")
    return "尚未筑基" in raw_text or "唯有筑基" in raw_text or "根基不稳" in raw_text


def _is_partner_manual_repair_text(text):
    raw_text = str(text or "")
    return "数据已损坏" in raw_text or "联系管理员" in raw_text or ".admin 补赐侍妾" in raw_text


def _is_tianji_resource_shortage_text(text):
    raw_text = str(text or "")
    return bool(RE_TIANJI_XIUWEI_SHORTAGE.search(raw_text)) or (
        "修为不足" in raw_text
        and "代卜天机" in raw_text
        and "消耗" in raw_text
    )


def _is_dream_cooldown_text(text):
    raw_text = str(text or "")
    return (
        "梦图感应尚未重启" in raw_text
        or "神念尚在恢复" in raw_text
        or "无法再次强行入梦" in raw_text
    )


def _parse_status_panel(text, now):
    raw_text = text or ""
    matched = RE_CONCUBINE_HEAD.search(raw_text)
    if not matched:
        if _is_no_partner_text(raw_text):
            return {
                "has_partner": False,
                "not_eligible": _is_partner_not_eligible_text(raw_text),
                "manual_repair": _is_partner_manual_repair_text(raw_text),
            }
        return None

    affinity_match = RE_CONCUBINE_AFFINITY.search(raw_text)
    oath_match = RE_CONCUBINE_OATH.search(raw_text)
    dream_match = RE_DREAM_COOLDOWN.search(raw_text)
    tianji_match = RE_TIANJI_COOLDOWN.search(raw_text)
    tianji_chain_match = RE_TIANJI_CHAIN.search(raw_text)
    tianji_chain, tianji_chain_due_at = _parse_tianji_chain(tianji_chain_match.group(1), now) if tianji_chain_match else ("", 0.0)
    progress = _parse_fragment_progress(raw_text)

    return {
        "has_partner": True,
        "kind": matched.group("kind").strip(),
        "name": matched.group("name").strip(),
        "location": matched.group("location").strip(),
        "affinity": int(affinity_match.group(1)) if affinity_match else 0,
        "oath": oath_match.group(1).strip() if oath_match else "",
        "dream_due_at": _parse_wait_due_at(dream_match.group(1), now) if dream_match else 0.0,
        "tianji_due_at": _parse_wait_due_at(tianji_match.group(1), now) if tianji_match else 0.0,
        "tianji_chain": tianji_chain,
        "tianji_chain_due_at": tianji_chain_due_at,
        "fragment_count": progress[0] if progress else int(state.get("concubine_fragment_count", 0) or 0),
        "fragment_total": progress[1] if progress else int(state.get("concubine_fragment_total", 4) or 4),
    }


def _is_puzzle_ready():
    total = int(state.get("concubine_fragment_total", 4) or 4)
    count = int(state.get("concubine_fragment_count", 0) or 0)
    return total > 0 and count >= total


def _has_available_partner():
    return state.get("concubine_availability") == "available" and bool((state.get("concubine_name") or "").strip())


def _has_main_due_action(now):
    if not _has_available_partner():
        return False
    if _is_puzzle_ready():
        return True
    return float(state.get("concubine_dream_due_at", 0) or 0) <= float(now)


def _is_tianji_affinity_blocked():
    return state.get("concubine_kind") == "道心侍妾" and int(state.get("concubine_affinity", 0) or 0) < 300


def _has_tianji_due_action(now):
    if not state.get("concubine_tianji_enabled"):
        return False
    if not _has_available_partner() or _is_tianji_affinity_blocked():
        return False
    return float(state.get("concubine_tianji_due_at", 0) or 0) <= float(now)


def _has_due_action(now):
    return (state.get("concubine_enabled") and _has_main_due_action(now)) or _has_tianji_due_action(now)


def _has_active_nanlong_pending(now):
    if not state.get("nanlong_enabled"):
        return False
    return (
        int(state.get("nanlong_reply_to_msg_id", 0) or 0) > 0
        and float(state.get("next_nanlong_time", 0) or 0) > 0
    )


def _clear_partner_snapshot():
    state["concubine_name"] = ""
    state["concubine_kind"] = ""
    state["concubine_location"] = ""
    state["concubine_affinity"] = 0
    state["concubine_oath"] = ""
    state["concubine_dream_due_at"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    state["concubine_fragment_count"] = 0
    state["concubine_fragment_total"] = 4


def _mark_no_partner(now, reason, *, allow_reacquire=True):
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    state["concubine_last_snapshot_at"] = 0
    state["concubine_last_error"] = str(reason or "暂无侍妾")
    clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if allow_reacquire and state.get("concubine_auto_reacquire") and now >= blocked_until:
        _schedule_after(now, 60, 1200)
    else:
        state["next_concubine_time"] = float(now + CONCUBINE_NO_PARTNER_RETRY_SEC)
    mark_dirty()


def _freeze_no_partner_until(until, reason):
    now = time.time()
    blocked_until = max(float(until or 0), now + 60)
    _clear_partner_snapshot()
    _set_availability("no_partner")
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
    state["concubine_last_snapshot_at"] = now
    state["concubine_last_error"] = str(reason or "侍妾暂不可补领")
    state["concubine_reacquire_blocked_until"] = blocked_until
    state["next_concubine_time"] = blocked_until
    mark_dirty()


def _apply_partner_acquired(name, now, *, kind="侍妾"):
    state["concubine_name"] = str(name or "").strip()
    state["concubine_kind"] = kind
    state["concubine_location"] = "待确认"
    state["concubine_affinity"] = 0
    state["concubine_oath"] = ""
    state["concubine_dream_due_at"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    state["concubine_fragment_count"] = 0
    state["concubine_fragment_total"] = 4
    state["concubine_last_snapshot_at"] = 0
    state["concubine_reacquire_attempts"] = 0
    state["concubine_reacquire_blocked_until"] = 0
    state["concubine_reacquire_command_override"] = ""
    state["concubine_last_error"] = ""
    _set_availability("available")
    _set_phase("idle")
    _clear_pending_msg_ids()
    _schedule_chain_action(now)


def _apply_status_snapshot(parsed, now):
    if not parsed:
        return False
    if not parsed.get("has_partner"):
        if parsed.get("manual_repair"):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "侍妾数据异常，等待人工修复")
            return True
        not_eligible = bool(parsed.get("not_eligible"))
        reason = "侍妾不可用：境界不足" if not_eligible else "暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible)
        return True

    state["concubine_name"] = parsed.get("name", "")
    state["concubine_kind"] = parsed.get("kind", "")
    state["concubine_location"] = parsed.get("location", "")
    state["concubine_affinity"] = int(parsed.get("affinity", 0) or 0)
    state["concubine_oath"] = parsed.get("oath", "")
    state["concubine_dream_due_at"] = float(parsed.get("dream_due_at", 0) or 0)
    state["concubine_tianji_due_at"] = float(parsed.get("tianji_due_at", 0) or 0)
    state["concubine_tianji_chain"] = parsed.get("tianji_chain", "")
    state["concubine_tianji_chain_due_at"] = float(parsed.get("tianji_chain_due_at", 0) or 0)
    state["concubine_fragment_count"] = int(parsed.get("fragment_count", 0) or 0)
    state["concubine_fragment_total"] = int(parsed.get("fragment_total", 4) or 4)
    state["concubine_last_snapshot_at"] = float(now)
    state["concubine_reacquire_command_override"] = ""
    state["concubine_last_error"] = ""
    _set_availability("available")
    _set_phase("idle")
    _clear_pending_msg_ids()

    if _is_puzzle_ready() and state.get("concubine_enabled"):
        _schedule_chain_action(now)
    elif _has_tianji_due_action(now) and not state.get("concubine_enabled"):
        _schedule_chain_action(now)
    else:
        due_times = []
        if state.get("concubine_enabled"):
            due_times.append(float(state.get("concubine_dream_due_at", 0) or 0))
        if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
            due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
        due_at = min(due_times) if due_times else now + CONCUBINE_STATUS_STALE_SEC
        _schedule_at_due_or_chain(now, due_at)
    return True


def _get_reacquire_command():
    override = str(state.get("concubine_reacquire_command_override") or "").strip()
    if override in CONCUBINE_REACQUIRE_COMMANDS:
        return override
    profile = get_send_as_profile()
    return CMD_CONCUBINE_SECT_MARRY if str(profile.get("sect_name") or "").strip() == "星宫" else CMD_CONCUBINE_ROMANCE


def _switch_reacquire_command(now, command, reason):
    state["concubine_reacquire_command_override"] = command if command in CONCUBINE_REACQUIRE_COMMANDS else ""
    state["concubine_last_error"] = reason
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    _schedule_chain_action(now)


def get_concubine_status_text():
    if not state.get("concubine_enabled", False) and not state.get("concubine_tianji_enabled", False):
        return "🌸 侍妾 - 未启用"

    phase_label = {
        "idle": "闲置",
        "status_pending": "侍妾状态校准中...",
        "dream_pending": "入梦寻图中...",
        "fragment_pending": "残图确认中...",
        "puzzle_ready": "残图已确认，等待拼图...",
        "puzzle_pending": "虚天残图拼合中...",
        "reacquire_pending": "补领侍妾中...",
        "tianji_pending": "天机代卜中...",
        "no_partner": "暂无侍妾",
    }.get(_phase(), _phase())
    strategy_label = {
        "reacquire_after_loss": "失去后自动补领",
        "shelter_trade_recall": "洞府安置-交易-召回（预留，未启用）",
    }.get(state.get("concubine_nanlong_strategy") or "", state.get("concubine_nanlong_strategy") or "未记录")

    lines = [
        "🌸 侍妾",
        f"- 当前阶段: {phase_label}",
        f"- 入梦寻图: {'开启' if state.get('concubine_enabled') else '关闭'}",
        f"- 天机代卜: {'开启' if state.get('concubine_tianji_enabled') else '关闭'}",
        f"- 自动补领: {'开启' if state.get('concubine_auto_reacquire') else '关闭'}",
        f"- 南陇侯策略: {strategy_label}",
    ]
    override = str(state.get("concubine_reacquire_command_override") or "").strip()
    if override:
        lines.append(f"- 补领指令校正: {override}")
    if _has_available_partner():
        kind = state.get("concubine_kind") or "侍妾"
        location = state.get("concubine_location") or "未知"
        lines.append(f"- 当前{kind}: {state.get('concubine_name')}（{location}）")
    else:
        lines.append("- 当前侍妾: 无/未确认")

    count = int(state.get("concubine_fragment_count", 0) or 0)
    total = int(state.get("concubine_fragment_total", 4) or 4)
    lines.append(f"- 虚天残图: {count}/{total}")
    dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
    if dream_due_at > 0:
        lines.append(f"- 入梦寻图: {fmt_abs_ts(dream_due_at)}（{fmt_remaining(dream_due_at)}）")
    else:
        lines.append("- 入梦寻图: 可施展/待确认")
    tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
    if tianji_due_at > 0:
        lines.append(f"- 天机代卜: {fmt_abs_ts(tianji_due_at)}（{fmt_remaining(tianji_due_at)}）")
    else:
        lines.append("- 天机代卜: 可施展/待确认")
    if state.get("concubine_tianji_chain"):
        chain_due_at = float(state.get("concubine_tianji_chain_due_at", 0) or 0)
        suffix = f"（{fmt_remaining(chain_due_at)}）" if chain_due_at > 0 else ""
        lines.append(f"- 天机卦象: {state.get('concubine_tianji_chain')}{suffix}")
    blocked_until = float(state.get("concubine_reacquire_blocked_until", 0) or 0)
    if blocked_until > 0:
        lines.append(f"- 补领冻结: {fmt_abs_ts(blocked_until)}（{fmt_remaining(blocked_until)}）")
    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time > 0:
        lines.append(f"- 下次动作: {fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    if state.get("concubine_last_error"):
        lines.append(f"- 最近异常: {state.get('concubine_last_error')}")
    if state.get("concubine_tianji_last_error"):
        lines.append(f"- 代卜异常: {state.get('concubine_tianji_last_error')}")
    return "\n".join(lines)


def clear_concubine_state(*, persist=False, keep_last_error=False, include_tianji=False):
    tianji_snapshot = {
        "concubine_tianji_due_at": state.get("concubine_tianji_due_at", 0),
        "concubine_tianji_chain": state.get("concubine_tianji_chain", ""),
        "concubine_tianji_chain_due_at": state.get("concubine_tianji_chain_due_at", 0),
        "concubine_tianji_last_error": state.get("concubine_tianji_last_error", ""),
    }
    state["next_concubine_time"] = 0
    state["concubine_phase"] = "idle"
    state["concubine_availability"] = "unknown"
    state["concubine_reacquire_command_override"] = ""
    _clear_partner_snapshot()
    _clear_pending_msg_ids()
    if include_tianji:
        clear_pending_tasks_by_commands(CONCUBINE_PENDING_COMMANDS, send_as_id=get_current_identity_id())
        state["concubine_tianji_due_at"] = 0
        state["concubine_tianji_chain"] = ""
        state["concubine_tianji_chain_due_at"] = 0
        state["concubine_tianji_last_error"] = ""
    else:
        clear_pending_tasks_by_commands(CONCUBINE_MAIN_PENDING_COMMANDS, send_as_id=get_current_identity_id())
        for key, value in tianji_snapshot.items():
            state[key] = value
    if not keep_last_error:
        state["concubine_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


def clear_concubine_tianji_state(*, persist=False, keep_last_error=False):
    state["concubine_tianji_msg_id"] = 0
    state["concubine_tianji_due_at"] = 0
    state["concubine_tianji_chain"] = ""
    state["concubine_tianji_chain_due_at"] = 0
    clear_pending_tasks_by_commands({CMD_CONCUBINE_TIANJI}, send_as_id=get_current_identity_id())
    if not keep_last_error:
        state["concubine_tianji_last_error"] = ""
    if not state.get("concubine_enabled"):
        state["next_concubine_time"] = 0
        state["concubine_phase"] = "idle"
    if persist:
        save_state()
    else:
        mark_dirty()


def restore_concubine_runtime(now):
    if _phase() in {"status_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending"}:
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
    if float(state.get("next_concubine_time", 0) or 0) <= 0:
        state["next_concubine_time"] = float(now + random.uniform(60, 1200))
    mark_dirty()


async def _send_status_command(now):
    msg = await send_game_command(CMD_CONCUBINE_STATUS, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .我的侍妾 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "status_pending")
        save_state()
        return False
    _set_phase("status_pending")
    state["concubine_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_dream_command(now):
    msg = await send_game_command(CMD_CONCUBINE_DREAM, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .入梦寻图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "dream_pending")
        save_state()
        return False
    _set_phase("dream_pending")
    state["concubine_dream_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_fragment_command(now):
    msg = await send_game_command(CMD_CONCUBINE_FRAGMENT, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .残图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "fragment_pending")
        save_state()
        return False
    _set_phase("fragment_pending")
    state["concubine_fragment_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_puzzle_command(now):
    msg = await send_game_command(CMD_CONCUBINE_PUZZLE, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = "发送 .拼图 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "puzzle_pending")
        save_state()
        return False
    _set_phase("puzzle_pending")
    state["concubine_puzzle_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_reacquire_command(now):
    command = _get_reacquire_command()
    msg = await send_game_command(command, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_last_error"] = f"发送 {command} 失败"
        _set_phase("no_partner")
        _backoff_after_pending_timeout(sent_at, "reacquire_pending")
        save_state()
        return False
    _set_phase("reacquire_pending")
    state["concubine_reacquire_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["concubine_reacquire_attempts"] = int(state.get("concubine_reacquire_attempts", 0) or 0) + 1
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def _send_tianji_command(now):
    msg = await send_game_command(CMD_CONCUBINE_TIANJI, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["concubine_tianji_last_error"] = "发送 .天机代卜 失败"
        _set_phase("idle")
        _backoff_after_pending_timeout(sent_at, "tianji_pending")
        save_state()
        return False
    _set_phase("tianji_pending")
    state["concubine_tianji_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
    save_state()
    return True


async def handle_concubine_status_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False) and not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_status" and CMD_CONCUBINE_STATUS not in orig_cmd:
        return False
    phase = _phase()
    if phase != "status_pending":
        if phase in {"dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending"}:
            console_log(f"🌸 忽略非等待期侍妾状态回复（phase={phase}）。")
            return True
        console_log(f"🌸 接受迟到的侍妾状态回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_status_msg_id"):
        console_log("🌸 忽略迟到的侍妾状态回复。")
        return True

    parsed = _parse_status_panel(text, now)
    if not parsed:
        state["concubine_last_error"] = f"未识别的侍妾状态回复: {(text or '')[:60]}"
        _set_phase("idle")
        _clear_pending_msg_ids()
        _backoff_after_pending_timeout(now, "status_pending")
        save_state()
        return False

    _apply_status_snapshot(parsed, now)
    save_state()
    if _is_puzzle_ready():
        await send_audit_log("🌸 虚天残图已凑齐，先自动 .残图 确认后再拼图。", scope="identity")
    return True


async def handle_concubine_dream_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_dream" and CMD_CONCUBINE_DREAM not in orig_cmd:
        return False
    phase = _phase()
    if phase != "dream_pending":
        if phase in {"fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending"}:
            console_log(f"🌸 忽略非等待期入梦寻图回复（phase={phase}）。")
            return True
        console_log(f"🌸 接受手动/迟到的入梦寻图回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_dream_msg_id"):
        console_log("🌸 忽略迟到的入梦寻图回复。")
        return True

    raw_text = text or ""
    if _is_dream_cooldown_text(raw_text):
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_DREAM_CD_SEC
        state["concubine_dream_due_at"] = now + wait_sec + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_at_due_or_chain(now, state["concubine_dream_due_at"])
        save_state()
        return True

    if "修为不足" in raw_text or "灵石不足" in raw_text or "资源不足" in raw_text:
        await _apply_concubine_resource_backoff(
            now,
            CONCUBINE_DREAM_RESOURCE_KEY,
            "concubine_dream_due_at",
            "concubine_last_error",
            "入梦寻图",
            raw_text,
        )
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "入梦寻图失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "入梦寻图失败：境界不足" if not_eligible else "入梦寻图失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=not not_eligible)
        save_state()
        return True

    progress = _parse_fragment_progress(raw_text)
    if "【入梦寻图】" in raw_text:
        _apply_dream_partner_hint(raw_text)
        if progress:
            state["concubine_fragment_count"] = progress[0]
            state["concubine_fragment_total"] = progress[1]
        state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        if _is_puzzle_ready():
            _schedule_chain_action(now)
            save_state()
            await send_audit_log("🌸 入梦寻图已达 4/4，将先 .残图 确认。", scope="identity")
        else:
            _schedule_at_due_or_chain(now, state["concubine_dream_due_at"])
            save_state()
        return True

    if "【全群异闻·虚天残图】" in raw_text:
        if progress:
            state["concubine_fragment_count"] = progress[0]
            state["concubine_fragment_total"] = progress[1]
        state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        reset_resource_shortage(CONCUBINE_DREAM_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        if _is_puzzle_ready():
            _schedule_chain_action(now)
            save_state()
            await send_audit_log("🌸 入梦寻图广播已达 4/4，将先 .残图 确认。", scope="identity")
        else:
            _schedule_at_due_or_chain(now, state["concubine_dream_due_at"])
            save_state()
        return True

    state["concubine_last_error"] = f"未识别的入梦寻图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "dream_pending")
    save_state()
    return False


async def handle_concubine_fragment_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_fragment" and CMD_CONCUBINE_FRAGMENT not in orig_cmd:
        return False
    if _phase() != "fragment_pending":
        console_log(f"🌸 忽略非等待期残图回复（phase={_phase()}）。")
        return True
    if not _is_current_reply(reply_to, "concubine_fragment_msg_id"):
        console_log("🌸 忽略迟到的残图回复。")
        return True

    raw_text = text or ""
    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "残图确认失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "残图确认失败：境界不足" if not_eligible else "残图确认失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=not not_eligible)
        save_state()
        return True

    progress = _parse_fragment_progress(raw_text)
    if progress:
        state["concubine_fragment_count"] = progress[0]
        state["concubine_fragment_total"] = progress[1]
    if "【虚天残图卷】" in raw_text:
        _set_phase("idle")
        _clear_pending_msg_ids()
        missing_match = RE_PUZZLE_MISSING.search(raw_text)
        missing_text = missing_match.group(1).strip() if missing_match else ""
        if _is_puzzle_ready() and (not missing_text or missing_text == "无"):
            state["concubine_last_error"] = ""
            _set_phase("puzzle_ready")
            _schedule_chain_action(now)
            save_state()
            await send_audit_log("🌸 残图确认 4/4，已排队自动 .拼图。", scope="identity")
        else:
            state["concubine_last_error"] = ""
            _schedule_at_due_or_chain(now, state.get("concubine_dream_due_at", 0))
            save_state()
        return True

    state["concubine_last_error"] = f"未识别的残图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "fragment_pending")
    save_state()
    return False


async def handle_concubine_puzzle_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_puzzle" and CMD_CONCUBINE_PUZZLE not in orig_cmd:
        return False
    if _phase() != "puzzle_pending":
        console_log(f"🌸 忽略非等待期拼图回复（phase={_phase()}）。")
        return True
    if not _is_current_reply(reply_to, "concubine_puzzle_msg_id"):
        console_log("🌸 忽略迟到的拼图回复。")
        return True

    raw_text = text or ""
    if "【虚天残图·拼合成功】" in raw_text:
        state["concubine_fragment_count"] = 0
        state["concubine_fragment_total"] = 4
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_at_due_or_chain(now, state["concubine_dream_due_at"])
        save_state()
        await send_audit_log("🌸 虚天残图拼合成功，已继续等待下一轮入梦。", scope="identity")
        return True

    if "残图尚未齐全" in raw_text:
        missing_match = RE_PUZZLE_MISSING.search(raw_text)
        if missing_match:
            missing_items = [item.strip() for item in re.split(r"[、,，]\s*", missing_match.group(1)) if item.strip()]
            total = max(4, len(missing_items))
            state["concubine_fragment_total"] = total
            state["concubine_fragment_count"] = max(0, total - len(missing_items))
        state["concubine_last_error"] = "拼图失败：残图尚未齐全"
        _set_phase("idle")
        _clear_pending_msg_ids()
        _backoff_after_pending_timeout(now, "puzzle_pending")
        save_state()
        return True

    if "【全群广播·虚天残图拼合】" in raw_text:
        state["concubine_fragment_count"] = 0
        state["concubine_fragment_total"] = 4
        if float(state.get("concubine_dream_due_at", 0) or 0) <= now:
            state["concubine_dream_due_at"] = now + CONCUBINE_DREAM_CD_SEC + CD_BUFFER_SEC
        state["concubine_last_error"] = ""
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_at_due_or_chain(now, state["concubine_dream_due_at"])
        save_state()
        return True

    state["concubine_last_error"] = f"未识别的拼图回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "puzzle_pending")
    save_state()
    return False


async def handle_concubine_reacquire_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_reacquire" and not any(cmd in orig_cmd for cmd in {CMD_CONCUBINE_SECT_MARRY, CMD_CONCUBINE_ROMANCE}):
        return False
    if _phase() != "reacquire_pending":
        console_log(f"🌸 忽略非等待期补领侍妾回复（phase={_phase()}）。")
        return True
    if not _is_current_reply(reply_to, "concubine_reacquire_msg_id"):
        console_log("🌸 忽略迟到的补领侍妾回复。")
        return True

    raw_text = text or ""
    if "开启了一段寻缘之旅" in raw_text:
        _set_phase("reacquire_pending")
        state["next_concubine_time"] = now + CONCUBINE_PHASE_TIMEOUT_SEC
        save_state()
        # 红尘寻缘常把这条中间态消息编辑成最终结果，不能提前消费。
        return False

    sect_match = RE_NEW_SECT_PARTNER.search(raw_text)
    romance_match = RE_NEW_ROMANCE_PARTNER.search(raw_text)
    if sect_match or romance_match:
        name = (sect_match or romance_match).group("name")
        _apply_partner_acquired(name, now, kind="道心侍妾" if sect_match else "红尘道侣")
        save_state()
        await send_audit_log(f"🌸 已补领侍妾【{name}】，稍后自动校准状态。", scope="identity")
        return True

    if "已有道侣" in raw_text or "已觅得红颜知己" in raw_text:
        state["concubine_last_error"] = "补领返回已有侍妾，将直接入梦校准"
        _set_availability("unknown")
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_chain_action(now)
        save_state()
        return True

    if "贡献不足" in raw_text or "灵石不足" in raw_text or "修为不足" in raw_text or "资源不足" in raw_text:
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领侍妾资源不足: {raw_text[:80]}")
        save_state()
        await send_audit_log(f"⚠️ 补领侍妾资源不足，已冻结 {int(CONCUBINE_REACQUIRE_RETRY_SEC / 3600)} 小时。", scope="identity")
        return True

    if _is_partner_not_eligible_text(raw_text):
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领侍妾条件不足: {raw_text[:80]}")
        save_state()
        await send_audit_log(f"⚠️ 补领侍妾条件不足，已冻结 {int(CONCUBINE_REACQUIRE_RETRY_SEC / 3600)} 小时。", scope="identity")
        return True

    if "踏遍万千红尘" in raw_text or "未能寻得有缘之人" in raw_text:
        _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "红尘寻缘未成功，停止连续尝试")
        save_state()
        return True

    if "神念消耗过剧" in raw_text:
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_REACQUIRE_RETRY_SEC
        _freeze_no_partner_until(now + wait_sec + CD_BUFFER_SEC, "补领侍妾冷却中")
        save_state()
        return True

    if "你乃星宫弟子" in raw_text or "宗门自有道心侍妾" in raw_text:
        if CMD_CONCUBINE_SECT_MARRY in orig_cmd:
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领指令校正异常: {raw_text[:80]}")
            save_state()
            return True
        _switch_reacquire_command(now, CMD_CONCUBINE_SECT_MARRY, "补领指令校正：改用宗门赐婚")
        save_state()
        await send_audit_log("🌸 检测到星宫身份，补领侍妾已改用 .宗门赐婚。", scope="identity")
        return True

    if "并非星宫弟子" in raw_text or "若为散修或其他宗门" in raw_text:
        if CMD_CONCUBINE_ROMANCE in orig_cmd:
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, f"补领指令校正异常: {raw_text[:80]}")
            save_state()
            return True
        _switch_reacquire_command(now, CMD_CONCUBINE_ROMANCE, "补领指令校正：改用红尘寻缘")
        save_state()
        await send_audit_log("🌸 检测到非星宫身份，补领侍妾已改用 .红尘寻缘。", scope="identity")
        return True

    state["concubine_last_error"] = f"未识别的补领回复: {raw_text[:60]}"
    _set_phase("no_partner")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "reacquire_pending")
    save_state()
    return False


async def handle_concubine_tianji_reply(text, now, reply_to, matched_family=None):
    if not state.get("concubine_tianji_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "concubine_tianji" and CMD_CONCUBINE_TIANJI not in orig_cmd:
        return False
    phase = _phase()
    if phase != "tianji_pending":
        if phase in {"status_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending"}:
            console_log(f"🌸 忽略非等待期天机代卜回复（phase={phase}）。")
            return True
        console_log(f"🌸 接受手动/迟到的天机代卜回复（phase={phase}）。")
    elif not _is_current_reply(reply_to, "concubine_tianji_msg_id"):
        console_log("🌸 忽略迟到的天机代卜回复。")
        return True

    raw_text = text or ""
    if "【天机代卜链】" in raw_text:
        gua_match = RE_TIANJI_GUA.search(raw_text)
        state["concubine_tianji_chain"] = gua_match.group("name").strip() if gua_match else ""
        state["concubine_tianji_due_at"] = now + CONCUBINE_TIANJI_CD_SEC + CD_BUFFER_SEC
        state["concubine_tianji_chain_due_at"] = state["concubine_tianji_due_at"]
        state["concubine_tianji_last_error"] = ""
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if "天机链路尚未重铸" in raw_text:
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else CONCUBINE_TIANJI_CD_SEC
        state["concubine_tianji_due_at"] = now + wait_sec + CD_BUFFER_SEC
        state["concubine_tianji_last_error"] = ""
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if _is_no_partner_text(raw_text):
        if _is_partner_manual_repair_text(raw_text):
            _freeze_no_partner_until(now + CONCUBINE_REACQUIRE_RETRY_SEC, "天机代卜失败：侍妾数据异常，等待人工修复")
            save_state()
            return True
        not_eligible = _is_partner_not_eligible_text(raw_text)
        reason = "天机代卜失败：境界不足" if not_eligible else "天机代卜失败：暂无侍妾"
        _mark_no_partner(now, reason, allow_reacquire=bool(state.get("concubine_enabled")) and not not_eligible)
        save_state()
        return True

    if "情缘未至" in raw_text or "情缘未深" in raw_text or "无法为你卜算天机" in raw_text:
        state["concubine_tianji_last_error"] = "情缘不足，暂缓天机代卜"
        state["concubine_tianji_due_at"] = now + CONCUBINE_TIANJI_CD_SEC
        reset_resource_shortage(CONCUBINE_TIANJI_RESOURCE_KEY)
        _set_phase("idle")
        _clear_pending_msg_ids()
        _schedule_after_tianji(now)
        save_state()
        return True

    if _is_tianji_resource_shortage_text(raw_text):
        await _apply_concubine_resource_backoff(
            now,
            CONCUBINE_TIANJI_RESOURCE_KEY,
            "concubine_tianji_due_at",
            "concubine_tianji_last_error",
            "天机代卜",
            raw_text,
        )
        save_state()
        return True

    state["concubine_tianji_last_error"] = f"未识别的天机代卜回复: {raw_text[:60]}"
    _set_phase("idle")
    _clear_pending_msg_ids()
    _backoff_after_pending_timeout(now, "tianji_pending")
    save_state()
    return False


async def handle_concubine_loss_broadcast(text, now, event):
    if not state.get("concubine_enabled", False) and not state.get("concubine_tianji_enabled", False):
        return False
    raw_text = text or ""
    if "南陇侯" not in raw_text or "侍妾" not in raw_text:
        return False
    if "掳走" not in raw_text and "选择将侍妾" not in raw_text:
        return False
    if not _text_matches_current_identity(raw_text):
        return False

    matched = RE_LOST_PARTNER_NAME.search(raw_text)
    partner_name = matched.group("name") if matched else (state.get("concubine_name") or "")
    _mark_no_partner(now, f"南陇侯导致侍妾失去: {partner_name or '未知'}", allow_reacquire=bool(state.get("concubine_enabled")))
    save_state()
    if state.get("concubine_auto_reacquire"):
        await send_audit_log("🌸 侍妾已被南陇侯带走，已进入自动补领等待。", scope="identity")
    else:
        await send_audit_log("🌸 侍妾已被南陇侯带走，入梦/拼图已熔断；自动补领未开启。", scope="identity")
    return True


async def run_concubine_scheduler(now):
    if _CONCUBINE_SCHEDULER_LOCK.locked():
        return
    async with _CONCUBINE_SCHEDULER_LOCK:
        await _run_concubine_scheduler(now)


async def _run_concubine_scheduler(now):
    if not state.get("concubine_enabled", False) and not state.get("concubine_tianji_enabled", False):
        return

    if _has_active_nanlong_pending(now):
        state["concubine_last_error"] = "南陇侯抉择中，侍妾模块暂缓"
        _schedule_after(now, 60, 600)
        save_state()
        return

    phase = _phase()
    if phase in {"status_pending", "dream_pending", "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending"}:
        pending_until = float(state.get("next_concubine_time", 0) or 0)
        if pending_until > now:
            return
        state["concubine_last_error"] = f"{phase} 等待回复超时"
        if _has_available_partner():
            _set_phase("idle")
        elif state.get("concubine_availability") == "no_partner":
            _set_phase("no_partner")
        else:
            _set_phase("idle")
        _clear_pending_msg_ids()
        retry_at = _backoff_after_pending_timeout(now, phase)
        save_state()
        await send_audit_log(
            f"⚠️ 侍妾模块 {phase} 超时，已停止当前链路；{fmt_time_after(max(0, retry_at - now))} 后再校准。",
            scope="identity",
        )
        return

    next_time = float(state.get("next_concubine_time", 0) or 0)
    if next_time > 0 and now < next_time and not _has_due_action(now):
        return

    if phase == "no_partner" or state.get("concubine_availability") == "no_partner":
        if state.get("concubine_enabled") and state.get("concubine_auto_reacquire") and now >= float(state.get("concubine_reacquire_blocked_until", 0) or 0):
            await _send_reacquire_command(now)
            return
        state["next_concubine_time"] = float(now + CONCUBINE_NO_PARTNER_RETRY_SEC)
        save_state()
        return

    if phase == "puzzle_ready":
        if state.get("concubine_enabled") and _is_puzzle_ready():
            await _send_puzzle_command(now)
            return
        _set_phase("idle")
        _schedule_status_recheck(now)
        save_state()
        return

    if not _has_available_partner():
        if state.get("concubine_tianji_enabled"):
            await _send_status_command(now)
        else:
            await _send_dream_command(now)
        return

    if state.get("concubine_enabled") and _is_puzzle_ready():
        await _send_fragment_command(now)
        return

    if state.get("concubine_enabled"):
        dream_due_at = float(state.get("concubine_dream_due_at", 0) or 0)
        if dream_due_at <= now:
            await _send_dream_command(now)
            return

    if state.get("concubine_tianji_enabled"):
        if _is_tianji_affinity_blocked():
            state["concubine_tianji_last_error"] = "情缘不足，暂缓天机代卜"
            state["concubine_tianji_due_at"] = now + CONCUBINE_TIANJI_CD_SEC
            _schedule_after_tianji(now)
            save_state()
            return
        tianji_due_at = float(state.get("concubine_tianji_due_at", 0) or 0)
        if tianji_due_at <= now:
            await _send_tianji_command(now)
            return

    due_times = []
    if state.get("concubine_enabled"):
        due_times.append(float(state.get("concubine_dream_due_at", 0) or 0))
    if state.get("concubine_tianji_enabled") and not _is_tianji_affinity_blocked():
        due_times.append(float(state.get("concubine_tianji_due_at", 0) or 0))
    due_times = [due_at for due_at in due_times if due_at > now]
    if due_times:
        state["next_concubine_time"] = min(due_times) + random.uniform(60, 600)
    else:
        _schedule_status_recheck(now)
    save_state()


__all__ = [
    "CONCUBINE_PENDING_COMMANDS",
    "clear_concubine_state",
    "clear_concubine_tianji_state",
    "get_concubine_status_text",
    "handle_concubine_dream_reply",
    "handle_concubine_fragment_reply",
    "handle_concubine_loss_broadcast",
    "handle_concubine_puzzle_reply",
    "handle_concubine_reacquire_reply",
    "handle_concubine_status_reply",
    "handle_concubine_tianji_reply",
    "restore_concubine_runtime",
    "run_concubine_scheduler",
]
