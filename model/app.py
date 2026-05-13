import asyncio
import json
import signal
import time
import traceback
from datetime import datetime

from telethon import events

from .config import BOT_SILENCE_TIMEOUT_SEC, CMD_IDENTITY_INFO, MESSAGES_DIR, TZ_LOCAL, client, create_account_client, get_all_clients, get_registered_client, is_account_offline, mark_account_offline, register_client
from .control import enforce_identity_module_availability, handle_identity_info_reply, handle_log_group_command, handle_realm_breakthrough_broadcast, hydrate_identity_profile, initialize_identity_runtime, run_identity_info_followup_scheduler, run_startup_account_integrity_check, scan_startup_timeout_tasks, spread_overdue_runtime_timers, toggle_global_enabled
from .features.checkin import handle_checkin_reply, handle_sect_teach_reply, run_checkin_scheduler
from .features._phaseful import has_phaseful_summary_block, observe_phaseful_identity_message
from .features.deep_retreat import (
    handle_deep_retreat_running_reply,
    handle_deep_retreat_status_reply,
    handle_deep_retreat_success_reply,
    handle_deep_retreat_summary_broadcast,
    run_deep_retreat_scheduler,
)
from .features.guanxing import (
    handle_guanxing_external_shift_command,
    handle_guanxing_finish_broadcast,
    handle_guanxing_query_reply,
    restore_guanxing_round_runtime,
    run_guanxing_scheduler,
)
from .features.guanxing_monitor import handle_guanxing_monitor_broadcast, restore_guanxing_monitor_runtime_state, run_guanxing_monitor_scheduler
from .features.concubine import (
    handle_concubine_dream_reply,
    handle_concubine_fragment_reply,
    handle_concubine_loss_broadcast,
    handle_concubine_puzzle_reply,
    handle_concubine_reacquire_reply,
    handle_concubine_status_reply,
    handle_concubine_heart_reply,
    handle_concubine_tianji_reply,
    restore_concubine_runtime,
    run_concubine_scheduler,
)
from .features.pet import handle_pet_cd_fix, handle_pet_warm_reply, handle_pet_trial_reply, run_pet_scheduler
from .features.ranch import handle_ranch_reply, handle_ranch_return_broadcast, run_ranch_scheduler
from .features.jiyin import handle_jiyin_prompt, run_jiyin_scheduler
from .features.join_dungeon import handle_dungeon_join_bot_message, handle_dungeon_join_mention, record_game_group_message
from .features.nanlong import handle_nanlong_prompt, handle_nanlong_reply, handle_nanlong_result_broadcast, run_nanlong_scheduler
from .features.quiz import handle_quiz_learning_prompt, handle_quiz_prompt, handle_quiz_result_broadcast, run_quiz_learning_scheduler, run_quiz_scheduler
from .features.tianti import handle_tianti_reply, run_tianti_scheduler
from .features.tiandao_judgement import handle_tiandao_judgement_prompt, handle_tiandao_judgement_punishment, run_tiandao_judgement_scheduler
from .features.tianji_quiz import handle_tianji_quiz_prompt, handle_tianji_quiz_result_broadcast, run_tianji_quiz_scheduler
from .features.small_world import (
    handle_small_world_disaster_broadcast,
    handle_small_world_harvest_reply,
    handle_small_world_manifest_reply,
    handle_small_world_preach_reply,
    handle_small_world_query_reply,
    handle_small_world_refine_reply,
    run_small_world_scheduler,
)
from .features.stargazer import (
    handle_stargazer_collect_reply,
    handle_stargazer_guide_reply,
    handle_stargazer_panel,
    handle_stargazer_soothe_reply,
    handle_stargazer_sync_reply,
    run_stargazer_scheduler,
)
from .features.storage_bag import handle_storage_bag_reply
from .features.tower import handle_tower_reply, run_tower_scheduler
from .features.tree import (
    handle_tree_cd_fix,
    handle_tree_exception_prompt,
    handle_tree_harvest_reply,
    handle_tree_invasion_end,
    handle_tree_invasion_start,
    handle_tree_panel,
    handle_tree_rebirth_reset,
    run_tree_bootstrap_check,
    run_tree_scheduler,
)
from .features.second_soul import (
    handle_second_soul_choice_result_broadcast,
    handle_second_soul_heart_demon_warning_broadcast,
    handle_second_soul_recovery_broadcast,
    handle_second_soul_return_broadcast,
    handle_second_soul_status_reply,
    handle_second_soul_train_reply,
    run_second_soul_bootstrap_check,
    run_second_soul_scheduler,
)
from .features.taiyi import (
    handle_taiyi_node_define_reply,
    handle_taiyi_node_search_reply,
    handle_taiyi_yindao_reply,
    run_taiyi_bootstrap_check,
    run_taiyi_scheduler,
)
from .features.yuanying import (
    handle_yuanying_running_reply,
    handle_yuanying_status_reply,
    handle_yuanying_success_reply,
    handle_yuanying_summary_broadcast,
    run_yuanying_scheduler,
)
from .features.wild_training import handle_wild_training_reply, run_wild_training_scheduler
from .persistence import flush_if_dirty, load_state, mark_dirty, save_state
from .action_guard import close_by_family as close_action_guard_by_family
from .runtime import (
    _fire_and_forget,
    check_bot_health_timeout,
    clear_all_pending_tasks,
    clear_pending_by_reply,
    gc_my_msg_ids,
    gc_ui_login_tokens,
    gc_ui_sessions,
    get_reply_context,
    is_identity_weak,
    is_account_session_error,
    is_reply_to_identity_message,
    mark_bot_health_recovered,
    note_game_bot_message,
    note_game_command_observed,
    note_identity_weakness,
    resolve_reply_family,
    run_retry_scheduler,
    schedule_cleanup,
    send_game_command,
    should_pause_for_bot_health,
    track_reply_chain_message,
    mono,
    send_audit_log,
)
from .state import (
    IDENTITY_TIMER_COLUMNS,
    get_accounts,
    get_game_bot_ids,
    get_game_group_id,
    get_global_enabled,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    set_game_bot_ids,
    state,
    use_identity,
)
from .timing import fmt_time_after
from .ui import start_ui_server, stop_ui_server

