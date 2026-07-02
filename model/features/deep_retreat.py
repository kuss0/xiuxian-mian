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
from ..action_guard import (
    clear_remote_block as clear_action_guard_remote_block,
    close_action as close_action_guard,
    note_remote_block as note_action_guard_remote_block,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, console_log, mono, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_display_name, get_identity_ids, get_send_as_tags, has_identity, state, use_identity
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
from . import workflow_log
from .tianxing import (
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    note_tianxing_retreat_force_exit_summary,
    normalize_tianxing_auto_config,
    normalize_tianxing_observation,
    normalize_tianxing_timeline_state,
    run_tianxing_timeline_scheduler,
)


DEEP_RETREAT_EMPTY_STATUS_RETRY_MIN_SEC = 2 * 60
DEEP_RETREAT_EMPTY_STATUS_RETRY_MAX_SEC = 5 * 60
DEEP_RETREAT_EMPTY_STATUS_RELAUNCH_MIN_SEC = 5
DEEP_RETREAT_EMPTY_STATUS_RELAUNCH_MAX_SEC = 15
DEEP_RETREAT_RUNNING_SUMMARY_EARLY_SEC = 10 * 60
DEEP_RETREAT_TIANXING_RETRY_MIN_SEC = 2 * 60
DEEP_RETREAT_TIANXING_RETRY_MAX_SEC = 5 * 60

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
    source_module="深度闭关",
    summary_trigger_command=CMD_DEEP_RETREAT_QUERY,
    summary_passive_triggers=("查看闭关", "1"),
    summary_passive_timeout_sec=120,
    summary_due_delay_min_sec=5 * 60,
    summary_due_delay_max_sec=15 * 60,
    summary_active_query_grace_sec=30 * 60,
    summary_due_timeout_action="wait_passive",
    queued_launch_timeout_action="relaunch",
    summary_retry_min_sec=5 * 60,
    summary_retry_max_sec=10 * 60,
    timeout_relaunch_min_sec=DEEP_RETREAT_EMPTY_STATUS_RETRY_MIN_SEC,
    timeout_relaunch_max_sec=DEEP_RETREAT_EMPTY_STATUS_RETRY_MAX_SEC,
)
register_phaseful_spec(DEEP_RETREAT_SPEC)


def _record_deep_retreat_event(
    event,
    *,
    kind="changed",
    reason="",
    identity_id=0,
    use_current_identity=True,
    reply_to=None,
    detail="",
    matched_text="",
    decision="",
    include_recent=None,
):
    try:
        reply_msg_id = int(getattr(reply_to, "id", 0) or 0)
        phase = str(state.get("deep_retreat_phase") or "").strip()
        resolved_identity_id = int(identity_id or 0)
        if resolved_identity_id <= 0 and use_current_identity:
            resolved_identity_id = int(get_current_identity_id() or 0)
        parts = [str(event or "深闭事件").strip() or "深闭事件"]
        if phase:
            parts.append(f"phase={phase}")
        if reply_msg_id:
            parts.append(f"reply_msg_id={reply_msg_id}")
        if detail:
            parts.append(str(detail).strip())
        workflow_log.append_workflow_event(
            "deep_retreat",
            op_id=f"{resolved_identity_id}:{phase}" if resolved_identity_id and phase else "",
            step=phase,
            event=str(event or "深闭事件").strip() or "深闭事件",
            status=kind,
            identity_id=resolved_identity_id,
            reply_to_msg_id=reply_msg_id,
            family="deep_retreat",
            text=matched_text,
            decision=decision or str(event or "").strip(),
            detail={"reason": reason, "detail": detail},
            route_source="deep_retreat",
            state_after=phase,
        )
        from . import passive_inbox

        return passive_inbox.record_passive_inbox_event(
            kind,
            module="deep_retreat",
            identity_id=resolved_identity_id,
            reason=reason,
            summary="｜".join(part for part in parts if part),
            family="deep_retreat",
            reply_to_msg_id=reply_msg_id,
            route_source="deep_retreat",
            matched_text=matched_text,
            decision=decision or str(event or "").strip(),
            state_after=phase,
            include_recent=include_recent,
        )
    except Exception:
        return False


def _is_deep_retreat_short_cd_text(text):
    raw_text = str(text or "")
    return "灵气尚未平复" in raw_text and "无法立即再次闭关" in raw_text and has_wait_time(raw_text)


