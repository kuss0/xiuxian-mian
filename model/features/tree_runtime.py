import asyncio
import html
import re
import time
from pathlib import Path

from ..runtime import send_audit_log
from ..state import (
    get_current_identity_id,
    get_global_enabled,
    get_identity_enabled,
    get_send_as_profile,
)
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from .tree_miniapp import extract_tree_miniapp_launch, normalize_tree_score_profile, run_tree_miniapp_game_production_flow


TREE_MINIAPP_MANUAL_AUTH_TTL_SEC = 10 * 60
TREE_MINIAPP_DEFAULT_MODE = "jump"
TREE_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"

_MANUAL_AUTH = {}
_RUN_LOCKS = {}
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")


def _identity_id(value=None):
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def authorize_tree_miniapp_manual_run(
    identity_id,
    *,
    now=None,
    ttl_sec=TREE_MINIAPP_MANUAL_AUTH_TTL_SEC,
    mode=TREE_MINIAPP_DEFAULT_MODE,
    score_profile=None,
    submit=True,
):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return 0
    now = float(now or time.time())
    normalized_mode = str(mode or TREE_MINIAPP_DEFAULT_MODE).strip().lower() or TREE_MINIAPP_DEFAULT_MODE
    try:
        normalized_score_profile = normalize_tree_score_profile(normalized_mode, score_profile)
    except Exception:
        normalized_score_profile = {}
    _MANUAL_AUTH[identity_id] = {
        "expires_at": now + max(30, float(ttl_sec or TREE_MINIAPP_MANUAL_AUTH_TTL_SEC)),
        "mode": normalized_mode,
        "score_profile": normalized_score_profile,
        "submit": bool(submit),
    }
    return float(_MANUAL_AUTH[identity_id]["expires_at"])


def revoke_tree_miniapp_manual_run(identity_id):
    _MANUAL_AUTH.pop(_identity_id(identity_id), None)


def _manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    auth = dict(_MANUAL_AUTH.get(identity_id) or {})
    expires_at = float(auth.get("expires_at", 0) or 0)
    if expires_at <= 0:
        return {}
    if float(now or time.time()) > expires_at:
        _MANUAL_AUTH.pop(identity_id, None)
        return {}
    return auth


def _run_lock(identity_id):
    identity_id = _identity_id(identity_id)
    lock = _RUN_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[identity_id] = lock
    return lock


def _entry_mentions_current_identity(text):
    usernames = {
        str(match.group(1) or "").strip().lower()
        for match in _MENTION_RE.finditer(str(text or ""))
    }
    usernames.discard("")
    if not usernames:
        return False
    profile_username = str((get_send_as_profile() or {}).get("username") or "").strip().lstrip("@").lower()
    return bool(profile_username and profile_username in usernames)


def _tree_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = TREE_MINIAPP_CAPTURE_DIR / f"tree-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


def _quota_text(state, mode):
    state = state if isinstance(state, dict) else {}
    quota = state.get(mode) if isinstance(state.get(mode), dict) else {}
    try:
        used = int(quota.get("used", 0) or 0)
        limit = int(quota.get("limit", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        used = limit = 0
    label = "跳一跳" if mode == "jump" else "飞一飞" if mode == "fly" else str(mode or "")
    if limit > 0:
        return f"{label} {used}/{limit}"
    return f"{label} 未开放"


def _format_tree_summary(result):
    result = dict(result or {})
    status = str(result.get("status") or "unknown").strip() or "unknown"
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    proof_summary = data.get("proof_summary") if isinstance(data.get("proof_summary"), dict) else {}
    mode = str(data.get("mode") or proof_summary.get("mode") or "").strip()
    if result.get("ok"):
        parts = [f"MiniApp {status}"]
        if mode:
            parts.append("跳一跳" if mode == "jump" else "飞一飞" if mode == "fly" else mode)
        parts.append("已结算，未解析到新增物资")
        return "｜".join(parts)
    if status == "mode_exhausted":
        label = "跳一跳" if mode == "jump" else "飞一飞" if mode == "fly" else (mode or "当前模式")
        return (
            f"MiniApp mode_exhausted｜{label}次数已用完｜"
            f"{_quota_text(state, 'jump')}｜{_quota_text(state, 'fly')}"
        )
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}｜{error or '未完成'}"


async def handle_tree_miniapp_entry(
    event,
    text,
    now,
    reply_to=None,
    matched_family=None,
    result_msg_id=0,
    require_identity_match=False,
):
    identity_id = _identity_id()
    auth = _manual_auth(identity_id, now)
    if identity_id <= 0 or not auth:
        return False
    if require_identity_match and not _entry_mentions_current_identity(text):
        return False
    launch = extract_tree_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    global_enabled = get_global_enabled()
    identity_enabled = get_identity_enabled(identity_id)
    if not global_enabled or not identity_enabled:
        revoke_tree_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled else "身份已停用"
        await send_audit_log(f"🌳 灵树 MiniApp {reason}，已跳过 WebView/HTTP 接管。", scope="identity", limit=180)
        return True

    lock = _run_lock(identity_id)
    if lock.locked():
        await send_audit_log("🌳 灵树 MiniApp 已在执行，重复入口忽略。", scope="identity", limit=160)
        return True

    async with lock:
        revoke_tree_miniapp_manual_run(identity_id)
        mode = str(auth.get("mode") or TREE_MINIAPP_DEFAULT_MODE).strip().lower() or TREE_MINIAPP_DEFAULT_MODE
        score_profile = dict(auth.get("score_profile") or {})
        submit = bool(auth.get("submit", True))
        await send_audit_log(
            f"🌳 灵树 MiniApp 接管入口，开始 WebView/HTTP 流程：{mode}。",
            scope="identity",
            priority="low",
            limit=200,
        )
        result = await run_tree_miniapp_game_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            mode=mode,
            submit=submit,
            capture_sink=_tree_miniapp_capture_store(now),
            capture_source=f"tree_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}",
            score_profile=score_profile,
        )
        summary = html.escape(_format_tree_summary(result), quote=False)
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(f"🌳 灵树结果｜{summary}", scope="identity", priority=priority, limit=220)
        return True


__all__ = [
    "TREE_MINIAPP_DEFAULT_MODE",
    "TREE_MINIAPP_MANUAL_AUTH_TTL_SEC",
    "authorize_tree_miniapp_manual_run",
    "handle_tree_miniapp_entry",
    "revoke_tree_miniapp_manual_run",
]
