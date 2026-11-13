"""Receipt-driven weekly adventures. No purchases, guessed retries, or hidden APIs."""
import asyncio
import itertools
import re
import time

from .hunt_weekly import week_key

KINDS = {
    "tianlan": {"label": "天澜草原", "open": ".天澜草原", "join": ".同行", "minimum": 3, "realm": "元婴初期"},
    "yuejing": {"label": "越京夜行", "open": ".夜探越京", "join": ".同往", "minimum": 3, "realm": "筑基初期"},
    "yinming": {"label": "阴冥之地", "open": ".阴冥之地", "join": ".同入阴冥", "minimum": 2, "realm": "元婴初期"},
}
ACTIVE = {"opening", "joining", "running", "held", "interrupted"}
_LOCK = asyncio.Lock()
_NAMES = re.compile(r"@([A-Za-z0-9_]{3,32})")


def yinming_path(text, *, known_dead=None, allow_risk=False):
    """Enumerate all clue-consistent arrangements; only choose a provably safe road."""
    clues = re.search(r"辨路者[：:](.*?)。感魂者[：:](.*?)。", text)
    if not clues:
        return ""
    dead_clue, ghost_clue = clues.groups()
    roads = "左中右"

    def dead_ok(dead, ghost):
        if dead_clue == "死路在阴魂的右手边":
            return dead > ghost
        if dead_clue == "死路在阴魂的左手边":
            return dead < ghost
        match = re.fullmatch(r"死路不在([左中右])边", dead_clue)
        return bool(match and dead != roads.index(match[1]))

    def ghost_ok(ghost):
        if ghost_clue in {"阴魂在两侧的某一条路上", "阴魂不在中边"}:
            return ghost != 1
        if ghost_clue == "阴魂守在正中那条路上":
            return ghost == 1
        match = re.fullmatch(r"阴魂不在([左中右])边", ghost_clue)
        return bool(match and ghost != roads.index(match[1]))

    possibilities = [(dead, ghost, safe) for dead, ghost, safe in itertools.permutations(range(3))
                     if dead_ok(dead, ghost) and ghost_ok(ghost)
                     and (known_dead is None or roads[dead] == known_dead)]
    safe = {option[2] for option in possibilities}
    if len(safe) == 1:
        return roads[safe.pop()]
    if allow_risk and possibilities:
        # Some official clues allow two safe-road candidates. Avoid every
        # possible ghost road; the remaining uncertainty costs at most 1 yang.
        choices = [road for road in range(3) if all(ghost != road for _dead, ghost, _safe in possibilities)]
        if choices:
            return roads[min(choices, key=lambda road: sum(dead == road for dead, _ghost, _safe in possibilities))]
    return ""


