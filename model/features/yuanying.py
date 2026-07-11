import asyncio
import random
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_YUANYING,
    CMD_YUANYING_SECT_RETREAT,
    CMD_YUANYING_STATUS,
    LAUNCHING_TIMEOUT_SEC,
    POST_SUMMARY_WAIT_SEC,
    RE_WHITESPACE,
    RETRY_MAX_SEC,
    SUMMARY_TIMEOUT_SEC,
    YUANYING_CD,
    YUANYING_PROTECT_SEC,
)
from ..action_guard import note_remote_block as note_action_guard_remote_block
from ..identity_levels import parse_yuanying_level_text, update_identity_level_record
from ..persistence import mark_dirty, save_state
from ..runtime import PHASEFUL_PASSIVE_TRIGGER_TEXT, _fire_and_forget, classify_game_send_block, console_log, mono, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_display_name, get_identity_ids, get_send_as_profile, get_send_as_tags, has_identity, is_cave_public_auto_enabled, state, use_identity
from ..timing import fmt_time_after, has_wait_time, parse_wait_time
from ._phaseful import (
    PhasefulSpec,
    begin_queued_launch,
    begin_post_summary_wait,
    begin_summary_wait,
    clear_summary_flags,
    delete_summary_trigger_msg,
    finalize_summary_broadcast,
    get_block_reason,
    get_phase_text,
    get_status_detail_text,
    mark_launch_command_sent,
    mark_success,
    register_phaseful_spec,
    run_phaseful_scheduler,
    set_phase,
    _recover_phaseful_sent_from_message_log,
    update_block_log_state,
)


YUANYING_SPEC = PhasefulSpec(
    enabled_key="yuanying_enabled",
    phase_key="yuanying_phase",
    next_time_key="next_yuanying_time",
    last_command_key="last_yuanying_command_time",
    probe_pending_key="yuanying_probe_pending",
    summary_sent_at_key="yuanying_summary_sent_at",
    last_summary_msg_id_key="last_yuanying_summary_msg_id",
    waiting_logged_key="yuanying_waiting_logged",
    protect_logged_key="yuanying_protect_logged",
    cd_sec=YUANYING_CD,
    protect_sec=YUANYING_PROTECT_SEC,
    launching_timeout_sec=LAUNCHING_TIMEOUT_SEC,
    post_summary_wait_sec=POST_SUMMARY_WAIT_SEC,
    summary_timeout_sec=SUMMARY_TIMEOUT_SEC,
    title="👶 元婴",
    summary_pending_label="归窍总结待触发",
    block_disabled="模块已关闭",
    block_waiting="等待归窍总结",
    block_post_wait="归窍后短缓冲中",
    block_launching="出窍指令已发出，等待回复",
    block_running="元婴执行中",
    block_protect="30秒保护中",
    phase_waiting="等待归窍总结",
    phase_post_wait="总结后缓冲中",
    phase_launching="出窍中",
    phase_running="云游中",
    phase_idle_cd="CD中",
    phase_idle_protect="30秒保护中",
    phase_idle_ready="待出窍",
    waiting_on_log="👶 元婴时间已到，但当前仍在等待归窍总结，暂不执行元婴出窍。",
    waiting_off_log="👶 等待归窍总结状态已结束。",
    protect_on_log="👶 元婴时间已到，但当前处于 30 秒保护中，暂不重复执行。",
    protect_off_log="👶 元婴 30 秒保护状态已结束。",
    launching_timeout_audit="👶 launching 超时，已回退。",
    waiting_anomaly_audit="👶 归窍等待异常，已解卡",
    waiting_timeout_audit="👶 归窍总结超时",
    post_wait_console="👶 归窍缓冲结束，继续元婴。",
    running_due_console="👶 元婴归来时间到",
    cd_due_console="👶 元婴 CD 到",
    summary_received_console="👶 收到归窍总结，短缓冲后继续。",
    source_module="元婴",
    summary_trigger_command=CMD_YUANYING_STATUS,
    summary_passive_trigger_command=PHASEFUL_PASSIVE_TRIGGER_TEXT,
    summary_passive_triggers=("元婴状态", PHASEFUL_PASSIVE_TRIGGER_TEXT),
    summary_passive_timeout_sec=45,
    summary_due_delay_min_sec=30,
    summary_due_delay_max_sec=90,
    summary_active_query_grace_sec=5 * 60,
    summary_due_timeout_action="wait_passive",
    queued_launch_timeout_action="relaunch",
    summary_observe_sec=45,
    summary_retry_min_sec=45,
    summary_retry_max_sec=90,
    ignore_summary_finalize_while_running_until_due_sec=10 * 60,
)
register_phaseful_spec(YUANYING_SPEC)

