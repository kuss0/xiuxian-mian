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
    build_miniapp_launch_request,
    build_miniapp_http_request,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)


FISHING_MINIAPP_GAME_KEY = "fishing"
FISHING_MINIAPP_LABEL = "灵溪垂钓"
FISHING_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
FISHING_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
FISHING_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-fishing/"
FISHING_MINIAPP_ENDPOINTS = {
    "start": f"{FISHING_MINIAPP_API_PATH_PREFIX}start",
    "finish": f"{FISHING_MINIAPP_API_PATH_PREFIX}finish",
    "result": f"{FISHING_MINIAPP_API_PATH_PREFIX}result",
    "next": f"{FISHING_MINIAPP_API_PATH_PREFIX}next",
}
FISHING_MINIAPP_START_PARAM_PATTERN = r"(?:fish_)?[A-Za-z0-9_-]{4,160}"
FISHING_MINIAPP_DEFAULT_SCORE_LOW = 92
FISHING_MINIAPP_DEFAULT_SCORE_HIGH = 97
FISHING_MINIAPP_PROOF_DURATION_CAP_MS = 120_000
FISHING_MINIAPP_BITE_WAIT_CAP_MS = 20_000
FISHING_MINIAPP_PLAY_RANGE_MS = (9_500, 15_500)
FISHING_MINIAPP_RESULT_POLL_LIMIT = 8
FISHING_MINIAPP_RESULT_POLL_DELAY_SEC = 1.5
FISHING_MINIAPP_PRODUCTION_BITE_WAIT_CAP_MS = 75_000
FISHING_MINIAPP_HTTP_TIMEOUT = (5, 20)
FISHING_MINIAPP_CHAIN_REST_RANGE_SEC = (2.0, 4.0)

FISHING_MINIAPP_ALREADY_SETTLED_ERRORS = {
    "fishing_token_used",
    "fishing_session_closed",
}
FISHING_MINIAPP_EXPIRED_ERRORS = {
    "fishing_token_expired",
    "fishing_bite_expired",
    "fishing_too_slow",
    "fishing_session_missing",
    "fishing_challenge_expired",
}
FISHING_MINIAPP_UNBINDABLE_ERRORS = {
    "fishing_token_channel_unbound",
    "fishing_token_user_mismatch",
}
FISHING_MINIAPP_DAILY_LIMIT_ERRORS = {
    "daily_limit",
    "no_remaining",
    "remaining_empty",
    "fishing_daily_limit",
    "fishing_no_remaining",
    "次数已尽",
    "次数用完",
}


def build_fishing_miniapp_adapter(*, api_base_url=FISHING_MINIAPP_DEFAULT_API_BASE_URL, bot_username=FISHING_MINIAPP_DEFAULT_BOT_USERNAME):
    return MiniAppAdapter(
        game_key=FISHING_MINIAPP_GAME_KEY,
        label=FISHING_MINIAPP_LABEL,
        bot_username=bot_username,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(FISHING_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(FISHING_MINIAPP_ENDPOINTS),
        start_param_pattern=FISHING_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_fishing_miniapp_request(endpoint, *, token, init_data_session=None, init_data="", payload=None, adapter=None):
    adapter = adapter or build_fishing_miniapp_adapter()
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


def extract_fishing_miniapp_launch(event, *, message_text=""):
    adapter = build_fishing_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event):
        if not url:
            continue
        summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
        if summary.get("game_hint") != FISHING_MINIAPP_GAME_KEY:
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


async def request_fishing_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_fishing_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "fishing miniapp launch not allowed")
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
    response = requests.request(
        str(request.get("method") or "POST"),
        request["url"],
        json=request.get("payload") or {},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            **dict(request.get("headers") or {}),
        },
        proxies=TG_REQUESTS_PROXIES,
        timeout=FISHING_MINIAPP_HTTP_TIMEOUT,
    )
    return response


