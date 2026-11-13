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


def _team_config(name=""):
    config = _config()
    if not name:
        return config if not config.get("teams") else {**config, "enabled": False}
    team = next((t for t in config.get("teams", []) if t["name"] == name), {})
    return {**team, "enabled": bool(config.get("enabled") and team.get("enabled", True))}


def _enabled(kind, name=""):
    config = _team_config(name)
    return get_global_enabled() and config.get("enabled", False) and kind in config.get("kinds", [])


def _available(identity_id, name=""):
    return (identity_id in _team_config(name).get("participant_identity_ids", []) and identity_id in get_identity_ids()
            and get_identity_enabled(identity_id)
            and get_registered_client(get_identity_account(identity_id)) is not None
            and not get_activity_block_reason(identity_id, include_weekly=False))


def _members(kind, name=""):
    config = _team_config(name)
    realm_gate = weekly.KINDS[kind]["realm"]
    minimum = REALM_SORT_ORDER.index(realm_gate) if realm_gate else 0
    members = []
    for identity_id in config.get("participant_identity_ids", []):
        profile = get_send_as_profile(identity_id)
        realm = profile.get("realm")
        telegram_username = str(profile.get("username") or "").strip().lstrip("@")
        username = str(config.get("member_names", {}).get(str(identity_id)) or telegram_username).strip().lstrip("@")
        if ((not realm_gate or realm in REALM_SORT_ORDER and REALM_SORT_ORDER.index(realm) >= minimum)
                and username and identity_id in get_identity_ids()):
            members.append({"identity_id": identity_id, "username": username, "telegram_username": telegram_username,
                            "linggen": profile.get("spiritual_root_attrs") or ""})
    return members


def _save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("周常状态保存失败，停止发送")


async def _send(command, *, send_as_id, send_guard, deadline, op_id, chain_id, reactive, team_name=""):
    chat, _topic = hunt_target(_team_config(team_name).get("group_role", "secondary"))
    return await send_game_command(
        command, track=False, send_as_id=send_as_id, target_chat_id=chat,
        operation_check=send_guard, max_retry=0, source_module="组队周常",
        priority="urgent_reactive" if reactive else "chain",
        queue_timeout=min(45, max(0.1, deadline - time.time())), op_id=op_id, chain_id=chain_id,
    )


def _context(name=""):
    config = _team_config(name)

    async def send(command, **kwargs):
        return await _send(command, **kwargs, team_name=name)

    return SimpleNamespace(
        records=lambda: get_expedition_run_state().setdefault("weekly", {}),
        enabled=lambda kind: _enabled(kind, name), available=lambda i: _available(i, name),
        members=lambda kind: _members(kind, name), target=lambda: hunt_target(_team_config(name).get("group_role", "secondary")),
        accept_run=lambda run: run.get("team", "") == name, team_name=name,
        run_key=lambda kind: f"{name}:{kind}" if name else kind,
        route=config.get("route"), use_talisman=config.get("use_talisman", True),
        save=_save, send=send, bot_ids=get_game_bot_ids, topic=_event_topic_id,
        unsent=is_game_send_definitely_unsent,
        event_time=lambda event: event.date.timestamp(),
        notice=lambda text: send_audit_log(text, scope="global", limit=1800),
    )


async def run_weekly_scheduler(now):
    global _RECOVERY_PENDING
    if _config().get("enabled") or get_expedition_run_state().get("weekly"):
        if _context().records().get("initialized_config") != _config():
            _backfill_weekly_receipts()
        if _RECOVERY_PENDING:
            await _recover_terminal_receipts(now)
            _RECOVERY_PENDING = False
        names = [""] + [t["name"] for t in _config().get("teams", [])]
        names += [r.get("team", "") for r in _context().records().get("runs", {}).values()]
        for name in dict.fromkeys(names):
            await weekly.scheduler(_context(name), now)


async def handle_weekly_game_event(event, **_kwargs):
    if get_expedition_run_state().get("weekly", {}).get("runs"):
        names = {r.get("team", "") for r in _context().records()["runs"].values()}
        for name in names:
            if await weekly.game_event(_context(name), event):
                return True
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
                for name in {r.get("team", "") for r in active}:
                    await weekly.game_event(_context(name) if name else ctx, event)


def _backfill_weekly_receipts():
    """Seed quotas from real reward rows in this week's existing local log."""
    now = time.time()
    local = datetime.fromtimestamp(now, TZ_LOCAL)
    records = _context().records()
    evidence = records.setdefault("receipt_keys", [])
    seen = set(evidence)
    receipt_members = records.setdefault("receipt_members", {})
    previous_members = records.get("initialized_participants", [])
    scopes = _config().get("teams") or [_config()]
    identities = list(dict.fromkeys(i for scope in scopes for i in scope.get("participant_identity_ids", [])))
    names_by_target = {}
    for scope in scopes:
        members = names_by_target.setdefault(hunt_target(scope.get("group_role", "secondary")), {})
        for identity in scope.get("participant_identity_ids", []):
            name = str(scope.get("member_names", {}).get(str(identity)) or get_send_as_profile(identity).get("username") or "").lower().lstrip("@")
            if name:
                members[name] = identity
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
                if (row.get("sender_id") not in get_game_bot_ids() or row.get("forwarded")
                        or (row.get("chat_id"), row.get("topic_id", 0)) not in names_by_target
                        or weekly.week_key(at) != weekly.week_key(now)):
                    continue
                text = str(row.get("text") or "")
                kind = next((kind for kind in weekly.KINDS if weekly.terminal(kind, text)), "")
                if not kind:
                    continue
                quotas = records.setdefault("quotas", {}).setdefault(kind, {})
                members = names_by_target[(row.get("chat_id"), row.get("topic_id", 0))]
                counted = set(receipt_members.get(receipt, previous_members if receipt in seen else []))
                found = False
                for reward in text.splitlines():
                    match = weekly._NAMES.match(reward)
                    identity = members.get(match[1].lower()) if match else None
                    if identity is None or identity in counted or not ("修为+" in reward or "本周有奖次数已满" in reward):
                        continue
                    key = str(identity)
                    previous = quotas.get(key, {})
                    used = previous.get("used", 0) if previous.get("week") == weekly.week_key(at) else 0
                    quotas[key] = {"week": weekly.week_key(at), "used": 2 if "本周有奖次数已满" in reward else min(2, used + 1),
                                   "source_msg_id": row["message_id"], "at": at}
                    found = True
                    counted.add(identity)
                if found:
                    if receipt not in seen:
                        evidence.append(receipt)
                    seen.add(receipt)
                    receipt_members[receipt] = sorted(counted)
    records["receipt_keys"] = evidence[-200:]
    records["receipt_members"] = {key: ids for key, ids in receipt_members.items() if key in records["receipt_keys"]}
    records["initialized_participants"] = list(identities)
    records["initialized_config"] = _config()
    mark_dirty()
