#!/usr/bin/env python3
"""Read-only long-running monitor for stability goals."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cmd(args: list[str], *, timeout: int = 45) -> dict[str, object]:
    try:
        proc = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "cmd": args,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip()[-4000:],
            "stderr": proc.stderr.strip()[-2000:],
        }
    except Exception as exc:  # pragma: no cover - defensive observer path
        return {"cmd": args, "returncode": -1, "error": repr(exc)}


def sqlite_rows(query: str) -> list[dict[str, object]]:
    db_path = PROJECT_ROOT / "data" / "state" / "chaogu_state.db"
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query).fetchall()]
    except sqlite3.Error as exc:
        return [{"error": str(exc)}]


def snapshot() -> dict[str, object]:
    fused_path = PROJECT_ROOT / "data" / "state" / "safety_watchdog_fused.json"
    fused_payload: object = None
    if fused_path.exists():
        try:
            fused_payload = json.loads(fused_path.read_text(encoding="utf-8"))
        except Exception:
            fused_payload = fused_path.read_text(encoding="utf-8", errors="replace")[:2000]
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "global": sqlite_rows("select key, value from meta where key='global_enabled'"),
        "fused": fused_payload,
        "watchdog": run_cmd(["tools/safety_watchdog.py", "--once", "--dry-run"]),
        "health": run_cmd([
            ".venv/bin/python",
            "tools/health_observer.py",
            "--once",
            "--journal-window-sec",
            "300",
            "--business-window-sec",
            "1800",
        ]),
        "wa_tianxing": sqlite_rows(
            "select send_as_id, tianxing_observation, tianxing_timeline_state "
            "from identity_runtime_state where send_as_id=8659059191"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only goal monitor")
    parser.add_argument("--duration-sec", type=int, default=24 * 3600)
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + max(1, args.duration_sec)
    interval = max(5, args.interval_sec)
    with args.output.open("a", encoding="utf-8") as handle:
        while time.time() < deadline:
            handle.write(json.dumps(snapshot(), ensure_ascii=False) + "\n")
            handle.flush()
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