async def run_fishing_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    max_rounds=1,
    transport=None,
    sleeper=None,
    adapter=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_fishing_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = await request_fishing_miniapp_init_data(identity_id, token=token, webview_url=webview_url, adapter=adapter)
        result = await asyncio.to_thread(
            run_fishing_miniapp_loop_lab_flow,
            token=token,
            init_data=init_data,
            transport=transport or _requests_transport,
            adapter=adapter,
            sleeper=sleeper or time.sleep,
            max_rounds=max_rounds,
            bite_wait_cap_ms=FISHING_MINIAPP_PRODUCTION_BITE_WAIT_CAP_MS,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        return result
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


def build_fishing_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=FISHING_MINIAPP_GAME_KEY,
        label=FISHING_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only flow declaration; production fishing scheduler is not wired",
        steps=(
            MiniAppFlowStep(
                key="launch",
                endpoint="telegram_webview",
                method="TELEGRAM",
                required_payload_keys=("token",),
                sends_init_data=False,
                note="RequestWebView 获取短 TTL initData，不落盘",
            ),
            MiniAppFlowStep(
                key="start_waiting",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="第一次 /start 返回 biteAt/serverNow",
            ),
            MiniAppFlowStep(
                key="wait_bite",
                endpoint="local_timer",
                method="LOCAL",
                required_payload_keys=("biteAt", "serverNow"),
                sends_init_data=False,
                waits_for="biteAt",
                note="本地等待咬钩，不能忙轮询",
            ),
            MiniAppFlowStep(
                key="start_bite",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="第二次 /start 返回 challengeId",
            ),
            MiniAppFlowStep(
                key="finish",
                endpoint="finish",
                required_payload_keys=("token", "initData", "fishingProof"),
                note="提交自然分 proof，不固定满分",
            ),
            MiniAppFlowStep(
                key="result",
                endpoint="result",
                required_payload_keys=("token", "initData"),
                poll_until_key="ready",
                note="有限轮询 ready=true",
            ),
            MiniAppFlowStep(
                key="next",
                endpoint="next",
                required_payload_keys=("token", "initData"),
                note="可选连钓 token，默认仍需 UI/开关控制",
            ),
        ),
    )


def classify_fishing_miniapp_error(error):
    code = str(error or "").strip()
    if code in FISHING_MINIAPP_ALREADY_SETTLED_ERRORS:
        return "already_settled"
    if code in FISHING_MINIAPP_EXPIRED_ERRORS:
        return "expired"
    if code in FISHING_MINIAPP_UNBINDABLE_ERRORS:
        return "unbindable"
    if code in FISHING_MINIAPP_DAILY_LIMIT_ERRORS or any(keyword in code for keyword in FISHING_MINIAPP_DAILY_LIMIT_ERRORS):
        return "daily_limit"
    return "failed"


def _rand_float(rng, low, high):
    return float(rng.uniform(float(low), float(high)))


def _rand_int(rng, low, high):
    return int(rng.randint(int(low), int(high)))


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _score_from_proof(progress, stability, danger_ms, slack_ms):
    penalty = float(danger_ms) / 430.0 + float(slack_ms) / 520.0 + max(0.0, 100.0 - float(progress)) * 0.04
    score = round(72.0 + float(stability) * 28.0 - penalty)
    return int(_clamp(score, 55, 100))


