"""Financial evidence for Yinluo operations; no state writes or send decisions.

The gameplay parsers tolerate missing values for display and scheduling. This
contract must instead preserve unknown amounts and phase-specific charges.
"""

import re
from dataclasses import dataclass, replace

from .config import (
    CMD_WANXIN_ASSIST_BANNER,
    CMD_WANXIN_ASSIST_STRIP,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_DAILY_SACRIFICE,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_REFINE,
    CMD_YINLUO_SOOTHE,
)
from .message_log_recovery import sender_matches_identity
from .profile_observation import timestamp
from .resource_accounting import MAX_BALANCE, valid_point
from .verified_event import VerifiedGameEvent


_ACTIONS = {
    CMD_YINLUO_BANNER: "banner",
    CMD_YINLUO_BLOOD_FOREST: "forest",
    CMD_YINLUO_COLLECT: "collect",
    CMD_YINLUO_CONVERT: "convert",
    CMD_YINLUO_DAILY_SACRIFICE: "sacrifice",
    CMD_YINLUO_DEMON_SUMMON: "summon",
    CMD_YINLUO_REFINE: "refine",
    CMD_YINLUO_SOOTHE: "soothe",
    CMD_WANXIN_ASSIST_BANNER: "assist_banner",
    CMD_WANXIN_ASSIST_STRIP: "assist_strip",
}
_TOKEN = r"(?P<amount>[^\s]+?)"


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
        return None
    value = int(value)
    return value if value <= MAX_BALANCE else None


@dataclass(frozen=True)
class YinluoCommand:
    action: str
    text: str
    amount: int | None = None
    slot: int | None = None
    target: str = ""


def parse_yinluo_resource_command(text):
    if not isinstance(text, str) or len(text) > 256 or "\n" in text or "\r" in text:
        return None
    parts = text.split()
    if not parts or parts[0] not in _ACTIONS:
        return None
    action = _ACTIONS[parts[0]]
    normalized = " ".join(parts)
    if action == "convert":
        amount = _amount(parts[1]) if len(parts) == 2 else None
        return YinluoCommand(action, normalized, amount=amount) if amount is not None and amount > 0 else None
    if action in {"refine", "soothe", "collect"}:
        if action == "collect" and len(parts) == 1:
            return YinluoCommand(action, normalized)
        slot = _amount(parts[1]) if len(parts) >= 2 else None
        if slot is None or not 1 <= slot <= 99:
            return None
        if action == "refine":
            target = " ".join(parts[2:])
            if not target or len(target) > 80 or target.startswith("."):
                return None
            return YinluoCommand(action, normalized, slot=slot, target=target)
        return YinluoCommand(action, normalized, slot=slot) if len(parts) == 2 else None
    if action in {"assist_banner", "assist_strip"}:
        if len(parts) != 2 or not re.fullmatch(r"@[A-Za-z0-9_]{1,32}", parts[1]):
            return None
        return YinluoCommand(action, normalized, target=parts[1][1:].casefold())
    return YinluoCommand(action, normalized) if len(parts) == 1 else None


@dataclass(frozen=True)
class ResourceEffect:
    component: str
    resource: str
    amount: int | None
    subject: str = "actor"

    @property
    def scope(self):
        return "charge" if self.component in {"summon_cost", "forest_sha"} else "outcome"


@dataclass(frozen=True)
class YinluoResourceReply:
    command: YinluoCommand
    phase: str
    required: tuple[tuple[str, str], ...]
    effects: tuple[ResourceEffect, ...]
    issues: tuple[str, ...] = ()
    actor_username: str = ""
    target_username: str = ""

    @property
    def scopes(self):
        """Revision scopes, not a terminal/pending decision for the scheduler."""
        scopes = {item.scope for item in self.effects}
        if self.phase in {"success", "failed"}:
            scopes.add("outcome")
        return tuple(sorted(scopes))


def _required(command):
    return {
        "convert": (("convert_cost", "cultivation"), ("convert_sha", "sha")),
        "soothe": (("soothe_cost", "cultivation"),),
        "sacrifice": (("sacrifice_sha", "sha"),),
        "summon": (("summon_cost", "cultivation"),),
        "refine": (("refine_sha", "sha"), ("refine_soul", f"soul:{command.target}")),
        "assist_banner": (("assist_sha", "sha"),),
        "assist_strip": (("assist_sha", "sha"),),
    }.get(command.action, ())


def _one_amount(pattern, text):
    matches = list(re.finditer(pattern, text))
    return _amount(matches[0].group("amount")) if len(matches) == 1 else None


