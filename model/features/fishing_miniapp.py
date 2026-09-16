import asyncio
import hashlib
import inspect
import math
import random
import re
from copy import deepcopy

from telethon import functions

from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..webapp_core import (
    MAX_MINIAPP_INLINE_RETRY_AFTER_SEC,
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    MiniAppRequestAborted,
    MiniAppRequestBudget,
    build_miniapp_launch_request,
    build_miniapp_http_request,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    iter_webapp_entry_links,
    require_miniapp_operation,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)
from .miniapp_common import (
    MiniAppFlowCancelled,
    append_http_event as _append_http_event,
    build_pooled_miniapp_transport,
    run_miniapp_blocking_flow,
)
from .fishing import FISHING_MAX_DAILY_LIMIT


FISHING_MINIAPP_GAME_KEY = "fishing"
FISHING_MINIAPP_LABEL = "灵溪垂钓"
FISHING_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
FISHING_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
FISHING_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS = (
    r"hantianzun\d{2}_bot",
    r"snpao_bot",
    r"xlqlcy_bot",
)
FISHING_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-fishing/"
FISHING_MINIAPP_ENDPOINTS = {
    "start": f"{FISHING_MINIAPP_API_PATH_PREFIX}start",
    "finish": f"{FISHING_MINIAPP_API_PATH_PREFIX}finish",
    "result": f"{FISHING_MINIAPP_API_PATH_PREFIX}result",
    "next": f"{FISHING_MINIAPP_API_PATH_PREFIX}next",
    "shop": f"{FISHING_MINIAPP_API_PATH_PREFIX}shop",
    "buy_bait": f"{FISHING_MINIAPP_API_PATH_PREFIX}buy-bait",
    "chum": f"{FISHING_MINIAPP_API_PATH_PREFIX}chum",
    "open": f"{FISHING_MINIAPP_API_PATH_PREFIX}open",
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
FISHING_MINIAPP_NO_ROD_MARKERS = (
    "需先在商城购买鱼竿",
    "需要先在商城购买鱼竿",
    "请先购买鱼竿",
    "未持有鱼竿",
    "没有鱼竿",
    "fishing_rod_missing",
    "no_rod",
    "rod_missing",
)


def build_fishing_miniapp_adapter(*, api_base_url=FISHING_MINIAPP_DEFAULT_API_BASE_URL, bot_username=FISHING_MINIAPP_DEFAULT_BOT_USERNAME):
    return MiniAppAdapter(
        game_key=FISHING_MINIAPP_GAME_KEY,
        label=FISHING_MINIAPP_LABEL,
        bot_username=bot_username,
        allowed_bot_username_patterns=FISHING_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS,
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




def extract_fishing_miniapp_launch(event, *, message_text=""):
    adapter = build_fishing_miniapp_adapter()
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
        if not url:
            continue
        launch = build_miniapp_launch_request(adapter, url)
        if not launch.allowed or not launch.start_param:
            continue
        summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
        game_hint = str(summary.get("game_hint") or "").strip()
        if game_hint and game_hint != FISHING_MINIAPP_GAME_KEY:
            continue
        return {
            "token": launch.start_param,
            "webview_url": url,
            "button_text": button_text,
            "safe_summary": launch.safe_summary(),
        }
    return {}


def _iter_dwelling_external_apps(data):
    if not isinstance(data, dict):
        return
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    account = root.get("account") if isinstance(root.get("account"), dict) else {}
    external = account.get("externalApps") if isinstance(account.get("externalApps"), dict) else {}
    for group in external.get("groups") or ():
        if not isinstance(group, dict):
            continue
        for app in group.get("apps") or ():
            if isinstance(app, dict):
                yield group, app


def _iter_nested_dicts(value, *, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nested_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nested_dicts(child, depth=depth + 1)


def extract_fishing_miniapp_launch_from_dwelling_payload(data):
    adapter = build_fishing_miniapp_adapter()
    for group, app in _iter_dwelling_external_apps(data):
        app_key = str(app.get("key") or app.get("action") or "").strip().lower()
        title = str(app.get("title") or "").strip()
        if app_key not in {"fishing", "fish"} and "钓" not in title:
            continue
        url = str(app.get("url") or app.get("webviewUrl") or app.get("webview_url") or "").strip()
        if not url:
            continue
        launch = build_miniapp_launch_request(adapter, url)
        if not launch.allowed or not launch.start_param:
            continue
        return {
            "token": launch.start_param,
            "webview_url": url,
            "button_text": str(app.get("buttonText") or app.get("title") or "").strip(),
            "group_key": str(group.get("key") or "").strip(),
            "app_key": app_key,
            "safe_summary": launch.safe_summary(),
        }
    for app in _iter_nested_dicts(data):
        app_key = str(app.get("key") or app.get("action") or "").strip().lower()
        title = str(app.get("title") or app.get("buttonText") or "").strip()
        url = str(app.get("url") or app.get("webviewUrl") or app.get("webview_url") or "").strip()
        if app_key not in {"fishing", "fish"} and "钓" not in title and "xianxia-fishing" not in url:
            continue
        if url.startswith("/"):
            url = f"{FISHING_MINIAPP_DEFAULT_API_BASE_URL.rstrip('/')}{url}"
        if not url:
            continue
        launch = build_miniapp_launch_request(adapter, url)
        if not launch.allowed or not launch.start_param:
            continue
        return {
            "token": launch.start_param,
            "webview_url": launch.webview_url,
            "button_text": str(app.get("buttonText") or app.get("title") or "").strip(),
            "group_key": "",
            "app_key": app_key,
            "safe_summary": launch.safe_summary(),
        }
    return {}


async def request_fishing_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None, operation_check=None):
    adapter = adapter or build_fishing_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "fishing miniapp launch not allowed")
    require_miniapp_operation(operation_check)
    account_id, client = _get_identity_client_with_account(identity_id)
    if client is None:
        raise RuntimeError("身份客户端不可用")
    async with account_rpc_slot(account_id=account_id, client_obj=client):
        require_miniapp_operation(operation_check)
        bot = await client.get_entity(launch.bot_username or adapter.bot_username)
        require_miniapp_operation(operation_check)
        bot_input = await client.get_input_entity(bot)
        require_miniapp_operation(operation_check)
        result = await client(functions.messages.RequestMainWebViewRequest(
            peer=bot_input,
            bot=bot_input,
            platform=launch.platform or adapter.platform,
            start_param=launch.start_param,
        ))
    require_miniapp_operation(operation_check)
    init_data = extract_miniapp_init_data_from_url(getattr(result, "url", "") or "")
    if not init_data:
        raise RuntimeError("WebView URL 缺少 tgWebAppData")
    return init_data


async def run_fishing_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    max_rounds=1,
    pond_choice="",
    bait_choice="",
    transport=None,
    sleeper=None,
    adapter=None,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    checkpoint=None,
):
    adapter = adapter or build_fishing_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        require_miniapp_operation(operation_check)
        init_data = str(init_data or "").strip() or await request_fishing_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
            operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            return run_fishing_miniapp_loop_lab_flow(
                token=token,
                init_data=init_data,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key,
                    identity_id=identity_id,
                    timeout=FISHING_MINIAPP_HTTP_TIMEOUT,
                    operation_check=operation.check,
                ),
                adapter=adapter,
                sleeper=operation.sleep,
                max_rounds=max_rounds,
                pond_choice=pond_choice,
                bait_choice=bait_choice,
                bite_wait_cap_ms=FISHING_MINIAPP_PRODUCTION_BITE_WAIT_CAP_MS,
                capture_sink=capture_sink,
                capture_source=capture_source,
                operation_check=operation.check,
                checkpoint=checkpoint,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppRequestAborted as exc:
        return _flow_result(False, "cancelled", error=exc)
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


