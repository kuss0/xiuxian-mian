import asyncio
import random

from ..config import (
    CD_BUFFER_SEC,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    DEEP_RETREAT_CD,
    LAUNCHING_TIMEOUT_SEC,
    MODULE_PROTECT_SEC,
    POST_SUMMARY_WAIT_SEC,
    RE_WHITESPACE,
    SUMMARY_TIMEOUT_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, console_log, mono, send_audit_log, send_game_command
from ..state import get_identity_display_name, get_identity_ids, get_send_as_tags, state, use_identity
from ..timing import fmt_time_after, has_wait_time, parse_wait_time
from ._phaseful import (
    PhasefulSpec,
    begin_post_summary_wait,
    begin_summary_wait,
    clear_summary_flags,
    delete_summary_trigger_msg,
    finalize_summary_broadcast,
    get_block_reason,
    get_phase_text,
    get_status_detail_text,
    mark_success,
    register_phaseful_spec,
    run_phaseful_scheduler,
    set_phase,
    update_block_log_state,
)


DEEP_RETREAT_SPEC = PhasefulSpec(
    enabled_key="deep_retreat_enabled",
    phase_key="deep_retreat_phase",
    next_time_key="next_deep_retreat_time",
    last_command_key="last_deep_retreat_command_time",
    probe_pending_key="deep_retreat_probe_pending",
    summary_sent_at_key="deep_retreat_summary_sent_at",
    last_summary_msg_id_key="last_deep_retreat_summary_msg_id",
    waiting_logged_key="deep_retreat_waiting_logged",
    protect_logged_key="deep_retreat_protect_logged",
    cd_sec=DEEP_RETREAT_CD,
    protect_sec=MODULE_PROTECT_SEC,
    launching_timeout_sec=LAUNCHING_TIMEOUT_SEC,
    post_summary_wait_sec=POST_SUMMARY_WAIT_SEC,
    summary_timeout_sec=SUMMARY_TIMEOUT_SEC,
    title="🧘 深度闭关",
    summary_pending_label="闭关总结待触发",
    block_disabled="模块已关闭",
    block_waiting="等待闭关总结",
    block_post_wait="总结后30秒缓冲中",
    block_launching="闭关指令已发出，等待回复",
    block_running="深度闭关执行中",
    block_protect="30秒保护中",
    phase_waiting="等待闭关总结",
    phase_post_wait="总结后缓冲中",
    phase_launching="闭关中",
    phase_running="闭关中",
    phase_idle_cd="CD中",
    phase_idle_protect="30秒保护中",
    phase_idle_ready="待闭关",
    waiting_on_log="🧘 深度闭关时间已到，但当前仍在等待闭关总结，暂不执行新的深度闭关。",
    waiting_off_log="🧘 等待闭关总结状态已结束。",
    protect_on_log="🧘 深度闭关时间已到，但当前处于 30 秒保护中，暂不重复执行。",
    protect_off_log="🧘 深度闭关 30 秒保护状态已结束。",
    launching_timeout_audit="🧘 launching 超时，已回退。",
    waiting_anomaly_audit="🧘 闭关等待异常，已解卡",
    waiting_timeout_audit="🧘 闭关总结超时",
    post_wait_console="🧘 闭关缓冲结束，继续深闭。",
    running_due_console="🧘 深闭时间到",
    cd_due_console="🧘 深闭 CD 到",
    summary_received_console="🧘 收到闭关总结，30 秒后继续。",
    summary_trigger_command=CMD_DEEP_RETREAT_QUERY,
    summary_passive_triggers=("查看闭关", "1"),
    summary_passive_timeout_sec=120,
    summary_due_delay_min_sec=5 * 60,
    summary_due_delay_max_sec=15 * 60,
    summary_retry_min_sec=5 * 60,
    summary_retry_max_sec=10 * 60,
)
register_phaseful_spec(DEEP_RETREAT_SPEC)

DEEP_RETREAT_EMPTY_STATUS_RETRY_MIN_SEC = 2 * 60
DEEP_RETREAT_EMPTY_STATUS_RETRY_MAX_SEC = 5 * 60


def _is_deep_retreat_short_cd_text(text):
    raw_text = str(text or "")
    return "灵气尚未平复" in raw_text and "无法立即再次闭关" in raw_text and has_wait_time(raw_text)


def set_deep_retreat_phase(phase):
    set_phase(DEEP_RETREAT_SPEC, phase)


def get_deep_retreat_block_reason(now=None):
    return get_block_reason(DEEP_RETREAT_SPEC, now)


async def update_deep_retreat_block_log_state(waiting=None, protect=None):
    await update_block_log_state(DEEP_RETREAT_SPEC, waiting=waiting, protect=protect)


def get_deep_retreat_phase_text(phase=None, now=None):
    return get_phase_text(DEEP_RETREAT_SPEC, phase=phase, now=now)


def get_deep_retreat_status_detail_text():
    return get_status_detail_text(DEEP_RETREAT_SPEC)


def mark_deep_retreat_success(now, next_time=None):
    mark_success(DEEP_RETREAT_SPEC, now, next_time=next_time)
    save_state()


def clear_deep_retreat_summary_flags():
    clear_summary_flags(DEEP_RETREAT_SPEC)


def begin_deep_retreat_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC):
    begin_post_summary_wait(DEEP_RETREAT_SPEC, now, delay=delay)


