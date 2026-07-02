import asyncio
import json
import os
import signal
import time
import traceback
from pathlib import Path

from telethon import events

from .app_message_log import (
    _append_game_group_message_log,
    _append_replica_dispatch_group_message_log,
    _append_replica_group_message_log,
)
from .config import (
    STATE_DIR,
    client,
    create_account_client,
    get_registered_client,
    is_account_offline,
    mark_account_offline,
    register_client,
    unregister_client,
)
from .persistence import has_persisted_identity_rows, load_state
from .state import (
    get_accounts,
    get_game_listener_account_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_listener_account_map,
    state,
)

LISTENER_HEARTBEAT_FILE = os.path.join(STATE_DIR, "listener_heartbeat.json")
LISTENER_HEARTBEAT_INTERVAL_SEC = 15

_listener_stats = {
    "started_at": time.time(),
    "last_event_at": 0.0,
    "last_event_type": "",
    "last_chat_id": 0,
    "last_message_id": 0,
    "message_count": 0,
    "edit_count": 0,
    "registered_accounts": [],
    "failed_accounts": [],
}


def _local_ts(epoch=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch or time.time())))


def _write_listener_heartbeat(extra=None):
    payload = dict(_listener_stats)
    payload["pid"] = os.getpid()
    payload["updated_at"] = time.time()
    payload["updated_at_text"] = _local_ts(payload["updated_at"])
    payload["started_at_text"] = _local_ts(payload.get("started_at"))
    if payload.get("last_event_at"):
        payload["last_event_at_text"] = _local_ts(payload.get("last_event_at"))
    if extra:
        payload.update(extra)
    path = Path(LISTENER_HEARTBEAT_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        print(traceback.format_exc(), flush=True)


def _listener_account_ids(accounts):
    account_ids = {int(account_id) for account_id in accounts.keys() if str(account_id).isdigit()}
    configured_ids = set(get_game_listener_account_ids())
    for raw_value in list((get_replica_listener_account_map() or {}).values()) + list((get_replica_dispatch_listener_account_map() or {}).values()):
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            configured_ids.add(value)
    selected = sorted(account_id for account_id in configured_ids if account_id in account_ids)
    if selected:
        return selected
    return sorted(account_ids)


async def _handle_listener_event(event, event_type):
    try:
        if event_type == "message":
            _listener_stats["message_count"] = int(_listener_stats.get("message_count") or 0) + 1
        elif event_type == "edit":
            _listener_stats["edit_count"] = int(_listener_stats.get("edit_count") or 0) + 1
        _listener_stats["last_event_at"] = time.time()
        _listener_stats["last_event_type"] = event_type
        _listener_stats["last_chat_id"] = int(getattr(event, "chat_id", 0) or 0)
        _listener_stats["last_message_id"] = int(getattr(event, "id", 0) or 0)

        if _append_replica_group_message_log(event, event_type=event_type):
            _write_listener_heartbeat()
            return
        if _append_replica_dispatch_group_message_log(event, event_type=event_type):
            _write_listener_heartbeat()
            return
        _append_game_group_message_log(event, event_type=event_type)
        _write_listener_heartbeat()
    except Exception:
        print(traceback.format_exc(), flush=True)
        _write_listener_heartbeat({"last_error": traceback.format_exc()[-1000:]})


def _register_listener_handlers(tc):
    async def on_new_message(event):
        await _handle_listener_event(event, "message")

    async def on_message_edited(event):
        await _handle_listener_event(event, "edit")

    tc.add_event_handler(on_new_message, events.NewMessage())
    tc.add_event_handler(on_message_edited, events.MessageEdited())


async def _connect_saved_accounts():
    loaded = load_state()
    if not loaded and has_persisted_identity_rows():
        raise RuntimeError("SQLite 状态加载失败，已阻止 listener 启动。")

    accounts = get_accounts()
    account_ids = _listener_account_ids(accounts)
    failed_accounts = []
    connected = []
    if not accounts:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("主 session 未授权，listener 无法启动。")
        me = await client.get_me()
        if me:
            state["my_user_id"] = me.id
            register_client(me.id, client)
            connected.append(int(me.id))
        _register_listener_handlers(client)
    else:
        for account_id in account_ids:
            acct_info = accounts.get(str(account_id)) or {}
            tc = None
            try:
                if is_account_offline(account_id):
                    continue
                tc = create_account_client(
                    account_id,
                    api_id=acct_info.get("api_id"),
                    api_hash=acct_info.get("api_hash"),
                )
                await tc.connect()
                if not await tc.is_user_authorized():
                    raise RuntimeError("session 未授权")
                register_client(account_id, tc)
                _register_listener_handlers(tc)
                connected.append(int(account_id))
            except Exception as exc:
                reason = str(exc) or "启动失败"
                mark_account_offline(account_id, reason)
                failed_accounts.append({"account_id": int(account_id), "error": reason[:300]})
                if tc is not None:
                    try:
                        await tc.disconnect()
                    except Exception:
                        pass

    _listener_stats["registered_accounts"] = connected
    _listener_stats["failed_accounts"] = failed_accounts
    _write_listener_heartbeat({"status": "running"})
    if not connected:
        raise RuntimeError("没有可用监听账号。")
    print(f"listener sidecar started: accounts={connected} failed={failed_accounts}", flush=True)
    return connected


async def _heartbeat_loop(stop_event):
    while not stop_event.is_set():
        _write_listener_heartbeat({"status": "running"})
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LISTENER_HEARTBEAT_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass


async def shutdown():
    for account_id in list(_listener_stats.get("registered_accounts") or []):
        try:
            tc = get_registered_client(account_id)
            if tc is not None:
                await tc.disconnect()
            unregister_client(account_id)
        except Exception:
            print(traceback.format_exc(), flush=True)
    if client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass
    _write_listener_heartbeat({"status": "stopped"})


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

    heartbeat_task = None
    try:
        await _connect_saved_accounts()
        heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event))
        await stop_event.wait()
    finally:
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await shutdown()
