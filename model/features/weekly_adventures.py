"""Receipt-driven weekly adventures. No purchases, guessed retries, or hidden APIs."""
import asyncio
import itertools
import re
import time
from datetime import datetime, timedelta, timezone

from .hunt_weekly import week_key
from . import council

KINDS = {
    "tianlan": {"label": "天澜草原", "open": ".天澜草原", "join": ".同行", "minimum": 3, "realm": "元婴初期"},
    "yuejing": {"label": "越京夜行", "open": ".夜探越京", "join": ".同往", "minimum": 3, "realm": "筑基初期"},
    "yinming": {"label": "阴冥之地", "open": ".阴冥之地", "join": ".同入阴冥", "minimum": 2, "realm": "元婴初期"},
    "qipai": {"label": "七派密议", "open": ".开密议", "join": ".入议", "minimum": 3, "realm": None},
}
ACTIVE = {"opening", "joining", "running", "dissolving", "held", "interrupted"}
START_MARKERS = {"tianlan": "一行进了草原", "yuejing": "潜入越国京城", "yinming": "（辨路）", "qipai": "【七派密议·开议】"}
_LOCK = asyncio.Lock()
_NAMES = re.compile(r"@([^\s，。、：:；;（）()【】!?！？]+)")
PARTIES = {"tianlan": r"【天澜草原】([^\n]+?)\s+一行进了草原",
           "yuejing": r"【越京夜行】[^\n]*?夜色里，([^\n]+?)\s+潜入越国京城",
           "yinming": r"【阴冥之地】([^\n]+?)\s+不见了踪影"}


def departure(run, text):
    match = re.search(PARTIES[run["kind"]], text) if run["kind"] in PARTIES else None
    if not match:
        return None
    listed = [name.lower() for name in _NAMES.findall(match[1])]
    captain = run.get("planned_members", run["members"])[0]["username"].lower()
    return listed if captain in listed and len(set(listed)) == len(listed) else None


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


