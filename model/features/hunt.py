"""Automatically respond to revealed boat events; log commands start/stop or rescue unknown events."""
import asyncio
import re
import time

ACTIONS = ("出手", "护船", "探路", "祭符")
SEAS = ("近海", "暗礁", "外海")
EVENT_ACTIONS = {
    "海市蜃楼": "探路",
    "雾锁暗礁": "探路",
    "妖兽群": "出手",
    "同行劫修": "出手",
    "磁山崩落": "护船",
    "墨蛟翻船": "护船",
}
SCOUT_RE = re.compile(r"^你攀上桅杆看清了[：:]\s*是【([^】]+)】")
ACTIVE_PHASES = {"opening", "recruiting", "sailing", "voting", "scouting", "awaiting_action", "acting", "acted"}
CONTROL_RE = re.compile(r"^\.猎妖\s+(出发|停止|出手|护船|探路|祭符)(?:\s+(\S+))?\s*$")
DAY_RE = re.compile(r"^【猎妖船】第([123])日(?:，选海域|\s*·)")
NAME_RE = re.compile(r"@([^\s…，。；｜|]+)")
HULL_RE = re.compile(r"^船体[^\r\n\d]*(\d+)\s*/\s*(\d+)\s+舱中收获", re.M)
_LOCKS = {}


def get_team_seas(team):
    """Three-day route, with backwards compatibility for a single old sea."""
    seas = team.get("seas") if "seas" in team else [team.get("sea", "外海")] * 3
    if not isinstance(seas, list) or len(seas) != 3 or any(sea not in SEAS for sea in seas):
        raise ValueError("请为第 1、2、3 日分别选择近海、暗礁或外海")
    return list(seas)


def choose_sea(run, panel):
    """Apply the configured preference to this owned day's fresh hull panel."""
    preferred = get_team_seas(run)[run["day"] - 1]
    match = HULL_RE.search(panel)
    hull = int(match.group(1)) if match else None
    maximum = int(match.group(2)) if match else None
    if match and (maximum <= 0 or not 0 < hull <= maximum):
        hull = None
    if preferred == "外海":
        if hull is None:
            raise ValueError("本轮船体未识别，未猜测外海风险")
        # User-supplied game rule: an outer-sea mistake costs at most 35.
        # At 36+ it cannot sink the ship in a single encounter.
        selected = "外海" if hull >= 36 else "近海"
    else:
        selected = preferred
    run.setdefault("route_decisions", {})[str(run["day"])] = {
        "preferred": preferred, "selected": selected, "hull": hull,
        "reason": "船体不足36，退近海降低风险" if selected != preferred else "按首选航向",
    }
    return selected


def normalize_teams(raw, known_ids, root_for_identity):
    if not isinstance(raw, list) or len(raw) > 20:
        raise ValueError("猎妖队伍必须是列表，最多 20 支")
    result, names = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("猎妖队伍配置格式错误")
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 24 or re.search(r"\s", name):
            raise ValueError("队伍名称须为 1–24 个字符，不能包含空格")
        if name in names:
            raise ValueError(f"猎妖队伍名称重复：{name}")
        names.add(name)
        role = item.get("group_role", "secondary")
        if role not in ("primary", "secondary"):
            raise ValueError(f"{name}：游戏群无效")
        automatic = item.get("auto_weekly", False)
        if type(automatic) is not bool:
            raise ValueError(f"{name}：周常自动补满须为开关")
        try:
            seas = get_team_seas(item)
        except ValueError as error:
            raise ValueError(f"{name}：{error}") from None
        members = item.get("member_identity_ids")
        if not isinstance(members, list) or not 2 <= len(members) <= 5:
            raise ValueError(f"{name}：请选择 2–5 名成员，首位为船长兼探路者")
        identities, roots = [], set()
        for value in members:
            try:
                identity_id = int(value)
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"{name}：成员 ID 无效") from None
            if identity_id not in known_ids:
                raise ValueError(f"{name}：未知本机身份 {identity_id}")
            root = root_for_identity(identity_id)
            if root in roots:
                raise ValueError(f"{name}：同一主魂只能选择一名成员（含分身）")
            roots.add(root)
            identities.append(identity_id)
        team = {"name": name, "group_role": role, "seas": seas, "member_identity_ids": identities}
        if automatic:
            team["auto_weekly"] = True
        result.append(team)
    return result


