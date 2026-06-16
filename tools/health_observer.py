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
HARD_PATTERN = re.compile(r"Traceback|ERROR|Exception|FATAL|FloodWait|FUSED|熔断|风暴", re.I)
WARN_PATTERN = re.compile(r"超时|补发|未发送|失窃|暂停|发送失败|回复失败|未识别|无法识别|过期|锁", re.I)
BENIGN_HARD_CONTEXT_PATTERN = re.compile(r"already fused:|探寻裂缝结果：遭遇风暴", re.I)
BENIGN_WARN_CONTEXT_PATTERN = re.compile(
    r"无补发|不补发|无需补发|题库内超时未作答|题库匹配|自动副本：收到 @，但未找到|worker 优雅退出超时，强制结束|归位结算吃掉原指令，已补发一次|launching 超时，已回退"
)
COOLDOWN_REPLY_PATTERN = re.compile(
    r"请在\s*\S+\s*后再试|无法立即|尚在\S*冷却中|尚未重启|灵气尚未平复|梦图感应尚未重启|天机链路尚未重铸"
)
ACTIVE_STATUS_COMMANDS = {".查看闭关", ".元婴状态"}
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


def local_ts(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time()).strftime("%Y-%m-%d %H:%M:%S")


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
    hard = [line for line in lines if is_hard_journal_line(line)]
    warn = [line for line in lines if is_warn_journal_line(line)]
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


def is_hard_journal_line(line: str) -> bool:
    text = str(line or "")
    if BENIGN_HARD_CONTEXT_PATTERN.search(text):
        return False
    return bool(HARD_PATTERN.search(text))


def is_warn_journal_line(line: str) -> bool:
    text = str(line or "")
    if is_hard_journal_line(text):
        return False
    if BENIGN_WARN_CONTEXT_PATTERN.search(text):
        return False
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


def command_key(text: str) -> str:
    raw = str(text or "").strip()
    for prefix in (".引道", ".神识淬炼", ".搜寻节点"):
        if raw.startswith(prefix + " "):
            return prefix
    return raw


def is_guarded_business_command(text: str) -> bool:
    raw = str(text or "").strip()
    return any(raw == prefix or raw.startswith(prefix + " ") for prefix in GUARDED_BUSINESS_PREFIXES)


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


def analyze_message_events(events: list[dict[str, object]], now: float, window_sec: int) -> dict[str, object]:
    window_start = float(now) - float(window_sec)
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
        if len(items) >= 4:
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
    return {
        "window_sec": int(window_sec),
        "sent_count": len(sent),
        "last_sent_at": last_sent_at,
        "last_sent_ts": local_ts(last_sent_at) if last_sent_at > 0 else "",
        "active_status_counts": dict(active_counts),
        "active_status_identity_counts": {f"{identity_id}:{command}": count for (identity_id, command), count in active_by_identity.items()},
        "cooldown_reply_count": len(cooldown_replies),
        "alerts": alerts,
    }


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

    return {
        "db_path": str(db_path),
        "available": True,
        "pending_total": pending_total,
        "overdue_pending": overdue_pending[:20],
        "stuck_phases": stuck_phases[:20],
        "alerts": alerts,
    }


def collect_business_snapshot(cfg: ObserverConfig, now: float) -> dict[str, object]:
    events = read_recent_message_events(current_message_log(cfg.project_root, now=now))
    message_state = analyze_message_events(events, now, cfg.business_window_sec)
    db_state = read_db_business_state(state_db_path(cfg.project_root), now)
    alerts = list(message_state.get("alerts") or []) + list(db_state.get("alerts") or [])
    return {
        "message_log": str(current_message_log(cfg.project_root, now=now)),
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
    journals = [
        read_journal_matches(
            service,
            cfg.journal_window_sec,
            cfg.max_journal_matches,
            service_start_epoch=parse_systemd_start_timestamp(
                service_states.get(service, {}).get("ExecMainStartTimestamp", "")
            ),
        )
        for service in cfg.services
    ]
    status, reasons = classify_snapshot(service_states, journals)
    business = collect_business_snapshot(cfg, now)
    status, business_reasons = merge_status(status, list(business.get("alerts") or []))
    reasons.extend(business_reasons)
    return {
        "ts": local_ts(),
        "epoch": now,
        "status": status,
        "reasons": reasons,
        "services": service_states,
        "journals": journals,
        "business": business,
        "policy": "read-only: no game commands, no Tianjige API calls",
    }


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
