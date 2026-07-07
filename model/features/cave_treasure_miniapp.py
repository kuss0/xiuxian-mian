import asyncio
import random
import re
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


CAVE_TREASURE_MINIAPP_GAME_KEY = "cave_treasure"
CAVE_TREASURE_MINIAPP_LABEL = "洞府寻宝"
CAVE_TREASURE_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
CAVE_TREASURE_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-dwelling/"
CAVE_TREASURE_MINIAPP_ENDPOINTS = {
    "start": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}start",
    "hunt": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt",
    "hunt_reveal": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt/reveal",
    "hunt_settle": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt/settle",
}
CAVE_TREASURE_MINIAPP_START_PARAM_PATTERN = r"(?:df_)?[A-Za-z0-9_-]{4,160}"
CAVE_TREASURE_MINIAPP_HTTP_TIMEOUT = (5, 20)
CAVE_TREASURE_SENDABLE_ACTIONS = {
    "enter",
    "search",
    "settle",
}

_RATIO_RE = re.compile(r"(?P<label>神识|出手|次数|游戏|局数)?\s*[:：]?\s*(?P<a>\d+)\s*/\s*(?P<b>\d+)")
_TARGET_RE = re.compile(r"(?:第|#)?\s*(?P<target>\d{1,2})\s*(?:个|号|处|位)")


def build_cave_treasure_miniapp_adapter(
    *,
    api_base_url=CAVE_TREASURE_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=CAVE_TREASURE_MINIAPP_GAME_KEY,
        label=CAVE_TREASURE_MINIAPP_LABEL,
        bot_username=bot_username,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(CAVE_TREASURE_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(CAVE_TREASURE_MINIAPP_ENDPOINTS),
        start_param_pattern=CAVE_TREASURE_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_cave_treasure_miniapp_request(endpoint, *, token, init_data_session=None, init_data="", payload=None, adapter=None):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
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


def summarize_cave_treasure_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = CAVE_TREASURE_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def extract_cave_treasure_miniapp_launch(event, *, message_text=""):
    adapter = build_cave_treasure_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event):
        if not url:
            continue
        summary = summarize_cave_treasure_entry(url, button_text=button_text, message_text=message_text)
        if not summary or summary.get("game_hint") != CAVE_TREASURE_MINIAPP_GAME_KEY:
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


def build_cave_treasure_launch_args(url, *, start_param="", bot_username=CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME):
    adapter = build_cave_treasure_miniapp_adapter(bot_username=bot_username)
    request = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return request, build_request_webview_args(adapter, request) if request.allowed else {}


async def request_cave_treasure_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "cave treasure miniapp launch not allowed")
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
        timeout=CAVE_TREASURE_MINIAPP_HTTP_TIMEOUT,
    )


def build_cave_treasure_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=CAVE_TREASURE_MINIAPP_GAME_KEY,
        label=CAVE_TREASURE_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only cave treasure declaration; endpoint names are capture candidates and production scheduler is not wired",
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
                note="读取洞府页面、寻宝 tab、神识/游戏次数和提示文案",
            ),
            MiniAppFlowStep(
                key="decide_action",
                endpoint="local_decision",
                method="LOCAL",
                required_payload_keys=("state",),
                sends_init_data=False,
                note="按页面剩余次数决策；当前样本通常入府后最多 7 次探索，但不写死",
            ),
            MiniAppFlowStep(
                key="hunt",
                endpoint="hunt",
                required_payload_keys=("token", "initData"),
                note="入府开启一局寻宝，返回 huntRun/sessionId/cells",
            ),
            MiniAppFlowStep(
                key="reveal",
                endpoint="hunt_reveal",
                required_payload_keys=("token", "initData", "sessionId", "index"),
                note="按提示或随机翻开一个未探明石室",
            ),
            MiniAppFlowStep(
                key="settle",
                endpoint="hunt_settle",
                required_payload_keys=("token", "initData", "sessionId"),
                note="见好就收结算本局寻宝",
            ),
        ),
    )


def _find_nested_dict(data, candidate_keys):
    if not isinstance(data, dict):
        return {}
    queue = [data]
    seen = set()
    while queue:
        item = queue.pop(0)
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        if not isinstance(item, dict):
            continue
        lowered_keys = {str(key).lower() for key in item}
        if lowered_keys.intersection(candidate_keys):
            return item
        for value in item.values():
            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(child for child in value if isinstance(child, dict))
    return {}


