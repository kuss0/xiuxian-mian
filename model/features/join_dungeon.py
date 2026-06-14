import asyncio
import hashlib
import re
import time
from collections import deque

from ..config import CMD_DUNGEON_HUANGLONG_JOIN, CMD_DUNGEON_JOIN, CMD_DUNGEON_ZHUIMO_JOIN, CMD_REPLICA_LUOYUN_JOIN
from ..persistence import save_state
from ..runtime import _fire_and_forget, console_log, send_audit_log, send_game_command
from ..state import (
    get_dungeon_join_run_state,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    set_dungeon_join_run_state,
)
from ..timing import has_wait_time, parse_wait_time
from . import workflow_log


# 自动副本只允许向游戏群发加入副本类指令，不能在游戏群补充说明。
# CD、状态、查询、满员/失败统计都必须留在日志群或 UI，不能在游戏群补充说明。
DUNGEON_KIND_VIRTUAL_HALL = "virtual_hall"
DUNGEON_KIND_ZHUIMO = "zhuimo"
DUNGEON_KIND_HUANGLONG = "huanglong"
DUNGEON_KIND_LUOYUN = "luoyun"
DUNGEON_KIND_META = {
    DUNGEON_KIND_VIRTUAL_HALL: {"name": "虚天殿", "join_command": CMD_DUNGEON_JOIN},
    DUNGEON_KIND_ZHUIMO: {"name": "坠魔谷", "join_command": CMD_DUNGEON_ZHUIMO_JOIN},
    DUNGEON_KIND_HUANGLONG: {"name": "黄龙山", "join_command": CMD_DUNGEON_HUANGLONG_JOIN},
    DUNGEON_KIND_LUOYUN: {"name": "落云秘圃", "join_command": CMD_REPLICA_LUOYUN_JOIN},
}
DUNGEON_INBOX_TTL_SEC = 5 * 60
DUNGEON_MATCH_WINDOW_SEC = 60
DUNGEON_INBOX_MAX_ITEMS = 3000
DUNGEON_JOIN_THROTTLE_SEC = 5 * 60
DUNGEON_JOIN_THROTTLE_MAX = 3
DUNGEON_SENT_TTL_SEC = 30 * 60
DUNGEON_ACTIVE_TTL_SEC = 2 * 60 * 60
DUNGEON_SETTLEMENT_CONTEXT_TTL_SEC = 2 * 60 * 60
DUNGEON_SUCCESS_COOLDOWN_SEC = 125 * 60
DUNGEON_COOLDOWN_BUFFER_SEC = 30
DUNGEON_FAILURE_GRACE_SEC = 3 * 60
DUNGEON_JOIN_FAST_RETRY_DELAY_SEC = 2.5
DUNGEON_JOIN_FAST_RETRY_LIMIT = 1

_JOIN_COMMANDS_RE = "|".join(re.escape(meta["join_command"]) for meta in DUNGEON_KIND_META.values())
_DUNGEON_ID_RE = re.compile(rf"(?:(?:副本|房间)ID\s*[:：]\s*|(?:{_JOIN_COMMANDS_RE})\s+)(\d+)")
_UNSUPPORTED_DUNGEON_MARKERS = ("血色试炼", "加入血色试炼", "进入血色试炼", "血色抉择")
_USERNAME_PATTERN = r"@[^\s，。！？、；：:,.!?()（）【】\[\]]+"
_USERNAME_RE = re.compile(_USERNAME_PATTERN)
_JOINED_RE = re.compile(rf"({_USERNAME_PATTERN})\s*已(?:成功)?加入(?:副本\s*(\d+)|坠魔谷(?:\s*(\d+))?|黄龙山(?:队伍)?(?:\s*(\d+))?|落云秘圃(?:队伍)?(?:\s*(\d+))?)")
_ROOM_DISSOLVED_RE = re.compile(r"已将副本房间\s*[（(]\s*ID\s*[:：]\s*(\d+)\s*[）)]\s*解散")
_inbox = deque()
_by_msg_id = {}
_join_keys = {}
_join_throttle = {}
_settlement_notice_keys = {}


def _event_chat_id(event):
    return int(getattr(event, "chat_id", 0) or 0)


def _event_msg_id(event):
    return int(getattr(event, "id", 0) or 0)


def _record_dungeon_workflow_event(
    event,
    *,
    status="",
    identity_id=0,
    dungeon_id="",
    dungeon_kind="",
    command="",
    msg_id=0,
    reply_to_msg_id=0,
    chat_id=0,
    source_message_id=0,
    text="",
    decision="",
    detail=None,
    now=None,
):
    try:
        identity_id = int(identity_id or 0)
    except (TypeError, ValueError):
        identity_id = 0
    dungeon_id = str(dungeon_id or "").strip()
    op_id = f"{identity_id}:{dungeon_id}" if identity_id and dungeon_id else ""
    event_name = str(event or "").strip() or "event"
    workflow_detail = {
        "dungeon_id": dungeon_id,
        "dungeon_kind": str(dungeon_kind or "").strip(),
    }
    if isinstance(detail, dict):
        workflow_detail.update(detail)
    elif detail:
        workflow_detail["detail"] = str(detail)
    return workflow_log.append_workflow_event(
        "dungeon_join",
        op_id=op_id,
        step=event_name,
        event=event_name,
        status=status,
        identity_id=identity_id,
        chat_id=chat_id,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        source_message_id=source_message_id,
        family="dungeon_join",
        command=command,
        text=text,
        decision=decision or event_name,
        detail=workflow_detail,
        route_source="dungeon_join",
        state_after=status or event_name,
        now=now,
    )
    return False


def _now_ts(now=None):
    return float(now if now is not None else time.time())


def _get_reply_to_msg_id(event):
    reply_header = getattr(event, "reply_to", None)
    return int(getattr(reply_header, "reply_to_msg_id", 0) or 0)


def _get_topic_id(event):
    reply_header = getattr(event, "reply_to", None)
    top_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0)
    if top_id > 0:
        return top_id
    reply_to_msg_id = int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    if bool(getattr(reply_header, "forum_topic", False)):
        return reply_to_msg_id
    if reply_to_msg_id > 0 and reply_to_msg_id == int(get_game_topic_id() or 0):
        return reply_to_msg_id
    return 0


