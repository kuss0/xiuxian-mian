import asyncio
import time
from pathlib import Path

from ..runtime import send_audit_log
from ..state import get_current_identity_id
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from .trial_miniapp import extract_trial_miniapp_launch, run_trial_miniapp_production_flow


TRIAL_MANUAL_AUTH_TTL_SEC = 10 * 60
TRIAL_MANUAL_MAX_ROUNDS = 99
TRIAL_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}


def _identity_id(value=None):
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def authorize_trial_miniapp_manual_run(identity_id, *, now=None, ttl_sec=TRIAL_MANUAL_AUTH_TTL_SEC):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return 0
    now = float(now or time.time())
    _MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or TRIAL_MANUAL_AUTH_TTL_SEC))
    return _MANUAL_AUTH_UNTIL[identity_id]


def revoke_trial_miniapp_manual_run(identity_id):
    _MANUAL_AUTH_UNTIL.pop(_identity_id(identity_id), None)


def _has_manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    expires_at = float(_MANUAL_AUTH_UNTIL.get(identity_id, 0) or 0)
    if expires_at <= 0:
        return False
    if float(now or time.time()) > expires_at:
        _MANUAL_AUTH_UNTIL.pop(identity_id, None)
        return False
    return True


def _run_lock(identity_id):
    identity_id = _identity_id(identity_id)
    lock = _RUN_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[identity_id] = lock
    return lock


def _format_trial_summary(result):
    result = dict(result or {})
    status = str(result.get("status") or "unknown").strip() or "unknown"
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    try:
        settled_count = int(result.get("settled_count") or data.get("settled_count") or 0)
    except (TypeError, ValueError, OverflowError):
        settled_count = 0
    if result.get("ok"):
        prefix = f"{settled_count}次｜" if settled_count > 0 else ""
        return f"MiniApp {status}｜{prefix}已结算"
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}｜{error or '未完成'}"


def _trial_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = TRIAL_MINIAPP_CAPTURE_DIR / f"trial-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


async def handle_trial_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0):
    identity_id = _identity_id()
    if identity_id <= 0 or not _has_manual_auth(identity_id, now):
        return False
    launch = extract_trial_miniapp_launch(event, message_text=text)
    if not launch:
        return False

    lock = _run_lock(identity_id)
    if lock.locked():
        await send_audit_log("🧪 天机试炼 MiniApp 已在执行，重复入口忽略。", scope="identity", limit=160)
        return True

    async with lock:
        revoke_trial_miniapp_manual_run(identity_id)
        await send_audit_log(
            "🧪 天机试炼 MiniApp 接管入口，开始 WebView/HTTP 流程。",
            scope="identity",
            priority="low",
            limit=180,
        )
        result = await run_trial_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            max_rounds=TRIAL_MANUAL_MAX_ROUNDS,
            capture_sink=_trial_miniapp_capture_store(now),
            capture_source=f"trial_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}",
        )
        summary = _format_trial_summary(result)
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(f"🧪 天机试炼结果｜{summary}", scope="identity", priority=priority, limit=220)
        return True


__all__ = [
    "TRIAL_MANUAL_AUTH_TTL_SEC",
    "TRIAL_MANUAL_MAX_ROUNDS",
    "authorize_trial_miniapp_manual_run",
    "handle_trial_miniapp_entry",
    "revoke_trial_miniapp_manual_run",
]
