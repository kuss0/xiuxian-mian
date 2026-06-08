#!/usr/bin/env python3
"""Offline game-record and command analysis for the xiuxian automation repo.

The script is deliberately read-only for runtime state: it reads saved JSONL
message logs, optionally reads xiuxian-mini-web's SQLite database, and writes
analysis artifacts. It never imports Telethon runtime modules and never sends
Telegram messages.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MESSAGES_DIR = PROJECT_ROOT / "data" / "messages"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "analysis"
DEFAULT_MINIWEB_DB = Path("/root/xiuxian-mini-web/data/miniweb.db")
DEFAULT_MINIWEB_ROOT = Path("/root/xiuxian-mini-web")

DEFAULT_GAME_BOT_IDS = {
    -1003983937918,
    7900199668,
    8349385938,
    8388633812,
    8400307678,
    8547797815,
    8567800706,
    8609885831,
    8757550896,
}
DEFAULT_FOCUS_SENDERS = (301299112,)

SPECIAL_TWO_TOKEN_COMMANDS = {
    ".神迹": {".神迹 布道", ".神迹 赈灾"},
    ".交换": {".交换 法宝", ".交换 功法"},
    ".抉择": {".抉择 强行突破", ".抉择 稳固道心"},
    ".开启": {".开启副本", ".开启虚天殿", ".开启全部"},
    ".关闭": {".关闭全部"},
}

COMMAND_FAMILY_PREFIXES = (
    ("identity", (".我的灵根", ".他的灵根", ".检测灵根", ".切换", ".战力")),
    ("status", (".状态", ".我的状态", ".status", ".查看")),
    ("sect", (".宗门点卯", ".宗门传功", ".宗门宝库", ".宗门捐献", ".宗门悬赏", ".我的宗门", ".拜入宗门", ".加入宗门", ".宗门列表", ".宗门战况", ".每日问安", ".晋升长老", ".晋升星宫长老", ".叛出宗门")),
    ("market", (".万宝楼", ".我的货摊", ".上架", ".购买", ".下架", ".兑换", ".小卖部", ".上架至万宝阁", ".从万宝阁取下", ".换取", ".购入")),
    ("stock", (".我的持仓", ".买入", ".卖出", ".股市任务", ".领股息", ".大盘", ".资产", ".股市", ".融资买入", ".推演", ".个股")),
    ("herb_garden", (".小药园", ".播种", ".采药", ".除草", ".除虫", ".浇水", ".扩建药园")),
    ("alchemy", (".炼制", ".学习", ".服用", ".使用")),
    ("divination", (".卜筮问天", ".解答", ".卜卦", ".天机回溯", ".推命", ".天机盘", ".天机遭遇战")),
    ("formation", (".启阵", ".助阵", ".布下剑阵", ".切换阵势", ".升级大阵", ".激活阵盘", ".借天门势", ".参战")),
    ("spirit_beast", (".我的灵兽", ".灵兽偷菜", ".灵兽出战", ".寻觅灵兽", ".喂养", ".喂养灵兽", ".放生", ".灵兽休息", ".灵兽改名", ".灵兽互动", ".灵兽巡游", ".灵兽升星", ".灵兽状态", ".召回灵兽", ".灵兽召回", ".灵兽抚摸", ".孵化灵兽蛋")),
    ("cave", (".洞府", ".开辟洞府", ".升级灵脉", ".升级静室", ".升级丹房", ".升级器室", ".布置景观", ".拜访洞府", ".查看访客", ".接待访客", ".洞天绘卷", ".洞天寻宝", ".开辟小世界", ".开启小世界", ".掌天瓶", ".神庙", ".升级神庙", ".护界禁制")),
    ("blood_trial", (".血色抉择", ".开启血色试炼", ".进入血色试炼", ".加入血色试炼")),
    ("duel", (".对决", ".斗法", ".抢", ".血洗山林", ".探渊", ".问道", ".召唤魔影", ".押", ".赌石", ".roll")),
    ("admin_game", (".admin", ".验证", ".举报机器人", ".举报", ".赎罪", ".授权")),
    ("choice", (".选择", ".选择道路", ".需求", ".邀请链接")),
    ("checkin", (".宗门点卯", ".宗门传功")),
    ("tower", (".闯塔", ".重置古塔", ".继续闯塔")),
    ("pet", (".抚摸法宝", ".温养器灵", ".器灵试炼", ".我的器灵", ".器灵", ".唤醒器灵", ".器灵护主", ".法宝", ".装备", ".卸下法宝")),
    ("ranch", (".一键放养",)),
    ("wild_training", (".野外历练",)),
    ("tree", (".灵树灌溉", ".灵树状态", ".协同守山", ".采摘灵果")),
    ("stargazer", (".观星台", ".牵引星辰", ".安抚星辰", ".收集精华")),
    ("guanxing", (".观星", ".改换星移")),
    ("tianti", (".天阶状态", ".问心台", ".登天阶", ".引九天罡风")),
    ("yuanying", (".元婴出窍", ".元婴状态", ".元婴闭关", ".元婴归窍", ".冲击元婴")),
    ("deep_retreat", (".深度闭关", ".查看闭关", ".闭关修炼", ".出关", ".强行出关")),
    ("small_world", (".小世界", ".显灵", ".收割香火", ".神识淬炼", ".神迹", ".神迹 布道", ".神迹 赈灾")),
    (
        "concubine",
        (
            ".我的侍妾",
            ".查看侍妾",
            ".入梦寻图",
            ".残图",
            ".拼图",
            ".宗门赐婚",
            ".红尘寻缘",
            ".天机代卜",
            ".共历心劫",
            ".稳",
            ".狠",
            ".骗",
            ".立誓",
            ".立誓确认",
            ".毁誓",
            ".赠予侍妾",
            ".赠送侍妾",
            ".安置侍妾",
            ".召回侍妾",
            ".遣散侍妾",
            ".请侍妾护法",
            ".侍妾卜算",
            ".入梦",
        ),
    ),
    ("nanlong", (".交换 法宝", ".交换 功法", ".拒绝交易")),
    ("hehuan", (".闭关双修", ".缔结同参", ".双修", ".种下心印", ".挣脱心印")),
    ("yinluo", (".我的阴罗幡", ".坠魔心劫", ".囚禁魂魄", ".化功为煞", ".一键安抚幡灵")),
    ("tianxing", (".星宫", ".观命", ".定命", ".扩建星台", ".星盘")),
    ("second_soul", (".第二元神", ".元神修炼", ".抉择 强行突破", ".抉择 稳固道心")),
    ("taiyi", (".引道", ".搜寻节点", ".定星")),
    ("explore_rift", (".探寻裂缝", ".探寻裂逢", ".探寻")),
    ("storage_bag", (".储物袋", ".上架", ".购买", ".赠送")),
    (
        "replica",
        (
            ".加入副本",
            ".加入坠魔谷",
            ".加入黄龙山",
            ".加入苍坤洞府",
            ".加入落云秘圃",
            ".查询副本",
            ".开启副本",
            ".加入副本",
            ".解散副本",
            ".匹配虚天殿",
            ".开启虚天殿",
            ".进入虚天殿",
            ".请离",
            ".虚天殿",
            ".坠魔谷",
            ".黄龙山",
            ".苍坤洞府",
            ".开启坠魔谷",
            ".开启黄龙山",
            ".开启苍坤洞府",
            ".解散坠魔谷",
            ".解散黄龙山",
            ".解散苍坤洞府",
            ".苍坤抉择",
            ".黄龙抉择",
            ".阵策",
            ".后殿抉择",
            ".后殿阵策",
            ".开启昆吾山",
            ".加入昆吾山",
            ".进入昆吾山",
            ".解散昆吾山",
            ".开启落云秘圃",
            ".加入落云秘圃",
            ".进入落云秘圃",
            ".落云抉择",
            ".开启坠魔谷",
            ".进入坠魔谷",
            ".开启黄龙山",
            ".进入黄龙山",
            ".开启苍坤洞府",
            ".进入苍坤洞府",
            ".争鼎",
        ),
    ),
    ("quiz", (".作答",)),
    ("tiandao", (".自证",)),
    ("jiyin", (".献上魂魄", ".收敛气息")),
    ("task", (".提交任务",)),
    ("log_group", (".帮助", ".指令", ".全局暂停", ".全局恢复", ".登录", ".开启全部", ".关闭全部")),
)

CORE_COMMAND_FAMILY_OVERRIDES = {
    ".宗门点卯": "checkin",
    ".宗门传功": "checkin",
    ".储物袋": "storage_bag",
    ".上架": "storage_bag",
    ".购买": "storage_bag",
    ".赠送": "storage_bag",
}

BOT_REPLY_MARKERS = (
    ("cooldown", ("请在", "后再试", "冷却", "调息")),
    ("resource_shortage", ("修为不足", "灵石不足", "贡献不足", "资源不足", "神念不足")),
    ("no_sect", ("散修无需点卯", "寻一宗门拜入", "尚未加入宗门")),
    ("success", ("成功", "获得", "增加", "已开启", "已加入", "已点卯")),
    ("failure", ("失败", "无法", "没有", "不足", "未能", "找不到")),
    ("risk", ("封禁", "天牢", "挂机嫌疑", "天道审判", "举报", "虚弱", "走火入魔")),
    ("replica", ("虚天殿", "坠魔谷", "黄龙山", "苍坤", "副本")),
    ("storage_bag", ("储物袋", "交易成功", "赠送", "上架")),
    ("concubine", ("侍妾", "道侣", "情缘", "心劫", "天机代卜")),
)

HARD_STOP_KEYWORDS = (
    "TG FloodWait",
    "FloodWait",
    "UserDeactivatedBan",
    "USER_DEACTIVATED_BAN",
    "AUTH_KEY_UNREGISTERED",
    "PHONE_NUMBER_BANNED",
    "已被封禁",
    "你已被封禁",
    "关入天牢",
    "被关天牢",
    "账号异常",
)


@dataclass
class CommandStats:
    count: int = 0
    sent_count: int = 0
    message_count: int = 0
    edit_count: int = 0
    first_ts: str = ""
    last_ts: str = ""
    senders: Counter = field(default_factory=Counter)
    chats: Counter = field(default_factory=Counter)
    topics: Counter = field(default_factory=Counter)
    examples: list[dict] = field(default_factory=list)

    def add(self, row: dict, *, example_limit: int) -> None:
        self.count += 1
        event_type = str(row.get("event_type") or "")
        if event_type == "sent":
            self.sent_count += 1
        elif event_type == "edit":
            self.edit_count += 1
        else:
            self.message_count += 1
        ts = str(row.get("ts") or "")
        if ts and (not self.first_ts or ts < self.first_ts):
            self.first_ts = ts
        if ts and ts > self.last_ts:
            self.last_ts = ts
        sender_id = int_or_zero(row.get("sender_id"))
        chat_id = int_or_zero(row.get("chat_id"))
        topic_id = int_or_zero(row.get("topic_id"))
        if sender_id:
            self.senders[str(sender_id)] += 1
        if chat_id:
            self.chats[str(chat_id)] += 1
        if topic_id:
            self.topics[str(topic_id)] += 1
        if len(self.examples) < example_limit:
            self.examples.append(
                {
                    "ts": ts,
                    "event_type": event_type,
                    "sender_id": sender_id,
                    "chat_id": chat_id,
                    "message_id": int_or_zero(row.get("message_id")),
                    "text": compact_text(str(row.get("text") or ""), 180),
                }
            )


@dataclass
class SentEvent:
    ts: str
    epoch: float
    sender_id: int
    chat_id: int
    topic_id: int
    message_id: int
    reply_to_msg_id: int
    text: str
    command: str
    family: str
    source_file: str
    line_no: int


@dataclass
class Analysis:
    scanned_lines: int = 0
    invalid_json: int = 0
    source_files: Counter = field(default_factory=Counter)
    event_types: Counter = field(default_factory=Counter)
    dates: Counter = field(default_factory=Counter)
    command_stats: dict[str, CommandStats] = field(default_factory=lambda: defaultdict(CommandStats))
    command_families: Counter = field(default_factory=Counter)
    unknown_commands: Counter = field(default_factory=Counter)
    sent_events: list[SentEvent] = field(default_factory=list)
    sent_by_sender: Counter = field(default_factory=Counter)
    sent_by_family: Counter = field(default_factory=Counter)
    log_group_command_stats: dict[str, CommandStats] = field(default_factory=lambda: defaultdict(CommandStats))
    bot_reply_headers: Counter = field(default_factory=Counter)
    bot_reply_categories: Counter = field(default_factory=Counter)
    bot_reply_by_parent: dict[int, list[dict]] = field(default_factory=lambda: defaultdict(list))
    hard_stop_hits: list[dict] = field(default_factory=list)
    focus_sender_rows: dict[int, list[dict]] = field(default_factory=lambda: defaultdict(list))
    focus_sender_commands: dict[int, Counter] = field(default_factory=lambda: defaultdict(Counter))


def int_or_zero(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_ts_epoch(ts: str) -> float:
    text = str(ts or "")[:19]
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(datetime.strptime(text, fmt).timetuple())
        except ValueError:
            continue
    return 0.0


def compact_text(text: str, limit: int = 120) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def command_key(text: str) -> str:
    raw = str(text or "").strip()
    if not raw.startswith("."):
        return ""
    raw = re.sub(r"\s+", " ", raw)
    parts = raw.split(" ")
    first = parts[0]
    if first in SPECIAL_TWO_TOKEN_COMMANDS and len(parts) >= 2:
        candidate = f"{first} {parts[1]}"
        if candidate in SPECIAL_TWO_TOKEN_COMMANDS[first]:
            return candidate
    if first.startswith(".加入") and first in {".加入副本", ".加入坠魔谷", ".加入黄龙山", ".加入苍坤洞府", ".加入昆吾山", ".加入落云秘圃"}:
        return first
    if first in {
        ".器灵试炼",
        ".温养器灵",
        ".神识淬炼",
        ".定星",
        ".引道",
        ".作答",
        ".自证",
        ".苍坤抉择",
        ".虚天殿",
        ".坠魔谷",
        ".黄龙山",
        ".开启副本",
        ".加入副本",
        ".匹配虚天殿",
        ".开启虚天殿",
        ".请离",
        ".赠送",
        ".上架",
        ".购买",
    }:
        return first
    return first


def command_family(command: str) -> str:
    if command in CORE_COMMAND_FAMILY_OVERRIDES:
        return CORE_COMMAND_FAMILY_OVERRIDES[command]
    for family, prefixes in COMMAND_FAMILY_PREFIXES:
        for prefix in prefixes:
            if command == prefix or command.startswith(prefix + " "):
                return family
    return "unknown"


def load_env_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def load_env_int(env_path: Path, key: str, default: int = 0) -> int:
    try:
        return int(load_env_value(env_path, key) or default)
    except (TypeError, ValueError):
        return default


def iter_log_files(messages_dir: Path, since: str = "", until: str = "", include_replica: bool = True) -> list[Path]:
    if not messages_dir.exists():
        return []
    files = []
    for path in sorted(messages_dir.glob("*.log")):
        stem = path.stem
        if stem.startswith("replica-") and not include_replica:
            continue
        date_key = stem.replace("replica-", "", 1)
        if since and date_key < since:
            continue
        if until and date_key > until:
            continue
        files.append(path)
    return files


def analyze_jsonl_logs(
    messages_dir: Path,
    *,
    since: str = "",
    until: str = "",
    include_replica: bool = True,
    game_bot_ids: set[int] | None = None,
    focus_senders: Iterable[int] = DEFAULT_FOCUS_SENDERS,
    log_group_id: int = 0,
    example_limit: int = 5,
) -> Analysis:
    bot_ids = set(game_bot_ids or DEFAULT_GAME_BOT_IDS)
    focus_ids = {int(item) for item in focus_senders}
    analysis = Analysis()
    files = iter_log_files(messages_dir, since=since, until=until, include_replica=include_replica)
    for log_file in files:
        with log_file.open("r", encoding="utf-8") as handle:
            for line_no, raw_line in enumerate(handle, 1):
                analysis.scanned_lines += 1
                analysis.source_files[log_file.name] += 1
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError:
                    analysis.invalid_json += 1
                    continue
                if not isinstance(row, dict):
                    analysis.invalid_json += 1
                    continue
                event_type = str(row.get("event_type") or "message")
                text = str(row.get("text") or "")
                sender_id = int_or_zero(row.get("sender_id"))
                chat_id = int_or_zero(row.get("chat_id"))
                topic_id = int_or_zero(row.get("topic_id"))
                message_id = int_or_zero(row.get("message_id"))
                reply_to_msg_id = int_or_zero(row.get("reply_to_msg_id"))
                ts = str(row.get("ts") or "")
                analysis.event_types[event_type] += 1
                if len(ts) >= 10:
                    analysis.dates[ts[:10]] += 1
                command = command_key(text)
                if sender_id in focus_ids:
                    focus_row = {
                        "ts": ts,
                        "event_type": event_type,
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "reply_to_msg_id": reply_to_msg_id,
                        "command": command,
                        "text": compact_text(text, 180),
                        "source_file": log_file.name,
                        "line_no": line_no,
                    }
                    if len(analysis.focus_sender_rows[sender_id]) < 200:
                        analysis.focus_sender_rows[sender_id].append(focus_row)
                    if command:
                        analysis.focus_sender_commands[sender_id][command] += 1
                if event_type == "sent":
                    family = command_family(command) if command else "non_command"
                    sent = SentEvent(
                        ts=ts,
                        epoch=parse_ts_epoch(ts),
                        sender_id=sender_id,
                        chat_id=chat_id,
                        topic_id=topic_id,
                        message_id=message_id,
                        reply_to_msg_id=reply_to_msg_id,
                        text=text.strip(),
                        command=command,
                        family=family,
                        source_file=log_file.name,
                        line_no=line_no,
                    )
                    analysis.sent_events.append(sent)
                    analysis.sent_by_sender[str(sender_id)] += 1
                    analysis.sent_by_family[family] += 1
                if command:
                    family = command_family(command)
                    analysis.command_stats[command].add(row, example_limit=example_limit)
                    analysis.command_families[family] += 1
                    if family == "unknown":
                        analysis.unknown_commands[command] += 1
                    if log_group_id and chat_id == log_group_id:
                        analysis.log_group_command_stats[command].add(row, example_limit=example_limit)
                if sender_id in bot_ids and text.strip() and not command:
                    reply_to = reply_to_msg_id
                    header = reply_header(text)
                    analysis.bot_reply_headers[header] += 1
                    category = classify_bot_reply(text)
                    analysis.bot_reply_categories[category] += 1
                    if reply_to:
                        analysis.bot_reply_by_parent[reply_to].append(
                            {
                                "ts": ts,
                                "sender_id": sender_id,
                                "message_id": message_id,
                                "header": header,
                                "category": category,
                                "text": compact_text(text, 180),
                            }
                        )
                    if any(keyword in text for keyword in HARD_STOP_KEYWORDS):
                        analysis.hard_stop_hits.append(
                            {
                                "ts": ts,
                                "sender_id": sender_id,
                                "message_id": message_id,
                                "keyword": first_matching_keyword(text, HARD_STOP_KEYWORDS),
                                "text": compact_text(text, 220),
                                "source_file": log_file.name,
                                "line_no": line_no,
                            }
                        )
    return analysis


def reply_header(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "<empty>"
    first_line = raw.splitlines()[0].strip()
    bracket = re.search(r"【([^】]{1,40})】", first_line)
    if bracket:
        return f"【{bracket.group(1)}】"
    return compact_text(first_line, 60)


def classify_bot_reply(text: str) -> str:
    raw = str(text or "")
    for category, markers in BOT_REPLY_MARKERS:
        if any(marker in raw for marker in markers):
            return category
    return "other"


def first_matching_keyword(text: str, keywords: Iterable[str]) -> str:
    raw = str(text or "")
    for keyword in keywords:
        if keyword in raw:
            return keyword
    return ""


def summarize_sent_health(analysis: Analysis) -> dict:
    sent = sorted(analysis.sent_events, key=lambda item: (item.epoch, item.message_id))
    duplicate_short_gap = []
    any_short_gap = []
    missing_direct_replies = []
    per_sender_minute: dict[tuple[int, int], int] = Counter()
    prev_by_sender_command: dict[tuple[int, str], SentEvent] = {}
    prev_by_sender: dict[int, SentEvent] = {}
    for item in sent:
        minute_bucket = int(item.epoch // 60) if item.epoch else 0
        if minute_bucket:
            per_sender_minute[(item.sender_id, minute_bucket)] += 1
        prev_same = prev_by_sender_command.get((item.sender_id, item.text))
        if prev_same and item.epoch and prev_same.epoch:
            gap = item.epoch - prev_same.epoch
            if 0 <= gap <= 90 and not is_allowed_fast_repeat(item.text):
                duplicate_short_gap.append(format_gap_pair(prev_same, item, gap))
        prev_any = prev_by_sender.get(item.sender_id)
        if prev_any and item.epoch and prev_any.epoch:
            gap = item.epoch - prev_any.epoch
            if 0 <= gap < 1.0:
                any_short_gap.append(format_gap_pair(prev_any, item, gap))
        prev_by_sender_command[(item.sender_id, item.text)] = item
        prev_by_sender[item.sender_id] = item

        replies = analysis.bot_reply_by_parent.get(item.message_id) or []
        if not replies and item.message_id:
            missing_direct_replies.append(
                {
                    "ts": item.ts,
                    "sender_id": item.sender_id,
                    "command": item.text,
                    "family": item.family,
                    "message_id": item.message_id,
                    "source_file": item.source_file,
                    "line_no": item.line_no,
                }
            )
    busiest_minutes = [
        {"sender_id": sender_id, "minute_epoch": minute_epoch, "count": count}
        for (sender_id, minute_epoch), count in per_sender_minute.most_common(30)
    ]
    return {
        "sent_total": len(sent),
        "duplicate_short_gap": duplicate_short_gap[:100],
        "any_short_gap": any_short_gap[:100],
        "busiest_minutes": busiest_minutes,
        "missing_direct_replies_total": len(missing_direct_replies),
        "missing_direct_replies_sample": missing_direct_replies[:200],
    }


def is_allowed_fast_repeat(text: str) -> bool:
    command = command_key(text)
    if command in {".加入副本", ".加入坠魔谷", ".加入黄龙山", ".加入苍坤洞府", ".加入昆吾山", ".加入落云秘圃"}:
        return True
    if command in {".稳", ".狠", ".骗"}:
        return True
    return False


def format_gap_pair(prev: SentEvent, cur: SentEvent, gap: float) -> dict:
    return {
        "sender_id": cur.sender_id,
        "gap_sec": round(gap, 3),
        "prev_ts": prev.ts,
        "cur_ts": cur.ts,
        "prev_command": prev.text,
        "cur_command": cur.text,
        "cur_family": cur.family,
        "prev_message_id": prev.message_id,
        "cur_message_id": cur.message_id,
        "source_file": cur.source_file,
        "line_no": cur.line_no,
    }


def extract_static_source_inventory(project_root: Path, miniweb_root: Path) -> dict:
    result = {
        "cmd_constants": {},
        "module_names": [],
        "log_group_help_commands": [],
        "log_group_actual_regexes": [],
        "replica_command_literals": [],
        "miniweb_parsers": [],
    }
    config_path = project_root / "model" / "config.py"
    control_path = project_root / "model" / "control.py"
    replica_path = project_root / "model" / "app_replica.py"
    miniweb_parsers_path = miniweb_root / "backend" / "parsers" / "__init__.py"
    if config_path.exists():
        source = config_path.read_text(encoding="utf-8")
        result["cmd_constants"] = extract_cmd_constants(source)
        result["module_names"] = extract_literal_assignment(source, "MODULE_NAMES") or []
        result["log_group_actual_regexes"] = extract_re_cmd_regexes(source)
    if control_path.exists():
        source = control_path.read_text(encoding="utf-8")
        result["log_group_help_commands"] = extract_log_group_help_commands(source)
    if replica_path.exists():
        source = replica_path.read_text(encoding="utf-8")
        result["replica_command_literals"] = sorted(extract_dotted_literals(source))
    if miniweb_parsers_path.exists():
        source = miniweb_parsers_path.read_text(encoding="utf-8")
        result["miniweb_parsers"] = re.findall(r"registry\.register\((\w+)\(\)\)", source)
    return result


def extract_cmd_constants(source: str) -> dict[str, str]:
    commands = {}
    for match in re.finditer(r"^(CMD_[A-Z0-9_]+)\s*=\s*([\"'])(.+?)\2", source, re.M):
        commands[match.group(1)] = match.group(3)
    return dict(sorted(commands.items()))


def extract_literal_assignment(source: str, name: str):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        try:
            return ast.literal_eval(node.value)
        except Exception:
            return None
    return None


def extract_log_group_help_commands(source: str) -> list[str]:
    commands: list[str] = []
    for name in ("module_commands", "control_commands"):
        values = extract_function_literal_assignment(source, "_format_log_group_help_html", name)
        if isinstance(values, list):
            commands.extend(str(item) for item in values)
    return commands


def extract_function_literal_assignment(source: str, function_name: str, assignment_name: str):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == assignment_name for target in child.targets):
                continue
            try:
                return ast.literal_eval(child.value)
            except Exception:
                return None
    return None


def extract_re_cmd_regexes(source: str) -> list[dict]:
    rows = []
    for match in re.finditer(r"^(RE_CMD_[A-Z0-9_]+)\s*=\s*re\.compile\((r?)([\"'])(.+?)\3", source, re.M):
        rows.append({"name": match.group(1), "pattern": match.group(4)})
    for match in re.finditer(r"\(re\.compile\((r?)([\"'])(.+?)\2\),\s*([\"'])(.+?)\4,\s*(True|False)\)", source):
        rows.append({"name": f"RE_CMD_ENABLE_PATTERN:{match.group(5)}:{match.group(6)}", "pattern": match.group(3)})
    for match in re.finditer(r"\(re\.compile\((r?)([\"'])(.+?)\2\),\s*([\"'])(.+?)\4\)", source):
        pattern = match.group(3)
        module_name = match.group(5)
        if "状态" in pattern:
            rows.append({"name": f"RE_CMD_SINGLE_STATUS:{module_name}", "pattern": pattern})
    return rows


def extract_dotted_literals(source: str) -> set[str]:
    values = set()
    for quote, text in re.findall(r"([\"'])(\.[^\"'\n\r]{1,40})\1", source):
        cleaned = text.strip()
        if cleaned and "\\" not in cleaned:
            values.add(cleaned)
    return values


def read_miniweb_summary(db_path: Path, miniweb_root: Path) -> dict:
    summary = {
        "available": False,
        "db_path": str(db_path),
        "raw_messages": {},
        "parsed_channels": [],
        "send_logs": [],
        "resource_events": [],
        "top_raw_commands": [],
        "latest_messages": [],
    }
    if not db_path.exists() or db_path.stat().st_size <= 0:
        return summary
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        summary["available"] = True
        summary["raw_messages"] = query_one_dict(
            conn,
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS count FROM raw_messages",
        )
        summary["parsed_channels"] = query_all_dicts(
            conn,
            "SELECT primary_channel, COUNT(*) AS count FROM parsed_cards GROUP BY primary_channel ORDER BY count DESC",
            limit=100,
        )
        summary["send_logs"] = query_all_dicts(
            conn,
            """
            SELECT kind, status, command, COUNT(*) AS count
            FROM send_logs
            GROUP BY kind, status, command
            ORDER BY count DESC
            LIMIT 100
            """,
        )
        summary["resource_events"] = query_all_dicts(
            conn,
            """
            SELECT source_type, source_name, result, COUNT(*) AS count
            FROM resource_events
            GROUP BY source_type, source_name, result
            ORDER BY count DESC
            LIMIT 100
            """,
        )
        summary["top_raw_commands"] = query_all_dicts(
            conn,
            """
            SELECT
                CASE
                    WHEN instr(text, ' ') > 0 THEN substr(text, 1, instr(text, ' ') - 1)
                    WHEN instr(text, char(10)) > 0 THEN substr(text, 1, instr(text, char(10)) - 1)
                    ELSE text
                END AS command,
                COUNT(*) AS count
            FROM raw_messages
            WHERE text LIKE '.%'
            GROUP BY command
            ORDER BY count DESC
            LIMIT 100
            """,
        )
        summary["latest_messages"] = query_all_dicts(
            conn,
            """
            SELECT date, source, sender_id, substr(replace(replace(text, char(10), ' '), char(13), ' '), 1, 220) AS text
            FROM raw_messages
            ORDER BY date DESC
            LIMIT 30
            """,
        )
    finally:
        conn.close()
    return summary


def query_one_dict(conn: sqlite3.Connection, sql: str) -> dict:
    row = conn.execute(sql).fetchone()
    return dict(row) if row is not None else {}


def query_all_dicts(conn: sqlite3.Connection, sql: str, limit: int | None = None) -> list[dict]:
    rows = conn.execute(sql).fetchall()
    result = [dict(row) for row in rows]
    return result[:limit] if limit is not None else result


def counter_to_rows(counter: Counter, limit: int = 50) -> list[dict]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def command_stats_to_json(analysis: Analysis, limit: int = 500) -> list[dict]:
    rows = []
    for command, stats in sorted(analysis.command_stats.items(), key=lambda item: (-item[1].count, item[0]))[:limit]:
        rows.append(
            {
                "command": command,
                "family": command_family(command),
                "count": stats.count,
                "sent_count": stats.sent_count,
                "message_count": stats.message_count,
                "edit_count": stats.edit_count,
                "first_ts": stats.first_ts,
                "last_ts": stats.last_ts,
                "top_senders": counter_to_rows(stats.senders, 10),
                "top_chats": counter_to_rows(stats.chats, 10),
                "top_topics": counter_to_rows(stats.topics, 10),
                "examples": stats.examples,
            }
        )
    return rows


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def render_summary_report(analysis: Analysis, static_inventory: dict, miniweb: dict, health: dict, args) -> str:
    lines = [
        "# 修仙游戏记录离线总览",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 主脚本日志目录: `{args.messages_dir}`",
        f"- 扫描范围: `{args.since or '最早'}` 到 `{args.until or '最新'}`",
        f"- 扫描 JSONL 行数: {analysis.scanned_lines}",
        f"- 无效 JSON 行数: {analysis.invalid_json}",
        f"- 日志文件数: {len(analysis.source_files)}",
        f"- 自动发送记录: {health.get('sent_total', 0)}",
        f"- 命令种类: {len(analysis.command_stats)}",
        f"- 已知模块数: {len(static_inventory.get('module_names') or [])}",
        f"- webmini 数据库: {'可用' if miniweb.get('available') else '不可用'}",
    ]
    if miniweb.get("available"):
        raw = miniweb.get("raw_messages") or {}
        lines.extend(
            [
                f"- webmini 消息: {raw.get('count', 0)} 条，{raw.get('min_date', '')} 到 {raw.get('max_date', '')}",
            ]
        )
    lines.extend(["", "## 事件类型", ""])
    lines.extend(render_counter_bullets(analysis.event_types, 20))
    lines.extend(["", "## 命令家族", ""])
    lines.extend(render_counter_bullets(analysis.command_families, 30))
    lines.extend(["", "## 高频命令 Top 40", ""])
    for row in command_stats_to_json(analysis, 40):
        lines.append(f"- `{row['command']}`: {row['count']} 次，sent {row['sent_count']}，family `{row['family']}`")
    lines.extend(["", "## 自动发送健康摘要", ""])
    duplicate_count = len(health.get("duplicate_short_gap") or [])
    any_gap_count = len(health.get("any_short_gap") or [])
    missing_count = int(health.get("missing_direct_replies_total") or 0)
    lines.append(f"- 同身份同命令 90 秒内重复样本: {duplicate_count}")
    lines.append(f"- 同身份 1 秒内连续发送样本: {any_gap_count}")
    lines.append(f"- 未找到直接 reply 的 sent 记录: {missing_count}")
    lines.append("- 说明: 未找到直接 reply 不等同于漏发；Telegram bot 有些回复不是 reply，需结合原文和时间窗复核。")
    if analysis.hard_stop_hits:
        lines.append(f"- 硬停关键词命中: {len(analysis.hard_stop_hits)}，详见 `storm_risk.md`。")
    lines.extend(["", "## 建议优先级", ""])
    lines.extend(
        [
            "1. 日志群指令应继续保持查询/开关职责，不并入自动连发链路。",
            "2. 副本门票、开房、归还必须以真实韩天尊文本为准，优先使用专项状态机，不依赖每次 `.储物袋` 刷新。",
            "3. webmini 的 parser/频道分类可作为分析器和 UI 设计参考；它的发送边界是人工确认/官方定时，不适合照搬成自动连发。",
            "4. 自动发送健康检查要区分直接 reply 缺失、冷却回复、资源不足和真实异常，避免误判。",
        ]
    )
    return "\n".join(lines)


def render_counter_bullets(counter: Counter, limit: int) -> list[str]:
    if not counter:
        return ["- 无"]
    return [f"- `{key}`: {count}" for key, count in counter.most_common(limit)]


def render_log_group_report(analysis: Analysis, static_inventory: dict, *, log_group_id: int = 0) -> str:
    help_commands = static_inventory.get("log_group_help_commands") or []
    regexes = static_inventory.get("log_group_actual_regexes") or []
    observed = dict(analysis.log_group_command_stats)
    lines = [
        "# 日志群指令分析",
        "",
        f"- 统计限定 chat_id: `{log_group_id or '未配置'}`",
        "",
        "## 当前帮助文案列出的指令",
        "",
    ]
    lines.extend(f"- `{cmd}`" for cmd in help_commands)
    lines.extend(["", "## 当前代码实际识别入口", ""])
    if regexes:
        for row in regexes:
            lines.append(f"- `{row.get('name')}`: `{row.get('pattern')}`")
    else:
        lines.append("- 未从源码提取到 RE_CMD 规则。")
    lines.extend(["", "## 日志群实际观察到的点命令", ""])
    if observed:
        for command, stats in sorted(observed.items(), key=lambda item: (-item[1].count, item[0])):
            lines.append(f"- `{command}`: {stats.count} 次，sent {stats.sent_count}，message {stats.message_count}")
    else:
        lines.append("- 当前扫描范围内未观察到日志群点命令，或未配置 `LOG_GROUP_ID`。")
    lines.extend(["", "## 帮助文案但未在日志群观察到", ""])
    missing = [cmd for cmd in help_commands if cmd not in observed]
    if missing:
        lines.extend(f"- `{cmd}`" for cmd in missing)
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 优化方案",
            "",
            "- 保留 `.状态`、单模块状态、模块开关、`.全局暂停/.全局恢复`、`.登录`、`.储物袋` 这类低风险指令。",
            "- 副本管理建议保持游戏群轻量命令入口: `.查询副本`、`.开启副本 @用户名`、`.加入副本 @1 @2`、`.解散副本`；日志群只展示状态和审计。",
            "- 控制类命令需要继续限定管理员，并保留身份选择器 `@昵称/身份ID`。",
            "- 不建议把 webmini 的 parser 动作草稿自动提交到日志群；它的价值在结构化识别和人工确认。",
            "- 需要新增日志群汇总时，优先做只读报告，例如 `.副本状态` 或 `.发送健康码`，不要让它直接触发游戏命令。",
        ]
    )
    return "\n".join(lines)


def render_gameplay_taxonomy(analysis: Analysis, static_inventory: dict, miniweb: dict) -> str:
    lines = [
        "# 玩法与指令分类",
        "",
        "## 主脚本模块",
        "",
    ]
    module_names = static_inventory.get("module_names") or []
    lines.extend(f"- {name}" for name in module_names)
    lines.extend(["", "## 源码命令常量", ""])
    for name, command in (static_inventory.get("cmd_constants") or {}).items():
        lines.append(f"- `{name}`: `{command}` -> `{command_family(command_key(command))}`")
    lines.extend(["", "## webmini parser 覆盖", ""])
    parsers = static_inventory.get("miniweb_parsers") or []
    if parsers:
        lines.extend(f"- `{parser}`" for parser in parsers)
    else:
        lines.append("- 未读取到 webmini parser 注册表。")
    lines.extend(["", "## 实际日志中的玩法热度", ""])
    lines.extend(render_counter_bullets(analysis.command_families, 40))
    if miniweb.get("available"):
        lines.extend(["", "## webmini 资源事件", ""])
        for row in miniweb.get("resource_events", [])[:80]:
            lines.append(
                f"- `{row.get('source_type')}` / `{row.get('source_name')}` / `{row.get('result')}`: {row.get('count')}"
            )
    lines.extend(
        [
            "",
            "## 吸收价值判断",
            "",
            "- webmini 的可取处是 parser 注册表、频道过滤、资源事件聚合、虚天/苍坤攻略展示。",
            "- 主脚本的可取处是完整运行态、全局发送队列、真实副本门票状态机、冷却/失败处理。",
            "- 两边融合时，parser 和报告可以共享思路；发送行为仍以主脚本全局队列和专项模块为准。",
        ]
    )
    return "\n".join(lines)


def render_unknown_commands(analysis: Analysis) -> str:
    lines = ["# 未归类点命令", ""]
    if not analysis.unknown_commands:
        lines.append("- 无")
        return "\n".join(lines)
    for command, count in analysis.unknown_commands.most_common(200):
        stats = analysis.command_stats.get(command)
        example = stats.examples[0]["text"] if stats and stats.examples else ""
        lines.append(f"- `{command}`: {count} 次。例: {example}")
    return "\n".join(lines)


def render_storm_risk(analysis: Analysis, health: dict) -> str:
    lines = [
        "# 发送健康与风险扫描",
        "",
        f"- 自动发送总数: {health.get('sent_total', 0)}",
        f"- 同身份同命令 90 秒内重复样本: {len(health.get('duplicate_short_gap') or [])}",
        f"- 同身份 1 秒内连续发送样本: {len(health.get('any_short_gap') or [])}",
        f"- 未找到直接 reply 的 sent 记录: {health.get('missing_direct_replies_total', 0)}",
        "",
        "## 高频分钟",
        "",
    ]
    for row in (health.get("busiest_minutes") or [])[:30]:
        minute_text = datetime.fromtimestamp(row["minute_epoch"] * 60).strftime("%Y-%m-%d %H:%M") if row.get("minute_epoch") else ""
        lines.append(f"- sender `{row.get('sender_id')}` @ {minute_text}: {row.get('count')} 条 sent")
    lines.extend(["", "## 短间隔重复样本", ""])
    samples = health.get("duplicate_short_gap") or []
    if samples:
        for row in samples[:80]:
            lines.append(
                f"- `{row['cur_ts']}` sender `{row['sender_id']}` gap {row['gap_sec']}s: `{row['cur_command']}` ({row['source_file']}:{row['line_no']})"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 1 秒内连续发送样本", ""])
    samples = health.get("any_short_gap") or []
    if samples:
        for row in samples[:80]:
            lines.append(
                f"- `{row['cur_ts']}` sender `{row['sender_id']}` gap {row['gap_sec']}s: `{row['prev_command']}` -> `{row['cur_command']}`"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 未找到直接 reply 样本", ""])
    missing = health.get("missing_direct_replies_sample") or []
    if missing:
        for row in missing[:80]:
            lines.append(
                f"- `{row['ts']}` sender `{row['sender_id']}` `{row['command']}` msg `{row['message_id']}` family `{row['family']}`"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## 硬停关键词", ""])
    if analysis.hard_stop_hits:
        for row in analysis.hard_stop_hits[:80]:
            lines.append(
                f"- `{row['ts']}` bot `{row['sender_id']}` keyword `{row['keyword']}`: {row['text']}"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 解读",
            "",
            "- 这里的异常是离线候选，不直接等同于风暴、错发或漏发。",
            "- 真正的风暴要同时看短间隔、重复命令、是否绕过全局队列、以及 bot 是否给出冷却/失败回复。",
            "- `missing_direct_replies` 只说明没有找到 `reply_to_msg_id == sent.message_id` 的 bot 回复；部分游戏回复不是直接 reply。",
        ]
    )
    return "\n".join(lines)


def render_webmini_insights(miniweb: dict, static_inventory: dict) -> str:
    lines = ["# webmini 可吸收内容与近期日志", ""]
    if not miniweb.get("available"):
        lines.append(f"- webmini DB 不可用: `{miniweb.get('db_path')}`")
        return "\n".join(lines)
    raw = miniweb.get("raw_messages") or {}
    lines.extend(
        [
            f"- DB: `{miniweb.get('db_path')}`",
            f"- raw_messages: {raw.get('count', 0)}",
            f"- 时间范围: {raw.get('min_date', '')} 到 {raw.get('max_date', '')}",
            "",
            "## parser 注册表",
            "",
        ]
    )
    parsers = static_inventory.get("miniweb_parsers") or []
    lines.extend(f"- `{parser}`" for parser in parsers)
    lines.extend(["", "## parsed channel 分布", ""])
    for row in miniweb.get("parsed_channels", []):
        lines.append(f"- `{row.get('primary_channel')}`: {row.get('count')}")
    lines.extend(["", "## raw 点命令 Top 100", ""])
    for row in miniweb.get("top_raw_commands", [])[:100]:
        lines.append(f"- `{row.get('command')}`: {row.get('count')}")
    lines.extend(["", "## resource_events Top 100", ""])
    for row in miniweb.get("resource_events", [])[:100]:
        lines.append(
            f"- `{row.get('source_type')}` / `{row.get('source_name')}` / `{row.get('result')}`: {row.get('count')}"
        )
    lines.extend(["", "## send_logs", ""])
    if miniweb.get("send_logs"):
        for row in miniweb.get("send_logs", [])[:100]:
            lines.append(
                f"- `{row.get('kind')}` / `{row.get('status')}` / `{row.get('command')}`: {row.get('count')}"
            )
    else:
        lines.append("- 无发送日志或未使用发送出口。")
    lines.extend(["", "## 近期消息样本", ""])
    for row in miniweb.get("latest_messages", [])[:30]:
        lines.append(f"- `{row.get('date')}` `{row.get('source')}` `{row.get('sender_id')}`: {row.get('text')}")
    lines.extend(
        [
            "",
            "## 可吸收点",
            "",
            "- parser 注册表顺序和 ParsedCard 输出适合给主脚本离线报告/后续 UI 做分层。",
            "- resource_events 已覆盖野外历练、灵树采摘、南陇侯、极阴、副本结果，可作为玩法收益统计参考。",
            "- dungeon_status 的聚合逻辑适合借鉴到副本状态展示，但不能照搬自动动作。",
            "- cangkun/xutian guide 适合保留为只读推荐和填命令草稿，不应直接绕过主脚本发送队列。",
        ]
    )
    return "\n".join(lines)


def render_focus_sender_report(analysis: Analysis, focus_senders: Iterable[int]) -> str:
    lines = ["# 重点 sender 排查", ""]
    for sender_id in focus_senders:
        sender_id = int(sender_id)
        lines.extend([f"## sender `{sender_id}`", ""])
        commands = analysis.focus_sender_commands.get(sender_id) or Counter()
        if commands:
            lines.append("### 命令统计")
            lines.append("")
            lines.extend(render_counter_bullets(commands, 80))
            lines.append("")
        rows = analysis.focus_sender_rows.get(sender_id) or []
        if rows:
            lines.append("### 样本")
            lines.append("")
            for row in rows[:120]:
                cmd = f" `{row['command']}`" if row.get("command") else ""
                lines.append(
                    f"- `{row['ts']}` {row['event_type']}{cmd} chat `{row['chat_id']}` msg `{row['message_id']}`: {row['text']}"
                )
        else:
            lines.append("- 扫描范围内无记录。")
        lines.append("")
    return "\n".join(lines)


def build_output_payload(analysis: Analysis, static_inventory: dict, miniweb: dict, health: dict, *, log_group_id: int = 0) -> dict:
    return {
        "summary": {
            "scanned_lines": analysis.scanned_lines,
            "invalid_json": analysis.invalid_json,
            "source_files": counter_to_rows(analysis.source_files, 200),
            "event_types": counter_to_rows(analysis.event_types, 20),
            "dates": counter_to_rows(analysis.dates, 80),
            "command_families": counter_to_rows(analysis.command_families, 80),
            "sent_by_sender": counter_to_rows(analysis.sent_by_sender, 80),
            "sent_by_family": counter_to_rows(analysis.sent_by_family, 80),
            "log_group_id": log_group_id,
            "log_group_commands": command_stats_rows_from_mapping(analysis.log_group_command_stats, 300),
            "bot_reply_categories": counter_to_rows(analysis.bot_reply_categories, 80),
            "hard_stop_hits": analysis.hard_stop_hits[:200],
        },
        "commands": command_stats_to_json(analysis, 1000),
        "bot_reply_headers": counter_to_rows(analysis.bot_reply_headers, 300),
        "unknown_commands": counter_to_rows(analysis.unknown_commands, 300),
        "health": health,
        "static_inventory": static_inventory,
        "miniweb": miniweb,
    }


def parse_focus_senders(raw: str) -> tuple[int, ...]:
    values = []
    for item in re.split(r"[,，\s]+", str(raw or "").strip()):
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError:
            continue
    return tuple(values)


def command_stats_rows_from_mapping(mapping: dict[str, CommandStats], limit: int = 500) -> list[dict]:
    rows = []
    for command, stats in sorted(mapping.items(), key=lambda item: (-item[1].count, item[0]))[:limit]:
        rows.append(
            {
                "command": command,
                "family": command_family(command),
                "count": stats.count,
                "sent_count": stats.sent_count,
                "message_count": stats.message_count,
                "edit_count": stats.edit_count,
                "first_ts": stats.first_ts,
                "last_ts": stats.last_ts,
                "top_senders": counter_to_rows(stats.senders, 10),
                "examples": stats.examples,
            }
        )
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline xiuxian game-record analyzer")
    parser.add_argument("--messages-dir", default=str(DEFAULT_MESSAGES_DIR), help="Main JSONL message log directory")
    parser.add_argument("--miniweb-db", default=str(DEFAULT_MINIWEB_DB), help="xiuxian-mini-web SQLite DB path")
    parser.add_argument("--miniweb-root", default=str(DEFAULT_MINIWEB_ROOT), help="xiuxian-mini-web repo root")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for generated reports")
    parser.add_argument("--run-name", default="latest", help="Subdirectory name under output-dir")
    parser.add_argument("--since", default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--until", default="", help="End date YYYY-MM-DD")
    parser.add_argument("--exclude-replica", action="store_true", help="Skip replica-*.log files")
    parser.add_argument("--focus-senders", default=",".join(str(x) for x in DEFAULT_FOCUS_SENDERS), help="Comma/space separated sender IDs to inspect")
    parser.add_argument("--log-group-id", type=int, default=None, help="Log group chat_id; defaults to LOG_GROUP_ID from .env")
    parser.add_argument("--example-limit", type=int, default=5, help="Examples kept per command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    messages_dir = Path(args.messages_dir)
    miniweb_db = Path(args.miniweb_db)
    miniweb_root = Path(args.miniweb_root)
    output_root = Path(args.output_dir)
    run_dir = output_root / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    focus_senders = parse_focus_senders(args.focus_senders)
    log_group_id = args.log_group_id
    if log_group_id is None:
        log_group_id = load_env_int(PROJECT_ROOT / ".env", "LOG_GROUP_ID", 0)

    analysis = analyze_jsonl_logs(
        messages_dir,
        since=args.since,
        until=args.until,
        include_replica=not args.exclude_replica,
        focus_senders=focus_senders,
        log_group_id=log_group_id,
        example_limit=max(1, int(args.example_limit or 1)),
    )
    health = summarize_sent_health(analysis)
    static_inventory = extract_static_source_inventory(PROJECT_ROOT, miniweb_root)
    miniweb = read_miniweb_summary(miniweb_db, miniweb_root)
    payload = build_output_payload(analysis, static_inventory, miniweb, health, log_group_id=log_group_id)

    write_json(run_dir / "analysis_payload.json", payload)
    write_json(run_dir / "command_inventory.json", payload["commands"])
    write_json(run_dir / "bot_reply_patterns.json", payload["bot_reply_headers"])
    write_text(run_dir / "summary.md", render_summary_report(analysis, static_inventory, miniweb, health, args))
    write_text(run_dir / "log_group_command_report.md", render_log_group_report(analysis, static_inventory, log_group_id=log_group_id))
    write_text(run_dir / "gameplay_taxonomy.md", render_gameplay_taxonomy(analysis, static_inventory, miniweb))
    write_text(run_dir / "unknown_commands.md", render_unknown_commands(analysis))
    write_text(run_dir / "storm_risk.md", render_storm_risk(analysis, health))
    write_text(run_dir / "webmini_insights.md", render_webmini_insights(miniweb, static_inventory))
    write_text(run_dir / "focus_senders.md", render_focus_sender_report(analysis, focus_senders))
    print(f"wrote offline analysis to {run_dir}")
    print(f"scanned_lines={analysis.scanned_lines} commands={len(analysis.command_stats)} sent={health.get('sent_total', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