def _is_deep_retreat_summary_text(text):
    compact_text = RE_WHITESPACE.sub("", text or "")
    return (
        "天道感应：检测到" in compact_text
        and "功成圆满，神魂正在归位" in compact_text
    ) or (
        "深度闭关总结" in compact_text
        and "本次结算时长" in compact_text
        and "神魂吐纳次数" in compact_text
    )


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


def _note_deep_retreat_remote_block(now, block_until, reason, kind):
    note_action_guard_remote_block(
        "deep_retreat",
        send_as_id=get_current_identity_id(),
        block_until=block_until,
        reason=reason,
        kind=kind,
        now=now,
        command=CMD_DEEP_RETREAT,
    )


def _clear_deep_retreat_remote_block_after_summary(now):
    send_as_id = get_current_identity_id()
    changed = clear_action_guard_remote_block(
        "deep_retreat",
        send_as_id=send_as_id,
        reason="deep_retreat_summary_finalized",
        now=now,
    )
    changed = close_action_guard(
        "deep_retreat",
        send_as_id=send_as_id,
        reason="deep_retreat_summary_finalized",
        now=now,
    ) or changed
    if changed:
        save_state()
    return changed


def begin_deep_retreat_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC, *, confirmed=False):
    begin_post_summary_wait(DEEP_RETREAT_SPEC, now, delay=delay, confirmed=confirmed)
    if confirmed:
        _clear_deep_retreat_remote_block_after_summary(now)


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
            next_time = now + wait_sec + CD_BUFFER_SEC
            mark_deep_retreat_success(now, next_time)
            _note_deep_retreat_remote_block(now, next_time, "执行中/CD未到", "success")
            _record_deep_retreat_event(
                "闭关成功",
                reply_to=reply_to,
                detail=f"wait={wait_sec}s",
                matched_text=text,
                decision="success_schedule_summary",
            )
            await send_audit_log(f"🧘 深闭成功→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
            return True

        set_deep_retreat_phase("running")
        next_time = float(state.get("next_deep_retreat_time", 0) or 0) or now + DEEP_RETREAT_CD + CD_BUFFER_SEC
        _note_deep_retreat_remote_block(now, next_time, "游戏提示深度闭关执行中", "running")
        _record_deep_retreat_event(
            "闭关成功待状态查询",
            reply_to=reply_to,
            matched_text=text,
            decision="success_probe_status",
        )
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
    estimated_next_time = float(state.get("next_deep_retreat_time", 0) or 0)
    if estimated_next_time <= now + DEEP_RETREAT_RUNNING_SUMMARY_EARLY_SEC:
        estimated_next_time = None
    _record_deep_retreat_event(
        "已在闭关中",
        reply_to=reply_to,
        matched_text=text,
        decision="running_keep_estimate" if estimated_next_time else "running_default_estimate",
    )
    state["deep_retreat_probe_pending"] = False
    if estimated_next_time:
        mark_dirty()
    else:
        mark_deep_retreat_success(now)
        estimated_next_time = float(state.get("next_deep_retreat_time", 0) or 0)
    _note_deep_retreat_remote_block(now, estimated_next_time, "游戏提示深度闭关执行中", "running")
    return True


async def handle_deep_retreat_status_reply(text, now, reply_to, matched_family=None):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    stripped_orig_cmd = orig_cmd.strip()
    passive_summary_reply = (
        stripped_orig_cmd in {str(trigger or "").strip() for trigger in DEEP_RETREAT_SPEC.summary_passive_triggers}
        and state.get("deep_retreat_phase") in ("summary_due", "observing_summary", "waiting_summary", "running")
    )
    is_status_reply = matched_family == "deep_retreat" or CMD_DEEP_RETREAT_QUERY in orig_cmd or passive_summary_reply
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
        _note_deep_retreat_remote_block(now, state["next_deep_retreat_time"], "游戏提示短冷却", "cooldown")
        _record_deep_retreat_event(
            "短冷却",
            reply_to=reply_to,
            detail=f"wait={wait_sec}s",
            matched_text=text,
            decision="short_cd_rescheduled",
        )
        save_state()
        await update_deep_retreat_block_log_state(waiting=False, protect=False)
        await send_audit_log(f"⏳ 深闭短冷却→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if "预计还需" in text and "即可功成圆满" in text:
        wait_sec = parse_wait_time(text)
        if not has_wait_time(text):
            return False
        next_time = now + wait_sec + CD_BUFFER_SEC
        mark_deep_retreat_success(now, next_time)
        _note_deep_retreat_remote_block(now, next_time, "游戏提示剩余闭关时间", "running")
        _record_deep_retreat_event(
            "闭关状态确认",
            reply_to=reply_to,
            detail=f"remain={wait_sec}s",
            matched_text=text,
            decision="running_remaining_scheduled",
        )
        await send_audit_log(f"⏳ 深闭 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if "并未处于深度闭关" in text or "未处于深度闭关" in text:
        phase = state.get("deep_retreat_phase", "idle")
        is_due = float(state.get("next_deep_retreat_time", 0) or 0) <= now
        if phase in ("summary_due", "observing_summary", "waiting_summary", "running", "queued_launch") or (phase == "idle" and is_due):
            await delete_deep_retreat_summary_trigger_msg()
            delay = random.uniform(DEEP_RETREAT_EMPTY_STATUS_RELAUNCH_MIN_SEC, DEEP_RETREAT_EMPTY_STATUS_RELAUNCH_MAX_SEC)
            begin_deep_retreat_post_summary_wait(now, delay=delay, confirmed=True)
            _record_deep_retreat_event(
                "确认未处于深闭",
                reply_to=reply_to,
                detail=f"relaunch={int(delay)}s",
                matched_text=text,
                decision="not_running_relaunch_soon",
            )
            await update_deep_retreat_block_log_state(waiting=False, protect=False)
            await send_audit_log(f"🧘 已确认未处于深闭，{int(delay)}秒后排队发起深度闭关。")
            return True

    return False


def _is_deep_retreat_summary_candidate_phase(now):
    phase = state.get("deep_retreat_phase")
    next_time = float(state.get("next_deep_retreat_time", 0) or 0)
    due_while_running = phase == "running" and now > 0 and 0 < next_time <= now
    near_due_while_running = (
        phase == "running"
        and now > 0
        and next_time > now
        and next_time - now <= DEEP_RETREAT_RUNNING_SUMMARY_EARLY_SEC
    )
    explicit_tagged_running_summary = phase == "running"
    return phase in ("summary_due", "observing_summary", "waiting_summary") or due_while_running or near_due_while_running or explicit_tagged_running_summary


def _reply_context_identity(reply_context):
    try:
        identity_id = int((reply_context or {}).get("send_as_id") or 0)
    except (TypeError, ValueError):
        identity_id = 0
    return identity_id if identity_id > 0 and has_identity(identity_id) else 0


def match_deep_retreat_summary_identity(text, now=None, reply_context=None):
    compact_text = RE_WHITESPACE.sub("", text or "")
    if not _is_deep_retreat_summary_text(text):
        return None, []
    now = float(now or 0)
    has_explicit_at = "@" in compact_text

    reply_identity_id = _reply_context_identity(reply_context)
    if reply_identity_id:
        with use_identity(reply_identity_id):
            if state["deep_retreat_enabled"] and _is_deep_retreat_summary_candidate_phase(now):
                return reply_identity_id, [reply_identity_id]
        return None, []

    if not has_explicit_at:
        return None, []

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["deep_retreat_enabled"]:
                continue
            if not _is_deep_retreat_summary_candidate_phase(now):
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], matched_ids
    return None, matched_ids


def _match_deep_retreat_post_summary_identity(text, now=None, reply_context=None):
    compact_text = RE_WHITESPACE.sub("", text or "")
    if not _is_deep_retreat_summary_text(text):
        return 0
    now = float(now or 0)

    reply_identity_id = _reply_context_identity(reply_context)
    if reply_identity_id:
        with use_identity(reply_identity_id):
            if state["deep_retreat_enabled"] and state.get("deep_retreat_phase") == "post_summary_wait":
                return reply_identity_id
        return 0

    if "@" not in compact_text:
        return 0

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["deep_retreat_enabled"]:
                continue
            if state.get("deep_retreat_phase") != "post_summary_wait":
                continue
            next_time = float(state.get("next_deep_retreat_time", 0) or 0)
            if now > 0 and next_time > 0 and next_time < now:
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)

    return matched_ids[0] if len(matched_ids) == 1 else 0


async def handle_deep_retreat_summary_broadcast(text, now, event=None, reply_to=None, reply_context=None):
    if not _is_deep_retreat_summary_text(text):
        return

    target_id, matched_ids = match_deep_retreat_summary_identity(text, now=now, reply_context=reply_context)
    if target_id is None:
        archived_id = _match_deep_retreat_post_summary_identity(text, now=now, reply_context=reply_context)
        if archived_id:
            with use_identity(archived_id):
                _record_deep_retreat_event(
                    "闭关总结已归档",
                    kind="skipped",
                    reason="no_change",
                    identity_id=archived_id,
                    matched_text=text,
                    decision="summary_already_finalized",
                )
            return
        if len(matched_ids) > 1:
            names = ", ".join(mono(get_identity_display_name(identity_id)) for identity_id in matched_ids)
            _record_deep_retreat_event(
                "闭关总结跳过",
                kind="skipped",
                reason="deep_retreat_summary_ambiguous",
                identity_id=0,
                use_current_identity=False,
                detail=names,
                matched_text=text,
                decision="summary_ambiguous_skip",
            )
            await send_audit_log(f"🧘 闭关总结命中多个身份，已跳过：{names}", scope="global", limit=280)
        else:
            _record_deep_retreat_event(
                "闭关总结跳过",
                kind="skipped",
                reason="deep_retreat_summary_no_match",
                identity_id=0,
                use_current_identity=False,
                matched_text=text,
                decision="summary_no_match_skip",
                include_recent=False,
            )
        return

    with use_identity(target_id):
        _record_deep_retreat_event(
            "闭关总结确认",
            identity_id=target_id,
            matched_text=text,
            decision="summary_finalized",
        )
        await finalize_summary_broadcast(DEEP_RETREAT_SPEC, now)
        _clear_deep_retreat_remote_block_after_summary(now)
        if note_tianxing_retreat_force_exit_summary(text, now=now):
            save_state()


def _deep_retreat_tianxing_consume_due_at(now, config):
    config = normalize_tianxing_auto_config(config)
    lead_sec = int(config.get("route_prepare_lead_sec", 5 * 60) or 5 * 60)
    phase = str(state.get("deep_retreat_phase") or "idle")
    next_time = float(state.get("next_deep_retreat_time", 0) or 0)
    due_at = 0.0

    if phase == "running":
        due_at = next_time
    elif phase == "post_summary_wait":
        due_at = next_time
    elif phase == "summary_due":
        due_at = next_time
        if DEEP_RETREAT_SPEC.summary_due_timeout_action == "wait_passive":
            grace_sec = float(DEEP_RETREAT_SPEC.summary_active_query_grace_sec or 0)
            started_at = float(state.get("deep_retreat_summary_sent_at", 0) or 0)
            if grace_sec > 0 and started_at > 0:
                due_at = max(due_at, started_at + grace_sec)

    if due_at <= 0:
        return 0.0
    if float(now) < due_at - max(0, lead_sec):
        return 0.0
    return due_at


def _deep_retreat_launch_due_for_tianxing(now, config=None):
    config = normalize_tianxing_auto_config(config if config is not None else state.get("tianxing_auto_config"))
    if _deep_retreat_tianxing_consume_due_at(now, config) > 0:
        return True
    phase = str(state.get("deep_retreat_phase") or "idle")
    next_time = float(state.get("next_deep_retreat_time", 0) or 0)
    if next_time > float(now):
        return False
    if phase == "post_summary_wait":
        return True
    if phase != "summary_due":
        return False
    grace_sec = float(DEEP_RETREAT_SPEC.summary_active_query_grace_sec or 0)
    started_at = float(state.get("deep_retreat_summary_sent_at", 0) or 0)
    if grace_sec > 0 and started_at > 0 and float(now) - started_at < grace_sec:
        return False
    return DEEP_RETREAT_SPEC.summary_due_timeout_action == "wait_passive"


def _deep_retreat_tianxing_retreat_farm_block_until(now):
    if not state.get("tianxing_enabled"):
        return 0.0
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    farm = timeline.get("retreat_farm") or {}
    if not float(farm.get("started_at", 0) or 0):
        return 0.0
    phase = str(farm.get("phase") or "").strip()
    cooldown_until = float(farm.get("cooldown_until", 0) or 0)
    next_time = float(farm.get("next_time", 0) or 0)
    if phase in {"sent_waiting_reply", "calibrating", "need_heqi_exchange", "ready_to_use_heqi", "need_lingshi_donation", "send_blocked"}:
        return max(cooldown_until, next_time, float(now) + DEEP_RETREAT_TIANXING_RETRY_MIN_SEC)
    if (
        phase == "ready"
        and str(observed.get("current_prediction") or "").strip() == "闭关"
        and float(observed.get("current_prediction_until", 0) or 0) > float(now)
    ):
        return max(next_time, float(now) + DEEP_RETREAT_TIANXING_RETRY_MIN_SEC)
    if phase == "cooldown" and cooldown_until > float(now):
        return cooldown_until
    return 0.0


def _deep_retreat_tianxing_timeline_block_until(now):
    if not state.get("tianxing_enabled"):
        return 0.0
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    active_step = dict(timeline.get("active_step") or {})
    phase = str(timeline.get("phase") or "").strip()
    active_status = str(active_step.get("status") or "").strip()
    if phase in {"idle", "completed", "downstream_released", "state_confirmed", "blocked_replan", "prediction_conflict"} and active_status not in {
        "pending",
        "sending",
        "sent_waiting_ack",
        "ack_timeout",
    }:
        return 0.0
    if active_status in {"pending", "sending", "sent_waiting_ack", "ack_timeout"} or phase in {
        "waiting_send",
        "sending",
        "sent_waiting_ack",
        "ack_timeout",
        "calibrating",
        "phaseful_deferred",
    }:
        candidates = [
            float(active_step.get("ack_due_at", 0) or 0),
            float(active_step.get("calibration_due_at", 0) or 0),
            float(timeline.get("blocked_until", 0) or 0),
        ]
        future = [item for item in candidates if item > float(now)]
        if future:
            return max(future)
        return float(now) + DEEP_RETREAT_TIANXING_RETRY_MIN_SEC
    return 0.0


async def _run_deep_retreat_tianxing_gate(now):
    config = normalize_tianxing_auto_config(state.get("tianxing_auto_config"))
    if not _deep_retreat_launch_due_for_tianxing(now, config=config):
        return True
    due_at = _deep_retreat_tianxing_consume_due_at(now, config) or float(state.get("next_deep_retreat_time", 0) or now)
    phase = str(state.get("deep_retreat_phase") or "idle")
    timeline_block_until = _deep_retreat_tianxing_timeline_block_until(now)
    if timeline_block_until > now:
        if phase == "running" and due_at > now and timeline_block_until <= due_at:
            state["next_deep_retreat_time"] = due_at
        else:
            state["next_deep_retreat_time"] = timeline_block_until + CD_BUFFER_SEC
        save_state()
        return False
    retreat_farm_block_until = _deep_retreat_tianxing_retreat_farm_block_until(now)
    if retreat_farm_block_until > now:
        if phase == "running" and due_at > now and retreat_farm_block_until <= due_at:
            state["next_deep_retreat_time"] = due_at
        else:
            state["next_deep_retreat_time"] = retreat_farm_block_until + CD_BUFFER_SEC
        save_state()
        return False
    if not config.get("deep_retreat_consume_enabled"):
        return True
    consume_windows = build_tianxing_consume_window("闭关", now=now, due_at=max(due_at, now), config=config, reason="深度闭关")
    route_config = dict(config)
    route_config["timeline_enabled"] = bool(consume_windows) and bool(config.get("timeline_enabled"))
    preflight = build_tianxing_route_preflight_plan("闭关", reason="深度闭关", now=now, config=route_config)
    if preflight.get("route_allowed"):
        return True
    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        if phase == "running" and due_at > now and blocked_until <= due_at:
            state["next_deep_retreat_time"] = due_at
        else:
            state["next_deep_retreat_time"] = blocked_until + CD_BUFFER_SEC
        save_state()
        return False
    if preflight.get("timeline_required") and consume_windows:
        await run_tianxing_timeline_scheduler(now, windows=consume_windows, config=config)
        if phase == "running" and due_at > now:
            state["next_deep_retreat_time"] = due_at
        else:
            state["next_deep_retreat_time"] = float(now + random.uniform(DEEP_RETREAT_TIANXING_RETRY_MIN_SEC, DEEP_RETREAT_TIANXING_RETRY_MAX_SEC))
        save_state()
        return False
    if phase == "running" and due_at > now:
        state["next_deep_retreat_time"] = due_at
    else:
        state["next_deep_retreat_time"] = float(now + random.uniform(DEEP_RETREAT_TIANXING_RETRY_MIN_SEC, DEEP_RETREAT_TIANXING_RETRY_MAX_SEC))
    save_state()
    return False


async def run_deep_retreat_scheduler(now):
    if state.get("deep_retreat_phase") == "post_summary_wait":
        _clear_deep_retreat_remote_block_after_summary(now)
    if not await _run_deep_retreat_tianxing_gate(now):
        return
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
