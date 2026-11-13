"""Bind weekly adventures to the production ownership and shared send queue."""
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from .activity_occupancy import get_activity_block_reason
from .app_hunt import hunt_target
from .config import MESSAGES_DIR, TZ_LOCAL, get_registered_client
from .features import weekly_adventures as weekly
from .features.xuangu import _event_topic_id
from .persistence import mark_dirty, save_state
from .runtime import is_game_send_definitely_unsent, send_audit_log, send_game_command
from .state import (
    REALM_SORT_ORDER, get_expedition_config, get_expedition_run_state, get_game_bot_ids,
    get_global_enabled, get_identity_account, get_identity_enabled, get_identity_ids,
    get_send_as_profile,
)

_RECOVERY_PENDING = False


def _config():
    return get_expedition_config().get("weekly", {})


def _enabled(kind):
    return get_global_enabled() and _config().get("enabled", False) and kind in _config().get("kinds", [])


def _available(identity_id):
    return (identity_id in _config().get("participant_identity_ids", []) and identity_id in get_identity_ids()
            and get_identity_enabled(identity_id)
            and get_registered_client(get_identity_account(identity_id)) is not None
            and not get_activity_block_reason(identity_id, include_weekly=False))


def _members(kind):
    realm_gate = weekly.KINDS[kind]["realm"]
    minimum = REALM_SORT_ORDER.index(realm_gate) if realm_gate else 0
    members = []
    for identity_id in _config().get("participant_identity_ids", []):
        profile = get_send_as_profile(identity_id)
        realm = profile.get("realm")
        username = str(profile.get("username") or "").strip().lstrip("@")
        if ((not realm_gate or realm in REALM_SORT_ORDER and REALM_SORT_ORDER.index(realm) >= minimum)
                and username and identity_id in get_identity_ids()):
            members.append({"identity_id": identity_id, "username": username,
                            "linggen": profile.get("spiritual_root_attrs") or ""})
    return members


def _save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("周常状态保存失败，停止发送")


async def _send(command, *, send_as_id, send_guard, deadline, op_id, chain_id, reactive):
    chat, _topic = hunt_target("secondary")
    return await send_game_command(
        command, track=False, send_as_id=send_as_id, target_chat_id=chat,
        operation_check=send_guard, max_retry=0, source_module="组队周常",
        priority="urgent_reactive" if reactive else "chain",
        queue_timeout=min(45, max(0.1, deadline - time.time())), op_id=op_id, chain_id=chain_id,
    )


def _context():
    return SimpleNamespace(
        records=lambda: get_expedition_run_state().setdefault("weekly", {}),
        enabled=_enabled, available=_available, members=_members, target=lambda: hunt_target("secondary"),
        save=_save, send=_send, bot_ids=get_game_bot_ids, topic=_event_topic_id,
        unsent=is_game_send_definitely_unsent,
        event_time=lambda event: event.date.timestamp(),
        notice=lambda text: send_audit_log(text, scope="global", limit=1800),
    )


async def run_weekly_scheduler(now):
    global _RECOVERY_PENDING
    if _config().get("enabled") or get_expedition_run_state().get("weekly"):
        if _context().records().get("initialized_participants") != _config().get("participant_identity_ids", []):
            _backfill_weekly_receipts()
        if _RECOVERY_PENDING:
            await _recover_terminal_receipts(now)
            _RECOVERY_PENDING = False
        await weekly.scheduler(_context(), now)


async def handle_weekly_game_event(event, **_kwargs):
    if get_expedition_run_state().get("weekly", {}).get("runs"):
        return await weekly.game_event(_context(), event)
    return False


def reset_weekly_on_startup():
    global _RECOVERY_PENDING
    _RECOVERY_PENDING = True
    _backfill_weekly_receipts()
    for run in get_expedition_run_state().get("weekly", {}).get("runs", {}).values():
        if run.get("phase") in {"opening", "joining", "running", "dissolving"}:
            run.update(phase="interrupted", requires_reconciliation=True, last_error="程序重启，需核对原局")
            mark_dirty()


