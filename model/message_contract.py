import json
from collections import Counter
from datetime import datetime

from .features import passive_event_ledger, passive_inbox
from .module_manifest import get_module_manifest, get_module_name_for_reply_family


UNHANDLED_ROUTED_REPLY_REASON = "unhandled_routed_reply"
UNHANDLED_ROUTED_REPLY_DECISION = "handler_not_matched"
MESSAGE_CONTRACT_GAP_REASONS = frozenset(
    {
        UNHANDLED_ROUTED_REPLY_REASON,
        "reply_context_no_identity",
        "external_identity_no_match",
        "no_reply_context",
    }
)


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean_text(value):
    return str(value or "").replace("\r", "\n").strip()


def _compact_excerpt(text, limit=120):
    compact = " ".join(_clean_text(text).split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip()


def replay_module_for_family(family):
    module_name = get_module_name_for_reply_family(family)
    manifest = get_module_manifest(module_name)
    replay_modules = tuple(getattr(manifest, "replay_modules", ()) or ())
    return replay_modules[0] if replay_modules else ""


def record_unhandled_routed_reply(event, text, routed_identity_id, matched_family, root_msg_id, *, event_kind="message", reply_to_sender_id=0):
    family = str(matched_family or "").strip()
    if not family:
        return False
    event_type = str(event_kind or "message").strip() or "message"
    message_id = _safe_int(getattr(event, "id", 0))
    return passive_inbox.record_passive_inbox_event(
        "skipped",
        module=get_module_name_for_reply_family(family),
        identity_id=routed_identity_id,
        reason=UNHANDLED_ROUTED_REPLY_REASON,
        summary=family,
        family=family,
        chat_id=_safe_int(getattr(event, "chat_id", 0)),
        msg_id=message_id,
        reply_to_msg_id=_safe_int(root_msg_id),
        reply_to_sender_id=_safe_int(reply_to_sender_id),
        root_msg_id=_safe_int(root_msg_id),
        event_type=event_type,
        route_source=f"{event_type}:reply_context",
        matched_text=text,
        decision=UNHANDLED_ROUTED_REPLY_DECISION,
        source_message_id=message_id,
        include_recent=True,
    )


def is_unhandled_routed_reply_event(event):
    return (
        isinstance(event, dict)
        and str(event.get("reason") or "") == UNHANDLED_ROUTED_REPLY_REASON
        and str(event.get("decision") or "") == UNHANDLED_ROUTED_REPLY_DECISION
    )


def _event_module_name(event):
    module = str(event.get("module") or "").strip()
    if module:
        return module
    return get_module_name_for_reply_family(event.get("family")) or "未归属"


def is_message_contract_gap_event(event):
    return (
        isinstance(event, dict)
        and str(event.get("kind") or "") == "skipped"
        and str(event.get("reason") or "") in MESSAGE_CONTRACT_GAP_REASONS
    )


def _filter_contract_event(event, *, module="", family="", identity_id=0, reason=""):
    if not is_message_contract_gap_event(event):
        return False
    if module and _event_module_name(event) != module:
        return False
    if family and str(event.get("family") or "") != family:
        return False
    if identity_id and _safe_int(event.get("identity_id")) != identity_id:
        return False
    if reason and str(event.get("reason") or "") != reason:
        return False
    return True


def iter_message_contract_gaps(path=None, limit=100, *, module="", family="", identity_id=0, reason=""):
    module = str(module or "").strip()
    family = str(family or "").strip()
    reason = str(reason or "").strip()
    identity_id = _safe_int(identity_id)
    for event in passive_event_ledger.iter_passive_events(path=path, limit=limit):
        if _filter_contract_event(event, module=module, family=family, identity_id=identity_id, reason=reason):
            yield event


def iter_unhandled_routed_replies(path=None, limit=100, *, module="", family="", identity_id=0):
    module = str(module or "").strip()
    family = str(family or "").strip()
    identity_id = _safe_int(identity_id)
    for event in passive_event_ledger.iter_passive_events(path=path, limit=limit):
        if not is_unhandled_routed_reply_event(event):
            continue
        if module and str(event.get("module") or "") != module:
            continue
        if family and str(event.get("family") or "") != family:
            continue
        if identity_id and _safe_int(event.get("identity_id")) != identity_id:
            continue
        yield event


def summarize_unhandled_routed_replies(events, *, latest_limit=8):
    items = [event for event in events if is_unhandled_routed_reply_event(event)]
    by_module = Counter(str(event.get("module") or "未归属") for event in items)
    by_family = Counter(str(event.get("family") or "unknown") for event in items)
    latest = sorted(items, key=lambda item: (_safe_int(item.get("ts")), _safe_int(item.get("msg_id"))))[-max(1, int(latest_limit or 1)):]
    return {
        "total": len(items),
        "by_module": dict(sorted(by_module.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_family": dict(sorted(by_family.items(), key=lambda pair: (-pair[1], pair[0]))),
        "latest": latest,
    }


def summarize_message_contract_gaps(events, *, latest_limit=8):
    items = [event for event in events if is_message_contract_gap_event(event)]
    by_reason = Counter(str(event.get("reason") or "unknown") for event in items)
    by_module = Counter(_event_module_name(event) for event in items)
    by_family = Counter(str(event.get("family") or "unknown") for event in items)
    latest = sorted(items, key=lambda item: (_safe_int(item.get("ts")), _safe_int(item.get("msg_id"))))[-max(1, int(latest_limit or 1)):]
    return {
        "total": len(items),
        "by_reason": dict(sorted(by_reason.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_module": dict(sorted(by_module.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_family": dict(sorted(by_family.items(), key=lambda pair: (-pair[1], pair[0]))),
        "latest": latest,
    }


def build_replay_sample_suggestion(event, *, sample_id="", source=""):
    family = str(event.get("family") or "").strip()
    msg_id = _safe_int(event.get("msg_id") or event.get("source_message_id"))
    sample_key = str(sample_id or "").strip()
    event_type = str(event.get("event_type") or "message").strip() or "message"
    if not sample_key:
        family_key = family or str(event.get("module") or "unknown").strip() or "unknown"
        text_hash = str(event.get("matched_text_hash") or "").strip()[:8]
        suffix = f"{event_type}.{msg_id or 'message'}"
        if text_hash:
            suffix = f"{suffix}.{text_hash}"
        sample_key = f"contract_gap.{family_key}.{suffix}"
    replay_module = replay_module_for_family(family)
    payload = {
        "source": str(source or "").strip() or f"passive_event_ledger:{msg_id or 'unknown'}",
        "module": replay_module or family or str(event.get("module") or "").strip(),
        "family": family,
        "event_type": event_type,
        "text": _clean_text(event.get("matched_text")),
    }
    return sample_key, payload


def format_unhandled_reply_line(event):
    ts = _safe_int(event.get("ts"))
    ts_text = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "unknown-time"
    module = str(event.get("module") or "未归属")
    if module == "未归属":
        module = _event_module_name(event)
    family = str(event.get("family") or "unknown")
    reason = str(event.get("reason") or "unknown")
    identity_id = _safe_int(event.get("identity_id"))
    msg_id = _safe_int(event.get("msg_id"))
    reply_to = _safe_int(event.get("reply_to_msg_id") or event.get("root_msg_id"))
    reply_to_sender = _safe_int(event.get("reply_to_sender_id"))
    excerpt = _compact_excerpt(event.get("matched_text"))
    sender_text = f" cmd_sender={reply_to_sender}" if reply_to_sender else ""
    return f"{ts_text} | {module}/{family} | reason={reason} identity={identity_id} msg={msg_id} reply={reply_to}{sender_text} | {excerpt}"


def dumps_replay_sample_suggestion(event, *, sample_id="", source=""):
    key, payload = build_replay_sample_suggestion(event, sample_id=sample_id, source=source)
    return json.dumps({key: payload}, ensure_ascii=False, indent=2)


def _format_counter(items, limit=8):
    if not items:
        return "无"
    ordered = sorted(items.items(), key=lambda pair: (-int(pair[1] or 0), str(pair[0])))
    return "、".join(f"{key}:{value}" for key, value in ordered[:limit])


def get_message_contract_status_text(limit=500, latest=5, *, module="", family="", reason=""):
    safe_limit = max(1, min(int(limit or 500), 5000))
    safe_latest = max(1, min(int(latest or 5), 20))
    events = list(
        iter_message_contract_gaps(
            limit=safe_limit,
            module=module,
            family=family,
            reason=reason,
        )
    )
    summary = summarize_message_contract_gaps(events, latest_limit=safe_latest)
    unhandled = summarize_unhandled_routed_replies(
        [
            event
            for event in events
            if str(event.get("reason") or "") == UNHANDLED_ROUTED_REPLY_REASON
        ],
        latest_limit=safe_latest,
    )
    lines = [
        "🧭 消息契约",
        "- 只读：不自动处理，不发送游戏命令。",
        "- 日志群只提醒：带 sample/msg/reply 回 Codex 补规则；不做确认、不写状态。",
        f"- 扫描：最近 {safe_limit} 行 ledger",
        f"- 契约缺口：{summary['total']}",
        f"- 未匹配 handler：{unhandled['total']}",
        f"- 按原因：{_format_counter(summary.get('by_reason') or {})}",
        f"- 按模块：{_format_counter(summary.get('by_module') or {})}",
        f"- 按 family：{_format_counter(summary.get('by_family') or {})}",
    ]
    latest_events = summary.get("latest") or []
    if latest_events:
        lines.append("- 最近缺口：")
        for event in latest_events[-safe_latest:]:
            sample_id, _payload = build_replay_sample_suggestion(event)
            lines.append(f"  {format_unhandled_reply_line(event)}｜sample={sample_id}")
    return "\n".join(lines)


__all__ = [
    "UNHANDLED_ROUTED_REPLY_DECISION",
    "UNHANDLED_ROUTED_REPLY_REASON",
    "MESSAGE_CONTRACT_GAP_REASONS",
    "build_replay_sample_suggestion",
    "dumps_replay_sample_suggestion",
    "format_unhandled_reply_line",
    "get_message_contract_status_text",
    "is_message_contract_gap_event",
    "is_unhandled_routed_reply_event",
    "iter_message_contract_gaps",
    "iter_unhandled_routed_replies",
    "record_unhandled_routed_reply",
    "replay_module_for_family",
    "summarize_message_contract_gaps",
    "summarize_unhandled_routed_replies",
]
