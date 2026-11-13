"""Fill weekly rewarded voyages from owned 船况 receipts, never guessed resets."""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

from . import hunt

_LOCK = asyncio.Lock()
_QUOTA_RE = re.compile(r"本周有奖出海[：:]\s*(\d+)\s*/\s*(\d+)")
_TZ = timezone(timedelta(hours=8))
QUERY_TIMEOUT = 120
BETWEEN_VOYAGES = 300


def week_key(now):
    return datetime.fromtimestamp(now, _TZ).strftime("%G-W%V")


def next_week_check(now):
    local = datetime.fromtimestamp(now, _TZ)
    monday = (local + timedelta(days=7 - local.weekday())).replace(hour=0, minute=10, second=0, microsecond=0)
    return monday.timestamp()


def _enabled_teams(ctx):
    return [team for team in ctx.teams() if team.get("auto_weekly")]


def _query_allowed(ctx, team, record, request):
    current = next((item for item in _enabled_teams(ctx) if item["name"] == team["name"]), None)
    return (ctx.enabled() and current == team and ctx.weekly().get(team["name"]) is record
            and record.get("query") is request and record.get("phase") == "checking"
            and ctx.target(team["group_role"]) == (request["chat_id"], request["topic_id"]))


async def _hold(ctx, record, reason):
    record.update(phase="held", last_error=reason)
    ctx.save()
    await ctx.notice(f"⛵ 猎妖周常已停止：{reason}；保留记录，请先核对游戏船况。")


async def reset_check(ctx, name):
    """Explicit operator retry only resets read queries, never an unknown boat."""
    async with _LOCK:
        record = ctx.weekly().get(name)
        if not record:
            return False, "没有该队伍的周常记录"
        run = ctx.runs().get(name, {})
        if hunt._active(run) or run.get("requires_reconciliation"):
            return False, "猎妖船仍在进行或待核对，请先确认原船结果"
        record.update(phase="idle", counters={}, query=None, next_at=0)
        ctx.save()
        return True, "已安排重新核对周次数，开船仍以服务器船况为准"


