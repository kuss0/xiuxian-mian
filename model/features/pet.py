import asyncio
import random
import re
import time

from ..config import CD_BUFFER_SEC, CMD_PET, CMD_PET_TRIAL, PET_CD, PET_TRIAL_CD, RETRY_MAX_SEC
from ..persistence import save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import get_pet_command, get_pet_name, get_pet_trial_command, get_pet_trial_name, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage


PET_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
PET_REPLY_HINT_KEYWORDS = ("法宝", "抚摸")
PET_NOT_FOUND_KEYWORDS = ("没有这件拥有器灵的法宝", "名字输入错误")
PET_NOT_FOUND_ERROR = "法宝不存在或名称错误，已关闭法宝模块"
PET_TRIAL_NOT_FOUND_ERROR = "法宝不存在或器灵未回应，已关闭器灵试炼"
RE_PET_TOUCH_SUCCESS = re.compile(r"[(（]\s*默契\s*\+\s*\d+\s*[,，]\s*经验\s*\+\s*\d+\s*[)）]")
RE_PET_TRIAL_SUCCESS = re.compile(r"【器灵试炼[·・][^】]+】")

_PET_SCHEDULER_LOCK = asyncio.Lock()
PET_TRIAL_RESOURCE_KEY = "pet_trial"


def _set_pet_next_time(next_time):
    state["next_pet_time"] = float(next_time or 0)
    save_state()


def _set_pet_trial_next_time(next_time):
    state["next_pet_trial_time"] = float(next_time or 0)
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


def _is_pet_trial_reply(text, reply_to, matched_family=None):
    if matched_family == "pet_trial":
        return True

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    pet_name = get_pet_trial_name()
    trial_command = get_pet_trial_command()
    return (
        CMD_PET_TRIAL in orig_cmd
        or trial_command in orig_cmd
        or ("器灵试炼" in str(text or "") and pet_name in orig_cmd)
    )


def _is_pet_trial_not_found_reply(text):
    raw_text = str(text or "")
    return "没有这件拥有器灵的法宝" in raw_text and "器灵并未回应" in raw_text


def get_pet_status_text():
    lines = [
        "🗡️ 法宝",
        f"- 已启用：{'是' if state['pet_enabled'] else '否'}",
        f"- 抚摸名称：{get_pet_name()}",
        f"- 下次执行：{fmt_abs_ts(state['next_pet_time'])}（{fmt_remaining(state['next_pet_time'])}）",
        f"- 器灵试炼：{'开启' if state.get('pet_trial_enabled') else '关闭'}",
        f"- 试炼名称：{get_pet_trial_name()}",
        f"- 试炼下次：{fmt_abs_ts(state.get('next_pet_trial_time', 0))}（{fmt_remaining(state.get('next_pet_trial_time', 0))}）",
    ]
    if state.get("pet_last_error"):
        lines.append(f"- 最近异常：{state.get('pet_last_error')}")
    if state.get("pet_trial_last_error"):
        lines.append(f"- 试炼异常：{state.get('pet_trial_last_error')}")
    return "\n".join(lines)


async def handle_pet_cd_fix(text, now, reply_to, matched_family=None):
    if not state["pet_enabled"]:
        return False

    if RE_PET_TOUCH_SUCCESS.search(str(text or "")) and _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        state["pet_last_error"] = ""
        _set_pet_next_time(now + PET_CD + CD_BUFFER_SEC)
        return True

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


async def handle_pet_trial_reply(text, now, reply_to, matched_family=None):
    if not state.get("pet_trial_enabled"):
        return False
    if not _is_pet_trial_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "")
    if RE_PET_TRIAL_SUCCESS.search(raw_text):
        state["pet_trial_last_error"] = ""
        reset_resource_shortage(PET_TRIAL_RESOURCE_KEY)
        _set_pet_trial_next_time(now + PET_TRIAL_CD + CD_BUFFER_SEC)
        return True

    if _is_pet_trial_not_found_reply(raw_text):
        state["pet_trial_enabled"] = False
        state["next_pet_trial_time"] = 0
        state["pet_trial_last_error"] = PET_TRIAL_NOT_FOUND_ERROR
        save_state()
        await send_audit_log("⚠️ 器灵试炼法宝名称异常，已关闭器灵试炼模块。")
        return True

    if "器灵试炼刚结束不久" in raw_text or ("请在" in raw_text and "后再启程" in raw_text):
        wait_sec = parse_wait_time(raw_text)
        if has_wait_time(raw_text):
            state["pet_trial_last_error"] = ""
            reset_resource_shortage(PET_TRIAL_RESOURCE_KEY)
            _set_pet_trial_next_time(now + wait_sec + CD_BUFFER_SEC)
            await send_audit_log(f"⏳ 器灵试炼 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
            return True

    if "修为不足" in raw_text or "灵石不足" in raw_text or "养魂木不足" in raw_text or "资源不足" in raw_text:
        backoff = record_resource_shortage(PET_TRIAL_RESOURCE_KEY, now, reason=raw_text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["pet_trial_last_error"] = f"器灵试炼资源不足: {raw_text[:80]}"
        _set_pet_trial_next_time(due_at)
        await send_audit_log(
            f"⚠️ 器灵试炼资源不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    state["pet_trial_last_error"] = f"未识别的器灵试炼回复: {raw_text[:60]}"
    _set_pet_trial_next_time(now + RETRY_MAX_SEC)
    return False


async def run_pet_scheduler(now):
    if _PET_SCHEDULER_LOCK.locked():
        return
    async with _PET_SCHEDULER_LOCK:
        await _run_pet_scheduler(now)


async def _run_pet_scheduler(now):
    if state.get("pet_enabled") and now >= float(state.get("next_pet_time", 0) or 0):
        p_delay = PET_CD + random.uniform(0, 30)
        msg = await send_game_command(get_pet_command(), track=True, max_retry=1)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            state["pet_last_error"] = "法宝发送失败"
            _set_pet_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 法宝发送失败，稍后重试。")
            return
        _set_pet_next_time(sent_at + p_delay)
        state["pet_last_error"] = ""
        save_state()
        console_log(f"🗡️ 法宝[{get_pet_name()}]已发送，等待回复确认。")
        return

    if state.get("pet_trial_enabled") and now >= float(state.get("next_pet_trial_time", 0) or 0):
        trial_delay = PET_TRIAL_CD + random.uniform(0, 60)
        msg = await send_game_command(get_pet_trial_command(), track=True, max_retry=1)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            state["pet_trial_last_error"] = "器灵试炼发送失败"
            _set_pet_trial_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 器灵试炼发送失败，稍后重试。")
            return
        _set_pet_trial_next_time(sent_at + trial_delay)
        state["pet_trial_last_error"] = ""
        save_state()
        console_log(f"🗡️ 器灵试炼[{get_pet_trial_name()}]已发送，等待回复确认。")


__all__ = [
    "get_pet_status_text",
    "handle_pet_cd_fix",
    "handle_pet_trial_reply",
    "run_pet_scheduler",
]
