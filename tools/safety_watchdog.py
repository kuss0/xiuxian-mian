#!/usr/bin/env python3
"""External safety watchdog for the xiuxian automation service.

This process is intentionally independent from the main runtime. It only reads
the append-only message log and the local SQLite state DB. On clear abnormal
send patterns it writes a fuse marker, disables the global switch in SQLite,
and can stop the main systemd service.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


GUARDED_PREFIXES = (
    ".入梦寻图",
    ".天机代卜",
    ".残图",
    ".拼图",
    ".宗门赐婚",
    ".红尘寻缘",
    ".器灵试炼",
    ".温养器灵",
    ".元神修炼",
    ".深度闭关",
    ".元婴出窍",
    ".闯塔",
    ".引道",
    ".搜寻节点",
    ".定星",
    ".神迹 布道",
    ".神迹 赈灾",
    ".显灵",
    ".收割香火",
    ".神识淬炼",
    ".卜筮问天",
    ".换取",
)

REFRESH_PREFIXES = (
    ".小世界",
)

SMALL_WORLD_TOOL_PREFIXES = (
    ".显灵",
    ".收割香火",
    ".神识淬炼",
)

DUNGEON_JOIN_PREFIXES = (".加入副本", ".加入坠魔谷", ".加入黄龙山", ".加入苍坤洞府", ".加入昆吾山", ".加入落云秘圃")
DUNGEON_FAST_CHAIN_PREFIXES = (
    ".开启副本",
    ".开启虚天殿",
    ".开启苍坤洞府",
    ".开启坠魔谷",
    ".开启黄龙山",
    ".开启昆吾山",
    ".开启落云秘圃",
    ".加入副本",
    ".加入坠魔谷",
    ".加入黄龙山",
    ".加入苍坤洞府",
    ".加入昆吾山",
    ".加入落云秘圃",
    ".解散副本",
    ".解散苍坤洞府",
    ".解散坠魔谷",
    ".解散黄龙山",
    ".解散昆吾山",
    ".请离",
    ".进入虚天殿",
    ".进入坠魔谷",
    ".进入黄龙山",
    ".进入苍坤洞府",
    ".进入昆吾山",
    ".进入落云秘圃",
    ".选择道路",
    ".阵策",
    ".争鼎",
    ".后殿抉择",
    ".后殿阵策",
    ".坠魔抉择",
    ".黄龙抉择",
    ".苍坤抉择",
    ".落云抉择",
)
SECT_TEACH_PREFIX = ".宗门传功"
SECT_TEACH_MAX_ATTEMPTS_10M = 3
HEART_CHOICE_COMMANDS = {".稳", ".狠", ".骗"}
CONCUBINE_STATUS_COMMAND = ".我的侍妾"
CONCUBINE_RECOVERY_CHAIN_PREFIXES = (".每日问安", ".储物袋", ".赠予侍妾")
PHASEFUL_REPLAY_OP_PREFIX = "phaseful_replay:"
CONCUBINE_VOYAGE_RETRY_OP_PREFIX = "concubine_voyage_retry:"
TOWER_SOURCE_MODULE = "闯塔"
DIVINATION_QUERY_COMMAND = ".卜筮问天"
DIVINATION_SOURCE_MODULE = "卜筮问天"
DIVINATION_DAILY_QUERY_MIN_GAP_SEC = 55
DIVINATION_DAILY_QUERY_MAX_ATTEMPTS_45M = 20
PROJECT_ROOT = Path(__file__).resolve().parents[1]

BOT_REPLY_HARD_STOP_KEYWORDS = (
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

BOT_REPLY_FALSE_POSITIVE_KEYWORDS = (
    "并未被封禁",
    "无需赎罪",
)


@dataclass
class WatchdogConfig:
    project_root: Path
    service_name: str
    interval_sec: float
    action: str
    dry_run: bool
    max_lines: int
    min_any_gap_sec: float
    total_2m_limit: int
    total_5m_limit: int
    total_15m_limit: int
    same_command_gap_sec: float
    guarded_repeat_gap_sec: float
    guarded_max_attempts_45m: int
    guarded_fourth_min_span_sec: float
    refresh_repeat_gap_sec: float
    refresh_max_attempts_90m: int
    journal_check_interval_sec: float


def load_dotenv(env_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not env_path.exists():
        return env
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            env.setdefault(key, value)
    return env


def parse_local_ts(raw: str) -> float:
    text = str(raw or "")[:19]
    try:
        return time.mktime(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timetuple())
    except Exception:
        return 0.0


def read_recent_log_lines(log_file: Path, max_lines: int) -> list[dict]:
    if not log_file.exists():
        return []
    rows: list[dict] = []
    with log_file.open("r", encoding="utf-8") as handle:
        for line in deque(handle, maxlen=max_lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payload["_epoch"] = parse_local_ts(payload.get("ts", ""))
            rows.append(payload)
    return rows


def is_guarded_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in GUARDED_PREFIXES)


def is_refresh_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in REFRESH_PREFIXES)


def is_dungeon_join_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in DUNGEON_JOIN_PREFIXES)


def is_known_replica_choice_command(text: str) -> bool:
    raw = str(text or "").strip()
    if raw in {".选择 强行摘取", ".选择 静待时机"}:
        return True
    suffix = raw.removeprefix(".选择 岔路")
    return suffix != raw and suffix.isdigit()


def is_dungeon_fast_chain_command(text: str) -> bool:
    raw = str(text or "").strip()
    return (
        any(raw == prefix or raw.startswith(prefix + " ") for prefix in DUNGEON_FAST_CHAIN_PREFIXES)
        or is_known_replica_choice_command(raw)
    )


def is_controlled_retry_event(item: dict) -> bool:
    if str(item.get("priority") or "").strip().lower() != "retry":
        return False
    return bool(str(item.get("family") or "").strip() or str(item.get("source_module") or "").strip())


def is_tower_retry_event(item: dict) -> bool:
    if not is_controlled_retry_event(item):
        return False
    return (
        command_key(str(item.get("text") or "")) == ".闯塔"
        and (
            str(item.get("family") or "").strip() == "tower"
            or str(item.get("source_module") or "").strip() == TOWER_SOURCE_MODULE
        )
    )


def is_safe_global_gap_pair(prev: dict, cur: dict) -> bool:
    return (
        is_dungeon_fast_chain_command(str(prev.get("text") or ""))
        or is_dungeon_fast_chain_command(str(cur.get("text") or ""))
        or is_controlled_retry_event(cur)
        or is_safe_heart_global_gap_pair(prev, cur)
    )


def is_sect_teach_command(text: str) -> bool:
    raw = str(text or "").strip()
    return raw == SECT_TEACH_PREFIX or raw.startswith(SECT_TEACH_PREFIX + " ")


def is_heart_choice_command(text: str) -> bool:
    return command_key(text) in HEART_CHOICE_COMMANDS


def is_concubine_heart_event(item: dict) -> bool:
    raw = command_key(str(item.get("text") or ""))
    if raw != ".共历心劫" and raw not in HEART_CHOICE_COMMANDS:
        return False
    return (
        str(item.get("family") or "") == "concubine_heart"
        or str(item.get("source_module") or "") == "共历心劫"
    )


def is_safe_heart_global_gap_pair(prev: dict, cur: dict) -> bool:
    if not is_concubine_heart_event(cur):
        return False
    cur_text = command_key(str(cur.get("text") or ""))
    if cur_text in HEART_CHOICE_COMMANDS:
        return True
    prev_sender = int(prev.get("sender_id", 0) or 0)
    cur_sender = int(cur.get("sender_id", 0) or 0)
    if prev_sender <= 0 or prev_sender != cur_sender:
        return False

    prev_text = command_key(str(prev.get("text") or ""))
    if prev_text == ".共历心劫" and cur_text in HEART_CHOICE_COMMANDS and is_concubine_heart_event(prev):
        return True
    if prev_text in HEART_CHOICE_COMMANDS and cur_text in HEART_CHOICE_COMMANDS and is_concubine_heart_event(prev):
        prev_reply = int(prev.get("reply_to_msg_id", 0) or 0)
        cur_reply = int(cur.get("reply_to_msg_id", 0) or 0)
        return prev_reply > 0 and prev_reply == cur_reply
    return False


def is_safe_heart_choice_repeat(items: list[dict]) -> bool:
    if len(items) > 3:
        return False
    if not all(is_concubine_heart_event(item) for item in items):
        return False
    reply_ids = {
        int(item.get("reply_to_msg_id", 0) or 0)
        for item in items
    }
    reply_ids.discard(0)
    return len(reply_ids) == 1


def has_matching_send_markers(prev: dict, cur: dict) -> bool:
    prev_markers = {
        str(prev.get("family") or "").strip(),
        str(prev.get("source_module") or "").strip(),
    }
    cur_markers = {
        str(cur.get("family") or "").strip(),
        str(cur.get("source_module") or "").strip(),
    }
    prev_markers.discard("")
    cur_markers.discard("")
    return bool(prev_markers and cur_markers and prev_markers.intersection(cur_markers))


def is_phaseful_replay_command(text: str) -> bool:
    raw = command_key(str(text or ""))
    return raw in {".入梦寻图", ".远航归来", ".侍妾远航"} or raw.startswith(".侍妾远航 ")


def is_concubine_voyage_command(text: str) -> bool:
    raw = command_key(str(text or ""))
    return raw in {".远航归来", ".侍妾远航"} or raw.startswith(".侍妾远航 ")


def is_safe_phaseful_replay_repeat(prev: dict, cur: dict, text: str) -> bool:
    if not is_phaseful_replay_command(text):
        return False
    if str(prev.get("priority") or "").strip().lower() == "retry":
        return False
    if not is_controlled_retry_event(cur):
        return False
    prev_sender = int(prev.get("sender_id", 0) or 0)
    prev_msg_id = int(prev.get("message_id", 0) or 0)
    if prev_sender <= 0 or prev_msg_id <= 0:
        return False
    expected_chain_id = f"{PHASEFUL_REPLAY_OP_PREFIX}{prev_sender}:{prev_msg_id}"
    op_id = str(cur.get("op_id") or "").strip()
    chain_id = str(cur.get("chain_id") or "").strip()
    return (
        chain_id == expected_chain_id
        and op_id == f"{expected_chain_id}:{text}"
        and has_matching_send_markers(prev, cur)
    )


def is_safe_concubine_voyage_retry_repeat(prev: dict, cur: dict, text: str) -> bool:
    if not is_concubine_voyage_command(text):
        return False
    if str(prev.get("priority") or "").strip().lower() == "retry":
        return False
    if not is_controlled_retry_event(cur):
        return False
    prev_sender = int(prev.get("sender_id", 0) or 0)
    prev_msg_id = int(prev.get("message_id", 0) or 0)
    if prev_sender <= 0 or prev_msg_id <= 0:
        return False
    expected_chain_id = f"{CONCUBINE_VOYAGE_RETRY_OP_PREFIX}{prev_sender}:{prev_msg_id}"
    op_id = str(cur.get("op_id") or "").strip()
    chain_id = str(cur.get("chain_id") or "").strip()
    return (
        chain_id == expected_chain_id
        and op_id == f"{expected_chain_id}:{text}"
        and has_matching_send_markers(prev, cur)
    )


def is_safe_same_command_retry(prev: dict, cur: dict, text: str) -> bool:
    if is_phaseful_replay_command(text):
        return False
    if str(prev.get("priority") or "").strip().lower() == "retry":
        return False
    if not is_controlled_retry_event(cur):
        return False
    return has_matching_send_markers(prev, cur)


def is_replica_button_choice_event(item: dict, text: str) -> bool:
    if not is_dungeon_fast_chain_command(text):
        return False
    if str(item.get("source_module") or "").strip() != "自动副本":
        return False
    return str(item.get("op_id") or "").strip().startswith("replica_button:")


def is_kunwu_auto_choice_event(item: dict, text: str) -> bool:
    if not is_dungeon_fast_chain_command(text):
        return False
    if str(item.get("source_module") or "").strip() != "自动副本":
        return False
    return str(item.get("op_id") or "").strip().startswith("kunwu_auto_choice:")


def is_replica_choice_event(item: dict, text: str) -> bool:
    return is_replica_button_choice_event(item, text) or is_kunwu_auto_choice_event(item, text)


def is_safe_replica_choice_repeat(prev: dict, cur: dict, text: str) -> bool:
    if not is_replica_choice_event(prev, text) or not is_replica_choice_event(cur, text):
        return False
    prev_op_id = str(prev.get("op_id") or "").strip()
    cur_op_id = str(cur.get("op_id") or "").strip()
    return bool(prev_op_id and cur_op_id and prev_op_id != cur_op_id)


def is_replica_lightweight_event(item: dict, text: str) -> bool:
    if not is_dungeon_fast_chain_command(text):
        return False
    if str(item.get("source_module") or "").strip() != "自动副本":
        return False
    return str(item.get("op_id") or "").strip().startswith("replica_lightweight_")


def is_safe_replica_lightweight_retry_repeat(prev: dict, cur: dict, text: str) -> bool:
    if not is_replica_lightweight_event(prev, text) or not is_replica_lightweight_event(cur, text):
        return False
    prev_op_id = str(prev.get("op_id") or "").strip()
    cur_op_id = str(cur.get("op_id") or "").strip()
    if not prev_op_id or not cur_op_id or prev_op_id == cur_op_id:
        return False
    if "_retry:" in prev_op_id or "_retry:" not in cur_op_id:
        return False
    prev_chain_id = str(prev.get("chain_id") or "").strip()
    cur_chain_id = str(cur.get("chain_id") or "").strip()
    return bool(prev_chain_id and prev_chain_id == cur_chain_id)


def has_duplicate_replica_choice_op_id(items: list[dict], text: str) -> bool:
    seen: set[str] = set()
    for item in items:
        if not is_replica_choice_event(item, text):
            return False
        op_id = str(item.get("op_id") or "").strip()
        if not op_id:
            return False
        if op_id in seen:
            return True
        seen.add(op_id)
    return False


def parse_divination_query_op(item: dict) -> tuple[int, str, int, int] | None:
    if command_key(str(item.get("text") or "")) != DIVINATION_QUERY_COMMAND:
        return None
    if str(item.get("source_module") or "").strip() != DIVINATION_SOURCE_MODULE:
        return None
    op_id = str(item.get("op_id") or "").strip()
    parts = op_id.split(":")
    if len(parts) != 5 or parts[0] != "divination_query":
        return None
    try:
        identity_id = int(parts[1])
        target_count = int(parts[3])
        try_no = int(str(parts[4]).removeprefix("try"))
    except (TypeError, ValueError):
        return None
    day_key = parts[2].strip()
    if identity_id <= 0 or not day_key or target_count <= 0 or try_no <= 0:
        return None
    return identity_id, day_key, target_count, try_no


def is_safe_divination_daily_query_chain(items: list[dict], text: str) -> bool:
    if text != DIVINATION_QUERY_COMMAND:
        return False
    if len(items) > DIVINATION_DAILY_QUERY_MAX_ATTEMPTS_45M:
        return False
    parsed = [parse_divination_query_op(item) for item in items]
    if any(item is None for item in parsed):
        return False
    parsed_items = [item for item in parsed if item is not None]
    identity_days = {(identity_id, day_key) for identity_id, day_key, _target_count, _try_no in parsed_items}
    if len(identity_days) != 1:
        return False
    op_ids = [str(item.get("op_id") or "").strip() for item in items]
    if len(op_ids) != len(set(op_ids)):
        return False
    previous_target = 0
    previous_try = 0
    for _identity_id, _day_key, target_count, try_no in parsed_items:
        if previous_target <= 0:
            previous_target = target_count
            previous_try = try_no
            continue
        if target_count < previous_target:
            return False
        if try_no <= previous_try:
            return False
        previous_target = target_count
        previous_try = try_no
    return True


def is_small_world_tool_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in SMALL_WORLD_TOOL_PREFIXES)


def command_key(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith(".器灵试炼 "):
        return ".器灵试炼"
    if raw.startswith(".温养器灵 "):
        return ".温养器灵"
    if raw.startswith(".神识淬炼 "):
        return ".神识淬炼"
    if raw.startswith(".定星 "):
        return ".定星"
    if raw.startswith(".引道 "):
        return ".引道"
    return raw


def is_send_burst_exempt_event(item: dict) -> bool:
    text = str(item.get("text") or "")
    return (
        is_dungeon_fast_chain_command(text)
        or (is_heart_choice_command(text) and is_concubine_heart_event(item))
    )


def has_intervening_small_world_tool(sent: list[dict], sender_id: int, prev: dict, cur: dict) -> bool:
    prev_epoch = float(prev.get("_epoch", 0) or 0)
    cur_epoch = float(cur.get("_epoch", 0) or 0)
    if sender_id <= 0 or prev_epoch <= 0 or cur_epoch <= prev_epoch:
        return False
    for item in sent:
        item_epoch = float(item.get("_epoch", 0) or 0)
        if item_epoch <= prev_epoch or item_epoch >= cur_epoch:
            continue
        if int(item.get("sender_id", 0) or 0) != sender_id:
            continue
        if is_small_world_tool_command(str(item.get("text") or "")):
            return True
    return False


def has_intervening_concubine_recovery_tool(sent: list[dict], sender_id: int, prev: dict, cur: dict) -> bool:
    prev_epoch = float(prev.get("_epoch", 0) or 0)
    cur_epoch = float(cur.get("_epoch", 0) or 0)
    if sender_id <= 0 or prev_epoch <= 0 or cur_epoch <= prev_epoch:
        return False
    for item in sent:
        item_epoch = float(item.get("_epoch", 0) or 0)
        if item_epoch <= prev_epoch or item_epoch >= cur_epoch:
            continue
        if int(item.get("sender_id", 0) or 0) != sender_id:
            continue
        raw = str(item.get("text") or "").strip()
        if any(raw == prefix or raw.startswith(prefix + " ") for prefix in CONCUBINE_RECOVERY_CHAIN_PREFIXES):
            return True
    return False


def count_since(events: list[dict], now: float, seconds: float) -> int:
    start = now - float(seconds)
    return sum(1 for item in events if float(item.get("_epoch", 0) or 0) >= start)


def count_non_burst_exempt_since(events: list[dict], now: float, seconds: float) -> int:
    start = now - float(seconds)
    return sum(
        1
        for item in events
        if float(item.get("_epoch", 0) or 0) >= start
        and not is_send_burst_exempt_event(item)
    )


def find_send_breach(events: list[dict], now: float, cfg: WatchdogConfig) -> str:
    sent = [
        item
        for item in events
        if item.get("event_type") == "sent" and float(item.get("_epoch", 0) or 0) > 0
    ]
    sent.sort(key=lambda item: float(item.get("_epoch", 0) or 0))
    if not sent:
        return ""

    if count_non_burst_exempt_since(sent, now, 120) >= cfg.total_2m_limit:
        return f"send burst: {cfg.total_2m_limit}+ sends in 120s"
    if count_non_burst_exempt_since(sent, now, 300) >= cfg.total_5m_limit:
        return f"send burst: {cfg.total_5m_limit}+ sends in 300s"
    if count_non_burst_exempt_since(sent, now, 900) >= cfg.total_15m_limit:
        return f"send burst: {cfg.total_15m_limit}+ sends in 900s"

    recent_gap_sent = [item for item in sent if float(item["_epoch"]) >= now - 30 * 60]
    for prev, cur in zip(recent_gap_sent, recent_gap_sent[1:]):
        gap = float(cur["_epoch"]) - float(prev["_epoch"])
        if is_safe_global_gap_pair(prev, cur):
            continue
        if 0 <= gap < cfg.min_any_gap_sec:
            return (
                f"global lock breach: gap {gap:.1f}s between "
                f"{prev.get('sender_id')}:{prev.get('text')} and {cur.get('sender_id')}:{cur.get('text')}"
            )

    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for item in sent:
        sender_id = int(item.get("sender_id", 0) or 0)
        text = command_key(str(item.get("text") or ""))
        if sender_id and text:
            grouped[(sender_id, text)].append(item)

    for (sender_id, text), all_items in grouped.items():
        all_items.sort(key=lambda item: float(item.get("_epoch", 0) or 0))
        guarded = is_guarded_command(text)
        refresh = is_refresh_command(text)
        sect_teach = is_sect_teach_command(text)
        window_sec = 90 * 60 if refresh else 45 * 60
        items = [item for item in all_items if float(item.get("_epoch", 0) or 0) >= now - window_sec]
        if len(items) < 2:
            continue
        heart_choice = is_heart_choice_command(text) and is_safe_heart_choice_repeat(items)
        replica_choice = all(is_replica_choice_event(item, text) for item in items)
        divination_daily_query_chain = is_safe_divination_daily_query_chain(items, text)
        if sect_teach or heart_choice:
            min_gap = 0
        elif divination_daily_query_chain:
            min_gap = DIVINATION_DAILY_QUERY_MIN_GAP_SEC
        elif guarded:
            min_gap = cfg.guarded_repeat_gap_sec
        elif refresh:
            min_gap = cfg.refresh_repeat_gap_sec
        elif is_dungeon_join_command(text):
            min_gap = 0
        else:
            min_gap = cfg.same_command_gap_sec
        for prev, cur in zip(items, items[1:]):
            gap = float(cur["_epoch"]) - float(prev["_epoch"])
            if refresh and has_intervening_small_world_tool(sent, sender_id, prev, cur):
                continue
            if text == CONCUBINE_STATUS_COMMAND and has_intervening_concubine_recovery_tool(sent, sender_id, prev, cur):
                continue
            if is_safe_phaseful_replay_repeat(prev, cur, text):
                continue
            if is_safe_concubine_voyage_retry_repeat(prev, cur, text):
                continue
            if is_safe_same_command_retry(prev, cur, text):
                continue
            if is_safe_replica_choice_repeat(prev, cur, text):
                continue
            if is_safe_replica_lightweight_retry_repeat(prev, cur, text):
                continue
            if min_gap > 0 and 0 <= gap < min_gap:
                return f"same command repeat: {sender_id}:{text} gap {gap:.1f}s"

        if replica_choice and has_duplicate_replica_choice_op_id(items, text):
            return f"same command repeat: {sender_id}:{text} duplicate replica choice op_id"
        if guarded and not replica_choice and not divination_daily_query_chain and len(items) > cfg.guarded_max_attempts_45m:
            return f"guarded command over attempts: {sender_id}:{text} {len(items)}/45m"
        if guarded and not replica_choice and not divination_daily_query_chain and len(items) >= 4:
            span = float(items[3]["_epoch"]) - float(items[0]["_epoch"])
            if span < cfg.guarded_fourth_min_span_sec:
                return f"guarded retry too dense: {sender_id}:{text} fourth span {span:.1f}s"
        if refresh and len(items) > cfg.refresh_max_attempts_90m:
            return f"refresh command over attempts: {sender_id}:{text} {len(items)}/90m"
        if sect_teach:
            recent_sect_items = [
                item for item in items
                if float(item.get("_epoch", 0) or 0) >= now - 10 * 60
            ]
            if len(recent_sect_items) > SECT_TEACH_MAX_ATTEMPTS_10M:
                return f"sect teach over attempts: {sender_id}:{text} {len(recent_sect_items)}/10m"

    return ""


def find_reply_breach(events: list[dict], now: float) -> str:
    recent_sent_ids = {
        int(item.get("message_id", 0) or 0)
        for item in events
        if item.get("event_type") == "sent" and float(item.get("_epoch", 0) or 0) >= now - 3600
    }
    recent_sent_ids.discard(0)
    if not recent_sent_ids:
        return ""
    for item in events:
        if item.get("event_type") not in {"message", "edit"}:
            continue
        if int(item.get("reply_to_msg_id", 0) or 0) not in recent_sent_ids:
            continue
        text = str(item.get("text") or "")
        if any(skip in text for skip in BOT_REPLY_FALSE_POSITIVE_KEYWORDS):
            continue
        for keyword in BOT_REPLY_HARD_STOP_KEYWORDS:
            if keyword in text:
                return f"hard-stop reply keyword: {keyword}"
    return ""


def find_journal_breach(service_name: str, since: str = "10 minutes ago") -> str:
    try:
        proc = subprocess.run(
            ["journalctl", "-u", service_name, "--since", since, "--no-pager"],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return f"journal check failed: {exc}"
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    for keyword in BOT_REPLY_HARD_STOP_KEYWORDS[:6]:
        if keyword in text:
            return f"journal hard-stop keyword: {keyword}"
    return ""


def state_db_path(project_root: Path) -> Path:
    return project_root / "data" / "state" / "chaogu_state.db"


def fuse_marker_path(project_root: Path) -> Path:
    return project_root / "data" / "state" / "safety_watchdog_fused.json"


def reset_marker_path(project_root: Path) -> Path:
    return project_root / "data" / "state" / "safety_watchdog_reset.json"


def get_reset_after_epoch(project_root: Path) -> float:
    marker = reset_marker_path(project_root)
    if not marker.exists():
        return 0.0
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    try:
        reset_at_epoch = float(payload.get("reset_at_epoch", 0) or 0) if isinstance(payload, dict) else 0.0
    except (TypeError, ValueError):
        reset_at_epoch = 0.0
    if reset_at_epoch > 0:
        return reset_at_epoch
    try:
        return float(marker.stat().st_mtime)
    except OSError:
        return 0.0


def disable_global_switch(project_root: Path) -> str:
    db_path = state_db_path(project_root)
    if not db_path.exists():
        return f"state db missing: {db_path}"
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('global_enabled', '0')")
        conn.commit()
    return "global_enabled=0"


def is_global_switch_enabled(project_root: Path) -> bool:
    db_path = state_db_path(project_root)
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return True
    return str(row[0]).strip() not in {"0", "false", "False", ""}


def stop_service(service_name: str) -> str:
    proc = subprocess.run(
        ["systemctl", "stop", service_name],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if proc.returncode == 0:
        return f"stopped {service_name}"
    return f"systemctl stop failed rc={proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:200]}"


def send_log_via_bot(env: dict[str, str], message: str) -> str:
    token = str(env.get("LOG_BOT_TOKEN") or "").strip()
    chat_id = str(env.get("LOG_GROUP_ID") or "").strip()
    if not token or not chat_id:
        return "log bot skipped: missing LOG_BOT_TOKEN or LOG_GROUP_ID"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
            "parse_mode": "HTML",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=payload, timeout=8) as response:
            body = response.read(256).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"log bot failed: {exc}"
    return f"log bot ok: {body[:120]}"


def admin_ids_from_env(env: dict[str, str]) -> list[int]:
    raw_values = [
        str(env.get("ADMIN_ID") or ""),
        str(env.get("ADMIN_IDS") or ""),
    ]
    ids: set[int] = set()
    for raw_value in raw_values:
        for part in raw_value.replace(";", ",").split(","):
            try:
                admin_id = int(part.strip() or 0)
            except (TypeError, ValueError):
                continue
            if admin_id > 0:
                ids.add(admin_id)
    return sorted(ids)


def format_admin_mentions_html(env: dict[str, str]) -> str:
    mentions = [
        f'<a href="tg://user?id={admin_id}">@管理员</a>'
        for admin_id in admin_ids_from_env(env)
    ]
    return " ".join(mentions)


def format_fuse_message(reason: str, action: str, actions: list[str], *, env: dict[str, str], dry_run: bool = False) -> str:
    lines = [
        "[SAFETY WATCHDOG WOULD FUSE]" if dry_run else "[SAFETY WATCHDOG FUSED]",
        f"reason: {reason}",
        f"action: {action}{' dry-run' if dry_run else ''}",
        *[f"- {item}" for item in actions],
    ]
    message = "\n".join(html.escape(line) for line in lines)
    mentions = format_admin_mentions_html(env)
    if mentions and not dry_run:
        message += f"\n关注：{mentions}"
    return message


def write_fuse_marker(project_root: Path, reason: str, actions: list[str]) -> None:
    marker = fuse_marker_path(project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fused_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "reason": reason,
        "actions": actions,
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_fuse_marker_reason(project_root: Path) -> str:
    marker = fuse_marker_path(project_root)
    if not marker.exists():
        return ""
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("reason") or "")


def perform_fuse(cfg: WatchdogConfig, env: dict[str, str], reason: str) -> None:
    marker = fuse_marker_path(cfg.project_root)
    if marker.exists():
        if cfg.dry_run or not is_global_switch_enabled(cfg.project_root):
            print(f"already fused: {marker}")
            return
        marker_reason = read_fuse_marker_reason(cfg.project_root)
        if marker_reason:
            print(f"stale fuse marker with global enabled, re-fusing: {marker}")
        else:
            print(f"stale unreadable fuse marker with global enabled, re-fusing: {marker}")
    actions: list[str] = []
    if cfg.dry_run:
        actions.append("dry-run: no action")
        message = (
            "[SAFETY WATCHDOG WOULD FUSE]\n"
            f"reason: {reason}\n"
            f"action: {cfg.action} dry-run\n"
            + "\n".join(f"- {item}" for item in actions)
        )
        print(message)
        return
    else:
        actions.append(disable_global_switch(cfg.project_root))
        if cfg.action == "stop":
            actions.append(stop_service(cfg.service_name))
    write_fuse_marker(cfg.project_root, reason, actions)
    message = (
        "[SAFETY WATCHDOG FUSED]\n"
        f"reason: {reason}\n"
        f"action: {cfg.action}{' dry-run' if cfg.dry_run else ''}\n"
        + "\n".join(f"- {item}" for item in actions)
    )
    print(message)
    print(send_log_via_bot(env, format_fuse_message(reason, cfg.action, actions, env=env)))


def current_log_file(project_root: Path) -> Path:
    return project_root / "data" / "messages" / f"{datetime.now().strftime('%Y-%m-%d')}.log"


def check_once(cfg: WatchdogConfig) -> str:
    now = time.time()
    events = read_recent_log_lines(current_log_file(cfg.project_root), cfg.max_lines)
    reset_after = get_reset_after_epoch(cfg.project_root)
    if reset_after > 0:
        events = [
            item for item in events
            if float(item.get("_epoch", 0) or 0) >= reset_after
        ]
    breach = find_send_breach(events, now, cfg)
    if breach:
        return breach
    breach = find_reply_breach(events, now)
    if breach:
        return breach
    return ""


def reset_fuse(cfg: WatchdogConfig) -> None:
    marker = fuse_marker_path(cfg.project_root)
    if marker.exists():
        marker.unlink()
        print(f"removed {marker}")
    else:
        print(f"no marker: {marker}")
    reset_marker = reset_marker_path(cfg.project_root)
    reset_marker.parent.mkdir(parents=True, exist_ok=True)
    reset_marker.write_text(
        json.dumps(
            {
                "reset_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC+8"),
                "reset_at_epoch": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {reset_marker}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xiuxian external safety watchdog")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--service", default="xiuxian")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--action", choices=("soft", "stop"), default="soft")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--max-lines", type=int, default=6000)
    parser.add_argument("--min-any-gap-sec", type=float, default=12.0)
    parser.add_argument("--total-2m-limit", type=int, default=8)
    parser.add_argument("--total-5m-limit", type=int, default=18)
    parser.add_argument("--total-15m-limit", type=int, default=38)
    parser.add_argument("--same-command-gap-sec", type=float, default=60.0)
    parser.add_argument("--guarded-repeat-gap-sec", type=float, default=90.0)
    parser.add_argument("--guarded-max-attempts-45m", type=int, default=4)
    parser.add_argument("--guarded-fourth-min-span-sec", type=float, default=14 * 60)
    parser.add_argument("--refresh-repeat-gap-sec", type=float, default=4 * 60)
    parser.add_argument("--refresh-max-attempts-90m", type=int, default=10)
    parser.add_argument("--journal-check-interval-sec", type=float, default=60.0)
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> WatchdogConfig:
    return WatchdogConfig(
        project_root=Path(args.project_root).resolve(),
        service_name=str(args.service),
        interval_sec=max(5.0, float(args.interval)),
        action=str(args.action),
        dry_run=bool(args.dry_run),
        max_lines=max(1000, int(args.max_lines)),
        min_any_gap_sec=float(args.min_any_gap_sec),
        total_2m_limit=int(args.total_2m_limit),
        total_5m_limit=int(args.total_5m_limit),
        total_15m_limit=int(args.total_15m_limit),
        same_command_gap_sec=float(args.same_command_gap_sec),
        guarded_repeat_gap_sec=float(args.guarded_repeat_gap_sec),
        guarded_max_attempts_45m=int(args.guarded_max_attempts_45m),
        guarded_fourth_min_span_sec=float(args.guarded_fourth_min_span_sec),
        refresh_repeat_gap_sec=float(args.refresh_repeat_gap_sec),
        refresh_max_attempts_90m=int(args.refresh_max_attempts_90m),
        journal_check_interval_sec=max(30.0, float(args.journal_check_interval_sec)),
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cfg = build_config(args)
    env = load_dotenv(cfg.project_root / ".env")
    if args.reset:
        reset_fuse(cfg)
        return 0

    if args.once:
        breach = check_once(cfg)
        journal_breach = find_journal_breach(cfg.service_name)
        if not breach and journal_breach and not journal_breach.startswith("journal check failed"):
            breach = journal_breach
        if breach:
            perform_fuse(cfg, env, breach)
            return 2
        print("watchdog ok")
        return 0

    last_journal_check = 0.0
    print(f"watchdog started: root={cfg.project_root} action={cfg.action}")
    while True:
        breach = check_once(cfg)
        now = time.time()
        if not breach and now - last_journal_check >= cfg.journal_check_interval_sec:
            last_journal_check = now
            journal_breach = find_journal_breach(cfg.service_name)
            if journal_breach and not journal_breach.startswith("journal check failed"):
                breach = journal_breach
        if breach:
            perform_fuse(cfg, env, breach)
        time.sleep(cfg.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
