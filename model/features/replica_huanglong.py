"""Huanglong conscription leaf domain for replica automation."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class HuanglongConscriptionContext:
    tz_local: Any
    query_command: str
    query_hour: int
    query_minute: int
    retry_interval_sec: int
    get_run_state: Callable[[], dict]
    save_run_state: Callable[[dict], Any]
    get_participant_identity_ids: Callable[[], list]
    get_identity_ids: Callable[[], list]
    get_identity_enabled: Callable[[int], bool]
    get_identity_account: Callable[[int], int]
    is_account_offline: Callable[[int], bool]
    send_audit_log: Callable[..., Any]
    send_game_command: Callable[..., Any]


def local_day_key(context, now=None):
    return datetime.fromtimestamp(float(now or time.time()), context.tz_local).strftime("%Y-%m-%d")


def get_conscription_state(context):
    run_state = context.get_run_state()
    state_item = run_state.get("huanglong_conscription")
    if not isinstance(state_item, dict):
        state_item = {}
    for key in ("notified_days", "query_sent_days", "query_attempts"):
        if not isinstance(state_item.get(key), dict):
            state_item[key] = {}
    return state_item


def save_conscription_state(context, state_item):
    run_state = context.get_run_state()
    run_state["huanglong_conscription"] = state_item if isinstance(state_item, dict) else {}
    context.save_run_state(run_state)


def parse_conscription_text(context, text, now=None):
    raw_text = str(text or "")
    if "黄龙山轮值军报" not in raw_text and "黄龙山宗门征调" not in raw_text:
        return {}
    day_match = re.search(r"黄龙山宗门征调\s*·\s*(\d{4}-\d{2}-\d{2})", raw_text)
    day_key = day_match.group(1) if day_match else local_day_key(context, now)
    sect_match = (
        re.search(r"轮值宗门[为：:\s]*【([^】]+)】", raw_text)
        or re.search(r"轮值宗门为\s*【([^】]+)】", raw_text)
    )
    if not sect_match:
        return {}
    stage_match = re.search(r"当前阶段[：:]\s*([^\n\r]+)", raw_text)
    signup_match = re.search(r"当前报名总数[：:]\s*(\d+)\s*人\s*/\s*可报名总数\s*(\d+)\s*人", raw_text)
    return {
        "day": day_key,
        "sect": str(sect_match.group(1) or "").strip(),
        "stage": str(stage_match.group(1) or "").strip() if stage_match else "",
        "signup_count": int(signup_match.group(1)) if signup_match else 0,
        "signup_total": int(signup_match.group(2)) if signup_match else 0,
    }


async def handle_conscription_text(context, text, now=None):
    now = float(now or time.time())
    parsed = parse_conscription_text(context, text, now=now)
    if not parsed:
        return False
    day_key = parsed.get("day") or local_day_key(context, now)
    sect = str(parsed.get("sect") or "").strip()
    if not sect:
        return False
    state_item = get_conscription_state(context)
    notified_days = state_item.get("notified_days")
    if not isinstance(notified_days, dict):
        notified_days = {}
    if day_key in notified_days:
        return False
    notified_days[day_key] = {
        "sect": sect,
        "stage": parsed.get("stage") or "",
        "signup_count": int(parsed.get("signup_count") or 0),
        "signup_total": int(parsed.get("signup_total") or 0),
        "notified_at": now,
    }
    state_item["notified_days"] = notified_days
    save_conscription_state(context, state_item)
    details = []
    if parsed.get("stage"):
        details.append(f"阶段：{parsed['stage']}")
    if int(parsed.get("signup_total") or 0) > 0:
        details.append(f"报名：{int(parsed.get('signup_count') or 0)}/{int(parsed.get('signup_total') or 0)}")
    suffix = "｜" + "｜".join(details) if details else ""
    await context.send_audit_log(
        f"🧩 黄龙征调：{day_key} 轮值宗门【{sect}】{suffix}",
        scope="global",
        priority="high",
        limit=320,
    )
    return True


def conscription_query_due(context, now):
    local_dt = datetime.fromtimestamp(float(now or time.time()), context.tz_local)
    return (local_dt.hour, local_dt.minute) >= (context.query_hour, context.query_minute)


def select_query_identity(context):
    candidate_ids = []
    for identity_id in [*context.get_participant_identity_ids(), *context.get_identity_ids()]:
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id > 0 and identity_id not in candidate_ids:
            candidate_ids.append(identity_id)
    for identity_id in candidate_ids:
        if not context.get_identity_enabled(identity_id):
            continue
        account_id = int(context.get_identity_account(identity_id) or 0)
        if account_id and context.is_account_offline(account_id):
            continue
        return identity_id
    return 0


async def run_conscription_scheduler(context, now):
    now = float(now or time.time())
    if not conscription_query_due(context, now):
        return 0
    day_key = local_day_key(context, now)
    state_item = get_conscription_state(context)
    if day_key in (state_item.get("notified_days") or {}):
        return 0
    if day_key in (state_item.get("query_sent_days") or {}):
        return 0
    query_attempts = state_item.get("query_attempts")
    if not isinstance(query_attempts, dict):
        query_attempts = {}
    last_attempt_at = float(query_attempts.get(day_key) or 0)
    if last_attempt_at > 0 and now < last_attempt_at + context.retry_interval_sec:
        return 0
    identity_id = select_query_identity(context)
    if identity_id <= 0:
        return 0
    query_attempts[day_key] = now
    state_item["query_attempts"] = query_attempts
    save_conscription_state(context, state_item)
    msg = await context.send_game_command(
        context.query_command,
        track=False,
        send_as_id=identity_id,
        priority="probe",
        max_retry=0,
        source_module="自动副本",
        op_id=f"huanglong_conscription:{day_key}",
        chain_id=f"huanglong_conscription:{day_key}",
    )
    if not msg:
        return 0
    state_item = get_conscription_state(context)
    query_sent_days = state_item.get("query_sent_days")
    if not isinstance(query_sent_days, dict):
        query_sent_days = {}
    query_sent_days[day_key] = float(getattr(msg, "sent_at", 0) or now)
    state_item["query_sent_days"] = query_sent_days
    save_conscription_state(context, state_item)
    return 1


__all__ = [
    "HuanglongConscriptionContext",
    "conscription_query_due",
    "get_conscription_state",
    "handle_conscription_text",
    "local_day_key",
    "parse_conscription_text",
    "run_conscription_scheduler",
    "save_conscription_state",
    "select_query_identity",
]
