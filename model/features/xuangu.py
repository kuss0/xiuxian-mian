"""玄骨遗府：集合群编排 + 游戏副群执行（跨实例协作）。

与虚天殿 / 苍坤洞府的差别：

1. 花名册来自**集合群**的 @提及，而不是单个身份 selector：
       .开启玄骨 @队长 @队员1 @队员2 @队员3 @队员4
   只有集合群（fuben_group_ids）里的这条指令才触发。
2. 参与者可能分散在**不同实例**（每人跑同一份脚本）。各实例靠游戏副群广播对齐，
   不需要彼此直接通信：谁能把花名册里的用户名解析成自己名下、且勾了
   「玄骨遗府」参与开关的身份，谁就负责那几个成员。
3. 队长必须是发起人自己名下的角色，否则回「队长不是您的账号」。

**认房规则（关键）**：游戏副群里可能同时有多个玄骨房间在集结（实测同一小时 3 个，
间隔仅 5 分钟），必须只认自己队长的房间。实测 61 条集结消息验证出：
  - `集结 1/5` 的成员列表**只有一个人**，那个人就是队长；
  - `2/5` 及以后队长**恒在第一位**。
所以判定规则是「集结消息里第一位 @用户名 == 我方花名册的队长」。

流程：
  集合群 .开启玄骨 @队长 @队员...
      ├─ 队长机  → 游戏副群 .开启玄骨遗府
      └─ 所有机  → 游戏副群监听 【玄骨遗府·集结 N/5】房间 <roomId>（首位==我方队长）
                     └─ 各自给自己名下队员发 .加入玄骨 <roomId>（队长已自动入队）
  游戏副群 【玄骨遗府·第N轮：XXX】 → 按分工发 .玄骨行动 N <动作>
  游戏副群 收尾提示              → 队长机按可选项保守收拢遗宝
"""

import re
import time

from .xuangu_context import XuanguContext

_FUBEN_KIND_XUANGU = "xuangu"

# Telegram 新消息和编辑消息可能并发到达，避免同一成员重复派发。
_XUANGU_DISPATCHING = set()

# 集合群：.开启玄骨 @队长 @队员1..4（5 个不同 @用户名）
_XUANGU_ROSTER_RE = re.compile(r"^\.开启玄骨\s+(?P<roster>(?:@[A-Za-z0-9_]{3,32}\s*)+)$")
_XUANGU_ROSTER_MIN = 5
_XUANGU_ROSTER_MAX = 5

# 游戏副群：系统播报
_XUANGU_ASSEMBLE_RE = re.compile(
    r"【玄骨遗府·集结\s+(?P<count>\d)/5】房间\s+(?P<room_id>[0-9a-fA-F]+)"
)
_XUANGU_MEMBER_ROLE_RE = re.compile(
    r"^@(?P<username>[A-Za-z0-9_]{3,32})[｜|][^\n｜|]+[｜|](?P<role>探禁|攻伐|护队|镇魂|御傀)[｜|]", re.M
)
_XUANGU_ACTION_ACK_RE = re.compile(r"已提交第(?P<round>\d+)轮【(?P<action>[^】]+)】")
_XUANGU_ROUND_RE = re.compile(r"【玄骨遗府·第(?P<round>\d)轮：(?P<scene>[^】]+)】")
_XUANGU_OUTCOME_RE = re.compile(
    r"【玄骨遗府·(?P<outcome>争取全收|收拢遗宝|一线生机|残图异动|立即撤离|魂阵溃散|集结取消)】"
)
_XUANGU_PANEL_ACTIONS_RE = re.compile(r"^本轮动作(?:[（(][^）)\n]*[）)])?[：:](?P<actions>[^\r\n]+)", re.M)
_XUANGU_PANEL_ROLE_RE = re.compile(
    r"(?P<role>探禁|攻伐|护队|镇魂|御傀)\s*@(?P<username>[A-Za-z0-9_]{3,32})"
)
_XUANGU_PANEL_SOUL_RE = re.compile(r"魂稳\s*(?P<soul>\d+)")
_XUANGU_PANEL_BODY_RE = re.compile(r"傀躯：(?P<body>[^\s｜|]+)")
_XUANGU_PANEL_GUARD_RE = re.compile(r"玄骨当前防备：(?P<guard>[^\n]+)")
_XUANGU_FINISH_RE = re.compile(r"队长\s*\.玄骨收尾\s*立即撤离/收拢遗宝/争取全收")

# 认房时间窗：只认集合群收到花名册之后的集结消息
_XUANGU_ROOM_MATCH_LOOKBACK_SEC = 5.0

_XUANGU_OPEN_TIMEOUT_SEC = 5 * 60
_XUANGU_JOIN_WINDOW_SEC = 3 * 60
_XUANGU_RUN_TTL_SEC = 30 * 60
_XUANGU_ROSTER_TTL_SEC = 15 * 60
_XUANGU_JOIN_DISPATCH_GAP_SEC = 2.0
# 五分工：按花名册顺序确定性分配 —— 所有实例算出的结果一致，跨机无需协调
_XUANGU_ROLE_ORDER = ("探禁", "攻伐", "护队", "镇魂", "御傀")
# 五人的分工必须在 bot 面板中确认后才进入；延迟仅用于发送节流。
_XUANGU_ROLE_SETTLE_SEC = 0.0
# 加入失败重试：集结名单里出现自己算成功；超时未出现就重发
_XUANGU_JOIN_MAX_ATTEMPTS = 4
_XUANGU_JOIN_RETRY_GAP_SEC = 8.0
_XUANGU_JOIN_CONFIRM_SEC = 90.0
# 已在队伍中的回执（游戏侧回复在加入指令下面）
_XUANGU_ALREADY_IN_TEAM_RE = re.compile(r"你已在玄骨队伍中")

# 每轮每个分工的计划动作（取自 2026-10-15~17 日志回放的最优解）
_XUANGU_ROUND_PLAN = {
    1: {"探禁": "验骨", "攻伐": "护队", "护队": "护队", "镇魂": "试池", "御傀": "护队"},
    2: {"探禁": "引蛇", "攻伐": "破敌", "护队": "护队", "镇魂": "镇魂", "御傀": "护队"},
    3: {"探禁": "侦察", "攻伐": "破敌", "护队": "护队", "镇魂": "侦察", "御傀": "护队"},
    4: {"探禁": "侦察", "攻伐": "护队", "护队": "护队", "镇魂": "镇魂", "御傀": "护傀"},
    5: {"探禁": "镇魂", "攻伐": "亮招 破敌", "护队": "护队", "镇魂": "收神", "御傀": "收神"},
}
_XUANGU_SAFE_ACTION = "护队"
_XUANGU_DEFAULT_ACTION = "防守"


# ---------------- 花名册解析 ----------------

def parse_xuangu_roster_command(text):
    """返回花名册用户名列表（首位为队长），格式不符返回 None。"""
    match = _XUANGU_ROSTER_RE.match(str(text or "").strip())
    if not match:
        return None
    usernames = re.findall(r"@[A-Za-z0-9_]{3,32}", match.group("roster"))
    if not (_XUANGU_ROSTER_MIN <= len(usernames) <= _XUANGU_ROSTER_MAX):
        return None
    # 去重保序
    seen, ordered = set(), []
    for name in usernames:
        key = name.lstrip("@").lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered if len(ordered) == 5 else None


