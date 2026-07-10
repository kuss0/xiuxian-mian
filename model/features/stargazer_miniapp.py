import asyncio
import re
import time

import requests
from telethon import functions

from ..config import STARGAZER_STAR_DURATIONS, TG_REQUESTS_PROXIES
from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    build_miniapp_launch_request,
    build_miniapp_http_request,
    build_request_webview_args,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    iter_webapp_entry_links,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)
from ..timing import has_wait_time, parse_wait_time


STARGAZER_MINIAPP_GAME_KEY = "stargazer"
STARGAZER_MINIAPP_LABEL = "观星台"
STARGAZER_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
STARGAZER_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
STARGAZER_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-sect-farm/"
STARGAZER_MINIAPP_ENDPOINTS = {
    "start": f"{STARGAZER_MINIAPP_API_PATH_PREFIX}start",
    "action": f"{STARGAZER_MINIAPP_API_PATH_PREFIX}action",
}
STARGAZER_MINIAPP_START_PARAM_PATTERN = r"(?:farm_)?[A-Za-z0-9_-]{4,160}"
STARGAZER_MINIAPP_BAD_STATUSES = {"星光黯淡", "元磁紊乱"}
STARGAZER_MINIAPP_READY_STATUSES = {"可收集", "精华已成"}
STARGAZER_MINIAPP_ACTIONS = {"soothe", "collect", "pull"}
STARGAZER_MINIAPP_HTTP_TIMEOUT = (5, 20)
STARGAZER_MINIAPP_MAX_ACTION_FLOOR = 4
RE_STARGAZER_MINIAPP_ITEM_DELTA = re.compile(r"(?:【(?P<bracket>[^】]+)】|(?P<plain>[^\s，、。:：【】]+))\s*[xX×]\s*(?P<count>[\d,]+)")


def build_stargazer_miniapp_adapter(
    *,
    api_base_url=STARGAZER_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=STARGAZER_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=STARGAZER_MINIAPP_GAME_KEY,
        label=STARGAZER_MINIAPP_LABEL,
        bot_username=bot_username,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(STARGAZER_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(STARGAZER_MINIAPP_ENDPOINTS),
        start_param_pattern=STARGAZER_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_stargazer_miniapp_request(
    endpoint,
    *,
    token,
    init_data_session=None,
    init_data="",
    player_id=None,
    payload=None,
    adapter=None,
):
    adapter = adapter or build_stargazer_miniapp_adapter()
    request_payload = {"token": str(token or "").strip()}
    if player_id not in (None, ""):
        request_payload["playerId"] = int(player_id)
    request_payload.update(dict(payload or {}))
    return build_miniapp_http_request(
        adapter,
        endpoint,
        request_payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )


async def request_stargazer_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_stargazer_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "stargazer miniapp launch not allowed")
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
        timeout=STARGAZER_MINIAPP_HTTP_TIMEOUT,
    )


def _iter_event_buttons(event, *, message_text=""):
    yield from iter_webapp_entry_links(event, message_text=message_text)


def extract_stargazer_miniapp_launch(event, *, message_text=""):
    adapter = build_stargazer_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event, message_text=message_text):
        if not url:
            continue
        summary = summarize_stargazer_entry(url, button_text=button_text, message_text=message_text)
        if not summary or summary.get("game_hint") != STARGAZER_MINIAPP_GAME_KEY:
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


def build_stargazer_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=STARGAZER_MINIAPP_GAME_KEY,
        label=STARGAZER_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only farm protocol declaration; stargazer production actions are not wired",
        replaces_commands=(".观星台",),
        state_outputs=("module_snapshot", "inventory_delta"),
        steps=(
            MiniAppFlowStep(
                key="launch",
                endpoint="telegram_webview",
                method="TELEGRAM",
                required_payload_keys=("start_param",),
                sends_init_data=False,
                note="RequestWebView 获取短 TTL initData，不落盘",
            ),
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="GET farm domain/plots state through POST /start",
            ),
            MiniAppFlowStep(
                key="decide_action",
                endpoint="local_decision",
                method="LOCAL",
                required_payload_keys=("start",),
                sends_init_data=False,
                note="纯函数解析 plots，建议 soothe/collect/pull/wait",
            ),
            MiniAppFlowStep(
                key="action",
                endpoint="action",
                required_payload_keys=("token", "initData", "action", "plotKey"),
                optional_payload_keys=("starName",),
                note="lab-only action request declaration; not wired to scheduler",
            ),
        ),
    )


def summarize_stargazer_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = STARGAZER_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def build_stargazer_launch_args(url, *, start_param="", bot_username=STARGAZER_MINIAPP_DEFAULT_BOT_USERNAME):
    adapter = build_stargazer_miniapp_adapter(bot_username=bot_username)
    request = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return request, build_request_webview_args(adapter, request) if request.allowed else {}