def panel(kind, text, members, *, path_dead=None, roles=None, decoys=None, observations=None, route=None, use_talisman=True):
    """Return a monotonic stage and exact actions, grounded in public game wording."""
    ids = [m["identity_id"] for m in members]
    if kind == "qipai":
        return council.panel(text, members, observations or [])
    if kind == "yuejing":
        match = re.search(r"第\s*([1-3])/3\s*轮(?:查访)?[：:]", text)
        if match and ".查访" in text:
            # A talisman bypasses feints, market reveals elements, banquet lowers alert.
            place = (route or ["城南道观", "散修集市", "秦府夜宴"])[int(match[1]) - 1]
            return int(match[1]), 60, [(i, ".查访 " + place) for i in ids]
        if "【越京夜行·闯宫】" in text:
            guards = re.findall(r"(青纹|铁罗|冰妖|叶蛇)[（(]([金木水火土])[）)]", text)
            if len(guards) != 4 or not ids:
                return 4, 90, []
            counter = {"木": "金", "土": "木", "水": "土", "火": "水", "金": "火"}
            if len(ids) < 4:
                labels = [guard for guard, _element in guards]
                elements = dict(guards)
                assignments = max(itertools.product(labels, repeat=len(members)), key=lambda assigned: (
                    len(set(assigned)), sum(counter[elements[guard]] in str(member.get("linggen") or "")
                                            for member, guard in zip(members, assigned))))
                return 4, 90, [(member["identity_id"], ".迎战 " + guard)
                               for member, guard in zip(members, assignments)]
            # Cover each guard; optimize available roots without assuming raw strength.
            best = max(itertools.permutations(members, 4), key=lambda assignment: sum(
                counter[element] in str(member.get("linggen") or "")
                for member, (_guard, element) in zip(assignment, guards)))
            actions = [(member["identity_id"], ".迎战 " + guard)
                       for member, (guard, _element) in zip(best, guards)]
            assigned = {i for i, _ in actions}
            # Reinforce the uncovered element instead of duplicating an
            # already countered guard while another solo fighter escapes.
            support = min(zip(best, guards), key=lambda pair: counter[pair[1][1]] in str(pair[0].get("linggen") or ""))[1][0]
            actions += [(i, ".迎战 " + support) for i in ids if i not in assigned]
            return 4, 90, actions
        match = re.search(r"第\s*([1-5])/5\s*回合.*?真凰符\s*(\d+)\s*张\n征兆[：:]([^\n]+)", text, re.S)
        if match and ".破幕 / .闪身 / .强攻" in text:
            clue = match[3]
            # These are base telegraphs, not guaranteed answers: real logs
            # show feints even for the wall and open-mouth clues.
            action = next((a for phrase, a in (
                ("血雾向教主身前聚拢", "破幕"), ("十指暴长", "闪身"),
                ("仰头张口", "强攻"), ("双手结印", "强攻"),
                ("厚茧", "破幕"), ("影子忽然拉长", "闪身"),
            ) if phrase in clue), "")
            health = re.search(r"教主\s*(\d+)\s*/\s*\d+", text)
            talismans = int(match[2])
            secured = health and int(health[1]) <= len(ids) * talismans
            if use_talisman and talismans > 0 and (secured or not action or any(p in clue for p in (
                    "厚茧", "影子忽然拉长", "双手结印", "一面墙", "仰头张口"))):
                return 4 + int(match[1]), 45, [(ids[0], ".真凰符")]
            return 4 + int(match[1]), 45, [(i, "." + action) for i in ids] if action else []
    elif kind == "tianlan":
        match = re.search(r"【天澜草原·第([1-3])站】", text)
        if match:
            weak = any(s in text for s in ("只拴着两匹马", "只有一个仙师"))
            action = "斩哨" if weak else "潜行"
            return int(match[1]), 90, [(i, "." + action) for i in ids] if ".潜行" in text else []
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
            fighters = ids[:-1] if decoys is None else [i for i in ids if i not in decoys]
            return 4 + int(match[1]), 60, [(i, "." + action) for i in fighters] if action else []
    else:
        match = re.search(r"【阴冥 第([1-6])/6步】", text)
        if match:
            road = yinming_path(text, known_dead=path_dead, allow_risk=True)
            actor = (roles or {}).get("辨路", ids[0])
            return int(match[1]), 120, [(actor, ".往 " + road)] if road and actor is not None else []
        match = re.search(r"【暴风山 第([1-3])/3招】", text)
        if match:
            clue = text.split("辨路者看温天仁的起手", 1)[-1].split("\n", 1)[0]
            moves = re.findall(r"「(沉肩|扫腿|虚晃)」", clue)
            # Official clues can leave two valid moves. Choose one documented
            # counter rather than timing out (which also loses yang).
            pairs = re.findall(r"(沉肩|扫腿|虚晃)\s*→\s*(侧闪|下压|不动)", text)
            counters = dict(pairs)
            complete = (len(pairs) == 3 and set(counters) == {"沉肩", "扫腿", "虚晃"}
                        and set(counters.values()) == {"侧闪", "下压", "不动"})
            action = counters.get(moves[0] if moves else "", "") if complete else ""
            actor = (roles or {}).get("感魂", ids[-1])
            return 6 + int(match[1]), 120, [(actor, ".应招 " + action)] if action and actor is not None else []
    return None


def terminal(kind, text):
    return any(marker in text for marker in {
        "tianlan": ("【天澜草原】收兽而归", "【天澜草原】败退而归"),
        "yuejing": ("【越京夜行·战果】",),
        "yinming": ("【走出阴冥】", "【困于阴冥】"),
        "qipai": ("【七派密议·散议】",),
    }[kind])


def names(text):
    return {name.lower() for name in _NAMES.findall(text)}


