"""Bind upstream hunting to the local guarded send and persistent state APIs."""
import time
from types import SimpleNamespace

from .activity_occupancy import get_activity_block_reason
from .config import get_registered_client
from .features import hunt, hunt_weekly
from .persistence import mark_dirty, save_state
from .runtime import is_game_send_definitely_unsent, send_audit_log, send_game_command
from .state import (
    get_game_bot_ids, get_game_group_id, get_game_group_ids, get_game_group_topic_id,
    get_global_enabled, get_hunt_run_state, get_hunt_teams, get_identity_account,
    get_identity_enabled, get_identity_ids, get_send_as_profile,
    get_expedition_run_state,
)


def hunt_target(role):
    groups = get_game_group_ids()
    primary = get_game_group_id()
    group_id = primary if role == "primary" else next((group for group in groups if group != primary), 0)
    return int(group_id or 0), int(get_game_group_topic_id(group_id) or 0) if group_id else 0


def _members(identity_ids):
    members, names = [], set()
    for identity_id in identity_ids:
        if identity_id not in get_identity_ids():
            return [], f"未知本机身份 {identity_id}"
        account_id = get_identity_account(identity_id)
        if account_id <= 0 or get_registered_client(account_id) is None:
            return [], f"身份 {identity_id} 的所属账号未登录"
        if not get_identity_enabled(identity_id):
            return [], f"身份 {identity_id} 已暂停"
        reason = get_activity_block_reason(identity_id)
        if reason:
            return [], f"身份 {identity_id} 当前不可出海：{reason}"
        profile = get_send_as_profile(identity_id)
        username = str(profile.get("username") or profile.get("daohao") or profile.get("label") or identity_id).strip().lstrip("@")
        if username.lower() in names:
            return [], "同名成员不能重复登船"
        names.add(username.lower())
        members.append({"identity_id": identity_id, "root_id": identity_id, "username": username})
    return members, ""


async def _send_hunt_command(command, *, send_as_id, target_group_role, send_guard, deadline=0, op_id=None, chain_id=None, **_ignored):
    chat_id, _topic_id = hunt_target(target_group_role)
    if not chat_id or not send_guard() or (deadline and time.time() >= deadline):
        return None
    critical = command in {".探路", ".出手", ".护船", ".祭符"} and deadline > 0
    queue_timeout = min(45.0, max(0.1, deadline - time.time())) if deadline else 45.0
    return await send_game_command(
        command, track=False, send_as_id=send_as_id, target_chat_id=chat_id,
        operation_check=lambda: send_guard() and (not deadline or time.time() < deadline),
        priority="urgent_reactive" if critical else "chain", max_retry=0,
        source_module="出海猎妖", queue_timeout=queue_timeout, op_id=op_id, chain_id=chain_id,
    )


def _context():
    def save():
        mark_dirty()
        if save_state() is False:
            raise RuntimeError("猎妖状态未能持久化，已停止发送")

    return SimpleNamespace(
        teams=get_hunt_teams, runs=get_hunt_run_state, save=save,
        target=hunt_target, members=_members, enabled=get_global_enabled,
        bot_ids=get_game_bot_ids, send=_send_hunt_command,
        definitely_unsent=is_game_send_definitely_unsent,
        weekly=lambda: get_expedition_run_state().setdefault("hunt_weekly", {}),
        notice=lambda text: send_audit_log(text, scope="global", limit=1800),
    )


async def handle_hunt_log_command(event):
    parts = str(getattr(event, "raw_text", "") or "").split()
    if len(parts) == 3 and parts[:2] == [".猎妖", "周常重查"]:
        import time
        if getattr(event, "date", None) is None or not 0 <= time.time() - hunt._event_time(event) <= 60:
            return True, "重查指令已过期，请重新发送"
        return True, (await hunt_weekly.reset_check(_context(), parts[2]))[1]
    return await hunt.control(_context(), event)


async def handle_hunt_game_event(event, *, event_type="message"):
    if any(team.get("auto_weekly") for team in get_hunt_teams()) and await hunt_weekly.game_event(_context(), event):
        return True
    if not get_hunt_run_state():
        return False
    return await hunt.game_event(_context(), event, event_type=event_type)


async def run_hunt_scheduler(now):
    if get_hunt_run_state():
        await hunt.scheduler(_context(), now)
    if any(team.get("auto_weekly") for team in get_hunt_teams()):
        await hunt_weekly.scheduler(_context(), now)


def reset_hunt_runtime_on_startup():
    changed = False
    for run in get_hunt_run_state().values():
        if isinstance(run, dict) and run.get("phase") in hunt.ACTIVE_PHASES:
            run.update(phase="interrupted", requires_reconciliation=True,
                       last_error="程序已重启，请确认游戏中的船况后重新出发")
            changed = True
    if changed:
        mark_dirty()
