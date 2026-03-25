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
    CMD_IDENTITY_INFO,
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
    UI_PUBLIC_BASE_URL,
    client,
    get_all_clients,
    get_client,
    is_identity_refresh_command_text,
)
from .persistence import mark_dirty
from .state import (
    get_current_identity_id,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_label,
    is_auto_delete_sent_messages_enabled,
    get_identity_account,
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


def _send_log_group_via_bot(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True):
    if not LOG_BOT_TOKEN:
        return False, "missing bot token"
    payload = {
        "chat_id": str(LOG_GROUP_ID),
        "text": text,
        "disable_web_page_preview": "true" if not link_preview else "false",
    }
    if int(reply_to_msg_id or 0) > 0:
        payload["reply_to_message_id"] = int(reply_to_msg_id)
    if int(message_thread_id or 0) > 0:
        payload["message_thread_id"] = int(message_thread_id)
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    with urlopen(url, data=urlencode(payload).encode("utf-8"), timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("ok") is True:
        return True, ""
    return False, body or "bot api returned non-ok response"


async def _send_log_group_message(text, *, reply_to_msg_id=None, message_thread_id=None, link_preview=True):
    if LOG_SEND_MODE == "bot":
        try:
            ok, error_text = await asyncio.to_thread(
                _send_log_group_via_bot,
                text,
                reply_to_msg_id=reply_to_msg_id,
                message_thread_id=message_thread_id,
                link_preview=link_preview,
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
        )
        return True
    except Exception as e:
        print(f"_send_log_group_message account failed: {e} | text={text}")
        return False


async def send_audit_log(content):
    now = datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
    message = f"【🍃 监控日志 {now}】\n{content}"
    ok = await _send_log_group_message(message, link_preview=False)
    if not ok:
        print(f"send_audit_log failed | content={content}")
    return ok


async def reply_log_group_message(event, text, *, audit_on_error=True, error_prefix="❌ 日志群回复失败", link_preview=True):
    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    # forum 群需要 message_thread_id 才能回复
    reply_header = getattr(event, "reply_to", None)
    thread_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0) or int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    ok = await _send_log_group_message(text, reply_to_msg_id=reply_to_msg_id, message_thread_id=thread_id, link_preview=link_preview)
    if ok:
        return True
    print(f"reply_log_group_message failed | text={text}")
    if audit_on_error:
        await send_audit_log(f"{error_prefix}")
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
    if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC:
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
        if now - float(payload.get("last_seen_at", 0) or 0) > UI_AUTH_IDLE_TIMEOUT_SEC
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
        if not account_id:
            raise ValueError(f"identity {send_as_id} 未关联任何账号")
        active_client = get_client(account_id)
        game_group_id = get_game_group_id()
        if not game_group_id:
            raise ValueError("游戏群聊 ID 未配置，请在 UI 基础配置中设置")
        try:
            peer = await active_client.get_input_entity(game_group_id)
        except ValueError:
            await active_client.get_dialogs()
            peer = await active_client.get_input_entity(game_group_id)
        # 如果 send_as_id 就是当前登录账号自己，不需要 send_as 参数
        me = await active_client.get_me()
        is_self = me and int(me.id) == send_as_id
        if not is_self:
            try:
                send_as_peer = await active_client.get_input_entity(send_as_id)
            except Exception as e:
                raise ValueError(f"无法解析 send_as 身份 {send_as_id} (account={account_id}, me={me.id if me else None}): {e}")
        else:
            send_as_peer = None
        reply_to_spec = None
        if reply_to:
            reply_to_spec = types.InputReplyToMessage(
                reply_to_msg_id=int(reply_to),
                top_msg_id=int(topic_id or 0) or None,
            )
        elif topic_id > 0:
            reply_to_spec = types.InputReplyToMessage(reply_to_msg_id=int(topic_id))
        request_kwargs = dict(
            peer=peer,
            message=command,
            reply_to=reply_to_spec,
        )
        if send_as_peer is not None:
            request_kwargs["send_as"] = send_as_peer
        result = await active_client(
            functions.messages.SendMessageRequest(**request_kwargs)
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
        return msg
    except Exception as e:
        await send_audit_log(
            f"❌ 指令发送失败[{get_send_as_label(send_as_id)}]: {command}\n"
            f"错误: {e}\n"
            f"identity={send_as_id}, account={get_identity_account(send_as_id)}, "
            f"game_group={get_game_group_id()}, topic={topic_id}"
        )
        return None


def _get_tracked_identity_message_ids(identity_state):
    tracked_ids = {
        int(identity_state.get("last_checkin_msg_id", 0) or 0),
        int(identity_state.get("last_sect_teach_msg_id", 0) or 0),
        int(identity_state.get("last_tower_msg_id", 0) or 0),
        int(identity_state.get("last_yuanying_summary_msg_id", 0) or 0),
        int(identity_state.get("last_deep_retreat_summary_msg_id", 0) or 0),
        int(identity_state.get("last_identity_info_msg_id", 0) or 0),
        *(int(msg_id or 0) for msg_id in identity_state.get("identity_info_reply_msg_ids", [])),
    }
    tracked_ids.discard(0)
    return tracked_ids


def find_identity_by_msg_id(msg_id):
    if not msg_id:
        return None
    msg_id = int(msg_id)
    for send_as_id in get_identity_ids():
        identity_state = get_identity_state(send_as_id)
        if msg_id in identity_state["my_msg_ids"] or msg_id in _get_tracked_identity_message_ids(identity_state):
            return send_as_id
    return None


def is_reply_to_identity_message(reply_to, send_as_id):
    if not reply_to:
        return False
    with use_identity(send_as_id):
        return reply_to.id in state["my_msg_ids"] or reply_to.id in _get_tracked_identity_message_ids(state)


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
    if changed:
        mark_dirty()


def clear_pending_by_reply(reply_to, send_as_id=None):
    if not reply_to:
        return

    if send_as_id is None:
        send_as_id = find_identity_by_msg_id(reply_to.id)
    if send_as_id is None:
        return

    with use_identity(send_as_id):
        item = state["pending_tasks"].pop(reply_to.id, None)

        replied_cmd = None
        if item:
            replied_cmd = item.get("cmd")
        elif reply_to.raw_text:
            for pending in state["pending_tasks"].values():
                if pending.get("cmd") and pending["cmd"] in reply_to.raw_text:
                    replied_cmd = pending["cmd"]
                    break
            if not replied_cmd:
                reply_text = (reply_to.raw_text or "").strip()
                for cmd in SCRIPT_COMMANDS:
                    if reply_text == cmd or reply_text.startswith(f"{cmd} "):
                        replied_cmd = reply_text
                        break

        if replied_cmd:
            remove_ids = [msg_id for msg_id, pending in state["pending_tasks"].items() if pending.get("cmd") == replied_cmd]
            for msg_id in remove_ids:
                state["pending_tasks"].pop(msg_id, None)

        if item or replied_cmd:
            mark_dirty()


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

            if now - send_time > threshold:
                with use_identity(identity_id) as identity_state:
                    if retry >= RETRY_LIMIT:
                        await send_audit_log(f"🧯 指令 `{cmd}`[{get_send_as_label(identity_id)}] 已重试 {RETRY_LIMIT} 次仍无响应，停止补发。")
                        if _is_identity_refresh_command(cmd):
                            identity_state["last_identity_info_msg_id"] = 0
                            identity_state["identity_info_reply_msg_ids"] = []
                            identity_state["identity_info_followup_due_at"] = 0
                            identity_state["identity_info_last_error"] = IDENTITY_INFO_REFRESH_ERROR_TEXT
                        identity_state["pending_tasks"].pop(msg_id, None)
                        mark_dirty()
                        continue

                await send_audit_log(f"⚠️ 指令 `{cmd}`[{get_send_as_label(identity_id)}] 响应超时({threshold}s)，正在补发...")
                new_msg = await send_game_command(cmd, send_as_id=identity_id)
                with use_identity(identity_id) as identity_state:
                    if new_msg and new_msg.id in identity_state["pending_tasks"]:
                        identity_state["pending_tasks"][new_msg.id]["retry"] = retry + 1
                        if _is_identity_refresh_command(cmd):
                            new_msg_id = int(new_msg.id)
                            tracked_ids = {
                                *(int(tracked_id or 0) for tracked_id in identity_state.get("identity_info_reply_msg_ids", [])),
                                new_msg_id,
                            }
                            tracked_ids.discard(0)
                            identity_state["last_identity_info_msg_id"] = new_msg_id
                            identity_state["identity_info_reply_msg_ids"] = sorted(tracked_ids)
                            identity_state["identity_info_followup_due_at"] = 0
                            identity_state["identity_info_last_requested_at"] = now
                    identity_state["pending_tasks"].pop(msg_id, None)
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
    "clear_ui_auth_state",
    "consume_unseen_startup_alerts",
    "fetch_forum_topics",
    "find_identity_by_msg_id",
    "gc_my_msg_ids",
    "gc_ui_login_tokens",
    "gc_ui_sessions",
    "is_reply_to_identity_message",
    "is_script_command_text",
    "issue_ui_login_token",
    "redeem_ui_login_token",
    "reply_log_group_message",
    "run_retry_scheduler",
    "schedule_cleanup",
    "send_audit_log",
    "send_game_command",
    "touch_ui_session",
    "validate_ui_session",
    "IDENTITY_INFO_REFRESH_ERROR_TEXT",
]
