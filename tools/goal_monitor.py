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
from typing import Any


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


def parse_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {"_raw": value[:1000]}
    return parsed if isinstance(parsed, dict) else {}


def short_text(value: object, limit: int = 240) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def local_time(value: object) -> str:
    try:
        ts = float(value or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def summarize_step(step: object) -> dict[str, object]:
    if not isinstance(step, dict):
        return {}
    return {
        "id": short_text(step.get("id"), 120),
        "action": step.get("action") or "",
        "arg": step.get("arg") or "",
        "route": step.get("route") or "",
        "command": step.get("command") or "",
        "status": step.get("status") or "",
        "sent_at": local_time(step.get("sent_at")),
        "ack_due_at": local_time(step.get("ack_due_at")),
        "reason": short_text(step.get("reason")),
    }


def summarize_farm(farm: object) -> dict[str, object]:
    if not isinstance(farm, dict):
        return {}
    return {
        "phase": farm.get("phase") or "",
        "next_time": local_time(farm.get("next_time")),
        "cooldown_until": local_time(farm.get("cooldown_until")),
        "target_tianji": farm.get("target_tianji") or 0,
        "estimated_tianji": farm.get("estimated_tianji") or 0,
        "daily_limit": farm.get("daily_limit") or 0,
        "daily_count": farm.get("daily_count") or 0,
        "success_count": farm.get("success_count") or 0,
        "hit_count": farm.get("hit_count") or 0,
        "miss_count": farm.get("miss_count") or 0,
        "last_action": farm.get("last_action") or "",
        "last_command": farm.get("last_command") or "",
        "last_result": short_text(farm.get("last_result")),
        "last_error": short_text(farm.get("last_error")),
        "handoff_ready": bool(farm.get("handoff_ready")),
    }


def summarize_tianxing_row(row: dict[str, object]) -> dict[str, object]:
    obs = parse_json_object(row.get("tianxing_observation"))
    timeline = parse_json_object(row.get("tianxing_timeline_state"))
    config = parse_json_object(row.get("tianxing_auto_config"))
    active_step = timeline.get("active_step") if isinstance(timeline.get("active_step"), dict) else {}
    released_routes = timeline.get("released_routes")
    released_route_keys = sorted(released_routes.keys()) if isinstance(released_routes, dict) else []
    return {
        "send_as_id": row.get("send_as_id"),
        "label": row.get("label") or "",
        "username": row.get("username") or "",
        "sect": row.get("sect_name") or "",
        "modules": {
            "tianxing": int(row.get("tianxing_enabled") or 0),
            "wild_training": int(row.get("wild_training_enabled") or 0),
            "explore_rift": int(row.get("explore_rift_enabled") or 0),
        },
        "timers": {
            "wild": local_time(row.get("next_wild_training_time")),
            "rift": local_time(row.get("next_explore_rift_time")),
            "wild_prepare_retry": local_time(row.get("wild_training_tianxing_prepare_retry_at")),
            "rift_prepare_retry": local_time(row.get("explore_rift_tianxing_prepare_retry_at")),
            "wild_reply_due": local_time(row.get("wild_training_reply_due_at")),
            "rift_reply_due": local_time(row.get("explore_rift_reply_due_at")),
        },
        "observation": {
            "available_stars": obs.get("available_stars") or [],
            "fixed_star": obs.get("fixed_star") or "",
            "prediction": obs.get("current_prediction") or "",
            "prediction_until": local_time(obs.get("current_prediction_until")),
            "change": obs.get("current_change") or "",
            "change_until": local_time(obs.get("current_change_until")),
            "tianji": obs.get("tianji_value") or 0,
            "calamity": obs.get("calamity_count") or 0,
            "hit": obs.get("hit_count") or 0,
            "miss": obs.get("miss_count") or 0,
            "change_count": obs.get("change_count") or 0,
            "last_action": obs.get("last_action") or "",
            "last_result": obs.get("last_result") or "",
            "last_route": obs.get("last_route") or "",
            "last_tianji_gain": obs.get("last_tianji_gain") or 0,
            "last_contrib_gain": obs.get("last_contrib_gain") or 0,
            "last_summary": short_text(obs.get("last_summary")),
            "auto_next": local_time(obs.get("auto_next_time")),
            "auto_last_action": obs.get("auto_last_action") or "",
            "auto_last_plan": obs.get("auto_last_plan") or "",
            "auto_last_error": short_text(obs.get("auto_last_error")),
            "pending_action": obs.get("auto_pending_action") or "",
            "pending_command": obs.get("auto_pending_command") or "",
            "pending_due": local_time(obs.get("auto_pending_due_at")),
        },
        "timeline": {
            "plan_id": short_text(timeline.get("plan_id"), 120),
            "phase": timeline.get("phase") or "",
            "route": timeline.get("route") or "",
            "reason": short_text(timeline.get("reason")),
            "updated_at": local_time(timeline.get("updated_at")),
            "deadline_at": local_time(timeline.get("deadline_at")),
            "blocked_until": local_time(timeline.get("blocked_until")),
            "active_step_index": timeline.get("active_step_index", -1),
            "active_step": summarize_step(active_step),
            "released_routes": released_route_keys,
            "last_error": short_text(timeline.get("last_error")),
            "craft_farm": summarize_farm(timeline.get("craft_farm")),
            "retreat_farm": summarize_farm(timeline.get("retreat_farm")),
        },
        "config": {
            "mode": config.get("mode") or "",
            "target_tianji": config.get("target_tianji") or 0,
            "craft_farm_enabled": config.get("craft_farm_enabled"),
            "craft_farm_off_window_enabled": config.get("craft_farm_off_window_enabled"),
            "craft_farm_daily_limit": config.get("craft_farm_daily_limit"),
            "explore_change_min_tianji": config.get("explore_change_min_tianji"),
        },
    }


def tianxing_snapshots() -> list[dict[str, object]]:
    rows = sqlite_rows(
        """
        select
            i.send_as_id,
            i.label,
            i.username,
            i.sect_name,
            m.tianxing_enabled,
            m.wild_training_enabled,
            m.explore_rift_enabled,
            t.next_wild_training_time,
            t.next_explore_rift_time,
            r.wild_training_tianxing_prepare_retry_at,
            r.explore_rift_tianxing_prepare_retry_at,
            r.wild_training_reply_due_at,
            r.explore_rift_reply_due_at,
            r.tianxing_observation,
            r.tianxing_auto_config,
            r.tianxing_timeline_state
        from identity_module_state m
        join identities i on i.send_as_id = m.send_as_id
        left join identity_timers t on t.send_as_id = m.send_as_id
        left join identity_runtime_state r on r.send_as_id = m.send_as_id
        where m.tianxing_enabled = 1
        order by i.label, i.username, i.send_as_id
        """
    )
    return [summarize_tianxing_row(row) for row in rows]


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
        "tianxing": tianxing_snapshots(),
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
