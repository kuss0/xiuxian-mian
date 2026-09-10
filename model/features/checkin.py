import random
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from ..config import (
    CMD_CHECKIN,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_RANCH,
    CMD_SECT_TEACH,
    CMD_STARGAZER_COLLECT,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_SOOTHE,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_YINDAO,
    RETRY_MAX_SEC,
    SECT_TEACH_DELAY_MAX_SEC,
    SECT_TEACH_DELAY_MIN_SEC,
)
from ..persistence import mark_dirty, save_state
from ..profile_observation import apply_profile_observation, field_clocks, timestamp, valid_evidence
from ..message_keys import find_message_key, get_message_record, message_key, message_key_parts, pop_message_record
from ..action_guard import close_by_family as close_action_guard_by_family
from ..runtime import (
    _get_identity_client, _resolve_identity_from_message_sender, classify_game_send_block,
    clear_pending_by_reply, console_log, send_audit_log, send_game_command,
)
from ..state import (
    format_window_text,
    get_active_identity_id,
    get_current_identity_id,
    get_game_bot_ids,
    get_game_group_id,
    get_game_group_ids,
    get_identity_account,
    get_identity_enabled,
    get_identity_state,
    get_module_window_hours,
    get_pending_command,
    get_send_as_profile,
    has_identity,
    is_module_available,
    is_auto_delete_sent_messages_enabled,
    state,
)
from ..verified_event import clean_event_type, telegram_event_timestamp
from ..timing import (
    fmt_abs_ts,
    fmt_remaining,
    get_checkin_day_key,
    reset_checkin_daily_state,
    schedule_next_checkin,
    schedule_next_checkin_after_completion,
)


CHECKIN_DONE_HINTS = ("已点卯", "已经点过")
NO_SECT_CHECKIN_HINTS = ("散修无需点卯", "速速寻一宗门拜入")
SECT_DEPENDENT_PENDING_COMMANDS = {
    CMD_CHECKIN,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_TREE_WATER,
    CMD_TREE_GUARD,
    CMD_TREE_STATUS,
    CMD_TREE_HARVEST,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_SOOTHE,
    CMD_STARGAZER_COLLECT,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_RANCH,
    CMD_YINDAO,
    CMD_NODE_SEARCH,
    CMD_NODE_DEFINE,
}


def _is_checkin_reply(reply_to, matched_family=None):
    if matched_family == "checkin":
        return True
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    return CMD_CHECKIN in orig_cmd



def _schedule_checkin_next_day(now):
    return schedule_next_checkin_after_completion(now, persist=False)


def _schedule_checkin_retry(now):
    retry_at = float(now) + RETRY_MAX_SEC
    if _is_checkin_window_time(retry_at):
        state["next_checkin_time"] = retry_at
        return retry_at
    return _schedule_checkin_next_day(now)


def _is_checkin_window_time(ts):
    start_hour_utc, end_hour_utc = get_module_window_hours("点卯")
    utc_time = datetime.fromtimestamp(float(ts), timezone.utc)
    day_start = utc_time.replace(hour=start_hour_utc, minute=0, second=0, microsecond=0)
    day_end = utc_time.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)
    return day_start <= utc_time < day_end


def _has_checkin_pending():
    pending_tasks = state.get("pending_tasks", {})
    last_msg_id = int(state.get("last_checkin_msg_id", 0) or 0)
    if last_msg_id > 0 and find_message_key(pending_tasks, last_msg_id) is not None:
        return True
    for pending in pending_tasks.values():
        if get_pending_command(pending) == CMD_CHECKIN:
            return True
    return False


def _has_recent_checkin_send(now):
    last_msg_id = int(state.get("last_checkin_msg_id", 0) or 0)
    if last_msg_id <= 0:
        return False
    try:
        sent_at = float(get_message_record(
            state.get("my_msg_ids") or {}, last_msg_id, 0,
            chat_id=state.get("last_checkin_chat_id") or None,
        ) or 0)
    except (TypeError, ValueError):
        sent_at = 0
    if sent_at <= 0:
        return False
    if get_checkin_day_key(sent_at) != get_checkin_day_key(now):
        return False
    return 0 <= float(now) - sent_at <= RETRY_MAX_SEC + 60


def is_no_sect_checkin_text(text):
    raw_text = str(text or "")
    return any(keyword in raw_text for keyword in NO_SECT_CHECKIN_HINTS)