def build_fishing_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=FISHING_MINIAPP_GAME_KEY,
        label=FISHING_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only flow declaration; production fishing scheduler is not wired",
        replaces_commands=(".钓鱼",),
        state_outputs=("module_snapshot", "daily_counter", "inventory_delta"),
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
    if _fishing_no_rod_reason(code):
        return "no_rod"
    if code in FISHING_MINIAPP_ALREADY_SETTLED_ERRORS:
        return "already_settled"
    if code in FISHING_MINIAPP_EXPIRED_ERRORS:
        return "expired"
    if code in FISHING_MINIAPP_UNBINDABLE_ERRORS:
        return "unbindable"
    if code in FISHING_MINIAPP_DAILY_LIMIT_ERRORS or any(keyword in code for keyword in FISHING_MINIAPP_DAILY_LIMIT_ERRORS):
        return "daily_limit"
    return "failed"


def _http_failure_status(result, fallback=None):
    if result.error_type == "operation_cancelled":
        return "cancelled"
    if result.error_type == "request_budget":
        return "request_budget"
    status = classify_fishing_miniapp_error(result.error)
    if status in {"no_rod", "daily_limit"}:
        return status
    return fallback or status


def _wait_fishing(delay, sleeper, operation_check):
    require_miniapp_operation(operation_check)
    if sleeper is not None:
        sleeper(delay)
    require_miniapp_operation(operation_check)


class _FishingRequestContext:
    def __init__(self, *, transport, adapter, init_data, sleeper, capture_sink, capture_source,
                 request_budget, operation_check, checkpoint=None):
        self.transport = transport
        self.adapter = adapter
        self.init_data = init_data
        self.sleeper = sleeper
        self.capture_sink = capture_sink
        self.capture_source = capture_source
        self.request_budget = request_budget
        self.operation_check = operation_check
        self.action_dispatched = False
        self.pending = None
        self.pending_round_known = False
        self.settled_round_keys = []
        self.round_receipts = []
        self.checkpoint = checkpoint
        self.checkpoint_sequence = 0
        self.checkpoint_error = ""
        self.retry_after_sec = 0
        self.last_dispatched = False
        self.last_prior_pending = None

    def request(self, endpoint, step, *, token, events, payload=None):
        if self.checkpoint_error:
            raise MiniAppRequestAborted(self.checkpoint_error)
        if endpoint in {"start", "finish"} and hashlib.sha256(str(token).encode()).hexdigest() in self.settled_round_keys:
            raise ValueError("fishing_round_already_confirmed")
        previous = self.pending
        previous_known = self.pending_round_known
        dispatched = False
        intent_saved = False

        def dispatch(request):
            nonlocal dispatched, intent_saved
            pending = (endpoint, hashlib.sha256(str(token).encode()).hexdigest())
            if endpoint in {"start", "finish", "next"}:
                if not self._checkpoint("intent", intent=pending):
                    raise MiniAppRequestAborted(self.checkpoint_error)
                intent_saved = True
                require_miniapp_operation(self.operation_check)
            dispatched = True
            if endpoint in {"start", "finish", "next"}:
                self.action_dispatched = True
                self.pending = pending
                self.pending_round_known = endpoint != "next"
            return self.transport(request)

        result = execute_miniapp_http_request(
            build_fishing_miniapp_request(endpoint, token=token, init_data=self.init_data,
                                          payload=payload, adapter=self.adapter),
            dispatch, sleeper=self.sleeper, backoff_sec=(),
            capture_sink=self.capture_sink, capture_source=self.capture_source, step_key=step,
            request_budget=self.request_budget, operation_check=self.operation_check,
        )
        if dispatched and endpoint in {"start", "finish", "next"} and (
            result.error_type == "app" and result.data.get("ok") is False
            and 200 <= result.status_code < 500 and result.status_code not in {408, 425, 429}
        ):
            self.pending = previous
            self.pending_round_known = previous_known
        if dispatched and endpoint == "next" and result.ok:
            next_token = _extract_next_token(result.data)
            if next_token:
                self.pending = ("next", hashlib.sha256(next_token.encode()).hexdigest())
                self.pending_round_known = True
        self.last_dispatched = dispatched
        self.last_prior_pending = previous
        _append_http_event(events, step, result)
        events[-1]["dispatched"] = dispatched
        self.retry_after_sec = max(self.retry_after_sec, result.retry_after_sec)
        if intent_saved or result.retry_after_sec > 0:
            self._checkpoint("response")
        return result

    def confirm_idle(self):
        if self.pending and self.pending[0] == "start" and self.last_prior_pending is None:
            self.pending = None
            self.pending_round_known = False
            self._checkpoint("idle")

    def confirm_settled(self, token, data):
        key = hashlib.sha256(str(token).encode()).hexdigest()
        if key in self.settled_round_keys:
            raise ValueError("fishing_round_already_confirmed")
        if self.pending and self.pending[1] == key and self.pending_round_known:
            self.pending = None
            self.pending_round_known = False
        self.settled_round_keys.append(key)
        projection_failed = False
        try:
            receipt = _fishing_round_receipt(key, data)
        except Exception:
            projection_failed = True
            receipt = {"round_key": key, "data": {"settled_count": 1}, "projection_error": True}
        self.round_receipts.append(receipt)
        self._checkpoint("settled")
        if projection_failed and not self.checkpoint_error:
            self.checkpoint_error = "fishing_receipt_projection_failed"

    def _checkpoint(self, phase, *, intent=None):
        if self.checkpoint_error:
            return False
        if self.checkpoint is None:
            return True
        record = self.annotate({})
        record.pop("checkpoint_sequence")
        record.pop("checkpoint_error")
        record.update(version=1, phase=phase, sequence=self.checkpoint_sequence + 1)
        if intent is not None:
            # An acknowledged intent is a barrier, not proof of transport entry.
            record.update(outcome_unknown=True, unresolved_action=intent[0], unresolved_round_key=intent[1],
                          unresolved_round_known=intent[0] != "next")
        try:
            acknowledged = self.checkpoint(deepcopy(record))
            if inspect.iscoroutine(acknowledged):
                acknowledged.close()
            if acknowledged is not True:
                raise ValueError("checkpoint_not_acknowledged")
        except Exception:
            self.checkpoint_error = "fishing_checkpoint_failed"
            return False
        self.checkpoint_sequence += 1
        return True

    def annotate(self, result):
        return {
            **result,
            "action_dispatched": self.action_dispatched,
            "outcome_unknown": self.pending is not None,
            "unresolved_action": self.pending[0] if self.pending else "",
            "unresolved_round_key": self.pending[1] if self.pending else "",
            "unresolved_round_known": self.pending_round_known,
            "settled_round_keys": list(self.settled_round_keys),
            "round_receipts": deepcopy(self.round_receipts),
            "checkpoint_sequence": self.checkpoint_sequence,
            "checkpoint_error": self.checkpoint_error,
            "retry_after_sec": self.retry_after_sec,
        }


