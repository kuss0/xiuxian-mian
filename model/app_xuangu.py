"""Xuangu adapters; existing replica reducers keep sole ownership of their rooms."""
import asyncio
import re
import time
from datetime import datetime

from .message_log_recovery import iter_message_log_rows
from .app_hunt import hunt_target
from .activity_occupancy import get_activity_block_reason
from .app_message_log import (
    _get_replica_event_listener_account_id, _is_replica_listener_self_event,
    _send_replica_group_message, _get_group_event_listener_account_id,
)
from .features import xuangu
from .features.hunt_weekly import week_key
from .config import MESSAGES_DIR, TZ_LOCAL
from .features.expedition_store import FubenFlowStore
from .features.xuangu_context import XuanguContext, XuanguCommands, XuanguIdentities, XuanguMessages
from .persistence import mark_dirty, save_state
from .runtime import is_game_send_definitely_unsent, send_audit_log, send_game_command, track_background_task
from .state import (
    get_expedition_config, get_expedition_run_state, get_game_bot_ids,
    get_global_enabled, get_identity_enabled, get_identity_ids, get_replica_group_ids,
    get_replica_listener_account_map, resolve_identity_selector, set_expedition_run_state,
    get_send_as_profile, get_identity_account, REALM_SORT_ORDER,
    get_storage_bag_records, get_miniapp_auto_config,
)

_TASKS = set()
_LOCK = asyncio.Lock()


def _listener_account_id(event):
    # A configured assembly group may also be the admin log group. The general
    # replica listener excludes that group; Xuangu still validates its mapped client.
    return (_get_replica_event_listener_account_id(event)
            or _get_group_event_listener_account_id(event, get_replica_group_ids(), get_replica_listener_account_map()))


def _enabled(identity_id=None, _kind=None):
    config = get_expedition_config().get("xuangu", {})
    if not config.get("enabled") or not get_global_enabled():
        return False
    return identity_id is None or (identity_id in get_identity_ids() and get_identity_enabled(identity_id)
                                  and identity_id in config.get("participant_identity_ids", []))


def _save(records):
    set_expedition_run_state(records)
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("玄骨状态未能持久化，已停止发送")


def _preflight_error(identity_id):
    from .config import get_registered_client
    realm = str(get_send_as_profile(identity_id).get("realm") or "").strip()
    if realm not in REALM_SORT_ORDER or REALM_SORT_ORDER.index(realm) < REALM_SORT_ORDER.index("结丹初期"):
        return "玄骨要求结丹初期以上，请先核对境界"
    if get_registered_client(get_identity_account(identity_id)) is None:
        return "所属账号未登录"
    return get_activity_block_reason(identity_id, include_xuangu=False)


async def _audit(text, **_kwargs):
    return await send_audit_log(text, scope="global", limit=1000)


async def _send(command, *, send_as_id, send_guard, deadline=0, op_id=None, chain_id=None, **_kwargs):
    chat_id, topic_id = hunt_target("secondary")
    if not chat_id or (deadline and time.time() >= deadline):
        return None
    critical = command.startswith((".玄骨行动 ", ".玄骨收尾 "))
    queue_timeout = min(45.0, max(0.1, deadline - time.time())) if deadline else 45.0
    return await send_game_command(
        command, send_as_id=send_as_id, track=False, target_chat_id=chat_id,
        operation_check=lambda: send_guard() and (not deadline or time.time() < deadline) and _enabled(send_as_id)
        and not get_activity_block_reason(send_as_id, include_xuangu=False)
        and hunt_target("secondary") == (chat_id, topic_id),
        source_module="玄骨遗府", priority="urgent_reactive" if critical else "chain",
        max_retry=0, queue_timeout=queue_timeout, op_id=op_id, chain_id=chain_id,
    )


async def _notice(chat_id, text):
    from .config import get_registered_client
    listener = int(get_replica_listener_account_map().get(str(chat_id)) or 0)
    client = get_registered_client(listener)
    if client is None:
        return False
    return await _send_replica_group_message(client, chat_id, text, listener_account_id=listener, log_text=text)


def _spawn(awaitable):
    async def serialized():
        async with _LOCK:
            return await awaitable

    task = track_background_task(asyncio.create_task(serialized()))
    _TASKS.add(task)

    def done(completed):
        _TASKS.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            print(f"Xuangu background task failed: {type(completed.exception()).__name__}")

    task.add_done_callback(done)
    return task