def panel(kind, text, members, *, path_dead=None):
    """Return a monotonic stage and exact actions, grounded in public game wording."""
    ids = [m["identity_id"] for m in members]
    if kind == "yuejing":
        match = re.search(r"第\s*([1-3])/3\s*轮(?:查访)?[：:]", text)
        if match and ".查访" in text:
            # A talisman bypasses feints, market reveals elements, banquet lowers alert.
            place = {1: "城南道观", 2: "散修集市", 3: "秦府夜宴"}[int(match[1])]
            return int(match[1]), 60, [(i, ".查访 " + place) for i in ids]
        if "【越京夜行·闯宫】" in text:
            guards = re.findall(r"(青纹|铁罗|冰妖|叶蛇)[（(]([金木水火土])[）)]", text)
            if len(guards) != 4 or len(ids) < 4:
                return 4, 90, []
            counter = {"木": "金", "土": "木", "水": "土", "火": "水", "金": "火"}
            # Cover each guard; optimize available roots without assuming raw strength.
            best = max(itertools.permutations(members, 4), key=lambda assignment: sum(
                counter[element] in str(member.get("linggen") or "")
                for member, (_guard, element) in zip(assignment, guards)))
            actions = [(member["identity_id"], ".迎战 " + guard)
                       for member, (guard, _element) in zip(best, guards)]
            assigned = {i for i, _ in actions}
            actions += [(i, ".迎战 " + guards[-1][0]) for i in ids if i not in assigned]
            return 4, 90, actions
        match = re.search(r"第\s*([1-5])/5\s*回合.*?真凰符\s*(\d+)\s*张\n征兆[：:]([^\n]+)", text, re.S)
        if match and ".破幕 / .闪身 / .强攻" in text:
            clue = match[3]
            # Spend the two talismans on the deliberately ambiguous feints.
            # Remaining explicit telegraphs can be answered without guessing.
            action = next((a for phrase, a in (
                ("血雾向教主身前聚拢", "破幕"), ("十指暴长", "闪身"),
                ("仰头张口", "强攻"), ("双手结印", "强攻"),
                ("厚茧", "闪身"), ("影子忽然拉长", "破幕"),
            ) if phrase in clue), "")
            if int(match[2]) > 0 and (not action or "厚茧" in clue or "影子忽然拉长" in clue):
                return 4 + int(match[1]), 45, [(ids[0], ".真凰符")]
            # The wall can be a feint too. Use a talisman when one is available;
            # after both are gone, retain the explicit base-action fallback.
            if "一面墙" in clue and int(match[2]) > 0:
                return 4 + int(match[1]), 45, [(ids[0], ".真凰符")]
            return 4 + int(match[1]), 45, [(i, "." + action) for i in ids] if action else []
    elif kind == "tianlan":
        match = re.search(r"【天澜草原·第([1-3])站】", text)
        if match:
            weak = any(s in text for s in ("只拴着两匹马", "只有一个仙师"))
            strong = any(s in text for s in ("少说十几骑", "钟声一阵紧过一阵"))
            action = "斩哨" if weak else "潜行" if strong else ""
            return int(match[1]), 90, [(i, "." + action) for i in ids] if action else []
        if "【天澜草原·圣女追杀】" in text:
            # With three people, two remain to fight after the lure.
            return 4, 60, [(ids[-1], ".诱敌")]
        match = re.search(r"【以鼎收兽·第(\d+)/(\d+)回合】([^\n]+)", text)
        if match:
            clue = match[3]
            action = next((a for phrase, a in (
                ("地底钻", "封地"), ("后腿绷", "闪避"), ("长啸", "稳心"),
                ("仰起头", "稳心"), ("喉", "稳心"),
            ) if phrase in clue), "")
            return 4 + int(match[1]), 60, [(i, "." + action) for i in ids[:-1]] if action else []
    else:
        match = re.search(r"【阴冥 第([1-6])/6步】", text)
        if match:
            road = yinming_path(text, known_dead=path_dead, allow_risk=True)
            return int(match[1]), 120, [(ids[0], ".往 " + road)] if road else []
        match = re.search(r"【暴风山 第([1-3])/3招】", text)
        if match:
            clue = text.split("辨路者看温天仁的起手", 1)[-1].split("\n", 1)[0]
            moves = re.findall(r"「(沉肩|扫腿|虚晃)」", clue)
            # Official clues can leave two valid moves. Choose one documented
            # counter rather than timing out (which also loses yang).
            action = {"沉肩": "下压", "扫腿": "不动", "虚晃": "侧闪"}.get(moves[0] if moves else "", "")
            return 6 + int(match[1]), 120, [(ids[1], ".应招 " + action)] if action else []
    return None


def terminal(kind, text):
    return any(marker in text for marker in {
        "tianlan": ("【天澜草原】收兽而归", "【天澜草原】败退而归"),
        "yuejing": ("【越京夜行·战果】",),
        "yinming": ("【走出阴冥】", "【困于阴冥】"),
    }[kind])


def names(text):
    return {name.lower() for name in _NAMES.findall(text)}


def _ours(run, text):
    expected = {m["username"].lower() for m in run["members"]}
    return expected and expected.issubset(names(text))


def _route(ctx, run, event):
    return (getattr(event, "chat_id", 0) == run["chat_id"]
            and ctx.topic(event) == run["topic_id"]
            and ctx.event_time(event) >= run["started_at"] - 1)


def observe(ctx, event):
    """Invalidate obsolete queued actions before awaiting the reducer lock."""
    text = str(getattr(event, "raw_text", "") or "")
    for kind, run in ctx.records().get("runs", {}).items():
        if run.get("phase") not in ACTIVE or not _route(ctx, run, event):
            continue
        foreign = (KINDS[kind]["label"] in text and any(s in text for s in
                   ("要横穿", "要夜探", "立在鬼雾", "一行进了草原", "潜入越国京城", "（辨路）"))
                   and names(text) and not names(text).issubset({m["username"].lower() for m in run["members"]}))
        if foreign:
            run["collision"] = True
        if terminal(kind, text) and _ours(run, text):
            run["terminal_pending"] = True
        stage = panel(kind, text, run["members"])
        if stage and run.get("bound"):
            run["observed_stage"] = max(int(run.get("observed_stage") or 0), stage[0])


async def hold(ctx, run, reason):
    run.update(phase="held", requires_reconciliation=True, last_error=reason)
    ctx.save()
    await ctx.notice(f"{KINDS[run['kind']]['label']}已暂停：{reason}，保留原记录待核对。\n"
                     f"核对游戏已结束后：.周常 确认结束 {run['kind']} {run['id']}")


