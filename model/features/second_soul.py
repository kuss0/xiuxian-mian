"""第二元神自动修炼模块。

设计参考：
- tree.py 的扁平事件驱动 + scheduler 模式（不用 phaseful，因为没有"出窍-归窍-总结"流程）
- yuanying.py 的 broadcast 身份匹配（match_yuanying_summary_identity）

phase 状态机:
    idle              - 默认/窍中温养，next_second_soul_time 到点会查询状态
    status_pending    - 已发 .第二元神，等回复
    ready_to_train    - 已确认窍中温养，等待 scheduler 安全发送 .元神修炼
    train_pending     - 已发 .元神修炼，等回复
    cultivating       - 修炼中，next_second_soul_time = 修炼结束时间 + buffer
    heart_demon_pending - 心魔试炼，已自动稳固道心，等结算 edit/broadcast
    injured           - 受伤，next_second_soul_time = 恢复时间 + buffer
    purge_ready       - 已确认需镇魔，等待安全发送
    purge_pending     - 镇魔结果待确认，不盲目补发
    purge_status_pending - 镇魔后查询魔染
    not_unlocked      - 尚未凝练第二元神，长冻结 7 天后重试

设计原则:
1. 未知状态先发 .第二元神 查状态；明确归位/窍中温养后，才进入修炼发送队列
2. heart_demon_pending 默认自动 .抉择 稳固道心；结算成功后进入安全修炼队列
3. heart_demon_pending 有 deadline 兜底（防 broadcast 漏接死锁）
4. 不使用 fire-and-forget 延迟调度（避开 tree 模块的 guard 漏洞）
"""

import asyncio
import copy
import math
import random
import re
import time
from types import SimpleNamespace
from uuid import uuid4

from ..config import (
    CD_BUFFER_SEC,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_SECOND_SOUL_DEMON_STATUS,
    CMD_SECOND_SOUL_PURGE,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
    RE_WHITESPACE,
    SECOND_SOUL_HEART_DEMON_DEADLINE_SEC,
    SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC,
    SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC,
    SECOND_SOUL_PENDING_TIMEOUT_MAX,
    SECOND_SOUL_PENDING_TIMEOUT_MIN,
    SECOND_SOUL_RECHECK_MAX,
    SECOND_SOUL_RECHECK_MIN,
    SECOND_SOUL_TRAIN_CD_SEC,
)
from ..identity_levels import parse_second_soul_level_text, update_identity_level_record
from ..message_keys import message_key_parts
from ..message_log_recovery import find_message_log_replies, find_message_log_replies_tail, recover_sent_command_from_message_log, sender_matches_identity
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_by_reply, classify_game_send_block, console_log, mono, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_game_bot_ids, get_game_group_id, get_game_group_ids, get_identity_account, get_identity_display_name, get_identity_enabled, get_identity_ids, get_identity_state, get_global_enabled, get_send_as_tags, has_identity, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


# 真实文本特征（来自历史日志样本扫描）：
RE_SECOND_SOUL_PANEL_HEAD = re.compile(r"【你的第二元神[：:]")
RE_SECOND_SOUL_STATUS_LINE = re.compile(r"状态[：:]\s*([^\n)]+?)(?:\s*[(（]剩余[：:]\s*([^)）\n]+)[)）])?\s*(?:\n|$)")
RE_SECOND_SOUL_MORAN = re.compile(r"魔染(?:度)?\s*[:：]?\s*(\d+)(?:\s*(?:→|->|=>)\s*(\d+))?")
RE_AT_USERNAME = re.compile(r"@([A-Za-z0-9_]+)")
SECOND_SOUL_PURGE_THRESHOLD_DEFAULT = 60
SECOND_SOUL_PURGE_REPLY_TIMEOUT_SEC = 120
SECOND_SOUL_PURGE_MAX_ATTEMPTS = 2
SECOND_SOUL_CHOICE_STRATEGIES = {
    "stable": ("稳固道心", CMD_SECOND_SOUL_CHOICE_STABLE),
    "break": ("强行突破", CMD_SECOND_SOUL_CHOICE_BREAK),
}
SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC = 60 * 60
SECOND_SOUL_COMMANDS = {
    "status": (CMD_SECOND_SOUL_STATUS, "status_pending", "second_soul_status_msg_id"),
    "train": (CMD_SECOND_SOUL_TRAIN, "train_pending", "second_soul_train_msg_id"),
    "purge": (CMD_SECOND_SOUL_PURGE, "purge_pending", "second_soul_purge_msg_id"),
    "demon_status": (CMD_SECOND_SOUL_DEMON_STATUS, "purge_status_pending", "second_soul_purge_status_msg_id"),
    "status_read": (CMD_SECOND_SOUL_STATUS, "", ""),
}
_SECOND_SOUL_INFLIGHT = set()
SECOND_SOUL_TRANSIENT_UNSENT_CODES = {
    "send_queue_timeout", "send_prepare_timeout", "global_disabled",
    "global_recovery_cooldown", "dungeon_quiet", "account_offline",
    "account_unbound", "account_client_missing", "account_client_not_ready",
    "account_session_error", "send_as_peer_invalid", "bot_health",
    "identity_weak", "pre_send_guard", "action_guard", "supervisor_quiesce",
}


def _phase():
    return state.get("second_soul_phase", "idle")


def _set_phase(new_phase):
    if state.get("second_soul_phase") != new_phase:
        state["second_soul_phase"] = new_phase


def _clear_heart_demon():
    state["second_soul_heart_demon_msg_id"] = 0
    state["second_soul_heart_demon_chat_id"] = 0
    state["second_soul_heart_demon_account_id"] = 0
    state["second_soul_heart_demon_choice_msg_id"] = 0
    state["second_soul_heart_demon_deadline"] = 0.0
    state["second_soul_heart_demon_notified"] = False


def _choice_strategy():
    strategy = str(state.get("second_soul_choice_strategy") or "stable").strip().lower()
    return strategy if strategy in SECOND_SOUL_CHOICE_STRATEGIES else "stable"


def _choice_command():
    return SECOND_SOUL_CHOICE_STRATEGIES[_choice_strategy()][1]


def _choice_label():
    return SECOND_SOUL_CHOICE_STRATEGIES[_choice_strategy()][0]


def get_second_soul_purge_threshold():
    try:
        value = int(state.get("second_soul_purge_threshold", SECOND_SOUL_PURGE_THRESHOLD_DEFAULT))
    except (TypeError, ValueError):
        value = SECOND_SOUL_PURGE_THRESHOLD_DEFAULT
    return max(1, min(100, value))


def _next_pending_timeout(now):
    return now + random.uniform(SECOND_SOUL_PENDING_TIMEOUT_MIN, SECOND_SOUL_PENDING_TIMEOUT_MAX)


def _number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _message_id(value):
    number = _number(value)
    return int(number) if number == int(number) else 0


def _capture_owner(send_as_id=None):
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    if not has_identity(identity_id):
        return None
    return identity_id, get_identity_state(identity_id), get_identity_account(identity_id)


def _owns_identity(owner, *, enabled=False):
    if not owner:
        return False
    identity_id, identity, account_id = owner
    return bool(
        has_identity(identity_id) and get_identity_state(identity_id) is identity
        and get_identity_account(identity_id) == account_id
        and (not enabled or (get_global_enabled() and get_identity_enabled(identity_id) and identity.get("second_soul_enabled")))
    )


def _business_snapshot(identity):
    return copy.deepcopy({key: value for key, value in identity.items() if (
        key == "next_second_soul_time" or (key.startswith("second_soul_") and key != "second_soul_commands")
    )})


