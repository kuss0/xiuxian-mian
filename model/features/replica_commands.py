"""Narrow orchestration boundary for lightweight replica group commands."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Pattern


@dataclass(frozen=True)
class ReplicaTicketQueryContext:
    query_command: str
    get_listener_account_id: Callable[[Any], int]
    claim_event: Callable[..., bool]
    cleanup_run_state: Callable[[float], dict]
    format_ticket_reply: Callable[..., str]
    build_open_buttons: Callable[..., Any]
    strip_html: Callable[[str], str]
    send_group_message: Callable[..., Any]
    now: Callable[[], float] = time.time


@dataclass(frozen=True)
class ReplicaCommandMatchContext:
    query_command: str
    open_pattern: Pattern[str]
    enter_pattern: Pattern[str]
    kunwu_kind: str
    kunwu_enter_command: str
    dissolve_command: str
    is_xiaoji_query_command: Callable[[str], bool]
    resolve_kind_alias: Callable[[str], str]


def parse_lightweight_open_command(raw_text, pattern, resolve_kind_alias):
    match = pattern.match(str(raw_text or "").strip())
    if not match:
        return "", ""
    rest = str(match.group("rest") or "").strip()
    if not rest:
        return "", ""
    selector = ""
    replica_kind = ""
    for token in re.split(r"\s+", rest):
        if not token:
            continue
        token_kind = resolve_kind_alias(token)
        if token_kind and not replica_kind:
            replica_kind = token_kind
            continue
        if not selector:
            selector = token
    return selector, replica_kind


def parse_lightweight_join_usernames(raw_text, pattern):
    match = pattern.match(str(raw_text or "").strip())
    if not match:
        return []
    rest = str(match.group("rest") or "").strip()
    if not rest:
        return []
    selectors = []
    seen = set()
    for token in (item for item in re.split(r"[\s,，、]+", rest) if item):
        normalized = str(token or "").strip()
        key = normalized.lstrip("@").casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        selectors.append(normalized)
    return selectors


def is_replica_group_command_text(context, text):
    raw_text = str(text or "").strip()
    if not raw_text.startswith("."):
        return False
    if raw_text == context.query_command or context.is_xiaoji_query_command(raw_text):
        return True
    if context.open_pattern.match(raw_text):
        _selector, requested_kind = parse_lightweight_open_command(
            raw_text,
            context.open_pattern,
            context.resolve_kind_alias,
        )
        return requested_kind == context.kunwu_kind
    if context.enter_pattern.match(raw_text):
        return raw_text == context.kunwu_enter_command
    return raw_text == context.dissolve_command


async def handle_ticket_query(context, event):
    listener_account_id = context.get_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if raw_text != context.query_command:
        return False
    if not context.claim_event(event, scope="replica_ticket_query"):
        return True
    now = float(context.now())
    records = context.cleanup_run_state(now)
    reply_text = context.format_ticket_reply(html=True)
    buttons = context.build_open_buttons(
        event.chat_id,
        listener_account_id,
        now=now,
        records=records,
    )
    await context.send_group_message(
        event.client,
        event.chat_id,
        reply_text,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=context.strip_html(reply_text),
        buttons=buttons,
    )
    return True


__all__ = [
    "ReplicaCommandMatchContext",
    "ReplicaTicketQueryContext",
    "handle_ticket_query",
    "is_replica_group_command_text",
    "parse_lightweight_join_usernames",
    "parse_lightweight_open_command",
]