_bot_silence_auto_paused = False
_identity_scheduler_task = None
_identity_scheduler_started_at = 0.0
_identity_scheduler_last_warn_at = 0.0
_runtime_event_claims = {}
_runtime_message_consumed = {}
_runtime_log_claims = {}
_suspected_game_bot_hits = {}

IDENTITY_SCHEDULER_STUCK_WARN_SEC = 15 * 60
UNKNOWN_GAME_BOT_LEARN_THRESHOLD = 3
UNKNOWN_GAME_BOT_HIT_TTL_SEC = 24 * 3600

BOT_REPLY_FAMILY_HINTS = {
    "checkin": ("点卯", "已点卯", "已经点过", "宗门"),
    "sect_teach": ("传功", "宗门", "贡献"),
    "tower": ("闯塔", "古塔", "塔灵", "挑战", "道心受挫"),
    "pet": ("器灵", "法宝", "默契", "经验", "休息"),
    "pet_warm": ("温养器灵", "温养", "灵光大振", "吞纳过灵机"),
    "pet_trial": ("器灵试炼", "试炼", "共鸣", "灵潮", "反噬"),
    "tree_panel": ("灵眼之树", "灵树", "果实", "采摘", "成熟"),
    "tree_guard": ("守山", "护山", "攻山", "灵树"),
    "tree_harvest": ("采摘", "灵果", "木髓", "灵树"),
    "stargazer_panel": ("观星台", "引星盘", "星辰"),
    "stargazer_guide": ("牵引", "星辰", "引星盘", "星力"),
    "stargazer_soothe": ("安抚", "狂暴星力", "引星盘"),
    "stargazer_collect": ("收集", "精华", "星辰", "引星盘"),
    "stargazer_sync": ("观星台", "引星盘", "星辰"),
    "guanxing_query": ("观星台", "引星盘", "空闲", "精华"),
    "guanxing_shift": ("牵引", "星辰", "引星盘", "星力"),
    "tianti_status": ("天梯", "问心", "罡风", "登天"),
    "tianti_wenxin": ("问心", "天梯", "道心"),
    "tianti_climb": ("天梯", "登天", "层", "修为"),
    "tianti_gangfeng": ("九天罡风", "罡风", "再聚"),
    "yuanying": ("元婴", "出窍", "归窍", "法则碎片", "探寻"),
    "deep_retreat": ("深度闭关", "闭关", "神魂", "功成圆满", "总结"),
    "small_world_preach": ("小世界", "香火", "信仰", "神识", "神迹"),
    "small_world_query": ("小世界", "香火", "祈愿", "显灵", "紫府"),
    "small_world_manifest": ("显灵", "祈愿", "清灵丹", "灵石", "小世界"),
    "small_world_harvest": ("收割香火", "香火", "库存", "小世界"),
    "small_world_refine": ("神识淬炼", "香火", "神识", "小世界"),
    "concubine_status": ("侍妾", "道侣", "红尘", "情缘", "残图"),
    "concubine_dream": ("入梦寻图", "侍妾", "残图", "梦图感应"),
    "concubine_fragment": ("虚天残图", "残图", "残纹", "拼片"),
    "concubine_puzzle": ("拼图", "虚天残图", "拼合", "残纹"),
    "concubine_reacquire": ("侍妾", "道侣", "红尘寻缘", "宗门赐婚", "红颜"),
    "concubine_tianji": ("天机代卜", "天机链路", "卜算天机", "代卜"),
    "concubine_heart": ("共历心劫", "坠魔心劫", "心劫余波", "心劫抉择"),
    "nanlong": ("南陇侯", "交易", "侍妾", "法宝", "功法"),
    "second_soul_status": ("第二元神", "元神", "心魔", "修炼"),
    "second_soul_train": ("第二元神", "元神", "修炼", "闭关"),
    "second_soul_choice": ("心魔", "抉择", "第二元神"),
    "taiyi_yindao": ("引道", "太一", "五行", "神识"),
    "taiyi_node_search": ("搜寻节点", "空间节点", "虚空", "神识"),
    "taiyi_node_define": ("定星", "空间节点", "稳固", "材料"),
}


def _track_manual_game_command(sender_id, text, msg_id):
    command = str(text or "").strip()
    if not command:
        return
    family = resolve_reply_family(command)
    if family:
        track_reply_chain_message(msg_id, sender_id, family, root_msg_id=msg_id)


def _looks_like_game_bot_reply(text, family):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    hints = BOT_REPLY_FAMILY_HINTS.get(str(family or "").strip()) or ()
    return any(hint in raw_text for hint in hints)


async def _note_game_bot_activity():
    global _bot_silence_auto_paused
    bot_health_action = note_game_bot_message(time.time())
    if bot_health_action == "probe":
        if _bot_silence_auto_paused:
            asyncio.create_task(_send_bot_health_probe())
    elif bot_health_action == "recover":
        if _bot_silence_auto_paused and not get_global_enabled():
            await toggle_global_enabled(True, source="bot_health_recovery")
        _bot_silence_auto_paused = False
        mark_bot_health_recovered("bot 恢复确认完成")


