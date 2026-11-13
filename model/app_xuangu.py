"""Xuangu adapters; existing replica reducers keep sole ownership of their rooms."""
import asyncio
import time

from .app_hunt import hunt_target
from .activity_occupancy import get_activity_block_reason
from .app_message_log import (
    _get_replica_event_listener_account_id, _is_replica_listener_self_event,
    _send_replica_group_message, _get_group_event_listener_account_id,
)
from .features import xuangu
from .features.expedition_store import FubenFlowStore
from .features.xuangu_context import XuanguContext, XuanguCommands, XuanguIdentities, XuanguMessages
from .persistence import mark_dirty, save_state
from .runtime import is_game_send_definitely_unsent, send_audit_log, send_game_command, track_background_task
from .state import (
    get_expedition_config, get_expedition_run_state, get_game_bot_ids,
    get_global_enabled, get_identity_enabled, get_identity_ids, get_replica_group_ids,
    get_replica_listener_account_map, resolve_identity_selector, set_expedition_run_state,
    get_send_as_profile, get_identity_account, REALM_SORT_ORDER,
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
    if outcome:
        roster = xuangu._match_roster_for_outcome(ctx, outcome, time.time(), event=event)
        if roster is not None:
            roster["terminal_pending"] = True
    async with _LOCK:
        return await xuangu.handle_xuangu_game_event(ctx, event)


async def run_xuangu_scheduler(now):
    if _enabled():
        async with _LOCK:
            await xuangu.run_xuangu_auto_scheduler(_context(), now)


def reset_xuangu_runtime_on_startup():
    changed = False
    for roster in _context().flows.load().values():
        if isinstance(roster, dict) and roster.get("phase") in {"waiting_open", "joining", "running", "finishing"}:
            roster.update(phase="interrupted", requires_reconciliation=True,
                          last_error="程序已重启，请核对游戏房间后重新集结")
            changed = True
    if changed:
        mark_dirty()
