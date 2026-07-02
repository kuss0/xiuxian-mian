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
from datetime import datetime, timezone, timedelta
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
SMALL_WORLD_SOURCE_MODULE = "小世界"
SMALL_WORLD_QUERY_FAMILY = "small_world_query"

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
CONCUBINE_HEART_CHOICE_OP_PREFIX = "concubine_heart_choice:"
CONCUBINE_VOYAGE_RETRY_OP_PREFIX = "concubine_voyage_retry:"
STORAGE_BAG_SOURCE_MODULE = "储物袋"
STORAGE_BAG_FAMILIES = {"storage_bag_listing", "storage_bag_buy", "storage_bag_gift"}
STORAGE_BAG_COMMANDS = {".上架", ".购买", ".赠送"}
STORAGE_BAG_CHAIN_PREFIX = "storage_bag:"
STORAGE_BAG_MAX_RETRIES = 3
TOWER_SOURCE_MODULE = "闯塔"
PHASEFUL_CHAIN_COMMANDS = {".深度闭关", ".元婴出窍"}
PHASEFUL_CHAIN_MARKERS = {"deep_retreat", "yuanying", "深度闭关", "元婴"}
PHASEFUL_CHAIN_MIN_GAP_SEC = 20
PHASEFUL_CHAIN_MAX_GAP_SEC = 5 * 60
DIVINATION_QUERY_COMMAND = ".卜筮问天"
DIVINATION_EXCHANGE_COMMAND = ".换取"
DIVINATION_SOURCE_MODULE = "卜筮问天"
DIVINATION_EXCHANGE_FAMILY = "divination_exchange"
DIVINATION_EXCHANGE_OP_PREFIX = "divination_exchange:"
DIVINATION_DAILY_QUERY_MIN_GAP_SEC = 55
DIVINATION_DAILY_QUERY_MAX_ATTEMPTS_45M = 20
WORLD_BOSS_SOURCE_MODULE = "真仙试锋"
WORLD_BOSS_FAMILY = "world_boss"
WORLD_BOSS_STATUS_COMMAND = ".世界boss"
WORLD_BOSS_MAX_STATUS_RETRY_TRY = 2
WORLD_BOSS_EVENT_COMMANDS = {
    WORLD_BOSS_STATUS_COMMAND,
    ".讨伐青元子 破幡",
    ".讨伐青元子 镇魂",
    ".讨伐青元子 护阵",
    ".讨伐青元子 强攻",
}
WORLD_BOSS_ACTION_COMMANDS = {
    ".讨伐青元子 破幡",
    ".讨伐青元子 镇魂",
    ".讨伐青元子 护阵",
    ".讨伐青元子 强攻",
}
WORLD_BOSS_MAX_ACTIONS_PER_IDENTITY_45M = 5
FISHING_SOURCE_MODULE = "灵溪垂钓"
FISHING_FAMILY = "fishing"
FISHING_SHORT_WINDOW_PREFIXES = (
    ".钓鱼状态",
    ".试探咬饵",
    ".提竿",
    ".收竿",
    ".开鱼",
    ".鱼篓",
)
FISHING_SHORT_WINDOW_PRIORITIES = {"urgent_reactive", "event_burst"}
FISHING_START_REPEAT_MIN_GAP_SEC = 30
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TZ_LOCAL = timezone(timedelta(hours=8))
LEGACY_PROJECT_ROOTS = (Path("/opt/xiuxian"),)

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


@dataclass
class BreachConfirmationState:
    reason: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    hits: int = 0


SOFT_CONFIRM_REASON_PREFIXES = (
    "same command repeat:",
    "global lock breach:",
    "guarded retry too dense:",
    "guarded command over attempts:",
    "refresh command over attempts:",
    "sect teach over attempts:",
)
HARD_BREACH_REASON_PREFIXES = (
    "send burst:",
    "hard-stop reply keyword:",
    "journal hard-stop keyword:",
    "world boss over attempts:",
)
SOFT_BREACH_CONFIRM_HITS = 2
SOFT_BREACH_CONFIRM_WINDOW_SEC = 90.0


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
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
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
    if is_storage_bag_command(str(item.get("text") or "")):
        return False
    return bool(str(item.get("family") or "").strip() or str(item.get("source_module") or "").strip())


def is_storage_bag_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == command or raw.startswith(command + " ") for command in STORAGE_BAG_COMMANDS)