async def _record_suspected_game_bot(sender_id, family, text):
    sender_id = int(sender_id or 0)
    if sender_id == 0 or sender_id in set(get_game_bot_ids()):
        return
    now = time.time()
    item = _suspected_game_bot_hits.get(sender_id) or {"count": 0, "first_seen": now, "notified": False, "learned": False}
    if now - float(item.get("first_seen", now) or now) > UNKNOWN_GAME_BOT_HIT_TTL_SEC:
        item = {"count": 0, "first_seen": now, "notified": False, "learned": False}
    item["count"] = int(item.get("count", 0) or 0) + 1
    _suspected_game_bot_hits[sender_id] = item

    if not item.get("notified"):
        item["notified"] = True
        await send_audit_log(
            f"🧩 检测到未登记游戏 bot 回复，已临时放行：{sender_id}｜{family}｜{str(text or '')[:60]}",
            scope="global",
            limit=260,
        )

    if int(item.get("count", 0) or 0) >= UNKNOWN_GAME_BOT_LEARN_THRESHOLD and not item.get("learned"):
        known_ids = set(get_game_bot_ids())
        known_ids.add(sender_id)
        set_game_bot_ids(sorted(known_ids))
        item["learned"] = True
        save_state()
        await send_audit_log(
            f"🧩 未登记游戏 bot {sender_id} 连续命中 {item['count']} 次，已自动加入 game_bot_ids。",
            scope="global",
            limit=220,
        )


async def _handle_suspected_game_bot_reply(event, text, now, *, edited=False):
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if sender_id in set(int(identity_id) for identity_id in get_identity_ids()):
        return False
    reply_to, reply_context = await _resolve_event_reply(event)
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0 or not matched_family:
        return False
    if not _looks_like_game_bot_reply(text, matched_family):
        return False

    handled_reply = await _handle_routed_reply_event(event, text, now, reply_to, reply_context)
    if handled_reply:
        await _note_game_bot_activity()
        await _record_suspected_game_bot(sender_id, matched_family, text)
    return handled_reply


def _is_identity_account_offline(identity_id):
    account_id = int(get_identity_account(identity_id) or 0)
    return bool(account_id and is_account_offline(account_id))


def _get_bot_health_probe_identity_id():
    for identity_id in get_identity_ids():
        if get_identity_enabled(identity_id) and not _is_identity_account_offline(identity_id):
            return int(identity_id)
    return None


async def _send_bot_health_probe():
    identity_id = _get_bot_health_probe_identity_id()
    if identity_id is None:
        await send_audit_log("🩺 天尊恢复探测跳过：没有可用身份，维持全局暂停。", scope="global", limit=220)
        return
    msg = await send_game_command(CMD_IDENTITY_INFO, track=True, send_as_id=identity_id, priority="probe", max_retry=0)
    if msg:
        await send_audit_log("🩺 天尊恢复探测已发送，等待确认回复后恢复普通调度。", scope="identity", send_as_id=identity_id, limit=220)