def build_fishing_proof(
    challenge,
    *,
    rng=None,
    score_low=FISHING_MINIAPP_DEFAULT_SCORE_LOW,
    score_high=FISHING_MINIAPP_DEFAULT_SCORE_HIGH,
    play_range_ms=FISHING_MINIAPP_PLAY_RANGE_MS,
):
    rng = rng or random
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise ValueError("challengeId missing")
    try:
        min_duration_ms = int(challenge.get("minDurationMs", 4200) or 4200)
    except (TypeError, ValueError, OverflowError):
        min_duration_ms = 4200
    try:
        max_duration_ms = float(challenge.get("maxDurationMs", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        max_duration_ms = 0

    low = int(_clamp(int(score_low or FISHING_MINIAPP_DEFAULT_SCORE_LOW), 55, 100))
    high = int(_clamp(int(score_high or FISHING_MINIAPP_DEFAULT_SCORE_HIGH), 55, 100))
    if low > high:
        low, high = high, low
    target_score = _rand_int(rng, low, high)

    min_duration_ms = float(min_duration_ms if min_duration_ms > 0 else 4200)
    play_low, play_high = play_range_ms
    duration_ms = max(min_duration_ms * 1.1, _rand_float(rng, play_low, play_high))
    if max_duration_ms > 0:
        duration_ms = min(duration_ms, max_duration_ms - 2000)
    duration_ms = min(max(duration_ms, min_duration_ms * 1.05), FISHING_MINIAPP_PROOF_DURATION_CAP_MS)
    duration_ms = round(duration_ms)

    danger_ratio = _rand_float(rng, 0.35, 0.65)
    penalty_rate = danger_ratio / 430.0 + (1.0 - danger_ratio) / 520.0
    penalty_scale = duration_ms * penalty_rate
    stability = ((target_score - 72.0 + penalty_scale) / (28.0 + penalty_scale))
    stability = _clamp(stability, 0.05, 0.99)
    out_of_bounds_ms = (1.0 - stability) * duration_ms
    danger_ms = round(danger_ratio * out_of_bounds_ms)
    slack_ms = round((1.0 - danger_ratio) * out_of_bounds_ms)
    samples = max(1, round((duration_ms / 16.7) * _rand_float(rng, 0.90, 0.99)))
    actions = _rand_int(rng, 6, 18)
    progress = 100.0
    score = _score_from_proof(progress, stability, danger_ms, slack_ms)
    return {
        "mode": str(challenge.get("mode") or "xianxiaFishingV1"),
        "challengeId": challenge_id,
        "durationMs": int(duration_ms),
        "progress": progress,
        "score": score,
        "stability": stability,
        "samples": int(samples),
        "actions": int(actions),
        "dangerMs": int(danger_ms),
        "slackMs": int(slack_ms),
    }


def _extract_start_view(data):
    data = dict(data or {})
    session = data.get("session") if isinstance(data.get("session"), dict) else {}
    challenge = data.get("challenge") if isinstance(data.get("challenge"), dict) else None
    bite_at = float(session.get("biteAt") or 0)
    server_now = float(session.get("serverNow") or 0)
    return {
        "phase": str(session.get("phase") or ""),
        "bite_in_ms": bite_at - server_now,
        "challenge": challenge,
    }


def _flow_result(ok, status, *, error="", data=None, events=None, proof=None):
    return {
        "ok": bool(ok),
        "status": status,
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
        "proof": dict(proof or {}),
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


def _extract_next_token(data):
    data = dict(data or {})
    for key in ("token", "nextToken", "next_token", "startParam", "start_param"):
        token = str(data.get(key) or "").strip()
        if token:
            return token
    nested = data.get("next") if isinstance(data.get("next"), dict) else {}
    for key in ("token", "nextToken", "next_token", "startParam", "start_param"):
        token = str(nested.get(key) or "").strip()
        if token:
            return token
    return ""


def _as_clean_text(value):
    text = sanitize_webapp_secret_text(value)
    return str(text or "").strip()


def _find_first_text(data, keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        if key in data:
            text = _as_clean_text(data.get(key))
            if text:
                return text
    return ""


def _extract_reward_items(data):
    rewards = []
    if not isinstance(data, dict):
        return rewards
    reward_containers = ("rewards", "reward", "bonusLoot", "bonus_loot", "loot", "drops", "items", "materials")
    raw_items = []
    for key in reward_containers:
        value = data.get(key)
        if isinstance(value, list):
            raw_items.extend(value)
        elif isinstance(value, dict):
            raw_items.extend(value.values() if all(not isinstance(v, (str, int, float)) for v in value.values()) else [value])
    for item in raw_items:
        if isinstance(item, str):
            name = _as_clean_text(item)
            qty = 1
        elif isinstance(item, dict):
            name = _find_first_text(item, ("name", "itemName", "item_name", "title", "label"))
            qty = item.get("qty", item.get("count", item.get("quantity", item.get("amount", 1))))
        else:
            continue
        try:
            qty = int(qty or 1)
        except (TypeError, ValueError, OverflowError):
            qty = 1
        if name:
            rewards.append({"name": name, "qty": max(1, qty)})
    return rewards


def _extract_catch_from_text(text):
    text = _as_clean_text(text)
    if not text:
        return {}
    fish = ""
    for pattern in (
        r"竟是[^\n【]*【(?P<fish>[^】]+)】",
        r"钓获[^\n【]*【(?P<fish>[^】]+)】",
        r"鱼获[^\n【]*【(?P<fish>[^】]+)】",
    ):
        match = re.search(pattern, text)
        if match:
            fish = _as_clean_text(match.group("fish"))
            break
    if not fish:
        return {}
    grade = ""
    match = re.search(r"品阶[:：]\s*(?P<grade>[^\n\r]+)", text)
    if match:
        grade = _as_clean_text(match.group("grade"))
    weight = ""
    match = re.search(r"重量[:：]\s*(?P<weight>[\d,.]+)\s*斤", text)
    if match:
        weight = f"{_as_clean_text(match.group('weight'))}斤"
    rewards = []
    for match in re.finditer(r"伴生机缘[:：]\s*【(?P<name>[^】]+)】(?:x(?P<qty>\d+))?", text):
        rewards.append({"name": _as_clean_text(match.group("name")), "qty": int(match.group("qty") or 1)})
    return {"fish": fish, "grade": grade, "weight": weight, "rewards": rewards, "companion": bool(rewards)}


def _extract_catch_from_mapping(data, *, context=""):
    if not isinstance(data, dict):
        return {}
    fish_value = data.get("fish")
    if isinstance(fish_value, dict):
        nested = _extract_catch_from_mapping(fish_value, context="fish")
        if nested:
            fish = nested.get("fish") or _find_first_text(data, ("fishName", "fish_name", "name"))
            nested["fish"] = fish or nested.get("fish", "")
            nested["grade"] = nested.get("grade") or _find_first_text(
                data,
                ("grade", "quality", "qualityLabel", "quality_label", "rank", "rarityLabel", "rarity_label", "rarity", "品阶"),
            )
            nested["rewards"] = nested.get("rewards") or _extract_reward_items(data)
            nested["companion"] = bool(nested.get("companion") or data.get("companion") or "伴生" in str(data))
            return nested
    fish = _as_clean_text(fish_value) if isinstance(fish_value, str) else ""
    fish = fish or _find_first_text(data, ("fishName", "fish_name", "fishTitle", "fish_title"))
    if not fish and context in {"details", "catch", "result", "fish"}:
        fish = _find_first_text(data, ("name", "title", "label"))
    if not fish:
        return {}
    grade = _find_first_text(data, ("grade", "quality", "qualityLabel", "quality_label", "rank", "rarity", "品阶"))
    raw_weight = data.get("weight", data.get("weightJin", data.get("weight_jin", data.get("jin", ""))))
    weight = ""
    if raw_weight not in (None, ""):
        weight_text = _as_clean_text(raw_weight)
        weight = weight_text if "斤" in weight_text else f"{weight_text}斤"
    rewards = _extract_reward_items(data)
    return {
        "fish": fish,
        "grade": grade,
        "weight": weight,
        "rewards": rewards,
        "companion": bool(data.get("companion") or data.get("companionChance") or data.get("bonus") or rewards),
    }


def extract_fishing_miniapp_catches(data):
    """Return non-sensitive catch summaries from a MiniApp result payload."""
    catches = []

    def visit(value, context=""):
        if isinstance(value, str):
            parsed = _extract_catch_from_text(value)
            if parsed:
                catches.append(parsed)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, context=context)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("catches"), list):
            for item in value.get("catches") or ():
                visit(item, context="catch")
        parsed = _extract_catch_from_mapping(value, context=context)
        if parsed:
            catches.append(parsed)
        for key in ("details", "detail", "catch", "result", "last_details", "last_result"):
            if key in value:
                visit(value.get(key), context=key.replace("last_", ""))

    visit(data or {}, context="result")
    deduped = []
    seen = set()
    for item in catches:
        fish = _as_clean_text(item.get("fish"))
        if not fish:
            continue
        entry = {
            "fish": fish,
            "grade": _as_clean_text(item.get("grade")),
            "weight": _as_clean_text(item.get("weight")),
            "rewards": [
                {"name": _as_clean_text(reward.get("name")), "qty": int(reward.get("qty") or 1)}
                for reward in (item.get("rewards") or ())
                if isinstance(reward, dict) and _as_clean_text(reward.get("name"))
            ],
            "companion": bool(item.get("companion")),
        }
        key = (entry["fish"], entry["grade"], entry["weight"], tuple((r["name"], r["qty"]) for r in entry["rewards"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def run_fishing_miniapp_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    bite_wait_cap_ms=FISHING_MINIAPP_BITE_WAIT_CAP_MS,
    result_poll_limit=FISHING_MINIAPP_RESULT_POLL_LIMIT,
    score_low=FISHING_MINIAPP_DEFAULT_SCORE_LOW,
    score_high=FISHING_MINIAPP_DEFAULT_SCORE_HIGH,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_fishing_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_fishing_miniapp_request("start", token=token, init_data=init_data, adapter=adapter)
    start_result = execute_miniapp_http_request(
        start_request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start_waiting",
    )
    _append_http_event(events, "start_waiting", start_result)
    if not start_result.ok:
        status = classify_fishing_miniapp_error(start_result.error)
        return _flow_result(False, status, error=start_result.error, events=events)

    view = _extract_start_view(start_result.data)
    if view["challenge"] is None:
        if view["phase"] == "expired":
            return _flow_result(False, "expired", error="session_phase_expired", events=events)
        if view["phase"] != "waiting" or view["bite_in_ms"] > float(bite_wait_cap_ms or 0):
            return _flow_result(False, "not_ready", data={"phase": view["phase"], "bite_in_ms": view["bite_in_ms"]}, events=events)
        wait_ms = max(0.0, view["bite_in_ms"])
        events.append({"step": "wait_bite", "ok": True, "wait_ms": wait_ms})
        if sleeper is not None:
            sleeper(wait_ms / 1000.0)
        start_request = build_fishing_miniapp_request("start", token=token, init_data=init_data, adapter=adapter)
        start_result = execute_miniapp_http_request(
            start_request,
            transport,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="start_bite",
        )
        _append_http_event(events, "start_bite", start_result)
        if not start_result.ok:
            status = classify_fishing_miniapp_error(start_result.error)
            return _flow_result(False, status, error=start_result.error, events=events)
        view = _extract_start_view(start_result.data)

    challenge = view["challenge"]
    if not challenge:
        return _flow_result(False, "not_ready", data={"phase": view["phase"]}, events=events)
    proof = build_fishing_proof(challenge, rng=rng, score_low=score_low, score_high=score_high)
    events.append({"step": "build_proof", "ok": True, "score": proof["score"], "durationMs": proof["durationMs"]})

    finish_request = build_fishing_miniapp_request("finish", token=token, init_data=init_data, payload={"fishingProof": proof}, adapter=adapter)
    finish_result = execute_miniapp_http_request(
        finish_request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="finish",
    )
    _append_http_event(events, "finish", finish_result)
    if not finish_result.ok:
        status = classify_fishing_miniapp_error(finish_result.error)
        return _flow_result(False, status, error=finish_result.error, events=events, proof=proof)

    result_data = finish_result.data.get("result") if isinstance(finish_result.data.get("result"), dict) else {}
    for attempt in range(max(0, int(result_poll_limit or 0))):
        result_request = build_fishing_miniapp_request("result", token=token, init_data=init_data, adapter=adapter)
        result = execute_miniapp_http_request(
            result_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="result",
        )
        _append_http_event(events, "result", result)
        if not result.ok:
            status = classify_fishing_miniapp_error(result.error)
            return _flow_result(False, status, error=result.error, events=events, proof=proof)
        nested_result = result.data.get("result") if isinstance(result.data.get("result"), dict) else {}
        ready = result.data.get("ready")
        if ready is None and nested_result:
            ready = nested_result.get("ready")
        if ready is True:
            result_data = nested_result or result.data
            return _flow_result(True, "settled", data=result_data, events=events, proof=proof)
        events.append({"step": "result_wait", "ok": True, "attempt": attempt + 1, "ready": bool(ready)})
        if sleeper is not None and attempt < max(0, int(result_poll_limit or 0)) - 1:
            sleeper(FISHING_MINIAPP_RESULT_POLL_DELAY_SEC)

    return _flow_result(
        False,
        "not_ready",
        error="result_not_ready",
        data={"phase": "finish_submitted", "ready": False},
        events=events,
        proof=proof,
    )


def run_fishing_miniapp_loop_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    max_rounds=1,
    bite_wait_cap_ms=FISHING_MINIAPP_BITE_WAIT_CAP_MS,
    result_poll_limit=FISHING_MINIAPP_RESULT_POLL_LIMIT,
    score_low=FISHING_MINIAPP_DEFAULT_SCORE_LOW,
    score_high=FISHING_MINIAPP_DEFAULT_SCORE_HIGH,
    rest_range_sec=FISHING_MINIAPP_CHAIN_REST_RANGE_SEC,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_fishing_miniapp_adapter()
    try:
        max_rounds = max(1, int(max_rounds or 1))
    except (TypeError, ValueError, OverflowError):
        max_rounds = 1
    current_token = str(token or "").strip()
    events = []
    rounds = []
    settled_count = 0
    last_result = {}
    last_status = "failed"
    last_error = ""

    for index in range(max_rounds):
        round_result = run_fishing_miniapp_lab_flow(
            token=current_token,
            init_data=init_data,
            transport=transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper,
            bite_wait_cap_ms=bite_wait_cap_ms,
            result_poll_limit=result_poll_limit,
            score_low=score_low,
            score_high=score_high,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        last_result = dict(round_result or {})
        last_status = str(last_result.get("status") or "").strip()
        last_error = str(last_result.get("error") or "").strip()
        catch_summary = extract_fishing_miniapp_catches(last_result.get("data") or {})
        rounds.append({
            "index": index + 1,
            "ok": bool(last_result.get("ok")),
            "status": last_status,
            "data_keys": sorted(last_result.get("data") or {}),
            "event_count": len(last_result.get("events") or ()),
            "proof_score": (last_result.get("proof") or {}).get("score"),
            "catch": catch_summary[0] if catch_summary else {},
        })
        events.append({
            "step": "round",
            "ok": bool(last_result.get("ok")),
            "index": index + 1,
            "status": last_status,
        })
        if not last_result.get("ok"):
            data = {
                "settled_count": settled_count,
                "rounds": rounds,
                "catches": [item.get("catch") for item in rounds if item.get("catch")],
                "last_status": last_status,
            }
            return _flow_result(settled_count > 0, last_status or "failed", error=last_error, data=data, events=events)

        settled_count += 1
        if index >= max_rounds - 1:
            break

        next_request = build_fishing_miniapp_request("next", token=current_token, init_data=init_data, adapter=adapter)
        next_result = execute_miniapp_http_request(
            next_request,
            transport,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="next",
        )
        _append_http_event(events, "next", next_result)
        if not next_result.ok:
            next_status = classify_fishing_miniapp_error(next_result.error)
            data = {
                "settled_count": settled_count,
                "rounds": rounds,
                "catches": [item.get("catch") for item in rounds if item.get("catch")],
                "last_status": last_status,
                "next_status": next_status,
                "next_error": next_result.error,
            }
            if isinstance(next_result.data, dict) and next_result.data.get("baitName"):
                data["next_bait_name"] = str(next_result.data.get("baitName") or "")
            ok = settled_count > 0
            return _flow_result(ok, "daily_limit" if next_status == "daily_limit" else "next_failed", error=next_result.error, data=data, events=events)

        new_token = _extract_next_token(next_result.data)
        if not new_token:
            data = {
                "settled_count": settled_count,
                "rounds": rounds,
                "catches": [item.get("catch") for item in rounds if item.get("catch")],
                "last_status": last_status,
                "next_status": "missing_token",
            }
            return _flow_result(True, "next_unavailable", error="next token missing", data=data, events=events)

        current_token = new_token
        if sleeper is not None:
            low, high = rest_range_sec
            sleeper(random.uniform(float(low), float(high)))

    data = {
        "settled_count": settled_count,
        "rounds": rounds,
        "catches": [item.get("catch") for item in rounds if item.get("catch")],
        "last_status": last_status,
    }
    if isinstance(last_result.get("data"), dict):
        data.update({f"last_{key}": value for key, value in last_result["data"].items() if key not in data})
    status = "settled" if all(item.get("status") == "settled" for item in rounds) else (last_status or "finish_submitted")
    return _flow_result(True, status, data=data, events=events)


__all__ = [
    "FISHING_MINIAPP_ENDPOINTS",
    "FISHING_MINIAPP_GAME_KEY",
    "build_fishing_proof",
    "build_fishing_miniapp_adapter",
    "build_fishing_miniapp_flow_plan",
    "build_fishing_miniapp_request",
    "classify_fishing_miniapp_error",
    "extract_fishing_miniapp_launch",
    "extract_fishing_miniapp_catches",
    "request_fishing_miniapp_init_data",
    "run_fishing_miniapp_lab_flow",
    "run_fishing_miniapp_loop_lab_flow",
    "run_fishing_miniapp_production_flow",
]