def is_storage_bag_transfer_event(item: dict, text: str | None = None) -> bool:
    if not is_storage_bag_command(str(text if text is not None else item.get("text") or "")):
        return False
    family = str(item.get("family") or "").strip()
    source_module = str(item.get("source_module") or "").strip()
    if family not in STORAGE_BAG_FAMILIES and source_module != STORAGE_BAG_SOURCE_MODULE:
        return False
    chain_id = str(item.get("chain_id") or "").strip()
    op_id = str(item.get("op_id") or "").strip()
    return chain_id.startswith(STORAGE_BAG_CHAIN_PREFIX) and op_id.startswith(f"{chain_id}:")


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
        or is_verified_world_boss_action_event(cur)
        or is_verified_world_boss_action_event(prev)
        or is_safe_world_boss_status_gap_pair(prev, cur)
        or is_safe_storage_bag_retry_repeat(prev, cur, str(cur.get("text") or ""))
        or is_controlled_retry_event(cur)
        or is_marked_heart_choice_event(cur)
        or (is_concubine_heart_event(prev) and is_marked_heart_choice_event(cur))
        or is_safe_heart_global_gap_pair(prev, cur)
        or is_safe_divination_exchange_global_gap_pair(prev, cur)
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
    prev_sender = int(prev.get("sender_id", 0) or 0)
    cur_sender = int(cur.get("sender_id", 0) or 0)
    if prev_sender <= 0 or prev_sender != cur_sender:
        return False

    prev_text = command_key(str(prev.get("text") or ""))
    if prev_text == ".共历心劫" and cur_text in HEART_CHOICE_COMMANDS and is_concubine_heart_event(prev):
        return True
    if prev_text in HEART_CHOICE_COMMANDS and cur_text in HEART_CHOICE_COMMANDS and is_concubine_heart_event(prev):
        prev_parsed = parse_heart_choice_op(prev)
        cur_parsed = parse_heart_choice_op(cur)
        if prev_parsed and cur_parsed:
            prev_prompt, prev_round, prev_try, _prev_command = prev_parsed
            cur_prompt, cur_round, cur_try, _cur_command = cur_parsed
            if prev_prompt != cur_prompt:
                return False
            if cur_round == prev_round and cur_try == prev_try + 1:
                return True
            return cur_round == prev_round + 1 and cur_try == 0
        prev_reply = int(prev.get("reply_to_msg_id", 0) or 0)
        cur_reply = int(cur.get("reply_to_msg_id", 0) or 0)
        return prev_reply > 0 and prev_reply == cur_reply
    return False


def parse_heart_choice_op(item: dict) -> tuple[int, int, int, str] | None:
    op_id = str(item.get("op_id") or "").strip()
    if not op_id.startswith(CONCUBINE_HEART_CHOICE_OP_PREFIX):
        return None
    parts = op_id.split(":")
    if len(parts) != 6 or parts[0] != CONCUBINE_HEART_CHOICE_OP_PREFIX.rstrip(":"):
        return None
    try:
        sender_id = int(parts[1])
        prompt_msg_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    round_token = parts[3]
    try_token = parts[4]
    if not round_token.startswith("round") or not try_token.startswith("try"):
        return None
    try:
        round_no = int(round_token.removeprefix("round"))
        try_no = int(try_token.removeprefix("try"))
    except (TypeError, ValueError):
        return None
    command = command_key(parts[5])
    chain_id = str(item.get("chain_id") or "").strip()
    if chain_id != ":".join(parts[:4]):
        return None
    if sender_id <= 0 or int(item.get("sender_id", 0) or 0) != sender_id:
        return None
    if prompt_msg_id <= 0 or int(item.get("reply_to_msg_id", 0) or 0) != prompt_msg_id:
        return None
    if round_no not in {1, 2, 3} or try_no not in {0, 1}:
        return None
    if command not in HEART_CHOICE_COMMANDS:
        return None
    if command_key(str(item.get("text") or "")) != command:
        return None
    return prompt_msg_id, round_no, try_no, command


def is_marked_heart_choice_event(item: dict) -> bool:
    if not is_heart_choice_command(str(item.get("text") or "")):
        return False
    if not is_concubine_heart_event(item):
        return False
    return parse_heart_choice_op(item) is not None


def is_safe_marked_heart_choice_repeat(items: list[dict]) -> bool:
    if len(items) > 6:
        return False
    parsed_items = []
    for item in sorted(items, key=lambda payload: float(payload.get("_epoch", 0) or 0)):
        parsed = parse_heart_choice_op(item)
        if not parsed:
            return False
        parsed_items.append((item, parsed))

    prompt_ids = {parsed[0] for _item, parsed in parsed_items}
    if len(prompt_ids) != 1:
        return False

    seen_round_tries: dict[int, set[int]] = defaultdict(set)
    last_round = 0
    for item, (_prompt_msg_id, round_no, try_no, _command) in parsed_items:
        if try_no in seen_round_tries[round_no]:
            return False
        if try_no == 0:
            if str(item.get("priority") or "").strip().lower() == "retry":
                return False
            if round_no != last_round + 1:
                return False
            last_round = round_no
        else:
            if not is_controlled_retry_event(item):
                return False
            if 0 not in seen_round_tries[round_no]:
                return False
            if round_no != last_round:
                return False
        seen_round_tries[round_no].add(try_no)

    return all(0 in tries and tries.issubset({0, 1}) for tries in seen_round_tries.values())