def _command_records():
    records = state.get("second_soul_commands", {})
    if not isinstance(records, dict) or records.keys() - SECOND_SOUL_COMMANDS.keys():
        return None
    for kind, item in records.items():
        if not isinstance(item, dict) or len(item) > 16 or (
            item.get("command") != SECOND_SOUL_COMMANDS[kind][0]
            or type(item.get("identity_id")) is not int or item["identity_id"] != get_current_identity_id()
            or type(item.get("account_id")) is not int or item["account_id"] <= 0
            or not isinstance(item.get("op_id"), str) or not 0 < len(item["op_id"]) <= 128
            or not isinstance(item.get("status"), str)
            or item.get("status") not in {"sending", "sent", "unknown", "unsent", "complete", "expired"}
            or type(item.get("started_at")) not in {int, float} or _number(item["started_at"]) <= 0
            or type(item.get("chat_id")) is not int or not item["chat_id"]
            or type(item.get("msg_id")) is not int or item["msg_id"] < 0
            or (item["status"] in {"sent", "complete"} and item["msg_id"] <= 0)
            or (item["msg_id"] > 0 and _number(item.get("sent_at")) < item["started_at"] - 1)
            or ("dispatch_at" in item and not item["started_at"] - 1 <= _number(item["dispatch_at"]) <= _number(item.get("sent_at")))
            or (item["status"] == "complete" and (item["msg_id"] <= 0 or _number(item.get("reply_at")) <= 0))
        ):
            return None
    return copy.deepcopy(records)


def _store_command(kind, record):
    records = _command_records()
    if records is None:
        return False
    records[kind] = copy.deepcopy(record)
    state["second_soul_commands"] = records
    mark_dirty()
    return True


def _command_ownership_error():
    records = _command_records()
    account_id = get_identity_account(get_current_identity_id())
    if records is None or any(
        record["account_id"] != account_id and record["status"] in {"sending", "sent", "unknown"}
        for kind, record in (records or {}).items() if kind != "status_read"
    ):
        return "第二元神命令归属记录异常或账号已变更，保留原记录等待核对"
    for kind in ("train", "purge", "demon_status"):
        _command, phase, state_key = SECOND_SOUL_COMMANDS[kind]
        if kind not in records and (_phase() == phase or state.get(state_key)):
            return "第二元神旧在途操作缺少账号和原群归属，保留原状态等待核对"
    if state.get("second_soul_heart_demon_msg_id") and (
        not account_id or state.get("second_soul_heart_demon_account_id") != account_id
    ):
        return "第二元神旧心魔警示缺少有效账号归属，保留原状态等待核对"
    return ""


def _adopt_command_receipt(record, now):
    if record.get("msg_id") or record["account_id"] != get_identity_account(get_current_identity_id()):
        return record
    matches = {}
    for key, pending in state.get("pending_tasks", {}).items():
        if not isinstance(pending, dict) or pending.get("op_id") != record["op_id"] or pending.get("cmd") != record["command"]:
            continue
        try:
            chat_id, msg_id = message_key_parts(key, pending)
        except (TypeError, ValueError, OverflowError):
            continue
        at = _number(pending.get("sent_at"))
        dispatch_at = _number(pending.get("send_started_at")) or record["started_at"]
        if (
            chat_id == record["chat_id"] and msg_id > 0 and record["started_at"] - 1 <= at <= now + 1
            and pending.get("source_module") == "第二元神"
            and _message_id(pending.get("account_id", record["account_id"])) == record["account_id"]
        ):
            if record["started_at"] - 1 <= dispatch_at <= at:
                matches[chat_id, msg_id] = at, dispatch_at
    if not matches:
        logged = recover_sent_command_from_message_log(
            record["command"], record["identity_id"], now,
            start_ts=max(record["started_at"] - 1, now - SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC),
            game_group_id=record["chat_id"], lookback_sec=SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC, lookahead_sec=1,
        ) or {}
        if (
            logged.get("event_type") == "sent" and logged.get("op_id") == record["op_id"]
            and logged.get("source_module") == "第二元神"
            and _message_id(logged.get("chat_id")) == record["chat_id"]
            and _message_id(logged.get("account_id")) == record["account_id"]
            and sender_matches_identity(logged.get("sender_id"), record["identity_id"])
            and _message_id(logged.get("message_id")) > 0
            and record["started_at"] - 1 <= _number(logged.get("ts_epoch")) <= now + 1
        ):
            matches[record["chat_id"], int(logged["message_id"])] = _number(logged["ts_epoch"]), record["started_at"]
    if len(matches) == 1:
        (chat_id, msg_id), (sent_at, dispatch_at) = next(iter(matches.items()))
        return dict(record, msg_id=msg_id, chat_id=chat_id, sent_at=sent_at, dispatch_at=dispatch_at)
    return record


def _reply_operation(kind, reply_to, now, reply_context=None):
    if reply_context is not None and not isinstance(reply_context, dict):
        return None
    context = reply_context or {}
    records = _command_records()
    if records is None:
        return None
    identity_id = get_current_identity_id()
    root = _message_id(context.get("root_msg_id") or getattr(reply_to, "id", 0))
    chat = _message_id(context.get("chat_id") or getattr(reply_to, "chat_id", 0))
    raw_command = str(getattr(reply_to, "raw_text", "") or "").strip()
    if (
        root <= 0 or not chat or (raw_command.startswith(".") and raw_command != SECOND_SOUL_COMMANDS[kind][0])
        or (context.get("send_as_id") is not None and _message_id(context["send_as_id"]) != identity_id)
        or (getattr(reply_to, "chat_id", 0) and _message_id(reply_to.chat_id) != chat)
    ):
        return None
    at = _number(context.get("server_event_at")) if reply_context is not None else _number(now)
    if at <= 0 or at > max(_number(now), time.time()) + 1:
        return None
    record = records.get(kind)
    if record is None or ((record["chat_id"], record["msg_id"]) != (chat, root) and context.get("source") == "manual_game_command"):
        # A native manual command can calibrate idle work, but must not replace
        # an unresolved automatic command whose outcome is still unknown.
        if context.get("source") != "manual_game_command" or kind == "status_read" or any(
            item["status"] in {"sending", "sent", "unknown"}
            for item_kind, item in records.items() if item_kind in {kind, "train", "purge"}
        ):
            return None
        sent_at = getattr(getattr(reply_to, "date", None), "timestamp", lambda: 0)()
        if (
            not 0 < _number(sent_at) <= at + 1 or chat not in get_game_group_ids()
            or get_identity_account(identity_id) <= 0 or raw_command != SECOND_SOUL_COMMANDS[kind][0]
            or not sender_matches_identity(getattr(reply_to, "sender_id", 0), identity_id)
            or (record is not None and sent_at <= record["started_at"])
        ):
            return None
        record = {
            "op_id": f"manual:{chat}:{root}", "identity_id": identity_id,
            "account_id": get_identity_account(identity_id), "command": SECOND_SOUL_COMMANDS[kind][0],
            "started_at": sent_at, "sent_at": sent_at, "msg_id": root, "chat_id": chat, "status": "sent",
            "manual": True,
        }
    record = _adopt_command_receipt(record, max(_number(now), time.time()))
    if (
        record["account_id"] != get_identity_account(identity_id)
        or (context.get("account_id") is not None and _message_id(context["account_id"]) != record["account_id"])
        or record["msg_id"] != root or record["chat_id"] != chat
        or record["status"] in {"unsent", "expired"} or at < _number(record.get("dispatch_at") or record["started_at"]) - 1
        or at < max((_number(item.get("reply_at")) for item in records.values()), default=0)
        or at < _number(state.get("second_soul_last_broadcast_at"))
        or at < _number(state.get("second_soul_last_train_started_at"))
    ):
        return None
    return dict(record, reply_at=at)


def _complete_command(kind, record):
    record = dict(record, status="complete")
    _store_command(kind, record)
    _clear_command_pending(kind, record)


def _clear_command_pending(kind, record):
    clear_pending_by_reply(
        send_as_id=record["identity_id"], reply_context={
            "send_as_id": record["identity_id"], "family": "second_soul_status" if kind == "status_read" else f"second_soul_{kind}",
            "root_msg_id": record["msg_id"], "reply_to_msg_id": record["msg_id"],
            "chat_id": record["chat_id"],
        }, clear_family=False,
    )


