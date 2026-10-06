"""Bounded, stdlib-only receipt validation for independent notification tools."""

import json
import urllib.error
from dataclasses import dataclass


MAX_REPLY_BYTES = 65536


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


def _reply_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate reply key")
        result[key] = value
    return result


def _parse_receipt(raw: bytes, status: int, chat_id: str, topic_id: int) -> BotDelivery:
    if len(raw) > MAX_REPLY_BYTES:
        return BotDelivery("unknown", "oversized response")
    try:
        payload = json.loads(raw, object_pairs_hook=_reply_object)
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


def read_bot_delivery(open_response, chat_id: str, *, topic_id: int = 0) -> BotDelivery:
    """Call the supplied opener once; never retry or fall back on an unknown send."""
    try:
        try:
            response = open_response()
        except urllib.error.HTTPError as exc:
            # Rejection requires matching JSON and HTTP status, not status alone.
            response = exc
        with response:
            return _parse_receipt(response.read(MAX_REPLY_BYTES + 1), response.status, chat_id, topic_id)
    except Exception as exc:
        # Exception text and response bodies can contain credentials or content.
        return BotDelivery("unknown", type(exc).__name__)
