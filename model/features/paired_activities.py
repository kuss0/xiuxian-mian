"""Persistent independent warm pairs and account-to-account global duels."""
import asyncio
import copy
import json
import random
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from ..activity_occupancy import get_activity_block_reason
from ..app_hunt import hunt_target
from ..config import MESSAGES_DIR, TZ_LOCAL, get_registered_client
from ..persistence import mark_dirty, save_state
from ..runtime import is_game_send_definitely_unsent, send_duel_private_magic, send_game_command
from ..state import get_expedition_config, get_expedition_run_state, get_game_bot_ids, get_global_enabled, get_identity_account, get_identity_enabled, get_identity_state, get_send_as_profile
from ..timing import parse_wait_time
from .xuangu import _event_topic_id

_LOCK = asyncio.Lock()
ACTIVE = {"sending", "lead_sent", "waiting_reply", "waiting_start", "waiting_magic", "magic_sending", "held"}


def records(kind):
    return get_expedition_run_state().setdefault(kind, {})


def save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("配对活动状态保存失败")


def available(identity, owner=None):
    if any(run is not owner and run.get("phase") in ACTIVE
           and identity in {run.get("source_identity_id"), run.get("target_identity_id")}
           for key in ("warm_pairs", "global_duel") for run in records(key).values()):
        return False
    return (get_identity_enabled(identity) and get_registered_client(get_identity_account(identity)) is not None
            and not get_activity_block_reason(identity, include_paired=False))