def _coerce_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _ratio_from_value(value):
    if isinstance(value, dict):
        for first_key, second_key in (
            ("remaining", "limit"),
            ("remain", "limit"),
            ("available", "limit"),
            ("used", "limit"),
            ("count", "limit"),
            ("current", "total"),
            ("value", "max"),
        ):
            if first_key in value and second_key in value:
                return _coerce_int(value.get(first_key)), _coerce_int(value.get(second_key))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _coerce_int(value[0]), _coerce_int(value[1])
    match = _RATIO_RE.search(str(value or ""))
    if match:
        return _coerce_int(match.group("a")), _coerce_int(match.group("b"))
    return 0, 0


def _first_ratio_by_keys(source, keys):
    source = source if isinstance(source, dict) else {}
    for key in keys:
        if key in source:
            ratio = _ratio_from_value(source.get(key))
            if ratio != (0, 0):
                return ratio
    return 0, 0


def _ratios_from_text(text):
    result = {"sense": (0, 0), "games": (0, 0)}
    for match in _RATIO_RE.finditer(str(text or "")):
        ratio = (_coerce_int(match.group("a")), _coerce_int(match.group("b")))
        label = str(match.group("label") or "")
        if label in {"神识", "出手", "次数"} and result["sense"] == (0, 0):
            result["sense"] = ratio
        elif label in {"游戏", "局数"} and result["games"] == (0, 0):
            result["games"] = ratio
    return result


def _bool_from_any(*values):
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on", "active"}:
            return True
        if text in {"0", "false", "no", "n", "off", "inactive"}:
            return False
    return False


def _extract_hint_target(text):
    text = str(text or "")
    match = _TARGET_RE.search(text)
    if match:
        return max(1, _coerce_int(match.group("target"), 0))
    return 0