def _stop_pending_retries(identity_state, commands):
    pending_tasks = identity_state.get("pending_tasks", {})
    if not isinstance(pending_tasks, dict):
        return False
    changed = False
    normalized_commands = {str(command or "").strip() for command in commands}
    for pending in pending_tasks.values():
        command = get_pending_command(pending)
        if isinstance(pending, dict) and command in normalized_commands and pending.get("max_retry") != 0:
            pending["max_retry"] = 0
            changed = True
    return changed


def _disable_sect_scheduling(identity_state):
    disabled = False
    for field_name in (
        "checkin_enabled",
        "sect_teach_enabled",
        "tower_enabled",
        "tree_enabled",
        "ranch_enabled",
        "stargazer_enabled",
        "guanxing_enabled",
        "tianti_enabled",
        "taiyi_enabled",
        "taiyi_node_search_enabled",
    ):
        disabled = bool(identity_state.get(field_name)) or disabled
        identity_state[field_name] = False
    identity_state["next_checkin_time"] = 0
    identity_state["next_sect_teach_time"] = 0
    # Stopping new work is not evidence that already-sent work did not execute.
    _stop_pending_retries(identity_state, SECT_DEPENDENT_PENDING_COMMANDS)
    mark_dirty()
    return disabled


def _no_sect_reply_evidence(now, reply_to, reply_context, event, event_type):
    identity_id = get_active_identity_id()
    if not identity_id or not has_identity(identity_id) or not isinstance(reply_context, dict):
        return None
    context = reply_context
    chat_id = getattr(event, "chat_id", 0)
    evidence = {
        "source": "telegram", "chat_id": chat_id, "msg_id": getattr(event, "id", 0),
        "edited": clean_event_type(event_type) == "edit",
    }
    observed_at = telegram_event_timestamp(event, event_type)
    if (
        type(getattr(event, "sender_id", None)) is not int or event.sender_id not in get_game_bot_ids()
        or chat_id not in get_game_group_ids() or not valid_evidence(evidence, observed_at)
        or observed_at > max(timestamp(now), time.time()) + 1
        or type(context.get("send_as_id", identity_id)) is not int
        or context.get("send_as_id", identity_id) != identity_id
        or context.get("family") not in (None, "", "checkin")
    ):
        return None
    refs = [getattr(reply_to, "id", None), getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None)]
    refs.extend(context[key] for key in ("root_msg_id", "reply_to_msg_id") if key in context)
    refs = [value for value in refs if value is not None]
    if not refs or any(type(value) is not int or value <= 0 or value != refs[0] for value in refs):
        return None
    root_id = refs[0]
    if evidence["msg_id"] <= root_id:
        return None
    for value in (getattr(reply_to, "chat_id", None), context.get("chat_id")):
        if value is not None and (type(value) is not int or value != chat_id):
            return None
    senders = [getattr(reply_to, "sender_id", None), context.get("reply_to_sender_id")]
    has_sender = False
    for sender in senders:
        if sender is None:
            continue
        if type(sender) is not int:
            return None
        if sender == 0:
            continue
        if _resolve_identity_from_message_sender(SimpleNamespace(sender_id=sender))[0] != identity_id:
            return None
        has_sender = True
    commands = [getattr(reply_to, "raw_text", None), context.get("reply_to_command")]
    commands = [command for command in commands if command not in (None, "")]
    if any(not isinstance(command, str) or command.strip() != CMD_CHECKIN for command in commands):
        return None
    command_at = telegram_event_timestamp(reply_to)
    context_at = timestamp(context.get("reply_to_server_at"))
    if "reply_to_server_at" in context and (
        type(context["reply_to_server_at"]) not in {int, float} or context_at != context["reply_to_server_at"]
    ):
        return None
    if command_at and context_at and command_at != context_at:
        return None
    command_at = command_at or context_at
    identity = get_identity_state(identity_id)
    pending_key = find_message_key(identity.get("pending_tasks", {}), root_id, chat_id=chat_id)
    pending = identity.get("pending_tasks", {}).get(pending_key)
    if pending is not None:
        if (
            not isinstance(pending, dict) or get_pending_command(pending) != CMD_CHECKIN
            or message_key_parts(pending_key, pending)[0] != chat_id
            or pending.get("account_id", get_identity_account(identity_id)) != get_identity_account(identity_id)
        ):
            return None
        command_at = command_at or timestamp(pending.get("send_started_at")) or timestamp(pending.get("sent_at"))
    elif not (has_sender and commands):
        return None
    if not command_at or command_at > observed_at + 1:
        return None
    return identity_id, root_id, observed_at, evidence