def parse_yinluo_resource_reply(command_text, text):
    """Return explicit components, keeping missing dependencies out of deltas.

    `required` is not a list of new debits. For example, a final summon reply
    requires its start charge, but must not replace that charge with an unknown
    amount just because the final edit no longer repeats the start sentence.
    """
    command = parse_yinluo_resource_command(command_text)
    if command is None or not isinstance(text, str) or len(text) > 32000:
        return None
    text = text.replace("**", "").replace("`", "").strip()
    action = command.action
    required = list(_required(command))
    effects = []
    issues = []
    actor_username = ""
    target_username = ""

    def finish(phase):
        return YinluoResourceReply(
            command, phase, tuple(required), tuple(effects), tuple(issues),
            actor_username, target_username,
        )

    def effect(component, resource, amount, *, cost=False, subject="actor"):
        if amount is None:
            issues.append(f"{component}_amount_unknown")
        effects.append(ResourceEffect(component, resource, -amount if cost and amount is not None else amount, subject))
        if (component, resource) not in required:
            required.append((component, resource))

    def denied():
        if effects:
            issues.append("rejection_with_resource_effect")
            return finish("conflict")
        required.clear()
        return finish("denied")

    def sha_income(prefix):
        effect(f"{prefix}_sha", "sha", _one_amount(r"煞气池增加了\s*" + _TOKEN + r"\s*点", text))
        if "额外获得了" in text and "精纯煞气" in text:
            effect(f"{prefix}_bonus", "sha", _one_amount(r"额外获得了\s*" + _TOKEN + r"\s*点精纯煞气", text))

    if "你并非阴罗宗弟子" in text:
        if any(marker in text for marker in (
            "【转化成功】", "【转化失败·反噬】", "你消耗了", "安抚成功",
            "召唤成功，镇压", "【血洗功成】", "一缕【", "煞气池增加了",
            "幡面煞气被削去", "阴罗幡煞气被吞去",
        )):
            issues.append("conflicting_outcomes")
            return finish("conflict")
        return denied()
    if action == "banner":
        phase = "panel" if re.search(r"【[^】\n]+的阴罗幡】", text) else "unknown"
        return finish(phase)
    if action == "convert":
        success = "【转化成功】" in text
        failed = "【转化失败·反噬】" in text or ("魔功失控" in text and "煞气反噬" in text)
        if success and failed:
            issues.append("conflicting_outcomes")
            return finish("conflict")
        if success or failed:
            pattern = r"成功将\s*" + _TOKEN + r"\s*点修为炼化" if success else r"(?:消耗的|消耗了)\s*" + _TOKEN + r"\s*点修为"
            cost = _one_amount(pattern, text)
            if cost is not None and cost != command.amount:
                # The exact original command establishes the actor. Its requested
                # amount cannot overrule the server's explicit actual charge.
                issues.append("convert_requested_amount_differs")
            effect("convert_cost", "cultivation", cost, cost=True)
            if success:
                sha_income("convert")
            else:
                required.remove(("convert_sha", "sha"))
            return finish("success" if success else "failed")
        if "你开始运转魔功" in text and "煞气" in text:
            return finish("pending")
        if ("刚施展过此术" in text and "经脉尚在恢复" in text) or "每次转化的修为需在" in text:
            return denied()
    elif action == "soothe":
        if "安抚成功" in text and "炼化槽" in text:
            effect("soothe_cost", "cultivation", _one_amount(r"消耗了\s*" + _TOKEN + r"\s*点修为", text), cost=True)
            return finish("success")
    elif action == "sacrifice":
        if "你引动九幽煞气灌入幡中" in text:
            sha_income("sacrifice")
            return finish("success")
        if "今日已献祭" in text and "幡灵已饱" in text:
            return denied()
    elif action == "summon":
        success = "召唤成功，镇压成功" in text
        failed = "召唤成功，镇压失败" in text
        if success and failed:
            issues.append("conflicting_outcomes")
            return finish("conflict")
        if "召唤魔域的投影" in text and "消耗了" in text:
            effect("summon_cost", "cultivation", _one_amount(r"你消耗了\s*" + _TOKEN + r"\s*点修为", text), cost=True)
        if failed:
            effect("summon_backlash", "cultivation", _one_amount(r"修为暴跌了\s*" + _TOKEN + r"\s*点", text), cost=True)
            return finish("failed")
        if success:
            matches = list(re.finditer(r"留下了一道精纯的【(?P<name>[^】\n]{1,80})】", text))
            if len(matches) == 1:
                effect("summon_soul", f"soul:{matches[0].group('name')}", 1)
            else:
                effect("summon_soul", "souls", None)
            return finish("success")
        if "召唤魔域的投影" in text or ("魔影已降临" in text and "神魂角力" in text):
            return finish("pending")
        if "魔域裂隙尚未平复" in text or "神魂之力不足以撕开魔域裂隙" in text:
            return denied()
    elif action == "forest":
        if "消耗了" in text and "点煞气" in text:
            effect("forest_sha", "sha", _one_amount(r"你消耗了\s*" + _TOKEN + r"\s*点煞气", text), cost=True)
        if "【血洗功成】" in text:
            for component, marker in (("forest_soul", "成功捕获了"), ("forest_bonus_soul", "额外拘来")):
                if component == "forest_bonus_soul" and marker not in text:
                    continue
                matches = list(re.finditer(marker + r"\s*" + _TOKEN + r"\s*缕【(?P<name>[^】\n]{1,80})】", text))
                if len(matches) == 1:
                    effect(component, f"soul:{matches[0].group('name')}", _amount(matches[0].group("amount")))
                else:
                    effect(component, "souls", None)
            return finish("success")
        if "你催动煞气" in text and "扫荡" in text:
            return finish("pending")
        if "生灵尚未恢复" in text and "煞气稀薄" in text:
            return denied()
    elif action == "refine":
        matches = list(re.finditer(r"一缕【(?P<name>[^】\n]{1,80})】被强行打入(?P<slot>[0-9]+)号炼化槽", text))
        if matches:
            if len(matches) != 1 or matches[0].group("name") != command.target or _amount(matches[0].group("slot")) != command.slot:
                issues.append("refine_target_mismatch")
                return finish("conflict")
            effect("refine_soul", f"soul:{command.target}", 1, cost=True)
            if "消耗了" in text and "点煞气" in text:
                effect("refine_sha", "sha", _one_amount(r"(?:你)?消耗了\s*" + _TOKEN + r"\s*点煞气", text), cost=True)
            return finish("success")
        if "你的煞气不足" in text or "魂魄袋中没有【" in text or ("炼化槽正在运转中" in text and "无法囚禁" in text):
            return denied()
    elif action in {"assist_banner", "assist_strip"}:
        failed = action == "assist_strip" and "【剥离咒源失败】" in text
        success = (action == "assist_banner" and "【借幡镇魂】" in text) or (
            action == "assist_strip" and ("【剥离咒源成功】" in text or (
                not failed and ("剥下一段阴罗残咒" in text or "剥离阴罗残咒" in text)
            ))
        )
        action_name = "借幡镇魂" if action == "assist_banner" else "剥离咒源"
        rejected = any(marker in text for marker in (
            "阴罗幡煞气不足", "你与对方没有有效的咒契协定", "咒源尚未辨明",
        )) or (
            action_name in text and "冷却" in text
            and re.search(r"请在\s*[^\n]+?\s*后再试", text) is not None
        )
        if (success and failed) or (rejected and (success or failed or any(
            marker in text for marker in ("幡面煞气被削去", "阴罗幡煞气被吞去", "修为折损")
        ))):
            issues.append("conflicting_outcomes")
            return finish("conflict")
        if rejected:
            return denied()
        if success or failed:
            actors = set(re.findall(r"@([A-Za-z0-9_]{1,32})\s+(?:借阴罗幡|以阴罗幡|替\s+@)", text))
            targets = set(re.findall(r"(?:替\s+@|@)([A-Za-z0-9_]{1,32})\s+(?:剥下一段|剥离阴罗残咒|魂封\s*[+\-])", text))
            actors = {name.casefold() for name in actors}
            targets = {name.casefold() for name in targets}
            if len(actors) > 1 or len(targets) > 1:
                issues.append("conflicting_assist_participants")
                return finish("conflict")
            actor_username = next(iter(actors), "")
            target_username = next(iter(targets), "")
            if "幡面煞气被削去" in text or "阴罗幡煞气被吞去" in text:
                effect("assist_sha", "sha", _one_amount(r"(?:幡面煞气被削去|阴罗幡煞气被吞去)\s*" + _TOKEN + r"\s*点", text), cost=True)
            if failed:
                matches = list(re.finditer(r"@(?P<owner>[A-Za-z0-9_]{1,32})\s+修为折损\s*(?P<amount>[^\s，,。]+)", text))
                if len(matches) == 1:
                    effect("assist_backlash", "cultivation", _amount(matches[0].group("amount").removesuffix("点")),
                           cost=True, subject=f"@{matches[0].group('owner').casefold()}")
                else:
                    effect("assist_backlash", "cultivation", None, cost=True)
            return finish("success" if success else "failed")
    elif action == "collect" and "收取成功！" in text and "炼化槽" in text:
        # Lineage progress is not another unrefined soul in the resource bag.
        return finish("success")
    return finish("unknown")


