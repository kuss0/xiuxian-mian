import asyncio
import json
import os
import random
import secrets
import time
import traceback
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from telethon import functions, types

from .config import (
    CMD_BATTLE_POWER,
    CMD_CHECKIN,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_IDENTITY_INFO,
    CMD_PET,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_TREE_GUARD,
    CMD_TREE_HARVEST,
    CMD_TREE_STATUS,
    CMD_TREE_WATER,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    LOG_BOT_TOKEN,
    LOG_GROUP_ID,
    LOG_SEND_MODE,
    MESSAGES_DIR,
    MY_MSG_MAX,
    MY_MSG_TTL,
    RETRY_LIMIT,
    RETRY_MAX_SEC,
    RETRY_MIN_SEC,
    SCRIPT_COMMANDS,
    TZ_LOCAL,
    UI_AUTH_IDLE_TIMEOUT_SEC,
    UI_AUTH_SESSION_TIMEOUT_SEC,
    UI_PUBLIC_BASE_URL,
    client,
    get_all_clients,
    get_client,
    is_identity_refresh_command_text,
)
from .persistence import mark_dirty
from .state import (
    get_active_identity_id,
    get_current_identity_id,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_label,
    has_active_identity_context,
    is_auto_delete_sent_messages_enabled,
    state,
    use_identity,
)


def _get_any_authed_client():
    """返回任意一个已认证的 client（优先账号 client，回退主 client）"""
    _all = get_all_clients()
    return next(iter(_all.values())) if _all else client


def _get_identity_client(send_as_id=None):
    """根据 identity 返回对应的已认证 client"""
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    account_id = get_identity_account(send_as_id)
    if account_id:
        return get_client(account_id)
    return _get_any_authed_client()


_background_tasks = set()
_ui_login_tokens = {}
_reply_chain_tracker = {}


REPLY_FAMILY_COMMANDS = {
    "checkin": {CMD_CHECKIN},
    "sect_teach": {CMD_SECT_TEACH},
    "tower": {CMD_TOWER},
    "pet": {CMD_PET},
    "tree_panel": {CMD_TREE_WATER, CMD_TREE_STATUS},
    "tree_guard": {CMD_TREE_GUARD},
    "tree_harvest": {CMD_TREE_HARVEST},
    "yuanying": {CMD_YUANYING, CMD_YUANYING_STATUS},
    "deep_retreat": {CMD_DEEP_RETREAT, CMD_DEEP_RETREAT_QUERY},
}
COMMAND_TO_REPLY_FAMILY = {
    command: family
    for family, commands in REPLY_FAMILY_COMMANDS.items()
    for command in commands
}


def _append_sent_message_log(msg_id, command, send_as_id, reply_to_msg_id=0):
    try:
        now = datetime.now(TZ_LOCAL)
        log_file = os.path.join(MESSAGES_DIR, f"{now.strftime('%Y-%m-%d')}.log")
        payload = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC+8"),
            "event_type": "sent",
            "message_id": int(msg_id or 0),
            "chat_id": get_game_group_id(),
            "sender_id": int(send_as_id or 0),
            "topic_id": get_game_topic_id(),
            "reply_to_msg_id": int(reply_to_msg_id or 0),
            "text": command or "",
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        traceback.print_exc()
_ui_sessions = {}
IDENTITY_INFO_REFRESH_ERROR_TEXT = "获取失败，请手动重新获取"


def _is_identity_refresh_command(command):
    return is_identity_refresh_command_text(command)