def reward_line(username, text):
    return next((line for line in text.splitlines()
                 if re.match("@" + re.escape(username) + r"(?:[：:\s])", line, re.I)
                 and ("本周有奖次数已满" in line or "修为+" in line)), "")


def _ours(run, text):
    expected = set(run.get("party_names") or [m["username"].lower() for m in run["members"]])
    return expected and expected.issubset(names(text))


def period(kind, now):
    return datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime("%Y-%m-%d") if kind == "qipai" else week_key(now)


def quota_limit(kind):
    return 3 if kind == "qipai" else 2


def _terminal_owned(run, event, text):
    if run["kind"] != "qipai":
        return _ours(run, text)
    count = re.search(r"(\d+)\s*位长老各得修为", text)
    linked = _reply(event) and _reply(event) != run["topic_id"] and (
        any(a.get("msg_id") == _reply(event) for a in run.get("attempts", {}).values())
        or _reply(event) in run.get("known_message_ids", []))
    return (run.get("bound") and not run.get("collision") and _owned(run, event, text)
            and (linked or _ours(run, text)) and count and int(count[1]) == len(run["members"]))


def _route(ctx, run, event):
    return (getattr(event, "chat_id", 0) == run["chat_id"]
            and ctx.topic(event) == run["topic_id"]
            and ctx.event_time(event) >= run["started_at"] - 1)


def _reply(event):
    return int(getattr(event, "reply_to_msg_id", 0) or
               getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)


def _owned(run, event, text):
    """Port upstream's reply-chain boundary without weakening local collision holds."""
    reply = _reply(event)
    if reply and reply != run["topic_id"]:
        return (any(a.get("msg_id") == reply for a in run.get("attempts", {}).values())
                or reply in run.get("known_message_ids", []))
    return not names(text) or names(text).issubset({m["username"].lower() for m in run["members"]}) or _ours(run, text)


def _decoy(run, event, text):
    if run["kind"] != "tianlan" or not run.get("bound"):
        return
    owned = next((a for a in run.get("attempts", {}).values() if a.get("msg_id") == _reply(event)), None)
    actor = None
    if owned and ((owned["command"] == ".诱敌" and "你引开了两位仙师。" in text)
                  or "你正被两位仙师追着跑，顾不上这边。" in text):
        actor = owned["identity_id"]
    if "【诱敌】" in text and "两位大仙师果然追了上去" in text:
        actor = next((m["identity_id"] for m in run["members"] if m["username"].lower() in names(text)), actor)
    if actor is not None and actor not in run.setdefault("decoy_ids", []):
        run["decoy_ids"].append(actor)


def _council_roster(run, event, text):
    match = re.search(r"@([^\s，。、：:；;（）()【】]+) 入议，现有\s*(\d+)\s*位长老：([^\n]+)", text)
    if run["kind"] != "qipai" or not match or run.get("bound") or run["phase"] != "joining":
        return
    listed = _NAMES.findall(match[3])
    listed = [name.lower() for name in listed]
    known = any(a.get("msg_id") == _reply(event) for a in run["attempts"].values())
    if not (known or listed[:1] == [run["members"][0]["username"].lower()]):
        return
    mid = int(getattr(event, "id", 0) or 0)
    if mid < run.get("roster_message_id", 0):
        return
    expected = {m["username"].lower() for m in run["members"]}
    confirmed = set(run.get("roster_names", []))
    run["roster_message_id"] = mid
    if (int(match[2]) != len(listed) or len(set(listed)) != len(listed)
            or not set(listed).issubset(expected) or not confirmed.issubset(listed)
            or match[1].lower() not in listed):
        run["roster_mismatch"] = True
    else:
        run["roster_names"] = listed