async def _apply_no_sect_checkin(text, now, reply_to=None, *, reply_context, event, event_type):
    """None rejects evidence; otherwise return whether owned state changed."""
    if not is_no_sect_checkin_text(text):
        return None
    owned = _no_sect_reply_evidence(now, reply_to, reply_context, event, event_type)
    if owned is None:
        return None
    identity_id, root_id, observed_at, evidence = owned
    clocks = field_clocks(identity_id)
    if clocks is None:
        return None
    legacy_at = timestamp(get_send_as_profile(identity_id).get("sect_updated_at")) if "sect_name" not in clocks else 0
    accepted = {} if observed_at <= legacy_at else apply_profile_observation(
        identity_id, {"sect_name": "散修"}, observed_at, evidence=evidence,
    )
    if accepted is None:
        return None
    disabled = _disable_sect_scheduling(get_identity_state(identity_id)) if accepted else False
    cleanup = clear_pending_by_reply(send_as_id=identity_id, reply_context={
        "send_as_id": identity_id, "family": "checkin", "chat_id": evidence["chat_id"],
        "root_msg_id": root_id, "reply_to_msg_id": root_id,
    })
    closed = close_action_guard_by_family(
        "checkin", send_as_id=identity_id, expected_msg_id=root_id, expected_chat_id=evidence["chat_id"],
        reason="no_sect_reply", now=now,
    )
    changed = bool(accepted or cleanup["removed_ids"] or closed)
    if changed:
        save_state()
    if disabled:
        await send_audit_log("⚠️ 当前身份无宗门，已关闭点卯、传功及宗门限定模块。", scope="identity", send_as_id=identity_id)
        console_log("⚠️ 散修无需点卯，已停止宗门功能。", scope="identity", send_as_id=identity_id)
    return changed


def _clear_unavailable_checkin_modules():
    identity_state = get_identity_state()
    disabled_modules = []

    if identity_state.get("checkin_enabled") and not is_module_available("点卯"):
        identity_state["checkin_enabled"] = False
        identity_state["next_checkin_time"] = 0
        _stop_pending_retries(identity_state, {CMD_CHECKIN})
        disabled_modules.append("点卯")

    if identity_state.get("sect_teach_enabled") and not is_module_available("宗门传功"):
        identity_state["sect_teach_enabled"] = False
        identity_state["next_sect_teach_time"] = 0
        identity_state["sect_teach_reply_to_msg_id"] = 0
        identity_state["sect_teach_reply_chat_id"] = 0
        _stop_pending_retries(identity_state, {CMD_SECT_TEACH})
        disabled_modules.append("宗门传功")

    if disabled_modules:
        mark_dirty()
    return disabled_modules



def apply_checkin_completion(now, reply_to_msg_id=0, *, chat_id=0):
    day_key = get_checkin_day_key(now)
    if day_key < max(str(state.get("checkin_teach_day") or ""), str(state.get("last_checkin_done_day") or "")):
        return False
    changed = False
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        changed = True
    first_completion = state["last_checkin_done_day"] != day_key
    if first_completion:
        state["last_checkin_done_day"] = day_key
        changed = True
    next_ts = float(state.get("next_checkin_time", 0) or 0)
    if first_completion or next_ts <= now or get_checkin_day_key(next_ts) == day_key:
        _schedule_checkin_next_day(now)
        changed = True
    key = _checkin_message_key(reply_to_msg_id, chat_id=chat_id)
    if key and key[1] > 0 and (
        first_completion or not state.get("last_checkin_msg_id") or not state.get("last_checkin_chat_id")
    ):
        state["last_checkin_msg_id"] = key[1]
        state["last_checkin_chat_id"] = key[0]
        remember_checkin_cleanup_msg_id(key[1], chat_id=key[0])
        changed = True
    # A repeated checkin must not rewind a queued or already-started teaching chain.
    if (
        state.get("sect_teach_enabled") and state["checkin_teach_count"] == 0
        and not state.get("last_sect_teach_msg_id") and not state.get("next_sect_teach_time")
        and state.get("last_checkin_msg_id") and state.get("last_checkin_chat_id")
    ):
        changed = schedule_sect_teach_chain(
            now, state["last_checkin_msg_id"], reply_chat_id=state["last_checkin_chat_id"],
        ) or changed
    if changed:
        mark_dirty()
    return changed



