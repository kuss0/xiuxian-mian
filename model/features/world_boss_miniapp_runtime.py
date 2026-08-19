"""Runtime bridge for serial World Boss MiniApp automation."""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from pathlib import Path

import requests
from telethon import functions

from ..config import MESSAGES_DIR, TG_REQUESTS_PROXIES
from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..state import get_game_bot_ids, get_identity_account
from ..timing import get_day_key
from ..webapp_core import (
    MiniAppCaptureStore,
    begin_miniapp_priority_window,
    build_miniapp_launch_request,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    iter_webapp_entry_links,
    end_miniapp_priority_window,
    safe_miniapp_event_detail,
    sanitize_webapp_secret_text,
)
from .world_boss_miniapp import (
    WORLD_BOSS_JOIN_WINDOW_SEC,
    build_world_boss_miniapp_request,
    build_world_boss_miniapp_adapter,
    build_world_boss_websocket_urls,
    decode_world_boss_websocket_message,
    join_world_boss_miniapp_lab,
    run_world_boss_joined_battle_lab_flow,
)

try:
    from websockets.asyncio.client import connect as _websocket_connect
except ImportError:  # pragma: no cover - optional production dependency
    _websocket_connect = None


WORLD_BOSS_MINIAPP_HTTP_TIMEOUT = (5, 20)
WORLD_BOSS_MINIAPP_CAPTURE_DIR = Path(MESSAGES_DIR) / "miniapp-captures"
WORLD_BOSS_MINIAPP_WS_CONNECT_TIMEOUT_SEC = 8.0
WORLD_BOSS_MINIAPP_WS_MESSAGE_TIMEOUT_SEC = 5.0
WORLD_BOSS_MINIAPP_WS_RECONNECT_SEC = 5.0
WORLD_BOSS_TOKEN_READY_RETRY_DELAYS_SEC = (1.0, 2.0, 3.0)
# The event can be killed by other players before the local 16-window timeline
# ends.  Leave two tail windows unused so every account has time to submit its
# own final settlement while preserving per-identity low-profile adjustments.
WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS = 2


def _world_boss_websocket_proxy():
    return str(
        (TG_REQUESTS_PROXIES or {}).get("https")
        or (TG_REQUESTS_PROXIES or {}).get("http")
        or ""
    ).strip() or None


class _WorldBossRealtimeFeed:
    """Optional WebSocket wake-up feed; HTTP remains authoritative."""

    def __init__(
        self,
        *,
        identity_id,
        token,
        init_data,
        transport,
        capture_sink=None,
        connector=None,
    ):
        self.identity_id = int(identity_id or 0)
        self.token = str(token or "").strip()
        self.init_data = str(init_data or "")
        self.transport = transport
        self.capture_sink = capture_sink
        self.connector = connector if connector is not None else _websocket_connect
        self.connected = False
        self.last_error = ""
        self.reconnect_count = 0
        self._latest_boss = {}
        self._last_signal = None
        self._latest_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._closed = asyncio.Event()
        self._task = None

    async def start(self):
        if not self.connector or not self.token or not self.init_data or self.transport is None:
            return False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return True

    async def close(self):
        self._closed.set()
        self._wake_event.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def latest_boss(self):
        with self._latest_lock:
            return dict(self._latest_boss)

    def safe_summary(self):
        return {
            "available": self.connector is not None,
            "connected": bool(self.connected),
            "reconnect_count": int(self.reconnect_count or 0),
            "has_state": bool(self.latest_boss()),
            "last_error": sanitize_webapp_secret_text(self.last_error),
        }

    def wait_for_update(self, timeout_sec):
        signaled = self._wake_event.wait(max(0.0, float(timeout_sec or 0)))
        if signaled:
            self._wake_event.clear()
        return bool(signaled)

    def _publish(self, boss):
        if not isinstance(boss, dict):
            return
        signal = (
            str(boss.get("eventStatus") or ""),
            str(boss.get("roomStatus") or ""),
            bool(boss.get("battleLocked")),
            str(boss.get("phase") or ""),
            str(boss.get("failureReason") or ""),
        )
        with self._latest_lock:
            self._latest_boss = dict(boss)
            if signal == self._last_signal:
                return
            self._last_signal = signal
        self._wake_event.set()

    async def _request_ticket(self):
        request = build_world_boss_miniapp_request(
            "ws_ticket",
            token=self.token,
            init_data=self.init_data,
        )
        return await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            self.transport,
            backoff_sec=(),
            capture_sink=self.capture_sink,
            capture_source=f"world_boss:ws_ticket:{self.identity_id}",
            step_key="ws_ticket",
        )

    async def _run(self):
        first_connection = True
        while not self._closed.is_set():
            try:
                ticket_result = await self._request_ticket()
                if not ticket_result.ok:
                    self.last_error = str(
                        sanitize_webapp_secret_text(ticket_result.error) or "ws ticket failed"
                    )
                    try:
                        await asyncio.wait_for(
                            self._closed.wait(),
                            timeout=WORLD_BOSS_MINIAPP_WS_RECONNECT_SEC,
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue
                urls = build_world_boss_websocket_urls(ticket_result.data)
                connection_error = None
                for url in urls:
                    try:
                        async with self.connector(
                            url,
                            proxy=_world_boss_websocket_proxy(),
                            open_timeout=WORLD_BOSS_MINIAPP_WS_CONNECT_TIMEOUT_SEC,
                            close_timeout=3,
                            ping_interval=20,
                            ping_timeout=20,
                            max_size=2 * 1024 * 1024,
                        ) as websocket:
                            self.connected = True
                            self.last_error = ""
                            if not first_connection:
                                self.reconnect_count += 1
                            first_connection = False
                            while not self._closed.is_set():
                                raw_message = await asyncio.wait_for(
                                    websocket.recv(),
                                    timeout=WORLD_BOSS_MINIAPP_WS_MESSAGE_TIMEOUT_SEC,
                                )
                                message = decode_world_boss_websocket_message(raw_message)
                                if message.get("type") == "ping":
                                    await websocket.send('{"type":"pong"}')
                                    continue
                                self._publish(message.get("boss"))
                        connection_error = None
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        connection_error = exc
                        self.connected = False
                        self._wake_event.set()
                        continue
                if connection_error is not None:
                    raise connection_error
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self.last_error = "world boss WebSocket state timeout"
            except Exception as exc:
                self.last_error = str(
                    sanitize_webapp_secret_text(exc) or "world boss WebSocket failed"
                )
            finally:
                self.connected = False
                self._wake_event.set()
            if self._closed.is_set():
                return
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=WORLD_BOSS_MINIAPP_WS_RECONNECT_SEC)
            except asyncio.TimeoutError:
                pass


