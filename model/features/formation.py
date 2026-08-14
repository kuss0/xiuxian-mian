import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_FORMATION_ASSIST,
    CMD_FORMATION_START,
    FORMATION_ASSIST_DELAY_MAX_SEC,
    FORMATION_ASSIST_DELAY_MIN_SEC,
    FORMATION_ASSIST_REPLY_TIMEOUT_SEC,
    FORMATION_INVITE_TTL_SEC,
    FORMATION_RECOVERY_DELAY_SEC,
    FORMATION_SUCCESS_COOLDOWN_SEC,
)
from ..message_log_recovery import find_message_log_message, find_message_log_replies
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, get_sent_message_chat_id, has_active_reply_dispatch, send_game_command
from ..state import (
    get_current_identity_id,
    get_formation_run_state,
    get_game_group_id,
    get_identity_account,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    has_identity,
    is_module_available,
    set_formation_run_state,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, parse_wait_time


_USERNAME_RE = re.compile(r"@[^\s,，、。.!！]+")
_INVITE_OWNER_RE = re.compile(r"【星宫】弟子\s*(@[^\s,，、。.!！]+)\s*正在布设大阵")
_PARTICIPANTS_RE = re.compile(r"参与者\s*[:：]\s*([^\n]+)")
_INVITE_REPLY_WINDOW_RE = re.compile(r"在\s*([^。！!\n\r]*?\d+\s*(?:小时|时辰|分钟|分|秒))\s*内回复")


def _normalize_username(username):
    raw = str(username or "").strip().rstrip("。.!！,，、")
    if not raw:
        return ""
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw.lower()


def normalize_formation_command(text):
    raw_text = str(text or "").strip()
    bare = raw_text.lstrip(".").strip()
    if bare == "启阵":
        return CMD_FORMATION_START
    if bare == "助阵":
        return CMD_FORMATION_ASSIST
    return raw_text


def _extract_usernames(text):
    usernames = []
    seen = set()
    for raw_username in _USERNAME_RE.findall(str(text or "")):
        username = _normalize_username(raw_username)
        if username and username not in seen:
            seen.add(username)
            usernames.append(username)
    return usernames


def _identity_username(identity_id):
    profile = get_send_as_profile(identity_id)
    return _normalize_username(profile.get("username") or "")


def _local_usernames():
    usernames = {_identity_username(identity_id) for identity_id in get_identity_ids()}
    usernames.discard("")
    return usernames


def _identity_ids_by_username():
    mapping = {}
    for identity_id in get_identity_ids():
        username = _identity_username(identity_id)
        if username:
            mapping[username] = int(identity_id)
    return mapping


def _map_usernames_to_identity_ids(usernames):
    by_username = _identity_ids_by_username()
    mapped = []
    seen = set()
    for username in usernames or []:
        identity_id = by_username.get(_normalize_username(username))
        if identity_id and identity_id not in seen:
            seen.add(identity_id)
            mapped.append(identity_id)
    return mapped


def _parse_invite_wait_sec(text):
    match = _INVITE_REPLY_WINDOW_RE.search(str(text or ""))
    if not match:
        return FORMATION_INVITE_TTL_SEC
    return parse_wait_time(match.group(1)) or FORMATION_INVITE_TTL_SEC


def _parse_formation_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return {"kind": "unknown"}

    if "【周天星斗大阵-启】" in raw_text and "正在布设大阵" in raw_text:
        owner_match = _INVITE_OWNER_RE.search(raw_text)
        return {
            "kind": "invite",
            "owner_username": _normalize_username(owner_match.group(1) if owner_match else ""),
            "wait_sec": _parse_invite_wait_sec(raw_text),
        }

    if "【周天星斗大阵-成】" in raw_text and "参与者" in raw_text:
        participant_match = _PARTICIPANTS_RE.search(raw_text)
        participant_text = participant_match.group(1) if participant_match else raw_text
        return {"kind": "success", "usernames": _extract_usernames(participant_text)}

    if "心神消耗巨大" in raw_text and "再次启阵" in raw_text:
        return {"kind": "cooldown_start", "wait_sec": parse_wait_time(raw_text)}
    if "心神消耗巨大" in raw_text and "再次助阵" in raw_text:
        return {"kind": "cooldown_assist", "wait_sec": parse_wait_time(raw_text)}
    if "已发布启阵邀请" in raw_text or "请勿重复操作" in raw_text:
        return {"kind": "duplicate_invite"}
    if "已在阵中" in raw_text or "无需重复助阵" in raw_text:
        return {"kind": "already_in"}
    if "召集已超时" in raw_text or "没有找到正在召集的大阵" in raw_text or "阵法已过期" in raw_text:
        return {"kind": "invite_failed"}
    if "并非星宫弟子" in raw_text or "非星宫弟子" in raw_text or "不是星宫弟子" in raw_text:
        return {"kind": "not_xinggong"}
    if ".助阵" in raw_text and "星宫" in raw_text and "周天星斗" in raw_text:
        return {"kind": "help"}
    return {"kind": "unknown"}


def is_formation_reply_text(text):
    return _parse_formation_text(text).get("kind") != "unknown"


def _formation_delay():
    return random.uniform(FORMATION_ASSIST_DELAY_MIN_SEC, FORMATION_ASSIST_DELAY_MAX_SEC)


def _normalize_run_state(records=None):
    records = records if isinstance(records, dict) else get_formation_run_state()
    active_invites = records.get("active_invites") if isinstance(records, dict) else {}
    attempted_assists = records.get("attempted_assists") if isinstance(records, dict) else {}
    return {
        "active_invites": active_invites if isinstance(active_invites, dict) else {},
        "attempted_assists": attempted_assists if isinstance(attempted_assists, dict) else {},
        "last_success": records.get("last_success") if isinstance(records, dict) and isinstance(records.get("last_success"), dict) else {},
        "last_error": str(records.get("last_error") or "") if isinstance(records, dict) else "",
        "updated_at": float(records.get("updated_at") or 0) if isinstance(records, dict) else 0,
    }


def _save_run_state(run_state):
    normalized = _normalize_run_state(run_state)
    normalized["updated_at"] = time.time()
    set_formation_run_state(normalized)
    mark_dirty()
    return normalized


def _attempts_for(run_state, identity_id):
    attempts = run_state.setdefault("attempted_assists", {})
    key = str(int(identity_id or 0))
    item = attempts.get(key)
    if not isinstance(item, dict):
        item = {}
        attempts[key] = item
    return item


def _get_attempt(run_state, identity_id, invite_msg_id):
    return (_attempts_for(run_state, identity_id).get(str(int(invite_msg_id or 0))) or {})


def _record_attempt(run_state, identity_id, invite_msg_id, **updates):
    record = dict(_get_attempt(run_state, identity_id, invite_msg_id))
    record.update(updates)
    record["updated_at"] = float(updates.get("updated_at") or time.time())
    _attempts_for(run_state, identity_id)[str(int(invite_msg_id or 0))] = record
    return record


def _clear_identity_pending(identity_id):
    if not has_identity(identity_id):
        return False
    with use_identity(identity_id):
        changed = False
        for key, value in {
            "formation_pending_invite_msg_id": 0,
            "formation_pending_assist_msg_id": 0,
        }.items():
            if state.get(key) != value:
                state[key] = value
                changed = True
        if state.get("formation_last_action") in {"已助阵外部邀请", "已助阵，等待成阵", "等待助阵回复"}:
            state["formation_last_action"] = ""
            changed = True
        if changed:
            mark_dirty()
    return True


def _set_identity_backoff(identity_id, now, error):
    if not has_identity(identity_id):
        return
    with use_identity(identity_id):
        state["next_formation_time"] = float(now or 0) + FORMATION_RECOVERY_DELAY_SEC
        state["formation_last_error"] = str(error or "")
        state["formation_last_action"] = ""
        mark_dirty()


def _set_identity_cooldown(identity_id, until_ts, now, *, result="", success=False):
    if not has_identity(identity_id):
        return
    with use_identity(identity_id):
        state["formation_cooldown_until"] = float(until_ts or 0)
        state["next_formation_time"] = float(until_ts or 0)
        state["formation_pending_invite_msg_id"] = 0
        state["formation_pending_assist_msg_id"] = 0
        state["formation_last_action"] = "冷却中" if float(until_ts or 0) > float(now or 0) else ""
        state["formation_last_result"] = str(result or state.get("formation_last_result") or "")
        state["formation_last_error"] = ""
        if success:
            state["formation_last_success_at"] = float(now or 0)
        mark_dirty()


def _record_invite(parsed, now, *, message_id=0, chat_id=0):
    message_id = int(message_id or 0)
    if message_id <= 0:
        return False
    owner_username = _normalize_username(parsed.get("owner_username"))
    if not owner_username:
        return True
    if owner_username in _local_usernames():
        return True

    wait_sec = int(parsed.get("wait_sec") or FORMATION_INVITE_TTL_SEC)
    run_state = _normalize_run_state()
    run_state.setdefault("active_invites", {})[str(message_id)] = {
        "msg_id": message_id,
        "chat_id": int(chat_id or 0),
        "owner_username": owner_username,
        "created_at": float(now or 0),
        "expire_at": float(now or 0) + max(1, wait_sec),
        "status": "open",
    }
    run_state["last_error"] = ""
    _save_run_state(run_state)
    return True


def _remove_success_invites(run_state, usernames, *, message_id=0, reply_to_msg_id=0):
    username_set = {_normalize_username(username) for username in usernames or []}
    username_set.discard("")
    message_ids = {int(message_id or 0), int(reply_to_msg_id or 0)}
    message_ids.discard(0)
    removed = []
    invites = run_state.setdefault("active_invites", {})
    for key, invite in list(invites.items()):
        invite_msg_id = int((invite or {}).get("msg_id") or 0)
        owner_username = _normalize_username((invite or {}).get("owner_username"))
        if (invite_msg_id in message_ids) or (owner_username and owner_username in username_set):
            removed.append(invite_msg_id)
            invites.pop(key, None)
    return removed


def _identity_ids_by_pending_invites(invite_msg_ids):
    invite_ids = {int(invite_id or 0) for invite_id in invite_msg_ids or []}
    invite_ids.discard(0)
    if not invite_ids:
        return []
    matched = []
    seen = set()
    for identity_id in get_identity_ids():
        identity_state = get_identity_state(identity_id)
        pending_invite_msg_id = int(identity_state.get("formation_pending_invite_msg_id", 0) or 0)
        if pending_invite_msg_id in invite_ids and int(identity_id) not in seen:
            seen.add(int(identity_id))
            matched.append(int(identity_id))
    return matched


def _merge_identity_ids(*identity_id_groups):
    merged = []
    seen = set()
    for identity_ids in identity_id_groups:
        for identity_id in identity_ids or []:
            identity_id = int(identity_id or 0)
            if identity_id > 0 and identity_id not in seen:
                seen.add(identity_id)
                merged.append(identity_id)
    return merged


def _clear_attempts_after_success(run_state, matched_ids, invite_msg_ids):
    invite_keys = {str(int(invite_id or 0)) for invite_id in invite_msg_ids or [] if int(invite_id or 0) > 0}
    attempts = run_state.setdefault("attempted_assists", {})
    for identity_id in matched_ids or []:
        attempts.pop(str(int(identity_id or 0)), None)
    if not invite_keys:
        return
    for identity_key, identity_attempts in list(attempts.items()):
        if not isinstance(identity_attempts, dict):
            attempts.pop(identity_key, None)
            continue
        for invite_key in invite_keys:
            identity_attempts.pop(invite_key, None)
        if not identity_attempts:
            attempts.pop(identity_key, None)


def _record_success(parsed, now, *, message_id=0, reply_to_msg_id=0):
    usernames = parsed.get("usernames") or []
    matched_ids = _map_usernames_to_identity_ids(usernames)
    run_state = _normalize_run_state()
    removed_invites = _remove_success_invites(run_state, usernames, message_id=message_id, reply_to_msg_id=reply_to_msg_id)
    candidate_invite_msg_ids = _merge_identity_ids(removed_invites, [message_id, reply_to_msg_id])
    pending_ids = _identity_ids_by_pending_invites(candidate_invite_msg_ids)
    success_ids = _merge_identity_ids(matched_ids)
    pending_only_ids = [identity_id for identity_id in pending_ids if identity_id not in set(success_ids)]
    for identity_id in pending_only_ids:
        _clear_identity_pending(identity_id)
    _clear_attempts_after_success(run_state, _merge_identity_ids(success_ids, pending_only_ids), candidate_invite_msg_ids)
    participant_text = "、".join(usernames) if usernames else "未知参与者"
    cooldown_until = float(now or 0) + FORMATION_SUCCESS_COOLDOWN_SEC
    for identity_id in success_ids:
        _set_identity_cooldown(
            identity_id,
            cooldown_until,
            now,
            result=f"布阵成功：{participant_text}",
            success=True,
        )
    run_state["last_success"] = {
        "at": float(now or 0),
        "usernames": usernames,
        "identity_ids": success_ids,
        "message_id": int(message_id or 0),
    }
    run_state["last_error"] = ""
    _save_run_state(run_state)
    return bool(success_ids or removed_invites)


def _resolve_failure_identity(reply_to_msg_id=0, identity_id_hint=0):
    identity_id_hint = int(identity_id_hint or 0)
    if identity_id_hint > 0 and has_identity(identity_id_hint):
        return identity_id_hint
    reply_to_msg_id = int(reply_to_msg_id or 0)
    if reply_to_msg_id <= 0:
        return 0
    for identity_id in get_identity_ids():
        identity_state = get_identity_state(identity_id)
        if reply_to_msg_id in (identity_state.get("my_msg_ids") or {}):
            return int(identity_id)
        if reply_to_msg_id == int(identity_state.get("formation_pending_assist_msg_id", 0) or 0):
            return int(identity_id)
        if reply_to_msg_id == int(identity_state.get("last_formation_msg_id", 0) or 0):
            return int(identity_id)
    return 0


def _record_failure(parsed, now, *, reply_to_msg_id=0, message_id=0, identity_id_hint=0, command_text=""):
    kind = str((parsed or {}).get("kind") or "")
    identity_id = _resolve_failure_identity(reply_to_msg_id=reply_to_msg_id, identity_id_hint=identity_id_hint)
    error_text = {
        "cooldown_start": "启阵冷却",
        "cooldown_assist": "助阵冷却",
        "duplicate_invite": "已有启阵邀请",
        "invite_failed": "助阵邀请已失效",
        "already_in": "已在阵中",
        "not_xinggong": "非星宫弟子",
        "help": "助阵指令帮助",
    }.get(kind, "周天星斗失败")
    if identity_id <= 0:
        return kind == "help"

    pending_invite_msg_id = int(get_identity_state(identity_id).get("formation_pending_invite_msg_id", 0) or 0)
    wait_sec = int((parsed or {}).get("wait_sec") or 0)
    if kind in {"cooldown_start", "cooldown_assist"}:
        until_ts = float(now or 0) + max(wait_sec, FORMATION_RECOVERY_DELAY_SEC) + CD_BUFFER_SEC
        _set_identity_cooldown(identity_id, until_ts, now, result=error_text, success=False)
    elif kind == "already_in":
        _set_identity_cooldown(
            identity_id,
            float(now or 0) + FORMATION_SUCCESS_COOLDOWN_SEC,
            now,
            result=error_text,
            success=True,
        )
    elif kind == "not_xinggong":
        with use_identity(identity_id):
            state["formation_enabled"] = False
            state["formation_last_error"] = error_text
            state["formation_pending_invite_msg_id"] = 0
            state["formation_pending_assist_msg_id"] = 0
            mark_dirty()
    else:
        _clear_identity_pending(identity_id)
        _set_identity_backoff(identity_id, now, error_text)

    run_state = _normalize_run_state()
    if pending_invite_msg_id > 0:
        _record_attempt(
            run_state,
            identity_id,
            pending_invite_msg_id,
            status="failed",
            reason=kind,
            message_id=int(message_id or 0),
            updated_at=float(now or 0),
        )
        if kind in {"invite_failed", "already_in"}:
            run_state.setdefault("active_invites", {}).pop(str(pending_invite_msg_id), None)
    run_state["last_error"] = error_text
    _save_run_state(run_state)
    return kind != "help"


def apply_formation_reply_snapshot(command_text, text, now, *, reply_to_msg_id=0, message_id=0, identity_id_hint=0, command_reply_to_msg_id=0, chat_id=0):
    parsed = _parse_formation_text(text)
    kind = parsed.get("kind")
    if kind == "unknown":
        return False
    if kind == "invite":
        return _record_invite(parsed, now, message_id=message_id, chat_id=chat_id)
    if kind == "success":
        return _record_success(parsed, now, message_id=message_id, reply_to_msg_id=reply_to_msg_id)
    return _record_failure(
        parsed,
        now,
        reply_to_msg_id=reply_to_msg_id,
        message_id=message_id,
        identity_id_hint=identity_id_hint,
        command_text=normalize_formation_command(command_text),
    )


async def handle_formation_event(text, now, event, reply_to=None, reply_context=None):
    if not is_formation_reply_text(text):
        return False
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if reply_to_msg_id <= 0:
        reply_header = getattr(event, "reply_to", None)
        reply_to_msg_id = int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    return apply_formation_reply_snapshot(
        str(getattr(reply_to, "raw_text", "") or ""),
        text,
        now,
        reply_to_msg_id=reply_to_msg_id,
        message_id=int(getattr(event, "id", 0) or 0),
        identity_id_hint=int((reply_context or {}).get("send_as_id") or 0),
        chat_id=int(getattr(event, "chat_id", 0) or 0),
    )


def _cleanup_run_state(now=None):
    current_time = float(now if now is not None else time.time())
    run_state = _normalize_run_state()
    changed = False
    invites = run_state.setdefault("active_invites", {})
    expired_invite_keys = set()
    for invite_key, invite in list(invites.items()):
        if not isinstance(invite, dict):
            invites.pop(invite_key, None)
            changed = True
            continue
        expire_at = float(invite.get("expire_at") or 0)
        if expire_at > 0 and current_time > expire_at:
            expired_invite_keys.add(str(invite_key))
        if expire_at > 0 and current_time > expire_at + 30:
            invites.pop(invite_key, None)
            changed = True

    attempts = run_state.setdefault("attempted_assists", {})
    cutoff = current_time - max(3600, FORMATION_INVITE_TTL_SEC * 10)
    for identity_key, identity_attempts in list(attempts.items()):
        if not isinstance(identity_attempts, dict):
            attempts.pop(identity_key, None)
            changed = True
            continue
        try:
            identity_id = int(identity_key)
        except (TypeError, ValueError):
            identity_id = 0
        for invite_key, attempt in list(identity_attempts.items()):
            updated_at = float((attempt or {}).get("updated_at") or 0)
            if updated_at <= cutoff:
                identity_attempts.pop(invite_key, None)
                changed = True
                continue
            if str((attempt or {}).get("status") or "") == "sent":
                deadline = float((attempt or {}).get("reply_deadline_at") or 0)
                if deadline > 0 and current_time > deadline:
                    if identity_id > 0:
                        _clear_identity_pending(identity_id)
                        _set_identity_backoff(identity_id, current_time, "助阵回复超时")
                    attempt["status"] = "failed"
                    attempt["reason"] = "assist_reply_timeout"
                    attempt["updated_at"] = current_time
                    changed = True
            elif str((attempt or {}).get("status") or "") == "scheduled" and str(invite_key) in expired_invite_keys:
                attempt["status"] = "failed"
                attempt["reason"] = "expired"
                attempt["updated_at"] = current_time
                changed = True
        if not identity_attempts:
            attempts.pop(identity_key, None)
            changed = True
    if changed:
        _save_run_state(run_state)
    return run_state


def _recover_timed_out_formation_replies(now):
    now = float(now or time.time())
    snapshot = _normalize_run_state()
    recovered = 0
    for identity_key, attempts in list((snapshot.get("attempted_assists") or {}).items()):
        try:
            identity_id = int(identity_key)
        except (TypeError, ValueError):
            continue
        for invite_key, attempt in list((attempts or {}).items()):
            if str((attempt or {}).get("status") or "") != "sent":
                continue
            deadline = float((attempt or {}).get("reply_deadline_at") or 0)
            if deadline <= 0 or now <= deadline:
                continue
            command_msg_id = int((attempt or {}).get("command_msg_id") or 0)
            try:
                invite_msg_id = int(invite_key)
            except (TypeError, ValueError):
                invite_msg_id = 0
            game_group_id = get_sent_message_chat_id(
                command_msg_id,
                default=get_game_group_id(),
                send_as_id=identity_id,
            )
            entries = find_message_log_replies(
                command_msg_id,
                now,
                lookback_sec=max(300, int(now - float((attempt or {}).get("sent_at") or deadline) + 60)),
                lookahead_sec=5,
                chat_id=game_group_id,
                predicate=lambda entry: str((entry or {}).get("event_type") or "") in {"message", "edit"},
            )
            invite_entry = find_message_log_message(
                invite_msg_id,
                now,
                lookback_sec=max(300, int(now - float((attempt or {}).get("updated_at") or deadline) + 60)),
                lookahead_sec=5,
                predicate=lambda entry: (
                    str((entry or {}).get("event_type") or "") in {"message", "edit"}
                    and int((entry or {}).get("chat_id") or 0) == game_group_id
                ),
            )
            if invite_entry:
                entries.append(invite_entry)
            for entry in entries:
                text = str((entry or {}).get("text") or "")
                if not is_formation_reply_text(text):
                    continue
                before_pending = int(get_identity_state(identity_id).get("formation_pending_assist_msg_id", 0) or 0)
                apply_formation_reply_snapshot(
                    CMD_FORMATION_ASSIST,
                    text,
                    float((entry or {}).get("ts_epoch") or now),
                    reply_to_msg_id=int((entry or {}).get("reply_to_msg_id") or command_msg_id),
                    message_id=int((entry or {}).get("message_id") or 0),
                    identity_id_hint=identity_id,
                    chat_id=int((entry or {}).get("chat_id") or 0),
                )
                after_pending = int(get_identity_state(identity_id).get("formation_pending_assist_msg_id", 0) or 0)
                if before_pending > 0 and after_pending <= 0:
                    recovered += 1
                    break
    return recovered


def _is_current_identity_assist_ready(now):
    identity_id = int(get_current_identity_id() or 0)
    if identity_id <= 0 or not has_identity(identity_id):
        return False
    if not get_identity_enabled(identity_id):
        return False
    if int(get_identity_account(identity_id) or 0) <= 0:
        return False
    if not is_module_available("周天星斗", identity_id):
        return False
    if has_active_reply_dispatch(identity_id, family="formation"):
        return False
    if not state.get("formation_enabled"):
        return False
    if int(state.get("formation_pending_assist_msg_id", 0) or 0) > 0:
        return False
    wait_until = max(float(state.get("next_formation_time", 0) or 0), float(state.get("formation_cooldown_until", 0) or 0))
    return wait_until <= float(now or 0)


def _open_external_invites(run_state, now):
    result = []
    for invite in (run_state.get("active_invites") or {}).values():
        if not isinstance(invite, dict):
            continue
        msg_id = int(invite.get("msg_id") or 0)
        expire_at = float(invite.get("expire_at") or 0)
        owner_username = _normalize_username(invite.get("owner_username"))
        if msg_id <= 0 or expire_at <= float(now or 0) or not owner_username:
            continue
        if owner_username in _local_usernames():
            continue
        result.append(invite)
    result.sort(key=lambda item: float(item.get("created_at") or 0))
    return result


async def _send_assist(identity_id, invite, now, run_state):
    invite_msg_id = int(invite.get("msg_id") or 0)
    route_kwargs = {}
    invite_chat_id = int(invite.get("chat_id") or 0)
    if invite_chat_id:
        route_kwargs["target_chat_id"] = invite_chat_id
    msg = await send_game_command(
        CMD_FORMATION_ASSIST,
        track=False,
        reply_to=invite_msg_id,
        send_as_id=identity_id,
        priority="urgent_reactive",
        source_module="周天星斗",
        **route_kwargs,
    )
    if not msg:
        _record_attempt(run_state, identity_id, invite_msg_id, status="failed", reason="send_failed", updated_at=float(now or 0))
        _set_identity_backoff(identity_id, now, "助阵发送失败")
        _save_run_state(run_state)
        save_state()
        return False
    msg_id = int(getattr(msg, "id", 0) or 0)
    _record_attempt(
        run_state,
        identity_id,
        invite_msg_id,
        status="sent",
        command_msg_id=msg_id,
        sent_at=float(now or 0),
        reply_deadline_at=float(now or 0) + FORMATION_ASSIST_REPLY_TIMEOUT_SEC,
        updated_at=float(now or 0),
    )
    state["formation_pending_invite_msg_id"] = invite_msg_id
    state["formation_pending_assist_msg_id"] = msg_id
    state["last_formation_msg_id"] = msg_id
    state["formation_last_action"] = "已助阵外部邀请"
    state["formation_last_error"] = ""
    state["next_formation_time"] = float(now or 0) + FORMATION_ASSIST_REPLY_TIMEOUT_SEC
    mark_dirty()
    _save_run_state(run_state)
    save_state()
    console_log(f"🌌 周天星斗助阵已发送：{get_identity_display_name(identity_id)} -> msg {invite_msg_id}", scope="identity", send_as_id=identity_id)
    return True


async def run_formation_scheduler(now):
    _recover_timed_out_formation_replies(now)
    run_state = _cleanup_run_state(now)
    if not _is_current_identity_assist_ready(now):
        return
    identity_id = int(get_current_identity_id() or 0)
    for invite in _open_external_invites(run_state, now):
        invite_msg_id = int(invite.get("msg_id") or 0)
        attempt = _get_attempt(run_state, identity_id, invite_msg_id)
        status = str((attempt or {}).get("status") or "")
        if status in {"sent", "failed", "success"}:
            continue
        if status != "scheduled":
            due_at = float(now or 0) + _formation_delay()
            _record_attempt(run_state, identity_id, invite_msg_id, status="scheduled", due_at=due_at, updated_at=float(now or 0))
            state["next_formation_time"] = due_at
            state["formation_last_action"] = "已安排助阵"
            state["formation_last_error"] = ""
            mark_dirty()
            _save_run_state(run_state)
            save_state()
            return
        due_at = float(attempt.get("due_at") or 0)
        if due_at > float(now or 0):
            return
        if float(invite.get("expire_at") or 0) <= float(now or 0):
            _record_attempt(run_state, identity_id, invite_msg_id, status="failed", reason="expired", updated_at=float(now or 0))
            _save_run_state(run_state)
            save_state()
            continue
        await _send_assist(identity_id, invite, now, run_state)
        return


def clear_formation_state():
    identity_id = int(get_current_identity_id() or 0)
    state["next_formation_time"] = 0
    state["formation_cooldown_until"] = 0
    state["last_formation_msg_id"] = 0
    state["formation_pending_invite_msg_id"] = 0
    state["formation_pending_assist_msg_id"] = 0
    state["formation_last_action"] = ""
    state["formation_last_result"] = ""
    state["formation_last_error"] = ""
    state["formation_last_success_at"] = 0
    run_state = _normalize_run_state()
    if identity_id > 0:
        run_state.setdefault("attempted_assists", {}).pop(str(identity_id), None)
        _save_run_state(run_state)
    mark_dirty()


def _get_current_sendable_text(now):
    identity_id = int(get_current_identity_id() or 0)
    if identity_id <= 0:
        return "否（未选择身份）"
    if not state.get("formation_enabled"):
        return "否（模块未开启）"
    if not get_identity_enabled(identity_id):
        return "否（身份已暂停）"
    if int(get_identity_account(identity_id) or 0) <= 0:
        return "否（身份未绑定账号）"
    if not is_module_available("周天星斗", identity_id):
        return "否（非星宫或宗门未知）"
    wait_until = max(float(state.get("next_formation_time", 0) or 0), float(state.get("formation_cooldown_until", 0) or 0))
    if wait_until > float(now or 0):
        return f"否（等待中，{fmt_remaining(wait_until)}）"
    return "是"


def get_formation_status_text():
    now = time.time()
    run_state = _cleanup_run_state(now)
    cooldown_until = float(state.get("formation_cooldown_until", 0) or 0)
    next_time = float(state.get("next_formation_time", 0) or 0)
    pending_invite = int(state.get("formation_pending_invite_msg_id", 0) or 0)
    pending_assist = int(state.get("formation_pending_assist_msg_id", 0) or 0)
    lines = [
        "🌌 周天星斗",
        "- 模式：被动助阵（不主动启阵）",
        f"- 开关：{'已开启' if state.get('formation_enabled') else '已关闭'}",
        f"- 可助阵：{_get_current_sendable_text(now)}",
        f"- 冷却至：{fmt_abs_ts(cooldown_until)}（{fmt_remaining(cooldown_until)}）",
        f"- 下次检查：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）",
        f"- 当前动作：{state.get('formation_last_action') or '空闲'}",
        f"- 待邀请消息：{pending_invite or '无'}",
        f"- 待助阵消息：{pending_assist or '无'}",
        f"- 已知外部邀请：{len(_open_external_invites(run_state, now))}",
        f"- 最近结果：{state.get('formation_last_result') or '未记录'}",
        f"- 最近错误：{state.get('formation_last_error') or '无'}",
    ]
    return "\n".join(lines)


__all__ = [
    "apply_formation_reply_snapshot",
    "clear_formation_state",
    "get_formation_status_text",
    "handle_formation_event",
    "is_formation_reply_text",
    "normalize_formation_command",
    "run_formation_scheduler",
]