def _normalize_checkin_schedule(now):
    day_key = get_checkin_day_key(now)
    next_checkin_time = float(state.get("next_checkin_time", 0) or 0)
    if _has_checkin_pending() or _has_recent_checkin_send(now):
        return next_checkin_time, True

    if state["last_checkin_done_day"] == day_key:
        if next_checkin_time <= 0 or get_checkin_day_key(next_checkin_time) == day_key:
            next_checkin_time = _schedule_checkin_next_day(now)
            save_state()
        return next_checkin_time, True

    if next_checkin_time <= 0:
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    if not _is_checkin_window_time(next_checkin_time):
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    if now >= next_checkin_time and not _is_checkin_window_time(now):
        schedule_next_checkin(now, persist=False)
        mark_dirty()
        return float(state.get("next_checkin_time", 0) or 0), True

    return next_checkin_time, False



def get_checkin_status_text():
    today_key = get_checkin_day_key()
    lines = [
        "📝 点卯",
        f"- 今日点卯是否已完成：{'是' if state['last_checkin_done_day'] == today_key else '否'}",
        f"- 下次执行：{fmt_abs_ts(state['next_checkin_time'])}（{fmt_remaining(state['next_checkin_time'])}）",
        f"- 执行窗口：{format_window_text('点卯')}",
    ]
    return "\n".join(lines)


def get_sect_teach_status_text():
    today_key = get_checkin_day_key()
    lines = [
        "📘 宗门传功",
        f"- 今日传功是否已完成：{'是' if state['checkin_teach_count'] >= 3 else '否'}（{state['checkin_teach_count']}/3）",
        f"- 下次传功：{fmt_abs_ts(state['next_sect_teach_time'])}（{fmt_remaining(state['next_sect_teach_time'])}）",
        f"- 点卯锚点：{'今日已记录' if state['last_checkin_done_day'] == today_key and state.get('last_checkin_msg_id') else '未记录'}",
    ]
    return "\n".join(lines)


def schedule_sect_teach_chain(now, reply_to_msg_id, *, reply_chat_id=0):
    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)

    if not is_module_available("宗门传功"):
        state["sect_teach_enabled"] = False
        state["next_sect_teach_time"] = 0
        state["sect_teach_reply_to_msg_id"] = 0
        state["sect_teach_reply_chat_id"] = 0
        _stop_pending_retries(state, {CMD_SECT_TEACH})
        save_state()
        return False

    key = _checkin_message_key(reply_to_msg_id, chat_id=reply_chat_id)
    if not state.get("sect_teach_enabled") or state["checkin_teach_count"] >= 3 or not key or not key[0]:
        state["next_sect_teach_time"] = 0
        state["sect_teach_reply_to_msg_id"] = 0
        state["sect_teach_reply_chat_id"] = 0
        save_state()
        return False

    state["next_sect_teach_time"] = now + random.uniform(SECT_TEACH_DELAY_MIN_SEC, SECT_TEACH_DELAY_MAX_SEC)
    state["sect_teach_reply_to_msg_id"] = reply_to_msg_id
    state["sect_teach_reply_chat_id"] = key[0]
    save_state()
    return True


def is_checkin_already_done_text(text):
    return any(keyword in text for keyword in CHECKIN_DONE_HINTS)


def is_checkin_completion_text(text):
    return "点卯成功" in str(text or "") or is_checkin_already_done_text(str(text or ""))


def is_sect_teach_already_done_text(text):
    return any(k in text for k in ["已经传功", "已传功"])


def _checkin_message_key(msg_id, *, chat_id=0):
    try:
        key = message_key(tuple(msg_id) if isinstance(msg_id, list) else msg_id, chat_id)
    except (TypeError, ValueError, OverflowError):
        return None
    if key[0]:
        return key
    chats = set()
    for records in (state.get("my_msg_ids") or {}, state.get("pending_tasks") or {}):
        for candidate, item in records.items():
            try:
                candidate_chat, candidate_id = message_key_parts(candidate, item)
            except (TypeError, ValueError, OverflowError):
                continue
            if candidate_id == key[1] and candidate_chat:
                chats.add(candidate_chat)
    return (next(iter(chats)), key[1]) if len(chats) == 1 else key