@dataclass(frozen=True)
class OwnedYinluoSource:
    identity_id: int
    account_id: int
    command: YinluoCommand
    chat_id: int
    command_msg_id: int
    command_at: float
    result_msg_id: int
    result_at: float
    edited: bool
    result_sender_id: int

    @property
    def start(self):
        return {"at": self.command_at, "evidence": {
            "source": "telegram", "chat_id": self.chat_id, "msg_id": self.command_msg_id, "edited": False,
        }}

    @property
    def end(self):
        return {"at": self.result_at, "evidence": {
            "source": "telegram", "chat_id": self.chat_id, "msg_id": self.result_msg_id, "edited": self.edited,
        }}

    def component_key(self, component):
        if not isinstance(component, str) or not re.fullmatch(r"[a-z_]{1,64}", component):
            raise ValueError("invalid Yinluo resource component")
        return f"yinluo_{component}:command:{self.chat_id}:{self.command_msg_id}"


def qualify_yinluo_resource_reply(source, reply, identity_usernames):
    """Validate named participants against the already owned original command.

    Aliases can confirm a rename, never choose a different payer. Contradictory
    or ambiguous names retain unknown effects for the actor, not debits to the
    named beneficiary. The caller must still check the current owner/account.
    """
    if (
        not isinstance(source, OwnedYinluoSource) or not isinstance(reply, YinluoResourceReply)
        or source.command != reply.command or not isinstance(identity_usernames, dict)
    ):
        return None
    usernames = {}
    for identity_id, names in identity_usernames.items():
        if type(identity_id) is not int or identity_id <= 0 or not isinstance(names, (list, tuple, set, frozenset)):
            return None
        usernames[identity_id] = set()
        for name in names:
            if not isinstance(name, str) or not re.fullmatch(r"@?[A-Za-z0-9_]{1,32}", name):
                return None
            usernames[identity_id].add(name.removeprefix("@").casefold())

    def owners(name):
        return {identity_id for identity_id, names in usernames.items() if name in names}

    issues = []
    if reply.actor_username and owners(reply.actor_username) != {source.identity_id}:
        issues.append("assist_actor_unverified")
    if reply.target_username:
        expected = source.command.target
        expected_owners, actual_owners = owners(expected), owners(reply.target_username)
        if (
            len(expected_owners) > 1 or len(actual_owners) > 1
            or (expected != reply.target_username and (not expected_owners or expected_owners != actual_owners))
        ):
            issues.append("assist_target_mismatch")
    if any(
        effect.subject != "actor" and owners(effect.subject.removeprefix("@")) != {source.identity_id}
        for effect in reply.effects
    ):
        issues.append("resource_payer_unverified")
    return replace(
        reply, phase="conflict" if issues else reply.phase,
        effects=tuple(replace(effect, amount=None if issues else effect.amount, subject="actor") for effect in reply.effects),
        issues=(*reply.issues, *issues),
    )


