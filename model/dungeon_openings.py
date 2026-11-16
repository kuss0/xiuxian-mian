"""Shared dungeon opening budgets, learned passively from trusted game replies.

Reward quotas belong to each feature. These limits only decide who may open
the next room, never whether an existing room may continue or receive rewards.
"""
import asyncio
import copy
import time
from datetime import datetime
from types import SimpleNamespace

from .config import MESSAGES_DIR, TZ_LOCAL
from .features.dungeon_limits import (OPENERS, GROUP_COOLDOWN, ROOT_DAILY_LIMIT,
                                    GROUP_DAILY_LIMIT, MARGIN, day, next_day, rejection)
from .message_log_recovery import iter_message_log_rows, parse_message_log_ts
from .persistence import mark_dirty, save_state
from .runtime import get_last_game_send_block, is_game_send_definitely_unsent
from .state import (get_avatar_owner_id, get_expedition_run_state, get_game_bot_ids,
                    get_game_group_ids, get_hunt_run_state, get_identity_ids)

_LOADED = False
_LOAD_LOCK = None
_EARLY = []
_REPLAYING = False


def _records():
    return get_expedition_run_state().setdefault("dungeon_openings", {})


def _change(update):
    records = _records()
    if _REPLAYING:
        return update(records)
    before = copy.deepcopy(records)
    try:
        if records.get("day") != day(time.time()):
            cutoff = datetime.fromtimestamp(time.time(), TZ_LOCAL).replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp() - GROUP_COOLDOWN - MARGIN
            records.update(day=day(time.time()), openings={k: v for k, v in records.get("openings", {}).items()
                if v.get("at", 0) >= cutoff or v.get("phase") == "pending"})
        result = update(records)
        if records != before:
            mark_dirty()
            if save_state() is False:
                raise RuntimeError("副本开局限额保存失败")
        return result
    except BaseException:
        records.clear()
        records.update(before)
        raise


def block(identity, chat, now=None, *, owner=""):
    now = time.time() if now is None else now
    records = _records()
    root = str(get_avatar_owner_id(identity) or identity)
    counter = records.get("roots", {}).get(root, {})
    if counter.get("day") == day(now) and counter.get("used", 0) >= counter.get("limit", ROOT_DAILY_LIMIT):
        return {"reason": "主魂今日共享发起次数已满，可继续作为队员", "until": next_day(now)}
    group = records.get("groups", {}).get(str(chat), {})
    if group.get("day") == day(now) and group.get("used", 0) >= group.get("limit", GROUP_DAILY_LIMIT):
        return {"reason": "本群今日共享发起次数已满", "until": next_day(now)}
    if group.get("until", 0) > now:
        return {"reason": "本群副本共享开局冷却中", "until": group["until"]}
    for key, opening in records.get("openings", {}).items():
        if key == owner or opening.get("phase") != "pending":
            continue
        confirmation = _operator_closed(opening)
        if confirmation:
            # Retain the exact manual proof before the feature can replace
            # its old run with a new one. Otherwise that replacement would
            # make this already-reconciled claim block the new opening again.
            _change(lambda current: current["openings"][key].update(
                phase="operator_confirmed", reconciliation=confirmation))
            continue
        if opening.get("chat_id") == chat or str(opening.get("root")) == root:
            return {"reason": "已有副本开局等待回执，不重复发起", "until": now + 60}
    return {}


def _operator_closed(opening):
    """Honor the existing explicit, persisted manual reconciliation workflow."""
    if opening.get("command") == ".出海猎妖":
        runs = get_hunt_run_state().values()
    else:
        runs = get_expedition_run_state().get("weekly", {}).get("runs", {}).values()
    for run in runs:
        if (not run.get("operator_confirmed_at") or run.get("requires_reconciliation")
                or run.get("phase") not in {"stopped", "operator_confirmed"}):
            continue
        if run.get("chat_id") != opening.get("chat_id"):
            continue
        proof = {"at": run["operator_confirmed_at"], "run_id": run.get("id", run.get("started_at"))}
        attempts = run.get("command_attempts", run.get("attempts", {})).values()
        if any(a.get("command") == opening.get("command") and a.get("msg_id")
               and a["msg_id"] == opening.get("msg_id") for a in attempts):
            return proof
        prefix = (f"hunt:{run.get('started_at')}:" if opening.get("command") == ".出海猎妖"
                  else f"weekly:{run.get('kind')}:{run.get('id')}:")
        if str(opening.get("op_id") or "").startswith(prefix):
            return proof
    return None


def ready(identity, chat, now=None):
    return not block(identity, chat, now)


