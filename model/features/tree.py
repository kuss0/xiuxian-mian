import asyncio
import random

from ..config import (
    CD_BUFFER_SEC,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    FREEZE_CD,
    GUARD_INTERVAL_MAX,
    GUARD_INTERVAL_MIN,
    IRR_INTERVAL_MAX,
    IRR_INTERVAL_MIN,
    RE_TREE_REMAINING,
    RETRY_MAX_SEC,
)
from ..persistence import save_state
from ..runtime import _fire_and_forget, send_audit_log, send_game_command
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time


def get_tree_status_text():
    if state['is_invading']:
        text = (
            "🌳 灵树\n"
            "- 当前状态：入侵中\n"
            f"- 下次守山：{fmt_abs_ts(state['next_guard_time'])}（{fmt_remaining(state['next_guard_time'])}）\n"
            f"- 待补偿灌溉：{'是' if state['pending_irrigation'] else '否'}"
        )
    elif state['is_maturing']:
        text = (
            "🌳 灵树\n"
            "- 当前状态：成熟采摘期\n"
            f"- 已采摘：{'是' if state['is_harvested'] else '否'}\n"
            f"- 待补偿灌溉：{'是' if state['pending_irrigation'] else '否'}"
        )
    else:
        text = (
            "🌳 灵树\n"
            "- 当前状态：正常生长\n"
            f"- 下次灌溉：{fmt_abs_ts(state['next_irr_time'])}（{fmt_remaining(state['next_irr_time'])}）"
        )
        if state['tree_bootstrap_check_needed']:
            text += "\n- 启动校验待执行：是"
    return text


async def handle_tree_invasion_start(text, now):
    if not state["tree_enabled"]:
        return

    if "古剑门来袭" in text or "古剑门入侵中" in text:
        if not state["is_invading"]:
            state["is_invading"] = True
            state["next_guard_time"] = now
            save_state()
            await send_audit_log("🚨 监测到古剑门入侵！灌溉逻辑已挂起，守山逻辑优先。")