def _gc_runtime_event_claims(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_event_claims.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_event_claims.pop(key, None)


def _claim_runtime_event(event, *, scope, ttl=120.0):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if msg_id <= 0 or chat_id == 0:
        return True
    now = time.time()
    _gc_runtime_event_claims(now)
    claim_key = f"{scope}:{chat_id}:{msg_id}"
    if float(_runtime_event_claims.get(claim_key, 0) or 0) > now:
        return False
    _runtime_event_claims[claim_key] = now + float(ttl or 0)
    return True


def _gc_runtime_message_consumed(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_message_consumed.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_message_consumed.pop(key, None)


def _gc_runtime_log_claims(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_log_claims.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_log_claims.pop(key, None)


def _claim_runtime_log_event(event, *, event_type, ttl=120.0):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    event_type = str(event_type or "message").strip() or "message"
    if msg_id <= 0 or chat_id == 0:
        return True
    now = time.time()
    _gc_runtime_log_claims(now)
    claim_key = f"{event_type}:{chat_id}:{msg_id}"
    if float(_runtime_log_claims.get(claim_key, 0) or 0) > now:
        return False
    _runtime_log_claims[claim_key] = now + float(ttl or 0)
    return True


def _get_runtime_message_consumed_key(event, family):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    family = str(family or "").strip()
    if msg_id <= 0 or chat_id == 0 or not family:
        return ""
    return f"{family}:{chat_id}:{msg_id}"


def _has_runtime_message_consumed(event, family):
    claim_key = _get_runtime_message_consumed_key(event, family)
    if not claim_key:
        return False
    now = time.time()
    _gc_runtime_message_consumed(now)
    return float(_runtime_message_consumed.get(claim_key, 0) or 0) > now


def _mark_runtime_message_consumed(event, family, *, ttl=120.0):
    claim_key = _get_runtime_message_consumed_key(event, family)
    if not claim_key:
        return
    now = time.time()
    _gc_runtime_message_consumed(now)
    _runtime_message_consumed[claim_key] = now + float(ttl or 0)


def _is_identity_owner_event(event, send_as_id):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return False
    account_id = get_identity_account(send_as_id)
    if not account_id:
        live_clients = [
            tc
            for live_account_id, tc in get_all_clients().items()
            if int(live_account_id or 0) > 0 and not is_account_offline(live_account_id)
        ]
        expected_client = live_clients[0] if len(live_clients) == 1 else client
    elif is_account_offline(account_id):
        return False
    else:
        expected_client = get_registered_client(account_id)
        if expected_client is None:
            return False
    return getattr(event, "client", None) is expected_client


def _append_game_group_message_log(event, *, event_type="message"):
    if event.chat_id != get_game_group_id():
        return
    if not _claim_runtime_log_event(event, event_type=event_type):
        return
    now = datetime.now(TZ_LOCAL)
    log_file = f"{MESSAGES_DIR}/{now.strftime('%Y-%m-%d')}.log"
    reply_header = getattr(event, "reply_to", None)
    reply_to_msg_id = int(getattr(reply_header, "reply_to_msg_id", 0) or 0)
    topic_id = int(getattr(reply_header, "reply_to_top_id", 0) or 0)
    payload = {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": event_type,
        "message_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "sender_id": int(getattr(event, "sender_id", 0) or 0),
        "topic_id": topic_id,
        "reply_to_msg_id": reply_to_msg_id,
        "text": event.raw_text or "",
    }
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        print(traceback.format_exc())


def _get_event_reply_header_msg_id(event):
    reply_header = getattr(event, "reply_to", None)
    return int(getattr(reply_header, "reply_to_msg_id", 0) or 0)


async def _resolve_event_reply(event):
    try:
        reply_to = await event.get_reply_message()
    except Exception as exc:
        if is_account_session_error(exc):
            event_client = getattr(event, "client", None)
            for account_id, tc in get_all_clients().items():
                if event_client is tc:
                    mark_account_offline(account_id, str(exc))
                    break
            reply_to = None
        else:
            raise
    reply_context = get_reply_context(reply_to, reply_to_msg_id=_get_event_reply_header_msg_id(event))
    return reply_to, reply_context


async def _run_for_all_identities(handler, *args, enabled_only=False):
    for identity_id in get_identity_ids():
        if enabled_only and not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            await handler(*args)


async def _run_until_handled_for_enabled_identities(handler, text, now, event):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            if await handler(text, now, event):
                return True
    return False


async def _run_claimed_prompt_handler(scope, handler, text, now, event):
    if not _claim_runtime_event(event, scope=scope):
        return False
    return await _run_until_handled_for_enabled_identities(handler, text, now, event)


def _is_concubine_loss_broadcast_candidate(text):
    raw_text = str(text or "")
    return (
        "南陇侯" in raw_text
        and "侍妾" in raw_text
        and ("掳走" in raw_text or "选择将侍妾" in raw_text)
    )


async def _dispatch_new_message_broadcasts(event, text, now, reply_to=None):
    await _dispatch_broadcast_handlers(event, text, now, _NEW_MESSAGE_BROADCAST_HANDLERS, reply_to=reply_to)
    if _is_concubine_loss_broadcast_candidate(text) and _claim_runtime_event(event, scope="concubine_loss"):
        await _run_until_handled_for_enabled_identities(handle_concubine_loss_broadcast, text, now, event)


_NEW_MESSAGE_BROADCAST_HANDLERS = (
    ("deep_retreat_summary", handle_deep_retreat_summary_broadcast),
    ("yuanying_summary", handle_yuanying_summary_broadcast),
    ("realm_breakthrough", handle_realm_breakthrough_broadcast),
    ("quiz_result", handle_quiz_result_broadcast),
    ("quiz_learning_prompt", handle_quiz_learning_prompt),
    ("tianji_quiz_result", handle_tianji_quiz_result_broadcast),
    ("tianji_quiz_prompt", handle_tianji_quiz_prompt),
    ("tiandao_judgement_punishment", handle_tiandao_judgement_punishment),
    ("tiandao_judgement_prompt", handle_tiandao_judgement_prompt),
    ("guanxing_finish", handle_guanxing_finish_broadcast),
    ("ranch_return", handle_ranch_return_broadcast),
)
_BROADCAST_EVENT_HANDLERS = {
    handle_quiz_learning_prompt,
    handle_tianji_quiz_prompt,
    handle_tiandao_judgement_punishment,
    handle_tiandao_judgement_prompt,
    handle_ranch_return_broadcast,
}
_BROADCAST_REPLY_CONTEXT_HANDLERS = {
    handle_tianji_quiz_result_broadcast,
}


async def _dispatch_broadcast_handlers(event, text, now, handlers, *, reply_to=None):
    for scope, handler in handlers:
        if not _claim_runtime_event(event, scope=scope):
            continue
        if handler in _BROADCAST_REPLY_CONTEXT_HANDLERS:
            await handler(text, now, event, reply_to=reply_to)
        elif handler in _BROADCAST_EVENT_HANDLERS:
            await handler(text, now, event)
        else:
            await handler(text, now)


async def _dispatch_tree_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="tree_invasion_end"):
        await _run_for_all_identities(handle_tree_invasion_end, text, now, False)
    if _claim_runtime_event(event, scope="tree_invasion_start"):
        await _run_for_all_identities(handle_tree_invasion_start, text, now)
    if _claim_runtime_event(event, scope="tree_rebirth_reset"):
        await _run_for_all_identities(handle_tree_rebirth_reset, text, now)
    if _claim_runtime_event(event, scope="tree_panel"):
        await _run_for_all_identities(handle_tree_panel, text, now, False)


async def _dispatch_stargazer_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="stargazer_panel"):
        await _run_for_all_identities(handle_stargazer_panel, text, now, False)


async def _dispatch_guanxing_monitor_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="guanxing_monitor_broadcast"):
        await handle_guanxing_monitor_broadcast(text, now)


async def _dispatch_small_world_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="small_world_disaster"):
        await _run_until_handled_for_enabled_identities(handle_small_world_disaster_broadcast, text, now, event)


async def _dispatch_nanlong_result_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="nanlong_result"):
        await _run_until_handled_for_enabled_identities(handle_nanlong_result_broadcast, text, now, event)


async def _dispatch_second_soul_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="second_soul_return"):
        await handle_second_soul_return_broadcast(text, now)
    if _claim_runtime_event(event, scope="second_soul_heart_demon_warning"):
        await handle_second_soul_heart_demon_warning_broadcast(text, now, event.id)
    if _claim_runtime_event(event, scope="second_soul_choice_result"):
        await handle_second_soul_choice_result_broadcast(text, now)
    if _claim_runtime_event(event, scope="second_soul_recovery"):
        await handle_second_soul_recovery_broadcast(text, now)


