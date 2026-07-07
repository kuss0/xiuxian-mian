import asyncio
import time

import requests
from telethon import functions

from ..config import TG_REQUESTS_PROXIES
from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    build_miniapp_http_request,
    build_miniapp_launch_request,
    build_request_webview_args,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)


TREE_MINIAPP_GAME_KEY = "tree"
TREE_MINIAPP_LABEL = "灵眼之树"
TREE_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
TREE_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
TREE_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-spirit-tree/"
TREE_MINIAPP_ENDPOINTS = {
    "start": f"{TREE_MINIAPP_API_PATH_PREFIX}start",
    "action": f"{TREE_MINIAPP_API_PATH_PREFIX}action",
    "run_start": f"{TREE_MINIAPP_API_PATH_PREFIX}run/start",
    "run_submit": f"{TREE_MINIAPP_API_PATH_PREFIX}run/submit",
    "reward_claim": f"{TREE_MINIAPP_API_PATH_PREFIX}reward/claim",
}
TREE_MINIAPP_START_PARAM_PATTERN = r"(?:tree|spirittree|spirit_tree|lyz)[_-][A-Za-z0-9_-]{4,180}"
TREE_MINIAPP_HTTP_TIMEOUT = (5, 20)
TREE_MINIAPP_STOP_ERROR_KEYWORDS = (
    "daily_limit",
    "no_remaining",
    "limit_reached",
    "次数已尽",
    "剩余 0",
    "season_closed",
    "reward_claimed",
)


def build_tree_miniapp_adapter(
    *,
    api_base_url=TREE_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=TREE_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=TREE_MINIAPP_GAME_KEY,
        label=TREE_MINIAPP_LABEL,
        bot_username=bot_username,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(TREE_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(TREE_MINIAPP_ENDPOINTS),
        start_param_pattern=TREE_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_tree_miniapp_request(endpoint, *, token, init_data_session=None, init_data="", payload=None, adapter=None):
    adapter = adapter or build_tree_miniapp_adapter()
    request_payload = {"token": str(token or "").strip()}
    request_payload.update(dict(payload or {}))
    return build_miniapp_http_request(
        adapter,
        endpoint,
        request_payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )


def _iter_event_buttons(event):
    message = getattr(event, "message", None) or event
    for raw_row in getattr(message, "buttons", None) or ():
        row = raw_row if isinstance(raw_row, (list, tuple)) else (raw_row,)
        for button in row:
            raw_button = getattr(button, "button", None) or button
            text = str(getattr(button, "text", "") or getattr(raw_button, "text", "") or "").strip()
            url = (
                getattr(raw_button, "url", "")
                or getattr(raw_button, "webview", "")
                or getattr(raw_button, "web_view", "")
                or ""
            )
            yield text, str(url or "").strip()


def summarize_tree_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = TREE_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def extract_tree_miniapp_launch(event, *, message_text=""):
    adapter = build_tree_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event):
        if not url:
            continue
        summary = summarize_tree_entry(url, button_text=button_text, message_text=message_text)
        if not summary or summary.get("game_hint") != TREE_MINIAPP_GAME_KEY:
            continue
        launch = build_miniapp_launch_request(adapter, url)
        if not launch.allowed or not launch.start_param:
            continue
        return {
            "token": launch.start_param,
            "webview_url": url,
            "button_text": button_text,
            "safe_summary": launch.safe_summary(),
        }
    return {}


def build_tree_launch_args(url, *, start_param="", bot_username=TREE_MINIAPP_DEFAULT_BOT_USERNAME):
    adapter = build_tree_miniapp_adapter(bot_username=bot_username)
    request = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return request, build_request_webview_args(adapter, request) if request.allowed else {}


async def request_tree_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_tree_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "tree miniapp launch not allowed")
    account_id, client = _get_identity_client_with_account(identity_id)
    if client is None:
        raise RuntimeError("身份客户端不可用")
    async with account_rpc_slot(account_id=account_id, client_obj=client):
        bot = await client.get_entity(launch.bot_username or adapter.bot_username)
        bot_input = await client.get_input_entity(bot)
        result = await client(functions.messages.RequestMainWebViewRequest(
            peer=bot_input,
            bot=bot_input,
            platform=launch.platform or adapter.platform,
            start_param=launch.start_param,
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
        timeout=TREE_MINIAPP_HTTP_TIMEOUT,
    )


def build_tree_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=TREE_MINIAPP_GAME_KEY,
        label=TREE_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only spirit-tree declaration;旧灵树 scheduler 已归档，不接生产自动跑分",
        steps=(
            MiniAppFlowStep(
                key="launch",
                endpoint="telegram_webview",
                method="TELEGRAM",
                required_payload_keys=("token",),
                sends_init_data=False,
                note="RequestMainWebView 获取短 TTL initData，不落盘",
            ),
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="读取灵眼之树/council 赛季、jump/fly 次数、排行与旧养护面板状态",
            ),
            MiniAppFlowStep(
                key="decide_mode",
                endpoint="local_decision",
                method="LOCAL",
                required_payload_keys=("start",),
                sends_init_data=False,
                note="只读决策 jump/fly 是否还有次数；当前不自动提交成绩",
            ),
            MiniAppFlowStep(
                key="run_start",
                endpoint="run_start",
                required_payload_keys=("token", "initData", "mode"),
                note="服务端开局，返回 runToken/seed；候选接口，未接生产",
            ),
            MiniAppFlowStep(
                key="run_submit",
                endpoint="run_submit",
                required_payload_keys=("token", "initData", "mode", "runToken", "proof"),
                note="提交 jump/fly proof；候选接口，需主控复核后才可上线",
            ),
            MiniAppFlowStep(
                key="reward_claim",
                endpoint="reward_claim",
                required_payload_keys=("token", "initData", "seasonId"),
                note="补领赛季奖励；候选接口，不自动领取",
            ),
        ),
    )