def _owned(run, event):
    reply = int(getattr(event, "reply_to_msg_id", 0) or getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    return (getattr(event, "sender_id", 0) in get_game_bot_ids() and not getattr(event, "forwarded", False)
            and not getattr(event, "fwd_from", None) and event.chat_id == run.get("chat_id")
            and int(getattr(event, "topic_id", 0) or _event_topic_id(event)) == run.get("topic_id", 0)
            and (reply == run.get("command_id") and reply > 0 or event.id == run.get("reply_id") and event.id > 0)
            and event.date.timestamp() >= run.get("started_at", 0) - 1)


def reduce_reply(kind, run, event, now):
    if not _owned(run, event):
        return False
    text = str(getattr(event, "raw_text", "") or "").strip()
    run["reply_id"] = event.id
    if kind == "warm_pairs":
        if "【温养双修" in text or "双修" in text and "修为增加" in text:
            run.update(phase="idle", next_at=now + random.uniform(3660, 3720), result=text[:1800], last_error="")
        elif any(p in text for p in ("契印感应", "准备进行温养双修")):
            run.update(phase="waiting_reply", progress_seen=True, deadline=max(run.get("deadline", 0), now + 120))
        elif any(p in text for p in ("冷却", "尚未恢复", "调息")) and parse_wait_time(text) > 0:
            run.update(phase="idle", next_at=now + parse_wait_time(text) + random.uniform(60, 120), result=text[:500])
        else:
            run.update(phase="held", last_error=text[:500] or "未知温养回执")
    elif text.startswith("正在锁定对手天机"):
        run.update(phase="waiting_start", deadline=now + 90)
    elif text.startswith("【决斗开启·第一回合】"):
        if run.get("phase") in {"waiting_start", "held"} and not run.get("magic"):
            run.update(phase="waiting_magic", next_at=now + random.uniform(5, 10), last_error="")
    else:
        run.update(phase="held", last_error=text[:500] or "未知对决回执")
    save()
    return True


def recover(kind, run, now):
    """Replay only existing local replies/edits; never fetch history or resend."""
    if not run.get("command_id"):
        return
    for day in dict.fromkeys(datetime.fromtimestamp(at, TZ_LOCAL).strftime("%Y-%m-%d") for at in (run.get("started_at", now), now)):
        path = Path(MESSAGES_DIR) / (day + ".log")
        if not path.is_file():
            continue
        with path.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("chat_id") != run.get("chat_id") or row.get("reply_to_msg_id") != run["command_id"]:
                    continue
                event = SimpleNamespace(raw_text=row.get("text", ""), chat_id=row["chat_id"], id=row.get("message_id", 0),
                    sender_id=row.get("sender_id"), reply_to_msg_id=row.get("reply_to_msg_id", 0), topic_id=row.get("topic_id", 0),
                    forwarded=row.get("forwarded", False), date=datetime.fromtimestamp(float(row.get("server_event_at") or 0), TZ_LOCAL))
                reduce_reply(kind, run, event, event.date.timestamp())


async def _send(config_key, config, run, command, identity, *, reply_to=None):
    target = hunt_target(run["group_role"])
    run.update(phase="sending", last_command=command)
    save()

    def allowed():
        return (get_global_enabled() and get_expedition_config().get(config_key, {}) == config
                and available(identity, run) and hunt_target(run["group_role"]) == target and run.get("phase") == "sending")

    try:
        message = await send_game_command(command, track=False, send_as_id=identity, reply_to=reply_to,
            target_chat_id=target[0], max_retry=0, queue_timeout=45, source_module="独立温养" if config_key == "warm_pairs" else "全局对决",
            operation_check=allowed, op_id=f"{config_key}:{run['id']}:{identity}:{command}")
    except (Exception, asyncio.CancelledError) as exc:
        run.update(phase="held", last_error=type(exc).__name__)
        save()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return False
    if not getattr(message, "id", 0):
        run.update(phase="idle" if is_game_send_definitely_unsent(identity, command) and not reply_to else "held",
                   next_at=time.time() + 300, last_error="未获发送确认")
        save()
        return False
    run.update(chat_id=target[0], topic_id=target[1], last_sent_id=message.id)
    save()
    return True


async def scheduler(now):
    if not get_global_enabled():
        return
    async with _LOCK:
        for kind in ("warm_pairs", "global_duel"):
            config = copy.deepcopy(get_expedition_config().get(kind, {}))
            if not config.get("enabled"):
                continue
            entries = config.get("pairs", []) if kind == "warm_pairs" else [config]
            for entry in entries:
                key = entry.get("name", "default")
                run = records(kind).setdefault(key, {})
                phase = run.get("phase", "idle")
                if phase in {"waiting_reply", "waiting_start", "held"}:
                    if (phase != "held" and now >= run.get("deadline", now + 1) or not run.get("last_recovery_at") and phase == "held"):
                        run["last_recovery_at"] = now
                        recover(kind, run, now)
                    phase = run.get("phase")
                    if phase != "held" and now > run.get("deadline", now + 1) and phase in {"waiting_reply", "waiting_start"}:
                        run.update(phase="held", last_error="缺少最终回执，不重复发送")
                        save()
                    continue
                if phase in {"held", "sending", "magic_sending", "done"} or float(run.get("next_at") or 0) > now:
                    continue
                a, b = int(entry["source_identity_id"]), int(entry["target_identity_id"])
                if not available(a, run) or not available(b, run):
                    continue
                if kind == "warm_pairs" and get_identity_state(b).get("hehuan_enabled"):
                    continue  # Independent and sect-managed warm automation must have one owner.
                if phase == "lead_sent":
                    if now - run["started_at"] > 600:
                        run.update(phase="held", last_error="温养引子已过期")
                    elif await _send(kind, config, run, ".双修 温养", b, reply_to=run["lead_id"]):
                        run.update(command_id=run["last_sent_id"], phase="waiting_reply", deadline=time.time() + 120)
                    save()
                    continue
                if kind == "global_duel" and phase == "waiting_magic":
                    run.update(phase="magic_sending", magic=run.get("magic", {}))
                    save()
                    for identity in (a, b):
                        if str(identity) in run["magic"]:
                            continue
                        run["magic"][str(identity)] = {"status": "unknown"}
                        save()
                        try:
                            message = await send_duel_private_magic(identity, operation_check=lambda: (
                                get_global_enabled() and get_expedition_config().get(kind, {}) == config
                                and run.get("phase") == "magic_sending" and available(identity, run)))
                        except (Exception, asyncio.CancelledError) as exc:
                            run.update(phase="held", last_error=type(exc).__name__)
                            save()
                            if isinstance(exc, asyncio.CancelledError):
                                raise
                            break
                        if not getattr(message, "id", 0):
                            run.update(phase="held", last_error="神通发送未确认，不重发")
                            break
                        run["magic"][str(identity)] = {"status": "sent", "msg_id": message.id}
                        save()
                    if run["phase"] != "held":
                        run["completed"] = int(run.get("completed") or 0) + 1
                        run.update(phase="done" if run["completed"] >= config["count"] else "idle",
                                   next_at=time.time() + random.uniform(300, 360), result="双方神通已发送，未据此判定胜负")
                    save()
                    continue
                completed = int(run.get("completed") or 0)
                run.clear()
                run.update(id=str(int(now * 1000)), phase="idle", started_at=now, group_role=entry.get("group_role", "primary"),
                           source_identity_id=a, target_identity_id=b, completed=completed)
                if kind == "warm_pairs":
                    if await _send(kind, config, run, config.get("lead_text", "修修"), a):
                        run.update(phase="lead_sent", lead_id=run["last_sent_id"], next_at=time.time() + random.uniform(1.5, 4))
                else:
                    username = str(get_send_as_profile(b).get("username") or "").lstrip("@")
                    if not username:
                        run.update(phase="held", last_error="目标账号缺少用户名")
                    elif await _send(kind, config, run, ".对决 @" + username, a):
                        run.update(phase="waiting_start", command_id=run["last_sent_id"], deadline=time.time() + 90)
                save()


async def game_event(event, **_kwargs):
    async with _LOCK:
        for kind in ("warm_pairs", "global_duel"):
            for run in records(kind).values():
                if run.get("phase") in ACTIVE and reduce_reply(kind, run, event, event.date.timestamp()):
                    return True
    return False


def reset_on_startup():
    for kind in ("warm_pairs", "global_duel"):
        for run in records(kind).values():
            run.pop("last_recovery_at", None)
            if run.get("phase") in ACTIVE - {"held"}:
                run.update(phase="held", last_error="重启前流程未结束，等待已有回执或人工核对")
                save()
