import random
import re
import time
from datetime import datetime, timedelta

from ..config import (
    CD_BUFFER_SEC,
    CMD_MULAN_COLLECT,
    CMD_MULAN_JUDGE,
    CMD_MULAN_PUBLISH,
    CMD_MULAN_SHADOW,
    MULAN_CD,
    MULAN_JITTER_MAX_SEC,
    MULAN_JITTER_MIN_SEC,
    MULAN_REPLY_TIMEOUT_SEC,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..action_guard import close_action as close_action_guard_action
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


MULAN_DEFAULT_IDS = (1, 2, 3)
MULAN_RECOVERY_MIN_SEC = 60
MULAN_RECOVERY_MAX_SEC = 180
MULAN_PHASE_IDLE = "idle"
MULAN_PHASE_COLLECT_PENDING = "collect_pending"
MULAN_PHASE_READY_TO_JUDGE = "ready_to_judge"
MULAN_PHASE_JUDGE_PENDING = "judge_pending"
MULAN_PHASE_READY_TO_PUBLISH = "ready_to_publish"
MULAN_PHASE_PUBLISH_PENDING = "publish_pending"
MULAN_PHASE_COOLDOWN = "cooldown"

RE_REPORT_ID = re.compile(r"(?:军报|情报|编号|第)?\s*([1-9]\d*)\s*(?:号|：|:|、|\.|．|\)|）)")
RE_COMMAND_ID = re.compile(r"^\.辨报\s+([1-9]\d*)")

RELIABLE_KEYWORDS = (
    "可靠性较高",
    "研判较高",
    "可信度较高",
    "可信较高",
    "情报可靠",
    "较为可靠",
    "确认为真",
    "确认属实",
    "基本属实",
    "可以公开",
    "可公开",
    "较高",
    "可靠",
    "可信",
    "属实",
    "准确",
    "无误",
)
SUSPICIOUS_KEYWORDS = (
    "研判可疑",
    "情报可疑",
    "明显可疑",
    "存疑",
    "虚假",
    "不可靠",
    "可靠性低",
    "可信度低",
    "疑点",
    "伪报",
    "误报",
    "可疑",
)
CD_KEYWORDS = ("尚未", "冷却", "稍后", "后再", "不可频繁")
DONE_KEYWORDS = (
    "今日已",
    "今天已",
    "本日已",
    "已经提交",
    "已提交",
    "已经公开",
    "已公开",
    "不可重复",
    "无需重复",
    "莫要重复",
    "没有可公开",
    "暂无可公开",
)


def _parse_int(value, default=0):
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _encode_ids(ids):
    normalized = []
    seen = set()
    for value in ids or ():
        report_id = _parse_int(value)
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        normalized.append(str(report_id))
    return ",".join(normalized)


def _decode_ids(value):
    ids = []
    seen = set()
    for part in re.split(r"[,，\s]+", str(value or "").strip()):
        report_id = _parse_int(part)
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        ids.append(report_id)
    return ids


def parse_mulan_report_ids(text):
    raw_text = str(text or "")
    ids = []
    seen = set()
    for match in RE_REPORT_ID.finditer(raw_text):
        report_id = _parse_int(match.group(1))
        if report_id <= 0 or report_id in seen:
            continue
        seen.add(report_id)
        ids.append(report_id)
    return ids or list(MULAN_DEFAULT_IDS)


def classify_mulan_judgement(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return "unknown"
    if any(keyword in raw_text for keyword in SUSPICIOUS_KEYWORDS):
        return "suspicious"
    if any(keyword in raw_text for keyword in RELIABLE_KEYWORDS):
        return "reliable"
    return "unknown"


def _pending_command_family(reply_to=None, matched_family=None):
    family = str(matched_family or "").strip()
    if family in {"mulan_collect", "mulan_judge", "mulan_publish", "mulan_panel"}:
        return family
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    if orig_cmd == CMD_MULAN_COLLECT or orig_cmd.startswith(f"{CMD_MULAN_COLLECT} "):
        return "mulan_collect"
    if orig_cmd == CMD_MULAN_SHADOW or orig_cmd.startswith(f"{CMD_MULAN_SHADOW} "):
        return "mulan_panel"
    if orig_cmd == CMD_MULAN_PUBLISH or orig_cmd.startswith(f"{CMD_MULAN_PUBLISH} "):
        return "mulan_publish"
    if orig_cmd == CMD_MULAN_JUDGE or orig_cmd.startswith(f"{CMD_MULAN_JUDGE} "):
        return "mulan_judge"
    return ""


def _action_key_for_family(family):
    if family in {"mulan_collect", "mulan_panel"}:
        return "mulan_collect"
    if family == "mulan_judge":
        return "mulan_judge"
    if family == "mulan_publish":
        return "mulan_publish"
    return ""


def _close_mulan_action_guard(family, now):
    action_key = _action_key_for_family(family)
    if action_key:
        close_action_guard_action(action_key, send_as_id=get_current_identity_id(), now=now, reason="mulan_reply")


def _daily_done_delay_sec(now):
    current = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    tomorrow = (current + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
    delay = (tomorrow - current).total_seconds()
    delay += random.uniform(MULAN_JITTER_MIN_SEC, MULAN_JITTER_MAX_SEC)
    return max(60, delay)


def _is_daily_done_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return False
    if not ("军报" in raw_text or "慕兰" in raw_text):
        return False
    return any(keyword in raw_text for keyword in DONE_KEYWORDS)


def _schedule_next_mulan(now, delay_sec=None):
    if delay_sec is None:
        delay_sec = MULAN_CD + random.uniform(MULAN_JITTER_MIN_SEC, MULAN_JITTER_MAX_SEC)
    state["next_mulan_time"] = float(now + max(1, delay_sec))
    return state["next_mulan_time"]


def _mulan_next_time_blocks(now):
    return cd_blocks(state.get("next_mulan_time", 0), now, 0)


def _clear_mulan_pending():
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_sent_at"] = 0


def _finish_mulan_cycle(now, result, *, delay_sec=None, error=""):
    _clear_mulan_pending()
    state["mulan_phase"] = MULAN_PHASE_COOLDOWN
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_last_result"] = str(result or "").strip()
    state["mulan_last_error"] = str(error or "").strip()
    state["mulan_cycle_count"] = int(state.get("mulan_cycle_count", 0) or 0) + 1
    _schedule_next_mulan(now, delay_sec=delay_sec)


def clear_mulan_state(*, persist=False, keep_last_error=False):
    last_error = state.get("mulan_last_error") if keep_last_error else ""
    state["next_mulan_time"] = 0
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_sent_at"] = 0
    state["mulan_last_msg_id"] = 0
    state["mulan_last_command"] = ""
    state["mulan_last_result"] = ""
    state["mulan_last_error"] = last_error or ""
    if persist:
        save_state()
    else:
        mark_dirty()


def get_mulan_status_text():
    lines = [
        "🕵️ 慕兰",
        f"- 已启用：{'是' if state.get('mulan_enabled') else '否'}",
        f"- 阶段：{state.get('mulan_phase') or MULAN_PHASE_IDLE}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_mulan_time', 0))}（{fmt_remaining(state.get('next_mulan_time', 0))}）",
        f"- 候选编号：{state.get('mulan_pending_ids') or '默认 1,2,3'}",
        f"- 当前辨报：{int(state.get('mulan_current_id', 0) or 0) or '无'}",
        f"- 待公开：{int(state.get('mulan_public_id', 0) or 0) or '无'}",
        f"- 待回复命令ID：{int(state.get('mulan_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('mulan_reply_due_at', 0))}（{fmt_remaining(state.get('mulan_reply_due_at', 0))}）",
        f"- 最近命令：{state.get('mulan_last_command') or '无'}",
        f"- 最近结果：{state.get('mulan_last_result') or '无'}",
    ]
    if state.get("mulan_last_msg_id"):
        lines.append(f"- 最近结果消息ID：{state['mulan_last_msg_id']}")
    if state.get("mulan_last_error"):
        lines.append(f"- 最近异常：{state['mulan_last_error']}")
    return "\n".join(lines)


async def _send_mulan_command(command, now, phase):
    msg = await send_game_command(command, track=False, max_retry=0, source_module="慕兰")
    if not msg:
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
        state["mulan_last_command"] = command
        state["mulan_last_error"] = f"{command} 发送失败"
        save_state()
        await send_audit_log(f"❌ 慕兰发送失败：{command}，稍后重试。", scope="identity", limit=180)
        return False

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["mulan_phase"] = phase
    state["mulan_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["mulan_reply_due_at"] = sent_at + MULAN_REPLY_TIMEOUT_SEC
    state["mulan_sent_at"] = sent_at
    state["mulan_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["mulan_last_command"] = command
    state["mulan_last_result"] = "已发送"
    state["mulan_last_error"] = ""
    state["next_mulan_time"] = state["mulan_reply_due_at"]
    save_state()
    console_log(f"🕵️ 慕兰已发送：{command}，等待回复→{fmt_abs_ts(state['mulan_reply_due_at'])}", scope="identity", limit=180)
    return True


async def handle_mulan_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("mulan_enabled"):
        return False
    family = _pending_command_family(reply_to=reply_to, matched_family=matched_family)
    if not family:
        return False
    _close_mulan_action_guard(family, now)

    raw_text = str(text or "").strip()
    result_msg_id = int(result_msg_id or 0)
    if result_msg_id > 0:
        state["mulan_last_msg_id"] = result_msg_id

    if _is_daily_done_text(raw_text):
        _finish_mulan_cycle(now, "今日已完成", delay_sec=_daily_done_delay_sec(now))
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log("🕵️ 慕兰今日已完成，已排到明日再查。", scope="identity", limit=180)
        return True

    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in CD_KEYWORDS):
        wait_sec = parse_wait_time(raw_text)
        _finish_mulan_cycle(now, "冷却中", delay_sec=wait_sec + CD_BUFFER_SEC)
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log(f"🕵️ 慕兰 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity", limit=180)
        return True

    if family in {"mulan_collect", "mulan_panel"}:
        ids = parse_mulan_report_ids(raw_text)
        state["mulan_phase"] = MULAN_PHASE_READY_TO_JUDGE
        state["mulan_pending_ids"] = _encode_ids(ids)
        state["mulan_current_id"] = 0
        state["mulan_public_id"] = 0
        state["mulan_last_result"] = f"已搜集：{state['mulan_pending_ids'] or '1,2,3'}"
        state["mulan_last_error"] = ""
        _clear_mulan_pending()
        state["next_mulan_time"] = float(now)
        save_state()
        return True

    if family == "mulan_judge":
        report_id = int(state.get("mulan_current_id", 0) or 0)
        if report_id <= 0:
            match = RE_COMMAND_ID.search(str(getattr(reply_to, "raw_text", "") or "").strip())
            report_id = _parse_int(match.group(1) if match else 0)
        verdict = classify_mulan_judgement(raw_text)
        pending_ids = [item for item in _decode_ids(state.get("mulan_pending_ids")) if item != report_id]
        _clear_mulan_pending()
        if verdict == "reliable":
            state["mulan_phase"] = MULAN_PHASE_READY_TO_PUBLISH
            state["mulan_pending_ids"] = _encode_ids(pending_ids)
            state["mulan_public_id"] = report_id
            state["mulan_current_id"] = 0
            state["mulan_last_result"] = f"{report_id}号可靠，准备公开"
            state["mulan_last_error"] = ""
            state["next_mulan_time"] = float(now)
            save_state()
            return True

        state["mulan_public_id"] = 0
        state["mulan_current_id"] = 0
        state["mulan_pending_ids"] = _encode_ids(pending_ids)
        if pending_ids:
            state["mulan_phase"] = MULAN_PHASE_READY_TO_JUDGE
            state["mulan_last_result"] = f"{report_id or '?'}号{('可疑' if verdict == 'suspicious' else '未识别')}，继续辨报"
            state["mulan_last_error"] = "" if verdict == "suspicious" else "辨报结果未识别，已保守跳过"
            state["next_mulan_time"] = float(now)
            save_state()
            return True

        _finish_mulan_cycle(
            now,
            "全部可疑，未公开",
            error="" if verdict == "suspicious" else "最后一条辨报未识别，已保守跳过",
        )
        save_state()
        await send_audit_log("🕵️ 慕兰本轮未公开：候选军报均未判可靠。", scope="identity", limit=180)
        return True

    if family == "mulan_publish":
        public_id = int(state.get("mulan_public_id", 0) or 0)
        if public_id <= 0:
            match = re.search(r"\.公开军报\s+([1-9]\d*)", str(getattr(reply_to, "raw_text", "") or "").strip())
            public_id = _parse_int(match.group(1) if match else 0)
        _finish_mulan_cycle(now, f"已公开{public_id or ''}号军报".strip())
        state["mulan_last_error"] = ""
        save_state()
        await send_audit_log(f"🕵️ 慕兰公开完成：{public_id or '未知'}号军报。", scope="identity", limit=180)
        return True

    return False


async def run_mulan_scheduler(now):
    if not state.get("mulan_enabled"):
        return

    reply_to_msg_id = int(state.get("mulan_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("mulan_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        phase = state.get("mulan_phase") or MULAN_PHASE_IDLE
        _clear_mulan_pending()
        state["mulan_last_error"] = f"{phase} 回复超时"
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
        save_state()
        await send_audit_log(f"⚠️ 慕兰回复超时，消息ID={reply_to_msg_id}，稍后重试。", scope="identity", limit=220)
        return

    phase = str(state.get("mulan_phase") or MULAN_PHASE_IDLE).strip()
    if phase == MULAN_PHASE_READY_TO_JUDGE:
        pending_ids = _decode_ids(state.get("mulan_pending_ids")) or list(MULAN_DEFAULT_IDS)
        report_id = pending_ids[0] if pending_ids else 0
        if report_id <= 0:
            _finish_mulan_cycle(now, "无候选军报，跳过")
            save_state()
            return
        state["mulan_current_id"] = report_id
        await _send_mulan_command(f"{CMD_MULAN_JUDGE} {report_id}", now, MULAN_PHASE_JUDGE_PENDING)
        return

    if phase == MULAN_PHASE_READY_TO_PUBLISH:
        public_id = int(state.get("mulan_public_id", 0) or 0)
        if public_id <= 0:
            _finish_mulan_cycle(now, "待公开编号丢失，跳过", error="待公开编号丢失")
            save_state()
            return
        await _send_mulan_command(f"{CMD_MULAN_PUBLISH} {public_id}", now, MULAN_PHASE_PUBLISH_PENDING)
        return

    if _mulan_next_time_blocks(now):
        return

    if phase not in {MULAN_PHASE_IDLE, MULAN_PHASE_COOLDOWN, ""}:
        state["mulan_phase"] = MULAN_PHASE_IDLE
        state["mulan_last_error"] = f"异常阶段 {phase}，已复位"
        state["next_mulan_time"] = float(now + RETRY_MAX_SEC)
        save_state()
        await send_audit_log(f"⚠️ 慕兰阶段异常已复位：{phase}", scope="identity", limit=180)
        return

    await _send_mulan_command(CMD_MULAN_COLLECT, now, MULAN_PHASE_COLLECT_PENDING)


def schedule_mulan_initial_check(now, *, persist=False, keep_last_error=True):
    last_error = state.get("mulan_last_error") if keep_last_error else ""
    state["mulan_phase"] = MULAN_PHASE_IDLE
    state["mulan_reply_to_msg_id"] = 0
    state["mulan_reply_due_at"] = 0
    state["mulan_pending_ids"] = ""
    state["mulan_current_id"] = 0
    state["mulan_public_id"] = 0
    state["mulan_sent_at"] = 0
    state["mulan_last_error"] = last_error or ""
    state["next_mulan_time"] = float(now + random.uniform(MULAN_RECOVERY_MIN_SEC, MULAN_RECOVERY_MAX_SEC))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_mulan_time"]


__all__ = [
    "MULAN_DEFAULT_IDS",
    "classify_mulan_judgement",
    "clear_mulan_state",
    "get_mulan_status_text",
    "handle_mulan_reply",
    "parse_mulan_report_ids",
    "run_mulan_scheduler",
    "schedule_mulan_initial_check",
]
