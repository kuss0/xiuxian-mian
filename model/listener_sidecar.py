import asyncio
import json
import os
import signal
import sqlite3
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
    SESSION_DIR,
    STATE_DIR,
    _create_telegram_client,
    client,
    get_registered_client,
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
LISTENER_ACCOUNT_RETRY_INTERVAL_SEC = 60

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
    "target_accounts": [],
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


def _session_file(session_base):
    return Path(f"{session_base}.session")


def _listener_session_base(account_id):
    return os.path.join(SESSION_DIR, f"listener_account_{int(account_id)}")


def _source_session_base(account_id):
    return os.path.join(SESSION_DIR, f"account_{int(account_id)}")


def _backup_sqlite_session(src_file, dst_file):
    tmp_file = Path(f"{dst_file}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        with sqlite3.connect(f"file:{Path(src_file).as_posix()}?mode=ro", uri=True, timeout=5.0) as src_conn:
            with sqlite3.connect(str(tmp_file), timeout=5.0) as dst_conn:
                src_conn.backup(dst_conn)
        os.chmod(tmp_file, 0o600)
        os.replace(tmp_file, dst_file)
    finally:
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass


def _ensure_listener_session_copy(account_id):
    source_base = _source_session_base(account_id)
    listener_base = _listener_session_base(account_id)
    source_file = _session_file(source_base)
    listener_file = _session_file(listener_base)
    if listener_file.exists():
        return listener_base
    if not source_file.exists():
        raise RuntimeError(f"源 session 不存在: {source_file}")
    listener_file.parent.mkdir(parents=True, exist_ok=True)
    _backup_sqlite_session(source_file, listener_file)
    return listener_base


def _create_listener_account_client(account_id, *, api_id=None, api_hash=None):
    session_base = _ensure_listener_session_copy(account_id)
    return _create_telegram_client(session_base, api_id=api_id, api_hash=api_hash)


def _remove_failed_account(account_id):
    account_id = int(account_id)
    _listener_stats["failed_accounts"] = [
        item
        for item in (_listener_stats.get("failed_accounts") or [])
        if int((item or {}).get("account_id") or 0) != account_id
    ]


def _record_failed_account(account_id, error):
    account_id = int(account_id)
    _remove_failed_account(account_id)
    _listener_stats.setdefault("failed_accounts", []).append({
        "account_id": account_id,
        "error": str(error or "启动失败")[:300],
        "last_failed_at": time.time(),
        "last_failed_at_text": _local_ts(),
    })


async def _connect_listener_account(account_id, acct_info):
    account_id = int(account_id)
    tc = None
    try:
        tc = _create_listener_account_client(
            account_id,
            api_id=(acct_info or {}).get("api_id"),
            api_hash=(acct_info or {}).get("api_hash"),
        )
        await tc.connect()
        if not await tc.is_user_authorized():
            listener_file = _session_file(_listener_session_base(account_id))
            if listener_file.exists():
                listener_file.unlink()
            raise RuntimeError("listener session 未授权，已删除副本，等待下轮从源 session 重建")
        register_client(account_id, tc)
        _register_listener_handlers(tc)
        registered = set(int(item) for item in (_listener_stats.get("registered_accounts") or []))
        registered.add(account_id)
        _listener_stats["registered_accounts"] = sorted(registered)
        _remove_failed_account(account_id)
        print(f"listener account connected: {account_id}", flush=True)
        return True
    except Exception as exc:
        reason = str(exc) or "启动失败"
        mark_account_offline(account_id, reason)
        _record_failed_account(account_id, reason)
        if tc is not None:
            try:
                await tc.disconnect()
            except Exception:
                pass
        return False


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
            if await _connect_listener_account(account_id, acct_info):
                connected.append(int(account_id))

    _listener_stats["target_accounts"] = account_ids
    _listener_stats["registered_accounts"] = connected
    _write_listener_heartbeat({"status": "running"})
    if not connected:
        raise RuntimeError("没有可用监听账号。")
    print(f"listener sidecar started: accounts={connected} failed={_listener_stats.get('failed_accounts')}", flush=True)
    return connected


async def _retry_failed_accounts_loop(stop_event, accounts):
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LISTENER_ACCOUNT_RETRY_INTERVAL_SEC)
            break
        except asyncio.TimeoutError:
            pass
        registered = set(int(item) for item in (_listener_stats.get("registered_accounts") or []))
        for account_id in list(_listener_stats.get("target_accounts") or []):
            account_id = int(account_id)
            if account_id in registered:
                continue
            await _connect_listener_account(account_id, accounts.get(str(account_id)) or {})
        _write_listener_heartbeat({"status": "running"})


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
    retry_task = None
    try:
        await _connect_saved_accounts()
        heartbeat_task = asyncio.create_task(_heartbeat_loop(stop_event))
        retry_task = asyncio.create_task(_retry_failed_accounts_loop(stop_event, get_accounts()))
        await stop_event.wait()
    finally:
        if retry_task and not retry_task.done():
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await shutdown()