def parse_cave_treasure_state(data):
    """Normalize cave treasure MiniApp payloads into decision state.

    The game currently reports two different ratio meanings:
    - 神识 8/8: remaining actions / total actions for the current round.
    - 游戏 0/3: used games / total games for the day.
    """

    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    dwelling = root.get("dwelling") if isinstance(root.get("dwelling"), dict) else {}
    hunt_panel = dwelling.get("hunt") if isinstance(dwelling.get("hunt"), dict) else {}
    hunt_run = root.get("huntRun") if isinstance(root.get("huntRun"), dict) else {}
    hunt_result = root.get("huntResult") if isinstance(root.get("huntResult"), dict) else {}
    treasure = _find_nested_dict(
        root,
        {
            "treasure",
            "hunt",
            "search",
            "shenshi",
            "sense",
            "gamecount",
            "games",
            "found",
            "phase",
            "tab",
        },
    ) or hunt_run or hunt_panel or root
    all_text = "\n".join(
        str(value)
        for key, value in treasure.items()
        if isinstance(value, str) and key.lower() in {"text", "message", "hint", "tips", "status", "phase", "tab", "title"}
    )
    outcome_text = "\n".join(
        str(value)
        for key, value in treasure.items()
        if isinstance(value, str) and key.lower() in {"text", "message", "result", "status", "phase", "title"}
    )
    if not all_text:
        all_text = sanitize_webapp_secret_text(str(data), limit=800)
    if not outcome_text:
        outcome_text = all_text

    sense_ratio = _first_ratio_by_keys(
        treasure,
        ("sense", "shenshi", "divineSense", "spiritSense", "actions", "attempts", "mind"),
    )
    if hunt_run:
        sense_ratio = (
            _coerce_int(hunt_run.get("ap"), sense_ratio[0]),
            _coerce_int(hunt_run.get("maxAp"), sense_ratio[1]),
        )
    elif hunt_panel:
        action_points = _coerce_int(hunt_panel.get("actionPoints"), 0)
        if action_points > 0:
            sense_ratio = (action_points, action_points)
    games_ratio = _first_ratio_by_keys(
        treasure,
        ("games", "gameCount", "rounds", "dailyGames", "playCount", "plays"),
    )
    if hunt_panel:
        games_ratio = (
            _coerce_int(hunt_panel.get("used"), games_ratio[0]),
            _coerce_int(hunt_panel.get("limit"), games_ratio[1]),
        )
    text_ratios = _ratios_from_text(all_text)
    if sense_ratio == (0, 0):
        sense_ratio = text_ratios["sense"]
    if games_ratio == (0, 0):
        games_ratio = text_ratios["games"]

    raw_cells = hunt_run.get("cells") if isinstance(hunt_run.get("cells"), list) else []
    board_size = _coerce_int(hunt_run.get("size"), 0)
    target_count = _coerce_int(
        treasure.get("targetCount")
        or treasure.get("npcCount")
        or treasure.get("dwarfCount")
        or treasure.get("pointCount")
        or treasure.get("gridCount")
        or (board_size * board_size if board_size > 0 else 0),
        0,
    )
    raw_targets = raw_cells or treasure.get("targets") or treasure.get("points") or treasure.get("dwarfs") or ()
    if not target_count and isinstance(raw_targets, list):
        target_count = len(raw_targets)
    if target_count <= 0:
        target_count = max(sense_ratio[1] or 0, 1)
    available_targets = []
    revealed_targets = set()
    if raw_cells:
        for cell in raw_cells:
            if not isinstance(cell, dict):
                continue
            cell_index = _coerce_int(cell.get("index"), len(available_targets)) + 1
            if _bool_from_any(cell.get("revealed")):
                revealed_targets.add(cell_index)
                continue
            available_targets.append(cell_index)

    status = str(hunt_run.get("status") or treasure.get("status") or "").strip()
    in_round = _bool_from_any(
        treasure.get("inRound"),
        treasure.get("entered"),
        treasure.get("active"),
        treasure.get("started"),
    ) or bool(hunt_run) or any(keyword in all_text for keyword in ("寻宝中", "已入府", "正在寻宝"))
    on_treasure_tab = _bool_from_any(
        treasure.get("onTreasureTab"),
        treasure.get("treasureTab"),
    ) or bool(hunt_panel or hunt_run or hunt_result) or "寻宝" in all_text
    explicit_treasure_found = _bool_from_any(
        treasure.get("found"),
        treasure.get("treasureFound"),
        treasure.get("hit"),
        hunt_run.get("foundMain"),
        bool(hunt_result),
    )
    treasure_found = explicit_treasure_found or any(
        keyword in outcome_text
        for keyword in ("发现宝", "命中宝", "主宝", "秘宝", "宝物到手", "见好就收", "再来一次")
    )
    settled = bool(hunt_result) or _bool_from_any(treasure.get("settled"), treasure.get("finished")) or any(
        keyword in all_text for keyword in ("结算完成", "已结算", "今日寻宝已结算", "已收获")
    )

    hint_text = str(treasure.get("hint") or treasure.get("tips") or treasure.get("message") or treasure.get("text") or "").strip()
    hint_target = _coerce_int(treasure.get("hintTarget") or treasure.get("answer") or treasure.get("targetIndex"), 0)
    if hint_target <= 0 and raw_cells:
        marker_targets = []
        for cell in raw_cells:
            if not isinstance(cell, dict):
                continue
            hint = cell.get("hint") if isinstance(cell.get("hint"), dict) else {}
            for marker in hint.get("markers") or ():
                if not isinstance(marker, dict):
                    continue
                if str(marker.get("kind") or "").strip() == "treasure":
                    marker_target = _coerce_int(marker.get("index"), -1) + 1
                    if marker_target > 0:
                        marker_targets.append(marker_target)
        available_set = set(available_targets)
        for marker_target in reversed(marker_targets):
            if marker_target in revealed_targets:
                continue
            if available_set and marker_target not in available_set:
                continue
            hint_target = marker_target
            break
    if hint_target <= 0 and hint_text:
        hint_target = _extract_hint_target(hint_text)

    return {
        "on_treasure_tab": bool(on_treasure_tab),
        "in_round": bool(in_round),
        "session_id": str(hunt_run.get("sessionId") or treasure.get("sessionId") or "").strip(),
        "status": status,
        "action_remaining": max(0, sense_ratio[0]),
        "action_limit": max(0, sense_ratio[1]),
        "games_used": max(0, games_ratio[0]),
        "games_limit": max(0, games_ratio[1]),
        "treasure_found": bool(treasure_found),
        "settled": bool(settled),
        "hint_text": sanitize_webapp_secret_text(hint_text, limit=160),
        "hint_target": max(0, hint_target),
        "target_count": max(1, target_count),
        "available_targets": available_targets,
    }


def choose_cave_treasure_action(state, *, rng=None):
    rng = rng or random
    state = dict(state or {})
    games_limit = _coerce_int(state.get("games_limit"), 0)
    games_used = _coerce_int(state.get("games_used"), 0)
    action_remaining = _coerce_int(state.get("action_remaining"), 0)
    target_count = max(1, _coerce_int(state.get("target_count"), 1))
    in_round = bool(state.get("in_round"))
    session_id = str(state.get("session_id") or "").strip()

    if games_limit > 0 and games_used >= games_limit and not in_round:
        return {"action": "done", "reason": "daily_games_exhausted"}
    if not in_round:
        return {"action": "enter", "reason": "not_in_round"}
    if state.get("treasure_found"):
        return {"action": "settle", "sessionId": session_id, "reason": "treasure_found"}
    if str(state.get("status") or "").strip() == "failed":
        return {"action": "settle", "sessionId": session_id, "reason": "round_failed"}
    if action_remaining > 0:
        target_index = _coerce_int(state.get("hint_target"), 0)
        candidates = [
            _coerce_int(item, 0)
            for item in state.get("available_targets") or ()
            if _coerce_int(item, 0) > 0
        ]
        if target_index > 0 and candidates and target_index not in candidates:
            target_index = 0
        reason = "hint_target" if target_index > 0 else "random_target"
        if target_index <= 0:
            target_index = int(rng.choice(candidates)) if candidates else int(rng.randint(1, target_count))
        return {
            "action": "search",
            "sessionId": session_id,
            "targetIndex": max(1, min(target_index, target_count)),
            "reason": reason,
        }
    return {"action": "settle", "sessionId": session_id, "reason": "round_actions_exhausted"}


