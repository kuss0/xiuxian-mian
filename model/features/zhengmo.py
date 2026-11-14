"""Daily expeditions ported from wxjerry/xiuxian 21900044; local guarded transport."""
import asyncio
import copy
from datetime import datetime
import re
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
MEETING_PHASES = {"meeting_query", "registering"}
ACTIVE_PHASES = {"querying", "pledging", "marching"} | MEETING_PHASES
_LOCKS = {}
_START_LOCK = asyncio.Lock()


def event_time(event):
    return event.date.timestamp()


def reply_ids(event):
    header = getattr(event, "reply_to", None)
    return (int(getattr(event, "reply_to_msg_id", 0) or getattr(header, "reply_to_msg_id", 0) or 0),
            int(getattr(header, "reply_to_top_id", 0) or 0))


def topic_matches(run, reply, top):
    return (not top or top == run["topic_id"]) and (not run["topic_id"] or top == run["topic_id"]
            or reply == run["topic_id"] or str(reply) in run["sent_commands"])


def day_key(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, TZ).strftime("%Y-%m-%d")


def normalize_config(raw, known_ids, root_for_identity):
    if not isinstance(raw, dict):
        raise ValueError("正魔大战配置必须为对象")
    enabled = raw.get("enabled", False)
    role = raw.get("group_role", "primary")
    hour = raw.get("hour", 9)
    auto_meeting = raw.get("auto_meeting", True)
    if not isinstance(enabled, bool) or role not in {"primary", "secondary"}:
        raise ValueError("正魔大战开关或游戏群无效")
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("每日出征时间须为北京时间 0–23 时")
    if type(auto_meeting) is not bool:
        raise ValueError("夺宝大会自动报名须为开关")
    entries = raw.get("identities", [])
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError("正魔大战参与身份须为列表，最多 100 个")
    identities, roots = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("正魔大战身份配置无效")
        try:
            if isinstance(entry.get("identity_id"), bool):
                raise ValueError("invalid identity")
            identity = int(entry.get("identity_id"))
        except (TypeError, ValueError):
            raise ValueError("正魔大战身份 ID 无效") from None
        if identity not in known_ids:
            raise ValueError(f"未知本机身份 {identity}")
        root = root_for_identity(identity)
        if root in roots:
            raise ValueError("正魔大战同一主魂只能选择一个身份")
        roots.add(root)
        faction = entry.get("faction", "")
        if faction not in {"", "正道", "魔道"}:
            raise ValueError("投效偏好须为正道、魔道或留空")
        identities.append({"identity_id": identity, "faction": faction})
    return dict(enabled=enabled, group_role=role, hour=hour, identities=identities, auto_meeting=auto_meeting)