def admit_yinluo_resource_source(event, *, identity_accounts, game_chats, game_bots, now):
    """Admit a native command/result pair, without choosing an account fallback."""
    def valid_ids(values, *, positive=False):
        return isinstance(values, (set, frozenset, list, tuple)) and all(
            type(value) is int and (value > 0 if positive else value != 0) for value in values
        )

    if (
        not isinstance(event, VerifiedGameEvent) or event.event_type not in {"message", "edit"}
        or not valid_ids(game_chats) or not valid_ids(game_bots, positive=True)
        or type(event.chat_id) is not int or event.chat_id not in game_chats
        or type(event.sender_id) is not int or event.sender_id not in game_bots
        or type(event.msg_id) is not int or event.msg_id <= 0
        or not isinstance(event.reply_context, dict)
        or not isinstance(identity_accounts, dict)
    ):
        return None
    context = event.reply_context
    command = parse_yinluo_resource_command(context.get("reply_to_command"))
    root_id = context.get("reply_to_msg_id")
    sender_id = context.get("reply_to_sender_id")
    command_at = timestamp(context.get("reply_to_server_at"))
    def matching_hint(key, expected, *, unresolved=False):
        if key not in context:
            return True
        value = context[key]
        # get_reply_context uses None/0 for an unresolved identity. Neither is
        # an account fallback; ownership still comes from the native sender.
        if unresolved and (value is None or (type(value) is int and value == 0)):
            return True
        return type(value) is int and value == expected

    end = {"at": event.server_event_at, "evidence": {
        "source": "telegram", "chat_id": event.chat_id, "msg_id": event.msg_id, "edited": event.is_edited_delivery,
    }}
    if (
        command is None or context.get("reply_to_command_edited") is not False
        or type(root_id) is not int or not 0 < root_id < event.msg_id
        or type(sender_id) is not int or sender_id == 0 or sender_id in game_bots
        or type(event.reply_to_sender_id) is not int or event.reply_to_sender_id != sender_id
        or not matching_hint("chat_id", event.chat_id)
        or not matching_hint("root_msg_id", root_id)
        or type(event.root_msg_id) is not int or event.root_msg_id not in {0, root_id}
        or not valid_point(end, telegram_only=True) or command_at <= 0
        or command_at > event.server_event_at or timestamp(now) <= 0 or event.server_event_at > timestamp(now) + 1
    ):
        return None
    owners = [
        identity_id for identity_id in identity_accounts
        if type(identity_id) is int and identity_id > 0 and sender_matches_identity(sender_id, identity_id)
    ]
    if len(owners) != 1:
        return None
    identity_id = owners[0]
    account_id = identity_accounts[identity_id]
    if (
        type(account_id) is not int or account_id <= 0
        or type(event.identity_id) is not int or event.identity_id not in {0, identity_id}
        or not matching_hint("send_as_id", identity_id, unresolved=True)
        or not matching_hint("account_id", account_id)
    ):
        return None
    return OwnedYinluoSource(identity_id, account_id, command, event.chat_id, root_id, command_at,
                            event.msg_id, event.server_event_at, event.is_edited_delivery, event.sender_id)


