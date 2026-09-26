import hashlib
import json
import os
import re
import tempfile
import time
from types import SimpleNamespace

from ..config import STATE_DIR
from ..action_guard import close_by_family as close_action_guard_by_family
from ..persistence import save_state
from ..message_keys import find_message_key
from ..state import get_identity_account, get_identity_ids, get_identity_state, get_send_as_profile, get_send_as_tags, has_identity, state, use_identity
from ..timing import get_day_key, has_wait_time, parse_wait_time
from ..verified_event import VerifiedGameEvent, from_telegram_event, telegram_event_timestamp
from . import checkin as checkin_mod
from . import concubine as concubine_mod
from . import heavenly_ban as heavenly_ban_mod
from . import hehuan as hehuan_mod
from . import pet as pet_mod
from . import second_soul as second_soul_mod
from . import small_world as small_world_mod
from . import stargazer as stargazer_mod
from . import storage_bag as storage_bag_mod
from . import tianxing as tianxing_mod
from . import tianti as tianti_mod
from . import tower as tower_mod
from . import tree as tree_mod
from . import yinluo as yinluo_mod
from .passive_event_ledger import append_passive_event


PASSIVE_INBOX_RECENT_LIMIT = 20
PASSIVE_INBOX_CONTRACT_GAP_SCAN_LIMIT = 500
PASSIVE_INBOX_STATS_FILE = os.path.join(STATE_DIR, "passive_inbox_stats.json")
PASSIVE_INBOX_RECENT_FIELD_LIMIT = 120
PASSIVE_INBOX_NOISY_SKIP_REASONS = {
    "no_identity",
    "no_reply_context",
    "reply_context_no_identity",
    "external_identity_no_match",
    "external_owner_no_match",
}
PASSIVE_INBOX_OBSERVED_TTL_SEC = 10 * 60
RE_AT_MENTION = re.compile(r"@([^\s\r\n\t，。！？；：、,.!?;:()（）\[\]【】<>《》]+)")
RE_YOU_MARKER = re.compile(r"[(（]\s*你\s*[)）]")
_PASSIVE_STATS_DEFAULT = {
    "total": 0,
    "changed": 0,
    "skipped": 0,
    "modules": {},
    "skip_reasons": {},
    "recent": [],
}
_passive_stats = dict(_PASSIVE_STATS_DEFAULT)
_observed_passive_events = {}