YUANYING_SECT_NAME = "元婴宗"
YUANYING_RUNNING_SUMMARY_EARLY_SEC = 10 * 60


def get_yuanying_launch_command(send_as_id=None):
    profile = get_send_as_profile(send_as_id or get_current_identity_id()) or {}
    sect_name = str(profile.get("sect_name") or "").strip()
    return CMD_YUANYING_SECT_RETREAT if sect_name == YUANYING_SECT_NAME else CMD_YUANYING


def _is_yuanying_launch_command(command):
    raw = str(command or "").strip()
    return raw in {CMD_YUANYING, CMD_YUANYING_SECT_RETREAT}


def set_yuanying_phase(phase):
    set_phase(YUANYING_SPEC, phase)


def get_yuanying_block_reason(now=None):
    return get_block_reason(YUANYING_SPEC, now)


async def update_yuanying_block_log_state(waiting=None, protect=None):
    await update_block_log_state(YUANYING_SPEC, waiting=waiting, protect=protect)


def get_yuanying_phase_text(phase=None, now=None):
    return get_phase_text(YUANYING_SPEC, phase=phase, now=now)


def get_yuanying_status_detail_text():
    return get_status_detail_text(YUANYING_SPEC)


def mark_yuanying_success(now, next_time=None):
    mark_success(YUANYING_SPEC, now, next_time=next_time)
    save_state()


def clear_yuanying_summary_flags():
    clear_summary_flags(YUANYING_SPEC)


def _note_yuanying_remote_block(now, block_until, reason, kind):
    note_action_guard_remote_block(
        "yuanying_launch",
        send_as_id=get_current_identity_id(),
        block_until=block_until,
        reason=reason,
        kind=kind,
        now=now,
        command=get_yuanying_launch_command(),
    )


def begin_yuanying_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC, *, confirmed=False):
    begin_post_summary_wait(YUANYING_SPEC, now, delay=delay, confirmed=confirmed)


def begin_yuanying_summary_wait(now):
    begin_summary_wait(YUANYING_SPEC, now)


async def delete_yuanying_summary_trigger_msg():
    await delete_summary_trigger_msg(YUANYING_SPEC)


async def schedule_yuanying_status_probe(delay=None):
    if delay is None:
        delay = random.uniform(5, 10)

    async def delayed_status():
        await asyncio.sleep(delay)
        # 如果已经出窍成功（phase=running），不再多余查询
        if state.get("yuanying_phase") not in ("launching",):
            return
        await send_game_command(CMD_YUANYING_STATUS, track=False, priority="chain")

    _fire_and_forget(delayed_status())
    console_log(f"👶 元婴执行中，{delay:.1f}s 后查状态。")


async def handle_yuanying_success_reply(text, now, reply_to, matched_family=None):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "yuanying" and not _is_yuanying_launch_command(orig_cmd):
        return False

    if "你心念一动" in text and "元婴化作一道流光飞出" in text:
        wait_sec = parse_wait_time(text)
        cd_sec = wait_sec if wait_sec > 0 else YUANYING_CD
        next_time = now + cd_sec + CD_BUFFER_SEC
        mark_yuanying_success(now, next_time)
        _note_yuanying_remote_block(now, next_time, "执行中/CD未到", "success")
        target_time = fmt_time_after(cd_sec + CD_BUFFER_SEC)
        await send_audit_log(f"👶 元婴成功→{target_time}")
        return True

    return False


async def handle_yuanying_running_reply(text, now, reply_to, matched_family=None):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "yuanying" and not _is_yuanying_launch_command(orig_cmd):
        return False

    probe_hit = (
        "你的元婴正在执行" in text
        and "任务" in text
        and "元婴归窍" in text
    )
    if not probe_hit:
        return False

    estimated_next_time = float(state.get("next_yuanying_time", 0) or 0)
    if estimated_next_time <= now + YUANYING_RUNNING_SUMMARY_EARLY_SEC:
        mark_yuanying_success(now)
    else:
        set_yuanying_phase("running")
        clear_yuanying_summary_flags()
    _note_yuanying_remote_block(now, float(state.get("next_yuanying_time", 0) or 0), "游戏提示元婴正在执行", "running")
    if state["yuanying_probe_pending"]:
        mark_dirty()
        return True

    state["yuanying_probe_pending"] = True
    mark_dirty()
    await schedule_yuanying_status_probe()
    return True


