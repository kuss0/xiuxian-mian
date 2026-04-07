import re
from datetime import datetime, timedelta

from ..config import GUANXING_NOTIFY_ADVANCE_SEC, GUANXING_SLOT_HOURS, GUANXING_TARGET_KEYWORDS, TZ_LOCAL
from ..persistence import save_state
from ..runtime import send_audit_log
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining


RE_GUANXING_PANEL = re.compile(r"【星盘显化】")
RE_GUANXING_EVOLUTION = re.compile(r"下一次天道演化将是\s*[:：]\s*【([^】]+)】")


def calc_guanxing_slot(now):
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


def _reset_guanxing_slot_state(slot_info):
    state["guanxing_slot_key"] = slot_info["slot_key"]
    state["guanxing_slot_start_at"] = float(slot_info["slot_start_at"])
    state["guanxing_slot_end_at"] = float(slot_info["slot_end_at"])
    state["next_guanxing_notify_time"] = float(slot_info["notify_at"])
    state["guanxing_seen_panel"] = False
    state["guanxing_matched_keyword"] = ""
    state["guanxing_matched_value"] = ""
    state["guanxing_last_evolution_value"] = ""
    state["guanxing_last_seen_at"] = 0


def restore_guanxing_runtime_state(now):
    slot_info = calc_guanxing_slot(now)
    if state.get("guanxing_slot_key") != slot_info["slot_key"]:
        _reset_guanxing_slot_state(slot_info)
        return slot_info, True

    changed = False
    if float(state.get("guanxing_slot_start_at", 0) or 0) != float(slot_info["slot_start_at"]):
        state["guanxing_slot_start_at"] = float(slot_info["slot_start_at"])
        changed = True
    if float(state.get("guanxing_slot_end_at", 0) or 0) != float(slot_info["slot_end_at"]):
        state["guanxing_slot_end_at"] = float(slot_info["slot_end_at"])
        changed = True
    if float(state.get("next_guanxing_notify_time", 0) or 0) != float(slot_info["notify_at"]):
        state["next_guanxing_notify_time"] = float(slot_info["notify_at"])
        changed = True
    return slot_info, changed


def _sync_guanxing_slot(now):
    slot_info, changed = restore_guanxing_runtime_state(now)
    if changed:
        save_state()
    return slot_info, changed


def _extract_guanxing_evolution_value(text):
    raw_text = str(text or "")
    if not RE_GUANXING_PANEL.search(raw_text):
        return ""
    match = RE_GUANXING_EVOLUTION.search(raw_text)
    return str(match.group(1) or "").strip() if match else ""


def _match_guanxing_keyword(evolution_value):
    raw_value = str(evolution_value or "").strip()
    for keyword in GUANXING_TARGET_KEYWORDS:
        if keyword in raw_value:
            return keyword
    return ""


def _format_slot_label(slot_start_at, slot_end_at):
    if slot_start_at <= 0 or slot_end_at <= 0:
        return "未设置"
    start_text = datetime.fromtimestamp(slot_start_at, TZ_LOCAL).strftime("%H:%M")
    end_text = datetime.fromtimestamp(slot_end_at, TZ_LOCAL).strftime("%H:%M")
    return f"{start_text}-{end_text}"


def get_guanxing_status_text():
    notify_at = float(state.get("next_guanxing_notify_time", 0) or 0)
    slot_start_at = float(state.get("guanxing_slot_start_at", 0) or 0)
    slot_end_at = float(state.get("guanxing_slot_end_at", 0) or 0)
    slot_label = _format_slot_label(slot_start_at, slot_end_at)
    matched_keyword = str(state.get("guanxing_matched_keyword") or "")
    matched_value = str(state.get("guanxing_matched_value") or "")
    last_evolution_value = str(state.get("guanxing_last_evolution_value") or "")
    last_seen_at = float(state.get("guanxing_last_seen_at", 0) or 0)
    current_slot_key = str(state.get("guanxing_slot_key") or "")
    last_notified_slot_key = str(state.get("guanxing_last_notified_slot_key") or "")

    if matched_keyword:
        result_text = f"命中 {matched_keyword}（{matched_value or '未记录内容'}）"
    elif last_evolution_value:
        result_text = f"非目标结果（{last_evolution_value}）"
    elif state.get("guanxing_seen_panel"):
        result_text = "已触发，未解析到目标天象"
    else:
        result_text = "当前时段未收到显化广播"

    return (
        "🌠 观星\n"
        f"- 当前时段：{slot_label}\n"
        f"- 收口时间：{fmt_abs_ts(notify_at)}（{fmt_remaining(notify_at)}）\n"
        f"- 已见显化：{'是' if state.get('guanxing_seen_panel') else '否'} ｜ 已收口：{'是' if current_slot_key and current_slot_key == last_notified_slot_key else '否'}\n"
        f"- 当前结果：{result_text}\n"
        f"- 最近显化：{fmt_abs_ts(last_seen_at)}"
    )


async def handle_guanxing_broadcast(text, now):
    if not state.get("guanxing_enabled"):
        return False

    raw_text = str(text or "")
    if not RE_GUANXING_PANEL.search(raw_text):
        return False

    _sync_guanxing_slot(now)
    state["guanxing_seen_panel"] = True
    state["guanxing_last_seen_at"] = float(now)

    evolution_value = _extract_guanxing_evolution_value(raw_text)
    if evolution_value:
        state["guanxing_last_evolution_value"] = evolution_value
        matched_keyword = _match_guanxing_keyword(evolution_value)
        if matched_keyword and not str(state.get("guanxing_matched_keyword") or ""):
            state["guanxing_matched_keyword"] = matched_keyword
            state["guanxing_matched_value"] = evolution_value

    save_state()
    return True


async def run_guanxing_scheduler(now):
    if not state.get("guanxing_enabled"):
        return

    slot_info, _ = _sync_guanxing_slot(now)
    notify_at = float(state.get("next_guanxing_notify_time", 0) or 0)
    if notify_at <= 0 or now < notify_at:
        return

    slot_key = str(slot_info.get("slot_key") or "")
    if slot_key and str(state.get("guanxing_last_notified_slot_key") or "") == slot_key:
        return

    slot_label = _format_slot_label(float(slot_info.get("slot_start_at", 0) or 0), float(slot_info.get("slot_end_at", 0) or 0))
    matched_keyword = str(state.get("guanxing_matched_keyword") or "")
    matched_value = str(state.get("guanxing_matched_value") or "")
    last_evolution_value = str(state.get("guanxing_last_evolution_value") or "")
    seen_panel = bool(state.get("guanxing_seen_panel"))

    if matched_keyword:
        await send_audit_log(f"🌠 当前时段观星命中：{matched_keyword}｜{matched_value}｜{slot_label}")
    elif not seen_panel:
        await send_audit_log(f"🌠 当前时段无人观星｜{slot_label}")
    elif last_evolution_value:
        await send_audit_log(f"🌠 当前时段观星非目标结果：{last_evolution_value}｜{slot_label}")
    else:
        await send_audit_log(f"🌠 当前时段观星已触发，但未解析到目标天象｜{slot_label}")

    state["guanxing_last_notified_slot_key"] = slot_key
    save_state()


__all__ = [
    "calc_guanxing_slot",
    "restore_guanxing_runtime_state",
    "get_guanxing_status_text",
    "handle_guanxing_broadcast",
    "run_guanxing_scheduler",
]