def _load_passive_stats():
    global _passive_stats
    try:
        with open(PASSIVE_INBOX_STATS_FILE, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(raw, dict):
        return
    loaded = dict(_PASSIVE_STATS_DEFAULT)
    for key in ("total", "changed", "skipped"):
        try:
            loaded[key] = int(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            loaded[key] = 0
    loaded["modules"] = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    loaded["skip_reasons"] = raw.get("skip_reasons") if isinstance(raw.get("skip_reasons"), dict) else {}
    loaded["recent"] = raw.get("recent") if isinstance(raw.get("recent"), list) else []
    loaded["recent"] = loaded["recent"][-PASSIVE_INBOX_RECENT_LIMIT:]
    _passive_stats = loaded


def _save_passive_stats():
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".passive_inbox_stats.", suffix=".tmp", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(_get_passive_stats_payload(), fp, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, PASSIVE_INBOX_STATS_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


_load_passive_stats()


def _empty_contract_gap_summary():
    return {
        "total": 0,
        "needs_attention_total": 0,
        "external_observation_total": 0,
        "weak_owner_hint_total": 0,
        "by_class": {},
        "by_reason": {},
        "by_module": {},
        "by_family": {},
        "latest": [],
    }


def _build_contract_gap_summary(limit=PASSIVE_INBOX_CONTRACT_GAP_SCAN_LIMIT, latest_limit=PASSIVE_INBOX_RECENT_LIMIT):
    try:
        from ..message_contract import iter_message_contract_gaps, summarize_message_contract_gaps
    except Exception:
        return _empty_contract_gap_summary()
    try:
        events = list(iter_message_contract_gaps(limit=limit))
        summary = summarize_message_contract_gaps(events, latest_limit=latest_limit)
    except Exception:
        return _empty_contract_gap_summary()
    return summary


def _bump_counter(bucket, key, amount=1):
    normalized = str(key or "unknown").strip() or "unknown"
    bucket[normalized] = int(bucket.get(normalized, 0) or 0) + int(amount or 1)


def _truncate_event_text(value, limit=PASSIVE_INBOX_RECENT_FIELD_LIMIT):
    text = str(value or "").replace("\r", "\n").strip()
    if not text:
        return ""
    text = " / ".join(part.strip() for part in text.splitlines() if part.strip())
    return text[: int(limit or PASSIVE_INBOX_RECENT_FIELD_LIMIT)]


def _event_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _event_text_hash(text):
    raw_text = str(text or "")
    if not raw_text:
        return ""
    return hashlib.sha256(raw_text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _gc_observed_passive_events(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _observed_passive_events.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _observed_passive_events.pop(key, None)


def _mark_observed_passive_event(chat_id=0, msg_id=0, text="", now=None):
    chat_id = _event_int(chat_id)
    msg_id = _event_int(msg_id)
    if chat_id == 0 or msg_id <= 0:
        return True
    text_hash = _event_text_hash(text)
    if not text_hash:
        return True
    now = float(now if now is not None else time.time())
    _gc_observed_passive_events(now)
    key = (chat_id, msg_id, text_hash)
    if float(_observed_passive_events.get(key, 0) or 0) > now:
        return False
    _observed_passive_events[key] = now + PASSIVE_INBOX_OBSERVED_TTL_SEC
    return True


def _append_recent_passive_event(kind, *, module="", identity_id=0, reason="", summary="", include_recent=None, **metadata):
    if include_recent is None:
        include_recent = not (kind != "changed" and reason in PASSIVE_INBOX_NOISY_SKIP_REASONS and not module)
    if not include_recent:
        return
    item = {
        "ts": float(time.time()),
        "kind": str(kind or ""),
        "module": str(module or ""),
        "identity_id": _event_int(identity_id),
        "reason": str(reason or ""),
        "summary": _truncate_event_text(summary),
    }
    for key, value in metadata.items():
        if key in {"msg_id", "reply_to_msg_id", "reply_to_sender_id", "root_msg_id", "source_message_id"}:
            int_value = _event_int(value)
            if int_value:
                item[key] = int_value
            continue
        text_value = _truncate_event_text(value)
        if text_value:
            item[key] = text_value
    recent = _passive_stats.setdefault("recent", [])
    recent.append(item)
    del recent[:-PASSIVE_INBOX_RECENT_LIMIT]


def _record_passive_event(
    kind,
    *,
    module="",
    identity_id=0,
    reason="",
    summary="",
    family="",
    chat_id=0,
    msg_id=0,
    reply_to_msg_id=0,
    reply_to_sender_id=0,
    root_msg_id=0,
    event_type="",
    route_source="",
    matched_text="",
    decision="",
    state_before="",
    state_after="",
    command="",
    source_message_id=0,
    include_recent=None,
):
    _passive_stats["total"] = int(_passive_stats.get("total", 0) or 0) + 1
    if kind == "changed":
        _passive_stats["changed"] = int(_passive_stats.get("changed", 0) or 0) + 1
        _bump_counter(_passive_stats["modules"], module or "unknown")
    else:
        _passive_stats["skipped"] = int(_passive_stats.get("skipped", 0) or 0) + 1
        _bump_counter(_passive_stats["skip_reasons"], reason or "unknown")

    _append_recent_passive_event(
        kind,
        module=module,
        identity_id=identity_id,
        reason=reason,
        summary=summary,
        include_recent=include_recent,
        family=family,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        reply_to_sender_id=reply_to_sender_id,
        root_msg_id=root_msg_id,
        route_source=route_source,
        matched_text=matched_text,
        decision=decision,
        state_before=state_before,
        state_after=state_after,
        command=command,
        source_message_id=source_message_id,
    )
    append_passive_event(
        kind=kind,
        module=module,
        identity_id=identity_id,
        reason=reason,
        summary=summary,
        family=family,
        chat_id=chat_id,
        msg_id=msg_id,
        reply_to_msg_id=reply_to_msg_id,
        reply_to_sender_id=reply_to_sender_id,
        root_msg_id=root_msg_id,
        event_type=event_type,
        route_source=route_source,
        matched_text=matched_text,
        matched_text_hash=_event_text_hash(matched_text),
        decision=decision,
        state_before=state_before,
        state_after=state_after,
        command=command,
        source_message_id=source_message_id,
    )
    _save_passive_stats()


def record_passive_inbox_event(
    kind,
    *,
    module="",
    identity_id=0,
    reason="",
    summary="",
    family="",
    chat_id=0,
    msg_id=0,
    reply_to_msg_id=0,
    reply_to_sender_id=0,
    root_msg_id=0,
    event_type="",
    route_source="",
    matched_text="",
    decision="",
    state_before="",
    state_after="",
    command="",
    source_message_id=0,
    include_recent=None,
):
    try:
        _record_passive_event(
            kind,
            module=module,
            identity_id=identity_id,
            reason=reason,
            summary=summary,
            family=family,
            chat_id=chat_id,
            msg_id=msg_id,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_sender_id=reply_to_sender_id,
            root_msg_id=root_msg_id,
            event_type=event_type,
            route_source=route_source,
            matched_text=matched_text,
            decision=decision,
            state_before=state_before,
            state_after=state_after,
            command=command,
            source_message_id=source_message_id,
            include_recent=include_recent,
        )
    except Exception:
        return False
    return True


def _get_passive_stats_payload():
    return {
        "total": int(_passive_stats.get("total", 0) or 0),
        "changed": int(_passive_stats.get("changed", 0) or 0),
        "skipped": int(_passive_stats.get("skipped", 0) or 0),
        "modules": dict(_passive_stats.get("modules") or {}),
        "skip_reasons": dict(_passive_stats.get("skip_reasons") or {}),
        "recent": list(_passive_stats.get("recent") or []),
    }


def get_passive_inbox_snapshot():
    contract_gap_summary = _build_contract_gap_summary()
    snapshot = {
        **_get_passive_stats_payload(),
        "attention_total": int(contract_gap_summary.get("needs_attention_total", 0) or 0),
        "attention_external_observation_total": int(contract_gap_summary.get("external_observation_total", 0) or 0),
        "attention_weak_owner_hint_total": int(contract_gap_summary.get("weak_owner_hint_total", 0) or 0),
        "attention_by_class": dict(contract_gap_summary.get("by_class") or {}),
        "attention_by_reason": dict(contract_gap_summary.get("by_reason") or {}),
        "attention_by_module": dict(contract_gap_summary.get("by_module") or {}),
        "attention_by_family": dict(contract_gap_summary.get("by_family") or {}),
        "attention_recent": list(contract_gap_summary.get("latest") or []),
        "contract_gap_summary": contract_gap_summary,
    }
    return snapshot


def get_passive_inbox_status_text():
    snapshot = get_passive_inbox_snapshot()

    def format_map(items):
        if not items:
            return "无"
        ordered = sorted(items.items(), key=lambda pair: (-int(pair[1] or 0), str(pair[0])))
        return "、".join(f"{key}:{value}" for key, value in ordered[:8])

    def format_recent_detail(item):
        parts = []
        identity_id = int(item.get("identity_id") or 0)
        if identity_id:
            parts.append(str(identity_id))
        for key, label in (
            ("family", "family"),
            ("decision", "decision"),
            ("msg_id", "msg"),
            ("reply_to_msg_id", "reply"),
            ("source_message_id", "source"),
            ("route_source", "route"),
            ("matched_text", "hit"),
            ("summary", ""),
        ):
            value = item.get(key)
            if value in (None, ""):
                continue
            if label:
                parts.append(f"{label}={value}")
            else:
                parts.append(str(value))
        return "｜".join(parts)

    lines = [
        "📥 消息盒子",
        f"- 总处理：{snapshot['total']}",
        f"- 成功更新：{snapshot['changed']}",
        f"- 跳过：{snapshot['skipped']}",
        f"- 待关注：{snapshot.get('attention_total', 0)}",
        f"- 命中模块：{format_map(snapshot.get('modules') or {})}",
        f"- 跳过原因：{format_map(snapshot.get('skip_reasons') or {})}",
    ]
    attention = snapshot.get("contract_gap_summary") or {}
    if attention:
        lines.append(f"- 关注分类：{format_map(attention.get('by_class') or {})}")
    recent = snapshot.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-8:]:
            kind = "更新" if item.get("kind") == "changed" else "跳过"
            subject = item.get("module") or item.get("reason") or "unknown"
            detail = format_recent_detail(item)
            lines.append(f"  {kind} {subject}{'｜' + detail if detail else ''}")
    return "\n".join(lines)


def _normalize_tag(text):
    return str(text or "").strip().lstrip("@").casefold()


def _normalize_loose_identity_text(text):
    return "".join(str(text or "").strip().lstrip("@").split()).casefold()


def _extract_at_mentions(text):
    mentions = set()
    for match in RE_AT_MENTION.finditer(str(text or "")):
        mention = _normalize_tag(match.group(1))
        if mention:
            mentions.add(mention)
    return mentions


def _match_identity_by_owner_name(owner_name):
    owner_key = _normalize_tag(owner_name)
    if not owner_key:
        return None
    matched = []
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidates = [
            profile.get("daohao"),
            profile.get("label"),
            profile.get("username"),
            *get_send_as_tags(identity_id),
        ]
        if owner_key in {_normalize_tag(item) for item in candidates if str(item or "").strip()}:
            matched.append(identity_id)
    return matched[0] if len(matched) == 1 else None


def _match_identity_by_at_text(text):
    mentions = _extract_at_mentions(text)
    if not mentions:
        return None
    matched = []
    for identity_id in get_identity_ids():
        tags = get_send_as_tags(identity_id) or []
        identity_tags = {_normalize_tag(tag) for tag in tags if _normalize_tag(tag)}
        if identity_tags & mentions:
            matched.append(identity_id)
    matched = sorted({int(identity_id) for identity_id in matched})
    return matched[0] if len(matched) == 1 else None


def _match_identity_by_you_line(text):
    matched = []
    for raw_line in str(text or "").splitlines():
        if not RE_YOU_MARKER.search(raw_line):
            continue
        line_key = _normalize_loose_identity_text(RE_YOU_MARKER.sub("", raw_line))
        if not line_key:
            continue
        for identity_id in get_identity_ids():
            candidates = []
            profile = get_send_as_profile(identity_id)
            candidates.extend(get_send_as_tags(identity_id) or [])
            candidates.extend([
                profile.get("username"),
                profile.get("label"),
                profile.get("daohao"),
            ])
            for candidate in candidates:
                candidate_key = _normalize_loose_identity_text(candidate)
                if len(candidate_key) < 3:
                    continue
                if candidate_key in line_key:
                    matched.append(int(identity_id))
                    break
    matched = sorted(set(matched))
    return matched[0] if len(matched) == 1 else None


def _resolve_owner_hint(raw_text):
    raw_text = str(raw_text or "")
    owner_match = small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text)
    if owner_match:
        return True, _match_identity_by_owner_name(owner_match.group("owner")), "owner_name"
    banner_match = yinluo_mod.RE_BANNER_TITLE.search(raw_text)
    if banner_match:
        return True, _match_identity_by_owner_name(banner_match.group("owner")), "owner_name"
    return False, None, ""


def _identity_from_reply_context(reply_context):
    try:
        identity_id = int((reply_context or {}).get("send_as_id") or 0)
    except (TypeError, ValueError):
        identity_id = 0
    return identity_id if identity_id > 0 else None


def _identity_from_reply_sender_id(sender_id):
    sender_id = _event_int(sender_id)
    if sender_id == 0:
        return None
    candidates = [sender_id]
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            candidates.append(_event_int(sender_abs[3:]))
    identity_ids = {int(identity_id) for identity_id in get_identity_ids()}
    for candidate in candidates:
        if int(candidate or 0) in identity_ids:
            return int(candidate)
    return None


def _context_msg_id(reply_context, key):
    try:
        return int((reply_context or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _looks_like_concubine_heart_reply(text):
    raw_text = str(text or "")
    return (
        "【坠魔心劫·" in raw_text
        or concubine_mod._is_heart_anchor_lost_text(raw_text)
        or "心劫余波未散" in raw_text
        or "心劫抉择正在进行" in raw_text
        or "你已有一场心劫抉择正在进行" in raw_text
        or "请回复一条包含侍妾/道侣内容的消息" in raw_text
        or ("修为不足" in raw_text and "开启共历心劫" in raw_text)
    )




def _concubine_pending_context_specs(family):
    family = str(family or "").strip()
    specs = {
        "concubine_greet": (
            {
                "state_key": "concubine_greet_msg_id",
                "phases": {"greet_pending"},
                "handler": concubine_mod.handle_concubine_greet_reply,
            },
        ),
        "storage_bag": (
            {
                "state_key": "concubine_gift_bag_msg_id",
                "phases": {"gift_bag_pending"},
                "handler": concubine_mod.handle_concubine_storage_bag_reply,
                "family": "storage_bag",
            },
        ),
        "concubine_gift": (
            {
                "state_key": "concubine_gift_msg_id",
                "phases": {"gift_pending"},
                "handler": concubine_mod.handle_concubine_gift_reply,
            },
        ),
        "concubine_reacquire": (
            {
                "state_key": "concubine_reacquire_msg_id",
                "phases": {"reacquire_pending"},
                "handler": concubine_mod.handle_concubine_reacquire_reply,
            },
        ),
        "concubine_tianji": (
            {
                "state_key": "concubine_tianji_msg_id",
                "phases": {"tianji_pending"},
                "handler": concubine_mod.handle_concubine_tianji_reply,
            },
        ),
    }
    return specs.get(family, ())


def _resolve_concubine_pending_identity_from_context(family, reply_context):
    specs = _concubine_pending_context_specs(family)
    if not specs:
        return None, "", None
    reply_to_msg_id = _context_msg_id(reply_context, "reply_to_msg_id")
    root_msg_id = _context_msg_id(reply_context, "root_msg_id")
    context_ids = {msg_id for msg_id in (reply_to_msg_id, root_msg_id) if msg_id > 0}
    if not context_ids:
        return None, "", None

    matched = []
    for identity_id in get_identity_ids():
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        phase = str(identity_state.get("concubine_phase") or "").strip()
        for spec in specs:
            if phase not in (spec.get("phases") or set()):
                continue
            tracked_msg_id = int(identity_state.get(spec.get("state_key") or "", 0) or 0)
            if tracked_msg_id > 0 and tracked_msg_id in context_ids:
                matched.append((int(identity_id), spec))
    identity_ids = sorted({identity_id for identity_id, _spec in matched})
    if len(identity_ids) != 1:
        return None, "", None
    selected_id = identity_ids[0]
    selected_specs = [spec for identity_id, spec in matched if identity_id == selected_id]
    if len(selected_specs) != 1:
        return None, "", None
    return selected_id, "concubine_pending_chain", selected_specs[0]


def _family_from_reply_context(reply_context):
    return str((reply_context or {}).get("family") or "").strip()


def _routed_reply_already_handled(reply_context):
    return bool((reply_context or {}).get("routed_reply_handled"))


def _route_source(event_type, route):
    route = str(route or "").strip()
    event_type = str(event_type or "").strip()
    if not route:
        return event_type
    return f"{event_type}:{route}" if event_type else route


def _resolve_passive_text_identity(raw_text, family):
    raw_text = str(raw_text or "")
    family = str(family or "").strip()
    if not _looks_like_supported_passive(raw_text, family):
        return None, ""

    has_owner_hint, target_id, owner_route = _resolve_owner_hint(raw_text)
    if has_owner_hint:
        if target_id is not None:
            return target_id, owner_route
        return None, ""

    target_id = _match_identity_by_at_text(raw_text)
    if target_id is not None:
        return target_id, "passive_tag"
    return None, ""


def _has_passive_owner_hint(raw_text):
    raw_text = str(raw_text or "")
    return bool(small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text) or yinluo_mod.RE_BANNER_TITLE.search(raw_text))


def _missing_identity_reason(raw_text, family, reply_to_sender_id=0):
    if _has_passive_owner_hint(raw_text):
        return "external_owner_no_match"
    if _extract_at_mentions(raw_text):
        return "external_identity_no_match"
    if _event_int(reply_to_sender_id) and _identity_from_reply_sender_id(reply_to_sender_id) is None:
        return "external_identity_no_match"
    return "reply_context_no_identity" if str(family or "").strip() else "no_reply_context"


async def _apply_tianti_passive(text, now, family, reply_context, event, event_type):
    if _routed_reply_already_handled(reply_context):
        return False
    context = dict(
        reply_context or {},
        chat_id=_event_int(getattr(event, "chat_id", 0)),
        msg_id=_event_int(getattr(event, "id", 0)),
        sender_id=_event_int(getattr(event, "sender_id", 0)),
        server_event_at=telegram_event_timestamp(event, event_type),
    )
    root = _context_msg_id(context, "root_msg_id") or _context_msg_id(context, "reply_to_msg_id")
    context["root_msg_id"] = root
    reply_to = SimpleNamespace(
        id=root, chat_id=context["chat_id"], raw_text=context.get("reply_to_command", ""),
        sender_id=context.get("reply_to_sender_id", 0), server_event_at=context.get("reply_to_server_at", 0),
    )
    return await tianti_mod.handle_tianti_reply(
        text, now, reply_to, matched_family=family, reply_context=context,
    )


async def _apply_second_soul_passive(text, now, family, reply_context, event, event_type):
    if _routed_reply_already_handled(reply_context):
        return False
    context = dict(reply_context or {})
    context["chat_id"] = _event_int(getattr(event, "chat_id", 0))
    context["server_event_at"] = telegram_event_timestamp(event, event_type)
    root = _context_msg_id(context, "root_msg_id") or _context_msg_id(context, "reply_to_msg_id")
    reply_to = SimpleNamespace(id=root, chat_id=context["chat_id"], raw_text="")
    # Passive delivery uses the same receipt contract, never a second text reducer.
    for handler in (
        second_soul_mod.handle_second_soul_purge_reply,
        second_soul_mod.handle_second_soul_demon_status_reply,
        second_soul_mod.handle_second_soul_status_reply,
        second_soul_mod.handle_second_soul_train_reply,
    ):
        if await handler(text, now, reply_to, matched_family=family, reply_context=context):
            return True
    return False


def _apply_pet_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    if family == "pet" or pet_mod.RE_PET_TOUCH_SUCCESS.search(raw_text):
        if pet_mod.RE_PET_TOUCH_SUCCESS.search(raw_text):
            state["next_pet_time"] = float(now + pet_mod.PET_CD + pet_mod.CD_BUFFER_SEC)
            state["pet_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and any(keyword in raw_text for keyword in pet_mod.PET_CD_HINT_KEYWORDS):
            state["next_pet_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_last_error"] = ""
            changed = True
    if family == "pet_warm" or pet_mod.RE_PET_WARM_SUCCESS.search(raw_text) or "后再行温养" in raw_text:
        if pet_mod.RE_PET_WARM_SUCCESS.search(raw_text):
            state["next_pet_warm_time"] = float(now + pet_mod.PET_WARM_CD + 180)
            state["pet_warm_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and ("器灵方才吞纳过灵机" in raw_text or "后再行温养" in raw_text):
            state["next_pet_warm_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_warm_last_error"] = ""
            changed = True
    if family == "pet_trial" or pet_mod.RE_PET_TRIAL_SUCCESS.search(raw_text) or "后再启程" in raw_text:
        if pet_mod.RE_PET_TRIAL_SUCCESS.search(raw_text):
            state["next_pet_trial_time"] = float(now + pet_mod.PET_TRIAL_CD + pet_mod.CD_BUFFER_SEC)
            state["pet_trial_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and ("器灵试炼刚结束不久" in raw_text or "后再启程" in raw_text):
            state["next_pet_trial_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_trial_last_error"] = ""
            changed = True
    if family == "pet_formation" or pet_mod.RE_PET_FORMATION_SUCCESS.search(raw_text):
        if pet_mod.RE_PET_FORMATION_SUCCESS.search(raw_text):
            state["next_pet_formation_time"] = float(now + pet_mod.PET_FORMATION_BUFF_SEC)
            state["pet_formation_last_error"] = ""
            state["pet_formation_retry_count"] = 0
            changed = True
    return changed


def _is_script_small_world_query_reply(family, reply_context):
    if str(family or "").strip() != "small_world_query":
        return False
    reply_to_msg_id = _context_msg_id(reply_context, "reply_to_msg_id")
    if reply_to_msg_id <= 0:
        return False
    return find_message_key(
        state.get("my_msg_ids") or {}, reply_to_msg_id,
        chat_id=_context_msg_id(reply_context, "chat_id") or None,
    ) is not None


def _is_active_small_world_query_panel(reply_context):
    if str(state.get("small_world_phase") or "") != "query_pending":
        return False
    query_msg_id = int(state.get("small_world_query_msg_id", 0) or 0)
    reply_to_msg_id = _context_msg_id(reply_context, "reply_to_msg_id")
    root_msg_id = _context_msg_id(reply_context, "root_msg_id")
    if query_msg_id > 0:
        return query_msg_id in {reply_to_msg_id, root_msg_id}

    # send_game_command can race with the bot reply: the command is visible in
    # the message log, but the runtime has not stored small_world_query_msg_id
    # yet. If the panel has already been routed to this identity, use it to close
    # the active query instead of leaving query_pending until timeout.
    if reply_to_msg_id > 0 or root_msg_id > 0:
        return True
    return "等待小世界面板" in str(state.get("small_world_last_error") or "")


def _has_active_small_world_phase():
    phase = str(state.get("small_world_phase") or "idle")
    return phase.endswith("_pending") or phase in {"harvest_sent", "refine_sent"}


async def _apply_small_world_passive(text, now, family="", reply_context=None):
    raw_text = str(text or "")
    family = str(family or "").strip()
    if _routed_reply_already_handled(reply_context) and family.startswith("small_world_"):
        return False

    if family == "small_world_harvest":
        stock_match = small_world_mod.RE_HARVEST_STOCK.search(raw_text)
        if stock_match:
            state["small_world_incense_stock"] = int(stock_match.group(1))
            state["small_world_pending_incense"] = 0
            state["small_world_last_error"] = ""
            if str(state.get("small_world_phase") or "") in {"harvest_sent", "harvest_pending", "harvest_before_manifest_sent"}:
                small_world_mod._clear_chain_pending()
            return True
        shortage_match = small_world_mod.RE_STOCK_SHORTAGE.search(raw_text)
        if shortage_match:
            state["small_world_incense_stock"] = int(shortage_match.group(1))
            state["small_world_last_error"] = "收割香火库存不足"
            if str(state.get("small_world_phase") or "") in {"harvest_sent", "harvest_pending", "harvest_before_manifest_sent"}:
                small_world_mod._clear_chain_pending()
            return True

    panel = small_world_mod._parse_small_world_panel(raw_text)
    if not panel or panel.get("realm_blocked"):
        return False

    if _is_script_small_world_query_reply(family, reply_context):
        return False

    active_phase = _has_active_small_world_phase()
    if _is_active_small_world_query_panel(reply_context):
        return await small_world_mod._handle_panel_decision(now, panel)

    small_world_mod._apply_small_world_panel_snapshot(now, panel)
    if state.get("small_world_phase") == "calibration_wait":
        state["small_world_phase"] = "idle"
    if active_phase:
        state["small_world_last_error"] = ""
        return True

    if panel.get("prayer_data_error"):
        small_world_mod._schedule_panel_wait(
            now,
            small_world_mod.SMALL_WORLD_MANIFEST_CD_SEC + small_world_mod.CD_BUFFER_SEC,
        )
        state["small_world_phase"] = "idle"
    elif panel.get("has_wait"):
        small_world_mod._schedule_panel_wait(now, int(panel.get("wait_sec", 0) or 0) + small_world_mod.CD_BUFFER_SEC)
        state["small_world_phase"] = "idle"
    elif panel.get("has_prayer"):
        state["small_world_phase"] = "idle"
        if float(state.get("next_small_world_time", 0) or 0) <= float(now or 0):
            small_world_mod._schedule_next_cycle(now)
        state["small_world_last_error"] = "被动发现小世界祈愿，未自动显灵"
        return True
    elif float(state.get("next_small_world_time", 0) or 0) <= float(now or 0):
        state["small_world_phase"] = "idle"
        small_world_mod._schedule_next_cycle(now)
        state["small_world_last_error"] = ""
        return True
    state["small_world_last_error"] = ""
    return True


def _is_tree_panel_text(text):
    raw_text = str(text or "")
    return (
        "【落云宗 · 灵眼之树】" in raw_text
        or "落云宗·灵眼之树" in raw_text
        or tree_mod._is_tree_pulse_panel(raw_text)
    )


def _is_tree_mature_broadcast(text):
    raw_text = str(text or "")
    return "🍎 灵果已完全成熟！ 采摘期开启！" in raw_text and "📊 天道榜单已定格！" in raw_text


def _apply_tree_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    is_panel = _is_tree_panel_text(raw_text)
    pulse_panel = tree_mod.parse_tree_pulse_panel(raw_text)
    current_status_snapshot = "你的当前状态:" in raw_text or "你的当前状态：" in raw_text
    trusted_panel = family == "tree_panel" or (is_panel and current_status_snapshot and tree_mod._tree_panel_matches_current_identity(raw_text))

    if trusted_panel and pulse_panel:
        tree_mod._apply_tree_pulse_panel(pulse_panel, now)
        progress = float(pulse_panel.get("progress", 0.0) or 0.0)
        daily_used = int(pulse_panel.get("daily_used", 0) or 0)
        daily_limit = int(pulse_panel.get("daily_limit", 0) or 0)
        if progress >= 99.9 or pulse_panel.get("blocked"):
            state["is_maturing"] = True
            state["pending_irrigation"] = False
            state["next_irr_time"] = float(now + tree_mod.FREEZE_CD)
            state["tree_pulse_last_error"] = "灵树已成熟或遭劫难，停止定脉"
        elif daily_limit > 0 and daily_used >= daily_limit:
            state["tree_pulse_last_error"] = "今日定脉令已满"
        changed = True
        return changed

    if trusted_panel and is_panel:
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        if "成熟采摘期" in raw_text or _is_tree_mature_broadcast(raw_text):
            state["is_maturing"] = True
            state["pending_irrigation"] = False
            state["next_irr_time"] = float(now + tree_mod.FREEZE_CD)
            if "你的当前状态: 已采摘" in raw_text or "你的当前状态：已采摘" in raw_text:
                state["is_harvested"] = True
                state["tree_harvest_inflight_until"] = 0
            elif state.get("is_harvested"):
                state["is_harvested"] = False
                state["tree_harvest_inflight_until"] = 0
            changed = True
        else:
            if state.get("is_maturing"):
                state["is_maturing"] = False
                state["tree_maturing_logged"] = False
                state["tree_harvest_followup_due_at"] = 0
                if float(state.get("next_irr_time", 0) or 0) > now + 24 * 3600:
                    state["next_irr_time"] = now
                changed = True
            if state.get("is_harvested"):
                state["is_harvested"] = False
                state["tree_harvest_inflight_until"] = 0
                changed = True
            if state.get("pending_irrigation") and not state.get("is_invading"):
                state["pending_irrigation"] = False
                state["next_irr_time"] = now
                changed = True

    is_irrigation_reply = family == "tree_panel"
    is_pulse_reply = family == "tree_pulse"
    is_guard_reply = family == "tree_guard"
    if is_irrigation_reply and tree_mod._is_tree_legacy_disabled_prompt(raw_text):
        state["tree_pulse_mode_seen"] = True
        state["pending_irrigation"] = False
        state["tree_pulse_last_error"] = "旧灌溉已关闭，切换定脉"
        changed = True
    if is_pulse_reply and tree_mod._is_tree_pulse_blocked_prompt(raw_text):
        state["is_maturing"] = True
        state["pending_irrigation"] = False
        state["next_irr_time"] = float(now + tree_mod.FREEZE_CD)
        state["tree_pulse_last_error"] = "灵树已成熟或遭劫难，定脉停止"
        changed = True
    if is_pulse_reply and tree_mod._is_tree_pulse_action_success(raw_text):
        state["tree_pulse_last_error"] = "定脉回执已确认"
        changed = True
    if is_irrigation_reply and tree_mod._is_tree_irrigation_success(raw_text):
        tree_mod.reset_resource_shortage(tree_mod.TREE_IRRIGATION_RESOURCE_KEY)
        state["pending_irrigation"] = False
        changed = True
    if is_guard_reply and tree_mod._is_tree_guard_success(raw_text):
        tree_mod.reset_resource_shortage(tree_mod.TREE_GUARD_RESOURCE_KEY)
        changed = True

    if family in {"tree_panel", "tree_guard"} and has_wait_time(raw_text):
        wait_sec = parse_wait_time(raw_text)
        if wait_sec > 0:
            if family == "tree_guard" or "守山" in raw_text or "协同" in raw_text or "大阵注入灵力" in raw_text:
                tree_mod.reset_resource_shortage(tree_mod.TREE_GUARD_RESOURCE_KEY)
                state["next_guard_time"] = float(now + wait_sec + tree_mod.CD_BUFFER_SEC)
                changed = True
            elif family == "tree_panel" or "灌溉" in raw_text:
                tree_mod.reset_resource_shortage(tree_mod.TREE_IRRIGATION_RESOURCE_KEY)
                state["next_irr_time"] = float(now + wait_sec + tree_mod.CD_BUFFER_SEC)
                changed = True
            elif family == "tree_pulse" or "定脉" in raw_text:
                tree_mod.reset_resource_shortage(tree_mod.TREE_PULSE_RESOURCE_KEY)
                state["next_irr_time"] = float(now + wait_sec + tree_mod.CD_BUFFER_SEC)
                changed = True

    if _is_tree_mature_broadcast(raw_text):
        # Broadcasts are intentionally observational here. The active tree
        # handler owns all global harvest decisions to avoid passive-triggered chains.
        return changed
    return changed


def _apply_storage_bag_passive(text, now):
    parsed = storage_bag_mod.parse_storage_bag_reply(text)
    if not parsed:
        return False, None
    identity_id = storage_bag_mod.resolve_storage_bag_identity_id(parsed.get("owner"))
    if identity_id <= 0:
        return False, None
    records = storage_bag_mod.get_storage_bag_records()
    records[str(identity_id)] = {
        "identity_id": identity_id,
        "label": storage_bag_mod._get_storage_bag_identity_label(identity_id, parsed),
        "owner": parsed.get("owner") or "",
        "owner_username": parsed.get("owner_username") or "",
        "updated_at": float(now or 0),
        "updated_at_text": storage_bag_mod.fmt_abs_ts(float(now or 0)),
        "items": parsed.get("items") or {},
        "sections": parsed.get("sections") or {},
        "empty": bool(parsed.get("empty")),
    }
    storage_bag_mod.set_storage_bag_records(records)
    save_state()
    return True, identity_id


def _apply_tower_passive(text, now, family):
    if family != "tower":
        return False
    raw_text = str(text or "")
    if "【琉璃问心塔】" in raw_text or "【试炼古塔" in raw_text or any(keyword in raw_text for keyword in tower_mod.TOWER_DONE_HINTS):
        tower_mod._mark_tower_done_today(now)
        return True
    if "闯塔" in raw_text or "塔" in raw_text:
        if float(state.get("next_tower_time", 0) or 0) <= now:
            tower_mod.schedule_next_tower(now, persist=False)
        return True
    return False


async def _apply_checkin_passive(text, now, family, reply_context=None, *, chat_id=0, event=None, event_type="message"):
    raw_text = str(text or "")
    reply_id = _context_msg_id(reply_context, "reply_to_msg_id") or _context_msg_id(reply_context, "root_msg_id")
    chat_id = int(chat_id or _context_msg_id(reply_context, "chat_id"))
    if family == "checkin":
        if checkin_mod.is_no_sect_checkin_text(raw_text):
            return bool(await checkin_mod._apply_no_sect_checkin(
                raw_text, now, reply_context=reply_context, event=event, event_type=event_type,
            ))
        if not checkin_mod.is_checkin_completion_text(raw_text):
            return False
        return checkin_mod.apply_checkin_completion(now, reply_id, chat_id=chat_id)
    if family == "sect_teach":
        return await checkin_mod.apply_sect_teach_reply(raw_text, now, reply_id, chat_id=chat_id)
    return False


def _apply_stargazer_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    followup_due_at = float(state.get("stargazer_followup_due_at", 0) or 0)
    queued_action = str(state.get("stargazer_queued_action") or "").strip()
    last_action = str(state.get("stargazer_last_action") or "")
    soothe_done = family == "stargazer_soothe" and (
        stargazer_mod._is_stargazer_soothe_success(raw_text)
        or stargazer_mod._is_stargazer_soothe_no_need(raw_text)
    )
    allow_soothe_recheck = soothe_done and (
        queued_action == "collect"
        or last_action == "queue_collect"
        or bool(state.get("stargazer_soothe_before_collect"))
    )
    if followup_due_at > now and last_action.startswith("queue_") and family not in {"stargazer_panel", "stargazer_sync"} and not allow_soothe_recheck:
        return False
    if family in {"stargazer_panel", "stargazer_sync"}:
        parsed = stargazer_mod._parse_stargazer_panel(raw_text)
        if not parsed:
            return False
        stargazer_mod._sync_stargazer_panel_state(parsed, now)
        if int(parsed.get("dim_slot_count", 0) or 0) > 0:
            state["stargazer_wait_full_collect"] = False
            stargazer_mod._clear_stargazer_collect_flags()
            stargazer_mod._queue_stargazer_followup_action(now, "soothe", 5)
            state["stargazer_last_action"] = "passive_dim_slot"
        elif parsed.get("all_ready"):
            state["stargazer_wait_full_collect"] = False
            stargazer_mod._clear_stargazer_collect_flags()
            stargazer_mod._queue_stargazer_followup_action(now, "collect", 5)
            state["stargazer_last_action"] = "passive_all_ready"
        elif parsed.get("max_wait", 0) > 0:
            state["next_stargazer_panel_time"] = float(now + int(parsed.get("max_wait", 0) or 0) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_waiting_panel"
        elif parsed.get("idle_slot_count", 0) > 0:
            state["stargazer_last_action"] = "passive_idle_slot"
        changed = True
    elif family == "stargazer_guide":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["next_stargazer_panel_time"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_guide_cd"
            changed = True
        elif "牵引成功" in raw_text:
            state["stargazer_idle_slot_count"] = 0
            state["stargazer_last_action"] = "passive_guide_success"
            changed = True
    elif family == "stargazer_soothe":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["next_stargazer_panel_time"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_soothe_cd"
            changed = True
        elif stargazer_mod._is_stargazer_soothe_success(raw_text) or stargazer_mod._is_stargazer_soothe_no_need(raw_text):
            stargazer_mod._clear_stargazer_collect_flags()
            stargazer_mod._queue_stargazer_followup_action(now, "panel", 5)
            state["stargazer_last_action"] = "passive_soothe_done"
            changed = True
    elif family == "stargazer_collect":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["stargazer_collect_due_at"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_collect_cd"
            changed = True
        elif "收集完成" in raw_text:
            count = stargazer_mod._extract_stargazer_collected_slot_count(raw_text)
            state["stargazer_collect_due_at"] = 0
            state["stargazer_busy_until"] = 0
            state["stargazer_ready_slot_count"] = max(0, int(state.get("stargazer_ready_slot_count", 0) or 0) - count)
            state["stargazer_last_action"] = "passive_collect_success"
            changed = True
    return changed


def _looks_like_supported_passive(text, family):
    raw_text = str(text or "")
    family = str(family or "").strip()
    if (
        family.startswith("tianti_")
        or family.startswith("second_soul")
        or family.startswith("concubine_")
        or family.startswith("hehuan_")
        or family.startswith("tianxing_")
        or family.startswith("yinluo_")
        or family.startswith("small_world_")
        or family.startswith("stargazer_")
        or family in {
            "pet",
            "pet_warm",
            "pet_trial",
            "tree_panel",
            "tree_pulse",
            "tree_guard",
            "tree_harvest",
            "wild_training",
            "checkin",
            "sect_teach",
            "tower",
        }
    ):
        return True
    if (
        "【第二元神归位】" in raw_text
        or second_soul_mod._is_second_soul_panel(raw_text)
        or small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text)
        or tianti_mod.RE_TIANTI_PANEL.search(raw_text)
        or _is_tree_panel_text(raw_text)
        or _is_tree_mature_broadcast(raw_text)
        or hehuan_mod.looks_like_hehuan_text(raw_text)
        or tianxing_mod.looks_like_tianxing_text(raw_text)
        or yinluo_mod.looks_like_yinluo_text(raw_text)
        or concubine_mod._is_heart_anchor_lost_text(raw_text)
    ):
        return True
    return False


def _normalize_passive_module_card_input(text, reply_context=None, event=None, event_type=""):
    if not isinstance(text, VerifiedGameEvent):
        return str(text or ""), reply_context, event, str(event_type or "").strip()

    verified = text
    context = dict(verified.reply_context) if isinstance(verified.reply_context, dict) else {}
    if verified.identity_id and not context.get("send_as_id"):
        context["send_as_id"] = verified.identity_id
    if verified.family and not context.get("family"):
        context["family"] = verified.family
    if verified.root_msg_id and not context.get("root_msg_id"):
        context["root_msg_id"] = verified.root_msg_id
    if verified.reply_to_sender_id and not context.get("reply_to_sender_id"):
        context["reply_to_sender_id"] = verified.reply_to_sender_id
    normalized_event = SimpleNamespace(
        id=verified.msg_id, chat_id=verified.chat_id, sender_id=verified.sender_id,
        server_event_at=verified.server_event_at,
    )
    return verified.text, context, normalized_event, verified.event_type


async def _apply_owned_heart_passive(text, now, family, reply_context, event, event_type):
    heart = family == "concubine_heart" or (not family and _looks_like_concubine_heart_reply(text))
    probe = family == "concubine_status"
    if not heart and not probe:
        return None
    context = dict(reply_context or {})
    root = concubine_mod._query_int(context.get("reply_to_msg_id")) or concubine_mod._query_int(context.get("root_msg_id"))
    chat = concubine_mod._query_int(getattr(event, "chat_id", 0))
    msg_id = concubine_mod._query_int(getattr(event, "id", 0))
    hint = concubine_mod._query_int(context.get("send_as_id")) or None
    if "send_as_id" in context and hint is None:
        return False
    candidates = []
    for identity_id in ([hint] if hint is not None else get_identity_ids()):
        if not has_identity(identity_id):
            continue
        with use_identity(identity_id):
            actions = concubine_mod.heart_actions
            matched = actions.owns_probe(root, chat, now) if probe else actions.matches_reply(root, msg_id, chat, now)
        if matched:
            candidates.append(identity_id)
    if len(candidates) != 1:
        return False if heart or candidates or context.get("source_module") == "concubine_heart" else None
    identity_id = candidates[0]
    with use_identity(identity_id):
        return await concubine_mod.heart_actions.handle_reply(
            text, now, SimpleNamespace(id=root, chat_id=chat,
                                      raw_text=context.get("reply_to_command") or context.get("command", ""),
                                      sender_id=context.get("reply_to_sender_id", 0)),
            current_msg_id=msg_id, current_chat_id=chat, observed_at=telegram_event_timestamp(event, event_type),
            reply_context=dict(context, sender_id=getattr(event, "sender_id", 0), event_type=event_type),
            probe=probe,
        )


async def _apply_owned_concubine_action_passive(text, now, family, reply_context, event, event_type):
    if heavenly_ban_mod.is_heavenly_ban_text(text) or heavenly_ban_mod.is_heavenly_pardon_text(text):
        # Account safety routing is independent of mutation completion.
        return None
    command = (reply_context or {}).get("reply_to_command") or (reply_context or {}).get("command", "")
    if not family and isinstance(command, str) and command.strip() == concubine_mod.CMD_CONCUBINE_STATUS:
        family = "concubine_status"
    heart_result = await _apply_owned_heart_passive(text, now, family, reply_context, event, event_type)
    if heart_result is not None:
        return heart_result
    if family not in {"storage_bag", "concubine_gift", "concubine_greet", "concubine_fragment", "concubine_status", "concubine_dream", "concubine_puzzle", "concubine_voyage", "concubine_tianji", "concubine_reacquire"}:
        return None
    fragment = family == "concubine_fragment"
    voyage = family == "concubine_voyage"
    divination = family == "concubine_tianji"
    reacquire = family == "concubine_reacquire"
    mutation = family in {"concubine_dream", "concubine_puzzle"} or voyage or divination or reacquire
    query_kinds = ({"fragment"} if fragment else {"voyage_status"} if voyage else
                   {"status", "gift_status"} if family == "concubine_status" else set())
    root = _context_msg_id(reply_context, "reply_to_msg_id") or _context_msg_id(reply_context, "root_msg_id")
    chat_id, msg_id = _event_int(getattr(event, "chat_id", 0)), _event_int(getattr(event, "id", 0))
    if reacquire or family == "concubine_status":
        chat_id = concubine_mod._query_int(getattr(event, "chat_id", 0))
        msg_id = concubine_mod._query_int(getattr(event, "id", 0))
    target_id = _identity_from_reply_context(reply_context)
    if target_id is None and mutation:
        if "send_as_id" in (reply_context or {}):
            return False
        candidates = []
        for identity_id in get_identity_ids():
            with use_identity(identity_id):
                if reacquire:
                    item = concubine_mod.reacquire_actions.record()
                    records = [(item, concubine_mod.reacquire_actions.SOURCE)] if item else []
                elif divination:
                    item = concubine_mod.divination_actions.record()
                    records = [(item, concubine_mod.divination_actions.SOURCE)] if item else []
                elif voyage:
                    records = [(item, concubine_mod.voyage_actions.SOURCE)
                               for item in (concubine_mod.voyage_actions.records() or {}).values()]
                    query = concubine_mod._status_query_record()
                    if query and query["kind"] == "voyage_status":
                        records.append((query, concubine_mod.CONCUBINE_QUERY_SOURCE))
                else:
                    record = (concubine_mod.fragment_actions.records() or {}).get(family.removeprefix("concubine_"))
                    records = [(record, concubine_mod.fragment_actions.SOURCE)] if record else []
                for record, source in records:
                    if record["account_id"] != get_identity_account(identity_id):
                        continue
                    record = concubine_mod._adopt_status_query_receipt(
                        record, now, source_module=source, include_logs=False,
                    )
                    if root > 0 and (chat_id, root) == (record["chat_id"], record["msg_id"]):
                        candidates.append(identity_id)
        if len(candidates) != 1:
            return False
        target_id = candidates[0]
    elif target_id is None and query_kinds:
        if "send_as_id" in (reply_context or {}):
            return False
        candidates = []
        unresolved_in_chat = False
        for identity_id in get_identity_ids():
            with use_identity(identity_id):
                record = concubine_mod._status_query_record()
                if not record or record["kind"] not in query_kinds or record["account_id"] != get_identity_account(identity_id):
                    continue
                unresolved_in_chat |= record["chat_id"] == chat_id and record["status"] in concubine_mod.CONCUBINE_QUERY_UNRESOLVED
                record = concubine_mod._adopt_status_query_receipt(record, now, include_logs=False)
                if root > 0 and (chat_id, root) == (record["chat_id"], record["msg_id"]):
                    candidates.append(identity_id)
        if len(candidates) > 1 or (fragment and not candidates):
            return False
        if voyage and not candidates:
            if command == concubine_mod.CMD_CONCUBINE_VOYAGE_STATUS or unresolved_in_chat:
                return False
            return None
        if candidates:
            target_id = candidates[0]
        else:
            target_id = _identity_from_reply_sender_id((reply_context or {}).get("reply_to_sender_id"))
            if target_id is None:
                target_id, _route, _spec = _resolve_concubine_pending_identity_from_context(family, reply_context)
            if target_id is None:
                # An early unbound reply must remain replayable once its receipt arrives.
                if unresolved_in_chat:
                    return False
                if family != "concubine_status":
                    return None
    elif target_id is None:
        target_id, _route, _spec = _resolve_concubine_pending_identity_from_context(family, reply_context)
    if target_id is None or not has_identity(target_id):
        if family == "concubine_status":
            sender = (reply_context or {}).get("reply_to_sender_id", 0)
            record_passive_inbox_event(
                "skipped", module="concubine", family=family,
                reason=_missing_identity_reason(text, family, sender),
                decision="skip_missing_identity", chat_id=chat_id, msg_id=msg_id,
                reply_to_msg_id=root, reply_to_sender_id=sender, event_type=event_type,
                source_message_id=msg_id,
            )
        return False if fragment or mutation or family == "concubine_status" else None
    with use_identity(target_id) as owner:
        account_id = get_identity_account(target_id)
        if not query_kinds and not mutation and not concubine_mod.owns_concubine_affinity_reply(family, root, chat_id):
            return None
        handler = {
            "storage_bag": concubine_mod.handle_concubine_storage_bag_reply,
            "concubine_gift": concubine_mod.handle_concubine_gift_reply,
            "concubine_greet": concubine_mod.handle_concubine_greet_reply,
            "concubine_fragment": concubine_mod.handle_concubine_fragment_reply,
            "concubine_status": concubine_mod.handle_concubine_status_reply,
            "concubine_dream": concubine_mod.handle_concubine_dream_reply,
            "concubine_puzzle": concubine_mod.handle_concubine_puzzle_reply,
            "concubine_voyage": concubine_mod.handle_concubine_voyage_reply,
            "concubine_tianji": concubine_mod.handle_concubine_tianji_reply,
            "concubine_reacquire": concubine_mod.handle_concubine_reacquire_reply,
        }[family]
        query = concubine_mod._status_query_record() if family == "concubine_status" else None
        observed_status = family == "concubine_status" and query is not None and (
            not query or query["status"] not in concubine_mod.CONCUBINE_QUERY_UNRESOLVED)
        route = "reply_context" if _identity_from_reply_context(reply_context) is not None else "concubine_pending_chain"
        if (reply_context or {}).get("matched_via") == "reply_sender" or (
            route != "reply_context" and _identity_from_reply_sender_id((reply_context or {}).get("reply_to_sender_id")) == target_id
        ):
            route = "reply_sender"
        handled = await handler(
            text, now, SimpleNamespace(
                id=root, chat_id=chat_id,
                raw_text=(reply_context or {}).get("reply_to_command") or (reply_context or {}).get("command", ""),
                sender_id=(reply_context or {}).get("reply_to_sender_id", 0),
            ), matched_family=family, current_msg_id=msg_id,
            current_chat_id=chat_id, observed_at=telegram_event_timestamp(event, event_type),
            reply_context=dict(reply_context or {},
                               sender_id=getattr(event, "sender_id", 0) if reacquire or family == "concubine_status" else _event_int(getattr(event, "sender_id", 0)),
                               event_type=event_type,
                               forwarded=bool((reply_context or {}).get("forwarded") or getattr(event, "fwd_from", None) or getattr(getattr(event, "message", None), "fwd_from", None))),
        )
        if (handled and has_identity(target_id) and get_identity_state(target_id) is owner
                and get_identity_account(target_id) == account_id):
            try:
                _record_passive_event(
                    "changed", module="concubine", identity_id=target_id, summary=family,
                    decision="observed_query_result" if observed_status else "owned_reacquire_result" if reacquire else "owned_divination_result" if divination else "owned_voyage_result" if voyage else "owned_fragment_result" if mutation else "owned_query_result" if query_kinds else "owned_affinity_result", chat_id=chat_id, msg_id=msg_id,
                    reply_to_msg_id=root, event_type=event_type, source_message_id=msg_id,
                    route_source=_route_source(event_type, route),
                )
            except Exception as exc:
                concubine_mod.console_log(f"Concubine result saved; passive diagnostic failed ({type(exc).__name__})")
        return handled


async def handle_passive_module_card(text, now=None, reply_context=None, event=None, event_type=""):
    now = float(now or time.time())
    resource_event = text if isinstance(text, VerifiedGameEvent) else from_telegram_event(
        event, text, reply_context, event_kind=event_type or "message",
    )
    resource_changed = yinluo_mod.observe_yinluo_resources(resource_event, now=now)
    raw_text, reply_context, event, event_type = _normalize_passive_module_card_input(
        text,
        reply_context=reply_context,
        event=event,
        event_type=event_type,
    )
    raw_text = str(raw_text or "")
    observed_msg_id = _event_int(getattr(event, "id", 0))
    observed_chat_id = _event_int(getattr(event, "chat_id", 0))
    event_type = str(event_type or "").strip()
    family = _family_from_reply_context(reply_context)
    # Owned actions and proven reads commit before generic inventory/text dedupe.
    owned_concubine_handled = await _apply_owned_concubine_action_passive(raw_text, now, family, reply_context, event, event_type)
    if owned_concubine_handled is not None:
        return owned_concubine_handled or resource_changed
    no_sect_reply = family == "checkin" and checkin_mod.is_no_sect_checkin_text(raw_text)
    # This reducer persists source order; text-only dedupe would discard newer edits.
    if not no_sect_reply and not _mark_observed_passive_event(observed_chat_id, observed_msg_id, raw_text, now=now):
        return resource_changed
    target_id = _identity_from_reply_context(reply_context)
    context_route_source = _route_source(event_type, "reply_context")
    passive_route_source = _route_source(event_type, "passive_match")
    target_route_source = context_route_source if target_id is not None else ""
    source_message_id = observed_msg_id
    reply_to_sender_id = _event_int((reply_context or {}).get("reply_to_sender_id", 0))
    if target_id is None:
        target_id = _identity_from_reply_sender_id(reply_to_sender_id)
        if target_id is not None:
            target_route_source = _route_source(event_type, "reply_sender")

    pardon_result = await heavenly_ban_mod.handle_heavenly_pardon_text(
        raw_text,
        now=now,
        identity_id_hint=target_id or 0,
        source="passive_inbox",
    )
    if pardon_result.get("handled"):
        _record_passive_event(
            "changed",
            module="heavenly_pardon",
            identity_id=pardon_result.get("identity_id") or 0,
            summary="heavenly_pardon",
            matched_text="天道赦免",
            decision="identity_restored" if pardon_result.get("matched") else "unmatched_pardon",
            chat_id=observed_chat_id,
            msg_id=observed_msg_id,
            reply_to_msg_id=(reply_context or {}).get("reply_to_msg_id", 0),
            reply_to_sender_id=reply_to_sender_id,
            root_msg_id=(reply_context or {}).get("root_msg_id", 0),
            event_type=event_type,
            route_source=target_route_source or event_type or "passive_match",
            source_message_id=source_message_id,
        )
        return True

    ban_result = await heavenly_ban_mod.handle_heavenly_ban_text(
        raw_text,
        now=now,
        identity_id_hint=target_id or 0,
        source="passive_inbox",
    )
    if ban_result.get("handled"):
        _record_passive_event(
            "changed",
            module="heavenly_ban",
            identity_id=ban_result.get("identity_id") or 0,
            summary="heavenly_ban",
            matched_text="天道封禁",
            decision="identity_disabled" if ban_result.get("matched") else "unmatched_ban",
            chat_id=observed_chat_id,
            msg_id=observed_msg_id,
            reply_to_msg_id=(reply_context or {}).get("reply_to_msg_id", 0),
            reply_to_sender_id=reply_to_sender_id,
            root_msg_id=(reply_context or {}).get("root_msg_id", 0),
            event_type=event_type,
            route_source=target_route_source or event_type or "passive_match",
            source_message_id=source_message_id,
        )
        return True

    storage_changed, storage_identity_id = _apply_storage_bag_passive(raw_text, now)
    if storage_changed:
        _record_passive_event(
            "changed",
            module="storage_bag",
            identity_id=storage_identity_id or 0,
            summary="storage_bag",
            matched_text="储物袋",
            decision="storage_bag_snapshot",
            chat_id=observed_chat_id,
            msg_id=observed_msg_id,
            event_type=event_type,
            route_source=event_type or "passive_match",
            source_message_id=source_message_id,
        )
        return True

    if target_id is None and "【第二元神归位】" in raw_text:
        target_id = _match_identity_by_at_text(raw_text)
        if target_id is not None:
            target_route_source = _route_source(event_type, "passive_tag")
    has_owner_hint, owner_target_id, owner_route = _resolve_owner_hint(raw_text)
    if target_id is None and has_owner_hint:
        if owner_target_id is not None:
            target_id = owner_target_id
            target_route_source = _route_source(event_type, owner_route)
    if target_id is None and not has_owner_hint:
        if family.startswith("hehuan_") or hehuan_mod.looks_like_hehuan_text(raw_text):
            target_id = _match_identity_by_at_text(raw_text)
            if target_id is not None:
                target_route_source = _route_source(event_type, "passive_tag")
        if target_id is None and (family.startswith("tianxing_") or tianxing_mod.looks_like_tianxing_text(raw_text)):
            target_id = _match_identity_by_at_text(raw_text)
            if target_id is not None:
                target_route_source = _route_source(event_type, "passive_tag")
        if target_id is None and (family.startswith("yinluo_") or yinluo_mod.looks_like_yinluo_text(raw_text)):
            target_id = _match_identity_by_at_text(raw_text)
            if target_id is not None:
                target_route_source = _route_source(event_type, "passive_tag")
        if target_id is None:
            target_id, passive_identity_route = _resolve_passive_text_identity(raw_text, family)
            if target_id is not None:
                target_route_source = _route_source(event_type, passive_identity_route)
        if target_id is None:
            target_id = _match_identity_by_you_line(raw_text)
            if target_id is not None:
                target_route_source = _route_source(event_type, "passive_you_line")
    concubine_pending_context_spec = None
    if target_id is None:
        target_id, pending_identity_route, concubine_pending_context_spec = _resolve_concubine_pending_identity_from_context(family, reply_context)
        if target_id is not None:
            target_route_source = _route_source(event_type, pending_identity_route)

    if target_id is None:
        if _looks_like_supported_passive(raw_text, family):
            missing_reason = _missing_identity_reason(raw_text, family, reply_to_sender_id=reply_to_sender_id)
            if family:
                _record_passive_event(
                    "skipped",
                    reason=missing_reason,
                    summary=family,
                    family=family,
                    chat_id=observed_chat_id,
                    msg_id=observed_msg_id,
                    reply_to_msg_id=(reply_context or {}).get("reply_to_msg_id", 0),
                    reply_to_sender_id=reply_to_sender_id,
                    root_msg_id=(reply_context or {}).get("root_msg_id", 0),
                    event_type=event_type,
                    route_source=context_route_source,
                    matched_text=raw_text,
                    decision="skip_missing_identity",
                    source_message_id=source_message_id,
                )
            else:
                _record_passive_event(
                    "skipped",
                    reason=missing_reason,
                    chat_id=observed_chat_id,
                    msg_id=observed_msg_id,
                    event_type=event_type,
                    matched_text=raw_text,
                    decision="skip_missing_identity",
                    source_message_id=source_message_id,
                    include_recent=False,
                )
        return False

    changed = resource_changed
    changed_modules = []
    event_recorded_by_handler = False
    if not has_identity(target_id):
        return False
    with use_identity(target_id) as owner_state:
        owner_account = get_identity_account(target_id)

        def owner_is_current():
            return (
                has_identity(target_id)
                and get_identity_state(target_id) is owner_state
                and get_identity_account(target_id) == owner_account
            )

        if family.startswith("tianti_") or tianti_mod.RE_TIANTI_PANEL.search(raw_text):
            module_changed = await _apply_tianti_passive(raw_text, now, family, reply_context, event, event_type)
            if not owner_is_current():
                return changed or module_changed
            if module_changed:
                changed_modules.append("tianti")
            changed = module_changed or changed
        if family.startswith("second_soul") or second_soul_mod._is_second_soul_panel(raw_text):
            module_changed = await _apply_second_soul_passive(raw_text, now, family, reply_context, event, event_type)
            if not owner_is_current():
                return changed or module_changed
            if module_changed:
                changed_modules.append("second_soul")
            changed = module_changed or changed
        if family in {"pet", "pet_warm", "pet_trial", "pet_formation"}:
            module_changed = _apply_pet_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if family.startswith("small_world_") or small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text):
            module_changed = await _apply_small_world_passive(raw_text, now, family, reply_context)
            if not owner_is_current():
                return changed or module_changed
            if module_changed:
                changed_modules.append("small_world")
            changed = module_changed or changed
        if concubine_pending_context_spec is not None:
            reply_to_msg_id = _context_msg_id(reply_context, "reply_to_msg_id") or _context_msg_id(reply_context, "root_msg_id")
            reply_to = SimpleNamespace(raw_text="", id=reply_to_msg_id) if reply_to_msg_id > 0 else None
            handler = concubine_pending_context_spec["handler"]
            matched_family = concubine_pending_context_spec.get("family") or family
            if concubine_pending_context_spec.get("current_msg_id"):
                module_changed = await handler(
                    raw_text,
                    now,
                    reply_to,
                    matched_family=matched_family,
                    current_msg_id=observed_msg_id,
                )
            else:
                module_changed = await handler(
                    raw_text,
                    now,
                    reply_to,
                    matched_family=matched_family,
                )
            if not owner_is_current():
                return changed or module_changed
            if module_changed:
                changed_modules.append("concubine")
            changed = module_changed or changed
        if ((family.startswith("hehuan_") and not (
                family == "hehuan_dual" and _routed_reply_already_handled(reply_context)))
                or (not family and hehuan_mod.looks_like_hehuan_text(raw_text))):
            module_changed = hehuan_mod.apply_hehuan_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("hehuan")
            changed = module_changed or changed
        if family.startswith("tianxing_") or (not family and tianxing_mod.looks_like_tianxing_text(raw_text)):
            module_changed = tianxing_mod.apply_tianxing_passive(
                raw_text, now, family,
                reply_context=dict(
                    reply_context or {}, send_as_id=target_id, chat_id=observed_chat_id,
                    msg_id=observed_msg_id, processed_at=max(now, time.time()),
                    server_event_at=telegram_event_timestamp(event, event_type), event_type=event_type,
                ),
            )
            if module_changed:
                changed_modules.append("tianxing")
            changed = module_changed or changed
        if family.startswith("yinluo_") or (not family and yinluo_mod.looks_like_yinluo_text(raw_text)):
            module_changed = resource_changed or yinluo_mod.apply_yinluo_passive(
                raw_text,
                now,
                family,
                event_context=resource_event,
            )
            if module_changed:
                changed_modules.append("yinluo")
            changed = module_changed or changed
        if family in {"tree_panel", "tree_pulse", "tree_guard", "tree_harvest"} or _is_tree_panel_text(raw_text) or _is_tree_mature_broadcast(raw_text):
            module_changed = _apply_tree_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("tree")
            changed = module_changed or changed
        if family in {"checkin", "sect_teach"}:
            module_changed = await _apply_checkin_passive(
                raw_text, now, family, reply_context, chat_id=observed_chat_id,
                event=event, event_type=event_type,
            )
            if not owner_is_current():
                return changed or module_changed
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if family == "tower":
            module_changed = _apply_tower_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("tower")
            changed = module_changed or changed
        if family.startswith("stargazer_"):
            module_changed = _apply_stargazer_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if (
            changed and family and family != "concubine_heart"
            and not (family == "checkin" and checkin_mod.is_no_sect_checkin_text(raw_text))
            and family not in tianxing_mod.TIANXING_REPLY_GUARD_FAMILIES
            and not family.startswith("yinluo_")
            and family not in {"wanxin_assist_banner", "wanxin_assist_strip"}
        ):
            close_action_guard_by_family(family, send_as_id=target_id, reason="passive_state_changed", now=now)
        if changed:
            save_state()
    if changed and not event_recorded_by_handler:
        _record_passive_event(
            "changed",
            module=",".join(changed_modules) or "unknown",
            identity_id=target_id,
            summary=family or "passive",
            family=family,
            chat_id=observed_chat_id,
            msg_id=observed_msg_id,
            reply_to_msg_id=(reply_context or {}).get("reply_to_msg_id", 0),
            reply_to_sender_id=reply_to_sender_id,
            root_msg_id=(reply_context or {}).get("root_msg_id", 0),
            event_type=event_type,
            route_source=target_route_source or (context_route_source if family else passive_route_source),
            matched_text=raw_text,
            decision="state_changed",
            source_message_id=source_message_id,
        )
    else:
        _record_passive_event(
            "skipped",
            identity_id=target_id,
            reason="no_change",
            summary=family or "passive",
            family=family,
            chat_id=observed_chat_id,
            msg_id=observed_msg_id,
            reply_to_msg_id=(reply_context or {}).get("reply_to_msg_id", 0),
            reply_to_sender_id=reply_to_sender_id,
            root_msg_id=(reply_context or {}).get("root_msg_id", 0),
            event_type=event_type,
            route_source=target_route_source or (context_route_source if family else passive_route_source),
            matched_text=raw_text,
            decision="no_state_change",
            source_message_id=source_message_id,
        )
    return changed


__all__ = [
    "get_passive_inbox_snapshot",
    "get_passive_inbox_status_text",
    "handle_passive_module_card",
    "record_passive_inbox_event",
]