def observe(ctx, event):
    """Invalidate obsolete queued actions before awaiting the reducer lock."""
    text = str(getattr(event, "raw_text", "") or "")
    if getattr(event, "sender_id", 0) not in ctx.bot_ids() or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None):
        return
    for kind, run in ctx.records().get("runs", {}).items():
        if not getattr(ctx, "accept_run", lambda r: True)(run):
            continue
        kind = run["kind"]
        if kind == "qipai" and run.get("phase") not in ACTIVE and _route(ctx, run, event):
            foreign_start = ("【七派密议】" in text and "召集密议" in text
                             or "【七派密议·开议】" in text)
            if foreign_start and names(text) and not names(text).issubset({m["username"].lower() for m in run["members"]}):
                run.update(foreign_busy=True, next_at=max(float(run.get("next_at") or 0), time.time() + 900))
                ctx.save()
            if run.get("foreign_busy") and terminal(kind, text):
                # A foreign result only schedules a future opening attempt. It
                # never confirms our run, claims rewards or grants occupancy.
                run.update(foreign_busy=False, next_at=max(float(run.get("cooldown_until") or run.get("completed_at", 0) + 300), time.time() + 60))
                ctx.save()
        if run.get("phase") not in ACTIVE or not _route(ctx, run, event):
            continue
        _council_roster(run, event, text)
        party = departure(run, text)
        if party and not run.get("bound"):
            run["departure_pending"] = True
        complete_start = bool(party) or START_MARKERS[kind] in text and (_ours(run, text) or kind == "qipai" and any(
            a.get("command") == ".开议" and a.get("msg_id") == _reply(event) for a in run.get("attempts", {}).values()))
        foreign = (not complete_start and KINDS[kind]["label"] in text and any(s in text for s in
                   ("要横穿", "要夜探", "立在鬼雾", "一行进了草原", "潜入越国京城", "（辨路）", "召集密议", "【七派密议·开议】"))
                   and names(text) and not names(text).issubset({m["username"].lower() for m in run["members"]}))
        if foreign:
            run["collision"] = True
            run["collision_names"] = sorted(names(text))
            run["collision_message_id"] = int(getattr(event, "id", 0) or 0)
        if terminal(kind, text) and _terminal_owned(run, event, text):
            run["terminal_pending"] = True
        if not _owned(run, event, text):
            continue
        _decoy(run, event, text)
        stage = panel(kind, text, run["members"])
        if stage and run.get("bound"):
            run["observed_stage"] = max(int(run.get("observed_stage") or 0), stage[0])


async def hold(ctx, run, reason):
    run.update(phase="held", requires_reconciliation=True, last_error=reason)
    ctx.save()
    await ctx.notice(f"{KINDS[run['kind']]['label']}已暂停：{reason}，保留原记录待核对。\n"
                     f"核对游戏已结束后：.周常 确认结束 {run.get('key', run['kind'])} {run['id']}")


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
    previous = run.setdefault("attempts", {}).get(key)
    if previous:
        if previous["status"] == "sent":
            return True
        if (previous["status"] != "unsent" or time.time() < previous.get("retry_at", 0)
                or int(previous.get("tries", 1)) >= 3):
            return False  # Never replay an unknown mutation.

    def allowed():
        dismiss = (command == ".散议" and run["kind"] == "qipai"
                   and (run.get("roster_mismatch") or run.get("cancel_unstarted")) and not run.get("bound"))
        return (ctx.enabled(run["kind"]) and ctx.records()["runs"].get(run.get("key", run["kind"])) is run
                and (run.get("phase") in {"opening", "joining", "running"} or dismiss and run.get("phase") == "dissolving")
                and (dismiss or not run.get("collision")) and not run.get("terminal_pending")
                and (dismiss or not run.get("roster_mismatch"))
                and not (run.get("departure_pending") and command in {KINDS[run["kind"]]["open"], KINDS[run["kind"]]["join"], ".入城"})
                and int(run.get("observed_stage") or 0) <= stage
                and time.time() < deadline and ctx.available(identity_id)
                and not (run["kind"] == "tianlan" and command in {".闪避", ".稳心", ".封地"}
                         and identity_id in run.get("decoy_ids", []))
                and ctx.target() == (run["chat_id"], run["topic_id"]))

    if not allowed():
        return False
    if run["kind"] == "qipai" and command == ".准":
        recent = [a.get("confirmed_at", a.get("at", 0)) for a in run["attempts"].values()
                  if a.get("status") == "sent" and a.get("identity_id") == identity_id and a.get("command") == command]
        wait = max(recent, default=0) + 11 - time.time()
        if wait > 0:
            await getattr(ctx, "sleep", asyncio.sleep)(wait)
            if not allowed():
                return False
    attempt = {"status": "unknown", "identity_id": identity_id, "command": command, "stage": stage, "at": time.time()}
    attempt["tries"] = int(previous.get("tries", 1)) + 1 if previous else 1
    if previous:
        attempt["previous"] = {k: v for k, v in previous.items() if k != "previous"}
    run["attempts"][key] = attempt
    ctx.save()
    message = await ctx.send(command, send_as_id=identity_id, send_guard=allowed, deadline=deadline,
                             op_id=f"weekly:{run['kind']}:{run['id']}:{key}",
                             chain_id=f"weekly:{run['kind']}:{run['id']}:{stage}",
                             reactive=stage > 0 or command in {KINDS[run["kind"]]["join"], ".开议"})
    mid = int(getattr(message, "id", 0) or 0)
    attempt.update(status="sent" if mid else "unknown", msg_id=mid, confirmed_at=time.time())
    if not mid and ctx.unsent(identity_id, command):
        attempt["status"] = "unsent"
        attempt["retry_at"] = time.time() + 15
    ctx.save()
    return bool(mid)


