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
    SUMMARY_TIMEOUT_SEC,
    YUANYING_CD,
    YUANYING_PROTECT_SEC,
    client,
)
from ..persistence import mark_dirty, save_state
from ..runtime import _fire_and_forget, send_audit_log, send_game_command
from ..state import get_game_group_id, get_identity_display_name, get_identity_ids, get_send_as_tags, is_auto_delete_sent_messages_enabled, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time


def set_yuanying_phase(phase):
    state["yuanying_phase"] = phase


def get_yuanying_block_reason(now=None):
    if now is None:
        now = time.time()

    phase = state.get("yuanying_phase", "idle")
    if not state["yuanying_enabled"]:
        return "模块已关闭"
    if phase == "waiting_summary":
        return "等待归窍总结"
    if phase == "post_summary_wait":
        return "归窍后30秒缓冲中"
    if phase == "launching":
        return "出窍指令已发出，等待回复"
    if phase == "running" or (phase == "idle" and state["next_yuanying_time"] > now):
        return "元婴执行中"
    if state["last_yuanying_command_time"] > 0 and now - state["last_yuanying_command_time"] < YUANYING_PROTECT_SEC:
        return "30秒保护中"
    return "无"


async def update_yuanying_block_log_state(waiting=None, protect=None):
    if waiting is not None:
        prev = state.get("yuanying_waiting_logged", False)
        if waiting and not prev:
            state["yuanying_waiting_logged"] = True
            await send_audit_log("👶 元婴时间已到，但当前仍在等待归窍总结，暂不执行元婴出窍。")
        elif not waiting and prev:
            state["yuanying_waiting_logged"] = False
            await send_audit_log("👶 等待归窍总结状态已结束。")

    if protect is not None:
        prev = state.get("yuanying_protect_logged", False)
        if protect and not prev:
            state["yuanying_protect_logged"] = True
            await send_audit_log("👶 元婴时间已到，但当前处于 30 秒保护中，暂不重复执行。")
        elif not protect and prev:
            state["yuanying_protect_logged"] = False
            await send_audit_log("👶 元婴 30 秒保护状态已结束。")


def get_yuanying_phase_text(phase=None, now=None):
    if phase is None:
        phase = state.get("yuanying_phase", "idle")
    if now is None:
        now = time.time()

    if phase == "waiting_summary":
        return "等待归窍总结"
    if phase == "post_summary_wait":
        return "总结后缓冲中"
    if phase == "launching":
        return "出窍中"
    if phase == "running":
        return "云游中"
    if phase == "idle":
        if state["next_yuanying_time"] > now:
            return "CD中"
        if state["last_yuanying_command_time"] > 0 and now - state["last_yuanying_command_time"] < YUANYING_PROTECT_SEC:
            return "30秒保护中"
        return "待出窍"
    return "待出窍"


def get_yuanying_status_detail_text():
    return (
        "👶 元婴\n"
        f"- 当前阶段：{get_yuanying_phase_text()}\n"
        f"- 当前阻塞原因：{get_yuanying_block_reason()}\n"
        f"- 下次执行：{fmt_abs_ts(state['next_yuanying_time'])}（{fmt_remaining(state['next_yuanying_time'])}）\n"
        f"- 归窍总结待触发：{'是' if state.get('yuanying_phase') == 'waiting_summary' else '否'}｜30秒缓冲中：{'是' if state.get('yuanying_phase') == 'post_summary_wait' else '否'}"
    )


def mark_yuanying_success(now, next_time=None):
    set_yuanying_phase("running")
    state["yuanying_probe_pending"] = False
    state["yuanying_summary_sent_at"] = 0
    state["last_yuanying_summary_msg_id"] = 0
    state["last_yuanying_command_time"] = now
    if next_time is None:
        next_time = now + YUANYING_CD + CD_BUFFER_SEC
    state["next_yuanying_time"] = next_time
    save_state()


def clear_yuanying_summary_flags():
    state["yuanying_summary_sent_at"] = 0
    state["last_yuanying_summary_msg_id"] = 0
    if state.get("yuanying_phase") == "waiting_summary":
        set_yuanying_phase("idle")


def begin_yuanying_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC):
    clear_yuanying_summary_flags()
    set_yuanying_phase("post_summary_wait")
    state["yuanying_probe_pending"] = False
    state["next_yuanying_time"] = now + delay
    save_state()


def begin_yuanying_summary_wait(now):
    set_yuanying_phase("waiting_summary")
    state["yuanying_summary_sent_at"] = now
    save_state()


