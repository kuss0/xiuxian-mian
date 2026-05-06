import asyncio
import random
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    LAUNCHING_TIMEOUT_SEC,
    POST_SUMMARY_WAIT_SEC,
    RE_WHITESPACE,
    RETRY_MAX_SEC,
    SUMMARY_TIMEOUT_SEC,
    YUANYING_CD,
    YUANYING_PROTECT_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, console_log, mono, send_audit_log, send_game_command
from ..state import get_identity_display_name, get_identity_ids, get_send_as_tags, state, use_identity
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
    post_summary_wait_sec=25,
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
    waiting_anomaly_audit="👶 归窍等待异常，已解卡继续。",
    waiting_timeout_audit="👶 归窍总结超时，按兜底继续。",
    post_wait_console="👶 归窍缓冲结束，继续元婴。",
    running_due_console="👶 元婴归来时间到",
    cd_due_console="👶 元婴 CD 到",
    summary_received_console="👶 收到归窍总结，短缓冲后继续。",
    summary_trigger_command=CMD_YUANYING_STATUS,
    summary_passive_triggers=("元婴状态", "1"),
    summary_passive_timeout_sec=45,
    summary_due_delay_min_sec=30,
    summary_due_delay_max_sec=90,
    summary_observe_sec=45,
    summary_retry_min_sec=45,
    summary_retry_max_sec=90,
)
register_phaseful_spec(YUANYING_SPEC)


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


def begin_yuanying_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC):
    begin_post_summary_wait(YUANYING_SPEC, now, delay=delay)


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
    if matched_family != "yuanying" and CMD_YUANYING not in orig_cmd:
        return False

    if "你心念一动" in text and "元婴化作一道流光飞出" in text:
        mark_yuanying_success(now)
        target_time = fmt_time_after(YUANYING_CD + CD_BUFFER_SEC)
        await send_audit_log(f"👶 元婴成功→{target_time}")
        return True

    return False


async def handle_yuanying_running_reply(text, now, reply_to, matched_family=None):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "yuanying" and CMD_YUANYING not in orig_cmd:
        return False

    probe_hit = (
        "你的元婴" in text and
        "元神出窍" in text and
        "任务" in text
    )
    if not probe_hit:
        return False

    set_yuanying_phase("running")
    if state["yuanying_probe_pending"]:
        mark_dirty()
        return True

    state["yuanying_probe_pending"] = True
    mark_dirty()
    await schedule_yuanying_status_probe()
    return True


async def handle_yuanying_status_reply(text, now, reply_to, matched_family=None):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_status_reply = matched_family == "yuanying" or CMD_YUANYING_STATUS in orig_cmd
    is_yuanying_cmd_status_like = (
        matched_family == "yuanying"
        or (CMD_YUANYING in orig_cmd and any(k in text for k in ["归来倒计时", "窍中温养", "尚未恢复", "冷却", "等待", "不足", "休息"]))
    )
    if not (is_status_reply or is_yuanying_cmd_status_like):
        return False

    has_cd_hint = any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息", "归来倒计时"])
    if has_cd_hint:
        wait_sec = parse_wait_time(text)
        if not has_wait_time(text):
            return False

        mark_yuanying_success(now, now + wait_sec + CD_BUFFER_SEC)
        target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 元婴 CD→{target_time}")
        return True

    if "窍中温养" in text:
        state["yuanying_probe_pending"] = False
        clear_yuanying_summary_flags()
        begin_queued_launch(YUANYING_SPEC, now)
        console_log("👶 窍中温养，直接继续元婴。")
        msg = await send_game_command(CMD_YUANYING, track=False, priority="chain")
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            mark_launch_command_sent(YUANYING_SPEC, sent_at)
        else:
            failed_at = time.time()
            set_yuanying_phase("idle")
            state["next_yuanying_time"] = failed_at + RETRY_MAX_SEC
            save_state()
        return True

    return False


def match_yuanying_summary_identity(text, now=None):
    compact_text = RE_WHITESPACE.sub("", text or "")
    old_summary_kw_hit = "元神归窍总结" in compact_text
    new_summary_kw_hit = (
        "元神回响" in compact_text and "神游归来" in compact_text and "清点收获" in compact_text
    )
    if not (old_summary_kw_hit or new_summary_kw_hit):
        return None, [], old_summary_kw_hit, new_summary_kw_hit
    now = float(now or 0)

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["yuanying_enabled"]:
                continue
            phase = state.get("yuanying_phase")
            due_while_running = (
                phase == "running"
                and now > 0
                and 0 < float(state.get("next_yuanying_time", 0) or 0) <= now
            )
            if phase not in ("summary_due", "observing_summary", "waiting_summary") and not due_while_running:
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)
            else:
                if ("修士" in compact_text and old_summary_kw_hit) or new_summary_kw_hit:
                    matched_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], matched_ids, old_summary_kw_hit, new_summary_kw_hit
    return None, matched_ids, old_summary_kw_hit, new_summary_kw_hit


async def handle_yuanying_summary_broadcast(text, now):
    target_id, matched_ids, old_summary_kw_hit, new_summary_kw_hit = match_yuanying_summary_identity(text, now=now)
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
    await run_phaseful_scheduler(
        YUANYING_SPEC,
        now,
        launch_command=CMD_YUANYING,
        schedule_probe=schedule_yuanying_status_probe,
    )


__all__ = [
    "begin_yuanying_post_summary_wait",
    "begin_yuanying_summary_wait",
    "clear_yuanying_summary_flags",
    "delete_yuanying_summary_trigger_msg",
    "get_yuanying_block_reason",
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