def _fishing_read_recovery_allowed(result):
    return (
        result.error_type == "transient" and result.status_code not in {401, 403, 429}
        and result.retry_after_sec <= MAX_MINIAPP_INLINE_RETRY_AFTER_SEC
    )


def _iter_fishing_response_texts(value, *, depth=0):
    if depth > 5:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in {
                "error", "message", "rawmessage", "raw_message", "statusmessage",
                "status_text", "description", "reason", "errorcode", "error_code",
            } and isinstance(child, (str, int, float)):
                yield str(child)
            else:
                yield from _iter_fishing_response_texts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_fishing_response_texts(child, depth=depth + 1)
    elif isinstance(value, str):
        yield value


def _fishing_no_rod_reason(value):
    for text in _iter_fishing_response_texts(value):
        normalized = str(text or "").strip().lower()
        if any(str(marker).lower() in normalized for marker in FISHING_MINIAPP_NO_ROD_MARKERS):
            return str(text or "未持有鱼竿").strip()[:180]
    return ""


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


def _build_fishing_v2_proof(challenge):
    """Build the event replay expected by the current fishing validator.

    The challenge keeps the historical V1 mode label, but the server now
    validates a 20 ms holding/release trajectory against the fish parameters.
    Keep this isolated so older challenge payloads can still use the legacy
    proof builder below.
    """
    challenge = dict(challenge or {})
    min_duration_ms = max(1000, _coerce_int(challenge.get("minDurationMs"), 5200))
    max_duration_ms = max(
        min_duration_ms + 500,
        _coerce_int(challenge.get("maxDurationMs"), 90000),
    )
    try:
        target_low = float(challenge.get("targetLow") or 41)
    except (TypeError, ValueError, OverflowError):
        target_low = 41.0
    try:
        target_high = float(challenge.get("targetHigh") or 68)
    except (TypeError, ValueError, OverflowError):
        target_high = 68.0
    if target_high < target_low:
        target_low, target_high = target_high, target_low
    try:
        fish_power = float(challenge.get("fishPower") or 1.7)
    except (TypeError, ValueError, OverflowError):
        fish_power = 1.7
    fish_power = max(0.1, fish_power)
    seed_offset = sum(ord(char) for char in str(challenge.get("fishSeed") or "seed")) / 19.0

    elapsed_ms = 0
    progress = 0.0
    tension = (target_low + target_high) / 2.0 - 8.0
    holding = False
    events = []
    threshold_margin = min(7.0, max(2.0, (target_high - target_low) * 0.3))
    hold_below = target_low + threshold_margin
    release_above = target_high - threshold_margin
    replay_deadline_ms = max_duration_ms - 200

    while progress < 100.0 and elapsed_ms + 20 <= replay_deadline_ms:
        desired_holding = holding
        if tension <= hold_below:
            desired_holding = True
        elif tension >= release_above:
            desired_holding = False
        if desired_holding != holding:
            holding = desired_holding
            events.append({"t": elapsed_ms + 20, "holding": holding})

        elapsed_ms += 20
        game_time = float(elapsed_ms)
        pulse = math.sin(game_time * 0.0027 * fish_power + seed_offset)
        surge = max(0.0, math.sin(game_time * 0.0041 + seed_offset * 1.7))
        fish_pull = fish_power * (0.72 + pulse * 0.24 + surge * 0.42)
        if holding:
            tension += (24.0 + fish_pull * 3.1) * 0.02
        else:
            tension += (fish_pull * 4.8 - 24.0) * 0.02
        tension += math.sin(game_time * 0.012 + seed_offset) * 0.24
        tension = max(0.0, min(100.0, tension))

        if target_low <= tension <= target_high:
            progress += (8.2 + fish_power * 0.7 + (2.2 if holding else 0.5)) * 0.02
        elif tension > target_high:
            progress -= (1.5 + fish_power * 0.25) * 0.02
        else:
            progress -= 0.9 * 0.02
        if holding and tension < target_low:
            progress += 1.1 * 0.02
        progress = max(0.0, min(100.0, progress))

    if progress < 100.0:
        raise ValueError("MiniApp 无法生成有效控线轨迹")
    duration_ms = max(elapsed_ms, min_duration_ms + 80)
    duration_ms = min(duration_ms, replay_deadline_ms)
    return {
        "mode": "xianxiaFishingV2",
        "challengeId": str(challenge.get("challengeId") or ""),
        "durationMs": int(duration_ms),
        "events": events,
    }


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
    if {"targetLow", "targetHigh", "fishPower", "fishSeed"}.issubset(challenge):
        return _build_fishing_v2_proof(challenge)
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


