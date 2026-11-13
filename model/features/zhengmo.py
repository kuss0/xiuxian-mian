"""Daily expeditions ported from wxjerry/xiuxian 21900044; local guarded transport."""
import asyncio
import copy
from datetime import datetime
import re
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
ACTIVE_PHASES = {"querying", "pledging", "marching"}
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
    if not isinstance(enabled, bool) or role not in {"primary", "secondary"}:
        raise ValueError("正魔大战开关或游戏群无效")
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("每日出征时间须为北京时间 0–23 时")
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
    return dict(enabled=enabled, group_role=role, hour=hour, identities=identities)


def parse_status(text):
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
    return {"season": int(season[1]), "faction": faction, "recommendation": recommendation,
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
    ctx.save()
    await notice(ctx, run, message)


async def send(ctx, run, command, phase):
    run.update(phase=phase, sequence=run.get("sequence", 0) + 1, deadline=time.time() + 60, expires_at=time.time() + 90)
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
        # Even if transmission was uncertain, the next attempt queries the server.
        run.update(phase="retry", next_check=time.time() + 300)
        ctx.save()
        return False
    run["sent_commands"][str(msg_id)] = {"command": command, "sequence": sequence}
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
            if previous.get("day") == day_key() and previous.get("phase") == "done":
                return False, "该主魂今日已出征"
            if previous.get("phase") in ACTIVE_PHASES and previous.get("expires_at", 0) > time.time():
                return False, "该主魂正在查询或出征"
            group, topic = ctx.target(config["group_role"])
            if not group:
                return False, "请配置正魔大战使用的游戏群"
            now = time.time()
            attempts = previous.get("query_attempts", 0) if previous.get("day") == day_key() else 0
            run = {"members": members, "day": day_key(), "config": config, "entry": entry,
                   "group_role": config["group_role"], "chat_id": group, "topic_id": topic,
                   "started_at": now, "phase": "querying", "expires_at": now + 90,
                   "sent_commands": {}, "query_attempts": attempts + 1, "sequence": 0}
            ctx.runs()[root] = run
            ctx.save()
            ok = await send(ctx, run, ".正魔大战", "querying")
            return ok, "已查询正魔大战，收到本人的今日军情后按推荐出征" if ok else "查询未成功发送，稍后重新查询"


async def game_event(ctx, event, *, event_type="message"):
    if (getattr(event, "sender_id", 0) not in ctx.bot_ids()
            or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None)):
        return False
    reply_id, top = reply_ids(event)
    text = (getattr(event, "raw_text", "") or "").strip()
    for root, run in list(ctx.runs().items()):
        if (run.get("phase") not in ACTIVE_PHASES or run["chat_id"] != event.chat_id
                or run["day"] != day_key() or event_time(event) < run["started_at"] - 1
                or int(getattr(event, "id", 0) or 0) <= run.get("opening_msg_id", 0)
                or not topic_matches(run, reply_id, top)):
            continue
        # Never infer personal state from someone else's query or a broadcast.
        if str(reply_id) not in run["sent_commands"]:
            continue
        async with lock(root):
            sent = run["sent_commands"].get(str(reply_id))
            if (not sent or sent["sequence"] != run["sequence"] or time.time() >= run["deadline"]
                    or not ctx.enabled() or ctx.config() != run["config"]):
                continue
            if sent["command"] == ".正魔大战" and run["phase"] == "querying":
                status = parse_status(text)
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
            if sent["command"].startswith(".投效 ") and run["phase"] == "pledging":
                if re.search(r"你投效了(魔道|正道)|你本季已在(魔道|正道)", text):
                    await send(ctx, run, ".正魔大战", "querying")
                else:
                    await finish(ctx, run, "blocked", "投效未获确认：" + text)
                return True
            if sent["command"].startswith(".出征 ") and run["phase"] == "marching":
                result = parse_result(text)
                if result:
                    run.update(result)
                    await finish(ctx, run, "done", text)
                else:
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