async def _send_owned_command(kind, send_as_id, now):
    now = max(float(now), time.time())
    owner = _capture_owner(send_as_id)
    if not _owns_identity(owner, enabled=True):
        return False
    identity_id, identity, account_id = owner
    command, phase, state_key = SECOND_SOUL_COMMANDS[kind]
    with use_identity(identity_id):
        records = _command_records()
        if records is None or _command_ownership_error() or account_id <= 0 or not get_game_group_id():
            return False
        if any(item["op_id"] in _SECOND_SOUL_INFLIGHT for item in records.values()) or records.get(kind, {}).get("status") in {"sending", "sent", "unknown"}:
            return False
        record = {
            "op_id": uuid4().hex, "identity_id": identity_id, "account_id": account_id,
            "command": command, "started_at": float(now), "msg_id": 0,
            "chat_id": get_game_group_id(), "status": "sending",
        }
        _store_command(kind, record)
        _set_phase(phase)
        state[state_key] = 0
        state["second_soul_last_error"] = ""
        if kind in {"status", "train"}:
            _clear_pending_msg_ids()
            state["next_second_soul_time"] = _next_pending_timeout(now)
        else:
            state["second_soul_purge_due_at"] = _purge_due_at(now)
            if kind == "purge":
                state["second_soul_purge_attempts"] = int(state.get("second_soul_purge_attempts", 0) or 0) + 1
                state["second_soul_purge_status_msg_id"] = 0
        expected = _business_snapshot(identity)

        def current_operation():
            if not _owns_identity(owner):
                return None
            with use_identity(identity_id):
                current = (_command_records() or {}).get(kind)
            if current and all(current.get(key) == record[key] for key in (
                "op_id", "identity_id", "account_id", "command", "started_at", "chat_id",
            )):
                return current
            return None

        def can_send():
            return bool(
                current_operation() == record and _owns_identity(owner, enabled=True)
                and _business_snapshot(identity) == expected
            )

        def unsent(reason, sent_at):
            _store_command(kind, dict(record, status="unsent"))
            if _business_snapshot(identity) == expected:
                _set_phase({"train": "ready_to_train", "purge": "purge_ready", "demon_status": "purge_pending"}.get(kind, "idle"))
                if kind in {"status", "train"}:
                    state["next_second_soul_time"] = sent_at + (60 if kind == "status" else 600)
                else:
                    state["second_soul_purge_due_at"] = sent_at + 600
                if kind == "purge":
                    state["second_soul_purge_attempts"] = max(0, int(state["second_soul_purge_attempts"]) - 1)
                state["second_soul_last_error"] = reason
            save_state()

        if save_state() is False:
            unsent("第二元神在途状态未保存，本次未发送", now)
            return False
        try:
            _SECOND_SOUL_INFLIGHT.add(record["op_id"])
            msg = await send_game_command(
                command, track=False, send_as_id=identity_id, priority="chain", max_retry=0,
                source_module="第二元神", op_id=record["op_id"], target_chat_id=record["chat_id"],
                operation_check=can_send,
            )
        except (asyncio.CancelledError, Exception):
            if current_operation() == record:
                _store_command(kind, dict(record, status="unknown"))
                save_state()
            raise
        finally:
            _SECOND_SOUL_INFLIGHT.discard(record["op_id"])
        current = current_operation()
        if current is None:
            return bool(msg)
        if current["status"] != "sending":
            if current["status"] == "complete":
                _clear_command_pending(kind, current)
                save_state()
            return bool(msg)
        sent_at = _number(getattr(msg, "sent_at", 0))
        if not msg:
            block = classify_game_send_block(identity_id, command)
            if block.get("status") == "unsent":
                code = str(block.get("code") or "")
                unsent("" if code in SECOND_SOUL_TRANSIENT_UNSENT_CODES else f"第二元神未发送：{code}", max(now, time.time()))
                await send_audit_log(
                    f"🌀 第二元神 {command} 未发送（{code}），稍后重新排队。",
                    scope="identity", send_as_id=identity_id, limit=220,
                )
                return False
        msg_id = _message_id(getattr(msg, "id", 0))
        chat_id = _message_id(getattr(msg, "chat_id", 0))
        dispatch_at = _number(getattr(msg, "send_started_at", 0)) or record["started_at"]
        known = bool(msg_id > 0 and chat_id == record["chat_id"] and now - 1 <= dispatch_at <= sent_at <= max(now, time.time()) + 1)
        current = dict(record, status="sent" if known else "unknown")
        if known:
            current.update(msg_id=msg_id, sent_at=sent_at, dispatch_at=dispatch_at)
        _store_command(kind, current)
        if _business_snapshot(identity) == expected:
            state[state_key] = msg_id if known else 0
            due_from = sent_at if known else max(now, time.time())
            if kind in {"status", "train"}:
                state["next_second_soul_time"] = _next_pending_timeout(due_from)
            else:
                state["second_soul_purge_due_at"] = _purge_due_at(due_from)
                if kind == "purge":
                    state["second_soul_purge_last_at"] = sent_at if known else 0
            state["second_soul_last_error"] = "" if known else f"{command} 发送状态未知，保留原操作等待反馈"
        save_state()
        if known and _owns_identity(owner):
            await _recover_second_soul_pending_from_message_log(max(now, time.time()), phase, tail=True)
        elif _owns_identity(owner):
            await send_audit_log(
                f"🌀 第二元神 {command} 发送状态未知，保留原操作等待反馈。",
                scope="identity", send_as_id=identity_id, limit=220,
            )
        return True


def restore_second_soul_runtime(now):
    ownership_error = _command_ownership_error()
    if ownership_error:
        state["second_soul_last_error"] = ownership_error
        mark_dirty()
        return True
    records = _command_records()
    if not records:
        return False
    active = []
    for kind, record in records.items():
        if kind == "status_read":
            continue
        if record["account_id"] != get_identity_account(get_current_identity_id()):
            continue
        if record["status"] in {"sending", "sent", "unknown"}:
            if record["status"] == "sending" and record["op_id"] not in _SECOND_SOUL_INFLIGHT:
                record["status"] = "unknown"
                _store_command(kind, record)
            active.append((kind, record))
    if not active:
        return False
    kind, record = max(active, key=lambda item: item[1]["started_at"])
    _command, phase, state_key = SECOND_SOUL_COMMANDS[kind]
    _set_phase(phase)
    state[state_key] = record["msg_id"]
    due_key = "next_second_soul_time" if kind in {"status", "train"} else "second_soul_purge_due_at"
    if _number(state.get(due_key)) <= 0:
        timeout = SECOND_SOUL_PENDING_TIMEOUT_MAX if kind in {"status", "train"} else SECOND_SOUL_PURGE_REPLY_TIMEOUT_SEC
        state[due_key] = _number(record.get("sent_at") or record["started_at"]) + timeout
    return True


async def remember_second_soul_status_read(msg, *, send_as_id, identity_state, account_id, requested_at):
    owner = send_as_id, identity_state, account_id
    if not _owns_identity(owner) or account_id <= 0:
        return False
    msg_id = _message_id(getattr(msg, "id", 0))
    chat_id = _message_id(getattr(msg, "chat_id", 0))
    sent_at = _number(getattr(msg, "sent_at", 0))
    if not msg_id or not chat_id or not 0 < requested_at <= sent_at + 1:
        return False
    with use_identity(send_as_id):
        record = {
            "op_id": uuid4().hex, "identity_id": send_as_id, "account_id": account_id,
            "command": CMD_SECOND_SOUL_STATUS, "started_at": requested_at,
            "sent_at": sent_at, "msg_id": msg_id, "chat_id": chat_id, "status": "sent",
        }
        if not _store_command("status_read", record) or save_state() is False:
            return False
        await _recover_second_soul_pending_from_message_log(
            max(sent_at, time.time()), "status_pending", tail=True, command_kind="status_read",
        )
        return True


def _calibrate_pending_from_panel(kind, panel):
    record = (_command_records() or {}).get(kind)
    if record and record["status"] in {"sending", "sent", "unknown"} and (
        record["op_id"] not in _SECOND_SOUL_INFLIGHT
        and panel["started_at"] > _number(record.get("sent_at") or record["started_at"])
        and panel["chat_id"] == record["chat_id"] and panel["account_id"] == record["account_id"]
    ):
        _store_command(kind, dict(record, status="expired"))


def _clear_pending_msg_ids():
    state["second_soul_status_msg_id"] = 0
    state["second_soul_train_msg_id"] = 0


def _reset_purge_state(*, keep_moran=False):
    if not keep_moran:
        state["second_soul_moran_value"] = 0
    state["second_soul_purge_msg_id"] = 0
    state["second_soul_purge_status_msg_id"] = 0
    state["second_soul_purge_attempts"] = 0
    state["second_soul_purge_due_at"] = 0
    state["second_soul_purge_last_at"] = 0


