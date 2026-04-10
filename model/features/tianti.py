import random
import re

from ..config import (
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    RETRY_MAX_SEC,
    TIANTI_CD_RANDOM_MAX_SEC,
    TIANTI_CD_RANDOM_MIN_SEC,
    TIANTI_RANK_CD_SECONDS,
)
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_tianti_rank_choice, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time

RE_TIANTI_PANEL = re.compile(r"【凌霄云阶】")
RE_TIANTI_PROGRESS = re.compile(r"当前进度[:：]\s*(\d+)\s*/\s*(\d+)\s*阶")
RE_TIANTI_CYCLE = re.compile(r"已完成周天[:：]\s*(\d+)\s*轮")
RE_TIANTI_GANGFENG = re.compile(r"罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)\s*层")
RE_TIANTI_COOLDOWN = re.compile(r"登阶冷却[:：]\s*(.+)")
RE_TIANTI_WENXIN = re.compile(r"问心状态[:：]\s*(.+)")
RE_TIANTI_WENXIN_PANEL = re.compile(r"【问心台回响】")
RE_TIANTI_WENXIN_GAIN_CONTRIB = re.compile(r"你因此获得了\s*(\d+)\s*点宗门贡献")
RE_TIANTI_WENXIN_EXTRA_GANGFENG = re.compile(r"九天罡风顺势入体，你的【罡风淬体】额外提升了\s*(\d+)\s*层")
RE_TIANTI_WENXIN_FAIL = re.compile(r"你今日已在问心台前静坐过一次，道台不会再回应你。")
RE_TIANTI_CLIMB_COST = re.compile(r"你消耗了\s*(\d+)\s*点修为")
RE_TIANTI_CLIMB_GAIN = re.compile(r"本次获得\s*(\d+)\s*点修为[、,，]\s*(\d+)\s*点宗门贡献")
RE_TIANTI_CLIMB_RESULT = re.compile(r"当前云阶进度[:：]\s*(\d+)\s*/\s*(\d+)[，,]\s*罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)")


