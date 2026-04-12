import re
import time

from ..config import (
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    GUANXING_EXECUTE_ADVANCE_SEC,
    GUANXING_SHIFT_START_DELAY_SEC,
    GUANXING_SHIFT_TARGET,
    TZ_LOCAL,
)
from ..persistence import save_state
from ..runtime import send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_guanxing_round_state,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_label,
    get_send_as_profile,
    set_guanxing_round_state,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, fmt_slot_label, get_day_key
from .guanxing_monitor import calc_guanxing_monitor_slot

RE_GUANXING_PANEL = re.compile(r"【星盘显化】")
RE_GUANXING_FINISH_BROADCAST = re.compile(r"【天机阁快报\s*-\s*[^】]+】")
RE_GUANXING_FINISH_RESULT = re.compile(r"天机演化结果[:：]\s*([^！!\n]+)[！!]")
RE_EXTERNAL_SHIFT = re.compile(r"^\s*\.改换星移\s+@\S+\s*$")

ROUND_STAGE_IDLE = "idle"
ROUND_STAGE_WAITING_FIRST_SHIFT = "waiting_first_shift"
ROUND_STAGE_WAITING_EXTERNAL = "waiting_external"
ROUND_STAGE_WAITING_FINISH = "waiting_finish"
ROUND_STAGE_FINISHED = "finished"


def _build_round_state(slot_info):
    slot_end_at = float(slot_info.get("slot_end_at", 0) or 0)
    return {
        "slot_key": str(slot_info.get("slot_key") or ""),
        "slot_start_at": float(slot_info.get("slot_start_at", 0) or 0),
        "slot_end_at": slot_end_at,
        "stage": ROUND_STAGE_IDLE,
        "gate_keyword": "",
        "gate_value": "",
        "query_due_at": slot_end_at - GUANXING_EXECUTE_ADVANCE_SEC,
        "shift_due_at": slot_end_at + GUANXING_SHIFT_START_DELAY_SEC,
        "participant_ids": [],
        "panel_ready_ids": [],
        "next_shift_index": 0,
        "consumed_external_msg_ids": [],
        "finish_reason": "",
        "finished_at": 0,
    }


def _normalize_round_state(round_state, slot_info):
    slot_key = str(slot_info.get("slot_key") or "")
    default_state = _build_round_state(slot_info)
    current_state = round_state if isinstance(round_state, dict) else {}
    if str(current_state.get("slot_key") or "") != slot_key:
        return default_state

    normalized = dict(default_state)
    normalized.update(current_state)
    normalized["slot_key"] = slot_key
    normalized["slot_start_at"] = float(slot_info.get("slot_start_at", 0) or 0)
    normalized["slot_end_at"] = float(slot_info.get("slot_end_at", 0) or 0)
    normalized["query_due_at"] = float(default_state["query_due_at"])
    normalized["shift_due_at"] = float(default_state["shift_due_at"])
    normalized["stage"] = str(normalized.get("stage") or ROUND_STAGE_IDLE)
    normalized["gate_keyword"] = str(normalized.get("gate_keyword") or "")
    normalized["gate_value"] = str(normalized.get("gate_value") or "")
    normalized["participant_ids"] = [
        int(identity_id)
        for identity_id in normalized.get("participant_ids") or []
        if int(identity_id or 0) > 0
    ]
    normalized["panel_ready_ids"] = [
        int(identity_id)
        for identity_id in normalized.get("panel_ready_ids") or []
        if int(identity_id or 0) > 0
    ]
    normalized["next_shift_index"] = max(0, int(normalized.get("next_shift_index", 0) or 0))
    normalized["consumed_external_msg_ids"] = [
        int(msg_id)
        for msg_id in normalized.get("consumed_external_msg_ids") or []
        if int(msg_id or 0) > 0
    ][-50:]
    normalized["finish_reason"] = str(normalized.get("finish_reason") or "")
    normalized["finished_at"] = float(normalized.get("finished_at", 0) or 0)
    return normalized