def _lock(name):
    return _LOCKS.setdefault(name, asyncio.Lock())


def _active(run):
    return run.get("phase") in ACTIVE_PHASES and float(run.get("expires_at", 0)) > time.time() and not run.get("stop_requested")


def _member_name(member):
    # Bind the game's actual mention from command replies; it may be a 道号 rather
    # than a Telegram username (e.g. @稀饭 in the captured logs).
    return str(member.get("boat_name") or member["username"]).lower()


def _onboard_members(run):
    """Once a sailing panel is seen, only operate local identities actually aboard."""
    ids = run.get("onboard_identity_ids")
    if ids is None:
        return run["members"]
    return [member for member in run["members"] if member["identity_id"] in ids]


def _event_time(event):
    date = getattr(event, "date", None)
    return date.timestamp() if date is not None else time.time()


async def _notice(ctx, run, message):
    await ctx.notice(f"⛵ 猎妖｜{run['name']}｜{message}")


async def _send(ctx, run, member, command, *, deadline=0):
    day = run["day"]
    deadline = float(deadline or run.get("deadline") or 0)

    def allowed():
        return (ctx.enabled() and _active(run) and not run.get("terminal_pending")
                and ctx.runs().get(run["name"]) is run and run["day"] == day
                and ctx.target(run["group_role"]) == (run["chat_id"], run["topic_id"])
                and (not deadline or time.time() < deadline)
                and (command in {".出海猎妖", ".登船"} or member in _onboard_members(run)))

    if not allowed():
        return False
    key = f"{day}:{member['identity_id']}:{command}"
    attempts = run.setdefault("command_attempts", {})
    if key in attempts:
        return False
    attempt = {"status": "unknown", "command": command, "identity_id": member["identity_id"], "at": time.time()}
    attempts[key] = attempt
    ctx.save()
    try:
        message = await ctx.send(command, track=False, send_as_id=member["identity_id"],
                                 target_group_role=run["group_role"], send_guard=allowed,
                                 deadline=deadline, op_id=f"hunt:{run['started_at']}:{key}",
                                 chain_id=f"hunt:{run['started_at']}:{day}")
        msg_id = int(getattr(message, "id", message) or 0)
    except Exception as error:
        run["last_error"] = str(error)
        ctx.save()
        return False
    if not msg_id:
        if getattr(ctx, "definitely_unsent", lambda *_: False)(member["identity_id"], command):
            attempt["status"] = "unsent"
        ctx.save()
        return False
    attempt.update(status="sent", msg_id=msg_id)
    run.setdefault("sent_commands", {})[str(msg_id)] = {
        "identity_id": member["identity_id"], "command": command, "day": day,
    }
    if command == ".探路" and member["identity_id"] == run["members"][0]["identity_id"] and run["phase"] == "scouting":
        run["scout_msg_id"] = msg_id
    ctx.save()
    return True


async def _fail(ctx, run, reason, *, definitive=False):
    uncertain = not definitive and any(a.get("status") in {"sent", "unknown"}
                                       for a in run.get("command_attempts", {}).values())
    run.update(phase="held" if uncertain else "failed", requires_reconciliation=uncertain,
               last_error=reason, finished_at=time.time())
    ctx.save()
    await _notice(ctx, run, reason)