def parse_status(text, now=None):
    if "正魔大战尚未开战" in text or "本季已结束" in text or "赛季已结束" in text:
        return {"unavailable": True}
    season = re.search(r"【正魔大战·第(\d+)季】", text)
    if not season:
        return None
    own = re.search(r"你：(正道|魔道)\s*·\s*战功\s*(\d+)\s*·\s*出征\s*(\d+)/7\s*天", text)
    fixed = re.search(r"你的宗门固定(正道|魔道)", text)
    faction = own[1] if own else fixed[1] if fixed else ""
    # The enemy tactic is also bracketed. Only the explicit counter after 以
    # is actionable, and its intelligence faction must match the actor's faction.
    hint = re.search(r"今日军情[（(](正道|魔道)[）)]：[^\n]*?以【(强攻|固守|奇袭)】应之", text)
    recommendation = hint[2] if hint and faction == hint[1] else ""
    season_day = re.search(r"第\s*(\d+)/14\s*天", text)
    meeting = re.search(r"燕家堡夺宝大会\s*(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text)
    meeting_at = 0
    if meeting:
        current = datetime.fromtimestamp(time.time() if now is None else now, TZ)
        try:
            candidates = [datetime(year, *map(int, meeting.groups()), tzinfo=TZ)
                          for year in (current.year - 1, current.year, current.year + 1)]
            meeting_at = min(candidates, key=lambda value: abs(value.timestamp() - current.timestamp())).timestamp()
        except ValueError:
            pass
    return {"season": int(season[1]), "faction": faction, "recommendation": recommendation,
            "season_day": int(season_day[1]) if season_day else 0, "fixed_faction": bool(fixed),
            "meeting_at": meeting_at,
            "done": bool(own and "今日已出征" in text), "points": int(own[2]) if own else None,
            "days": int(own[3]) if own else 0,
            "needs_faction": not faction and ("投效" in text or "没有阵营" in text)}


def parse_result(text):
    if "今天已出征过了" in text or "今日已出征" in text:
        return {"already_done": True}
    personal = re.search(r"战功\+(\d+)", text)
    days = re.search(r"本季出征\s*(\d+)/7\s*天", text)
    if not text.startswith("【正魔大战·") or not personal or not days:
        return None
    front = re.search(r"计入战线\s*(\d+)", text)
    return {"personal_points": int(personal[1]), "front_points": int(front[1]) if front else int(personal[1]), "days": int(days[1])}


def lock(root):
    return _LOCKS.setdefault(str(root), asyncio.Lock())


async def notice(ctx, run, message):
    await ctx.notice(f"⚔️ 正魔大战｜{run['members'][0]['username']}｜{message}")


async def finish(ctx, run, phase, message):
    run.update(phase=phase, result=message, finished_at=time.time())
    if phase == "done":
        run.update(done=True, daily_phase=phase, daily_result=message)
    ctx.save()
    await notice(ctx, run, message)
    if phase == "done":
        await maybe_meeting(ctx, run)


async def close_meeting(ctx, run, phase, message):
    run["meeting"].update(phase=phase, result=message, updated_at=time.time())
    if run.get("phase") in MEETING_PHASES:
        run.update(phase=run.get("daily_phase", "done"), result=run.get("daily_result", run.get("result", "")))
    ctx.save()
    await notice(ctx, run, "夺宝大会｜" + message)


async def maybe_meeting(ctx, run):
    """One registration per season and root; uncertain registration is never replayed."""
    now = time.time()
    if (not ctx.enabled() or ctx.config() != run["config"]
            or not run["config"].get("enabled")
            or not run["config"].get("auto_meeting", True) or not run.get("faction")
            or not now < run.get("meeting_at", 0) <= now + 4 * 86400):
        return
    meeting = run.setdefault("meeting", {})
    if meeting.get("season") != run.get("season"):
        meeting.clear()
        meeting.update(season=run["season"], phase="idle")
    if meeting.get("phase") in {"registered", "closed", "held"} or meeting.get("registration_attempted"):
        return
    if meeting.get("checked_day") == run["day"]:
        return
    meeting.update(checked_day=run["day"], phase="querying")
    ctx.save()
    await send(ctx, run, ".夺宝大会", "meeting_query")


def registration_result(text):
    if re.search(r"你报名了燕家堡夺宝大会|(?:你(?:已经?|本季已)|(?:本季)?已)报名|报名成功|已(?:经)?报过名|你已赴会", text):
        return "registered"
    if re.search(r"大会(?:已结束|已经结束|已开赛)|报名(?:已截止|已经截止)|开赛后不能报名|本季夺宝大会已", text):
        return "closed"
    return ""


async def send(ctx, run, command, phase):
    run.update(phase=phase, sequence=run.get("sequence", 0) + 1, deadline=time.time() + 60, expires_at=time.time() + 90)
    if phase == "registering":
        run["meeting"].update(chat_id=run["chat_id"], topic_id=run["topic_id"],
                              registration_started_at=time.time())
    sequence = run["sequence"]
    ctx.save()
    member = run["members"][0]
    def allowed():
        config = copy.deepcopy(ctx.config())
        return (ctx.enabled() and config.get("enabled") and ctx.member_enabled(member["identity_id"])
                and ctx.runs().get(str(member["root_id"])) is run and run["phase"] == phase
                and run["sequence"] == sequence and run["day"] == day_key()
                and time.time() < run["deadline"] and not run.get("stop_requested")
                and config == run["config"] and ctx.target(run["group_role"]) == (run["chat_id"], run["topic_id"]))
    if not allowed():
        if phase in MEETING_PHASES:
            await close_meeting(ctx, run, "held", "发送前条件已变化，停止本次大会流程")
            return False
        run.update(phase="retry", next_check=time.time() + 300)
        ctx.save()
        return False
    try:
        message = await ctx.send(command, track=False, send_as_id=member["identity_id"],
                                 target_group_role=run["group_role"], send_guard=allowed,
                                 deadline=run["deadline"],
                                 op_id=f"zhengmo:{run['day']}:{member['root_id']}:{sequence}:{command}",
                                 chain_id=f"zhengmo:{run['day']}:{member['root_id']}")
        msg_id = int(getattr(message, "id", message) or 0)
    except Exception as error:
        msg_id = 0
        run["last_error"] = str(error)
    if not msg_id:
        rejected = getattr(ctx, "send_rejection", lambda *_: "")(member["identity_id"], command)
        if rejected:
            if phase in MEETING_PHASES:
                await close_meeting(ctx, run, "held", rejected)
            else:
                await finish(ctx, run, "blocked", rejected)
            return False
        if phase in MEETING_PHASES:
            await close_meeting(ctx, run, "held", "发送结果未确认，保留本季记录，不重放报名")
            return False
        # Even if transmission was uncertain, the next attempt queries the server.
        run.update(phase="retry", next_check=time.time() + 300)
        ctx.save()
        return False
    run["sent_commands"][str(msg_id)] = {"command": command, "sequence": sequence}
    if phase == "registering":
        run["meeting"].update(registration_msg_id=msg_id)
    run.setdefault("opening_msg_id", msg_id)
    ctx.save()
    return True


async def start(ctx, identity_id):
    async with _START_LOCK:
        config = copy.deepcopy(ctx.config())
        entry = next((i for i in config.get("identities", []) if i["identity_id"] == identity_id), None)
        if not ctx.enabled() or not config.get("enabled") or entry is None:
            return False, "请先开启全局和正魔大战，并配置该参与身份"
        members, error = ctx.members([identity_id])
        if error:
            return False, error
        member = members[0]
        root = str(member["root_id"])
        async with lock(root):
            previous = ctx.runs().get(root, {})
            if previous.get("day") == day_key() and (previous.get("phase") == "done" or previous.get("daily_phase") == "done"):
                return False, "该主魂今日已出征"
            if previous.get("phase") in ACTIVE_PHASES and previous.get("expires_at", 0) > time.time():
                return False, "该主魂正在查询或出征"
            group, topic = ctx.target(config["group_role"])
            if not group:
                return False, "请配置正魔大战使用的游戏群"
            now = time.time()
            same_day = previous.get("day") == day_key()
            same_scope = same_day and (previous.get("chat_id"), previous.get("topic_id")) == (group, topic)
            attempts = previous.get("query_attempts", 0) if same_day else 0
            run = {"members": members, "day": day_key(), "config": config, "entry": entry,
                   "group_role": config["group_role"], "chat_id": group, "topic_id": topic,
                   "started_at": previous.get("started_at", now) if same_scope else now,
                   "phase": "querying", "expires_at": now + 90,
                   "sent_commands": copy.deepcopy(previous.get("sent_commands", {})) if same_scope else {},
                   "query_attempts": attempts + 1, "sequence": previous.get("sequence", 0) if same_day else 0,
                   "meeting": copy.deepcopy(previous.get("meeting", {}))}
            if same_scope and previous.get("opening_msg_id"):
                run["opening_msg_id"] = previous["opening_msg_id"]
            if same_scope:
                for key in ("season", "faction", "meeting_at", "points", "status_done", "points_observed_at"):
                    if key in previous:
                        run[key] = copy.deepcopy(previous[key])
            ctx.runs()[root] = run
            ctx.save()
            ok = await send(ctx, run, ".正魔大战", "querying")
            return ok, "已查询正魔大战，收到本人的今日军情后按推荐出征" if ok else "查询未成功发送，稍后重新查询"


async def record_march(ctx, run, event, reply_id, text, result):
    """Owned terminal evidence can settle a timeout; replay never adds a delta twice."""
    if run.get("march_receipt", {}).get("command_id") == reply_id:
        return
    was_done = run.get("daily_phase") == "done" or run.get("phase") == "done"
    if result.get("personal_points") is not None and run.get("points") is not None and not run.get("status_done", run.get("done", False)):
        # A later raid/defence or dungeon can change the real total independently.
        run["estimated_points"] = run["points"] + result["personal_points"]
    run.update(result)
    run.update(done=True, daily_phase="done", daily_result=text,
               march_receipt={"command_id": reply_id, "message_id": event.id,
                              "event_at": event_time(event), "text": text})
    meeting_active = run.get("phase") in MEETING_PHASES
    if not meeting_active:
        run.update(phase="done", result=text, finished_at=time.time())
    ctx.save()
    if not was_done:
        await notice(ctx, run, text)
    if not meeting_active:
        await maybe_meeting(ctx, run)


async def game_event(ctx, event, *, event_type="message"):
    if (getattr(event, "sender_id", 0) not in ctx.bot_ids()
            or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None)):
        return False
    reply_id, top = reply_ids(event)
    if not reply_id:
        return False
    text = (getattr(event, "raw_text", "") or "").strip()
    candidates = [(root, run) for root, run in list(ctx.runs().items())
                  if event.chat_id in {run.get("chat_id"), run.get("meeting", {}).get("chat_id")}]
    owned = [(root, run) for root, run in candidates if str(reply_id) in run["sent_commands"]
             or reply_id == run.get("meeting", {}).get("registration_msg_id")]
    # Known anchors never wait on another identity. For an early unknown ID,
    # wait independently on in-flight sends, then require an exact owned anchor.
    candidates = owned or [(root, run) for root, run in candidates if lock(root).locked()]
    async def consume(root, run):
        async with lock(root):
            if ctx.runs().get(root) is not run:
                return False
            return await _run_event(ctx, run, event, reply_id, top, text)
    return any(await asyncio.gather(*(consume(root, run) for root, run in candidates)))