def _context():
    store = FubenFlowStore(get_expedition_run_state, _save)
    return XuanguContext(
        flows=store.collection("xuangu_auto", "rosters"),
        messages=XuanguMessages(
            game_command=_send, audit=_audit, group_message=_send_replica_group_message,
            notice_by_chat=_notice, listener_account_id=_listener_account_id,
            is_listener_self=_is_replica_listener_self_event, group_ids=get_replica_group_ids,
            definitely_unsent=is_game_send_definitely_unsent,
        ),
        identities=XuanguIdentities(resolve_identity_selector, _enabled, _preflight_error),
        commands=XuanguCommands(
            open=".开启玄骨遗府", join=".加入玄骨", enter=".进入玄骨", act=".玄骨行动",
            finish=".玄骨收尾", disband=".解散玄骨", role=".玄骨分工",
        ),
        sleep=asyncio.sleep, spawn=_spawn,
        finish_choice=get_expedition_config().get("xuangu", {}).get("finish_choice", "收拢遗宝"),
        target=lambda: hunt_target("secondary"),
    )


async def handle_xuangu_group_command(event):
    if not _enabled():
        return False
    text = str(getattr(event, "raw_text", "") or "").strip()
    if not text.startswith((".开启玄骨 ", ".解散玄骨", ".确认玄骨结束 ")):
        return False
    date = getattr(event, "date", None)
    if date is not None and time.time() - date.timestamp() > 60:
        return False
    ctx = _context()
    async with _LOCK:
        return (await xuangu.handle_xuangu_roster_command(ctx, event)
                or await xuangu.handle_xuangu_disband_command(ctx, event)
                or await xuangu.handle_xuangu_confirmation(ctx, event))


