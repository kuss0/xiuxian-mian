import re
from datetime import datetime, timedelta

from ..config import (
    GUANXING_MONITOR_JUDGE_DELAY_SEC,
    GUANXING_NOTIFY_ADVANCE_SEC,
    GUANXING_SLOT_HOURS,
    TZ_LOCAL,
)
from ..persistence import save_state
from ..runtime import send_audit_log
from ..state import get_guanxing_monitor_targets, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_slot_label


RE_GUANXING_PANEL = re.compile(r"【星盘显化】")
RE_GUANXING_EVOLUTION = re.compile(r"下一次天道演化将是\s*[:：]\s*【([^】]+)】")


def calc_guanxing_monitor_slot(now):
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    slot_hour = (local_now.hour // GUANXING_SLOT_HOURS) * GUANXING_SLOT_HOURS
    slot_start = local_now.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
    slot_end = slot_start + timedelta(hours=GUANXING_SLOT_HOURS)
    slot_key = slot_start.strftime("%Y-%m-%d-%H")
    notify_at = slot_end - timedelta(seconds=GUANXING_NOTIFY_ADVANCE_SEC)
    return {
        "slot_key": slot_key,
        "slot_start_at": slot_start.timestamp(),
        "slot_end_at": slot_end.timestamp(),
        "notify_at": notify_at.timestamp(),
    }


def _reset_guanxing_monitor_slot_state(slot_info):
    state["guanxing_monitor_slot_key"] = slot_info["slot_key"]
    state["guanxing_monitor_slot_start_at"] = float(slot_info["slot_start_at"])
    state["guanxing_monitor_slot_end_at"] = float(slot_info["slot_end_at"])
    state["next_guanxing_monitor_notify_time"] = float(slot_info["notify_at"])
    state["guanxing_monitor_seen_panel"] = False
    state["guanxing_monitor_matched_keyword"] = ""
    state["guanxing_monitor_matched_value"] = ""
    state["guanxing_monitor_last_evolution_value"] = ""
    state["guanxing_monitor_last_seen_at"] = 0


def restore_guanxing_monitor_runtime_state(now):
    slot_info = calc_guanxing_monitor_slot(now)
    if state.get("guanxing_monitor_slot_key") != slot_info["slot_key"]:
        _reset_guanxing_monitor_slot_state(slot_info)
        return slot_info, True

    changed = False
    if float(state.get("guanxing_monitor_slot_start_at", 0) or 0) != float(slot_info["slot_start_at"]):
        state["guanxing_monitor_slot_start_at"] = float(slot_info["slot_start_at"])
        changed = True
    if float(state.get("guanxing_monitor_slot_end_at", 0) or 0) != float(slot_info["slot_end_at"]):
        state["guanxing_monitor_slot_end_at"] = float(slot_info["slot_end_at"])
        changed = True
    if float(state.get("next_guanxing_monitor_notify_time", 0) or 0) != float(slot_info["notify_at"]):
        state["next_guanxing_monitor_notify_time"] = float(slot_info["notify_at"])
        changed = True
    return slot_info, changed


def _sync_guanxing_monitor_slot(now):
    slot_info, changed = restore_guanxing_monitor_runtime_state(now)
    if changed:
        save_state()
    return slot_info, changed


def _is_guanxing_monitor_judge_window_open(now, slot_info=None):
    current_slot_info = slot_info or calc_guanxing_monitor_slot(now)
    slot_start_at = float(current_slot_info.get("slot_start_at", 0) or 0)
    if slot_start_at <= 0:
        return False
    return now >= slot_start_at + GUANXING_MONITOR_JUDGE_DELAY_SEC


def _extract_guanxing_monitor_evolution_value(text):
    raw_text = str(text or "")
    if not RE_GUANXING_PANEL.search(raw_text):
        return ""
    match = RE_GUANXING_EVOLUTION.search(raw_text)
    return str(match.group(1) or "").strip() if match else ""


def _match_guanxing_monitor_keyword(evolution_value):
    raw_value = str(evolution_value or "").strip()
    for keyword in get_guanxing_monitor_targets():
        if keyword in raw_value:
            return keyword
    return ""


def _get_guanxing_monitor_result_text(now=None):
    matched_keyword = str(state.get("guanxing_monitor_matched_keyword") or "")
    matched_value = str(state.get("guanxing_monitor_matched_value") or "")
    last_evolution_value = str(state.get("guanxing_monitor_last_evolution_value") or "")

    if not state.get("guanxing_monitor_enabled"):
        return "已关闭"
    if now is None:
        now = datetime.now(TZ_LOCAL).timestamp()
    slot_info = {
        "slot_start_at": float(state.get("guanxing_monitor_slot_start_at", 0) or 0),
        "slot_end_at": float(state.get("guanxing_monitor_slot_end_at", 0) or 0),
    }
    if not _is_guanxing_monitor_judge_window_open(now, slot_info):
        return "本轮前10分钟内不判断"
    if matched_keyword:
        return f"命中 {matched_keyword}（{matched_value or '未记录内容'}）"
    if last_evolution_value:
        return f"非目标结果（{last_evolution_value}）"
    if state.get("guanxing_monitor_seen_panel"):
        return "已触发，未解析到目标天象"
    return "当前时段未收到显化广播"


def get_guanxing_monitor_summary_text():
    return _get_guanxing_monitor_result_text()


def get_guanxing_monitor_status_text():
    now = datetime.now(TZ_LOCAL).timestamp()
    notify_at = float(state.get("next_guanxing_monitor_notify_time", 0) or 0)
    slot_start_at = float(state.get("guanxing_monitor_slot_start_at", 0) or 0)
    slot_end_at = float(state.get("guanxing_monitor_slot_end_at", 0) or 0)
    slot_label = fmt_slot_label(slot_start_at, slot_end_at)
    last_seen_at = float(state.get("guanxing_monitor_last_seen_at", 0) or 0)
    current_slot_key = str(state.get("guanxing_monitor_slot_key") or "")
    last_notified_slot_key = str(state.get("guanxing_monitor_last_notified_slot_key") or "")
    lines = [
        "🌠 观星监控（全局）",
        f"- 当前时段：{slot_label}",
        f"- 收口时间：{fmt_abs_ts(notify_at)}（{fmt_remaining(notify_at)}）",
        f"- 已启用：{'是' if state.get('guanxing_monitor_enabled') else '否'}",
        f"- 已见显化：{'是' if state.get('guanxing_monitor_seen_panel') else '否'} ｜ 已收口：{'是' if current_slot_key and current_slot_key == last_notified_slot_key else '否'}",
        f"- 当前结果：{_get_guanxing_monitor_result_text(now)}",
        f"- 最近显化：{fmt_abs_ts(last_seen_at)}",
    ]
    return "\n".join(lines)


async def handle_guanxing_monitor_broadcast(text, now):
    if not state.get("guanxing_monitor_enabled"):
        return False

    raw_text = str(text or "")
    if not RE_GUANXING_PANEL.search(raw_text):
        return False

    slot_info, _changed = _sync_guanxing_monitor_slot(now)
    if not _is_guanxing_monitor_judge_window_open(now, slot_info):
        return True

    state["guanxing_monitor_seen_panel"] = True
    state["guanxing_monitor_last_seen_at"] = float(now)

    evolution_value = _extract_guanxing_monitor_evolution_value(raw_text)
    if evolution_value:
        state["guanxing_monitor_last_evolution_value"] = evolution_value
        matched_keyword = _match_guanxing_monitor_keyword(evolution_value)
        if matched_keyword and not str(state.get("guanxing_monitor_matched_keyword") or ""):
            state["guanxing_monitor_matched_keyword"] = matched_keyword
            state["guanxing_monitor_matched_value"] = evolution_value

    save_state()
    return True


async def run_guanxing_monitor_scheduler(now):
    if not state.get("guanxing_monitor_enabled"):
        return

    slot_info, _ = _sync_guanxing_monitor_slot(now)
    notify_at = float(state.get("next_guanxing_monitor_notify_time", 0) or 0)
    if notify_at <= 0 or now < notify_at:
        return

    slot_key = str(slot_info.get("slot_key") or "")
    if slot_key and str(state.get("guanxing_monitor_last_notified_slot_key") or "") == slot_key:
        return

    slot_label = fmt_slot_label(float(slot_info.get("slot_start_at", 0) or 0), float(slot_info.get("slot_end_at", 0) or 0))
    matched_keyword = str(state.get("guanxing_monitor_matched_keyword") or "")
    matched_value = str(state.get("guanxing_monitor_matched_value") or "")
    last_evolution_value = str(state.get("guanxing_monitor_last_evolution_value") or "")
    seen_panel = bool(state.get("guanxing_monitor_seen_panel"))

    if matched_keyword:
        await send_audit_log(f"🌠 观星监控命中：{matched_keyword}｜{matched_value}｜{slot_label}")
    elif not seen_panel:
        await send_audit_log(f"🌠 当前时段无人触发观星监控｜{slot_label}")
    elif last_evolution_value:
        await send_audit_log(f"🌠 观星监控非目标：{last_evolution_value}｜{slot_label}")
    else:
        await send_audit_log(f"🌠 观星监控已触发：未解析到目标天象｜{slot_label}")

    state["guanxing_monitor_last_notified_slot_key"] = slot_key
    save_state()


__all__ = [
    "calc_guanxing_monitor_slot",
    "restore_guanxing_monitor_runtime_state",
    "get_guanxing_monitor_summary_text",
    "get_guanxing_monitor_status_text",
    "handle_guanxing_monitor_broadcast",
    "run_guanxing_monitor_scheduler",
]