def build_cave_treasure_action_request(decision, *, token, init_data_session=None, init_data="", adapter=None):
    decision = dict(decision or {})
    action = str(decision.get("action") or "").strip()
    if action not in CAVE_TREASURE_SENDABLE_ACTIONS:
        raise ValueError(f"cave treasure action not sendable: {action or 'missing'}")
    payload = {}
    endpoint = "hunt"
    if action == "search":
        endpoint = "hunt_reveal"
        payload["sessionId"] = str(decision.get("sessionId") or "").strip()
        payload["index"] = max(0, _coerce_int(decision.get("targetIndex"), 1) - 1)
    elif action == "settle":
        endpoint = "hunt_settle"
        payload["sessionId"] = str(decision.get("sessionId") or "").strip()
    return build_cave_treasure_miniapp_request(
        endpoint,
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload=payload,
        adapter=adapter,
    )


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
        "status_code": result.status_code,
        "error_type": result.error_type,
        "attempts": result.attempts,
        "data_keys": sorted(result.data) if isinstance(result.data, dict) else [],
        "error": sanitize_webapp_secret_text(result.error),
    })


def run_cave_treasure_miniapp_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    max_steps=32,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_cave_treasure_miniapp_request("start", token=token, init_data=init_data, adapter=adapter)
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
    last_state = {}
    results = []
    for _step_index in range(max(1, int(max_steps or 1))):
        state = parse_cave_treasure_state(current_data)
        last_state = state
        decision = choose_cave_treasure_action(state, rng=rng)
        events.append({
            "step": "decide",
            "ok": True,
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "action_remaining": state.get("action_remaining", 0),
            "games": f"{state.get('games_used', 0)}/{state.get('games_limit', 0)}",
        })
        if decision.get("action") == "done":
            return _flow_result(
                True,
                "daily_limit",
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )
        if decision.get("action") not in CAVE_TREASURE_SENDABLE_ACTIONS:
            return _flow_result(
                False,
                "blocked",
                error=f"unsendable action: {decision.get('action')}",
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )

        action_request = build_cave_treasure_action_request(
            decision,
            token=token,
            init_data=init_data,
            adapter=adapter,
        )
        action_result = execute_miniapp_http_request(
            action_request,
            transport,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"action:{decision.get('action')}",
        )
        _append_http_event(events, f"action:{decision.get('action')}", action_result)
        if not action_result.ok:
            return _flow_result(
                False,
                "failed",
                error=action_result.error,
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )
        current_data = action_result.data
        if decision.get("action") == "settle":
            hunt_result = current_data.get("huntResult") if isinstance(current_data.get("huntResult"), dict) else {}
            results.append(dict(hunt_result or current_data or {}))

    return _flow_result(
        False,
        "step_limit",
        data={"state": last_state, "results": results, "settled_count": len(results)},
        events=events,
    )


async def run_cave_treasure_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    transport=None,
    adapter=None,
    rng=None,
    sleeper=None,
    max_steps=32,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = await request_cave_treasure_miniapp_init_data(identity_id, token=token, webview_url=webview_url, adapter=adapter)
        return await asyncio.to_thread(
            run_cave_treasure_miniapp_lab_flow,
            token=token,
            init_data=init_data,
            transport=transport or _requests_transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper or time.sleep,
            max_steps=max_steps,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "CAVE_TREASURE_MINIAPP_GAME_KEY",
    "CAVE_TREASURE_MINIAPP_ENDPOINTS",
    "build_cave_treasure_action_request",
    "build_cave_treasure_launch_args",
    "build_cave_treasure_miniapp_adapter",
    "build_cave_treasure_miniapp_flow_plan",
    "build_cave_treasure_miniapp_request",
    "choose_cave_treasure_action",
    "extract_cave_treasure_miniapp_launch",
    "parse_cave_treasure_state",
    "request_cave_treasure_miniapp_init_data",
    "run_cave_treasure_miniapp_lab_flow",
    "run_cave_treasure_miniapp_production_flow",
    "summarize_cave_treasure_entry",
]