async def _dispatch_message_edited_realm_breakthrough(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, _EARLY_MESSAGE_EDIT_BROADCAST_HANDLERS)


async def _dispatch_message_edited_tree_panel(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("tree_panel_edit", handle_tree_panel),))


async def _dispatch_message_edited_stargazer_panel(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("stargazer_panel_edit", handle_stargazer_panel),))


async def _dispatch_message_edited_guanxing_monitor(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("guanxing_monitor_broadcast_edit", handle_guanxing_monitor_broadcast),))


async def _dispatch_message_edited_concubine_loss(event, text, now):
    if _is_concubine_loss_broadcast_candidate(text) and _claim_runtime_event(event, scope="concubine_loss"):
        await _run_until_handled_for_enabled_identities(handle_concubine_loss_broadcast, text, now, event)


async def _dispatch_message_edited_phaseful_summaries(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, _PHASEFUL_MESSAGE_EDIT_BROADCAST_HANDLERS)


_EARLY_MESSAGE_EDIT_BROADCAST_HANDLERS = (
    ("realm_breakthrough_edit", handle_realm_breakthrough_broadcast),
)
_PHASEFUL_MESSAGE_EDIT_BROADCAST_HANDLERS = (
    ("deep_retreat_summary_edit", handle_deep_retreat_summary_broadcast),
    ("yuanying_summary_edit", handle_yuanying_summary_broadcast),
)
_MESSAGE_EDIT_IDENTITY_BROADCAST_HANDLERS = {
    handle_tree_panel,
    handle_stargazer_panel,
}
_MESSAGE_EDIT_EVENT_BROADCAST_HANDLERS = {
    handle_ranch_return_broadcast,
}


async def _dispatch_message_edited_broadcasts(event, text, now, handlers):
    for scope, handler in handlers:
        if not _claim_runtime_event(event, scope=scope):
            continue
        if handler in _MESSAGE_EDIT_IDENTITY_BROADCAST_HANDLERS:
            await _run_for_all_identities(handler, text, now, False)
        elif handler in _MESSAGE_EDIT_EVENT_BROADCAST_HANDLERS:
            await handler(text, now, event)
        else:
            await handler(text, now)


async def _run_identity_schedulers(now):
    phaseful_schedulers = (
        run_deep_retreat_scheduler,
        run_yuanying_scheduler,
    )
    ordinary_schedulers = (
        run_tree_bootstrap_check,
        run_tree_scheduler,
        run_pet_scheduler,
        run_ranch_scheduler,
        run_wild_training_scheduler,
        run_stargazer_scheduler,
        run_tianti_scheduler,
        run_quiz_scheduler,
        run_jiyin_scheduler,
        run_concubine_scheduler,
        run_nanlong_scheduler,
        run_small_world_scheduler,
        run_checkin_scheduler,
        run_tower_scheduler,
        run_second_soul_bootstrap_check,
        run_second_soul_scheduler,
        run_taiyi_bootstrap_check,
        run_taiyi_scheduler,
    )

    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            if is_identity_weak(identity_id, now):
                continue
            for scheduler in phaseful_schedulers:
                await scheduler(now)

    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            if is_identity_weak(identity_id, now):
                continue
            if has_phaseful_summary_block(now):
                continue
            for scheduler in ordinary_schedulers:
                await scheduler(now)


async def _run_global_schedulers(now):
    await run_guanxing_monitor_scheduler(now)
    await run_guanxing_scheduler(now)
    await run_tiandao_judgement_scheduler(now)
    await run_tianji_quiz_scheduler(now)


async def _run_identity_schedulers_background(now):
    await _run_identity_schedulers(now)


def _handle_identity_scheduler_done(task):
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        print("identity scheduler crashed:")
        print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        _fire_and_forget(
            send_audit_log(
                f"❌ 身份调度后台异常：{str(exc)[:180]}",
                scope="global",
                limit=300,
            )
        )


def _start_identity_schedulers_if_idle(now):
    global _identity_scheduler_task, _identity_scheduler_started_at, _identity_scheduler_last_warn_at
    if _identity_scheduler_task and not _identity_scheduler_task.done():
        if now - _identity_scheduler_started_at >= IDENTITY_SCHEDULER_STUCK_WARN_SEC and now - _identity_scheduler_last_warn_at >= IDENTITY_SCHEDULER_STUCK_WARN_SEC:
            _identity_scheduler_last_warn_at = now
            _fire_and_forget(
                send_audit_log(
                    "⏳ 身份调度仍在等待发送队列，主循环未阻塞，会继续按全局间隔串行发送。",
                    scope="global",
                    limit=260,
                )
            )
        return
    _identity_scheduler_started_at = float(now)
    _identity_scheduler_task = asyncio.create_task(_run_identity_schedulers_background(now))
    _identity_scheduler_task.add_done_callback(_handle_identity_scheduler_done)


def _cancel_identity_schedulers():
    global _identity_scheduler_task
    if _identity_scheduler_task and not _identity_scheduler_task.done():
        _identity_scheduler_task.cancel()


