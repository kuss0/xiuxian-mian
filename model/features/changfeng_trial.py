"""Bounded Longwind trials, ported from wxjerry 9d3b056a to local ownership APIs."""
import asyncio
import copy
import json
import math
import random
import time
import uuid
from datetime import datetime, timedelta

from ..config import TZ_LOCAL
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, track_background_task
from ..activity_occupancy import get_activity_block_reason
from ..state import (
    get_expedition_config, get_expedition_run_state, get_global_enabled,
    get_identity_ids, get_miniapp_auto_config,
    is_cave_public_identity_available,
)
from ..webapp_core import (
    MiniAppRequestBudget, MiniAppRequestPolicy, build_miniapp_http_request,
    execute_miniapp_http_request, require_miniapp_operation,
)
from .cave_treasure_miniapp import build_cave_treasure_miniapp_adapter
from .miniapp_common import MiniAppIdentityOwner, build_pooled_miniapp_transport, run_miniapp_blocking_flow

TRIAL_ARENAS = {"longwind": "麦田试炼", "bamboo": "竹林试炼"}
TRIAL_CARRY = 3
TRIAL_PATH = "/api/miniapp/xianxia-dwelling/trial"
_TASKS = {}


class TrialError(RuntimeError):
    pass


def _trial_carry(status):
    value = status.get("carryAvailable")
    try:
        available = TRIAL_CARRY if value is None else int(value)
    except (TypeError, ValueError, OverflowError):
        available = 0
    return min(TRIAL_CARRY, max(0, available))


def report_score(report):
    metrics = report["metrics"]
    keys = ("enemyHits", "parries", "reflects", "guardBreaks", "potions", "retries")
    if any(type(metrics.get(key)) is not int or not 0 <= metrics[key] <= 100 for key in keys):
        raise TrialError("trial_report_invalid")
    return (100 - 4 * metrics["enemyHits"] + 3 * metrics["parries"]
            + 4 * metrics["reflects"] + 2 * metrics["guardBreaks"]
            - 3 * metrics["potions"] - metrics["retries"])


def _trial_report(_profile, wave, carry, *, rng=None):
    """Use a varied conservative grade; never emit upstream's fixed top-grade metrics."""
    rng = rng or random.SystemRandom()
    for _ in range(1000):
        metrics = {"enemyHits": rng.randint(10, 16), "parries": rng.randint(3, 7),
                   "reflects": rng.randint(0, 2), "guardBreaks": rng.randint(1, 3),
                   "potions": rng.randint(0, 2), "retries": 0}
        report = {"metrics": metrics, "finisher": None, "hpPotionsUsed": metrics["potions"],
                  "manaPotionsUsed": 0, "carryLeft": max(0, min(TRIAL_CARRY, int(carry)))}
        serialized = json.dumps(report, separators=(",", ":"))
        if 55 <= report_score(json.loads(serialized)) < 75:
            return report
    raise TrialError("trial_report_generation_failed")


def _operation_id(kind):
    return f"{kind}-{uuid.uuid4().hex}"


