import asyncio
import json
import time
import traceback
from datetime import datetime

from telethon import events

from .config import BOT_SILENCE_TIMEOUT_SEC, MESSAGES_DIR, TZ_LOCAL, client, create_account_client, get_client, register_client
from .control import enforce_identity_module_availability, handle_identity_info_reply, handle_log_group_command, handle_realm_breakthrough_broadcast, hydrate_identity_profile, initialize_identity_runtime, run_identity_info_followup_scheduler, run_startup_account_integrity_check, scan_startup_timeout_tasks, toggle_global_enabled
from .features.checkin import handle_checkin_reply, handle_sect_teach_reply, run_checkin_scheduler
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
from .features.pet import handle_pet_cd_fix, run_pet_scheduler
from .features.jiyin import handle_jiyin_prompt, run_jiyin_scheduler
from .features.nanlong import handle_nanlong_prompt, handle_nanlong_reply, handle_nanlong_result_broadcast, run_nanlong_scheduler
from .features.quiz import handle_quiz_learning_prompt, handle_quiz_prompt, handle_quiz_result_broadcast, run_quiz_learning_scheduler, run_quiz_scheduler
from .features.tianti import handle_tianti_reply, run_tianti_scheduler
from .features.tiandao_judgement import handle_tiandao_judgement_prompt, run_tiandao_judgement_scheduler
from .features.tianji_quiz import handle_tianji_quiz_prompt, handle_tianji_quiz_result_broadcast, run_tianji_quiz_scheduler
from .features.small_world import handle_small_world_disaster_broadcast, handle_small_world_preach_reply, run_small_world_scheduler
from .features.stargazer import (
    handle_stargazer_collect_reply,
    handle_stargazer_guide_reply,
    handle_stargazer_panel,
    handle_stargazer_soothe_reply,
    handle_stargazer_sync_reply,
    run_stargazer_scheduler,
)
from .features.tower import handle_tower_reply, run_tower_scheduler
from .features.tree import (
    handle_tree_cd_fix,
    handle_tree_exception_prompt,
    handle_tree_invasion_end,
    handle_tree_invasion_start,
    handle_tree_panel,
    handle_tree_rebirth_reset,
    run_tree_bootstrap_check,
    run_tree_scheduler,
)
from .features.yuanying import (
    handle_yuanying_running_reply,
    handle_yuanying_status_reply,
    handle_yuanying_success_reply,
    handle_yuanying_summary_broadcast,
    run_yuanying_scheduler,
)
from .persistence import flush_if_dirty, load_state, mark_dirty, save_state
from .runtime import (
    clear_pending_by_reply,
    gc_my_msg_ids,
    gc_ui_login_tokens,
    gc_ui_sessions,
    get_reply_context,
    is_reply_to_identity_message,
    run_retry_scheduler,
    schedule_cleanup,
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
    state,
    use_identity,
)
from .timing import (
    fmt_time_after,
    get_checkin_day_key,
    get_day_key,
    reset_checkin_daily_state,
    schedule_next_checkin,
    schedule_next_checkin_after_completion,
    schedule_next_tower,
    schedule_next_tower_after_completion,
)
from .ui import start_ui_server

_bot_silence_triggered_at = 0  # 检测到 . 指令的时间，0 表示未触发
_bot_last_seen_at = 0          # bot 最后发言时间
_bot_silence_auto_paused = False
_runtime_event_claims = {}
_runtime_message_consumed = {}
_runtime_log_claims = {}


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
    expected_client = get_client(account_id) if account_id else client
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
    reply_to = await event.get_reply_message()
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


async def _dispatch_new_message_broadcasts(event, text, now, reply_to=None):
    if _claim_runtime_event(event, scope="deep_retreat_summary"):
        await handle_deep_retreat_summary_broadcast(text, now)
    if _claim_runtime_event(event, scope="yuanying_summary"):
        await handle_yuanying_summary_broadcast(text, now)
    if _claim_runtime_event(event, scope="realm_breakthrough"):
        await handle_realm_breakthrough_broadcast(text, now)
    if _claim_runtime_event(event, scope="quiz_result"):
        await handle_quiz_result_broadcast(text, now)
    if _claim_runtime_event(event, scope="quiz_learning_prompt"):
        await handle_quiz_learning_prompt(text, now, event)
    if _claim_runtime_event(event, scope="tianji_quiz_result"):
        await handle_tianji_quiz_result_broadcast(text, now, event, reply_to=reply_to)
    if _claim_runtime_event(event, scope="tianji_quiz_prompt"):
        await handle_tianji_quiz_prompt(text, now, event)
    if _claim_runtime_event(event, scope="tiandao_judgement_prompt"):
        await handle_tiandao_judgement_prompt(text, now, event)
    if _claim_runtime_event(event, scope="guanxing_finish"):
        await handle_guanxing_finish_broadcast(text, now)


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