def begin_deep_retreat_summary_wait(now):
    begin_summary_wait(DEEP_RETREAT_SPEC, now)


async def delete_deep_retreat_summary_trigger_msg():
    await delete_summary_trigger_msg(DEEP_RETREAT_SPEC)


async def schedule_deep_retreat_status_probe(delay=None, allowed_phases=("launching",)):
    if delay is None:
        delay = random.uniform(5, 10)

    async def delayed_status():
        await asyncio.sleep(delay)
        if state.get("deep_retreat_phase") not in tuple(allowed_phases or ("launching",)):
            return
        await send_game_command(CMD_DEEP_RETREAT_QUERY, track=False, priority="chain")

    _fire_and_forget(delayed_status())
    console_log(f"🧘 深闭执行中，{delay:.1f}s 后查状态。")


async def handle_deep_retreat_success_reply(text, now, reply_to, matched_family=None):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "deep_retreat" and CMD_DEEP_RETREAT not in orig_cmd:
        return False

    if "你已进入深度闭关状态" in text and "神魂将自行吐纳" in text:
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            mark_deep_retreat_success(now, now + wait_sec + CD_BUFFER_SEC)
            await send_audit_log(f"🧘 深闭成功→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
            return True

        set_deep_retreat_phase("running")
        if state["deep_retreat_probe_pending"]:
            mark_dirty()
            return True
        state["deep_retreat_probe_pending"] = True
        mark_dirty()
        await schedule_deep_retreat_status_probe()
        return True

    return False


async def handle_deep_retreat_running_reply(text, now, reply_to, matched_family=None):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "deep_retreat" and CMD_DEEP_RETREAT not in orig_cmd:
        return False

    already_in_retreat_hit = "你已在深度闭关之中" in text
    if not already_in_retreat_hit:
        return False

    set_deep_retreat_phase("running")
    if state["deep_retreat_probe_pending"]:
        mark_dirty()
        return True

    state["deep_retreat_probe_pending"] = True
    mark_dirty()
    await schedule_deep_retreat_status_probe(random.uniform(10, 15), allowed_phases=("running", "launching"))
    return True


async def handle_deep_retreat_status_reply(text, now, reply_to, matched_family=None):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_status_reply = matched_family == "deep_retreat" or CMD_DEEP_RETREAT_QUERY in orig_cmd
    is_retreat_cmd_status_like = (
        matched_family == "deep_retreat"
        or (CMD_DEEP_RETREAT in orig_cmd and any(k in text for k in ["预计还需", "尚未恢复", "尚未平复", "冷却", "等待", "不足", "休息", "功成圆满"]))
    )
    if not (is_status_reply or is_retreat_cmd_status_like):
        return False

    if _is_deep_retreat_short_cd_text(text):
        wait_sec = parse_wait_time(text)
        clear_deep_retreat_summary_flags()
        set_deep_retreat_phase("idle")
        state["deep_retreat_probe_pending"] = False
        state["last_deep_retreat_command_time"] = now
        state["next_deep_retreat_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await update_deep_retreat_block_log_state(waiting=False, protect=False)
        await send_audit_log(f"⏳ 深闭短冷却→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if "预计还需" in text and "即可功成圆满" in text:
        wait_sec = parse_wait_time(text)
        if not has_wait_time(text):
            return False
        mark_deep_retreat_success(now, now + wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 深闭 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if "并未处于深度闭关" in text or "未处于深度闭关" in text:
        phase = state.get("deep_retreat_phase", "idle")
        is_due = float(state.get("next_deep_retreat_time", 0) or 0) <= now
        if phase in ("summary_due", "observing_summary", "waiting_summary", "running") or (phase == "idle" and is_due):
            await delete_deep_retreat_summary_trigger_msg()
            delay = random.uniform(DEEP_RETREAT_EMPTY_STATUS_RETRY_MIN_SEC, DEEP_RETREAT_EMPTY_STATUS_RETRY_MAX_SEC)
            begin_deep_retreat_post_summary_wait(now, delay=delay)
            await update_deep_retreat_block_log_state(waiting=False, protect=False)
            await send_audit_log(f"🧘 已确认未处于深闭，{int(delay / 60)}分钟后排队发起深度闭关。")
            return True

    return False


def match_deep_retreat_summary_identity(text, now=None):
    compact_text = RE_WHITESPACE.sub("", text or "")
    summary_kw_hit = (
        ("天道感应：检测到" in compact_text and "功成圆满，神魂正在归位" in compact_text)
        or ("深度闭关总结" in compact_text and "本次结算时长" in compact_text and "神魂吐纳次数" in compact_text)
    )
    if not summary_kw_hit:
        return None, []
    now = float(now or 0)
    has_explicit_at = "@" in compact_text

    matched_ids = []
    fallback_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["deep_retreat_enabled"]:
                continue
            phase = state.get("deep_retreat_phase")
            due_while_running = (
                phase == "running"
                and now > 0
                and 0 < float(state.get("next_deep_retreat_time", 0) or 0) <= now
            )
            if phase not in ("summary_due", "observing_summary", "waiting_summary") and not due_while_running:
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)
                elif not has_explicit_at:
                    fallback_ids.append(identity_id)
            elif not has_explicit_at:
                fallback_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], matched_ids
    if not matched_ids and len(fallback_ids) == 1:
        return fallback_ids[0], fallback_ids
    if not matched_ids:
        matched_ids = fallback_ids
    return None, matched_ids


async def handle_deep_retreat_summary_broadcast(text, now):
    target_id, matched_ids = match_deep_retreat_summary_identity(text, now=now)
    if target_id is None:
        if len(matched_ids) > 1:
            names = ", ".join(mono(get_identity_display_name(identity_id)) for identity_id in matched_ids)
            await send_audit_log(f"🧘 闭关总结命中多个身份，已跳过：{names}", scope="global", limit=280)
        return

    with use_identity(target_id):
        await finalize_summary_broadcast(DEEP_RETREAT_SPEC, now)


async def run_deep_retreat_scheduler(now):
    await run_phaseful_scheduler(
        DEEP_RETREAT_SPEC,
        now,
        launch_command=CMD_DEEP_RETREAT,
        schedule_probe=schedule_deep_retreat_status_probe,
    )


__all__ = [
    "begin_deep_retreat_post_summary_wait",
    "begin_deep_retreat_summary_wait",
    "clear_deep_retreat_summary_flags",
    "delete_deep_retreat_summary_trigger_msg",
    "get_deep_retreat_block_reason",
    "get_deep_retreat_phase_text",
    "get_deep_retreat_status_detail_text",
    "handle_deep_retreat_running_reply",
    "handle_deep_retreat_status_reply",
    "handle_deep_retreat_success_reply",
    "handle_deep_retreat_summary_broadcast",
    "mark_deep_retreat_success",
    "match_deep_retreat_summary_identity",
    "run_deep_retreat_scheduler",
    "schedule_deep_retreat_status_probe",
    "set_deep_retreat_phase",
    "update_deep_retreat_block_log_state",
]
