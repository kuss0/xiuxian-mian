"""Native fishing entry; retains the existing UI plan and locks."""

import math
import time
from copy import deepcopy

from ..state import use_identity
from .. import persistence
from . import fishing_runtime as fishing
from .fishing_dwelling_miniapp import run_native_fishing_production_flow
from .fishing_dwelling_store import STATE_KEY
from .fishing_dwelling_journal import validate
from . import fishing_dwelling_supply as supply
from .miniapp_common import MiniAppFlowCancelled


SITE_CHOICES = {"青溪浅滩": "west-shore", "灵眼寒潭": "waterfall-pool", "乱星海礁": "east-shore"}
VOYAGE_HANDOFF_SEC = 15 * 60
SAILING_RECHECK_SEC = 10 * 60


def pending(identity):
    for key, validator in ((STATE_KEY, validate), (supply.STATE_KEY, supply.validate)):
        record = identity.get(key, {})
        if record != {}:
            try:
                validator(record)
            except (ValueError, TypeError, OverflowError, RecursionError):
                return True
            if record["phase"] != "accounted":
                return True
    return False


def integrated(raw):
    if not isinstance(raw, dict):
        return False
    account = raw.get("account")
    center = account.get("commandCenter") if isinstance(account, dict) else None
    entries = center.get("entries") if isinstance(center, dict) else None
    return isinstance(entries, list) and any(isinstance(row, dict) and row.get("key") == "fishing"
                                            and row.get("status") == "integrated" for row in entries)


def voyage_launch_wait_reason(identity_id, now):
    """Bound the return-to-fishing handoff; never postpone return/status reads."""
    identity = fishing.get_identity_state(identity_id)
    if fishing._fishing_send_lock(identity_id).locked():
        return "钓鱼执行中，暂缓再次远航"
    record = identity.get(STATE_KEY, {})
    if record:
        try:
            validate(record)
        except (ValueError, TypeError, OverflowError, RecursionError):
            record = {}
        if (record and record["phase"] not in {"settled", "accounted"}
                and 0 <= now - record["created_at"] < 300):
            return "当前鱼竿仍在结算窗口，暂缓再次远航"
    config = fishing.get_miniapp_auto_config()
    if not (config.get("cave_public_entry_urls") or config.get("cave_public_entry_url")):
        return ""
    if not (identity.get("fishing_enabled") or fishing.is_cave_public_auto_enabled("fishing", identity_id)):
        return ""
    returned_at = identity.get("concubine_voyage_settled_at", 0)
    if type(returned_at) not in (int, float) or not 0 < returned_at <= now < returned_at + VOYAGE_HANDOFF_SEC:
        return ""
    if not fishing.is_cave_public_identity_available(identity_id):
        return ""
    if float(identity.get("next_fishing_time", 0) or 0) >= returned_at + VOYAGE_HANDOFF_SEC:
        return ""
    if identity.get("fishing_daily_day") == fishing.get_day_key(now):
        if int(identity.get("fishing_daily_count", 0) or 0) >= max(1, int(identity.get("fishing_daily_limit", 5) or 5)):
            return ""
        last_result = str(identity.get("fishing_last_result") or "")
        if "今日跳过" in last_result or "daily_limit" in last_result.lower():
            return ""
    if pending(identity) or identity.get("fishing_operation") or identity.get("fishing_result_pending"):
        return ""
    return "归航后预留钓鱼窗口，最迟 15 分钟后恢复远航"