async def _apply_round_action(ctx, run, action, *, source):
    """Caller holds the team lock; claim this round before any sends await."""
    if run["phase"] not in {"scouting", "awaiting_action"}:
        return "该轮动作已经提交，请等待下一日"
    if not ctx.enabled():
        return "全局已暂停，未发送猎妖动作"
    if time.time() >= run["deadline"]:
        return "该轮已过期，未发送猎妖动作"
    run.update(phase="acting", action=action, action_source=source,
               action_sent=[], action_confirmed=[], action_failed=[])
    ctx.save()
    captain_id = run["members"][0]["identity_id"]
    # The captain already has a confirmed scout choice. Give the other crew
    # their first choice before spending the window replacing that scout.
    members = sorted(_onboard_members(run), key=lambda member: member["identity_id"] == captain_id)
    for member in members:
        if not _active(run) or run.get("terminal_pending"):
            return "本次猎妖已停止，取消后续动作"
        # The first member's successful scout is already this round's 探路 action.
        # For combat/defence, replace it too so every crew member responds.
        if member["identity_id"] == captain_id and action == "探路":
            run["action_sent"].append(member["identity_id"])
            run["action_confirmed"].append(member["identity_id"])
        elif await _send(ctx, run, member, "." + action, deadline=run["deadline"]):
            run["action_sent"].append(member["identity_id"])
        else:
            run["action_failed"].append(member["username"])
        ctx.save()
    if not _active(run) or run.get("terminal_pending"):
        return "本次猎妖已停止，取消后续动作"
    run["phase"] = "acted"
    ctx.save()
    suffix = "；未发送：" + "、".join(run["action_failed"]) if run["action_failed"] else ""
    reused = "（首位沿用已确认的探路）" if action == "探路" else "（含船长，覆盖原探路）"
    return f"第{run['day']}日｜{run.get('sea_event') or '未揭示海况'}｜{source} .{action}，已提交 {len(run['action_sent'])}/{len(_onboard_members(run))} 名在船本机成员{reused}{suffix}"


async def _handle_scout_result(ctx, run, text):
    run["scout_text"] = text
    match = SCOUT_RE.match(text)
    run["sea_event"] = match.group(1) if match else ""
    action = EVENT_ACTIONS.get(run["sea_event"])
    ctx.save()
    if action and ctx.enabled():
        message = await _apply_round_action(ctx, run, action, source="自动应对")
        await _notice(ctx, run, text + "\n" + message)
    else:
        run["phase"] = "awaiting_action"
        ctx.save()
        reason = "全局已暂停" if action else ("未知海况" if match else "探路尚未揭示真实海况")
        await _notice(ctx, run, f"第{run['day']}日：{text}\n{reason}，未猜测动作；可在截止前手动 .猎妖 动作 {run['name']}")


async def start(ctx, name):
    team = next((team for team in ctx.teams() if team["name"] == name), None)
    if team is None:
        return False, f"未配置猎妖队伍 {name}，请先在副本配置保存队伍"
    async with _lock(name):
        if not ctx.enabled():
            return False, "全局已暂停，不能开始猎妖"
        chat_id, topic_id = ctx.target(team["group_role"])
        if not chat_id:
            return False, "请先配置队伍所用的游戏主群/副群"
        try:
            seas = get_team_seas(team)
        except ValueError as error:
            return False, f"{name}：{error}"
        members, error = ctx.members(team["member_identity_ids"])
        if error:
            return False, error
        for run in ctx.runs().values():
            if not (_active(run) or run.get("requires_reconciliation")):
                continue
            if run["name"] == name or run["chat_id"] == chat_id:
                return False, f"{run['name']} 正在该游戏群猎妖，请等待结束或停止后再出发"
            roots = {m["root_id"] for m in run["members"]}
            if any(m["root_id"] in roots for m in members):
                return False, "所选成员正在其他猎妖队伍中"
        now = time.time()
        run = {**team, "seas": seas, "chat_id": chat_id, "topic_id": topic_id, "members": members,
               "phase": "opening", "started_at": now, "expires_at": now + 15 * 60,
               "deadline": now + 30, "day": 0, "confirmed": [], "sent_commands": {},
               "seen_events": [], "action_sent": []}
        ctx.runs()[name] = run
        ctx.save()
        if not await _send(ctx, run, members[0], ".出海猎妖"):
            await _fail(ctx, run, "船长开船未获发送确认，已停止自动操作，请核对游戏船况")
            return False, run["last_error"]
        return True, f"{name} 已开船，等待确认后其余 {len(members) - 1} 名成员登船"