async def _handle_routed_reply_event(event, text, now, reply_to, reply_context, *, allow_tree_panel_claim=True, event_kind="message"):
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0:
        return False

    family_scope = str(matched_family or "unknown").strip() or "unknown"
    kind_scope = str(event_kind or "message").strip() or "message"
    if not _claim_runtime_event(event, scope=f"routed_reply:{kind_scope}:{routed_identity_id}:{family_scope}"):
        return False

    # In multi-client mode a bot reply can be delivered to a different account
    # client than the one that sent the command. The reply_to message id is the
    # authoritative owner here; requiring the owner client would leave pending
    # tasks uncleared and trigger retry storms.
    already_consumed = bool(matched_family) and _has_runtime_message_consumed(event, matched_family)
    with use_identity(routed_identity_id):
        is_reply_to_me = is_reply_to_identity_message(reply_to, routed_identity_id)
        clear_result = clear_pending_by_reply(reply_to, routed_identity_id, reply_context=reply_context)
        root_msg_id = int((reply_context or {}).get("root_msg_id") or (clear_result or {}).get("reply_to_msg_id") or 0)
        if root_msg_id <= 0:
            root_msg_id = int(getattr(reply_to, "id", 0) or 0)
        if matched_family:
            track_reply_chain_message(event.id, routed_identity_id, matched_family, root_msg_id=root_msg_id)
            close_action_guard_by_family(matched_family, send_as_id=routed_identity_id, reason="bot_reply", now=now)

        handled_any = False
        note_identity_weakness(text, now, routed_identity_id, source=matched_family or "reply")
        await handle_tree_invasion_end(text, now, is_reply_to_me)
        await handle_tree_invasion_start(text, now)
        await handle_tree_rebirth_reset(text, now)
        if matched_family == "stargazer_sync":
            synced_panel = handle_stargazer_sync_reply(text, now=now)
            if synced_panel:
                declared_total_slots = int(synced_panel.get("declared_total_slots", 0) or 0)
                idle_slot_count = int(synced_panel.get("idle_slot_count", 0) or 0)
                dim_slot_count = int(synced_panel.get("dim_slot_count", 0) or 0)
                ready_slot_count = int(synced_panel.get("ready_slot_count", 0) or 0)
                max_wait = int(synced_panel.get("max_wait", 0) or 0)
                await send_audit_log(
                    f"🔭 已同步观星台[{routed_identity_id}]：总星盘 {declared_total_slots}，空闲 {idle_slot_count}，黯淡 {dim_slot_count}，精华已成 {ready_slot_count}，最长等待 {fmt_time_after(max_wait) if max_wait > 0 else '已无等待'}",
                    scope="identity",
                    send_as_id=routed_identity_id,
                )
                handled_any = True

        if allow_tree_panel_claim and not already_consumed and matched_family != "stargazer_sync":
            tree_panel_done = await handle_tree_panel(text, now, is_reply_to_me)
            handled_any = handled_any or tree_panel_done
            stargazer_panel_done = await handle_stargazer_panel(text, now, is_reply_to_me, matched_family=matched_family)
            handled_any = handled_any or stargazer_panel_done
            handled_any = await handle_tree_harvest_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any

        if not already_consumed and matched_family != "stargazer_sync":
            handled_any = await handle_tree_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_warm_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_trial_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_ranch_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_wild_training_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_checkin_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_sect_teach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tower_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_guide_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_soothe_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_collect_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tianti_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_status_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any
            handled_any = await handle_concubine_dream_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_fragment_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_puzzle_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_reacquire_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_tianji_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_heart_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any
            handled_any = await handle_nanlong_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_guanxing_query_reply(text, now, reply_to, event.id, matched_family=matched_family) or handled_any
            handled_any = await handle_identity_info_reply(text, now, reply_to, event.id) or handled_any
            deep_retreat_done = await handle_deep_retreat_success_reply(text, now, reply_to, matched_family=matched_family)
            handled_any = handled_any or deep_retreat_done
            if not deep_retreat_done:
                deep_retreat_done = await handle_deep_retreat_running_reply(text, now, reply_to, matched_family=matched_family)
                handled_any = handled_any or deep_retreat_done
            if not deep_retreat_done:
                handled_any = await handle_deep_retreat_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            yuanying_done = await handle_yuanying_success_reply(text, now, reply_to, matched_family=matched_family)
            handled_any = handled_any or yuanying_done
            if not yuanying_done:
                yuanying_done = await handle_yuanying_running_reply(text, now, reply_to, matched_family=matched_family)
                handled_any = handled_any or yuanying_done
            if not yuanying_done:
                handled_any = await handle_yuanying_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tree_exception_prompt(text) or handled_any
            handled_any = await handle_small_world_preach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_query_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_manifest_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_harvest_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_refine_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_second_soul_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_second_soul_train_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_yindao_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_node_search_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_node_define_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_storage_bag_reply(text, now, reply_to, matched_family=matched_family) or handled_any

        if matched_family and handled_any and not already_consumed:
            _mark_runtime_message_consumed(event, matched_family)

    await schedule_cleanup(reply_to, send_as_id=routed_identity_id)
    return handled_any