async def dispatch(ctx, run, actions=None, *, stage=None, deadline=None):
    """Continue only proven-unsent actions within their original live window."""
    if actions is not None:
        run["pending_actions"] = {"actions": actions, "stage": stage, "deadline": deadline}
        ctx.save()
    plan = run.get("pending_actions")
    if not plan or run.get("phase") not in {"opening", "joining", "running", "dissolving"}:
        return
    number, expires = plan["stage"], plan["deadline"]
    if run.get("terminal_pending") or run.get("observed_stage", 0) > number:
        run.pop("pending_actions", None)
        ctx.save()
        return
    for identity, command in plan["actions"]:
        if identity in run.get("decoy_ids", []) and command in {".闪避", ".稳心", ".封地"}:
            continue
        if await send(ctx, run, identity, command, number, expires):
            continue
        attempt = run.get("attempts", {}).get(f"{number}:{identity}:{command}", {})
        if run.get("departure_pending") and number == 0:
            run.pop("pending_actions", None)
            ctx.save()
            return
        if (attempt.get("status") == "unsent" and int(attempt.get("tries", 1)) < 3
                and time.time() < expires):
            return
        if (run.get("phase") == "opening" and not run.get("bound") and run.get("attempts")
                and all(a.get("status") == "unsent" for a in run["attempts"].values())):
            run.update(phase="failed", requires_reconciliation=False, next_at=time.time() + 900,
                       last_error="已确认所有开局尝试均未发送，稍后重新安排")
            run.pop("pending_actions", None)
            ctx.save()
            return
        if not run.get("terminal_pending") and run.get("observed_stage", 0) <= number:
            await hold(ctx, run, "招募或入场名单出现其他玩家，停止后续入队与动作" if run.get("collision")
                       else "本轮动作未获发送确认，不补发旧轮")
        return
    run.pop("pending_actions", None)
    ctx.save()


async def dismiss_council(ctx, run):
    if run.get("bound") or run.get("phase") == "dissolving":
        return
    run.update(phase="dissolving", deadline=time.time() + 60, last_error="七派集结未完成，等待游戏确认散议")
    await dispatch(ctx, run, [(run["members"][0]["identity_id"], ".散议")], stage=0, deadline=run["deadline"])


