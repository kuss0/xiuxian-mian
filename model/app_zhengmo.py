"""Bind upstream daily war logic to local identities, occupancy and send queue."""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from .activity_occupancy import get_activity_block_reason
from .app_hunt import hunt_target
from .config import MESSAGES_DIR, TZ_LOCAL, get_registered_client
from .features import zhengmo
from .persistence import mark_dirty, save_state
from .runtime import get_last_game_send_block, send_game_command, send_audit_log
from .state import (
    get_expedition_config, get_expedition_run_state, get_game_bot_ids, get_global_enabled,
    get_identity_account, get_identity_enabled, get_identity_ids, get_send_as_profile,
)

_RECOVERY_PENDING = True


def _enabled(identity):
    return (identity in get_identity_ids() and get_identity_enabled(identity)
            and get_registered_client(get_identity_account(identity)) is not None)


def _members(identities):
    members = []
    for identity in identities:
        if not _enabled(identity):
            return [], f"身份 {identity} 未登录或已暂停"
        reason = get_activity_block_reason(identity)
        if reason:
            return [], reason
        members.append({"identity_id": identity, "root_id": identity,
                        "username": str(get_send_as_profile(identity).get("username") or identity).lstrip("@")})
    return members, ""


def _save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("正魔大战状态未能持久化，停止发送")


def _send_rejection(identity, command):
    block = get_last_game_send_block(identity, command, max_age_sec=60)
    detail = str(block.get("detail") or block.get("reason") or "")
    if block.get("code") == "send_exception" and detail.startswith("You're banned from sending messages in supergroups/channels"):
        return "Telegram 拒绝该账号在群组发言，今日停止自动尝试；需解除账号或群发言限制后再执行"
    return ""


async def _send(command, *, send_as_id, target_group_role, send_guard, deadline, op_id, chain_id, **_ignored):
    target = hunt_target(target_group_role)
    return await send_game_command(
        command, send_as_id=send_as_id, target_chat_id=target[0], track=False, max_retry=0,
        source_module="正魔大战", priority="chain", op_id=op_id, chain_id=chain_id,
        queue_timeout=min(45, max(.1, deadline - time.time())),
        operation_check=lambda: (send_guard() and _enabled(send_as_id) and time.time() < deadline
                                 and hunt_target(target_group_role) == target
                                 and not get_activity_block_reason(send_as_id, include_zhengmo=False)),
    )


def _context():
    return SimpleNamespace(
        config=lambda: get_expedition_config().get("zhengmo", {}),
        runs=lambda: get_expedition_run_state().setdefault("zhengmo", {}),
        enabled=get_global_enabled, members=_members, member_enabled=_enabled,
        root=lambda i: i, target=hunt_target, save=_save, send=_send, bot_ids=get_game_bot_ids,
        send_rejection=_send_rejection,
        notice=lambda text: send_audit_log(text, scope="global", limit=1800),
    )


async def handle_zhengmo_game_event(event, **kwargs):
    return await zhengmo.game_event(_context(), event, **kwargs)


async def run_zhengmo_scheduler(now):
    global _RECOVERY_PENDING
    ctx = _context()
    if _RECOVERY_PENDING:
        await _recover_meeting_receipts(ctx, now)
        _RECOVERY_PENDING = False
    await zhengmo.scheduler(ctx, now)


async def _recover_meeting_receipts(ctx, now):
    """Replay only held registration receipts from existing local logs; never query or resend."""
    pending = [run.get("meeting", {}) for run in ctx.runs().values()
               if run.get("meeting", {}).get("phase") == "held"
               and run["meeting"].get("registration_msg_id") and run["meeting"].get("registration_started_at")]
    if not pending:
        return
    local = datetime.fromtimestamp(now, TZ_LOCAL)
    first = datetime.fromtimestamp(min(m["registration_started_at"] for m in pending), TZ_LOCAL)
    days = min(7, max(0, (local.date() - first.date()).days))
    anchors = {(m["chat_id"], m["registration_msg_id"]) for m in pending}
    for offset in range(days, -1, -1):
        path = Path(MESSAGES_DIR) / f"{(local - timedelta(days=offset)).strftime('%Y-%m-%d')}.log"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not any(str(mid) in line for _, mid in anchors):
                    continue
                try:
                    row = json.loads(line)
                    if (row.get("chat_id"), row.get("reply_to_msg_id")) not in anchors:
                        continue
                    event = SimpleNamespace(raw_text=str(row.get("text") or ""), id=row.get("message_id", 0),
                        chat_id=row.get("chat_id"), sender_id=row.get("sender_id"),
                        reply_to_msg_id=row.get("reply_to_msg_id"),
                        reply_to=SimpleNamespace(reply_to_top_id=row.get("topic_id", 0)),
                        date=datetime.fromtimestamp(float(row.get("server_event_at") or 0), TZ_LOCAL),
                        forwarded=row.get("forwarded", False))
                except (ValueError, TypeError):
                    continue
                await zhengmo.game_event(ctx, event, event_type=row.get("event_type", "message"))


def reset_zhengmo_runtime_on_startup():
    global _RECOVERY_PENDING
    _RECOVERY_PENDING = True
    for run in _context().runs().values():
        if run.get("phase") in zhengmo.MEETING_PHASES:
            run.setdefault("meeting", {}).update(phase="held", result="程序重启，大会结果待核对，不重放报名")
            run.update(phase=run.get("daily_phase", "done"), result=run.get("daily_result", "今日出征已确认"))
            mark_dirty()
        elif run.get("phase") in zhengmo.ACTIVE_PHASES:
            # The next action is a status query; never replay a march after restart.
            run.update(phase="retry", next_check=0)
            mark_dirty()