async def _run_event(ctx, run, event, reply_id, top, text):
    meeting = run.get("meeting", {})
    if (meeting.get("registration_msg_id") == reply_id and reply_id
            and event.chat_id == meeting.get("chat_id")
            and (not top or top == meeting.get("topic_id"))
            and event_time(event) >= meeting.get("registration_started_at", time.time()) - 1
            and registration_result(text) in {"registered", "closed"}
            and meeting.get("phase") == "held"):
        # Late owned receipt can settle uncertainty without sending anything.
        await close_meeting(ctx, run, registration_result(text), text)
        return True
    sent = run["sent_commands"].get(str(reply_id))
    if (not sent or run["chat_id"] != event.chat_id or run["day"] != day_key()
            or day_key(event_time(event)) != run["day"]
            or event_time(event) < run["started_at"] - 1
            or int(getattr(event, "id", 0) or 0) <= reply_id
            or not topic_matches(run, reply_id, top)):
        return False
    # Terminal receipts close only the march they reply to, even after a
    # restart, timeout, config change or a newer status query. Stale status
    # panels must never start a new action.
    if sent["command"].startswith(".出征 "):
        result = parse_result(text)
        if result:
            await record_march(ctx, run, event, reply_id, text, result)
            return True
    if (run.get("phase") not in ACTIVE_PHASES or sent["sequence"] != run["sequence"]
            or time.time() >= run["deadline"] or not ctx.enabled() or ctx.config() != run["config"]):
        return False
    if sent["command"] == ".正魔大战" and run["phase"] == "querying":
        status = parse_status(text)
        if status is not None and not status.get("unavailable"):
            run.update(status)
            run.update(status_done=status["done"], points_observed_at=event_time(event))
        if status is None:
            await finish(ctx, run, "blocked", "未识别战况回复，未出征：" + text)
        elif status.get("unavailable"):
            await finish(ctx, run, "unavailable", text)
        elif status["done"]:
            run.update(status)
            await finish(ctx, run, "done", f"今日已出征，本季 {status['days']}/7 天，个人战功 {status['points']}")
        elif status["needs_faction"]:
            faction = run["entry"].get("faction")
            if faction and not run.get("pledge_attempted"):
                run["pledge_attempted"] = True
                await send(ctx, run, ".投效 " + faction, "pledging")
            else:
                await finish(ctx, run, "skipped", "无阵营，按配置跳过今日出征；不会自动投效")
        elif status["recommendation"]:
            run.update(status)
            await notice(ctx, run, f"{status['faction']}今日军情推荐【{status['recommendation']}】，准备出征")
            await send(ctx, run, ".出征 " + status["recommendation"], "marching")
        else:
            await finish(ctx, run, "blocked", "未找到与本人阵营一致的“以【战术】应之”提示，未猜测出征打法")
        return True
    if sent["command"] == ".夺宝大会" and run["phase"] == "meeting_query":
        run["meeting"].update(panel=text, panel_msg_id=event.id, checked_at=time.time())
        result = registration_result(text)
        if result:
            await close_meeting(ctx, run, result, text)
        elif "夺宝大会" not in text or time.time() >= run.get("meeting_at", 0):
            await close_meeting(ctx, run, "held", "大会状态未确认，未报名：" + text)
        else:
            # Persist the one-shot reservation before a potentially uncertain send.
            run["meeting"].update(registration_attempted=True, phase="registering")
            ctx.save()
            await send(ctx, run, ".赴会", "registering")
        return True
    if sent["command"] == ".赴会" and run["phase"] == "registering":
        await close_meeting(ctx, run, registration_result(text) or "held", text)
        return True
    if sent["command"].startswith(".投效 ") and run["phase"] == "pledging":
        if re.search(r"你投效了(魔道|正道)|你本季已在(魔道|正道)", text):
            await send(ctx, run, ".正魔大战", "querying")
        else:
            await finish(ctx, run, "blocked", "投效未获确认：" + text)
        return True
    if sent["command"].startswith(".出征 ") and run["phase"] == "marching":
        await finish(ctx, run, "blocked", "出征未获确认：" + text)
        return True
    return False


