import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from ..config import MESSAGES_DIR, STATE_DIR, TZ_LOCAL
from ..runtime import send_audit_log
from ..state import get_identity_display_name, get_identity_ids, get_send_as_profile


REPORT_HOUR = 23
REPORT_MINUTE = 55
STATE_FILE = os.path.join(STATE_DIR, "duel_daily_report_state.json")
RE_ATTACKER = re.compile(r"攻方[:：]\s*@(?P<username>[^\s·|]+)")
RE_WINNER = re.compile(r"胜者[:：]\s*(?P<username>@[^\s|]+).*?净得修为\s*\+(?P<amount>[\d.]+)\s*(?P<unit>万)?")
RE_LOSER = re.compile(r"败者[:：]\s*(?P<username>@[^\s|]+).*?损失修为\s*-(?P<amount>[\d.]+)\s*(?P<unit>万)?")

_report_state_loaded = False
_report_state = {}
_last_sent_day_memory = ""
_next_retry_at = 0.0


def _parse_amount(value, unit=""):
    amount = float(value or 0)
    if unit:
        amount *= 10_000
    return max(0, int(round(amount)))


def _format_amount(amount):
    amount = max(0, int(amount or 0))
    if amount and amount % 10_000 == 0:
        return f"{amount // 10_000}万"
    return f"{amount:,}"


def _identity_username_map():
    result = {}
    for identity_id in get_identity_ids():
        identity_id = int(identity_id or 0)
        if identity_id <= 0:
            continue
        profile = get_send_as_profile(identity_id) or {}
        username = str(profile.get("username") or "").strip().lstrip("@").casefold()
        if username:
            result[username] = {
                "identity_id": identity_id,
                "name": get_identity_display_name(identity_id),
            }
    return result


def _daily_log_entries(day, messages_dir=None):
    path = os.path.join(messages_dir or MESSAGES_DIR, f"{day}.log")
    latest = {}
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for seq, line in enumerate(handle, start=1):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = int(payload.get("message_id") or 0)
            chat_id = int(payload.get("chat_id") or 0)
            latest[(chat_id, msg_id or f"seq:{seq}")] = payload
    return list(latest.values())


def build_duel_daily_report(day=None, messages_dir=None):
    day = str(day or datetime.now(TZ_LOCAL).strftime("%Y-%m-%d"))
    identities = _identity_username_map()
    by_identity = Counter()
    by_identity_count = Counter()
    target_gains = Counter()
    for entry in _daily_log_entries(day, messages_dir):
        text = str(entry.get("text") or "").strip()
        if not text.startswith("【天道战报·文字版】"):
            continue
        attacker = RE_ATTACKER.search(text)
        loser = RE_LOSER.search(text)
        if not attacker or not loser:
            continue
        attacker_key = attacker.group("username").casefold()
        identity = identities.get(attacker_key)
        if not identity:
            continue
        loss = _parse_amount(loser.group("amount"), loser.group("unit"))
        if loss <= 0 or loser.group("username").lstrip("@").casefold() != attacker_key:
            continue
        identity_key = str(identity["identity_id"])
        entry_key = (identity_key, identity["name"])
        by_identity[entry_key] += loss
        by_identity_count[entry_key] += 1
        winner = RE_WINNER.search(text)
        if winner:
            target_gains[winner.group("username")] += _parse_amount(winner.group("amount"), winner.group("unit"))
    entries = [
        {"identity_id": int(identity_id), "name": name, "amount": amount, "count": by_identity_count[(identity_id, name)]}
        for (identity_id, name), amount in by_identity.items()
    ]
    entries.sort(key=lambda item: (-item["amount"], item["name"]))
    return {
        "day": day,
        "entries": entries,
        "target_gains": dict(target_gains),
        "total_amount": sum(item["amount"] for item in entries),
        "total_count": sum(item["count"] for item in entries),
    }


def format_duel_daily_report(report):
    lines = [
        f"🗡️ 斗法修为转移日结｜{report.get('day') or ''}",
        f"合计：{int(report.get('total_count') or 0)}场｜转出修为 {_format_amount(report.get('total_amount'))}",
    ]
    for item in report.get("entries") or ():
        lines.append(f"- {item.get('name')}: {int(item.get('count') or 0)}场｜转出 {_format_amount(item.get('amount'))}")
    targets = report.get("target_gains") or {}
    if targets:
        target_text = "、".join(
            f"{target} +{_format_amount(amount)}"
            for target, amount in sorted(targets.items(), key=lambda item: (-item[1], item[0]))
        )
        lines.append(f"目标获得：{target_text}")
    lines.append("口径：仅统计真实天道战报；目标CD、拒绝、超时和未发送不计。")
    return "\n".join(lines)


def _load_report_state():
    global _report_state_loaded, _report_state
    if _report_state_loaded:
        return _report_state
    _report_state_loaded = True
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    _report_state = data if isinstance(data, dict) else {}
    return _report_state


def _save_report_state(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_path = f"{STATE_FILE}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, STATE_FILE)


def _report_day_for_now(local_now):
    if (local_now.hour, local_now.minute) >= (REPORT_HOUR, REPORT_MINUTE):
        return local_now.strftime("%Y-%m-%d")
    if local_now.hour == 0:
        return (local_now - timedelta(days=1)).strftime("%Y-%m-%d")
    return ""


async def run_duel_daily_report_scheduler(now):
    global _last_sent_day_memory, _next_retry_at
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    day = _report_day_for_now(local_now)
    if not day or float(now) < _next_retry_at:
        return False
    report_state = _load_report_state()
    if report_state.get("last_report_day") == day or _last_sent_day_memory == day:
        return False
    report = build_duel_daily_report(day)
    if int(report.get("total_count") or 0) <= 0:
        return False
    ok = await send_audit_log(format_duel_daily_report(report), scope="global", priority="normal", limit=1200)
    if not ok:
        _next_retry_at = float(now) + 5 * 60
        return False
    report_state["last_report_day"] = day
    report_state["last_sent_at"] = int(time.time())
    report_state["total_count"] = int(report.get("total_count") or 0)
    report_state["total_amount"] = int(report.get("total_amount") or 0)
    _save_report_state(report_state)
    _last_sent_day_memory = day
    return True


__all__ = [
    "build_duel_daily_report",
    "format_duel_daily_report",
    "run_duel_daily_report_scheduler",
]