def is_safe_marked_heart_choice_sequence(items: list[dict]) -> bool:
    if not items or len(items) > 6:
        return False
    parsed_items = []
    for item in sorted(items, key=lambda payload: float(payload.get("_epoch", 0) or 0)):
        parsed = parse_heart_choice_op(item)
        if not parsed:
            return False
        parsed_items.append((item, parsed))

    prompt_ids = {parsed[0] for _item, parsed in parsed_items}
    if len(prompt_ids) != 1:
        return False

    seen_round_tries: dict[int, set[int]] = defaultdict(set)
    last_round: int | None = None
    for item, (_prompt_msg_id, round_no, try_no, _command) in parsed_items:
        if try_no in seen_round_tries[round_no]:
            return False
        if last_round is not None:
            if round_no < last_round or round_no > last_round + 1:
                return False
            if round_no == last_round and not seen_round_tries[round_no]:
                return False
        if try_no == 0:
            if str(item.get("priority") or "").strip().lower() == "retry":
                return False
            if 1 in seen_round_tries[round_no]:
                return False
            last_round = round_no
        else:
            if not is_controlled_retry_event(item):
                return False
            if 0 not in seen_round_tries[round_no] and last_round is not None:
                return False
            if last_round is not None and round_no != last_round:
                return False
            last_round = round_no
        seen_round_tries[round_no].add(try_no)

    return True


def is_safe_heart_choice_repeat(items: list[dict]) -> bool:
    if not all(is_concubine_heart_event(item) for item in items):
        return False
    reply_ids = {
        int(item.get("reply_to_msg_id", 0) or 0)
        for item in items
    }
    reply_ids.discard(0)
    if len(reply_ids) != 1:
        return False
    has_marked_choice = any(
        str(item.get("op_id") or "").strip().startswith(CONCUBINE_HEART_CHOICE_OP_PREFIX)
        for item in items
    )
    if has_marked_choice:
        return is_safe_marked_heart_choice_repeat(items)
    if len(items) <= 3:
        return True
    return False


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
    if is_safe_storage_bag_retry_repeat(prev, cur, text):
        return True
    if str(prev.get("priority") or "").strip().lower() == "retry":
        return False
    if not is_controlled_retry_event(cur):
        return False
    return has_matching_send_markers(prev, cur)


def parse_storage_bag_transfer_op(item: dict, text: str | None = None) -> tuple[str, str, int] | None:
    if not is_storage_bag_transfer_event(item, text):
        return None
    op_id = str(item.get("op_id") or "").strip()
    chain_id = str(item.get("chain_id") or "").strip()
    tail = op_id[len(chain_id) + 1:]
    parts = tail.rsplit(":", 2)
    if len(parts) != 3:
        return None
    family, stage, count_text = parts
    if family not in {"storage_bag_listing", "storage_bag_buy", "storage_bag_gift", "storage_bag_gift_locator"}:
        return None
    if stage not in {"send", "retry"}:
        return None
    try:
        count = int(count_text)
    except (TypeError, ValueError):
        return None
    if stage == "send" and count != 0:
        return None
    if stage == "retry" and not (1 <= count <= STORAGE_BAG_MAX_RETRIES):
        return None
    priority = str(item.get("priority") or "").strip().lower()
    if stage == "send" and priority not in {"event_burst", "chain", "normal"}:
        return None
    if stage == "retry" and priority != "retry":
        return None
    return chain_id, family, count


def is_safe_storage_bag_retry_repeat(prev: dict, cur: dict, text: str) -> bool:
    prev_parsed = parse_storage_bag_transfer_op(prev, text)
    cur_parsed = parse_storage_bag_transfer_op(cur, text)
    if not prev_parsed or not cur_parsed:
        return False
    prev_chain, prev_family, prev_count = prev_parsed
    cur_chain, cur_family, cur_count = cur_parsed
    if prev_chain != cur_chain or prev_family != cur_family:
        return False
    return cur_count == prev_count + 1


def is_safe_storage_bag_retry_chain(items: list[dict], text: str) -> bool:
    parsed_items = []
    for item in sorted(items, key=lambda payload: float(payload.get("_epoch", 0) or 0)):
        parsed = parse_storage_bag_transfer_op(item, text)
        if not parsed:
            return False
        parsed_items.append((item, parsed))
    if len(parsed_items) < 2:
        return False
    chains = {parsed[0] for _item, parsed in parsed_items}
    families = {parsed[1] for _item, parsed in parsed_items}
    if len(chains) != 1 or len(families) != 1:
        return False
    op_ids = [str(item.get("op_id") or "").strip() for item, _parsed in parsed_items]
    if len(op_ids) != len(set(op_ids)):
        return False
    expected = 0
    for _item, (_chain, _family, count) in parsed_items:
        if count != expected:
            return False
        expected += 1
    return expected <= STORAGE_BAG_MAX_RETRIES + 1


