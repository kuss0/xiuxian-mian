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
from ..runtime import _fire_and_forget, console_log, send_audit_log, send_game_command
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


TREE_MATURING_FIRST_STATUS_MIN_SEC = 10 * 60
TREE_MATURING_FIRST_STATUS_MAX_SEC = 20 * 60
TREE_HARVEST_FOLLOWUP_DELAY_SEC = 30 * 60
TREE_IRRIGATION_INSUFFICIENT_POWER_KEYWORDS = ("修为不足", "无法调动天地灵气")


def _is_tree_irrigation_insufficient_power(text):
    return all(keyword in str(text or "") for keyword in TREE_IRRIGATION_INSUFFICIENT_POWER_KEYWORDS)


def get_tree_status_text():
    lines = ["🌳 灵树"]
    if state['is_invading']:
        lines.extend([
            "- 当前状态：入侵中",
            f"- 下次守山：{fmt_abs_ts(state['next_guard_time'])}（{fmt_remaining(state['next_guard_time'])}）",
            f"- 待补偿灌溉：{'是' if state['pending_irrigation'] else '否'}",
        ])
    elif state['is_maturing']:
        lines.extend([
            "- 当前状态：成熟采摘期",
            f"- 已采摘：{'是' if state['is_harvested'] else '否'}",
            f"- 待补偿灌溉：{'是' if state['pending_irrigation'] else '否'}",
        ])
    else:
        lines.extend([
            "- 当前状态：正常生长",
            f"- 下次灌溉：{fmt_abs_ts(state['next_irr_time'])}（{fmt_remaining(state['next_irr_time'])}）",
        ])
        if state['tree_bootstrap_check_needed']:
            lines.append("- 启动校验待执行：是")
    return "\n".join(lines)


async def handle_tree_invasion_start(text, now):
    if not state["tree_enabled"]:
        return

    if "古剑门来袭" in text or "古剑门入侵中" in text:
        if not state["is_invading"]:
            state["is_invading"] = True
            state["next_guard_time"] = now
            save_state()
            await send_audit_log("🚨 检测到入侵，已切守山。")