def _no_rod_flow_result(data, events):
    reason = _fishing_no_rod_reason(data)
    if not reason:
        return None
    return _flow_result(
        False,
        "no_rod",
        error=reason,
        data={"terminal_skip": True, "rod_required": True},
        events=events,
    )


def _flow_result(ok, status, *, error="", data=None, events=None, proof=None, active_token=""):
    result = {
        "ok": bool(ok),
        "status": status,
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
        "proof": dict(proof or {}),
        "action_dispatched": False,
        "outcome_unknown": False,
        "unresolved_action": "",
        "unresolved_round_key": "",
        "unresolved_round_known": False,
        "settled_round_keys": [],
        "round_receipts": [],
        "checkpoint_sequence": 0,
        "checkpoint_error": "",
        "retry_after_sec": 0,
    }
    if active_token:
        result["_active_token"] = str(active_token)
    return result




def _extract_next_token(data):
    if not isinstance(data, dict):
        return ""
    keys = ("token", "nextToken", "next_token", "startParam", "start_param")
    values = [data[key] for key in keys if key in data]
    if not values:
        nested = data.get("next") if isinstance(data.get("next"), dict) else {}
        values = [nested[key] for key in keys if key in nested]
    if not values or any(not isinstance(value, str) or not re.fullmatch(FISHING_MINIAPP_START_PARAM_PATTERN, value.strip())
                         for value in values):
        return ""
    tokens = [value.strip() for value in values]
    return tokens[0] if all(token == tokens[0] for token in tokens) else ""


def _coerce_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _fishing_shop(data):
    data = dict(data or {})
    return data.get("shop") if isinstance(data.get("shop"), dict) else {}


def _select_fishing_pond(shop, preferred=""):
    preferred = str(preferred or "").strip().lower()
    ponds = [item for item in dict(shop or {}).get("ponds") or () if isinstance(item, dict)]
    available = [item for item in ponds if item.get("unlocked") is not False]
    for item in available:
        values = {str(item.get(key) or "").strip().lower() for key in ("key", "name")}
        if preferred and preferred in values:
            return item
    return available[0] if available else None


def _select_fishing_bait(shop, preferred=""):
    preferred = str(preferred or "").strip().lower()
    baits = [item for item in dict(shop or {}).get("baits") or () if isinstance(item, dict)]
    available = [
        item
        for item in baits
        if item.get("unlocked") is not False and _coerce_int(item.get("count"), 0) > 0
    ]
    for item in available:
        values = {str(item.get(key) or "").strip().lower() for key in ("key", "itemId", "name")}
        if preferred and preferred in values:
            return item
    return available[0] if available else None


def _enter_fishing_lobby(
    *,
    token,
    requests,
    pond_choice,
    bait_choice,
    events,
):
    shop_result = requests.request("shop", "shop", token=token, events=events)
    if not shop_result.ok:
        return "", _flow_result(False, _http_failure_status(shop_result, "shop_failed"), error=shop_result.error, events=events)
    shop = _fishing_shop(shop_result.data)
    pond = _select_fishing_pond(shop, pond_choice)
    bait = _select_fishing_bait(shop, bait_choice)
    if not pond:
        return "", _flow_result(False, "pond_unavailable", error="no unlocked fishing pond", events=events)
    if not bait:
        return "", _flow_result(False, "bait_missing", error="no available fishing bait", events=events)
    next_result = requests.request(
        "next", "lobby_next", token=token, events=events,
        payload={
            "pondKey": str(pond.get("key") or "").strip(),
            "baitItemId": str(bait.get("itemId") or "").strip(),
        },
    )
    if not next_result.ok:
        return "", _flow_result(False, _http_failure_status(next_result, "next_failed"), error=next_result.error, events=events)
    next_token = _extract_next_token(next_result.data)
    if not next_token:
        return "", _flow_result(False, "next_unavailable", error="lobby next token missing", events=events)
    events.append({
        "step": "lobby_selected",
        "ok": True,
        "pond_key": str(pond.get("key") or ""),
        "pond_name": str(pond.get("name") or ""),
        "bait_name": str(bait.get("name") or ""),
    })
    return next_token, None


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


_FISHING_REWARD_KEYS = {"rewards", "reward", "bonusloot", "bonusitems", "bonus", "loot", "drops", "items", "materials"}
_FISHING_REWARD_NAME_KEYS = ("name", "itemName", "item_name", "title", "label")
_FISHING_REWARD_QUANTITY_KEYS = ("qty", "count", "quantity", "amount")
_FISHING_RESULT_WRAPPERS = ("result", "details", "detail")
_FISHING_GAIN_KEYS = {
    "expGain": {"expgain", "experiencegain"},
    "lingShiGain": {"lingshigain", "lingstonegain", "spiritstonegain", "stonegain"},
}
_FISHING_DAILY_KEYS = {
    "limit": {"dailylimit", "dailyrodslimit", "rodlimit", "maxrods", "totalrods"},
    "used": {"dailyused", "dailycount", "dailyrodsused", "rodcount", "usedrods", "todaycount"},
    "remaining": {"dailyremaining", "dailyrodsremaining", "remainingrods", "leftrods"},
}
_FISHING_DAILY_GENERIC_KEYS = {
    "limit": {"limit", "quota", "total"}, "used": {"used", "count"}, "remaining": {"remaining", "left"},
}
_FISHING_DAILY_CONTAINERS = {"daily", "dailyquota", "dailyprogress", "dailycounter"}
_FISHING_DAILY_NAMED_KEYS = frozenset().union(*_FISHING_DAILY_KEYS.values())


