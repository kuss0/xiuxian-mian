"""Bounded, stdlib-only receipt validation for independent notification tools."""

import hashlib
import json
import sys
import time
import urllib.error
import uuid
from dataclasses import dataclass


MAX_REPLY_BYTES = 65536
TOOL_SOURCES = frozenset({"safety_watchdog", "miniapp_daily_report", "storage_bag_report"})
RECEIPT_PREFIX = "TG_NOTIFICATION_DELIVERY "


@dataclass(frozen=True)
class BotDelivery:
    outcome: str
    detail: str
    message_id: int = 0

    @property
    def diagnostic(self) -> str:
        label = "ok" if self.outcome == "confirmed" else self.outcome
        return f"log bot {label}: {self.detail}"

    def require_confirmed(self, context: str) -> None:
        if self.outcome != "confirmed":
            raise RuntimeError(f"{context}: {self.diagnostic}; no automatic retry") from None


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_receipt(raw: bytes, status: int, chat_id: str, topic_id: int) -> BotDelivery:
    if len(raw) > MAX_REPLY_BYTES:
        return BotDelivery("unknown", "oversized response")
    try:
        payload = json.loads(raw, object_pairs_hook=unique_json_object)
    except (ValueError, TypeError, RecursionError):
        return BotDelivery("unknown", "invalid response")
    if not isinstance(payload, dict):
        return BotDelivery("unknown", "invalid response")
    if payload.get("ok") is False:
        code = payload.get("error_code")
        if type(code) is int and 400 <= code < 500 and status == code:
            return BotDelivery("unconfirmed", f"HTTP {code}")
        return BotDelivery("unknown", "unproven rejection")
    result = payload.get("result")
    if status != 200 or payload.get("ok") is not True or not isinstance(result, dict):
        return BotDelivery("unknown", "missing message receipt")
    receipt_chat = result.get("chat")
    if (type(result.get("message_id")) is not int or not 0 < result["message_id"] < 2**63
            or not isinstance(receipt_chat, dict) or type(receipt_chat.get("id")) is not int
            or str(receipt_chat["id"]) != chat_id):
        return BotDelivery("unknown", "invalid message receipt")
    if topic_id > 0 and (type(result.get("message_thread_id")) is not int
                         or result["message_thread_id"] != topic_id):
        return BotDelivery("unknown", "invalid topic receipt")
    return BotDelivery("confirmed", f"message_id={result['message_id']}", result["message_id"])


def _monotonic():
    try:
        return time.monotonic()
    except Exception:
        return None


def _emit_tool_receipt(result, *, source, message, chat_id, topic_id, started):
    if source not in TOOL_SOURCES or started is None:
        return
    # Unlike runtime schema 1, these are raw payload sizes, not rendered HTML.
    row = {
        "schema": 2, "receipt_id": uuid.uuid4().hex, "at": time.time(),
        "source": source, "chat_id": int(chat_id), "topic_id": topic_id,
        "transport": "bot", "outcome": result.outcome,
        "elapsed_ms": round(max(0, time.monotonic() - started) * 1000),
        "payload_sha256": hashlib.sha256(message.encode("utf-8", errors="surrogatepass")).hexdigest(),
        "payload_utf16_units": len(message.encode("utf-16-le", errors="surrogatepass")) // 2,
        "payload_lines": message.count("\n") + 1,
    }
    print(RECEIPT_PREFIX + json.dumps(row, separators=(",", ":")), file=sys.stderr, flush=True)


def read_bot_delivery(open_response, chat_id: str, *, topic_id: int = 0,
                      source: str = "", message: str = "") -> BotDelivery:
    """Call the supplied opener once; never retry or fall back on an unknown send."""
    started = _monotonic() if source else None
    try:
        try:
            response = open_response()
        except urllib.error.HTTPError as exc:
            # Rejection requires matching JSON and HTTP status, not status alone.
            response = exc
        with response:
            result = _parse_receipt(response.read(MAX_REPLY_BYTES + 1), response.status, chat_id, topic_id)
    except Exception as exc:
        # Exception text and response bodies can contain credentials or content.
        result = BotDelivery("unknown", type(exc).__name__)
    if source:
        try:
            _emit_tool_receipt(result, source=source, message=message, chat_id=chat_id,
                               topic_id=topic_id, started=started)
        except Exception:
            # Logging must never turn a confirmed send into a retry or failure.
            pass
    return result