async def run_protocol(call, arena, record, save, *, sleeper=asyncio.sleep, clock=time.monotonic, rng=None):
    """Replayable protocol kernel. Transport errors never authorize replay of a mutation."""
    if arena not in TRIAL_ARENAS:
        raise TrialError("trial_arena_invalid")
    trial = await call("status", arena=arena)
    status = trial.get("status")
    if not isinstance(status, dict):
        raise TrialError("trial_status_missing")
    deadline = clock() + 900
    for attempt in range(3):
        tier = (status.get("tier") or {}).get("next")
        if type(tier) is not int or tier < 1:
            raise TrialError("trial_tier_missing")
        carry = _trial_carry(status)
        record.update(phase="starting", operation_id=_operation_id("start"), arena=arena, tier=tier)
        save()
        try:
            trial = await call("start", operationId=record["operation_id"], arena=arena, tier=tier, carry=carry)
        except TrialError as exc:
            if str(exc) != "trial_tier_locked" or attempt == 2:
                raise
            trial = await call("status", arena=arena)
            status = trial.get("status") or {}
            continue
        break
    run = trial.get("run")
    if not isinstance(run, dict) or not str(run.get("id") or "") or type(run.get("rewarded")) is not bool:
        raise TrialError("trial_run_missing")
    record.update(run_id=str(run["id"]), rewarded=run["rewarded"], phase="running", wave=0)
    # Starting a rewarded run consumes its daily slot even if later evidence is held.
    if run["rewarded"]:
        record["daily_used"] = int(record.get("daily_used") or 0) + 1
    save()
    if not run["rewarded"]:
        record.update(phase="ending", operation_id=_operation_id("end"))
        save()
        await call("end", operationId=record["operation_id"], runId=record["run_id"], reason="quit",
                   report=_trial_report("rough", 0, carry, rng=rng))
        record.update(phase="done", result="今日有奖次数已用完", exhausted=True)
        save()
        return record
    gains, cultivation = {}, 0
    for wave in range(4):
        report = _trial_report("rough", wave, carry, rng=rng)
        # Recompute the score from the exact serialized payload before transmission.
        serialized = json.loads(json.dumps(report, separators=(",", ":")))
        if not 55 <= report_score(serialized) < 75:
            raise TrialError("trial_report_score_mismatch")
        for _ in range(14):
            if clock() >= deadline:
                raise TrialError("trial_checkpoint_deadline")
            record.update(phase="checkpoint", wave=wave, report=serialized,
                          operation_id=_operation_id(f"wave{wave}"))
            save()
            trial = await call("checkpoint", operationId=record["operation_id"],
                               runId=record["run_id"], wave=wave, report=serialized)
            record.setdefault("receipts", []).append({
                "wave": wave, "report": serialized,
                "receipt": {key: copy.deepcopy(trial[key]) for key in
                            ("pending", "retryAfter", "anomaly", "grants", "cultivation", "result") if key in trial},
            })
            save()
            if trial.get("anomaly"):
                raise TrialError("trial_verification_mismatch")
            if not trial.get("pending"):
                break
            wait = trial.get("retryAfter")
            if type(wait) not in (int, float) or not math.isfinite(wait) or wait <= 0 or clock() + wait >= deadline:
                raise TrialError("trial_retry_after_invalid")
            # Pending is explicit permission to retry this wave after the full server delay.
            await sleeper(wait + 0.4)
        else:
            raise TrialError("trial_checkpoint_timeout")
        result = trial.get("result") or {}
        if result.get("grade") and result["grade"] != "乙":
            raise TrialError("trial_verification_mismatch")
        if "score" in result and (type(result["score"]) is not int or result["score"] != report_score(serialized)):
            raise TrialError("trial_verification_mismatch")
        for grant in trial.get("grants") or []:
            if isinstance(grant, dict) and isinstance(grant.get("name"), str) and type(grant.get("quantity")) is int and grant["quantity"] > 0:
                gains[grant["name"]] = gains.get(grant["name"], 0) + grant["quantity"]
        amount = trial.get("cultivation")
        if type(amount) is int and amount > 0:
            cultivation += amount
        record.update(phase="running", confirmed_wave=wave, gains=dict(gains), cultivation=cultivation)
        save()
    if not isinstance(trial.get("result"), dict) or not trial["result"].get("grade"):
        raise TrialError("trial_terminal_receipt_missing")
    record.update(phase="done", result="、".join(f"{name}×{count}" for name, count in gains.items()) or "无物品收获",
                  completed_at=time.time(), grade=trial["result"]["grade"])
    if cultivation:
        record["result"] += f"，修为+{cultivation}"
    save()
    return record


def _records():
    return get_expedition_run_state().setdefault("changfeng", {})


def _save():
    mark_dirty()
    if save_state() is False:
        raise TrialError("trial_state_save_failed")


def _day(now):
    return datetime.fromtimestamp(now, TZ_LOCAL).strftime("%Y-%m-%d")