async def control(ctx, event):
    text = (getattr(event, "raw_text", "") or "").strip()
    confirmation = re.fullmatch(r"\.猎妖\s+确认结束\s+(\S+)\s+(\S+)", text)
    if confirmation:
        name, receipt_key = confirmation.groups()
        run = ctx.runs().get(name, {})
        if getattr(event, "date", None) is None or not 0 <= time.time() - _event_time(event) <= 60:
            return True, "确认指令已过期，请重新发送"
        if not run.get("requires_reconciliation") or str(run.get("started_at") or "") != receipt_key:
            return True, "须为待核对猎妖记录，并填写状态中显示的准确记录号"
        run.update(phase="stopped", requires_reconciliation=False, operator_confirmed_at=time.time())
        if hasattr(ctx, "weekly"):
            record = ctx.weekly().get(name)
            if record:
                record.update(phase="idle", counters={}, query=None, next_at=time.time() + 300)
        ctx.save()
        return True, "已记录人工确认结束，保留原回执；周常下一趟会重新核对服务器次数"
    match = CONTROL_RE.fullmatch(text)
    if match is None:
        if text.startswith(".猎妖"):
            return True, "用法：.猎妖 出发 队伍1 / .猎妖 出手 [队伍名] / .猎妖 停止 队伍1"
        return False, ""
    action, name = match.groups()
    if action == "出发":
        if time.time() - _event_time(event) > 60:
            return True, "出发指令已过期，请重新发送"
        if not name:
            return True, "请指定队伍：.猎妖 出发 队伍1"
        _, message = await start(ctx, name)
        return True, message
    if action == "停止":
        if not name:
            return True, "请指定队伍：.猎妖 停止 队伍1"
        run = ctx.runs().get(name)
        if not run or not _active(run):
            return True, f"{name} 没有正在进行的猎妖"
        run["stop_requested"] = True
        run["requires_reconciliation"] = True
        ctx.save()
        async with _lock(name):
            run.update(phase="held", finished_at=time.time())
            ctx.save()
        return True, f"已停止 {name} 的自动操作（不会自动解散游戏中的船）"
    candidates = [r for r in ctx.runs().values() if _active(r) and r["phase"] == "awaiting_action" and (not name or r["name"] == name)]
    if len(candidates) != 1:
        return True, "没有可执行的手动兜底轮次（已自动应对或未完成探路），或多队同时等待；请使用 .猎妖 动作 队伍名"
    run = candidates[0]
    async with _lock(run["name"]):
        if run["phase"] != "awaiting_action":
            return True, "该轮动作已经提交，请等待下一日"
        if not ctx.enabled():
            return True, "全局已暂停，未发送猎妖动作"
        if _event_time(event) < run["round_started_at"] - 1 or time.time() >= run["deadline"]:
            return True, "动作指令已过期，未发送到游戏群"
        message = await _apply_round_action(ctx, run, action, source="手动兜底")
        return True, f"{run['name']} {message}"


async def _sail(ctx, run):
    if len(run["confirmed"]) != len(run["members"]) or run["phase"] != "recruiting":
        return
    run["phase"] = "sailing"
    run["deadline"] = time.time() + 35
    ctx.save()
    if not await _send(ctx, run, run["members"][0], ".起锚"):
        await _fail(ctx, run, "满员起锚发送失败，本次已停止")
    else:
        await _notice(ctx, run, "所选成员已全部登船，已起锚")


