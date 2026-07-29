"""Narrow orchestration boundary for lightweight replica group commands."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import escape
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


@dataclass(frozen=True)
class ReplicaOpenCommandConfig:
    command_pattern: Pattern[str]
    cangkun_kind: str
    open_timeout_sec: float


@dataclass(frozen=True)
class ReplicaOpenRuntimePort:
    get_listener_account_id: Callable[[Any], int]
    claim_event: Callable[..., bool]
    send_game_command: Callable[..., Any]
    build_send_intent: Callable[..., dict]
    schedule_fast_retry: Callable[..., bool]
    now: Callable[[], float] = time.time


@dataclass(frozen=True)
class ReplicaOpenIdentityPort:
    resolve_kind_alias: Callable[[str], str]
    resolve_identity: Callable[[str], int]
    is_identity_enabled: Callable[[int], bool]
    select_open_kind: Callable[..., str]
    format_ticket_counts: Callable[[int], str]
    get_openable_kinds: Callable[[int], list[str]]
    is_open_requirement_available: Callable[[int, str], bool]
    format_open_requirement: Callable[[int, str], str]
    get_ticket_count: Callable[[int, str], int]
    get_identity_username: Callable[[int], str]
    get_kind_name: Callable[[str], str]
    get_open_game_command: Callable[[str], str]
    get_identity_block_reason: Callable[..., str]


@dataclass(frozen=True)
class ReplicaOpenStatePort:
    mark_notice_once: Callable[..., bool]
    get_active_room: Callable[..., Any]
    find_active_flow: Callable[..., Any]
    is_flow_active: Callable[..., bool]
    remove_flow: Callable[[str], bool]
    make_flow_id: Callable[[int, int, float], str]
    upsert_flow: Callable[[dict], bool]


@dataclass(frozen=True)
class ReplicaOpenViewPort:
    format_usage: Callable[..., str]
    format_next_commands: Callable[..., str]
    format_open_commands_for_identity: Callable[..., str]
    format_open_command_for_identity: Callable[[int, str], str]
    format_existing_room_notice: Callable[..., str]
    existing_room_buttons: Callable[[dict], Any]
    format_existing_open_notice: Callable[..., str]
    existing_open_buttons: Callable[[dict], Any]
    build_open_buttons: Callable[..., Any]
    build_flow_buttons: Callable[[dict], Any]
    strip_html: Callable[[str], str]
    send_group_message: Callable[..., Any]


@dataclass(frozen=True)
class ReplicaOpenCommandContext:
    config: ReplicaOpenCommandConfig
    runtime: ReplicaOpenRuntimePort
    identity: ReplicaOpenIdentityPort
    state: ReplicaOpenStatePort
    view: ReplicaOpenViewPort


@dataclass(frozen=True)
class ReplicaJoinDispatchConfig:
    cangkun_kind: str


@dataclass(frozen=True)
class ReplicaJoinDispatchRuntimePort:
    send_game_command: Callable[..., Any]
    build_send_intent: Callable[..., dict]
    schedule_fast_retry: Callable[..., bool]
    now: Callable[[], float] = time.time


@dataclass(frozen=True)
class ReplicaJoinDispatchIdentityPort:
    resolve_identity: Callable[[str], int]
    is_identity_enabled: Callable[[int], bool]
    is_cangkun_realm_available: Callable[[int], bool]
    format_cangkun_realm_requirement: Callable[[int], str]
    get_identity_block_reason: Callable[..., str]
    get_identity_username: Callable[[int], str]


@dataclass(frozen=True)
class ReplicaJoinDispatchStatePort:
    reserve_join: Callable[..., Any]
    mark_join_sent: Callable[..., Any]
    set_room: Callable[[dict], bool]


@dataclass(frozen=True)
class ReplicaJoinDispatchViewPort:
    get_kind_name: Callable[[str], str]
    get_enter_command: Callable[[str], str]
    is_room_enter_actionable: Callable[[dict], bool]
    is_room_dissolve_actionable: Callable[[dict], bool]
    get_room_team_identity_ids: Callable[..., list[int]]
    get_room_usernames: Callable[[dict], list[str]]
    format_team_notice_details: Callable[..., list[str]]
    format_cangkun_sense: Callable[..., str]
    format_next_commands: Callable[..., str]
    build_room_buttons: Callable[..., Any]
    strip_html: Callable[[str], str]
    send_group_message: Callable[..., Any]


@dataclass(frozen=True)
class ReplicaJoinDispatchContext:
    config: ReplicaJoinDispatchConfig
    runtime: ReplicaJoinDispatchRuntimePort
    identity: ReplicaJoinDispatchIdentityPort
    state: ReplicaJoinDispatchStatePort
    view: ReplicaJoinDispatchViewPort


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


def normalize_replica_username(username):
    username = str(username or "").strip()
    if not username:
        return ""
    if not username.startswith("@"):
        username = f"@{username}"
    return username.lower()


def normalize_replica_usernames(usernames):
    normalized = []
    seen = set()
    for username in usernames or []:
        item = normalize_replica_username(username)
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def format_replica_skipped_selector(selector, reason=""):
    selector = str(selector or "").strip() or "未知身份"
    reason = str(reason or "").strip()
    return f"{selector}({reason})" if reason else selector


async def _send_open_notice(context, event, listener_account_id, text, buttons):
    await context.view.send_group_message(
        event.client,
        event.chat_id,
        text,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=context.view.strip_html(text),
        buttons=buttons,
    )


async def handle_lightweight_open_command(context, event):
    runtime = context.runtime
    identity = context.identity
    state = context.state
    view = context.view
    config = context.config

    listener_account_id = runtime.get_listener_account_id(event)
    if not listener_account_id:
        return False
    raw_text = str(getattr(event, "raw_text", "") or "").strip()
    if not config.command_pattern.match(raw_text):
        return False
    if not runtime.claim_event(event, scope="replica_lightweight_open"):
        return True

    now = float(runtime.now())
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    event_id = int(getattr(event, "id", 0) or 0)
    selector, requested_kind = parse_lightweight_open_command(
        raw_text,
        config.command_pattern,
        identity.resolve_kind_alias,
    )
    if not selector:
        text = f"用法：{view.format_usage(html=True)}\n\n" + view.format_next_commands(".查询副本", html=True)
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.build_open_buttons(chat_id, listener_account_id, now=now),
        )
        return True

    identity_id = identity.resolve_identity(selector)
    if identity_id <= 0 or not identity.is_identity_enabled(identity_id):
        text = f"未找到可用身份：{escape(selector)}\n\n" + view.format_next_commands(".查询副本", html=True)
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.build_open_buttons(chat_id, listener_account_id, now=now),
        )
        return True

    replica_kind = identity.select_open_kind(identity_id, requested_kind=requested_kind)
    if not replica_kind:
        ticket_text = identity.format_ticket_counts(identity_id) or "无可用门票"
        requested_text = identity.get_kind_name(requested_kind) if requested_kind else "副本"
        reason_text = ticket_text
        openable_kinds = identity.get_openable_kinds(identity_id)
        if not requested_kind and len(openable_kinds) > 1:
            sender_id = int(getattr(event, "sender_id", 0) or 0)
            dedupe_key = f"ambiguous_open:{chat_id}:{sender_id}:{identity_id}"
            if not state.mark_notice_once(dedupe_key, now):
                return True
            open_commands = view.format_open_commands_for_identity(identity_id, html=True)
            text = (
                f"{escape(selector)} 有多种可开副本（{escape(ticket_text)}），请指定类型，避免默认误开虚天殿。\n\n"
                "开房兜底命令：\n"
                f"{open_commands}\n\n"
                + view.format_next_commands(".查询副本", html=True)
            )
            await _send_open_notice(
                context,
                event,
                listener_account_id,
                text,
                view.build_open_buttons(chat_id, listener_account_id, identity_id=identity_id, now=now),
            )
            return True
        if requested_kind and not identity.is_open_requirement_available(identity_id, requested_kind):
            reason_text = identity.format_open_requirement(identity_id, requested_kind) or ticket_text
        elif (
            not requested_kind
            and identity.get_ticket_count(identity_id, config.cangkun_kind) > 0
            and not identity.is_open_requirement_available(identity_id, config.cangkun_kind)
        ):
            reason_text = f"{ticket_text}；{identity.format_open_requirement(identity_id, config.cangkun_kind)}"
        text = f"{escape(selector)} 不能开启{escape(requested_text)}：{escape(reason_text)}\n\n" + view.format_next_commands(".查询副本", html=True)
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.build_open_buttons(chat_id, listener_account_id, identity_id=identity_id, now=now),
        )
        return True

    leader_username = normalize_replica_username(identity.get_identity_username(identity_id))
    active_room = state.get_active_room(chat_id, replica_kind=replica_kind, now=now)
    if active_room:
        text = view.format_existing_room_notice(active_room, html=True)
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.existing_room_buttons(active_room),
        )
        return True

    active_flow = state.find_active_flow(chat_id, replica_kind=replica_kind, now=now)
    if active_flow:
        if state.is_flow_active(active_flow, now=now):
            text = view.format_existing_open_notice(active_flow, html=True)
            await _send_open_notice(
                context,
                event,
                listener_account_id,
                text,
                view.existing_open_buttons(active_flow),
            )
            return True
        state.remove_flow(active_flow.get("flow_id"))

    flow = {
        "flow_id": state.make_flow_id(chat_id, identity_id, now),
        "phase": "opening",
        "replica_chat_id": chat_id,
        "listener_account_id": int(listener_account_id or 0),
        "leader_identity_id": int(identity_id or 0),
        "leader_username": leader_username,
        "replica_kind": replica_kind,
        "selector": selector,
        "replica_command_msg_id": event_id,
        "open_command_msg_id": 0,
        "open_requested_at": now,
        "updated_at": now,
        "expires_at": now + float(config.open_timeout_sec),
        "last_error": "",
    }
    state.upsert_flow(flow)
    command = identity.get_open_game_command(replica_kind)
    blocked_reason = identity.get_identity_block_reason(identity_id, now=now)
    if blocked_reason:
        state.remove_flow(flow.get("flow_id"))
        text = (
            f"{escape(command)} 未发送：{escape(selector)}（{escape(blocked_reason)}）\n\n"
            + view.format_next_commands(view.format_open_command_for_identity(identity_id, replica_kind), html=True)
        )
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.build_open_buttons(chat_id, listener_account_id, identity_id=identity_id, now=now),
        )
        return True

    msg = await runtime.send_game_command(
        command,
        track=False,
        send_as_id=identity_id,
        priority="urgent_reactive",
        **runtime.build_send_intent(
            op_id=f"replica_lightweight_open:{chat_id}:{event_id}:{identity_id}",
            chain_id=f"replica_lightweight_open:{replica_kind}:{flow['flow_id']}",
        ),
    )
    msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
    if msg_id <= 0:
        unknown_at = float(runtime.now())
        flow.update({
            "open_command_msg_id": 0,
            "open_send_unknown_at": unknown_at,
            "updated_at": unknown_at,
            "last_error": "开房发送结果未知，等待开房广播",
        })
        state.upsert_flow(flow)
        blocked_reason = identity.get_identity_block_reason(identity_id) or "发送结果未知"
        text = (
            f"{escape(command)} 已请求：{escape(selector)}（{escape(blocked_reason)}），等待开房广播；未重复发送。\n\n"
            + view.format_next_commands(".查询副本", ".解散副本", html=True)
        )
        await _send_open_notice(
            context,
            event,
            listener_account_id,
            text,
            view.build_flow_buttons(flow),
        )
        return True

    updated_at = float(runtime.now())
    flow.update({"open_command_msg_id": msg_id, "updated_at": updated_at})
    state.upsert_flow(flow)
    runtime.schedule_fast_retry(
        "open",
        identity_id,
        replica_kind,
        flow["flow_id"],
        command,
        chat_id,
        event_id,
        msg_id,
    )
    text = (
        f"已用 {escape(leader_username or selector)} 发送 {escape(command)}，等待开房广播。\n\n"
        + view.format_next_commands(".加入副本 @用户名 @用户名", ".解散副本", html=True)
    )
    await _send_open_notice(
        context,
        event,
        listener_account_id,
        text,
        view.build_flow_buttons(flow),
    )
    return True


async def dispatch_lightweight_join_members(
    context,
    event,
    listener_account_id,
    room,
    selectors,
    command,
):
    runtime = context.runtime
    identity = context.identity
    state = context.state
    view = context.view
    replica_kind = str(room.get("replica_kind") or "")
    room_id = str(room.get("room_id") or "").strip()
    leader_identity_id = int(room.get("leader_identity_id") or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    event_id = int(getattr(event, "id", 0) or 0)

    sent_usernames = []
    unknown_usernames = []
    skipped = []
    seen_identity_ids = set()
    for selector in selectors:
        identity_id = identity.resolve_identity(selector)
        if identity_id <= 0:
            skipped.append(format_replica_skipped_selector(selector, "未找到身份"))
            continue
        if not identity.is_identity_enabled(identity_id):
            skipped.append(format_replica_skipped_selector(selector, "身份未启用"))
            continue
        if replica_kind == context.config.cangkun_kind and not identity.is_cangkun_realm_available(identity_id):
            skipped.append(
                format_replica_skipped_selector(
                    selector,
                    identity.format_cangkun_realm_requirement(identity_id),
                )
            )
            continue
        if identity_id == leader_identity_id or identity_id in seen_identity_ids:
            continue
        seen_identity_ids.add(identity_id)
        blocked_reason = identity.get_identity_block_reason(identity_id)
        if blocked_reason:
            skipped.append(format_replica_skipped_selector(selector, blocked_reason))
            continue
        reserve_now = float(runtime.now())
        allowed, reserve_reason = state.reserve_join(
            identity_id,
            replica_kind,
            room_id,
            event,
            reserve_now,
        )
        if not allowed:
            skipped.append(format_replica_skipped_selector(selector, reserve_reason))
            continue
        msg = await runtime.send_game_command(
            command,
            track=False,
            send_as_id=identity_id,
            priority="urgent_reactive",
            **runtime.build_send_intent(
                op_id=f"replica_lightweight_join:{chat_id}:{event_id}:{identity_id}",
                chain_id=f"replica_lightweight_room:{replica_kind}:{room_id}",
            ),
        )
        sent_at = float(getattr(msg, "sent_at", 0) or runtime.now()) if msg else float(runtime.now())
        profile_username = normalize_replica_username(identity.get_identity_username(identity_id) or selector)
        sent_msg_id = int(getattr(msg, "id", 0) or 0) if msg else 0
        if sent_msg_id > 0:
            state.mark_join_sent(
                identity_id,
                replica_kind,
                room_id,
                sent_msg_id,
                sent_at,
            )
            runtime.schedule_fast_retry(
                "join",
                identity_id,
                replica_kind,
                room_id,
                command,
                chat_id,
                event_id,
                sent_msg_id,
            )
            sent_usernames.append(profile_username)
        else:
            state.mark_join_sent(identity_id, replica_kind, room_id, 0, sent_at)
            unknown_usernames.append(profile_username)

    requested_usernames = sent_usernames + unknown_usernames
    room["join_requested_usernames"] = normalize_replica_usernames(
        (room.get("join_requested_usernames") or []) + requested_usernames
    )
    if unknown_usernames:
        room["join_unknown_usernames"] = normalize_replica_usernames(
            (room.get("join_unknown_usernames") or []) + unknown_usernames
        )
    room["updated_at"] = float(runtime.now())
    state.set_room(room)

    if unknown_usernames:
        action_label = "已请求加入"
    else:
        action_label = "已发送加入" if sent_usernames else "未发送加入"
    summary = (
        f"{action_label}{view.get_kind_name(replica_kind)} {room_id}："
        f"{' '.join(requested_usernames) if requested_usernames else '无'}"
    )
    if sent_usernames and unknown_usernames:
        summary += f"\n已发出：{' '.join(sent_usernames)}"
    if unknown_usernames:
        summary += f"\n发送结果未知，等待游戏回包：{' '.join(unknown_usernames)}"
    if skipped:
        summary += f"\n未发送：{' '.join(skipped)}"

    enter_actionable = view.is_room_enter_actionable(room)
    if replica_kind == context.config.cangkun_kind:
        detail_now = float(runtime.now())
        cangkun_team_ids = view.get_room_team_identity_ids(room, now=detail_now)
        projected_usernames = view.get_room_usernames(room)
        detail_lines = view.format_team_notice_details(
            replica_kind,
            projected_usernames,
            now=detail_now,
            team_professions_by_username=room.get("team_professions_by_username"),
        )
        if detail_lines:
            summary += "\n预计队伍明细：\n" + "\n".join(detail_lines)
        summary += "\n" + view.format_cangkun_sense(cangkun_team_ids)

    summary = escape(summary)
    enter_command = view.get_enter_command(replica_kind)
    next_commands = [enter_command] if enter_actionable and enter_command else []
    if view.is_room_dissolve_actionable(room):
        next_commands.append(".解散副本")
    if not next_commands:
        next_commands.append(".查询副本")
    summary += "\n\n" + view.format_next_commands(*next_commands, html=True)
    await view.send_group_message(
        event.client,
        event.chat_id,
        summary,
        parse_mode="html",
        listener_account_id=listener_account_id,
        log_text=view.strip_html(summary),
        buttons=view.build_room_buttons(
            room,
            include_enter=enter_actionable,
            include_dissolve=True,
            include_query=True,
        ),
    )
    return True


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
    "ReplicaJoinDispatchConfig",
    "ReplicaJoinDispatchContext",
    "ReplicaJoinDispatchIdentityPort",
    "ReplicaJoinDispatchRuntimePort",
    "ReplicaJoinDispatchStatePort",
    "ReplicaJoinDispatchViewPort",
    "ReplicaOpenCommandConfig",
    "ReplicaOpenCommandContext",
    "ReplicaOpenIdentityPort",
    "ReplicaOpenRuntimePort",
    "ReplicaOpenStatePort",
    "ReplicaOpenViewPort",
    "ReplicaTicketQueryContext",
    "dispatch_lightweight_join_members",
    "format_replica_skipped_selector",
    "handle_lightweight_open_command",
    "handle_ticket_query",
    "is_replica_group_command_text",
    "normalize_replica_username",
    "normalize_replica_usernames",
    "parse_lightweight_join_usernames",
    "parse_lightweight_open_command",
]
