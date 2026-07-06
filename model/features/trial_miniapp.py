import asyncio
import random
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


TRIAL_MINIAPP_GAME_KEY = "trial"
TRIAL_MINIAPP_LABEL = "天机试炼"
TRIAL_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
TRIAL_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
TRIAL_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-trial/"
TRIAL_MINIAPP_ENDPOINTS = {
    "start": f"{TRIAL_MINIAPP_API_PATH_PREFIX}start",
    "finish": f"{TRIAL_MINIAPP_API_PATH_PREFIX}finish",
    "next": f"{TRIAL_MINIAPP_API_PATH_PREFIX}next",
}
TRIAL_MINIAPP_START_PARAM_PATTERN = r"(?:trial_)?[A-Za-z0-9_-]{4,160}"
TRIAL_MINIAPP_DEFAULT_DURATION_PADDING_MS = (1_000, 15_000)
TRIAL_MINIAPP_DEFAULT_MIN_DURATION_MS = 3_200
TRIAL_MINIAPP_DEFAULT_MAX_DURATION_MS = 90_000
TRIAL_MINIAPP_HTTP_TIMEOUT = (5, 20)
TRIAL_MINIAPP_STOP_ERROR_KEYWORDS = (
    "daily_limit",
    "no_remaining",
    "次数已尽",
    "today_exhausted",
    "limit_reached",
    "剩余 0",
)


def build_trial_miniapp_adapter(*, api_base_url=TRIAL_MINIAPP_DEFAULT_API_BASE_URL, bot_username=TRIAL_MINIAPP_DEFAULT_BOT_USERNAME):
    return MiniAppAdapter(
        game_key=TRIAL_MINIAPP_GAME_KEY,
        label=TRIAL_MINIAPP_LABEL,
        bot_username=bot_username,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(TRIAL_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(TRIAL_MINIAPP_ENDPOINTS),
        start_param_pattern=TRIAL_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_trial_miniapp_request(endpoint, *, token, init_data_session=None, init_data="", payload=None, adapter=None):
    adapter = adapter or build_trial_miniapp_adapter()
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


def extract_trial_miniapp_launch(event, *, message_text=""):
    adapter = build_trial_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event):
        if not url:
            continue
        summary = summarize_trial_entry(url, button_text=button_text, message_text=message_text)
        if not summary or summary.get("game_hint") != TRIAL_MINIAPP_GAME_KEY:
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


async def request_trial_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_trial_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "trial miniapp launch not allowed")
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
        timeout=TRIAL_MINIAPP_HTTP_TIMEOUT,
    )
    return response


def build_trial_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=TRIAL_MINIAPP_GAME_KEY,
        label=TRIAL_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only trial declaration; production scheduler is not wired",
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
                note="读取试炼 challenge，包括 sequence/trapIds",
            ),
            MiniAppFlowStep(
                key="solve",
                endpoint="local_solver",
                method="LOCAL",
                required_payload_keys=("challenge",),
                sends_init_data=False,
                note="本地按 sequence 生成 trialProof，不触发陷阱",
            ),
            MiniAppFlowStep(
                key="finish",
                endpoint="finish",
                required_payload_keys=("token", "initData", "trialProof"),
                note="提交 trialProof",
            ),
            MiniAppFlowStep(
                key="next",
                endpoint="next",
                required_payload_keys=("token", "initData"),
                note="可选连刷 token，默认仍需 UI/开关控制",
            ),
        ),
    )


def summarize_trial_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = TRIAL_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def build_trial_launch_args(url, *, start_param="", bot_username=TRIAL_MINIAPP_DEFAULT_BOT_USERNAME):
    adapter = build_trial_miniapp_adapter(bot_username=bot_username)
    request = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return request, build_request_webview_args(adapter, request) if request.allowed else {}


def classify_trial_miniapp_error(error):
    raw = str(error or "").strip()
    lowered = raw.lower()
    if any(keyword in lowered for keyword in TRIAL_MINIAPP_STOP_ERROR_KEYWORDS):
        return "daily_limit"
    return "failed"


def build_trial_proof(challenge, *, rng=None):
    rng = rng or random
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise ValueError("challengeId missing")

    sequence = list(challenge.get("sequence") or ())
    points = list(challenge.get("points") or ())
    trap_ids = {str(item) for item in (challenge.get("trapIds") or ())}
    point_map = {str(point.get("id")): dict(point) for point in points if isinstance(point, dict)}
    taps = []
    trap_hits = 0
    for raw_point_id in sequence:
        point_id = str(raw_point_id)
        point = point_map.get(point_id) or {}
        if point_id in trap_ids:
            trap_hits += 1
            continue
        taps.append({
            "id": raw_point_id,
            "x": point.get("x", 50),
            "y": point.get("y", 50),
        })

    try:
        min_duration_ms = int(challenge.get("minDurationMs", TRIAL_MINIAPP_DEFAULT_MIN_DURATION_MS) or TRIAL_MINIAPP_DEFAULT_MIN_DURATION_MS)
    except (TypeError, ValueError, OverflowError):
        min_duration_ms = TRIAL_MINIAPP_DEFAULT_MIN_DURATION_MS
    try:
        max_duration_ms = int(challenge.get("maxDurationMs", TRIAL_MINIAPP_DEFAULT_MAX_DURATION_MS) or TRIAL_MINIAPP_DEFAULT_MAX_DURATION_MS)
    except (TypeError, ValueError, OverflowError):
        max_duration_ms = TRIAL_MINIAPP_DEFAULT_MAX_DURATION_MS

    pad_low, pad_high = TRIAL_MINIAPP_DEFAULT_DURATION_PADDING_MS
    lower = max(min_duration_ms + pad_low, 5_000)
    upper = min(max_duration_ms, min_duration_ms + pad_high)
    if upper < lower:
        upper = lower
    duration_ms = int(rng.randint(lower, upper))
    proof = {
        "mode": str(challenge.get("mode") or "tianjiMeridianV1"),
        "challengeId": challenge_id,
        "durationMs": duration_ms,
        "sequence": sequence,
        "taps": taps,
        "trapHits": trap_hits,
        "misses": int(rng.randint(0, 1)),
    }
    return proof


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


