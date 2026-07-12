"""World Boss MiniApp pure-HTTP protocol helpers.

This module is intentionally lab-only.  It declares and exercises the HTTP
protocol without wiring schedulers, runtime state, UI controls, or Telegram
command handling.
"""

from __future__ import annotations

import random
import time

from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    build_miniapp_http_request,
    execute_miniapp_http_request,
    sanitize_webapp_secret_text,
)


WORLD_BOSS_MINIAPP_GAME_KEY = "world_boss"
WORLD_BOSS_MINIAPP_LABEL = "世界 Boss"
WORLD_BOSS_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
WORLD_BOSS_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
WORLD_BOSS_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-world-boss/"
WORLD_BOSS_MINIAPP_ENDPOINTS = {
    "start": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}start",
    "state": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}state",
    "hit": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}hit",
    "finish": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}finish",
}
WORLD_BOSS_MINIAPP_START_PARAM_PATTERN = r"boss_[A-Za-z0-9_-]{4,160}"
WORLD_BOSS_PROOF_MODE = "qyz_focus_burst_v2"
WORLD_BOSS_STANCE = "强攻"
WORLD_BOSS_HOLD_RANGE_MS = (700, 1100)

WORLD_BOSS_ERROR_TYPES = (
    "boss_token_missing",
    "event_closed",
    "join_closed",
    "action_limit",
    "not_enough_participants",
)

_VERIFICATION_KEYS = (
    "needsVerification",
    "verificationRequired",
    "requiresVerification",
    "captchaRequired",
)
_VERIFICATION_MARKERS = (
    "xianxia-verify",
    "verification_required",
    "needs_verification",
    "captcha_required",
    "验证码",
)
_STAT_KEYS = ("dodges", "grazes", "damage", "hits", "perfects", "combo", "bestCombo")


def build_world_boss_miniapp_adapter(
    *,
    api_base_url=WORLD_BOSS_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=WORLD_BOSS_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=WORLD_BOSS_MINIAPP_GAME_KEY,
        label=WORLD_BOSS_MINIAPP_LABEL,
        bot_username=bot_username,
        allowed_bot_username_patterns=(r"hantianzun\d+_bot",),
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(WORLD_BOSS_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(WORLD_BOSS_MINIAPP_ENDPOINTS),
        start_param_pattern=WORLD_BOSS_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_world_boss_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=WORLD_BOSS_MINIAPP_GAME_KEY,
        label=WORLD_BOSS_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only HTTP protocol; no production scheduler or verification bypass",
        replaces_commands=(".世界boss",),
        state_outputs=("event_state", "identity_selection", "battle_result"),
        steps=(
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData", "playerId"),
                note="join with one selected player identity",
            ),
            MiniAppFlowStep(
                key="state",
                endpoint="state",
                required_payload_keys=("token", "initData"),
                note="read challenge when start does not include windows",
            ),
            MiniAppFlowStep(
                key="plan",
                endpoint="local_window_plan",
                method="LOCAL",
                required_payload_keys=("challenge",),
                sends_init_data=False,
                note="schedule conservative actions at server window centers",
            ),
            MiniAppFlowStep(
                key="hit",
                endpoint="hit",
                required_payload_keys=("token", "initData", "challengeId", "windowId", "elapsedMs", "holdMs"),
                note="one request per real window; no retry after uncertain mutation",
            ),
            MiniAppFlowStep(
                key="finish",
                endpoint="finish",
                required_payload_keys=("token", "initData", "bossProof"),
                note="submit once after all planned hit calls",
            ),
        ),
    )