def remember_checkin_cleanup_msg_id(msg_id, *, chat_id=0):
    key = _checkin_message_key(msg_id, chat_id=chat_id)
    if key is None:
        return
    msg_ids = state.setdefault("checkin_cleanup_msg_ids", [])
    if list(key) not in msg_ids:
        msg_ids.append(list(key))
        mark_dirty()


def remember_sect_teach_completion(msg_id, *, chat_id=0, reported_count=None):
    key = _checkin_message_key(msg_id, chat_id=chat_id)
    completed = state.setdefault("sect_teach_completed_message_keys", [])
    if not key or not key[0] or list(key) in completed or state["checkin_teach_count"] >= 3:
        return False
    if reported_count is not None and not state["checkin_teach_count"] < reported_count <= 3:
        return False
    completed.append(list(key))
    state["checkin_teach_count"] = min(3, reported_count if reported_count is not None else state["checkin_teach_count"] + 1)
    mark_dirty()
    return True


async def cleanup_checkin_chain_messages():
    identity_id = get_current_identity_id()
    identity = get_identity_state(identity_id)
    owner_account = get_identity_account(identity_id)
    msg_ids = list(identity.get("checkin_cleanup_msg_ids", []))
    if not msg_ids:
        return
    if not is_auto_delete_sent_messages_enabled():
        state["checkin_cleanup_msg_ids"] = []
        save_state()
        return
    entries = [(entry, _checkin_message_key(entry)) for entry in msg_ids]
    deleted_keys = set()
    try:
        from ..runtime import _get_identity_client_with_account, _run_account_rpc
        account_id, client = _get_identity_client_with_account()
        msg_ids_by_chat = {}
        for _entry, key in entries:
            if key and key[0]:
                msg_ids_by_chat.setdefault(key[0], set()).add(key[1])
        for chat_id, routed_msg_ids in msg_ids_by_chat.items():
            if (
                not has_identity(identity_id)
                or get_identity_state(identity_id) is not identity
                or get_identity_account(identity_id) != owner_account
            ):
                return
            if not is_auto_delete_sent_messages_enabled():
                break
            await _run_account_rpc(
                client.delete_messages(chat_id, sorted(routed_msg_ids)),
                account_id=account_id,
                client_obj=client,
            )
            if (
                not has_identity(identity_id)
                or get_identity_state(identity_id) is not identity
                or get_identity_account(identity_id) != owner_account
            ):
                return
            for msg_id in routed_msg_ids:
                pop_message_record(identity["my_msg_ids"], msg_id, chat_id=chat_id)
                deleted_keys.add((chat_id, msg_id))
    except Exception as e:
        print(f"cleanup_checkin_chain_messages failed: {e} | msg_ids={msg_ids}")
    if (
        not has_identity(identity_id)
        or get_identity_state(identity_id) is not identity
        or get_identity_account(identity_id) != owner_account
    ):
        return
    deleted_entries = [entry for entry, key in entries if key in deleted_keys]
    identity["checkin_cleanup_msg_ids"] = [item for item in identity["checkin_cleanup_msg_ids"] if item not in deleted_entries]
    save_state()


async def _notify_sect_teach_completed(*, send_as_id):
    try:
        await send_audit_log("📘 今日传功完成", scope="identity", send_as_id=send_as_id)
    except Exception as e:
        print(f"notify_sect_teach_completed failed: {e}")


async def handle_checkin_reply(text, now, reply_to, matched_family=None, *, event=None, reply_context=None):
    if not _is_checkin_reply(reply_to, matched_family=matched_family):
        return False

    if is_no_sect_checkin_text(text):
        return (await _apply_no_sect_checkin(
            text, now, reply_to, reply_context=reply_context, event=event,
            event_type=reply_context.get("event_type", "message") if isinstance(reply_context, dict) else "message",
        )) is not None

    if not state["checkin_enabled"]:
        return False

    if not is_checkin_completion_text(text):
        return False
    if apply_checkin_completion(
        now, getattr(reply_to, "id", 0), chat_id=int(getattr(reply_to, "chat_id", 0) or 0),
    ):
        save_state()
        console_log(f"📝 点卯已完成→{fmt_abs_ts(state['next_checkin_time'])}")
    return True