def parse_xuangu_assemble(text):
    """解析集结播报，返回 (count, room_id, usernames按出现顺序)。"""
    match = _XUANGU_ASSEMBLE_RE.search(str(text or ""))
    if not match:
        return None
    usernames = re.findall(r"@[A-Za-z0-9_]{3,32}", str(text or ""))
    return int(match.group("count")), match.group("room_id"), usernames


def parse_xuangu_round(text):
    """解析轮次面板，返回 dict 或 None。"""
    match = _XUANGU_ROUND_RE.search(str(text or ""))
    if not match:
        return None
    body = str(text or "")
    actions_match = _XUANGU_PANEL_ACTIONS_RE.search(body)
    actions = []
    if actions_match:
        for item in actions_match.group("actions").split("/"):
            action = item.strip()
            command = re.fullmatch(r"\.玄骨行动\s+(\d+)\s+([^\s]+)", action)
            if command:
                if int(command.group(1)) != int(match.group("round")):
                    continue
                action = command.group(2)
            if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", action) and action not in actions:
                actions.append(action)
    roles = {m.group("role"): m.group("username") for m in _XUANGU_PANEL_ROLE_RE.finditer(body)}
    soul_match = _XUANGU_PANEL_SOUL_RE.search(body)
    body_match = _XUANGU_PANEL_BODY_RE.search(body)
    guard_match = _XUANGU_PANEL_GUARD_RE.search(body)
    return {
        "round": int(match.group("round")),
        "scene": match.group("scene").strip(),
        "actions": actions,
        "roles": roles,
        "soul": int(soul_match.group("soul")) if soul_match else 0,
        "body": (body_match.group("body") if body_match else "").strip(),
        "guard": (guard_match.group("guard") if guard_match else "").strip(),
        "finish_prompt": bool(_XUANGU_FINISH_RE.search(body)),
        "remaining": int(seconds.group(1)) if (seconds := re.search(r"剩余\s*(\d+)\s*秒", body)) else 0,
    }


def parse_xuangu_outcome(text):
    match = _XUANGU_OUTCOME_RE.search(str(text or ""))
    return match.group("outcome") if match else ""


def assign_xuangu_roles(roster):
    """按花名册顺序给每个成员定分工：队长探禁、其后攻伐/护队/镇魂/御傀。

    这是**纯函数**（只依赖花名册），所以每台机器算出的分配完全相同，
    不需要实例之间通信，也不会出现两人抢同一分工。
    """
    members = [str(n).lstrip("@") for n in (roster.get("members") or [])]
    return {
        name: _XUANGU_ROLE_ORDER[index]
        for index, name in enumerate(members)
        if index < len(_XUANGU_ROLE_ORDER)
    }


def _roster_username_set(roster):
    return {str(n).lstrip("@").lower() for n in (roster.get("members") or []) if str(n or "").strip()}


def find_xuangu_intruders(roster, assemble_usernames):
    """集结名单里不属于花名册的成员（按出现顺序）。"""
    allowed = _roster_username_set(roster)
    intruders = []
    for name in assemble_usernames or []:
        key = str(name or "").lstrip("@").lower()
        if key and key not in allowed:
            intruders.append("@" + key)
    return intruders


def is_xuangu_roster_complete(roster, assemble_usernames):
    """满员时名单是否与花名册完全一致（不多不少）。"""
    allowed = _roster_username_set(roster)
    got = {str(n).lstrip("@").lower() for n in (assemble_usernames or []) if str(n or "").strip()}
    return bool(allowed) and got == allowed


def is_captain_room(roster, assemble_usernames):
    """集结消息的首位成员是否就是本花名册的队长。"""
    if not roster or not assemble_usernames:
        return False
    leader = str(roster.get("leader_username") or "").lstrip("@").lower()
    first = str(assemble_usernames[0] or "").lstrip("@").lower()
    return bool(leader) and leader == first


# ---------------- 决策 ----------------

def decide_xuangu_action(round_no, role, panel, *, special_used=False):
    """按分工 + 面板决定本轮动作。

    返回 (动作字符串, 是否使用亮招)。动作一定取自面板给出的动作池。
    """
    pool = list((panel or {}).get("actions") or [])
    if not pool:
        return _XUANGU_DEFAULT_ACTION, False
    plan = _XUANGU_ROUND_PLAN.get(int(round_no or 0), {})
    planned = plan.get(str(role or ""), "")

    # Low soul makes required mechanics more urgent: never replace 引蛇/镇魂/收神
    # with five copies of 护队. The plan already avoids optional greed actions.
    use_special = False
    if planned.startswith("亮招"):
        planned = planned.split("亮招", 1)[1].strip()
        use_special = not special_used
    if planned and planned in pool:
        return planned, use_special

    # 计划动作不在本轮池里：退到护队 → 池里第一个非防守 → 防守
    if _XUANGU_SAFE_ACTION in pool:
        return _XUANGU_SAFE_ACTION, False
    for action in pool:
        if action != _XUANGU_DEFAULT_ACTION:
            return action, False
    return _XUANGU_DEFAULT_ACTION, False


# ---------------- 持久化 ----------------


def _make_xuangu_roster_id(chat_id, leader_username, now):
    return f"{int(chat_id or 0)}:{str(leader_username or '').lstrip('@').lower()}:{int(float(now or 0) * 1000)}"


def _upsert_xuangu_roster(ctx, roster):
    if not isinstance(roster, dict):
        return False
    if not str(roster.get("flow_id") or ""):
        roster["flow_id"] = str(roster.get("roster_id") or "")
    return ctx.flows.upsert(roster)


def _cleanup_xuangu_rosters(ctx, now=None):
    return ctx.flows.cleanup(float(now or time.time()))


def _active_xuangu_rosters(ctx, now=None):
    return ctx.flows.active(float(now or time.time()))


def _my_xuangu_members(ctx, roster):
    """花名册里属于本机的成员：能解析成身份 + 勾了玄骨参与开关。"""
    mine = []
    for username in roster.get("members") or []:
        identity_id = ctx.identities.resolve_selector(username)
        if identity_id is None:
            continue
        if not ctx.identities.participation_enabled(identity_id, _FUBEN_KIND_XUANGU):
            continue
        mine.append({"username": str(username).lstrip("@"), "identity_id": int(identity_id)})
    return mine


# ---------------- 集合群：花名册指令 ----------------