@dataclass(frozen=True)
class YinluoResourcePanel:
    sha: int | None
    sha_max: int | None
    souls: tuple[tuple[str, int], ...] | None
    issues: tuple[str, ...] = ()


def parse_yinluo_resource_panel(text):
    """Read independent absolute fields, without making omitted stock zero.

    A panel is still not an authoritative balance until its caller supplies
    owned, ordered native evidence. In particular, an HTTP request clock does
    not become a server snapshot revision by passing this parser.
    """
    if not isinstance(text, str) or len(text) > 32000:
        return None
    lines = [line.strip() for line in text.replace("**", "").replace("`", "").strip().splitlines()]
    if (
        not lines or not re.fullmatch(r"【[^】\n]{1,80}的阴罗幡】", lines[0])
        or sum(bool(re.fullmatch(r"【[^】\n]{1,80}的阴罗幡】", line)) for line in lines) != 1
    ):
        return None
    issues = []
    sha = sha_max = None
    sha_lines = [line for line in lines if re.match(r"煞气池[:：]", line)]
    if sha_lines:
        match = re.fullmatch(r"煞气池[:：]\s*(\S+)\s*/\s*(\S+)\s*\(([0-9]{1,3})%\)", sha_lines[0])
        if (
            len(sha_lines) != 1 or match is None
            or _amount(match.group(1)) is None or _amount(match.group(2)) is None
            or int(match.group(3)) > 100
        ):
            issues.append("invalid_sha_panel")
        else:
            sha, sha_max = _amount(match.group(1)), _amount(match.group(2))
    souls = None
    headers = [index for index, line in enumerate(lines) if re.fullmatch(r"魂魄储备[:：]", line)]
    if headers:
        stocks = {}
        valid = len(headers) == 1
        for line in lines[headers[0] + 1:]:
            if not line:
                continue
            if re.fullmatch(r"(?:炼化槽|幡魂谱系|当前特性|命令)[:：]", line):
                break
            match = re.fullmatch(r"-\s*([^:：\n]{1,80})[:：]\s*(\S+)\s*缕", line)
            if match is None:
                valid = False
                break
            name, count = match.group(1).strip(), _amount(match.group(2))
            if not name or name in stocks or "·" in name or count is None:
                valid = False
                break
            stocks[name] = count
        if valid and stocks:
            souls = tuple(sorted(stocks.items()))
        else:
            issues.append("incomplete_soul_panel")
    return YinluoResourcePanel(sha, sha_max, souls, tuple(issues))