async def _dispatch_message_edited_realm_breakthrough(event, text, now):
    if _claim_runtime_event(event, scope="realm_breakthrough_edit"):
        await handle_realm_breakthrough_broadcast(text, now)


async def _dispatch_message_edited_tree_panel(event, text, now):
    if _claim_runtime_event(event, scope="tree_panel_edit"):
        await _run_for_all_identities(handle_tree_panel, text, now, False)


async def _dispatch_message_edited_stargazer_panel(event, text, now):
    if _claim_runtime_event(event, scope="stargazer_panel_edit"):
        await _run_for_all_identities(handle_stargazer_panel, text, now, False)


async def _dispatch_message_edited_guanxing_monitor(event, text, now):
    if _claim_runtime_event(event, scope="guanxing_monitor_broadcast_edit"):
        await handle_guanxing_monitor_broadcast(text, now)


async def _run_identity_schedulers(now):
    identity_schedulers = (
        run_tree_bootstrap_check,
        run_tree_scheduler,
        run_pet_scheduler,
        run_stargazer_scheduler,
        run_tianti_scheduler,
        run_quiz_scheduler,
        run_jiyin_scheduler,
        run_nanlong_scheduler,
        run_small_world_scheduler,
        run_checkin_scheduler,
        run_tower_scheduler,
        run_deep_retreat_scheduler,
        run_yuanying_scheduler,
    )
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            for scheduler in identity_schedulers:
                await scheduler(now)


async def _run_global_schedulers(now):
    await run_guanxing_monitor_scheduler(now)
    await run_guanxing_scheduler(now)
    await run_tiandao_judgement_scheduler(now)
    await run_tianji_quiz_scheduler(now)


async def _handle_routed_reply_event(event, text, now, reply_to, reply_context, *, allow_tree_panel_claim=True):
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0:
        return False
    if not _is_identity_owner_event(event, routed_identity_id):
        # 非属主 client 只能弃权，不能把 routed reply 误标为已处理。
        return False

    already_consumed = bool(matched_family) and _has_runtime_message_consumed(event, matched_family)
    with use_identity(routed_identity_id):
        is_reply_to_me = is_reply_to_identity_message(reply_to, routed_identity_id)
        clear_result = clear_pending_by_reply(reply_to, routed_identity_id, reply_context=reply_context)
        root_msg_id = int((reply_context or {}).get("root_msg_id") or (clear_result or {}).get("reply_to_msg_id") or 0)
        if root_msg_id <= 0:
            root_msg_id = int(getattr(reply_to, "id", 0) or 0)
        if matched_family:
            track_reply_chain_message(event.id, routed_identity_id, matched_family, root_msg_id=root_msg_id)

        handled_any = False
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

        if not already_consumed and matched_family != "stargazer_sync":
            handled_any = await handle_tree_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_checkin_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_sect_teach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tower_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_guide_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_soothe_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_collect_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tianti_reply(text, now, reply_to, matched_family=matched_family) or handled_any
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

    # bot 静默监测：记录 . 开头指令的触发时间
    global _bot_silence_triggered_at
    raw_text = (event.raw_text or "").strip()
    if raw_text.startswith(".") and event.sender_id not in set(get_game_bot_ids()):
        if _bot_silence_triggered_at <= 0 and get_global_enabled():
            _bot_silence_triggered_at = time.time()

    if event.sender_id not in set(get_game_bot_ids()):
        now = time.time()
        text = event.raw_text or ""
        try:
            if _claim_runtime_event(event, scope="guanxing_external_shift"):
                await handle_guanxing_external_shift_command(text, now, event)
        except Exception:
            print(traceback.format_exc())
        return

    # bot 静默监测：bot 有发言，重置触发状态
    global _bot_last_seen_at, _bot_silence_auto_paused
    _bot_last_seen_at = time.time()
    _bot_silence_triggered_at = 0
    if _bot_silence_auto_paused:
        _bot_silence_auto_paused = False
        if not get_global_enabled():
            await toggle_global_enabled(True, source="bot_silence_recovery")

    now = time.time()
    text = event.raw_text or ""

    try:
        reply_to, reply_context = await _resolve_event_reply(event)

        await _dispatch_new_message_broadcasts(event, text, now, reply_to=reply_to)

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

    except Exception:
        print(traceback.format_exc())


@client.on(events.MessageEdited())
async def on_message_edited(event):
    _append_game_group_message_log(event, event_type="edit")

    if event.chat_id != get_game_group_id():
        return
    if event.sender_id not in set(get_game_bot_ids()):
        return

    now = time.time()
    text = event.raw_text or ""

    try:
        reply_to, reply_context = await _resolve_event_reply(event)

        await _dispatch_message_edited_realm_breakthrough(event, text, now)

        if int((reply_context or {}).get("send_as_id") or 0) > 0:
            handled_reply = await _handle_routed_reply_event(
                event,
                text,
                now,
                reply_to,
                reply_context,
            )
            if handled_reply:
                return

        await _dispatch_message_edited_tree_panel(event, text, now)
        await _dispatch_message_edited_stargazer_panel(event, text, now)
        await _dispatch_message_edited_guanxing_monitor(event, text, now)
    except Exception:
        print(traceback.format_exc())