def _set_round_state(round_state):
    return set_guanxing_round_state(round_state)


def _get_round_slot_info(round_state):
    return {
        "slot_key": str((round_state or {}).get("slot_key") or ""),
        "slot_start_at": float((round_state or {}).get("slot_start_at", 0) or 0),
        "slot_end_at": float((round_state or {}).get("slot_end_at", 0) or 0),
    }


def _should_preserve_existing_round(round_state, current_slot_info):
    current_state = round_state if isinstance(round_state, dict) else {}
    if not current_state:
        return False
    current_slot_key = str(current_state.get("slot_key") or "")
    target_slot_key = str(current_slot_info.get("slot_key") or "")
    if not current_slot_key or current_slot_key == target_slot_key:
        return False

    stage = str(current_state.get("stage") or ROUND_STAGE_IDLE)
    if stage not in {ROUND_STAGE_WAITING_FIRST_SHIFT, ROUND_STAGE_WAITING_EXTERNAL, ROUND_STAGE_WAITING_FINISH}:
        return False

    participant_ids = [
        int(identity_id)
        for identity_id in current_state.get("participant_ids") or []
        if int(identity_id or 0) > 0
    ]
    if not participant_ids:
        return False

    slot_end_at = float(current_state.get("slot_end_at", 0) or 0)
    next_slot_start_at = float(current_slot_info.get("slot_start_at", 0) or 0)
    if slot_end_at <= 0 or next_slot_start_at <= 0:
        return False

    return next_slot_start_at <= slot_end_at


def clear_guanxing_identity_runtime(send_as_id=None, *, panel_slot_key=""):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return False
    changed = False
    with use_identity(send_as_id):
        runtime_defaults = {
            "guanxing_last_query_msg_id": 0,
            "guanxing_last_panel_msg_id": 0,
            "guanxing_panel_slot_key": str(panel_slot_key or ""),
            "guanxing_last_panel_seen_at": 0,
            "guanxing_last_shift_msg_id": 0,
            "guanxing_last_shift_slot_key": "",
            "guanxing_last_shift_target": "",
            "guanxing_last_error": "",
        }
        for key, value in runtime_defaults.items():
            if state.get(key) != value:
                state[key] = value
                changed = True
    return changed


def _reset_identity_guanxing_runtime_for_new_slot():
    changed = False
    for identity_id in get_identity_ids():
        changed = clear_guanxing_identity_runtime(identity_id) or changed
    return changed


def restore_guanxing_round_runtime(now):
    slot_info = calc_guanxing_monitor_slot(now)
    current_round_state = get_guanxing_round_state()
    preserve_existing_round = _should_preserve_existing_round(current_round_state, slot_info)
    effective_slot_info = _get_round_slot_info(current_round_state) if preserve_existing_round else slot_info
    normalized_round_state = _normalize_round_state(current_round_state, effective_slot_info)
    changed = normalized_round_state != current_round_state
    slot_changed = (
        not preserve_existing_round
        and str(current_round_state.get("slot_key") or "") != str(slot_info.get("slot_key") or "")
    )
    if slot_changed:
        changed = _reset_identity_guanxing_runtime_for_new_slot() or changed
    if changed:
        _set_round_state(normalized_round_state)
    return normalized_round_state, changed


def sync_guanxing_round_from_monitor(now):
    round_state, changed = restore_guanxing_round_runtime(now)
    current_slot_info = calc_guanxing_monitor_slot(now)
    if str(round_state.get("slot_key") or "") != str(current_slot_info.get("slot_key") or ""):
        if changed:
            _set_round_state(round_state)
            save_state()
        return round_state, changed

    gate_keyword = str(state.get("guanxing_monitor_matched_keyword") or "")
    gate_value = str(state.get("guanxing_monitor_matched_value") or "")
    if round_state.get("gate_keyword") != gate_keyword:
        round_state["gate_keyword"] = gate_keyword
        changed = True
    if round_state.get("gate_value") != gate_value:
        round_state["gate_value"] = gate_value
        changed = True
    if changed:
        _set_round_state(round_state)
        save_state()
    return round_state, changed


