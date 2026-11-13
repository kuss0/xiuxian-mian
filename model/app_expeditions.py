"""Configuration, lifecycle, and admin commands for optional upstream activities."""
import asyncio
import copy
import time
import traceback

from .app_hunt import handle_hunt_game_event, handle_hunt_log_command, hunt_target, reset_hunt_runtime_on_startup, run_hunt_scheduler
from .app_xuangu import handle_xuangu_game_event, reset_xuangu_runtime_on_startup, run_xuangu_scheduler
from .features import changfeng_trial, hunt
from .persistence import save_state
from .runtime import send_audit_log, track_background_task
from .state import (
    get_expedition_config, get_expedition_run_state, get_hunt_run_state, get_hunt_teams,
    get_identity_ids, get_identity_ui_display_name, resolve_identity_selector,
    set_expedition_config, set_hunt_teams,
)

_TICK_TASK = None
_MANUAL_TASKS = set()
_EVENT_TASKS = set()


async def _isolated(handler, *args, **kwargs):
    try:
        return await handler(*args, **kwargs)
    except Exception:
        print(traceback.format_exc())
        return False


async def _manual_trial(identity_id, arena, *, status_only=False):
    ok, message = await changfeng_trial.run_changfeng_once(identity_id, arena, manual=True, status_only=status_only)
    if not ok:
        await send_audit_log(f"长风试炼未完成：{message}", scope="identity", send_as_id=identity_id, limit=400)


def get_expedition_snapshot():
    config = get_expedition_config()
    xuangu = config.get("xuangu", {})
    changfeng = config.get("changfeng", {})
    return {
        "hunt_teams": get_hunt_teams(),
        "hunt_runs": [{key: copy.deepcopy(run.get(key)) for key in ("name", "phase", "day", "last_error", "result", "started_at", "requires_reconciliation")}
                      for run in get_hunt_run_state().values() if isinstance(run, dict)],
        "hunt_weekly": copy.deepcopy(get_expedition_run_state().get("hunt_weekly", {})),
        "xuangu": {"enabled": bool(xuangu.get("enabled")), "participant_identity_ids": xuangu.get("participant_identity_ids", []),
                   "finish_choice": xuangu.get("finish_choice", "收拢遗宝")},
        "xuangu_runs": [{"name": f"玄骨 @{run.get('leader_username') or ''}",
                         **{key: copy.deepcopy(run.get(key)) for key in ("phase", "room_id", "outcome", "last_error", "roster_id", "requires_reconciliation")}}
                        for run in get_expedition_run_state().get("xuangu_auto", {}).get("rosters", {}).values() if isinstance(run, dict)],
        "changfeng": {"enabled": bool(changfeng.get("enabled")), "participant_identity_ids": changfeng.get("participant_identity_ids", []),
                      "arenas": changfeng.get("arenas", ["longwind", "bamboo"]), "daily_limit": changfeng.get("daily_limit", 1)},
        "changfeng_runs": [{key: copy.deepcopy(run.get(key)) for key in ("identity_id", "phase", "arena", "daily_used", "result", "last_error", "run_id", "operation_id")}
                           for run in get_expedition_run_state().get("changfeng", {}).values() if isinstance(run, dict)],
    }


def _participants(raw):
    if not isinstance(raw, list):
        raise ValueError("参与身份必须是列表")
    if any(isinstance(value, bool) for value in raw):
        raise ValueError("参与身份无效")
    ids = list(dict.fromkeys(int(value) for value in raw))
    if any(identity_id not in get_identity_ids() for identity_id in ids):
        raise ValueError("包含未登记身份")
    return ids


def ui_set_expedition_config(payload):
    if not isinstance(payload, dict):
        return False, "活动配置格式错误"
    previous_config, previous_teams = get_expedition_config(), get_hunt_teams()
    config, teams = copy.deepcopy(previous_config), previous_teams
    try:
        if "hunt_teams" in payload:
            teams = hunt.normalize_teams(payload["hunt_teams"], set(get_identity_ids()), lambda identity: identity)
        if "xuangu" in payload:
            raw = payload["xuangu"]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("玄骨配置格式错误")
            choice = raw.get("finish_choice", "收拢遗宝")
            if choice not in {"立即撤离", "收拢遗宝", "争取全收"}:
                raise ValueError("玄骨收尾选项无效")
            if raw.get("enabled") and not hunt_target("secondary")[0]:
                raise ValueError("请先配置游戏副群")
            config["xuangu"] = {"enabled": raw.get("enabled", False), "finish_choice": choice,
                                "participant_identity_ids": _participants(raw.get("participant_identity_ids", []))}
        if "changfeng" in payload:
            raw = payload["changfeng"]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("长风配置格式错误")
            arenas = raw.get("arenas", ["longwind", "bamboo"])
            if not isinstance(arenas, list) or not arenas or any(arena not in changfeng_trial.TRIAL_ARENAS for arena in arenas):
                raise ValueError("请选择麦田或竹林试炼")
            limit = raw.get("daily_limit", 1)
            if isinstance(limit, bool) or not 1 <= int(limit) <= 8:
                raise ValueError("长风每日限额须为 1–8 场")
            config["changfeng"] = {"enabled": raw.get("enabled", False), "arenas": list(dict.fromkeys(arenas)),
                                   "daily_limit": int(limit), "participant_identity_ids": _participants(raw.get("participant_identity_ids", []))}
    except (TypeError, ValueError, OverflowError) as exc:
        return False, str(exc)
    set_hunt_teams(teams)
    set_expedition_config(config)
    if save_state() is False:
        set_hunt_teams(previous_teams)
        set_expedition_config(previous_config)
        return False, "配置未能保存，请稍后重试"
    return True, "已保存；猎妖勾选周常自动补满后会核对船况并补至每周5次，其余队伍手动出发"