def is_safe_phaseful_chain_relaunch(prev: dict, cur: dict, text: str, gap: float) -> bool:
    if command_key(text) not in PHASEFUL_CHAIN_COMMANDS:
        return False
    if not (PHASEFUL_CHAIN_MIN_GAP_SEC <= float(gap) <= PHASEFUL_CHAIN_MAX_GAP_SEC):
        return False
    if str(prev.get("priority") or "").strip().lower() != "chain":
        return False
    if str(cur.get("priority") or "").strip().lower() != "chain":
        return False
    markers = {
        str(prev.get("family") or "").strip(),
        str(prev.get("source_module") or "").strip(),
        str(cur.get("family") or "").strip(),
        str(cur.get("source_module") or "").strip(),
    }
    markers.discard("")
    return bool(markers.intersection(PHASEFUL_CHAIN_MARKERS) and has_matching_send_markers(prev, cur))


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


def is_safe_divination_exchange_global_gap_pair(prev: dict, cur: dict) -> bool:
    if command_key(str(prev.get("text") or "")) != DIVINATION_QUERY_COMMAND:
        return False
    if command_key(str(cur.get("text") or "")) != DIVINATION_EXCHANGE_COMMAND:
        return False
    if int(prev.get("sender_id", 0) or 0) != int(cur.get("sender_id", 0) or 0):
        return False
    if str(prev.get("source_module") or "").strip() != DIVINATION_SOURCE_MODULE:
        return False
    if str(cur.get("source_module") or "").strip() != DIVINATION_SOURCE_MODULE:
        return False
    if str(cur.get("family") or "").strip() != DIVINATION_EXCHANGE_FAMILY:
        return False
    if str(cur.get("priority") or "").strip().lower() != "urgent_reactive":
        return False
    return str(cur.get("op_id") or "").strip().startswith(DIVINATION_EXCHANGE_OP_PREFIX)


def is_small_world_tool_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in SMALL_WORLD_TOOL_PREFIXES)


def is_marked_small_world_refresh_event(item: dict) -> bool:
    if command_key(str(item.get("text") or "")) != ".小世界":
        return False
    return (
        str(item.get("family") or "").strip() == SMALL_WORLD_QUERY_FAMILY
        and str(item.get("source_module") or "").strip() == SMALL_WORLD_SOURCE_MODULE
        and str(item.get("priority") or "").strip().lower() == "chain"
    )


def is_world_boss_event(item: dict, text: str | None = None) -> bool:
    raw = command_key(str(text if text is not None else item.get("text") or ""))
    if raw not in WORLD_BOSS_EVENT_COMMANDS:
        return False
    return (
        str(item.get("source_module") or "").strip() == WORLD_BOSS_SOURCE_MODULE
        or str(item.get("family") or "").strip() == WORLD_BOSS_FAMILY
    )


def is_world_boss_action_event(item: dict, text: str | None = None) -> bool:
    if command_key(str(text if text is not None else item.get("text") or "")) not in WORLD_BOSS_ACTION_COMMANDS:
        return False
    return is_world_boss_event(item, text)


def is_world_boss_status_event(item: dict, text: str | None = None) -> bool:
    if command_key(str(text if text is not None else item.get("text") or "")) != WORLD_BOSS_STATUS_COMMAND:
        return False
    return is_world_boss_event(item, text)


def is_fishing_short_window_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in FISHING_SHORT_WINDOW_PREFIXES)


def is_fishing_short_window_event(item: dict) -> bool:
    if not is_fishing_short_window_command(str(item.get("text") or "")):
        return False
    if str(item.get("priority") or "").strip().lower() not in FISHING_SHORT_WINDOW_PRIORITIES:
        return False
    return (
        str(item.get("source_module") or "").strip() == FISHING_SOURCE_MODULE
        or str(item.get("family") or "").strip() == FISHING_FAMILY
    )


def is_fishing_start_command(text: str) -> bool:
    return str(text or "").strip().startswith(".钓鱼 ")


def is_marked_fishing_event(item: dict) -> bool:
    return (
        str(item.get("source_module") or "").strip() == FISHING_SOURCE_MODULE
        or str(item.get("family") or "").strip() == FISHING_FAMILY
    )


def is_marked_fishing_start_event(item: dict, text: str | None = None) -> bool:
    return is_fishing_start_command(str(text if text is not None else item.get("text") or "")) and is_marked_fishing_event(item)