async def scheduler(ctx, now):
    for root, run in list(ctx.runs().items()):
        if (run.get("phase") == "retry" and run.get("day") == day_key(now)
                and run.get("query_attempts", 0) >= 3 and run.get("next_check", 0) <= now):
            async with lock(root):
                await finish(ctx, run, "blocked", "今日三次尝试仍未确认完成，已停止自动重试；检查游戏后可用 .正魔 执行 身份ID 重新查询")
        if run.get("phase") in ACTIVE_PHASES and (run.get("deadline", 0) <= now or run.get("day") != day_key(now)):
            async with lock(root):
                if run.get("phase") in MEETING_PHASES:
                    await close_meeting(ctx, run, "held", "等待大会回执超时，未重放报名；请核对大会面板")
                    continue
                if run.get("phase") in ACTIVE_PHASES:
                    run.update(phase="retry", next_check=now + 300)
                    ctx.save()
                    await notice(ctx, run, "等待回复超时，稍后先重新查询战况再决定是否出征")
    config = ctx.config()
    if not ctx.enabled() or not config.get("enabled") or datetime.fromtimestamp(now, TZ).hour < config.get("hour", 9):
        return
    for entry in config.get("identities", []):
        root = str(ctx.root(entry["identity_id"]))
        run = ctx.runs().get(root, {})
        if run.get("day") == day_key(now):
            if run.get("phase") != "retry" or run.get("next_check", 0) > now or run.get("query_attempts", 0) >= 3:
                continue
        if not ctx.member_enabled(entry["identity_id"]):
            continue
        ok, _ = await start(ctx, entry["identity_id"])
        if ok:
            break  # Bound the amount of outbound work per scheduler tick.


async def control(ctx, event):
    text = (getattr(event, "raw_text", "") or "").strip()
    match = re.fullmatch(r"\.正魔\s+(执行|状态)\s+(\d+)", text)
    if not match:
        return text.startswith(".正魔"), "用法：.正魔 执行/状态 身份ID；执行会先查询再按今日军情出征"
    identity = int(match[2])
    if match[1] == "执行":
        if not 0 <= time.time() - event_time(event) <= 60:
            return True, "执行指令已过期，请重新发送"
        _, message = await start(ctx, identity)
        return True, message
    run = ctx.runs().get(str(ctx.root(identity)), {})
    return True, f"正魔大战｜{run.get('day', '')}｜{run.get('phase', '未执行')}\n{run.get('result') or run.get('last_error') or ''}"