def _fishing_business_key(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).lower()


def _fishing_current_field_values(data, keys, *, depth=0):
    if not isinstance(data, dict) or depth > 4:
        return []
    values = [value for key, value in data.items() if _fishing_business_key(key) in keys]
    if values:
        return values
    return [value for key in _FISHING_RESULT_WRAPPERS
            for value in _fishing_current_field_values(data.get(key), keys, depth=depth + 1)]


def _fishing_quantity(value):
    if type(value) is int and value >= 0:
        return value
    if type(value) is float and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _fishing_daily_fields(data):
    if not isinstance(data, dict):
        return {}
    fields = {}
    for field, keys in _FISHING_DAILY_KEYS.items():
        aliases = keys | _FISHING_DAILY_GENERIC_KEYS[field]
        values = [_fishing_quantity(value) for key, value in data.items() if _fishing_business_key(key) in aliases]
        if values:
            if values[0] is None or any(value != values[0] for value in values):
                return {}
            fields[field] = values[0]
    limit = fields.get("limit", 0)
    if not 0 < limit <= FISHING_MAX_DAILY_LIMIT or not ({"used", "remaining"} & fields.keys()):
        return {}
    if any(value > limit for value in fields.values()):
        return {}
    used = fields.get("used", limit - fields.get("remaining", limit))
    remaining = fields.get("remaining", limit - used)
    if used + remaining != limit:
        return {}
    return {"limit": limit, "used": used, "remaining": remaining}


def extract_fishing_miniapp_daily_progress(data):
    def candidates(value, depth=0):
        if not isinstance(value, dict) or depth > 4:
            return []
        current = [_fishing_daily_fields(child) for key, child in value.items()
                   if _fishing_business_key(key) in _FISHING_DAILY_CONTAINERS]
        if any(_fishing_business_key(key) in _FISHING_DAILY_NAMED_KEYS for key in value):
            current.append(_fishing_daily_fields(value))
        # An explicit empty/invalid current family must not inherit a nested quota.
        if current:
            return current
        return [item for key in _FISHING_RESULT_WRAPPERS for item in candidates(value.get(key), depth + 1)]

    values = candidates(data)
    return values[0] if values and all(value == values[0] for value in values) else {}


def _fishing_reward_item(value, *, fallback_name=""):
    if isinstance(value, str) and not fallback_name:
        name, qty = _as_clean_text(value), 1
    elif isinstance(value, dict):
        names = [value[key] for key in _FISHING_REWARD_NAME_KEYS if key in value]
        if any(not isinstance(name, str) for name in names):
            return {}
        name = _find_first_text(value, _FISHING_REWARD_NAME_KEYS) or _as_clean_text(fallback_name)
        quantities = [_fishing_quantity(value[key]) for key in _FISHING_REWARD_QUANTITY_KEYS if key in value]
        qty = quantities[0] if quantities else 1
        if any(amount != qty for amount in quantities):
            return {}
    else:
        name, qty = _as_clean_text(fallback_name), _fishing_quantity(value)
    return {"name": name, "qty": qty} if name and qty is not None and qty > 0 else {}


def _fishing_reward_container(value):
    if isinstance(value, list):
        rewards = [_fishing_reward_item(item) for item in value]
    elif isinstance(value, dict) and not any(key in value for key in (*_FISHING_REWARD_NAME_KEYS, *_FISHING_REWARD_QUANTITY_KEYS)):
        rewards = [_fishing_reward_item(item, fallback_name=name) for name, item in value.items()]
    else:
        rewards = [_fishing_reward_item(value)]
    return [reward for reward in rewards if reward]


def _fishing_reward_consensus(values):
    variants = [_fishing_reward_container(value) for value in values]
    # Alternative fields describe one reward family, not additional rounds.
    return variants[0] if variants and all(value == variants[0] for value in variants) else []


def _extract_reward_items(data):
    if not isinstance(data, dict):
        return []
    return _fishing_reward_consensus([
        value for key, value in data.items() if _fishing_business_key(key) in _FISHING_REWARD_KEYS
    ])


def extract_fishing_miniapp_rewards(data):
    """Standalone rewards; catch-attached rewards are already in each catch."""
    if not isinstance(data, dict):
        return []
    if not _fishing_current_field_values(data, {"catches"}) and extract_fishing_miniapp_catches(data):
        return []
    return _fishing_reward_consensus(_fishing_current_field_values(data, _FISHING_REWARD_KEYS))


def extract_fishing_miniapp_gains(data):
    gains = {}
    for key, aliases in _FISHING_GAIN_KEYS.items():
        values = [_fishing_quantity(value) for value in _fishing_current_field_values(data, aliases)]
        if values and values[0] is not None and values[0] > 0 and all(value == values[0] for value in values):
            gains[key] = values[0]
    return gains


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
            if not _fishing_current_field_values(fish_value, _FISHING_REWARD_KEYS):
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


def _normalize_catch_entry(item):
    if not isinstance(item, dict):
        return {}
    fish = _as_clean_text(item.get("fish"))
    if not fish:
        return {}
    return {
        "fish": fish,
        "grade": _as_clean_text(item.get("grade")),
        "weight": _as_clean_text(item.get("weight")),
        "rewards": _fishing_reward_container(item.get("rewards", [])),
        "companion": bool(item.get("companion")),
    }


