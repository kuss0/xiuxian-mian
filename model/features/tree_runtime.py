import asyncio
import html
import re
import time
import uuid
from pathlib import Path

from ..config import STATE_DIR
from ..miniapp_state import record_miniapp_state
from ..runtime import send_audit_log
from ..state import (
    get_current_identity_id,
    get_global_enabled,
    get_global_pause_source,
    get_identity_ids,
    get_send_as_profile,
    has_active_identity_context,
    is_cave_public_identity_available,
    use_identity,
)
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore, miniapp_retry_after_sec
from .miniapp_common import append_business_capture, resolve_identity_id as _identity_id
from .tree_miniapp import (
    extract_tree_miniapp_launch,
    normalize_tree_score_profile,
    run_tree_miniapp_daily_production_flow,
    run_tree_miniapp_game_production_flow,
)


TREE_MINIAPP_MANUAL_AUTH_TTL_SEC = 10 * 60
TREE_MINIAPP_DEFAULT_MODE = "jump"
TREE_MINIAPP_CAPTURE_DIR = Path(STATE_DIR) / "miniapp_capture"

_MANUAL_AUTH = {}
_GLOBAL_RUN_LOCK = None
_COORDINATOR = {
    "phase": "idle",
    "identity_id": 0,
    "day_key": "",
    "op_id": "",
    "command_msg_id": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
    "result": {},
    "error": "",
    "retry_after_sec": 0.0,
    "retry_at": 0.0,
}
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")






def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


def authorize_tree_miniapp_manual_run(
    identity_id,
    *,
    now=None,
    ttl_sec=TREE_MINIAPP_MANUAL_AUTH_TTL_SEC,
    mode=TREE_MINIAPP_DEFAULT_MODE,
    score_profile=None,
    submit=True,
    command_msg_id=0,
    op_id="",
    day_key="",
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
        "kind": "manual",
        "identity_id": identity_id,
        "day_key": str(day_key or get_day_key(now)),
        "op_id": str(op_id or uuid.uuid4().hex),
        "command_msg_id": max(0, int(command_msg_id or 0)),
        "mode": normalized_mode,
        "score_profile": normalized_score_profile,
        "submit": bool(submit),
    }
    return float(_MANUAL_AUTH[identity_id]["expires_at"])


def check_tree_miniapp_eligibility(identity_id, *, enabled=None):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return False, "身份无效"
    if enabled is not None and not bool(enabled):
        return False, "MiniApp 自动开关未开启"
    if not is_cave_public_identity_available(identity_id):
        return False, "身份已停用"
    sect_name = str((get_send_as_profile(identity_id) or {}).get("sect_name") or "").strip()
    if sect_name != "落云宗":
        return False, f"宗门不匹配:{sect_name or '未知'}"
    return True, ""


def prepare_tree_miniapp_daily_run(
    identity_id,
    *,
    enabled,
    day_key="",
    now=None,
    ttl_sec=TREE_MINIAPP_MANUAL_AUTH_TTL_SEC,
    op_id="",
    command_msg_id=0,
    score_profiles=None,
):
    identity_id = _identity_id(identity_id)
    eligible, reason = check_tree_miniapp_eligibility(identity_id, enabled=enabled)
    if not eligible:
        return {"ok": False, "reason": reason, "identity_id": identity_id}
    now = float(now or time.time())
    if _COORDINATOR.get("phase") in {"entry_pending", "running"}:
        active_identity_id = _identity_id(_COORDINATOR.get("identity_id"))
        active_auth = _manual_auth(active_identity_id, now) if active_identity_id > 0 else {}
        if _COORDINATOR.get("phase") == "running" or active_auth:
            return {
                "ok": False,
                "reason": "灵树 MiniApp 全局已有任务",
                "identity_id": identity_id,
                "active_identity_id": active_identity_id,
                "active_op_id": str(_COORDINATOR.get("op_id") or ""),
            }
        _set_coordinator("blocked", error="entry authorization expired", now=now)
    auth = {
        "kind": "daily",
        "identity_id": identity_id,
        "day_key": str(day_key or get_day_key(now)),
        "op_id": str(op_id or uuid.uuid4().hex),
        "command_msg_id": max(0, int(command_msg_id or 0)),
        "expires_at": now + max(30, float(ttl_sec or TREE_MINIAPP_MANUAL_AUTH_TTL_SEC)),
        "score_profiles": {
            mode: normalize_tree_score_profile(mode, (score_profiles or {}).get(mode))
            for mode in ("jump", "fly")
        },
    }
    _MANUAL_AUTH[identity_id] = auth
    _set_coordinator("entry_pending", auth=auth, now=now)
    return {
        "ok": True,
        "identity_id": identity_id,
        "day_key": auth["day_key"],
        "op_id": auth["op_id"],
        "command_msg_id": auth["command_msg_id"],
        "expires_at": auth["expires_at"],
    }