def build_world_boss_miniapp_request(
    endpoint,
    *,
    token,
    init_data_session=None,
    init_data="",
    player_id=None,
    challenge_id="",
    window_id="",
    elapsed_ms=None,
    hold_ms=None,
    boss_proof=None,
    adapter=None,
):
    adapter = adapter or build_world_boss_miniapp_adapter()
    endpoint = str(endpoint or "").strip()
    if endpoint not in WORLD_BOSS_MINIAPP_ENDPOINTS:
        raise ValueError(f"unsupported world boss endpoint: {endpoint}")

    payload = {"token": str(token or "").strip()}
    if endpoint == "start" and player_id not in (None, ""):
        payload["playerId"] = player_id
    elif endpoint == "hit":
        payload.update({
            "challengeId": str(challenge_id or "").strip(),
            "windowId": str(window_id or "").strip(),
            "elapsedMs": _int_value(elapsed_ms),
            "holdMs": _int_value(hold_ms),
        })
    elif endpoint == "finish":
        payload["bossProof"] = dict(boss_proof or {})

    return build_miniapp_http_request(
        adapter,
        endpoint,
        payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )


def classify_world_boss_miniapp_error(error):
    raw = str(error or "").strip().lower()
    for error_type in WORLD_BOSS_ERROR_TYPES:
        if raw == error_type or error_type in raw:
            return error_type
    return "failed"