@client.on(events.NewMessage())
async def on_message(event):
    _append_game_group_message_log(event, event_type="message")

    if _claim_runtime_event(event, scope="log_group_command"):
        if await handle_log_group_command(event):
            return

    if event.chat_id != get_game_group_id():
        return
    now = time.time()
    record_game_group_message(event, now=now, event_type="message")

    # bot 健康监测：记录 . 开头指令的触发时间
    raw_text = (event.raw_text or "").strip()
    sender_id = int(event.sender_id or 0)
    if raw_text.startswith(".") and sender_id in set(int(identity_id) for identity_id in get_identity_ids()) and get_global_enabled():
        note_game_command_observed(raw_text)

    if event.sender_id not in set(get_game_bot_ids()):
        text = event.raw_text or ""
        if sender_id in set(int(identity_id) for identity_id in get_identity_ids()):
            observe_phaseful_identity_message(sender_id, text, now=now, msg_id=event.id)
            _track_manual_game_command(sender_id, text, event.id)
        try:
            await handle_dungeon_join_mention(event, text, now)
            if await _handle_suspected_game_bot_reply(event, text, now):
                return
            if _claim_runtime_event(event, scope="guanxing_external_shift"):
                await handle_guanxing_external_shift_command(text, now, event)
        except Exception:
            print(traceback.format_exc())
        return

    # bot 健康监测：bot 有发言后，暂停态先探测，再恢复
    await _note_game_bot_activity()

    text = event.raw_text or ""

    try:
        reply_to, reply_context = await _resolve_event_reply(event)

        await _dispatch_new_message_broadcasts(event, text, now, reply_to=reply_to)
        await handle_dungeon_join_bot_message(event, text, now)

        if await _run_claimed_prompt_handler("quiz_prompt", handle_quiz_prompt, text, now, event):
            return

        if await _run_claimed_prompt_handler("jiyin_prompt", handle_jiyin_prompt, text, now, event):
            return

        if await _run_claimed_prompt_handler("nanlong_prompt", handle_nanlong_prompt, text, now, event):
            return

        if int((reply_context or {}).get("send_as_id") or 0) > 0:
            handled_reply = await _handle_routed_reply_event(event, text, now, reply_to, reply_context)
            if handled_reply:
                return

        await _dispatch_tree_broadcast_fallbacks(event, text, now)
        await _dispatch_stargazer_broadcast_fallbacks(event, text, now)
        await _dispatch_guanxing_monitor_broadcast_fallbacks(event, text, now)
        await _dispatch_small_world_broadcast_fallbacks(event, text, now)
        await _dispatch_nanlong_result_broadcast_fallbacks(event, text, now)
        await _dispatch_second_soul_broadcast_fallbacks(event, text, now)
        await handle_storage_bag_reply(text, now, reply_to)

    except Exception:
        print(traceback.format_exc())


@client.on(events.MessageEdited())
async def on_message_edited(event):
    _append_game_group_message_log(event, event_type="edit")

    if event.chat_id != get_game_group_id():
        return
    if event.sender_id not in set(get_game_bot_ids()):
        try:
            now = time.time()
            text = event.raw_text or ""
            await _handle_suspected_game_bot_reply(event, text, now, edited=True)
        except Exception:
            print(traceback.format_exc())
        return

    await _note_game_bot_activity()

    now = time.time()
    text = event.raw_text or ""

    try:
        reply_to, reply_context = await _resolve_event_reply(event)

        await _dispatch_message_edited_realm_breakthrough(event, text, now)
        await _dispatch_message_edited_concubine_loss(event, text, now)
        await _dispatch_message_edited_phaseful_summaries(event, text, now)
        await handle_dungeon_join_bot_message(event, text, now)

        if int((reply_context or {}).get("send_as_id") or 0) > 0:
            handled_reply = await _handle_routed_reply_event(
                event,
                text,
                now,
                reply_to,
                reply_context,
                event_kind="edit",
            )
            if handled_reply:
                return

        await _dispatch_message_edited_tree_panel(event, text, now)
        await _dispatch_message_edited_stargazer_panel(event, text, now)
        await _dispatch_message_edited_guanxing_monitor(event, text, now)
        await _dispatch_message_edited_broadcasts(event, text, now, (("ranch_return_edit", handle_ranch_return_broadcast),))
        await _dispatch_second_soul_broadcast_fallbacks(event, text, now)
    except Exception:
        print(traceback.format_exc())


def _register_event_handlers(tc):
    tc.add_event_handler(on_message, events.NewMessage())
    tc.add_event_handler(on_message_edited, events.MessageEdited())


