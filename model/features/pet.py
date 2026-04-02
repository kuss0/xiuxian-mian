import random

from ..config import CD_BUFFER_SEC, CMD_PET, PET_CD, RETRY_MAX_SEC
from ..persistence import save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_pet_command, get_pet_name, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, parse_wait_time


PET_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
PET_REPLY_HINT_KEYWORDS = ("法宝", "抚摸")


def _set_pet_next_time(next_time):
    state["next_pet_time"] = float(next_time or 0)
    save_state()


def _is_pet_cd_reply(text, reply_to, matched_family=None):
    if matched_family == "pet":
        return True

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    pet_name = get_pet_name()
    pet_command = get_pet_command()
    return (
        any(keyword in text for keyword in PET_REPLY_HINT_KEYWORDS)
        or pet_name in text
        or pet_command in orig_cmd
        or CMD_PET in orig_cmd
    )



def get_pet_status_text():
    return (
        "🗡️ 法宝\n"
        f"- 当前名称：{get_pet_name()}\n"
        f"- 下次执行：{fmt_abs_ts(state['next_pet_time'])}（{fmt_remaining(state['next_pet_time'])}）"
    )


async def handle_pet_cd_fix(text, now, reply_to, matched_family=None):
    if not state["pet_enabled"]:
        return False

    if not any(keyword in text for keyword in PET_CD_HINT_KEYWORDS):
        return False

    wait_sec = parse_wait_time(text)
    if wait_sec <= 0 or not _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        return False

    _set_pet_next_time(now + wait_sec + CD_BUFFER_SEC)
    target_time = fmt_time_after(wait_sec + CD_BUFFER_SEC)
    await send_audit_log(f"⏳ 法宝 CD→{target_time}")
    return True


async def run_pet_scheduler(now):
    if not state["pet_enabled"]:
        return

    if now >= state["next_pet_time"]:
        p_delay = PET_CD + random.uniform(0, 30)
        _set_pet_next_time(now + p_delay)
        p_next_t = fmt_time_after(p_delay)
        msg = await send_game_command(get_pet_command())
        if not msg:
            _set_pet_next_time(now + RETRY_MAX_SEC)
            await send_audit_log("❌ 法宝发送失败，稍后重试。")
            return
        console_log(f"🗡️ 法宝[{get_pet_name()}]→{p_next_t}")


__all__ = [
    "get_pet_status_text",
    "handle_pet_cd_fix",
    "run_pet_scheduler",
]