async def scheduler(ctx, now):
    async with _LOCK:
        records = ctx.records()
        for kind, run in records.setdefault("runs", {}).items():
            if not getattr(ctx, "accept_run", lambda r: True)(run):
                continue
            if (run.get("kind") == "qipai" and run.get("phase") in {"held", "interrupted"}
                    and not run.get("bound") and int(run.get("observed_stage") or 0) == 0
                    and now > float(run.get("deadline") or now) + 45
                    and any(a.get("command") == ".开密议" and a.get("status") == "sent" and a.get("msg_id")
                            for a in run.get("attempts", {}).values())
                    and not any(a.get("command") in {".开议", ".散议"} for a in run.get("attempts", {}).values())):
                run["cancel_unstarted"] = True
                await dismiss_council(ctx, run)
            await dispatch(ctx, run)
            if run.get("phase") in {"opening", "joining", "running", "dissolving"} and now > run["deadline"] + 45:
                await hold(ctx, run, "未收到下一阶段或结算回执，不重新开局")
        if any(r.get("phase") in ACTIVE for r in records["runs"].values()):
            return
        for kind, spec in KINDS.items():
            if not ctx.enabled(kind):
                continue
            progress = records.setdefault("quotas", {}).setdefault(kind, {})
            members = ctx.members(kind)
            eligible = [m for m in members if ctx.available(m["identity_id"])]
            if kind == "qipai" and len(eligible) != len(members):
                continue  # Keep the configured council together while daily solo tasks finish.
            field = "day" if kind == "qipai" else "week"
            unfinished = [m for m in eligible if progress.get(str(m["identity_id"]), {}).get(field) != period(kind, now)
                          or int(progress[str(m["identity_id"])].get("used") or 0) < quota_limit(kind)]
            key = getattr(ctx, "run_key", lambda k: k)(kind)
            old = records["runs"].get(key, {})
            if float(old.get("next_at") or 0) > now or not unfinished:
                continue
            unfinished.sort(key=lambda m: int(progress.get(str(m["identity_id"]), {}).get("used") or 0)
                            if progress.get(str(m["identity_id"]), {}).get(field) == period(kind, now) else 0)
            maximum = 2 if kind == "yinming" else 8 if kind == "qipai" else 5
            chosen = (unfinished + [m for m in eligible if m not in unfinished])[:maximum]
            if len(chosen) < spec["minimum"]:
                continue
            chat, topic = ctx.target()
            if not chat:
                continue
            run = {"id": str(int(now * 1000)), "kind": kind, "members": chosen, "started_at": now,
                   "chat_id": chat, "topic_id": topic, "phase": "opening", "deadline": now + 120,
                   "stage": 0, "observed_stage": 0, "bound": False, "attempts": {}}
            run.update(key=key, team=getattr(ctx, "team_name", ""),
                       route=getattr(ctx, "route", None), use_talisman=getattr(ctx, "use_talisman", True))
            records["runs"][key] = run
            ctx.save()
            await dispatch(ctx, run, [(chosen[0]["identity_id"], spec["open"])], stage=0, deadline=now + 40)
            return