async def _send_xuangu_command(ctx, command, *, track=False, send_as_id=None):
    is_disband = command == ctx.commands.disband
    matches = [roster for roster in ctx.flows.active(time.time())
               if (not roster.get("disbanded") or is_disband)
               and roster.get("phase") in ({"disbanded"} if is_disband else {"waiting_open", "joining", "running", "finishing"})
               and any(int(member.get("identity_id") or 0) == int(send_as_id or 0)
                       for member in roster.get("my_members", []))]
    if len(matches) != 1:
        return None
    roster = matches[0]
    key = f"{int(send_as_id or 0)}:{command}"
    attempts = roster.setdefault("command_attempts", {})
    if key in attempts:
        return None
    phase, round_no = roster.get("phase"), roster.get("current_round")
    action_command = command.startswith(".玄骨行动 ")
    critical = action_command or command.startswith(".玄骨收尾 ")
    deadline_key = "round_deadline" if action_command else "finish_deadline"
    deadline = float(roster.get(deadline_key) or 0) if critical else 0
    if not deadline and phase == "joining":
        deadline = float(roster.get("room_seen_at") or time.time()) + _XUANGU_JOIN_WINDOW_SEC

    def allowed():
        return (ctx.flows.load().get(roster.get("flow_id")) is roster
                and (not getattr(ctx, "target", None) or not roster.get("game_chat_id")
                     or ctx.target() == (roster["game_chat_id"], int(roster.get("game_topic_id") or 0)))
                and (not roster.get("disbanded") or is_disband) and roster.get("phase") == phase
                and roster.get("current_round") == round_no
                and (not deadline or time.time() < deadline)
                and (not command.startswith(".玄骨行动 ") or (
                    not roster.get("terminal_pending") and int(roster.get("observed_round") or 0) <= int(round_no or 0)))
                and float(roster.get("expires_at") or 0) > time.time()
                and ctx.identities.participation_enabled(send_as_id, _FUBEN_KIND_XUANGU))

    if not allowed():
        return None
    # A crash or timeout after dispatch never authorizes replay of this mutation.
    attempts[key] = {"status": "unknown", "at": time.time()}
    _upsert_xuangu_roster(ctx, roster)
    msg = await ctx.messages.game_command(
        command, track=track, send_as_id=send_as_id, target_group_role="secondary", send_guard=allowed,
        deadline=deadline, op_id=f"xuangu:{roster.get('roster_id')}:{key}",
        chain_id=f"xuangu:{roster.get('roster_id')}:{round_no or phase}",
    )
    attempts[key]["msg_id"] = int(getattr(msg, "id", 0) or 0)
    if attempts[key]["msg_id"]:
        attempts[key]["status"] = "sent"
    elif getattr(ctx.messages, "definitely_unsent", lambda *_: False)(send_as_id, command):
        attempts[key]["status"] = "unsent"
    _upsert_xuangu_roster(ctx, roster)
    return msg


async def _reply_assemble(ctx, event, text):
    await ctx.messages.group_message(
        event.client,
        event.chat_id,
        text,
        listener_account_id=ctx.messages.listener_account_id(event),
        log_text=text,
    )


async def handle_xuangu_roster_command(ctx: XuanguContext, event):
    """集合群 .开启玄骨 @队长 @队员... 处理。

    所有实例都会记下花名册（跨实例协作需要），但只有**发起人自己的机器**
    （监听账号 == 消息发送者）才回执并去游戏副群开房。
    """
    raw_text = str(getattr(event, "raw_text", "") or getattr(event, "message", "") or "").strip()
    if not raw_text or raw_text.split(maxsplit=1)[0] != ".开启玄骨":
        return False
    # 只处理集合群；游戏副群的 .开启玄骨遗府 是另一条指令
    if int(getattr(event, "chat_id", 0) or 0) not in set(ctx.messages.group_ids() or []):
        return False

    listener_account_id = ctx.messages.listener_account_id(event)
    sender_is_mine = ctx.messages.is_listener_self(event, listener_account_id)
    roster_names = parse_xuangu_roster_command(raw_text)
    if not roster_names:
        if sender_is_mine:
            await _reply_assemble(ctx, event, "用法：.开启玄骨 @队长 @队员1 @队员2 @队员3 @队员4")
        return True

    return await start_xuangu_roster(ctx, roster_names, int(event.chat_id), sender_is_mine=sender_is_mine, event=event)


async def start_xuangu_roster(ctx, roster_names, chat_id, *, sender_is_mine=True, event=None, auto_daily=False):
    """Share one opening chain between manual assembly and ticket-gated automation."""
    async def reply(text):
        if event is not None:
            await _reply_assemble(ctx, event, text)
        else:
            await ctx.messages.audit(text, tag="MiniApp")

    now = time.time()
    _cleanup_xuangu_rosters(ctx, now)
    leader_username = roster_names[0]
    leader_identity_id = ctx.identities.resolve_selector(leader_username)
    captain_mine = bool(
        leader_identity_id is not None
        and ctx.identities.participation_enabled(leader_identity_id, _FUBEN_KIND_XUANGU)
    )

    roster = {
        "roster_id": _make_xuangu_roster_id(chat_id, leader_username, now),
        "assemble_chat_id": chat_id,
        "leader_username": str(leader_username).lstrip("@"),
        "leader_identity_id": int(leader_identity_id or 0),
        "members": [str(name).lstrip("@") for name in roster_names],
        "phase": "waiting_open",
        "auto_daily": auto_daily,
        "room_id": "",
        "is_captain": captain_mine,
        "my_members": [],
        "join_sent_usernames": [],
        "seen_at": now,
        "open_requested_at": 0.0,
        "open_command_msg_id": 0,
        "join_sent_at": 0.0,
        "round_done": [],
        "special_used": False,
        "last_error": "",
        "updated_at": now,
        "expires_at": now + _XUANGU_ROSTER_TTL_SEC,
    }
    roster["my_members"] = _my_xuangu_members(ctx, roster)
    for member in roster["my_members"]:
        error = getattr(ctx.identities, "preflight_error", lambda _: "")(member["identity_id"])
        if error:
            if sender_is_mine:
                await reply(f"玄骨未开房：@{member['username']} {error}")
            return True
    # Keep one active roster per local identity, including commands from other hosts.
    mine = {member["identity_id"] for member in roster["my_members"]}
    if any(mine & {member.get("identity_id") for member in old.get("my_members", [])}
           for old in _active_xuangu_rosters(ctx, now)
           if old.get("requires_reconciliation") or old.get("phase") in {"waiting_open", "joining", "running", "finishing", "held", "interrupted"}):
        if sender_is_mine:
            await reply("本机成员已有玄骨队伍，请先核对原房间")
        return True
    roster["flow_id"] = roster["roster_id"]
    _upsert_xuangu_roster(ctx, roster)

    if not sender_is_mine:
        # 不是本机账号发起的：只记花名册，静默等游戏副群的集结播报
        return True

    if not captain_mine:
        roster["phase"] = "failed"
        roster["last_error"] = "队长不是发起人的账号"
        roster["updated_at"] = time.time()
        _upsert_xuangu_roster(ctx, roster)
        await reply(f"队长不是您的账号：{leader_username}")
        return True

    mates = " ".join("@" + name for name in roster["members"][1:]) or "无"
    await reply(
        f"玄骨遗府集结：队长 {leader_username}｜队员 {mates}\n"
        f"本机负责 {len(roster['my_members'])} 人｜正在游戏副群开启房间…",
    )

    msg = await _send_xuangu_command(ctx, ctx.commands.open, track=False, send_as_id=leader_identity_id)
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    roster["open_requested_at"] = now
    roster["open_command_msg_id"] = msg_id
    if msg_id <= 0:
        attempt = roster.get("command_attempts", {}).get(f"{leader_identity_id}:{ctx.commands.open}", {})
        known_unsent = attempt.get("status") == "unsent"
        roster["phase"] = "failed" if known_unsent else "held"
        roster["requires_reconciliation"] = not known_unsent
        roster["last_error"] = "开房命令未发送" if known_unsent else "开房结果未确认，已停止自动操作，请核对游戏房间"
        _upsert_xuangu_roster(ctx, roster)
        await reply(f"❌ 玄骨遗府：{roster['last_error']}｜{leader_username}")
        return True
    roster["updated_at"] = time.time()
    _upsert_xuangu_roster(ctx, roster)
    return True