async def scheduler(ctx, now):
    if not ctx.enabled():
        return
    async with _LOCK:
        for team in _enabled_teams(ctx):
            record = ctx.weekly().setdefault(team["name"], {"phase": "idle"})
            # A changed team needs fresh per-member quota evidence.
            if record.get("team") != team:
                record.clear()
                record.update(phase="idle", team=dict(team), counters={})
                ctx.save()
            if record.get("phase") == "held":
                continue
            if record.get("phase") == "running":
                run = ctx.runs().get(team["name"], {})
                if run.get("started_at") != record.get("run_started_at"):
                    await _hold(ctx, record, "周常运行记录与当前猎妖船不一致")
                    continue
                if run.get("phase") == "done":
                    # A receipt proves this voyage ended, not the remaining
                    # weekly quota. Refresh both server counters before sailing.
                    record.update(phase="idle", counters={}, next_at=now + BETWEEN_VOYAGES)
                    ctx.save()
                elif run.get("phase") not in hunt.ACTIVE_PHASES:
                    await _hold(ctx, record, "上一趟猎妖未确认正常返航，不自动重开")
                continue
            if record.get("phase") == "starting":
                await _hold(ctx, record, "开船过程曾中断，请核对游戏船况")
                continue
            if record.get("phase") == "checking" and record.get("query"):
                if now > float(record["query"].get("sent_at") or now) + QUERY_TIMEOUT:
                    await _hold(ctx, record, "船况查询未获身份绑定回执")
                continue
            if float(record.get("next_at") or 0) > now:
                continue
            if record.get("phase") == "done" and record.get("checked_week") == week_key(now):
                continue
            active = [run for run in ctx.runs().values()
                      if isinstance(run, dict) and (hunt._active(run) or run.get("requires_reconciliation"))]
            if any(run.get("chat_id") == ctx.target(team["group_role"])[0]
                   or set(team["member_identity_ids"]) & {m["identity_id"] for m in run.get("members", [])}
                   for run in active):
                continue
            members, error = ctx.members(team["member_identity_ids"])
            if error or len(members) != len(team["member_identity_ids"]):
                record.update(last_error=error or "成员信息不完整", next_at=now + 300)
                ctx.save()
                continue
            if record.get("checked_week") != week_key(now):
                record["counters"] = {}
            counters = record.setdefault("counters", {})
            if any(now - float(counter.get("observed_at") or 0) > 180 for counter in counters.values()):
                counters.clear()
            pending = [m for m in members if str(m["identity_id"]) not in counters]
            if pending:
                member = pending[0]
                chat_id, topic_id = ctx.target(team["group_role"])
                if not chat_id:
                    continue
                request = {"identity_id": member["identity_id"], "chat_id": chat_id,
                           "topic_id": topic_id, "sent_at": now, "msg_id": 0}
                record.update(phase="checking", query=request, checked_week=week_key(now))
                ctx.save()
                msg = await ctx.send(".船况", send_as_id=member["identity_id"],
                                     target_group_role=team["group_role"],
                                     send_guard=lambda: _query_allowed(ctx, team, record, request))
                request["msg_id"] = int(getattr(msg, "id", 0) or 0)
                if not request["msg_id"]:
                    await _hold(ctx, record, "船况查询发送未确认，未开船")
                ctx.save()
                return
            if all(int(counter["used"]) >= 5 for counter in counters.values()):
                record.update(phase="done", checked_week=week_key(now), next_at=next_week_check(now), last_error="")
                ctx.save()
                await ctx.notice(f"⛵ 猎妖周常｜{team['name']}｜所有成员本周有奖出海已达 5/5，停止开船。")
                continue
            # Quotas are checked for every voyage, including a completed helper
            # accompanying a member who still has rewarded slots.
            record.update(phase="starting", last_error="")
            ctx.save()
            ok, message = await hunt.start(ctx, team["name"])
            run = ctx.runs().get(team["name"], {})
            if ok:
                record.update(phase="running", run_started_at=run["started_at"])
                ctx.save()
            else:
                await _hold(ctx, record, message)
            return


async def game_event(ctx, event):
    if getattr(event, "sender_id", 0) not in ctx.bot_ids():
        return False
    reply = int(getattr(event, "reply_to_msg_id", 0) or
                getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    if not reply:
        return False
    async with _LOCK:
        matches = [(team, ctx.weekly().get(team["name"], {})) for team in _enabled_teams(ctx)]
        matches = [(team, record) for team, record in matches
                   if record.get("phase") == "checking"
                   and record.get("team") == team
                   and int((record.get("query") or {}).get("msg_id") or 0) == reply
                   and int((record.get("query") or {}).get("chat_id") or 0) == getattr(event, "chat_id", 0)]
        if len(matches) != 1:
            return False
        team, record = matches[0]
        query = record["query"]
        if ctx.target(team["group_role"]) != (query["chat_id"], query["topic_id"]):
            return False
        topic = int(getattr(getattr(event, "reply_to", None), "reply_to_top_id", 0) or 0)
        if topic and topic != query["topic_id"]:
            return False
        if hunt._event_time(event) < query["sent_at"] - 1:
            return False
        text = str(getattr(event, "raw_text", "") or "")
        match = _QUOTA_RE.search(text)
        if not match or int(match.group(2)) != 5 or not 0 <= int(match.group(1)) <= 5:
            await _hold(ctx, record, "船况周次数格式未识别，未猜测可用次数")
        elif not any(value in text for value in ("你眼下不在任何猎妖船上", "你不在任何猎妖船上")):
            await _hold(ctx, record, "游戏船况未确认空闲，请核对现有猎妖船")
        else:
            record.setdefault("counters", {})[str(query["identity_id"])] = {
                "used": int(match.group(1)), "limit": 5, "observed_at": time.time(),
                "source_msg_id": int(getattr(event, "id", 0) or 0),
            }
            record.update(phase="checking", query=None, next_at=0)
            ctx.save()
        return True