def _int_value(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _first_mapping(value, keys):
    if not isinstance(value, dict):
        return {}
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return candidate
    return {}


def _challenge_from_payload(data):
    if not isinstance(data, dict):
        return {}
    challenge = _first_mapping(data, ("challenge", "bossChallenge", "boss_challenge"))
    if challenge:
        return challenge
    for key in ("state", "event", "battle", "result", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            challenge = _challenge_from_payload(nested)
            if challenge:
                return challenge
    if data.get("challengeId") and isinstance(data.get("windows"), list):
        return data
    return {}


def _error_from_payload(data):
    if not isinstance(data, dict):
        return ""
    for key in ("error", "code", "reason", "message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            classified = classify_world_boss_miniapp_error(value)
            if classified != "failed":
                return classified
    return ""


def _verification_required(data):
    if not isinstance(data, dict):
        return False
    if any(data.get(key) is True for key in _VERIFICATION_KEYS):
        return True
    text = " ".join(str(data.get(key) or "") for key in ("error", "code", "reason", "message", "verifyUrl"))
    lowered = text.lower()
    if any(marker in lowered for marker in _VERIFICATION_MARKERS):
        return True
    return any(_verification_required(value) for value in data.values() if isinstance(value, dict))


def _identity_selection(data):
    if not isinstance(data, dict):
        return None
    required = bool(data.get("needsIdentitySelection"))
    options = None
    for key in ("identities", "identityOptions", "players", "availablePlayers"):
        if isinstance(data.get(key), list):
            options = data.get(key)
            break
    if not required:
        for key in ("identity", "selection", "data", "state"):
            nested = data.get(key)
            if isinstance(nested, dict):
                found = _identity_selection(nested)
                if found is not None:
                    return found
        return None
    safe_options = []
    for item in options or ():
        if not isinstance(item, dict):
            continue
        safe_options.append({
            key: item.get(key)
            for key in ("playerId", "username", "daoName", "label", "selected", "available")
            if key in item
        })
    return {"needsIdentitySelection": True, "identities": safe_options}


def _window_center_ms(window):
    for key in ("centerMs", "center", "elapsedMs", "targetMs"):
        if window.get(key) not in (None, ""):
            return _int_value(window.get(key), -1)
    start = None
    end = None
    for key in ("startMs", "openMs", "fromMs"):
        if window.get(key) not in (None, ""):
            start = _int_value(window.get(key), -1)
            break
    for key in ("endMs", "closeMs", "toMs"):
        if window.get(key) not in (None, ""):
            end = _int_value(window.get(key), -1)
            break
    if start is not None and end is not None and start >= 0 and end >= start:
        return start + (end - start) // 2
    return -1


def build_world_boss_action_plan(challenge, *, rng=None, hold_range_ms=WORLD_BOSS_HOLD_RANGE_MS):
    rng = rng or random
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise ValueError("challengeId missing")
    windows = challenge.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("challenge windows missing")
    try:
        hold_low, hold_high = hold_range_ms
        hold_low = max(700, int(hold_low))
        hold_high = min(1100, int(hold_high))
    except (TypeError, ValueError, OverflowError):
        hold_low, hold_high = WORLD_BOSS_HOLD_RANGE_MS
    if hold_low > hold_high:
        raise ValueError("invalid hold range")

    plan = []
    seen = set()
    for window in windows:
        if not isinstance(window, dict):
            continue
        window_id = str(window.get("windowId") or window.get("id") or "").strip()
        elapsed_ms = _window_center_ms(window)
        if not window_id or window_id in seen or elapsed_ms < 0:
            continue
        seen.add(window_id)
        hold_ms = int(rng.randint(hold_low, hold_high))
        plan.append({
            "challengeId": challenge_id,
            "windowId": window_id,
            "elapsedMs": elapsed_ms,
            "holdMs": hold_ms,
            "stance": WORLD_BOSS_STANCE,
        })
    plan.sort(key=lambda item: (item["elapsedMs"], item["windowId"]))
    if not plan:
        raise ValueError("challenge has no usable windows")
    return plan


def _nested_mappings(data):
    if not isinstance(data, dict):
        return
    yield data
    for key in ("result", "state", "battle", "player", "stats", "clientStats", "boss"):
        nested = data.get(key)
        if isinstance(nested, dict):
            yield from _nested_mappings(nested)


def _latest_value(payloads, keys, default=0):
    value = default
    for payload in payloads:
        for mapping in _nested_mappings(payload):
            for key in keys:
                if mapping.get(key) not in (None, ""):
                    value = mapping.get(key)
                    break
    return value


def _client_stats(hit_payloads):
    latest = {}
    for payload in hit_payloads:
        for mapping in _nested_mappings(payload):
            if any(key in mapping for key in _STAT_KEYS):
                latest = mapping
    return {key: max(0, _int_value(latest.get(key), 0)) for key in _STAT_KEYS}


def build_world_boss_proof(challenge, actions, hit_payloads):
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise ValueError("challengeId missing")
    clean_actions = [
        {
            "t": _int_value(action.get("elapsedMs")),
            "holdMs": _int_value(action.get("holdMs")),
            "stance": WORLD_BOSS_STANCE,
        }
        for action in actions or ()
    ]
    if not clean_actions:
        raise ValueError("boss actions missing")
    payloads = [dict(item or {}) for item in hit_payloads or ()]
    duration_ms = max(action["t"] + action["holdMs"] for action in clean_actions)
    duration_ms = max(duration_ms, _int_value(challenge.get("minDurationMs"), 0))
    realtime_damage = _latest_value(payloads, ("realtimeDamageApplied", "damageApplied", "appliedDamage"), 0)
    return {
        "mode": WORLD_BOSS_PROOF_MODE,
        "challengeId": challenge_id,
        "stance": WORLD_BOSS_STANCE,
        "durationMs": duration_ms,
        "playerHp": max(0, _int_value(_latest_value(payloads, ("playerHp", "hp"), challenge.get("playerHp", 0)))),
        "dead": bool(_latest_value(payloads, ("dead", "isDead"), challenge.get("dead", False))),
        "actions": clean_actions,
        "clientStats": _client_stats(payloads),
        "realtimeDamageApplied": max(0, _int_value(realtime_damage, 0)),
    }


def _flow_result(ok, status, *, error="", data=None, events=None, proof=None):
    return {
        "ok": bool(ok),
        "status": str(status or "failed"),
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
        "proof": dict(proof or {}),
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


def _current_elapsed_ms(data, challenge):
    for payload in (data, challenge):
        if not isinstance(payload, dict):
            continue
        for key in ("elapsedMs", "currentElapsedMs", "battleElapsedMs"):
            if payload.get(key) not in (None, ""):
                return max(0, _int_value(payload.get(key)))
    return 0


def run_world_boss_miniapp_lab_flow(
    *,
    token,
    init_data,
    player_id=None,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    clock=None,
    capture_sink=None,
    capture_source="",
):
    """Join and execute one World Boss challenge without browser automation."""

    adapter = adapter or build_world_boss_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    if not token:
        return _flow_result(False, "boss_token_missing", error="boss_token_missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_world_boss_miniapp_request(
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
        status = classify_world_boss_miniapp_error(start_result.error)
        return _flow_result(False, status, error=start_result.error, data=start_result.data, events=events)
    start_data = start_result.data if isinstance(start_result.data, dict) else {}
    if _verification_required(start_data):
        return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
    selection = _identity_selection(start_data)
    if selection is not None:
        return _flow_result(False, "needs_identity_selection", data=selection, events=events)
    payload_error = _error_from_payload(start_data)
    if payload_error:
        return _flow_result(False, payload_error, error=payload_error, data=start_data, events=events)

    challenge = _challenge_from_payload(start_data)
    state_data = start_data
    if not challenge:
        state_request = build_world_boss_miniapp_request(
            "state",
            token=token,
            init_data=init_data,
            adapter=adapter,
        )
        state_result = execute_miniapp_http_request(
            state_request,
            transport,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="state",
        )
        _append_http_event(events, "state", state_result)
        if not state_result.ok:
            status = classify_world_boss_miniapp_error(state_result.error)
            return _flow_result(False, status, error=state_result.error, data=state_result.data, events=events)
        state_data = state_result.data if isinstance(state_result.data, dict) else {}
        if _verification_required(state_data):
            return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
        selection = _identity_selection(state_data)
        if selection is not None:
            return _flow_result(False, "needs_identity_selection", data=selection, events=events)
        payload_error = _error_from_payload(state_data)
        if payload_error:
            return _flow_result(False, payload_error, error=payload_error, data=state_data, events=events)
        challenge = _challenge_from_payload(state_data)
    if not challenge:
        return _flow_result(False, "not_ready", error="world boss challenge missing", events=events)

    try:
        plan = build_world_boss_action_plan(challenge, rng=rng)
    except ValueError as exc:
        return _flow_result(False, "not_ready", error=exc, events=events)
    events.append({"step": "plan", "ok": True, "window_count": len(plan)})

    initial_elapsed_ms = _current_elapsed_ms(state_data, challenge)
    timeline_origin = float(clock()) - (float(initial_elapsed_ms) / 1000.0)
    hit_payloads = []
    for action in plan:
        current_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
        wait_ms = max(0, int(action["elapsedMs"]) - current_elapsed_ms)
        if wait_ms:
            sleeper(wait_ms / 1000.0)
        events.append({"step": "wait_window", "ok": True, "windowId": action["windowId"], "wait_ms": wait_ms})
        hit_request = build_world_boss_miniapp_request(
            "hit",
            token=token,
            init_data=init_data,
            challenge_id=action["challengeId"],
            window_id=action["windowId"],
            elapsed_ms=action["elapsedMs"],
            hold_ms=action["holdMs"],
            adapter=adapter,
        )
        hit_result = execute_miniapp_http_request(
            hit_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="hit",
        )
        _append_http_event(events, "hit", hit_result)
        if not hit_result.ok:
            status = classify_world_boss_miniapp_error(hit_result.error)
            return _flow_result(False, status, error=hit_result.error, data=hit_result.data, events=events)
        hit_data = hit_result.data if isinstance(hit_result.data, dict) else {}
        if _verification_required(hit_data):
            return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
        hit_payloads.append(hit_data)

    proof = build_world_boss_proof(challenge, plan, hit_payloads)
    finish_request = build_world_boss_miniapp_request(
        "finish",
        token=token,
        init_data=init_data,
        boss_proof=proof,
        adapter=adapter,
    )
    finish_result = execute_miniapp_http_request(
        finish_request,
        transport,
        sleeper=sleeper,
        backoff_sec=(),
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="finish",
    )
    _append_http_event(events, "finish", finish_result)
    if not finish_result.ok:
        status = classify_world_boss_miniapp_error(finish_result.error)
        return _flow_result(False, status, error=finish_result.error, data=finish_result.data, events=events, proof=proof)
    finish_data = finish_result.data if isinstance(finish_result.data, dict) else {}
    if _verification_required(finish_data):
        return _flow_result(False, "verification_required", error="xianxia-verify required", events=events, proof=proof)
    return _flow_result(True, "settled", data=finish_data, events=events, proof=proof)

