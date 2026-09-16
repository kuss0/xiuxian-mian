import asyncio
import html
import logging
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

from ..config import STATE_DIR
from ..runtime import send_audit_log, track_background_task
from ..state import get_current_identity_id, get_global_enabled, get_global_pause_source, get_identity_display_name, is_cave_public_identity_available, get_send_as_profile
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from .trial_miniapp import extract_trial_miniapp_launch, run_trial_miniapp_production_flow
from .miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner, append_business_capture, resolve_identity_id as _identity_id
from .trial_receipts import TRIAL_GAIN_KEYS as _TRIAL_GAIN_KEYS, TRIAL_REWARD_CONTAINER_KEYS as _TRIAL_REWARD_CONTAINER_KEYS, normalize_trial_result_key, parse_trial_rewards
from . import trial_operations


TRIAL_MANUAL_AUTH_TTL_SEC = 10 * 60
TRIAL_MANUAL_MAX_ROUNDS = 99
TRIAL_BATCH_TIMEOUT_SEC = 45 * 60
TRIAL_MINIAPP_CAPTURE_DIR = Path(STATE_DIR) / "miniapp_capture"

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}
_BATCH_RUNS = {}
_BATCH_BY_IDENTITY = {}
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")




def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


def _batch_accepts_identity(batch, identity_id):
    return bool(
        batch and not batch.get("finalized") and identity_id > 0
        and identity_id in (batch.get("identity_ids") or ())
    )


def _batch_accepts_authorization(batch, identity_id):
    return _batch_accepts_identity(batch, identity_id) and not batch.get("authorization_closed")


def authorize_trial_miniapp_manual_run(identity_id, *, now=None, ttl_sec=TRIAL_MANUAL_AUTH_TTL_SEC, batch_id=""):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0 or is_trial_miniapp_busy(identity_id):
        return 0
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is not None and not trial_operations.admission_allowed(owner):
        return 0
    batch_id = str(batch_id or "").strip()
    if batch_id and not _batch_accepts_authorization(_BATCH_RUNS.get(batch_id), identity_id):
        return 0
    now = float(now or time.time())
    _MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or TRIAL_MANUAL_AUTH_TTL_SEC))
    if batch_id:
        _BATCH_BY_IDENTITY[identity_id] = batch_id
    else:
        _BATCH_BY_IDENTITY.pop(identity_id, None)
    return _MANUAL_AUTH_UNTIL[identity_id]


def revoke_trial_miniapp_manual_run(identity_id, *, batch_id=None):
    identity_id = _identity_id(identity_id)
    if batch_id is not None and _BATCH_BY_IDENTITY.get(identity_id) != str(batch_id or "").strip():
        return False
    _MANUAL_AUTH_UNTIL.pop(identity_id, None)
    _BATCH_BY_IDENTITY.pop(identity_id, None)
    return True


def _has_manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    expires_at = float(_MANUAL_AUTH_UNTIL.get(identity_id, 0) or 0)
    if expires_at <= 0 or float(now or time.time()) > expires_at:
        revoke_trial_miniapp_manual_run(identity_id)
        return False
    batch_id = _BATCH_BY_IDENTITY.get(identity_id)
    if batch_id and not _batch_accepts_authorization(_BATCH_RUNS.get(batch_id), identity_id):
        revoke_trial_miniapp_manual_run(identity_id, batch_id=batch_id)
        return False
    return True


def is_trial_miniapp_busy(identity_id):
    lock = _RUN_LOCKS.get(_identity_id(identity_id))
    return lock is not None and lock.locked()


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


def _normalize_result_key(key):
    return normalize_trial_result_key(key)


def _parse_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _trial_rewards_from_container(value):
    rewards, _error = parse_trial_rewards(value)
    return rewards


def _merge_reward_counts(target, rewards):
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        quantity = _parse_int(reward.get("qty"), 0)
        if quantity > 0:
            target[name] = int(target.get(name, 0) or 0) + quantity


def _collect_trial_materials(value, *, rewards=None, gains=None, depth=0):
    rewards = rewards if rewards is not None else {}
    gains = gains if gains is not None else {}
    if depth > 4:
        return rewards, gains
    if isinstance(value, list):
        for item in value:
            _collect_trial_materials(item, rewards=rewards, gains=gains, depth=depth + 1)
        return rewards, gains
    if not isinstance(value, dict):
        return rewards, gains
    for key, child in value.items():
        normalized = _normalize_result_key(key)
        if normalized in _TRIAL_REWARD_CONTAINER_KEYS:
            _merge_reward_counts(rewards, _trial_rewards_from_container(child))
            continue
        gain_label = _TRIAL_GAIN_KEYS.get(normalized)
        if gain_label:
            amount = _parse_int(child, 0)
            if amount > 0:
                gains[gain_label] = int(gains.get(gain_label, 0) or 0) + amount
            continue
        if normalized in {"score", "sessionid", "qualitybonus", "ready", "durationms", "mode", "challengeid"}:
            continue
        _collect_trial_materials(child, rewards=rewards, gains=gains, depth=depth + 1)
    return rewards, gains


