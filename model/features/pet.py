import asyncio
import random
import re
import time
from types import SimpleNamespace

from ..config import CD_BUFFER_SEC, CMD_PET, CMD_PET_WARM, CMD_PET_TRIAL, CMD_PET_FORMATION, PET_CD, PET_TRIAL_CD, RETRY_MAX_SEC
from ..message_log_recovery import find_message_log_replies, find_recent_message_log_command
from ..persistence import save_state
from ..runtime import classify_game_send_block, clear_pending_tasks_by_commands, console_log, get_sent_message_chat_id, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_game_group_id, get_pending_command, get_pet_command, get_pet_name, get_pet_warm_command, get_pet_warm_name, get_pet_trial_command, get_pet_trial_name, get_pet_formation_command, state
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage
from .storage_bag import apply_storage_bag_item_text_delta


PET_CD_HINT_KEYWORDS = ("尚未恢复", "冷却", "等待", "不足", "休息")
PET_REPLY_HINT_KEYWORDS = ("法宝", "抚摸")
PET_NOT_FOUND_KEYWORDS = ("没有这件拥有器灵的法宝", "名字输入错误")
PET_NOT_FOUND_ERROR = "法宝不存在或名称错误，已关闭法宝模块"
PET_WARM_NOT_FOUND_ERROR = "法宝不存在或名称错误，已关闭温养器灵"
PET_TRIAL_NOT_FOUND_ERROR = "法宝不存在或器灵未回应，已关闭器灵试炼"
PET_REPLY_TIMEOUT_SEC = 30
RE_PET_TOUCH_SUCCESS = re.compile(r"[(（]\s*默契\s*\+\s*\d+\s*[,，]\s*经验\s*\+\s*\d+\s*[)）]")
RE_PET_WARM_SUCCESS = re.compile(r"【温养器灵】")
RE_PET_TRIAL_SUCCESS = re.compile(r"【器灵试炼[·・][^】]+】")
RE_PET_FORMATION_SUCCESS = re.compile(r"剑阵已成|布下了【大庚剑阵】")

_PET_SCHEDULER_LOCK = asyncio.Lock()
PET_TRIAL_RESOURCE_KEY = "pet_trial"
PET_WARM_RESOURCE_KEY = "pet_warm"
PET_WARM_CD = 6 * 3600
PET_FORMATION_BUFF_SEC = 12 * 3600
PET_FORMATION_RETRY_BACKOFF_SEC = 15 * 60
PET_FORMATION_LOG_REPLAY_LOOKBACK_SEC = 5 * 60


def _pet_send_was_definitely_unsent(command):
    return classify_game_send_block(get_current_identity_id(), command).get("status") == "unsent"


def _set_pet_next_time(next_time):
    state["next_pet_time"] = float(next_time or 0)
    save_state()


def _set_pet_trial_next_time(next_time):
    state["next_pet_trial_time"] = float(next_time or 0)
    save_state()


def _set_pet_warm_next_time(next_time):
    state["next_pet_warm_time"] = float(next_time or 0)
    save_state()


def _set_pet_formation_next_time(next_time):
    state["next_pet_formation_time"] = float(next_time or 0)
    save_state()


def _pet_next_time_blocks(key, now):
    return cd_blocks(state.get(key, 0), now, 0)


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


def _is_pet_warm_reply(text, reply_to, matched_family=None):
    if matched_family == "pet_warm":
        return True

    raw_text = str(text or "")
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    pet_name = get_pet_warm_name()
    warm_command = get_pet_warm_command()
    if RE_PET_WARM_SUCCESS.search(raw_text) and pet_name and pet_name in raw_text:
        return _has_pending_pet_warm_command()
    return (
        CMD_PET_WARM in orig_cmd
        or warm_command in orig_cmd
        or ("温养器灵" in raw_text and pet_name in orig_cmd)
    )


def _command_matches_prefix(command, prefix):
    raw_command = str(command or "").strip()
    prefix = str(prefix or "").strip()
    return bool(prefix) and (raw_command == prefix or raw_command.startswith(f"{prefix} "))