async def handle_xuangu_disband_command(ctx: XuanguContext, event):
    """副本监听群手动解散最近一支玄骨队伍，由持有队长身份的实例执行。"""
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if raw_text != ctx.commands.disband or chat_id not in set(ctx.messages.group_ids() or []):
        return False
    listener_id = ctx.messages.listener_account_id(event)
    if not ctx.messages.is_listener_self(event, listener_id):
        return True
    now = time.time()
    candidates = [
        roster for roster in _active_xuangu_rosters(ctx, now)
        if int(roster.get("assemble_chat_id") or 0) == chat_id
        and roster.get("phase") in ("waiting_open", "joining", "running", "finishing")
        and not roster.get("disbanded")
    ]
    if not candidates:
        listener_id = ctx.messages.listener_account_id(event)
        if ctx.messages.is_listener_self(event, listener_id):
            await _reply_assemble(ctx, event, "没有可解散的自动玄骨遗府副本")
        return True
    roster = max(candidates, key=lambda item: float(item.get("seen_at") or 0))
    await _xuangu_disband(ctx, roster, now, "副本监听群手动解散")
    return True


async def handle_xuangu_confirmation(ctx: XuanguContext, event):
    parts = str(getattr(event, "raw_text", "") or "").split()
    if len(parts) != 2 or parts[0] != ".确认玄骨结束":
        return False
    if int(getattr(event, "chat_id", 0) or 0) not in set(ctx.messages.group_ids() or []):
        return False
    listener = ctx.messages.listener_account_id(event)
    if not ctx.messages.is_listener_self(event, listener):
        return True
    date = getattr(event, "date", None)
    if date is None or not 0 <= time.time() - date.timestamp() <= 60:
        await _reply_assemble(ctx, event, "确认指令已过期，请重新发送")
        return True
    roster = next((r for r in ctx.flows.load().values()
                   if r.get("roster_id") == parts[1] and r.get("requires_reconciliation")
                   and int(r.get("assemble_chat_id") or 0) == int(event.chat_id)), None)
    if roster is None:
        await _reply_assemble(ctx, event, "须填写待核对玄骨记录的准确记录号")
        return True
    roster.update(phase="operator_confirmed", requires_reconciliation=False,
                  operator_confirmed_at=time.time(), expires_at=time.time() + 300)
    _upsert_xuangu_roster(ctx, roster)
    await _reply_assemble(ctx, event, "已记录人工确认结束，保留原回执，不重放旧动作")
    return True


# ---------------- 游戏副群：集结 / 轮次 / 结局 ----------------

