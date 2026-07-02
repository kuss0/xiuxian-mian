import asyncio
import hashlib
import json
import os
import sqlite3
import traceback
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlparse

import requests

from .app_runtime import _claim_runtime_log_event
from .config import LOG_BOT_TOKEN, LOG_GROUP_ID, LOG_SEND_MODE, MESSAGES_DIR, TG_REQUESTS_PROXIES, TZ_LOCAL, client, get_all_clients
from .log_retention import cleanup_message_logs
from .runtime import send_audit_log
from .state import (
    get_game_group_id,
    get_game_listener_account_ids,
    get_replica_dispatch_group_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_group_ids,
    get_replica_listener_account_map,
    state,
)

_MESSAGE_LOG_BUTTON_MAX_ROWS = 20
_MESSAGE_LOG_BUTTON_MAX_COLS = 20
_MESSAGE_LOG_BUTTON_TEXT_MAX_LEN = 128
_REPLICA_BOT_CONNECT_TIMEOUT_SEC = 3
_REPLICA_BOT_READ_TIMEOUT_SEC = 8
_REPLICA_BOT_TOTAL_TIMEOUT_SEC = 12
_MESSAGE_LOG_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_log_events (
    event_key TEXT PRIMARY KEY,
    log_file TEXT NOT NULL,
    claimed_at TEXT NOT NULL
)
"""
_MESSAGE_LOG_LEDGER_RETENTION_DAYS = 3
_message_log_ledger_last_cleanup_at = 0.0
_message_log_ledger_initialized = set()


def _message_log_ledger_file():
    return os.path.join(MESSAGES_DIR, "message-log-events.sqlite3")


def _truncate_message_log_button_text(text):
    text = str(text or "").strip()
    if len(text) <= _MESSAGE_LOG_BUTTON_TEXT_MAX_LEN:
        return text
    return text[:_MESSAGE_LOG_BUTTON_TEXT_MAX_LEN] + "…"


def _get_url_host(url):
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.netloc:
        return parsed.netloc
    if parsed.scheme or not raw_url:
        return ""
    return urlparse("//" + raw_url).netloc


def _get_message_log_button_type(raw_button):
    class_name = type(raw_button).__name__.lower()
    if getattr(raw_button, "data", None) is not None or "callback" in class_name:
        return "callback"
    if getattr(raw_button, "url", None):
        return "url"
    if getattr(raw_button, "webview", None) or getattr(raw_button, "web_view", None) or "webview" in class_name or "web_view" in class_name:
        return "web_view"
    if "switchinline" in class_name or "switch_inline" in class_name:
        return "switch_inline"
    if "requestphone" in class_name or getattr(raw_button, "request_phone", False):
        return "request_phone"
    if "requestgeo" in class_name or getattr(raw_button, "request_geo", False):
        return "request_geo"
    if "requestpoll" in class_name or getattr(raw_button, "request_poll", None) is not None:
        return "request_poll"
    if "game" in class_name:
        return "game"
    if "buy" in class_name:
        return "buy"
    return "unknown"


def _extract_message_log_buttons(event):
    message = getattr(event, "message", None) or event
    rows = getattr(message, "buttons", None) or []
    result = []
    for raw_row in list(rows)[:_MESSAGE_LOG_BUTTON_MAX_ROWS]:
        row_buttons = raw_row if isinstance(raw_row, (list, tuple)) else [raw_row]
        row = []
        for button in list(row_buttons)[:_MESSAGE_LOG_BUTTON_MAX_COLS]:
            raw_button = getattr(button, "button", None) or button
            text = _truncate_message_log_button_text(getattr(button, "text", "") or getattr(raw_button, "text", ""))
            button_type = _get_message_log_button_type(raw_button)
            if not text and button_type == "unknown":
                continue
            item = {"text": text, "type": button_type}
            if button_type == "callback":
                item["has_callback_data"] = getattr(raw_button, "data", None) is not None
            elif button_type == "url":
                url_host = _get_url_host(getattr(raw_button, "url", ""))
                if url_host:
                    item["url_host"] = url_host
            row.append(item)
        if row:
            result.append(row)
    return result


def _build_message_log_payload(event, *, event_type="message"):
    now = datetime.now(TZ_LOCAL)
    reply_header = getattr(event, "reply_to", None)
    reply_to_msg_id = int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    topic_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0)
    sender = getattr(event, "sender", None)
    payload = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": event_type,
        "message_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "sender_id": int(getattr(event, "sender_id", 0) or 0),
        "topic_id": topic_id,
        "reply_to_msg_id": reply_to_msg_id,
        "text": event.raw_text or "",
    }
    if sender is not None:
        username = str(getattr(sender, "username", "") or "").strip()
        if username:
            payload["sender_username"] = username
        name = " ".join(
            part
            for part in (
                str(getattr(sender, "first_name", "") or "").strip(),
                str(getattr(sender, "last_name", "") or "").strip(),
            )
            if part
        )
        title = str(getattr(sender, "title", "") or "").strip()
        if name:
            payload["sender_name"] = name
        if title:
            payload["sender_title"] = title
        if hasattr(sender, "bot"):
            payload["sender_is_bot"] = bool(getattr(sender, "bot", False))
    buttons = _extract_message_log_buttons(event)
    if buttons:
        payload["buttons"] = buttons
    return now, payload


def _message_log_event_key(scope, payload):
    event_type = str((payload or {}).get("event_type") or "message").strip() or "message"
    chat_id = int((payload or {}).get("chat_id") or 0)
    message_id = int((payload or {}).get("message_id") or 0)
    if chat_id == 0 or message_id <= 0:
        return ""
    key = f"{scope}:{event_type}:{chat_id}:{message_id}"
    if event_type == "edit":
        raw_text = str((payload or {}).get("text") or "")
        text_hash = hashlib.blake2s(raw_text.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()
        key = f"{key}:{text_hash}"
    return key


def _claim_message_log_file_event(log_file, payload, *, scope="game"):
    global _message_log_ledger_last_cleanup_at
    event_key = _message_log_event_key(scope, payload)
    if not event_key:
        return True
    try:
        os.makedirs(MESSAGES_DIR, exist_ok=True)
        ledger_file = _message_log_ledger_file()
        with sqlite3.connect(ledger_file, timeout=2.0) as conn:
            if ledger_file not in _message_log_ledger_initialized:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(_MESSAGE_LOG_LEDGER_SCHEMA)
                _message_log_ledger_initialized.add(ledger_file)
            now_ts = datetime.now(TZ_LOCAL)
            now_epoch = now_ts.timestamp()
            if now_epoch - _message_log_ledger_last_cleanup_at >= 3600:
                cutoff = now_ts - timedelta(days=_MESSAGE_LOG_LEDGER_RETENTION_DAYS)
                conn.execute(
                    "DELETE FROM message_log_events WHERE claimed_at < ?",
                    (cutoff.strftime("%Y-%m-%d %H:%M:%S UTC+8"),),
                )
                _message_log_ledger_last_cleanup_at = now_epoch
            try:
                conn.execute(
                    "INSERT INTO message_log_events(event_key, log_file, claimed_at) VALUES (?, ?, ?)",
                    (event_key, os.path.basename(str(log_file or "")), now_ts.strftime("%Y-%m-%d %H:%M:%S UTC+8")),
                )
            except sqlite3.IntegrityError:
                return False
            return True
    except Exception:
        print(traceback.format_exc())
        return True


def _write_message_log(log_file, payload, *, scope="game"):
    try:
        cleanup_message_logs()
        if not _claim_message_log_file_event(log_file, payload, scope=scope):
            return False
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception:
        print(traceback.format_exc())
        return False


def _append_game_group_message_log(event, *, event_type="message"):
    if event.chat_id != get_game_group_id():
        return
    listener_account_id = _get_group_event_listener_account_id(
        event,
        [get_game_group_id()],
        {},
    )
    configured_listener_ids = set(get_game_listener_account_ids())
    if configured_listener_ids and listener_account_id not in configured_listener_ids:
        return
    if not _claim_runtime_log_event(event, event_type=event_type):
        return
    now, payload = _build_message_log_payload(event, event_type=event_type)
    if listener_account_id:
        payload["listener_account_id"] = listener_account_id
    _write_message_log(f"{MESSAGES_DIR}/{now.strftime('%Y-%m-%d')}.log", payload, scope="game")


def _get_group_event_listener_account_id(event, group_ids, listener_map):
    try:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if chat_id not in set(group_ids or []):
        return 0
    event_client = getattr(event, "client", None)
    listener_account_id = int((listener_map or {}).get(str(chat_id)) or 0)
    if listener_account_id <= 0:
        for account_id, account_client in get_all_clients().items():
            if event_client is account_client:
                return int(account_id or 0)
        if event_client is client and int(state.get("my_user_id") or 0) > 0:
            return int(state.get("my_user_id") or 0)
        return 0
    expected_client = get_all_clients().get(listener_account_id)
    if expected_client is None and int(state.get("my_user_id") or 0) == listener_account_id:
        expected_client = client
    if expected_client is None or event_client is not expected_client:
        return 0
    return listener_account_id


def _get_replica_event_listener_account_id(event):
    try:
        button_listener_account_id = int(getattr(event, "_replica_button_listener_account_id", 0) or 0)
    except (TypeError, ValueError):
        button_listener_account_id = 0
    if button_listener_account_id > 0:
        return button_listener_account_id
    if int(getattr(event, "chat_id", 0) or 0) == int(LOG_GROUP_ID or 0):
        return 0
    return _get_group_event_listener_account_id(event, get_replica_group_ids(), get_replica_listener_account_map())


def _get_replica_dispatch_event_listener_account_id(event):
    try:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if chat_id and chat_id == get_game_group_id():
        return 0
    if chat_id and chat_id == int(LOG_GROUP_ID or 0):
        return 0
    return _get_group_event_listener_account_id(
        event,
        get_replica_dispatch_group_ids(),
        get_replica_dispatch_listener_account_map(),
    )


def _is_replica_listener_self_event(event, listener_account_id=0):
    try:
        sender_id = int(getattr(event, "sender_id", 0) or 0)
    except (TypeError, ValueError):
        return False
    listener_account_id = int(listener_account_id or _get_replica_event_listener_account_id(event) or 0)
    return listener_account_id > 0 and sender_id == listener_account_id


def _append_replica_group_message_log(event, *, event_type="message"):
    listener_account_id = _get_replica_event_listener_account_id(event)
    if not listener_account_id:
        return False
    if not _claim_runtime_log_event(event, event_type=f"replica_{event_type}"):
        return True
    now, payload = _build_message_log_payload(event, event_type=event_type)
    payload["listener_account_id"] = listener_account_id
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload, scope="replica")
    return True


def _append_replica_dispatch_group_message_log(event, *, event_type="message"):
    listener_account_id = _get_replica_dispatch_event_listener_account_id(event)
    if not listener_account_id:
        return False
    if not _claim_runtime_log_event(event, event_type=f"replica_dispatch_{event_type}"):
        return True
    now, payload = _build_message_log_payload(event, event_type=event_type)
    payload["listener_account_id"] = listener_account_id
    payload["replica_group_role"] = "dispatch"
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload, scope="replica_dispatch")
    return True


def _normalize_sent_button_log_rows(buttons):
    rows = []
    for raw_row in buttons or []:
        row_items = raw_row if isinstance(raw_row, (list, tuple)) else [raw_row]
        row = []
        for raw_item in row_items:
            item = raw_item if isinstance(raw_item, dict) else {}
            text = _truncate_message_log_button_text(item.get("text") or "")
            callback_data = str(item.get("callback_data") or item.get("data") or "").strip()
            if not text:
                continue
            button_item = {"text": text, "type": "callback" if callback_data else "unknown"}
            if callback_data:
                button_item["has_callback_data"] = True
            row.append(button_item)
        if row:
            rows.append(row)
    return rows


def _append_sent_replica_group_message_log(chat_id, msg_id, text, listener_account_id=0, reply_to_msg_id=0, sent_via="account", buttons=None):
    sent_via = str(sent_via or "account").strip().lower() or "account"
    now = datetime.now(TZ_LOCAL)
    payload = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": "sent",
        "message_id": int(msg_id or 0),
        "chat_id": int(chat_id or 0),
        "sender_id": 0 if sent_via == "bot" else int(listener_account_id or 0),
        "topic_id": 0,
        "reply_to_msg_id": int(reply_to_msg_id or 0),
        "text": text or "",
        "listener_account_id": int(listener_account_id or 0),
        "sent_via": sent_via,
    }
    button_rows = _normalize_sent_button_log_rows(buttons)
    if button_rows:
        payload["buttons"] = button_rows
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload)


def _normalize_inline_keyboard_buttons(buttons):
    rows = []
    for raw_row in buttons or []:
        row_items = raw_row if isinstance(raw_row, (list, tuple)) else [raw_row]
        row = []
        for raw_item in row_items:
            item = raw_item if isinstance(raw_item, dict) else {}
            text = str(item.get("text") or "").strip()
            callback_data = str(item.get("callback_data") or item.get("data") or "").strip()
            if not text or not callback_data:
                continue
            row.append({"text": text[:64], "callback_data": callback_data[:64]})
        if row:
            rows.append(row)
    return rows


def _send_replica_group_via_bot(chat_id, text, *, parse_mode=None, reply_to=None, buttons=None):
    if not LOG_BOT_TOKEN:
        return False, 0, "missing bot token"
    payload = {
        "chat_id": str(int(chat_id or 0)),
        "text": str(text or ""),
        "disable_web_page_preview": True,
    }
    keyboard = _normalize_inline_keyboard_buttons(buttons)
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if int(reply_to or 0) > 0:
        payload["reply_to_message_id"] = int(reply_to or 0)
        payload["allow_sending_without_reply"] = True
    url = f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data=payload,
            timeout=(_REPLICA_BOT_CONNECT_TIMEOUT_SEC, _REPLICA_BOT_READ_TIMEOUT_SEC),
            proxies=TG_REQUESTS_PROXIES,
        )
    except requests.exceptions.Timeout as e:
        return False, 0, f"timeout: {e}"
    except requests.exceptions.ProxyError as e:
        return False, 0, f"proxy error: {e}"
    except requests.exceptions.RequestException as e:
        return False, 0, str(e)
    body = response.text
    if not response.ok:
        return False, 0, f"HTTP {response.status_code}: {body}"
    try:
        data = response.json()
    except Exception:
        data = None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return False, 0, body or "bot api returned non-ok response"
    try:
        msg_id = int(((data.get("result") or {}).get("message_id")) or 0)
    except (TypeError, ValueError):
        msg_id = 0
    if msg_id <= 0:
        return False, 0, "bot api response missing message_id"
    return True, msg_id, ""


async def _send_replica_group_message(client_obj, chat_id, text, *, parse_mode=None, reply_to=None, listener_account_id=0, log_text=None, buttons=None):
    log_payload_text = log_text if log_text is not None else str(text or "")
    if LOG_SEND_MODE == "bot":
        try:
            ok, msg_id, error_text = await asyncio.wait_for(
                asyncio.to_thread(
                    _send_replica_group_via_bot,
                    chat_id,
                    str(text or ""),
                    parse_mode=parse_mode or None,
                    reply_to=int(reply_to or 0) or None,
                    buttons=buttons,
                ),
                timeout=_REPLICA_BOT_TOTAL_TIMEOUT_SEC,
            )
            if ok and msg_id > 0:
                _append_sent_replica_group_message_log(
                    chat_id,
                    msg_id,
                    log_payload_text,
                    listener_account_id=listener_account_id,
                    reply_to_msg_id=int(reply_to or 0),
                    sent_via="bot",
                    buttons=buttons,
                )
                return SimpleNamespace(id=msg_id)
            await send_audit_log(f"❌ 副本群 bot 消息发送失败：{error_text}", scope="global", limit=200)
            print(f"_send_replica_group_message bot failed: {error_text} | text={log_payload_text}")
            return None
        except asyncio.TimeoutError:
            await send_audit_log("❌ 副本群 bot 消息发送超时", scope="global", limit=160)
            print(f"_send_replica_group_message bot timeout | text={log_payload_text}")
            return None
        except Exception:
            await send_audit_log("❌ 副本群 bot 消息发送异常", scope="global", limit=160)
            print(traceback.format_exc())
            return None
    try:
        send_kwargs = {}
        keyboard = _normalize_inline_keyboard_buttons(buttons)
        if keyboard:
            try:
                from telethon import Button
                send_kwargs["buttons"] = [
                    [Button.inline(item["text"], data=item["callback_data"].encode("utf-8")) for item in row]
                    for row in keyboard
                ]
            except Exception:
                traceback.print_exc()
        msg = await client_obj.send_message(
            int(chat_id or 0),
            str(text or ""),
            parse_mode=parse_mode or None,
            reply_to=int(reply_to or 0) or None,
            **send_kwargs,
        )
        msg_id = int(getattr(msg, "id", 0) or 0)
        if msg_id <= 0:
            raise ValueError("无法从副本群发送结果中解析消息 ID")
        _append_sent_replica_group_message_log(
            chat_id,
            msg_id,
            log_payload_text,
            listener_account_id=listener_account_id,
            reply_to_msg_id=int(reply_to or 0),
            sent_via="account",
            buttons=buttons,
        )
        return msg
    except Exception:
        await send_audit_log("❌ 副本群消息发送失败", scope="global", limit=160)
        print(traceback.format_exc())
        return None


__all__ = [
    "_append_game_group_message_log",
    "_append_replica_dispatch_group_message_log",
    "_append_replica_group_message_log",
    "_get_replica_dispatch_event_listener_account_id",
    "_get_replica_event_listener_account_id",
    "_is_replica_listener_self_event",
    "_send_replica_group_message",
]
