"""Shared read-only business checks; no runtime imports or game requests."""
import json
import math
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
LABELS = {"qipai": "七派密议", "tianlan": "天澜草原", "yuejing": "越京夜行", "yinming": "阴冥之地",
          "xuangu": "玄骨遗府", "changfeng": "长风试炼", "zhengmo": "正魔大战"}


def _dict(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _epoch(value):
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def expedition_health(config, records, enabled_ids, now):
    """Warn about stale occupancy and missed daily scheduling, never clear it."""
    local = datetime.fromtimestamp(now, TZ)
    today = local.strftime("%Y-%m-%d")
    alerts = []

    def alert(kind, key, identities, reason, **extra):
        ids = sorted(set(identities) & enabled_ids)
        if ids:
            alerts.append(dict(level="at_risk", module="expeditions", kind=kind, run_key=str(key),
                               identity_ids=ids, reason=f"{LABELS.get(kind, kind)}：{reason}", **extra))

    collections = [("weekly", _dict(records.get("weekly")).get("runs", {})),
                   ("xuangu", _dict(records.get("xuangu_auto")).get("rosters", {})),
                   ("changfeng", records.get("changfeng", {})), ("zhengmo", records.get("zhengmo", {}))]
    for module, runs in collections:
        for key, run in _dict(runs).items():
            if not isinstance(run, dict):
                continue
            kind = run.get("kind", module)
            held = run.get("requires_reconciliation") or run.get("phase") in {"held", "interrupted"}
            started = _epoch(run.get("started_at") or run.get("created_at"))
            overdue = (run.get("phase") in {"opening", "joining", "running", "dissolving", "waiting_open", "finishing", "loading", "starting", "querying", "marching"}
                       and _epoch(run.get("deadline")) > 0 and now > _epoch(run["deadline"]) + 300)
            if not started or now - started < 900 or not (held or overdue):
                continue
            ids = [m.get("identity_id") for m in run.get("members", run.get("my_members", [])) if isinstance(m, dict)]
            if not ids and module in {"changfeng", "zhengmo"}:
                ids = [run.get("identity_id") or (int(key) if str(key).isdigit() else None)]
            reason = "原局待核对超过15分钟，可能持续占用账号" if held else "运行阶段已超过回执期限，检查调度与回包"
            alert(kind, key, ids, reason, age_sec=int(now - started))

    for kind in ("changfeng", "zhengmo"):
        cfg = _dict(config.get(kind))
        if not cfg.get("enabled"):
            continue
        # Allow startup, account staggering and the normal query/retry window.
        hour = max(0, min(23, int(_epoch(cfg.get("hour", 9))))) if kind == "zhengmo" else 9
        if local.hour < min(23, hour + 1):
            continue
        ids = ([entry.get("identity_id") for entry in cfg.get("identities", []) if isinstance(entry, dict)]
               if kind == "zhengmo" else cfg.get("participant_identity_ids", []))
        for identity in ids:
            run = _dict(_dict(records.get(kind)).get(str(identity)))
            if run.get("day") == today or _epoch(run.get("next_at") or run.get("next_check")) > now:
                continue
            alert(kind, identity, [identity], "今日尚无执行记录，检查调度与活动占用")
    return alerts


def read_expedition_health(conn, now):
    """Use the caller's SQLite snapshot; tolerate older schemas."""
    if not {"key", "value"} <= {r[1] for r in conn.execute("PRAGMA table_info(meta)")}:
        return []
    meta = dict(conn.execute("SELECT key,value FROM meta WHERE key IN "
                            "('expedition_config','expedition_run_state','global_enabled',"
                            "'global_recovery_hold_until','global_recovery_throttle_until','channel_send_as_health')"))
    if str(meta.get("global_enabled", "true")).strip().lower() in {"0", "false", "off"}:
        return []
    if now < max(_epoch(meta.get("global_recovery_hold_until")), _epoch(meta.get("global_recovery_throttle_until"))):
        return []
    if not {"send_as_id", "enabled"} <= {r[1] for r in conn.execute("PRAGMA table_info(identities)")}:
        return []
    enabled = {r[0] for r in conn.execute("SELECT send_as_id FROM identities WHERE enabled=1")}
    enabled -= set(_dict(meta.get("channel_send_as_health")).get("frozen_identity_ids", []))
    return expedition_health(_dict(meta.get("expedition_config")), _dict(meta.get("expedition_run_state")), enabled, now)
