#!/usr/bin/env python3
"""Read-only audit for the proposed identity-level high-incense silence policy."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ_LOCAL = timezone(timedelta(hours=8))
TS_FORMAT = "%Y-%m-%d %H:%M:%S UTC+8"
MIN_SILENCE_STOCK = 100_000


@dataclass(frozen=True)
class TimerPolicy:
    timer_key: str
    enabled_key: str
    module: str
    decision: str
    reason: str
    miniapp_key: str = ""


TIMER_POLICIES = (
    TimerPolicy("next_deep_retreat_time", "deep_retreat_enabled", "深度闭关", "allow_conditional", "只放行启动/续轮；主动 .查看闭关 查询应阻断", "deep_retreat"),
    TimerPolicy("next_yuanying_time", "yuanying_enabled", "元婴", "allow_conditional", "只放行 .元婴出窍/.元婴闭关；主动 .元婴状态 查询应阻断", "yuanying"),
    TimerPolicy("next_second_soul_time", "second_soul_enabled", "第二元神", "allow_conditional", "只放行长 CD 修炼/必要链路动作；.第二元神 状态查询应阻断"),
    TimerPolicy("next_stargazer_panel_time", "stargazer_enabled", "观星台", "miniapp_only", "仅纯 MiniApp 公共入口可放行，群内入口命令应阻断", "stargazer"),
    TimerPolicy("next_fishing_time", "fishing_enabled", "灵溪垂钓", "miniapp_only", "仅纯 MiniApp 公共入口可放行，群内 .钓鱼 入口应阻断", "fishing"),
    TimerPolicy("next_irr_time", "tree_enabled", "灵树", "block", "MiniApp 入口仍依赖游戏群发言时应阻断"),
    TimerPolicy("next_guard_time", "tree_enabled", "灵树守护", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_pet_time", "pet_enabled", "灵兽抚摸", "block", "抚摸不属于长 CD 白名单"),
    TimerPolicy("next_pet_warm_time", "pet_warm_enabled", "灵兽温养", "block", "温养不属于长 CD 白名单"),
    TimerPolicy("next_pet_trial_time", "pet_trial_enabled", "灵兽试炼", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_pet_formation_time", "pet_formation_enabled", "灵兽阵法", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_tianti_status_time", "tianti_enabled", "登天阶", "block", "宗门/登天阶动作应阻断"),
    TimerPolicy("next_checkin_time", "checkin_enabled", "点卯", "block", "每日点卯应阻断"),
    TimerPolicy("next_sect_teach_time", "sect_teach_enabled", "宗门传功", "block", "宗门动作应阻断"),
    TimerPolicy("next_tower_time", "tower_enabled", "闯塔", "block", "闯塔应阻断"),
    TimerPolicy("next_quiz_time", "quiz_enabled", "玄骨问答", "block", "问答会在游戏群发言"),
    TimerPolicy("next_jiyin_time", "jiyin_enabled", "机缘", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_concubine_time", "concubine_enabled", "侍妾", "block", "侍妾/婉影动作应阻断"),
    TimerPolicy("next_nanlong_time", "nanlong_enabled", "南陇侯", "block", "侍妾相关动作应阻断"),
    TimerPolicy("next_small_world_time", "small_world_enabled", "小世界", "block", "模块内部静默之外，身份级静默还应阻断主动查询/维护"),
    TimerPolicy("next_ranch_time", "ranch_enabled", "放养", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_wild_training_time", "wild_training_enabled", "野外历练", "block", "野外/天星属于高频高风险动作"),
    TimerPolicy("next_search_node_time", "search_node_enabled", "搜寻节点", "block", "高香火静默期不主动在游戏群发言"),
    TimerPolicy("next_wendao_time", "wendao_enabled", "问道", "block", "问道应阻断"),
    TimerPolicy("next_formation_time", "formation_enabled", "合欢温养", "block", "温养不属于长 CD 白名单"),
    TimerPolicy("next_explore_rift_time", "explore_rift_enabled", "探寻裂缝", "block", "裂缝/天星属于高频高风险动作"),
    TimerPolicy("next_duel_time", "duel_enabled", "斗法", "block", "斗法应阻断"),
    TimerPolicy("next_mulan_time", "mulan_enabled", "慕兰", "block", "慕兰应阻断"),
)


PURE_MINIAPP_CONFIG_KEYS = {
    "deep_retreat": "cave_public_deep_status_enabled",
    "yuanying": "cave_public_yuanying_enabled",
    "stargazer": "cave_public_stargazer_enabled",
    "fishing": "cave_public_fishing_enabled",
}


def _connect_read_only(db_path):
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _decode_json(value, fallback):
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback
    return decoded


def _load_miniapp_config(connection):
    row = connection.execute("SELECT value FROM meta WHERE key = 'miniapp_auto_config'").fetchone()
    value = _decode_json(row[0] if row else "{}", {})
    return value if isinstance(value, dict) else {}


def _effective_threshold(configured):
    try:
        value = int(configured or 0)
    except (TypeError, ValueError):
        value = 0
    return max(MIN_SILENCE_STOCK, value)


def _identity_rows(connection):
    columns = [policy.timer_key for policy in TIMER_POLICIES]
    enabled = [policy.enabled_key for policy in TIMER_POLICIES]
    select_columns = ", ".join(
        [f"t.{key} AS {key}" for key in sorted(set(columns))]
        + [f"m.{key} AS {key}" for key in sorted(set(enabled))]
    )
    query = f"""
        SELECT i.send_as_id, i.username, i.label, i.enabled,
               m.small_world_barrier_min_stock,
               r.small_world_incense_stock,
               r.small_world_last_panel_at,
               {select_columns}
          FROM identities i
          JOIN identity_module_state m USING(send_as_id)
          JOIN identity_runtime_state r USING(send_as_id)
          JOIN identity_timers t USING(send_as_id)
         WHERE m.small_world_enabled = 1 OR r.small_world_incense_stock > 0
         ORDER BY r.small_world_incense_stock DESC, i.send_as_id
    """
    return connection.execute(query).fetchall()


def _miniapp_available(policy, identity_id, config):
    config_key = PURE_MINIAPP_CONFIG_KEYS.get(policy.miniapp_key)
    if not config_key or not config.get(config_key):
        return False
    if policy.miniapp_key == "fishing":
        selected = {int(value) for value in config.get("cave_public_fishing_identity_ids") or ()}
        return int(identity_id) in selected
    return bool(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))


def _format_due(value):
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, TZ_LOCAL).strftime(TS_FORMAT)


def _forecast_identity(row, config, *, now, horizon_hours):
    end_ts = now.timestamp() + max(1.0, float(horizon_hours or 24)) * 3600
    actions = []
    for policy in TIMER_POLICIES:
        if not bool(row[policy.enabled_key]):
            continue
        try:
            due_at = float(row[policy.timer_key] or 0)
        except (TypeError, ValueError):
            due_at = 0
        if due_at > end_ts:
            continue
        decision = policy.decision
        miniapp_available = _miniapp_available(policy, row["send_as_id"], config)
        if decision == "miniapp_only" and not miniapp_available:
            decision = "block"
        actions.append({
            "module": policy.module,
            "timer_key": policy.timer_key,
            "due_at": _format_due(due_at),
            "overdue": due_at <= now.timestamp(),
            "decision": decision,
            "pure_miniapp_available": miniapp_available,
            "reason": policy.reason,
        })
    return actions


def _parse_ts(value):
    try:
        return datetime.strptime(str(value or ""), TS_FORMAT).replace(tzinfo=TZ_LOCAL)
    except (TypeError, ValueError):
        return None


def _iter_message_rows(messages_dir, start, end):
    day = start.date()
    while day <= end.date():
        path = Path(messages_dir) / f"{day.isoformat()}.log"
        if path.exists():
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    ts = _parse_ts(row.get("ts"))
                    if ts is not None and start <= ts <= end:
                        yield row
        day += timedelta(days=1)


def classify_command(command):
    text = " ".join(str(command or "").strip().split())
    if text in {".深度闭关", ".元婴出窍", ".元婴闭关", ".元神修炼"}:
        return "allow_long_cd"
    if text.startswith((".查看闭关", ".元婴状态", ".第二元神")):
        return "block_status_probe"
    if text.startswith((".抉择 ", ".元神镇魔", ".五子同心魔")):
        return "review_chain"
    return "block"


def build_audit(db_path, messages_dir, *, now=None, horizon_hours=24, recent_hours=24):
    now = now or datetime.now(TZ_LOCAL)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_LOCAL)
    else:
        now = now.astimezone(TZ_LOCAL)
    connection = _connect_read_only(db_path)
    try:
        config = _load_miniapp_config(connection)
        rows = list(_identity_rows(connection))
    finally:
        connection.close()

    identities = []
    active_ids = set()
    tracked_ids = set()
    for row in rows:
        identity_id = int(row["send_as_id"])
        stock = int(row["small_world_incense_stock"] or 0)
        threshold = _effective_threshold(row["small_world_barrier_min_stock"])
        active = bool(row["enabled"]) and stock >= threshold
        tracked_ids.add(identity_id)
        if active:
            active_ids.add(identity_id)
        identities.append({
            "identity_id": identity_id,
            "username": str(row["username"] or ""),
            "label": str(row["label"] or ""),
            "enabled": bool(row["enabled"]),
            "incense_stock": stock,
            "threshold": threshold,
            "silence_active": active,
            "last_panel_at": _format_due(row["small_world_last_panel_at"]),
            "forecast": _forecast_identity(row, config, now=now, horizon_hours=horizon_hours),
        })

    recent_start = now - timedelta(hours=max(1.0, float(recent_hours or 24)))
    recent = []
    active_violations = []
    for row in _iter_message_rows(messages_dir, recent_start, now):
        if row.get("event_type") != "sent":
            continue
        identity_id = int(row.get("sender_id") or 0)
        if identity_id not in tracked_ids:
            continue
        decision = classify_command(row.get("text"))
        item = {
            "ts": str(row.get("ts") or ""),
            "identity_id": identity_id,
            "message_id": int(row.get("message_id") or 0),
            "command": str(row.get("text") or "")[:160],
            "source_module": str(row.get("source_module") or ""),
            "policy_decision": decision,
        }
        recent.append(item)
        if identity_id in active_ids and decision.startswith("block"):
            active_violations.append(item)

    return {
        "policy": "read-only forecast; no runtime guard, send, state mutation, or retry",
        "generated_at": now.strftime(TS_FORMAT),
        "horizon_hours": float(horizon_hours),
        "recent_hours": float(recent_hours),
        "summary": {
            "tracked_identities": len(identities),
            "active_silence_identities": len(active_ids),
            "active_violations": len(active_violations),
        },
        "identities": identities,
        "recent_sends": recent,
        "active_violations": active_violations,
    }


def format_report(payload):
    summary = payload["summary"]
    lines = [
        f"high-incense silence audit: {payload['generated_at']}",
        f"policy: {payload['policy']}",
        (
            f"tracked={summary['tracked_identities']} active={summary['active_silence_identities']} "
            f"violations={summary['active_violations']} horizon={payload['horizon_hours']:g}h"
        ),
    ]
    for identity in payload.get("identities") or ():
        name = identity.get("label") or identity.get("username") or identity["identity_id"]
        lines.append(
            f"- {name}[{identity['identity_id']}]: stock={identity['incense_stock']} "
            f"threshold={identity['threshold']} active={'yes' if identity['silence_active'] else 'no'}"
        )
        for action in identity.get("forecast") or ():
            due = action.get("due_at") or "due/unknown"
            lines.append(
                f"  {action['decision']}: {action['module']} @ {due} | {action['reason']}"
            )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/state/chaogu_state.db")
    parser.add_argument("--messages-dir", default="data/messages")
    parser.add_argument("--horizon-hours", type=float, default=24.0)
    parser.add_argument("--recent-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build_audit(
        args.db,
        args.messages_dir,
        horizon_hours=args.horizon_hours,
        recent_hours=args.recent_hours,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