def finalize_tree_miniapp_daily_command(op_id, command_msg_id, *, now=None):
    op_id = str(op_id or "").strip()
    command_msg_id = max(0, int(command_msg_id or 0))
    if not op_id or command_msg_id <= 0:
        return False
    now = float(now or time.time())
    for identity_id in list(_MANUAL_AUTH):
        auth = _manual_auth(identity_id, now)
        if auth.get("kind") != "daily" or auth.get("op_id") != op_id:
            continue
        auth["command_msg_id"] = command_msg_id
        _MANUAL_AUTH[int(identity_id)] = auth
        _set_coordinator("entry_pending", auth=auth, now=now)
        return True
    return False


def cancel_tree_miniapp_daily_run(op_id, *, reason="cancelled", now=None):
    op_id = str(op_id or "").strip()
    if not op_id:
        return False
    for identity_id, auth in list(_MANUAL_AUTH.items()):
        if auth.get("kind") != "daily" or auth.get("op_id") != op_id:
            continue
        _MANUAL_AUTH.pop(identity_id, None)
        _set_coordinator("blocked", auth=auth, error=reason, now=now)
        return True
    return False


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


def _global_run_lock():
    global _GLOBAL_RUN_LOCK
    if _GLOBAL_RUN_LOCK is None:
        _GLOBAL_RUN_LOCK = asyncio.Lock()
    return _GLOBAL_RUN_LOCK


def get_tree_miniapp_coordinator_snapshot():
    return dict(_COORDINATOR)


def _set_coordinator(phase, *, auth=None, result=None, error="", now=None):
    auth = dict(auth or {})
    result = dict(result or {})
    now = float(now or time.time())
    retry_after_sec = miniapp_retry_after_sec(result) if phase == "retry_pending" else 0.0
    _COORDINATOR.update({
        "phase": str(phase or "idle"),
        "identity_id": _identity_id(auth.get("identity_id")),
        "day_key": str(auth.get("day_key") or ""),
        "op_id": str(auth.get("op_id") or ""),
        "command_msg_id": max(0, int(auth.get("command_msg_id") or 0)),
        "error": str(error or ""),
        "retry_after_sec": retry_after_sec,
        "retry_at": now + retry_after_sec if retry_after_sec > 0 else 0.0,
    })
    if phase in {"entry_pending", "running"}:
        _COORDINATOR["started_at"] = now
        _COORDINATOR["finished_at"] = 0.0
        _COORDINATOR["result"] = {}
    elif phase in {"completed", "blocked", "unknown", "retry_pending"}:
        _COORDINATOR["finished_at"] = now
        _COORDINATOR["result"] = result


def _entry_mentions_identity(text, identity_id):
    usernames = {
        str(match.group(1) or "").strip().lower()
        for match in _MENTION_RE.finditer(str(text or ""))
    }
    usernames.discard("")
    if not usernames:
        return False
    profile_username = str((get_send_as_profile(identity_id) or {}).get("username") or "").strip().lstrip("@").lower()
    return bool(profile_username and profile_username in usernames)


def _entry_mentioned_identity_id(text):
    for identity_id in get_identity_ids():
        if _entry_mentions_identity(text, identity_id):
            return int(identity_id)
    return 0


