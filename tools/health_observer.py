#!/usr/bin/env python3
"""Read-only runtime observer for the Xiuxian automation service.

This observer records service state and recent journal warning/error signals.
It never sends Telegram/game commands and never calls Tianjige APIs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICES = ("xiuxian.service", "xiuxian-safety-watchdog.service")
LEGACY_PROJECT_ROOTS = (Path("/opt/xiuxian"),)
HARD_PATTERN = re.compile(r"Traceback|ERROR|Exception|FATAL|FloodWait|FUSED|熔断|风暴", re.I)
WARN_PATTERN = re.compile(r"超时|补发|未发送|失窃|暂停|发送失败|回复失败|未识别|无法识别|过期|锁", re.I)
BENIGN_HARD_CONTEXT_PATTERN = re.compile(
    r"already fused:|探寻裂缝结果：遭遇风暴|answerCallbackQuery failed:.*query is too old and response timeout expired",
    re.I,
)
BENIGN_WARN_CONTEXT_PATTERN = re.compile(
    r"无补发|不补发|无需补发|题库内超时未作答|题库匹配|自动副本：收到 @，但未找到|worker 优雅退出超时，强制结束|归位结算吃掉原指令，已补发一次|launching 超时，已回退|launching 超时，改用状态查询校准|共历心劫抉择无回合推进，已停止旧 prompt|续轮指令超时无确认，改用状态查询校准|准备补发一次|结果编辑未留存，已按正常周期恢复|交由模块状态机继续|指令排队超时未发送"
)
TELETHON_WRONG_SESSION_PATTERN = re.compile(
    r"Security error while unpacking a received message: Server replied with a wrong session ID",
    re.I,
)
COOLDOWN_REPLY_PATTERN = re.compile(
    r"请在\s*\S+\s*后再试|无法立即|尚在\S*冷却中|尚未重启|灵气尚未平复|梦图感应尚未重启|天机链路尚未重铸"
)
MODULE_ERROR_ATTENTION_PATTERN = re.compile(r"超时|失败|异常|无法|未识别|安全锁|熔断|风暴|吞|卡住|人工|manual", re.I)
BENIGN_MODULE_ERROR_PATTERN = re.compile(
    r"今日.*已达上限|今日.*已达\s*\d+\s*轮|次数已达上限|冷却中|尚未恢复|尚未重启|等待|无需|不补发|稍后重试|准备补发一次|回到时间线重算|需重算时间线|不连续查盘"
)
ACTIVE_STATUS_COMMANDS = {".查看闭关", ".元婴状态"}
GUARDED_COMMAND_REPEAT_ALERT_MIN = 4
GUARDED_COMMAND_REPEAT_ALERT_MIN_BY_COMMAND = {
    # One small-world prayer refresh round can legitimately send:
    # initial panel + post-tool panel + up to 7 refresh panels.
    ".小世界": 10,
}
GUARDED_BUSINESS_PREFIXES = (
    ".入梦寻图",
    ".天机代卜",
    ".深度闭关",
    ".元婴出窍",
    ".闯塔",
    ".引道",
    ".搜寻节点",
    ".小世界",
    ".神迹 布道",
    ".神迹 赈灾",
    ".显灵",
    ".收割香火",
    ".神识淬炼",
    ".卜筮问天",
    ".换取",
)
PENDING_PHASE_SUFFIX = "_pending"
PHASEFUL_ATTENTION_PHASES = {
    "summary_due",
    "observing_summary",
    "waiting_summary",
    "post_summary_wait",
    "queued_launch",
    "launching",
}
IDLE_PHASE_VALUES = {"", "idle", "normal", "none", "{}", "[]"}
NEXT_LAG_WARN_SEC = 180
NEXT_LAG_ERROR_SEC = 600
MODULE_HEALTH_SPECS = [
    {
        "key": "tianxing",
        "label": "天星",
        "enabled": "tianxing_enabled",
        "json_fields": ("tianxing_observation", "tianxing_timeline_state", "tianxing_auto_config"),
    },
    {
        "key": "fishing",
        "label": "钓鱼",
        "enabled": "fishing_enabled",
        "phase_fields": (("fishing_phase", "阶段"),),
        "pending_fields": (("fishing_reply_to_msg_id", "回复"), ("fishing_status_msg_id", "状态")),
        "due_fields": (("fishing_reply_due_at", "回复截止"), ("fishing_transfer_due_at", "赠送截止")),
        "next_fields": (("next_fishing_time", "下次"),),
        "last_result_fields": (("fishing_last_result", "结果"),),
        "last_error_fields": (("fishing_last_error", "错误"),),
    },
    {
        "key": "hehuan",
        "label": "合欢",
        "enabled": "hehuan_enabled",
        "json_fields": ("hehuan_observation",),
        "phase_fields": (("concubine_partner_kind", "道侣锚点"),),
        "pending_fields": (("concubine_reply_to_msg_id", "回复"),),
        "due_fields": (("concubine_reply_due_at", "回复截止"),),
    },
    {
        "key": "explore_rift",
        "label": "探寻裂缝",
        "enabled": "explore_rift_enabled",
        "pending_fields": (
            ("explore_rift_reply_to_msg_id", "裂缝回复"),
            ("explore_rift_pending_result_msg_id", "裂缝结果"),
            ("explore_rift_rebirth_request_msg_id", "夺舍请求"),
            ("explore_rift_rebirth_options_msg_id", "夺舍选项"),
            ("explore_rift_rebirth_select_msg_id", "夺舍选择"),
        ),
        "due_fields": (
            ("explore_rift_reply_due_at", "裂缝回复截止"),
            ("explore_rift_rebirth_due_at", "夺舍截止"),
            ("explore_rift_fatal_confirm_due_at", "死亡确认截止"),
        ),
        "next_fields": (("next_explore_rift_time", "下次"),),
        "phase_fields": (("explore_rift_rebirth_phase", "夺舍"),),
        "flag_fields": (("explore_rift_manual_required", "需人工"), ("explore_rift_rebirth_required", "需夺舍")),
        "last_result_fields": (("explore_rift_last_result", "结果"), ("explore_rift_rebirth_last_result", "夺舍结果")),
        "last_error_fields": (("explore_rift_last_error", "错误"), ("explore_rift_rebirth_last_error", "夺舍错误")),
    },
    {
        "key": "wild_training",
        "label": "野外历练",
        "enabled": "wild_training_enabled",
        "pending_fields": (("wild_training_reply_to_msg_id", "回复"),),
        "due_fields": (("wild_training_reply_due_at", "回复截止"),),
        "next_fields": (("next_wild_training_time", "下次"),),
        "last_result_fields": (("wild_training_last_result", "结果"),),
        "last_error_fields": (("wild_training_last_error", "错误"),),
    },
    {
        "key": "deep_retreat",
        "label": "深度闭关",
        "enabled": "deep_retreat_enabled",
        "phase_fields": (("deep_retreat_phase", "阶段"),),
        "pending_fields": (("last_deep_retreat_summary_msg_id", "结算"),),
        "next_fields": (("next_deep_retreat_time", "下次"),),
    },
    {
        "key": "yuanying",
        "label": "元婴",
        "enabled": "yuanying_enabled",
        "phase_fields": (("yuanying_phase", "阶段"),),
        "pending_fields": (("last_yuanying_summary_msg_id", "结算"),),
        "next_fields": (("next_yuanying_time", "下次"),),
    },
    {
        "key": "second_soul",
        "label": "第二元神",
        "enabled": "second_soul_enabled",
        "phase_fields": (("second_soul_phase", "阶段"),),
        "pending_fields": (
            ("second_soul_status_msg_id", "状态"),
            ("second_soul_train_msg_id", "历练"),
            ("second_soul_purge_msg_id", "镇魔"),
            ("second_soul_heart_demon_msg_id", "心魔"),
        ),
        "due_fields": (("second_soul_purge_due_at", "镇魔截止"),),
        "next_fields": (("next_second_soul_time", "下次"),),
        "last_error_fields": (("second_soul_last_error", "错误"),),
    },
    {
        "key": "small_world",
        "label": "小世界",
        "enabled": "small_world_enabled",
        "phase_fields": (("small_world_phase", "阶段"),),
        "pending_fields": (
            ("small_world_query_msg_id", "查询"),
            ("small_world_preach_reply_to_msg_id", "布道"),
            ("small_world_manifest_msg_id", "显灵"),
            ("small_world_harvest_msg_id", "收割"),
            ("small_world_barrier_msg_id", "护界"),
        ),
        "due_fields": (("small_world_preach_due_at", "布道截止"), ("small_world_barrier_due_at", "护界截止")),
        "next_fields": (("next_small_world_time", "下次"), ("small_world_god_cooldown_until", "神迹冷却")),
        "last_error_fields": (("small_world_last_error", "错误"),),
    },
    {
        "key": "world_boss",
        "label": "世界Boss",
        "enabled": "world_boss_enabled",
        "pending_fields": (("world_boss_pending_msg_id", "待回复"),),
        "phase_fields": (("world_boss_pending_action", "动作"),),
        "last_result_fields": (("world_boss_last_action", "上次动作"),),
        "last_error_fields": (("world_boss_last_error", "错误"),),
    },
    {
        "key": "concubine",
        "label": "侍妾",
        "enabled": "concubine_enabled",
        "phase_fields": (("concubine_phase", "阶段"), ("concubine_voyage_status", "八仙过海")),
        "pending_fields": (
            ("concubine_reply_to_msg_id", "回复"),
            ("concubine_heart_msg_id", "心劫"),
            ("concubine_voyage_msg_id", "出海"),
            ("concubine_voyage_return_msg_id", "归来"),
        ),
        "due_fields": (
            ("concubine_reply_due_at", "回复截止"),
            ("concubine_heart_due_at", "心劫截止"),
            ("concubine_voyage_due_at", "出海截止"),
        ),
        "next_fields": (("next_concubine_time", "下次"),),
        "last_result_fields": (("concubine_last_result", "结果"), ("concubine_voyage_last_result", "出海结果")),
        "last_error_fields": (
            ("concubine_last_error", "错误"),
            ("concubine_tianji_last_error", "代卜错误"),
            ("concubine_greet_last_error", "问安错误"),
            ("concubine_gift_last_error", "赠予错误"),
            ("concubine_heart_last_error", "心劫错误"),
            ("concubine_voyage_last_error", "出海错误"),
        ),
    },
    {
        "key": "wendao",
        "label": "问道",
        "enabled": "wendao_enabled",
        "pending_fields": (("wendao_reply_to_msg_id", "回复"), ("wendao_pending_result_msg_id", "结果")),
        "due_fields": (("wendao_reply_due_at", "回复截止"),),
        "next_fields": (("next_wendao_time", "下次"),),
        "last_result_fields": (("wendao_last_result", "结果"),),
        "last_error_fields": (("wendao_last_error", "错误"),),
    },
    {
        "key": "mulan",
        "label": "慕兰烽烟",
        "enabled": "mulan_enabled",
        "phase_fields": (("mulan_phase", "阶段"),),
        "pending_fields": (("mulan_reply_to_msg_id", "回复"),),
        "due_fields": (("mulan_reply_due_at", "回复截止"),),
        "next_fields": (("next_mulan_time", "下次"),),
        "last_result_fields": (("mulan_last_result", "结果"), ("mulan_last_command", "命令")),
        "last_error_fields": (("mulan_last_error", "错误"),),
    },
    {
        "key": "duel",
        "label": "斗法",
        "enabled": "duel_enabled",
        "pending_fields": (("duel_reply_to_msg_id", "回复"), ("duel_open_msg_id", "开局")),
        "due_fields": (("duel_reply_due_at", "回复截止"), ("duel_magic_due_at", "神通截止")),
        "next_fields": (("next_duel_time", "下次"),),
        "last_result_fields": (("duel_last_result", "结果"),),
        "last_error_fields": (("duel_last_error", "错误"),),
    },
    {
        "key": "tower",
        "label": "闯塔",
        "enabled": "tower_enabled",
        "pending_fields": (("last_tower_msg_id", "闯塔"),),
        "due_fields": (("tower_reply_due_at", "回复截止"),),
        "next_fields": (("next_tower_time", "下次"),),
    },
]


@dataclass
class ObserverConfig:
    project_root: Path
    services: tuple[str, ...]
    interval_sec: float
    journal_window_sec: int
    max_journal_matches: int
    max_event_lines: int
    state_dir: Path
    business_window_sec: int

    @property
    def latest_path(self) -> Path:
        return self.state_dir / "latest.json"

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def latest_md_path(self) -> Path:
        return self.state_dir / "latest.md"


def local_ts(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def local_day_key(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time()).strftime("%Y-%m-%d")


def parse_local_ts(raw: str) -> float:
    text = str(raw or "")[:19]
    try:
        return time.mktime(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timetuple())
    except Exception:
        return 0.0


def parse_systemd_start_timestamp(raw: str) -> float:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", str(raw or ""))
    if not match:
        return 0.0
    return parse_local_ts(f"{match.group(1)} {match.group(2)}")


def parse_optional_epoch(value: object) -> float:
    try:
        epoch = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return epoch if epoch > 0 else 0.0


def run_command(args: list[str], *, timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def read_proc_cmdline(pid: int) -> str:
    try:
        return (Path("/proc") / str(int(pid)) / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def read_foreign_xiuxian_processes(project_root: Path) -> list[dict[str, object]]:
    current_script = str(Path(project_root).resolve() / "xiuxian.py")
    legacy_scripts = {str(root / "xiuxian.py") for root in LEGACY_PROJECT_ROOTS}
    rows: list[dict[str, object]] = []
    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
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
        legacy = any(script in cmdline for script in legacy_scripts)
        rows.append({
            "pid": pid,
            "cmdline": cmdline[:500],
            "legacy": legacy,
        })
    return rows


def parse_systemctl_show(output: str) -> dict[str, dict[str, str]]:
    services: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line:
            if current.get("Id"):
                services[current["Id"]] = current
            current = {}
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key] = value
    if current.get("Id"):
        services[current["Id"]] = current
    return services


def read_service_states(services: Iterable[str]) -> dict[str, dict[str, str]]:
    args = [
        "systemctl",
        "show",
        *services,
        "--property=Id,ActiveState,SubState,MainPID,NRestarts,ExecMainStartTimestamp,ExecMainStatus,ExecMainCode",
        "--no-pager",
    ]
    code, stdout, stderr = run_command(args)
    parsed = parse_systemctl_show(stdout)
    if code != 0:
        parsed["_systemctl_error"] = {"stderr": stderr.strip(), "returncode": str(code)}
    return parsed


def journal_since_text(window_sec: int, *, service_start_epoch: float = 0.0) -> str:
    since_epoch = time.time() - max(10, int(window_sec or 0))
    if service_start_epoch > 0:
        since_epoch = max(since_epoch, float(service_start_epoch))
    return local_ts(since_epoch)


def read_journal_matches(service: str, window_sec: int, limit: int, *, service_start_epoch: float = 0.0) -> dict[str, object]:
    since = journal_since_text(window_sec, service_start_epoch=service_start_epoch)
    code, stdout, stderr = run_command(
        ["journalctl", "-u", service, "--since", since, "--no-pager"],
        timeout=12.0,
    )
    lines = [line for line in stdout.splitlines() if line.strip()]
    hard = [
        line
        for index, line in enumerate(lines)
        if not _is_benign_disconnected_traceback_block(lines, index) and is_hard_journal_line(line)
    ]
    warn = [
        line
        for index, line in enumerate(lines)
        if not _is_benign_disconnected_traceback_block(lines, index) and is_warn_journal_line(line)
    ]
    max_items = max(1, int(limit or 1))
    return {
        "service": service,
        "since": since,
        "returncode": code,
        "stderr": stderr.strip()[:500],
        "total_lines": len(lines),
        "hard_count": len(hard),
        "warn_count": len(warn),
        "hard": hard[-max_items:],
        "warn": warn[-max_items:],
    }


def journal_filter_start_epoch(service: str, *, service_start_epoch: float = 0.0, watchdog_reset_epoch: float = 0.0) -> float:
    start_epoch = parse_optional_epoch(service_start_epoch)
    reset_epoch = parse_optional_epoch(watchdog_reset_epoch)
    if reset_epoch > 0 and "watchdog" in str(service or "").lower():
        start_epoch = max(start_epoch, reset_epoch)
    return start_epoch


def _is_benign_disconnected_traceback_block(lines: list[str], index: int) -> bool:
    text = str(lines[index] if 0 <= index < len(lines) else "")
    if "Cannot send requests while disconnected" in text:
        return True
    if "Traceback (most recent call last)" not in text:
        return False
    tail = "\n".join(str(line or "") for line in lines[index:index + 40])
    return "Cannot send requests while disconnected" in tail


def is_hard_journal_line(line: str) -> bool:
    text = str(line or "")
    if BENIGN_HARD_CONTEXT_PATTERN.search(text):
        return False
    if TELETHON_WRONG_SESSION_PATTERN.search(text):
        return False
    return bool(HARD_PATTERN.search(text))


def is_warn_journal_line(line: str) -> bool:
    text = str(line or "")
    if is_hard_journal_line(text):
        return False
    if BENIGN_WARN_CONTEXT_PATTERN.search(text):
        return False
    if TELETHON_WRONG_SESSION_PATTERN.search(text):
        return True
    if "主线拉人未发送" in text and "send_failed" not in text:
        return False
    return bool(WARN_PATTERN.search(text))


def current_message_log(project_root: Path, now: float | None = None) -> Path:
    if os.environ.get("XIUXIAN_MESSAGES_DIR"):
        messages_dir = Path(os.environ["XIUXIAN_MESSAGES_DIR"])
    elif os.environ.get("XIUXIAN_DATA_DIR"):
        messages_dir = Path(os.environ["XIUXIAN_DATA_DIR"]) / "messages"
    else:
        messages_dir = project_root / "data" / "messages"
    return messages_dir / f"{datetime.fromtimestamp(now or time.time()).strftime('%Y-%m-%d')}.log"


def listener_heartbeat_path(project_root: Path) -> Path:
    if os.environ.get("XIUXIAN_STATE_DIR"):
        state_dir = Path(os.environ["XIUXIAN_STATE_DIR"])
    elif os.environ.get("XIUXIAN_DATA_DIR"):
        state_dir = Path(os.environ["XIUXIAN_DATA_DIR"]) / "state"
    else:
        state_dir = project_root / "data" / "state"
    return state_dir / "listener_heartbeat.json"


def read_listener_heartbeat(project_root: Path, now: float) -> dict[str, object]:
    path = listener_heartbeat_path(project_root)
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"available": False, "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {"available": False, "path": str(path), "error": "invalid heartbeat payload"}
    updated_at = parse_optional_epoch(payload.get("updated_at"))
    last_event_at = parse_optional_epoch(payload.get("last_event_at"))
    payload["available"] = True
    payload["path"] = str(path)
    payload["age_sec"] = int(max(0, float(now or 0) - updated_at)) if updated_at > 0 else None
    payload["last_event_age_sec"] = int(max(0, float(now or 0) - last_event_at)) if last_event_at > 0 else None
    return payload


def state_db_path(project_root: Path) -> Path:
    if os.environ.get("XIUXIAN_DB_FILE"):
        return Path(os.environ["XIUXIAN_DB_FILE"])
    if os.environ.get("XIUXIAN_STATE_DIR"):
        state_dir = Path(os.environ["XIUXIAN_STATE_DIR"])
    elif os.environ.get("XIUXIAN_DATA_DIR"):
        state_dir = Path(os.environ["XIUXIAN_DATA_DIR"]) / "state"
    else:
        state_dir = project_root / "data" / "state"
    return state_dir / "chaogu_state.db"


def safety_watchdog_fused_path(project_root: Path) -> Path:
    return state_db_path(project_root).parent / "safety_watchdog_fused.json"


def safety_watchdog_reset_path(project_root: Path) -> Path:
    return state_db_path(project_root).parent / "safety_watchdog_reset.json"


def read_safety_reset_epoch(project_root: Path) -> float:
    path = safety_watchdog_reset_path(project_root)
    if not path.exists():
        return 0.0
    payload: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except Exception:
        payload = {}
    reset_at_epoch = parse_optional_epoch(payload.get("reset_at_epoch"))
    if reset_at_epoch > 0:
        return reset_at_epoch
    reset_at = parse_local_ts(str(payload.get("reset_at") or ""))
    if reset_at > 0:
        return reset_at
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def read_safety_state(project_root: Path) -> dict[str, object]:
    path = safety_watchdog_fused_path(project_root)
    reset_epoch = read_safety_reset_epoch(project_root)
    reset_info = {
        "reset_path": str(safety_watchdog_reset_path(project_root)),
        "reset_at_epoch": reset_epoch,
        "reset_at_ts": local_ts(reset_epoch) if reset_epoch > 0 else "",
    }
    if not path.exists():
        return {"fused": False, "path": str(path), **reset_info}
    payload: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    except Exception as exc:
        payload = {"read_error": str(exc)}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "fused": True,
        "path": str(path),
        "mtime": mtime,
        "mtime_ts": local_ts(mtime) if mtime > 0 else "",
        "reason": str(payload.get("reason") or payload.get("message") or "")[:240],
        "action": str(payload.get("action") or "")[:80],
        "payload": {key: payload.get(key) for key in ("reason", "action", "command", "identity_id") if key in payload},
        **reset_info,
    }


def command_key(text: str) -> str:
    raw = str(text or "").strip()
    for prefix in (".引道", ".神识淬炼", ".搜寻节点"):
        if raw.startswith(prefix + " "):
            return prefix
    return raw


def is_guarded_business_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in GUARDED_BUSINESS_PREFIXES)


def is_expected_divination_query_chain(items: list[dict[str, object]]) -> bool:
    if not items:
        return False
    for item in items:
        text = command_key(str(item.get("text") or ""))
        if text != ".卜筮问天":
            return False
        if str(item.get("source_module") or "").strip() != "卜筮问天":
            return False
        if str(item.get("family") or "").strip() != "divination":
            return False
        if not str(item.get("op_id") or "").strip().startswith("divination_query:"):
            return False
        if not str(item.get("chain_id") or "").strip().startswith("divination:"):
            return False
    return True


def event_identity_id(item: dict[str, object]) -> int:
    for key in ("sender_id", "send_as_id", "identity_id"):
        try:
            value = int(item.get(key, 0) or 0)
        except Exception:
            value = 0
        if value:
            return value
    return 0


def read_recent_message_events(log_file: Path, max_lines: int = 10000) -> list[dict[str, object]]:
    if not log_file.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as handle:
            for line in deque(handle, maxlen=max_lines):
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                payload["_epoch"] = parse_local_ts(str(payload.get("ts") or ""))
                rows.append(payload)
    except OSError:
        return []
    return rows


def business_alert(message: str, *, severity: str = "warn", **extra) -> dict[str, object]:
    payload: dict[str, object] = {
        "severity": str(severity or "warn"),
        "message": str(message or "").strip(),
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def event_ref(item: dict[str, object]) -> dict[str, object]:
    return {
        key: item.get(key)
        for key in (
            "ts",
            "event_type",
            "message_id",
            "reply_to_msg_id",
            "sender_id",
            "send_as_id",
            "identity_id",
            "text",
            "source_module",
            "family",
            "op_id",
            "chain_id",
        )
        if item.get(key) not in (None, "")
    }


def analyze_message_events(events: list[dict[str, object]], now: float, window_sec: int, *, reset_after_epoch: float = 0.0) -> dict[str, object]:
    window_start = max(float(now) - float(window_sec), float(reset_after_epoch or 0))
    recent = [
        item for item in events
        if float(item.get("_epoch", 0) or 0) >= window_start
    ]
    sent = [
        item for item in recent
        if str(item.get("event_type") or "") == "sent"
        and float(item.get("_epoch", 0) or 0) > 0
    ]
    alerts: list[dict[str, object]] = []
    repeated_command_samples: list[dict[str, object]] = []
    sent_ids = {int(item.get("message_id", 0) or 0) for item in sent}
    sent_ids.discard(0)

    active_counts = Counter(command_key(str(item.get("text") or "")) for item in sent)
    active_counts = Counter({key: count for key, count in active_counts.items() if key in ACTIVE_STATUS_COMMANDS})
    active_by_identity: Counter[tuple[int, str]] = Counter()
    for item in sent:
        command = command_key(str(item.get("text") or ""))
        if command in ACTIVE_STATUS_COMMANDS:
            active_by_identity[(event_identity_id(item), command)] += 1
    for (identity_id, command), count in sorted(active_by_identity.items()):
        if count >= 2:
            identity_part = f"{identity_id}:" if identity_id else ""
            sample = [
                event_ref(item)
                for item in sent
                if event_identity_id(item) == identity_id and command_key(str(item.get("text") or "")) == command
            ][-4:]
            repeated_command_samples.append({
                "kind": "active_status",
                "identity_id": identity_id,
                "command": command,
                "count": count,
                "sample": sample,
            })
            alerts.append(
                business_alert(
                    f"active status query repeated: {identity_part}{command} x{count}/{int(window_sec / 60)}m",
                    identity_id=identity_id or None,
                    command=command,
                    count=count,
                )
            )

    grouped: dict[tuple[int, str], list[dict[str, object]]] = defaultdict(list)
    for item in sent:
        text = command_key(str(item.get("text") or ""))
        sender_id = int(item.get("sender_id", 0) or 0)
        if sender_id and text and is_guarded_business_command(text):
            grouped[(sender_id, text)].append(item)
    for (sender_id, text), items in sorted(grouped.items()):
        if text == ".卜筮问天" and is_expected_divination_query_chain(items):
            continue
        alert_min = GUARDED_COMMAND_REPEAT_ALERT_MIN_BY_COMMAND.get(text, GUARDED_COMMAND_REPEAT_ALERT_MIN)
        if len(items) >= alert_min:
            repeated_command_samples.append({
                "kind": "guarded_command",
                "identity_id": sender_id,
                "command": text,
                "count": len(items),
                "sample": [event_ref(item) for item in items[-4:]],
            })
            alerts.append(
                business_alert(
                    f"guarded command repeated: {sender_id}:{text} x{len(items)}/{int(window_sec / 60)}m",
                    sender_id=sender_id,
                    command=text,
                    count=len(items),
                )
            )

    cooldown_replies = []
    for item in recent:
        if str(item.get("event_type") or "") not in {"message", "edit"}:
            continue
        if int(item.get("reply_to_msg_id", 0) or 0) not in sent_ids:
            continue
        text = str(item.get("text") or "")
        if COOLDOWN_REPLY_PATTERN.search(text):
            cooldown_replies.append(item)
    if len(cooldown_replies) >= 3:
        alerts.append(
            business_alert(
                f"cooldown replies to script sends: {len(cooldown_replies)}/{int(window_sec / 60)}m",
                count=len(cooldown_replies),
            )
        )

    last_sent_at = max((float(item.get("_epoch", 0) or 0) for item in sent), default=0.0)
    command_counts = Counter(command_key(str(item.get("text") or "")) for item in sent if str(item.get("text") or "").strip())
    module_counts = Counter(str(item.get("source_module") or item.get("family") or "").strip() for item in sent)
    module_counts = Counter({key: value for key, value in module_counts.items() if key})
    return {
        "window_sec": int(window_sec),
        "window_start": window_start,
        "sent_count": len(sent),
        "last_sent_at": last_sent_at,
        "last_sent_ts": local_ts(last_sent_at) if last_sent_at > 0 else "",
        "active_status_counts": dict(active_counts),
        "active_status_identity_counts": {f"{identity_id}:{command}": count for (identity_id, command), count in active_by_identity.items()},
        "command_counts": dict(command_counts.most_common(12)),
        "module_counts": dict(module_counts.most_common(12)),
        "cooldown_reply_count": len(cooldown_replies),
        "last_sent_sample": [event_ref(item) for item in sent[-8:]],
        "repeated_command_samples": repeated_command_samples[:12],
        "cooldown_reply_sample": [event_ref(item) for item in cooldown_replies[-8:]],
        "alerts": alerts,
    }


def sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def sqlite_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not sqlite_table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def fetch_table_rows_by_identity(conn: sqlite3.Connection, table: str) -> dict[int, dict[str, object]]:
    if not sqlite_table_exists(conn, table):
        return {}
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.Error:
        return {}
    result: dict[int, dict[str, object]] = {}
    for row in rows:
        mapping = dict(row)
        try:
            identity_id = int(mapping.get("send_as_id") or 0)
        except Exception:
            identity_id = 0
        if identity_id:
            result[identity_id] = mapping
    return result


def short_value(value: object, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def parse_json_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "enabled", "启用", "开启"}


def positive_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def positive_epoch(value: object) -> float:
    try:
        epoch = float(value or 0)
    except Exception:
        return 0.0
    if epoch <= 0:
        return 0.0
    return epoch


def monitors_next_lag(field: object) -> bool:
    text = str(field or "").strip()
    return text.startswith("next_") and text.endswith("_time")


def module_error_needs_attention(text: object) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if BENIGN_MODULE_ERROR_PATTERN.search(raw):
        return False
    return bool(MODULE_ERROR_ATTENTION_PATTERN.search(raw))


def module_error_is_retryable_warning(field: str, text: object, payload: dict[str, object]) -> bool:
    raw = str(text or "").strip()
    if str(field or "") != "auto_last_error" or not raw:
        return False
    if "补发已达" in raw or "上限" in raw:
        return False
    if "发送失败或被安全策略拦截" in raw and positive_epoch((payload or {}).get("auto_next_time")) > 0:
        return True
    retry_count = positive_int((payload or {}).get("auto_retry_count"))
    pending_msg_id = positive_int((payload or {}).get("auto_pending_msg_id"))
    return retry_count > 0 or pending_msg_id > 0


def module_error_has_scheduled_retry(text: object, spec: dict[str, object], value_for, now: float) -> bool:
    raw = str(text or "").strip()
    if not raw or "失败" not in raw:
        return False
    has_future_next = any(
        positive_epoch(value_for(str(next_field))) > now
        for next_field, _next_label in spec.get("next_fields", ())
    )
    if not has_future_next:
        return False
    return "发送失败或被安全策略拦截" in raw or "发送" in raw


def normalize_json_state_for_health(field: str, payload: dict[str, object], now: float | None = None) -> dict[str, object]:
    if not payload:
        return {}
    normalized = dict(payload)
    field_name = str(field or "")
    if field_name == "hehuan_observation":
        auto_last_error = str(normalized.get("auto_last_error") or "").strip()
        auto_error_at = positive_epoch(normalized.get("auto_last_error_at"))
        last_observed_at = positive_epoch(normalized.get("last_observed_at"))
        if (
            auto_last_error
            and str(normalized.get("last_result") or "").strip().lower() == "success"
            and positive_int(normalized.get("auto_pending_msg_id")) <= 0
            and positive_int(normalized.get("auto_retry_count")) <= 0
            and (auto_error_at <= 0 or auto_error_at <= last_observed_at)
        ):
            normalized["auto_last_error"] = ""
            normalized["auto_last_error_at"] = 0
        return normalized

    if field_name != "tianxing_observation":
        return normalized

    fixed_star = str(normalized.get("fixed_star") or "").strip()
    fixed_star_day = str(normalized.get("fixed_star_day") or "").strip()
    if fixed_star and fixed_star_day and fixed_star_day != local_day_key(now):
        normalized["stale_fixed_star"] = fixed_star
        normalized["fixed_star"] = ""

    last_action = str(normalized.get("last_action") or "").strip()
    last_result = str(normalized.get("last_result") or "").strip()
    last_error = str(normalized.get("last_error") or "").strip()
    if last_result == "cooldown" and (
        (last_action == "推命" and last_error == "推命尚未应验")
        or (last_action == "改命" and last_error == "改命尚未耗尽")
    ):
        normalized["last_error"] = ""
        last_error = ""

    auto_last_error = str(normalized.get("auto_last_error") or "").strip()
    if (
        last_result == "cooldown"
        and not str(normalized.get("auto_pending_action") or "").strip()
        and not last_error
        and auto_last_error == "天星宗自动动作回复超时，暂缓重试；不继续推进下游。"
    ):
        normalized["auto_last_error"] = ""
        normalized["auto_last_error_at"] = 0
        auto_last_error = ""
    auto_error_at = positive_epoch(normalized.get("auto_last_error_at"))
    if (
        auto_last_error
        and "发送失败或被安全策略拦截" in auto_last_error
        and not str(normalized.get("auto_pending_action") or "").strip()
        and positive_int(normalized.get("auto_pending_msg_id")) <= 0
        and (
            (
                auto_error_at <= 0
                and last_result == "cooldown"
                and positive_epoch(normalized.get("current_prediction_until")) > 0
                and not str(normalized.get("last_error") or "").strip()
            )
            or (
                auto_error_at > 0
                and positive_epoch(normalized.get("last_observed_at")) >= auto_error_at
            )
        )
    ):
        normalized["auto_last_error"] = ""
        normalized["auto_last_error_at"] = 0
    return normalized


def add_module_detail(details: list[str], label: str, value: object, *, limit: int = 80) -> None:
    text = short_value(value, limit)
    if text:
        details.append(f"{label}:{text}")


def summarize_json_state(field: str, payload: dict[str, object], now: float, details: list[str], evidence: dict[str, object]) -> tuple[bool, bool]:
    if not payload:
        return False, False
    evidence.setdefault("json_fields", []).append(field)
    warn = False
    error = False
    for key, label in (
        ("fixed_star", "定命"),
        ("stale_fixed_star", "旧定命"),
        ("current_prediction", "推命"),
        ("current_change", "改命"),
        ("tianji_value", "天机"),
        ("last_action", "动作"),
        ("last_result", "结果"),
        ("last_summary", "摘要"),
        ("auto_last_action", "自动"),
        ("phase", "阶段"),
        ("route", "路线"),
        ("reason", "原因"),
    ):
        value = payload.get(key)
        if value not in (None, "", {}, []):
            add_module_detail(details, label, value)
    for key, label in (("last_error", "错误"), ("auto_last_error", "自动错误")):
        value = payload.get(key)
        if value not in (None, ""):
            add_module_detail(details, label, value)
            if module_error_needs_attention(value):
                if module_error_is_retryable_warning(key, value, payload):
                    warn = True
                else:
                    error = True
    for key, label in (
        ("current_prediction_until", "推命到期"),
        ("current_change_until", "改命到期"),
        ("auto_next_time", "自动下次"),
        ("deadline_at", "计划截止"),
        ("automation_paused_until", "暂停至"),
    ):
        epoch = positive_epoch(payload.get(key))
        if epoch > 0:
            add_module_detail(details, label, local_ts(epoch))
            if key in {"deadline_at"} and now > epoch + 300:
                warn = True
    paused_until = float(payload.get("automation_paused_until", 0) or 0)
    if paused_until < 0 or paused_until > now:
        reason = str(payload.get("automation_paused_reason") or "手动暂停").strip()
        add_module_detail(details, "接管", f"暂停 {reason}")
    return warn, error


def build_module_summary(conn: sqlite3.Connection, now: float, *, limit: int = 120) -> list[dict[str, object]]:
    identities = fetch_table_rows_by_identity(conn, "identities")
    runtime_rows = fetch_table_rows_by_identity(conn, "identity_runtime_state")
    timer_rows = fetch_table_rows_by_identity(conn, "identity_timers")
    module_rows = fetch_table_rows_by_identity(conn, "identity_module_state")
    identity_ids = sorted(set(identities) | set(runtime_rows) | set(timer_rows) | set(module_rows))
    summary: list[dict[str, object]] = []
    for identity_id in identity_ids:
        identity = identities.get(identity_id, {})
        runtime = runtime_rows.get(identity_id, {})
        timers = timer_rows.get(identity_id, {})
        modules = module_rows.get(identity_id, {})
        username = str(identity.get("username") or "").strip()
        label = str(identity.get("label") or "").strip()

        def value_for(key: str) -> object:
            if key in runtime:
                return runtime.get(key)
            if key in timers:
                return timers.get(key)
            if key in modules:
                return modules.get(key)
            return identity.get(key)

        for spec in MODULE_HEALTH_SPECS:
            module_key = str(spec["key"])
            enabled_key = str(spec.get("enabled") or "")
            enabled = boolish(value_for(enabled_key)) if enabled_key else False
            details: list[str] = []
            pending: list[dict[str, object]] = []
            due_items: list[dict[str, object]] = []
            next_items: list[dict[str, object]] = []
            flags: list[str] = []
            evidence: dict[str, object] = {
                "identity_id": identity_id,
                "db_tables": ["identity_runtime_state", "identity_timers", "identity_module_state"],
            }
            active = False
            warn = False
            error = False

            for field in spec.get("json_fields", ()):
                json_payload = parse_json_dict(value_for(str(field)))
                json_payload = normalize_json_state_for_health(str(field), json_payload, now=now)
                json_warn, json_error = summarize_json_state(str(field), json_payload, now, details, evidence)
                warn = warn or json_warn
                error = error or json_error

            for field, label_text in spec.get("phase_fields", ()):
                text = str(value_for(str(field)) or "").strip()
                if text.lower() not in IDLE_PHASE_VALUES:
                    add_module_detail(details, str(label_text), text)
                    active = True
                    warn = warn or any(token in text for token in ("manual", "人工", "失败", "异常"))

            for field, label_text in spec.get("pending_fields", ()):
                msg_id = positive_int(value_for(str(field)))
                if msg_id > 0:
                    pending.append({"field": str(field), "label": str(label_text), "msg_id": msg_id})
                    add_module_detail(details, str(label_text), f"msg={msg_id}")
                    active = True

            for field, label_text in spec.get("due_fields", ()):
                epoch = positive_epoch(value_for(str(field)))
                if epoch > 0:
                    overdue_sec = int(now - epoch) if now > epoch else 0
                    stale_without_pending = not (pending or active or warn)
                    if str(field) == "concubine_heart_due_at":
                        concubine_phase = str(value_for("concubine_phase") or "").strip()
                        heart_active = (
                            concubine_phase in {"heart_pending", "heart_choice_pending", "heart_choice_reply_pending"}
                            or positive_int(value_for("concubine_heart_msg_id")) > 0
                            or positive_int(value_for("concubine_heart_prompt_msg_id")) > 0
                        )
                        stale_without_pending = not heart_active
                    due_items.append({
                        "field": str(field),
                        "label": str(label_text),
                        "at": local_ts(epoch),
                        "overdue_sec": overdue_sec,
                        "stale_without_pending": stale_without_pending,
                    })
                    if not stale_without_pending:
                        add_module_detail(details, str(label_text), local_ts(epoch))
                    if overdue_sec > 120 and not stale_without_pending:
                        error = True
                    elif overdue_sec > 0 and not stale_without_pending:
                        active = True

            for field, label_text in spec.get("flag_fields", ()):
                if boolish(value_for(str(field))):
                    flags.append(str(label_text))
                    add_module_detail(details, str(label_text), "是")
                    warn = True

            for field, label_text in spec.get("next_fields", ()):
                field_name = str(field)
                epoch = positive_epoch(value_for(field_name))
                if epoch > 0:
                    overdue_sec = int(now - epoch) if now > epoch else 0
                    lag_without_anchor = (
                        enabled
                        and monitors_next_lag(field_name)
                        and overdue_sec > NEXT_LAG_WARN_SEC
                        and not pending
                        and not active
                        and not flags
                    )
                    next_items.append({
                        "field": field_name,
                        "label": str(label_text),
                        "at": local_ts(epoch),
                        "overdue_sec": overdue_sec,
                        "lag_without_anchor": lag_without_anchor,
                    })
                    if enabled:
                        add_module_detail(details, str(label_text), local_ts(epoch))
                    if lag_without_anchor:
                        add_module_detail(details, "调度滞后", f"{label_text}+{overdue_sec}s")
                        if overdue_sec > NEXT_LAG_ERROR_SEC:
                            error = True
                        else:
                            warn = True

            for field, label_text in spec.get("last_result_fields", ()):
                text = str(value_for(str(field)) or "").strip()
                if text:
                    add_module_detail(details, str(label_text), text)

            for field, label_text in spec.get("last_error_fields", ()):
                text = str(value_for(str(field)) or "").strip()
                if text:
                    add_module_detail(details, str(label_text), text)
                    if enabled and module_error_needs_attention(text):
                        scheduled_retry = module_error_has_scheduled_retry(text, spec, value_for, now)
                        if scheduled_retry:
                            warn = True
                        else:
                            error = True

            if not enabled and not pending and not flags and not error and not warn:
                continue
            status = "error" if error else "warn" if warn else "active" if active else "ok" if enabled else "inactive"
            summary.append({
                "identity_id": identity_id,
                "username": username,
                "label": label,
                "module": module_key,
                "module_label": str(spec.get("label") or module_key),
                "enabled": enabled,
                "status": status,
                "details": details[:10],
                "pending": pending[:8],
                "due": due_items[:8],
                "next": next_items[:6],
                "flags": flags[:8],
                "evidence": evidence,
            })
    priority = {"error": 0, "warn": 1, "active": 2, "ok": 3, "inactive": 4}
    summary.sort(key=lambda item: (priority.get(str(item.get("status")), 9), int(item.get("identity_id") or 0), str(item.get("module") or "")))
    return summary[:limit]


def summarize_module_pending(module_summary: list[dict[str, object]], *, limit: int = 12) -> tuple[int, list[dict[str, object]]]:
    total = 0
    samples: list[dict[str, object]] = []
    for item in module_summary:
        if not isinstance(item, dict):
            continue
        pending = item.get("pending")
        if not isinstance(pending, list) or not pending:
            continue
        total += len(pending)
        if len(samples) >= int(limit or 0):
            continue
        samples.append({
            "identity_id": item.get("identity_id"),
            "username": item.get("username"),
            "label": item.get("label"),
            "module": item.get("module"),
            "module_label": item.get("module_label"),
            "status": item.get("status"),
            "pending": pending[:4],
            "details": (item.get("details") or [])[:4],
        })
    return total, samples


def read_db_business_state(db_path: Path, now: float) -> dict[str, object]:
    if not db_path.exists():
        return {
            "db_path": str(db_path),
            "available": False,
            "alerts": [business_alert(f"state db missing: {db_path}", severity="error")],
        }
    alerts: list[dict[str, object]] = []
    pending_total = 0
    overdue_pending: list[dict[str, object]] = []
    stuck_phases: list[dict[str, object]] = []
    module_summary: list[dict[str, object]] = []
    module_pending_total = 0
    module_pending_samples: list[dict[str, object]] = []
    uri = f"file:{db_path}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            pending_rows = conn.execute(
                """
                SELECT send_as_id, cmd, sent_at, timeout, retry, max_retry, source_module
                FROM pending_tasks
                """
            ).fetchall()
            pending_total = len(pending_rows)
            for row in pending_rows:
                sent_at = float(row["sent_at"] or 0)
                timeout = float(row["timeout"] or 0)
                due_at = sent_at + timeout
                if sent_at > 0 and timeout > 0 and now > due_at + 120:
                    overdue_pending.append({
                        "identity_id": int(row["send_as_id"] or 0),
                        "cmd": row["cmd"],
                        "age_sec": int(now - sent_at),
                        "overdue_sec": int(now - due_at),
                        "retry": int(row["retry"] or 0),
                        "max_retry": int(row["max_retry"] or 0),
                        "source_module": row["source_module"],
                    })

            runtime_rows = conn.execute(
                """
                SELECT
                    r.send_as_id,
                    COALESCE(i.username, '') AS username,
                    r.concubine_phase,
                    t.next_concubine_time,
                    r.deep_retreat_phase,
                    r.deep_retreat_summary_sent_at,
                    t.next_deep_retreat_time,
                    r.yuanying_phase,
                    r.yuanying_summary_sent_at,
                    t.next_yuanying_time,
                    r.tower_reply_due_at,
                    r.last_tower_msg_id
                FROM identity_runtime_state r
                LEFT JOIN identity_timers t ON t.send_as_id = r.send_as_id
                LEFT JOIN identities i ON i.send_as_id = r.send_as_id
                """
            ).fetchall()
            for row in runtime_rows:
                identity_id = int(row["send_as_id"] or 0)
                username = str(row["username"] or "")
                concubine_phase = str(row["concubine_phase"] or "")
                next_concubine_time = float(row["next_concubine_time"] or 0)
                if concubine_phase.endswith(PENDING_PHASE_SUFFIX) and next_concubine_time > 0 and now > next_concubine_time + 300:
                    stuck_phases.append({
                        "identity_id": identity_id,
                        "username": username,
                        "module": "concubine",
                        "phase": concubine_phase,
                        "overdue_sec": int(now - next_concubine_time),
                    })

                for module, phase_key, next_key in (
                    ("deep_retreat", "deep_retreat_phase", "next_deep_retreat_time"),
                    ("yuanying", "yuanying_phase", "next_yuanying_time"),
                ):
                    phase = str(row[phase_key] or "")
                    next_time = float(row[next_key] or 0)
                    if phase in PHASEFUL_ATTENTION_PHASES and next_time > 0 and now > next_time + 300:
                        stuck_phases.append({
                            "identity_id": identity_id,
                            "username": username,
                            "module": module,
                            "phase": phase,
                            "overdue_sec": int(now - next_time),
                        })

                tower_due = float(row["tower_reply_due_at"] or 0)
                if int(row["last_tower_msg_id"] or 0) and tower_due > 0 and now > tower_due + 120:
                    stuck_phases.append({
                        "identity_id": identity_id,
                        "username": username,
                        "module": "tower",
                        "phase": "reply_wait",
                        "overdue_sec": int(now - tower_due),
                    })
            full_module_summary = build_module_summary(conn, now, limit=1000)
            module_pending_total, module_pending_samples = summarize_module_pending(full_module_summary)
            module_summary = full_module_summary[:120]
    except sqlite3.Error as exc:
        return {
            "db_path": str(db_path),
            "available": False,
            "alerts": [business_alert(f"state db read failed: {exc}", severity="error")],
        }

    if pending_total >= 5:
        alerts.append(business_alert(f"pending task backlog: {pending_total}", count=pending_total))
    if overdue_pending:
        alerts.append(
            business_alert(
                f"overdue pending tasks: {len(overdue_pending)}",
                count=len(overdue_pending),
                sample=overdue_pending[:5],
            )
        )
    if stuck_phases:
        alerts.append(
            business_alert(
                f"stuck runtime phases: {len(stuck_phases)}",
                count=len(stuck_phases),
                sample=stuck_phases[:8],
            )
        )
    module_errors = [item for item in module_summary if item.get("status") == "error"]
    module_warnings = [item for item in module_summary if item.get("status") == "warn"]
    if module_errors:
        alerts.append(
            business_alert(
                f"module runtime errors: {len(module_errors)}",
                severity="error",
                count=len(module_errors),
                sample=[
                    {
                        "identity_id": item.get("identity_id"),
                        "module": item.get("module"),
                        "details": item.get("details", [])[:3],
                    }
                    for item in module_errors[:8]
                ],
            )
        )
    elif module_warnings:
        alerts.append(
            business_alert(
                f"module runtime warnings: {len(module_warnings)}",
                count=len(module_warnings),
                sample=[
                    {
                        "identity_id": item.get("identity_id"),
                        "module": item.get("module"),
                        "details": item.get("details", [])[:3],
                    }
                    for item in module_warnings[:8]
                ],
            )
        )

    return {
        "db_path": str(db_path),
        "available": True,
        "pending_total": pending_total,
        "module_pending_total": module_pending_total,
        "module_pending_samples": module_pending_samples,
        "overdue_pending": overdue_pending[:20],
        "stuck_phases": stuck_phases[:20],
        "module_summary": module_summary,
        "alerts": alerts,
    }


def collect_business_snapshot(cfg: ObserverConfig, now: float) -> dict[str, object]:
    events = read_recent_message_events(current_message_log(cfg.project_root, now=now))
    reset_after_epoch = read_safety_reset_epoch(cfg.project_root)
    message_state = analyze_message_events(events, now, cfg.business_window_sec, reset_after_epoch=reset_after_epoch)
    db_state = read_db_business_state(state_db_path(cfg.project_root), now)
    alerts = list(message_state.get("alerts") or []) + list(db_state.get("alerts") or [])
    return {
        "message_log": str(current_message_log(cfg.project_root, now=now)),
        "reset_after_epoch": reset_after_epoch,
        "reset_after_ts": local_ts(reset_after_epoch) if reset_after_epoch > 0 else "",
        "message_state": message_state,
        "db_state": db_state,
        "alerts": alerts,
    }


def merge_status(base_status: str, business_alerts: list[dict[str, object]]) -> tuple[str, list[str]]:
    error_count = sum(1 for item in business_alerts if item.get("severity") == "error")
    warn_count = len(business_alerts) - error_count
    reasons: list[str] = []
    if error_count:
        reasons.append(f"business errors: {error_count}")
    if warn_count:
        reasons.append(f"business warnings: {warn_count}")
    if error_count:
        return "error", reasons
    if warn_count and base_status == "ok":
        return "warn", reasons
    return base_status, reasons


def health_level_from_score(score: int, status: str) -> str:
    if status == "error" or score < 50:
        return "critical" if score < 35 else "error"
    if status == "warn" or score < 85:
        return "warn"
    return "ok"


def previous_health_times(latest_path: Path, now_ts: str, is_ok: bool) -> tuple[str, str]:
    last_ok_at = now_ts if is_ok else ""
    last_bad_at = "" if is_ok else now_ts
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        health = payload.get("health") if isinstance(payload, dict) else {}
        if isinstance(health, dict):
            last_ok_at = str(health.get("last_ok_at") or last_ok_at)
            last_bad_at = str(health.get("last_bad_at") or last_bad_at)
    except Exception:
        pass
    if is_ok:
        last_ok_at = now_ts
    else:
        last_bad_at = now_ts
    return last_ok_at, last_bad_at


def build_health_payload(snapshot: dict[str, object], cfg: ObserverConfig) -> dict[str, object]:
    score = 100
    risk_reasons: list[dict[str, object]] = []

    def add_risk(code: str, message: str, severity: str, deduct: int, **extra) -> None:
        nonlocal score
        score -= int(deduct)
        payload: dict[str, object] = {
            "code": code,
            "message": message,
            "severity": severity,
            "deduct": int(deduct),
        }
        payload.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
        risk_reasons.append(payload)

    services = snapshot.get("services") if isinstance(snapshot.get("services"), dict) else {}
    for service, info in services.items():
        if str(service).startswith("_") or not isinstance(info, dict):
            continue
        if info.get("ActiveState") != "active" or info.get("SubState") != "running":
            add_risk(
                "service_not_running",
                f"{service} not running: {info.get('ActiveState')}/{info.get('SubState')}",
                "critical",
                35,
                service=service,
            )

    listener = snapshot.get("listener") if isinstance(snapshot.get("listener"), dict) else {}
    listener_expected = "xiuxian-listener.service" in services or bool(listener.get("available"))
    if listener_expected:
        listener_service = services.get("xiuxian-listener.service") if isinstance(services.get("xiuxian-listener.service"), dict) else {}
        listener_running = listener_service.get("ActiveState") == "active" and listener_service.get("SubState") == "running"
        if not listener.get("available"):
            severity = "error" if listener_running else "warn"
            add_risk("listener_heartbeat_missing", "listener heartbeat missing", severity, 18 if listener_running else 8, path=listener.get("path"))
        else:
            age_sec = listener.get("age_sec")
            try:
                age_value = int(age_sec)
            except Exception:
                age_value = 999999
            if age_value > 180:
                add_risk("listener_heartbeat_stale", f"listener heartbeat stale: {age_value}s", "error", 22, path=listener.get("path"))
            elif age_value > 90:
                add_risk("listener_heartbeat_lag", f"listener heartbeat lag: {age_value}s", "warn", 8, path=listener.get("path"))
            if listener_running and str(listener.get("status") or "") not in {"running", ""}:
                add_risk("listener_status_not_running", f"listener heartbeat status: {listener.get('status')}", "warn", 8, path=listener.get("path"))

    safety = snapshot.get("safety") if isinstance(snapshot.get("safety"), dict) else {}
    if safety.get("fused"):
        add_risk("safety_watchdog_fused", "safety watchdog fused marker exists", "critical", 40, path=safety.get("path"), reason=safety.get("reason"))

    foreign_processes = snapshot.get("foreign_xiuxian_processes")
    if isinstance(foreign_processes, list) and foreign_processes:
        add_risk(
            "foreign_xiuxian_process",
            f"foreign xiuxian processes running: {len(foreign_processes)}",
            "critical",
            45,
            sample=foreign_processes[:6],
        )

    journals = snapshot.get("journals") if isinstance(snapshot.get("journals"), list) else []
    hard_total = sum(int(item.get("hard_count") or 0) for item in journals if isinstance(item, dict))
    warn_total = sum(int(item.get("warn_count") or 0) for item in journals if isinstance(item, dict))
    if hard_total:
        add_risk("journal_hard", f"journal hard matches: {hard_total}", "error", min(35, 18 + hard_total * 3))
    if warn_total:
        add_risk("journal_warn", f"journal warn matches: {warn_total}", "warn", min(18, 4 + warn_total))

    business = snapshot.get("business") if isinstance(snapshot.get("business"), dict) else {}
    for alert in business.get("alerts") or []:
        if not isinstance(alert, dict):
            continue
        severity = str(alert.get("severity") or "warn")
        deduct = 16 if severity == "error" else 7
        add_risk(
            "business_alert",
            str(alert.get("message") or "business alert"),
            "error" if severity == "error" else "warn",
            deduct,
            sample=alert.get("sample"),
        )

    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    sent_count = int(message_state.get("sent_count") or 0)
    window_min = max(1, int(int(message_state.get("window_sec") or 0) / 60))
    if sent_count >= 90:
        add_risk("send_density_high", f"sent density high: {sent_count}/{window_min}m", "warn", 10)
    elif sent_count >= 60:
        add_risk("send_density_elevated", f"sent density elevated: {sent_count}/{window_min}m", "warn", 6)
    for key, count in (message_state.get("module_counts") or {}).items():
        try:
            value = int(count or 0)
        except Exception:
            value = 0
        if value >= 20:
            add_risk("module_send_density", f"module send density: {key} x{value}/{window_min}m", "warn", 8, module=key)
            break

    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    module_summary = db_state.get("module_summary") if isinstance(db_state.get("module_summary"), list) else []
    module_error_count = sum(1 for item in module_summary if isinstance(item, dict) and item.get("status") == "error")
    module_warn_count = sum(1 for item in module_summary if isinstance(item, dict) and item.get("status") == "warn")
    if module_error_count:
        add_risk("module_errors", f"module runtime errors: {module_error_count}", "error", min(24, 8 + module_error_count * 3))
    if module_warn_count:
        add_risk("module_warnings", f"module runtime warnings: {module_warn_count}", "warn", min(15, 4 + module_warn_count))

    score = max(0, min(100, score))
    status = str(snapshot.get("status") or "ok")
    level = health_level_from_score(score, status)
    now_ts = str(snapshot.get("ts") or local_ts())
    last_ok_at, last_bad_at = previous_health_times(cfg.latest_path, now_ts, level == "ok")
    return {
        "score": score,
        "level": level,
        "risk_reasons": risk_reasons[:24],
        "last_ok_at": last_ok_at,
        "last_bad_at": last_bad_at,
        "policy": "read-only sidecar; no commands, no restarts, no Tianjige API",
    }


def build_evidence_refs(snapshot: dict[str, object]) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    business = snapshot.get("business") if isinstance(snapshot.get("business"), dict) else {}
    message_log = str(business.get("message_log") or "")
    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    if message_log:
        refs.append({
            "kind": "message_log",
            "path": message_log,
            "window_sec": message_state.get("window_sec"),
            "sent_count": message_state.get("sent_count"),
            "last_sent_ts": message_state.get("last_sent_ts"),
        })
    for item in message_state.get("repeated_command_samples") or []:
        if isinstance(item, dict):
            refs.append({
                "kind": "repeat_sample",
                "identity_id": item.get("identity_id"),
                "command": item.get("command"),
                "count": item.get("count"),
                "sample": item.get("sample"),
            })
    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    if db_state.get("available"):
        refs.append({
            "kind": "state_db",
            "path": db_state.get("db_path"),
            "pending_total": db_state.get("pending_total"),
            "module_pending_total": db_state.get("module_pending_total"),
            "module_pending_samples": (db_state.get("module_pending_samples") or [])[:5],
            "overdue_pending": (db_state.get("overdue_pending") or [])[:5],
            "stuck_phases": (db_state.get("stuck_phases") or [])[:5],
        })
    listener = snapshot.get("listener") if isinstance(snapshot.get("listener"), dict) else {}
    if listener:
        refs.append({
            "kind": "listener_heartbeat",
            "path": listener.get("path"),
            "available": listener.get("available"),
            "age_sec": listener.get("age_sec"),
            "registered_accounts": listener.get("registered_accounts"),
            "last_event_at": listener.get("last_event_at_text"),
        })
    safety = snapshot.get("safety") if isinstance(snapshot.get("safety"), dict) else {}
    if safety.get("fused"):
        refs.append({"kind": "safety_watchdog_fused", "path": safety.get("path"), "reason": safety.get("reason")})
    foreign_processes = snapshot.get("foreign_xiuxian_processes")
    if isinstance(foreign_processes, list) and foreign_processes:
        refs.append({"kind": "foreign_xiuxian_processes", "sample": foreign_processes[:8]})
    journals = snapshot.get("journals") if isinstance(snapshot.get("journals"), list) else []
    for item in journals:
        if not isinstance(item, dict):
            continue
        if int(item.get("hard_count") or 0) or int(item.get("warn_count") or 0):
            refs.append({
                "kind": "journal",
                "service": item.get("service"),
                "since": item.get("since"),
                "hard_count": item.get("hard_count"),
                "warn_count": item.get("warn_count"),
                "hard": (item.get("hard") or [])[-3:],
                "warn": (item.get("warn") or [])[-3:],
            })
    return refs[:30]


def format_audit_pack_markdown(snapshot: dict[str, object]) -> str:
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    business = snapshot.get("business") if isinstance(snapshot.get("business"), dict) else {}
    db_state = business.get("db_state") if isinstance(business.get("db_state"), dict) else {}
    message_state = business.get("message_state") if isinstance(business.get("message_state"), dict) else {}
    module_summary = db_state.get("module_summary") if isinstance(db_state.get("module_summary"), list) else []
    risk_reasons = health.get("risk_reasons") if isinstance(health.get("risk_reasons"), list) else []
    evidence_refs = snapshot.get("evidence_refs") if isinstance(snapshot.get("evidence_refs"), list) else []

    lines = [
        "# Xiuxian Health Audit Pack",
        "",
        f"- ts: {snapshot.get('ts') or '-'}",
        f"- status: {snapshot.get('status') or '-'}",
        f"- score: {health.get('score', '-')}",
        f"- level: {health.get('level', '-')}",
        f"- policy: {snapshot.get('policy') or health.get('policy') or 'read-only'}",
        "",
        "## Summary",
        f"- services: {len(snapshot.get('services') or {})}",
        f"- journal hard/warn: {sum(int(item.get('hard_count') or 0) for item in snapshot.get('journals') or [])}/{sum(int(item.get('warn_count') or 0) for item in snapshot.get('journals') or [])}",
        f"- sent: {message_state.get('sent_count', 0)} in {int((message_state.get('window_sec') or 0) / 60)}m",
        f"- pending: tasks={db_state.get('pending_total', 0)} module={db_state.get('module_pending_total', 0)}",
        "",
        "## Risk Reasons",
    ]
    if risk_reasons:
        for item in risk_reasons[:12]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- [{item.get('severity')}] {item.get('message')} ({item.get('code')}, -{item.get('deduct')})")
    else:
        lines.append("- none")

    lines.extend(["", "## Module Summary"])
    interesting_modules = [
        item for item in module_summary
        if isinstance(item, dict) and item.get("status") in {"error", "warn"}
    ][:16]
    if interesting_modules:
        for item in interesting_modules:
            who = item.get("username") or item.get("label") or item.get("identity_id")
            details = "；".join(str(part) for part in (item.get("details") or [])[:4])
            lines.append(f"- {who} {item.get('module_label') or item.get('module')}: {item.get('status')}｜{details or '-'}")
    else:
        lines.append("- no abnormal modules")
    pending_modules = db_state.get("module_pending_samples") if isinstance(db_state.get("module_pending_samples"), list) else []
    for item in pending_modules[:8]:
        if not isinstance(item, dict):
            continue
        who = item.get("username") or item.get("label") or item.get("identity_id")
        pending_text = "、".join(
            f"{entry.get('label') or entry.get('field')} msg={entry.get('msg_id')}"
            for entry in (item.get("pending") or [])[:3]
            if isinstance(entry, dict)
        )
        lines.append(f"- pending {who} {item.get('module_label') or item.get('module')}: {pending_text or '-'}")

    lines.extend(["", "## Evidence Refs"])
    if evidence_refs:
        for item in evidence_refs[:12]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or "evidence"
            if kind == "message_log":
                lines.append(f"- message_log: {item.get('path')} sent={item.get('sent_count')} last={item.get('last_sent_ts')}")
            elif kind == "state_db":
                lines.append(
                    f"- state_db: {item.get('path')} pending={item.get('pending_total')} "
                    f"module_pending={item.get('module_pending_total')}"
                )
            elif kind == "repeat_sample":
                lines.append(f"- repeat: {item.get('identity_id')} {item.get('command')} x{item.get('count')}")
            elif kind == "journal":
                lines.append(f"- journal: {item.get('service')} since={item.get('since')} hard={item.get('hard_count')} warn={item.get('warn_count')}")
            else:
                lines.append(f"- {kind}: {short_value(item, 180)}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def classify_snapshot(service_states: dict[str, dict[str, str]], journals: list[dict[str, object]]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    for service, info in service_states.items():
        if service.startswith("_"):
            reasons.append(f"{service}: {info}")
            continue
        if info.get("ActiveState") != "active" or info.get("SubState") != "running":
            reasons.append(f"{service} not running: {info.get('ActiveState')}/{info.get('SubState')}")

    hard_total = sum(int(item.get("hard_count") or 0) for item in journals)
    warn_total = sum(int(item.get("warn_count") or 0) for item in journals)
    if hard_total:
        reasons.append(f"journal hard matches: {hard_total}")
    if warn_total:
        reasons.append(f"journal warn matches: {warn_total}")

    if any("not running" in item for item in reasons) or hard_total:
        return "error", reasons
    if reasons:
        return "warn", reasons
    return "ok", []


def collect_snapshot(cfg: ObserverConfig) -> dict[str, object]:
    now = time.time()
    service_states = read_service_states(cfg.services)
    safety = read_safety_state(cfg.project_root)
    listener = read_listener_heartbeat(cfg.project_root, now)
    watchdog_reset_epoch = parse_optional_epoch(safety.get("reset_at_epoch"))
    foreign_processes = read_foreign_xiuxian_processes(cfg.project_root)
    journals = [
        read_journal_matches(
            service,
            cfg.journal_window_sec,
            cfg.max_journal_matches,
            service_start_epoch=journal_filter_start_epoch(
                service,
                service_start_epoch=parse_systemd_start_timestamp(
                    service_states.get(service, {}).get("ExecMainStartTimestamp", "")
                ),
                watchdog_reset_epoch=watchdog_reset_epoch,
            ),
        )
        for service in cfg.services
    ]
    status, reasons = classify_snapshot(service_states, journals)
    business = collect_business_snapshot(cfg, now)
    status, business_reasons = merge_status(status, list(business.get("alerts") or []))
    reasons.extend(business_reasons)
    if safety.get("fused") and status == "ok":
        status = "error"
    if safety.get("fused"):
        reasons.append("safety watchdog fused marker exists")
    if foreign_processes:
        status = "error"
        reasons.append(f"foreign xiuxian processes running: {len(foreign_processes)}")
    snapshot = {
        "ts": local_ts(),
        "epoch": now,
        "status": status,
        "reasons": reasons,
        "services": service_states,
        "listener": listener,
        "safety": safety,
        "foreign_xiuxian_processes": foreign_processes,
        "journals": journals,
        "business": business,
        "policy": "read-only: no game commands, no Tianjige API calls",
    }
    snapshot["health"] = build_health_payload(snapshot, cfg)
    snapshot["evidence_refs"] = build_evidence_refs(snapshot)
    return snapshot


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def append_event(path: Path, payload: dict[str, object], max_lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line)
    max_lines = max(100, int(max_lines or 0))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError:
        pass


def observe_once(cfg: ObserverConfig) -> dict[str, object]:
    snapshot = collect_snapshot(cfg)
    write_json_atomic(cfg.latest_path, snapshot)
    write_text_atomic(cfg.latest_md_path, format_audit_pack_markdown(snapshot))
    append_event(cfg.events_path, snapshot, cfg.max_event_lines)
    return snapshot


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Xiuxian runtime health observer")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--service", action="append", dest="services", default=[])
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--journal-window-sec", type=int, default=10 * 60)
    parser.add_argument("--max-journal-matches", type=int, default=12)
    parser.add_argument("--max-event-lines", type=int, default=5000)
    parser.add_argument("--business-window-sec", type=int, default=30 * 60)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> ObserverConfig:
    project_root = Path(args.project_root).resolve()
    services = tuple(args.services or DEFAULT_SERVICES)
    return ObserverConfig(
        project_root=project_root,
        services=services,
        interval_sec=max(15.0, float(args.interval or 60.0)),
        journal_window_sec=max(60, int(args.journal_window_sec or 600)),
        max_journal_matches=max(1, int(args.max_journal_matches or 12)),
        max_event_lines=max(100, int(args.max_event_lines or 5000)),
        state_dir=project_root / "data" / "state" / "health_observer",
        business_window_sec=max(300, int(args.business_window_sec or 1800)),
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cfg = build_config(args)
    if args.once:
        snapshot = observe_once(cfg)
        print(f"{snapshot['ts']} health_observer {snapshot['status']}: {', '.join(snapshot.get('reasons') or []) or 'ok'}")
        return 0 if snapshot["status"] == "ok" else 1

    print(f"health observer started: root={cfg.project_root} interval={cfg.interval_sec}s")
    while True:
        snapshot = observe_once(cfg)
        print(f"{snapshot['ts']} {snapshot['status']}: {', '.join(snapshot.get('reasons') or []) or 'ok'}", flush=True)
        time.sleep(cfg.interval_sec)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
