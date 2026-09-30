"""Canary entry for native fishing; retains the existing UI plan and locks."""

import time

from ..state import use_identity
from .. import persistence
from . import fishing_runtime as fishing
from .fishing_dwelling_miniapp import run_native_fishing_production_flow
from .fishing_dwelling_store import STATE_KEY
from .fishing_dwelling_journal import validate
from .miniapp_common import MiniAppFlowCancelled


SITE_CHOICES = {"青溪浅滩": "west-shore", "灵眼寒潭": "waterfall-pool", "乱星海礁": "east-shore"}


def pending(identity):
    record = identity.get(STATE_KEY, {})
    if record == {}:
        return False
    try:
        validate(record)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return True
    return record["phase"] != "accounted"


def integrated(raw):
    if not isinstance(raw, dict):
        return False
    account = raw.get("account")
    center = account.get("commandCenter") if isinstance(account, dict) else None
    entries = center.get("entries") if isinstance(center, dict) else None
    return isinstance(entries, list) and any(isinstance(row, dict) and row.get("key") == "fishing"
                                            and row.get("status") == "integrated" for row in entries)


async def run_selected_identity(operation, session, *, token, can_continue):
    """The public caller already owns both locks and verified this player."""
    identity_id = operation.owner.identity_id
    raw = ((session.get("result") or {}).get("data") or {}).get("raw") or {}
    if not integrated(raw):
        return {"ok": False, "message": "未确认洞府原生钓鱼目录，未发送动作", "extra": {"status": "native_directory_missing"}}
    if not operation.owner.identity.get("fishing_enabled"):
        return {"ok": False, "message": "钓鱼开关已关闭，未启动原生钓鱼", "extra": {"status": "disabled"}}
    record = operation.owner.identity.get(STATE_KEY) or {}
    site_id = record.get("site_id") if pending(operation.owner.identity) and isinstance(record, dict) else SITE_CHOICES.get(operation.pond_choice)
    model = raw.get("characterModel")
    model_id = model.get("selectedId") if isinstance(model, dict) else None
    if not site_id or not isinstance(model_id, str) or not model_id:
        return {"ok": False, "message": "原生钓鱼缺少已选钓位或人物模型，未发送动作", "extra": {"status": "native_placement_missing"}}
    if pending(operation.owner.identity) and record.get("model_id") != model_id:
        return {"ok": False, "message": "当前人物模型与原鱼竿不一致，保留记录待核对", "extra": {"status": "native_placement_changed"}}
    cancelled = None
    try:
        result = await run_native_fishing_production_flow(
            identity_id, player_id=session["player_id"], token=token, init_data=session["init_data"],
            site_id=site_id, model_id=model_id, bait_id="", bait_choice=operation.bait_choice,
            operation_check=lambda: can_continue() and bool(operation.owner.identity.get("fishing_enabled")),
        )
    except MiniAppFlowCancelled as exc:
        cancelled = exc
        result = exc.result or {"ok": False, "status": "cancelled"}
    except Exception as exc:
        result = {"ok": False, "status": "blocked", "error": type(exc).__name__}
    committed = result.get("committed") is True
    if not operation.owner.is_current():
        response = {"ok": False, "message": "钓鱼身份已变更，未回写结果", "extra": {"status": "cancelled"}}
    else:
        data = result.get("data") or {}
        catches = data.get("catches") if isinstance(data.get("catches"), dict) else {}
        reason = str(result.get("error") or result.get("status") or "unknown")
        message = ("洞府原生钓鱼：" + (fishing._format_count_map(catches) if catches else "空竿")) if committed else "洞府原生钓鱼未完成：" + reason
        if not committed and can_continue() and cancelled is None:
            before = {key: operation.owner.identity.get(key) for key in ("next_fishing_time", "fishing_last_result", "fishing_last_error")}
            with use_identity(identity_id):
                now = time.time()
                delay = max(60, float(result.get("retry_after_sec") or 0))
                if reason in {"fishing_bait_missing", "fishing_rod_missing", "fishing_companion_sailing"}:
                    delay = max(delay, 1800)
                operation.owner.identity.update(next_fishing_time=now + delay, fishing_last_result=message, fishing_last_error=reason)
                if persistence.save_state() is not True:
                    operation.owner.identity.update(before)
                    persistence.mark_dirty()
                    message = "原生钓鱼状态保存失败，待核对"
        response = {"ok": committed, "message": message, "extra": {
            "status": "settled" if committed else result.get("status", "blocked"), "native": True,
            "outcome_unknown": bool(result.get("outcome_unknown")), "committed": committed,
        }}
    if cancelled is not None:
        raise MiniAppFlowCancelled(response) from None
    return response