def _get_identity_label(send_as_id):
    return get_send_as_label(send_as_id) or str(send_as_id)


def _get_participant_ids(now):
    participant_ids = []
    day_key = get_day_key(now)
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        profile = get_send_as_profile(identity_id)
        if str(profile.get("sect_name") or "").strip() != "星宫":
            continue
        identity_state = get_identity_state(identity_id)
        if not identity_state.get("guanxing_enabled"):
            continue
        if str(identity_state.get("last_guanxing_done_day") or "") == day_key:
            continue
        participant_ids.append(int(identity_id))
    return participant_ids


async def _send_guanxing_query(identity_id, slot_key, now):
    with use_identity(identity_id):
        state["guanxing_last_query_msg_id"] = 0
        state["guanxing_last_panel_msg_id"] = 0
        state["guanxing_panel_slot_key"] = str(slot_key or "")
        state["guanxing_last_panel_seen_at"] = 0
        state["guanxing_last_shift_msg_id"] = 0
        state["guanxing_last_shift_slot_key"] = ""
        state["guanxing_last_shift_target"] = ""
        state["guanxing_last_error"] = ""
    msg = await send_game_command(CMD_GUANXING, track=False, send_as_id=identity_id)
    with use_identity(identity_id):
        if msg:
            state["guanxing_last_query_msg_id"] = int(getattr(msg, "id", 0) or 0)
            state["guanxing_last_error"] = ""
        else:
            state["guanxing_last_error"] = "发送 .观星 失败"
    return msg


async def _send_guanxing_shift(identity_id, slot_key):
    with use_identity(identity_id):
        if not state.get("guanxing_enabled"):
            state["guanxing_last_error"] = "模块已关闭"
            return False, "模块已关闭"
        panel_slot_key = str(state.get("guanxing_panel_slot_key") or "")
        panel_msg_id = int(state.get("guanxing_last_panel_msg_id", 0) or 0)
        if panel_slot_key != slot_key or panel_msg_id <= 0:
            state["guanxing_last_error"] = "未收到当前时段 panel"
            return False, "未收到当前时段 panel"

    shift_command = f"{CMD_GUANXING_SHIFT} {GUANXING_SHIFT_TARGET}"
    msg = await send_game_command(shift_command, track=False, reply_to=panel_msg_id, send_as_id=identity_id)
    with use_identity(identity_id):
        if msg:
            state["guanxing_last_shift_msg_id"] = int(getattr(msg, "id", 0) or 0)
            state["guanxing_last_shift_slot_key"] = slot_key
            state["guanxing_last_shift_target"] = GUANXING_SHIFT_TARGET
            state["guanxing_last_error"] = ""
            return True, ""
        state["guanxing_last_error"] = "发送 .改换星移 失败"
        return False, "发送 .改换星移 失败"


def _append_panel_ready(round_state, identity_id):
    panel_ready_ids = [int(item) for item in round_state.get("panel_ready_ids") or [] if int(item or 0) > 0]
    if identity_id not in panel_ready_ids:
        panel_ready_ids.append(identity_id)
    round_state["panel_ready_ids"] = panel_ready_ids
    return round_state