async def handle_yuanying_status_reply(text, now, reply_to, matched_family=None):
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_status_reply = matched_family == "yuanying" or CMD_YUANYING_STATUS in orig_cmd
    is_yuanying_cmd_status_like = (
        matched_family == "yuanying"
        or (_is_yuanying_launch_command(orig_cmd) and any(k in text for k in ["归来倒计时", "窍中温养", "元婴闭关", "尚未恢复", "冷却", "等待", "不足", "休息"]))
    )
    if not (is_status_reply or is_yuanying_cmd_status_like):
        return False

    level_updated = False
    if is_status_reply:
        level_text = parse_yuanying_level_text(text)
        if level_text:
            level_updated = update_identity_level_record(
                get_current_identity_id(),
                "yuanying_level",
                level_text,
                now=now,
                source="yuanying_status",
            )
            if level_updated:
                mark_dirty()

    if not state["yuanying_enabled"]:
        return level_updated

    has_cd_hint = any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息", "归来倒计时"])
    if has_cd_hint:
        wait_sec = parse_wait_time(text)
        if not has_wait_time(text):
            return False

        mark_yuanying_success(now, now + wait_sec + CD_BUFFER_SEC)
        _note_yuanying_remote_block(now, now + wait_sec + CD_BUFFER_SEC, "游戏提示冷却/归来倒计时", "cooldown")
        target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 元婴 CD→{target_time}")
        return True

    compact_text = RE_WHITESPACE.sub("", text or "").replace("：", ":")
    if "状态:元婴闭关" in compact_text:
        phase_before = str(state.get("yuanying_phase") or "")
        previous_next_time = float(state.get("next_yuanying_time", 0) or 0)
        retry_after = max(60.0, float(YUANYING_SPEC.summary_active_query_grace_sec or 0))
        is_summary_calibration = phase_before in {
            "summary_due",
            "observing_summary",
            "waiting_summary",
            "post_summary_wait",
        }
        next_time = now + retry_after if is_summary_calibration else max(previous_next_time, now + retry_after)
        clear_yuanying_summary_flags()
        set_yuanying_phase("running")
        state["yuanying_probe_pending"] = False
        state["next_yuanying_time"] = next_time
        save_state()
        _note_yuanying_remote_block(now, next_time, "游戏状态确认元婴仍在闭关", "running")
        await send_audit_log(f"👶 元婴仍在闭关，已清理陈旧结算等待→{fmt_time_after(next_time - now)}", priority="low")
        return True

    if "窍中温养" in text:
        state["yuanying_probe_pending"] = False
        clear_yuanying_summary_flags()
        begin_queued_launch(YUANYING_SPEC, now)
        console_log("👶 窍中温养，直接继续元婴。")
        command = get_yuanying_launch_command()
        attempt_started_at = time.time()
        msg = await send_game_command(command, track=False, priority="chain")
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            mark_launch_command_sent(YUANYING_SPEC, sent_at)
        else:
            failed_at = time.time()
            send_block = classify_game_send_block(get_current_identity_id(), command)
            if send_block.get("status") != "unsent":
                recovered_msg = _recover_phaseful_sent_from_message_log(command, attempt_started_at, failed_at)
                if recovered_msg:
                    sent_at = float(getattr(recovered_msg, "sent_at", 0) or failed_at)
                    mark_launch_command_sent(YUANYING_SPEC, sent_at)
                    await send_audit_log("👶 元婴续发发送超时，已从消息日志恢复。", priority="low")
                else:
                    mark_launch_command_sent(YUANYING_SPEC, failed_at)
                    await send_audit_log("👶 元婴续发状态未知，按已发起等待状态校准。", priority="low")
            else:
                set_yuanying_phase("idle")
                state["next_yuanying_time"] = failed_at + RETRY_MAX_SEC
                save_state()
        return True

    return False