async def handle_xuangu_game_event(ctx: XuanguContext, event):
    """游戏副群里的玄骨播报处理。返回是否已处理。"""
    raw_text = str(getattr(event, "raw_text", "") or getattr(event, "message", "") or "").strip()
    now = time.time()
    if raw_text == "你当前没有玄骨队伍。":
        roster = _match_roster_for_outcome(ctx, "无队伍", now, event=event)
        reply_id = int(getattr(event, "reply_to_msg_id", 0) or
                       getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
        if roster is not None and reply_id and any(
                key.endswith(":" + ctx.commands.disband) and attempt.get("msg_id") == reply_id
                for key, attempt in roster.get("command_attempts", {}).items()):
            roster.update(phase="settled", requires_reconciliation=False, finished=True,
                          outcome="无队伍", terminal_msg_id=int(getattr(event, "id", 0) or 0),
                          updated_at=now, expires_at=now + 300)
            _upsert_xuangu_roster(ctx, roster)
            await ctx.messages.audit("🪦 玄骨原队长确认无队伍，已解除待核对占用", tag="MiniApp")
            return True
        return False
    assemble = parse_xuangu_assemble(raw_text)
    if assemble:
        await _on_xuangu_assemble(ctx, event, assemble, now)
        return True

    round_panel = parse_xuangu_round(raw_text)
    if round_panel:
        await _on_xuangu_round(ctx, event, round_panel, now)
        return True

    if _XUANGU_ALREADY_IN_TEAM_RE.search(raw_text):
        await _on_xuangu_already_in_team(ctx, event, now)
        return True

    outcome = parse_xuangu_outcome(raw_text)
    if outcome:
        await _on_xuangu_outcome(ctx, event, outcome, now)
        return True
    # 最后一人的行动可能直接收到下一轮面板或收尾提示，必须先处理阶段播报。
    return await _on_xuangu_action_reply(ctx, event, raw_text, now)


async def _on_xuangu_already_in_team(ctx, event, now):
    """「你已在玄骨队伍中」= 该成员其实已在房里，按成功处理，别再重试。"""
    reply_to = int(getattr(event, "reply_to_msg_id", 0) or 0)
    if reply_to <= 0:
        return
    for roster in _active_xuangu_rosters(ctx, now):
        username = (roster.get("join_msg_ids") or {}).get(str(reply_to))
        if not username or roster.get("phase") != "joining" or roster.get("disbanded") or roster.get("entered"):
            continue
        if roster.get("game_chat_id") and int(roster["game_chat_id"]) != int(getattr(event, "chat_id", 0) or 0):
            continue
        confirmed = set(roster.get("join_confirmed_usernames") or [])
        confirmed.add(str(username).lstrip("@").lower())
        roster["join_confirmed_usernames"] = sorted(confirmed)
        roster["updated_at"] = now
        _upsert_xuangu_roster(ctx, roster)
        await _set_xuangu_confirmed_roles(ctx, roster, now)
        return


async def _set_xuangu_confirmed_roles(ctx, roster, now):
    """只给游戏回执已确认入队的本机成员设置分工；失败留给调度器补发。"""
    role_map = assign_xuangu_roles(roster)
    for member in roster.get("my_members") or []:
        if roster.get("disbanded") or roster.get("entered") or roster.get("enter_requested_at") or roster.get("phase") != "joining":
            return
        username = str(member.get("username") or "")
        key = username.lstrip("@").lower()
        confirmed = {str(name).lstrip("@").lower() for name in roster.get("join_confirmed_usernames") or []}
        roles_set = {str(name).lstrip("@").lower() for name in roster.get("role_set_usernames") or []}
        role = role_map.get(username)
        dispatch_key = (roster.get("roster_id"), "role", key)
        if not role or key not in confirmed or key in roles_set or (roster.get("role_confirmed_map") or {}).get(key) == role or dispatch_key in _XUANGU_DISPATCHING:
            continue
        _XUANGU_DISPATCHING.add(dispatch_key)
        try:
            msg = await _send_xuangu_command(
                ctx, f"{ctx.commands.role} {role}", send_as_id=member.get("identity_id")
            )
            if int(getattr(msg, "id", 0) or 0) > 0:
                # 这只是发送记录；分工成功要等 bot 的集结面板确认。
                roles_set = set(roster.get("role_set_usernames") or [])
                roles_set.add(username)
                roster["role_set_usernames"] = sorted(roles_set)
                roster["role_map"] = role_map
                roster["updated_at"] = now
                _upsert_xuangu_roster(ctx, roster)
                await ctx.sleep(_XUANGU_JOIN_DISPATCH_GAP_SEC)
        finally:
            _XUANGU_DISPATCHING.discard(dispatch_key)


async def _on_xuangu_assemble(ctx, event, assemble, now):
    """集结播报：先校验名单，再认自己队长的房间，最后给自己名下成员发加入。"""
    count, room_id, usernames = assemble
    roster = None
    for candidate in _active_xuangu_rosters(ctx, now):
        if candidate.get("phase") not in ("waiting_open", "joining", "running", "disbanded"):
            continue
        if float(candidate.get("seen_at") or 0) - _XUANGU_ROOM_MATCH_LOOKBACK_SEC > now:
            continue
        if not _roster_matches_event(candidate, event):
            continue
        if is_captain_room(candidate, usernames):
            roster = candidate
            break
    if roster is None:
        # 不是我方队长的房间：静默忽略（同群可能同时有多个玄骨房间）
        return
    if roster.get("disbanded") or roster.get("entered"):
        return
    if str(roster.get("room_id") or "") and str(roster.get("room_id")) != str(room_id):
        return  # 本花名册已锁定别的房间

    if roster.get("auto_daily"):
        body = str(getattr(event, "raw_text", "") or "")
        for name in usernames:
            key = name.lstrip("@").lower()
            line = next((line for line in body.splitlines() if line.lower().startswith("@" + key + "｜")), "")
            if not line.endswith("｜可领奖"):
                roster.update(room_id=str(room_id), game_chat_id=int(getattr(event, "chat_id", 0) or 0),
                              game_topic_id=_event_topic_id(event))
                await _xuangu_disband(ctx, roster, now, "游戏未确认全队可领奖，取消并保留门票")
                return

    if roster.get("game_chat_id") and int(roster["game_chat_id"]) != int(getattr(event, "chat_id", 0) or 0):
        return

    # 名单校验放在发加入之前：有外人就立刻解散，不白费加入指令
    intruders = find_xuangu_intruders(roster, usernames)
    if intruders:
        await _xuangu_disband(ctx, roster, now, f"名单有外人：{'、'.join(intruders)}")
        return

    if not roster.get("room_id"):
        roster["room_id"] = str(room_id)
        roster["phase"] = "joining"
        roster["room_seen_at"] = now
        roster["game_chat_id"] = int(getattr(event, "chat_id", 0) or 0)
        roster["game_topic_id"] = _event_topic_id(event)
        _upsert_xuangu_roster(ctx, roster)
        await ctx.messages.audit(
            f"🪦 玄骨遗府房间已锁定：{room_id}｜队长 @{roster.get('leader_username')}",
            tag="MiniApp",
        )

    # 集结名单是游戏给出的入队确认；发送加入指令本身不代表加入成功。
    confirmed = {str(name).lstrip("@").lower() for name in roster.get("join_confirmed_usernames") or []}
    confirmed.update(str(name).lstrip("@").lower() for name in usernames)
    roster["join_confirmed_usernames"] = sorted(confirmed)
    expected_roles = {name.lower(): role for name, role in assign_xuangu_roles(roster).items()}
    role_confirmed = dict(roster.get("role_confirmed_map") or {})
    for match in _XUANGU_MEMBER_ROLE_RE.finditer(str(getattr(event, "raw_text", "") or "")):
        name, role = match.group("username").lower(), match.group("role")
        if expected_roles.get(name) == role:
            role_confirmed[name] = role
    roster["role_confirmed_map"] = role_confirmed
    roster["updated_at"] = now
    _upsert_xuangu_roster(ctx, roster)

    leader_key = str(roster.get("leader_username") or "").lstrip("@").lower()
    for member in roster.get("my_members") or []:
        if roster.get("disbanded") or roster.get("entered"):
            return
        username = str(member.get("username") or "")
        key = username.lstrip("@").lower()
        joined = {str(name).lstrip("@").lower() for name in roster.get("join_sent_usernames") or []}
        confirmed = {str(name).lstrip("@").lower() for name in roster.get("join_confirmed_usernames") or []}
        dispatch_key = (roster.get("roster_id"), "join", key)
        if not key or key == leader_key or key in joined or key in confirmed or dispatch_key in _XUANGU_DISPATCHING:
            continue
        _XUANGU_DISPATCHING.add(dispatch_key)
        try:
            msg = await _send_xuangu_command(
                ctx, f"{ctx.commands.join} {room_id}", send_as_id=member.get("identity_id")
            )
            msg_id = int(getattr(msg, "id", 0) or 0)
            if msg_id > 0:
                joined = set(roster.get("join_sent_usernames") or [])
                joined.add(username)
                roster["join_sent_usernames"] = sorted(joined)
                join_msg_ids = dict(roster.get("join_msg_ids") or {})
                join_msg_ids[str(msg_id)] = username
                roster["join_msg_ids"] = join_msg_ids
                attempts = dict(roster.get("join_attempts") or {})
                attempts[username] = {
                    "count": int((attempts.get(username) or {}).get("count") or 0) + 1, "last_at": now,
                }
                roster["join_attempts"] = attempts
                roster["join_sent_at"] = now
                _upsert_xuangu_roster(ctx, roster)
                await ctx.sleep(_XUANGU_JOIN_DISPATCH_GAP_SEC)
        finally:
            _XUANGU_DISPATCHING.discard(dispatch_key)
    await _set_xuangu_confirmed_roles(ctx, roster, now)
    if roster.get("disbanded") or roster.get("entered"):
        return

    if int(count) < 5:
        return

    # 满员：再校验一次「不多不少」，然后队长发 .进入玄骨 正式出发（门票在此扣除）
    if not is_xuangu_roster_complete(roster, usernames):
        allowed = "、".join("@" + n for n in sorted(_roster_username_set(roster)))
        got = "、".join("@" + str(n).lstrip("@").lower() for n in usernames)
        await _xuangu_disband(ctx, roster, now, f"满员名单不符｜期望 {allowed}｜实际 {got}")
        return
    roster["team_complete"] = True
    _upsert_xuangu_roster(ctx, roster)
    _schedule_xuangu_enter(ctx, roster)


def _xuangu_roles_confirmed(roster):
    expected = {name.lower(): role for name, role in assign_xuangu_roles(roster).items()}
    confirmed = roster.get("role_confirmed_map") or {}
    return len(expected) == 5 and all(confirmed.get(name) == role for name, role in expected.items())


def _schedule_xuangu_enter(ctx, roster):
    if (not roster.get("team_complete") or not roster.get("is_captain")
            or roster.get("enter_scheduled") or roster.get("enter_requested_at")
            or roster.get("entered") or roster.get("disbanded") or not _xuangu_roles_confirmed(roster)):
        return
    roster["enter_scheduled"] = True
    _upsert_xuangu_roster(ctx, roster)
    ctx.spawn(_delayed_xuangu_enter(ctx, roster.get("roster_id"), _XUANGU_ROLE_SETTLE_SEC))


def _find_xuangu_roster_by_id(ctx, roster_id, now=None):
    key = str(roster_id or "")
    if not key:
        return None
    for roster in _active_xuangu_rosters(ctx, now):
        if str(roster.get("roster_id") or "") == key:
            return roster
    return None


async def _delayed_xuangu_enter(ctx, roster_id, delay):
    """只有 bot 确认全队分工后才发送进入；收到首轮面板才标记已进入。"""
    await ctx.sleep(max(0.0, float(delay or 0.0)))
    now = time.time()
    roster = _find_xuangu_roster_by_id(ctx, roster_id, now)
    if roster is None or roster.get("disbanded") or roster.get("entered") or roster.get("enter_requested_at"):
        return
    if not roster.get("is_captain"):
        return
    if not roster.get("team_complete") or not _xuangu_roles_confirmed(roster):
        roster["enter_scheduled"] = False
        _upsert_xuangu_roster(ctx, roster)
        return
    leader_identity_id = int(roster.get("leader_identity_id") or 0)
    dispatch_key = (roster_id, "enter")
    if leader_identity_id <= 0 or dispatch_key in _XUANGU_DISPATCHING:
        return
    _XUANGU_DISPATCHING.add(dispatch_key)
    try:
        msg = await _send_xuangu_command(ctx, ctx.commands.enter, send_as_id=leader_identity_id)
        if int(getattr(msg, "id", 0) or 0) > 0:
            roster["enter_requested_at"] = now
            roster["enter_command_msg_id"] = int(msg.id)
        else:
            roster["enter_scheduled"] = False
        roster["updated_at"] = now
        _upsert_xuangu_roster(ctx, roster)
    finally:
        _XUANGU_DISPATCHING.discard(dispatch_key)


async def _xuangu_disband(ctx, roster, now, reason):
    """停止本机后续动作，并由队长在副群发送解散命令。"""
    roster["phase"] = "disbanded"
    roster["disbanded"] = True
    roster["requires_reconciliation"] = True
    roster["last_error"] = reason
    roster["updated_at"] = now
    roster["expires_at"] = now + 300
    _upsert_xuangu_roster(ctx, roster)
    status = "玄骨遗府自动流程已停止"
    sent = False
    if roster.get("is_captain"):
        leader_identity_id = int(roster.get("leader_identity_id") or 0)
        if leader_identity_id > 0:
            msg = await _send_xuangu_command(
                ctx, ctx.commands.disband, send_as_id=leader_identity_id
            )
            sent = int(getattr(msg, "id", 0) or 0) > 0
        status = "玄骨遗府已发送解散命令" if sent else "玄骨遗府解散命令发送失败，自动流程已停止，请人工处理"
    await ctx.messages.audit(
        f"🪦 {status}｜队长 @{roster.get('leader_username')}｜{reason}",
        tag="MiniApp",
    )
    if roster.get("is_captain") and roster.get("assemble_chat_id"):
        try:
            await ctx.messages.notice_by_chat(
                roster["assemble_chat_id"], f"{status}：{reason}",
            )
        except Exception:
            pass
    return sent


def _confirm_xuangu_action(roster, round_key, actions, username, action, event, now, kind):
    action.update(status="confirmed", reply_at=now, reply_msg_id=int(getattr(event, "id", 0) or 0),
                  confirmation_kind=kind)
    if action.get("special"):
        used = set(roster.get("special_used_usernames") or [])
        used.add(username)
        roster["special_used_usernames"] = sorted(used)
        roster["special_used"] = True
    local = {str(m.get("username") or "").lower() for m in roster.get("my_members") or []}
    if local and all((actions.get(name) or {}).get("status") == "confirmed" for name in local):
        roster["round_done"] = sorted({int(n) for n in roster.get("round_done") or []} | {int(round_key)})
    roster["updated_at"] = now


def _confirm_xuangu_transition(ctx, roster, event, completed_round, now):
    """The last accepted action receives the next phase instead of a plain ack."""
    reply_id = int(getattr(event, "reply_to_msg_id", 0) or
                   getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    if not reply_id:
        return
    actions = (roster.get("round_actions") or {}).get(str(completed_round), {})
    for username, action in actions.items():
        if int(action.get("msg_id") or 0) == reply_id and action.get("status") == "pending":
            _confirm_xuangu_action(roster, completed_round, actions, username, action, event, now, "phase_transition")
            _upsert_xuangu_roster(ctx, roster)
            return


async def _on_xuangu_action_reply(ctx, event, text, now):
    """把行动的发送状态与 bot 的提交确认分开，拒绝/无回执均不算完成。"""
    reply_to = int(getattr(event, "reply_to_msg_id", 0) or 0)
    if not reply_to:
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    for roster in _active_xuangu_rosters(ctx, now):
        if roster.get("phase") not in ("joining", "running", "finishing", "settled", "held", "interrupted", "disbanded"):
            continue
        if int(roster.get("game_chat_id") or 0) != chat_id or not _roster_matches_event(roster, event):
            continue
        for round_key, actions in (roster.get("round_actions") or {}).items():
            for username, action in actions.items():
                if int(action.get("msg_id") or 0) != reply_to:
                    continue
                ack = _XUANGU_ACTION_ACK_RE.search(text)
                if ack and int(ack.group("round")) == int(round_key) and ack.group("action").strip() == (("亮招·" if action.get("special") else "") + str(action.get("action") or "")):
                    _confirm_xuangu_action(roster, round_key, actions, username, action, event, now, "action_ack")
                else:
                    action["status"] = "rejected"
                    roster["last_error"] = text
                action["reply_at"] = now
                roster["updated_at"] = now
                _upsert_xuangu_roster(ctx, roster)
                return True
    return False


async def _on_xuangu_round(ctx, event, panel, now):
    """每个动作由 bot 的当轮面板触发，单独等提交确认，重复/旧面板不重发。"""
    room_id = _find_room_for_event(panel, event)
    roster = _match_roster_for_round(ctx, room_id, panel, now, event=event)
    if roster is None or roster.get("disbanded") or roster.get("phase") not in ("joining", "running"):
        return
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if roster.get("game_chat_id") and int(roster["game_chat_id"]) != chat_id:
        return
    round_no = int(panel.get("round") or 0)
    if not 1 <= round_no <= 5:
        return
    # A shared broadcast can start the next dispatch before its direct reply
    # gets this lock. Record that owned receipt even if a later round is active.
    _confirm_xuangu_transition(ctx, roster, event, round_no - 1, now)
    if round_no < int(roster.get("current_round") or 0):
        return
    roster["current_round"] = round_no
    roster["entered"] = True  # 这是 bot 发布的实际进度，不是发送成功的推断。
    roster["phase"] = "running"
    roster["game_chat_id"] = chat_id
    date = getattr(event, "date", None)
    event_at = date.timestamp() if date is not None else now
    # Legacy injected panels have no timer; real panels must supply their timer.
    remaining = int(panel.get("remaining", 30))
    deadline = event_at + min(30, max(0, remaining)) - 1
    if int(roster.get("deadline_round") or 0) == round_no:
        deadline = min(deadline, float(roster.get("round_deadline") or deadline))
    roster.update(round_deadline=deadline, deadline_round=round_no,
                  observed_round=max(round_no, int(roster.get("observed_round") or 0)))
    roster["expires_at"] = now + _XUANGU_RUN_TTL_SEC
    _upsert_xuangu_roster(ctx, roster)
    roles = panel.get("roles") or {}
    members = list(roster.get("my_members") or [])
    if round_no == 1:
        # The captain just entered; let the other identities act while its
        # existing per-identity cooldown expires.
        members.sort(key=lambda member: int(member.get("identity_id") or 0) == int(roster.get("leader_identity_id") or 0))
    for member in members:
        if roster.get("disbanded") or roster.get("phase") != "running" or int(roster.get("current_round") or 0) != round_no:
            return
        username = str(member.get("username") or "")
        key = username.lower()
        role = _role_of_username(roles, username)
        if not role:
            continue
        if time.time() >= deadline or roster.get("terminal_pending") or int(roster.get("observed_round") or 0) > round_no:
            return
        if not panel.get("actions"):
            roster["last_error"] = f"第{round_no}轮动作池无法识别，未猜测或发送防守"
            _upsert_xuangu_roster(ctx, roster)
            return
        actions = roster.setdefault("round_actions", {}).setdefault(str(round_no), {})
        if key in actions:
            continue
        action, use_special = decide_xuangu_action(
            round_no, role, panel, special_used=key in set(roster.get("special_used_usernames") or []),
        )
        command = f"{ctx.commands.act} {round_no} " + ("亮招 " if use_special else "") + action
        # 先留待确认记录，防止多个相同 bot 面板并发造成重复提交。
        record = {"status": "sending", "action": action, "special": use_special, "msg_id": 0}
        actions[key] = record
        _upsert_xuangu_roster(ctx, roster)
        msg = await _send_xuangu_command(ctx, command, send_as_id=member.get("identity_id"))
        msg_id = int(getattr(msg, "id", 0) or 0)
        record["msg_id"] = msg_id
        record["status"] = "pending" if msg_id > 0 else "send_failed"
        if msg_id <= 0:
            roster["last_error"] = f"第{round_no}轮行动发送失败：@{username}"
        record["sent_at"] = now
        roster["updated_at"] = now
        _upsert_xuangu_roster(ctx, roster)
        # The shared reactive queue provides pacing; no extra sleep in the
        # thirty-second action window.


async def _on_xuangu_outcome(ctx, event, outcome, now):
    roster = _match_roster_for_outcome(ctx, outcome, now, event=event)
    if roster is None:
        return
    _confirm_xuangu_transition(ctx, roster, event, 5, now)
    # 残图异动是 bot 给出的收尾选择点，发完选择后仍需等待最终结算回执。
    if outcome == "残图异动":
        if roster.get("disbanded") or roster.get("phase") != "running":
            return  # Restart/uncertainty never authorizes resuming an old mutation.
        if roster.get("finish_requested_at"):
            return
        text = str(getattr(event, "raw_text", "") or "")
        choices = {choice for choice, status in re.findall(
            r"^(立即撤离|收拢遗宝|争取全收)[（(](可选|不可选)[）)]", text, re.M) if status == "可选"}
        choice = next((value for value in (ctx.finish_choice, "收拢遗宝", "立即撤离") if value in choices), "")
        if not choice:
            roster.update(phase="held", requires_reconciliation=True, last_error="未识别可用收尾选项，请核对游戏房间")
            _upsert_xuangu_roster(ctx, roster)
            return
        roster["phase"] = "finishing"
        roster["terminal_pending"] = False
        date = getattr(event, "date", None)
        roster["finish_deadline"] = (date.timestamp() if date is not None else now) + 29
        if time.time() >= roster["finish_deadline"]:
            roster["last_error"] = "收尾提示已过期，未发送旧选择"
            _upsert_xuangu_roster(ctx, roster)
            return
        _upsert_xuangu_roster(ctx, roster)
        if roster.get("is_captain"):
            leader_identity_id = int(roster.get("leader_identity_id") or 0)
            dispatch_key = (roster.get("roster_id"), "finish")
            if leader_identity_id > 0 and dispatch_key not in _XUANGU_DISPATCHING:
                _XUANGU_DISPATCHING.add(dispatch_key)
                try:
                    finish = f"{ctx.commands.finish} {choice}"
                    msg = await _send_xuangu_command(ctx, finish, send_as_id=leader_identity_id)
                    if int(getattr(msg, "id", 0) or 0) > 0:
                        roster["finish_requested_at"] = now
                        roster["finish_command_msg_id"] = int(msg.id)
                        roster["finish_choice"] = choice
                        _upsert_xuangu_roster(ctx, roster)
                finally:
                    _XUANGU_DISPATCHING.discard(dispatch_key)
        return
    roster["phase"] = "settled"
    roster["requires_reconciliation"] = False
    roster["finished"] = True
    roster["outcome"] = outcome
    roster["updated_at"] = now
    roster["expires_at"] = now + 300
    _upsert_xuangu_roster(ctx, roster)
    await ctx.messages.audit(
        f"🪦 玄骨遗府结算：{outcome}｜队长 @{roster.get('leader_username')}", tag="MiniApp",
    )


def _role_of_username(roles, username):
    key = str(username or "").lstrip("@").lower()
    for role, name in (roles or {}).items():
        if str(name or "").lstrip("@").lower() == key:
            return role
    return ""


def _find_room_for_event(panel, event):
    room_id = str(getattr(event, "room_id", "") or "")
    if room_id:
        return room_id
    match = re.search(r"房间(?:\s*ID)?\s*[:：]?\s*([0-9a-fA-F]+)\b", str(getattr(event, "raw_text", "") or ""))
    return match.group(1) if match else ""


def _event_topic_id(event):
    header = getattr(event, "reply_to", None)
    if header is None:
        header = getattr(getattr(event, "message", None), "reply_to", None)
    top_id = int(getattr(header, "reply_to_top_id", 0) or 0)
    if not top_id and getattr(header, "forum_topic", False):
        top_id = int(getattr(header, "reply_to_msg_id", 0) or 0)
    return top_id


def _roster_matches_event(roster, event):
    if event is None:
        return True
    date = getattr(event, "date", None)
    if date is not None and date.timestamp() < float(roster.get("seen_at") or 0) - _XUANGU_ROOM_MATCH_LOOKBACK_SEC:
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if roster.get("game_chat_id") and int(roster["game_chat_id"]) != chat_id:
        return False
    topic_id = _event_topic_id(event)
    expected_topic = int(roster.get("game_topic_id") or 0)
    return not (topic_id and expected_topic and topic_id != expected_topic)


def _match_roster_for_round(ctx, room_id, panel, now, *, event=None):
    """明确房号必须匹配；缺少房号时，仅接受群内唯一的队长候选。"""
    candidates = [roster for roster in _active_xuangu_rosters(ctx, now)
                  if roster.get("phase") in ("joining", "running")
                  and not roster.get("disbanded") and _roster_matches_event(roster, event)]
    if room_id:
        matches = [roster for roster in candidates if str(roster.get("room_id") or "") == room_id]
        return matches[0] if len(matches) == 1 else None
    roles = panel.get("roles") or {}
    names = {str(v or "").lstrip("@").lower() for v in roles.values()}
    matches = [roster for roster in candidates
               if str(roster.get("leader_username") or "").lstrip("@").lower() in names]
    return matches[0] if len(matches) == 1 else None


def _match_roster_for_outcome(ctx, outcome, now, *, event=None):
    """Require room, owned reply, or complete named roster; shared broadcasts
    never belong to us merely because this host has one active room.
    """
    candidates = [roster for roster in _active_xuangu_rosters(ctx, now)
                  if roster.get("phase") in ("waiting_open", "joining", "running", "finishing", "held", "interrupted", "disbanded")
                  and (not roster.get("disbanded") or roster.get("requires_reconciliation"))]
    if event is not None:
        candidates = [r for r in candidates if _roster_matches_event(r, event)]
        room_id = _find_room_for_event(None, event)
        if room_id:
            matched = [r for r in candidates if str(r.get("room_id") or "") == room_id]
            return matched[0] if len(matched) == 1 else None
        reply_to = int(getattr(event, "reply_to_msg_id", 0) or getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
        if reply_to:
            matched = [r for r in candidates if reply_to in {
                int(r.get("enter_command_msg_id") or 0), int(r.get("finish_command_msg_id") or 0),
            } or any(int(a.get("msg_id") or 0) == reply_to
                     for actions in (r.get("round_actions") or {}).values() for a in actions.values())
                or any(int(a.get("msg_id") or 0) == reply_to for a in r.get("command_attempts", {}).values())]
            if len(matched) == 1:
                return matched[0]
        names = {name.lower() for name in re.findall(r"@([A-Za-z0-9_]{3,32})", str(getattr(event, "raw_text", "") or ""))}
        matched = [r for r in candidates if _roster_username_set(r) and _roster_username_set(r).issubset(names)]
        if len(matched) == 1:
            return matched[0]
    return None


async def _retry_xuangu_joins(ctx, roster, now):
    """加入失败重试：未确认加入的成员，按间隔重发，超过上限就放弃并告警。"""
    room_id = str(roster.get("room_id") or "")
    if not room_id:
        return
    # 超过确认窗口就不再重试（避免房间早就散了还在发指令）
    if now - float(roster.get("room_seen_at") or roster.get("seen_at") or now) > _XUANGU_JOIN_CONFIRM_SEC:
        return
    attempts = dict(roster.get("join_attempts") or {})
    join_msg_ids = dict(roster.get("join_msg_ids") or {})
    changed = False
    for member in roster.get("my_members") or []:
        username = str(member.get("username") or "")
        key = username.lstrip("@").lower()
        if roster.get("disbanded"):
            return
        confirmed = {str(name).lstrip("@").lower() for name in roster.get("join_confirmed_usernames") or []}
        if not key or key in confirmed or key == str(roster.get("leader_username") or "").lstrip("@").lower():
            continue
        record = attempts.get(username) or {}
        count = int(record.get("count") or 0)
        # A sent or uncertain join requires a real reply; never blindly replay it.
        if count > 0:
            continue
        last_at = float(record.get("last_at") or 0)
        if count >= _XUANGU_JOIN_MAX_ATTEMPTS:
            if not record.get("given_up"):
                record["given_up"] = True
                attempts[username] = record
                changed = True
                await ctx.messages.audit(
                    f"❌ 玄骨遗府加入失败（已重试 {count} 次）：@{username}｜房间 {room_id}",
                    tag="MiniApp",
                )
                if roster.get("assemble_chat_id"):
                    try:
                        await ctx.messages.notice_by_chat(
                            roster["assemble_chat_id"],
                            f"⚠️ @{username} 加入玄骨遗府失败（已重试 {count} 次），请人工处理",
                        )
                    except Exception:
                        pass
            continue
        if now - last_at < _XUANGU_JOIN_RETRY_GAP_SEC:
            continue
        dispatch_key = (roster.get("roster_id"), "join", key)
        if dispatch_key in _XUANGU_DISPATCHING:
            continue
        _XUANGU_DISPATCHING.add(dispatch_key)
        try:
            msg = await _send_xuangu_command(
                ctx, f"{ctx.commands.join} {room_id}", send_as_id=member.get("identity_id")
            )
            msg_id = int(getattr(msg, "id", 0) or 0)
            record["count"] = count + 1
            record["last_at"] = now
            attempts[username] = record
            if msg_id > 0:
                join_msg_ids[str(msg_id)] = username
                joined = set(roster.get("join_sent_usernames") or [])
                joined.add(username)
                roster["join_sent_usernames"] = sorted(joined)
            roster["join_attempts"] = attempts
            roster["join_msg_ids"] = join_msg_ids
            roster["updated_at"] = now
            _upsert_xuangu_roster(ctx, roster)
            changed = True
            await ctx.sleep(_XUANGU_JOIN_DISPATCH_GAP_SEC)
        finally:
            _XUANGU_DISPATCHING.discard(dispatch_key)
    if changed:
        roster["join_attempts"] = attempts
        roster["join_msg_ids"] = join_msg_ids
        roster["updated_at"] = now
        _upsert_xuangu_roster(ctx, roster)


async def run_xuangu_auto_scheduler(ctx: XuanguContext, now):
    """兜底：清过期花名册、开房超时提醒、加入失败重试。"""
    now = float(now or time.time())
    for roster in list(ctx.flows.load().values()):
        phase = roster.get("phase")
        # A lost join must not reserve five identities forever. Cancel only a
        # known captain's never-entered, expired assembly; retain ownership until
        # the captain's own cancellation/no-team reply confirms it is over.
        if (phase in {"held", "interrupted"} and roster.get("requires_reconciliation")
                and roster.get("auto_daily") and roster.get("is_captain") and roster.get("room_id")
                and not roster.get("entered") and not roster.get("enter_requested_at")
                and not roster.get("current_round")
                and not any(":.进入玄骨" in key for key in roster.get("command_attempts", {}))
                and 0 < float(roster.get("expires_at") or 0) <= now
                and not any(key.endswith(":" + ctx.commands.disband) for key in roster.get("command_attempts", {}))
                and len([r for r in ctx.flows.load().values() if r.get("requires_reconciliation")
                         and r.get("leader_identity_id") == roster.get("leader_identity_id")]) == 1):
            await _xuangu_disband(ctx, roster, now, "集结未进入且已过期，核对取消原房间；不重开、不重放加入")
            continue
        cutoff = 0
        if phase == "joining":
            cutoff = float(roster.get("room_seen_at") or 0) + _XUANGU_JOIN_WINDOW_SEC
        elif phase == "running" and roster.get("round_deadline"):
            cutoff = float(roster["round_deadline"]) + 45
        elif phase == "finishing" and roster.get("finish_deadline"):
            cutoff = float(roster["finish_deadline"]) + 45
        if cutoff and now > cutoff:
            roster.update(phase="held", requires_reconciliation=True,
                          last_error="未收到下一阶段或最终回执，保留房间待核对", updated_at=now)
            _upsert_xuangu_roster(ctx, roster)
            await ctx.messages.audit(f"⚠️ 玄骨待核对｜{roster.get('room_id')}｜{roster['last_error']}", tag="MiniApp")
    _cleanup_xuangu_rosters(ctx, now)
    for roster in _active_xuangu_rosters(ctx, now):
        if roster.get("phase") == "joining" and not roster.get("disbanded") and not roster.get("entered"):
            await _set_xuangu_confirmed_roles(ctx, roster, now)
            await _retry_xuangu_joins(ctx, roster, now)
            _schedule_xuangu_enter(ctx, roster)
    for roster in _active_xuangu_rosters(ctx, now):
        if roster.get("phase") != "waiting_open":
            continue
        if not roster.get("open_requested_at"):
            continue
        if now - float(roster.get("open_requested_at") or 0) < _XUANGU_OPEN_TIMEOUT_SEC:
            continue
        roster["phase"] = "held"
        roster["requires_reconciliation"] = True
        roster["last_error"] = "开房超时未收到集结播报"
        roster["updated_at"] = now
        _upsert_xuangu_roster(ctx, roster)
        await ctx.messages.audit(
            f"⚠️ 玄骨遗府开房超时｜队长 @{roster.get('leader_username')}",
            tag="MiniApp",
        )
    return False