async def _send_next_shift(round_state, now, *, reason_text):
    participant_ids = [int(identity_id) for identity_id in round_state.get("participant_ids") or [] if int(identity_id or 0) > 0]
    slot_key = str(round_state.get("slot_key") or "")
    next_shift_index = max(0, int(round_state.get("next_shift_index", 0) or 0))

    while next_shift_index < len(participant_ids):
        identity_id = participant_ids[next_shift_index]
        sent, error_text = await _send_guanxing_shift(identity_id, slot_key)
        if sent:
            round_state["next_shift_index"] = next_shift_index + 1
            round_state["stage"] = (
                ROUND_STAGE_WAITING_EXTERNAL
                if round_state["next_shift_index"] < len(participant_ids)
                else ROUND_STAGE_WAITING_FINISH
            )
            _set_round_state(round_state)
            save_state()
            await send_audit_log(
                f"🌠 观星执行：{_get_identity_label(identity_id)}｜{reason_text}｜目标 {GUANXING_SHIFT_TARGET}",
                scope="identity",
                send_as_id=identity_id,
                limit=300,
            )
            return True

        round_state["next_shift_index"] = next_shift_index + 1
        _set_round_state(round_state)
        save_state()
        await send_audit_log(
            f"🌠 观星跳过：{_get_identity_label(identity_id)}｜{error_text}",
            scope="identity",
            send_as_id=identity_id,
            limit=300,
        )
        next_shift_index += 1

    round_state["stage"] = ROUND_STAGE_WAITING_FINISH
    _set_round_state(round_state)
    save_state()
    return False


def _get_round_stage_text(round_state, now):
    stage = str(round_state.get("stage") or ROUND_STAGE_IDLE)
    gate_keyword = str(round_state.get("gate_keyword") or "")
    participant_ids = [int(identity_id) for identity_id in round_state.get("participant_ids") or [] if int(identity_id or 0) > 0]
    query_due_at = float(round_state.get("query_due_at", 0) or 0)
    finish_reason = str(round_state.get("finish_reason") or "")

    if stage == ROUND_STAGE_FINISHED:
        return f"已结束（{finish_reason or '本轮收口'}）"
    if not gate_keyword:
        return "未命中关键字，当前时段不执行"
    if not participant_ids and now < query_due_at:
        return "已命中关键字，等待 T-3 分钟发送 .观星"
    if not participant_ids:
        return "已命中关键字，等待可参与身份"
    if stage == ROUND_STAGE_WAITING_FIRST_SHIFT:
        return "已发送 .观星，等待首个身份起手"
    if stage == ROUND_STAGE_WAITING_EXTERNAL:
        return "等待外部 .改换星移 触发下一位身份"
    if stage == ROUND_STAGE_WAITING_FINISH:
        return "本轮已无后续身份，等待天机阁快报"
    return "待命"


def _get_guanxing_gate_text(round_state):
    gate_keyword = str(round_state.get("gate_keyword") or "")
    if not gate_keyword:
        return "当前未命中"
    gate_value = str(round_state.get("gate_value") or "")
    return f"已命中 {gate_keyword}（{gate_value or '未记录内容'}）"


def _get_guanxing_slot_text(round_state):
    return fmt_slot_label(
        float(round_state.get("slot_start_at", 0) or 0),
        float(round_state.get("slot_end_at", 0) or 0),
    )


def get_guanxing_round_summary_text():
    now = time.time()
    round_state, _changed = sync_guanxing_round_from_monitor(now)
    gate_keyword = str(round_state.get("gate_keyword") or "")
    if not gate_keyword:
        return ""
    query_due_at = float(round_state.get("query_due_at", 0) or 0)
    if query_due_at > 0 and now < query_due_at:
        return ""
    stage_text = _get_round_stage_text(round_state, now)
    return f"命中 {gate_keyword}｜{stage_text}"