def has_intervening_fishing_progress(sent: list[dict], sender_id: int, prev: dict, cur: dict) -> bool:
    if not is_marked_fishing_start_event(prev) or not is_marked_fishing_start_event(cur):
        return False
    prev_epoch = float(prev.get("_epoch", 0) or 0)
    cur_epoch = float(cur.get("_epoch", 0) or 0)
    if cur_epoch - prev_epoch < FISHING_START_REPEAT_MIN_GAP_SEC:
        return False
    if prev_epoch <= 0 or cur_epoch <= prev_epoch:
        return False
    for item in sent:
        if int(item.get("sender_id", 0) or 0) != int(sender_id or 0):
            continue
        epoch = float(item.get("_epoch", 0) or 0)
        if not (prev_epoch < epoch < cur_epoch):
            continue
        if is_fishing_short_window_event(item):
            return True
    return False


def parse_world_boss_action_op(item: dict) -> tuple[str, int, str, int, int] | None:
    if not is_world_boss_action_event(item):
        return None
    op_id = str(item.get("op_id") or "").strip()
    chain_id = str(item.get("chain_id") or "").strip()
    prefix = f"{chain_id}:action:"
    if not chain_id.startswith("world_boss:") or not op_id.startswith(prefix):
        return None
    tail = op_id[len(prefix):]
    parts = tail.rsplit(":", 3)
    if len(parts) != 4:
        return None
    identity_text, action, action_seq_text, try_token = parts
    if not try_token.startswith("try"):
        return None
    try:
        identity_id = int(identity_text)
        action_seq = int(action_seq_text)
        try_no = int(try_token.removeprefix("try"))
    except (TypeError, ValueError):
        return None
    if identity_id <= 0 or action_seq <= 0 or try_no < 0:
        return None
    if int(item.get("sender_id", 0) or 0) != identity_id:
        return None
    if command_key(str(item.get("text") or "")) != f".讨伐青元子 {action}":
        return None
    if not _world_boss_chain_day_matches_item(chain_id, item):
        return None
    return chain_id, identity_id, action, action_seq, try_no


def is_verified_world_boss_action_event(item: dict) -> bool:
    return parse_world_boss_action_op(item) is not None


def parse_world_boss_status_op(item: dict) -> tuple[str, int, int, int] | None:
    if not is_world_boss_status_event(item):
        return None
    op_id = str(item.get("op_id") or "").strip()
    chain_id = str(item.get("chain_id") or "").strip()
    prefix = f"{chain_id}:status:"
    if not chain_id.startswith("world_boss:") or not op_id.startswith(prefix):
        return None
    tail = op_id[len(prefix):]
    parts = tail.rsplit(":", 2)
    if len(parts) != 3:
        return None
    identity_text, try_token, ts_text = parts
    if not try_token.startswith("try"):
        return None
    try:
        identity_id = int(identity_text)
        try_no = int(try_token.removeprefix("try"))
        sent_ts = int(ts_text)
    except (TypeError, ValueError):
        return None
    if identity_id <= 0 or try_no < 0 or sent_ts <= 0:
        return None
    if int(item.get("sender_id", 0) or 0) != identity_id:
        return None
    if not _world_boss_chain_day_matches_item(chain_id, item):
        return None
    return chain_id, identity_id, try_no, sent_ts


def is_verified_world_boss_status_event(item: dict) -> bool:
    return parse_world_boss_status_op(item) is not None


def is_safe_world_boss_status_repeat(items: list[dict], sender_id: int) -> bool:
    parsed_items = []
    for item in sorted(items, key=lambda payload: float(payload.get("_epoch", 0) or 0)):
        parsed = parse_world_boss_status_op(item)
        if not parsed:
            return False
        parsed_items.append((item, parsed))
    if not parsed_items:
        return False
    chains = {parsed[0] for _item, parsed in parsed_items}
    identities = {parsed[1] for _item, parsed in parsed_items}
    if len(chains) != 1 or identities != {int(sender_id)}:
        return False
    previous_try = -1
    previous_ts = 0
    for item, (_chain_id, _identity_id, try_no, sent_ts) in parsed_items:
        priority = str(item.get("priority") or "").strip().lower()
        if try_no == 0:
            if priority == "retry":
                return False
        elif priority != "retry":
            return False
        if try_no > WORLD_BOSS_MAX_STATUS_RETRY_TRY:
            return False
        if try_no <= previous_try:
            return False
        if sent_ts < previous_ts:
            return False
        previous_try = try_no
        previous_ts = sent_ts
    return True