async def confirm_finished(ctx, kind, run_id):
    async with _LOCK:
        run = ctx.records().get("runs", {}).get(kind, {})
        if run.get("phase") not in {"held", "interrupted"} or run.get("id") != run_id:
            return False, "须为待核对记录，并填写准确记录号"
        run.update(phase="operator_confirmed", requires_reconciliation=False, operator_confirmed_at=time.time(), next_at=time.time() + 300)
        ctx.save()
        return True, "已记录人工确认结束，保留原动作记录；五分钟后重新安排"


async def send(ctx, run, identity_id, command, stage, deadline):
    key = f"{stage}:{identity_id}:{command}"
    if key in run.setdefault("attempts", {}):
        return False  # Unknown and definitely-unsent mutations are never replayed.

    def allowed():
        return (ctx.enabled(run["kind"]) and ctx.records()["runs"].get(run["kind"]) is run
                and run.get("phase") in {"opening", "joining", "running"}
                and not run.get("collision") and not run.get("terminal_pending")
                and int(run.get("observed_stage") or 0) <= stage
                and time.time() < deadline and ctx.available(identity_id)
                and ctx.target() == (run["chat_id"], run["topic_id"]))

    if not allowed():
        return False
    attempt = {"status": "unknown", "identity_id": identity_id, "command": command, "stage": stage, "at": time.time()}
    run["attempts"][key] = attempt
    ctx.save()
    message = await ctx.send(command, send_as_id=identity_id, send_guard=allowed, deadline=deadline,
                             op_id=f"weekly:{run['kind']}:{run['id']}:{key}",
                             chain_id=f"weekly:{run['kind']}:{run['id']}:{stage}", reactive=stage > 0)
    mid = int(getattr(message, "id", 0) or 0)
    attempt.update(status="sent" if mid else "unknown", msg_id=mid)
    if not mid and ctx.unsent(identity_id, command):
        attempt["status"] = "unsent"
    ctx.save()
    return bool(mid)


async def scheduler(ctx, now):
    async with _LOCK:
        records = ctx.records()
        for kind, run in records.setdefault("runs", {}).items():
            if run.get("phase") in {"opening", "joining", "running"} and now > run["deadline"] + 45:
                await hold(ctx, run, "未收到下一阶段或结算回执，不重新开局")
        if any(r.get("phase") in ACTIVE for r in records["runs"].values()):
            return
        for kind, spec in KINDS.items():
            if not ctx.enabled(kind):
                continue
            progress = records.setdefault("quotas", {}).setdefault(kind, {})
            members = ctx.members(kind)
            eligible = [m for m in members if ctx.available(m["identity_id"])]
            unfinished = [m for m in eligible if progress.get(str(m["identity_id"]), {}).get("week") != week_key(now)
                          or int(progress[str(m["identity_id"])].get("used") or 0) < 2]
            old = records["runs"].get(kind, {})
            if float(old.get("next_at") or 0) > now or not unfinished:
                continue
            if kind == "yinming":
                chosen = (unfinished + [m for m in eligible if m not in unfinished])[:2]
            else:
                chosen = eligible[:5]
            if len(chosen) < spec["minimum"] or (kind == "yuejing" and len(chosen) < 4):
                continue
            chat, topic = ctx.target()
            if not chat:
                continue
            run = {"id": str(int(now * 1000)), "kind": kind, "members": chosen, "started_at": now,
                   "chat_id": chat, "topic_id": topic, "phase": "opening", "deadline": now + 120,
                   "stage": 0, "observed_stage": 0, "bound": False, "attempts": {}}
            records["runs"][kind] = run
            ctx.save()
            if not await send(ctx, run, chosen[0]["identity_id"], spec["open"], 0, now + 40):
                await hold(ctx, run, "开局发送未获确认")
            return