def get_guanxing_status_text():
    now = time.time()
    round_state, _changed = sync_guanxing_round_from_monitor(now)
    current_identity_id = get_current_identity_id()
    round_participant_ids = [int(identity_id) for identity_id in round_state.get("participant_ids") or [] if int(identity_id or 0) > 0]
    panel_ready_ids = [int(identity_id) for identity_id in round_state.get("panel_ready_ids") or [] if int(identity_id or 0) > 0]
    next_shift_index = max(0, int(round_state.get("next_shift_index", 0) or 0))
    next_identity_text = "无"
    if next_shift_index < len(round_participant_ids):
        next_identity_text = _get_identity_label(round_participant_ids[next_shift_index])

    identity_state = get_identity_state(current_identity_id)
    panel_ready = (
        str(identity_state.get("guanxing_panel_slot_key") or "") == str(round_state.get("slot_key") or "")
        and int(identity_state.get("guanxing_last_panel_msg_id", 0) or 0) > 0
    )
    shift_done = (
        str(identity_state.get("guanxing_last_shift_slot_key") or "") == str(round_state.get("slot_key") or "")
        and int(identity_state.get("guanxing_last_shift_msg_id", 0) or 0) > 0
    )
    today_participated = str(identity_state.get("last_guanxing_done_day") or "") == get_day_key(now)
    gate_keyword = str(round_state.get("gate_keyword") or "")
    finish_reason = str(round_state.get("finish_reason") or "")
    stage = str(round_state.get("stage") or ROUND_STAGE_IDLE)
    query_due_at = float(round_state.get("query_due_at", 0) or 0)
    shift_due_at = float(round_state.get("shift_due_at", 0) or 0)

    lines = [
        "🌠 观星",
        f"- 当前时段：{_get_guanxing_slot_text(round_state)}",
        f"- 轮次状态：{_get_round_stage_text(round_state, now)}",
    ]

    lines.append(f"- Gate：{_get_guanxing_gate_text(round_state)}")
    if gate_keyword:
        if not round_participant_ids and now < query_due_at:
            lines.append(f"- 查询时间：{fmt_abs_ts(query_due_at)}（{fmt_remaining(query_due_at)}）")
        else:
            lines.append(
                f"- 参与身份：{len(round_participant_ids)} ｜ Panel 就绪：{len(panel_ready_ids)} ｜ 下一位：{next_identity_text}"
            )
            if stage == ROUND_STAGE_WAITING_FIRST_SHIFT:
                lines.append(f"- 首发时间：{fmt_abs_ts(shift_due_at)}（{fmt_remaining(shift_due_at)}）")
            lines.append(f"- 今日参与：{'已参与' if today_participated else '未参与'}")
            lines.append(f"- 本身份 panel：{'已就绪' if panel_ready else '未就绪'}")
            if stage in {ROUND_STAGE_WAITING_EXTERNAL, ROUND_STAGE_WAITING_FINISH, ROUND_STAGE_FINISHED} or shift_done:
                lines.append(f"- 本身份改换星移：{'已发送' if shift_done else '未发送'}")
            if panel_ready:
                lines.append(f"- 最近 panel：{fmt_abs_ts(identity_state.get('guanxing_last_panel_seen_at', 0))}")

    if identity_state.get("guanxing_last_error"):
        lines.append(f"- 最近错误：{identity_state.get('guanxing_last_error')}")
    if finish_reason:
        lines.append(f"- 结束原因：{finish_reason}")

    return "\n".join(lines)


async def handle_guanxing_query_reply(text, now, reply_to, current_msg_id, matched_family=None):
    if matched_family != "guanxing_query":
        return False
    if not RE_GUANXING_PANEL.search(str(text or "")):
        return False

    round_state, _changed = sync_guanxing_round_from_monitor(now)
    slot_key = str(round_state.get("slot_key") or "")
    current_identity_id = get_current_identity_id()
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    current_msg_id = int(current_msg_id or 0)

    with use_identity(current_identity_id):
        if int(state.get("guanxing_last_query_msg_id", 0) or 0) != reply_to_msg_id:
            return False
        state["guanxing_last_panel_msg_id"] = current_msg_id
        state["guanxing_panel_slot_key"] = slot_key
        state["guanxing_last_panel_seen_at"] = float(now)
        state["last_guanxing_done_day"] = get_day_key(now)
        state["guanxing_last_error"] = ""

    _append_panel_ready(round_state, current_identity_id)
    _set_round_state(round_state)
    save_state()
    return True