def _miniapp_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _coerce_remaining_seconds(plot):
    for key in ("remainingSec", "remainingSeconds", "remainSeconds", "remaining_sec", "remain_sec"):
        try:
            seconds = int(float(plot.get(key)))
        except (TypeError, ValueError, OverflowError):
            continue
        if seconds > 0:
            return seconds
    for key in ("remainingText", "statusLabel", "status"):
        text = str(plot.get(key) or "")
        if has_wait_time(text):
            return int(parse_wait_time(text) or 0)
    name = str(plot.get("name") or plot.get("starName") or plot.get("star") or "").strip()
    duration_sec = int(STARGAZER_STAR_DURATIONS.get(name, 0) or 0)
    if duration_sec > 0:
        try:
            progress = float(plot.get("progress"))
        except (TypeError, ValueError, OverflowError):
            progress = 0
        if 0 <= progress < 100:
            return int(duration_sec * max(0.0, 100.0 - progress) / 100.0)
    return 0


def parse_stargazer_farm_state(data):
    """Normalize a farm MiniApp /start or /action response into safe plot state."""
    if not isinstance(data, dict):
        return {}
    domain = data.get("domain") if isinstance(data.get("domain"), dict) else {}
    if not domain and isinstance(data.get("data"), dict):
        domain = data["data"].get("domain") if isinstance(data["data"].get("domain"), dict) else {}
    if not isinstance(domain, dict) or domain.get("mode") != "stars":
        return {}
    raw_plots = domain.get("plots") or ()
    if not isinstance(raw_plots, list) or not raw_plots:
        return {}

    plots = []
    idle_slot_count = 0
    dim_slot_count = 0
    ready_slot_count = 0
    busy_waits = []
    for index, item in enumerate(raw_plots, start=1):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or index).strip()
        status = str(item.get("status") or item.get("statusLabel") or "").strip()
        empty = _miniapp_bool(item.get("empty"))
        remaining_sec = _coerce_remaining_seconds(item)
        plot = {
            "key": key,
            "name": str(item.get("name") or "").strip(),
            "status": status,
            "statusLabel": str(item.get("statusLabel") or "").strip(),
            "empty": empty,
            "remaining_sec": remaining_sec,
        }
        plots.append(plot)
        if empty:
            idle_slot_count += 1
        elif status in STARGAZER_MINIAPP_BAD_STATUSES:
            dim_slot_count += 1
        elif status in STARGAZER_MINIAPP_READY_STATUSES:
            ready_slot_count += 1
        elif remaining_sec > 0:
            busy_waits.append(remaining_sec)

    total_slots = len(plots)
    if total_slots <= 0:
        return {}
    return {
        "mode": "stars",
        "total_slots": total_slots,
        "idle_slot_count": idle_slot_count,
        "dim_slot_count": dim_slot_count,
        "ready_slot_count": ready_slot_count,
        "busy_waits": busy_waits,
        "min_wait": min(busy_waits) if busy_waits else 0,
        "max_wait": max(busy_waits) if busy_waits else 0,
        "all_ready": ready_slot_count == total_slots and total_slots > 0,
        "plots": plots,
    }


def choose_stargazer_farm_action(farm_state, *, star_choice=""):
    state = dict(farm_state or {})
    plots = list(state.get("plots") or ())
    for plot in plots:
        if not plot.get("empty") and str(plot.get("status") or "") in STARGAZER_MINIAPP_BAD_STATUSES:
            return {"action": "soothe", "plotKey": str(plot.get("key") or ""), "reason": "bad_plot"}
    if state.get("all_ready"):
        return {"action": "collect", "plotKey": "", "reason": "all_ready"}
    for plot in plots:
        if plot.get("empty"):
            decision = {"action": "pull", "plotKey": str(plot.get("key") or ""), "reason": "empty_plot"}
            if star_choice:
                decision["starName"] = str(star_choice).strip()
            return decision
    wait_sec = int(state.get("max_wait", 0) or 0)
    if wait_sec > 0:
        return {"action": "wait", "plotKey": "", "wait_sec": wait_sec, "reason": "busy"}
    return {"action": "inspect", "plotKey": "", "reason": "unknown"}


def build_stargazer_farm_action_request(
    decision,
    *,
    token,
    init_data_session=None,
    init_data="",
    player_id=None,
    adapter=None,
):
    decision = dict(decision or {})
    action = str(decision.get("action") or "").strip()
    if action not in STARGAZER_MINIAPP_ACTIONS:
        raise ValueError(f"stargazer farm action not sendable: {action or 'missing'}")
    payload = {"action": action, "plotKey": str(decision.get("plotKey") or "")}
    if action == "pull" and decision.get("starName"):
        payload["starName"] = str(decision.get("starName") or "").strip()
    return build_stargazer_miniapp_request(
        "action",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        player_id=player_id,
        payload=payload,
        adapter=adapter,
    )


