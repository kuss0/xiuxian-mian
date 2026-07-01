import re
import time

from ..config import mark_account_offline, mark_account_online
from ..persistence import save_state
from ..runtime import mono, send_audit_log
from ..state import (
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_send_as_profile,
    get_send_as_tags,
    set_identity_enabled,
    state,
    use_identity,
)


RE_AT_MENTION = re.compile(r"@([^\s\r\n\t，。！？；：、,.!?;:()（）\[\]【】<>《》]+)")
HEAVENLY_BAN_MARKERS = ("天道封禁", "封禁烙印")
HEAVENLY_PARDON_MARKERS = ("天道赦免", "封禁已解除", "罪业已洗刷")


def is_heavenly_ban_text(text):
    raw_text = str(text or "")
    return any(marker in raw_text for marker in HEAVENLY_BAN_MARKERS)


def is_heavenly_pardon_text(text):
    raw_text = str(text or "")
    return any(marker in raw_text for marker in HEAVENLY_PARDON_MARKERS)


def _normalize_identity_token(value):
    return re.sub(r"\s+", "", str(value or "").strip().lstrip("@")).lower()


def _identity_tokens(identity_id):
    profile = get_send_as_profile(identity_id)
    candidates = [
        profile.get("username"),
        profile.get("label"),
        profile.get("daohao"),
        *list(get_send_as_tags(identity_id) or []),
    ]
    return {
        token
        for token in (_normalize_identity_token(item) for item in candidates)
        if token
    }


def _match_identity_by_mentions(text):
    mentions = {_normalize_identity_token(item) for item in RE_AT_MENTION.findall(str(text or ""))}
    mentions.discard("")
    if not mentions:
        return 0
    matched = []
    for identity_id in get_identity_ids():
        if _identity_tokens(identity_id) & mentions:
            matched.append(int(identity_id))
    matched = sorted(set(matched))
    return matched[0] if len(matched) == 1 else 0


def resolve_heavenly_ban_identity_id(text, identity_id_hint=0):
    mention_id = _match_identity_by_mentions(text)
    if mention_id > 0:
        return mention_id
    try:
        identity_id_hint = int(identity_id_hint or 0)
    except (TypeError, ValueError):
        identity_id_hint = 0
    known_ids = {int(identity_id) for identity_id in get_identity_ids()}
    return identity_id_hint if identity_id_hint in known_ids else 0


def _identity_label(identity_id):
    profile = get_send_as_profile(identity_id)
    return (
        str(profile.get("label") or "").strip()
        or str(profile.get("username") or "").strip()
        or str(identity_id)
    )


async def handle_heavenly_ban_text(text, *, now=None, identity_id_hint=0, source=""):
    if not is_heavenly_ban_text(text):
        return {"handled": False, "identity_id": 0}

    now = float(now if now is not None else time.time())
    identity_id = resolve_heavenly_ban_identity_id(text, identity_id_hint=identity_id_hint)
    raw_text = str(text or "").strip()
    reason = "检测到天道封禁/封禁烙印"
    source = str(source or "unknown").strip() or "unknown"

    if identity_id <= 0:
        await send_audit_log(
            f"🚨 {reason}，但未匹配本地身份｜来源：{source}｜{mono(raw_text[:160])}",
            scope="global",
            limit=520,
            priority="high",
        )
        return {"handled": True, "identity_id": 0, "matched": False}

    account_id = int(get_identity_account(identity_id) or 0)
    previous_enabled = bool(get_identity_enabled(identity_id))
    set_identity_enabled(identity_id, False)
    if account_id > 0:
        mark_account_offline(account_id, f"{reason}：identity={identity_id}")
    with use_identity(identity_id):
        state["pending_tasks"] = {}
        state["heavenly_ban_active"] = True
        state["heavenly_ban_prev_identity_enabled"] = previous_enabled
        state["heavenly_ban_detected_at"] = now
        state["heavenly_ban_reason"] = raw_text[:240]
    save_state()

    label = _identity_label(identity_id)
    account_text = f"｜acc={account_id}" if account_id > 0 else ""
    for index in range(5):
        await send_audit_log(
            (
                f"🚨 天道封禁命中本地身份：{mono(label)}｜id={identity_id}{account_text}"
                f"｜已停用身份、标记账号离线并清空待发任务｜来源：{source}｜提醒 {index + 1}/5"
            ),
            scope="global",
            limit=520,
            priority="high",
        )
    return {"handled": True, "identity_id": identity_id, "matched": True}


async def handle_heavenly_pardon_text(text, *, now=None, identity_id_hint=0, source=""):
    if not is_heavenly_pardon_text(text):
        return {"handled": False, "identity_id": 0}

    now = float(now if now is not None else time.time())
    identity_id = resolve_heavenly_ban_identity_id(text, identity_id_hint=identity_id_hint)
    raw_text = str(text or "").strip()
    reason = "检测到天道赦免/封禁解除"
    source = str(source or "unknown").strip() or "unknown"

    if identity_id <= 0:
        await send_audit_log(
            f"⚠️ {reason}，但未匹配本地身份｜来源：{source}｜{mono(raw_text[:160])}",
            scope="global",
            limit=520,
            priority="high",
        )
        return {"handled": True, "identity_id": 0, "matched": False}

    account_id = int(get_identity_account(identity_id) or 0)
    with use_identity(identity_id):
        previous_enabled = state.get("heavenly_ban_prev_identity_enabled")
        restore_enabled = bool(previous_enabled) if isinstance(previous_enabled, bool) else True
        state["heavenly_ban_active"] = False
        state["heavenly_ban_cleared_at"] = now
        state["heavenly_ban_pardon_text"] = raw_text[:240]
        state["heavenly_ban_reason"] = ""
    if account_id > 0:
        mark_account_online(account_id)
    set_identity_enabled(identity_id, restore_enabled)
    save_state()

    label = _identity_label(identity_id)
    account_text = f"｜acc={account_id}" if account_id > 0 else ""
    await send_audit_log(
        (
            f"✅ 天道赦免已恢复本地身份：{mono(label)}｜id={identity_id}{account_text}"
            f"｜身份开关={'开启' if restore_enabled else '保持关闭'}｜来源：{source}"
        ),
        scope="global",
        limit=520,
        priority="high",
    )
    return {"handled": True, "identity_id": identity_id, "matched": True}