def _register_event_handlers(tc):
    tc.add_event_handler(on_message, events.NewMessage())
    tc.add_event_handler(on_message_edited, events.MessageEdited())


async def bootstrap():
    # 主 client 仅 connect：有 session 时自动恢复认证，无 session 时等待 UI 登录
    await client.connect()
    loaded = load_state()

    # 启动已保存的额外账号 client
    failed_accounts = []
    for acct_id_str, acct_info in get_accounts().items():
        try:
            acct_id = int(acct_id_str)
            tc = create_account_client(acct_id)
            await tc.start()
            try:
                await tc.get_dialogs()
            except Exception:
                pass
            register_client(acct_id, tc)
            _register_event_handlers(tc)
        except Exception:
            error_text = traceback.format_exc().strip().splitlines()[-1] if traceback.format_exc().strip() else "启动失败"
            failed_accounts.append({"account_id": int(acct_id_str), "error": error_text})
            print(f"启动额外账号 {acct_id_str} 失败: {traceback.format_exc()}")

    await start_ui_server()

    # 获取 my_user_id：优先主 client，再尝试已登录账号
    try:
        _me = await client.get_me()
        if _me:
            state["my_user_id"] = _me.id
    except Exception:
        pass
    if not state.get("my_user_id"):
        for _acct_id_str in get_accounts():
            try:
                _tc = get_client(int(_acct_id_str))
                _me = await _tc.get_me()
                if _me:
                    state["my_user_id"] = _me.id
                    break
            except Exception:
                pass

    identity_ids = get_identity_ids()
    startup_account_check_result = run_startup_account_integrity_check(identity_ids, failed_accounts)
    for send_as_id in identity_ids:
        try:
            account_id = get_identity_account(send_as_id)
            if not account_id:
                print(f"hydrate_identity_profile skipped (no account): {send_as_id}")
                continue
            tc = get_client(account_id)
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
        save_state()
    else:
        for identity_id in identity_ids:
            if not get_identity_enabled(identity_id):
                continue
            with use_identity(identity_id):
                if state["tree_enabled"] and (state["is_maturing"] or state["is_invading"] or state["pending_irrigation"]):
                    state["tree_bootstrap_check_needed"] = True
                    mark_dirty()
                if state["checkin_enabled"]:
                    day_key = get_checkin_day_key(now)
                    if state["checkin_teach_day"] != day_key:
                        reset_checkin_daily_state(now)
                        mark_dirty()
                    if state["last_checkin_done_day"] == day_key and state["next_checkin_time"] <= now:
                        schedule_next_checkin_after_completion(now, persist=False)
                        mark_dirty()
                    elif state["next_checkin_time"] <= 0:
                        schedule_next_checkin(now, persist=False)
                        mark_dirty()
                if state["tower_enabled"]:
                    day_key = get_day_key(now)
                    if state["last_tower_day"] == day_key and state["next_tower_time"] <= now:
                        schedule_next_tower_after_completion(now, persist=False)
                        mark_dirty()
                    elif state["last_tower_day"] != day_key and state["next_tower_time"] <= 0:
                        schedule_next_tower(now, persist=False)
                        mark_dirty()
                if state["deep_retreat_enabled"] and state["next_deep_retreat_time"] <= 0:
                    state["next_deep_retreat_time"] = now + 1
                    mark_dirty()
                if state["yuanying_enabled"] and state["next_yuanying_time"] <= 0:
                    state["next_yuanying_time"] = now + 1
                    mark_dirty()
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
    await send_audit_log("\n".join(audit_lines), scope="global", limit=1200)


async def main_loop():
    while True:
        now = time.time()

        gc_my_msg_ids(now)
        gc_ui_login_tokens(now)
        gc_ui_sessions(now)
        flush_if_dirty(now)
        await run_retry_scheduler(now)
        await run_identity_info_followup_scheduler(now)

        # bot 静默监测：触发后超时且 bot 无发言，自动全局暂停
        global _bot_silence_triggered_at, _bot_silence_auto_paused
        if (
            _bot_silence_triggered_at > 0
            and now - _bot_silence_triggered_at >= BOT_SILENCE_TIMEOUT_SEC
            and _bot_last_seen_at < _bot_silence_triggered_at
            and get_global_enabled()
        ):
            _bot_silence_triggered_at = 0
            _bot_silence_auto_paused = True
            await toggle_global_enabled(False, source="bot_silence_monitor")
        if not get_global_enabled():
            await asyncio.sleep(5)
            continue

        await _run_global_schedulers(now)
        await _run_identity_schedulers(now)
        await run_quiz_learning_scheduler(now)
        await asyncio.sleep(5)


async def main():
    await bootstrap()
    await main_loop()


__all__ = ["bootstrap", "main", "main_loop", "on_message"]