async def handle_tree_invasion_end(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return

    is_guard_success = "【守护成功！】" in text or "攻势已被成功击退" in text
    if is_guard_success or (is_reply_to_me and "无需加固大阵" in text):
        if state["is_invading"]:
            state["is_invading"] = False
            state["next_guard_time"] = now + FREEZE_CD
            if state["pending_irrigation"]:
                state["pending_irrigation"] = False
                state["next_irr_time"] = now
                save_state()
                await send_audit_log("🛡️ 入侵状态解除！检测到待补偿任务，立即执行灌溉。")
            else:
                save_state()
                await send_audit_log("🛡️ 入侵状态解除，恢复常态化监控。")


async def handle_tree_rebirth_reset(text, now):
    if not state["tree_enabled"]:
        return

    if "灵眼之树" in text and "轮回" in text:
        state["is_harvested"] = False
        state["tree_bootstrap_check_needed"] = False
        if state["is_maturing"]:
            state["is_maturing"] = False
            delay = random.uniform(10, 20)
            state["next_irr_time"] = now + delay
            save_state()
            target_time = fmt_time_after(delay)
            await send_audit_log(f"🌳 收到轮回重置提示！采摘状态已重置。将在 {delay:.1f} 秒后 ({target_time}) 恢复常规灌溉。")
        else:
            save_state()


async def handle_tree_cd_fix(text, now, reply_to):
    if not state["tree_enabled"]:
        return

    if not any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息"]):
        return

    wait_sec = parse_wait_time(text)
    if wait_sec <= 0:
        return

    target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
    orig_cmd = reply_to.raw_text if reply_to else ""

    if "守山" in text or "协同" in text or CMD_TREE_GUARD in orig_cmd:
        state["next_guard_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 守山 CD 修正：预计于 {target_time} 恢复。")
    elif "灌溉" in text or CMD_TREE_WATER in orig_cmd:
        state["next_irr_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 灌溉 CD 修正：预计于 {target_time} 恢复。")


async def handle_tree_exception_prompt(text):
    if not state["tree_enabled"]:
        return

    if "已然成熟或正遭劫难" not in text:
        return

    delay = random.randint(5, 7)

    async def delayed_status():
        await asyncio.sleep(delay)
        await send_game_command(CMD_TREE_STATUS)

    _fire_and_forget(delayed_status())
    await send_audit_log(f"🔍 收到异常提示，已计划在 {delay}s 后同步灵树状态。")


async def handle_tree_panel(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return

    if "【落云宗 · 灵眼之树】" not in text and "落云宗·灵眼之树" not in text:
        return

    state["tree_bootstrap_check_needed"] = False

    if "成熟采摘期" in text:
        was_maturing = state["is_maturing"]
        state["is_maturing"] = True

        if is_reply_to_me and not state["is_harvested"]:
            state["is_harvested"] = True
            save_state()

            async def delayed_harvest():
                await asyncio.sleep(random.uniform(1.5, 3.5))
                await send_game_command(CMD_TREE_HARVEST)

            _fire_and_forget(delayed_harvest())
            await send_audit_log("🍒 灵果已成熟且本地判定未采摘，执行自动采摘并锁定采摘状态。")

        remaining_match = RE_TREE_REMAINING.search(text)
        if remaining_match:
            remain_sec = parse_wait_time(remaining_match.group(1))
            if remain_sec > 0:
                state["next_irr_time"] = now + FREEZE_CD
                save_state()
                end_t = fmt_time_after(remain_sec)

                has_pending_status = any(
                    p["cmd"] == CMD_TREE_STATUS for p in state["pending_tasks"].values()
                )
                if not state["is_harvested"] and not has_pending_status:
                    async def delayed_status():
                        await asyncio.sleep(random.uniform(2, 4))
                        await send_game_command(CMD_TREE_STATUS)

                    _fire_and_forget(delayed_status())
                    if not state.get("tree_maturing_logged", False):
                        state["tree_maturing_logged"] = True
                        await send_audit_log(f"🌳 进入成熟采摘期，剩余 {remain_sec}s，预计 {end_t} 结束。当前检测到本地未采摘，已主动查询状态触发采摘。")
                else:
                    if not was_maturing or not state.get("tree_maturing_logged", False):
                        state["tree_maturing_logged"] = True
                        await send_audit_log(f"🌳 进入成熟采摘期，剩余 {remain_sec}s，预计 {end_t} 结束。等待重置广播以恢复灌溉。")
    else:
        state_changed = False
        if state["is_maturing"]:
            state["is_maturing"] = False
            state_changed = True
            if state.get("tree_maturing_logged", False):
                state["tree_maturing_logged"] = False
                await send_audit_log("🌳 成熟采摘期状态已结束，恢复常规灌溉监控。")
        if state["is_harvested"]:
            state["is_harvested"] = False
            state_changed = True
            await send_audit_log("🌳 当前灵树面板已不在成熟采摘期，已同步清除本地“已采摘”标记。")
        if state["pending_irrigation"] and not state["is_invading"]:
            state["pending_irrigation"] = False
            state["next_irr_time"] = now
            state_changed = True
            await send_audit_log("🌳 当前灵树面板已恢复非关键状态，已同步释放待补偿灌溉，并立即恢复灌溉调度。")
        if state_changed:
            save_state()


async def run_tree_bootstrap_check(now):
    if not state["tree_enabled"]:
        return
    if not state["tree_bootstrap_check_needed"]:
        return

    state["tree_bootstrap_check_needed"] = False
    save_state()
    await send_audit_log("🌳 检测到上次启动时灵树处于关键状态，执行一次启动校验：查询灵树状态。")
    await send_game_command(CMD_TREE_STATUS)


async def run_tree_scheduler(now):
    if not state["tree_enabled"]:
        return

    if not state["is_maturing"] and now >= state["next_irr_time"]:
        if state["is_invading"]:
            if not state["pending_irrigation"]:
                state["pending_irrigation"] = True
                state["next_irr_time"] = now + FREEZE_CD
                save_state()
                await send_audit_log("⏳ 灌溉 CD 已到，但目前处于入侵状态，已记录至补偿队列。")
        else:
            delay = random.uniform(IRR_INTERVAL_MIN, IRR_INTERVAL_MAX)
            state["next_irr_time"] = now + delay
            save_state()
            next_t_str = fmt_time_after(delay)
            msg = await send_game_command(CMD_TREE_WATER)
            if not msg:
                state["next_irr_time"] = now + RETRY_MAX_SEC
                save_state()
                await send_audit_log("❌ 灌溉发送失败，已改为稍后重试。")
            else:
                await send_audit_log(f"🚀 执行常规灌溉。下次预计：{next_t_str}")

    if state["is_invading"] and now >= state["next_guard_time"]:
        g_delay = random.uniform(GUARD_INTERVAL_MIN, GUARD_INTERVAL_MAX)
        state["next_guard_time"] = now + g_delay
        save_state()
        g_next_t = fmt_time_after(g_delay)
        msg = await send_game_command(CMD_TREE_GUARD)
        if not msg:
            state["next_guard_time"] = now + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 协同守山发送失败，已改为稍后重试。")
        else:
            await send_audit_log(f"🛡️ 执行协同守山。下次预计：{g_next_t}")


__all__ = [
    "get_tree_status_text",
    "handle_tree_cd_fix",
    "handle_tree_exception_prompt",
    "handle_tree_invasion_end",
    "handle_tree_invasion_start",
    "handle_tree_panel",
    "handle_tree_rebirth_reset",
    "run_tree_bootstrap_check",
    "run_tree_scheduler",
]
