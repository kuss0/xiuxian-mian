#!/usr/bin/env python3
"""External safety watchdog for the xiuxian automation service.

This process is intentionally independent from the main runtime. It only reads
the append-only message log and the local SQLite state DB. On clear abnormal
send patterns it writes a fuse marker, disables the global switch in SQLite,
and can stop the main systemd service.
"""

from __future__ import annotations

import argparse
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
    ".元神修炼",
    ".深度闭关",
    ".元婴出窍",
    ".闯塔",
    ".引道",
    ".搜寻节点",
    ".定星",
    ".神迹 布道",
    ".显灵",
    ".收割香火",
    ".神识淬炼",
)

REFRESH_PREFIXES = (
    ".小世界",
)

SMALL_WORLD_TOOL_PREFIXES = (
    ".显灵",
    ".收割香火",
    ".神识淬炼",
)

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


def is_small_world_tool_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in SMALL_WORLD_TOOL_PREFIXES)


def command_key(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith(".器灵试炼 "):
        return ".器灵试炼"
    if raw.startswith(".神识淬炼 "):
        return ".神识淬炼"
    if raw.startswith(".定星 "):
        return ".定星"
    if raw.startswith(".引道 "):
        return ".引道"
    return raw


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


def count_since(events: list[dict], now: float, seconds: float) -> int:
    start = now - float(seconds)
    return sum(1 for item in events if float(item.get("_epoch", 0) or 0) >= start)


def find_send_breach(events: list[dict], now: float, cfg: WatchdogConfig) -> str:
    sent = [
        item
        for item in events
        if item.get("event_type") == "sent" and float(item.get("_epoch", 0) or 0) > 0
    ]
    sent.sort(key=lambda item: float(item.get("_epoch", 0) or 0))
    if not sent:
        return ""

    if count_since(sent, now, 120) >= cfg.total_2m_limit:
        return f"send burst: {cfg.total_2m_limit}+ sends in 120s"
    if count_since(sent, now, 300) >= cfg.total_5m_limit:
        return f"send burst: {cfg.total_5m_limit}+ sends in 300s"
    if count_since(sent, now, 900) >= cfg.total_15m_limit:
        return f"send burst: {cfg.total_15m_limit}+ sends in 900s"

    recent_gap_sent = [item for item in sent if float(item["_epoch"]) >= now - 30 * 60]
    for prev, cur in zip(recent_gap_sent, recent_gap_sent[1:]):
        gap = float(cur["_epoch"]) - float(prev["_epoch"])
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
        window_sec = 90 * 60 if refresh else 45 * 60
        items = [item for item in all_items if float(item.get("_epoch", 0) or 0) >= now - window_sec]
        if len(items) < 2:
            continue
        if guarded:
            min_gap = cfg.guarded_repeat_gap_sec
        elif refresh:
            min_gap = cfg.refresh_repeat_gap_sec
        else:
            min_gap = cfg.same_command_gap_sec
        for prev, cur in zip(items, items[1:]):
            gap = float(cur["_epoch"]) - float(prev["_epoch"])
            if refresh and has_intervening_small_world_tool(sent, sender_id, prev, cur):
                continue
            if 0 <= gap < min_gap:
                return f"same command repeat: {sender_id}:{text} gap {gap:.1f}s"

        if guarded and len(items) > cfg.guarded_max_attempts_45m:
            return f"guarded command over attempts: {sender_id}:{text} {len(items)}/45m"
        if guarded and len(items) >= 4:
            span = float(items[3]["_epoch"]) - float(items[0]["_epoch"])
            if span < cfg.guarded_fourth_min_span_sec:
                return f"guarded retry too dense: {sender_id}:{text} fourth span {span:.1f}s"
        if refresh and len(items) > cfg.refresh_max_attempts_90m:
            return f"refresh command over attempts: {sender_id}:{text} {len(items)}/90m"

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


def disable_global_switch(project_root: Path) -> str:
    db_path = state_db_path(project_root)
    if not db_path.exists():
        return f"state db missing: {db_path}"
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('global_enabled', '0')")
        conn.commit()
    return "global_enabled=0"


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
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=payload, timeout=8) as response:
            body = response.read(256).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"log bot failed: {exc}"
    return f"log bot ok: {body[:120]}"


def write_fuse_marker(project_root: Path, reason: str, actions: list[str]) -> None:
    marker = fuse_marker_path(project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fused_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "reason": reason,
        "actions": actions,
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def perform_fuse(cfg: WatchdogConfig, env: dict[str, str], reason: str) -> None:
    marker = fuse_marker_path(cfg.project_root)
    if marker.exists():
        print(f"already fused: {marker}")
        return
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
    print(send_log_via_bot(env, message))


def current_log_file(project_root: Path) -> Path:
    return project_root / "data" / "messages" / f"{datetime.now().strftime('%Y-%m-%d')}.log"


def check_once(cfg: WatchdogConfig) -> str:
    now = time.time()
    events = read_recent_log_lines(current_log_file(cfg.project_root), cfg.max_lines)
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Xiuxian external safety watchdog")
    parser.add_argument("--project-root", default="/opt/xiuxian-main")
    parser.add_argument("--service", default="xiuxian")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--action", choices=("soft", "stop"), default="stop")
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