def _has_pending_pet_command(*prefixes):
    for pending in state.get("pending_tasks", {}).values():
        pending_command = get_pending_command(pending)
        if any(_command_matches_prefix(pending_command, prefix) for prefix in prefixes):
            return True
    return False


def _has_pending_pet_warm_command():
    return _has_pending_pet_command(CMD_PET_WARM)


def _has_pending_pet_formation_command():
    return _has_pending_pet_command(CMD_PET_FORMATION)


def _clear_pet_pending(*prefixes):
    clear_pending_tasks_by_commands(set(prefixes), send_as_id=get_current_identity_id())


def _is_pet_warm_resource_shortage(text):
    raw_text = str(text or "")
    if any(keyword in raw_text for keyword in ("修为不足", "灵石不足", "养魂木不足", "资源不足")):
        return True
    return "温养器灵需要" in raw_text and "你当前尚缺" in raw_text


def get_pet_status_text():
    lines = [
        "🗡️ 法宝",
        f"- 已启用：{'是' if state['pet_enabled'] else '否'}",
        f"- 抚摸名称：{get_pet_name()}",
        f"- 下次执行：{fmt_abs_ts(state['next_pet_time'])}（{fmt_remaining(state['next_pet_time'])}）",
        f"- 温养器灵：{'开启' if state.get('pet_warm_enabled') else '关闭'}",
        f"- 温养名称：{get_pet_warm_name()}",
        f"- 温养下次：{fmt_abs_ts(state.get('next_pet_warm_time', 0))}（{fmt_remaining(state.get('next_pet_warm_time', 0))}）",
        f"- 器灵试炼：{'开启' if state.get('pet_trial_enabled') else '关闭'}",
        f"- 试炼名称：{get_pet_trial_name()}",
        f"- 试炼下次：{fmt_abs_ts(state.get('next_pet_trial_time', 0))}（{fmt_remaining(state.get('next_pet_trial_time', 0))}）",
        f"- 布下剑阵：{'开启' if state.get('pet_formation_enabled') else '关闭'}",
        f"- 剑阵下次：{fmt_abs_ts(state.get('next_pet_formation_time', 0))}（{fmt_remaining(state.get('next_pet_formation_time', 0))}）",
    ]
    if state.get("pet_last_error"):
        lines.append(f"- 最近异常：{state.get('pet_last_error')}")
    if state.get("pet_trial_last_error"):
        lines.append(f"- 试炼异常：{state.get('pet_trial_last_error')}")
    if state.get("pet_warm_last_error"):
        lines.append(f"- 温养异常：{state.get('pet_warm_last_error')}")
    if state.get("pet_formation_last_error"):
        lines.append(f"- 剑阵异常：{state.get('pet_formation_last_error')}")
    return "\n".join(lines)