def _reply_to_msg_id(reply_to):
    try:
        return int(getattr(reply_to, "id", reply_to) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _active_authorizations(now):
    active = []
    for identity_id in list(_MANUAL_AUTH):
        auth = _manual_auth(identity_id, now)
        if auth:
            active.append((int(identity_id), auth))
    return active


def _resolve_entry_authorization(identity_id, text, now, *, reply_to=None, require_identity_match=False):
    identity_id = _identity_id(identity_id)
    reply_msg_id = _reply_to_msg_id(reply_to)
    auth = _manual_auth(identity_id, now) if identity_id > 0 else {}
    if identity_id <= 0 and reply_msg_id > 0:
        chain_matches = [
            (candidate_id, candidate_auth)
            for candidate_id, candidate_auth in _active_authorizations(now)
            if int(candidate_auth.get("command_msg_id") or 0) == reply_msg_id
        ]
        if len(chain_matches) == 1:
            return chain_matches[0]
        return 0, {}
    if auth and int(auth.get("command_msg_id") or 0) > 0:
        if reply_msg_id == int(auth["command_msg_id"]):
            return identity_id, auth
        if reply_msg_id > 0:
            return 0, {}

    mentioned_identity_id = _entry_mentioned_identity_id(text)
    if require_identity_match or reply_msg_id <= 0:
        if mentioned_identity_id <= 0:
            return 0, {}
        candidates = [
            (candidate_id, candidate_auth)
            for candidate_id, candidate_auth in _active_authorizations(now)
            if candidate_id == mentioned_identity_id and _entry_mentions_identity(text, candidate_id)
        ]
        if len(candidates) != 1:
            return 0, {}
        candidate_id, candidate_auth = candidates[0]
        if candidate_auth.get("kind") == "daily" and int(candidate_auth.get("command_msg_id") or 0) <= 0:
            return 0, {}
        return candidate_id, candidate_auth

    if auth and auth.get("kind") == "manual" and int(auth.get("command_msg_id") or 0) <= 0:
        return identity_id, auth
    return 0, {}


def _tree_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = TREE_MINIAPP_CAPTURE_DIR / f"tree-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


def _record_tree_business_capture(capture_sink, result, *, source, now):
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    rewards = data.get("rewards") if isinstance(data.get("rewards"), dict) else {}
    items = rewards.get("items") if isinstance(rewards.get("items"), dict) else {}
    gains = rewards.get("gains") if isinstance(rewards.get("gains"), dict) else {}
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    settled_count = len(runs) or (1 if str(result.get("status") or "") == "settled" else 0)
    if settled_count <= 0:
        return {}
    return append_business_capture(
        capture_sink,
        adapter_key="tree",
        detail={
            "settled_count": settled_count,
            "gains": gains,
            "items": items,
        },
        source=source,
        created_at=now,
    )


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
    if data.get("phase"):
        quotas = data.get("quotas") if isinstance(data.get("quotas"), dict) else {}
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        rewards = data.get("rewards") if isinstance(data.get("rewards"), dict) else {}
        items = rewards.get("items") if isinstance(rewards.get("items"), dict) else {}
        gains = rewards.get("gains") if isinstance(rewards.get("gains"), dict) else {}
        parts = [f"MiniApp {status}", f"阶段 {data.get('phase')}"]
        parts.extend(_quota_text(quotas, item) for item in ("jump", "fly"))
        if runs:
            parts.append(f"完成 {len(runs)} 局")
            jump_scores = [str(int(item.get("score") or 0)) for item in runs if item.get("mode") == "jump"]
            fly_scores = [str(int(item.get("score") or 0)) for item in runs if item.get("mode") == "fly"]
            if jump_scores:
                parts.append("跳分 " + "/".join(jump_scores))
            if fly_scores:
                parts.append("飞分 " + "/".join(fly_scores))
            verification_mismatches = [
                item
                for item in runs
                if bool(item.get("verification_mismatch"))
            ]
            if verification_mismatches:
                mismatch_parts = []
                for item in verification_mismatches:
                    mode_label = "跳" if item.get("mode") == "jump" else "飞"
                    mismatch_parts.append(
                        f"{mode_label} client={int(item.get('client_score') or 0)}/server={int(item.get('score') or 0)}"
                    )
                parts.append("服务验轨偏差 " + "、".join(mismatch_parts) + "，已停止对应模式")
            failed_verification = next(
                (
                    item.get("server_verification")
                    for item in runs
                    if int(item.get("score") or 0) <= 0
                    and isinstance(item.get("server_verification"), dict)
                    and item.get("server_verification")
                ),
                {},
            )
            if failed_verification:
                verification_parts = []
                for key in ("ok", "hit", "score", "durationMs"):
                    if key in failed_verification:
                        value = int(failed_verification[key]) if isinstance(failed_verification[key], bool) else failed_verification[key]
                        verification_parts.append(f"{key}={value}")
                if verification_parts:
                    parts.append("服务校验 " + ",".join(verification_parts))
            ranking_notes = []
            for mode, label in (("jump", "跳"), ("fly", "飞")):
                item = next((run for run in runs if run.get("mode") == mode), {})
                target = item.get("ranking_target") if isinstance(item.get("ranking_target"), dict) else {}
                top_scores = [str(int(score)) for score in (target.get("top_scores") or [])]
                if top_scores:
                    ranking_notes.append(
                        f"{label}榜前三={'/'.join(top_scores)}，目标{int(target.get('target_score') or 0)}"
                    )
            if ranking_notes:
                parts.append("；".join(ranking_notes))
        material_parts = [f"{name}x{amount}" for name, amount in sorted(items.items()) if int(amount or 0)]
        material_parts.extend(f"{name}+{amount}" for name, amount in sorted(gains.items()) if int(amount or 0))
        parts.append("收获 " + "、".join(material_parts) if material_parts else "未解析到新增物资")
        if result.get("error"):
            parts.append(str(result.get("error")))
        return "｜".join(parts)
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


def _record_tree_daily_result(auth, result, phase, *, now=None):
    auth = dict(auth or {})
    if auth.get("kind") != "daily":
        return
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    now = float(now or time.time())
    retry_after_sec = miniapp_retry_after_sec(result) if phase == "retry_pending" else 0.0
    record_miniapp_state(
        int(auth.get("identity_id") or 0),
        "tree",
        {
            "kind": "daily",
            "day_key": str(auth.get("day_key") or ""),
            "phase": str(phase or "blocked"),
            "completed_today": str(phase or "") == "completed",
            "quotas": dict(data.get("quotas") or {}),
            "runs": list(data.get("runs") or ()),
            "rewards": dict(data.get("rewards") or {}),
            "errors": list(data.get("errors") or ()),
            "status": str(result.get("status") or ""),
            "error": str(result.get("error") or ""),
            "retry_after_sec": retry_after_sec,
            "retry_at": now + retry_after_sec if retry_after_sec > 0 else 0.0,
        },
        source="tree_daily_runtime",
        source_id=f"tree_daily:{auth.get('identity_id')}:{auth.get('day_key')}:{auth.get('op_id')}",
        now=now,
        outputs=("daily_counter", "score_policy", "rewards"),
        replaces_commands=(".灵树",),
    )


async def handle_tree_miniapp_entry(
    event,
    text,
    now,
    reply_to=None,
    matched_family=None,
    result_msg_id=0,
    require_identity_match=False,
):
    identity_id = _identity_id() if has_active_identity_context() else 0
    identity_id, auth = _resolve_entry_authorization(
        identity_id,
        text,
        now,
        reply_to=reply_to,
        require_identity_match=require_identity_match,
    )
    if identity_id <= 0 or not auth:
        return False
    launch = extract_tree_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    if not has_active_identity_context():
        with use_identity(identity_id):
            return await handle_tree_miniapp_entry(
                event,
                text,
                now,
                reply_to=reply_to,
                matched_family=matched_family,
                result_msg_id=result_msg_id,
                require_identity_match=require_identity_match,
            )
    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    eligible, eligibility_reason = check_tree_miniapp_eligibility(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not eligible:
        revoke_tree_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled and not maintenance_miniapp_allowed else eligibility_reason
        _set_coordinator("blocked", auth=auth, error=reason, now=now)
        await send_audit_log(
            f"🌳 灵树 MiniApp {reason}，已跳过 WebView/HTTP 接管。",
            scope="identity",
            send_as_id=identity_id,
            limit=180,
        )
        return True
    lock = _global_run_lock()
    if lock.locked():
        await send_audit_log(
            "🌳 灵树 MiniApp 全局已有身份执行，当前入口忽略。",
            scope="identity",
            send_as_id=identity_id,
            limit=160,
        )
        return True
    async with lock:
        revoke_tree_miniapp_manual_run(identity_id)
        _set_coordinator("running", auth=auth, now=now)
        is_daily = auth.get("kind") == "daily"
        mode = str(auth.get("mode") or TREE_MINIAPP_DEFAULT_MODE).strip().lower() or TREE_MINIAPP_DEFAULT_MODE
        await send_audit_log(
            "🌳 灵树 MiniApp 接管入口，开始 WebView/HTTP 流程："
            + ("daily jump→fly。" if is_daily else f"{mode}。")
            + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
            scope="identity",
            send_as_id=identity_id,
            priority="low",
            limit=200,
        )
        capture_sink = _tree_miniapp_capture_store(now)
        capture_source = f"tree_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        common_kwargs = {
            "token": launch.get("token"),
            "webview_url": launch.get("webview_url"),
            "capture_sink": capture_sink,
            "capture_source": capture_source,
        }
        if is_daily:
            result = await run_tree_miniapp_daily_production_flow(
                identity_id,
                score_profiles=dict(auth.get("score_profiles") or {}),
                **common_kwargs,
            )
        else:
            result = await run_tree_miniapp_game_production_flow(
                identity_id,
                mode=mode,
                submit=bool(auth.get("submit", True)),
                score_profile=dict(auth.get("score_profile") or {}),
                **common_kwargs,
            )
        _record_tree_business_capture(capture_sink, result, source=capture_source, now=now)
        result_data = dict(result or {}).get("data") if isinstance(dict(result or {}).get("data"), dict) else {}
        result_phase = str(result_data.get("phase") or ("completed" if dict(result or {}).get("ok") else "blocked"))
        if not dict(result or {}).get("ok") and miniapp_retry_after_sec(result) > 0:
            result_phase = "retry_pending"
        _set_coordinator(
            result_phase if result_phase in {"completed", "blocked", "unknown", "retry_pending"} else "blocked",
            auth=auth,
            result=result,
            error=str(dict(result or {}).get("error") or ""),
            now=now,
        )
        _record_tree_daily_result(auth, result, result_phase, now=time.time())
        summary = html.escape(_format_tree_summary(result), quote=False)
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(
            f"🌳 灵树结果｜{summary}",
            scope="identity",
            send_as_id=identity_id,
            priority=priority,
            limit=220,
        )
        return True


async def run_tree_miniapp_daily_direct(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    day_key="",
    op_id="",
    score_profiles=None,
    now=None,
):
    """Run the daily tree flow from a trusted MiniApp launch without a group command."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    eligible, reason = check_tree_miniapp_eligibility(identity_id, enabled=True)
    if not eligible:
        return {"ok": False, "status": "blocked", "error": reason, "data": {}}
    auth = {
        "kind": "daily",
        "identity_id": identity_id,
        "day_key": str(day_key or get_day_key(now)),
        "op_id": str(op_id or f"tree_public:{get_day_key(now)}:{identity_id}"),
        "command_msg_id": 0,
        "score_profiles": {
            mode: normalize_tree_score_profile(mode, (score_profiles or {}).get(mode))
            for mode in ("jump", "fly")
        },
    }
    lock = _global_run_lock()
    if lock.locked():
        return {"ok": False, "status": "blocked", "error": "灵树 MiniApp 全局已有任务", "data": {}}

    async with lock:
        _set_coordinator("running", auth=auth, now=now)
        capture_sink = _tree_miniapp_capture_store(now)
        capture_source = f"tree_public:{identity_id}:{auth['day_key']}"
        result = await run_tree_miniapp_daily_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            init_data=init_data,
            capture_sink=capture_sink,
            capture_source=capture_source,
            score_profiles=auth["score_profiles"],
        )
        _record_tree_business_capture(capture_sink, result, source=capture_source, now=now)
        result_data = dict(result or {}).get("data") if isinstance(dict(result or {}).get("data"), dict) else {}
        phase = str(result_data.get("phase") or ("completed" if dict(result or {}).get("ok") else "blocked"))
        if not dict(result or {}).get("ok") and miniapp_retry_after_sec(result) > 0:
            phase = "retry_pending"
        if phase not in {"completed", "blocked", "unknown", "retry_pending"}:
            phase = "blocked"
        _set_coordinator(
            phase,
            auth=auth,
            result=result,
            error=str(dict(result or {}).get("error") or ""),
            now=now,
        )
        _record_tree_daily_result(auth, result, phase, now=time.time())
        await send_audit_log(
            f"🌳 灵树结果｜{_format_tree_summary(result)}",
            scope="identity",
            send_as_id=identity_id,
            priority="low" if dict(result or {}).get("ok") else "normal",
            limit=520,
        )
        return dict(result or {})


__all__ = [
    "TREE_MINIAPP_DEFAULT_MODE",
    "TREE_MINIAPP_MANUAL_AUTH_TTL_SEC",
    "authorize_tree_miniapp_manual_run",
    "cancel_tree_miniapp_daily_run",
    "check_tree_miniapp_eligibility",
    "finalize_tree_miniapp_daily_command",
    "get_tree_miniapp_coordinator_snapshot",
    "handle_tree_miniapp_entry",
    "prepare_tree_miniapp_daily_run",
    "revoke_tree_miniapp_manual_run",
    "run_tree_miniapp_daily_direct",
]
