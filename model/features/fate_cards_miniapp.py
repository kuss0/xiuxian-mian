"""Protocol adapter for the 天机命脉 MiniApp.

The public page exposes several non-idempotent POST actions.  The generic flow
plan remains read-only, while the dwelling runtime may execute an explicitly
configured choice with one POST per action and an authoritative ``/start``
reconciliation after each mutation.
"""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urljoin

from telethon import functions

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
)
from .miniapp_common import append_http_event, build_miniapp_transport


FATE_CARDS_MINIAPP_GAME_KEY = "fate_cards"
FATE_CARDS_MINIAPP_LABEL = "天机命脉"
FATE_CARDS_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
FATE_CARDS_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
FATE_CARDS_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-fate-cards/"
FATE_CARDS_MINIAPP_ENDPOINTS = {
    "start": f"{FATE_CARDS_MINIAPP_API_PATH_PREFIX}start",
    "draw": f"{FATE_CARDS_MINIAPP_API_PATH_PREFIX}draw",
    "interpret": f"{FATE_CARDS_MINIAPP_API_PATH_PREFIX}interpret",
    "choose": f"{FATE_CARDS_MINIAPP_API_PATH_PREFIX}choose",
    "settle": f"{FATE_CARDS_MINIAPP_API_PATH_PREFIX}settle",
}
FATE_CARDS_MINIAPP_START_PARAM_PATTERN = r"fate[_-][A-Za-z0-9_-]{4,160}"
FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY = "cultivation"
FATE_CARDS_CHOICE_KEYS = frozenset({"accept", "defy", "hide"})
FATE_CARDS_AUTOMATION_CHOICE_KEYS = frozenset({"accept", "hide"})
FATE_CARDS_READ_ONLY_ENDPOINTS = frozenset({"start"})
FATE_CARDS_MUTATION_ENDPOINTS = frozenset({"draw", "interpret", "choose", "settle"})
FATE_CARDS_HTTP_TIMEOUT = (5, 20)


