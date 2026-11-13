"""Configuration, lifecycle, and admin commands for optional upstream activities."""
import asyncio
import copy
import re
import time
import traceback

from .app_hunt import handle_hunt_game_event, handle_hunt_log_command, hunt_target, reset_hunt_runtime_on_startup, run_hunt_scheduler
from .app_xuangu import handle_xuangu_game_event, reset_xuangu_runtime_on_startup, run_xuangu_scheduler
from .app_weekly_adventures import handle_weekly_game_event, reset_weekly_on_startup, run_weekly_scheduler
from .app_zhengmo import handle_zhengmo_game_event, reset_zhengmo_runtime_on_startup, run_zhengmo_scheduler
from . import app_spirit_beast
from . import avatar_soul
from .features import changfeng_trial, hunt, paired_activities, zhengmo
from .features.weekly_adventures import KINDS
from .persistence import mark_dirty, save_state
from .runtime import send_audit_log, track_background_task
from .state import (
    get_expedition_config, get_expedition_run_state, get_hunt_run_state, get_hunt_teams,
    get_avatar_owner_id, get_identity_account, get_identity_ids, get_identity_ui_display_name, get_identity_state, get_send_as_profile, is_avatar_identity, resolve_identity_selector, resolve_identity_selector_detail,
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


async def ui_additional_activity_action(payload):
    try:
        action = payload.get("action")
        if action == "profile":
            return await avatar_soul.refresh_avatar_profile(int(payload.get("identity_id")))
        if action == "restart_global_duel":
            config = get_expedition_config().get("global_duel", {})
            if not config.get("enabled"):
                return False, "请先保存并开启全局对决配置"
            runs = get_expedition_run_state().setdefault("global_duel", {})
            if paired_activities._LOCK.locked() or any(r.get("phase") in paired_activities.ACTIVE for r in runs.values()):
                return False, "原对决尚未结束或结果待核对"
            before = copy.deepcopy(runs)
            runs[config["name"]] = {"phase": "idle", "completed": 0}
            try:
                paired_activities.save()
            except Exception:
                runs.clear()
                runs.update(before)
                return False, "状态保存失败，保留原对决记录"
            return True, "已按保存的账号和轮数安排新一轮对决"
        if action in {"scan", "register", "return"}:
            owner = int(payload.get("owner_id"))
            if action == "scan":
                ok, message, _entries = await avatar_soul.scan_account_avatars(owner)
                return ok, message
            if action == "register":
                player = int(payload.get("player_id"))
                ok, message = avatar_soul.register_avatar_identity(owner, player)
                if ok:
                    identity = next(int(i) for i, e in avatar_soul.records()["entries"].items()
                                    if e["owner_id"] == owner and e["player_id"] == player)
                    _refreshed, profile_message = await avatar_soul.refresh_avatar_profile(identity)
                    message += "；" + profile_message
                return ok, message
            ok = await avatar_soul.return_to_main(owner)
            return ok, "已收回主魂" if ok else "收回尚未确认，请核对原活动或切换回执"
        if action == "confirm_finished":
            kind = payload.get("kind")
            if kind not in {"spirit_beast", "warm_pairs", "global_duel"}:
                return False, "活动无效"
            run = get_expedition_run_state().get(kind, {}).get(str(payload.get("key")), {})
            if run.get("phase") != "held" or str(run.get("id") or run.get("started_at")) != str(payload.get("run_id")):
                return False, "须核对准确的待确认记录号"
            before = copy.deepcopy(run)
            run.update(phase="done" if kind == "global_duel" else "idle", operator_confirmed_at=time.time(),
                       next_at=time.time() + (86400 if kind == "spirit_beast" else 3720), result="已人工核对结束，本轮不重放")
            run.pop("pending", None)
            mark_dirty()
            try:
                if save_state() is False:
                    raise RuntimeError("状态保存失败")
            except Exception:
                run.clear()
                run.update(before)
                return False, "状态保存失败"
            return True, "已记录核对结果，本轮不重放"
    except (TypeError, ValueError):
        return False, "活动参数格式错误"
    return False, "未知活动动作"


def get_expedition_snapshot():
    config = get_expedition_config()
    xuangu = config.get("xuangu", {})
    changfeng = config.get("changfeng", {})
    return {
        "hunt_teams": get_hunt_teams(),
        "hunt_runs": [{key: copy.deepcopy(run.get(key)) for key in ("name", "phase", "day", "last_error", "result", "started_at", "requires_reconciliation")}
                      for run in get_hunt_run_state().values() if isinstance(run, dict)],
        "hunt_weekly": copy.deepcopy(get_expedition_run_state().get("hunt_weekly", {})),
        "weekly": copy.deepcopy(config.get("weekly", {})),
        "weekly_state": copy.deepcopy(get_expedition_run_state().get("weekly", {})),
        **{key: copy.deepcopy(config.get(key, {})) for key in ("spirit_beast", "warm_pairs", "global_duel")},
        "additional_runs": {key: copy.deepcopy(get_expedition_run_state().get(key, {})) for key in ("spirit_beast", "warm_pairs", "global_duel")},
        "avatars": copy.deepcopy(get_expedition_run_state().get("avatars", {})),
        "zhengmo": copy.deepcopy(config.get("zhengmo", {})),
        "zhengmo_runs": copy.deepcopy(get_expedition_run_state().get("zhengmo", {})),
        "xuangu": {"enabled": bool(xuangu.get("enabled")), "participant_identity_ids": xuangu.get("participant_identity_ids", []),
                   "finish_choice": xuangu.get("finish_choice", "收拢遗宝"), "auto_daily": bool(xuangu.get("auto_daily")),
                   "weekly_limit": xuangu.get("weekly_limit", 3)},
        "xuangu_daily": copy.deepcopy(get_expedition_run_state().get("xuangu_daily", {})),
        "xuangu_runs": [{"name": f"玄骨 @{run.get('leader_username') or ''}",
                         **{key: copy.deepcopy(run.get(key)) for key in ("phase", "room_id", "outcome", "last_error", "roster_id", "requires_reconciliation")}}
                        for run in get_expedition_run_state().get("xuangu_auto", {}).get("rosters", {}).values() if isinstance(run, dict)],
        "changfeng": {"enabled": bool(changfeng.get("enabled")), "participant_identity_ids": changfeng.get("participant_identity_ids", []),
                      "arenas": changfeng.get("arenas", ["longwind", "bamboo"]), "daily_limit": changfeng.get("daily_limit", 1),
                      "report_profile": changfeng.get("report_profile", "rough")},
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


def _normalize_weekly(raw, previous):
    if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
        raise ValueError("组队周常配置格式错误")
    kinds = raw.get("kinds", list(KINDS))
    if not isinstance(kinds, list) or any(kind not in KINDS for kind in kinds):
        raise ValueError("组队周常玩法无效")
    role = raw.get("group_role", previous.get("group_role", "secondary"))
    if role not in {"primary", "secondary"}:
        raise ValueError("周常游戏群无效")
    if raw.get("enabled") and not hunt_target(role)[0]:
        raise ValueError("请先配置周常使用的游戏群")
    participants = _participants(raw.get("participant_identity_ids", []))
    roots = [get_avatar_owner_id(i) or i for i in participants]
    if len(roots) != len(set(roots)):
        raise ValueError("同一主魂及其分身不能同时加入一支队伍")
    if any(is_avatar_identity(i) for i in participants):
        raise ValueError("分身请使用身份设置中的群玩法，组队周常暂不支持分身切换")
    aliases = raw.get("member_names", previous.get("member_names", {}))
    if not isinstance(aliases, dict):
        raise ValueError("成员道号格式错误")
    aliases = {str(i): str(aliases.get(str(i)) or "").strip().lstrip("@") for i in participants}
    if any(value and (len(value) > 64 or re.search(r"[\s，。、：:；;（）()【】!?！？@]", value)) for value in aliases.values()):
        raise ValueError("请填写游戏 @ 后的完整道号，不含空格或标点")
    aliases = {key: value for key, value in aliases.items() if value}
    names = [aliases.get(str(i)) or str(get_send_as_profile(i).get("username") or "").lstrip("@") for i in participants]
    names = [name.casefold() for name in names if name]
    if len(names) != len(set(names)):
        raise ValueError("成员道号重复，请为每位成员填写不同的游戏道号")
    route = raw.get("route", ["城南道观", "散修集市", "秦府夜宴"])
    if not isinstance(route, list) or len(route) != 3 or any(p not in {"城南道观", "散修集市", "秦府夜宴"} for p in route):
        raise ValueError("越京须配置三轮查访地点")
    if type(raw.get("use_talisman", True)) is not bool:
        raise ValueError("真凰符选项须为开关")
    return {"enabled": raw.get("enabled", False), "kinds": list(dict.fromkeys(kinds)),
            "group_role": role, "member_names": aliases, "route": route,
            "use_talisman": raw.get("use_talisman", True), "participant_identity_ids": participants}


def ui_set_expedition_config(payload):
    if not isinstance(payload, dict):
        return False, "活动配置格式错误"
    previous_config, previous_teams = get_expedition_config(), get_hunt_teams()
    config, teams = copy.deepcopy(previous_config), previous_teams
    try:
        if "spirit_beast" in payload:
            raw = payload["spirit_beast"]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("驭灵行迹配置无效")
            config["spirit_beast"] = {"enabled": raw.get("enabled", False), "participant_identity_ids": _participants(raw.get("participant_identity_ids", []))}
        for key in ("warm_pairs", "global_duel"):
            if key not in payload:
                continue
            raw = payload[key]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("配对活动配置无效")
            if key == "global_duel" and not raw.get("enabled") and not raw.get("source_identity_id") and not raw.get("target_identity_id"):
                config[key] = {"enabled": False}
                continue
            rows = raw.get("pairs", []) if key == "warm_pairs" else [raw]
            if not isinstance(rows, list) or len(rows) > 20:
                raise ValueError("温养配对最多20组")
            normalized, occupied = [], set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("配对格式无效")
                ids = _participants([row.get("source_identity_id"), row.get("target_identity_id")])
                if len(ids) != 2 or set(ids) & occupied:
                    raise ValueError("配对身份不能重复，每个身份只能参加一组温养配对")
                occupied.update(ids)
                role = row.get("group_role", "primary")
                if role not in {"primary", "secondary"} or raw.get("enabled") and not hunt_target(role)[0]:
                    raise ValueError("请配置活动使用的游戏群")
                name = str(row.get("name") or "default").strip()
                if not re.fullmatch(r"[\w\-]{1,32}", name) or any(r["name"] == name for r in normalized):
                    raise ValueError("配对名称须唯一，只能含中文、字母、数字、下划线或短横线")
                if key == "global_duel" and any(get_identity_account(i) != i for i in ids):
                    raise ValueError("全局对决仅支持两个已登录的本人账号")
                if key == "warm_pairs" and any(is_avatar_identity(i) for i in ids):
                    raise ValueError("独立温养配对不支持分身，请选择本人或频道身份")
                if key == "warm_pairs" and raw.get("enabled") and get_identity_state(ids[1]).get("hehuan_enabled"):
                    raise ValueError("B号已有合欢自动温养，请先关闭该入口以避免重复")
                normalized.append({"name": name, "source_identity_id": ids[0], "target_identity_id": ids[1], "group_role": role})
            if key == "warm_pairs":
                lead = str(raw.get("lead_text", "修修")).strip()
                if not 1 <= len(lead) <= 120 or lead.startswith((".", "/", "。")) or "\n" in lead:
                    raise ValueError("温养引子须为1–120字的单行普通文字")
                config[key] = {"enabled": raw.get("enabled", False), "pairs": normalized, "lead_text": lead}
            else:
                count = raw.get("count", 1)
                if type(count) is not int or not 1 <= count <= 100:
                    raise ValueError("全局对决次数须为1–100")
                config[key] = {**normalized[0], "enabled": raw.get("enabled", False), "count": count}
        if "hunt_teams" in payload:
            teams = hunt.normalize_teams(payload["hunt_teams"], set(get_identity_ids()), lambda i: get_avatar_owner_id(i) or i)
            if any(is_avatar_identity(i) for team in teams for i in team["member_identity_ids"]):
                raise ValueError("猎妖船暂不支持分身切换，请选择本人或频道身份")
        if "xuangu" in payload:
            raw = payload["xuangu"]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("玄骨配置格式错误")
            choice = raw.get("finish_choice", "收拢遗宝")
            if choice not in {"立即撤离", "收拢遗宝", "争取全收"}:
                raise ValueError("玄骨收尾选项无效")
            if raw.get("enabled") and not hunt_target("secondary")[0]:
                raise ValueError("请先配置游戏副群")
            automatic = raw.get("auto_daily", config.get("xuangu", {}).get("auto_daily", False))
            if type(automatic) is not bool:
                raise ValueError("玄骨有票自动须为开关")
            weekly_limit = raw.get("weekly_limit", config.get("xuangu", {}).get("weekly_limit", 3))
            if type(weekly_limit) is not int or not 1 <= weekly_limit <= 7:
                raise ValueError("玄骨每周限额须为1–7次")
            participants = _participants(raw.get("participant_identity_ids", []))
            roots = [get_avatar_owner_id(i) or i for i in participants]
            if len(roots) != len(set(roots)):
                raise ValueError("玄骨同队不能同时选择主魂及其分身")
            if any(is_avatar_identity(i) for i in participants):
                raise ValueError("玄骨暂不支持分身切换，请选择本人或频道身份")
            config["xuangu"] = {"enabled": raw.get("enabled", False), "finish_choice": choice,
                                "auto_daily": automatic, "weekly_limit": weekly_limit,
                                "participant_identity_ids": participants}
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
            profile = raw.get("report_profile", config.get("changfeng", {}).get("report_profile", "rough"))
            if profile not in changfeng_trial.REPORT_PROFILES:
                raise ValueError("请选择长风甲上、甲或乙档位")
            config["changfeng"] = {"enabled": raw.get("enabled", False), "arenas": list(dict.fromkeys(arenas)), "report_profile": profile,
                                   "daily_limit": int(limit), "participant_identity_ids": _participants(raw.get("participant_identity_ids", []))}
        if "weekly" in payload:
            raw = payload["weekly"]
            if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
                raise ValueError("组队周常配置格式错误")
            previous = config.get("weekly", {})
            normalized = _normalize_weekly(raw, previous)
            raw_teams = raw.get("teams", previous.get("teams", []))
            if not isinstance(raw_teams, list) or len(raw_teams) > 20:
                raise ValueError("周常最多配置20支队伍")
            named = []
            for team in raw_teams:
                if not isinstance(team, dict):
                    raise ValueError("队伍格式错误")
                name = str(team.get("name") or "").strip()
                if not re.fullmatch(r"[\w\-]{1,32}", name) or any(t["name"] == name for t in named):
                    raise ValueError("队名须唯一，由1–32个中文、字母、数字、下划线或短横线组成")
                named.append({**_normalize_weekly(team, {}), "name": name})
            normalized["teams"] = named
            aliases_by_group = {}
            for team in named or [normalized]:
                names = aliases_by_group.setdefault(team["group_role"], {})
                for identity in team["participant_identity_ids"]:
                    alias = (team["member_names"].get(str(identity)) or str(get_send_as_profile(identity).get("username") or "").lstrip("@")).casefold()
                    if alias and alias in names and names[alias] != identity:
                        raise ValueError("同群不同队的游戏道号不能指向不同身份")
                    if alias:
                        names[alias] = identity
            config["weekly"] = normalized
        if "zhengmo" in payload:
            config["zhengmo"] = zhengmo.normalize_config(payload["zhengmo"], set(get_identity_ids()), lambda i: i)
            if config["zhengmo"]["enabled"] and not hunt_target(config["zhengmo"]["group_role"])[0]:
                raise ValueError("请先配置正魔大战使用的游戏群")
        for kind in ("warm_pairs", "global_duel"):
            previous = previous_config.get(kind, {})
            current = config.get(kind, {})
            if (current != previous and current != {**previous, "enabled": False}
                    and any(r.get("phase") in paired_activities.ACTIVE for r in paired_activities.records(kind).values())):
                raise ValueError("原配对活动尚未结束或待核对，须先结束再修改账号、群或轮数")
    except (TypeError, ValueError, OverflowError) as exc:
        return False, str(exc)
    set_hunt_teams(teams)
    set_expedition_config(config)
    if save_state() is False:
        set_hunt_teams(previous_teams)
        set_expedition_config(previous_config)
        return False, "配置未能保存，请稍后重试"
    return True, "已保存；开启的玩法会按参与名单和日周限额自动执行"


async def handle_expedition_log_command(event):
    text = str(getattr(event, "raw_text", "") or "").strip()
    if text.startswith(".分身 "):
        parts = text.split()
        if len(parts) == 3 and parts[1] in {"扫描", "收回"}:
            owner, error = resolve_identity_selector_detail(parts[2])
            if error:
                return True, error
            if parts[1] == "扫描":
                ok, message, _entries = await avatar_soul.scan_account_avatars(owner)
                return True, message
            ok = await avatar_soul.return_to_main(owner)
            return True, "已收回主魂" if ok else "收回未确认或原活动尚未结束，保留暂停"
        if len(parts) == 4 and parts[1] == "登记":
            owner, error = resolve_identity_selector_detail(parts[2])
            if error:
                return True, error
            try:
                _ok, message = avatar_soul.register_avatar_identity(owner, int(parts[3]))
                return True, message
            except ValueError:
                return True, "分身ID格式无效"
        return True, ".分身 扫描 @本人｜.分身 登记 @本人 洞府分身ID｜.分身 收回 @本人"
    if str(getattr(event, "raw_text", "") or "").strip().startswith(".正魔 "):
        from .app_zhengmo import _context as war_context
        return await zhengmo.control(war_context(), event)
    handled, message = await handle_hunt_log_command(event)
    if handled:
        return handled, message
    text = str(getattr(event, "raw_text", "") or "").strip()
    if text.startswith(".周常"):
        from .app_weekly_adventures import _context
        from .features.weekly_adventures import confirm_finished
        parts = text.split()
        if len(parts) == 4 and parts[1] == "确认结束":
            date = getattr(event, "date", None)
            if date is None or not 0 <= time.time() - date.timestamp() <= 60:
                return True, "确认指令已过期，请重新发送"
            ok, message = await confirm_finished(_context(), parts[2], parts[3])
            if ok:
                # A roster conflict can finish with fewer than the intended
                # members. Retain their real rewards before arranging a new run.
                from .app_weekly_adventures import _backfill_weekly_receipts, _save
                _backfill_weekly_receipts()
                _save()
            return True, message
        return True, "待核对玩法须先核对游戏结果，再发送 .周常 确认结束 tianlan/yuejing/yinming/qipai 记录号"
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
    for handler in (handle_hunt_game_event, handle_xuangu_game_event, handle_weekly_game_event, handle_zhengmo_game_event, paired_activities.game_event, avatar_soul.game_event):
        if handler is handle_hunt_game_event and not get_hunt_run_state() and not any(team.get("auto_weekly") for team in get_hunt_teams()):
            continue
        if handler is handle_xuangu_game_event and not get_expedition_config().get("xuangu", {}).get("enabled"):
            continue
        task = track_background_task(asyncio.create_task(_isolated(handler, event, event_type=event_type)))
        _EVENT_TASKS.add(task)
        task.add_done_callback(_EVENT_TASKS.discard)


async def _run_tick(now):
    for handler in (run_hunt_scheduler, run_xuangu_scheduler, run_weekly_scheduler, run_zhengmo_scheduler, changfeng_trial.run_changfeng_scheduler, app_spirit_beast.scheduler, paired_activities.scheduler, avatar_soul.supervisor):
        await _isolated(handler, now)


async def run_expedition_scheduler(now):
    global _TICK_TASK
    config = get_expedition_config()
    if not get_hunt_run_state() and not get_expedition_run_state().get("weekly") and not get_expedition_run_state().get("avatars") and not any(team.get("auto_weekly") for team in get_hunt_teams()) and not any((config.get(key) or {}).get("enabled") for key in ("xuangu", "changfeng", "weekly", "zhengmo", "spirit_beast", "warm_pairs", "global_duel")):
        return
    if _TICK_TASK is not None and not _TICK_TASK.done():
        return
    if _TICK_TASK is not None and not _TICK_TASK.cancelled() and _TICK_TASK.exception() is not None:
        print(f"Expedition scheduler failed: {type(_TICK_TASK.exception()).__name__}")
    _TICK_TASK = track_background_task(asyncio.create_task(_run_tick(now)))


def reset_expedition_runtime_on_startup():
    avatar_soul.reset_on_startup()
    app_spirit_beast.reset_on_startup()
    paired_activities.reset_on_startup()
    reset_zhengmo_runtime_on_startup()
    reset_hunt_runtime_on_startup()
    reset_xuangu_runtime_on_startup()
    changfeng_trial.reset_changfeng_runtime_on_startup()
    reset_weekly_on_startup()


async def shutdown_expeditions():
    from . import app_xuangu
    tasks = [*_MANUAL_TASKS, *_EVENT_TASKS, *changfeng_trial._TASKS.values(), *app_xuangu._TASKS, *app_spirit_beast._TASKS.values(), *avatar_soul._TASKS.values()]
    if _TICK_TASK is not None:
        tasks.append(_TICK_TASK)
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