def _set_tianti_next_status_time(next_time, *, persist=False):
    state["next_tianti_status_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_wenxin_time(next_time, *, persist=False):
    state["next_tianti_wenxin_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_climb_time(next_time, *, persist=False):
    state["next_tianti_climb_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _schedule_tianti_status_retry(now, *, persist=False):
    next_time = float(now) + RETRY_MAX_SEC
    _set_tianti_next_status_time(next_time, persist=persist)
    return next_time


def _schedule_tianti_wenxin_retry(now, *, persist=False):
    next_time = float(now) + 86400 + random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    _set_tianti_next_wenxin_time(next_time, persist=persist)
    return next_time


def _apply_tianti_wenxin_result(raw_text, now, reply_to):
    handled = False
    if RE_TIANTI_WENXIN_FAIL.search(raw_text):
        state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["tianti_wenxin_status"] = "今日已问心"
        _schedule_tianti_wenxin_retry(now, persist=False)
        return True

    if not RE_TIANTI_WENXIN_PANEL.search(raw_text):
        return False

    state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    state["tianti_wenxin_status"] = "今日已问心，下次登天阶奖励提升"
    handled = True

    extra_gangfeng_match = RE_TIANTI_WENXIN_EXTRA_GANGFENG.search(raw_text)
    if extra_gangfeng_match:
        extra_level = int(extra_gangfeng_match.group(1) or 0)
        if extra_level > 0:
            state["tianti_gangfeng_level"] = int(state.get("tianti_gangfeng_level", 0) or 0) + extra_level
            handled = True

    _schedule_tianti_wenxin_retry(now, persist=False)
    return handled


def _schedule_tianti_climb_retry(now, rank_choice=None, *, persist=False):
    rank_choice = (rank_choice or get_tianti_rank_choice()).strip()
    cd_seconds = int(TIANTI_RANK_CD_SECONDS.get(rank_choice, TIANTI_RANK_CD_SECONDS["普通"]))
    random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    next_time = float(now) + cd_seconds + random_delay
    _set_tianti_next_climb_time(next_time, persist=persist)
    state["tianti_cooldown_text"] = fmt_time_after(cd_seconds + random_delay)
    if persist:
        save_state()
    else:
        mark_dirty()
    return next_time


def _has_tianti_status_snapshot():
    return any(
        value not in {None, "", 0, "未记录"}
        for value in (
            state.get("tianti_progress_current"),
            state.get("tianti_cycle_count"),
            state.get("tianti_gangfeng_level"),
            state.get("tianti_cooldown_text"),
            state.get("tianti_wenxin_status"),
        )
    )


def _tianti_status_sync_due(now):
    next_status_time = float(state.get("next_tianti_status_time", 0) or 0)
    if next_status_time > 0 and now >= next_status_time:
        return True
    if not _has_tianti_status_snapshot():
        return True
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    return next_climb_time > 0 and now >= next_climb_time


async def sync_tianti_status(send_as_id):
    send_as_id = int(send_as_id)
    msg = await send_game_command(CMD_TIANTI_STATUS, track=False, send_as_id=send_as_id)
    if not msg:
        return False, "天阶状态同步发送失败"
    return True, f"已发送天阶状态同步指令[{send_as_id}]，等待回复"


def _is_tianti_reply(text, reply_to, matched_family=None):
    if matched_family in {"tianti_status", "tianti_wenxin", "tianti_climb"}:
        return True
    raw_text = str(text or "")
    if RE_TIANTI_PANEL.search(raw_text):
        return True
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    return any(command in orig_cmd for command in {CMD_TIANTI_STATUS, CMD_TIANTI_WENXIN, CMD_TIANTI_CLIMB})


def _parse_tianti_panel(text):
    raw_text = str(text or "")
    if not RE_TIANTI_PANEL.search(raw_text):
        return None

    payload = {}
    progress_match = RE_TIANTI_PROGRESS.search(raw_text)
    if progress_match:
        payload["progress_current"] = int(progress_match.group(1) or 0)
        payload["progress_total"] = int(progress_match.group(2) or 0)
    cycle_match = RE_TIANTI_CYCLE.search(raw_text)
    if cycle_match:
        payload["cycle_count"] = int(cycle_match.group(1) or 0)
    gangfeng_match = RE_TIANTI_GANGFENG.search(raw_text)
    if gangfeng_match:
        payload["gangfeng_level"] = int(gangfeng_match.group(1) or 0)
        payload["gangfeng_total"] = int(gangfeng_match.group(2) or 0)
    cooldown_match = RE_TIANTI_COOLDOWN.search(raw_text)
    if cooldown_match:
        payload["cooldown_text"] = str(cooldown_match.group(1) or "").strip()
    wenxin_match = RE_TIANTI_WENXIN.search(raw_text)
    if wenxin_match:
        payload["wenxin_status"] = str(wenxin_match.group(1) or "").strip()
    return payload or None


def _apply_tianti_panel_payload(payload):
    if not isinstance(payload, dict):
        return False
    changed = False
    mapping = {
        "progress_current": "tianti_progress_current",
        "progress_total": "tianti_progress_total",
        "cycle_count": "tianti_cycle_count",
        "gangfeng_level": "tianti_gangfeng_level",
        "gangfeng_total": "tianti_gangfeng_total",
        "cooldown_text": "tianti_cooldown_text",
        "wenxin_status": "tianti_wenxin_status",
    }
    for payload_key, state_key in mapping.items():
        if payload_key not in payload:
            continue
        value = payload[payload_key]
        if state.get(state_key) != value:
            state[state_key] = value
            changed = True
    return changed


def get_tianti_status_text():
    lines = [
        "☁️ 登天阶",
        f"- 档位：{get_tianti_rank_choice()}",
        f"- 当前进度：{int(state.get('tianti_progress_current', 0) or 0)} / {int(state.get('tianti_progress_total', 12) or 12)} 阶",
        f"- 已完成周天：{int(state.get('tianti_cycle_count', 0) or 0)} 轮",
        f"- 罡风淬体：{int(state.get('tianti_gangfeng_level', 0) or 0)} / {int(state.get('tianti_gangfeng_total', 12) or 12)} 层",
        f"- 登阶冷却：{state.get('tianti_cooldown_text') or '未记录'}",
        f"- 问心状态：{state.get('tianti_wenxin_status') or '未记录'}",
        f"- 下次查状态：{fmt_abs_ts(float(state.get('next_tianti_status_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_status_time', 0) or 0))}）",
        f"- 下次问心：{fmt_abs_ts(float(state.get('next_tianti_wenxin_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_wenxin_time', 0) or 0))}）",
        f"- 下次登阶：{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_climb_time', 0) or 0))}）",
    ]
    last_gain_xiuwei = int(state.get("tianti_last_gain_xiuwei", 0) or 0)
    last_gain_contrib = int(state.get("tianti_last_gain_contrib", 0) or 0)
    last_cost_xiuwei = int(state.get("tianti_last_cost_xiuwei", 0) or 0)
    if last_gain_xiuwei > 0 or last_gain_contrib > 0 or last_cost_xiuwei > 0:
        lines.append(
            f"- 最近登阶：消耗 {last_cost_xiuwei} 修为｜获得 {last_gain_xiuwei} 修为 / {last_gain_contrib} 贡献"
        )
    if state.get("tianti_last_error"):
        lines.append(f"- 最近异常：{state.get('tianti_last_error')}")
    return "\n".join(lines)


async def handle_tianti_reply(text, now, reply_to, matched_family=None):
    if not state.get("tianti_enabled"):
        return False
    if not _is_tianti_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "")
    handled = False

    panel_payload = _parse_tianti_panel(raw_text)
    if panel_payload:
        if _apply_tianti_panel_payload(panel_payload):
            handled = True
        if matched_family == "tianti_status":
            state["tianti_last_status_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            state["tianti_status_reply_to_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            _set_tianti_next_status_time(0, persist=False)
            handled = True

    if matched_family == "tianti_wenxin":
        handled = _apply_tianti_wenxin_result(raw_text, now, reply_to) or handled

    climb_cost_match = RE_TIANTI_CLIMB_COST.search(raw_text)
    climb_gain_match = RE_TIANTI_CLIMB_GAIN.search(raw_text)
    climb_result_match = RE_TIANTI_CLIMB_RESULT.search(raw_text)
    if climb_cost_match and climb_gain_match and climb_result_match:
        state["tianti_last_climb_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["tianti_last_cost_xiuwei"] = int(climb_cost_match.group(1) or 0)
        state["tianti_last_gain_xiuwei"] = int(climb_gain_match.group(1) or 0)
        state["tianti_last_gain_contrib"] = int(climb_gain_match.group(2) or 0)
        state["tianti_progress_current"] = int(climb_result_match.group(1) or 0)
        state["tianti_progress_total"] = int(climb_result_match.group(2) or 0)
        state["tianti_gangfeng_level"] = int(climb_result_match.group(3) or 0)
        state["tianti_gangfeng_total"] = int(climb_result_match.group(4) or 0)
        state["tianti_last_error"] = ""
        _schedule_tianti_climb_retry(now, persist=False)
        handled = True

    if has_wait_time(raw_text) and matched_family == "tianti_climb":
        wait_sec = parse_wait_time(raw_text)
        if wait_sec > 0:
            state["tianti_last_climb_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
            _set_tianti_next_climb_time(now + wait_sec, persist=False)
            state["tianti_cooldown_text"] = fmt_time_after(wait_sec)
            handled = True

    if handled:
        state["tianti_last_error"] = ""
        save_state()
        return True

    return False


async def run_tianti_scheduler(now):
    if not state.get("tianti_enabled"):
        return

    if _tianti_status_sync_due(now):
        msg = await send_game_command(CMD_TIANTI_STATUS)
        if not msg:
            _schedule_tianti_status_retry(now, persist=True)
            state["tianti_last_error"] = "天阶状态发送失败"
            await send_audit_log("❌ 登天阶状态发送失败，稍后重试。")
            return
        state["tianti_status_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
        _set_tianti_next_status_time(0, persist=True)
        console_log("☁️ 查询天阶状态")
        return

    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if next_climb_time > 0 and now >= next_climb_time:
        msg = await send_game_command(CMD_TIANTI_CLIMB)
        if not msg:
            _set_tianti_next_climb_time(now + RETRY_MAX_SEC, persist=True)
            state["tianti_last_error"] = "登天阶发送失败"
            await send_audit_log("❌ 登天阶发送失败，稍后重试。")
            return
        state["tianti_last_climb_msg_id"] = int(getattr(msg, "id", 0) or 0)
        _schedule_tianti_climb_retry(now, persist=True)
        console_log(f"☁️ 执行登天阶→{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}")


__all__ = [
    "get_tianti_status_text",
    "handle_tianti_reply",
    "run_tianti_scheduler",
    "sync_tianti_status",
]