async def delete_yuanying_summary_trigger_msg():
    msg_id = state.get("last_yuanying_summary_msg_id", 0)
    if not msg_id:
        return
    if is_auto_delete_sent_messages_enabled():
        try:
            await client.delete_messages(get_game_group_id(), [msg_id])
        except Exception:
            pass
    state["my_msg_ids"].pop(msg_id, None)


async def schedule_yuanying_status_probe(delay=None):
    if delay is None:
        delay = random.uniform(5, 10)

    async def delayed_status():
        await asyncio.sleep(delay)
        await send_game_command(CMD_YUANYING_STATUS, track=False)

    _fire_and_forget(delayed_status())
    await send_audit_log(f"👶 检测到元婴已在执行任务，将在 {delay:.1f}s 后查询元婴状态。")


async def handle_yuanying_success_reply(text, now, reply_to):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_YUANYING not in orig_cmd:
        return False

    if "你心念一动" in text and "元婴化作一道流光飞出" in text:
        mark_yuanying_success(now)
        target_time = fmt_time_after(YUANYING_CD + CD_BUFFER_SEC)
        await send_audit_log(f"👶 元婴出窍成功，已按 8 小时设置 CD。下次预计：{target_time}")
        return True

    return False


async def handle_yuanying_running_reply(text, now, reply_to):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_YUANYING not in orig_cmd:
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


async def handle_yuanying_status_reply(text, now, reply_to):
    if not state["yuanying_enabled"]:
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_status_reply = CMD_YUANYING_STATUS in orig_cmd
    is_yuanying_cmd_status_like = (
        CMD_YUANYING in orig_cmd and any(k in text for k in ["归来倒计时", "窍中温养", "尚未恢复", "冷却", "等待", "不足", "休息"])
    )
    if not (is_status_reply or is_yuanying_cmd_status_like):
        return False

    has_cd_hint = any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息", "归来倒计时"])
    if has_cd_hint:
        wait_sec = parse_wait_time(text)
        if wait_sec <= 0:
            return False

        mark_yuanying_success(now, now + wait_sec + CD_BUFFER_SEC)
        target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 元婴出窍 CD 修正：预计于 {target_time} 恢复。")
        return True

    if "窍中温养" in text:
        set_yuanying_phase("launching")
        state["yuanying_probe_pending"] = False
        clear_yuanying_summary_flags()
        state["next_yuanying_time"] = now + YUANYING_CD + CD_BUFFER_SEC
        state["last_yuanying_command_time"] = now
        save_state()
        await send_audit_log("👶 元婴状态显示’窍中温养’，判定可直接继续元婴出窍。")
        msg = await send_game_command(CMD_YUANYING)
        if not msg:
            set_yuanying_phase("idle")
            save_state()
        return True

    return False


def match_yuanying_summary_identity(text):
    compact_text = RE_WHITESPACE.sub("", text or "")
    old_summary_kw_hit = "元神归窍总结" in compact_text
    new_summary_kw_hit = (
        "元神回响" in compact_text and "神游归来" in compact_text and "清点收获" in compact_text
    )
    if not (old_summary_kw_hit or new_summary_kw_hit):
        return None, [], old_summary_kw_hit, new_summary_kw_hit

    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state["yuanying_enabled"]:
                continue
            if state.get("yuanying_phase") != "waiting_summary":
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
    target_id, matched_ids, old_summary_kw_hit, new_summary_kw_hit = match_yuanying_summary_identity(text)
    if target_id is None:
        if len(matched_ids) > 1:
            names = ", ".join(get_identity_display_name(identity_id) for identity_id in matched_ids)
            await send_audit_log(f"👶 归窍总结命中多个身份，已跳过自动推进：{names}")
        return

    compact_text = RE_WHITESPACE.sub("", text or "")
    tags = get_send_as_tags(target_id)
    compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags}
    has_expected_tag = any(tag in compact_text for tag in compact_tags) if compact_tags else False
    preview = (text or "").replace("\n", "\\n")[:300]

    await send_audit_log(
        "🧪 归窍总结调试\n"
        f"- 身份: {get_identity_display_name(target_id)}\n"
        f"- 旧关键词命中: {'是' if old_summary_kw_hit else '否'}\n"
        f"- 新关键词命中: {'是' if new_summary_kw_hit else '否'}\n"
        f"- @用户名命中: {'是' if has_expected_tag else '否'}\n"
        f"- 最终命中: 是\n"
        f"- 原文预览: {preview}"
    )

    with use_identity(target_id):
        await delete_yuanying_summary_trigger_msg()
        begin_yuanying_post_summary_wait(now, delay=POST_SUMMARY_WAIT_SEC)
        await update_yuanying_block_log_state(waiting=False, protect=False)
        await send_audit_log(f"👶 检测到元神归窍总结[{get_identity_display_name(target_id)}]，已删除触发用的 1，将在 30 秒后继续执行元婴出窍。")