def is_safe_world_boss_status_gap_pair(prev: dict, cur: dict) -> bool:
    sender_id = int(cur.get("sender_id", 0) or 0)
    if sender_id <= 0 or int(prev.get("sender_id", 0) or 0) != sender_id:
        return False
    prev_parsed = parse_world_boss_status_op(prev)
    cur_parsed = parse_world_boss_status_op(cur)
    if not prev_parsed or not cur_parsed:
        return False
    prev_chain, _prev_identity, prev_try, _prev_ts = prev_parsed
    cur_chain, _cur_identity, cur_try, _cur_ts = cur_parsed
    return prev_chain == cur_chain and cur_try == prev_try + 1 and is_safe_world_boss_status_repeat([prev, cur], sender_id)


def _world_boss_chain_day(chain_id: str) -> str:
    parts = str(chain_id or "").split(":")
    if len(parts) < 3 or parts[0] != "world_boss":
        return ""
    candidate = parts[1]
    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        return candidate
    return ""


def _item_local_day(item: dict) -> str:
    epoch = float(item.get("_epoch", 0) or 0)
    if epoch <= 0:
        return ""
    return datetime.fromtimestamp(epoch, TZ_LOCAL).strftime("%Y-%m-%d")


def _world_boss_chain_day_matches_item(chain_id: str, item: dict) -> bool:
    chain_day = _world_boss_chain_day(chain_id)
    if not chain_day:
        return True
    item_day = _item_local_day(item)
    return bool(item_day and item_day == chain_day)


def has_world_boss_status_retry_marker(items: list[dict]) -> bool:
    return any(str(item.get("priority") or "").strip().lower() == "retry" for item in items)


def find_world_boss_attempt_breach(items: list[dict], sender_id: int) -> str:
    action_tries: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    for item in items:
        parsed = parse_world_boss_action_op(item)
        if not parsed:
            return f"same command repeat: {sender_id}:{command_key(str(item.get('text') or ''))} invalid world boss op_id"
        chain_id, identity_id, action, action_seq, try_no = parsed
        if identity_id != sender_id:
            return f"same command repeat: {sender_id}:{command_key(str(item.get('text') or ''))} invalid world boss sender"
        key = (chain_id, action, action_seq)
        if try_no in action_tries[key]:
            return f"same command repeat: {sender_id}:{command_key(str(item.get('text') or ''))} duplicate world boss try"
        action_tries[key].add(try_no)

    if len(action_tries) > WORLD_BOSS_MAX_ACTIONS_PER_IDENTITY_45M:
        return f"world boss over attempts: {sender_id} {len(action_tries)}/45m"
    return ""


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
        or is_verified_world_boss_action_event(item)
        or is_fishing_short_window_event(item)
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


def get_marked_heart_choice_burst_exempt_ids(events: list[dict]) -> set[int]:
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for item in events:
        if not is_heart_choice_command(str(item.get("text") or "")) or not is_concubine_heart_event(item):
            continue
        parsed = parse_heart_choice_op(item)
        if not parsed:
            continue
        prompt_msg_id, _round_no, _try_no, _command = parsed
        sender_id = int(item.get("sender_id", 0) or 0)
        if sender_id <= 0 or prompt_msg_id <= 0:
            continue
        grouped[(sender_id, prompt_msg_id)].append(item)

    exempt_ids: set[int] = set()
    for items in grouped.values():
        if is_safe_marked_heart_choice_sequence(items):
            exempt_ids.update(id(item) for item in items)
    return exempt_ids


def get_world_boss_status_burst_exempt_ids(events: list[dict]) -> set[int]:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for item in events:
        parsed = parse_world_boss_status_op(item)
        if not parsed:
            continue
        chain_id, identity_id, _try_no, _sent_ts = parsed
        grouped[(identity_id, chain_id)].append(item)

    exempt_ids: set[int] = set()
    for (identity_id, _chain_id), items in grouped.items():
        if len(items) < 2:
            continue
        if is_safe_world_boss_status_repeat(items, identity_id):
            exempt_ids.update(id(item) for item in items)
    return exempt_ids


def count_non_burst_exempt_since(events: list[dict], now: float, seconds: float) -> int:
    start = now - float(seconds)
    recent = [item for item in events if float(item.get("_epoch", 0) or 0) >= start]
    marked_heart_choice_exempt_ids = get_marked_heart_choice_burst_exempt_ids(recent)
    world_boss_status_exempt_ids = get_world_boss_status_burst_exempt_ids(recent)
    storage_bag_exempt_ids = get_storage_bag_retry_exempt_ids(recent)
    return sum(
        1
        for item in recent
        if not is_send_burst_exempt_event(item)
        and id(item) not in marked_heart_choice_exempt_ids
        and id(item) not in world_boss_status_exempt_ids
        and id(item) not in storage_bag_exempt_ids
    )