def _format_trial_material_summary(data):
    rewards, gains = _collect_trial_materials(data or {})
    parts = []
    if gains:
        parts.append("收益:" + "、".join(f"{name}+{amount}" for name, amount in sorted(gains.items()) if amount > 0))
    if rewards:
        parts.append("奖励:" + "、".join(f"{name}x{amount}" for name, amount in sorted(rewards.items()) if amount > 0))
    return "｜".join(parts)


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
        material_text = _format_trial_material_summary(data)
        summary = f"MiniApp {status}｜{prefix}{material_text or '已结算'}"
        error = str(result.get("error") or "").strip()
        if error and not _trial_result_completed_ok(result):
            summary += f"｜{error}"
        return summary
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}｜{error or '未完成'}"


def _trial_recovery_response(result):
    rewards, gains = _trial_batch_materials(result)
    return {
        "ok": _trial_result_completed_ok(result),
        "message": f"天机试炼本地恢复：{_format_trial_summary(result)}",
        "extra": {"status": result.get("status"), "error": result.get("error"),
                  "persistence_only": True, "rewards": rewards, "gains": gains,
                  "settled_count": (result.get("data") or {}).get("settled_count", 0),
                  "confirmed_rounds_retained": result.get("confirmed_rounds_retained", 0)},
    }


def _trial_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = TRIAL_MINIAPP_CAPTURE_DIR / f"trial-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


