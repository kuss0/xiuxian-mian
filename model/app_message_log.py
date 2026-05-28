import asyncio
import json
import traceback
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

import requests

from .app_runtime import _claim_runtime_log_event
from .config import LOG_BOT_TOKEN, LOG_SEND_MODE, MESSAGES_DIR, TG_REQUESTS_PROXIES, TZ_LOCAL, client, get_all_clients
from .runtime import send_audit_log
from .state import (
    get_game_group_id,
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
    buttons = _extract_message_log_buttons(event)
    if buttons:
        payload["buttons"] = buttons
    return now, payload


def _write_message_log(log_file, payload):
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        print(traceback.format_exc())


def _append_game_group_message_log(event, *, event_type="message"):
    if event.chat_id != get_game_group_id():
        return
    if not _claim_runtime_log_event(event, event_type=event_type):
        return
    now, payload = _build_message_log_payload(event, event_type=event_type)
    _write_message_log(f"{MESSAGES_DIR}/{now.strftime('%Y-%m-%d')}.log", payload)


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
    return _get_group_event_listener_account_id(event, get_replica_group_ids(), get_replica_listener_account_map())


def _get_replica_dispatch_event_listener_account_id(event):
    try:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        return 0
    if chat_id and chat_id == get_game_group_id():
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
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload)
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
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload)
    return True


def _append_sent_replica_group_message_log(chat_id, msg_id, text, listener_account_id=0, reply_to_msg_id=0, sent_via="account"):
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
    _write_message_log(f"{MESSAGES_DIR}/replica-{now.strftime('%Y-%m-%d')}.log", payload)


def _send_replica_group_via_bot(chat_id, text, *, parse_mode=None, reply_to=None):
    if not LOG_BOT_TOKEN:
        return False, 0, "missing bot token"
    payload = {
        "chat_id": str(int(chat_id or 0)),
        "text": str(text or ""),
        "disable_web_page_preview": True,
    }
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


async def _send_replica_group_message(client_obj, chat_id, text, *, parse_mode=None, reply_to=None, listener_account_id=0, log_text=None):
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
        msg = await client_obj.send_message(
            int(chat_id or 0),
            str(text or ""),
            parse_mode=parse_mode or None,
            reply_to=int(reply_to or 0) or None,
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