def get_storage_bag_retry_exempt_ids(events: list[dict]) -> set[int]:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for item in events:
        raw_text = str(item.get("text") or "")
        parsed = parse_storage_bag_transfer_op(item, raw_text)
        if not parsed:
            continue
        chain_id, family, _count = parsed
        sender_id = int(item.get("sender_id", 0) or 0)
        if sender_id > 0:
            grouped[(sender_id, chain_id, family)].append(item)

    exempt_ids: set[int] = set()
    for (_sender_id, _chain_id, _family), items in grouped.items():
        if len(items) < 2:
            continue
        if is_safe_storage_bag_retry_chain(items, str(items[0].get("text") or "")):
            exempt_ids.update(id(item) for item in items)
    return exempt_ids


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

    world_boss_by_sender: dict[int, list[dict]] = defaultdict(list)
    for item in sent:
        if float(item.get("_epoch", 0) or 0) < now - 45 * 60:
            continue
        if not is_world_boss_action_event(item):
            continue
        sender_id = int(item.get("sender_id", 0) or 0)
        if sender_id > 0:
            world_boss_by_sender[sender_id].append(item)
    for sender_id, items in world_boss_by_sender.items():
        breach = find_world_boss_attempt_breach(items, sender_id)
        if breach:
            return breach

    recent_gap_sent = [
        item
        for item in sent
        if float(item["_epoch"]) >= now - 30 * 60
        and not is_fishing_short_window_event(item)
    ]
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
        marked_heart_choice_sequence = is_heart_choice_command(text) and is_safe_marked_heart_choice_sequence(items)
        replica_choice = all(is_replica_choice_event(item, text) for item in items)
        divination_daily_query_chain = is_safe_divination_daily_query_chain(items, text)
        world_boss_action_chain = all(is_world_boss_action_event(item, text) for item in items)
        world_boss_status_chain = all(is_world_boss_status_event(item, text) for item in items)
        fishing_short_window_chain = all(is_fishing_short_window_event(item) for item in items)
        storage_bag_retry_chain = is_safe_storage_bag_retry_chain(items, text)
        safe_world_boss_status_chain = world_boss_status_chain and is_safe_world_boss_status_repeat(items, sender_id)
        marked_small_world_refresh_chain = refresh and all(is_marked_small_world_refresh_event(item) for item in items)
        if (
            sect_teach
            or heart_choice
            or marked_heart_choice_sequence
            or world_boss_action_chain
            or safe_world_boss_status_chain
            or fishing_short_window_chain
            or storage_bag_retry_chain
            or marked_small_world_refresh_chain
        ):
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
            if refresh and is_marked_small_world_refresh_event(cur):
                continue
            if text == CONCUBINE_STATUS_COMMAND and has_intervening_concubine_recovery_tool(sent, sender_id, prev, cur):
                continue
            if is_safe_phaseful_replay_repeat(prev, cur, text):
                continue
            if is_safe_concubine_voyage_retry_repeat(prev, cur, text):
                continue
            if is_safe_same_command_retry(prev, cur, text):
                continue
            if is_fishing_start_command(text) and has_intervening_fishing_progress(sent, sender_id, prev, cur):
                continue
            if is_safe_phaseful_chain_relaunch(prev, cur, text, gap):
                continue
            if is_safe_replica_choice_repeat(prev, cur, text):
                continue
            if is_safe_replica_lightweight_retry_repeat(prev, cur, text):
                continue
            if safe_world_boss_status_chain:
                continue
            if storage_bag_retry_chain:
                continue
            if min_gap > 0 and 0 <= gap < min_gap:
                return f"same command repeat: {sender_id}:{text} gap {gap:.1f}s"

        if replica_choice and has_duplicate_replica_choice_op_id(items, text):
            return f"same command repeat: {sender_id}:{text} duplicate replica choice op_id"
        if world_boss_action_chain:
            op_ids = [str(item.get("op_id") or "").strip() for item in items]
            if not all(op_ids):
                return f"same command repeat: {sender_id}:{text} missing world boss op_id"
            if len(op_ids) != len(set(op_ids)):
                return f"same command repeat: {sender_id}:{text} duplicate world boss op_id"
            breach = find_world_boss_attempt_breach(items, sender_id)
            if breach:
                return breach
        if world_boss_status_chain and has_world_boss_status_retry_marker(items) and not safe_world_boss_status_chain:
            return f"same command repeat: {sender_id}:{text} invalid world boss status retry"
        if (
            guarded
            and not replica_choice
            and not divination_daily_query_chain
            and not world_boss_status_chain
            and len(items) > cfg.guarded_max_attempts_45m
        ):
            return f"guarded command over attempts: {sender_id}:{text} {len(items)}/45m"
        if (
            guarded
            and not replica_choice
            and not divination_daily_query_chain
            and not world_boss_status_chain
            and len(items) >= 4
        ):
            span = float(items[3]["_epoch"]) - float(items[0]["_epoch"])
            if span < cfg.guarded_fourth_min_span_sec:
                return f"guarded retry too dense: {sender_id}:{text} fourth span {span:.1f}s"
        if refresh:
            unmarked_refresh_items = [
                item for item in items
                if not is_marked_small_world_refresh_event(item)
            ]
            if len(unmarked_refresh_items) > cfg.refresh_max_attempts_90m:
                return (
                    f"refresh command over attempts: {sender_id}:{text} "
                    f"{len(unmarked_refresh_items)}/90m"
                )
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