async def handle_xuangu_game_event(event, *, event_type="message"):
    if not _enabled() or getattr(event, "sender_id", 0) not in get_game_bot_ids():
        return False
    chat_id, _topic_id = hunt_target("secondary")
    if not chat_id or getattr(event, "chat_id", 0) != chat_id:
        return False
    _chat_id, topic_id = hunt_target("secondary")
    ctx = _context()
    actual_topic = xuangu._event_topic_id(event)
    if actual_topic != topic_id:
        if actual_topic:
            return False
        reply_id = int(getattr(event, "reply_to_msg_id", 0) or
                       getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
        owned = [record for record in ctx.flows.active(time.time())
                 if int(record.get("game_chat_id") or 0) == chat_id
                 and int(record.get("game_topic_id") or 0) == topic_id
                 and reply_id > 0 and any(int(attempt.get("msg_id") or 0) == reply_id
                                         for attempt in record.get("command_attempts", {}).values())]
        if len(owned) != 1:
            return False
    # Invalidate old sends as soon as authoritative evidence arrives, even
    # while the previous dispatch owns the reducer lock.
    text = str(getattr(event, "raw_text", "") or "")
    panel = xuangu.parse_xuangu_round(text)
    if panel:
        room = xuangu._find_room_for_event(panel, event)
        roster = xuangu._match_roster_for_round(ctx, room, panel, time.time(), event=event)
        if roster is not None:
            roster["observed_round"] = max(int(roster.get("observed_round") or 0), panel["round"])
    outcome = xuangu.parse_xuangu_outcome(text)
    outcome_roster = None
    if outcome:
        roster = xuangu._match_roster_for_outcome(ctx, outcome, time.time(), event=event)
        if roster is not None:
            roster["terminal_pending"] = True
            outcome_roster = roster
    async with _LOCK:
        handled = await xuangu.handle_xuangu_game_event(ctx, event)
        if outcome_roster is not None and outcome_roster.get("phase") == "settled":
            _record_daily_outcome(outcome_roster, event, outcome, float(outcome_roster["updated_at"]))
            _save(get_expedition_run_state())
        return handled


def _day(now):
    return datetime.fromtimestamp(now, TZ_LOCAL).strftime("%Y-%m-%d")


def _record_daily_outcome(roster, event, outcome, now):
    text = str(getattr(event, "raw_text", "") or "")
    daily = get_expedition_run_state().setdefault("xuangu_daily", {})
    rewards = daily.setdefault("rewards", {})
    found = False
    for member in roster.get("my_members", []):
        line = next((line for line in text.splitlines() if re.match(
            "@" + re.escape(member["username"]) + r"[：:]", line, re.I)), "")
        if "修为+" in line or "今日" in line and "已" in line:
            rewards[str(member["identity_id"])] = {"day": _day(now), "at": now,
                "source_msg_id": int(getattr(event, "id", 0) or 0), "room_id": roster.get("room_id"), "outcome": outcome}
            found = True
    if found:
        daily.setdefault("quota_days", {})[_day(now)] = {"week": week_key(now), "source_msg_id": int(getattr(event, "id", 0) or 0),
                                                       "reason": "rewarded"}


def _weekly_used(daily, now):
    return sum(value.get("week") == week_key(now) for value in daily.get("quota_days", {}).values())


async def _run_daily(now):
    """Reserve a daily slot and one of three weekly slots before spending a ticket."""
    config = get_expedition_config().get("xuangu", {})
    if not config.get("auto_daily"):
        return
    daily = get_expedition_run_state().setdefault("xuangu_daily", {})
    identities = config.get("participant_identity_ids", [])
    if (len(identities) != 5 or daily.get("attempt_day") == _day(now)
            or _weekly_used(daily, now) >= int(config.get("weekly_limit", 3))
            or float(daily.get("next_check") or 0) > now):
        return
    if any(daily.get("rewards", {}).get(str(i), {}).get("day") == _day(now) for i in identities):
        return
    if any(r.get("requires_reconciliation") or r.get("phase") in {"waiting_open", "joining", "running", "finishing"}
           for r in get_expedition_run_state().get("xuangu_auto", {}).get("rosters", {}).values()):
        return
    if any(_preflight_error(i) or not _enabled(i) for i in identities):
        return
    groups = get_replica_group_ids()
    if not groups:
        return
    daily.update(next_check=now + 3600, last_error="")
    _save(get_expedition_run_state())
    from .features import cave_treasure_runtime as cave
    entry = get_miniapp_auto_config()
    urls = entry.get("cave_public_entry_urls") or [entry.get("cave_public_entry_url")]
    url = next((u for u in urls if isinstance(u, str) and u.strip()), "")
    if not url:
        daily["last_error"] = "洞府公共入口未配置，未查询门票"
        _save(get_expedition_run_state())
        return
    for identity in identities:
        result = await cave.run_cave_public_inventory(identity, url)
        bag = get_storage_bag_records().get(str(identity), {})
        count = bag.get("items", {}).get("古府寻踪珠")
        fresh = bag.get("source") == "cave_inventory_miniapp" and 0 <= time.time() - float(bag.get("updated_at") or 0) <= 120
        if not result.get("ok") or not fresh or type(count) is not int or count < 1:
            continue
        if get_expedition_config().get("xuangu", {}) != config or any(_preflight_error(i) or not _enabled(i) for i in identities):
            return
        ordered = [identity] + [i for i in identities if i != identity]
        names = [str(get_send_as_profile(i).get("username") or "").strip().lstrip("@") for i in ordered]
        if any(not re.fullmatch(r"[A-Za-z0-9_]{3,32}", name) for name in names):
            return
        daily.update(attempt_day=_day(now), ticket_owner=identity, ticket_count=count, ticket_checked_at=time.time())
        daily.setdefault("quota_days", {})[_day(now)] = {"week": week_key(now), "reason": "reserved"}
        _save(get_expedition_run_state())
        await xuangu.start_xuangu_roster(_context(), names, groups[0], auto_daily=True)
        return
    daily["last_error"] = "未从新鲜完整背包确认古府寻踪珠，跳过；一小时后再检查"
    _save(get_expedition_run_state())


async def run_xuangu_scheduler(now):
    if _enabled():
        async with _LOCK:
            await xuangu.run_xuangu_auto_scheduler(_context(), now)
            await _run_daily(now)


def reset_xuangu_runtime_on_startup():
    _backfill_week_rewards()
    changed = False
    for roster in _context().flows.load().values():
        if isinstance(roster, dict) and roster.get("phase") in {"waiting_open", "joining", "running", "finishing"}:
            roster.update(phase="interrupted", requires_reconciliation=True,
                          last_error="程序已重启，请核对游戏房间后重新集结")
            changed = True
    if changed:
        mark_dirty()


def _backfill_week_rewards():
    """Keep this week's real reward evidence after short-lived room cleanup."""
    from types import SimpleNamespace
    now = time.time()
    local = datetime.fromtimestamp(now, TZ_LOCAL)
    chat, topic = hunt_target("secondary")
    members = [{"identity_id": i, "username": str(get_send_as_profile(i).get("username") or "").lstrip("@")}
               for i in get_expedition_config().get("xuangu", {}).get("participant_identity_ids", [])]
    for row in iter_message_log_rows(now - local.weekday() * 86400, now, messages_dir=MESSAGES_DIR,
                                     line_predicate=lambda line: "玄骨遗府·" in line and "修为+" in line):
        text = str(row.get("text") or "")
        try:
            at = float(row.get("server_event_at") or 0)
            week_key(at)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        outcome = xuangu.parse_xuangu_outcome(text)
        if (outcome and outcome != "残图异动" and row.get("sender_id") in get_game_bot_ids()
                and row.get("chat_id") == chat and row.get("topic_id", 0) == topic
                and not row.get("forwarded") and week_key(at) == week_key(now)):
            _record_daily_outcome({"my_members": members}, SimpleNamespace(raw_text=text, id=row.get("message_id")), outcome, at)
    mark_dirty()