def _record_trial_business_capture(capture_sink, result, *, source, now):
    result = dict(result or {})
    if not result.get("ok"):
        return {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    settled_count = _parse_int(result.get("settled_count") or data.get("settled_count"), 0)
    if settled_count <= 0:
        settled_count = 1 if str(result.get("status") or "") == "settled" else 0
    if settled_count <= 0:
        return {}
    rewards, gains = _collect_trial_materials(data)
    return append_business_capture(
        capture_sink,
        adapter_key="trial",
        detail={
            "settled_count": settled_count,
            "gains": gains,
            "items": rewards,
        },
        source=source,
        created_at=now,
    )


def start_trial_miniapp_batch_run(identity_ids, *, now=None, timeout_sec=TRIAL_BATCH_TIMEOUT_SEC):
    now = float(now or time.time())
    ids = []
    seen = set()
    for raw_id in identity_ids or ():
        identity_id = _identity_id(raw_id)
        if identity_id <= 0 or identity_id in seen:
            continue
        seen.add(identity_id)
        ids.append(identity_id)
    if not ids:
        return ""
    batch_id = f"trial_batch_{int(now)}_{uuid.uuid4().hex}"
    _BATCH_RUNS[batch_id] = {
        "batch_id": batch_id,
        "identity_ids": ids,
        "started_at": now,
        "timeout_at": now + max(300, float(timeout_sec or TRIAL_BATCH_TIMEOUT_SEC)),
        "send": {},
        "results": {},
        "finalized": False,
    }
    timeout_coro = _trial_batch_timeout_worker(batch_id, max(300, float(timeout_sec or TRIAL_BATCH_TIMEOUT_SEC)))
    try:
        track_background_task(asyncio.create_task(timeout_coro))
    except RuntimeError:
        timeout_coro.close()
        pass
    return batch_id


def note_trial_batch_send_result(batch_id, identity_id, *, ok, msg_id=0, error=""):
    batch = _BATCH_RUNS.get(str(batch_id or "").strip())
    identity_id = _identity_id(identity_id)
    if not _batch_accepts_identity(batch, identity_id):
        return
    batch["send"][identity_id] = {
        "ok": bool(ok),
        "msg_id": int(msg_id or 0),
        "error": str(error or "").strip(),
    }
    if not ok and identity_id not in batch["results"]:
        _record_trial_batch_result(batch["batch_id"], identity_id, {
            "ok": False,
            "status": "send_failed",
            "error": str(error or "未发送").strip() or "未发送",
            "data": {},
        })


async def _trial_batch_timeout_worker(batch_id, timeout_sec):
    await asyncio.sleep(max(1.0, float(timeout_sec or TRIAL_BATCH_TIMEOUT_SEC)))
    await finalize_trial_batch_run(batch_id, reason="timeout")


def _trial_batch_materials(result):
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
    rewards, gains = _collect_trial_materials(data or {})
    return rewards, gains


def _trial_result_completed_ok(result):
    """Return whether a trial result completed its requested flow.

    Settled rounds remain reportable during partial, unavailable or cancelled
    runs. Only a terminal result completes the requested flow.
    """
    result = dict(result or {})
    status = str(result.get("status") or "").strip().lower()
    return (result.get("ok") is True and status == "settled") or status == "daily_limit"


def _trial_has_settlements(result):
    result = dict(result or {})
    if result.get("ok") is not True:
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return (
        _parse_int(result.get("settled_count") or data.get("settled_count"), 0) > 0
        or result.get("status") == "settled"
    )


def _merge_counts(target, source):
    for name, amount in (source or {}).items():
        if not name:
            continue
        target[name] = int(target.get(name, 0) or 0) + int(amount or 0)


def _record_trial_batch_result(batch_id, identity_id, result):
    batch = _BATCH_RUNS.get(str(batch_id or "").strip())
    identity_id = _identity_id(identity_id)
    if not _batch_accepts_identity(batch, identity_id):
        return False
    batch["results"][identity_id] = deepcopy(dict(result or {}))
    return True


def _all_trial_batch_results_ready(batch):
    expected = set(batch.get("identity_ids") or ())
    return bool(expected) and expected.issubset(set((batch.get("results") or {}).keys()))


async def maybe_finalize_trial_batch_run(batch_id):
    batch = _BATCH_RUNS.get(str(batch_id or "").strip())
    if batch and _all_trial_batch_results_ready(batch):
        await finalize_trial_batch_run(batch_id, reason="complete")


async def finalize_trial_batch_run(batch_id, *, reason="complete"):
    batch_id = str(batch_id or "").strip()
    batch = _BATCH_RUNS.get(batch_id)
    if not batch or batch.get("finalized"):
        return False
    lock = batch.setdefault("report_lock", asyncio.Lock())
    async with lock:
        if _BATCH_RUNS.get(batch_id) is not batch or batch.get("finalized"):
            return False
        return await _deliver_trial_batch_report(batch_id, batch, reason=reason)


async def _deliver_trial_batch_report(batch_id, batch, *, reason):
    expected = list(batch.get("identity_ids") or ())
    results = deepcopy(batch.get("results") or {})
    send_results = deepcopy(batch.get("send") or {})
    snapshot = {"identity_ids": expected, "results": results, "send": send_results}
    if batch.get("reported_snapshot") == snapshot:
        return False
    ok_ids = []
    failed = []
    pending = []
    total_rewards = {}
    total_gains = {}
    for identity_id in expected:
        result = results.get(identity_id)
        if not result:
            send = send_results.get(identity_id) or {}
            if send.get("ok"):
                pending.append(identity_id)
            else:
                failed.append((identity_id, send.get("error") or "未发送"))
            continue
        if _trial_result_completed_ok(result):
            ok_ids.append(identity_id)
        else:
            failed.append((identity_id, result.get("error") or result.get("status") or "未完成"))
        if _trial_has_settlements(result):
            rewards, gains = _trial_batch_materials(result)
            _merge_counts(total_rewards, rewards)
            _merge_counts(total_gains, gains)

    lines = [
        f"🧪 天机试炼批量结果｜{len(ok_ids)}/{len(expected)} 成功｜原因:{reason}",
    ]
    if total_gains:
        lines.append("收益：" + "、".join(f"{name}+{amount}" for name, amount in sorted(total_gains.items()) if amount > 0))
    if total_rewards:
        lines.append("奖励：" + "、".join(f"{name}x{amount}" for name, amount in sorted(total_rewards.items()) if amount > 0))
    if ok_ids:
        lines.append("成功：" + "、".join(get_identity_display_name(identity_id) for identity_id in ok_ids))
    if failed:
        lines.append("失败：" + "、".join(
            f"{get_identity_display_name(identity_id)}({html.escape(str(error), quote=False)[:40]})"
            for identity_id, error in failed[:12]
        ))
        if len(failed) > 12:
            lines.append(f"失败余量：{len(failed) - 12} 个")
    if pending:
        lines.append("未回包：" + "、".join(get_identity_display_name(identity_id) for identity_id in pending[:12]))
        if len(pending) > 12:
            lines.append(f"未回包余量：{len(pending) - 12} 个")

    try:
        delivered = await send_audit_log("\n".join(lines), scope="global", priority="normal", limit=1600)
    except Exception as exc:
        logging.getLogger(__name__).warning("Trial batch report failed (%s); results retained", type(exc).__name__)
        return False
    if delivered is not True or _BATCH_RUNS.get(batch_id) is not batch:
        return False
    batch["reported_snapshot"] = snapshot
    # A late result belongs in a subsequent report, not in this delivered snapshot.
    if (
        results != (batch.get("results") or {})
        or send_results != (batch.get("send") or {})
        or expected != list(batch.get("identity_ids") or ())
    ):
        return False
    batch["authorization_closed"] = True
    for identity_id in expected:
        revoke_trial_miniapp_manual_run(identity_id, batch_id=batch_id)
    if not _all_trial_batch_results_ready(batch):
        return False
    batch["finalized"] = True
    _BATCH_RUNS.pop(batch_id, None)
    return True


async def handle_trial_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0, require_identity_match=False):
    identity_id = _identity_id()
    owner = MiniAppIdentityOwner.capture(identity_id)
    if identity_id <= 0 or owner is None or not _has_manual_auth(identity_id, now):
        return False
    if require_identity_match and not _entry_mentions_current_identity(text):
        return False
    launch = extract_trial_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    identity_available = is_cave_public_identity_available(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_available:
        revoke_trial_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled else "身份已停用"
        await send_audit_log(f"🧪 天机试炼 MiniApp {reason}，已跳过 WebView/HTTP 接管。", scope="identity", limit=180)
        return True

    lock = _run_lock(identity_id)
    if lock.locked():
        await send_audit_log("🧪 天机试炼 MiniApp 已在执行，重复入口忽略。", scope="identity", limit=160)
        return True

    def can_continue():
        return (
            owner.is_current() and is_cave_public_identity_available(identity_id)
            and (get_global_enabled() or _miniapp_http_allowed_during_pause())
        )

    async with lock:
        if not can_continue():
            return True
        batch_id = _BATCH_BY_IDENTITY.pop(identity_id, "")
        batch = _BATCH_RUNS.get(batch_id)
        revoke_trial_miniapp_manual_run(identity_id)
        recovered = trial_operations.recover_local(identity_id)
        if not batch_id and recovered is None:
            await send_audit_log(
                "🧪 天机试炼 MiniApp 接管入口，开始 WebView/HTTP 流程。"
                + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
                scope="identity",
                send_as_id=identity_id,
                priority="low",
                limit=180,
            )
        if not can_continue():
            return True
        capture_sink = _trial_miniapp_capture_store(now)
        capture_source = f"trial_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        cancelled_flow = None
        writer = None
        if recovered is not None:
            result = recovered
        else:
            writer = trial_operations.CheckpointWriter(owner, operation_check=can_continue)
            try:
                result = await run_trial_miniapp_production_flow(
                    identity_id,
                    token=launch.get("token"),
                    webview_url=launch.get("webview_url"),
                    max_rounds=TRIAL_MANUAL_MAX_ROUNDS,
                    capture_sink=capture_sink,
                    capture_source=capture_source,
                    operation_check=lambda: can_continue() and writer.is_current(),
                    checkpoint=writer,
                )
            except MiniAppFlowCancelled as exc:
                cancelled_flow = exc
                result = exc.result if isinstance(exc.result, dict) else {}
        result = dict(result or {})
        if not owner.is_current():
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return True
        if writer is not None:
            result = trial_operations.finish_result(writer, result)
        if _trial_has_settlements(result):
            _record_trial_business_capture(capture_sink, result, source=capture_source, now=now)
        summary = _format_trial_summary(result)
        safe_summary = html.escape(summary, quote=False)
        if batch_id:
            if batch is not None and _BATCH_RUNS.get(batch_id) is batch:
                _record_trial_batch_result(batch_id, identity_id, result)
                if cancelled_flow is None:
                    try:
                        await maybe_finalize_trial_batch_run(batch_id)
                    except asyncio.CancelledError:
                        raise MiniAppFlowCancelled(result if owner.is_current() else None) from None
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(result) from None
            return True
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(result) from None
        priority = "low" if _trial_result_completed_ok(result) else "normal"
        try:
            await send_audit_log(f"🧪 天机试炼结果｜{safe_summary}", scope="identity", send_as_id=identity_id, priority=priority, limit=220)
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(result if owner.is_current() else None) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Trial result report failed (%s); settlement retained", type(exc).__name__)
        return True


__all__ = [
    "TRIAL_MANUAL_AUTH_TTL_SEC",
    "TRIAL_MANUAL_MAX_ROUNDS",
    "authorize_trial_miniapp_manual_run",
    "finalize_trial_batch_run",
    "handle_trial_miniapp_entry",
    "is_trial_miniapp_busy",
    "note_trial_batch_send_result",
    "revoke_trial_miniapp_manual_run",
    "start_trial_miniapp_batch_run",
]
