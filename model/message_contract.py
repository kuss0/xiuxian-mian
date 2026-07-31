import json
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime

from .features import passive_event_ledger, passive_inbox
from .module_manifest import get_module_manifest, get_module_name_for_reply_family
from .state import get_identity_ids
from .verified_event import VerifiedGameEvent, from_telegram_event


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
MESSAGE_CONTRACT_CLASS_HANDLER_GAP = "handler_gap"
MESSAGE_CONTRACT_CLASS_UNRESOLVED_IDENTITY = "unresolved_identity"
MESSAGE_CONTRACT_CLASS_EXTERNAL_OBSERVATION = "external_observation"
MESSAGE_CONTRACT_CLASS_WEAK_OWNER_HINT = "weak_owner_hint"
MESSAGE_CONTRACT_CLASS_OTHER = "other"
MESSAGE_BOX_SHADOW_STATUS_CHANGED = "changed"
MESSAGE_BOX_SHADOW_STATUS_UNHANDLED = "unhandled"
MESSAGE_BOX_SHADOW_STATUS_NO_CHANGE = "no_change"
MESSAGE_BOX_SHADOW_STATUS_GAP = "gap"
MESSAGE_BOX_SHADOW_STATUS_OBSERVED = "observed"
MESSAGE_BOX_SHADOW_STATUS_MISSING = "missing"
MESSAGE_CONTRACT_EXTERNAL_REASONS = frozenset(
    {
        "external_identity_no_match",
        "external_owner_no_match",
    }
)
MESSAGE_CONTRACT_UNRESOLVED_IDENTITY_REASONS = frozenset(
    {
        "reply_context_no_identity",
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


def _reply_sender_maps_to_identity(sender_id):
    sender_id = _safe_int(sender_id)
    if sender_id == 0:
        return False
    candidates = [sender_id]
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            candidates.append(_safe_int(sender_abs[3:]))
    try:
        identity_ids = {int(identity_id) for identity_id in get_identity_ids()}
    except Exception:
        identity_ids = set()
    return any(int(candidate or 0) in identity_ids for candidate in candidates)


def _has_external_reply_sender_evidence(event):
    sender_id = _safe_int((event or {}).get("reply_to_sender_id"))
    return sender_id != 0 and not _reply_sender_maps_to_identity(sender_id)


def _is_weak_owner_hint_gap(event):
    return (
        str((event or {}).get("reason") or "") == "no_reply_context"
        and not str((event or {}).get("family") or "").strip()
        and bool(_clean_text((event or {}).get("matched_text")))
    )


def replay_module_for_family(family):
    module_name = get_module_name_for_reply_family(family)
    manifest = get_module_manifest(module_name)
    replay_modules = tuple(getattr(manifest, "replay_modules", ()) or ())
    return replay_modules[0] if replay_modules else ""


def record_unhandled_routed_reply(event, text=None, routed_identity_id=None, matched_family=None, root_msg_id=None, *, event_kind="message", reply_to_sender_id=0):
    if isinstance(event, VerifiedGameEvent):
        verified_event = event
        text = verified_event.text
        routed_identity_id = verified_event.identity_id
        matched_family = verified_event.family
        root_msg_id = verified_event.root_msg_id
        event_kind = verified_event.event_type
        reply_to_sender_id = verified_event.reply_to_sender_id
        message_id = verified_event.msg_id
        chat_id = verified_event.chat_id
        route_source = verified_event.route_source or f"{event_kind}:reply_context"
    else:
        message_id = _safe_int(getattr(event, "id", 0))
        chat_id = _safe_int(getattr(event, "chat_id", 0))
        route_source = ""

    family = str(matched_family or "").strip()
    if not family:
        return False
    event_type = str(event_kind or "message").strip() or "message"
    return passive_inbox.record_passive_inbox_event(
        "skipped",
        module=get_module_name_for_reply_family(family),
        identity_id=routed_identity_id,
        reason=UNHANDLED_ROUTED_REPLY_REASON,
        summary=family,
        family=family,
        chat_id=chat_id,
        msg_id=message_id,
        reply_to_msg_id=_safe_int(root_msg_id),
        reply_to_sender_id=_safe_int(reply_to_sender_id),
        root_msg_id=_safe_int(root_msg_id),
        event_type=event_type,
        route_source=route_source or f"{event_type}:reply_context",
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


def _is_phaseful_prefix_reply(event):
    """Recognize long-running settlements emitted before the requested reply."""
    if not is_unhandled_routed_reply_event(event):
        return False
    if str(event.get("family") or "").strip() in {"yuanying", "deep_retreat"}:
        return False
    text = _clean_text(event.get("matched_text"))
    return (
        "元婴闭关结算" in text
        or ("元神归窍总结" in text and "神游" in text)
        or ("深度闭关总结" in text and "本次结算时长" in text)
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


def classify_message_contract_gap(event):
    if not is_message_contract_gap_event(event):
        return ""
    reason = str(event.get("reason") or "").strip()
    if reason == UNHANDLED_ROUTED_REPLY_REASON:
        return MESSAGE_CONTRACT_CLASS_HANDLER_GAP
    if reason in MESSAGE_CONTRACT_EXTERNAL_REASONS:
        return MESSAGE_CONTRACT_CLASS_EXTERNAL_OBSERVATION
    if reason in MESSAGE_CONTRACT_UNRESOLVED_IDENTITY_REASONS and _has_external_reply_sender_evidence(event):
        return MESSAGE_CONTRACT_CLASS_EXTERNAL_OBSERVATION
    if _is_weak_owner_hint_gap(event):
        return MESSAGE_CONTRACT_CLASS_WEAK_OWNER_HINT
    if reason in MESSAGE_CONTRACT_UNRESOLVED_IDENTITY_REASONS:
        return MESSAGE_CONTRACT_CLASS_UNRESOLVED_IDENTITY
    return MESSAGE_CONTRACT_CLASS_OTHER


def _routed_reply_resolution_key(event):
    if not isinstance(event, dict):
        return None
    msg_id = _safe_int(event.get("source_message_id") or event.get("msg_id") or event.get("message_id"))
    family = str(event.get("family") or "").strip()
    identity_id = _safe_int(event.get("identity_id"))
    if msg_id <= 0 or not family or identity_id <= 0:
        return None
    return identity_id, family, msg_id


def _message_box_fact_resolution_key(fact):
    if fact is None:
        return None
    context = getattr(fact, "reply_context", None)
    context = context if isinstance(context, dict) else {}
    msg_id = _safe_int(getattr(fact, "msg_id", 0))
    family = str(getattr(fact, "family", "") or context.get("family") or "").strip()
    identity_id = _safe_int(getattr(fact, "identity_id", 0) or context.get("send_as_id"))
    if msg_id <= 0 or not family or identity_id <= 0:
        return None
    return identity_id, family, msg_id


def _iter_message_box_shadow_facts(source, *, include_edits=True):
    if source is None:
        return
    if hasattr(source, "snapshot"):
        source = source.snapshot()
    if hasattr(source, "scan_after_seq"):
        yield from source.scan_after_seq(None, include_edits=include_edits)
        return
    for item in source:
        if getattr(item, "is_edit", False) and not include_edits:
            continue
        yield item


def _message_box_fact_payload(fact):
    context = getattr(fact, "reply_context", None)
    context = context if isinstance(context, dict) else {}
    return {
        "identity_id": _safe_int(getattr(fact, "identity_id", 0) or context.get("send_as_id")),
        "family": str(getattr(fact, "family", "") or context.get("family") or "").strip(),
        "msg_id": _safe_int(getattr(fact, "msg_id", 0)),
        "chat_id": _safe_int(getattr(fact, "chat_id", 0)),
        "event_type": str(getattr(fact, "event_type", "") or "").strip() or "message",
        "route_source": str(getattr(fact, "route_source", "") or "").strip(),
        "reply_to_msg_id": _safe_int(getattr(fact, "reply_to_msg_id", 0) or context.get("reply_to_msg_id")),
        "reply_to_sender_id": _safe_int(getattr(fact, "reply_to_sender_id", 0) or context.get("reply_to_sender_id")),
        "root_msg_id": _safe_int(getattr(fact, "root_msg_id", 0) or context.get("root_msg_id")),
        "matched_text": _clean_text(getattr(fact, "raw_text", "")),
        "text_hash": str(getattr(fact, "text_hash", "") or "").strip(),
    }


def _classify_message_box_shadow_events(events):
    if not events:
        return MESSAGE_BOX_SHADOW_STATUS_MISSING
    if any(str(event.get("kind") or "") == "changed" for event in events):
        return MESSAGE_BOX_SHADOW_STATUS_CHANGED
    if any(is_unhandled_routed_reply_event(event) for event in events):
        return MESSAGE_BOX_SHADOW_STATUS_UNHANDLED
    if any(str(event.get("reason") or "") == "no_change" for event in events):
        return MESSAGE_BOX_SHADOW_STATUS_NO_CHANGE
    if any(is_message_contract_gap_event(event) for event in events):
        return MESSAGE_BOX_SHADOW_STATUS_GAP
    return MESSAGE_BOX_SHADOW_STATUS_OBSERVED


def summarize_message_box_shadow_alignment(message_box_source, passive_events, *, include_edits=True, latest_limit=8):
    passive_events = list(passive_events or [])
    event_index = defaultdict(list)
    for event in passive_events:
        key = _routed_reply_resolution_key(event)
        if key:
            event_index[key].append(event)

    observed_total = 0
    routeable = OrderedDict()
    for fact in _iter_message_box_shadow_facts(message_box_source, include_edits=include_edits):
        observed_total += 1
        key = _message_box_fact_resolution_key(fact)
        if not key:
            continue
        routeable[key] = _message_box_fact_payload(fact)

    by_status = Counter()
    by_family = Counter()
    latest_missing = []
    for key, payload in routeable.items():
        status = _classify_message_box_shadow_events(event_index.get(key) or [])
        by_status[status] += 1
        by_family[payload["family"]] += 1
        if status == MESSAGE_BOX_SHADOW_STATUS_MISSING:
            latest_missing.append(payload)

    latest_limit = max(1, int(latest_limit or 1))
    latest_missing = sorted(
        latest_missing,
        key=lambda item: (_safe_int(item.get("msg_id")), str(item.get("family") or "")),
    )[-latest_limit:]
    return {
        "observed_total": observed_total,
        "routeable_total": len(routeable),
        "passive_event_total": len(passive_events),
        "matched_total": len(routeable) - int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_MISSING, 0) or 0),
        "missing_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_MISSING, 0) or 0),
        "changed_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_CHANGED, 0) or 0),
        "unhandled_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_UNHANDLED, 0) or 0),
        "no_change_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_NO_CHANGE, 0) or 0),
        "gap_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_GAP, 0) or 0),
        "observed_only_total": int(by_status.get(MESSAGE_BOX_SHADOW_STATUS_OBSERVED, 0) or 0),
        "by_status": dict(sorted(by_status.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_family": dict(sorted(by_family.items(), key=lambda pair: (-pair[1], pair[0]))),
        "latest_missing": latest_missing,
    }


def format_message_box_shadow_alignment(summary, *, latest_limit=8):
    summary = summary if isinstance(summary, dict) else {}
    lines = [
        "📦 MessageBox shadow 对账",
        "- 只读：不自动处理，不发送游戏命令。",
        "- missing 只表示 shadow 里有可路由事实，但 passive ledger 没有 changed/skipped 证据。",
        f"- shadow 观察：{_safe_int(summary.get('observed_total'))}",
        f"- 可路由事实：{_safe_int(summary.get('routeable_total'))}",
        f"- 已匹配 ledger：{_safe_int(summary.get('matched_total'))}",
        f"- 缺失 ledger 证据：{_safe_int(summary.get('missing_total'))}",
        f"- 状态分布：{_format_counter(summary.get('by_status') or {})}",
        f"- family 分布：{_format_counter(summary.get('by_family') or {})}",
    ]
    latest = list(summary.get("latest_missing") or [])[-max(1, int(latest_limit or 1)):]
    if latest:
        lines.append("- 最近 missing：")
        for item in latest:
            family = str(item.get("family") or "unknown")
            identity_id = _safe_int(item.get("identity_id"))
            msg_id = _safe_int(item.get("msg_id"))
            reply_to = _safe_int(item.get("reply_to_msg_id") or item.get("root_msg_id"))
            event_type = str(item.get("event_type") or "message")
            excerpt = _compact_excerpt(item.get("matched_text"))
            lines.append(f"  {family} identity={identity_id} msg={msg_id} reply={reply_to} type={event_type} | {excerpt}")
    return "\n".join(lines)


def _handled_routed_reply_keys(events):
    handled = set()
    for event in events:
        if str(event.get("kind") or "") != "changed":
            continue
        key = _routed_reply_resolution_key(event)
        if key:
            handled.add(key)
    return handled


def _is_resolved_unhandled_routed_reply(event, handled_keys):
    return is_unhandled_routed_reply_event(event) and _routed_reply_resolution_key(event) in handled_keys


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
    events = passive_event_ledger.iter_passive_events(path=path, limit=limit)
    handled_keys = _handled_routed_reply_keys(events)
    for event in events:
        if _is_phaseful_prefix_reply(event):
            continue
        if _is_resolved_unhandled_routed_reply(event, handled_keys):
            continue
        if _filter_contract_event(event, module=module, family=family, identity_id=identity_id, reason=reason):
            yield event


def iter_unhandled_routed_replies(path=None, limit=100, *, module="", family="", identity_id=0):
    module = str(module or "").strip()
    family = str(family or "").strip()
    identity_id = _safe_int(identity_id)
    events = passive_event_ledger.iter_passive_events(path=path, limit=limit)
    handled_keys = _handled_routed_reply_keys(events)
    for event in events:
        if not is_unhandled_routed_reply_event(event):
            continue
        if _is_phaseful_prefix_reply(event):
            continue
        if _is_resolved_unhandled_routed_reply(event, handled_keys):
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
    by_class = Counter(classify_message_contract_gap(event) or "unknown" for event in items)
    by_module = Counter(_event_module_name(event) for event in items)
    by_family = Counter(str(event.get("family") or "unknown") for event in items)
    latest = sorted(items, key=lambda item: (_safe_int(item.get("ts")), _safe_int(item.get("msg_id"))))[-max(1, int(latest_limit or 1)):]
    external_observation_total = int(by_class.get(MESSAGE_CONTRACT_CLASS_EXTERNAL_OBSERVATION, 0) or 0)
    weak_owner_hint_total = int(by_class.get(MESSAGE_CONTRACT_CLASS_WEAK_OWNER_HINT, 0) or 0)
    needs_attention_total = len(items) - external_observation_total - weak_owner_hint_total
    return {
        "total": len(items),
        "needs_attention_total": needs_attention_total,
        "external_observation_total": external_observation_total,
        "weak_owner_hint_total": weak_owner_hint_total,
        "by_class": dict(sorted(by_class.items(), key=lambda pair: (-pair[1], pair[0]))),
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
        (
            f"- 待修/待归因：{summary.get('needs_attention_total', summary['total'])}；"
            f"外部观察：{summary.get('external_observation_total', 0)}；"
            f"弱归属：{summary.get('weak_owner_hint_total', 0)}"
        ),
        f"- 未匹配 handler：{unhandled['total']}",
        f"- 按分类：{_format_counter(summary.get('by_class') or {})}",
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
    "VerifiedGameEvent",
    "build_replay_sample_suggestion",
    "classify_message_contract_gap",
    "dumps_replay_sample_suggestion",
    "format_unhandled_reply_line",
    "from_telegram_event",
    "get_message_contract_status_text",
    "is_message_contract_gap_event",
    "is_unhandled_routed_reply_event",
    "iter_message_contract_gaps",
    "iter_unhandled_routed_replies",
    "record_unhandled_routed_reply",
    "replay_module_for_family",
    "summarize_message_box_shadow_alignment",
    "summarize_message_contract_gaps",
    "summarize_unhandled_routed_replies",
]