def _at(event):
    date = getattr(event, "date", None)
    return date.timestamp() if date is not None else 0


def _accepted(command, text):
    _kind, header, marker = OPENERS[command]
    return (text.startswith(header) and marker in text and "@" in text
            or command == ".天澜草原" and text == "你在草原边上等同行的人。")


def observe(event):
    """No history fetch, HTTP, Telegram RPC, or gameplay action in this observer."""
    chat = int(getattr(event, "chat_id", 0) or 0)
    at = _at(event)
    if (chat not in get_game_group_ids() or not at or at > time.time() + 30
            or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None)):
        return
    text = str(getattr(event, "raw_text", "") or "").strip()
    mid = int(getattr(event, "id", 0) or 0)
    sender = int(getattr(event, "sender_id", 0) or 0)
    if not mid:
        return
    if sender not in get_game_bot_ids():
        if text not in OPENERS:
            return
        def command_update(records):
            key = f"{chat}:{mid}"
            records.setdefault("openings", {}).setdefault(key, {
                "command": text, "chat_id": chat, "topic_id": int(getattr(event, "topic_id", 0) or
                    getattr(getattr(event, "reply_to", None), "reply_to_top_id", 0) or 0),
                "root": get_avatar_owner_id(sender) or sender if sender in get_identity_ids() else 0,
                "identity_id": sender, "msg_id": mid, "at": at, "phase": "observed"})
        _change(command_update)
        return
    reply = int(getattr(event, "reply_to_msg_id", 0) or
                getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    refused = rejection(text, at)
    command = next((cmd for cmd in OPENERS if _accepted(cmd, text)), "")
    if not refused and not command:
        return
    key = f"{chat}:{reply}"
    opening = _records().get("openings", {}).get(key)
    if opening and (at < opening["at"] - 1 or command and opening["command"] != command):
        return
    actual_topic = int(getattr(event, "topic_id", 0) or
        getattr(getattr(event, "reply_to", None), "reply_to_top_id", 0) or 0)
    if opening and actual_topic and actual_topic != opening.get("topic_id", 0):
        return
    if not opening:
        if reply and any(o.get("chat_id") == chat and o.get("phase") == "pending" and not o.get("msg_id")
                         for o in _records().get("openings", {}).values()):
            _EARLY.append(event)
            del _EARLY[:-32]
            return
        # A trusted, explicit group cooldown still applies when another user's
        # command was missed. A root refusal cannot be assigned without its opener.
        if refused and refused["scope"] == "root":
            _EARLY.append(event)
            del _EARLY[:-32]
            return
        # A reply to an unknown command may be a status/help query replaying
        # recruitment text. Only an observed opener can consume daily slots.
        if command and reply:
            return
        key = f"{chat}:{reply}" if reply else f"broadcast:{chat}:{mid}"

    def update(records):
        openings = records.setdefault("openings", {})
        current = openings.get(key)
        if current and current.get("phase") in {"accepted", "rejected"}:
            return
        current = current or {"command": command, "chat_id": chat, "root": 0, "at": at, "msg_id": reply}
        group = records.setdefault("groups", {}).setdefault(str(chat), {})
        today = day(time.time())
        current_day = day(at) == today
        if group.get("day") != today:
            group.update(day=today, used=0, limit=GROUP_DAILY_LIMIT)
        root = current.get("root", 0)
        counter = records.setdefault("roots", {}).setdefault(str(root), {}) if root else {}
        if root and counter.get("day") != today:
            counter.update(day=today, used=0, limit=ROOT_DAILY_LIMIT)
        proof = {"message_id": mid, "reply_to_msg_id": reply, "at": at, "text": text,
                 "sender_id": sender, "chat_id": chat, "topic_id": actual_topic}
        current.update(phase="rejected" if refused else "accepted", receipt=proof)
        openings[key] = current
        if refused:
            if refused["scope"] == "root" and root and current_day:
                counter.update(used=refused["limit"], limit=refused["limit"], evidence=proof)
            elif refused["scope"] == "group" and current_day:
                group.update(used=refused["limit"], limit=refused["limit"], evidence=proof)
            elif refused["scope"] == "cooldown":
                group.update(until=max(group.get("until", 0), refused["until"]), evidence=proof)
        else:
            group.update(used=group.get("used", 0) + int(current_day),
                         until=max(group.get("until", 0), at + GROUP_COOLDOWN + MARGIN), evidence=proof)
            if root and current_day:
                counter.update(used=counter.get("used", 0) + 1, evidence=proof)
    _change(update)


async def ensure_loaded():
    """Replay local evidence once before scheduling, outside the send queue."""
    global _LOADED, _LOAD_LOCK, _REPLAYING
    if _LOADED:
        return
    if _LOAD_LOCK is None:
        _LOAD_LOCK = asyncio.Lock()
    async with _LOAD_LOCK:
        if _LOADED:
            return
        now = time.time()
        start = datetime.fromtimestamp(now, TZ_LOCAL).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - GROUP_COOLDOWN - MARGIN
        rows = await asyncio.to_thread(lambda: list(iter_message_log_rows(start, now, messages_dir=MESSAGES_DIR,
            line_predicate=lambda line: any(marker in line for marker in
                (*OPENERS, *(rule[1] for rule in OPENERS.values()),
                 "主魂今日发起", "本群今日发起", "本群开局冷却", "你在草原边上")))))
        events = []
        for row in rows:
            at = float(row.get("server_event_at") or parse_message_log_ts(row.get("ts")))
            if not start <= at <= now or row.get("event_type") not in {"sent", "message", "edit"}:
                continue
            events.append(SimpleNamespace(raw_text=str(row.get("text") or ""),
                id=row.get("message_id", 0), chat_id=row.get("chat_id", 0), sender_id=row.get("sender_id", 0),
                topic_id=row.get("topic_id", 0), reply_to_msg_id=row.get("reply_to_msg_id", 0),
                forwarded=row.get("forwarded", False), date=datetime.fromtimestamp(at, TZ_LOCAL)))
        before = copy.deepcopy(_records())
        try:
            _REPLAYING = True
            for event in sorted(events, key=_at):
                observe(event)
            records = _records()
            records["openings"] = {k: v for k, v in records.get("openings", {}).items()
                if v.get("at", 0) >= start or v.get("phase") == "pending"}
            mark_dirty()
            if save_state() is False:
                raise RuntimeError("副本开局历史限额保存失败")
        except BaseException:
            _records().clear()
            _records().update(before)
            raise
        finally:
            _REPLAYING = False
        _LOADED = True


async def send(command, identity, chat, topic, op_id, sender):
    """Reserve a room before queuing; retain unknown sends until their receipt."""
    if command not in OPENERS:
        return await sender(lambda: True)
    await ensure_loaded()
    if block(identity, chat):
        # Let the existing transport classify this as definitely unsent.
        return await sender(lambda: False)
    owner = f"pending:{op_id or (identity, command, time.time_ns())}"
    started_at = time.time()
    _change(lambda records: records.setdefault("openings", {}).update({owner: {
        "command": command, "chat_id": chat, "topic_id": topic,
        "root": get_avatar_owner_id(identity) or identity, "identity_id": identity, "op_id": op_id,
        "msg_id": 0, "at": started_at, "phase": "pending"}}))
    message = None
    try:
        message = await sender(lambda: not block(identity, chat, owner=owner))
        mid = int(getattr(message, "id", 0) or 0)
        if mid:
            def sent(records):
                pending = records["openings"].pop(owner)
                pending["msg_id"] = mid
                key = f"{chat}:{mid}"
                existing = records["openings"].get(key, {})
                if existing.get("phase") not in {"accepted", "rejected"}:
                    records["openings"][key] = {**existing, **pending}
            _change(sent)
            for event in list(_EARLY):
                if getattr(event, "chat_id", 0) == chat and int(getattr(event, "reply_to_msg_id", 0) or 0) == mid:
                    observe(event)
            return message
        return None
    finally:
        if (not message and get_last_game_send_block(identity, command).get("at", 0) >= started_at
                and is_game_send_definitely_unsent(identity, command)):
            _change(lambda records: records.get("openings", {}).pop(owner, None))


def hunt_rejections(runs):
    for run in runs.values():
        if run.get("phase") not in {"opening", "held", "interrupted", "failed"} or run.get("day") != 0:
            continue
        attempts = list(run.get("command_attempts", {}).values())
        if len(attempts) != 1 or attempts[0].get("command") != ".出海猎妖":
            continue
        key = f"{run.get('chat_id')}:{attempts[0].get('msg_id')}"
        proof = (_records().get("openings", {}).get(key, {}).get("receipt") or {})
        if proof and rejection(proof["text"], proof["at"]):
            yield SimpleNamespace(raw_text=proof["text"], id=proof["message_id"], sender_id=proof["sender_id"],
                chat_id=proof["chat_id"], topic_id=proof["topic_id"], reply_to_msg_id=proof["reply_to_msg_id"],
                reply_to=SimpleNamespace(reply_to_msg_id=proof["reply_to_msg_id"], reply_to_top_id=proof["topic_id"]),
                date=datetime.fromtimestamp(proof["at"], TZ_LOCAL))