async def handle_guanxing_external_shift_command(text, now, event):
    raw_text = str(text or "").strip()
    if not RE_EXTERNAL_SHIFT.match(raw_text):
        return False

    round_state, _changed = sync_guanxing_round_from_monitor(now)
    participant_ids = [int(identity_id) for identity_id in round_state.get("participant_ids") or [] if int(identity_id or 0) > 0]
    if not participant_ids:
        return False
    if str(round_state.get("stage") or "") != ROUND_STAGE_WAITING_EXTERNAL:
        return False

    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if sender_id in participant_ids:
        return False

    event_msg_id = int(getattr(event, "id", 0) or 0)
    consumed_ids = [int(msg_id) for msg_id in round_state.get("consumed_external_msg_ids") or [] if int(msg_id or 0) > 0]
    if event_msg_id in consumed_ids:
        return False
    consumed_ids.append(event_msg_id)
    round_state["consumed_external_msg_ids"] = consumed_ids[-50:]
    _set_round_state(round_state)
    save_state()
    return await _send_next_shift(round_state, now, reason_text="外部触发继续")


async def handle_guanxing_finish_broadcast(text, now):
    raw_text = str(text or "")
    if not RE_GUANXING_FINISH_BROADCAST.search(raw_text):
        return False
    result_match = RE_GUANXING_FINISH_RESULT.search(raw_text)
    if not result_match:
        return False

    round_state, _changed = sync_guanxing_round_from_monitor(now)
    stage = str(round_state.get("stage") or ROUND_STAGE_IDLE)
    has_round = bool(round_state.get("gate_keyword") or round_state.get("participant_ids") or stage != ROUND_STAGE_IDLE)
    if not has_round:
        return False

    finish_result = str(result_match.group(1) or "").strip()
    round_state["stage"] = ROUND_STAGE_FINISHED
    round_state["finish_reason"] = f"天机演化结果：{finish_result}" if finish_result else "天机演化结果已公布"
    round_state["finished_at"] = float(now)
    _set_round_state(round_state)
    save_state()
    await send_audit_log(f"🌠 观星收口：{round_state['finish_reason']}", scope="global", limit=300)
    return True


async def run_guanxing_scheduler(now):
    round_state, _changed = sync_guanxing_round_from_monitor(now)
    gate_keyword = str(round_state.get("gate_keyword") or "")
    if not gate_keyword:
        return
    if str(round_state.get("stage") or "") == ROUND_STAGE_FINISHED:
        return

    participant_ids = [int(identity_id) for identity_id in round_state.get("participant_ids") or [] if int(identity_id or 0) > 0]
    if not participant_ids and now >= float(round_state.get("query_due_at", 0) or 0):
        participant_ids = _get_participant_ids(now)
        if not participant_ids:
            return
        sent_count = 0
        slot_key = str(round_state.get("slot_key") or "")
        for identity_id in participant_ids:
            msg = await _send_guanxing_query(identity_id, slot_key, now)
            if msg:
                sent_count += 1
        round_state["participant_ids"] = participant_ids
        round_state["panel_ready_ids"] = []
        round_state["next_shift_index"] = 0
        round_state["stage"] = ROUND_STAGE_WAITING_FIRST_SHIFT
        _set_round_state(round_state)
        save_state()
        await send_audit_log(
            f"🌠 观星已发起：命中 {gate_keyword}｜参与身份 {len(participant_ids)}｜发送成功 {sent_count}",
            scope="global",
            limit=300,
        )
        return

    if participant_ids and str(round_state.get("stage") or "") == ROUND_STAGE_WAITING_FIRST_SHIFT:
        if now < float(round_state.get("shift_due_at", 0) or 0):
            return
        await _send_next_shift(round_state, now, reason_text="首发")


__all__ = [
    "clear_guanxing_identity_runtime",
    "get_guanxing_round_summary_text",
    "get_guanxing_status_text",
    "handle_guanxing_external_shift_command",
    "handle_guanxing_finish_broadcast",
    "handle_guanxing_query_reply",
    "restore_guanxing_round_runtime",
    "run_guanxing_scheduler",
    "sync_guanxing_round_from_monitor",
]