async def game_event(ctx, event, *, event_type="message"):
    if getattr(event, "sender_id", 0) not in ctx.bot_ids():
        return False
    text = (getattr(event, "raw_text", "") or "").strip()
    candidates = [run for run in ctx.runs().values() if (_active(run) or run.get("requires_reconciliation")) and run["chat_id"] == event.chat_id]
    if len(candidates) != 1:
        return False
    run = candidates[0]
    if _event_time(event) < run["started_at"] - 1:
        return False
    header = getattr(event, "reply_to", None)
    top = int(getattr(header, "reply_to_top_id", 0) or 0)
    reply_id = int(getattr(header, "reply_to_msg_id", 0) or 0)
    if top and top != run["topic_id"]:
        return False
    known_reply = run["sent_commands"].get(str(reply_id))
    relevant = ("猎妖船" in text or "登船（" in text or "攀上桅杆看清了" in text
                or text.startswith(tuple("你选择【" + action + "】" for action in ACTIONS))
                or text.startswith("你投了") or text in {"起锚！", "起锚!", "现在还没遇上海况。"})
    if not relevant and not known_reply:
        # Soul-switch replies must reach their handshake handler while a boat
        # send holds this lock. For an early generic rejection, inspect its
        # original command before deciding whether it belongs to hunting.
        get_reply = getattr(event, "get_reply_message", None)
        if get_reply is None:
            return False
        original = await get_reply()
        command = str(getattr(original, "raw_text", "") or "").strip()
        if command not in {".出海猎妖", ".登船", ".起锚", *("." + action for action in ACTIONS)} and not command.startswith(".航向 "):
            return False
    topic_matches = (top == run["topic_id"] or reply_id == run["topic_id"] or known_reply
                     or not run["topic_id"])
    result_names = {name.lower() for name in NAME_RE.findall(text)}
    expected_names = {_member_name(member) for member in _onboard_members(run)}
    if (topic_matches and "【猎妖船·返航】" in text
            and _member_name(run["members"][0]) in result_names
            and expected_names.issubset(result_names)):
        # Invalidate the remaining sends while the terminal event waits for the lock.
        run["terminal_pending"] = True
    # The command ID is registered after send returns. Read it after acquiring
    # the sender's team lock so an early reply sees the completed registration.
    async with _lock(run["name"]):
        if not (_active(run) or run.get("requires_reconciliation")):
            return False
        sent = run["sent_commands"].get(str(reply_id))
        if run["topic_id"] and not (top == run["topic_id"] or reply_id == run["topic_id"] or sent):
            return False
        if not relevant and not sent:
            return False
        key = f"{getattr(event, 'id', 0)}:{event_type}:{text}"
        if key in run["seen_events"]:
            return True
        run["seen_events"] = (run["seen_events"] + [key])[-100:]
        settlement = "【猎妖船·返航】" in text
        if settlement:
            result_names = {n.lower() for n in NAME_RE.findall(text)}
            expected = {_member_name(m) for m in _onboard_members(run)}
            if _member_name(run["members"][0]) not in result_names or not expected.issubset(result_names):
                return False
        cancelled = "凑不齐两名船员" in text and run["phase"] == "recruiting"
        sunk = ("沉没" in text or "沉船" in text) and (sent or all("@" + m["username"] in text for m in run["members"]))
        if settlement or cancelled or sunk:
            run.update(phase="done" if settlement else "failed", requires_reconciliation=False,
                       result=text, finished_at=time.time())
            ctx.save()
            await _notice(ctx, run, text)
            return True
        if run.get("requires_reconciliation"):
            return False
        if "挂起猎妖旗" in text and run["phase"] == "opening":
            names = NAME_RE.findall(text)
            own_open_reply = sent and sent["command"] == ".出海猎妖" and sent["identity_id"] == run["members"][0]["identity_id"]
            if not names or (not own_open_reply and names[0].lower() != _member_name(run["members"][0])):
                return False
            run["members"][0]["boat_name"] = names[0]
            run.update(phase="recruiting", confirmed=[run["members"][0]["identity_id"]], deadline=time.time() + 92)
            ctx.save()
            for member in run["members"][1:]:
                if not await _send(ctx, run, member, ".登船", deadline=run["deadline"]):
                    run.setdefault("join_rejected", []).append(member["identity_id"])
                    ctx.save()
                    await _notice(ctx, run, f"{member['username']} 登船发送失败，继续等待实际船员面板")
            return True
        if "登船（" in text:
            names = NAME_RE.findall(text)
            joined_local = False
            if names:
                for member in run["members"]:
                    own_join_reply = sent and sent["command"] == ".登船" and sent["identity_id"] == member["identity_id"]
                    if (own_join_reply or (not sent and _member_name(member) == names[0].lower())) and member["identity_id"] not in run["confirmed"]:
                        member["boat_name"] = names[0]
                        run["confirmed"].append(member["identity_id"])
                        joined_local = True
            if run["phase"] != "recruiting":
                # A boarding reply can arrive after automatic departure. Bind the
                # alias, but do not send another anchor or roll back the stage.
                if "boat_names" in run:
                    run["onboard_identity_ids"] = [
                        member["identity_id"] for member in run["members"]
                        if _member_name(member) in run["boat_names"]
                    ]
                ctx.save()
                return True
            count = re.search(r"登船[（(](\d+)/5[）)]", text)
            if joined_local and count and int(count.group(1)) >= 5:
                run.update(phase="sailing", deadline=time.time() + 35)
                ctx.save()
                await _notice(ctx, run, "船员已满 5 人，等待自动出发面板；包含外人时仍控制已登船的本机成员")
            else:
                ctx.save()
                await _sail(ctx, run)
            return True
        if sent and sent["command"] == ".登船" and text == "这里没有正在招人的猎妖船。发 .出海猎妖 自己开一条。":
            member = next((m for m in run["members"] if m["identity_id"] == sent["identity_id"]), None)
            rejected = run.setdefault("join_rejected", [])
            if member and member["identity_id"] not in rejected:
                rejected.append(member["identity_id"])
                ctx.save()
                await _notice(ctx, run, f"{member['username']} 未能登船，船可能已满员出发；继续依据实际船员面板执行，不另开船")
            return True
        if sent and sent["command"] == ".起锚" and text in {"起锚！", "起锚!"}:
            # This is a successful acknowledgement, not a stage panel or rejection.
            # A delayed acknowledgement must not roll back voting/scouting either.
            if run["phase"] == "sailing":
                run["anchor_confirmed"] = True
                ctx.save()
            return True
        match = DAY_RE.match(text)
        if match and "船员：" in text:
            panel_names = {name.lower() for name in NAME_RE.findall(text.split("船员：", 1)[1].split("剩余", 1)[0])}
            # The configured captain identifies this boat; outsiders are not an error.
            # A panel containing only another selected member belongs to another boat.
            if _member_name(run["members"][0]) not in panel_names:
                return False
            onboard = [member["identity_id"] for member in run["members"] if _member_name(member) in panel_names]
            previous_names = run.get("boat_names")
            run.update(boat_names=sorted(panel_names), onboard_identity_ids=onboard)
            ctx.save()
            if previous_names != run["boat_names"] and len(onboard) != len(panel_names):
                await _notice(ctx, run, f"实际船员 {len(panel_names)} 人，其中本机 {len(onboard)} 人；继续执行航向/探路/应对，不控制外人或未登船角色")
        if match and not sent and "船员：" not in text:
            return False
        if match and "，选海域" in text:
            day = int(match.group(1))
            if day <= run["day"]:
                return True
            run.update(phase="voting", day=day, action_sent=[], action_confirmed=[],
                       action_failed=[], action_rejected=[], action="", action_source="",
                       sea_event="", scout_text="", scout_msg_id=0, deadline=_event_time(event) + 30)
            try:
                sea = choose_sea(run, text)
            except ValueError as error:
                await _fail(ctx, run, str(error))
                return True
            ctx.save()
            # One captain vote chooses the configured route. Having every crew
            # member vote consumes send slots and can start the sea stage early,
            # leaving the scout and response chain behind the local throttle.
            if not await _send(ctx, run, run["members"][0], ".航向 " + sea, deadline=run["deadline"]):
                await _fail(ctx, run, "航向发送失败/超时，本次已停止")
                return True
            return True
        if match and "征兆：" in text:
            day = int(match.group(1))
            if run["phase"] != "voting" or day != run["day"]:
                return True
            seconds = re.search(r"剩余\s*(\d+)\s*秒", text)
            remaining = min(45, int(seconds.group(1))) if seconds else 45
            now = _event_time(event)
            run.update(phase="scouting", round_started_at=now, deadline=now + max(0, remaining - 2), scout_text="")
            ctx.save()
            if not await _send(ctx, run, run["members"][0], ".探路", deadline=run["deadline"]):
                await _fail(ctx, run, "首位成员探路发送失败/超时，本次已停止")
            return True
        if run["phase"] in {"scouting", "awaiting_action"} and reply_id == run.get("scout_msg_id") and sent and sent["command"] == ".探路" and sent["identity_id"] == run["members"][0]["identity_id"]:
            if _event_time(event) < run["round_started_at"] - 1 or time.time() >= run["deadline"]:
                return True
            if SCOUT_RE.match(text) or text.startswith("你选择【探路】"):
                await _handle_scout_result(ctx, run, text)
            else:
                await _fail(ctx, run, "探路未成功：" + text)
            return True
        if match and "：是【" in text and int(match.group(1)) == run["day"] and run["phase"] in {"scouting", "awaiting_action", "acting", "acted"}:
            run["phase"] = "sailing"
            run["deadline"] = time.time() + 30
            ctx.save()
            return True
        if sent and run.get("action") and sent.get("day") == run["day"] and sent["command"] == "." + run["action"]:
            member_id = sent["identity_id"]
            if member_id not in run.get("action_sent", []):
                return True
            if text.startswith("你选择【" + run["action"] + "】"):
                if member_id not in run.setdefault("action_confirmed", []):
                    run["action_confirmed"].append(member_id)
                    ctx.save()
            elif text in {"你不在任何猎妖船上。", "现在还没遇上海况。"}:
                if member_id not in run.setdefault("action_rejected", []):
                    run["action_rejected"].append(member_id)
                    ctx.save()
                    await _notice(ctx, run, f"第{run['day']}日成员 {member_id} 的 .{run['action']} 未被接受：{text}（不自动补发）")
            return True
        if sent and run["phase"] in {"opening", "recruiting", "sailing"} and text and not text.startswith("你投了"):
            await _fail(ctx, run, "游戏拒绝猎妖指令：" + text, definitive=run["phase"] == "opening")
            return True
        ctx.save()
        return True


async def scheduler(ctx, now):
    for run in list(ctx.runs().values()):
        if run.get("phase") not in ACTIVE_PHASES:
            continue
        async with _lock(run["name"]):
            if float(run.get("expires_at", 0)) <= now:
                await _fail(ctx, run, "本次猎妖已超时，自动操作停止")
            elif run["phase"] in {"scouting", "awaiting_action"} and now >= run["deadline"]:
                run["phase"] = "acted"
                ctx.save()
                await _notice(ctx, run, f"第{run['day']}日等待探路/兜底动作已超时，未向其他成员补发，等待游戏结算")
            elif run["phase"] in {"opening", "recruiting", "sailing", "voting"} and now > run["deadline"] + 15:
                await _fail(ctx, run, "未收到预期游戏阶段确认，本次自动操作停止")
