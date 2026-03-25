import asyncio
import random
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    LAUNCHING_TIMEOUT_SEC,
    POST_SUMMARY_WAIT_SEC,
    RE_WHITESPACE,
    SUMMARY_TIMEOUT_SEC,
    YUANYING_PROTECT_SEC,
    client,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, send_audit_log, send_game_command
from ..state import get_game_group_id, get_identity_display_name, get_identity_ids, get_send_as_tags, is_auto_delete_sent_messages_enabled, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time


def set_deep_retreat_phase(phase):
    state["deep_retreat_phase"] = phase


def get_deep_retreat_block_reason(now=None):
    if now is None:
        now = time.time()

    phase = state.get("deep_retreat_phase", "idle")
    if not state["deep_retreat_enabled"]:
        return "模块已关闭"
    if phase == "waiting_summary":
        return "等待闭关总结"
    if phase == "post_summary_wait":
        return "总结后30秒缓冲中"
    if phase == "launching":
        return "闭关指令已发出，等待回复"
    if phase == "running" or (phase == "idle" and state["next_deep_retreat_time"] > now):
        return "深度闭关执行中"
    if state["last_deep_retreat_command_time"] > 0 and now - state["last_deep_retreat_command_time"] < YUANYING_PROTECT_SEC:
        return "30秒保护中"
    return "无"


async def update_deep_retreat_block_log_state(waiting=None, protect=None):
    if waiting is not None:
        prev = state.get("deep_retreat_waiting_logged", False)
        if waiting and not prev:
            state["deep_retreat_waiting_logged"] = True
            await send_audit_log("🧘 深度闭关时间已到，但当前仍在等待闭关总结，暂不执行新的深度闭关。")
        elif not waiting and prev:
            state["deep_retreat_waiting_logged"] = False
            await send_audit_log("🧘 等待闭关总结状态已结束。")

    if protect is not None:
        prev = state.get("deep_retreat_protect_logged", False)
        if protect and not prev:
            state["deep_retreat_protect_logged"] = True
            await send_audit_log("🧘 深度闭关时间已到，但当前处于 30 秒保护中，暂不重复执行。")
        elif not protect and prev:
            state["deep_retreat_protect_logged"] = False
            await send_audit_log("🧘 深度闭关 30 秒保护状态已结束。")


def get_deep_retreat_phase_text(phase=None, now=None):
    if phase is None:
        phase = state.get("deep_retreat_phase", "idle")
    if now is None:
        now = time.time()

    if phase == "waiting_summary":
        return "等待闭关总结"
    if phase == "post_summary_wait":
        return "总结后缓冲中"
    if phase == "launching":
        return "闭关中"
    if phase == "running":
        return "闭关中"
    if phase == "idle":
        if state["next_deep_retreat_time"] > now:
            return "CD中"
        if state["last_deep_retreat_command_time"] > 0 and now - state["last_deep_retreat_command_time"] < YUANYING_PROTECT_SEC:
            return "30秒保护中"
        return "待闭关"
    return "待闭关"


def get_deep_retreat_status_detail_text():
    return (
        "🧘 深度闭关\n"
        f"- 当前阶段：{get_deep_retreat_phase_text()}\n"
        f"- 当前阻塞原因：{get_deep_retreat_block_reason()}\n"
        f"- 下次执行：{fmt_abs_ts(state['next_deep_retreat_time'])}（{fmt_remaining(state['next_deep_retreat_time'])}）\n"
        f"- 闭关总结待触发：{'是' if state.get('deep_retreat_phase') == 'waiting_summary' else '否'}｜30秒缓冲中：{'是' if state.get('deep_retreat_phase') == 'post_summary_wait' else '否'}"
    )


def mark_deep_retreat_success(now, next_time=None):
    set_deep_retreat_phase("running")
    state["deep_retreat_probe_pending"] = False
    state["deep_retreat_summary_sent_at"] = 0
    state["last_deep_retreat_summary_msg_id"] = 0
    state["last_deep_retreat_command_time"] = now
    if next_time is None:
        next_time = now + 8 * 3600 + CD_BUFFER_SEC
    state["next_deep_retreat_time"] = next_time
    save_state()


def clear_deep_retreat_summary_flags():
    state["deep_retreat_summary_sent_at"] = 0
    state["last_deep_retreat_summary_msg_id"] = 0
    if state.get("deep_retreat_phase") == "waiting_summary":
        set_deep_retreat_phase("idle")