async def run_selected_identity(operation, session, *, token, can_continue, update_schedule=True, recovery_only=False,
                                allow_public_auto=False):
    """The public caller already owns both locks and verified this player."""
    identity_id = operation.owner.identity_id
    raw = ((session.get("result") or {}).get("data") or {}).get("raw") or {}
    if not integrated(raw):
        return {"ok": False, "message": "未确认洞府原生钓鱼目录，未发送动作", "extra": {"status": "native_directory_missing"}}
    public_authorized = allow_public_auto and fishing.is_cave_public_auto_enabled("fishing", identity_id)

    def enabled():
        return (fishing.is_cave_public_auto_enabled("fishing", identity_id) if public_authorized
                else bool(operation.owner.identity.get("fishing_enabled")))

    if not enabled():
        return {"ok": False, "message": "钓鱼开关已关闭，未启动原生钓鱼", "extra": {"status": "disabled"}}
    record = operation.owner.identity.get(STATE_KEY) or {}
    supply_record = operation.owner.identity.get(supply.STATE_KEY) or {}
    if isinstance(supply_record, dict) and supply_record and supply_record.get("phase") != "accounted":
        record = supply_record
    site_id = record.get("site_id") if pending(operation.owner.identity) and isinstance(record, dict) else SITE_CHOICES.get(operation.pond_choice)
    model = raw.get("characterModel")
    model_id = model.get("selectedId") if isinstance(model, dict) else None
    if not site_id or not isinstance(model_id, str) or not model_id:
        return {"ok": False, "message": "原生钓鱼缺少已选钓位或人物模型，未发送动作", "extra": {"status": "native_placement_missing"}}
    if pending(operation.owner.identity) and record.get("model_id") != model_id:
        return {"ok": False, "message": "当前人物模型与原鱼竿不一致，保留记录待核对", "extra": {"status": "native_placement_changed"}}
    cancelled = None
    plan_keys = ("fishing_bait", "fishing_pond", "fishing_auto_buy_bait_enabled", "fishing_auto_buy_bait_count",
                 "fishing_auto_chum_enabled", "fishing_chum_name", "fishing_chum_names")
    plan = {key: deepcopy(operation.owner.identity.get(key)) for key in plan_keys}
    config = fishing.fishing_behavior.current_fishing_config(operation.owner.identity)
    try:
        result = await run_native_fishing_production_flow(
            identity_id, player_id=session["player_id"], token=token, init_data=session["init_data"],
            site_id=site_id, model_id=model_id, bait_id="", bait_choice=operation.bait_choice,
            update_schedule=update_schedule,
            recovery_only=recovery_only,
            capture_sink=fishing._fishing_miniapp_capture_store(time.time()),
            capture_source=f"cave_public_native_fishing:{identity_id}",
            supply_settings={"bait_choice": operation.bait_choice, "auto_buy": config.auto_buy_bait_enabled,
                             "buy_count": config.auto_buy_bait_count,
                             "chum_names": config.chum_names if config.auto_chum_enabled else ()},
            operation_check=lambda: can_continue() and enabled()
            and all(operation.owner.identity.get(key) == value for key, value in plan.items()),
        )
    except MiniAppFlowCancelled as exc:
        cancelled = exc
        result = exc.result or {"ok": False, "status": "cancelled"}
    except Exception as exc:
        result = {"ok": False, "status": "blocked", "error": type(exc).__name__}
    committed = result.get("committed") is True
    supplied = result.get("supply_committed") is True
    if not operation.owner.is_current():
        response = {"ok": False, "message": "钓鱼身份已变更，未回写结果", "extra": {"status": "cancelled"}}
    else:
        data = result.get("data") or {}
        catches = data.get("catches") if isinstance(data.get("catches"), dict) else {}
        rewards = data.get("rewards") if isinstance(data.get("rewards"), dict) else {}
        reason = str(result.get("error") or result.get("status") or "unknown")
        terminal_skip = (reason in {"fishing_rod_missing", "fishing_daily_limit_reached", "fishing_companion_missing"}
                         and not result.get("outcome_unknown") and not pending(operation.owner.identity)
                         and can_continue() and enabled() and cancelled is None)
        expected_wait = (
            reason == "fishing_companion_sailing" and result.get("ok") is False
            and result.get("status") == "blocked" and not committed and not supplied
            and not result.get("outcome_unknown") and not pending(operation.owner.identity)
            and can_continue() and enabled() and cancelled is None
        )
        message = ("洞府原生钓鱼：" + (fishing._format_count_map(catches) if catches else "空竿")) if committed else "洞府原生钓鱼未完成：" + reason
        if committed and rewards:
            message += "；额外 " + fishing._format_count_map(rewards)
        if supplied:
            message = "洞府钓鱼补给已确认：" + str(data.get("supply_action") or "补给") + "，未抛竿"
        if expected_wait:
            message = "洞府原生钓鱼：侍妾远航中，等待归航后钓鱼"
        if terminal_skip:
            message = {
                "fishing_rod_missing": "洞府原生钓鱼：未持有鱼竿，今日跳过",
                "fishing_companion_missing": "洞府原生钓鱼：无可用侍妾，今日跳过",
                "fishing_daily_limit_reached": "洞府原生钓鱼：今日次数已用尽，等待次日（fishing_daily_limit_reached）",
            }[reason]
        if (not committed or not update_schedule) and can_continue() and enabled() and cancelled is None:
            before = {key: operation.owner.identity.get(key) for key in (
                "next_fishing_time", "fishing_last_result", "fishing_last_error", "fishing_daily_day", "fishing_daily_count")}
            with use_identity(identity_id):
                now = time.time()
                if terminal_skip:
                    _, _, _, daily_updates = fishing.fishing_behavior.normalize_daily_counter(operation.owner.identity, now)
                    operation.owner.identity.update(daily_updates)
                delay = max(30 if supplied else 60, float(result.get("retry_after_sec") or 0))
                if result.get("status") == "supply_pending":
                    delay = max(delay, 1800)
                if reason in {"fishing_bait_missing", "fishing_rod_missing"}:
                    delay = max(delay, 1800)
                if reason == "fishing_companion_sailing":
                    # A 30-minute retry can miss the entire 15-minute return handoff.
                    delay = max(delay, SAILING_RECHECK_SEC)
                    return_at = operation.owner.identity.get("concubine_voyage_return_at")
                    if (operation.owner.identity.get("concubine_voyage_status") == "sailing"
                            and type(return_at) in (int, float) and math.isfinite(return_at)
                            and return_at > now):
                        delay = max(delay, return_at - now + 60)
                operation.owner.identity.update(
                    fishing_last_result=message,
                    fishing_last_error="" if committed or supplied or terminal_skip or expected_wait else reason,
                )
                if update_schedule:
                    operation.owner.identity["next_fishing_time"] = (
                        fishing.fishing_behavior.next_fishing_reset_timestamp(now, fishing._fishing_reset_jitter_sec(identity_id))
                        if terminal_skip else now + delay)
                try:
                    saved = persistence.save_state() is True
                except Exception:
                    saved = False
                if not saved:
                    operation.owner.identity.update(before)
                    persistence.mark_dirty()
                    message = "原生钓鱼状态保存失败，待核对"
                    terminal_skip = False
                    expected_wait = False
        response = {"ok": committed or supplied or terminal_skip, "message": message, "extra": {
            "status": "settled" if committed else "skipped" if terminal_skip else result.get("status", "blocked"), "native": True,
            "outcome_unknown": bool(result.get("outcome_unknown")), "committed": committed,
            "supply_committed": supplied,
            "expected_wait": expected_wait,
            "checkpoint_observations": result.get("checkpoint_observations", []),
            "terminal_skip": terminal_skip and update_schedule,
        }}
    if cancelled is not None:
        raise MiniAppFlowCancelled(response) from None
    return response