async def bootstrap():
    loaded = load_state()
    saved_accounts = get_accounts()

    # 多账号模式下只启动账号 client，避免主 session 也挂一个空转 Telegram 会话。
    # 没有保存账号时保留旧的单账号主 session 启动路径。
    if not saved_accounts:
        await client.connect()

    # 启动已保存的额外账号 client
    failed_accounts = []
    for acct_id_str, acct_info in saved_accounts.items():
        acct_id = 0
        tc = None
        try:
            acct_id = int(acct_id_str)
            acct_info = acct_info if isinstance(acct_info, dict) else {}
            tc = create_account_client(
                acct_id,
                api_id=acct_info.get("api_id"),
                api_hash=acct_info.get("api_hash"),
            )
            await tc.connect()
            if not await tc.is_user_authorized():
                error_text = "session 未授权，请通过 UI 重新登录账号"
                mark_account_offline(acct_id, error_text)
                failed_accounts.append({"account_id": acct_id, "error": error_text})
                print(f"启动额外账号 {acct_id_str} 跳过: {error_text}")
                try:
                    await tc.disconnect()
                except Exception:
                    pass
                continue
            try:
                await tc.get_dialogs()
            except Exception:
                pass
            register_client(acct_id, tc)
            _register_event_handlers(tc)
        except Exception:
            tb = traceback.format_exc()
            error_text = tb.strip().splitlines()[-1] if tb.strip() else "启动失败"
            mark_account_offline(acct_id_str, error_text)
            failed_accounts.append({"account_id": acct_id or int(acct_id_str), "error": error_text})
            if tc is not None and tc.is_connected():
                try:
                    await tc.disconnect()
                except Exception:
                    pass
            print(f"启动额外账号 {acct_id_str} 失败: {tb}")

    await start_ui_server()

    # 获取 my_user_id：优先主 client，再尝试已登录账号
    if client.is_connected():
        try:
            _me = await client.get_me()
            if _me:
                state["my_user_id"] = _me.id
                if get_registered_client(_me.id) is None:
                    register_client(_me.id, client)
        except Exception:
            pass
    if not state.get("my_user_id"):
        for _acct_id_str in get_accounts():
            try:
                _account_id = int(_acct_id_str)
                if is_account_offline(_account_id):
                    continue
                _tc = get_registered_client(_account_id)
                if _tc is None:
                    continue
                _me = await _tc.get_me()
                if _me:
                    state["my_user_id"] = _me.id
                    break
            except Exception:
                pass

    identity_ids = get_identity_ids()
    startup_account_check_result = run_startup_account_integrity_check(identity_ids, failed_accounts)
    runtime_account_ids = [
        int(account_id)
        for account_id in get_all_clients().keys()
        if int(account_id or 0) > 0 and not is_account_offline(account_id)
    ]
    single_runtime_account_id = runtime_account_ids[0] if len(runtime_account_ids) == 1 else 0
    for send_as_id in identity_ids:
        try:
            account_id = get_identity_account(send_as_id) or single_runtime_account_id
            if not account_id:
                print(f"hydrate_identity_profile skipped (no account): {send_as_id}")
                continue
            if is_account_offline(account_id):
                print(f"hydrate_identity_profile skipped (offline account): {send_as_id} acc={account_id}")
                continue
            tc = get_registered_client(account_id)
            if tc is None:
                print(f"hydrate_identity_profile skipped (account client missing): {send_as_id} acc={account_id}")
                continue
            send_as_entity = await tc.get_entity(send_as_id)
            hydrate_identity_profile(send_as_entity)
        except Exception:
            print(f"hydrate_identity_profile failed: {send_as_id}")
        enforce_identity_module_availability(send_as_id, persist=False)

    now = time.time()
    if state.get("guanxing_monitor_enabled"):
        restore_guanxing_monitor_runtime_state(now)
        mark_dirty()
    _round_state, round_changed = restore_guanxing_round_runtime(now)
    if round_changed:
        mark_dirty()
    startup_scan_result = scan_startup_timeout_tasks(now) if loaded else {"closed_count": 0, "affected_identity_ids": [], "alerts": []}
    any_loaded = False
    if loaded:
        for identity_id in identity_ids:
            identity_state = get_identity_state(identity_id)
            if any(identity_state.get(timer_key, 0) > 0 for timer_key in IDENTITY_TIMER_COLUMNS):
                any_loaded = True
                break

    if not any_loaded:
        for identity_id in identity_ids:
            initialize_identity_runtime(identity_id, now)
        spread_overdue_runtime_timers(now, reason="启动初始化")
        save_state()
    else:
        for identity_id in identity_ids:
            initialize_identity_runtime(identity_id, now)
            mark_dirty()
        spread_overdue_runtime_timers(now, reason="启动恢复")
        save_state()

    identity_lines = [
        f"- {send_as_id}: {mono('@' + (get_send_as_profile(send_as_id).get('username') or '未获取到'))}"
        for send_as_id in identity_ids
    ]
    recovery_text = "成功" if any_loaded else ("无待恢复任务" if loaded else "首次初始化")
    audit_lines = [
        "🚀 自动化系统启动成功",
        f"👤 账号: {state.get('my_user_id') or '未登录（等待 UI 登录）'}",
        f"🎭 并发身份数: {len(identity_ids)}",
        "📡 模式: 多身份 + SQLite 持久化",
        f"💾 状态恢复: {recovery_text}",
        "🪪 身份列表:",
        *identity_lines,
    ]
    if any_loaded:
        audit_lines.extend([
            "♻️ 启动恢复：检测到本地状态，已按 SQLite 中的多身份状态恢复运行。",
            "📌 本次为恢复模式启动：不会执行全量探测，只按各身份本地状态与时间继续调度。",
        ])
    if startup_scan_result.get("closed_count", 0) > 0:
        audit_lines.append(
            f"⚠️ 启动扫描：发现超时任务并自动关闭 {startup_scan_result['closed_count']} 个模块，登录 UI 后可手动恢复。"
        )
    audit_lines.extend(startup_account_check_result.get("audit_lines") or [])
    _fire_and_forget(send_audit_log("\n".join(audit_lines), scope="global", limit=1200))


async def _sleep_or_stop(stop_event, delay):
    if stop_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


async def main_loop(stop_event=None):
    while stop_event is None or not stop_event.is_set():
        now = time.time()

        gc_my_msg_ids(now)
        gc_ui_login_tokens(now)
        gc_ui_sessions(now)
        flush_if_dirty(now)

        # bot 健康监测：疑似静默/探测中直接全局暂停，避免继续普通发送
        global _bot_silence_auto_paused
        check_bot_health_timeout(now, BOT_SILENCE_TIMEOUT_SEC)
        if should_pause_for_bot_health() and get_global_enabled():
            _bot_silence_auto_paused = True
            _cancel_identity_schedulers()
            clear_all_pending_tasks("天尊健康暂停")
            await toggle_global_enabled(False, source="bot_health_monitor")
        if not get_global_enabled():
            _cancel_identity_schedulers()
            await _sleep_or_stop(stop_event, 5)
            continue

        await _run_global_schedulers(now)
        await run_quiz_learning_scheduler(now)
        await run_retry_scheduler(now)
        await run_identity_info_followup_scheduler(now)
        _start_identity_schedulers_if_idle(now)
        await _sleep_or_stop(stop_event, 5)


async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop():
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: request_stop())

    try:
        await bootstrap()
        await main_loop(stop_event)
    finally:
        await shutdown()


async def shutdown():
    _cancel_identity_schedulers()
    save_state()
    await stop_ui_server()
    clients = [client]
    clients.extend(get_all_clients().values())
    seen = set()
    for tc in clients:
        if tc is None or id(tc) in seen:
            continue
        seen.add(id(tc))
        try:
            await tc.disconnect()
        except Exception:
            traceback.print_exc()


__all__ = ["bootstrap", "main", "main_loop", "on_message", "shutdown"]