def _is_yuanying_summary_candidate_phase(now):
    phase = state.get("yuanying_phase")
    next_time = float(state.get("next_yuanying_time", 0) or 0)
    due_while_running = phase == "running" and now > 0 and 0 < next_time <= now
    near_due_while_running = phase == "running" and now > 0 and next_time > now and next_time - now <= 10 * 60
    return phase in ("summary_due", "observing_summary", "waiting_summary", "post_summary_wait") or due_while_running or near_due_while_running


def _reply_context_identity(reply_context):
    try:
        identity_id = int((reply_context or {}).get("send_as_id") or 0)
    except (TypeError, ValueError):
        identity_id = 0
    return identity_id if identity_id > 0 and has_identity(identity_id) else 0


def match_yuanying_summary_identity(text, now=None, reply_context=None):
    compact_text = RE_WHITESPACE.sub("", text or "")
    old_summary_kw_hit = "元神归窍总结" in compact_text
    new_summary_kw_hit = (
        "元神回响" in compact_text and "神游归来" in compact_text and "清点收获" in compact_text
    ) or "元婴闭关结算" in compact_text
    if not (old_summary_kw_hit or new_summary_kw_hit):
        return None, [], old_summary_kw_hit, new_summary_kw_hit
    now = float(now or 0)
    has_explicit_at = "@" in compact_text

    reply_identity_id = _reply_context_identity(reply_context)
    if reply_identity_id:
        with use_identity(reply_identity_id):
            if state["yuanying_enabled"] and _is_yuanying_summary_candidate_phase(now):
                return reply_identity_id, [reply_identity_id], old_summary_kw_hit, new_summary_kw_hit
        return None, [], old_summary_kw_hit, new_summary_kw_hit

    if not has_explicit_at:
        return None, [], old_summary_kw_hit, new_summary_kw_hit

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["yuanying_enabled"]:
                continue
            if not _is_yuanying_summary_candidate_phase(now):
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], matched_ids, old_summary_kw_hit, new_summary_kw_hit
    return None, matched_ids, old_summary_kw_hit, new_summary_kw_hit


async def handle_yuanying_summary_broadcast(text, now, event=None, reply_to=None, reply_context=None):
    target_id, matched_ids, old_summary_kw_hit, new_summary_kw_hit = match_yuanying_summary_identity(text, now=now, reply_context=reply_context)
    if target_id is None:
        if len(matched_ids) > 1:
            names = ", ".join(mono(get_identity_display_name(identity_id)) for identity_id in matched_ids)
            await send_audit_log(f"👶 归窍总结命中多个身份，已跳过：{names}", scope="global", limit=280)
        return

    compact_text = RE_WHITESPACE.sub("", text or "")
    tags = get_send_as_tags(target_id)
    compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
    has_expected_tag = any(tag in compact_text for tag in compact_tags) if compact_tags else False
    preview = (text or "").replace("\n", "\\n")[:300]

    console_log(
        f"🧪 归窍总结命中：旧={'是' if old_summary_kw_hit else '否'} 新={'是' if new_summary_kw_hit else '否'} @={'是' if has_expected_tag else '否'} 预览={preview}",
        scope="identity",
        send_as_id=target_id,
        limit=260,
    )

    with use_identity(target_id):
        await finalize_summary_broadcast(YUANYING_SPEC, now)


async def run_yuanying_scheduler(now):
    if is_cave_public_auto_enabled("yuanying"):
        return
    await run_phaseful_scheduler(
        YUANYING_SPEC,
        now,
        launch_command=get_yuanying_launch_command(),
        schedule_probe=schedule_yuanying_status_probe,
    )


__all__ = [
    "begin_yuanying_post_summary_wait",
    "begin_yuanying_summary_wait",
    "clear_yuanying_summary_flags",
    "delete_yuanying_summary_trigger_msg",
    "get_yuanying_block_reason",
    "get_yuanying_launch_command",
    "get_yuanying_phase_text",
    "get_yuanying_status_detail_text",
    "handle_yuanying_running_reply",
    "handle_yuanying_status_reply",
    "handle_yuanying_success_reply",
    "handle_yuanying_summary_broadcast",
    "mark_yuanying_success",
    "match_yuanying_summary_identity",
    "run_yuanying_scheduler",
    "schedule_yuanying_status_probe",
    "set_yuanying_phase",
    "update_yuanying_block_log_state",
]