def _mark_ready_to_train(now):
    """已确认可修炼。这里只改状态，不直接发命令，实际发送交给 scheduler + 全局锁。"""
    _set_phase("ready_to_train")
    state["next_second_soul_time"] = now
    state["second_soul_last_error"] = ""
    _clear_heart_demon()
    _clear_pending_msg_ids()


def _parse_moran_value(text):
    value = None
    for match in RE_SECOND_SOUL_MORAN.finditer(text or ""):
        raw_value = match.group(2) or match.group(1)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
    return value


def _remember_moran_from_text(text):
    moran = _parse_moran_value(text)
    if moran is not None:
        state["second_soul_moran_value"] = int(moran)
    return moran


def _purge_due_at(now):
    return float(now) + SECOND_SOUL_PURGE_REPLY_TIMEOUT_SEC


def _finish_purge_ready(now, *, moran=None, last_error=""):
    if moran is not None:
        state["second_soul_moran_value"] = int(moran)
    _reset_purge_state(keep_moran=True)
    _mark_ready_to_train(now)
    if last_error:
        state["second_soul_last_error"] = last_error


async def _send_second_soul_purge(send_as_id, now, *, reason=""):
    owner = _capture_owner(send_as_id)
    if not _owns_identity(owner, enabled=True):
        return False
    with use_identity(owner[0]):
        attempts = int(state.get("second_soul_purge_attempts", 0) or 0)
        if attempts >= SECOND_SOUL_PURGE_MAX_ATTEMPTS:
            _finish_purge_ready(now, last_error="元神镇魔已达自动上限，等待人工确认")
            save_state()
            await send_audit_log(
                "⚠️ 第二元神魔染仍高，但元神镇魔自动次数已达上限，已停止补发。",
                scope="identity", send_as_id=send_as_id, limit=240,
            )
            return False
        sent = await _send_owned_command("purge", owner[0], now)
        if sent and _owns_identity(owner) and _phase() == "purge_pending":
            record = (_command_records() or {}).get("purge", {})
            status = "已发送" if record.get("status") == "sent" else "发送结果待确认"
            await send_audit_log(
                f"🌀 第二元神元神镇魔{status}，第 {attempts + 1}/{SECOND_SOUL_PURGE_MAX_ATTEMPTS} 次。{reason}",
                scope="identity", send_as_id=send_as_id, limit=240,
            )
        return sent


async def _send_second_soul_demon_status(send_as_id, now):
    return await _send_owned_command("demon_status", send_as_id, now)


def _broadcast_key(kind, text):
    compact = RE_WHITESPACE.sub("", text or "")
    return f"{kind}:{compact[:400]}"


def _is_recent_duplicate_broadcast(kind, text, now, window_sec=6 * 3600):
    key = _broadcast_key(kind, text)
    last_key = state.get("second_soul_last_broadcast_key", "")
    last_at = float(state.get("second_soul_last_broadcast_at", 0) or 0)
    return key == last_key and last_at > 0 and now - last_at < window_sec


def _remember_broadcast(kind, text, now):
    state["second_soul_last_broadcast_key"] = _broadcast_key(kind, text)
    state["second_soul_last_broadcast_at"] = now


def _recently_confirmed_training(now):
    started_at = float(state.get("second_soul_last_train_started_at", 0) or 0)
    if started_at <= 0:
        return False
    if now < started_at:
        return False
    if now - started_at > SECOND_SOUL_RECHECK_MAX:
        return False
    return state.get("next_second_soul_time", 0) > now + SECOND_SOUL_RECHECK_MAX


def _broadcast_time(event, now):
    if event is None:
        return _number(now)
    stamp = getattr(event, "edit_date", None) or getattr(event, "date", None)
    at = _number(getattr(stamp, "timestamp", lambda: 0)())
    return at if 0 < at <= max(_number(now), time.time()) + 1 else 0.0


def _broadcast_is_stale(now):
    records = _command_records()
    if records is None:
        return True
    latest = max((_number(item.get("reply_at")) for item in records.values()), default=0)
    return bool(
        now <= 0 or now < latest or now < _number(state.get("second_soul_last_train_started_at"))
        or now < _number(state.get("second_soul_last_broadcast_at"))
    )


