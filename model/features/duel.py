import random
import re
import time

from ..config import CD_BUFFER_SEC, CMD_DUEL
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_send_as_profile, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining
from .tianxing import (
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    normalize_tianxing_auto_config,
    run_tianxing_timeline_scheduler,
)


DUEL_MIN_REALM = "元婴后期"
DUEL_MIN_XIUWEI = 600_000
DUEL_REPLY_TIMEOUT_SEC = 120
DUEL_NORMAL_COOLDOWN_SEC = 310
DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC = 610
DUEL_RECOVERY_MIN_SEC = 60
DUEL_RECOVERY_MAX_SEC = 180
DUEL_RESULT_GRACE_SEC = 30
DUEL_WAITING_PREFIX = "正在锁定对手天机，请稍候"
DUEL_READY_PREFIX = "⚔️ 法宝齐出！"
DUEL_REPORT_PREFIX = "【天道战报·文字版】"
DUEL_FINAL_PREFIX = "【斗法终局】"
DUEL_SETTLING_TEXT = "战斗结束，正在整理天道战报"
RE_DUEL_WINNER = re.compile(r"(?:胜者[:：]\s*|胜者：)(@[^\s|]+)")
RE_DUEL_LOSER = re.compile(r"(?:败者[:：]\s*|败者：)(@[^\s|]+)")


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


def _target_token():
    return normalize_duel_target(state.get("duel_target", ""))


def build_duel_command(target=None):
    target = normalize_duel_target(_target_token() if target is None else target)
    return f"{CMD_DUEL} {target}" if target else ""


def _schedule_next_duel(now, delay_sec):
    state["next_duel_time"] = float(now + max(1, delay_sec))
    return state["next_duel_time"]


def _clear_duel_pending():
    state["duel_reply_to_msg_id"] = 0
    state["duel_reply_due_at"] = 0
    state["duel_open_msg_id"] = 0
    state["duel_magic_due_at"] = 0
    state["duel_magic_sent_at"] = 0
    state["duel_started_at"] = 0


def _set_duel_error(message, *, next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=None, persist=True):
    state["duel_last_error"] = str(message or "").strip()
    if now is None:
        now = time.time()
    _schedule_next_duel(now, next_delay)
    if persist:
        save_state()
    else:
        mark_dirty()


def _profile_gate_reason():
    profile = get_send_as_profile(get_current_identity_id()) or {}
    realm = str(profile.get("realm") or "").strip()
    xiuwei_current = _parse_int(profile.get("xiuwei_current", 0))
    if realm != DUEL_MIN_REALM:
        return f"境界需为{DUEL_MIN_REALM}，当前={realm or '未知'}"
    if xiuwei_current <= DUEL_MIN_XIUWEI:
        current_text = xiuwei_current if xiuwei_current > 0 else "未知"
        return f"修为需 >{DUEL_MIN_XIUWEI}，当前={current_text}"
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
        or "无法再次斗法" in raw
        or "元神尚未平复" in raw
        or "虚弱" in raw
        or "无法锁定对手" in raw
        or "尚未踏入仙途" in raw
        or "对方正在斗法" in raw
        or "你已在斗法" in raw
        or "小隐于野" in raw
    )


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
            "无法再次斗法",
            "元神尚未平复",
            "无法锁定对手",
            "尚未踏入仙途",
            "对方正在斗法",
            "你已在斗法",
            "小隐于野",
        )
    )


def parse_duel_result_summary(text):
    raw = str(text or "").strip()
    if not raw:
        return "未知"
    if raw.startswith(DUEL_REPORT_PREFIX) or raw.startswith(DUEL_FINAL_PREFIX):
        match = RE_DUEL_WINNER.search(raw)
        return f"斗法结束，胜者 {match.group(1)}" if match else "斗法结束"
    if raw.startswith(DUEL_READY_PREFIX):
        return "法宝齐出，等待战报"
    if raw.startswith(DUEL_SETTLING_TEXT):
        return "战斗结束，等待战报"
    if raw.startswith(DUEL_WAITING_PREFIX):
        return "正在锁定对手"
    return _first_line(raw)[:80] or "未知"


def _duel_next_time_blocks(now):
    return cd_blocks(state.get("next_duel_time", 0), now, 0)


