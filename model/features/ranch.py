"""Archived compatibility surface for the retired group-command ranch flow.

The active spirit-beast path is being rebuilt around the MiniApp protocol.  This
module keeps passive parsing for historical ``.一键放养`` replies and delayed
``【灵兽归来】`` broadcasts, but it never schedules or sends the legacy command.
"""

from __future__ import annotations

import re

from ..config import CMD_RANCH
from ..persistence import mark_dirty, save_state
from ..runtime import mono, send_audit_log
from ..state import (
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_send_as_tags,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts


RANCH_SUCCESS_PREFIX = "【万兽奔腾】"
RANCH_NO_IDLE_PET_TEXT = "你当前没有处于【休息中】的灵兽可供放养。"
RANCH_WRONG_SECT_TEXT = "你并非万灵宗弟子，不知如何开启万兽谷的群体传送阵。"
RANCH_RETURN_SUMMARY_PREFIX = "【灵兽归来】"
RANCH_RETURN_INITIAL_TEXT = "已自行归来"
RANCH_RETURN_READY_GRACE_SEC = 30 * 60
RANCH_ARCHIVE_REASON = "旧 .一键放养 自动链已归档；等待万兽谷·驭灵行迹 MiniApp Gate C"
RE_WHITESPACE = re.compile(r"\s+")


def _set_ranch_return_pending(now):
    state["ranch_return_pending"] = True
    state["ranch_return_seen_msg_id"] = 0
    state["ranch_return_wait_since"] = float(now or 0)
    state["ranch_return_last_notified_at"] = 0


def _clear_ranch_return_wait():
    state["ranch_return_pending"] = False
    state["ranch_return_wait_since"] = 0
    state["ranch_return_last_notified_at"] = 0


def _compact_text(text):
    return RE_WHITESPACE.sub("", str(text or "")).lower()


def _is_ranch_return_broadcast_text(text):
    raw_text = str(text or "")
    return "你放养的" in raw_text and (
        RANCH_RETURN_INITIAL_TEXT in raw_text
        or RANCH_RETURN_SUMMARY_PREFIX in raw_text
    )


def _is_ranch_return_ready(now):
    if not state.get("ranch_return_pending"):
        return False
    next_ranch_time = float(state.get("next_ranch_time", 0) or 0)
    return next_ranch_time <= 0 or float(now or 0) >= next_ranch_time - RANCH_RETURN_READY_GRACE_SEC


def _is_ranch_wrong_sect_text(text):
    raw_text = str(text or "")
    return (
        RANCH_WRONG_SECT_TEXT in raw_text
        or (
            "并非万灵宗弟子" in raw_text
            and any(keyword in raw_text for keyword in ("御兽", "灵兽", "万兽谷"))
        )
    )


def _match_ranch_return_identity_ids(text, now):
    compact_text = _compact_text(text)
    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            # The active module is archived and disabled, but an expedition
            # started before retirement may still produce a delayed broadcast.
            if not state.get("ranch_return_pending"):
                continue
            if not _is_ranch_return_ready(now):
                continue
            compact_tags = {_compact_text(tag) for tag in get_send_as_tags(identity_id) if tag}
            if any(tag and tag in compact_text for tag in compact_tags):
                matched_ids.append(identity_id)
    return matched_ids


def retire_ranch_legacy_state(
    *,
    persist=False,
    preserve_return_pending=True,
    record_reason=True,
):
    """Disable every active legacy ranch field without losing an in-flight return."""
    before = (
        bool(state.get("ranch_enabled")),
        state.get("next_ranch_time", 0),
        state.get("ranch_reply_to_msg_id", 0),
        state.get("ranch_reply_due_at", 0),
        state.get("ranch_retry_count", 0),
        bool(state.get("ranch_return_pending")),
        state.get("ranch_last_error", ""),
    )

    state["ranch_enabled"] = False
    state["next_ranch_time"] = 0
    state["ranch_reply_to_msg_id"] = 0
    state["ranch_reply_due_at"] = 0
    state["ranch_retry_count"] = 0
    if not preserve_return_pending:
        _clear_ranch_return_wait()
    if record_reason and not str(state.get("ranch_last_error") or "").strip():
        state["ranch_last_error"] = RANCH_ARCHIVE_REASON

    after = (
        bool(state.get("ranch_enabled")),
        state.get("next_ranch_time", 0),
        state.get("ranch_reply_to_msg_id", 0),
        state.get("ranch_reply_due_at", 0),
        state.get("ranch_retry_count", 0),
        bool(state.get("ranch_return_pending")),
        state.get("ranch_last_error", ""),
    )
    changed = before != after
    if changed:
        if persist:
            save_state()
        else:
            mark_dirty()
    return changed


def clear_ranch_state(*, persist=False, keep_last_error=False):
    last_error = state.get("ranch_last_error") if keep_last_error else ""
    state["next_ranch_time"] = 0
    state["ranch_reply_to_msg_id"] = 0
    state["ranch_reply_due_at"] = 0
    state["ranch_retry_count"] = 0
    state["ranch_last_msg_id"] = 0
    state["ranch_last_result"] = ""
    state["ranch_last_error"] = last_error or ""
    state["ranch_return_seen_msg_id"] = 0
    _clear_ranch_return_wait()
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_ranch_initial_check(now, *, persist=False, keep_last_error=True):
    """Fail closed when old startup/control code asks to schedule the module."""
    del now
    keep_existing_reason = keep_last_error and bool(str(state.get("ranch_last_error") or "").strip())
    retire_ranch_legacy_state(
        persist=persist,
        preserve_return_pending=True,
        record_reason=not keep_existing_reason,
    )
    return 0.0


def get_ranch_status_text():
    return_pending = bool(state.get("ranch_return_pending"))
    lines = [
        "🐾 放养",
        "- 旧版群命令自动化：已归档",
        "- 当前替代入口：万兽谷·驭灵行迹 MiniApp（Gate C 未开放）",
        f"- 等待历史归来广播：{'是' if return_pending else '否'}",
        f"- 最近结果：{state.get('ranch_last_result') or '无'}",
        f"- 说明：{RANCH_ARCHIVE_REASON}",
    ]
    if return_pending:
        lines.append(f"- 等待归来起点：{fmt_abs_ts(state.get('ranch_return_wait_since', 0))}")
    if state.get("ranch_return_seen_msg_id"):
        lines.append(f"- 最近归来消息ID：{state.get('ranch_return_seen_msg_id')}")
    return "\n".join(lines)


def _is_ranch_reply(text, reply_to, matched_family=None):
    if matched_family == "ranch":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    return (
        CMD_RANCH in orig_cmd
        or "万兽谷" in str(text or "")
        or str(text or "").startswith(RANCH_SUCCESS_PREFIX)
    )


async def handle_ranch_reply(text, now, reply_to, matched_family=None):
    """Parse a late legacy reply without reconnecting the active send chain."""
    if not _is_ranch_reply(text, reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    if raw_text.startswith(RANCH_SUCCESS_PREFIX):
        state["ranch_last_result"] = "历史放养成功，等待灵兽归来"
        state["ranch_last_error"] = ""
        _set_ranch_return_pending(now)
        audit_text = "🐾 已采纳历史放养成功回复，继续等待灵兽归来广播。"
    elif RANCH_NO_IDLE_PET_TEXT in raw_text:
        if state.get("ranch_return_pending"):
            state["ranch_last_result"] = "无休息中灵兽，继续等待既有归来广播"
        else:
            state["ranch_last_result"] = "无休息中灵兽"
            _clear_ranch_return_wait()
        state["ranch_last_error"] = ""
        audit_text = "🐾 已采纳历史放养回复；旧自动链保持归档。"
    elif _is_ranch_wrong_sect_text(raw_text):
        state["ranch_last_result"] = "非万灵宗弟子"
        state["ranch_last_error"] = raw_text[:120] or RANCH_WRONG_SECT_TEXT
        _clear_ranch_return_wait()
        audit_text = "⚠️ 历史放养回复确认当前身份并非万灵宗弟子；旧自动链保持归档。"
    else:
        return False

    state["ranch_last_msg_id"] = int(getattr(reply_to, "id", 0) or 0)
    retire_ranch_legacy_state(
        persist=False,
        preserve_return_pending=True,
        record_reason=False,
    )
    save_state()
    await send_audit_log(audit_text, scope="identity")
    return True


async def handle_ranch_return_broadcast(text, now, event=None):
    if not _is_ranch_return_broadcast_text(text):
        return False

    msg_id = int(getattr(event, "id", 0) or 0) if event is not None else 0
    if msg_id > 0:
        for identity_id in get_identity_ids():
            with use_identity(identity_id):
                if int(state.get("ranch_return_seen_msg_id", 0) or 0) == msg_id:
                    return True

    matched_ids = _match_ranch_return_identity_ids(text, now)
    if len(matched_ids) != 1:
        if len(matched_ids) > 1:
            names = ", ".join(mono(get_identity_display_name(identity_id)) for identity_id in matched_ids)
            await send_audit_log(f"🐾 灵兽归来命中多个身份，跳过：{names}", scope="global", limit=260)
        return False

    target_id = matched_ids[0]
    with use_identity(target_id):
        _clear_ranch_return_wait()
        state["ranch_enabled"] = False
        state["ranch_return_seen_msg_id"] = msg_id
        state["ranch_retry_count"] = 0
        state["ranch_last_result"] = "灵兽归来已确认"
        state["ranch_last_error"] = ""
        save_state()
        await send_audit_log("🐾 灵兽归来已确认。", scope="identity")
    return True


async def run_ranch_scheduler(now):
    """Compatibility tombstone: disable stale state and never send a command."""
    del now
    retire_ranch_legacy_state(
        persist=True,
        preserve_return_pending=True,
        record_reason=True,
    )
    return False


__all__ = [
    "RANCH_ARCHIVE_REASON",
    "clear_ranch_state",
    "get_ranch_status_text",
    "handle_ranch_reply",
    "handle_ranch_return_broadcast",
    "retire_ranch_legacy_state",
    "run_ranch_scheduler",
    "schedule_ranch_initial_check",
]