def extract_world_boss_miniapp_launch(event, *, message_text=""):
    adapter = build_world_boss_miniapp_adapter()
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    sender = getattr(event, "sender", None)
    sender_username = str(
        getattr(sender, "username", "")
        or getattr(event, "sender_username", "")
        or ""
    ).strip().lstrip("@")
    sender_is_official_bot = bool(
        (
            getattr(event, "_xiuxian_sender_is_game_bot", False)
            or sender_id in set(get_game_bot_ids())
        )
        and getattr(sender, "bot", getattr(event, "sender_is_bot", False))
        and re.fullmatch(r"[A-Za-z0-9_]{5,64}_bot", sender_username, flags=re.IGNORECASE)
    )
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
        if not url:
            continue
        dynamic_bot_verified = False
        launch = build_miniapp_launch_request(adapter, url)
        if (
            not launch.allowed
            and launch.reason == "bot username not allowed"
            and sender_is_official_bot
            and launch.bot_username.casefold() == sender_username.casefold()
        ):
            launch = build_miniapp_launch_request(
                build_world_boss_miniapp_adapter(bot_username=sender_username),
                url,
            )
            dynamic_bot_verified = bool(launch.allowed)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": url,
                "button_text": str(button_text or ""),
                "bot_username": launch.bot_username or adapter.bot_username,
                "dynamic_bot_verified": dynamic_bot_verified,
                "safe_summary": launch.safe_summary(),
            }
    return {}


async def request_world_boss_miniapp_init_data(identity_id, launch):
    launch = dict(launch or {})
    adapter = build_world_boss_miniapp_adapter()
    request = build_miniapp_launch_request(
        adapter,
        launch.get("webview_url") or "",
        start_param=launch.get("token") or "",
        bot_username=launch.get("bot_username") or "",
    )
    if (
        not request.allowed
        and request.reason == "bot username not allowed"
        and launch.get("dynamic_bot_verified")
        and launch.get("bot_username")
    ):
        adapter = build_world_boss_miniapp_adapter(bot_username=launch["bot_username"])
        request = build_miniapp_launch_request(
            adapter,
            launch.get("webview_url") or "",
            start_param=launch.get("token") or "",
            bot_username=launch.get("bot_username") or "",
        )
    if not request.allowed:
        raise ValueError(request.reason or "world boss MiniApp launch not allowed")
    account_id, client = _get_identity_client_with_account(identity_id)
    if client is None:
        raise RuntimeError("身份客户端不可用")
    async with account_rpc_slot(account_id=account_id, client_obj=client):
        bot = await client.get_entity(request.bot_username or adapter.bot_username)
        bot_input = await client.get_input_entity(bot)
        result = await client(functions.messages.RequestMainWebViewRequest(
            peer=bot_input,
            bot=bot_input,
            platform=request.platform or adapter.platform,
            start_param=request.start_param,
        ))
    init_data = extract_miniapp_init_data_from_url(getattr(result, "url", "") or "")
    if not init_data:
        raise RuntimeError("WebView URL 缺少 tgWebAppData")
    return init_data


