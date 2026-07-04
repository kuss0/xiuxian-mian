#!/usr/bin/env python3
"""Read-only proactive preflight for live Xiuxian monitor-repair loops."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "state" / "chaogu_state.db"
HEARTBEAT_PATH = PROJECT_ROOT / "data" / "state" / "listener_heartbeat.json"

TIANXING_TIMELINE_ACK_TIMEOUT_SEC = 90
TIANXING_TIMELINE_SEND_TIMEOUT_SEC = 75
TIANXING_TIMELINE_QUEUE_RETRY_MAX_SEC = 45
TIANXING_CHANGE_ROUTE_PREPARE_MIN_SEC = 10 * 60
TIANXING_TIME_BUFFER_SEC = 60


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _epoch(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _fmt(ts: Any) -> str:
    value = _epoch(ts)
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _effective_prepare_lead(config: dict[str, Any], *, require_change_fate: bool) -> int:
    try:
        lead_sec = int(config.get("route_prepare_lead_sec", 5 * 60) or 5 * 60)
    except (TypeError, ValueError, OverflowError):
        lead_sec = 5 * 60
    lead_sec = max(30, min(60 * 60, lead_sec))
    if not require_change_fate:
        return lead_sec
    try:
        send_timeout = int(config.get("send_timeout_sec", TIANXING_TIMELINE_SEND_TIMEOUT_SEC) or TIANXING_TIMELINE_SEND_TIMEOUT_SEC)
    except (TypeError, ValueError, OverflowError):
        send_timeout = TIANXING_TIMELINE_SEND_TIMEOUT_SEC
    if send_timeout == 35:
        send_timeout = TIANXING_TIMELINE_SEND_TIMEOUT_SEC
    try:
        ack_timeout = int(config.get("ack_timeout_sec", TIANXING_TIMELINE_ACK_TIMEOUT_SEC) or TIANXING_TIMELINE_ACK_TIMEOUT_SEC)
    except (TypeError, ValueError, OverflowError):
        ack_timeout = TIANXING_TIMELINE_ACK_TIMEOUT_SEC
    two_step_budget = 2 * (max(send_timeout, ack_timeout) + TIANXING_TIMELINE_QUEUE_RETRY_MAX_SEC) + TIANXING_TIME_BUFFER_SEC
    return max(lead_sec, TIANXING_CHANGE_ROUTE_PREPARE_MIN_SEC, two_step_budget)


def _active_step_summary(timeline: dict[str, Any]) -> str:
    step = timeline.get("active_step")
    if not isinstance(step, dict):
        return ""
    action = str(step.get("action") or "")
    arg = str(step.get("arg") or "")
    status = str(step.get("status") or "")
    if not action and not status:
        return ""
    return " ".join(part for part in (action, arg, status) if part)


def _has_valid_route_state(obs: dict[str, Any], now: float, *, route: str) -> tuple[bool, bool]:
    pred = str(obs.get("current_prediction") or "").strip()
    pred_until = _epoch(obs.get("current_prediction_until"))
    chg = str(obs.get("current_change") or "").strip()
    chg_until = _epoch(obs.get("current_change_until"))
    return pred == route and pred_until > now, chg == route and chg_until > now


def _tianxing_action_status(
    *,
    label: str,
    username: str,
    action: str,
    due_at: float,
    retry_at: float,
    obs: dict[str, Any],
    timeline: dict[str, Any],
    config: dict[str, Any],
    now: float,
    prior_consume_at: float = 0,
) -> dict[str, Any] | None:
    if due_at <= 0:
        return None
    due_in = int(due_at - now)
    route = "探索"
    lead = _effective_prepare_lead(config, require_change_fate=True)
    pred_ok, change_ok = _has_valid_route_state(obs, now, route=route)
    phase = str(timeline.get("phase") or "")
    active_step = _active_step_summary(timeline)
    timeline_preparing = phase in {
        "sending",
        "sent_waiting_ack",
        "state_confirmed",
        "downstream_released",
        "calibrating",
        "waiting_send",
    } or bool(active_step)
    retry_live = retry_at > now
    prior_will_consume = bool(prior_consume_at > now and prior_consume_at < due_at)
    if pred_ok and change_ok and not prior_will_consume:
        level = "healthy"
        reason = "推命/改命探索均有效。"
    elif pred_ok and change_ok and prior_will_consume:
        level = "watch"
        reason = f"当前推命/改命探索预计会先被 {_fmt(prior_consume_at)} 的探索动作消费，后续需重算。"
    elif due_in > lead:
        level = "watch"
        reason = f"尚未进入天星准备窗口，提前 {due_in - lead}s 后复查。"
    elif timeline_preparing:
        level = "watch"
        reason = f"已进入准备窗口，时间线处理中：{phase or active_step}。"
    elif retry_live and retry_at <= due_at:
        level = "watch"
        reason = f"已安排准备重试：{_fmt(retry_at)}。"
    else:
        level = "at_risk"
        reason = "已进入准备窗口但未见有效推命/改命、时间线处理或准备重试。"
    return {
        "level": level,
        "module": "tianxing",
        "label": label,
        "username": username,
        "action": action,
        "due_at": _fmt(due_at),
        "due_in_sec": due_in,
        "prepare_lead_sec": lead,
        "prediction": str(obs.get("current_prediction") or ""),
        "prediction_until": _fmt(obs.get("current_prediction_until")),
        "change": str(obs.get("current_change") or ""),
        "change_until": _fmt(obs.get("current_change_until")),
        "tianji": int(obs.get("tianji_value") or 0),
        "timeline_phase": phase,
        "active_step": active_step,
        "retry_at": _fmt(retry_at),
        "reason": reason,
    }


def _fetch_rows(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query).fetchall()]


def _listener_status(now: float) -> dict[str, Any]:
    if not HEARTBEAT_PATH.exists():
        return {"level": "at_risk", "module": "listener", "reason": "listener heartbeat missing"}
    try:
        payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"level": "at_risk", "module": "listener", "reason": f"listener heartbeat unreadable: {exc!r}"}
    updated_at = _epoch(payload.get("updated_at"))
    age = int(now - updated_at) if updated_at else 999999
    failed = payload.get("failed_accounts") or []
    if age > 120 or failed:
        level = "at_risk"
    elif age > 45:
        level = "watch"
    else:
        level = "healthy"
    return {
        "level": level,
        "module": "listener",
        "age_sec": age,
        "registered_accounts": payload.get("registered_accounts") or [],
        "failed_accounts": failed,
        "reason": "listener heartbeat fresh" if level == "healthy" else "listener heartbeat stale or failed account present",
    }


def snapshot(*, horizon_sec: int) -> dict[str, Any]:
    now = time.time()
    checks: list[dict[str, Any]] = [_listener_status(now)]
    with sqlite3.connect(DB_PATH) as conn:
        pending = _fetch_rows(
            conn,
            """
            SELECT send_as_id, cmd, sent_at, retry, timeout, source_module, op_id
            FROM pending_tasks
            ORDER BY sent_at
            """,
        )
        if pending:
            oldest = min(_epoch(row.get("sent_at")) for row in pending)
            age = int(now - oldest) if oldest else 0
            checks.append(
                {
                    "level": "watch" if age < 120 else "at_risk",
                    "module": "pending_tasks",
                    "count": len(pending),
                    "oldest_age_sec": age,
                    "samples": [
                        {
                            "send_as_id": row.get("send_as_id"),
                            "cmd": row.get("cmd"),
                            "sent_at": _fmt(row.get("sent_at")),
                            "source_module": row.get("source_module"),
                            "op_id": _short(row.get("op_id"), 80),
                        }
                        for row in pending[:5]
                    ],
                    "reason": "pending task queue is not empty",
                }
            )
        else:
            checks.append({"level": "healthy", "module": "pending_tasks", "count": 0, "reason": "pending queue empty"})

        rows = _fetch_rows(
            conn,
            """
            SELECT
                i.label,
                i.username,
                m.wild_training_enabled,
                m.explore_rift_enabled,
                t.next_wild_training_time,
                t.next_explore_rift_time,
                r.wild_training_tianxing_prepare_retry_at,
                r.explore_rift_tianxing_prepare_retry_at,
                r.tianxing_observation,
                r.tianxing_timeline_state,
                r.tianxing_auto_config
            FROM identity_module_state m
            JOIN identities i ON i.send_as_id = m.send_as_id
            LEFT JOIN identity_timers t ON t.send_as_id = m.send_as_id
            LEFT JOIN identity_runtime_state r ON r.send_as_id = m.send_as_id
            WHERE m.tianxing_enabled = 1
            ORDER BY t.next_wild_training_time
            """,
        )
    for row in rows:
        obs = _parse_json(row.get("tianxing_observation"))
        timeline = _parse_json(row.get("tianxing_timeline_state"))
        config = _parse_json(row.get("tianxing_auto_config"))
        label = str(row.get("label") or "")
        username = str(row.get("username") or "")
        if int(row.get("wild_training_enabled") or 0):
            due_at = _epoch(row.get("next_wild_training_time"))
            if 0 < due_at - now <= horizon_sec:
                item = _tianxing_action_status(
                    label=label,
                    username=username,
                    action="野外历练",
                    due_at=due_at,
                    retry_at=_epoch(row.get("wild_training_tianxing_prepare_retry_at")),
                    obs=obs,
                    timeline=timeline,
                    config=config,
                    now=now,
                )
                if item:
                    checks.append(item)
        if int(row.get("explore_rift_enabled") or 0):
            due_at = _epoch(row.get("next_explore_rift_time"))
            if 0 < due_at - now <= max(horizon_sec, 8 * 3600):
                item = _tianxing_action_status(
                    label=label,
                    username=username,
                    action="探寻裂缝",
                    due_at=due_at,
                    retry_at=_epoch(row.get("explore_rift_tianxing_prepare_retry_at")),
                    obs=obs,
                    timeline=timeline,
                    config=config,
                    now=now,
                    prior_consume_at=(
                        _epoch(row.get("next_wild_training_time"))
                        if int(row.get("wild_training_enabled") or 0)
                        and _epoch(row.get("next_wild_training_time")) > now
                        and _epoch(row.get("next_wild_training_time")) < due_at
                        else 0
                    ),
                )
                if item:
                    checks.append(item)
    severity_order = {"at_risk": 0, "watch": 1, "healthy": 2}
    checks.sort(key=lambda item: (severity_order.get(str(item.get("level")), 9), str(item.get("module")), int(item.get("due_in_sec", 999999))))
    overall = "healthy"
    if any(item.get("level") == "at_risk" for item in checks):
        overall = "at_risk"
    elif any(item.get("level") == "watch" for item in checks):
        overall = "watch"
    return {
        "ts": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "status": overall,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only defensive Xiuxian live preflight.")
    parser.add_argument("--horizon-sec", type=int, default=3600)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of concise text.")
    args = parser.parse_args()
    data = snapshot(horizon_sec=max(60, args.horizon_sec))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data["status"] != "at_risk" else 2
    print(f"{data['ts']} defensive_preflight: {data['status']}")
    for item in data["checks"]:
        level = item.get("level")
        module = item.get("module")
        label = item.get("label") or ""
        action = item.get("action") or ""
        due = item.get("due_at") or ""
        due_in = item.get("due_in_sec")
        head = " ".join(str(part) for part in (level, module, label, action) if part)
        if due:
            head += f" due={due} in={due_in}s"
        print(f"- {head}: {item.get('reason')}")
    return 0 if data["status"] != "at_risk" else 2


if __name__ == "__main__":
    raise SystemExit(main())
