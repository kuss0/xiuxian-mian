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
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICES = ("xiuxian.service", "xiuxian-safety-watchdog.service")
HARD_PATTERN = re.compile(r"Traceback|ERROR|Exception|FATAL|FloodWait|FUSED|熔断|风暴", re.I)
WARN_PATTERN = re.compile(r"超时|补发|未发送|失窃|暂停|发送失败|回复失败|未识别|无法识别|过期|锁", re.I)
BENIGN_HARD_CONTEXT_PATTERN = re.compile(r"already fused:", re.I)
BENIGN_WARN_CONTEXT_PATTERN = re.compile(r"无补发|不补发|无需补发|题库内超时未作答|题库匹配|自动副本：收到 @，但未找到")


@dataclass
class ObserverConfig:
    project_root: Path
    services: tuple[str, ...]
    interval_sec: float
    journal_window_sec: int
    max_journal_matches: int
    max_event_lines: int
    state_dir: Path

    @property
    def latest_path(self) -> Path:
        return self.state_dir / "latest.json"

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.jsonl"


def local_ts(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time()).strftime("%Y-%m-%d %H:%M:%S")


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


def journal_since_text(window_sec: int) -> str:
    since_epoch = time.time() - max(10, int(window_sec or 0))
    return local_ts(since_epoch)


def read_journal_matches(service: str, window_sec: int, limit: int) -> dict[str, object]:
    since = journal_since_text(window_sec)
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
    return bool(WARN_PATTERN.search(text))


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
    service_states = read_service_states(cfg.services)
    journals = [
        read_journal_matches(service, cfg.journal_window_sec, cfg.max_journal_matches)
        for service in cfg.services
    ]
    status, reasons = classify_snapshot(service_states, journals)
    return {
        "ts": local_ts(),
        "epoch": time.time(),
        "status": status,
        "reasons": reasons,
        "services": service_states,
        "journals": journals,
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