def _requests_transport(request, *, session=None):
    requester = session.request if session is not None else requests.request
    return requester(
        str(request.get("method") or "POST"),
        request["url"],
        json=request.get("payload") or {},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            **dict(request.get("headers") or {}),
        },
        proxies=TG_REQUESTS_PROXIES,
        timeout=WORLD_BOSS_MINIAPP_HTTP_TIMEOUT,
    )


def _capture_store(now):
    path = WORLD_BOSS_MINIAPP_CAPTURE_DIR / f"world_boss-{get_day_key(now)}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


async def _emit_progress(callback, item):
    if callback is None:
        return
    result = callback(dict(item or {}))
    if inspect.isawaitable(result):
        await result


async def run_world_boss_miniapp_event(
    identity_ids,
    event,
    *,
    message_text="",
    opened_at=None,
    account_gap_sec=0,
    battle_priority_gap_sec=0,
    transport=None,
    init_data_provider=None,
    progress_callback=None,
    window_skip_by_identity=None,
):
    """Join accounts in parallel, then run one serial timeline per account."""

    now = float(opened_at or time.time())
    launch = extract_world_boss_miniapp_launch(event, message_text=message_text)
    if not launch:
        return {"ok": False, "status": "entry_missing", "joined_count": 0, "results": []}
    shared_transport = transport
    init_data_provider = init_data_provider or request_world_boss_miniapp_init_data
    capture_sink = _capture_store(now)
    raw_window_skips = window_skip_by_identity if isinstance(window_skip_by_identity, dict) else {}
    try:
        battle_priority_gap_sec = max(0.0, min(0.75, float(battle_priority_gap_sec or 0)))
    except (TypeError, ValueError, OverflowError):
        battle_priority_gap_sec = 0.0

    def window_skip_for(identity_id):
        raw_value = raw_window_skips.get(identity_id, raw_window_skips.get(str(identity_id), 0))
        try:
            identity_extra = max(0, min(32, int(raw_value or 0)))
        except (TypeError, ValueError, OverflowError):
            identity_extra = 0
        total = min(32, WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS + identity_extra)
        return total, identity_extra

    async def join_one(raw_identity_id):
        identity_id = int(raw_identity_id or 0)
        if identity_id <= 0:
            return None, None
        if time.time() - now > WORLD_BOSS_JOIN_WINDOW_SEC:
            join_result = {
                "identity_id": identity_id,
                "phase": "join",
                "ok": False,
                "status": "join_deadline_exceeded",
                "error": "world boss join window exceeded",
            }
            await _emit_progress(progress_callback, join_result)
            return None, join_result
        session = None
        identity_transport = shared_transport
        if identity_transport is None:
            session = requests.Session()

            def identity_transport(request):
                return _requests_transport(request, session=session)

        try:
            init_data = await init_data_provider(identity_id, launch)
            receipt = None
            for attempt in range(len(WORLD_BOSS_TOKEN_READY_RETRY_DELAYS_SEC) + 1):
                receipt = await asyncio.to_thread(
                    join_world_boss_miniapp_lab,
                    token=launch["token"],
                    init_data=init_data,
                    player_id=identity_id,
                    identity_id=identity_id,
                    account_id=get_identity_account(identity_id),
                    transport=identity_transport,
                    capture_sink=capture_sink,
                    capture_source=f"world_boss:join:{identity_id}",
                )
                if receipt.joined or str(receipt.status or "") != "boss_token_missing":
                    break
                if attempt >= len(WORLD_BOSS_TOKEN_READY_RETRY_DELAYS_SEC):
                    break
                retry_delay = WORLD_BOSS_TOKEN_READY_RETRY_DELAYS_SEC[attempt]
                if time.time() + retry_delay - now > WORLD_BOSS_JOIN_WINDOW_SEC:
                    break
                await asyncio.sleep(retry_delay)
        except Exception as exc:
            if session is not None:
                session.close()
            join_result = {
                "identity_id": identity_id,
                "phase": "join",
                "ok": False,
                "status": "runtime_error",
                "error": str(safe_miniapp_event_detail({"error": str(exc)}).get("error") or "runtime error"),
            }
            await _emit_progress(progress_callback, join_result)
            return None, join_result
        total_window_skip, identity_extra_window_skip = window_skip_for(identity_id)
        join_result = {
            "identity_id": identity_id,
            "phase": "join",
            "ok": bool(receipt.joined),
            **receipt.safe_summary(),
        }
        if receipt.joined:
            await _emit_progress(progress_callback, join_result)
            return (
                identity_id,
                init_data,
                receipt,
                identity_transport,
                session,
                total_window_skip,
                identity_extra_window_skip,
            ), join_result
        if session is not None:
            session.close()
        await _emit_progress(progress_callback, join_result)
        return None, join_result

    async def battle_one(context, priority_index):
        (
            identity_id,
            init_data,
            receipt,
            identity_transport,
            session,
            window_skip_count,
            identity_extra_window_skip,
        ) = context
        launch_delay_sec = float(priority_index) * battle_priority_gap_sec
        if launch_delay_sec > 0:
            await asyncio.sleep(launch_delay_sec)
        battle_token = getattr(receipt, "session_token", "") or launch["token"]
        realtime_feed = None
        realtime_summary = {"available": _websocket_connect is not None, "started": False}
        try:
            try:
                if _websocket_connect is not None:
                    def websocket_transport(request):
                        return _requests_transport(request)

                    candidate_feed = _WorldBossRealtimeFeed(
                        identity_id=identity_id,
                        token=battle_token,
                        init_data=init_data,
                        transport=websocket_transport,
                        capture_sink=capture_sink,
                    )
                    if await candidate_feed.start():
                        realtime_feed = candidate_feed
                        realtime_summary["started"] = True
                battle = await asyncio.to_thread(
                    run_world_boss_joined_battle_lab_flow,
                    receipt,
                    token=battle_token,
                    entry_token=launch["token"],
                    init_data=init_data,
                    transport=identity_transport,
                    capture_sink=capture_sink,
                    capture_source=f"world_boss:battle:{identity_id}",
                    window_skip_count=window_skip_count,
                    stop_event=stop_event,
                    realtime_waiter=realtime_feed.wait_for_update if realtime_feed else None,
                    realtime_state_provider=realtime_feed.latest_boss if realtime_feed else None,
                )
            finally:
                if realtime_feed is not None:
                    realtime_summary.update(realtime_feed.safe_summary())
                    await realtime_feed.close()
                if session is not None:
                    session.close()
            safe_battle = safe_miniapp_event_detail(battle)
            data = safe_battle.get("data") or {}
            battle_summary = data.get("result") if isinstance(data, dict) else {}
            if not isinstance(battle_summary, dict):
                battle_summary = {}
            else:
                battle_summary = dict(battle_summary)
            battle_summary["finish_reserve_window_count"] = WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS
            battle_summary["identity_extra_window_skip_count"] = identity_extra_window_skip
            battle_summary["launch_priority_index"] = int(priority_index)
            battle_summary["launch_delay_ms"] = int(round(launch_delay_sec * 1000))
            battle_summary["realtime_feed"] = dict(realtime_summary)
            battle_result = {
                "identity_id": identity_id,
                "phase": "battle",
                "ok": bool(battle.get("ok")),
                "status": str(battle.get("status") or "failed"),
                "summary": battle_summary,
                "error": str(safe_battle.get("error") or ""),
            }
            await _emit_progress(progress_callback, battle_result)
            return battle_result
        except Exception as exc:
            battle_result = {
                "identity_id": identity_id,
                "phase": "battle",
                "ok": False,
                "status": "runtime_error",
                "error": str(safe_miniapp_event_detail({"error": str(exc)}).get("error") or "runtime error"),
            }
            await _emit_progress(progress_callback, battle_result)
            return battle_result

    priority_owner = f"world_boss:{int(now)}"
    # All selected identities share one server-side event.  Once it closes,
    # stop stale local timelines before they emit more /hit requests.
    stop_event = threading.Event()
    begin_miniapp_priority_window(priority_owner)
    try:
        joined = await asyncio.gather(*(join_one(identity_id) for identity_id in (identity_ids or ())))
        contexts = [context for context, _result in joined if context is not None]
        results = [result for _context, result in joined if result is not None]
        results.extend(await asyncio.gather(*(
            battle_one(context, priority_index)
            for priority_index, context in enumerate(contexts)
        )))
    finally:
        end_miniapp_priority_window(priority_owner)

    battle_results = [item for item in results if item.get("phase") == "battle"]
    effective_results = [item for item in battle_results if item.get("ok") and item.get("status") == "settled"]
    failed_join_results = [
        item
        for item in results
        if item.get("phase") == "join" and not item.get("ok")
    ]
    all_joined_settled = (
        bool(contexts)
        and not failed_join_results
        and len(effective_results) == len(contexts)
    )
    return {
        "ok": all_joined_settled,
        "status": "settled" if all_joined_settled else "partial",
        "joined_count": len(contexts),
        "results": results,
        "entry": launch.get("safe_summary") or {},
    }


__all__ = [
    "WORLD_BOSS_MINIAPP_FINISH_RESERVE_WINDOWS",
    "extract_world_boss_miniapp_launch",
    "request_world_boss_miniapp_init_data",
    "run_world_boss_miniapp_event",
]