async def _recover_terminal_receipts(now):
    """One startup replay from local logs; never issue history/API probes."""
    ctx = _context()
    active = [r for r in ctx.records().get("runs", {}).values() if r.get("phase") in weekly.ACTIVE | {"waiting_room"}]
    if not active:
        return
    earliest = min(float(r["started_at"]) for r in active)
    local = datetime.fromtimestamp(now, TZ_LOCAL)
    days = min(7, max(0, (local.date() - datetime.fromtimestamp(earliest, TZ_LOCAL).date()).days))
    for offset in range(days, -1, -1):
        path = Path(MESSAGES_DIR) / f"{(local - timedelta(days=offset)).strftime('%Y-%m-%d')}.log"
        if not path.is_file():
            continue
        with path.open() as stream:
            for line in stream:
                if not any(marker in line for marker in ("战果", "收兽而归", "败退而归", "走出阴冥", "困于阴冥", "散议", "密议散了", "这里已经有一场密议了")):
                    continue
                try:
                    row = json.loads(line)
                    at = float(row.get("server_event_at") or 0)
                except (ValueError, TypeError):
                    continue
                if at < earliest - 1:
                    continue
                event = SimpleNamespace(raw_text=str(row.get("text") or ""), date=datetime.fromtimestamp(at, TZ_LOCAL),
                    chat_id=row.get("chat_id"), sender_id=row.get("sender_id"), id=row.get("message_id", 0),
                    topic_id=row.get("topic_id", 0), reply_to_msg_id=row.get("reply_to_msg_id", 0),
                    reply_to=SimpleNamespace(reply_to_top_id=row.get("topic_id", 0)), forwarded=row.get("forwarded", False))
                await weekly.game_event(ctx, event)


def _backfill_weekly_receipts():
    """Seed quotas from real reward rows in this week's existing local log."""
    now = time.time()
    local = datetime.fromtimestamp(now, TZ_LOCAL)
    records = _context().records()
    evidence = records.setdefault("receipt_keys", [])
    seen = set(evidence)
    identities = get_expedition_config().get("weekly", {}).get("participant_identity_ids", [])
    members = {str(get_send_as_profile(i).get("username") or "").lower().lstrip("@"): i for i in identities}
    chat, topic = hunt_target("secondary")
    for offset in range(local.weekday(), -1, -1):
        path = Path(MESSAGES_DIR) / f"{(local - timedelta(days=offset)).strftime('%Y-%m-%d')}.log"
        if not path.is_file():
            continue
        with path.open() as stream:
            for line in stream:
                if not any(marker in line for marker in ("战果", "收兽而归", "败退而归", "走出阴冥", "困于阴冥")):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                at = float(row.get("server_event_at") or 0)
                receipt = f"{row.get('chat_id')}:{row.get('message_id')}"
                if (receipt in seen or row.get("sender_id") not in get_game_bot_ids() or row.get("forwarded")
                        or row.get("chat_id") != chat or row.get("topic_id", 0) != topic
                        or weekly.week_key(at) != weekly.week_key(now)):
                    continue
                text = str(row.get("text") or "")
                kind = next((kind for kind in weekly.KINDS if weekly.terminal(kind, text)), "")
                if not kind:
                    continue
                quotas = records.setdefault("quotas", {}).setdefault(kind, {})
                found = False
                for reward in text.splitlines():
                    match = weekly._NAMES.match(reward)
                    identity = members.get(match[1].lower()) if match else None
                    if identity is None or not ("修为+" in reward or "本周有奖次数已满" in reward):
                        continue
                    key = str(identity)
                    previous = quotas.get(key, {})
                    used = previous.get("used", 0) if previous.get("week") == weekly.week_key(at) else 0
                    quotas[key] = {"week": weekly.week_key(at), "used": 2 if "本周有奖次数已满" in reward else min(2, used + 1),
                                   "source_msg_id": row["message_id"], "at": at}
                    found = True
                if found:
                    evidence.append(receipt)
                    seen.add(receipt)
    records["receipt_keys"] = evidence[-200:]
    records["initialized_participants"] = list(identities)
    mark_dirty()