async def game_event(ctx, event):
    if getattr(event, "sender_id", 0) not in ctx.bot_ids():
        return False
    observe(ctx, event)
    async with _LOCK:
        text = str(getattr(event, "raw_text", "") or "")
        reply = int(getattr(event, "reply_to_msg_id", 0) or 0)
        for kind, run in ctx.records().get("runs", {}).items():
            if run.get("phase") not in ACTIVE or not _route(ctx, run, event):
                continue
            attempts = run.get("attempts", {}).values()
            owned = next((a for a in attempts if reply and a.get("msg_id") == reply), None)
            if terminal(kind, text) and _ours(run, text):
                for member in run["members"]:
                    line = next((line for line in text.splitlines() if re.match(
                        "@" + re.escape(member["username"]) + r"(?:[：:\s])", line, re.I)), "")
                    if not line or not ("本周有奖次数已满" in line or "修为+" in line):
                        await hold(ctx, run, "结算缺少成员奖励明细")
                        return True
                quotas = ctx.records().setdefault("quotas", {}).setdefault(kind, {})
                for member in run["members"]:
                    key = str(member["identity_id"])
                    old = quotas.get(key, {})
                    used = int(old.get("used") or 0) if old.get("week") == week_key(run["started_at"]) else 0
                    line = next(line for line in text.splitlines() if re.match(
                        "@" + re.escape(member["username"]) + r"(?:[：:\s])", line, re.I))
                    quotas[key] = {"week": week_key(run["started_at"]), "used": 2 if "本周有奖次数已满" in line else min(2, used + 1),
                                   "source_msg_id": int(getattr(event, "id", 0)), "at": ctx.event_time(event)}
                run.update(phase="done", result=text[:4000], completed_at=ctx.event_time(event), requires_reconciliation=False,
                           next_at=time.time() + 300)
                receipt_key = f"{getattr(event, 'chat_id', 0)}:{getattr(event, 'id', 0)}"
                evidence = ctx.records().setdefault("receipt_keys", [])
                if receipt_key not in evidence:
                    evidence.append(receipt_key)
                ctx.records()["receipt_keys"] = evidence[-200:]
                ctx.save()
                await ctx.notice(text[:1800])
                return True
            if run.get("collision"):
                if run.get("phase") != "held":
                    await hold(ctx, run, "同群出现其他同类队伍，无法可靠归属无房号面板")
                continue
            if run.get("phase") in {"held", "interrupted"}:
                continue
            if kind == "yinming" and run.get("bound"):
                failed = re.search(r"@([A-Za-z0-9_]+) 选了([左中右])边——一头撞上死路", text)
                if (failed and failed[1].lower() == run["members"][0]["username"].lower()
                        and 1 <= run.get("stage", 0) <= 6 and any(
                            a.get("command") == ".往 " + failed[2] and a.get("stage") == run["stage"] and a.get("status") == "sent"
                            for a in run["attempts"].values())):
                    run.setdefault("path_dead", {})[str(run["stage"])] = failed[2]
                    run["retry_step"] = run["stage"]
                    ctx.save()
            spec = KINDS[kind]
            recruit = (spec["label"] in text and spec["join"] in text
                       and names(text) == {run["members"][0]["username"].lower()})
            own_open = owned and owned["command"] == spec["open"]
            sent_open = any(a.get("command") == spec["open"] and a.get("status") == "sent" for a in attempts)
            if run["phase"] == "opening" and (own_open or recruit and sent_open):
                if own_open and kind == "tianlan" and text == "你在草原边上等同行的人。":
                    run["open_acknowledged"] = True
                    ctx.save()
                    return True  # The separately broadcast named recruitment owns the join chain.
                if not recruit:
                    await hold(ctx, run, "游戏未确认本队招募：" + text[:250])
                    return True
                run.update(phase="joining", deadline=ctx.event_time(event) + (300 if kind == "yuejing" else 120))
                ctx.save()
                for member in run["members"][1:]:
                    if not await send(ctx, run, member["identity_id"], spec["join"], 0, run["deadline"] - 3):
                        await hold(ctx, run, "入队发送未获确认")
                        break
                return True
            start_marker = {"tianlan": "一行进了草原", "yuejing": "潜入越国京城", "yinming": "（辨路）"}[kind]
            if start_marker in text and _ours(run, text):
                run.update(phase="running", bound=True, deadline=ctx.event_time(event) + 130)
                ctx.save()
            # Yuejing with four eligible members needs explicit entry; five starts automatically.
            if kind == "yuejing" and len(run["members"]) < 5 and run["phase"] == "joining" and owned and "加入了队伍" in text:
                listed = names(text)
                if _ours(run, text) and len(listed) == len(run["members"]):
                    await send(ctx, run, run["members"][0]["identity_id"], ".入城", 0, run["deadline"] - 3)
            if run.get("bound"):
                step = re.search(r"【阴冥 第([1-6])/6步】", text) if kind == "yinming" else None
                stage = panel(kind, text, run["members"], path_dead=run.get("path_dead", {}).get(step[1]) if step else None)
                if stage and (stage[0] > int(run.get("stage") or 0) or stage[0] == run.get("retry_step")):
                    number, seconds, actions = stage
                    if not actions:
                        await hold(ctx, run, "本轮线索无法确定安全动作")
                        return True
                    run.update(stage=number, deadline=ctx.event_time(event) + seconds - 3)
                    run.pop("retry_step", None)
                    ctx.save()
                    for identity, command in actions:
                        if not await send(ctx, run, identity, command, number, run["deadline"]):
                            if not run.get("terminal_pending") and run.get("observed_stage", 0) <= number:
                                await hold(ctx, run, "本轮动作未获发送确认，不补发旧轮")
                            break
                    return True
        return False