async def handle_pet_cd_fix(text, now, reply_to, matched_family=None):
    if not state["pet_enabled"]:
        return False

    if RE_PET_TOUCH_SUCCESS.search(str(text or "")) and _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        state["pet_last_error"] = ""
        _clear_pet_pending(CMD_PET)
        _set_pet_next_time(now + PET_CD + CD_BUFFER_SEC)
        return True

    if _is_pet_not_found_reply(text) and _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        state["pet_enabled"] = False
        state["next_pet_time"] = 0
        state["pet_last_error"] = PET_NOT_FOUND_ERROR
        _clear_pet_pending(CMD_PET)
        save_state()
        await send_audit_log("⚠️ 法宝名称错误，已关闭法宝模块。")
        return True

    if not any(keyword in text for keyword in PET_CD_HINT_KEYWORDS):
        return False

    wait_sec = parse_wait_time(text)
    if not has_wait_time(text) or not _is_pet_cd_reply(text, reply_to, matched_family=matched_family):
        return False

    state["pet_last_error"] = ""
    _clear_pet_pending(CMD_PET)
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
        _clear_pet_pending(CMD_PET_TRIAL)
        _set_pet_trial_next_time(now + PET_TRIAL_CD + CD_BUFFER_SEC)
        return True

    if _is_pet_trial_not_found_reply(raw_text):
        state["pet_trial_enabled"] = False
        state["next_pet_trial_time"] = 0
        state["pet_trial_last_error"] = PET_TRIAL_NOT_FOUND_ERROR
        _clear_pet_pending(CMD_PET_TRIAL)
        save_state()
        await send_audit_log("⚠️ 器灵试炼法宝名称异常，已关闭器灵试炼模块。")
        return True

    if "器灵试炼刚结束不久" in raw_text or ("请在" in raw_text and "后再启程" in raw_text):
        wait_sec = parse_wait_time(raw_text)
        if has_wait_time(raw_text):
            state["pet_trial_last_error"] = ""
            reset_resource_shortage(PET_TRIAL_RESOURCE_KEY)
            _clear_pet_pending(CMD_PET_TRIAL)
            _set_pet_trial_next_time(now + wait_sec + CD_BUFFER_SEC)
            await send_audit_log(f"⏳ 器灵试炼 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
            return True

    if "修为不足" in raw_text or "灵石不足" in raw_text or "养魂木不足" in raw_text or "资源不足" in raw_text:
        backoff = record_resource_shortage(PET_TRIAL_RESOURCE_KEY, now, reason=raw_text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["pet_trial_last_error"] = f"器灵试炼资源不足: {raw_text[:80]}"
        _clear_pet_pending(CMD_PET_TRIAL)
        _set_pet_trial_next_time(due_at)
        await send_audit_log(
            f"⚠️ 器灵试炼资源不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    state["pet_trial_last_error"] = f"未识别的器灵试炼回复: {raw_text[:60]}"
    _clear_pet_pending(CMD_PET_TRIAL)
    _set_pet_trial_next_time(now + RETRY_MAX_SEC)
    return False


async def handle_pet_warm_reply(text, now, reply_to, matched_family=None):
    if not state.get("pet_warm_enabled"):
        return False
    if not _is_pet_warm_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "")
    if RE_PET_WARM_SUCCESS.search(raw_text):
        state["pet_warm_last_error"] = ""
        reset_resource_shortage(PET_WARM_RESOURCE_KEY)
        _clear_pet_pending(CMD_PET_WARM)
        apply_storage_bag_item_text_delta(get_current_identity_id(), raw_text, sign=-1, allow_plain=True)
        _set_pet_warm_next_time(now + PET_WARM_CD + random.uniform(60, 300))
        return True

    if _is_pet_not_found_reply(raw_text):
        state["pet_warm_enabled"] = False
        state["next_pet_warm_time"] = 0
        state["pet_warm_last_error"] = PET_WARM_NOT_FOUND_ERROR
        _clear_pet_pending(CMD_PET_WARM)
        save_state()
        await send_audit_log("⚠️ 温养器灵法宝名称异常，已关闭温养器灵模块。")
        return True

    if "器灵方才吞纳过灵机" in raw_text or ("请在" in raw_text and "后再行温养" in raw_text):
        wait_sec = parse_wait_time(raw_text)
        if has_wait_time(raw_text):
            state["pet_warm_last_error"] = ""
            reset_resource_shortage(PET_WARM_RESOURCE_KEY)
            _clear_pet_pending(CMD_PET_WARM)
            _set_pet_warm_next_time(now + wait_sec + CD_BUFFER_SEC)
            await send_audit_log(f"⏳ 温养器灵 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
            return True

    if _is_pet_warm_resource_shortage(raw_text):
        backoff = record_resource_shortage(PET_WARM_RESOURCE_KEY, now, reason=raw_text)
        due_at = float(backoff.get("next_at", 0) or 0)
        state["pet_warm_last_error"] = f"温养器灵资源不足: {raw_text[:80]}"
        _clear_pet_pending(CMD_PET_WARM)
        _set_pet_warm_next_time(due_at)
        await send_audit_log(
            f"⚠️ 温养器灵资源不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        return True

    state["pet_warm_last_error"] = f"未识别的温养器灵回复: {raw_text[:60]}"
    _clear_pet_pending(CMD_PET_WARM)
    _set_pet_warm_next_time(now + RETRY_MAX_SEC)
    return False


def _is_pet_formation_reply(text, reply_to, matched_family=None):
    if matched_family == "pet_formation":
        return True

    raw_text = str(text or "")
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if CMD_PET_FORMATION in orig_cmd:
        return True
    return bool(RE_PET_FORMATION_SUCCESS.search(raw_text) and _has_pending_pet_formation_command())


async def handle_pet_formation_reply(text, now, reply_to, matched_family=None):
    if not state.get("pet_formation_enabled"):
        return False
    if not _is_pet_formation_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "")
    if RE_PET_FORMATION_SUCCESS.search(raw_text):
        state["pet_formation_last_error"] = ""
        state["pet_formation_retry_count"] = 0
        _clear_pet_pending(CMD_PET_FORMATION)
        _set_pet_formation_next_time(now + PET_FORMATION_BUFF_SEC)
        return True

    state["pet_formation_last_error"] = f"未识别的布下剑阵回复: {raw_text[:60]}"
    _clear_pet_pending(CMD_PET_FORMATION)
    _set_pet_formation_next_time(now + PET_FORMATION_RETRY_BACKOFF_SEC)
    return False


async def _recover_pet_formation_reply_from_message_log(now):
    if "等待回执" not in str(state.get("pet_formation_last_error") or ""):
        return False
    identity_id = get_current_identity_id()
    command = get_pet_formation_command()
    sent = find_recent_message_log_command(
        now,
        sender_id=identity_id,
        start_ts=max(0.0, float(now or 0) - PET_FORMATION_LOG_REPLAY_LOOKBACK_SEC),
        lookahead_sec=5,
        chat_id=0,
        command_predicate=lambda entry: (
            str((entry or {}).get("event_type") or "") == "sent"
            and str((entry or {}).get("text") or "").strip() == command
            and str((entry or {}).get("source_module") or "") == "布下剑阵"
        ),
    )
    command_msg_id = int((sent or {}).get("message_id") or 0)
    if command_msg_id <= 0:
        return False
    command_chat_id = int((sent or {}).get("chat_id") or get_sent_message_chat_id(
        command_msg_id,
        default=get_game_group_id(),
        send_as_id=identity_id,
    ))
    replies = find_message_log_replies(
        command_msg_id,
        now,
        lookback_sec=PET_FORMATION_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=5,
        chat_id=command_chat_id,
        predicate=lambda entry: (
            str((entry or {}).get("event_type") or "") in {"message", "edit"}
            and bool((entry or {}).get("sender_is_bot"))
        ),
    )
    reply_to = SimpleNamespace(id=command_msg_id, raw_text=command)
    for entry in replies:
        handled = await handle_pet_formation_reply(
            str((entry or {}).get("text") or ""),
            float((entry or {}).get("ts_epoch") or now),
            reply_to,
            matched_family="pet_formation",
        )
        if handled:
            console_log(f"🗡️ 布下剑阵日志回捞成功，原消息ID={command_msg_id}。")
            return True
    return False


async def run_pet_scheduler(now):
    if _PET_SCHEDULER_LOCK.locked():
        return
    async with _PET_SCHEDULER_LOCK:
        await _run_pet_scheduler(now)


async def _run_pet_scheduler(now):
    if state.get("pet_formation_enabled") and not _pet_next_time_blocks("next_pet_formation_time", now):
        if await _recover_pet_formation_reply_from_message_log(now):
            return
        if _has_pending_pet_command(CMD_PET_FORMATION):
            return
        retry_count = max(0, int(state.get("pet_formation_retry_count", 0) or 0))
        waiting_reply = "等待回执" in str(state.get("pet_formation_last_error") or "")
        if waiting_reply and retry_count >= 1:
            state["pet_formation_last_error"] = "布下剑阵回复超时，补发已达 1 次上限"
            state["pet_formation_retry_count"] = 0
            _set_pet_formation_next_time(now + PET_FORMATION_RETRY_BACKOFF_SEC)
            await send_audit_log("⚠️ 布下剑阵回复超时，补发已达 1 次上限，15分钟后重试。")
            return
        msg = await send_game_command(
            get_pet_formation_command(),
            track=True,
            max_retry=0,
            reply_timeout=PET_REPLY_TIMEOUT_SEC,
            source_module="布下剑阵",
        )
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            if _pet_send_was_definitely_unsent(get_pet_formation_command()):
                state["pet_formation_last_error"] = ""
                state["pet_formation_retry_count"] = 0
                _set_pet_formation_next_time(sent_at + random.uniform(10 * 60, 30 * 60))
                return
            state["pet_formation_last_error"] = "布下剑阵发送失败"
            state["pet_formation_retry_count"] = 0
            _set_pet_formation_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 布下剑阵发送失败，稍后重试。")
            return
        state["pet_formation_retry_count"] = retry_count + 1 if waiting_reply else 0
        state["pet_formation_last_error"] = "布下剑阵已发送，等待回执确认"
        _set_pet_formation_next_time(sent_at + PET_REPLY_TIMEOUT_SEC)
        console_log("🗡️ 布下剑阵已发送，等待回复确认。")
        return

    if state.get("pet_enabled") and not _pet_next_time_blocks("next_pet_time", now):
        if _has_pending_pet_command(CMD_PET):
            return
        p_delay = PET_CD + random.uniform(0, 30)
        msg = await send_game_command(get_pet_command(), track=True, max_retry=1, reply_timeout=PET_REPLY_TIMEOUT_SEC)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            if _pet_send_was_definitely_unsent(get_pet_command()):
                state["pet_last_error"] = ""
                _set_pet_next_time(sent_at + random.uniform(10 * 60, 30 * 60))
                return
            state["pet_last_error"] = "法宝发送失败"
            _set_pet_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 法宝发送失败，稍后重试。")
            return
        _set_pet_next_time(sent_at + p_delay)
        state["pet_last_error"] = "法宝已发送，等待回执确认"
        save_state()
        console_log(f"🗡️ 法宝[{get_pet_name()}]已发送，等待回复确认。")
        return

    if state.get("pet_trial_enabled") and not _pet_next_time_blocks("next_pet_trial_time", now):
        if _has_pending_pet_command(CMD_PET_TRIAL):
            return
        trial_delay = PET_TRIAL_CD + random.uniform(0, 60)
        msg = await send_game_command(get_pet_trial_command(), track=True, max_retry=1, reply_timeout=PET_REPLY_TIMEOUT_SEC)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            if _pet_send_was_definitely_unsent(get_pet_trial_command()):
                state["pet_trial_last_error"] = ""
                _set_pet_trial_next_time(sent_at + random.uniform(10 * 60, 30 * 60))
                return
            state["pet_trial_last_error"] = "器灵试炼发送失败"
            _set_pet_trial_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 器灵试炼发送失败，稍后重试。")
            return
        _set_pet_trial_next_time(sent_at + trial_delay)
        state["pet_trial_last_error"] = "器灵试炼已发送，等待回执确认"
        save_state()
        console_log(f"🗡️ 器灵试炼[{get_pet_trial_name()}]已发送，等待回复确认。")
        return

    if state.get("pet_warm_enabled") and not _pet_next_time_blocks("next_pet_warm_time", now):
        if _has_pending_pet_command(CMD_PET_WARM):
            return
        warm_delay = PET_WARM_CD + random.uniform(60, 300)
        msg = await send_game_command(get_pet_warm_command(), track=True, max_retry=1, reply_timeout=PET_REPLY_TIMEOUT_SEC)
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            if _pet_send_was_definitely_unsent(get_pet_warm_command()):
                state["pet_warm_last_error"] = ""
                _set_pet_warm_next_time(sent_at + random.uniform(10 * 60, 30 * 60))
                return
            state["pet_warm_last_error"] = "温养器灵发送失败"
            _set_pet_warm_next_time(sent_at + RETRY_MAX_SEC)
            await send_audit_log("❌ 温养器灵发送失败，稍后重试。")
            return
        _set_pet_warm_next_time(sent_at + warm_delay)
        state["pet_warm_last_error"] = "温养器灵已发送，等待回执确认"
        save_state()
        console_log(f"🗡️ 温养器灵[{get_pet_warm_name()}]已发送，等待回复确认。")


__all__ = [
    "get_pet_status_text",
    "handle_pet_cd_fix",
    "handle_pet_warm_reply",
    "handle_pet_trial_reply",
    "handle_pet_formation_reply",
    "run_pet_scheduler",
]