def begin_deep_retreat_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC):
    clear_deep_retreat_summary_flags()
    set_deep_retreat_phase("post_summary_wait")
    state["deep_retreat_probe_pending"] = False
    state["next_deep_retreat_time"] = now + delay
    save_state()


def begin_deep_retreat_summary_wait(now):
    set_deep_retreat_phase("waiting_summary")
    state["deep_retreat_summary_sent_at"] = now
    save_state()


async def delete_deep_retreat_summary_trigger_msg():
    msg_id = state.get("last_deep_retreat_summary_msg_id", 0)
    if not msg_id:
        return
    if is_auto_delete_sent_messages_enabled():
        try:
            await client.delete_messages(get_game_group_id(), [msg_id])
        except Exception:
            pass
    state["my_msg_ids"].pop(msg_id, None)


async def schedule_deep_retreat_status_probe(delay=None):
    if delay is None:
        delay = random.uniform(5, 10)

    async def delayed_status():
        await asyncio.sleep(delay)
        await send_game_command(CMD_DEEP_RETREAT_QUERY, track=False)

    _fire_and_forget(delayed_status())
    await send_audit_log(f"🧘 检测到深度闭关已在执行，将在 {delay:.1f}s 后查询闭关状态。")


async def handle_deep_retreat_success_reply(text, now, reply_to):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_DEEP_RETREAT not in orig_cmd:
        return False

    if "你已进入深度闭关状态" in text and "神魂将自行吐纳" in text:
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            mark_deep_retreat_success(now, now + wait_sec + CD_BUFFER_SEC)
            await send_audit_log(f"🧘 深度闭关成功，已按回包时长设置 CD。下次预计：{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
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


async def handle_deep_retreat_running_reply(text, now, reply_to):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_DEEP_RETREAT not in orig_cmd:
        return False

    probe_hit = (
        "深度闭关" in text and any(k in text for k in ["正在", "闭关中", "预计还需", "功成圆满"])
    )
    if not probe_hit:
        return False

    set_deep_retreat_phase("running")
    if state["deep_retreat_probe_pending"]:
        mark_dirty()
        return True

    state["deep_retreat_probe_pending"] = True
    mark_dirty()
    await schedule_deep_retreat_status_probe()
    return True


async def handle_deep_retreat_status_reply(text, now, reply_to):
    if not state["deep_retreat_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_status_reply = CMD_DEEP_RETREAT_QUERY in orig_cmd
    is_retreat_cmd_status_like = (
        CMD_DEEP_RETREAT in orig_cmd and any(k in text for k in ["预计还需", "尚未恢复", "冷却", "等待", "不足", "休息", "功成圆满"])
    )
    if not (is_status_reply or is_retreat_cmd_status_like):
        return False

    if "预计还需" in text and "即可功成圆满" in text:
        wait_sec = parse_wait_time(text)
        if wait_sec <= 0:
            return False
        mark_deep_retreat_success(now, now + wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 深度闭关 CD 修正：预计于 {fmt_time_after(wait_sec + CD_BUFFER_SEC)} 恢复。")
        return True

    return False


def match_deep_retreat_summary_identity(text):
    compact_text = RE_WHITESPACE.sub("", text or "")
    summary_kw_hit = "天道感应：检测到" in compact_text and "功成圆满，神魂正在归位" in compact_text
    if not summary_kw_hit:
        return None, []

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["deep_retreat_enabled"]:
                continue
            if state.get("deep_retreat_phase") != "waiting_summary":
                continue
            tags = get_send_as_tags(identity_id)
            if tags:
                compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
                if any(tag in compact_text for tag in compact_tags):
                    matched_ids.append(identity_id)
            else:
                matched_ids.append(identity_id)

    if len(matched_ids) == 1:
        return matched_ids[0], matched_ids
    return None, matched_ids


async def handle_deep_retreat_summary_broadcast(text, now):
    target_id, matched_ids = match_deep_retreat_summary_identity(text)
    if target_id is None:
        if len(matched_ids) > 1:
            names = ", ".join(get_identity_display_name(identity_id) for identity_id in matched_ids)
            await send_audit_log(f"🧘 闭关总结命中多个身份，已跳过自动推进：{names}")
        return

    with use_identity(target_id):
        await delete_deep_retreat_summary_trigger_msg()
        begin_deep_retreat_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC)
        await update_deep_retreat_block_log_state(waiting=False, protect=False)
        await send_audit_log(f"🧘 检测到闭关总结[{get_identity_display_name(target_id)}]，已删除触发用的 1，将在 30 秒后继续执行深度闭关。")


async def run_deep_retreat_scheduler(now):
    if not state["deep_retreat_enabled"]:
        return

    if state.get("deep_retreat_phase") == "launching":
        if state["last_deep_retreat_command_time"] > 0 and now - state["last_deep_retreat_command_time"] >= LAUNCHING_TIMEOUT_SEC:
            set_deep_retreat_phase("idle")
            save_state()
            await send_audit_log("🧘 深度闭关 launching 状态超过 2 分钟无回复，已自动回退至 idle。")
        return

    if state.get("deep_retreat_phase") == "waiting_summary" and state["deep_retreat_summary_sent_at"] <= 0:
        await delete_deep_retreat_summary_trigger_msg()
        clear_deep_retreat_summary_flags()
        set_deep_retreat_phase("launching")
        state["last_deep_retreat_command_time"] = now
        state["next_deep_retreat_time"] = now + 8 * 3600 + CD_BUFFER_SEC
        save_state()
        await send_audit_log("🧘 检测到闭关总结等待状态异常（缺少触发时间），已自动解卡并继续执行深度闭关。")
        msg = await send_game_command(CMD_DEEP_RETREAT)
        if msg:
            await schedule_deep_retreat_status_probe(random.uniform(8, 12))
        else:
            set_deep_retreat_phase("idle")
            save_state()
        return

    if state.get("deep_retreat_phase") == "waiting_summary" and state["deep_retreat_summary_sent_at"] > 0 and now - state["deep_retreat_summary_sent_at"] >= SUMMARY_TIMEOUT_SEC:
        await delete_deep_retreat_summary_trigger_msg()
        clear_deep_retreat_summary_flags()
        set_deep_retreat_phase("launching")
        state["last_deep_retreat_command_time"] = now
        state["next_deep_retreat_time"] = now + 8 * 3600 + CD_BUFFER_SEC
        save_state()
        await send_audit_log("🧘 发送 1 超过 5 分钟仍未等到闭关总结，按兜底逻辑直接继续执行深度闭关。")
        msg = await send_game_command(CMD_DEEP_RETREAT)
        if msg:
            await schedule_deep_retreat_status_probe(random.uniform(8, 12))
        else:
            set_deep_retreat_phase("idle")
            save_state()
        return

    if state.get("deep_retreat_phase") == "post_summary_wait":
        await update_deep_retreat_block_log_state(waiting=False, protect=False)
        if now < state["next_deep_retreat_time"]:
            return

        set_deep_retreat_phase("launching")
        state["last_deep_retreat_command_time"] = now
        state["next_deep_retreat_time"] = now + 8 * 3600 + CD_BUFFER_SEC
        save_state()
        await send_audit_log("🧘 闭关总结后的 30 秒缓冲已结束，继续执行深度闭关。")
        msg = await send_game_command(CMD_DEEP_RETREAT)
        if not msg:
            set_deep_retreat_phase("idle")
            save_state()
        return

    if state.get("deep_retreat_phase") == "running" and state["next_deep_retreat_time"] > 0 and now >= state["next_deep_retreat_time"]:
        state["deep_retreat_probe_pending"] = False
        begin_deep_retreat_summary_wait(now)
        await send_audit_log("🧘 深度闭关时间已到，先发送 1 触发闭关总结，再继续深度闭关。")
        msg = await send_game_command("1", track=False)
        if msg:
            state["last_deep_retreat_summary_msg_id"] = msg.id
            save_state()
        return

    if state.get("deep_retreat_phase") == "waiting_summary":
        if state["next_deep_retreat_time"] <= now:
            await update_deep_retreat_block_log_state(waiting=True, protect=False)
        else:
            await update_deep_retreat_block_log_state(waiting=False, protect=False)
        return
    else:
        await update_deep_retreat_block_log_state(waiting=False)

    if state.get("deep_retreat_phase") == "running":
        await update_deep_retreat_block_log_state(protect=False)
        return

    if state["last_deep_retreat_command_time"] > 0 and now - state["last_deep_retreat_command_time"] < YUANYING_PROTECT_SEC:
        if state["next_deep_retreat_time"] <= now:
            await update_deep_retreat_block_log_state(waiting=False, protect=True)
        else:
            await update_deep_retreat_block_log_state(protect=False)
        return
    else:
        await update_deep_retreat_block_log_state(protect=False)

    if now >= state["next_deep_retreat_time"]:
        begin_deep_retreat_summary_wait(now)
        await send_audit_log("🧘 深度闭关 CD 已到，先发送 1 触发闭关总结。")
        msg = await send_game_command("1", track=False)
        if msg:
            state["last_deep_retreat_summary_msg_id"] = msg.id
            save_state()


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
