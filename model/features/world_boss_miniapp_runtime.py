"""Runtime bridge for serial World Boss MiniApp automation."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import requests
from telethon import functions

from ..config import MESSAGES_DIR, TG_REQUESTS_PROXIES
from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..state import get_identity_account
from ..timing import get_day_key
from ..webapp_core import (
    MiniAppCaptureStore,
    begin_miniapp_priority_window,
    build_miniapp_launch_request,
    extract_miniapp_init_data_from_url,
    iter_webapp_entry_links,
    end_miniapp_priority_window,
    safe_miniapp_event_detail,
)
from .world_boss_miniapp import (
    WORLD_BOSS_JOIN_WINDOW_SEC,
    build_world_boss_miniapp_adapter,
    join_world_boss_miniapp_lab,
    run_world_boss_joined_battle_lab_flow,
)


WORLD_BOSS_MINIAPP_HTTP_TIMEOUT = (5, 20)
WORLD_BOSS_MINIAPP_CAPTURE_DIR = Path(MESSAGES_DIR) / "miniapp-captures"


def extract_world_boss_miniapp_launch(event, *, message_text=""):
    adapter = build_world_boss_miniapp_adapter()
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
        if not url:
            continue
        launch = build_miniapp_launch_request(adapter, url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": url,
                "button_text": str(button_text or ""),
                "bot_username": launch.bot_username or adapter.bot_username,
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


def _requests_transport(request):
    return requests.request(
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


async def run_world_boss_miniapp_event(
    identity_ids,
    event,
    *,
    message_text="",
    opened_at=None,
    account_gap_sec=0,
    transport=None,
    init_data_provider=None,
):
    """Join accounts in parallel, then run one serial timeline per account."""

    now = float(opened_at or time.time())
    launch = extract_world_boss_miniapp_launch(event, message_text=message_text)
    if not launch:
        return {"ok": False, "status": "entry_missing", "joined_count": 0, "results": []}
    transport = transport or _requests_transport
    init_data_provider = init_data_provider or request_world_boss_miniapp_init_data
    capture_sink = _capture_store(now)
    async def join_one(raw_identity_id):
        identity_id = int(raw_identity_id or 0)
        if identity_id <= 0:
            return None, None
        if time.time() - now > WORLD_BOSS_JOIN_WINDOW_SEC:
            return None, {
                "identity_id": identity_id,
                "phase": "join",
                "ok": False,
                "status": "join_deadline_exceeded",
                "error": "world boss join window exceeded",
            }
        try:
            init_data = await init_data_provider(identity_id, launch)
            receipt = await asyncio.to_thread(
                join_world_boss_miniapp_lab,
                token=launch["token"],
                init_data=init_data,
                player_id=identity_id,
                identity_id=identity_id,
                account_id=get_identity_account(identity_id),
                transport=transport,
                capture_sink=capture_sink,
                capture_source=f"world_boss:join:{identity_id}",
            )
        except Exception as exc:
            return None, {
                "identity_id": identity_id,
                "phase": "join",
                "ok": False,
                "status": "runtime_error",
                "error": str(safe_miniapp_event_detail({"error": str(exc)}).get("error") or "runtime error"),
            }
        join_result = {
            "identity_id": identity_id,
            "phase": "join",
            "ok": bool(receipt.joined),
            **receipt.safe_summary(),
        }
        if receipt.joined:
            return (identity_id, init_data, receipt), join_result
        return None, join_result

    async def battle_one(context):
        identity_id, init_data, receipt = context
        battle_token = getattr(receipt, "session_token", "") or launch["token"]
        try:
            battle = await asyncio.to_thread(
                run_world_boss_joined_battle_lab_flow,
                receipt,
                token=battle_token,
                entry_token=launch["token"],
                init_data=init_data,
                transport=transport,
                capture_sink=capture_sink,
                capture_source=f"world_boss:battle:{identity_id}",
            )
            safe_battle = safe_miniapp_event_detail(battle)
            return {
                "identity_id": identity_id,
                "phase": "battle",
                "ok": bool(battle.get("ok")),
                "status": str(battle.get("status") or "failed"),
                "data": safe_battle.get("data") or {},
                "error": str(safe_battle.get("error") or ""),
            }
        except Exception as exc:
            return {
                "identity_id": identity_id,
                "phase": "battle",
                "ok": False,
                "status": "runtime_error",
                "error": str(safe_miniapp_event_detail({"error": str(exc)}).get("error") or "runtime error"),
            }

    priority_owner = f"world_boss:{int(now)}"
    begin_miniapp_priority_window(priority_owner)
    try:
        joined = await asyncio.gather(*(join_one(identity_id) for identity_id in (identity_ids or ())))
        contexts = [context for context, _result in joined if context is not None]
        results = [result for _context, result in joined if result is not None]
        results.extend(await asyncio.gather(*(battle_one(context) for context in contexts)))
    finally:
        end_miniapp_priority_window(priority_owner)

    battle_results = [item for item in results if item.get("phase") == "battle"]
    return {
        "ok": bool(contexts) and bool(battle_results) and all(item.get("ok") for item in battle_results),
        "status": "settled" if battle_results and all(item.get("ok") for item in battle_results) else "partial",
        "joined_count": len(contexts),
        "results": results,
        "entry": launch.get("safe_summary") or {},
    }


__all__ = [
    "extract_world_boss_miniapp_launch",
    "request_world_boss_miniapp_init_data",
    "run_world_boss_miniapp_event",
]