def _next_day(now, identity_id):
    tomorrow = (datetime.fromtimestamp(now, TZ_LOCAL) + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
    return tomorrow.timestamp() + abs(int(identity_id)) % 24 * 150


def _entry_url():
    config = get_miniapp_auto_config()
    urls = config.get("cave_public_entry_urls") or [config.get("cave_public_entry_url")]
    return next((url for url in urls if isinstance(url, str) and url.strip()), "")


def confirm_changfeng_finished(identity_id, receipt_key):
    """An explicit operator acknowledgement releases a held record, never replays it."""
    record = _records().get(str(identity_id)) or {}
    key = str(record.get("run_id") or record.get("operation_id") or "")
    if record.get("phase") != "held" or not key or receipt_key != key:
        return False, "须为待核对记录，并填写状态中显示的准确记录号"
    record.update(phase="done", operator_confirmed_at=time.time(), result="人工已核对结束", next_at=_next_day(time.time(), identity_id))
    # An uncertain start may already have consumed today's quota.
    record["daily_used"] = max(1, int(record.get("daily_used") or 0))
    _save()
    return True, "已记录人工确认，保留原回执；本次不会重放，次日再安排"


async def run_changfeng_once(identity_id, arena="longwind", *, manual=False, status_only=False):
    from . import cave_treasure_runtime as cave
    identity_id = int(identity_id)
    config = copy.deepcopy(get_expedition_config().get("changfeng", {}))
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None or not is_cave_public_identity_available(identity_id) or not get_global_enabled():
        return False, "身份不可用或全局已暂停"
    if get_activity_block_reason(identity_id, include_changfeng=False):
        return False, "身份正在参加其他活动"
    if not manual and (not config.get("enabled") or identity_id not in config.get("participant_identity_ids", [])):
        return False, "长风试炼未启用"
    old = _records().get(str(identity_id), {})
    if not status_only and old.get("phase") not in {None, "done", "idle"}:
        return False, "旧试炼尚未核对，保留记录，请先在游戏中确认结果"
    now = time.time()
    used = int(old.get("daily_used") or 0) if old.get("day") == _day(now) else 0
    if not status_only and used >= int(config.get("daily_limit", 1)):
        return False, "已达到本地每日试炼限额"
    lock = cave._public_entry_lock(identity_id)
    if lock.locked():
        return False, "洞府公共入口操作执行中"
    public_url = _entry_url()
    token, webview_url, error = cave._parse_public_cave_entry_url(public_url)
    if error:
        return False, error
    record = {"phase": "loading", "day": _day(now), "daily_used": used, "arena": arena,
              "identity_id": identity_id, "account_id": owner.account_id, "started_at": now}
    if status_only:
        record = old or {"phase": "idle", "identity_id": identity_id}

    def allowed():
        return (owner.is_current() and is_cave_public_identity_available(identity_id)
                and not get_activity_block_reason(identity_id, include_changfeng=False) and get_global_enabled() and cave._public_entry_allowed()
                and get_expedition_config().get("changfeng", {}) == config
                and _entry_url() == public_url and _records().get(str(identity_id)) is record)

    async with lock:
        _records()[str(identity_id)] = record
        _save()
        try:
            session = await cave._load_cave_public_identity_session(
                identity_id, token, webview_url, now=now, capture_source=f"changfeng:{identity_id}",
                include_details=False, operation_check=allowed,
            )
            require_miniapp_operation(allowed)
            if not session.get("ok"):
                raise TrialError("trial_session_unavailable")
            adapter = build_cave_treasure_miniapp_adapter()
            transport = build_pooled_miniapp_transport(adapter_key="changfeng_trial", identity_id=identity_id, operation_check=allowed)
            budget = MiniAppRequestBudget(MiniAppRequestPolicy(min_interval_sec=1, max_requests_per_run=64, max_attempts_per_request=1))
            last_request = [0.0]

            async def call(action, **payload):
                require_miniapp_operation(allowed)
                wait = 1 - (time.monotonic() - last_request[0])
                if wait > 0:
                    await asyncio.sleep(wait)
                request = build_miniapp_http_request(
                    adapter, TRIAL_PATH, {"token": token, "playerId": session["player_id"], "action": action, **payload},
                    init_data=session["init_data"],
                )
                last_request[0] = time.monotonic()
                result = await run_miniapp_blocking_flow(
                    lambda operation: execute_miniapp_http_request(
                        request, transport, request_budget=budget, operation_check=operation.check, sleeper=operation.sleep,
                        capture_sink=cave._capture_store(now), capture_source=f"changfeng:{identity_id}", step_key=action,
                    ), operation_check=allowed,
                )
                require_miniapp_operation(allowed)
                data = result.data
                if not result.ok or not isinstance(data, dict) or data.get("ok") is not True:
                    code = data.get("error") if isinstance(data, dict) else None
                    raise TrialError(code if code == "trial_tier_locked" else "trial_request_unconfirmed")
                if not isinstance(data.get("trial"), dict):
                    raise TrialError("trial_payload_missing")
                if data.get("playerId") is not None and str(data["playerId"]) != str(session["player_id"]):
                    raise TrialError("trial_player_mismatch")
                return data["trial"]

            if status_only:
                response = await call("status", arena=arena)
                status = response.get("status")
                if not isinstance(status, dict):
                    raise TrialError("trial_status_missing")
                observed = {key: copy.deepcopy(status[key]) for key in ("used", "limit", "remaining", "carryAvailable", "tier", "day", "arena") if key in status}
                record.update(observed_status=observed, observed_at=time.time())
                record.setdefault("observed_arenas", {})[arena] = {"status": observed, "observed_at": record["observed_at"]}
                _save()
                message = f"游戏状态：{json.dumps(observed, ensure_ascii=False)}；本地阶段 {record['phase']}，核对不会补发动作"
                await send_audit_log(message, scope="identity", send_as_id=identity_id, limit=700)
                return True, message
            await run_protocol(call, arena, record, _save)
            record["next_at"] = (_next_day(time.time(), identity_id) if record.get("exhausted")
                                 or record.get("daily_used", 0) >= int(config.get("daily_limit", 1))
                                 else time.time() + 15 * 60)
            _save()
            await send_audit_log(f"⚔️ 长风试炼·{TRIAL_ARENAS[arena]}｜{record['result']}",
                                 scope="identity", send_as_id=identity_id, limit=500)
            return True, record["result"]
        except asyncio.CancelledError:
            if not status_only:
                if record.get("operation_id"):
                    record.update(phase="held", last_error="试炼执行中断，需核对游戏结果")
                else:
                    record.update(phase="idle", next_at=time.time() + 900, last_error="入口读取中断，尚未开局")
            _save()
            raise
        except Exception as exc:
            code = str(exc) if isinstance(exc, TrialError) else type(exc).__name__
            if status_only:
                record["last_status_error"] = code
            elif not record.get("operation_id"):
                record.update(phase="idle", next_at=time.time() + 900, last_error=code)
            else:
                record.update(phase="held", last_error=code)
            _save()
            await send_audit_log(f"⚔️ 长风试炼已停止，保留记录待核对：{code}", scope="identity", send_as_id=identity_id, limit=300)
            return False, "试炼未获完整确认，已保留记录待核对"


async def run_changfeng_scheduler(now):
    config = get_expedition_config().get("changfeng", {})
    if not config.get("enabled") or not get_global_enabled() or not _entry_url() or _TASKS:
        return
    for identity_id in config.get("participant_identity_ids", []):
        if identity_id not in get_identity_ids() or not is_cave_public_identity_available(identity_id):
            continue
        record = _records().get(str(identity_id), {})
        if record.get("phase") not in {None, "done", "idle"} or float(record.get("next_at") or 0) > now:
            continue
        if record.get("day") == _day(now) and int(record.get("daily_used") or 0) >= int(config.get("daily_limit", 1)):
            continue
        arenas = config.get("arenas") or ["longwind", "bamboo"]
        arena = arenas[(int(record.get("daily_used") or 0) + abs(identity_id)) % len(arenas)]
        task = track_background_task(asyncio.create_task(run_changfeng_once(identity_id, arena)))
        _TASKS[identity_id] = task

        def done(completed, identity=identity_id):
            _TASKS.pop(identity, None)
            if not completed.cancelled() and completed.exception() is not None:
                print(f"Changfeng task failed: {type(completed.exception()).__name__}")

        task.add_done_callback(done)
        return


def reset_changfeng_runtime_on_startup():
    changed = False
    for record in _records().values():
        if isinstance(record, dict) and record.get("phase") not in {None, "done", "idle", "held"}:
            record.update(phase="held", last_error="程序重启，试炼结果待核对")
            changed = True
    if changed:
        mark_dirty()
