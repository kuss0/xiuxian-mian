import asyncio
import json
import time
import traceback
from datetime import datetime

from telethon import events

from .config import BOT_SILENCE_TIMEOUT_SEC, MESSAGES_DIR, TZ_LOCAL, client, create_account_client, get_all_clients, get_client, register_client
from .control import enforce_identity_module_availability, handle_identity_info_reply, handle_log_group_command, handle_realm_breakthrough_broadcast, hydrate_identity_profile, initialize_identity_runtime, run_identity_info_followup_scheduler, scan_startup_timeout_tasks, toggle_global_enabled
from .features.checkin import handle_checkin_reply, handle_sect_teach_reply, run_checkin_scheduler
from .features.deep_retreat import (
    get_deep_retreat_phase_text,
    handle_deep_retreat_running_reply,
    handle_deep_retreat_status_reply,
    handle_deep_retreat_success_reply,
    handle_deep_retreat_summary_broadcast,
    run_deep_retreat_scheduler,
)
from .features.pet import handle_pet_cd_fix, run_pet_scheduler
from .features.quiz import handle_quiz_learning_prompt, handle_quiz_prompt, handle_quiz_result_broadcast, run_quiz_learning_scheduler, run_quiz_scheduler
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
    get_yuanying_phase_text,
    handle_yuanying_running_reply,
    handle_yuanying_status_reply,
    handle_yuanying_success_reply,
    handle_yuanying_summary_broadcast,
    run_yuanying_scheduler,
)
from .persistence import flush_if_dirty, load_state, mark_dirty, save_state
from .runtime import (
    clear_pending_by_reply,
    find_identity_by_msg_id,
    gc_my_msg_ids,
    gc_ui_login_tokens,
    gc_ui_sessions,
    is_reply_to_identity_message,
    run_retry_scheduler,
    schedule_cleanup,
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
    get_send_as_label,
    get_send_as_profile,
    state,
    use_identity,
)
from .timing import (
    fmt_abs_ts,
    fmt_remaining,
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


def _append_game_group_message_log(event, *, event_type="message"):
    if event.chat_id != get_game_group_id():
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


@client.on(events.NewMessage())
async def on_message(event):
    _append_game_group_message_log(event, event_type="message")

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
        return

    # bot 静默监测：bot 有发言，重置触发状态
    global _bot_last_seen_at
    _bot_last_seen_at = time.time()
    _bot_silence_triggered_at = 0

    now = time.time()
    text = event.raw_text or ""

    try:
        reply_to = await event.get_reply_message()
        routed_identity_id = find_identity_by_msg_id(reply_to.id) if reply_to else None

        await handle_deep_retreat_summary_broadcast(text, now)
        await handle_yuanying_summary_broadcast(text, now)
        await handle_realm_breakthrough_broadcast(text, now)
        await handle_quiz_result_broadcast(text, now)
        await handle_quiz_learning_prompt(text, now, event)

        handled_quiz_prompt = False
        for identity_id in get_identity_ids():
            if not get_identity_enabled(identity_id):
                continue
            with use_identity(identity_id):
                if await handle_quiz_prompt(text, now, event):
                    handled_quiz_prompt = True
                    break
        if handled_quiz_prompt:
            return

        if routed_identity_id is not None:
            with use_identity(routed_identity_id):
                is_reply_to_me = is_reply_to_identity_message(reply_to, routed_identity_id)
                clear_pending_by_reply(reply_to, routed_identity_id)

                await handle_tree_invasion_end(text, now, is_reply_to_me)
                await handle_tree_invasion_start(text, now)
                await handle_tree_rebirth_reset(text, now)
                await handle_tree_panel(text, now, is_reply_to_me)

                if is_reply_to_me:
                    await handle_tree_cd_fix(text, now, reply_to)
                    await handle_pet_cd_fix(text, now, reply_to)
                    await handle_checkin_reply(text, now, reply_to)
                    await handle_sect_teach_reply(text, now, reply_to)
                    await handle_tower_reply(text, now, reply_to)
                    await handle_identity_info_reply(text, now, reply_to, event.id)
                    deep_retreat_done = await handle_deep_retreat_success_reply(text, now, reply_to)
                    if not deep_retreat_done:
                        deep_retreat_done = await handle_deep_retreat_running_reply(text, now, reply_to)
                    if not deep_retreat_done:
                        await handle_deep_retreat_status_reply(text, now, reply_to)
                    yuanying_done = await handle_yuanying_success_reply(text, now, reply_to)
                    if not yuanying_done:
                        yuanying_done = await handle_yuanying_running_reply(text, now, reply_to)
                    if not yuanying_done:
                        await handle_yuanying_status_reply(text, now, reply_to)
                    await handle_tree_exception_prompt(text)

            await schedule_cleanup(reply_to, send_as_id=routed_identity_id)
            return

        for identity_id in get_identity_ids():
            with use_identity(identity_id):
                await handle_tree_invasion_end(text, now, False)
                await handle_tree_invasion_start(text, now)
                await handle_tree_rebirth_reset(text, now)

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
        reply_to = await event.get_reply_message()
        routed_identity_id = find_identity_by_msg_id(reply_to.id) if reply_to else None

        await handle_realm_breakthrough_broadcast(text, now)

        if routed_identity_id is None:
            return
        with use_identity(routed_identity_id):
            is_reply_to_me = is_reply_to_identity_message(reply_to, routed_identity_id)
            if not is_reply_to_me:
                return
            await handle_identity_info_reply(text, now, reply_to, event.id)
    except Exception:
        print(traceback.format_exc())


def _register_event_handlers(tc):
    tc.add_event_handler(on_message, events.NewMessage())
    tc.add_event_handler(on_message_edited, events.MessageEdited())


async def bootstrap():
    # 主 client 仅连接不认证，所有账号通过 UI 登录
    await client.connect()
    loaded = load_state()

    # 启动已保存的额外账号 client
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
            print(f"启动额外账号 {acct_id_str} 失败: {traceback.format_exc()}")

    await start_ui_server()
    me = await client.get_me()
    if me:
        state["my_user_id"] = me.id

    identity_ids = get_identity_ids()
    for send_as_id in identity_ids:
        try:
            account_id = get_identity_account(send_as_id)
            tc = get_client(account_id) if account_id else client
            send_as_entity = await tc.get_entity(send_as_id)
            hydrate_identity_profile(send_as_entity)
        except Exception:
            print(f"hydrate_identity_profile failed: {send_as_id}")
        enforce_identity_module_availability(send_as_id, persist=False)

    now = time.time()
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

    identity_lines = [f"- {send_as_id}: @{get_send_as_profile(send_as_id).get('username') or '未获取到'}" for send_as_id in identity_ids]
    recovery_text = "成功" if any_loaded else ("无待恢复任务" if loaded else "首次初始化")
    audit_lines = [
        "🚀 自动化系统启动成功",
        f"👤 账号: {me.first_name if me else '未登录（等待 UI 登录）'}",
        f"🎭 并发身份数: {len(identity_ids)}",
        "📡 模式: 多 SEND_AS_ID + SQLite 持久化",
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
    await send_audit_log("\n".join(audit_lines))


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
        global _bot_silence_triggered_at
        if (
            _bot_silence_triggered_at > 0
            and now - _bot_silence_triggered_at >= BOT_SILENCE_TIMEOUT_SEC
            and _bot_last_seen_at < _bot_silence_triggered_at
            and get_global_enabled()
        ):
            _bot_silence_triggered_at = 0
            await toggle_global_enabled(False, source="bot_silence_monitor")
        if not get_global_enabled():
            await asyncio.sleep(5)
            continue
        for identity_id in get_identity_ids():
            if not get_identity_enabled(identity_id):
                continue
            with use_identity(identity_id):
                await run_tree_bootstrap_check(now)
                await run_tree_scheduler(now)
                await run_pet_scheduler(now)
                await run_quiz_scheduler(now)
                await run_checkin_scheduler(now)
                await run_tower_scheduler(now)
                await run_deep_retreat_scheduler(now)
                await run_yuanying_scheduler(now)

        await run_quiz_learning_scheduler(now)
        await asyncio.sleep(5)


async def main():
    await bootstrap()
    await main_loop()


__all__ = ["bootstrap", "main", "main_loop", "on_message"]