async def game_event(ctx, event):
    if (getattr(event, "sender_id", 0) not in ctx.bot_ids()
            or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None)):
        return False
    observe(ctx, event)
    async with _LOCK:
        text = str(getattr(event, "raw_text", "") or "")
        reply = _reply(event)
        for kind, run in ctx.records().get("runs", {}).items():
            if not getattr(ctx, "accept_run", lambda r: True)(run):
                continue
            kind = run["kind"]
            if run.get("phase") not in ACTIVE or not _route(ctx, run, event):
                continue
            attempts = run.get("attempts", {}).values()
            owned = next((a for a in attempts if reply and a.get("msg_id") == reply), None)
            party = departure(run, text)
            if (kind == "qipai" and owned and owned["command"] == ".开密议" and not run.get("bound")
                    and text.strip() == "这里已经有一场密议了。.密议 看局面，.入议 加入。"):
                run.update(phase="waiting_room", requires_reconciliation=False, foreign_busy=True,
                           next_at=time.time() + 900, cooldown_until=time.time() + 300,
                           last_error=None, result="别队密议进行中，等空房后自动继续")
                run.pop("pending_actions", None)
                ctx.save()
                return True
            if kind == "qipai" and owned and owned["command"] == ".散议" and text.strip() == "这一场密议散了。" and not run.get("bound"):
                retry_at = time.time() + 300
                if run.get("cancel_unstarted"):
                    local = datetime.fromtimestamp(time.time(), timezone(timedelta(hours=8)))
                    retry_at = (local + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).timestamp()
                run.update(phase="cancelled", requires_reconciliation=False, last_error=None, result=text,
                           completed_at=ctx.event_time(event), next_at=retry_at)
                run.pop("pending_actions", None)
                ctx.save()
                return True
            if kind == "qipai" and run.get("roster_mismatch") and not run.get("bound"):
                await dismiss_council(ctx, run)
                continue
            if terminal(kind, text) and _terminal_owned(run, event, text):
                receipt_key = f"{getattr(event, 'chat_id', 0)}:{getattr(event, 'id', 0)}"
                evidence = ctx.records().setdefault("receipt_keys", [])
                for member in run["members"]:
                    if kind != "qipai" and not reward_line(member["username"], text):
                        await hold(ctx, run, "结算缺少成员奖励明细")
                        return True
                quotas = ctx.records().setdefault("quotas", {}).setdefault(kind, {})
                for member in (() if receipt_key in evidence else run["members"]):
                    key = str(member["identity_id"])
                    old = quotas.get(key, {})
                    field = "day" if kind == "qipai" else "week"
                    settled_period = period(kind, ctx.event_time(event))
                    used = int(old.get("used") or 0) if old.get(field) == settled_period else 0
                    line = reward_line(member["username"], text)
                    quotas[key] = {field: settled_period, "used": quota_limit(kind) if "本周有奖次数已满" in line else min(quota_limit(kind), used + 1),
                                   "source_msg_id": int(getattr(event, "id", 0)), "at": ctx.event_time(event)}
                run.update(phase="done", result=text[:4000], completed_at=ctx.event_time(event), requires_reconciliation=False, last_error=None,
                           next_at=time.time() + 300, cooldown_until=time.time() + 300)
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
            if not party and not _owned(run, event, text):
                continue
            if not terminal(kind, text):
                known = run.setdefault("known_message_ids", [])
                mid = int(getattr(event, "id", 0) or 0)
                if mid and mid not in known:
                    known.append(mid)
            if party and not run.get("bound") and run.get("phase") in {"held", "interrupted"}:
                run.update(phase="joining", requires_reconciliation=False, last_error=None)
            if run.get("phase") in {"held", "interrupted"} and run.get("bound"):
                resumed = panel(kind, text, run["members"], roles=run.get("yinming_roles"), decoys=run.get("decoy_ids", []))
                uncertain = [a for a in run.get("attempts", {}).values()
                             if a.get("status") == "unknown" and int(a.get("stage", 0)) >= (resumed[0] if resumed else 0)]
                if (resumed and resumed[0] > run.get("stage", 0) and not uncertain
                        and ctx.event_time(event) + resumed[1] - 3 > time.time()):
                    run.update(phase="running", requires_reconciliation=False, last_error=None, resumed_at=time.time())
                    run.pop("pending_actions", None)
                    ctx.save()
            if run.get("phase") in {"held", "interrupted"}:
                continue
            if kind == "qipai":
                roster = re.search(r"入议，现有\s*(\d+)\s*位长老：([^\n]+)", text)
                if roster and run["phase"] == "joining":
                    listed = names(roster[2])
                    expected = {m["username"].lower() for m in run["members"]}
                    if listed == expected:
                        run["roster_confirmed"] = True
                        await dispatch(ctx, run, [(run["members"][0]["identity_id"], ".开议")], stage=0, deadline=run["deadline"])
                obs = council.observation(text)
                if obs and not any(o["round"] == obs["round"] for o in run.setdefault("observations", [])):
                    run["observations"].append(obs)
                    ctx.save()
            if kind == "yinming" and run.get("bound"):
                failed = re.search(r"@([A-Za-z0-9_]+) 选了([左中右])边——一头撞上死路", text)
                path_actor = run.get("yinming_roles", {}).get("辨路", run["members"][0]["identity_id"])
                path_name = next(m["username"].lower() for m in run["members"] if m["identity_id"] == path_actor)
                if (failed and failed[1].lower() == path_name
                        and 1 <= run.get("stage", 0) <= 6
                        and run.get("path_dead", {}).get(str(run["stage"])) != failed[2] and any(
                            a.get("command") == ".往 " + failed[2] and a.get("stage") == run["stage"] and a.get("status") == "sent"
                            and ctx.event_time(event) >= a.get("at", 0)
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
                await dispatch(ctx, run, [(m["identity_id"], spec["join"]) for m in run["members"][1:]],
                               stage=0, deadline=run["deadline"] - 3)
                return True
            council_started = (kind == "qipai" and run.get("roster_confirmed") and owned and owned["command"] == ".开议")
            if not run.get("bound") and START_MARKERS[kind] in text and (party or _ours(run, text) or council_started):
                if party and not run.get("bound"):
                    planned = run.setdefault("planned_members", run["members"])
                    run["members"] = [m for m in planned if m["username"].lower() in party]
                    run["party_names"] = party
                    run["missing_members"] = [m["username"] for m in planned if m not in run["members"]]
                    run.pop("pending_actions", None)
                if kind == "yinming":
                    by_name = {m["username"].lower(): m["identity_id"] for m in run["members"]}
                    assignments = re.findall(r"@([^\s，。、：:；;（）()【】!?！？@]+)[（(](辨路|感魂)[）)]", text)
                    roles = {role: by_name.get(name.lower()) for name, role in assignments}
                    if len(assignments) != 2 or set(roles) != {"辨路", "感魂"} or len({n.lower() for n, _ in assignments}) != 2:
                        await hold(ctx, run, "入场回执缺少两人实际辨路/感魂分工")
                        return True
                    run["yinming_roles"] = roles
                run.update(phase="running", bound=True, deadline=ctx.event_time(event) + 130)
                ctx.save()
            # Yuejing with four eligible members needs explicit entry; five starts automatically.
            if kind == "yuejing" and len(run["members"]) < 5 and run["phase"] == "joining" and owned and "加入了队伍" in text:
                listed = names(text)
                if _ours(run, text) and len(listed) == len(run["members"]):
                    await send(ctx, run, run["members"][0]["identity_id"], ".入城", 0, run["deadline"] - 3)
            if run.get("bound"):
                step = re.search(r"【阴冥 第([1-6])/6步】", text) if kind == "yinming" else None
                stage = panel(kind, text, run["members"], path_dead=run.get("path_dead", {}).get(step[1]) if step else None,
                              roles=run.get("yinming_roles"), decoys=run.get("decoy_ids", []), observations=run.get("observations", []),
                              route=run.get("route"), use_talisman=run.get("use_talisman", True))
                if stage and (stage[0] > int(run.get("stage") or 0) or stage[0] == run.get("retry_step")):
                    number, seconds, actions = stage
                    external_actor = (kind == "yinming" and run.get("yinming_roles", {}).get("辨路" if number <= 6 else "感魂", True) is None)
                    if not actions and not external_actor:
                        await hold(ctx, run, "本轮线索无法确定安全动作")
                        return True
                    run.update(stage=number, deadline=ctx.event_time(event) + seconds - 3)
                    run.pop("retry_step", None)
                    ctx.save()
                    await dispatch(ctx, run, actions, stage=number, deadline=run["deadline"])
                    return True
        return False
