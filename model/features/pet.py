import random

from ..config import CD_BUFFER_SEC, CMD_PET, PET_CD, RETRY_MAX_SEC
from ..persistence import save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_pet_command, get_pet_name, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


PET_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
PET_REPLY_HINT_KEYWORDS = ("法宝", "抚摸")
PET_NOT_FOUND_KEYWORDS = ("没有这件拥有器灵的法宝", "名字输入错误")
PET_NOT_FOUND_ERROR = "法宝不存在或名称错误，已关闭法宝模块"


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


def _is_pet_not_found_reply(text):
    return all(keyword in str(text or "") for keyword in PET_NOT_FOUND_KEYWORDS)


def get_pet_status_text():
    lines = [
        "🗡️ 法宝",
        f"- 已启用：{'是' if state['pet_enabled'] else '否'}",
        f"- 当前名称：{get_pet_name()}",
        f"- 下次执行：{fmt_abs_ts(state['next_pet_time'])}（{fmt_remaining(state['next_pet_time'])}）",
    ]
    if state.get("pet_last_error"):
        lines.append(f"- 最近异常：{state.get('pet_last_error')}")
    return "\n".join(lines)


async def handle_pet_cd_fix(text, now, reply_to, matched_family=None):
    if not state["pet_enabled"]:
        return False

    if _is_pet_not_found_reply(text) and _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        state["pet_enabled"] = False
        state["next_pet_time"] = 0
        state["pet_last_error"] = PET_NOT_FOUND_ERROR
        save_state()
        await send_audit_log("⚠️ 法宝名称错误，已关闭法宝模块。")
        return True

    if not any(keyword in text for keyword in PET_CD_HINT_KEYWORDS):
        return False

    wait_sec = parse_wait_time(text)
    if not has_wait_time(text) or not _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        return False

    state["pet_last_error"] = ""
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
            state["pet_last_error"] = "法宝发送失败"
            _set_pet_next_time(now + RETRY_MAX_SEC)
            await send_audit_log("❌ 法宝发送失败，稍后重试。")
            return
        state["pet_last_error"] = ""
        save_state()
        console_log(f"🗡️ 法宝[{get_pet_name()}]→{p_next_t}")


__all__ = [
    "get_pet_status_text",
    "handle_pet_cd_fix",
    "run_pet_scheduler",
]