async def run_yuanying_scheduler(now):
    if not state["yuanying_enabled"]:
        return

    if state.get("yuanying_phase") == "launching":
        if state["last_yuanying_command_time"] > 0 and now - state["last_yuanying_command_time"] >= LAUNCHING_TIMEOUT_SEC:
            set_yuanying_phase("idle")
            save_state()
            await send_audit_log("👶 launching 状态超过 2 分钟无回复，已自动回退至 idle。")
        return

    if state.get("yuanying_phase") == "waiting_summary" and state["yuanying_summary_sent_at"] <= 0:
        await delete_yuanying_summary_trigger_msg()
        clear_yuanying_summary_flags()
        set_yuanying_phase("launching")
        state["last_yuanying_command_time"] = now
        state["next_yuanying_time"] = now + YUANYING_CD + CD_BUFFER_SEC
        save_state()
        await send_audit_log("👶 检测到归窍总结等待状态异常（缺少触发时间），已自动解卡并继续执行元婴出窍。")
        msg = await send_game_command(CMD_YUANYING)
        if msg:
            await schedule_yuanying_status_probe(random.uniform(8, 12))
        else:
            set_yuanying_phase("idle")
            save_state()
        return

    if state.get("yuanying_phase") == "waiting_summary" and state["yuanying_summary_sent_at"] > 0 and now - state["yuanying_summary_sent_at"] >= SUMMARY_TIMEOUT_SEC:
        await delete_yuanying_summary_trigger_msg()
        clear_yuanying_summary_flags()
        set_yuanying_phase("launching")
        state["last_yuanying_command_time"] = now
        state["next_yuanying_time"] = now + YUANYING_CD + CD_BUFFER_SEC
        save_state()
        await send_audit_log("👶 发送 1 超过 5 分钟仍未等到归窍总结，按兜底逻辑直接继续执行元婴出窍。")
        msg = await send_game_command(CMD_YUANYING)
        if msg:
            await schedule_yuanying_status_probe(random.uniform(8, 12))
        else:
            set_yuanying_phase("idle")
            save_state()
        return

    if state.get("yuanying_phase") == "post_summary_wait":
        await update_yuanying_block_log_state(waiting=False, protect=False)
        if now < state["next_yuanying_time"]:
            return

        set_yuanying_phase("launching")
        state["last_yuanying_command_time"] = now
        state["next_yuanying_time"] = now + YUANYING_CD + CD_BUFFER_SEC
        save_state()
        await send_audit_log("👶 归窍总结后的 30 秒缓冲已结束，继续执行元婴出窍。")
        msg = await send_game_command(CMD_YUANYING)
        if not msg:
            set_yuanying_phase("idle")
            save_state()
        return

    if state.get("yuanying_phase") == "running" and state["next_yuanying_time"] > 0 and now >= state["next_yuanying_time"]:
        state["yuanying_probe_pending"] = False
        begin_yuanying_summary_wait(now)
        await send_audit_log("👶 元婴归来时间已到，先发送 1 触发归窍总结，再继续元婴出窍。")
        msg = await send_game_command("1", track=False)
        if msg:
            state["last_yuanying_summary_msg_id"] = msg.id
            save_state()
        return

    if state.get("yuanying_phase") == "waiting_summary":
        if state["next_yuanying_time"] <= now:
            await update_yuanying_block_log_state(waiting=True, protect=False)
        else:
            await update_yuanying_block_log_state(waiting=False, protect=False)
        return
    else:
        await update_yuanying_block_log_state(waiting=False)

    if state.get("yuanying_phase") == "running":
        await update_yuanying_block_log_state(protect=False)
        return

    if state["last_yuanying_command_time"] > 0 and now - state["last_yuanying_command_time"] < YUANYING_PROTECT_SEC:
        if state["next_yuanying_time"] <= now:
            await update_yuanying_block_log_state(waiting=False, protect=True)
        else:
            await update_yuanying_block_log_state(protect=False)
        return
    else:
        await update_yuanying_block_log_state(protect=False)

    if now >= state["next_yuanying_time"]:
        begin_yuanying_summary_wait(now)
        await send_audit_log("👶 元婴 CD 已到，先发送 1 触发归窍总结。")
        msg = await send_game_command("1", track=False)
        if msg:
            state["last_yuanying_summary_msg_id"] = msg.id
            save_state()


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