async def handle_sect_teach_reply(text, now, reply_to, matched_family=None):
    if not state.get("sect_teach_enabled"):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    if matched_family != "sect_teach" and CMD_SECT_TEACH not in orig_cmd:
        return False

    if "传功玉简已记录！" not in text and not is_sect_teach_already_done_text(text):
        return False
    await apply_sect_teach_reply(
        text, now, int(getattr(reply_to, "id", 0) or 0),
        chat_id=int(getattr(reply_to, "chat_id", 0) or 0),
    )
    return True


async def apply_sect_teach_reply(text, now, reply_id, *, chat_id=0):
    success = "传功玉简已记录！" in text
    if not success and not is_sect_teach_already_done_text(text):
        return False
    day_key = get_checkin_day_key(now)
    if day_key < str(state.get("checkin_teach_day") or ""):
        return False
    changed = False
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        mark_dirty()
        changed = True
    count_match = re.search(r"今日已传功\s*([0-3])\s*/\s*3\s*次", text)
    reported_count = int(count_match[1]) if count_match else None
    if success:
        if not remember_sect_teach_completion(reply_id, chat_id=chat_id, reported_count=reported_count):
            return changed
    else:
        if reported_count is not None and reported_count > state["checkin_teach_count"]:
            state["checkin_teach_count"] = reported_count
            mark_dirty()
            changed = True
        if (
            state["last_sect_teach_msg_id"] == reply_id
            and state["last_sect_teach_chat_id"] == chat_id
            and not any(state.get(key) for key in (
                "next_sect_teach_time", "sect_teach_reply_to_msg_id", "sect_teach_reply_chat_id",
            ))
        ):
            return changed
    state["last_sect_teach_msg_id"] = reply_id
    state["last_sect_teach_chat_id"] = chat_id
    remember_checkin_cleanup_msg_id(reply_id, chat_id=chat_id)
    mark_dirty()

    if success and state["checkin_teach_count"] < 3:
        if state.get("sect_teach_enabled"):
            schedule_sect_teach_chain(now, reply_id, reply_chat_id=chat_id)
        console_log(f"📘 传功成功 {state['checkin_teach_count']}/3")
        return True

    state["next_sect_teach_time"] = 0
    state["sect_teach_reply_to_msg_id"] = 0
    state["sect_teach_reply_chat_id"] = 0
    save_state()
    identity_id = get_current_identity_id()
    owner_state = get_identity_state(identity_id)
    owner_account = get_identity_account(identity_id)
    if not state.get("sect_teach_enabled") or not get_identity_enabled(identity_id):
        return True
    await cleanup_checkin_chain_messages()
    if (
        not has_identity(identity_id)
        or get_identity_state(identity_id) is not owner_state
        or get_identity_account(identity_id) != owner_account
        or not get_identity_enabled(identity_id)
        or not owner_state.get("sect_teach_enabled")
        or owner_state.get("checkin_teach_day") != day_key
    ):
        return True
    if success:
        console_log("📘 传功成功 3/3")
        await _notify_sect_teach_completed(send_as_id=identity_id)
    else:
        console_log(f"📘 传功暂不可执行 {state['checkin_teach_count']}/3")
    return True