def get_second_soul_status_text():
    lines = ["🌀 第二元神"]
    if not state.get("second_soul_enabled", False):
        lines.append("- 未启用")
        return "\n".join(lines)

    phase = _phase()
    next_time = state.get("next_second_soul_time", 0)

    if phase == "idle":
        lines.append("- 当前：闲置（窍中温养）")
        if next_time > 0:
            lines.append(f"- 下次检查：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "status_pending":
        lines.append("- 当前：状态查询中…")
        if next_time > 0:
            lines.append(f"- 回捞校准：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "ready_to_train":
        lines.append("- 当前：已归位，等待修炼入队")
        if next_time > 0:
            lines.append(f"- 入队时间：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "train_pending":
        lines.append("- 当前：修炼指令已发送，等待确认…")
        if next_time > 0:
            lines.append(f"- 回捞校准：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "cultivating":
        lines.append("- 当前：修炼中")
        if "短复查" in str(state.get("second_soul_last_error") or ""):
            lines.append(f"- 下次复查：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
        else:
            lines.append(f"- 修炼结束：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "injured":
        lines.append("- 当前：受伤")
        lines.append(f"- 恢复后：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "heart_demon_pending":
        deadline = state.get("second_soul_heart_demon_deadline", 0)
        if state.get("second_soul_auto_choice_enabled", True):
            lines.append(f"- ⚠️ 心魔试炼中，自动抉择：{_choice_label()}")
        else:
            lines.append("- ⚠️ 心魔试炼中，自动抉择关闭")
        if deadline > 0:
            lines.append(f"- 抉择截止：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）")
        msg_id = state.get("second_soul_heart_demon_msg_id", 0)
        if msg_id:
            lines.append(f"- 警示消息：{msg_id}")
    elif phase == "purge_pending":
        lines.append("- 当前：魔染镇压中")
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > 0:
            lines.append(f"- 镇魔确认：{fmt_abs_ts(due_at)}（{fmt_remaining(due_at)}）")
        lines.append(f"- 镇魔次数：{int(state.get('second_soul_purge_attempts', 0) or 0)}/{SECOND_SOUL_PURGE_MAX_ATTEMPTS}")
    elif phase == "purge_ready":
        lines.append("- 当前：等待镇魔入队")
    elif phase == "purge_status_pending":
        lines.append("- 当前：镇魔后查魔染")
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > 0:
            lines.append(f"- 查询确认：{fmt_abs_ts(due_at)}（{fmt_remaining(due_at)}）")
    elif phase == "not_unlocked":
        lines.append("- 未凝练第二元神")
        if next_time > 0:
            lines.append(f"- 下次重试：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")

    moran = int(state.get("second_soul_moran_value", 0) or 0)
    if moran:
        lines.append(f"- 魔染：{moran}")
    lines.append(f"- 自动镇魔阈值：魔染 ≥ {get_second_soul_purge_threshold()}")
    last_err = state.get("second_soul_last_error", "")
    if last_err:
        lines.append(f"- 最近异常：{last_err}")
    if phase != "heart_demon_pending":
        lines.append(f"- 心魔抉择：{'自动' if state.get('second_soul_auto_choice_enabled', True) else '手动'} / {_choice_label()}")
    return "\n".join(lines)


# ============== reply 路径 handlers ==============

def _is_second_soul_panel(text):
    return bool(RE_SECOND_SOUL_PANEL_HEAD.search(text or ""))


def _parse_status_field(text):
    """从面板文本里抠出 (status, remain_sec)。
    status 是简洁串：'窍中温养' / '修炼中' / '受伤' / '心魔试炼中'
    remain_sec 没有则 0。
    """
    m = RE_SECOND_SOUL_STATUS_LINE.search(text or "")
    if not m:
        return None, 0
    status = m.group(1).strip()
    remain_str = m.group(2)
    remain_sec = 0
    if remain_str and has_wait_time(remain_str):
        remain_sec = parse_wait_time(remain_str)
    return status, remain_sec


async def handle_second_soul_status_reply(text, now, reply_to, matched_family=None, *, reply_context=None):
    """处理 .第二元神 命令的回复（面板）。
    也兼容 .元神修炼 收到面板（罕见但可能）。
    """
    not_unlocked = "尚未凝练第二元神" in text
    if not _is_second_soul_panel(text) and not not_unlocked:
        return False

    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    is_relevant = (
        matched_family in ("second_soul_status", "second_soul_train")
        or CMD_SECOND_SOUL_STATUS in orig_cmd
        or CMD_SECOND_SOUL_TRAIN in orig_cmd
    )
    if not is_relevant:
        return False
    kind = "train" if orig_cmd.strip() == CMD_SECOND_SOUL_TRAIN or matched_family == "second_soul_train" else "status"
    operation = _reply_operation(kind, reply_to, now, reply_context)
    if operation is None and kind == "status":
        kind = "status_read"
        operation = _reply_operation(kind, reply_to, now, reply_context)
    if operation is None:
        return False
    if operation["status"] == "complete":
        return True
    if not_unlocked:
        _complete_command(kind, operation)
        if _phase() not in {"purge_ready", "purge_pending", "purge_status_pending"}:
            _set_phase("not_unlocked")
            state["next_second_soul_time"] = operation["reply_at"] + SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC
            state["second_soul_last_error"] = "尚未凝练第二元神"
            _clear_pending_msg_ids()
        save_state()
        return True
    status, remain_sec = _parse_status_field(text)
    if status not in {"窍中温养", "修炼中", "受伤", "心魔试炼中"}:
        return False
    now = operation["reply_at"]
    level_text = parse_second_soul_level_text(text)
    if level_text:
        update_identity_level_record(
            get_current_identity_id(),
            "second_soul_level",
            level_text,
            now=now,
            source="second_soul_status",
        )
    _complete_command(kind, operation)
    if kind == "status_read":
        _calibrate_pending_from_panel("status", operation)
    phase = _phase()
    if phase in {"purge_ready", "purge_pending", "purge_status_pending"}:
        save_state()
        return True

    state["second_soul_last_error"] = ""

    if status == "窍中温养":
        if phase == "train_pending":
            save_state()
            console_log("🌀 修炼确认等待中，忽略窍中温养状态面板，避免重复 .元神修炼。")
            return True
        if phase == "ready_to_train":
            save_state()
            console_log("🌀 第二元神已在修炼入队状态，忽略重复的窍中温养面板。")
            return True
        if phase == "cultivating" and _recently_confirmed_training(now):
            save_state()
            console_log("🌀 已确认进入 24h 修炼态，忽略过早的窍中温养面板，避免旧回复回滚状态。")
            return True
        if kind in {"status", "status_read"}:
            _calibrate_pending_from_panel("train", operation)
        _mark_ready_to_train(now)
        save_state()
        await send_audit_log("🌀 第二元神已确认窍中温养，修炼指令进入安全队列。")
        return True

    if status == "修炼中":
        if kind in {"status", "status_read"}:
            _calibrate_pending_from_panel("train", operation)
        _set_phase("cultivating")
        _clear_heart_demon()
        _clear_pending_msg_ids()
        if remain_sec > 0:
            state["next_second_soul_time"] = now + remain_sec + CD_BUFFER_SEC
        else:
            # 无剩余字段（比如刚开始或已快结束）：30-60min 后再查
            state["next_second_soul_time"] = now + random.uniform(SECOND_SOUL_RECHECK_MIN, SECOND_SOUL_RECHECK_MAX)
        save_state()
        await send_audit_log(f"🌀 第二元神修炼中，下次检查→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    if status == "受伤":
        if kind in {"status", "status_read"}:
            _calibrate_pending_from_panel("train", operation)
        _set_phase("injured")
        _clear_heart_demon()
        _clear_pending_msg_ids()
        if remain_sec > 0:
            state["next_second_soul_time"] = now + remain_sec + CD_BUFFER_SEC
        else:
            state["next_second_soul_time"] = now + SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC
        save_state()
        await send_audit_log(f"🤕 第二元神受伤，恢复后→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    if status == "心魔试炼中":
        if kind in {"status", "status_read"}:
            _calibrate_pending_from_panel("train", operation)
        # 通过面板得知心魔——可能 broadcast 警示我们漏了
        _set_phase("heart_demon_pending")
        if state.get("second_soul_heart_demon_deadline", 0) <= 0:
            # 我们漏了警示 broadcast，没法精确知道剩余时间，给一个保守 deadline
            state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
        _clear_pending_msg_ids()
        save_state()
        if not state.get("second_soul_heart_demon_notified", False):
            state["second_soul_heart_demon_notified"] = True
            save_state()
            await send_audit_log(
                "⚠️ 第二元神心魔试炼中（通过状态查询发现）！需人工 .抉择 强行突破/稳固道心。"
            )
        return True

    return False


async def handle_second_soul_purge_reply(text, now, reply_to, matched_family=None, *, reply_context=None):
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    is_relevant = (
        matched_family == "second_soul_purge"
        or CMD_SECOND_SOUL_PURGE in orig_cmd
    )
    if not is_relevant:
        return False
    operation = _reply_operation("purge", reply_to, now, reply_context)
    if operation is None:
        return False
    if operation["status"] == "complete":
        return True
    moran = _parse_moran_value(text)
    if moran is not None and "【元神镇魔】" in text:
        _complete_command("purge", operation)
        now = operation["reply_at"]
        state["second_soul_moran_value"] = moran
        if operation.get("manual") and _phase() in {"cultivating", "injured", "heart_demon_pending", "not_unlocked"}:
            save_state()
            return True
        if operation.get("manual"):
            state["second_soul_purge_attempts"] = min(
                SECOND_SOUL_PURGE_MAX_ATTEMPTS, int(state.get("second_soul_purge_attempts") or 0) + 1,
            )
        if moran >= get_second_soul_purge_threshold() and int(state.get("second_soul_purge_attempts", 0) or 0) < SECOND_SOUL_PURGE_MAX_ATTEMPTS:
            send_as_id = get_current_identity_id()
            _set_phase("purge_ready")
            state["second_soul_purge_due_at"] = now
            if save_state() is False:
                return False
            await _send_second_soul_purge(send_as_id, now, reason=f"镇魔回复显示魔染 {moran}。")
            return True
        _finish_purge_ready(now, moran=moran)
        save_state()
        await send_audit_log(
            f"🌀 第二元神镇魔回复已收口，当前魔染 {moran}，修炼指令恢复队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=220,
        )
        return True
    return False


async def handle_second_soul_demon_status_reply(text, now, reply_to, matched_family=None, *, reply_context=None):
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    is_relevant = (
        matched_family == "second_soul_demon_status"
        or CMD_SECOND_SOUL_DEMON_STATUS in orig_cmd
    )
    if not is_relevant:
        return False
    operation = _reply_operation("demon_status", reply_to, now, reply_context)
    if operation is None:
        return False
    if operation["status"] == "complete":
        return True
    moran = _parse_moran_value(text)
    if moran is None or not ("【五子同心魔】" in text or _is_second_soul_panel(text)):
        return False
    _complete_command("demon_status", operation)
    now = operation["reply_at"]
    state["second_soul_moran_value"] = moran
    if operation.get("manual") and _phase() in {"cultivating", "injured", "heart_demon_pending", "not_unlocked"}:
        save_state()
        return True
    threshold = get_second_soul_purge_threshold()
    pending_purge = (_command_records() or {}).get("purge", {})
    if pending_purge.get("status") in {"sending", "sent", "unknown"}:
        if moran >= threshold:
            _set_phase("purge_pending")
            state["second_soul_purge_due_at"] = now + SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC
            state["second_soul_last_error"] = "原镇魔结果仍未确认，保留操作，不按高魔染重复消耗修为"
            save_state()
            return True
        _calibrate_pending_from_panel("purge", operation)
    if moran >= threshold and int(state.get("second_soul_purge_attempts", 0) or 0) < SECOND_SOUL_PURGE_MAX_ATTEMPTS:
        send_as_id = get_current_identity_id()
        _set_phase("purge_ready")
        state["second_soul_purge_due_at"] = now
        if save_state() is False:
            return False
        await _send_second_soul_purge(
            send_as_id,
            now,
            reason=f"五子同心魔确认魔染 {moran}。",
        )
        return True
    _finish_purge_ready(now, moran=moran)
    save_state()
    if moran >= threshold:
        await send_audit_log(
            f"⚠️ 第二元神魔染仍为 {moran}，但自动镇魔已达上限，停止补发并恢复修炼队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=260,
        )
    else:
        await send_audit_log(
            f"🌀 第二元神魔染已低于阈值（{moran}），修炼指令恢复队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=220,
        )
    return True


async def handle_second_soul_train_reply(text, now, reply_to, matched_family=None, *, reply_context=None):
    """处理 .元神修炼 命令的回复。"""
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    is_relevant = (
        matched_family == "second_soul_train"
        or CMD_SECOND_SOUL_TRAIN in orig_cmd
    )
    if not is_relevant:
        return False
    operation = _reply_operation("train", reply_to, now, reply_context)
    if operation is None:
        return False
    if operation["status"] == "complete":
        return True
    now = operation["reply_at"]

    # 修炼成功
    if "你的第二元神已开始闭关修炼" in text and "24小时" in text:
        _complete_command("train", operation)
        _set_phase("cultivating")
        state["next_second_soul_time"] = now + SECOND_SOUL_TRAIN_CD_SEC + CD_BUFFER_SEC
        state["second_soul_last_train_started_at"] = now
        state["second_soul_last_error"] = ""
        _clear_heart_demon()
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log(f"🌀 第二元神已修炼→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    # 各种"无法分心修炼"
    if "无法分心修炼" in text:
        if "(修炼中)" in text or "（修炼中）" in text:
            _complete_command("train", operation)
            _set_phase("cultivating")
            state["next_second_soul_time"] = now + random.uniform(
                SECOND_SOUL_RECHECK_MIN, SECOND_SOUL_RECHECK_MAX
            )
            state["second_soul_last_error"] = "修炼中未返回剩余时间，短复查校准"
            _clear_pending_msg_ids()
            save_state()
            console_log("🌀 第二元神已在修炼中，稍后复查。")
            return True
        if "(受伤)" in text or "（受伤）" in text:
            _complete_command("train", operation)
            _set_phase("injured")
            state["next_second_soul_time"] = now + SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC
            _clear_pending_msg_ids()
            save_state()
            console_log("🤕 第二元神受伤中，6h 后复查。")
            return True
        if "(心魔试炼中)" in text or "（心魔试炼中）" in text:
            _complete_command("train", operation)
            _set_phase("heart_demon_pending")
            if state.get("second_soul_heart_demon_deadline", 0) <= 0:
                state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
            _clear_pending_msg_ids()
            save_state()
            if not state.get("second_soul_heart_demon_notified", False):
                state["second_soul_heart_demon_notified"] = True
                save_state()
                await send_audit_log("⚠️ 第二元神心魔试炼中（通过修炼指令发现）！需人工抉择。")
            return True

    # 尚未凝练
    if "尚未凝练第二元神" in text:
        _complete_command("train", operation)
        _set_phase("not_unlocked")
        state["next_second_soul_time"] = now + SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC
        state["second_soul_last_error"] = "尚未凝练第二元神"
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log("ℹ️ 尚未凝练第二元神，已进入冻结，7 天后重试。")
        return True

    return False


# ============== broadcast 路径 handlers ==============

def _match_identity_by_at_username(text):
    """从 broadcast 文本里提取 @username，匹配到 enabled 的 identity。
    返回 (target_id, matched_ids)。target_id 仅在唯一匹配时非 None。
    """
    mentions = {username.casefold() for username in RE_AT_USERNAME.findall(text or "")}
    if not mentions:
        return None, []
    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state.get("second_soul_enabled", False):
                continue
            tags = get_send_as_tags(identity_id) or []
            usernames = {tag[1:].casefold() for tag in tags if RE_AT_USERNAME.fullmatch(tag)}
            if mentions & usernames:
                matched_ids.append(identity_id)
    target = matched_ids[0] if len(matched_ids) == 1 else None
    return target, matched_ids


def _match_heart_demon_identity(event):
    """Terminal edits retain the warning ID, not the shared topic reply header."""
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    reply_id = int(
        getattr(event, "reply_to_msg_id", 0)
        or getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0
    )
    message_ids = {int(getattr(event, "id", 0) or 0), reply_id} - {0}
    if not chat_id or not message_ids:
        return None, []
    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if _phase() != "heart_demon_pending" or int(state.get("second_soul_heart_demon_chat_id") or 0) != chat_id:
                continue
            if state.get("second_soul_heart_demon_account_id") != get_identity_account(identity_id):
                continue
            anchors = {
                int(state.get("second_soul_heart_demon_msg_id") or 0),
                int(state.get("second_soul_heart_demon_choice_msg_id") or 0),
            } - {0}
            if anchors & message_ids:
                matched_ids.append(identity_id)
    target = matched_ids[0] if len(matched_ids) == 1 else None
    return target, matched_ids


async def handle_second_soul_heart_demon_warning_broadcast(text, now, event_msg_id, *, event_chat_id=0, event=None):
    """处理【天道警示·心魔试炼】broadcast。含 @username。
    event_msg_id 是这条警示自己的 msg_id（用于以后回复 .抉择）。
    """
    if "【天道警示·心魔试炼】" not in text:
        return False
    if "第二元神" not in text:
        return False
    if event is not None:
        # Editing or delivering an old warning does not renew its choice window.
        now = _number(getattr(getattr(event, "date", None), "timestamp", lambda: 0)())
        if not 0 < now <= time.time() + 1:
            return False
    event_msg_id = int(event_msg_id or 0)
    event_chat_id = int(event_chat_id or 0)

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 心魔警示命中多个身份，已跳过自动标记：{names}",
                scope="global", limit=280,
            )
        return False

    should_send_choice = False
    owner = _capture_owner(target_id)
    with use_identity(target_id):
        if _broadcast_is_stale(now) and not (
            _phase() == "heart_demon_pending" and not state.get("second_soul_heart_demon_msg_id")
            and now >= _number(state.get("second_soul_last_train_started_at"))
        ):
            return False
        if _phase() == "heart_demon_pending":
            existing_msg_id = int(state.get("second_soul_heart_demon_msg_id") or 0)
            if existing_msg_id:
                # Old state may lack a chat but may already have sent a choice.
                if existing_msg_id == event_msg_id and event_chat_id and not state.get("second_soul_heart_demon_chat_id"):
                    state["second_soul_heart_demon_chat_id"] = event_chat_id
                    save_state()
                return True
            if state.get("second_soul_heart_demon_choice_msg_id"):
                return True
        _set_phase("heart_demon_pending")
        state["second_soul_heart_demon_msg_id"] = event_msg_id
        state["second_soul_heart_demon_chat_id"] = event_chat_id
        state["second_soul_heart_demon_account_id"] = owner[2]
        state["second_soul_heart_demon_choice_msg_id"] = 0
        state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
        state["second_soul_heart_demon_notified"] = True
        _clear_pending_msg_ids()
        expired = max(now, time.time()) >= state["second_soul_heart_demon_deadline"]
        should_send_choice = event_msg_id > 0 and bool(event_chat_id) and not expired and bool(state.get("second_soul_auto_choice_enabled", True))
        missing_anchor = not event_chat_id or event_msg_id <= 0
        if missing_anchor:
            state["second_soul_last_error"] = "心魔警示缺少原始群或消息 ID，未自动抉择"
        elif expired:
            state["second_soul_last_error"] = "原心魔警示已过期，等待状态校准，不重开抉择窗口"
        if save_state() is False:
            return False
        choice_command = _choice_command()
        choice_label = _choice_label()
        expected = _business_snapshot(owner[1])

        def can_send_choice():
            if not _owns_identity(owner, enabled=True):
                return False
            with use_identity(target_id):
                return bool(
                    _business_snapshot(owner[1]) == expected and not _command_ownership_error()
                    and max(now, time.time()) < _number(owner[1].get("second_soul_heart_demon_deadline"))
                )
        await send_audit_log(
            (
                f"🔥 第二元神心魔试炼来袭，自动回复警示消息 {event_msg_id}：\n  {choice_command}"
                if should_send_choice
                else (
                    "⚠️ 第二元神心魔警示缺少原始群或消息 ID，未自动抉择。"
                    if missing_anchor else "第二元神原心魔警示已过期，等待状态校准。"
                    if expired else f"🔥 第二元神心魔试炼来袭，自动抉择已关闭，请人工回复警示消息 {event_msg_id}。"
                )
            ),
            scope="identity", send_as_id=target_id, limit=280,
        )
    if should_send_choice:
        if not can_send_choice():
            return True
        with use_identity(target_id):
            if (
                not state.get("second_soul_enabled") or not state.get("second_soul_auto_choice_enabled", True)
                or _phase() != "heart_demon_pending"
                or int(state.get("second_soul_heart_demon_msg_id") or 0) != event_msg_id
                or int(state.get("second_soul_heart_demon_chat_id") or 0) != event_chat_id
            ):
                return True
        msg = await send_game_command(
            choice_command,
            track=False,
            reply_to=int(event_msg_id or 0),
            target_chat_id=event_chat_id,
            send_as_id=target_id,
            priority="reactive",
            operation_check=can_send_choice,
        )
        if not _owns_identity(owner) or _business_snapshot(owner[1]) != expected:
            return True
        with use_identity(target_id):
            if (
                _phase() != "heart_demon_pending"
                or int(state.get("second_soul_heart_demon_msg_id") or 0) != event_msg_id
                or int(state.get("second_soul_heart_demon_chat_id") or 0) != event_chat_id
            ):
                return True
            if msg:
                state["second_soul_heart_demon_choice_msg_id"] = int(getattr(msg, "id", 0) or 0)
                state["second_soul_last_error"] = ""
                save_state()
                await send_audit_log(
                    f"🔥 第二元神已自动抉择{choice_label}，等待结算编辑消息 {event_msg_id}。",
                    scope="identity", send_as_id=target_id, limit=220,
                )
            else:
                state["second_soul_last_error"] = f"自动抉择{choice_label}发送结果未确认，等待原警示结算或 deadline 自检"
                save_state()
                await send_audit_log(
                    f"⚠️ 第二元神自动抉择{choice_label}结果未确认，保留警示消息 {event_msg_id} 等待反馈。",
                    scope="identity", send_as_id=target_id, limit=260,
                )
    return True


async def handle_second_soul_choice_result_broadcast(text, now, event=None):
    """处理心魔结算结果，使用原警示/抉择消息的群与 ID 归属。
    覆盖：稳扎稳打·成功 / 破而后立·成功 / 破而后立·失败
    """
    is_stable_success = "【稳扎稳打·成功】" in text
    is_break_success = "【破而后立·成功】" in text
    is_break_fail = "【破而后立·失败】" in text
    if not (is_stable_success or is_break_success or is_break_fail):
        return False
    now = _broadcast_time(event, now)
    if now <= 0:
        return False

    target_id, matched = _match_heart_demon_identity(event)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 心魔结算锚点命中多个身份，跳过自动归属：{names}",
                scope="global", limit=280,
            )
        return False

    with use_identity(target_id):
        if _broadcast_is_stale(now):
            return False
        _remember_broadcast("choice", text, now)
        if is_break_fail:
            _set_phase("injured")
            state["next_second_soul_time"] = now + SECOND_SOUL_TRAIN_CD_SEC + CD_BUFFER_SEC
            _clear_heart_demon()
            _clear_pending_msg_ids()
            save_state()
            await send_audit_log(
                f"💀 第二元神破而后立失败，受伤 24 小时→{fmt_abs_ts(state['next_second_soul_time'])}",
                scope="identity", send_as_id=target_id,
            )
        else:
            # 成功即已结算并可继续修炼，交给 scheduler 通过全局锁发送 .元神修炼。
            _mark_ready_to_train(now)
            save_state()
            label = "稳扎稳打·成功" if is_stable_success else "破而后立·成功"
            await send_audit_log(
                f"✨ 第二元神 {label}！本轮结算完成，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    return True


async def handle_second_soul_return_broadcast(text, now, *, event=None):
    """处理正常修炼结束的【第二元神归位】广播。
    这是确定可修炼状态，但不在广播 handler 里直接发命令。
    """
    if "【第二元神归位】" not in text:
        return False
    if "已结束修炼" not in text or "回归窍中温养" not in text:
        return False
    now = _broadcast_time(event, now)

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 第二元神归位 broadcast 命中多个身份，跳过：{names}",
                scope="global", limit=280,
            )
        return False

    should_purge = False
    moran = None
    owner = _capture_owner(target_id)
    with use_identity(target_id):
        phase = _phase()
        if _command_ownership_error() or _broadcast_is_stale(now):
            return False
        if _is_recent_duplicate_broadcast("return", text, now):
            return True
        if phase in ("train_pending", "purge_ready", "purge_pending", "purge_status_pending"):
            return True
        if phase == "cultivating" and _recently_confirmed_training(now):
            return True
        _remember_broadcast("return", text, now)
        moran = _remember_moran_from_text(text)
        if moran is not None and moran >= get_second_soul_purge_threshold():
            _reset_purge_state(keep_moran=True)
            _set_phase("purge_ready")
            state["second_soul_purge_due_at"] = now
            should_purge = True
        elif phase == "ready_to_train":
            save_state()
            return True
        else:
            _mark_ready_to_train(now)
        if save_state() is False:
            return False
        expected = _business_snapshot(owner[1])
        if should_purge:
            await send_audit_log(
                f"🌀 第二元神已归位但魔染 {moran}，先镇魔再恢复修炼队列。",
                scope="identity", send_as_id=target_id, limit=240,
            )
        else:
            await send_audit_log(
                "🌀 第二元神已归位，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    if should_purge and _owns_identity(owner, enabled=True) and _business_snapshot(owner[1]) == expected:
        await _send_second_soul_purge(target_id, now, reason="归位广播触发。")
    return True


async def handle_second_soul_recovery_broadcast(text, now, *, event=None):
    """处理两种带 @username 的恢复广播：
    - "你的第二元神已从【受伤】状态中恢复"
    - "心魔幻境已消散，元神已自动归位（本次修炼无收益）"
    """
    is_injury_recovery = "你的第二元神已从【受伤】状态中恢复" in text
    is_heart_demon_dissolve = "心魔幻境已消散" in text and "元神已自动归位" in text
    if not (is_injury_recovery or is_heart_demon_dissolve):
        return False
    now = _broadcast_time(event, now)

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 第二元神恢复 broadcast 命中多个身份，跳过：{names}",
                scope="global", limit=280,
            )
        return False

    with use_identity(target_id):
        if _command_ownership_error() or _broadcast_is_stale(now):
            return False
        if _is_recent_duplicate_broadcast("recovery", text, now):
            return True
        if _phase() in ("ready_to_train", "train_pending", "purge_ready", "purge_pending", "purge_status_pending"):
            return True
        if _phase() == "cultivating" and _recently_confirmed_training(now):
            console_log("🌀 忽略迟到的第二元神恢复广播：当前已是新的 24h 修炼态。")
            return True
        _remember_broadcast("recovery", text, now)
        _mark_ready_to_train(now)
        save_state()
        if is_heart_demon_dissolve:
            await send_audit_log(
                "🌫️ 第二元神心魔幻境消散（本次无收益），修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
        else:
            await send_audit_log(
                "🩹 第二元神受伤恢复，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    return True


# ============== scheduler ==============

def _second_soul_pending_replay_spec(phase):
    return {
        "status_pending": ("second_soul_status_msg_id", CMD_SECOND_SOUL_STATUS, "second_soul_status", handle_second_soul_status_reply),
        "train_pending": ("second_soul_train_msg_id", CMD_SECOND_SOUL_TRAIN, "second_soul_train", handle_second_soul_train_reply),
        "purge_pending": ("second_soul_purge_msg_id", CMD_SECOND_SOUL_PURGE, "second_soul_purge", handle_second_soul_purge_reply),
        "purge_status_pending": ("second_soul_purge_status_msg_id", CMD_SECOND_SOUL_DEMON_STATUS, "second_soul_demon_status", handle_second_soul_demon_status_reply),
    }.get(str(phase or ""))


async def _recover_second_soul_pending_from_message_log(now, phase, *, tail=False, command_kind=None):
    spec = _second_soul_pending_replay_spec(phase)
    if not spec:
        return False
    _state_key, command, family, handler = spec
    kind = command_kind or next(kind for kind, value in SECOND_SOUL_COMMANDS.items() if value[0] == command)
    owner = _capture_owner()
    records = _command_records()
    record = records.get(kind) if records is not None else None
    if not record or record["account_id"] != owner[2] or record["status"] in {"complete", "unsent", "expired"}:
        return False
    recovered = _adopt_command_receipt(record, now)
    if recovered["msg_id"] <= 0:
        return False
    if recovered != record:
        _store_command(kind, recovered)
        save_state()
    msg_id = recovered["msg_id"]
    finder = find_message_log_replies_tail if tail else find_message_log_replies
    replies = finder(
        msg_id,
        now,
        lookback_sec=SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC,
        lookahead_sec=5,
        chat_id=recovered["chat_id"],
        predicate=lambda entry: (
            str((entry or {}).get("event_type") or "") in {"message", "edit"}
            and bool((entry or {}).get("sender_is_bot"))
            and _message_id((entry or {}).get("sender_id")) in get_game_bot_ids()
            and _number((entry or {}).get("server_event_at")) >= _number(recovered.get("dispatch_at") or recovered["started_at"]) - 1
        ),
    )
    reply_to = SimpleNamespace(id=msg_id, chat_id=recovered["chat_id"], raw_text=command)
    for entry in replies:
        if not _owns_identity(owner):
            return True
        if (
            _message_id(entry.get("chat_id")) != recovered["chat_id"]
            or _message_id(entry.get("reply_to_msg_id")) != msg_id
            or _message_id(entry.get("sender_id")) not in get_game_bot_ids()
            or entry.get("event_type") not in {"message", "edit"}
        ):
            continue
        before = _business_snapshot(owner[1])
        handled = await handler(
            str((entry or {}).get("text") or ""),
            float((entry or {}).get("ts_epoch") or now),
            reply_to,
            matched_family=family,
            reply_context={
                "send_as_id": owner[0], "account_id": owner[2], "root_msg_id": msg_id,
                "chat_id": recovered["chat_id"], "server_event_at": entry.get("server_event_at"),
            },
        )
        if not _owns_identity(owner):
            return True
        if handled or _business_snapshot(owner[1]) != before:
            console_log(f"🌀 第二元神 {phase} 日志回捞成功，原消息ID={msg_id}。")
            return True
    return False

async def run_second_soul_scheduler(now):
    owner = _capture_owner()
    if not _owns_identity(owner, enabled=True):
        return
    message = _command_ownership_error()
    if message:
        if state.get("second_soul_last_error") != message:
            state["second_soul_last_error"] = message
            save_state()
            await send_audit_log(message, scope="identity", send_as_id=owner[0], limit=220)
        return

    phase = _phase()

    if phase == "purge_ready":
        if _number(state.get("second_soul_purge_due_at")) <= now:
            await _send_second_soul_purge(owner[0], now)
        return

    if any(record["op_id"] in _SECOND_SOUL_INFLIGHT for record in _command_records().values()):
        return

    # not_unlocked 长冻结：仅到点重试
    if phase == "not_unlocked":
        if state.get("next_second_soul_time", 0) > now:
            return
        # 到点：清状态重新查询
        _set_phase("idle")
        save_state()
        # 走下面的查询路径

    # heart_demon_pending：deadline 兜底防死锁
    if phase == "heart_demon_pending":
        deadline = state.get("second_soul_heart_demon_deadline", 0)
        if deadline <= 0:
            deadline = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
            state["second_soul_heart_demon_deadline"] = deadline
            state["second_soul_last_error"] = "心魔 deadline 缺失，已补齐"
            save_state()
        if deadline > 0 and now > deadline + 60:
            # deadline 过了 1 分钟还没收到结果 broadcast，强制清状态自检
            _set_phase("idle")
            state["next_second_soul_time"] = now
            _clear_heart_demon()
            save_state()
            await send_audit_log(
                "⏰ 第二元神心魔抉择 deadline 已过仍无结算广播，强制重查状态。"
            )
            if not _owns_identity(owner, enabled=True) or _phase() != "idle":
                return
            phase = "idle"
        else:
            return  # 仍在抉择期，不发任何命令

    if phase == "purge_pending":
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > now:
            return
        if await _recover_second_soul_pending_from_message_log(now, phase):
            return
        if not _owns_identity(owner, enabled=True) or _phase() != phase:
            return
        attempts = int(state.get("second_soul_purge_attempts", 0) or 0)
        query = (_command_records() or {}).get("demon_status", {})
        if query.get("status") in {"complete", "expired"}:
            state["second_soul_purge_due_at"] = now + SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC
            save_state()
            return
        if attempts <= 1:
            await send_audit_log("🌀 第二元神镇魔未收到回复，补查五子同心魔确认魔染。")
            if _owns_identity(owner, enabled=True) and _phase() == phase:
                await _send_second_soul_demon_status(owner[0], now)
            return
        state["second_soul_purge_due_at"] = now + SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC
        state["second_soul_last_error"] = "第二次元神镇魔结果未确认，保留原操作等待反馈"
        save_state()
        await send_audit_log(
            "⚠️ 第二元神第二次元神镇魔仍无回复，停止补发，保留原操作等待反馈。",
            limit=260,
        )
        return

    if phase == "purge_status_pending":
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > now:
            return
        if await _recover_second_soul_pending_from_message_log(now, phase):
            return
        if not _owns_identity(owner, enabled=True) or _phase() != phase:
            return
        query = (_command_records() or {}).get("demon_status")
        if query:
            _store_command("demon_status", dict(query, status="expired"))
        _set_phase("purge_pending")
        state["second_soul_purge_due_at"] = now + SECOND_SOUL_LOG_REPLAY_LOOKBACK_SEC
        state["second_soul_last_error"] = "五子同心魔查询无回复，保留原镇魔操作等待反馈"
        save_state()
        await send_audit_log(
            "⚠️ 第二元神五子同心魔查询无回复，停止补发，等待原镇魔反馈。",
            limit=260,
        )
        return

    if phase in ("status_pending", "train_pending"):
        # 第二元神不用通用 retry 补发，避免 bot 延迟时重复刷屏。
        # 查询可以过期重查；未知修炼仍保留归属，只允许后发面板校准。
        if state.get("next_second_soul_time", 0) > now:
            return
        if await _recover_second_soul_pending_from_message_log(now, phase):
            return
        if not _owns_identity(owner, enabled=True) or _phase() != phase:
            return
        if phase == "status_pending":
            record = (_command_records() or {}).get("status")
            if record:
                _store_command("status", dict(record, status="expired"))
                _clear_command_pending("status", record)
        _set_phase("idle")
        state["next_second_soul_time"] = now
        if phase == "train_pending":
            state["second_soul_last_error"] = "修炼确认等待超时，保留原操作并重查状态"
        else:
            state["second_soul_last_error"] = "状态查询等待超时，已自愈重查"
        _clear_pending_msg_ids()
        save_state()
        if phase == "train_pending":
            await send_audit_log("🌀 第二元神修炼确认等待超时，保留原操作并查询状态，不补发修炼。")
        else:
            await send_audit_log("🌀 第二元神状态等待超时，已清理旧指令并重新查询。")
        if not _owns_identity(owner, enabled=True) or _phase() != "idle":
            return
        phase = "idle"

    if phase == "ready_to_train":
        if state.get("next_second_soul_time", 0) > now:
            return
        await _send_owned_command("train", owner[0], now)
        return

    # 到点：发 .第二元神 查状态
    if state.get("next_second_soul_time", 0) > now:
        return

    await _send_owned_command("status", owner[0], now)


async def run_second_soul_bootstrap_check(now):
    """已弃用：残留清理移至 control._restore_second_soul_runtime（启动一次性）。
    保留空函数以维持 scheduler 注册兼容。
    """
    return


__all__ = [
    "get_second_soul_purge_threshold",
    "get_second_soul_status_text",
    "handle_second_soul_choice_result_broadcast",
    "handle_second_soul_demon_status_reply",
    "handle_second_soul_heart_demon_warning_broadcast",
    "handle_second_soul_purge_reply",
    "handle_second_soul_recovery_broadcast",
    "handle_second_soul_return_broadcast",
    "handle_second_soul_status_reply",
    "handle_second_soul_train_reply",
    "remember_second_soul_status_read",
    "restore_second_soul_runtime",
    "run_second_soul_bootstrap_check",
    "run_second_soul_scheduler",
]