async def handle_expedition_log_command(event):
    handled, message = await handle_hunt_log_command(event)
    if handled:
        return handled, message
    text = str(getattr(event, "raw_text", "") or "").strip()
    if not text.startswith(".长风试炼"):
        return False, ""
    parts = text.split()
    if len(parts) == 1 or parts[1] == "状态":
        records = get_expedition_run_state().get("changfeng", {})
        rows = [f"{get_identity_ui_display_name(int(identity))}：{record.get('phase', '空闲')}｜{record.get('result') or record.get('last_error') or '-'}｜记录号 {record.get('run_id') or record.get('operation_id') or '-'}"
                for identity, record in records.items()]
        return True, "长风试炼\n" + ("\n".join(rows) or "尚未执行")
    if parts[1] == "确认结束" and len(parts) == 4:
        identity_id = resolve_identity_selector(parts[2])
        if identity_id is None:
            return True, "身份不存在或不唯一"
        date = getattr(event, "date", None)
        if date is None or not 0 <= time.time() - date.timestamp() <= 60:
            return True, "确认指令已过期，请重新发送"
        ok, message = changfeng_trial.confirm_changfeng_finished(identity_id, parts[3])
        if ok:
            await send_audit_log(f"长风试炼人工确认结束：{parts[3]}", scope="identity", send_as_id=identity_id, limit=300)
        return True, message
    if parts[1] not in {"执行", "核对"} or len(parts) not in {3, 4}:
        return True, "用法：.长风试炼 状态 / .长风试炼 执行或核对 @身份 [麦田|竹林]"
    date = getattr(event, "date", None)
    if date is not None and time.time() - date.timestamp() > 60:
        return True, "执行指令已过期，请重新发送"
    identity_id = resolve_identity_selector(parts[2])
    if identity_id is None:
        return True, "身份不存在或不唯一"
    arena = {"麦田": "longwind", "竹林": "bamboo", "longwind": "longwind", "bamboo": "bamboo"}.get(parts[3] if len(parts) == 4 else "麦田")
    if arena is None:
        return True, "场地须为麦田或竹林"
    task = track_background_task(asyncio.create_task(_manual_trial(identity_id, arena, status_only=parts[1] == "核对")))
    _MANUAL_TASKS.add(task)
    task.add_done_callback(_manual_done)
    return True, "已安排本次长风试炼，结果会发送到日志群"


def _manual_done(task):
    _MANUAL_TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        print(f"Manual trial failed: {type(task.exception()).__name__}")


async def handle_expedition_game_event(event, *, event_type="message"):
    # Feature locks serialize early replies without holding the shared listener pipeline.
    for handler in (handle_hunt_game_event, handle_xuangu_game_event):
        if handler is handle_hunt_game_event and not get_hunt_run_state() and not any(team.get("auto_weekly") for team in get_hunt_teams()):
            continue
        if handler is handle_xuangu_game_event and not get_expedition_config().get("xuangu", {}).get("enabled"):
            continue
        task = track_background_task(asyncio.create_task(_isolated(handler, event, event_type=event_type)))
        _EVENT_TASKS.add(task)
        task.add_done_callback(_EVENT_TASKS.discard)


async def _run_tick(now):
    for handler in (run_hunt_scheduler, run_xuangu_scheduler, changfeng_trial.run_changfeng_scheduler):
        await _isolated(handler, now)


async def run_expedition_scheduler(now):
    global _TICK_TASK
    config = get_expedition_config()
    if not get_hunt_run_state() and not any(team.get("auto_weekly") for team in get_hunt_teams()) and not any((config.get(key) or {}).get("enabled") for key in ("xuangu", "changfeng")):
        return
    if _TICK_TASK is not None and not _TICK_TASK.done():
        return
    if _TICK_TASK is not None and not _TICK_TASK.cancelled() and _TICK_TASK.exception() is not None:
        print(f"Expedition scheduler failed: {type(_TICK_TASK.exception()).__name__}")
    _TICK_TASK = track_background_task(asyncio.create_task(_run_tick(now)))


def reset_expedition_runtime_on_startup():
    reset_hunt_runtime_on_startup()
    reset_xuangu_runtime_on_startup()
    changfeng_trial.reset_changfeng_runtime_on_startup()


async def shutdown_expeditions():
    from . import app_xuangu
    tasks = [*_MANUAL_TASKS, *_EVENT_TASKS, *changfeng_trial._TASKS.values(), *app_xuangu._TASKS]
    if _TICK_TASK is not None:
        tasks.append(_TICK_TASK)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