async def run_checkin_scheduler(now):
    if not state.get("checkin_enabled") and not state.get("sect_teach_enabled"):
        return

    disabled_modules = _clear_unavailable_checkin_modules()
    if disabled_modules:
        save_state()
        disabled_text = "、".join(disabled_modules)
        await send_audit_log(f"⚠️ 当前身份无宗门或宗门不支持，已关闭{disabled_text}。", scope="identity")
        console_log(f"⚠️ 宗门门禁阻断{disabled_text}，已清理旧调度。")
        return

    day_key = get_checkin_day_key(now)
    if state["checkin_teach_day"] != day_key:
        reset_checkin_daily_state(now)
        mark_dirty()

    if state.get("sect_teach_enabled") and state["next_sect_teach_time"] > 0 and now >= state["next_sect_teach_time"]:
        identity_id = get_current_identity_id()
        identity = get_identity_state(identity_id)
        reply_to_msg_id = state.get("sect_teach_reply_to_msg_id", 0)
        reply_chat_id = int(state.get("sect_teach_reply_chat_id") or 0)
        if reply_to_msg_id and reply_chat_id and state["checkin_teach_count"] < 3:
            expected = (reply_to_msg_id, reply_chat_id, state["next_sect_teach_time"], state["checkin_teach_count"])
            msg = await send_game_command(
                CMD_SECT_TEACH, track=False, reply_to=reply_to_msg_id, target_chat_id=reply_chat_id,
            )
            if not has_identity(identity_id) or get_identity_state(identity_id) is not identity:
                return
            if not state.get("sect_teach_enabled") or expected != (
                state["sect_teach_reply_to_msg_id"], state["sect_teach_reply_chat_id"],
                state["next_sect_teach_time"], state["checkin_teach_count"],
            ):
                return
            if msg:
                state["last_sect_teach_msg_id"] = msg.id
                state["last_sect_teach_chat_id"] = int(getattr(msg, "chat_id", 0) or reply_chat_id)
                state["next_sect_teach_time"] = 0
                state["sect_teach_reply_to_msg_id"] = 0
                state["sect_teach_reply_chat_id"] = 0
                save_state()
                console_log(f"📘 执行传功 {state['checkin_teach_count'] + 1}/3")
            else:
                failed_at = time.time()
                state["next_sect_teach_time"] = failed_at + RETRY_MAX_SEC
                save_state()
                send_block = classify_game_send_block(command=CMD_SECT_TEACH)
                if send_block.get("status") == "unsent":
                    console_log(
                        f"📘 传功未发送：{send_block.get('code') or 'runtime_block'}，延后至 {fmt_abs_ts(state['next_sect_teach_time'])}"
                    )
                elif send_block.get("status") == "unknown":
                    await send_audit_log(
                        f"⚠️ 传功发送状态未知，保留链路并延后至 {fmt_abs_ts(state['next_sect_teach_time'])}。"
                    )
                else:
                    await send_audit_log("❌ 传功发送失败，稍后重试。")
        else:
            if reply_to_msg_id and not reply_chat_id:
                console_log("⚠️ 传功缺少原始群，停止旧链路并等待有锚点的回包。")
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
            state["sect_teach_reply_chat_id"] = 0
            mark_dirty()

    if not state.get("checkin_enabled"):
        return

    next_checkin_time, should_return = _normalize_checkin_schedule(now)
    if should_return:
        return

    if now >= next_checkin_time:
        identity_id = get_current_identity_id()
        identity = get_identity_state(identity_id)
        expected = (state["next_checkin_time"], state["last_checkin_done_day"], state["last_checkin_msg_id"])
        msg = await send_game_command(CMD_CHECKIN, max_retry=1)
        if not has_identity(identity_id) or get_identity_state(identity_id) is not identity:
            return
        if not state.get("checkin_enabled") or expected != (
            state["next_checkin_time"], state["last_checkin_done_day"], state["last_checkin_msg_id"],
        ):
            return
        if not msg:
            failed_at = time.time()
            _schedule_checkin_retry(failed_at)
            save_state()
            send_block = classify_game_send_block(command=CMD_CHECKIN)
            if send_block.get("status") == "unsent":
                console_log(
                    f"📝 点卯未发送：{send_block.get('code') or 'runtime_block'}，延后至 {fmt_abs_ts(state['next_checkin_time'])}"
                )
            elif send_block.get("status") == "unknown":
                await send_audit_log(
                    f"⚠️ 点卯发送状态未知，延后至 {fmt_abs_ts(state['next_checkin_time'])} 等待被动校准。"
                )
            else:
                await send_audit_log("❌ 点卯发送失败，稍后重试。")
            return
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        msg_id = int(getattr(msg, "id", 0) or 0)
        if msg_id:
            state["last_checkin_msg_id"] = msg_id
            state["last_checkin_chat_id"] = int(getattr(msg, "chat_id", 0) or 0)
            key = message_key(msg, getattr(msg, "chat_id", 0) or 0)
            state.setdefault("my_msg_ids", {})[key] = sent_at
            remember_checkin_cleanup_msg_id(msg_id, chat_id=state["last_checkin_chat_id"])
        next_ts = _schedule_checkin_next_day(sent_at)
        save_state()
        console_log(f"📝 执行点卯，等待回复→{fmt_abs_ts(next_ts)}")


__all__ = [
    "apply_checkin_completion",
    "apply_sect_teach_reply",
    "cleanup_checkin_chain_messages",
    "get_checkin_status_text",
    "get_sect_teach_status_text",
    "handle_checkin_reply",
    "handle_sect_teach_reply",
    "is_checkin_already_done_text",
    "is_checkin_completion_text",
    "is_no_sect_checkin_text",
    "is_sect_teach_already_done_text",
    "remember_checkin_cleanup_msg_id",
    "run_checkin_scheduler",
    "schedule_sect_teach_chain",
]