def _cleanup(now=None):
    now = _now_ts(now)
    while _inbox and now - float((_inbox[0] or {}).get("date", 0) or 0) > DUNGEON_INBOX_TTL_SEC:
        old = _inbox.popleft()
        old_id = int((old or {}).get("msg_id", 0) or 0)
        if old_id > 0 and _by_msg_id.get(old_id) is old:
            _by_msg_id.pop(old_id, None)
    while len(_inbox) > DUNGEON_INBOX_MAX_ITEMS:
        old = _inbox.popleft()
        old_id = int((old or {}).get("msg_id", 0) or 0)
        if old_id > 0 and _by_msg_id.get(old_id) is old:
            _by_msg_id.pop(old_id, None)
    for key, item in list(_join_keys.items()):
        sent_at = float((item or {}).get("at", 0) or 0)
        if now - float(sent_at or 0) > DUNGEON_SENT_TTL_SEC:
            _join_keys.pop(key, None)
    for identity_id, items in list(_join_throttle.items()):
        kept = [ts for ts in items if now - float(ts or 0) <= DUNGEON_JOIN_THROTTLE_SEC]
        if kept:
            _join_throttle[identity_id] = kept
        else:
            _join_throttle.pop(identity_id, None)
    records = _get_run_records()
    changed = False
    for raw_identity_id, record in list(records.items()):
        normalized = _normalize_run_record(record)
        active_until = float(normalized.get("active_until", 0) or 0)
        cooldown_until = float(normalized.get("cooldown_until", 0) or 0)
        pending_until = float(normalized.get("pending_until", 0) or 0)
        if normalized.get("participating") and active_until > 0 and now >= active_until:
            normalized["participating"] = False
            normalized["room_id"] = ""
            changed = True
        if pending_until > 0 and now >= pending_until:
            normalized["pending_msg_id"] = 0
            normalized["pending_room_id"] = ""
            normalized["pending_until"] = 0
            normalized["retry_count"] = 0
            changed = True
        if float(normalized.get("settlement_until", 0) or 0) > 0 and now >= float(normalized.get("settlement_until", 0) or 0):
            normalized["settlement_room_id"] = ""
            normalized["settlement_team_ids"] = []
            normalized["settlement_until"] = 0
            changed = True
        if normalized != record:
            records[str(raw_identity_id)] = normalized
    if changed:
        _save_run_records(records)


