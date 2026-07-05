import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import MESSAGES_DIR, TZ_LOCAL


def parse_message_log_ts(raw_ts):
    raw = str(raw_ts or "").strip()
    if not raw:
        return 0.0
    for suffix in (" UTC+8", "+08:00"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=TZ_LOCAL).timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
    return 0.0


def iter_message_log_entries_between(start_ts, end_ts, *, messages_dir=None):
    try:
        start_ts = max(0.0, float(start_ts or 0))
        end_ts = max(start_ts, float(end_ts or start_ts or 0))
        start_day = datetime.fromtimestamp(start_ts, TZ_LOCAL).date()
        end_day = datetime.fromtimestamp(end_ts, TZ_LOCAL).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return

    base_dir = Path(messages_dir or MESSAGES_DIR)
    day = start_day
    while day <= end_day:
        log_path = base_dir / f"{day.isoformat()}.log"
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        entry_ts = parse_message_log_ts(entry.get("ts"))
                        if start_ts <= entry_ts <= end_ts:
                            yield entry, entry_ts
            except OSError:
                pass
        day += timedelta(days=1)


def find_message_log_replies(command_msg_id, now, *, lookback_sec=900, lookahead_sec=30, predicate=None, messages_dir=None):
    command_msg_id = int(command_msg_id or 0)
    if command_msg_id <= 0:
        return []
    end_ts = float(now or 0) + max(0, int(lookahead_sec or 0))
    start_ts = max(0.0, end_ts - max(1, int(lookback_sec or 1)))
    matches = []
    for entry, entry_ts in iter_message_log_entries_between(start_ts, end_ts, messages_dir=messages_dir):
        if int((entry or {}).get("reply_to_msg_id") or 0) != command_msg_id:
            continue
        if predicate is not None and not predicate(entry):
            continue
        item = dict(entry)
        item["ts_epoch"] = entry_ts
        matches.append(item)
    matches.sort(key=lambda item: (float(item.get("ts_epoch") or 0), int(item.get("message_id") or 0)))
    return matches


def find_message_log_message(msg_id, now, *, lookback_sec=900, lookahead_sec=30, predicate=None, messages_dir=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None
    end_ts = float(now or 0) + max(0, int(lookahead_sec or 0))
    start_ts = max(0.0, end_ts - max(1, int(lookback_sec or 1)))
    found = None
    for entry, entry_ts in iter_message_log_entries_between(start_ts, end_ts, messages_dir=messages_dir):
        if int((entry or {}).get("message_id") or 0) != msg_id:
            continue
        if predicate is not None and not predicate(entry):
            continue
        found = dict(entry)
        found["ts_epoch"] = entry_ts
    return found


def find_recent_message_log_command(now, *, sender_id=0, command_predicate=None, start_ts=0, lookback_sec=900, lookahead_sec=30, messages_dir=None):
    sender_id = int(sender_id or 0)
    end_ts = float(now or 0) + max(0, int(lookahead_sec or 0))
    start_ts = float(start_ts or 0)
    if start_ts <= 0:
        start_ts = max(0.0, end_ts - max(1, int(lookback_sec or 1)))
    found = None
    for entry, entry_ts in iter_message_log_entries_between(start_ts, end_ts, messages_dir=messages_dir):
        if sender_id > 0 and int((entry or {}).get("sender_id") or 0) != sender_id:
            continue
        if command_predicate is not None and not command_predicate(entry):
            continue
        found = dict(entry)
        found["ts_epoch"] = entry_ts
    return found


def sender_matches_identity(sender_id, identity_id):
    try:
        sender_id = int(sender_id or 0)
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if sender_id == 0 or identity_id == 0:
        return False
    if sender_id in {identity_id, -identity_id}:
        return True
    if sender_id < 0:
        raw_sender = str(abs(sender_id))
        raw_identity = str(abs(identity_id))
        return raw_sender == f"100{raw_identity}"
    return False


def recover_sent_command_from_message_log(
    command,
    send_as_id,
    now,
    *,
    start_ts=0,
    game_group_id=0,
    topic_id=0,
    reply_to_msg_id=0,
    lookback_sec=900,
    lookahead_sec=30,
    messages_dir=None,
):
    """Recover a sent command when Telegram accepted it but the RPC result timed out."""
    command = str(command or "").strip()
    if not command:
        return None
    try:
        send_as_id = int(send_as_id or 0)
        game_group_id = int(game_group_id or 0)
        topic_id = int(topic_id or 0)
        reply_to_msg_id = int(reply_to_msg_id or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if send_as_id <= 0:
        return None

    def predicate(entry):
        if str((entry or {}).get("event_type") or "") not in {"message", "sent"}:
            return False
        if game_group_id and int((entry or {}).get("chat_id") or 0) != game_group_id:
            return False
        if str((entry or {}).get("text") or "").strip() != command:
            return False
        entry_reply_to = int((entry or {}).get("reply_to_msg_id") or 0)
        if reply_to_msg_id > 0:
            if entry_reply_to != reply_to_msg_id:
                return False
        elif topic_id > 0 and entry_reply_to not in {0, topic_id}:
            return False
        return sender_matches_identity((entry or {}).get("sender_id"), send_as_id)

    found = find_recent_message_log_command(
        now,
        sender_id=0,
        command_predicate=predicate,
        start_ts=start_ts,
        lookback_sec=lookback_sec,
        lookahead_sec=lookahead_sec,
        messages_dir=messages_dir,
    )
    if not found:
        return None
    try:
        msg_id = int(found.get("message_id") or 0)
        sent_at = float(found.get("ts_epoch") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if msg_id <= 0 or sent_at <= 0:
        return None
    item = dict(found)
    item["message_id"] = msg_id
    item["ts_epoch"] = sent_at
    return item