def extract_fishing_miniapp_catches(data):
    """Return non-sensitive catch summaries from a MiniApp result payload."""
    current_lists = _fishing_current_field_values(data, {"catches"})
    if current_lists:
        if not all(isinstance(value, list) and value == current_lists[0] for value in current_lists):
            return []
        direct_catches = []
        for raw_item in current_lists[0]:
            parsed = _extract_catch_from_mapping(raw_item, context="catch") if isinstance(raw_item, dict) else {}
            if not parsed and isinstance(raw_item, str):
                parsed = _extract_catch_from_text(raw_item)
            normalized = _normalize_catch_entry(parsed)
            if normalized:
                direct_catches.append(normalized)
        return direct_catches

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
        for key in ("details", "detail", "catch", "result"):
            if key in value:
                visit(value.get(key), context=key)

    visit(data or {}, context="result")
    deduped = []
    seen = set()
    for item in catches:
        entry = _normalize_catch_entry(item)
        if not entry:
            continue
        key = (entry["fish"], entry["grade"], entry["weight"], tuple((r["name"], r["qty"]) for r in entry["rewards"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


_LOOP_GAIN_FIELDS = (
    "expGain",
    "experienceGain",
    "lingShiGain",
    "lingshiGain",
    "lingstoneGain",
    "spiritStoneGain",
    "stoneGain",
)


def _merge_loop_gain_fields(target, data):
    for key, amount in extract_fishing_miniapp_gains(data).items():
        target[key] = int(target.get(key, 0) or 0) + amount
    return target


def _fishing_round_receipt(round_key, data):
    catches = extract_fishing_miniapp_catches(data)
    if len(catches) > 1:
        raise ValueError("ambiguous_fishing_catches")
    payload = {
        "settled_count": 1, "catches": catches,
        "rewards": extract_fishing_miniapp_rewards(data),
        "daily": extract_fishing_miniapp_daily_progress(data), **extract_fishing_miniapp_gains(data),
    }
    if isinstance(data, dict) and type(data.get("caught")) is bool:
        payload["caught"] = data["caught"]
    return {"round_key": round_key, "data": payload}


def _poll_fishing_result(requests, *, token, events, proof, failure, sleeper,
                         operation_check, result_poll_limit):
    poll_limit = max(0, int(result_poll_limit or 0))
    for attempt in range(poll_limit):
        result = requests.request("result", "result", token=token, events=events)
        if not result.ok:
            if attempt < poll_limit - 1 and _fishing_read_recovery_allowed(result):
                _wait_fishing(max(FISHING_MINIAPP_RESULT_POLL_DELAY_SEC, result.retry_after_sec), sleeper, operation_check)
                continue
            return failure(_http_failure_status(result), result.error)
        if "result" in result.data and not isinstance(result.data["result"], dict):
            return failure("not_ready", "invalid_result_shape")
        nested_result = result.data.get("result") if isinstance(result.data.get("result"), dict) else {}
        ready_values = [payload["ready"] for payload in (result.data, nested_result) if "ready" in payload]
        if any(type(value) is not bool for value in ready_values) or (True in ready_values and False in ready_values):
            return failure("not_ready", "conflicting_result_ready")
        ready = bool(ready_values) and all(ready_values)
        if ready is True:
            quota_keys = _FISHING_DAILY_NAMED_KEYS | _FISHING_DAILY_CONTAINERS
            settled_data = {key: value for key, value in (nested_result or result.data).items()
                            if _fishing_business_key(key) not in quota_keys}
            settled_data["daily"] = extract_fishing_miniapp_daily_progress(result.data)
            requests.confirm_settled(token, settled_data)
            return requests.annotate(_flow_result(True, "settled", data=settled_data,
                                                 events=events, proof=proof, active_token=token))
        events.append({"step": "result_wait", "ok": True, "attempt": attempt + 1, "ready": bool(ready)})
        if attempt < poll_limit - 1:
            _wait_fishing(FISHING_MINIAPP_RESULT_POLL_DELAY_SEC, sleeper, operation_check)
    return failure("not_ready", "result_not_ready")


def _fishing_recovery_error(token, expected_round_key, pending_action, pending_round_known, settled_round_keys):
    if not isinstance(expected_round_key, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_round_key):
        return "invalid_round_key"
    if not isinstance(token, str) or not token.strip() or hashlib.sha256(token.strip().encode()).hexdigest() != expected_round_key:
        return "round_key_mismatch"
    if pending_action not in ("start", "finish", "next"):
        return "invalid_pending_action"
    if pending_round_known is not True:
        return "unknown_next_round" if pending_action == "next" else "unknown_round"
    if (not isinstance(settled_round_keys, (list, tuple)) or len(settled_round_keys) > FISHING_MAX_DAILY_LIMIT
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) for key in settled_round_keys)
            or len(set(settled_round_keys)) != len(settled_round_keys)):
        return "invalid_confirmed_rounds"
    if expected_round_key in settled_round_keys:
        return "round_already_confirmed"
    return ""


def _fishing_recovery_failure(status, error, expected_round_key, pending_action, pending_round_known):
    key = expected_round_key if isinstance(expected_round_key, str) and re.fullmatch(r"[a-f0-9]{64}", expected_round_key) else ""
    return dict(
        _flow_result(False, status, error=error), outcome_unknown=True,
        unresolved_action=pending_action if pending_action in ("start", "finish", "next") else "",
        unresolved_round_key=key, unresolved_round_known=pending_round_known is True,
    )


def run_fishing_miniapp_recovery_lab_flow(
    *, token, init_data, expected_round_key, transport, pending_action="finish", pending_round_known=True,
    settled_round_keys=(), adapter=None, sleeper=None, result_poll_limit=FISHING_MINIAPP_RESULT_POLL_LIMIT,
    capture_sink=None, capture_source="", operation_check=None, request_budget=None, checkpoint=None,
):
    error = _fishing_recovery_error(token, expected_round_key, pending_action, pending_round_known, settled_round_keys)
    if error or not str(init_data or "").strip():
        return _fishing_recovery_failure("recovery_blocked" if error else "failed", error or "initData missing",
                                         expected_round_key, pending_action, pending_round_known)
    adapter = adapter or build_fishing_miniapp_adapter()
    if request_budget is None:
        request_budget = MiniAppRequestBudget(adapter.request_policy, sleeper=sleeper)
    requests = _FishingRequestContext(
        transport=transport, adapter=adapter, init_data=init_data, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source,
        request_budget=request_budget, operation_check=operation_check, checkpoint=checkpoint,
    )
    requests.pending = (pending_action, expected_round_key)
    requests.pending_round_known = True
    events = []

    def failure(status, reason=""):
        return requests.annotate(_flow_result(False, status, error=reason, events=events))

    try:
        result = _poll_fishing_result(
            requests, token=token.strip(), events=events, proof={}, failure=failure, sleeper=sleeper,
            operation_check=operation_check, result_poll_limit=result_poll_limit,
        )
        result.pop("_active_token", None)
        return result
    except MiniAppRequestAborted as exc:
        return failure("cancelled", exc)
    except Exception as exc:
        return failure("failed", exc)


async def run_fishing_miniapp_recovery_production_flow(
    identity_id, *, token, webview_url, expected_round_key, pending_action="finish", pending_round_known=True,
    settled_round_keys=(), init_data="", transport=None, sleeper=None, adapter=None, capture_sink=None,
    capture_source="", operation_check=None, result_poll_limit=FISHING_MINIAPP_RESULT_POLL_LIMIT, checkpoint=None,
):
    def failure(status, error):
        return _fishing_recovery_failure(status, error, expected_round_key, pending_action, pending_round_known)

    error = _fishing_recovery_error(token, expected_round_key, pending_action, pending_round_known, settled_round_keys)
    if error:
        return failure("recovery_blocked", error)
    adapter = adapter or build_fishing_miniapp_adapter()
    try:
        require_miniapp_operation(operation_check)
        init_data = str(init_data or "").strip() or await request_fishing_miniapp_init_data(
            identity_id, token=token, webview_url=webview_url, adapter=adapter, operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            return run_fishing_miniapp_recovery_lab_flow(
                token=token, init_data=init_data, expected_round_key=expected_round_key,
                pending_action=pending_action, pending_round_known=pending_round_known,
                settled_round_keys=settled_round_keys, adapter=adapter,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key, identity_id=identity_id, timeout=FISHING_MINIAPP_HTTP_TIMEOUT,
                    operation_check=operation.check,
                ),
                sleeper=operation.sleep, result_poll_limit=result_poll_limit,
                capture_sink=capture_sink, capture_source=capture_source, operation_check=operation.check,
                checkpoint=checkpoint,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppFlowCancelled:
        raise
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(failure("cancelled", "authorization_cancelled")) from None
    except MiniAppRequestAborted as exc:
        return failure("cancelled", exc)
    except Exception as exc:
        return failure("failed", exc)


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
    pond_choice="",
    bait_choice="",
    capture_sink=None,
    capture_source="",
    operation_check=None,
    request_budget=None,
    _request_context=None,
    checkpoint=None,
):
    adapter = adapter or build_fishing_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    proof = {}
    finish_submitted = False
    finish_attempted = False
    if request_budget is None:
        request_budget = MiniAppRequestBudget(adapter.request_policy, sleeper=sleeper)
    requests = _request_context or _FishingRequestContext(
        transport=transport, adapter=adapter, init_data=init_data, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source,
        request_budget=request_budget, operation_check=operation_check, checkpoint=checkpoint,
    )

    def request(endpoint, step, *, payload=None):
        return requests.request(endpoint, step, token=token, events=events, payload=payload)

    def failure(status, error="", data=None):
        if data is None and finish_attempted:
            data = {"phase": "finish_submitted" if finish_submitted else "finish_unknown", "ready": False}
        return requests.annotate(_flow_result(False, status, error=error, data=data, events=events, proof=proof))

    try:
        start_result = request("start", "start_waiting")
        if not start_result.ok:
            return failure(_http_failure_status(start_result), start_result.error)
        no_rod = _no_rod_flow_result(start_result.data, events)
        if no_rod:
            requests.confirm_idle()
            return requests.annotate(no_rod)

        view = _extract_start_view(start_result.data)
        if view["phase"] == "lobby" and view["challenge"] is None:
            requests.confirm_idle()
            token, lobby_failure = _enter_fishing_lobby(
                token=token, requests=requests, pond_choice=pond_choice, bait_choice=bait_choice, events=events,
            )
            if lobby_failure:
                return requests.annotate(lobby_failure)
            start_result = request("start", "start_after_lobby")
            if not start_result.ok:
                return failure(_http_failure_status(start_result), start_result.error)
            no_rod = _no_rod_flow_result(start_result.data, events)
            if no_rod:
                requests.confirm_idle()
                return requests.annotate(no_rod)
            view = _extract_start_view(start_result.data)
        if view["challenge"] is None:
            if view["phase"] == "expired":
                return failure("expired", "session_phase_expired")
            if view["phase"] != "waiting" or view["bite_in_ms"] > float(bite_wait_cap_ms or 0):
                return failure("not_ready", data={"phase": view["phase"], "bite_in_ms": view["bite_in_ms"]})
            wait_ms = max(0.0, view["bite_in_ms"])
            events.append({"step": "wait_bite", "ok": True, "wait_ms": wait_ms})
            _wait_fishing(wait_ms / 1000.0, sleeper, operation_check)
            start_result = request("start", "start_bite")
            if not start_result.ok:
                return failure(_http_failure_status(start_result), start_result.error)
            no_rod = _no_rod_flow_result(start_result.data, events)
            if no_rod:
                requests.confirm_idle()
                return requests.annotate(no_rod)
            view = _extract_start_view(start_result.data)

        challenge = view["challenge"]
        if not challenge:
            return failure("not_ready", data={"phase": view["phase"]})
        require_miniapp_operation(operation_check)
        proof = build_fishing_proof(challenge, rng=rng, score_low=score_low, score_high=score_high)
        events.append({
            "step": "build_proof", "ok": True, "mode": proof.get("mode"),
            "score": proof.get("score"), "event_count": len(proof.get("events") or ()),
            "durationMs": proof["durationMs"],
        })
        if proof.get("mode") == "xianxiaFishingV2":
            # Match the client replay's duration before submitting its proof.
            _wait_fishing(float(proof["durationMs"]) / 1000.0, sleeper, operation_check)

        finish_result = request("finish", "finish", payload={"fishingProof": proof})
        finish_attempted = requests.last_dispatched
        finish_submitted = finish_result.ok
        if not finish_result.ok and not (finish_attempted and _fishing_read_recovery_allowed(finish_result)):
            return failure(_http_failure_status(finish_result), finish_result.error)

        poll_limit = max(0, int(result_poll_limit or 0))
        if not finish_result.ok and poll_limit:
            _wait_fishing(max(FISHING_MINIAPP_RESULT_POLL_DELAY_SEC, finish_result.retry_after_sec), sleeper, operation_check)
        return _poll_fishing_result(
            requests, token=token, events=events, proof=proof, failure=failure, sleeper=sleeper,
            operation_check=operation_check, result_poll_limit=poll_limit,
        )
    except MiniAppRequestAborted as exc:
        return failure("cancelled", exc)
    except Exception as exc:
        return failure("failed", exc)


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
    pond_choice="",
    bait_choice="",
    rest_range_sec=FISHING_MINIAPP_CHAIN_REST_RANGE_SEC,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    request_budget=None,
    checkpoint=None,
):
    adapter = adapter or build_fishing_miniapp_adapter()
    try:
        max_rounds = max(1, min(FISHING_MAX_DAILY_LIMIT, int(max_rounds or 1)))
    except (TypeError, ValueError, OverflowError):
        max_rounds = 1
    current_token = str(token or "").strip()
    events = []
    rounds = []
    settled_count = 0
    loop_gains = {}
    loop_rewards = []
    loop_daily = {}
    last_result = {}
    last_status = "failed"
    if request_budget is None:
        request_budget = MiniAppRequestBudget(adapter.request_policy, sleeper=sleeper)
    requests = _FishingRequestContext(
        transport=transport, adapter=adapter, init_data=init_data, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source,
        request_budget=request_budget, operation_check=operation_check, checkpoint=checkpoint,
    )

    def finish(status, error="", extra=None):
        data = {
            "settled_count": settled_count, "rounds": rounds,
            "catches": [item["catch"] for item in rounds if item.get("catch")],
            "rewards": list(loop_rewards), "daily": dict(loop_daily),
            "last_status": last_status, **loop_gains, **dict(extra or {}),
        }
        if isinstance(last_result.get("data"), dict):
            data.update({
                f"last_{key}": value for key, value in last_result["data"].items()
                if key not in data and key not in _LOOP_GAIN_FIELDS
            })
        return requests.annotate(_flow_result(settled_count > 0, status, error=error, data=data, events=events))

    def request(endpoint, step, *, payload=None):
        return requests.request(endpoint, step, token=current_token, events=events, payload=payload)

    try:
        for index in range(max_rounds):
            require_miniapp_operation(operation_check)
            round_result = run_fishing_miniapp_lab_flow(
                token=current_token, init_data=init_data, transport=transport,
                adapter=adapter, rng=rng, sleeper=sleeper, bite_wait_cap_ms=bite_wait_cap_ms,
                result_poll_limit=result_poll_limit, score_low=score_low, score_high=score_high,
                pond_choice=pond_choice, bait_choice=bait_choice,
                capture_sink=capture_sink, capture_source=capture_source,
                operation_check=operation_check, request_budget=request_budget, _request_context=requests,
            )
            last_result = dict(round_result or {})
            current_token = str(last_result.pop("_active_token", "") or current_token)
            last_status = str(last_result.get("status") or "").strip()
            round_data = last_result.get("data") or {}
            confirmed = last_result.get("ok") is True and last_status == "settled"
            # Completion survives optional catch decoding and later admission failures.
            if confirmed:
                settled_count += 1
                _merge_loop_gain_fields(loop_gains, round_data)
                loop_daily = extract_fishing_miniapp_daily_progress(round_data)
            rounds.append({
                "index": index + 1, "ok": confirmed, "status": last_status,
                "data_keys": sorted(round_data), "event_count": len(last_result.get("events") or ()),
                "proof_score": (last_result.get("proof") or {}).get("score"),
                "catch": {},
            })
            events.extend({**event, "round_index": index + 1} for event in last_result.get("events") or ())
            events.append({"step": "round", "ok": confirmed, "index": index + 1, "status": last_status})
            if not confirmed:
                partial_not_ready = settled_count > 0 and last_status == "not_ready"
                return finish(
                    "partial_not_ready" if partial_not_ready else (last_status or "failed"),
                    "" if partial_not_ready else last_result.get("error") or "",
                )
            catch_summary = extract_fishing_miniapp_catches(round_data)
            if len(catch_summary) > 1:
                raise ValueError("ambiguous_fishing_catches")
            rounds[-1]["catch"] = catch_summary[0] if catch_summary else {}
            loop_rewards.extend(extract_fishing_miniapp_rewards(round_data))
            if requests.checkpoint_error:
                return finish("persistence_pending", requests.checkpoint_error)
            if index >= max_rounds - 1:
                break

            next_payload = {}
            if str(pond_choice or "").strip() or str(bait_choice or "").strip():
                shop_result = request("shop", "next_shop")
                if not shop_result.ok:
                    return finish(_http_failure_status(shop_result, "shop_failed"), shop_result.error)
                shop = _fishing_shop(shop_result.data)
                pond = _select_fishing_pond(shop, pond_choice)
                bait = _select_fishing_bait(shop, bait_choice)
                if not pond or not bait:
                    status = "pond_unavailable" if not pond else "bait_missing"
                    return finish(status, status)
                next_payload = {
                    "pondKey": str(pond.get("key") or "").strip(),
                    "baitItemId": str(bait.get("itemId") or "").strip(),
                }
            next_result = request("next", "next", payload=next_payload)
            if not next_result.ok:
                extra = {"next_status": _http_failure_status(next_result), "next_error": next_result.error}
                if isinstance(next_result.data, dict) and next_result.data.get("baitName"):
                    extra["next_bait_name"] = str(next_result.data.get("baitName") or "")
                return finish(_http_failure_status(next_result, "next_failed"), next_result.error, extra)

            new_token = _extract_next_token(next_result.data)
            if not new_token:
                return finish("next_unavailable", "next token missing", {"next_status": "missing_token"})
            current_token = new_token
            low, high = rest_range_sec
            _wait_fishing(random.uniform(float(low), float(high)), sleeper, operation_check)
    except MiniAppRequestAborted as exc:
        return finish("cancelled", exc)
    except Exception as exc:
        return finish("failed", exc)

    return finish("settled")


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
    "extract_fishing_miniapp_daily_progress",
    "extract_fishing_miniapp_gains",
    "extract_fishing_miniapp_rewards",
    "extract_fishing_miniapp_launch_from_dwelling_payload",
    "request_fishing_miniapp_init_data",
    "run_fishing_miniapp_lab_flow",
    "run_fishing_miniapp_loop_lab_flow",
    "run_fishing_miniapp_production_flow",
    "run_fishing_miniapp_recovery_lab_flow",
    "run_fishing_miniapp_recovery_production_flow",
]