def extract_stargazer_miniapp_item_deltas(data):
    """Extract safe item deltas from MiniApp action result text."""
    texts = []

    def visit(value):
        if isinstance(value, str):
            texts.append(value)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("message", "text", "summary", "resultText", "result_text", "rewardText", "reward_text"):
            if key in value:
                visit(value.get(key))
        for key in ("result", "reward", "rewards", "items", "loot", "drops"):
            if key in value:
                visit(value.get(key))

    visit(data or {})
    deltas = {}
    for text in texts:
        for match in RE_STARGAZER_MINIAPP_ITEM_DELTA.finditer(str(text or "")):
            name = str(match.group("bracket") or match.group("plain") or "").strip()
            if not name or name in {"修为", "灵石"}:
                continue
            try:
                count = int(str(match.group("count") or "1").replace(",", ""))
            except (TypeError, ValueError, OverflowError):
                count = 1
            if count > 0:
                deltas[name] = int(deltas.get(name, 0) or 0) + count
    return deltas


def _flow_result(ok, status, *, error="", data=None, events=None):
    return {
        "ok": bool(ok),
        "status": status,
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
    }


def _append_http_event(events, step, result):
    events.append({
        "step": step,
        "ok": bool(result.ok),
        "status_code": int(result.status_code or 0),
        "error_type": result.error_type,
        "attempts": int(result.attempts or 0),
        "data_keys": sorted(result.data) if isinstance(result.data, dict) else [],
        "error": sanitize_webapp_secret_text(result.error),
    })


def _merge_item_deltas(target, source):
    for name, count in dict(source or {}).items():
        name = str(name or "").strip()
        try:
            amount = int(count or 0)
        except (TypeError, ValueError, OverflowError):
            amount = 0
        if name and amount > 0:
            target[name] = int(target.get(name, 0) or 0) + amount
    return target


def run_stargazer_miniapp_lab_flow(
    *,
    token,
    init_data,
    player_id=None,
    star_choice="",
    transport,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_stargazer_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    action_counts = {"soothe": 0, "collect": 0, "pull": 0}
    item_deltas = {}

    start_request = build_stargazer_miniapp_request(
        "start",
        token=token,
        init_data=init_data,
        player_id=player_id,
        adapter=adapter,
    )
    start_result = execute_miniapp_http_request(
        start_request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start",
    )
    _append_http_event(events, "start", start_result)
    if not start_result.ok:
        return _flow_result(False, "failed", error=start_result.error, events=events)

    current_data = start_result.data
    farm_state = parse_stargazer_farm_state(current_data)
    if not farm_state:
        return _flow_result(False, "failed", error="MiniApp 返回不是观星台状态", events=events, data={"raw_keys": sorted(current_data)})

    max_actions = max(STARGAZER_MINIAPP_MAX_ACTION_FLOOR, int(farm_state.get("total_slots", 0) or 0) * 3 + 3)
    for index in range(max_actions):
        farm_state = parse_stargazer_farm_state(current_data)
        if not farm_state:
            return _flow_result(False, "failed", error="MiniApp 返回不是观星台状态", events=events)
        decision = choose_stargazer_farm_action(farm_state, star_choice=star_choice)
        action = str(decision.get("action") or "").strip()
        if action in {"wait", "inspect"}:
            return _flow_result(True, action, data={
                "farm_state": farm_state,
                "decision": decision,
                "action_counts": action_counts,
                "item_deltas": item_deltas,
            }, events=events)

        action_request = build_stargazer_farm_action_request(
            decision,
            token=token,
            init_data=init_data,
            player_id=player_id,
            adapter=adapter,
        )
        action_result = execute_miniapp_http_request(
            action_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"action_{action}",
        )
        _append_http_event(events, f"action_{action}", action_result)
        if not action_result.ok:
            return _flow_result(False, "failed", error=action_result.error, events=events, data={
                "farm_state": farm_state,
                "decision": decision,
                "action_counts": action_counts,
                "item_deltas": item_deltas,
            })
        if action in action_counts:
            action_counts[action] += 1
        if action == "collect":
            _merge_item_deltas(item_deltas, extract_stargazer_miniapp_item_deltas(action_result.data))
        current_data = action_result.data

    farm_state = parse_stargazer_farm_state(current_data) or farm_state
    return _flow_result(True, "action_limit", data={
        "farm_state": farm_state,
        "decision": {"action": "wait", "reason": "action_limit"},
        "action_counts": action_counts,
        "item_deltas": item_deltas,
    }, events=events)


async def run_stargazer_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    star_choice="",
    init_data="",
    player_id=None,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_stargazer_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_stargazer_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        return await asyncio.to_thread(
            run_stargazer_miniapp_lab_flow,
            token=token,
            init_data=init_data,
            player_id=player_id,
            star_choice=star_choice,
            transport=transport or _requests_transport,
            adapter=adapter,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "STARGAZER_MINIAPP_GAME_KEY",
    "build_stargazer_farm_action_request",
    "build_stargazer_launch_args",
    "build_stargazer_miniapp_adapter",
    "build_stargazer_miniapp_flow_plan",
    "build_stargazer_miniapp_request",
    "choose_stargazer_farm_action",
    "extract_stargazer_miniapp_launch",
    "extract_stargazer_miniapp_item_deltas",
    "parse_stargazer_farm_state",
    "request_stargazer_miniapp_init_data",
    "run_stargazer_miniapp_lab_flow",
    "run_stargazer_miniapp_production_flow",
    "summarize_stargazer_entry",
]