def _as_int(value, default=0):
    try:
        return int(float(str(value if value not in (None, "") else default).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return int(default or 0)


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ready", "enabled", "selected", "default"}
    return bool(value)


def _safe_text(value, *, limit=160):
    return sanitize_webapp_secret_text(value or "", limit=limit)


def _iter_dicts(value, *, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child, depth=depth + 1)


def build_fate_cards_miniapp_adapter(
    *,
    api_base_url=FATE_CARDS_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=FATE_CARDS_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=FATE_CARDS_MINIAPP_GAME_KEY,
        label=FATE_CARDS_MINIAPP_LABEL,
        bot_username=bot_username,
        allowed_bot_username_patterns=(r"hantianzun\d{2}_bot",),
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(FATE_CARDS_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(FATE_CARDS_MINIAPP_ENDPOINTS),
        start_param_pattern=FATE_CARDS_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_fate_cards_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=FATE_CARDS_MINIAPP_GAME_KEY,
        label=FATE_CARDS_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        read_scope="single_identity_public_entry_probe",
        state_outputs=("daily_record", "quest_state", "reward_delta"),
        note="执行计划只含 launch/start；抽牌、解读、命择和结算不注册进通用 runner。",
        steps=(
            MiniAppFlowStep(
                key="launch",
                endpoint="telegram_webview",
                method="TELEGRAM",
                required_payload_keys=("token",),
                sends_init_data=False,
                note="RequestMainWebView 获取短 TTL initData，不落盘。",
            ),
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="只读今日牌阵、服务端选项和命脉任务状态。",
            ),
        ),
    )


def build_fate_cards_launch_args(
    url,
    *,
    start_param="",
    bot_username=FATE_CARDS_MINIAPP_DEFAULT_BOT_USERNAME,
):
    adapter = build_fate_cards_miniapp_adapter(bot_username=bot_username)
    launch = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return launch, build_request_webview_args(adapter, launch) if launch.allowed else {}


def build_fate_cards_miniapp_request(
    endpoint,
    *,
    token,
    init_data_session=None,
    init_data="",
    payload=None,
    adapter=None,
    allow_mutation=False,
):
    endpoint = str(endpoint or "").strip().lower()
    if endpoint not in FATE_CARDS_READ_ONLY_ENDPOINTS | FATE_CARDS_MUTATION_ENDPOINTS:
        raise ValueError("天机命脉 MiniApp endpoint 不在白名单")
    if endpoint in FATE_CARDS_MUTATION_ENDPOINTS and not allow_mutation:
        raise ValueError("天机命脉非幂等动作需要显式 Lab 授权")
    request_payload = {"token": str(token or "").strip()}
    request_payload.update(dict(payload or {}))
    return build_miniapp_http_request(
        adapter or build_fate_cards_miniapp_adapter(),
        endpoint,
        request_payload,
        init_data_session=init_data_session,
        init_data=init_data,
        timeout_sec=FATE_CARDS_HTTP_TIMEOUT[1],
    )


def normalize_fate_cards_question_key(value):
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", normalized):
        raise ValueError("天机命脉问天主题格式无效")
    return normalized


def normalize_fate_cards_choice_key(value, *, automation=False):
    normalized = str(value or "").strip().lower()
    allowed = FATE_CARDS_AUTOMATION_CHOICE_KEYS if automation else FATE_CARDS_CHOICE_KEYS
    if normalized not in allowed:
        raise ValueError("天机命脉命择不在自动化白名单" if automation else "天机命脉命择不在白名单")
    return normalized


def _normalize_option(item):
    if not isinstance(item, dict):
        return {}
    key = str(item.get("key") or item.get("id") or "").strip()
    if not key:
        return {}
    return {
        "key": _safe_text(key, limit=80),
        "name": _safe_text(item.get("name") or item.get("title") or "", limit=80),
        "prompt": _safe_text(item.get("prompt") or item.get("description") or "", limit=180),
        "symbol": _safe_text(item.get("symbol") or "", limit=16),
        "is_default": any(
            _as_bool(item.get(field))
            for field in ("default", "isDefault", "selected", "isSelected")
        ),
    }


def _explicit_default_key(root, options, *, kind):
    field_names = (
        ("defaultQuestionKey", "selectedQuestionKey", "questionKey")
        if kind == "question"
        else ("defaultChoiceKey", "selectedChoiceKey")
    )
    for field in field_names:
        value = str(root.get(field) or "").strip()
        if value:
            return _safe_text(value, limit=80)
    for item in options:
        if item.get("is_default"):
            return str(item.get("key") or "")
    return ""


def parse_fate_cards_state(data):
    """Return a secret-free decision snapshot from ``/start`` or later replies."""
    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        return {}
    record = root.get("record") if isinstance(root.get("record"), dict) else {}
    quest = record.get("quest") if isinstance(record.get("quest"), dict) else {}
    questions = [
        normalized
        for normalized in (_normalize_option(item) for item in root.get("questions") or ())
        if normalized
    ]
    choices = [
        normalized
        for normalized in (_normalize_option(item) for item in root.get("choices") or ())
        if normalized
    ]
    question_key = str(record.get("questionKey") or "").strip()
    choice_key = str(record.get("choiceKey") or "").strip()
    default_question_key = _explicit_default_key(root, questions, kind="question")
    if not default_question_key and any(item.get("key") == FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY for item in questions):
        default_question_key = FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY
    default_choice_key = _explicit_default_key(root, choices, kind="choice")
    cards = record.get("cards") if isinstance(record.get("cards"), list) else []
    has_drawn = _as_bool(root.get("hasDrawn")) or bool(record and cards)
    quest_status = str(quest.get("status") or "").strip().lower()
    quest_can_settle = _as_bool(quest.get("canSettle"))

    state = {
        "challenge_date": _safe_text(root.get("challengeDate") or record.get("challengeDate") or "", limit=40),
        "trace_balance": _as_int(root.get("traceBalance"), 0),
        "questions": questions,
        "choices": choices,
        "question_count": len(questions),
        "choice_count": len(choices),
        "default_question_key": _safe_text(default_question_key, limit=80),
        "default_choice_key": _safe_text(default_choice_key, limit=80),
        "has_drawn": bool(has_drawn),
        "question_key": _safe_text(question_key, limit=80),
        "choice_key": _safe_text(choice_key, limit=80),
        "card_count": len(cards),
        "has_ai_reading": bool(isinstance(record.get("aiReading"), dict) and record.get("aiReading")),
        "quest": {
            "title": _safe_text(quest.get("title") or "", limit=100),
            "description": _safe_text(quest.get("description") or "", limit=220),
            "metric": _safe_text(quest.get("metric") or "", limit=80),
            "unit": _safe_text(quest.get("unit") or "", limit=40),
            "progress": _as_int(quest.get("progress"), 0),
            "target": _as_int(quest.get("target"), 0),
            "status": _safe_text(quest_status, limit=40),
            "can_settle": bool(quest_can_settle),
            "started_at": _safe_text(quest.get("startedAt") or "", limit=48),
            "expires_at": _safe_text(quest.get("expiresAt") or "", limit=48),
        },
    }
    state["decision"] = decide_fate_cards_next_step(state)
    return state


def decide_fate_cards_next_step(state):
    """Describe the next action without authorizing a mutation."""
    state = dict(state or {})
    quest = state.get("quest") if isinstance(state.get("quest"), dict) else {}
    if not state.get("has_drawn"):
        question_key = str(state.get("default_question_key") or "").strip()
        if not question_key:
            return {"action": "blocked", "reason": "default_question_unknown", "safe_to_auto": False}
        return {
            "action": "manual_draw",
            "reason": "daily_non_idempotent",
            "question_key": question_key,
            "safe_to_auto": False,
        }
    if not str(state.get("choice_key") or "").strip():
        choice_key = str(state.get("default_choice_key") or "").strip()
        if not choice_key:
            return {"action": "blocked", "reason": "default_choice_unknown", "safe_to_auto": False}
        return {
            "action": "manual_choose",
            "reason": "choice_irreversible",
            "choice_key": choice_key,
            "safe_to_auto": False,
        }
    status = str(quest.get("status") or "").strip().lower()
    if status == "settled":
        return {"action": "done", "reason": "already_settled", "safe_to_auto": True}
    if status == "expired":
        return {"action": "done", "reason": "quest_expired", "safe_to_auto": True}
    if quest.get("can_settle"):
        return {"action": "manual_settle", "reason": "quest_complete", "safe_to_auto": False}
    return {"action": "wait", "reason": status or "quest_incomplete", "safe_to_auto": True}


def parse_fate_cards_reward(data):
    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    reward = root.get("reward") if isinstance(root.get("reward"), dict) else {}
    trace_gain = _as_int(reward.get("tianjiTrace"), 0)
    return {"天机残痕": trace_gain} if trace_gain > 0 else {}


def find_fate_cards_external_app(value):
    """Find the current cave-directory entry without retaining its token URL."""
    fallback = {}
    for item in _iter_dicts(value):
        key = str(item.get("key") or "").strip().lower()
        action = str(item.get("action") or "").strip().lower()
        title = " ".join(str(item.get(field) or "").strip().lower() for field in ("title", "subtitle", "buttonText"))
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        matched = (
            key in {"fate", "fate_cards", "tianji_fate", "tianji_fate_cards"}
            or action in {"fate", "fate_cards", "tianji_fate", "tianji_fate_cards"}
            or "天机命脉" in title
            or "xianxia-fate-cards" in url.lower()
            or "startapp=fate_" in url.lower()
            or "startapp=fate-" in url.lower()
        )
        if not matched:
            continue
        candidate = {
            "action": action if action in {"fate", "fate_cards", "tianji_fate", "tianji_fate_cards"} else "",
            "url": url,
            "title": _safe_text(item.get("title") or item.get("buttonText") or key, limit=100),
            "available": _as_bool(item.get("available", True)),
            "key": _safe_text(key, limit=80),
        }
        if candidate["available"] and (candidate["url"] or candidate["action"]):
            return candidate
        if not fallback:
            fallback = candidate
    return fallback


def extract_fate_cards_launch_from_payload(value):
    for item in _iter_dicts(value):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        if not url:
            continue
        if url.startswith("/"):
            url = urljoin(FATE_CARDS_MINIAPP_DEFAULT_API_BASE_URL + "/", url)
        elif "://" not in url:
            url = urljoin(FATE_CARDS_MINIAPP_DEFAULT_API_BASE_URL + "/miniapp/xianxia-fate-cards", url)
        launch, _args = build_fate_cards_launch_args(url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": launch.webview_url,
                "title": _safe_text(item.get("title") or item.get("buttonText") or item.get("key") or "", limit=100),
                "safe_summary": launch.safe_summary(),
            }
    return {}


async def request_fate_cards_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_fate_cards_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "天机命脉 MiniApp launch 不允许")
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


def _flow_result(ok, status, *, error="", data=None, events=None):
    return {
        "ok": bool(ok),
        "status": str(status or ""),
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
    }


def run_fate_cards_start_probe(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    """Execute only the idempotent-looking ``/start`` panel read."""
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")
    adapter = adapter or build_fate_cards_miniapp_adapter()
    result = execute_miniapp_http_request(
        build_fate_cards_miniapp_request(
            "start",
            token=token,
            init_data=init_data,
            adapter=adapter,
        ),
        transport,
        backoff_sec=(1.0,),
        sleeper=sleeper or time.sleep,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start",
    )
    events = []
    append_http_event(events, "start", result)
    if not result.ok:
        return _flow_result(False, "failed", error=result.error, events=events)
    state = parse_fate_cards_state(result.data)
    if not state:
        return _flow_result(False, "failed", error="MiniApp 返回不是天机命脉状态", events=events)
    return _flow_result(True, "observed", data={"state": state}, events=events)


def run_fate_cards_action(
    endpoint,
    *,
    token,
    init_data,
    transport,
    payload=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    """Execute one explicitly selected mutation without HTTP replay."""
    endpoint = str(endpoint or "").strip().lower()
    if endpoint not in FATE_CARDS_MUTATION_ENDPOINTS:
        return _flow_result(False, "failed", error="mutation endpoint invalid")
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")
    request_payload = dict(payload or {})
    if endpoint == "draw":
        try:
            request_payload = {"questionKey": normalize_fate_cards_question_key(request_payload.get("questionKey"))}
        except ValueError as exc:
            return _flow_result(False, "failed", error=exc)
    elif endpoint == "choose":
        try:
            request_payload = {"choiceKey": normalize_fate_cards_choice_key(request_payload.get("choiceKey"))}
        except ValueError as exc:
            return _flow_result(False, "failed", error=exc)
    else:
        request_payload = {}
    adapter = adapter or build_fate_cards_miniapp_adapter()
    result = execute_miniapp_http_request(
        build_fate_cards_miniapp_request(
            endpoint,
            token=token,
            init_data=init_data,
            payload=request_payload,
            adapter=adapter,
            allow_mutation=True,
        ),
        transport,
        backoff_sec=(),
        sleeper=sleeper or time.sleep,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key=endpoint,
    )
    events = []
    append_http_event(events, endpoint, result)
    if not result.ok:
        return _flow_result(False, "failed", error=result.error, data=result.data, events=events)
    state = parse_fate_cards_state(result.data)
    data = {"raw": dict(result.data or {})}
    if state:
        data["state"] = state
    reward = parse_fate_cards_reward(result.data)
    if reward:
        data["reward"] = reward
    return _flow_result(True, endpoint, data=data, events=events)


async def run_fate_cards_start_probe_production(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    """Manual production helper; it still performs only ``/start``."""
    adapter = adapter or build_fate_cards_miniapp_adapter()
    try:
        init_data = str(init_data or "").strip() or await request_fate_cards_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        return await asyncio.to_thread(
            run_fate_cards_start_probe,
            token=token,
            init_data=init_data,
            transport=transport or build_miniapp_transport(timeout=FATE_CARDS_HTTP_TIMEOUT),
            adapter=adapter,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_fate_cards_action_production(
    identity_id,
    endpoint,
    *,
    token,
    webview_url,
    init_data="",
    payload=None,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_fate_cards_miniapp_adapter()
    try:
        init_data = str(init_data or "").strip() or await request_fate_cards_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        return await asyncio.to_thread(
            run_fate_cards_action,
            endpoint,
            token=token,
            init_data=init_data,
            transport=transport or build_miniapp_transport(timeout=FATE_CARDS_HTTP_TIMEOUT),
            payload=payload,
            adapter=adapter,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY",
    "FATE_CARDS_AUTOMATION_CHOICE_KEYS",
    "FATE_CARDS_CHOICE_KEYS",
    "FATE_CARDS_MINIAPP_GAME_KEY",
    "build_fate_cards_launch_args",
    "build_fate_cards_miniapp_adapter",
    "build_fate_cards_miniapp_flow_plan",
    "build_fate_cards_miniapp_request",
    "decide_fate_cards_next_step",
    "extract_fate_cards_launch_from_payload",
    "find_fate_cards_external_app",
    "normalize_fate_cards_choice_key",
    "normalize_fate_cards_question_key",
    "parse_fate_cards_reward",
    "parse_fate_cards_state",
    "request_fate_cards_miniapp_init_data",
    "run_fate_cards_action",
    "run_fate_cards_action_production",
    "run_fate_cards_start_probe",
    "run_fate_cards_start_probe_production",
]