def record_game_group_message(event, *, now=None, event_type="message"):
    if str(event_type or "message") != "message":
        return
    game_group_id = int(get_game_group_id() or 0)
    if not game_group_id or _event_chat_id(event) != game_group_id:
        return False
    now = _now_ts(now)
    _cleanup(now)
    msg_id = int(getattr(event, "id", 0) or 0)
    if msg_id <= 0:
        return
    item = {
        "msg_id": msg_id,
        "sender_id": int(getattr(event, "sender_id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "topic_id": _get_topic_id(event),
        "reply_to_msg_id": _get_reply_to_msg_id(event),
        "text": getattr(event, "raw_text", "") or "",
        "date": now,
        "sender_is_game_bot": bool(getattr(event, "_xiuxian_sender_is_game_bot", False))
        or int(getattr(event, "sender_id", 0) or 0) in set(get_game_bot_ids()),
    }
    existing = _by_msg_id.get(msg_id)
    if existing:
        existing.update(item)
        return
    _inbox.append(item)
    _by_msg_id[msg_id] = item
    dungeon_id = _parse_dungeon_id(item["text"])
    if dungeon_id and item["sender_is_game_bot"]:
        _record_dungeon_workflow_event(
            "announcement_seen",
            status="observed",
            dungeon_id=dungeon_id,
            dungeon_kind=_infer_dungeon_kind(item["text"]),
            chat_id=item["chat_id"],
            msg_id=msg_id,
            reply_to_msg_id=item["reply_to_msg_id"],
            text=item["text"],
            decision="passive_announcement_recorded",
            now=now,
        )
        console_log(f"🧩 副本公告入箱：ID={dungeon_id} msg_id={msg_id}", scope="global", limit=160)


def _parse_dungeon_id(text):
    raw = str(text or "")
    if not _is_supported_dungeon_join_text(raw):
        return ""
    match = _DUNGEON_ID_RE.search(raw)
    return str(match.group(1) or "").strip() if match else ""


def _is_supported_dungeon_join_text(text):
    raw = str(text or "")
    if any(marker in raw for marker in _UNSUPPORTED_DUNGEON_MARKERS):
        return False
    if any(meta["name"] in raw or meta["join_command"] in raw for meta in DUNGEON_KIND_META.values()):
        return True
    return "副本ID" in raw or CMD_DUNGEON_JOIN in raw


def _infer_dungeon_kind(text):
    raw = str(text or "")
    if not _is_supported_dungeon_join_text(raw):
        return ""
    for kind, meta in DUNGEON_KIND_META.items():
        if meta["name"] in raw or meta["join_command"] in raw:
            return kind
    if "副本ID" in raw or "加入副本" in raw:
        return DUNGEON_KIND_VIRTUAL_HALL
    return DUNGEON_KIND_VIRTUAL_HALL


def _format_dungeon_join_command(dungeon_id, dungeon_kind=""):
    kind = dungeon_kind if dungeon_kind in DUNGEON_KIND_META else DUNGEON_KIND_VIRTUAL_HALL
    command = DUNGEON_KIND_META[kind]["join_command"]
    return f"{command} {str(dungeon_id or '').strip()}"


def _identity_usernames(identity_id):
    profile = get_send_as_profile(identity_id)
    usernames = []
    for raw in (profile.get("username"),):
        value = str(raw or "").strip()
        if not value:
            continue
        username = value.lstrip("@").lower()
        if username and username not in usernames:
            usernames.append(username)
    return usernames


def _normalize_username(username):
    return str(username or "").strip().lstrip("@").lower()


def _extract_usernames(text):
    usernames = []
    for raw_username in _USERNAME_RE.findall(str(text or "")):
        username = _normalize_username(raw_username)
        if username and username not in usernames:
            usernames.append(username)
    return usernames


def _extract_team_section(text):
    raw_text = str(text or "")
    if "当前队伍" not in raw_text:
        return ""
    team_section = raw_text.split("当前队伍", 1)[1]
    for marker in ("【卦象词条】", "【", "当前契合", "断术", "行运", "爻意"):
        if marker in team_section:
            team_section = team_section.split(marker, 1)[0]
    return team_section


def _extract_team_usernames(text):
    team_section = _extract_team_section(text)
    if not team_section:
        return []
    return _extract_usernames(team_section)


def _identity_id_by_username(username):
    username = _normalize_username(username)
    if not username:
        return 0
    for identity_id in get_identity_ids():
        if username in _identity_usernames(identity_id):
            return int(identity_id)
    return 0


def _normalize_run_record(record):
    record = record if isinstance(record, dict) else {}
    settlement_team_ids = []
    for raw_identity_id in record.get("settlement_team_ids") or []:
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id > 0 and identity_id not in settlement_team_ids:
            settlement_team_ids.append(identity_id)
    return {
        "participating": bool(record.get("participating")),
        "room_id": str(record.get("room_id") or ""),
        "joined_at": float(record.get("joined_at", 0) or 0),
        "active_until": float(record.get("active_until", 0) or 0),
        "cooldown_until": float(record.get("cooldown_until", 0) or 0),
        "pending_msg_id": int(record.get("pending_msg_id", 0) or 0),
        "pending_room_id": str(record.get("pending_room_id") or ""),
        "pending_until": float(record.get("pending_until", 0) or 0),
        "retry_count": int(record.get("retry_count", 0) or 0),
        "last_result": str(record.get("last_result") or ""),
        "last_error": str(record.get("last_error") or ""),
        "updated_at": float(record.get("updated_at", 0) or 0),
        "settlement_room_id": str(record.get("settlement_room_id") or ""),
        "settlement_team_ids": settlement_team_ids,
        "settlement_until": float(record.get("settlement_until", 0) or 0),
    }


def _get_run_records():
    records = get_dungeon_join_run_state()
    return records if isinstance(records, dict) else {}


def _save_run_records(records):
    set_dungeon_join_run_state(records if isinstance(records, dict) else {})
    save_state()


def _get_identity_run_record(records, identity_id):
    return _normalize_run_record((records or {}).get(str(int(identity_id or 0))))


def _get_active_until(record):
    active_until = float((record or {}).get("active_until", 0) or 0)
    joined_at = float((record or {}).get("joined_at", 0) or 0)
    if joined_at > 0:
        joined_active_until = joined_at + DUNGEON_ACTIVE_TTL_SEC
        return min(active_until, joined_active_until) if active_until > 0 else joined_active_until
    return active_until


def _get_identity_block_reason(identity_id, now):
    records = _get_run_records()
    record = _get_identity_run_record(records, identity_id)
    cooldown_until = float(record.get("cooldown_until", 0) or 0)
    if cooldown_until > now:
        return "cooldown"
    active_until = _get_active_until(record)
    if record.get("participating") and active_until > now:
        return "participating"
    pending_until = float(record.get("pending_until", 0) or 0)
    if pending_until > now:
        return "pending"
    return ""


def _mark_pending_join(identity_id, dungeon_id, msg_id, now, retry_count=0):
    records = _get_run_records()
    record = _get_identity_run_record(records, identity_id)
    record.update({
        "pending_msg_id": int(msg_id or 0),
        "pending_room_id": str(dungeon_id or ""),
        "pending_until": float(now or 0) + DUNGEON_SENT_TTL_SEC,
        "retry_count": max(0, int(retry_count or 0)),
        "last_result": "pending",
        "last_error": "",
        "updated_at": float(now or 0),
    })
    records[str(int(identity_id))] = record
    _save_run_records(records)


def _mark_join_success(identity_id, dungeon_id, now, *, msg_id=0, chat_id=0, reply_to_msg_id=0, text=""):
    records = _get_run_records()
    record = _get_identity_run_record(records, identity_id)
    dungeon_id = str(dungeon_id or record.get("pending_room_id") or record.get("room_id") or "")
    record.update({
        "participating": True,
        "room_id": dungeon_id,
        "joined_at": float(now or 0),
        "active_until": float(now or 0) + DUNGEON_ACTIVE_TTL_SEC,
        "cooldown_until": 0,
        "pending_msg_id": 0,
        "pending_room_id": "",
        "pending_until": 0,
        "retry_count": 0,
        "last_result": "joined",
        "last_error": "",
        "updated_at": float(now or 0),
    })
    records[str(int(identity_id))] = record
    _save_run_records(records)
    _record_dungeon_workflow_event(
        "join_success",
        status="success",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        chat_id=chat_id,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        text=text,
        decision="join_success_observed",
        now=now,
    )


def _mark_join_cooldown(identity_id, wait_sec, now, *, dungeon_id="", msg_id=0, chat_id=0, reply_to_msg_id=0, text=""):
    records = _get_run_records()
    record = _get_identity_run_record(records, identity_id)
    record.update({
        "participating": False,
        "room_id": "",
        "cooldown_until": float(now or 0) + max(0, int(wait_sec or 0)) + DUNGEON_COOLDOWN_BUFFER_SEC,
        "pending_msg_id": 0,
        "pending_room_id": "",
        "pending_until": 0,
        "retry_count": 0,
        "last_result": "cooldown",
        "last_error": "",
        "updated_at": float(now or 0),
    })
    if dungeon_id:
        record["room_id"] = str(dungeon_id)
    records[str(int(identity_id))] = record
    _save_run_records(records)
    _record_dungeon_workflow_event(
        "join_cooldown",
        status="cooldown",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        chat_id=chat_id,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        text=text,
        decision="join_cooldown_observed",
        detail={"wait_sec": int(wait_sec or 0)},
        now=now,
    )


def _mark_join_failure(identity_id, reason, now, *, dungeon_id="", msg_id=0, chat_id=0, reply_to_msg_id=0, text=""):
    records = _get_run_records()
    record = _get_identity_run_record(records, identity_id)
    record.update({
        "participating": False,
        "room_id": str(dungeon_id or record.get("room_id") or ""),
        "pending_msg_id": 0,
        "pending_room_id": "",
        "pending_until": 0,
        "retry_count": 0,
        "last_result": "failed",
        "last_error": str(reason or ""),
        "updated_at": float(now or 0),
    })
    records[str(int(identity_id))] = record
    _save_run_records(records)
    _record_dungeon_workflow_event(
        "join_failure",
        status="failed",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        chat_id=chat_id,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        text=text,
        decision=f"join_failed_{str(reason or '').strip() or 'unknown'}",
        detail={"reason": str(reason or "")},
        now=now,
    )


def _active_identity_ids(now):
    records = _get_run_records()
    identity_ids = []
    for raw_identity_id, record in records.items():
        normalized = _normalize_run_record(record)
        if not normalized.get("participating") or _get_active_until(normalized) <= now:
            continue
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id > 0:
            identity_ids.append(identity_id)
    return identity_ids


def _active_room_ids(now):
    records = _get_run_records()
    room_ids = []
    for record in records.values():
        normalized = _normalize_run_record(record)
        if not normalized.get("participating") or _get_active_until(normalized) <= now:
            continue
        room_id = str(normalized.get("room_id") or "").strip()
        if room_id and room_id not in room_ids:
            room_ids.append(room_id)
    return room_ids


def _active_identity_ids_for_room(room_id, now):
    room_id = str(room_id or "").strip()
    if not room_id:
        return []
    records = _get_run_records()
    identity_ids = []
    for raw_identity_id, record in records.items():
        normalized = _normalize_run_record(record)
        if not normalized.get("participating") or _get_active_until(normalized) <= now:
            continue
        if str(normalized.get("room_id") or "").strip() != room_id:
            continue
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id > 0 and identity_id not in identity_ids:
            identity_ids.append(identity_id)
    return identity_ids


def _normalize_identity_ids(identity_ids):
    normalized = []
    for raw_identity_id in identity_ids or []:
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id > 0 and identity_id not in normalized:
            normalized.append(identity_id)
    return normalized


def _build_settlement_contexts(records, identity_ids, now):
    contexts = {}
    identity_ids = _normalize_identity_ids(identity_ids)
    for identity_id in identity_ids:
        record = _get_identity_run_record(records, identity_id)
        room_id = str(record.get("room_id") or record.get("pending_room_id") or record.get("settlement_room_id") or "").strip()
        if room_id:
            team_ids = _active_identity_ids_for_room(room_id, now) or identity_ids
        else:
            team_ids = identity_ids
        contexts[identity_id] = {
            "settlement_room_id": room_id,
            "settlement_team_ids": _normalize_identity_ids(team_ids),
            "settlement_until": float(now or 0) + DUNGEON_SETTLEMENT_CONTEXT_TTL_SEC,
        }
    return contexts


def _recent_settlement_identity_ids(now):
    records = _get_run_records()
    contexts = {}
    for record in records.values():
        normalized = _normalize_run_record(record)
        if float(normalized.get("settlement_until", 0) or 0) <= float(now or 0):
            continue
        team_ids = _normalize_identity_ids(normalized.get("settlement_team_ids") or [])
        if not team_ids:
            continue
        room_id = str(normalized.get("settlement_room_id") or "").strip()
        key = room_id or ",".join(str(identity_id) for identity_id in team_ids)
        contexts[key] = team_ids
    if len(contexts) == 1:
        return next(iter(contexts.values()))
    return []


def _expand_to_active_room_participants(identity_ids, now):
    records = _get_run_records()
    expanded = []
    for identity_id in identity_ids or []:
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id <= 0:
            continue
        record = _get_identity_run_record(records, identity_id)
        if not record.get("participating") or _get_active_until(record) <= float(now or 0):
            continue
        room_id = str(record.get("room_id") or record.get("pending_room_id") or "").strip()
        room_participants = _active_identity_ids_for_room(room_id, now) if room_id else []
        for candidate_id in room_participants or [identity_id]:
            if candidate_id > 0 and candidate_id not in expanded:
                expanded.append(candidate_id)
    return expanded


def _identity_ids_from_text_usernames(text):
    identity_ids = []
    for username in _extract_usernames(text):
        identity_id = _identity_id_by_username(username)
        if identity_id > 0 and identity_id not in identity_ids:
            identity_ids.append(identity_id)
    return identity_ids


def _identity_ids_from_team_usernames(text):
    identity_ids = []
    for username in _extract_team_usernames(text):
        identity_id = _identity_id_by_username(username)
        if identity_id > 0 and identity_id not in identity_ids:
            identity_ids.append(identity_id)
    return identity_ids


def _identity_ids_from_progress_usernames(text):
    if _extract_team_section(text):
        return _identity_ids_from_team_usernames(text)
    return _identity_ids_from_text_usernames(text)


def _resolve_progress_identity_ids(text, now):
    terminal_settlement = _is_terminal_settlement_text(text)
    if _extract_team_section(text):
        identity_ids = _expand_to_active_room_participants(_identity_ids_from_team_usernames(text), now)
        if identity_ids or not terminal_settlement:
            return identity_ids
        return _recent_settlement_identity_ids(now)
    usernames = _extract_usernames(text)
    if usernames:
        identity_ids = _expand_to_active_room_participants(_identity_ids_from_text_usernames(text), now)
        if identity_ids or not terminal_settlement:
            return identity_ids
        return _recent_settlement_identity_ids(now)
    active_room_ids = _active_room_ids(now)
    if len(active_room_ids) == 1:
        return _active_identity_ids_for_room(active_room_ids[0], now)
    if terminal_settlement:
        return _recent_settlement_identity_ids(now)
    return []


def _is_success_progress_text(text):
    raw = str(text or "")
    if "【鼎前抉择】" in raw:
        return True
    if "【战利品结算" in raw:
        return True
    if "【后殿冲关止步】" in raw and "结算所得早已锁定" in raw:
        return True
    return False


def _is_terminal_settlement_text(text):
    raw = str(text or "")
    if "【战利品结算" in raw:
        return True
    if "【后殿冲关止步】" in raw and "结算所得早已锁定" in raw:
        return True
    return False


def _cleanup_settlement_notice_keys(now):
    now = float(now or time.time())
    for key, created_at in list(_settlement_notice_keys.items()):
        try:
            created_at = float(created_at or 0)
        except (TypeError, ValueError):
            created_at = 0
        if created_at <= 0 or now >= created_at + DUNGEON_SENT_TTL_SEC:
            _settlement_notice_keys.pop(key, None)
    if len(_settlement_notice_keys) > DUNGEON_INBOX_MAX_ITEMS:
        keep = {
            key
            for key, _ts in sorted(_settlement_notice_keys.items(), key=lambda item: float(item[1] or 0), reverse=True)[:DUNGEON_INBOX_MAX_ITEMS]
        }
        for key in list(_settlement_notice_keys):
            if key not in keep:
                _settlement_notice_keys.pop(key, None)


def _mark_settlement_notice_once(text, now):
    _cleanup_settlement_notice_keys(now)
    digest = hashlib.sha1(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    if digest in _settlement_notice_keys:
        return False
    _settlement_notice_keys[digest] = float(now or time.time())
    return True


def _format_settlement_notice_text(text, identity_ids):
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    excerpt = "\n".join(lines[:10])
    if len(excerpt) > 800:
        excerpt = excerpt[:800].rstrip() + "..."
    dungeon_kind = _infer_dungeon_kind(raw) or DUNGEON_KIND_VIRTUAL_HALL
    dungeon_name = (DUNGEON_KIND_META.get(dungeon_kind) or DUNGEON_KIND_META[DUNGEON_KIND_VIRTUAL_HALL])["name"]
    identity_count = len({int(identity_id or 0) for identity_id in identity_ids or [] if int(identity_id or 0) > 0})
    cd_text = f"已记录 {identity_count} 个身份 CD" if identity_count else "未匹配到队伍身份，未写 CD"
    return f"🧩 {dungeon_name}结算：{cd_text}\n\n结算成果：\n{excerpt}"


async def _send_settlement_notice_once(text, identity_ids, now):
    if not _is_terminal_settlement_text(text):
        return False
    if not _mark_settlement_notice_once(text, now):
        return False
    await send_audit_log(_format_settlement_notice_text(text, identity_ids), scope="global", limit=1200)
    return True


def _mark_success_cooldown(identity_ids, now):
    records = _get_run_records()
    identity_ids = _normalize_identity_ids(identity_ids)
    settlement_contexts = _build_settlement_contexts(records, identity_ids, now)
    changed = False
    for identity_id in identity_ids:
        record = _get_identity_run_record(records, identity_id)
        room_id = str(record.get("room_id") or record.get("pending_room_id") or "")
        record.update({
            "participating": False,
            "room_id": "",
            "cooldown_until": max(
                float(record.get("cooldown_until", 0) or 0),
                float(now or 0) + DUNGEON_SUCCESS_COOLDOWN_SEC,
            ),
            "pending_msg_id": 0,
            "pending_room_id": "",
            "pending_until": 0,
            "retry_count": 0,
            "last_result": "success_cooldown",
            "last_error": "",
            "updated_at": float(now or 0),
        })
        record.update(settlement_contexts.get(identity_id) or {})
        records[str(int(identity_id))] = record
        _record_dungeon_workflow_event(
            "progress_success_cooldown",
            status="cooldown",
            identity_id=identity_id,
            dungeon_id=room_id,
            decision="progress_success_sets_cooldown",
            now=now,
        )
        changed = True
    if changed:
        _save_run_records(records)
    return changed


def _clear_room_participants(room_id, now, reason):
    room_id = str(room_id or "").strip()
    if not room_id:
        return False
    records = _get_run_records()
    changed = False
    for raw_identity_id, record in list(records.items()):
        normalized = _normalize_run_record(record)
        if str(normalized.get("room_id") or "") != room_id:
            continue
        normalized.update({
            "participating": False,
            "room_id": "",
            "pending_msg_id": 0,
            "pending_room_id": "",
            "pending_until": 0,
            "retry_count": 0,
            "last_result": "failed",
            "last_error": str(reason or ""),
            "updated_at": float(now or 0),
        })
        records[str(raw_identity_id)] = normalized
        _record_dungeon_workflow_event(
            "room_cleared",
            status="failed",
            identity_id=raw_identity_id,
            dungeon_id=room_id,
            decision=f"room_cleared_{str(reason or '').strip() or 'unknown'}",
            detail={"reason": str(reason or "")},
            now=now,
        )
        changed = True
    if changed:
        _save_run_records(records)
    return changed


def _mark_failure_pending(identity_ids, now):
    records = _get_run_records()
    changed = False
    for identity_id in identity_ids or []:
        record = _get_identity_run_record(records, identity_id)
        room_id = str(record.get("room_id") or record.get("pending_room_id") or "")
        record.update({
            "participating": False,
            "room_id": "",
            "pending_msg_id": 0,
            "pending_room_id": "",
            "pending_until": float(now or 0) + DUNGEON_FAILURE_GRACE_SEC,
            "retry_count": 0,
            "last_result": "failed",
            "last_error": "challenge_failed",
            "updated_at": float(now or 0),
        })
        records[str(int(identity_id))] = record
        _record_dungeon_workflow_event(
            "progress_failure_pending",
            status="failed",
            identity_id=identity_id,
            dungeon_id=room_id,
            decision="challenge_failed_observed",
            detail={"reason": "challenge_failed"},
            now=now,
        )
        changed = True
    if changed:
        _save_run_records(records)
    return changed


def _find_pending_identity_by_reply_msg_id(reply_to_msg_id):
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id <= 0:
        return 0, ""
    for (identity_id, dungeon_id), item in list(_join_keys.items()):
        msg_ids = {int((item or {}).get("msg_id", 0) or 0)}
        msg_ids.update(int(msg_id or 0) for msg_id in (item or {}).get("msg_ids", []) if int(msg_id or 0) > 0)
        if reply_to_msg_id in msg_ids:
            return int(identity_id), str(dungeon_id)
    for raw_identity_id, record in _get_run_records().items():
        normalized = _normalize_run_record(record)
        if int(normalized.get("pending_msg_id", 0) or 0) == reply_to_msg_id:
            try:
                return int(raw_identity_id), str(normalized.get("pending_room_id") or "")
            except (TypeError, ValueError):
                return 0, ""
    return 0, ""


def _extract_mention_usernames(event, text):
    raw_text = str(text or "")
    usernames = []
    message = getattr(event, "message", None) or event
    for entity in getattr(message, "entities", None) or []:
        entity_name = entity.__class__.__name__
        if entity_name != "MessageEntityMention":
            continue
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if length <= 1:
            continue
        token = _slice_utf16_units(raw_text, offset, length).strip()
        if not token.startswith("@"):
            continue
        username = token.lstrip("@").lower()
        if username and username not in usernames:
            usernames.append(username)
    return usernames


def _slice_utf16_units(text, offset, length):
    raw = str(text or "")
    start = max(0, int(offset or 0))
    end = max(start, start + max(0, int(length or 0)))
    units = raw.encode("utf-16-le", "surrogatepass")
    total_units = len(units) // 2
    start = min(start, total_units)
    end = min(end, total_units)
    chunk = units[start * 2: end * 2]
    return chunk.decode("utf-16-le", "replace")


def _extract_mention_user_ids(event):
    user_ids = []
    message = getattr(event, "message", None) or event
    for entity in getattr(message, "entities", None) or []:
        entity_name = entity.__class__.__name__
        if entity_name != "MessageEntityMentionName":
            continue
        try:
            user_id = int(getattr(entity, "user_id", 0) or 0)
        except (TypeError, ValueError):
            user_id = 0
        if user_id > 0 and user_id not in user_ids:
            user_ids.append(user_id)
    return user_ids


def _mentioned_enabled_identity_ids(event, text):
    mention_usernames = set(_extract_mention_usernames(event, text))
    mention_user_ids = set(_extract_mention_user_ids(event))
    if not mention_usernames and not mention_user_ids:
        return []
    identity_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        identity_state = get_identity_state(identity_id)
        if not identity_state.get("dungeon_join_enabled"):
            continue
        if int(identity_id) in mention_user_ids:
            identity_ids.append(int(identity_id))
            continue
        if mention_usernames.intersection(_identity_usernames(identity_id)):
            identity_ids.append(int(identity_id))
    return identity_ids


def _find_matching_dungeon(at_item, *, now=None):
    now = _now_ts(now)
    at_msg_id = int((at_item or {}).get("msg_id", 0) or 0)
    at_sender_id = int((at_item or {}).get("sender_id", 0) or 0)
    at_topic_id = int((at_item or {}).get("topic_id", 0) or 0)
    if at_msg_id <= 0 or at_sender_id <= 0:
        return None
    game_bot_ids = set(get_game_bot_ids())
    for item in reversed(_inbox):
        if int((item or {}).get("msg_id", 0) or 0) >= at_msg_id:
            continue
        if now - float((item or {}).get("date", 0) or 0) > DUNGEON_MATCH_WINDOW_SEC:
            break
        if int((item or {}).get("topic_id", 0) or 0) != at_topic_id:
            continue
        if not (bool((item or {}).get("sender_is_game_bot")) or int((item or {}).get("sender_id", 0) or 0) in game_bot_ids):
            continue
        dungeon_id = _parse_dungeon_id((item or {}).get("text") or "")
        if not dungeon_id:
            continue
        parent_msg_id = int((item or {}).get("reply_to_msg_id", 0) or 0)
        if parent_msg_id <= 0:
            continue
        parent = _by_msg_id.get(parent_msg_id)
        if not parent:
            continue
        if int((parent or {}).get("sender_id", 0) or 0) != at_sender_id:
            continue
        return {
            "dungeon_id": dungeon_id,
            "dungeon_kind": _infer_dungeon_kind((item or {}).get("text") or ""),
            "announcement_msg_id": int((item or {}).get("msg_id", 0) or 0),
            "opener_msg_id": parent_msg_id,
        }
    return None


def _allow_join(identity_id, dungeon_id, now):
    identity_id = int(identity_id)
    dungeon_id = str(dungeon_id or "").strip()
    if not dungeon_id:
        return False, "missing_id"
    block_reason = _get_identity_block_reason(identity_id, now)
    if block_reason:
        return False, block_reason
    sent_key = (identity_id, dungeon_id)
    if sent_key in _join_keys:
        return False, "duplicate"
    recent = [ts for ts in _join_throttle.get(identity_id, []) if now - float(ts or 0) <= DUNGEON_JOIN_THROTTLE_SEC]
    _join_throttle[identity_id] = recent
    if len(recent) >= DUNGEON_JOIN_THROTTLE_MAX:
        return False, "throttled"
    return True, ""


def _reserve_join(identity_id, dungeon_id, now, *, dungeon_kind="", command="", chat_id=0, source_message_id=0):
    _join_keys[(int(identity_id), str(dungeon_id))] = {"at": float(now or 0), "status": "inflight"}
    _record_dungeon_workflow_event(
        "join_reserved",
        status="inflight",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        dungeon_kind=dungeon_kind,
        command=command,
        chat_id=chat_id,
        source_message_id=source_message_id,
        decision="join_reserved_before_send",
        now=now,
    )


def _mark_join_sent(identity_id, dungeon_id, now, msg_id=0, retry_count=0, *, dungeon_kind="", command="", chat_id=0, source_message_id=0):
    key = (int(identity_id), str(dungeon_id))
    previous = _join_keys.get(key) or {}
    msg_ids = [int(item or 0) for item in previous.get("msg_ids", []) if int(item or 0) > 0]
    msg_id = int(msg_id or 0)
    if msg_id > 0 and msg_id not in msg_ids:
        msg_ids.append(msg_id)
    _join_keys[key] = {
        "at": float(now or 0),
        "status": "sent",
        "msg_id": msg_id,
        "msg_ids": msg_ids[-3:],
        "retry_count": max(0, int(retry_count or 0)),
    }
    _mark_pending_join(identity_id, dungeon_id, msg_id, now, retry_count=retry_count)
    _record_dungeon_workflow_event(
        "join_sent",
        status="sent",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        dungeon_kind=dungeon_kind,
        command=command,
        chat_id=chat_id,
        msg_id=msg_id,
        source_message_id=source_message_id,
        decision="join_command_sent" if int(retry_count or 0) <= 0 else "join_fast_retry_sent",
        detail={"retry_count": max(0, int(retry_count or 0)), "msg_ids": msg_ids[-3:]},
        now=now,
    )


def _release_join_reservation(identity_id, dungeon_id):
    key = (int(identity_id), str(dungeon_id))
    item = _join_keys.get(key) or {}
    if item.get("status") == "inflight":
        _join_keys.pop(key, None)


def _should_fast_retry_join(identity_id, dungeon_id, first_msg_id, now):
    key = (int(identity_id), str(dungeon_id))
    item = _join_keys.get(key) or {}
    record = _get_identity_run_record(_get_run_records(), identity_id)
    if item.get("status") != "sent":
        return False
    if int(item.get("retry_count") or 0) >= DUNGEON_JOIN_FAST_RETRY_LIMIT:
        return False
    if first_msg_id and int(item.get("msg_id") or 0) != int(first_msg_id or 0):
        return False
    if str(record.get("pending_room_id") or "") != str(dungeon_id or ""):
        return False
    if first_msg_id and int(record.get("pending_msg_id") or 0) != int(first_msg_id or 0):
        return False
    if float(record.get("pending_until") or 0) <= float(now or 0):
        return False
    if record.get("participating") and _get_active_until(record) > float(now or 0):
        return False
    if float(record.get("cooldown_until") or 0) > float(now or 0):
        return False
    if int(record.get("retry_count") or 0) >= DUNGEON_JOIN_FAST_RETRY_LIMIT:
        return False
    return True


async def _retry_join_once(identity_id, dungeon_id, dungeon_kind, join_command, first_msg_id, delay_sec=None):
    delay_sec = DUNGEON_JOIN_FAST_RETRY_DELAY_SEC if delay_sec is None else max(0, float(delay_sec or 0))
    await asyncio.sleep(delay_sec)
    now = _now_ts()
    _cleanup(now)
    if not _should_fast_retry_join(identity_id, dungeon_id, first_msg_id, now):
        _record_dungeon_workflow_event(
            "join_fast_retry_skipped",
            status="skipped",
            identity_id=identity_id,
            dungeon_id=dungeon_id,
            dungeon_kind=dungeon_kind,
            command=join_command,
            msg_id=first_msg_id,
            decision="fast_retry_guard_blocked",
            detail={"first_msg_id": int(first_msg_id or 0)},
            now=now,
        )
        return False
    retry_count = 1
    _join_keys[(int(identity_id), str(dungeon_id))]["retry_count"] = retry_count
    _mark_pending_join(identity_id, dungeon_id, first_msg_id, now, retry_count=retry_count)
    msg = await send_game_command(
        join_command,
        track=False,
        send_as_id=identity_id,
        priority="urgent_reactive",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if msg:
        _mark_join_sent(
            identity_id,
            dungeon_id,
            sent_at,
            msg_id=int(getattr(msg, "id", 0) or 0),
            retry_count=retry_count,
            dungeon_kind=dungeon_kind,
            command=join_command,
        )
        await send_audit_log(
            f"🧩 自动副本快补发：{join_command}｜retry={retry_count}",
            scope="identity",
            send_as_id=identity_id,
            limit=180,
            priority="low",
        )
        return True
    _record_dungeon_workflow_event(
        "join_fast_retry_send_failed",
        status="failed",
        identity_id=identity_id,
        dungeon_id=dungeon_id,
        dungeon_kind=dungeon_kind,
        command=join_command,
        msg_id=first_msg_id,
        decision="fast_retry_send_failed",
        detail={"retry_count": retry_count},
        now=sent_at,
    )
    return False


def _schedule_join_fast_retry(identity_id, dungeon_id, dungeon_kind, join_command, first_msg_id):
    _fire_and_forget(_retry_join_once(identity_id, dungeon_id, dungeon_kind, join_command, first_msg_id))


async def handle_dungeon_join_mention(event, text, now=None):
    now = _now_ts(now)
    _cleanup(now)
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if sender_id <= 0 or sender_id in set(get_game_bot_ids()):
        return False
    identity_ids = _mentioned_enabled_identity_ids(event, text)
    if not identity_ids:
        return False
    at_item = _by_msg_id.get(int(getattr(event, "id", 0) or 0))
    if not at_item:
        record_game_group_message(event, now=now)
        at_item = _by_msg_id.get(int(getattr(event, "id", 0) or 0))
    matched = _find_matching_dungeon(at_item, now=now)
    if not matched:
        _record_dungeon_workflow_event(
            "mention_no_match",
            status="skipped",
            chat_id=_event_chat_id(event),
            msg_id=_event_msg_id(event),
            text=text,
            decision="mention_without_recent_dungeon_match",
            now=now,
        )
        console_log("🧩 自动副本：收到 @，但未找到同话题/同开门人/60s 内的副本公告。", scope="global", limit=180)
        return False

    dungeon_id = matched["dungeon_id"]
    dungeon_kind = matched.get("dungeon_kind") or DUNGEON_KIND_VIRTUAL_HALL
    join_command = _format_dungeon_join_command(dungeon_id, dungeon_kind)
    handled = False
    for identity_id in identity_ids:
        event_detail = {
            "announcement_msg_id": matched["announcement_msg_id"],
            "opener_msg_id": matched["opener_msg_id"],
            "mention_msg_id": _event_msg_id(event),
        }
        allowed, reason = _allow_join(identity_id, dungeon_id, now)
        if not allowed:
            _record_dungeon_workflow_event(
                "join_skipped",
                status="skipped",
                identity_id=identity_id,
                dungeon_id=dungeon_id,
                dungeon_kind=dungeon_kind,
                command=join_command,
                chat_id=_event_chat_id(event),
                msg_id=_event_msg_id(event),
                text=text,
                decision=f"join_guard_{reason or 'blocked'}",
                detail={**event_detail, "reason": reason},
                now=now,
            )
            if reason == "throttled":
                await send_audit_log(f"🧩 自动副本节流：5分钟内加入次数过多，跳过副本 {dungeon_id}", scope="identity", send_as_id=identity_id, limit=180)
            elif reason in {"cooldown", "participating", "pending"}:
                console_log(f"🧩 自动副本跳过：{reason}｜副本={dungeon_id}", scope="identity", send_as_id=identity_id, limit=160)
            continue
        _reserve_join(
            identity_id,
            dungeon_id,
            now,
            dungeon_kind=dungeon_kind,
            command=join_command,
            chat_id=_event_chat_id(event),
            source_message_id=matched["announcement_msg_id"],
        )
        msg = await send_game_command(
            join_command,
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
        )
        if not msg:
            _release_join_reservation(identity_id, dungeon_id)
            _record_dungeon_workflow_event(
                "join_send_failed",
                status="failed",
                identity_id=identity_id,
                dungeon_id=dungeon_id,
                dungeon_kind=dungeon_kind,
                command=join_command,
                chat_id=_event_chat_id(event),
                source_message_id=matched["announcement_msg_id"],
                text=text,
                decision="join_command_send_failed",
                detail=event_detail,
                now=now,
            )
            continue
        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        sent_msg_id = int(getattr(msg, "id", 0) or 0)
        _mark_join_sent(
            identity_id,
            dungeon_id,
            sent_at,
            msg_id=sent_msg_id,
            dungeon_kind=dungeon_kind,
            command=join_command,
            chat_id=_event_chat_id(event),
            source_message_id=matched["announcement_msg_id"],
        )
        _schedule_join_fast_retry(identity_id, dungeon_id, dungeon_kind, join_command, sent_msg_id)
        _join_throttle.setdefault(int(identity_id), []).append(now)
        await send_audit_log(
            f"🧩 自动副本已发出：{join_command}｜公告={matched['announcement_msg_id']}｜开门={matched['opener_msg_id']}",
            scope="identity",
            send_as_id=identity_id,
            limit=220,
            priority="low",
        )
        handled = True
    return handled


async def handle_dungeon_join_bot_message(event, text, now=None):
    now = _now_ts(now)
    _cleanup(now)
    raw = str(text or "")
    if not raw:
        return False

    if _is_success_progress_text(raw):
        identity_ids = _resolve_progress_identity_ids(raw, now)
        handled = _mark_success_cooldown(identity_ids, now)
        if handled:
            await _send_settlement_notice_once(raw, identity_ids, now)
        return handled
    if "挑战失败！" in raw:
        identity_ids = _resolve_progress_identity_ids(raw, now)
        return _mark_failure_pending(identity_ids, now)
    dissolved_match = _ROOM_DISSOLVED_RE.search(raw)
    if dissolved_match:
        return _clear_room_participants(dissolved_match.group(1), now, "room_dissolved")

    reply_to_msg_id = _get_reply_to_msg_id(event)
    identity_id, dungeon_id = _find_pending_identity_by_reply_msg_id(reply_to_msg_id)
    joined_match = _JOINED_RE.search(raw)
    if identity_id <= 0 and joined_match:
        identity_id = _identity_id_by_username(joined_match.group(1))
        dungeon_id = next((str(group or "").strip() for group in joined_match.groups()[1:] if str(group or "").strip()), "")
    if identity_id <= 0:
        return False

    if joined_match:
        dungeon_id = dungeon_id or next((str(group or "").strip() for group in joined_match.groups()[1:] if str(group or "").strip()), "")
    if "你已在队伍中" in raw or any(keyword in raw for keyword in ("已成功加入副本", "已成功加入坠魔谷", "已成功加入黄龙山", "已加入落云秘圃", "已成功加入落云秘圃")):
        _mark_join_success(
            identity_id,
            dungeon_id,
            now,
            msg_id=_event_msg_id(event),
            chat_id=_event_chat_id(event),
            reply_to_msg_id=reply_to_msg_id,
            text=raw,
        )
        return True

    if (
        ("无法立即加入新副本" in raw and "请在" in raw and "后再试" in raw)
        or (
            "无法加入队伍" in raw
            and (
                "独立冷却" in raw
                or "剩余时间" in raw
                or "冷却结束" in raw
            )
        )
    ):
        wait_sec = parse_wait_time(raw) if has_wait_time(raw) else 0
        if wait_sec > 0:
            _mark_join_cooldown(
                identity_id,
                wait_sec,
                now,
                dungeon_id=dungeon_id,
                msg_id=_event_msg_id(event),
                chat_id=_event_chat_id(event),
                reply_to_msg_id=reply_to_msg_id,
                text=raw,
            )
            return True

    if "此队伍已满员" in raw or "队伍已满" in raw:
        _mark_join_failure(
            identity_id,
            "full",
            now,
            dungeon_id=dungeon_id,
            msg_id=_event_msg_id(event),
            chat_id=_event_chat_id(event),
            reply_to_msg_id=reply_to_msg_id,
            text=raw,
        )
        return True
    if "找不到此副本房间" in raw or "副本房间不存在" in raw:
        _mark_join_failure(
            identity_id,
            "not_found",
            now,
            dungeon_id=dungeon_id,
            msg_id=_event_msg_id(event),
            chat_id=_event_chat_id(event),
            reply_to_msg_id=reply_to_msg_id,
            text=raw,
        )
        return True
    return False


def get_dungeon_join_inbox_snapshot(limit=20):
    _cleanup(time.time())
    items = []
    for item in list(_inbox)[-int(limit or 20):]:
        text = (item or {}).get("text") or ""
        dungeon_id = _parse_dungeon_id(text)
        if not dungeon_id:
            continue
        dungeon_kind = _infer_dungeon_kind(text)
        kind_meta = DUNGEON_KIND_META.get(dungeon_kind) or DUNGEON_KIND_META[DUNGEON_KIND_VIRTUAL_HALL]
        items.append({
            "dungeon_id": dungeon_id,
            "dungeon_kind": dungeon_kind,
            "dungeon_name": kind_meta["name"],
            "join_command": _format_dungeon_join_command(dungeon_id, dungeon_kind),
            "msg_id": int((item or {}).get("msg_id", 0) or 0),
            "reply_to_msg_id": int((item or {}).get("reply_to_msg_id", 0) or 0),
            "topic_id": int((item or {}).get("topic_id", 0) or 0),
            "date": float((item or {}).get("date", 0) or 0),
        })
    return items