async def handle_tree_invasion_end(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return

    is_guard_success_broadcast = "【守护成功！】" in text or "攻势已被成功击退" in text
    is_no_invasion = "当前并无外敌入侵，无需加固大阵。" in text or (is_reply_to_me and "无需加固大阵" in text)
    if is_guard_success_broadcast or is_no_invasion:
        if state["is_invading"]:
            state["is_invading"] = False
            state["next_guard_time"] = now + FREEZE_CD
            if state["pending_irrigation"]:
                state["pending_irrigation"] = False
                state["next_irr_time"] = now
                save_state()
                await send_audit_log("🛡️ 入侵结束，立即补灌溉。")
            else:
                save_state()
                await send_audit_log("🛡️ 入侵结束，恢复常规监控。")


async def handle_tree_rebirth_reset(text, now):
    if not state["tree_enabled"]:
        return

    if "灵眼之树" in text and ("新的轮回开始" in text or "贡献度已重置" in text):
        state["is_harvested"] = False
        state["tree_bootstrap_check_needed"] = False
        if state["is_maturing"]:
            state["is_maturing"] = False
            delay = random.uniform(10, 20)
            state["next_irr_time"] = now + delay
            save_state()
            target_time = fmt_time_after(delay)
            await send_audit_log(f"🌳 轮回重置，{delay:.1f}s 后恢复灌溉（{target_time}）。")
        else:
            save_state()


async def handle_tree_cd_fix(text, now, reply_to, matched_family=None):
    if not state["tree_enabled"]:
        return False

    orig_cmd = reply_to.raw_text if reply_to else ""
    if _is_tree_irrigation_insufficient_power(text) and (matched_family == "tree_panel" or CMD_TREE_WATER in orig_cmd):
        await send_audit_log("⚠️ 灌溉修为不足，需手动处理。")
        return True

    if not any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息", "调息"]):
        return False

    wait_sec = parse_wait_time(text)
    if not has_wait_time(text):
        return False

    target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)

    if matched_family == "tree_guard" or "守山" in text or "协同" in text or CMD_TREE_GUARD in orig_cmd:
        state["next_guard_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 守山 CD→{target_time}")
        return True
    if matched_family == "tree_panel" or "灌溉" in text or CMD_TREE_WATER in orig_cmd:
        state["next_irr_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        await send_audit_log(f"⏳ 灌溉 CD→{target_time}")
        return True
    return False


async def handle_tree_exception_prompt(text):
    if not state["tree_enabled"]:
        return

    if "已然成熟或正遭劫难" not in text:
        return

    delay = random.randint(5, 7)

    async def delayed_status():
        await asyncio.sleep(delay)
        if not state["tree_enabled"]:
            return
        await send_game_command(CMD_TREE_STATUS)

    _fire_and_forget(delayed_status())
    console_log(f"🔍 灵树异常，{delay}s 后查状态。")


async def handle_tree_panel(text, now, is_reply_to_me):
    if not state["tree_enabled"]:
        return False

    is_tree_panel = "【落云宗 · 灵眼之树】" in text or "落云宗·灵眼之树" in text
    is_maturing_broadcast = "🍎 灵果已完全成熟！ 采摘期开启！" in text and "📊 天道榜单已定格！" in text
    if not is_tree_panel and not is_maturing_broadcast:
        return False

    if is_maturing_broadcast and not is_tree_panel:
        has_pending_tree_cmd = any(
            p["cmd"] in {CMD_TREE_WATER, CMD_TREE_STATUS} for p in state["pending_tasks"].values()
        )
        if not has_pending_tree_cmd and not state["is_maturing"]:
            return False
        remove_ids = [
            msg_id for msg_id, pending in state["pending_tasks"].items()
            if pending.get("cmd") in {CMD_TREE_WATER, CMD_TREE_STATUS}
        ]
        for msg_id in remove_ids:
            state["pending_tasks"].pop(msg_id, None)

    state["tree_bootstrap_check_needed"] = False

    if "成熟采摘期" in text or is_maturing_broadcast:
        was_maturing = state["is_maturing"]
        state["is_maturing"] = True
        has_pending_status = any(
            p["cmd"] == CMD_TREE_STATUS for p in state["pending_tasks"].values()
        )
        has_pending_harvest = any(
            p["cmd"] == CMD_TREE_HARVEST for p in state["pending_tasks"].values()
        )

        remaining_match = RE_TREE_REMAINING.search(text)
        remain_sec = parse_wait_time(remaining_match.group(1)) if remaining_match else 0
        if remain_sec > 0:
            state["next_irr_time"] = now + FREEZE_CD

        no_contribution_snapshot = "(暂无弟子贡献)" in text
        if no_contribution_snapshot and remain_sec > TREE_HARVEST_FOLLOWUP_DELAY_SEC:
            next_followup_at = now + TREE_HARVEST_FOLLOWUP_DELAY_SEC
            previous_followup_at = float(state.get("tree_harvest_followup_due_at", 0) or 0)
            state["tree_harvest_followup_due_at"] = next_followup_at
            should_schedule_followup = (not has_pending_status) and previous_followup_at <= now
            save_state()

            if should_schedule_followup:
                async def delayed_status_followup():
                    await asyncio.sleep(TREE_HARVEST_FOLLOWUP_DELAY_SEC)
                    if not state["is_maturing"] or state["is_harvested"]:
                        return
                    await send_game_command(CMD_TREE_STATUS)

                _fire_and_forget(delayed_status_followup())

            if previous_followup_at <= now:
                state["tree_maturing_logged"] = True
                await send_audit_log(f"🌳 榜单未定格，30 分钟后复查（{fmt_abs_ts(next_followup_at)}）。")
                save_state()
            return True

        state["tree_harvest_followup_due_at"] = 0

        already_harvested_snapshot = "你的当前状态: 已采摘" in text or "你的当前状态：已采摘" in text
        if already_harvested_snapshot and not state["is_harvested"]:
            state["is_harvested"] = True
            save_state()

        if is_reply_to_me and not state["is_harvested"] and not has_pending_harvest:
            state["is_harvested"] = True
            save_state()

            async def delayed_harvest():
                await asyncio.sleep(random.uniform(1.5, 3.5))
                if not state["is_maturing"] or not state["tree_enabled"]:
                    return
                await send_game_command(CMD_TREE_HARVEST)

            _fire_and_forget(delayed_harvest())
            await send_audit_log("🍒 灵果成熟，已自动采摘。")

        first_status_delay_sec = random.uniform(TREE_MATURING_FIRST_STATUS_MIN_SEC, TREE_MATURING_FIRST_STATUS_MAX_SEC)
        if remain_sec > 0:
            end_t = fmt_time_after(remain_sec)
            if not state["is_harvested"] and not has_pending_status and remain_sec > first_status_delay_sec:
                async def delayed_status():
                    await asyncio.sleep(first_status_delay_sec)
                    if state["is_harvested"] or not state["is_maturing"]:
                        return
                    await send_game_command(CMD_TREE_STATUS)

                _fire_and_forget(delayed_status())
                if not state.get("tree_maturing_logged", False):
                    state["tree_maturing_logged"] = True
                    await send_audit_log(f"🌳 成熟期，{first_status_delay_sec / 60:.1f} 分钟后查状态。")
            else:
                if not was_maturing or not state.get("tree_maturing_logged", False):
                    state["tree_maturing_logged"] = True
                    await send_audit_log(f"🌳 成熟期至 {end_t}，等重置广播。")
            save_state()
        elif is_maturing_broadcast:
            state["next_irr_time"] = now + FREEZE_CD
            if not state["is_harvested"] and not has_pending_status:
                async def delayed_status():
                    await asyncio.sleep(first_status_delay_sec)
                    if state["is_harvested"] or not state["is_maturing"]:
                        return
                    await send_game_command(CMD_TREE_STATUS)

                _fire_and_forget(delayed_status())
                if not state.get("tree_maturing_logged", False):
                    state["tree_maturing_logged"] = True
                    await send_audit_log(f"🌳 收到成熟广播，{first_status_delay_sec / 60:.1f} 分钟后查状态。")
            else:
                if not was_maturing or not state.get("tree_maturing_logged", False):
                    state["tree_maturing_logged"] = True
                    await send_audit_log("🌳 收到成熟广播，等重置广播。")
            save_state()
        return True
    if is_tree_panel:
        state_changed = False
        if state["is_maturing"]:
            state["is_maturing"] = False
            state["tree_harvest_followup_due_at"] = 0
            # 退出成熟期时，next_irr_time 之前被推到 FREEZE_CD，必须重置
            # 否则会永久卡在 115 天后
            if state["next_irr_time"] > now + 24 * 3600:
                state["next_irr_time"] = now
            state_changed = True
            if state.get("tree_maturing_logged", False):
                state["tree_maturing_logged"] = False
                await send_audit_log("🌳 成熟期结束，恢复灌溉。")
        if state["is_harvested"]:
            state["is_harvested"] = False
            state_changed = True
            console_log('🌳 已清除本地采摘标记。')
        if state["pending_irrigation"] and not state["is_invading"]:
            state["pending_irrigation"] = False
            state["next_irr_time"] = now
            state_changed = True
            console_log("🌳 已释放补偿灌溉，恢复调度。")
        if state_changed:
            save_state()
        return True
    return False


async def run_tree_bootstrap_check(now):
    if not state["tree_enabled"]:
        return
    if not state["tree_bootstrap_check_needed"]:
        return

    state["tree_bootstrap_check_needed"] = False
    save_state()
    console_log("🌳 启动校验：查询灵树状态。")
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
                await send_audit_log("⏳ 入侵中，灌溉已转补偿队列。")
        else:
            delay = random.uniform(IRR_INTERVAL_MIN, IRR_INTERVAL_MAX)
            state["next_irr_time"] = now + delay
            save_state()
            next_t_str = fmt_time_after(delay)
            msg = await send_game_command(CMD_TREE_WATER)
            if not msg:
                state["next_irr_time"] = now + RETRY_MAX_SEC
                save_state()
                await send_audit_log("❌ 灌溉发送失败，稍后重试。")
            else:
                await send_audit_log(f"🚀 灌溉→{next_t_str}")

    if state["is_invading"] and now >= state["next_guard_time"]:
        g_delay = random.uniform(GUARD_INTERVAL_MIN, GUARD_INTERVAL_MAX)
        state["next_guard_time"] = now + g_delay
        save_state()
        g_next_t = fmt_time_after(g_delay)
        msg = await send_game_command(CMD_TREE_GUARD)
        if not msg:
            state["next_guard_time"] = now + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 守山发送失败，稍后重试。")
        else:
            await send_audit_log(f"🛡️ 守山→{g_next_t}")


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