def _challenge_from_start(data):
    data = dict(data or {})
    challenge = data.get("challenge") if isinstance(data.get("challenge"), dict) else {}
    trial = data.get("trial") if isinstance(data.get("trial"), dict) else {}
    return challenge, trial


def run_trial_miniapp_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_trial_miniapp_request("start", token=token, init_data=init_data, adapter=adapter)
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
        status = classify_trial_miniapp_error(start_result.error)
        return _flow_result(False, status, error=start_result.error, events=events)

    challenge, trial = _challenge_from_start(start_result.data)
    if not challenge:
        return _flow_result(False, "not_ready", data={"trial_keys": sorted(trial)}, events=events)
    proof = build_trial_proof(challenge, rng=rng)
    events.append({
        "step": "solve",
        "ok": True,
        "mode": proof["mode"],
        "sequence_len": len(proof.get("sequence") or ()),
        "trapHits": proof.get("trapHits", 0),
        "durationMs": proof["durationMs"],
    })
    if sleeper is not None:
        sleeper(float(proof["durationMs"]) / 1000.0)

    finish_request = build_trial_miniapp_request(
        "finish",
        token=token,
        init_data=init_data,
        payload={"trialProof": proof},
        adapter=adapter,
    )
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
        status = classify_trial_miniapp_error(finish_result.error)
        return _flow_result(False, status, error=finish_result.error, events=events, proof=proof)

    data = finish_result.data.get("result") if isinstance(finish_result.data.get("result"), dict) else finish_result.data
    return _flow_result(True, "settled", data=data, events=events, proof=proof)


def _extract_next_trial_token(data):
    data = dict(data or {})
    token = str(data.get("token") or data.get("nextToken") or data.get("trialToken") or "").strip()
    return token


def run_trial_miniapp_loop_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    max_rounds=99,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    results = []
    current_token = token
    max_rounds = max(1, int(max_rounds or 1))
    for round_index in range(1, max_rounds + 1):
        round_result = run_trial_miniapp_lab_flow(
            token=current_token,
            init_data=init_data,
            transport=transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        events.append({
            "step": "round",
            "round": round_index,
            "ok": bool(round_result.get("ok")),
            "status": str(round_result.get("status") or ""),
            "event_count": len(round_result.get("events") or ()),
        })
        if not round_result.get("ok"):
            status = str(round_result.get("status") or "failed")
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(bool(results), status if not results else "partial", error=round_result.get("error", ""), data=data, events=events)

        results.append(dict(round_result.get("data") or {}))
        next_request = build_trial_miniapp_request(
            "next",
            token=current_token,
            init_data=init_data,
            adapter=adapter,
        )
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
            status = classify_trial_miniapp_error(next_result.error)
            if status == "daily_limit":
                break
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(True, "next_failed", error=next_result.error, data=data, events=events)

        next_token = _extract_next_trial_token(next_result.data)
        if not next_token:
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(True, "next_unavailable", data=data, events=events)
        current_token = next_token

    data = {"results": results, "settled_count": len(results)}
    return _flow_result(True, "settled", data=data, events=events)


async def run_trial_miniapp_production_flow(
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
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = await request_trial_miniapp_init_data(identity_id, token=token, webview_url=webview_url, adapter=adapter)
        runner = run_trial_miniapp_loop_lab_flow if int(max_rounds or 1) > 1 else run_trial_miniapp_lab_flow
        kwargs = {
            "token": token,
            "init_data": init_data,
            "transport": transport or _requests_transport,
            "adapter": adapter,
            "sleeper": sleeper or time.sleep,
            "capture_sink": capture_sink,
            "capture_source": capture_source,
        }
        if runner is run_trial_miniapp_loop_lab_flow:
            kwargs["max_rounds"] = max_rounds
        return await asyncio.to_thread(runner, **kwargs)
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "TRIAL_MINIAPP_ENDPOINTS",
    "TRIAL_MINIAPP_GAME_KEY",
    "build_trial_launch_args",
    "build_trial_miniapp_adapter",
    "build_trial_miniapp_flow_plan",
    "build_trial_miniapp_request",
    "build_trial_proof",
    "classify_trial_miniapp_error",
    "extract_trial_miniapp_launch",
    "request_trial_miniapp_init_data",
    "run_trial_miniapp_lab_flow",
    "run_trial_miniapp_loop_lab_flow",
    "run_trial_miniapp_production_flow",
    "summarize_trial_entry",
]
