import asyncio
import copy
import math
import random
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from ..config import (
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    RETRY_MAX_SEC,
    TIANTI_CD_RANDOM_MAX_SEC,
    TIANTI_CD_RANDOM_MIN_SEC,
    TIANTI_GANGFENG_CD_SECONDS,
    TIANTI_RANK_CD_SECONDS,
    TZ_LOCAL,
)
from ..message_keys import message_key_parts
from ..message_log_recovery import find_message_log_message, find_message_log_replies, find_recent_message_log_commands, sender_matches_identity
from ..persistence import mark_dirty, save_state
from ..runtime import classify_game_send_block, clear_pending_by_reply, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_game_bot_ids, get_game_group_id, get_game_group_ids, get_global_enabled, get_identity_account, get_identity_enabled, get_identity_state, get_miniapp_auto_config, get_pending_command, get_tianti_rank_choice, has_identity, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key, has_wait_time, parse_wait_time
from ..verified_event import telegram_event_timestamp
from .resource_backoff import record_resource_shortage, reset_resource_shortage

RE_TIANTI_PANEL = re.compile(r"【凌霄云阶】")
RE_TIANTI_PROGRESS = re.compile(r"当前进度[:：]\s*(\d+)\s*/\s*(\d+)\s*阶")
RE_TIANTI_CYCLE = re.compile(r"已完成周天[:：]\s*(\d+)\s*轮")
RE_TIANTI_GANGFENG = re.compile(r"罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)\s*层")
RE_TIANTI_COOLDOWN = re.compile(r"登阶冷却[:：]\s*(.+)")
RE_TIANTI_WENXIN = re.compile(r"问心状态[:：]\s*(.+)")
RE_TIANTI_WENXIN_PANEL = re.compile(r"【问心台回响】")
RE_TIANTI_WENXIN_GAIN_CONTRIB = re.compile(r"你因此获得了\s*(\d+)\s*点宗门贡献")
RE_TIANTI_WENXIN_EXTRA_GANGFENG = re.compile(r"九天罡风顺势入体，你的【罡风淬体】额外提升了\s*(\d+)\s*层")
RE_TIANTI_WENXIN_FAIL = re.compile(r"你今日已在问心台前静坐过一次，道台不会再回应你。")
RE_TIANTI_CLIMB_COST = re.compile(r"你消耗了\s*(\d+)\s*点修为")
RE_TIANTI_CLIMB_GAIN = re.compile(r"本次获得\s*(\d+)\s*点修为[、,，]\s*(\d+)\s*点宗门贡献")
RE_TIANTI_CLIMB_CYCLE = re.compile(r"完成了第\s*(\d+)\s*轮【周天巡天】")
RE_TIANTI_CLIMB_RESULT = re.compile(r"当前云阶进度(?:[:：]|仍为)\s*(\d+)\s*/\s*(\d+)[，,]\s*罡风淬体[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TIANTI_GANGFENG_PANEL = re.compile(r"【九天罡风】")
RE_TIANTI_GANGFENG_COST = re.compile(r"消耗了\s*(\d+)\s*点修为")
RE_TIANTI_GANGFENG_RESULT = re.compile(r"【罡风淬体】提升至\s*(\d+)\s*/\s*(\d+)\s*层")
RE_TIANTI_GANGFENG_FAIL = re.compile(r"九天罡风尚未再聚，请在\s*(.+?)\s*后再(?:施展此术|试)。")
RE_TIANTI_GANGFENG_COOLDOWN = re.compile(r"\.引九天罡风[:：]\s*(.+)")
TIANTI_CLIMB_RESOURCE_KEY = "tianti_climb"
TIANTI_GANGFENG_RESOURCE_KEY = "tianti_gangfeng"
TIANTI_STATUS_FRESH_SEC = 30 * 60
TIANTI_TRIGGER_BUCKET_SEC = 600
TIANTI_GANGFENG_WINDOW_SEC = 600
TIANTI_GANGFENG_INFLIGHT_GATE_SEC = RETRY_MAX_SEC + 10
TIANTI_WENXIN_INFLIGHT_GATE_SEC = RETRY_MAX_SEC + 10
TIANTI_WENXIN_DAY_END_FALLBACK_SEC = 45 * 60
TIANTI_LOG_REPLAY_LOOKBACK_SEC = 15 * 60
TIANTI_REPLAY_INTERVAL_SEC = 300
TIANTI_SOURCE_MODULE = "登天阶"
TIANTI_COMMANDS = {
    "wenxin": (CMD_TIANTI_WENXIN, "tianti_last_wenxin_msg_id"),
    "gangfeng": (CMD_TIANTI_GANGFENG, "tianti_last_gangfeng_msg_id"),
    "status": (CMD_TIANTI_STATUS, "tianti_status_reply_to_msg_id"),
    "climb": (CMD_TIANTI_CLIMB, "tianti_last_climb_msg_id"),
}
TIANTI_UNRESOLVED = {"sending", "sent", "unknown"}
TIANTI_PANEL_REQUIRED = {
    "progress_current", "progress_total", "cycle_count", "gangfeng_level", "gangfeng_total",
    "cooldown_text", "wenxin_status",
}
_TIANTI_RUN_LOCKS = {}
_TIANTI_LEGACY_REPLAY_AFTER = {}


def _number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _message_id(value):
    value = _number(value)
    return int(value) if value == int(value) else 0


def _tianti_due_text(due_at):
    return datetime.fromtimestamp(due_at, TZ_LOCAL).strftime("%H:%M:%S")


def _capture_tianti_owner(send_as_id=None):
    identity_id = _message_id(send_as_id or get_current_identity_id())
    if not has_identity(identity_id):
        return None
    return identity_id, get_identity_state(identity_id), get_identity_account(identity_id)


def _owns_tianti(owner, *, sending=False, explicit_read=False):
    if not owner:
        return False
    identity_id, identity, account_id = owner
    return bool(
        has_identity(identity_id) and get_identity_state(identity_id) is identity
        and get_identity_account(identity_id) == account_id
        and (not sending or (
            account_id > 0 and get_global_enabled() and get_identity_enabled(identity_id)
            and (explicit_read or identity.get("tianti_enabled"))
        ))
    )


def _tianti_plan_snapshot(owner):
    identity_id, identity, _account_id = owner
    return (
        copy.deepcopy({key: value for key, value in identity.items() if (
            key.startswith(("tianti_", "next_tianti_")) and key != "tianti_commands"
        )}),
        get_tianti_rank_choice(identity_id), get_game_group_id(),
        is_tianti_public_status_selected(identity_id), get_global_enabled(), get_identity_enabled(identity_id),
    )


def _tianti_commands():
    records = state.get("tianti_commands", {})
    if not isinstance(records, dict) or records.keys() - TIANTI_COMMANDS.keys():
        return None
    fields = {
        "op_id", "identity_id", "account_id", "command", "started_at", "chat_id", "msg_id",
        "status", "rank_choice", "sent_at", "dispatch_at", "reply_at", "replay_after",
    }
    for kind, record in records.items():
        if not isinstance(record, dict) or record.keys() - fields or (
            record.get("command") != TIANTI_COMMANDS[kind][0]
            or type(record.get("identity_id")) is not int or record["identity_id"] != get_current_identity_id()
            or type(record.get("account_id")) is not int or record["account_id"] <= 0
            or not isinstance(record.get("op_id"), str) or not 0 < len(record["op_id"]) <= 128
            or not isinstance(record.get("rank_choice"), str) or record["rank_choice"] not in TIANTI_RANK_CD_SECONDS
            or not isinstance(record.get("status"), str)
            or record["status"] not in TIANTI_UNRESOLVED | {"unsent", "complete", "expired"}
            or type(record.get("chat_id")) is not int or not record["chat_id"]
            or type(record.get("msg_id")) is not int or record["msg_id"] < 0
            or type(record.get("started_at")) not in {int, float} or _number(record["started_at"]) <= 0
            or (record["status"] in {"sent", "complete"} and record["msg_id"] <= 0)
            or (record["status"] == "unsent" and record["msg_id"] != 0)
            or (record["status"] == "expired" and kind != "status")
            or (record["msg_id"] and not (
                record["started_at"] - 1 <= _number(record.get("dispatch_at", record["started_at"]))
                <= _number(record.get("sent_at"))
            ))
            or (record["status"] == "complete" and (
                _number(record.get("reply_at")) <= 0 or _number(record.get("reply_at")) < record["started_at"] - 1
            ))
            or any(type(record[key]) not in {int, float} or _number(record[key]) <= 0
                   for key in ("sent_at", "dispatch_at", "reply_at", "replay_after") if key in record)
        ):
            return None
    return copy.deepcopy(records)


def _store_tianti_command(kind, record):
    records = _tianti_commands()
    if records is None:
        return False
    records[kind] = dict(record)
    state["tianti_commands"] = records
    mark_dirty()
    return True


def _tianti_native_block_reason():
    records = _tianti_commands()
    if records is None:
        return "天阶命令归属记录异常，保留状态等待核对"
    if any(record["status"] in TIANTI_UNRESOLVED for record in records.values()):
        return "天阶仍有未确认操作，等待原命令反馈"
    pending = state.get("pending_tasks")
    if not isinstance(pending, dict) or any(not isinstance(item, dict) for item in pending.values()):
        return "天阶待办记录异常，保留状态等待核对"
    if any(_has_pending_tianti_command(command) for command, _key in TIANTI_COMMANDS.values()):
        return "天阶仍有旧待办，缺少完整归属时不自动补发"
    # Historical last_*_msg_id values also name completed results, not just work in flight.
    return ""


def _clear_tianti_command_pending(record):
    family = next(f"tianti_{kind}" for kind, spec in TIANTI_COMMANDS.items() if spec[0] == record["command"])
    clear_pending_by_reply(
        send_as_id=record["identity_id"],
        reply_context={
            "send_as_id": record["identity_id"], "root_msg_id": record["msg_id"],
            "reply_to_msg_id": record["msg_id"], "chat_id": record["chat_id"], "family": family,
        },
        clear_family=False,
    )


async def _notify_tianti(message, identity_id):
    try:
        await send_audit_log(message, scope="identity", send_as_id=identity_id, limit=300)
    except Exception as exc:
        console_log(f"天阶结果已保存，通知失败 ({type(exc).__name__})")


async def _send_tianti_command(kind, owner, now, *, trigger_key="", explicit_read=False):
    if not _owns_tianti(owner, sending=True, explicit_read=explicit_read):
        return False
    identity_id, identity, account_id = owner
    command, msg_key = TIANTI_COMMANDS[kind]
    if _tianti_native_block_reason() or not get_game_group_id():
        return False
    if kind in {"wenxin", "gangfeng"} and not identity.get(f"tianti_{kind}_enabled"):
        return False
    previous = _tianti_commands().get(kind)
    if previous and previous["status"] == "unsent" and _number(state.get(f"next_tianti_{kind}_time")) > now:
        return False
    record = {
        "op_id": uuid4().hex, "identity_id": identity_id, "account_id": account_id,
        "command": command, "chat_id": get_game_group_id(), "msg_id": 0,
        "started_at": max(float(now), time.time()), "status": "sending",
        "rank_choice": get_tianti_rank_choice(identity_id),
    }
    _store_tianti_command(kind, record)
    expected = _tianti_plan_snapshot(owner)

    def current_operation():
        if not _owns_tianti(owner):
            return None
        with use_identity(identity_id):
            current = (_tianti_commands() or {}).get(kind)
        if current and all(current.get(key) == record[key] for key in (
            "op_id", "identity_id", "account_id", "command", "started_at", "chat_id", "rank_choice",
        )):
            return current
        return None

    def can_send():
        return bool(
            _owns_tianti(owner, sending=True, explicit_read=explicit_read)
            and current_operation() == record and _tianti_plan_snapshot(owner) == expected
        )

    def note_unsent(reason):
        _store_tianti_command(kind, dict(record, status="unsent"))
        if _tianti_plan_snapshot(owner) == expected:
            state[f"next_tianti_{kind}_time"] = max(float(now), time.time()) + RETRY_MAX_SEC
            state["tianti_last_error"] = reason
        save_state()

    if save_state() is False:
        note_unsent("天阶在途状态未保存，本次未发送")
        return False
    previous_block = classify_game_send_block(identity_id, command)
    try:
        msg = await send_game_command(
            command, max_retry=0, send_as_id=identity_id, priority="chain",
            source_module=TIANTI_SOURCE_MODULE, op_id=record["op_id"], target_chat_id=record["chat_id"],
            operation_check=can_send,
        )
    except (asyncio.CancelledError, Exception):
        if current_operation() == record:
            _store_tianti_command(kind, dict(record, status="unknown"))
            save_state()
        raise
    current = current_operation()
    if current is None:
        return False
    if current["status"] != "sending":
        if current["status"] == "complete":
            _clear_tianti_command_pending(current)
            save_state()
        return current["status"] in {"sent", "complete"}
    at = max(record["started_at"], time.time())
    if not msg:
        block = classify_game_send_block(identity_id, command)
        if (
            block.get("status") == "unsent" and block != previous_block
            and record["started_at"] <= _number(block.get("at")) <= at + 1
        ):
            note_unsent(f"天阶未发送：{block.get('code') or 'blocked'}")
            return False
    msg_id, chat = _message_id(getattr(msg, "id", 0)), _message_id(getattr(msg, "chat_id", 0))
    sent_at = _number(getattr(msg, "sent_at", 0))
    dispatch_at = _number(getattr(msg, "send_started_at", 0))
    known = bool(
        msg_id > 0 and chat == record["chat_id"]
        and record["started_at"] - 1 <= dispatch_at <= sent_at <= at + 1
    )
    current = dict(record, status="sent" if known else "unknown")
    if known:
        current.update(msg_id=msg_id, sent_at=sent_at, dispatch_at=dispatch_at)
    _store_tianti_command(kind, current)
    if _tianti_plan_snapshot(owner) == expected:
        state["tianti_last_error"] = "" if known else f"{command} 发送状态未知，保留原操作等待反馈"
        if known:
            state[msg_key] = msg_id
            if kind == "climb":
                _schedule_tianti_climb_retry(sent_at, rank_choice=record["rank_choice"])
                _calc_tianti_wenxin_plan(sent_at)
            else:
                state[f"next_tianti_{kind}_time"] = sent_at + {
                    "wenxin": TIANTI_WENXIN_INFLIGHT_GATE_SEC,
                    "gangfeng": TIANTI_GANGFENG_INFLIGHT_GATE_SEC,
                    "status": RETRY_MAX_SEC,
                }[kind]
                if kind in {"wenxin", "gangfeng"}:
                    state[f"tianti_{kind}_last_trigger_key"] = str(trigger_key)
                if kind == "gangfeng":
                    state["tianti_gangfeng_status"] = "等待回复"
    save_state()
    if not known:
        await _notify_tianti(f"{command} 发送状态未知，等待原命令反馈，不自动补发。", identity_id)
    return known


def _adopt_tianti_receipt(record, now):
    if record["msg_id"] or record["account_id"] != get_identity_account(record["identity_id"]):
        return record
    candidates = {}
    pending_tasks = state.get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        return record
    for key, pending in pending_tasks.items():
        if not isinstance(pending, dict) or (
            pending.get("op_id") != record["op_id"] or pending.get("cmd") != record["command"]
            or pending.get("source_module") != TIANTI_SOURCE_MODULE
            or ("account_id" in pending and pending["account_id"] != record["account_id"])
        ):
            continue
        try:
            chat, root = message_key_parts(key, pending)
        except (TypeError, ValueError, OverflowError):
            continue
        sent_at, dispatch_at = _number(pending.get("sent_at")), _number(pending.get("send_started_at"))
        if chat == record["chat_id"] and root > 0 and record["started_at"] - 1 <= dispatch_at <= sent_at <= now + 1:
            candidates[chat, root] = (sent_at, dispatch_at)
    if not candidates:
        # The original send window remains searchable after downtime; never
        # select a later command just because it has the same text.
        def owned_send(entry):
            return bool(
                isinstance(entry, dict) and entry.get("event_type") == "sent" and entry.get("op_id") == record["op_id"]
                and entry.get("source_module") == TIANTI_SOURCE_MODULE and entry.get("account_id") == record["account_id"]
                and _message_id(entry.get("chat_id")) == record["chat_id"] and str(entry.get("text", "")).strip() == record["command"]
                and sender_matches_identity(entry.get("sender_id"), record["identity_id"])
                and _message_id(entry.get("message_id")) > 0
            )

        for logged in find_recent_message_log_commands(
            min(now, record["started_at"] + TIANTI_LOG_REPLAY_LOOKBACK_SEC),
            command_predicate=owned_send, start_ts=record["started_at"] - 1, chat_id=record["chat_id"],
            lookback_sec=TIANTI_LOG_REPLAY_LOOKBACK_SEC, lookahead_sec=1,
        ):
            sent_at = _number(logged.get("ts_epoch"))
            if owned_send(logged) and record["started_at"] - 1 <= sent_at <= now + 1:
                candidates[record["chat_id"], int(logged["message_id"])] = (sent_at, record["started_at"])
    if len(candidates) != 1:
        return record
    (chat, root), (sent_at, dispatch_at) = next(iter(candidates.items()))
    return dict(record, msg_id=root, chat_id=chat, sent_at=sent_at, dispatch_at=dispatch_at, status="sent")


def _legacy_tianti_pending(now, records, *, allow_log=False):
    pending_tasks = state.get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        return {}
    identity_id, account_id = get_current_identity_id(), get_identity_account(get_current_identity_id())
    if account_id <= 0:
        return {}
    candidates = {}
    for key, pending in pending_tasks.items():
        if not isinstance(pending, dict):
            continue
        kind = next((kind for kind, spec in TIANTI_COMMANDS.items() if get_pending_command(pending) == spec[0]), None)
        if kind is None or kind in records:
            continue
        try:
            chat, root = message_key_parts(key, pending)
        except (TypeError, ValueError, OverflowError):
            continue
        sent_at = _number(pending.get("sent_at"))
        if not chat or root <= 0 or not 0 < sent_at <= now + 1:
            continue
        candidates.setdefault(kind, []).append((chat, root, sent_at, pending))
    imported = {}
    for kind, items in candidates.items():
        if len(items) != 1:
            continue
        chat, root, sent_at, pending = items[0]
        command = TIANTI_COMMANDS[kind][0]
        account = pending.get("account_id")
        if account is None:
            if not allow_log:
                continue
            logged = find_message_log_message(
                root, sent_at + 1, chat_id=chat, lookback_sec=3, lookahead_sec=0,
                predicate=lambda entry: entry.get("event_type") == "sent",
            ) or {}
            if not (
                logged.get("event_type") == "sent" and _message_id(logged.get("chat_id")) == chat
                and _message_id(logged.get("message_id")) == root and str(logged.get("text", "")).strip() == command
                and sender_matches_identity(logged.get("sender_id"), identity_id)
                and abs(_number(logged.get("ts_epoch")) - sent_at) <= 1
            ):
                continue
            account = logged.get("account_id")
        if type(account) is not int or account != account_id:
            continue
        dispatch_at = _number(pending.get("send_started_at")) or sent_at
        if not 0 < dispatch_at <= sent_at:
            continue
        imported[kind] = {
            "op_id": f"legacy:{chat}:{root}", "identity_id": identity_id, "account_id": account_id,
            "command": command, "chat_id": chat, "msg_id": root, "started_at": dispatch_at,
            "sent_at": sent_at, "dispatch_at": dispatch_at, "status": "sent",
            "rank_choice": get_tianti_rank_choice(identity_id),
        }
    return imported


def _tianti_reply_operation(kind, reply_to, now, context):
    if not isinstance(context, dict) or not has_identity(get_current_identity_id()):
        return None
    records = _tianti_commands()
    if records is None:
        return None
    identity_id = get_current_identity_id()
    chat, root = _message_id(context.get("chat_id")), _message_id(context.get("root_msg_id"))
    at = telegram_event_timestamp(SimpleNamespace(server_event_at=context.get("server_event_at")))
    command = TIANTI_COMMANDS[kind][0]
    if (
        not chat or root <= 0 or context.get("send_as_id") != identity_id
        or _message_id(context.get("sender_id")) not in get_game_bot_ids()
        or _message_id(context.get("msg_id")) <= 0
        or not 0 < at <= max(_number(now), time.time()) + 1
        or (getattr(reply_to, "id", 0) and _message_id(reply_to.id) != root)
        or (getattr(reply_to, "chat_id", 0) and _message_id(reply_to.chat_id) != chat)
        or any(str(value).strip() != command for value in (
            getattr(reply_to, "raw_text", ""), context.get("reply_to_command"),
        ) if value)
        or context.get("reply_to_command_edited")
    ):
        return None
    records.update(_legacy_tianti_pending(max(_number(now), time.time()), records))
    record = records.get(kind)
    if record is None or (
        (record["chat_id"], record["msg_id"]) != (chat, root)
        and context.get("source") == "manual_game_command"
    ):
        if context.get("source") != "manual_game_command" or any(
            item["status"] in TIANTI_UNRESOLVED for other, item in records.items() if other != "status" or kind == "status"
        ):
            return None
        sent_at = (
            telegram_event_timestamp(SimpleNamespace(server_event_at=context["reply_to_server_at"]))
            if "reply_to_server_at" in context else telegram_event_timestamp(reply_to)
        )
        sender_id = context.get("reply_to_sender_id") or getattr(reply_to, "sender_id", 0)
        raw_command = context.get("reply_to_command") or getattr(reply_to, "raw_text", "")
        if (
            not 0 < sent_at <= at + 1 or chat not in get_game_group_ids()
            or get_identity_account(identity_id) <= 0 or str(raw_command).strip() != command
            or not sender_matches_identity(sender_id, identity_id)
            or sent_at <= max((item["started_at"] for item in records.values()), default=0)
        ):
            return None
        record = {
            "op_id": f"manual:{chat}:{root}", "identity_id": identity_id,
            "account_id": get_identity_account(identity_id), "command": command,
            "started_at": sent_at, "sent_at": sent_at, "dispatch_at": sent_at,
            "msg_id": root, "chat_id": chat, "status": "sent",
            "rank_choice": get_tianti_rank_choice(identity_id),
        }
    record = _adopt_tianti_receipt(record, max(_number(now), time.time()))
    if (
        record["status"] not in TIANTI_UNRESOLVED
        or record["account_id"] != get_identity_account(identity_id)
        or ("account_id" in context and context["account_id"] != record["account_id"])
        or (record["chat_id"], record["msg_id"]) != (chat, root)
        or at < _number(record.get("dispatch_at", record["started_at"])) - 1
        or at < max((_number(item.get("reply_at")) for item in records.values()), default=0)
        or at < _number(state.get("tianti_last_status_seen_at"))
        or (kind == "status" and any(
            other != "status" and item["status"] in TIANTI_UNRESOLVED
            for other, item in records.items()
        ))
    ):
        return None
    return dict(record, reply_at=at)


def _set_tianti_next_wenxin_time(next_time, *, persist=False):
    state["next_tianti_wenxin_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_climb_time(next_time, *, persist=False):
    state["next_tianti_climb_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_tianti_next_gangfeng_time(next_time, *, persist=False):
    state["next_tianti_gangfeng_time"] = float(next_time or 0)
    if persist:
        save_state()
    else:
        mark_dirty()


def _schedule_tianti_wenxin_retry(now, *, persist=False):
    next_time = _get_tianti_day_end_ts(now) + random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    _set_tianti_next_wenxin_time(next_time, persist=persist)
    return next_time


def _get_tianti_cd_seconds(rank_choice=None):
    rank_choice = (rank_choice or get_tianti_rank_choice()).strip()
    return int(TIANTI_RANK_CD_SECONDS.get(rank_choice, TIANTI_RANK_CD_SECONDS["普通"]))


def _set_tianti_skip_reason(reason):
    if str(state.get("tianti_last_skip_reason") or "") == reason:
        return False
    state["tianti_last_skip_reason"] = reason
    return True


def _has_pending_tianti_command(command):
    command = str(command or "").strip()
    if not command:
        return False
    for pending in state.get("pending_tasks", {}).values():
        pending_command = get_pending_command(pending)
        if pending_command == command or pending_command.startswith(f"{command} "):
            return True
    return False


def _mark_tianti_status_synced(now, reply_to=None):
    msg_id = int(getattr(reply_to, "id", 0) or 0)
    changed = False
    seen_at = float(now)
    if float(state.get("tianti_last_status_seen_at", 0) or 0) != seen_at:
        state["tianti_last_status_seen_at"] = seen_at
        changed = True
    if msg_id > 0:
        if int(state.get("tianti_last_status_msg_id", 0) or 0) != msg_id:
            state["tianti_last_status_msg_id"] = msg_id
            changed = True
        if int(state.get("tianti_status_reply_to_msg_id", 0) or 0) != msg_id:
            state["tianti_status_reply_to_msg_id"] = msg_id
            changed = True
    if float(state.get("next_tianti_status_time", 0) or 0) != 0:
        state["next_tianti_status_time"] = 0
        changed = True
    return changed


def _reset_tianti_wenxin_daily_state(now):
    state["tianti_last_wenxin_day"] = ""
    state["tianti_wenxin_last_trigger_key"] = ""
    state["tianti_theoretical_max_stage"] = 0
    state["tianti_wenxin_trigger_stage"] = 0
    state["tianti_last_skip_reason"] = ""
    state["next_tianti_wenxin_time"] = 0


def _ensure_tianti_wenxin_daily_state(now):
    today_key = get_day_key(now)
    last_wenxin_day = str(state.get("tianti_last_wenxin_day") or "")
    trigger_key = str(state.get("tianti_wenxin_last_trigger_key") or "")

    should_reset = False
    if last_wenxin_day and last_wenxin_day != today_key:
        should_reset = True
    elif trigger_key and not trigger_key.startswith(f"{today_key}|"):
        should_reset = True

    if not should_reset:
        return False

    _reset_tianti_wenxin_daily_state(now)
    console_log("☁️ 问心日切：reset daily state")
    return True


def _get_tianti_day_end_ts(now):
    local_now = datetime.fromtimestamp(now, TZ_LOCAL)
    next_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_day.timestamp()



def _estimate_tianti_remaining_climb_count(now):
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    day_end_ts = _get_tianti_day_end_ts(now)
    cd_seconds = _get_tianti_cd_seconds()
    if next_climb_time <= 0 or cd_seconds <= 0 or next_climb_time >= day_end_ts:
        return 0
    remaining_count = 1 + int((day_end_ts - next_climb_time - 1) // cd_seconds)
    return max(0, remaining_count)



def _sync_tianti_remaining_climb_count(now):
    remaining_count = _estimate_tianti_remaining_climb_count(now)
    if int(state.get("tianti_remaining_climb_count", 0) or 0) == remaining_count:
        return False
    state["tianti_remaining_climb_count"] = remaining_count
    return True



def _calc_tianti_wenxin_plan(now=None):
    if now is not None:
        _sync_tianti_remaining_climb_count(now)

    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    max_stage = int(state.get("tianti_progress_total", 12) or 12)
    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)

    target_stage = 0
    trigger_stage = 0
    if remaining_count > 0 and 0 <= current_stage <= max_stage:
        if current_stage >= max_stage:
            target_stage = max_stage
            trigger_stage = 0
        else:
            target_stage = max_stage if current_stage + remaining_count >= max_stage else current_stage + remaining_count
            trigger_stage = max(0, target_stage - 1)

    changed = False
    if int(state.get("tianti_theoretical_max_stage", 0) or 0) != target_stage:
        state["tianti_theoretical_max_stage"] = target_stage
        changed = True
    if int(state.get("tianti_wenxin_trigger_stage", 0) or 0) != trigger_stage:
        state["tianti_wenxin_trigger_stage"] = trigger_stage
        changed = True
    return target_stage, trigger_stage, changed


def _tianti_window_bucket(ts):
    return int(float(ts or 0) // TIANTI_TRIGGER_BUCKET_SEC)


def _build_tianti_wenxin_trigger_key(now, trigger_reason=""):
    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    if target_stage <= 0 or next_climb_time <= 0:
        return ""
    return (
        f"{get_day_key(now)}|{current_stage}|{target_stage}|"
        f"bucket={_tianti_window_bucket(next_climb_time)}|{trigger_reason}"
    )


def _is_same_tianti_wenxin_trigger_window(stored_key, trigger_key, now, current_stage, target_stage, next_climb_time, trigger_reason):
    stored_key = str(stored_key or "").strip()
    if not stored_key:
        return False
    if stored_key == str(trigger_key or ""):
        return True

    # Legacy trigger keys used exact next-climb seconds:
    # YYYY-MM-DD|current|target|next_climb_ts|reason.
    parts = stored_key.split("|")
    if len(parts) != 5 or parts[0] != get_day_key(now):
        return False
    if parts[1] != str(int(current_stage or 0)) or parts[2] != str(int(target_stage or 0)):
        return False
    if parts[4] != str(trigger_reason or ""):
        return False
    try:
        stored_next_climb = int(float(parts[3]))
    except (TypeError, ValueError):
        return False
    return _tianti_window_bucket(stored_next_climb) == _tianti_window_bucket(next_climb_time)


def _should_defer_wenxin_by_timer(now, today_key):
    next_wenxin_time = float(state.get("next_tianti_wenxin_time", 0) or 0)
    if next_wenxin_time <= 0 or now >= next_wenxin_time:
        return False
    if str(state.get("tianti_last_error") or "") == "问心台发送失败":
        return True
    if next_wenxin_time - now <= RETRY_MAX_SEC + 60:
        return True
    next_day_key = get_day_key(next_wenxin_time)
    return bool(next_day_key and next_day_key > today_key)


def _should_advance_tianti_wenxin_for_gangfeng(now, current_stage, trigger_stage):
    if trigger_stage <= 0 or current_stage != trigger_stage - 1:
        return False
    if not state.get("tianti_gangfeng_enabled"):
        return False
    if int(state.get("tianti_cycle_count", 0) or 0) < 1:
        return False
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    if next_climb_time <= 0 or next_gangfeng_time <= now:
        return False
    original_wenxin_cycle_start = next_climb_time
    original_wenxin_cycle_end = next_climb_time + _get_tianti_cd_seconds()
    return original_wenxin_cycle_start <= next_gangfeng_time <= original_wenxin_cycle_end


def _should_trigger_tianti_wenxin(now):
    if not state.get("tianti_wenxin_enabled"):
        return False, "wenxin_disabled"
    today_key = get_day_key(now)
    if str(state.get("tianti_last_wenxin_day") or "") == today_key:
        return False, "already_done"
    if _has_pending_tianti_command(CMD_TIANTI_WENXIN):
        return False, "wenxin_pending"
    if _should_defer_wenxin_by_timer(now, today_key):
        return False, "wenxin_not_today"

    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)
    day_end_ts = _get_tianti_day_end_ts(now)
    if remaining_count <= 0:
        if 0 < day_end_ts - now <= TIANTI_WENXIN_DAY_END_FALLBACK_SEC:
            trigger_key = f"{today_key}|day_end_fallback"
            if str(state.get("tianti_wenxin_last_trigger_key") or "") == trigger_key:
                return False, "trigger_key_hit"
            return True, trigger_key
        return False, "remain=0"

    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    progress_total = int(state.get("tianti_progress_total", 12) or 12)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if target_stage <= 0:
        return False, "target=0"
    if next_climb_time <= 0:
        return False, "next_climb=0"

    if target_stage >= progress_total:
        should_use = current_stage == progress_total - 1
        trigger_reason = "final_stage"
    else:
        should_use = remaining_count == 1
        trigger_reason = "last_climb_today"
    if not should_use:
        return False, f"wait_{trigger_reason}"

    window_start = next_climb_time - 600
    if not (window_start <= now < next_climb_time):
        return False, "window_closed"
    trigger_key = _build_tianti_wenxin_trigger_key(now, trigger_reason)
    if _is_same_tianti_wenxin_trigger_window(
        state.get("tianti_wenxin_last_trigger_key"),
        trigger_key,
        now,
        current_stage,
        target_stage,
        next_climb_time,
        trigger_reason,
    ):
        return False, "trigger_key_hit"
    return True, trigger_key


def get_tianti_estimated_wenxin_window_text(now=None):
    if now is None:
        now = datetime.now(TZ_LOCAL).timestamp()

    if not state.get("tianti_wenxin_enabled"):
        return "已关闭"

    if str(state.get("tianti_last_wenxin_day") or "") == get_day_key(now):
        return "今日已问心"

    remaining_count = int(state.get("tianti_remaining_climb_count", 0) or 0)
    if remaining_count <= 0:
        day_end_ts = _get_tianti_day_end_ts(now)
        if 0 < day_end_ts - now <= TIANTI_WENXIN_DAY_END_FALLBACK_SEC:
            return f"日切前兜底：{fmt_abs_ts(now)} - {fmt_abs_ts(day_end_ts)}"
        return f"今日无预计登阶，日切前 {int(TIANTI_WENXIN_DAY_END_FALLBACK_SEC // 60)} 分钟兜底"

    target_stage = int(state.get("tianti_theoretical_max_stage", 0) or 0)
    trigger_stage = int(state.get("tianti_wenxin_trigger_stage", 0) or 0)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    progress_total = int(state.get("tianti_progress_total", 12) or 12)
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)

    if target_stage <= 0:
        return "等待状态同步"
    if next_climb_time <= 0:
        return "等待下次登阶时间"
    if current_stage >= progress_total:
        return "当前已满阶，无需问心"

    note = ""
    if target_stage >= progress_total:
        trigger_stage = progress_total - 1
        if current_stage == trigger_stage:
            window_end = next_climb_time
            note = "下一次登第12阶，优先使用问心台"
        elif current_stage < trigger_stage:
            climbs_until_trigger = trigger_stage - current_stage
            window_end = next_climb_time + climbs_until_trigger * _get_tianti_cd_seconds()
            note = f"预计到达 {trigger_stage}/{progress_total} 后，留给第12阶"
        else:
            return "当前进度已超过第12阶触发点，等待重新规划"
    elif remaining_count == 1:
        window_end = next_climb_time
        note = "今日到不了第12阶，留给今日最后一次登阶"
    else:
        climbs_until_last = max(0, remaining_count - 1)
        window_end = next_climb_time + climbs_until_last * _get_tianti_cd_seconds()
        note = "今日到不了第12阶，留给今日最后一次登阶"

    window_start = max(0, window_end - 600)
    return f"{fmt_abs_ts(window_start)} - {fmt_abs_ts(window_end)}（{note}）"


def _apply_tianti_wenxin_result(raw_text, now, reply_to):
    handled = False
    if RE_TIANTI_WENXIN_FAIL.search(raw_text):
        state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
        state["tianti_wenxin_status"] = "今日已问心"
        state["tianti_last_wenxin_day"] = get_day_key(now)
        _schedule_tianti_wenxin_retry(now, persist=False)
        console_log("☁️ 问心收口：今日已问心")
        return True

    if not RE_TIANTI_WENXIN_PANEL.search(raw_text):
        return False

    state["tianti_last_wenxin_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    state["tianti_wenxin_status"] = "今日已问心，下次登天阶奖励提升"
    state["tianti_last_wenxin_day"] = get_day_key(now)
    console_log("☁️ 问心收口：成功，下次登天阶奖励提升")
    handled = True

    extra_gangfeng_match = RE_TIANTI_WENXIN_EXTRA_GANGFENG.search(raw_text)
    if extra_gangfeng_match:
        extra_level = int(extra_gangfeng_match.group(1) or 0)
        if extra_level > 0:
            state["tianti_gangfeng_level"] = min(
                int(state.get("tianti_gangfeng_total", 12) or 12),
                int(state.get("tianti_gangfeng_level", 0) or 0) + extra_level,
            )
            handled = True

    _schedule_tianti_wenxin_retry(now, persist=False)
    return handled


def _schedule_tianti_climb_retry(now, rank_choice=None, *, persist=False):
    rank_choice = (rank_choice or get_tianti_rank_choice()).strip()
    cd_seconds = int(TIANTI_RANK_CD_SECONDS.get(rank_choice, TIANTI_RANK_CD_SECONDS["普通"]))
    random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    next_time = float(now) + cd_seconds + random_delay
    _set_tianti_next_climb_time(next_time, persist=persist)
    state["tianti_cooldown_text"] = _tianti_due_text(next_time)
    if persist:
        save_state()
    else:
        mark_dirty()
    return next_time


def _schedule_tianti_gangfeng_retry(now, wait_sec=None, *, persist=False):
    base_wait = TIANTI_GANGFENG_CD_SECONDS if wait_sec is None else max(0, int(wait_sec or 0))
    random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
    total_wait_sec = base_wait + random_delay
    next_time = float(now) + total_wait_sec
    state["next_tianti_gangfeng_time"] = float(next_time or 0)
    state["tianti_gangfeng_status"] = _tianti_due_text(next_time)
    if persist:
        save_state()
    else:
        mark_dirty()
    return next_time


def _get_tianti_gangfeng_ready_time(now):
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    if next_climb_time <= 0:
        return float(now)
    return max(float(now), next_climb_time - TIANTI_GANGFENG_WINDOW_SEC)


def _schedule_tianti_gangfeng_ready(now, *, persist=False):
    ready_time = _get_tianti_gangfeng_ready_time(now)
    current_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    changed = abs(current_time - ready_time) > 1
    if changed:
        _set_tianti_next_gangfeng_time(ready_time, persist=persist)
    elif persist:
        save_state()
    return changed


def _parse_tianti_gangfeng_wait_reply(raw_text):
    match = RE_TIANTI_GANGFENG_FAIL.search(str(raw_text or ""))
    if not match:
        return 0
    wait_text = str(match.group(1) or "").strip()
    return parse_wait_time(wait_text) if has_wait_time(wait_text) else 0


def _build_tianti_gangfeng_trigger_key(now, next_climb_time=None):
    if next_climb_time is None:
        next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_climb_time = float(next_climb_time or 0)
    if next_climb_time <= 0:
        return ""
    bucket = _tianti_window_bucket(next_climb_time)
    current_stage = int(state.get("tianti_progress_current", 0) or 0)
    return f"{get_day_key(now)}|stage={current_stage}|bucket={bucket}"


def _is_same_tianti_gangfeng_trigger_window(stored_key, trigger_key, now, next_climb_time):
    stored_key = str(stored_key or "").strip()
    if not stored_key:
        return False
    if stored_key == str(trigger_key or ""):
        return True

    # Legacy trigger keys used exact second timestamps: YYYY-MM-DD|next_climb_ts.
    # Treat those as the same 10-minute pre-climb window so a small status jitter
    # cannot unlock a second gangfeng send.
    parts = stored_key.split("|")
    if len(parts) != 2 or parts[0] != get_day_key(now):
        return False
    try:
        stored_next_climb = int(float(parts[1]))
    except (TypeError, ValueError):
        return False
    current_bucket = _tianti_window_bucket(next_climb_time)
    stored_bucket = _tianti_window_bucket(stored_next_climb)
    return current_bucket == stored_bucket


def _should_trigger_tianti_gangfeng(now):
    if not state.get("tianti_gangfeng_enabled"):
        return False, "gangfeng_disabled"
    if _has_pending_tianti_command(CMD_TIANTI_GANGFENG):
        return False, "gangfeng_pending"
    if int(state.get("tianti_cycle_count", 0) or 0) < 1:
        return False, "cycle<1"
    next_climb_time = float(state.get("next_tianti_climb_time", 0) or 0)
    next_gangfeng_time = float(state.get("next_tianti_gangfeng_time", 0) or 0)
    if next_climb_time <= 0:
        return False, "next_climb=0"
    if next_gangfeng_time > now:
        return False, "gangfeng_cd"
    if next_gangfeng_time <= 0 and str(state.get("tianti_gangfeng_status") or "") != "可用":
        return False, "gangfeng_timer_missing"
    trigger_key = _build_tianti_gangfeng_trigger_key(now, next_climb_time)
    if _is_same_tianti_gangfeng_trigger_window(
        state.get("tianti_gangfeng_last_trigger_key"),
        trigger_key,
        now,
        next_climb_time,
    ):
        return False, "gangfeng_trigger_key_hit"
    window_start = next_climb_time - TIANTI_GANGFENG_WINDOW_SEC
    if not (window_start <= now < next_climb_time):
        return False, "gangfeng_window_closed"
    return True, trigger_key


def _has_tianti_status_snapshot():
    return any(
        value not in {None, "", 0, "未记录"}
        for value in (
            state.get("tianti_progress_current"),
            state.get("tianti_cycle_count"),
            state.get("tianti_gangfeng_level"),
            state.get("tianti_cooldown_text"),
            state.get("tianti_wenxin_status"),
        )
    )


def _has_fresh_tianti_status_snapshot(now):
    if not _has_tianti_status_snapshot():
        return False
    seen_at = float(state.get("tianti_last_status_seen_at", 0) or 0)
    return seen_at > 0 and float(now) - seen_at <= TIANTI_STATUS_FRESH_SEC


def _active_tianti_status_sync_due(now):
    # Gangfeng is timer-driven: do not poll .天阶状态 merely to confirm it.
    return False


def _tianti_status_sync_due(now):
    next_status_time = float(state.get("next_tianti_status_time", 0) or 0)
    if next_status_time > 0 and now >= next_status_time:
        return True
    if not _has_tianti_status_snapshot():
        return True
    if _active_tianti_status_sync_due(now):
        return True
    return False


def is_tianti_status_sync_due(now, send_as_id=None, *, require_enabled=True):
    """Check status freshness against an explicit identity when provided.

    Public Tianjige status sync is a read-only calibration path and may be
    explicitly selected while the climb automation remains disabled.
    """
    timestamp = float(now or time.time())
    if send_as_id is not None:
        try:
            identity_id = int(send_as_id)
        except (TypeError, ValueError, OverflowError):
            identity_id = 0
        if identity_id > 0:
            with use_identity(identity_id):
                enabled = bool(state.get("tianti_enabled"))
                return (enabled or not require_enabled) and _tianti_status_sync_due(timestamp)
    enabled = bool(state.get("tianti_enabled"))
    return (enabled or not require_enabled) and _tianti_status_sync_due(timestamp)


def is_tianti_public_status_selected(send_as_id=None):
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    if identity_id <= 0:
        return False
    config = dict(get_miniapp_auto_config() or {})
    if not bool(config.get("cave_public_tianti_status_enabled")):
        return False
    selected_ids = {
        int(item)
        for item in (config.get("cave_public_tianti_status_identity_ids") or ())
        if str(item or "").strip().lstrip("-").isdigit() and int(item) > 0
    }
    if identity_id not in selected_ids:
        return False
    return bool(config.get("cave_public_entry_urls") or str(config.get("cave_public_entry_url") or "").strip())


async def sync_tianti_status(send_as_id):
    owner = _capture_tianti_owner(send_as_id)
    if not owner or owner[0] in _TIANTI_RUN_LOCKS:
        return False, "天阶身份不可用或已有操作执行中"
    token = object()
    _TIANTI_RUN_LOCKS[owner[0]] = token
    try:
        with use_identity(owner[0]):
            before = _tianti_plan_snapshot(owner)
            if await _recover_due_tianti_replies(time.time()):
                return True, "天阶原操作回包已校准"
            if not _owns_tianti(owner, sending=True, explicit_read=True) or _tianti_plan_snapshot(owner) != before:
                return False, "天阶查询计划已变更"
            _expire_tianti_status_read(time.time())
            ok = await _send_tianti_command("status", owner, time.time(), explicit_read=True)
        return ok, "已发送天阶状态同步，等待回复" if ok else "未新增天阶查询，等待原操作反馈或重新排队"
    finally:
        if _TIANTI_RUN_LOCKS.get(owner[0]) is token:
            _TIANTI_RUN_LOCKS.pop(owner[0], None)


def tianti_miniapp_status_block_reason(now):
    try:
        timestamp = float(now)
        seen_at = float(state.get("tianti_last_status_seen_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return "invalid_clock"
    if not all(math.isfinite(value) for value in (timestamp, seen_at)) or timestamp <= 0:
        return "invalid_clock"
    if seen_at > timestamp:
        return "stale_observation"
    records = _tianti_commands()
    if records is None:
        return "invalid_pending"
    if any(kind != "status" and item["status"] in TIANTI_UNRESOLVED for kind, item in records.items()):
        return "active_pending"
    pending_tasks = state.get("pending_tasks")
    if not isinstance(pending_tasks, dict):
        return "invalid_pending"
    for pending in pending_tasks.values():
        if not isinstance(pending, dict):
            return "invalid_pending"
        family = str(pending.get("family") or "")
        command = get_pending_command(pending).split()
        if (
            (family.startswith("tianti_") and family != "tianti_status")
            or (command and command[0] in {CMD_TIANTI_CLIMB, CMD_TIANTI_WENXIN, CMD_TIANTI_GANGFENG})
        ):
            return "active_pending"
    return ""


def sync_tianti_miniapp_status(raw_text, now=None, *, message_id=0):
    """Apply a complete read-only panel and leave dispatch to the scheduler.

    Native replies share this panel reducer, but a public-entry read does not
    complete or claim a Telegram command's pending operation.
    """
    now = time.time() if now is None else now
    block_reason = tianti_miniapp_status_block_reason(now)
    if block_reason:
        return {"handled": False, "reason": block_reason, "payload": {}}
    now = float(now)
    payload = _parse_tianti_panel(str(raw_text or ""))
    if not payload:
        return {"handled": False, "reason": "panel_unrecognized", "payload": {}}
    if not TIANTI_PANEL_REQUIRED.issubset(payload):
        # An incomplete read must not certify cached progress or rearm timers.
        return {"handled": False, "reason": "incomplete_panel", "payload": payload}

    _ensure_tianti_wenxin_daily_state(now)
    changed = _mark_tianti_status_synced(
        now,
        SimpleNamespace(id=int(message_id or 0)) if int(message_id or 0) > 0 else None,
    )
    changed = _apply_tianti_panel_payload(payload, now=now) or changed
    _calc_tianti_wenxin_plan(now)
    state["tianti_last_error"] = ""
    save_state()
    return {
        "handled": True,
        "changed": bool(changed),
        "payload": dict(payload),
        "message": "天阶状态已由洞府天机阁同步",
    }


def _parse_tianti_panel(text):
    raw_text = re.sub(r"[*_\x60]+", "", str(text or ""))
    if len(RE_TIANTI_PANEL.findall(raw_text)) != 1:
        return None

    payload = {}
    lines = [line.strip().removeprefix("- ").strip() for line in raw_text.splitlines()]
    for label, pattern, keys in (
        ("当前进度", RE_TIANTI_PROGRESS, ("progress_current", "progress_total")),
        ("已完成周天", RE_TIANTI_CYCLE, ("cycle_count",)),
        ("罡风淬体", RE_TIANTI_GANGFENG, ("gangfeng_level", "gangfeng_total")),
        ("登阶冷却", RE_TIANTI_COOLDOWN, ("cooldown_text",)),
        ("问心状态", RE_TIANTI_WENXIN, ("wenxin_status",)),
        (".引九天罡风", RE_TIANTI_GANGFENG_COOLDOWN, ("gangfeng_cooldown_text",)),
    ):
        label_pattern = re.compile(re.escape(label) + r"\s*[:：]")
        count = len(label_pattern.findall(raw_text))
        if count == 0:
            continue
        matches = [pattern.fullmatch(line) for line in lines if label_pattern.search(line)]
        if count != 1 or len(matches) != 1 or matches[0] is None:
            return None
        for key, value in zip(keys, matches[0].groups()):
            try:
                payload[key] = value.strip() if key.endswith(("_text", "_status")) else int(value)
            except (TypeError, ValueError, OverflowError):
                return None
    return payload if _valid_tianti_panel_payload(payload) else None


def _tianti_panel_cooldown(text, *, gangfeng=False):
    """Only explicit ready wording or one well-formed countdown is actionable."""
    raw = str(text or "").strip().rstrip("。！!")
    ready = {"可用", "可立即使用"} if gangfeng else {"可用", "可立即登阶"}
    if raw in ready:
        return "ready", 0
    if gangfeng and raw == "未解锁":
        return "locked", 0
    duration = re.fullmatch(
        r"(?:(?:剩余(?:时间)?|还需等待|需再等待|尚需等待|还需|请在)\s*[:：]?\s*)?"
        r"(?P<duration>(?:\d+\s*(?:小时|分钟|时|分|秒)\s*)+)"
        r"(?:后(?:再试|可用|可登阶|再施展此术))?",
        raw,
    )
    if duration is None:
        return None
    units = {"小时": 3600, "时": 3600, "分钟": 60, "分": 60, "秒": 1}
    parts = re.findall(r"(\d+)\s*(小时|分钟|时|分|秒)", duration.group("duration"))
    order = [units[unit] for _, unit in parts]
    if order != sorted(set(order), reverse=True):
        return None
    try:
        wait_sec = sum(int(value) * units[unit] for value, unit in parts)
        finite = math.isfinite(float(wait_sec))
    except (TypeError, ValueError, OverflowError):
        return None
    if wait_sec <= 0 or not finite:
        return None
    return "waiting", wait_sec


def _tianti_panel_wenxin(text):
    raw = str(text or "").strip()
    ready = "今日尚未问心" in raw
    done = "今日已问心" in raw or "不会再回应" in raw
    if ready == done:
        return ""
    if re.match(r"^今日尚未问心(?:$|[。！!；;，,（(\s])", raw) and not done:
        return "ready"
    if re.match(r"^今日已问心(?:$|[。！!；;，,（(\s])", raw) and not ready:
        return "done"
    return ""


def _valid_tianti_panel_payload(payload):
    if not isinstance(payload, dict) or not payload:
        return False
    for current_key, total_key in (("progress_current", "progress_total"), ("gangfeng_level", "gangfeng_total")):
        if current_key not in payload and total_key not in payload:
            continue
        current, total = payload.get(current_key), payload.get(total_key)
        if type(current) is not int or type(total) is not int or not 0 <= current <= total or total <= 0:
            return False
    if "cycle_count" in payload and (type(payload["cycle_count"]) is not int or payload["cycle_count"] < 0):
        return False
    for key, gangfeng in (("cooldown_text", False), ("gangfeng_cooldown_text", True)):
        if key in payload and _tianti_panel_cooldown(payload[key], gangfeng=gangfeng) is None:
            return False
    return "wenxin_status" not in payload or bool(_tianti_panel_wenxin(payload["wenxin_status"]))


def _apply_tianti_panel_payload(payload, now=None):
    if not _valid_tianti_panel_payload(payload):
        return False
    if now is None:
        now = datetime.now(TZ_LOCAL).timestamp()
    changed = False
    mapping = {
        "progress_current": "tianti_progress_current",
        "progress_total": "tianti_progress_total",
        "cycle_count": "tianti_cycle_count",
        "gangfeng_level": "tianti_gangfeng_level",
        "gangfeng_total": "tianti_gangfeng_total",
        "cooldown_text": "tianti_cooldown_text",
        "wenxin_status": "tianti_wenxin_status",
    }
    for payload_key, state_key in mapping.items():
        if payload_key not in payload:
            continue
        value = payload[payload_key]
        if state.get(state_key) != value:
            state[state_key] = value
            changed = True

    cooldown_text = str(payload.get("cooldown_text") or "")
    if cooldown_text:
        kind, wait_sec = _tianti_panel_cooldown(cooldown_text)
        if kind == "waiting":
            if wait_sec > 0:
                random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
                total_wait_sec = wait_sec + random_delay
                next_climb = float(now + total_wait_sec)
                if abs(float(state.get("next_tianti_climb_time", 0) or 0) - next_climb) > 1:
                    _set_tianti_next_climb_time(next_climb, persist=False)
                    changed = True
                display_text = _tianti_due_text(next_climb)
                if state.get("tianti_cooldown_text") != display_text:
                    state["tianti_cooldown_text"] = display_text
                    changed = True
        elif kind == "ready":
            next_climb = float(state.get("next_tianti_climb_time", 0) or 0)
            if not _has_pending_tianti_command(CMD_TIANTI_CLIMB) and (next_climb <= 0 or next_climb > now):
                _set_tianti_next_climb_time(now, persist=False)
                changed = True

    wenxin_text = str(payload.get("wenxin_status") or "")
    if wenxin_text:
        today_key = get_day_key(now)
        if _tianti_panel_wenxin(wenxin_text) == "ready":
            if str(state.get("tianti_last_wenxin_day") or "") == today_key:
                state["tianti_last_wenxin_day"] = ""
                changed = True
            if float(state.get("next_tianti_wenxin_time", 0) or 0) > 0:
                _set_tianti_next_wenxin_time(0, persist=False)
                changed = True
        elif _tianti_panel_wenxin(wenxin_text) == "done":
            if str(state.get("tianti_last_wenxin_day") or "") != today_key:
                state["tianti_last_wenxin_day"] = today_key
                changed = True
            if float(state.get("next_tianti_wenxin_time", 0) or 0) <= now:
                _schedule_tianti_wenxin_retry(now, persist=False)
                changed = True

    gangfeng_cd_text = str(payload.get("gangfeng_cooldown_text") or "")
    if gangfeng_cd_text:
        kind, wait_sec = _tianti_panel_cooldown(gangfeng_cd_text, gangfeng=True)
        if kind == "locked":
            pass
        elif kind == "ready":
            if _schedule_tianti_gangfeng_ready(now, persist=False):
                changed = True
            if state.get("tianti_gangfeng_status") != "可用":
                state["tianti_gangfeng_status"] = "可用"
                changed = True
        elif kind == "waiting":
            if wait_sec > 0:
                random_delay = random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
                total_wait_sec = wait_sec + random_delay
                next_gangfeng = float(now + total_wait_sec)
                if abs(float(state.get("next_tianti_gangfeng_time", 0) or 0) - next_gangfeng) > 1:
                    _set_tianti_next_gangfeng_time(next_gangfeng, persist=False)
                    changed = True
                display_text = _tianti_due_text(next_gangfeng)
                if state.get("tianti_gangfeng_status") != display_text:
                    state["tianti_gangfeng_status"] = display_text
                    changed = True
    return changed


def _apply_tianti_climb_result(climb_result_match):
    state["tianti_progress_current"] = int(climb_result_match.group(1) or 0)
    state["tianti_progress_total"] = int(climb_result_match.group(2) or 0)
    state["tianti_gangfeng_level"] = int(climb_result_match.group(3) or 0)
    state["tianti_gangfeng_total"] = int(climb_result_match.group(4) or 0)


def get_tianti_status_text():
    now = datetime.now(TZ_LOCAL).timestamp()
    lines = [
        "☁️ 登天阶",
        f"- 当前进度：{int(state.get('tianti_progress_current', 0) or 0)} / {int(state.get('tianti_progress_total', 12) or 12)} 阶",
        f"- 已完成周天：{int(state.get('tianti_cycle_count', 0) or 0)} 轮",
        f"- 罡风淬体：{int(state.get('tianti_gangfeng_level', 0) or 0)} / {int(state.get('tianti_gangfeng_total', 12) or 12)} 层",
        f"- 问心状态：{state.get('tianti_wenxin_status') or '未记录'}",
        f"- 今日剩余次数：{int(state.get('tianti_remaining_climb_count', 0) or 0)}",
        f"- 今日目标阶：{int(state.get('tianti_theoretical_max_stage', 0) or 0)} ｜ 触发阶：{int(state.get('tianti_wenxin_trigger_stage', 0) or 0)}",
        f"- 下次问心：{fmt_abs_ts(float(state.get('next_tianti_wenxin_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_wenxin_time', 0) or 0))}）",
        f"- 预计问心窗口：{get_tianti_estimated_wenxin_window_text(now)}",
        f"- 下次登阶：{fmt_abs_ts(float(state.get('next_tianti_climb_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_climb_time', 0) or 0))}）",
        f"- 下次罡风：{fmt_abs_ts(float(state.get('next_tianti_gangfeng_time', 0) or 0))}（{fmt_remaining(float(state.get('next_tianti_gangfeng_time', 0) or 0))}）",
    ]
    last_gain_xiuwei = int(state.get("tianti_last_gain_xiuwei", 0) or 0)
    last_gain_contrib = int(state.get("tianti_last_gain_contrib", 0) or 0)
    last_cost_xiuwei = int(state.get("tianti_last_cost_xiuwei", 0) or 0)
    if last_gain_xiuwei > 0 or last_gain_contrib > 0 or last_cost_xiuwei > 0:
        lines.append(
            f"- 最近登阶：消耗 {last_cost_xiuwei} 修为｜获得 {last_gain_xiuwei} 修为 / {last_gain_contrib} 贡献"
        )
    if state.get("tianti_last_error"):
        lines.append(f"- 最近异常：{state.get('tianti_last_error')}")
    return "\n".join(lines)


def _parse_tianti_native_result(kind, text):
    raw = re.sub(r"[*_\x60]+", "", str(text or ""))
    if kind in {"climb", "gangfeng"} and ("修为不足" in raw or "资源不足" in raw):
        if RE_TIANTI_CLIMB_COST.search(raw) or RE_TIANTI_CLIMB_GAIN.search(raw) or RE_TIANTI_GANGFENG_RESULT.search(raw):
            return None
        return "shortage", raw
    if kind == "status":
        payload = _parse_tianti_panel(raw)
        return ("status", payload) if payload and TIANTI_PANEL_REQUIRED.issubset(payload) else None
    if kind == "wenxin":
        if RE_TIANTI_WENXIN_FAIL.search(raw) and not RE_TIANTI_WENXIN_GAIN_CONTRIB.search(raw):
            return "wenxin", raw
        if not RE_TIANTI_WENXIN_FAIL.search(raw) and (
            len(RE_TIANTI_WENXIN_PANEL.findall(raw)) == 1
            and len(RE_TIANTI_WENXIN_GAIN_CONTRIB.findall(raw)) == 1
        ):
            return "wenxin", raw
        return None
    wait = _parse_tianti_gangfeng_wait_reply(raw)
    if wait > 0:
        return "gangfeng_wait" if kind == "climb" else "wait", wait
    if kind == "gangfeng":
        matches = list(RE_TIANTI_GANGFENG_RESULT.finditer(raw))
        if len(RE_TIANTI_GANGFENG_PANEL.findall(raw)) == 1 and len(matches) == 1:
            level, total = map(int, matches[0].groups())
            if 0 <= level <= total and total > 0:
                return "gangfeng", (level, total)
        return None
    costs = list(RE_TIANTI_CLIMB_COST.finditer(raw))
    gains = list(RE_TIANTI_CLIMB_GAIN.finditer(raw))
    results = list(RE_TIANTI_CLIMB_RESULT.finditer(raw))
    cycles = list(RE_TIANTI_CLIMB_CYCLE.finditer(raw))
    if len(costs) == len(results) == 1 and len(gains) <= 1 and len(cycles) <= 1:
        progress, total, level, max_level = map(int, results[0].groups())
        if not 0 <= progress <= total or total <= 0 or not 0 <= level <= max_level or max_level <= 0:
            return None
        if not gains and "未能更进一步" not in raw:
            return None
        return "climb", (costs[0], gains[0] if gains else None, cycles[0] if cycles else None, results[0])
    if not costs and not results and has_wait_time(raw) and any(word in raw for word in ("后再", "冷却", "请等待")):
        wait = parse_wait_time(raw)
        if wait > 0:
            return "wait", wait
    return None


async def handle_tianti_reply(text, now, reply_to, matched_family=None, *, reply_context=None):
    owner = _capture_tianti_owner()
    if not _owns_tianti(owner):
        return False
    kind = next((kind for kind in TIANTI_COMMANDS if matched_family == f"tianti_{kind}"), None)
    if kind is None and not matched_family:
        command = str(getattr(reply_to, "raw_text", "") or "").strip()
        kind = next((kind for kind, spec in TIANTI_COMMANDS.items() if spec[0] == command), None)
    if kind is None:
        return False
    record = _tianti_reply_operation(kind, reply_to, now, reply_context)
    result = _parse_tianti_native_result(kind, text) if record else None
    if result is None:
        return False

    at = record["reply_at"]
    result_kind, payload = result
    _ensure_tianti_wenxin_daily_state(at)
    state[TIANTI_COMMANDS[kind][1]] = record["msg_id"]
    state["tianti_last_error"] = ""
    if result_kind == "shortage":
        resource_key = TIANTI_CLIMB_RESOURCE_KEY if kind == "climb" else TIANTI_GANGFENG_RESOURCE_KEY
        backoff = record_resource_shortage(resource_key, at, reason=payload)
        due_at = float(backoff["next_at"])
        state[f"next_tianti_{kind}_time"] = due_at
        state["tianti_cooldown_text" if kind == "climb" else "tianti_gangfeng_status"] = _tianti_due_text(due_at)
        state["tianti_last_error"] = f"{record['command']} 资源不足: {payload[:80]}"
        message = f"{record['command']} 资源不足，第 {backoff['count']} 档退避至 {fmt_abs_ts(due_at)}"
    elif result_kind == "status":
        _mark_tianti_status_synced(at, SimpleNamespace(id=record["msg_id"]))
        _apply_tianti_panel_payload(payload, now=at)
        message = f"天阶状态已同步：{state['tianti_progress_current']}/{state['tianti_progress_total']}，{state['tianti_cooldown_text']}"
    elif result_kind == "wenxin":
        _apply_tianti_wenxin_result(payload, at, SimpleNamespace(id=record["msg_id"]))
        message = f"问心完成：{state['tianti_wenxin_status']}"
    elif result_kind == "gangfeng":
        state["tianti_gangfeng_level"], state["tianti_gangfeng_total"] = payload
        reset_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY)
        _schedule_tianti_gangfeng_retry(at)
        message = f"九天罡风成功：{payload[0]}/{payload[1]}，下次 {state['tianti_gangfeng_status']}"
    elif result_kind == "climb":
        cost, gain, cycle, progress = payload
        state["tianti_last_cost_xiuwei"] = int(cost.group(1))
        state["tianti_last_gain_xiuwei"] = int(gain.group(1)) if gain else 0
        state["tianti_last_gain_contrib"] = int(gain.group(2)) if gain else 0
        if cycle:
            state["tianti_cycle_count"] = int(cycle.group(1))
        _apply_tianti_climb_result(progress)
        reset_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY)
        _schedule_tianti_climb_retry(at, rank_choice=record["rank_choice"])
        message = f"登阶{'成功' if gain else '未进'}：{state['tianti_progress_current']}/{state['tianti_progress_total']}，下次 {state['tianti_cooldown_text']}"
    else:
        wait = payload + random.randint(TIANTI_CD_RANDOM_MIN_SEC, TIANTI_CD_RANDOM_MAX_SEC)
        state[f"next_tianti_{kind}_time"] = at + wait
        state["tianti_cooldown_text" if kind == "climb" else "tianti_gangfeng_status"] = _tianti_due_text(at + wait)
        reset_resource_shortage(TIANTI_CLIMB_RESOURCE_KEY if kind == "climb" else TIANTI_GANGFENG_RESOURCE_KEY)
        if result_kind == "gangfeng_wait":
            state["next_tianti_gangfeng_time"] = at + wait
            state["tianti_gangfeng_status"] = _tianti_due_text(at + wait)
            reset_resource_shortage(TIANTI_GANGFENG_RESOURCE_KEY)
        message = f"{record['command']} CD：{_tianti_due_text(at + wait)}"
    _calc_tianti_wenxin_plan(at)
    completed = dict(record, status="complete")
    completed.pop("replay_after", None)
    _store_tianti_command(kind, completed)
    _clear_tianti_command_pending(completed)
    old_read = (_tianti_commands() or {}).get("status")
    if (
        kind != "status" and old_read and old_read["status"] in TIANTI_UNRESOLVED
        and old_read["account_id"] == record["account_id"] and old_read["started_at"] < record["started_at"]
    ):
        _store_tianti_command("status", dict(old_read, status="expired"))
        if old_read["msg_id"]:
            _clear_tianti_command_pending(old_read)
    # Business outcome, completion evidence and cleanup commit before any await.
    if save_state() is not False:
        await _notify_tianti(message, owner[0])
    return True


async def _recover_due_tianti_replies(now):
    owner = _capture_tianti_owner()
    records = _tianti_commands()
    if not owner or records is None:
        return False
    for identity_id in list(_TIANTI_LEGACY_REPLAY_AFTER):
        if not has_identity(identity_id):
            _TIANTI_LEGACY_REPLAY_AFTER.pop(identity_id, None)
    previous_account, retry_after = _TIANTI_LEGACY_REPLAY_AFTER.get(owner[0], (0, 0))
    allow_legacy_log = previous_account != owner[2] or now >= retry_after
    imported = _legacy_tianti_pending(now, records, allow_log=allow_legacy_log)
    if allow_legacy_log:
        _TIANTI_LEGACY_REPLAY_AFTER[owner[0]] = (owner[2], now + TIANTI_REPLAY_INTERVAL_SEC)
    if imported:
        for kind, record in imported.items():
            _store_tianti_command(kind, record)
        save_state()
        records.update(imported)
    for kind, record in records.items():
        if (
            record["status"] not in TIANTI_UNRESOLVED or record["account_id"] != owner[2]
            or _number(record.get("replay_after")) > now
        ):
            continue
        recovered = _adopt_tianti_receipt(record, now)
        recovered = dict(recovered, replay_after=now + TIANTI_REPLAY_INTERVAL_SEC)
        _store_tianti_command(kind, recovered)
        save_state()
        root = recovered["msg_id"]
        if root <= 0:
            continue

        def trusted_reply(entry):
            return bool(
                isinstance(entry, dict) and entry.get("event_type") in {"message", "edit"}
                and entry.get("sender_is_bot") is True and _message_id(entry.get("sender_id")) in get_game_bot_ids()
                and _message_id(entry.get("chat_id")) == recovered["chat_id"]
                and _message_id(entry.get("reply_to_msg_id")) == root
                and _message_id(entry.get("message_id")) > 0
                and recovered.get("dispatch_at", recovered["started_at"]) - 1
                <= telegram_event_timestamp(SimpleNamespace(server_event_at=entry.get("server_event_at"))) <= now + 1
            )

        # Read the original reply window and a recent window, not all intervening history.
        windows = {min(now, recovered["started_at"] + TIANTI_LOG_REPLAY_LOOKBACK_SEC), now}
        replies = []
        for end_at in sorted(windows):
            replies.extend(find_message_log_replies(
                root, end_at, lookback_sec=TIANTI_LOG_REPLAY_LOOKBACK_SEC + 1, lookahead_sec=1,
                chat_id=recovered["chat_id"], predicate=trusted_reply,
            ))
        replies.sort(key=lambda item: (_number(item.get("server_event_at")), _message_id(item.get("message_id"))))
        for entry in replies:
            if not _owns_tianti(owner):
                return True
            if not trusted_reply(entry):
                continue
            if await handle_tianti_reply(
                entry.get("text", ""), now,
                SimpleNamespace(id=root, chat_id=recovered["chat_id"], raw_text=recovered["command"]),
                matched_family=f"tianti_{kind}",
                reply_context={
                    "send_as_id": owner[0], "account_id": owner[2], "root_msg_id": root,
                    "chat_id": recovered["chat_id"], "server_event_at": entry["server_event_at"],
                    "sender_id": entry["sender_id"], "msg_id": entry["message_id"],
                },
            ):
                return True
            if not _owns_tianti(owner):
                return True
    return False


def _expire_tianti_status_read(now):
    records = _tianti_commands()
    record = records.get("status") if records is not None else None
    if (
        record and record["status"] in TIANTI_UNRESOLVED
        and record["account_id"] == get_identity_account(get_current_identity_id())
        and now >= record["started_at"] + TIANTI_REPLAY_INTERVAL_SEC
    ):
        # Only a read may expire. Mutation records never become retryable by age.
        _store_tianti_command("status", dict(record, status="expired"))
        if record["msg_id"]:
            _clear_tianti_command_pending(record)
        save_state()


async def run_tianti_scheduler(now):
    owner = _capture_tianti_owner()
    if not _owns_tianti(owner, sending=True) or owner[0] in _TIANTI_RUN_LOCKS:
        return
    token = object()
    _TIANTI_RUN_LOCKS[owner[0]] = token
    try:
        before = _tianti_plan_snapshot(owner)
        if await _recover_due_tianti_replies(now):
            return
        if not _owns_tianti(owner, sending=True) or _tianti_plan_snapshot(owner) != before:
            return
        _expire_tianti_status_read(now)
        blocked = _tianti_native_block_reason()
        if blocked:
            if state.get("tianti_last_error") != blocked:
                state["tianti_last_error"] = blocked
                mark_dirty()
            return
        if _ensure_tianti_wenxin_daily_state(now):
            mark_dirty()
        _calc_tianti_wenxin_plan(now)
        should_wenxin, wenxin_reason = _should_trigger_tianti_wenxin(now)
        if should_wenxin:
            await _send_tianti_command("wenxin", owner, now, trigger_key=wenxin_reason)
            return
        if _set_tianti_skip_reason(str(wenxin_reason or "")):
            mark_dirty()
        should_gangfeng, gangfeng_reason = _should_trigger_tianti_gangfeng(now)
        if should_gangfeng:
            await _send_tianti_command("gangfeng", owner, now, trigger_key=gangfeng_reason)
            return
        if _tianti_status_sync_due(now):
            if not is_tianti_public_status_selected():
                await _send_tianti_command("status", owner, now)
            return
        next_climb = float(state.get("next_tianti_climb_time", 0) or 0)
        if next_climb > 0 and now >= next_climb:
            await _send_tianti_command("climb", owner, now)
    finally:
        if _TIANTI_RUN_LOCKS.get(owner[0]) is token:
            _TIANTI_RUN_LOCKS.pop(owner[0], None)


__all__ = [
    "get_tianti_estimated_wenxin_window_text",
    "get_tianti_status_text",
    "handle_tianti_reply",
    "is_tianti_public_status_selected",
    "is_tianti_status_sync_due",
    "run_tianti_scheduler",
    "sync_tianti_miniapp_status",
    "sync_tianti_status",
]
