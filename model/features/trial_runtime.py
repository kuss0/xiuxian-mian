import asyncio
import html
import re
import time
from pathlib import Path

from ..runtime import send_audit_log
from ..state import get_current_identity_id, get_global_enabled, get_global_pause_source, get_identity_display_name, is_cave_public_identity_available, get_send_as_profile
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from .trial_miniapp import extract_trial_miniapp_launch, run_trial_miniapp_production_flow


TRIAL_MANUAL_AUTH_TTL_SEC = 10 * 60
TRIAL_MANUAL_MAX_ROUNDS = 99
TRIAL_BATCH_TIMEOUT_SEC = 45 * 60
TRIAL_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}
_BATCH_RUNS = {}
_BATCH_BY_IDENTITY = {}
_TRIAL_GAIN_KEYS = {
    "expgain": "经验",
    "experiencegain": "经验",
    "tracegain": "天机残痕",
    "tianjitracegain": "天机残痕",
    "rewardtrace": "天机残痕",
    "rewardtracegain": "天机残痕",
    "cultivationgain": "修为",
    "xiuweigain": "修为",
    "lingshigain": "灵石",
    "spiritstonegain": "灵石",
}


def _miniapp_http_allowed_during_pause():
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"
_TRIAL_REWARD_CONTAINER_KEYS = {"rewards", "reward", "bonusloot", "loot", "drops", "items", "materials"}
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")


def _identity_id(value=None):
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def authorize_trial_miniapp_manual_run(identity_id, *, now=None, ttl_sec=TRIAL_MANUAL_AUTH_TTL_SEC, batch_id=""):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return 0
    now = float(now or time.time())
    _MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or TRIAL_MANUAL_AUTH_TTL_SEC))
    batch_id = str(batch_id or "").strip()
    if batch_id:
        _BATCH_BY_IDENTITY[identity_id] = batch_id
    return _MANUAL_AUTH_UNTIL[identity_id]


def revoke_trial_miniapp_manual_run(identity_id):
    identity_id = _identity_id(identity_id)
    _MANUAL_AUTH_UNTIL.pop(identity_id, None)
    _BATCH_BY_IDENTITY.pop(identity_id, None)


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
    return re.sub(r"[^A-Za-z0-9]", "", str(key or "")).lower()


def _parse_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _trial_reward_from_value(value, *, fallback_name=""):
    if isinstance(value, str):
        name = value.strip()
        return {"name": name, "qty": 1} if name else {}
    if isinstance(value, (int, float)) and fallback_name:
        qty = _parse_int(value, 0)
        return {"name": str(fallback_name).strip(), "qty": qty} if qty > 0 else {}
    if not isinstance(value, dict):
        return {}
    name = ""
    for key in ("name", "itemName", "item_name", "title", "label"):
        if value.get(key) not in (None, ""):
            name = str(value.get(key) or "").strip()
            break
    if not name and fallback_name:
        name = str(fallback_name).strip()
    if not name:
        return {}
    qty = value.get("qty", value.get("count", value.get("quantity", value.get("amount", 1))))
    return {"name": name, "qty": max(1, _parse_int(qty, 1))}


def _trial_rewards_from_container(value):
    rewards = []
    if isinstance(value, list):
        for item in value:
            reward = _trial_reward_from_value(item)
            if reward:
                rewards.append(reward)
        return rewards
    if isinstance(value, dict):
        direct = _trial_reward_from_value(value)
        if direct:
            return [direct]
        for name, amount in value.items():
            reward = _trial_reward_from_value(amount, fallback_name=name)
            if reward:
                rewards.append(reward)
    return rewards


def _merge_reward_counts(target, rewards):
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        target[name] = int(target.get(name, 0) or 0) + max(1, _parse_int(reward.get("qty"), 1))


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
        return f"MiniApp {status}｜{prefix}{material_text or '已结算'}"
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}｜{error or '未完成'}"


def _trial_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = TRIAL_MINIAPP_CAPTURE_DIR / f"trial-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


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
    batch_id = f"trial_batch_{int(now)}_{len(ids)}"
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
        asyncio.create_task(timeout_coro)
    except RuntimeError:
        timeout_coro.close()
        pass
    return batch_id


def note_trial_batch_send_result(batch_id, identity_id, *, ok, msg_id=0, error=""):
    batch = _BATCH_RUNS.get(str(batch_id or "").strip())
    identity_id = _identity_id(identity_id)
    if not batch or identity_id <= 0:
        return
    batch["send"][identity_id] = {
        "ok": bool(ok),
        "msg_id": int(msg_id or 0),
        "error": str(error or "").strip(),
    }
    if not ok:
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


def _merge_counts(target, source):
    for name, amount in (source or {}).items():
        if not name:
            continue
        target[name] = int(target.get(name, 0) or 0) + int(amount or 0)


def _record_trial_batch_result(batch_id, identity_id, result):
    batch = _BATCH_RUNS.get(str(batch_id or "").strip())
    identity_id = _identity_id(identity_id)
    if not batch or identity_id <= 0:
        return False
    batch["results"][identity_id] = dict(result or {})
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
    batch["finalized"] = True
    expected = list(batch.get("identity_ids") or ())
    results = dict(batch.get("results") or {})
    send_results = dict(batch.get("send") or {})
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
        if result.get("ok"):
            ok_ids.append(identity_id)
            rewards, gains = _trial_batch_materials(result)
            _merge_counts(total_rewards, rewards)
            _merge_counts(total_gains, gains)
        else:
            failed.append((identity_id, result.get("error") or result.get("status") or "未完成"))

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

    await send_audit_log("\n".join(lines), scope="global", priority="normal", limit=1600)
    for identity_id in expected:
        if _BATCH_BY_IDENTITY.get(identity_id) == batch_id:
            _BATCH_BY_IDENTITY.pop(identity_id, None)
    _BATCH_RUNS.pop(batch_id, None)
    return True


async def handle_trial_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0, require_identity_match=False):
    identity_id = _identity_id()
    if identity_id <= 0 or not _has_manual_auth(identity_id, now):
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

    async with lock:
        batch_id = _BATCH_BY_IDENTITY.pop(identity_id, "")
        revoke_trial_miniapp_manual_run(identity_id)
        if not batch_id:
            await send_audit_log(
                "🧪 天机试炼 MiniApp 接管入口，开始 WebView/HTTP 流程。"
                + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
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
        safe_summary = html.escape(summary, quote=False)
        if batch_id:
            _record_trial_batch_result(batch_id, identity_id, dict(result or {}))
            await maybe_finalize_trial_batch_run(batch_id)
            return True
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(f"🧪 天机试炼结果｜{safe_summary}", scope="identity", priority=priority, limit=220)
        return True


__all__ = [
    "TRIAL_MANUAL_AUTH_TTL_SEC",
    "TRIAL_MANUAL_MAX_ROUNDS",
    "authorize_trial_miniapp_manual_run",
    "finalize_trial_batch_run",
    "handle_trial_miniapp_entry",
    "note_trial_batch_send_result",
    "revoke_trial_miniapp_manual_run",
    "start_trial_miniapp_batch_run",
]