def _int_value(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _mode_quota(data, mode):
    council = data.get("council") if isinstance(data, dict) else {}
    daily = council.get("daily") if isinstance(council, dict) else {}
    quota = daily.get(mode) if isinstance(daily, dict) else {}
    if not isinstance(quota, dict):
        quota = {}
    used = _int_value(quota.get("used"), 0)
    limit = _int_value(quota.get("limit"), 0)
    best = _int_value(quota.get("best"), 0)
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used) if limit > 0 else 0,
        "best": best,
    }


def parse_tree_miniapp_state(data):
    data = data if isinstance(data, dict) else {}
    tree = data.get("tree") if isinstance(data.get("tree"), dict) else {}
    council = data.get("council") if isinstance(data.get("council"), dict) else {}
    season = council.get("season") if isinstance(council.get("season"), dict) else {}
    ranking = data.get("ranking") if isinstance(data.get("ranking"), dict) else {}
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    jump = _mode_quota(data, "jump")
    fly = _mode_quota(data, "fly")
    return {
        "ok": bool(data.get("ok")),
        "gameplay_mode": str(tree.get("gameplayMode") or ""),
        "gameplay_name": str(tree.get("gameplayName") or ""),
        "status": str(tree.get("status") or ""),
        "status_label": str(tree.get("statusLabel") or ""),
        "maturity": float(tree.get("maturity") or 0.0),
        "season_id": str(season.get("seasonId") or ""),
        "season_status": str(season.get("status") or ""),
        "season_day_index": _int_value(season.get("dayIndex"), 0),
        "jump": jump,
        "fly": fly,
        "my_contribution_points": _int_value(ranking.get("myContributionPoints"), 0),
        "branch_rank": _int_value(ranking.get("branchRank"), 0),
        "claimed": bool(ranking.get("claimed")),
        "can_run_game": bool(jump["remaining"] or fly["remaining"]),
        "can_claim_reward": bool(ranking.get("claimed") is False and str(season.get("status") or "") in {"ended", "settled", "claimable"}),
        "actions": {
            key: bool(actions.get(key))
            for key in ("canMeridian", "canRitual", "canDarkScheme", "canHarvest", "canIrrigate", "canGuard", "canOffer")
        },
    }


def classify_tree_miniapp_error(error):
    raw = str(error or "").strip()
    lowered = raw.lower()
    if any(keyword in lowered for keyword in TREE_MINIAPP_STOP_ERROR_KEYWORDS):
        return "daily_limit"
    return "failed"


def _flow_result(ok, status, *, data=None, events=None, error=""):
    return {
        "ok": bool(ok),
        "status": str(status or ""),
        "data": data if isinstance(data, dict) else {},
        "events": list(events or ()),
        "error": sanitize_webapp_secret_text(error),
    }


def run_tree_miniapp_start_lab_flow(
    *,
    token,
    init_data,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_tree_miniapp_adapter()
    transport = transport or _requests_transport
    sleeper = sleeper or time.sleep
    events = []
    request = build_tree_miniapp_request("start", token=token, init_data=init_data, adapter=adapter)
    result = execute_miniapp_http_request(
        request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start",
    )
    events.append({"step": "start", "ok": result.ok, "summary": result.safe_summary()})
    if not result.ok:
        return _flow_result(False, classify_tree_miniapp_error(result.error), error=result.error, events=events)
    parsed = parse_tree_miniapp_state(result.data)
    return _flow_result(True, "ready", data={"state": parsed}, events=events)


async def run_tree_miniapp_start_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_tree_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = await request_tree_miniapp_init_data(identity_id, token=token, webview_url=webview_url, adapter=adapter)
        return await asyncio.to_thread(
            run_tree_miniapp_start_lab_flow,
            token=token,
            init_data=init_data,
            transport=transport or _requests_transport,
            adapter=adapter,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "TREE_MINIAPP_ENDPOINTS",
    "TREE_MINIAPP_GAME_KEY",
    "build_tree_launch_args",
    "build_tree_miniapp_adapter",
    "build_tree_miniapp_flow_plan",
    "build_tree_miniapp_request",
    "classify_tree_miniapp_error",
    "extract_tree_miniapp_launch",
    "parse_tree_miniapp_state",
    "request_tree_miniapp_init_data",
    "run_tree_miniapp_start_lab_flow",
    "run_tree_miniapp_start_production_flow",
    "summarize_tree_entry",
]