async def _prepare_duel_tianxing_route(now, *, due_at=0):
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan("斗法", reason="斗法", now=now)
    if preflight.get("route_allowed"):
        return True

    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        state["next_duel_time"] = blocked_until + CD_BUFFER_SEC
        state["duel_last_error"] = str(preflight.get("reason") or "斗法天星预检阻断")
        save_state()
        return False

    if preflight.get("timeline_required"):
        config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
        if not config.get("duel_route_enabled"):
            return True
        windows = build_tianxing_consume_window("斗法", now=now, due_at=max(due_at, now), reason="斗法")
        if not windows:
            state["duel_last_error"] = str(preflight.get("reason") or "斗法等待天星时间线准备窗口")
            save_state()
            return False
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows)
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
    state["next_duel_time"] = 0
    state["duel_target"] = target
    state["duel_total_count"] = total_count
    state["duel_completed_count"] = 0
    _clear_duel_pending()
    state["duel_last_msg_id"] = 0
    state["duel_last_result"] = ""
    state["duel_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def apply_duel_config(target=None, total_count=None, *, reset_progress=False, now=None, persist=True):
    if target is not None:
        state["duel_target"] = normalize_duel_target(target)
    if total_count is not None:
        state["duel_total_count"] = max(0, _parse_int(total_count))
    if reset_progress:
        state["duel_completed_count"] = 0
    if now is not None and state.get("duel_enabled") and not _duel_next_time_blocks(now):
        state["next_duel_time"] = float(now + 1)
    if persist:
        save_state()
    else:
        mark_dirty()
    return {
        "target": state.get("duel_target", ""),
        "total_count": int(state.get("duel_total_count", 0) or 0),
        "completed_count": int(state.get("duel_completed_count", 0) or 0),
    }


def get_duel_status_text():
    target = _target_token() or "未配置"
    total_count = int(state.get("duel_total_count", 0) or 0)
    completed_count = int(state.get("duel_completed_count", 0) or 0)
    profile = get_send_as_profile(get_current_identity_id()) or {}
    lines = [
        "🗡️ 斗法",
        f"- 已启用：{'是' if state.get('duel_enabled') else '否'}",
        f"- 目标：{target}",
        f"- 次数：{completed_count}/{total_count if total_count > 0 else '未配置'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_duel_time', 0))}（{fmt_remaining(state.get('next_duel_time', 0))}）",
        f"- 境界门槛：{DUEL_MIN_REALM} 且修为 >{DUEL_MIN_XIUWEI}",
        f"- 当前境界：{profile.get('realm') or '未知'}",
        f"- 当前修为：{_parse_int(profile.get('xiuwei_current', 0)) or '未知'}",
        f"- 待回复命令ID：{int(state.get('duel_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 斗法消息ID：{int(state.get('duel_open_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('duel_reply_due_at', 0))}（{fmt_remaining(state.get('duel_reply_due_at', 0))}）",
        f"- 冷却：正常/无虚弱 {DUEL_NORMAL_COOLDOWN_SEC}s；虚弱/逃跑/未知 {DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC}s",
        f"- 最近结果：{state.get('duel_last_result') or '无'}",
    ]
    if state.get("duel_last_error"):
        lines.append(f"- 最近异常：{state['duel_last_error']}")
    return "\n".join(lines)


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


async def _handle_duel_text(text, now, *, result_msg_id=0):
    raw_text = str(text or "").strip()
    if not raw_text:
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

    summary = parse_duel_result_summary(raw_text)
    weak_or_unknown = _is_weak_or_unknown_result(raw_text)
    _clear_duel_pending()
    state["duel_last_msg_id"] = int(result_msg_id or 0)
    state["duel_last_result"] = summary
    state["duel_last_error"] = "" if not weak_or_unknown else summary
    if raw_text.startswith(DUEL_REPORT_PREFIX) or raw_text.startswith(DUEL_FINAL_PREFIX):
        state["duel_completed_count"] = int(state.get("duel_completed_count", 0) or 0) + 1
        total_count = int(state.get("duel_total_count", 0) or 0)
        if total_count > 0 and int(state.get("duel_completed_count", 0) or 0) >= total_count:
            state["duel_enabled"] = False
            state["next_duel_time"] = 0
            save_state()
            await send_audit_log(f"✅ 斗法完成：{state['duel_completed_count']}/{total_count}", scope="identity", limit=180)
            return True
    cooldown = DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC if weak_or_unknown else DUEL_NORMAL_COOLDOWN_SEC
    _schedule_next_duel(now, cooldown + CD_BUFFER_SEC)
    save_state()
    await send_audit_log(f"🗡️ 斗法结果：{summary}", scope="identity", limit=220)
    return True


async def run_duel_scheduler(now):
    if not state.get("duel_enabled"):
        return

    target = _target_token()
    if not target:
        if not _duel_next_time_blocks(now):
            _set_duel_error("斗法目标未配置", next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    gate_reason = _profile_gate_reason()
    if gate_reason:
        if not _duel_next_time_blocks(now):
            _set_duel_error(gate_reason, next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    total_count = int(state.get("duel_total_count", 0) or 0)
    completed_count = int(state.get("duel_completed_count", 0) or 0)
    if total_count > 0 and completed_count >= total_count:
        state["duel_enabled"] = False
        state["next_duel_time"] = 0
        state["duel_last_result"] = f"任务完成：{completed_count}/{total_count}"
        save_state()
        return
    if total_count <= 0:
        if not _duel_next_time_blocks(now):
            _set_duel_error("斗法次数未配置", next_delay=DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC, now=now)
        return

    reply_to_msg_id = int(state.get("duel_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("duel_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        _clear_duel_pending()
        state["duel_last_error"] = "斗法回复超时"
        _schedule_next_duel(now, DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC)
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

    if not await _prepare_duel_tianxing_route(now, due_at=now):
        return

    command = build_duel_command(target)
    msg = await send_game_command(command, track=False, max_retry=0, source_module="斗法")
    if not msg:
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
    state["duel_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["duel_last_result"] = "已发送"
    state["duel_last_error"] = ""
    state["next_duel_time"] = state["duel_reply_due_at"]
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
    "DUEL_MIN_REALM",
    "DUEL_MIN_XIUWEI",
    "DUEL_NORMAL_COOLDOWN_SEC",
    "DUEL_WEAK_OR_UNKNOWN_COOLDOWN_SEC",
    "apply_duel_config",
    "build_duel_command",
    "clear_duel_state",
    "get_duel_status_text",
    "handle_duel_broadcast",
    "handle_duel_reply",
    "is_duel_reply_text",
    "normalize_duel_target",
    "parse_duel_result_summary",
    "run_duel_scheduler",
    "schedule_duel_initial_check",
]