def read_proc_cmdline(pid: int) -> str:
    try:
        return (Path("/proc") / str(int(pid)) / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def find_legacy_xiuxian_processes(project_root: Path) -> list[dict[str, object]]:
    current_script = str(Path(project_root).resolve() / "xiuxian.py")
    legacy_scripts = {str(root / "xiuxian.py") for root in LEGACY_PROJECT_ROOTS}
    rows: list[dict[str, object]] = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return rows
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline = read_proc_cmdline(pid)
        if "xiuxian.py" not in cmdline:
            continue
        if current_script in cmdline:
            continue
        if any(script in cmdline for script in legacy_scripts):
            rows.append({"pid": pid, "cmdline": cmdline[:500]})
    return rows


def stop_legacy_xiuxian_processes(processes: list[dict[str, object]]) -> str:
    stopped = []
    failed = []
    for item in processes:
        try:
            pid = int(item.get("pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue
        try:
            os.kill(pid, 9)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except Exception as exc:
            failed.append(f"{pid}:{exc}")
    parts = []
    if stopped:
        parts.append(f"stopped legacy xiuxian pids={','.join(str(pid) for pid in stopped)}")
    if failed:
        parts.append(f"legacy stop failed={';'.join(failed)[:160]}")
    return "; ".join(parts) or "legacy xiuxian already gone"


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
        if cfg.dry_run:
            print(f"already fused: {marker}")
            return
        marker_reason = read_fuse_marker_reason(cfg.project_root)
        if marker_reason == reason:
            if is_global_switch_enabled(cfg.project_root):
                action = disable_global_switch(cfg.project_root)
                print(f"already fused for same reason; refreshed {action}: {marker}")
            else:
                print(f"already fused: {marker}")
            return
        if not is_global_switch_enabled(cfg.project_root):
            print(f"already fused: {marker}")
            return
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
        if reason.startswith("legacy xiuxian process:"):
            actions.append(stop_legacy_xiuxian_processes(find_legacy_xiuxian_processes(cfg.project_root)))
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
    legacy_processes = find_legacy_xiuxian_processes(cfg.project_root)
    if legacy_processes:
        sample = legacy_processes[0]
        return f"legacy xiuxian process: pid={sample.get('pid')} {sample.get('cmdline')}"
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


def reset_breach_confirmation(state: BreachConfirmationState) -> None:
    state.reason = ""
    state.first_seen = 0.0
    state.last_seen = 0.0
    state.hits = 0


def is_hard_breach_reason(reason: str) -> bool:
    raw = str(reason or "")
    return any(raw.startswith(prefix) for prefix in HARD_BREACH_REASON_PREFIXES)


def needs_soft_breach_confirmation(reason: str) -> bool:
    raw = str(reason or "")
    if is_hard_breach_reason(raw):
        return False
    return any(raw.startswith(prefix) for prefix in SOFT_CONFIRM_REASON_PREFIXES)


def should_fuse_breach(reason: str, state: BreachConfirmationState, now: float) -> bool:
    raw = str(reason or "").strip()
    if not raw:
        reset_breach_confirmation(state)
        return False
    if not needs_soft_breach_confirmation(raw):
        reset_breach_confirmation(state)
        return True
    if (
        state.reason != raw
        or state.last_seen <= 0
        or float(now) - state.last_seen > SOFT_BREACH_CONFIRM_WINDOW_SEC
    ):
        state.reason = raw
        state.first_seen = float(now)
        state.last_seen = float(now)
        state.hits = 1
        return False
    state.last_seen = float(now)
    state.hits += 1
    return state.hits >= SOFT_BREACH_CONFIRM_HITS


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
    breach_confirmation = BreachConfirmationState()
    print(f"watchdog started: root={cfg.project_root} action={cfg.action}")
    while True:
        breach = check_once(cfg)
        now = time.time()
        if not breach and now - last_journal_check >= cfg.journal_check_interval_sec:
            last_journal_check = now
            journal_breach = find_journal_breach(cfg.service_name)
            if journal_breach and not journal_breach.startswith("journal check failed"):
                breach = journal_breach
        if breach and should_fuse_breach(breach, breach_confirmation, now):
            perform_fuse(cfg, env, breach)
        time.sleep(cfg.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
