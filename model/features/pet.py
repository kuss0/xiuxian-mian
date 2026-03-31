import random

from ..config import CD_BUFFER_SEC, CMD_PET, PET_CD, RETRY_MAX_SEC
from ..persistence import save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_pet_command, get_pet_name, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time


def get_pet_status_text():
    return (
        "🗡️ 法宝\n"
        f"- 当前名称：{get_pet_name()}\n"
        f"- 下次执行：{fmt_abs_ts(state['next_pet_time'])}（{fmt_remaining(state['next_pet_time'])}）"
    )


async def handle_pet_cd_fix(text, now, reply_to, matched_family=None):
    if not state["pet_enabled"]:
        return False

    if not any(k in text for k in ["尚未恢复", "冷却", "等待", "不足", "休息"]):
        return False

    wait_sec = parse_wait_time(text)
    if wait_sec <= 0:
        return False

    orig_cmd = reply_to.raw_text if reply_to else ""
    pet_name = get_pet_name()
    pet_command = get_pet_command()
    if matched_family == "pet" or "法宝" in text or "抚摸" in text or pet_name in text or pet_command in orig_cmd or CMD_PET in orig_cmd:
        state["next_pet_time"] = now + wait_sec + CD_BUFFER_SEC
        save_state()
        target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
        await send_audit_log(f"⏳ 法宝 CD→{target_time}")
        return True
    return False


async def run_pet_scheduler(now):
    if not state["pet_enabled"]:
        return

    if now >= state["next_pet_time"]:
        p_delay = PET_CD + random.uniform(0, 30)
        state["next_pet_time"] = now + p_delay
        save_state()
        p_next_t = fmt_time_after(p_delay)
        msg = await send_game_command(get_pet_command())
        if not msg:
            state["next_pet_time"] = now + RETRY_MAX_SEC
            save_state()
            await send_audit_log("❌ 法宝发送失败，稍后重试。")
            return
        console_log(f"🗡️ 法宝[{get_pet_name()}]→{p_next_t}")


__all__ = [
    "get_pet_status_text",
    "handle_pet_cd_fix",
    "run_pet_scheduler",
]
