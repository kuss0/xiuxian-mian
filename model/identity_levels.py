import re
import time

from .state import get_tianjige_dao_path_records, has_identity, set_tianjige_dao_path_records
from .timing import fmt_abs_ts


_LEVEL_RE = re.compile(r"等级\s*[:：]\s*(\d+)\s*级")
_YUANYING_LEVEL_RE = re.compile(r"元婴[^\n]{0,24}?等级\s*[:：]?\s*(\d+)\s*级")
_SECOND_SOUL_LEVEL_RE = re.compile(r"第二元神[^\n]{0,24}?等级\s*[:：]?\s*(\d+)\s*级")


def _format_level_text(value):
    try:
        level = int(value)
    except (TypeError, ValueError):
        return ""
    return f"{level}级" if level > 0 else ""


def parse_yuanying_level_text(text):
    raw = str(text or "")
    match = _YUANYING_LEVEL_RE.search(raw)
    if not match and "元婴" in raw:
        match = _LEVEL_RE.search(raw)
    return _format_level_text(match.group(1)) if match else ""


def parse_second_soul_level_text(text):
    raw = str(text or "")
    match = _SECOND_SOUL_LEVEL_RE.search(raw)
    if not match and "第二元神" in raw:
        match = _LEVEL_RE.search(raw)
    return _format_level_text(match.group(1)) if match else ""


def update_identity_level_record(send_as_id, field, level_text, *, now=None, source="command"):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0 or not has_identity(send_as_id):
        return False
    if field not in {"yuanying_level", "second_soul_level"}:
        return False
    level_text = str(level_text or "").strip()
    if not level_text:
        return False

    now = float(now or time.time())
    records = dict(get_tianjige_dao_path_records())
    key = str(send_as_id)
    record = records.get(key)
    record = dict(record) if isinstance(record, dict) else {}
    if record.get(field) == level_text:
        return False
    record[field] = level_text
    record[f"{field}_source"] = str(source or "command")
    record[f"{field}_updated_at"] = now
    record[f"{field}_updated_at_text"] = fmt_abs_ts(now)
    record["updated_at"] = max(float(record.get("updated_at") or 0), now)
    record["updated_at_text"] = fmt_abs_ts(record["updated_at"])
    records[key] = record
    set_tianjige_dao_path_records(records)
    return True