def _fire_and_forget(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _secure_lookup(store, token):
    token = (token or "").strip()
    if not token:
        return None, None
    for stored_token, payload in store.items():
        if secrets.compare_digest(stored_token, token):
            return stored_token, payload
    return None, None


def _new_runtime_token(store):
    while True:
        token = secrets.token_urlsafe(32)
        if token not in store:
            return token


def is_script_command_text(text):
    raw_text = (text or "").strip()
    if not raw_text:
        return False
    return any(raw_text == cmd or raw_text.startswith(f"{cmd} ") for cmd in SCRIPT_COMMANDS)


def _gc_reply_chain_tracker(now=None):
    now = float(now if now is not None else time.time())
    expired_msg_ids = [
        msg_id
        for msg_id, payload in _reply_chain_tracker.items()
        if now - float((payload or {}).get("tracked_at", 0) or 0) > MY_MSG_TTL
    ]
    for msg_id in expired_msg_ids:
        _reply_chain_tracker.pop(msg_id, None)


def resolve_reply_family(command):
    raw_command = str(command or "").strip()
    if not raw_command:
        return None
    for prefix, family in COMMAND_TO_REPLY_FAMILY.items():
        if raw_command == prefix or raw_command.startswith(f"{prefix} "):
            return family
    return None


def get_reply_family_commands(family):
    return set(REPLY_FAMILY_COMMANDS.get(str(family or "").strip(), set()))


def _get_special_tracked_message_family(identity_state, msg_id):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None
    if msg_id == int(identity_state.get("last_checkin_msg_id", 0) or 0):
        return "checkin"
    if msg_id == int(identity_state.get("last_sect_teach_msg_id", 0) or 0):
        return "sect_teach"
    if msg_id == int(identity_state.get("last_tower_msg_id", 0) or 0):
        return "tower"
    if msg_id == int(identity_state.get("last_identity_info_msg_id", 0) or 0):
        return "identity_info"
    tracked_identity_info_ids = {
        int(tracked_msg_id or 0)
        for tracked_msg_id in identity_state.get("identity_info_reply_msg_ids", [])
    }
    tracked_identity_info_ids.discard(0)
    if msg_id in tracked_identity_info_ids:
        return "identity_info"
    return None


def _resolve_identity_message_owner(msg_id, send_as_id=None):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return None, None

    _gc_reply_chain_tracker()
    tracker_payload = _reply_chain_tracker.get(msg_id)
    tracked_identity_id = int((tracker_payload or {}).get("send_as_id", 0) or 0)
    if tracked_identity_id > 0 and (send_as_id is None or int(send_as_id) == tracked_identity_id):
        return tracked_identity_id, "reply_chain_tracker"

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    for identity_id in target_ids:
        identity_state = get_identity_state(identity_id)
        if msg_id in identity_state["my_msg_ids"]:
            return identity_id, "my_msg_ids"
        if _get_special_tracked_message_family(identity_state, msg_id):
            return identity_id, "tracked_ids"
    return None, None


def _resolve_identity_message_family(msg_id, send_as_id):
    msg_id = int(msg_id or 0)
    send_as_id = int(send_as_id or 0)
    if msg_id <= 0 or send_as_id <= 0:
        return None, 0

    _gc_reply_chain_tracker()
    tracker_payload = _reply_chain_tracker.get(msg_id)
    if tracker_payload and int(tracker_payload.get("send_as_id", 0) or 0) == send_as_id:
        return tracker_payload.get("family") or None, int(tracker_payload.get("root_msg_id", 0) or msg_id)

    identity_state = get_identity_state(send_as_id)
    pending_item = identity_state.get("pending_tasks", {}).get(msg_id)
    if pending_item:
        return resolve_reply_family(pending_item.get("cmd")), msg_id

    special_family = _get_special_tracked_message_family(identity_state, msg_id)
    if special_family:
        return special_family, msg_id

    return None, msg_id


def get_reply_context(reply_to=None, *, reply_to_msg_id=None, send_as_id=None):
    resolved_reply_to_msg_id = int(reply_to_msg_id or getattr(reply_to, "id", 0) or 0)
    if resolved_reply_to_msg_id <= 0:
        return {
            "send_as_id": None,
            "family": None,
            "reply_to_msg_id": 0,
            "matched_via": "none",
            "root_msg_id": 0,
        }

    resolved_send_as_id, matched_via = _resolve_identity_message_owner(resolved_reply_to_msg_id, send_as_id=send_as_id)
    family = None
    root_msg_id = resolved_reply_to_msg_id
    if resolved_send_as_id is not None:
        family, root_msg_id = _resolve_identity_message_family(resolved_reply_to_msg_id, resolved_send_as_id)
    if family is None and reply_to is not None:
        family = resolve_reply_family(getattr(reply_to, "raw_text", ""))
    if family is None and resolved_send_as_id is not None and reply_to is not None:
        identity_state = get_identity_state(resolved_send_as_id)
        reply_text = str(getattr(reply_to, "raw_text", "") or "").strip()
        if reply_text:
            for pending in identity_state.get("pending_tasks", {}).values():
                pending_cmd = str((pending or {}).get("cmd") or "").strip()
                if pending_cmd and pending_cmd in reply_text:
                    family = resolve_reply_family(pending_cmd)
                    break

    return {
        "send_as_id": resolved_send_as_id,
        "family": family,
        "reply_to_msg_id": resolved_reply_to_msg_id,
        "matched_via": matched_via or ("reply_header" if reply_to is None else "reply_object"),
        "root_msg_id": int(root_msg_id or resolved_reply_to_msg_id),
    }


def track_reply_chain_message(msg_id, send_as_id, family, *, root_msg_id=None):
    msg_id = int(msg_id or 0)
    send_as_id = int(send_as_id or 0)
    family = str(family or "").strip()
    root_msg_id = int(root_msg_id or 0) or msg_id
    if msg_id <= 0 or send_as_id <= 0 or not family:
        return False
    _gc_reply_chain_tracker()
    _reply_chain_tracker[msg_id] = {
        "send_as_id": send_as_id,
        "family": family,
        "root_msg_id": root_msg_id,
        "tracked_at": time.time(),
    }
    return True


def _clear_pending_tasks_by_commands_locked(commands):
    commands = {str(command or "").strip() for command in commands if str(command or "").strip()}
    if not commands:
        return []

    families = {resolve_reply_family(command) for command in commands}
    families.discard(None)
    remove_ids = []
    for msg_id, pending in state.get("pending_tasks", {}).items():
        pending_cmd = str((pending or {}).get("cmd") or "").strip()
        pending_family = resolve_reply_family(pending_cmd)
        if pending_cmd in commands or (pending_family and pending_family in families):
            remove_ids.append(msg_id)
    for msg_id in remove_ids:
        state["pending_tasks"].pop(msg_id, None)
    return remove_ids


def clear_pending_tasks_by_commands(commands, send_as_id=None):
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    removed_ids = []
    changed = False
    for identity_id in target_ids:
        with use_identity(identity_id):
            current_removed_ids = _clear_pending_tasks_by_commands_locked(commands)
            if current_removed_ids:
                changed = True
                removed_ids.extend(current_removed_ids)
    if changed:
        mark_dirty()
    return removed_ids


def _send_log_group_via_bot(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True, parse_mode=None):
    if not LOG_BOT_TOKEN:
        return False, "missing bot token"
    payload = {
        "chat_id": str(LOG_GROUP_ID),
        "text": text,
        "disable_web_page_preview": "true" if not link_preview else "false",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if int(reply_to_msg_id or 0) > 0:
        payload["reply_to_message_id"] = int(reply_to_msg_id)
        payload["allow_sending_without_reply"] = "true"
    if int(message_thread_id or 0) > 0:
        payload["message_thread_id"] = int(message_thread_id)
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    try:
        from urllib.error import HTTPError
        with urlopen(url, data=urlencode(payload).encode("utf-8"), timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, str(e)
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("ok") is True:
        return True, ""
    return False, body or "bot api returned non-ok response"


async def _send_log_group_message(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True, parse_mode=None):
    if LOG_SEND_MODE == "bot":
        try:
            ok, error_text = await asyncio.to_thread(
                _send_log_group_via_bot,
                text,
                reply_to_msg_id=reply_to_msg_id,
                message_thread_id=message_thread_id,
                link_preview=link_preview,
                parse_mode=parse_mode,
            )
            if ok:
                return True
            print(f"_send_log_group_message bot fallback: {error_text} | text={text}")
        except Exception as e:
            print(f"_send_log_group_message bot failed: {e} | text={text}")
    try:
        _fb = _get_any_authed_client()
        await _fb.send_message(
            LOG_GROUP_ID,
            text,
            reply_to=int(reply_to_msg_id or 0) or None,
            link_preview=link_preview,
            parse_mode=parse_mode or None,
        )
        return True
    except Exception as e:
        print(f"_send_log_group_message account failed: {e} | text={text}")
        return False


def mono(text):
    """将文本包裹为 HTML monospace 格式，防止 Telegram @提及"""
    from html import escape
    return f"<code>{escape(str(text))}</code>"


def _truncate_log_text(text, limit=220):
    raw = str(text or "").strip()
    if not raw or len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _resolve_log_identity(scope="auto", send_as_id=None):
    resolved_scope = (scope or "auto").strip().lower()
    if resolved_scope == "global":
        return None
    if send_as_id is not None:
        try:
            return int(send_as_id)
        except (TypeError, ValueError):
            return None
    if resolved_scope == "identity":
        active_identity_id = get_active_identity_id()
        if active_identity_id is not None:
            return active_identity_id
        current_identity_id = int(get_current_identity_id() or 0)
        return current_identity_id or None
    if has_active_identity_context():
        return get_active_identity_id()
    return None


def _format_log_identity_prefix(send_as_id, *, html=False):
    if send_as_id is None:
        return ""
    label = _truncate_log_text(get_send_as_label(send_as_id), limit=32)
    if not label:
        return ""
    return f"[{mono(label) if html else label}] "


def _format_log_message(content, *, scope="auto", send_as_id=None, html=False, limit=220):
    text = _truncate_log_text(content, limit=limit)
    identity_id = _resolve_log_identity(scope=scope, send_as_id=send_as_id)
    prefix = _format_log_identity_prefix(identity_id, html=html)
    return f"{prefix}{text}" if prefix else text


async def send_audit_log(content, *, scope="auto", send_as_id=None, limit=220):
    now = datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
    message_body = _format_log_message(content, scope=scope, send_as_id=send_as_id, html=True, limit=limit)
    message = f"【🍃 监控日志 {now}】\n{message_body}"
    ok = await _send_log_group_message(message, link_preview=False, parse_mode="HTML")
    if not ok:
        print(f"send_audit_log failed | content={_truncate_log_text(content, limit=240)}")
    return ok


def console_log(content, *, scope="auto", send_as_id=None, limit=180):
    ts = datetime.now(TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")
    message = _format_log_message(content, scope=scope, send_as_id=send_as_id, limit=limit)
    print(f"[{ts}] {message}")


async def reply_log_group_message(event, text, *, audit_on_error=True, error_prefix="❌ 日志群回复失败", link_preview=True, scope="global", send_as_id=None, limit=350):
    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    # forum 群需要 message_thread_id 才能回复
    reply_header = getattr(event, "reply_to", None)
    thread_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0) or int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    message = _format_log_message(text, scope=scope, send_as_id=send_as_id, limit=limit)
    ok = await _send_log_group_message(message, reply_to_msg_id=reply_to_msg_id, message_thread_id=thread_id, link_preview=link_preview)
    if ok:
        return True
    print(f"reply_log_group_message failed | text={_truncate_log_text(text, limit=240)}")
    if audit_on_error:
        await send_audit_log(error_prefix, scope="global")
    return False


def _map_forum_topics_error(error):
    error_text = str(error or "").strip()
    error_code = error_text.upper()
    if "CHANNEL_FORUM_MISSING" in error_code or ("FORUM" in error_code and "MISSING" in error_code):
        return "该群未开启话题功能"
    if any(code in error_code for code in {"CHANNEL_INVALID", "CHANNEL_PRIVATE", "CHAT_ID_INVALID", "PEER_ID_INVALID"}):
        return "游戏群聊不存在或当前账号无权访问"
    if "TOPIC" in error_code and "INVALID" in error_code:
        return "话题接口返回无效结果"
    return error_text or "读取话题列表失败"


async def fetch_forum_topics(group_id):
    raw_group_id = str(group_id or "").strip()
    if not raw_group_id:
        return False, "游戏群聊 ID 不能为空", []
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        return False, "游戏群聊 ID 必须是整数", []
    if group_id == 0:
        return False, "游戏群聊 ID 不能为 0", []

    try:
        _tc = _get_any_authed_client()
        peer = await _tc.get_input_entity(group_id)
        entity = await _tc.get_entity(peer)
    except Exception:
        return False, "游戏群聊不存在或当前账号无权访问", []

    request_cls = getattr(getattr(functions, "channels", None), "GetForumTopicsRequest", None)
    if request_cls is None:
        request_cls = getattr(getattr(functions, "messages", None), "GetForumTopicsRequest", None)
    if request_cls is None:
        return False, "当前 Telethon 版本不支持自动读取话题列表", []

    group_title = str(getattr(entity, "title", "") or "").strip() or str(group_id)
    if not bool(getattr(entity, "forum", False)):
        return False, f"群聊[{group_title}]未开启话题功能", []

    request_kwargs = {
        "q": "",
        "offset_date": None,
        "offset_id": 0,
        "offset_topic": 0,
        "limit": 100,
    }
    request = None
    for peer_key in ("channel", "peer"):
        try:
            request = request_cls(**{peer_key: peer, **request_kwargs})
            break
        except TypeError as e:
            if f"unexpected keyword argument '{peer_key}'" not in str(e):
                return False, _map_forum_topics_error(e), []
    if request is None:
        return False, "当前 Telethon 版本不支持自动读取话题列表", []

    try:
        result = await _tc(request)
    except Exception as e:
        return False, _map_forum_topics_error(e), []

    topics = []
    seen_topic_ids = set()
    for topic in getattr(result, "topics", None) or []:
        topic_id = int(getattr(topic, "id", 0) or 0)
        if topic_id <= 0 or topic_id in seen_topic_ids:
            continue
        seen_topic_ids.add(topic_id)
        title = str(getattr(topic, "title", "") or "").strip() or f"话题 {topic_id}"
        if bool(getattr(topic, "hidden", False)):
            title = f"{title}（已隐藏）"
        elif bool(getattr(topic, "closed", False)):
            title = f"{title}（已关闭）"
        topics.append({
            "id": topic_id,
            "title": title,
            "top_message": int(getattr(topic, "top_message", 0) or 0),
        })

    topics.sort(key=lambda item: item["id"])
    return True, f"已读取群聊[{group_title}]的话题列表，共 {len(topics)} 个", topics


def issue_ui_login_token(sender_id, now=None):
    if now is None:
        now = time.time()
    token = _new_runtime_token(_ui_login_tokens)
    _ui_login_tokens[token] = {
        "sender_id": int(sender_id) if sender_id is not None else 0,
        "created_at": now,
        "last_seen_at": now,
    }
    return token


def build_ui_login_url(token):
    return f"{UI_PUBLIC_BASE_URL}/#token={quote((token or '').strip(), safe='')}"


def redeem_ui_login_token(token, now=None):
    if now is None:
        now = time.time()
    stored_token, payload = _secure_lookup(_ui_login_tokens, token)
    if not stored_token or not payload:
        return None
    if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC:
        _ui_login_tokens.pop(stored_token, None)
        return None

    _ui_login_tokens.pop(stored_token, None)
    session_token = _new_runtime_token(_ui_sessions)
    _ui_sessions[session_token] = {
        "sender_id": int(payload.get("sender_id") or 0),
        "created_at": now,
        "last_seen_at": now,
        "seen_startup_alert_keys": [],
    }
    return session_token


def validate_ui_session(session_token, now=None):
    if now is None:
        now = time.time()
    stored_token, payload = _secure_lookup(_ui_sessions, session_token)
    if not stored_token or not payload:
        return None
    if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_SESSION_TIMEOUT_SEC:
        _ui_sessions.pop(stored_token, None)
        return None
    return {
        "session_token": stored_token,
        **payload,
    }


def touch_ui_session(session_token, now=None):
    if now is None:
        now = time.time()
    session = validate_ui_session(session_token, now)
    if not session:
        return None
    _ui_sessions[session["session_token"]]["last_seen_at"] = now
    session["last_seen_at"] = now
    return session


def gc_ui_login_tokens(now=None):
    if now is None:
        now = time.time()
    expired = [
        token
        for token, payload in _ui_login_tokens.items()
        if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC
    ]
    for token in expired:
        _ui_login_tokens.pop(token, None)
    return len(expired)


def gc_ui_sessions(now=None):
    if now is None:
        now = time.time()
    expired = [
        token
        for token, payload in _ui_sessions.items()
        if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_SESSION_TIMEOUT_SEC
    ]
    for token in expired:
        _ui_sessions.pop(token, None)
    return len(expired)


def clear_ui_auth_state():
    _ui_login_tokens.clear()
    _ui_sessions.clear()


def consume_unseen_startup_alerts(session_token, alerts):
    session = validate_ui_session(session_token)
    if not session:
        return []
    stored_session = _ui_sessions.get(session["session_token"], {})
    seen_keys = {
        str(alert_key)
        for alert_key in stored_session.get("seen_startup_alert_keys", [])
        if str(alert_key).strip()
    }
    unseen_alerts = []
    for alert in alerts or []:
        alert_key = str((alert or {}).get("key") or "").strip()
        if not alert_key or alert_key in seen_keys:
            continue
        unseen_alerts.append(alert)
        seen_keys.add(alert_key)
    stored_session["seen_startup_alert_keys"] = sorted(seen_keys)
    return unseen_alerts


def _extract_sent_message_id(result):
    direct_msg_id = int(getattr(result, "id", 0) or 0)
    if direct_msg_id > 0:
        return direct_msg_id

    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id > 0:
            return message_id
        update_id = int(getattr(update, "id", 0) or 0)
        if update_id > 0:
            return update_id
    return 0


async def send_game_command(command, track=True, reply_to=None, send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    topic_id = get_game_topic_id()

    try:
        account_id = get_identity_account(send_as_id)
        if account_id:
            active_client = get_client(account_id)
        else:
            active_client = _get_any_authed_client()
        game_group_id = get_game_group_id()
        if not game_group_id:
            raise ValueError("游戏群聊 ID 未配置，请在 UI 基础配置中设置")
        try:
            peer = await active_client.get_input_entity(game_group_id)
        except ValueError:
            await active_client.get_dialogs()
            peer = await active_client.get_input_entity(game_group_id)
        send_as_peer = await active_client.get_input_entity(send_as_id)
        reply_to_spec = None
        if reply_to:
            reply_to_spec = types.InputReplyToMessage(
                reply_to_msg_id=int(reply_to),
                top_msg_id=int(topic_id or 0) or None,
            )
        elif topic_id > 0:
            reply_to_spec = types.InputReplyToMessage(reply_to_msg_id=int(topic_id))
        result = await active_client(
            functions.messages.SendMessageRequest(
                peer=peer,
                message=command,
                reply_to=reply_to_spec,
                send_as=send_as_peer,
            )
        )
        msg_id = _extract_sent_message_id(result)
        if msg_id <= 0:
            raise ValueError("无法从发送结果中解析消息 ID")
        msg = SimpleNamespace(id=msg_id)
        _append_sent_message_log(msg_id, command, send_as_id, reply_to_msg_id=int(reply_to or 0))
        with use_identity(send_as_id) as identity_state:
            sent_at = time.time()
            identity_state["my_msg_ids"][msg_id] = sent_at
            if track:
                identity_state["pending_tasks"][msg_id] = {
                    "cmd": command,
                    "sent_at": sent_at,
                    "retry": 0,
                    "timeout": random.randint(RETRY_MIN_SEC, RETRY_MAX_SEC),
                    "reply_to_msg_id": int(reply_to or 0),
                }
            mark_dirty()
        family = resolve_reply_family(command)
        if family:
            track_reply_chain_message(msg_id, send_as_id, family, root_msg_id=msg_id)
        return msg
    except Exception as e:
        await send_audit_log(
            (
                f"❌ 指令发送失败：{_truncate_log_text(command, limit=48)} | "
                f"{_truncate_log_text(e, limit=72)} | "
                f"acc={get_identity_account(send_as_id)} group={get_game_group_id()} topic={topic_id}"
            ),
            scope="identity",
            send_as_id=send_as_id,
            limit=240,
        )
        return None


def _get_tracked_identity_message_ids(identity_state):
    tracked_ids = {
        int(identity_state.get("last_checkin_msg_id", 0) or 0),
        int(identity_state.get("last_sect_teach_msg_id", 0) or 0),
        int(identity_state.get("last_tower_msg_id", 0) or 0),
        int(identity_state.get("last_identity_info_msg_id", 0) or 0),
        *(int(msg_id or 0) for msg_id in identity_state.get("identity_info_reply_msg_ids", [])),
    }
    tracked_ids.discard(0)
    return tracked_ids


def find_identity_by_msg_id(msg_id):
    resolved_send_as_id, _matched_via = _resolve_identity_message_owner(msg_id)
    return resolved_send_as_id


def is_reply_to_identity_message(reply_to, send_as_id):
    if not reply_to:
        return False
    resolved_send_as_id, _matched_via = _resolve_identity_message_owner(getattr(reply_to, "id", 0), send_as_id=send_as_id)
    return resolved_send_as_id == int(send_as_id or 0)


def gc_my_msg_ids(now=None, send_as_id=None):
    if now is None:
        now = time.time()

    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    changed = False
    for identity_id in target_ids:
        with use_identity(identity_id) as identity_state:
            expired_ids = [msg_id for msg_id, sent_at in identity_state["my_msg_ids"].items() if now - sent_at > MY_MSG_TTL]
            if expired_ids:
                changed = True
                for msg_id in expired_ids:
                    identity_state["my_msg_ids"].pop(msg_id, None)

            if len(identity_state["my_msg_ids"]) > MY_MSG_MAX:
                sorted_items = sorted(identity_state["my_msg_ids"].items(), key=lambda x: x[1], reverse=True)
                trimmed_items = dict(sorted_items[:MY_MSG_MAX])
                if trimmed_items != identity_state["my_msg_ids"]:
                    identity_state["my_msg_ids"] = trimmed_items
                    changed = True
    _gc_reply_chain_tracker(now)
    if changed:
        mark_dirty()


def clear_pending_by_reply(reply_to=None, send_as_id=None, reply_context=None):
    if reply_context is None:
        reply_context = get_reply_context(reply_to, send_as_id=send_as_id)

    resolved_send_as_id = int((reply_context or {}).get("send_as_id") or 0)
    family = (reply_context or {}).get("family") or None
    reply_to_msg_id = int((reply_context or {}).get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0)
    if resolved_send_as_id <= 0 or reply_to_msg_id <= 0:
        return {"send_as_id": None, "family": family, "removed_ids": [], "matched": False}

    removed_ids = []
    with use_identity(resolved_send_as_id):
        if reply_to_msg_id in state["pending_tasks"]:
            state["pending_tasks"].pop(reply_to_msg_id, None)
            removed_ids.append(reply_to_msg_id)

        if family:
            family_commands = get_reply_family_commands(family)
            for msg_id, pending in list(state["pending_tasks"].items()):
                pending_cmd = str((pending or {}).get("cmd") or "").strip()
                if pending_cmd in family_commands or resolve_reply_family(pending_cmd) == family:
                    state["pending_tasks"].pop(msg_id, None)
                    removed_ids.append(msg_id)

        if removed_ids:
            mark_dirty()

    unique_removed_ids = sorted({int(msg_id) for msg_id in removed_ids if int(msg_id or 0) > 0})
    return {
        "send_as_id": resolved_send_as_id,
        "family": family,
        "removed_ids": unique_removed_ids,
        "matched": bool(unique_removed_ids or family),
    }


def _is_pending_consumed(identity_state, msg_id, family):
    msg_id = int(msg_id or 0)
    if msg_id <= 0:
        return True
    if msg_id not in identity_state.get("pending_tasks", {}):
        return True
    if family:
        family_commands = get_reply_family_commands(family)
        if not any(
            (str((pending or {}).get("cmd") or "").strip() in family_commands)
            or (resolve_reply_family((pending or {}).get("cmd")) == family)
            for pending in identity_state.get("pending_tasks", {}).values()
        ):
            return True
    return False


def _refresh_identity_info_retry_tracking(identity_state, new_msg_id, now):
    if new_msg_id <= 0:
        return
    tracked_ids = {
        *(int(tracked_id or 0) for tracked_id in identity_state.get("identity_info_reply_msg_ids", [])),
        new_msg_id,
    }
    tracked_ids.discard(0)
    identity_state["last_identity_info_msg_id"] = new_msg_id
    identity_state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
    identity_state["identity_info_followup_due_at"] = 0
    identity_state["identity_info_last_requested_at"] = now


async def run_retry_scheduler(now, send_as_id=None):
    target_ids = [int(send_as_id)] if send_as_id is not None else get_identity_ids()
    for identity_id in target_ids:
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id) as identity_state:
            retry_items = list(identity_state["pending_tasks"].items())
        for msg_id, item in retry_items:
            cmd = item["cmd"]
            send_time = item["sent_at"]
            threshold = item["timeout"]
            retry = item["retry"]
            family = resolve_reply_family(cmd)

            if now - send_time <= threshold:
                continue

            with use_identity(identity_id) as identity_state:
                current_item = identity_state["pending_tasks"].get(msg_id)
                if not current_item:
                    continue
                if _is_pending_consumed(identity_state, msg_id, family):
                    identity_state["pending_tasks"].pop(msg_id, None)
                    mark_dirty()
                    continue
                retry = int(current_item.get("retry", retry) or 0)
                cmd = current_item.get("cmd") or cmd

                if retry >= RETRY_LIMIT:
                    await send_audit_log(
                        f"🧯 指令 {mono(_truncate_log_text(cmd, limit=40))} 重试 {RETRY_LIMIT} 次仍无响应，已停补发。",
                        scope="identity",
                        send_as_id=identity_id,
                    )
                    if _is_identity_refresh_command(cmd):
                        identity_state["last_identity_info_msg_id"] = 0
                        identity_state["identity_info_reply_msg_ids"] = []
                        identity_state["identity_info_followup_due_at"] = 0
                        identity_state["identity_info_last_error"] = IDENTITY_INFO_REFRESH_ERROR_TEXT
                    identity_state["pending_tasks"].pop(msg_id, None)
                    mark_dirty()
                    continue

            console_log(
                f"⚠️ 指令 {_truncate_log_text(cmd, limit=40)} 超时 {threshold}s，正在补发。",
                scope="identity",
                send_as_id=identity_id,
            )
            new_msg = await send_game_command(cmd, send_as_id=identity_id)
            with use_identity(identity_id) as identity_state:
                current_item = identity_state["pending_tasks"].get(msg_id)
                if current_item:
                    identity_state["pending_tasks"].pop(msg_id, None)
                if new_msg and new_msg.id in identity_state["pending_tasks"]:
                    identity_state["pending_tasks"][new_msg.id]["retry"] = retry + 1
                    if _is_identity_refresh_command(cmd):
                        _refresh_identity_info_retry_tracking(identity_state, int(new_msg.id), now)
                mark_dirty()


async def schedule_cleanup(reply_to, send_as_id=None):
    if not reply_to or not is_auto_delete_sent_messages_enabled():
        return

    if send_as_id is None:
        send_as_id = find_identity_by_msg_id(reply_to.id)
    if send_as_id is None:
        return

    with use_identity(send_as_id) as identity_state:
        is_my_msg = reply_to.id in identity_state["my_msg_ids"]
        is_script_cmd = is_script_command_text(reply_to.raw_text)
        if not (is_my_msg and is_script_cmd):
            return

        msg_id = reply_to.id
        if msg_id in identity_state.get("checkin_cleanup_msg_ids", []):
            return
        if msg_id == identity_state.get("sect_teach_reply_to_msg_id") and identity_state.get("next_sect_teach_time", 0) > 0:
            return
        if (
            is_identity_refresh_command_text(reply_to.raw_text)
            and (
                any(_is_identity_refresh_command(pending.get("cmd")) for pending in identity_state["pending_tasks"].values())
                or identity_state.get("identity_info_reply_msg_ids")
                or identity_state.get("last_identity_info_msg_id", 0)
                or float(identity_state.get("identity_info_followup_due_at", 0) or 0) > 0
            )
        ):
            return

    async def safe_delete():
        await asyncio.sleep(1)
        try:
            await reply_to.delete()
        except Exception as e:
            print(f"schedule_cleanup delete failed: {e} | msg_id={msg_id}")
        with use_identity(send_as_id) as identity_state:
            identity_state["my_msg_ids"].pop(msg_id, None)
            mark_dirty()

    _fire_and_forget(safe_delete())


__all__ = [
    "_fire_and_forget",
    "build_ui_login_url",
    "clear_pending_by_reply",
    "clear_pending_tasks_by_commands",
    "clear_ui_auth_state",
    "consume_unseen_startup_alerts",
    "fetch_forum_topics",
    "find_identity_by_msg_id",
    "gc_my_msg_ids",
    "gc_ui_login_tokens",
    "gc_ui_sessions",
    "get_reply_context",
    "get_reply_family_commands",
    "is_reply_to_identity_message",
    "is_script_command_text",
    "issue_ui_login_token",
    "redeem_ui_login_token",
    "reply_log_group_message",
    "resolve_reply_family",
    "run_retry_scheduler",
    "schedule_cleanup",
    "send_audit_log",
    "send_game_command",
    "touch_ui_session",
    "track_reply_chain_message",
    "validate_ui_session",
    "IDENTITY_INFO_REFRESH_ERROR_TEXT",
]
