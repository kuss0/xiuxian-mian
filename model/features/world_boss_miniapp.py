"""World Boss MiniApp pure-HTTP protocol helpers.

This module is intentionally lab-only.  It declares and exercises the HTTP
protocol without wiring schedulers, runtime state, UI controls, or Telegram
command handling.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

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
WORLD_BOSS_MINIAPP_START_PARAM_PATTERN = r"qyz_[A-Za-z0-9_-]{4,160}"
WORLD_BOSS_PROOF_MODE = "qyz_focus_burst_v2"
WORLD_BOSS_STANCE = "强攻"
WORLD_BOSS_HOLD_RANGE_MS = (700, 1100)
WORLD_BOSS_JOIN_WINDOW_SEC = 60.0

WORLD_BOSS_ERROR_TYPES = (
    "boss_token_missing",
    "boss_event_closed",
    "boss_join_closed",
    "boss_action_limit",
    "boss_not_enough_participants",
    "boss_battle_not_started",
    "boss_token_used",
    "boss_token_expired",
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
_TERMINAL_ERROR_TYPES = {
    "boss_token_missing",
    "boss_event_closed",
    "boss_join_closed",
    "boss_action_limit",
    "boss_not_enough_participants",
    "boss_token_used",
    "boss_token_expired",
}


@dataclass(frozen=True)
class WorldBossJoinReceipt:
    """Non-secret admission result passed from the join phase to battle."""

    joined: bool
    status: str
    player_id: str = ""
    identity_id: int = 0
    account_id: int = 0
    calibrated: bool = False
    terminal: bool = False
    needs_identity_selection: bool = False
    verification_required: bool = False
    identity_choices: tuple = field(default_factory=tuple, repr=False)
    challenge: dict = field(default_factory=dict, repr=False)
    error: str = ""

    def safe_summary(self):
        return {
            "joined": bool(self.joined),
            "status": self.status,
            "player_id": self.player_id,
            "identity_id": int(self.identity_id or 0),
            "account_id": int(self.account_id or 0),
            "calibrated": bool(self.calibrated),
            "terminal": bool(self.terminal),
            "needs_identity_selection": bool(self.needs_identity_selection),
            "verification_required": bool(self.verification_required),
            "identity_choice_count": len(self.identity_choices),
            "has_challenge": bool(self.challenge),
            "error": sanitize_webapp_secret_text(self.error),
        }


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


def is_terminal_world_boss_miniapp_error(error):
    return classify_world_boss_miniapp_error(error) in _TERMINAL_ERROR_TYPES


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
    for key in ("identityChoices", "identities", "identityOptions", "players", "availablePlayers"):
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


def _mapping_from_payload(data, keys):
    if not isinstance(data, dict):
        return {}
    direct = _first_mapping(data, keys)
    if direct:
        return direct
    for key in ("data", "state", "event", "battle", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = _mapping_from_payload(nested, keys)
            if found:
                return found
    return {}


def parse_world_boss_join_response(
    data,
    *,
    player_id=None,
    identity_id=0,
    account_id=0,
    calibrated=False,
    assume_joined_on_ok=False,
):
    data = data if isinstance(data, dict) else {}
    selection = _identity_selection(data)
    if _verification_required(data):
        return WorldBossJoinReceipt(
            False,
            "verification_required",
            player_id=str(player_id or ""),
            identity_id=_int_value(identity_id),
            account_id=_int_value(account_id),
            calibrated=bool(calibrated),
            verification_required=True,
            error="xianxia-verify required",
        )
    if selection is not None:
        return WorldBossJoinReceipt(
            False,
            "needs_identity_selection",
            player_id=str(player_id or ""),
            identity_id=_int_value(identity_id),
            account_id=_int_value(account_id),
            calibrated=bool(calibrated),
            needs_identity_selection=True,
            identity_choices=tuple(selection.get("identities") or ()),
        )

    error = _error_from_payload(data)
    if error:
        return WorldBossJoinReceipt(
            False,
            error,
            player_id=str(player_id or ""),
            identity_id=_int_value(identity_id),
            account_id=_int_value(account_id),
            calibrated=bool(calibrated),
            terminal=is_terminal_world_boss_miniapp_error(error),
            error=error,
        )

    challenge = _challenge_from_payload(data)
    player = _mapping_from_payload(data, ("player", "identity"))
    room = _mapping_from_payload(data, ("room",))
    resolved_player_id = str(
        player.get("playerId")
        or player.get("id")
        or data.get("playerId")
        or player_id
        or ""
    )
    explicit_joined = any(
        data.get(key) is True
        for key in ("joined", "isJoined", "participating", "entered")
    )
    joined = bool(
        explicit_joined
        or challenge
        or player
        or room
        or (assume_joined_on_ok and data.get("ok") is True)
    )
    return WorldBossJoinReceipt(
        joined,
        "joined_calibrated" if joined and calibrated else "joined" if joined else "unknown",
        player_id=resolved_player_id,
        identity_id=_int_value(identity_id),
        account_id=_int_value(account_id),
        calibrated=bool(calibrated),
        challenge=dict(challenge or {}),
        error="" if joined else "join state not confirmed",
    )


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


def filter_world_boss_action_plan(plan, current_elapsed_ms, *, grace_ms=0):
    """Keep only windows whose target has not already passed."""

    cutoff_ms = max(0, _int_value(current_elapsed_ms)) - max(0, _int_value(grace_ms))
    return [
        dict(action)
        for action in plan or ()
        if _int_value((action or {}).get("elapsedMs"), -1) >= cutoff_ms
    ]


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
    return {
        "mode": WORLD_BOSS_PROOF_MODE,
        "challengeId": challenge_id,
        "stance": WORLD_BOSS_STANCE,
        "durationMs": duration_ms,
        "playerHp": max(0, _int_value(_latest_value(payloads, ("playerHp", "hp"), challenge.get("playerHp", 0)))),
        "dead": bool(_latest_value(payloads, ("dead", "isDead"), challenge.get("dead", False))),
        "actions": clean_actions,
        "clientStats": _client_stats(payloads),
        # Damage has already been committed by the serial /hit requests.
        "realtimeDamageApplied": True,
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


def _failed_join_receipt(result, *, player_id=None, identity_id=0, account_id=0, calibrated=False):
    status = classify_world_boss_miniapp_error(result.error)
    return WorldBossJoinReceipt(
        False,
        status,
        player_id=str(player_id or ""),
        identity_id=_int_value(identity_id),
        account_id=_int_value(account_id),
        calibrated=bool(calibrated),
        terminal=is_terminal_world_boss_miniapp_error(status),
        error=str(result.error or status),
    )


def reconcile_world_boss_join_state_lab(
    *,
    token,
    init_data,
    player_id=None,
    identity_id=0,
    account_id=0,
    transport,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    events=None,
):
    """Calibrate an uncertain join using read-only /state."""

    adapter = adapter or build_world_boss_miniapp_adapter()
    events = events if events is not None else []
    state_request = build_world_boss_miniapp_request(
        "state", token=token, init_data=init_data, adapter=adapter,
    )
    state_result = execute_miniapp_http_request(
        state_request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="join_state_reconcile",
    )
    _append_http_event(events, "join_state_reconcile", state_result)
    if not state_result.ok:
        return _failed_join_receipt(
            state_result,
            player_id=player_id,
            identity_id=identity_id,
            account_id=account_id,
            calibrated=True,
        )
    return parse_world_boss_join_response(
        state_result.data,
        player_id=player_id,
        identity_id=identity_id,
        account_id=account_id,
        calibrated=True,
        assume_joined_on_ok=False,
    )


def join_world_boss_miniapp_lab(
    *,
    token,
    init_data,
    player_id=None,
    identity_id=0,
    account_id=0,
    transport,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    events=None,
):
    """Perform one admission attempt without entering the battle loop."""

    adapter = adapter or build_world_boss_miniapp_adapter()
    events = events if events is not None else []
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return WorldBossJoinReceipt(False, "boss_token_missing", terminal=True, error="boss_token_missing")
    if not init_data:
        return WorldBossJoinReceipt(False, "failed", error="initData missing")

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
        backoff_sec=(),
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start",
    )
    _append_http_event(events, "start", start_result)
    if not start_result.ok:
        if bool(start_result.retryable) or start_result.error_type == "transient":
            return reconcile_world_boss_join_state_lab(
                token=token,
                init_data=init_data,
                player_id=player_id,
                identity_id=identity_id,
                account_id=account_id,
                transport=transport,
                adapter=adapter,
                sleeper=sleeper,
                capture_sink=capture_sink,
                capture_source=capture_source,
                events=events,
            )
        return _failed_join_receipt(
            start_result,
            player_id=player_id,
            identity_id=identity_id,
            account_id=account_id,
        )
    return parse_world_boss_join_response(
        start_result.data,
        player_id=player_id,
        identity_id=identity_id,
        account_id=account_id,
        assume_joined_on_ok=True,
    )


def join_world_boss_batch_lab(
    entries,
    *,
    transport,
    adapter=None,
    sleeper=None,
    clock=None,
    opened_at=None,
    join_window_sec=WORLD_BOSS_JOIN_WINDOW_SEC,
    capture_sink=None,
    capture_source="",
):
    """Join all selected accounts serially before any battle work starts."""

    clock = clock or time.monotonic
    started_at = float(clock()) if opened_at is None else float(opened_at)
    deadline_at = started_at + max(1.0, float(join_window_sec or WORLD_BOSS_JOIN_WINDOW_SEC))
    receipts = []
    events = []
    for entry in entries or ():
        entry = dict(entry or {})
        if float(clock()) >= deadline_at:
            receipts.append(WorldBossJoinReceipt(
                False,
                "join_deadline_exceeded",
                player_id=str(entry.get("player_id") or ""),
                identity_id=_int_value(entry.get("identity_id")),
                account_id=_int_value(entry.get("account_id")),
                terminal=True,
                error="world boss join window exceeded",
            ))
            continue
        receipt = join_world_boss_miniapp_lab(
            token=entry.get("token"),
            init_data=entry.get("init_data"),
            player_id=entry.get("player_id"),
            identity_id=entry.get("identity_id", 0),
            account_id=entry.get("account_id", 0),
            transport=transport,
            adapter=adapter,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            events=events,
        )
        receipts.append(receipt)
        if receipt.terminal and receipt.status in {
            "boss_token_missing", "boss_event_closed", "boss_join_closed", "boss_token_expired",
        }:
            break
    joined_count = sum(1 for receipt in receipts if receipt.joined)
    return {
        "ok": bool(receipts) and joined_count == len(receipts),
        "status": "join_barrier_ready" if receipts and joined_count == len(receipts) else "join_barrier_partial",
        "deadline_at": deadline_at,
        "joined_count": joined_count,
        "receipts": tuple(receipts),
        "events": events,
    }


def run_world_boss_joined_battle_lab_flow(
    receipt,
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    clock=None,
    capture_sink=None,
    capture_source="",
    events=None,
):
    """Refresh state, discard expired windows, then execute one joined battle."""

    adapter = adapter or build_world_boss_miniapp_adapter()
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    events = events if events is not None else []
    if not isinstance(receipt, WorldBossJoinReceipt) or not receipt.joined:
        status = getattr(receipt, "status", "join_not_confirmed")
        error = getattr(receipt, "error", "join not confirmed")
        return _flow_result(False, status, error=error, events=events)

    state_request = build_world_boss_miniapp_request(
        "state", token=token, init_data=init_data, adapter=adapter,
    )
    state_result = execute_miniapp_http_request(
        state_request,
        transport,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="battle_state_refresh",
    )
    _append_http_event(events, "battle_state_refresh", state_result)
    if not state_result.ok:
        status = classify_world_boss_miniapp_error(state_result.error)
        return _flow_result(False, status, error=state_result.error, data=state_result.data, events=events)
    state_data = state_result.data if isinstance(state_result.data, dict) else {}
    if _verification_required(state_data):
        return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
    payload_error = _error_from_payload(state_data)
    if payload_error:
        return _flow_result(False, payload_error, error=payload_error, data=state_data, events=events)

    challenge = _challenge_from_payload(state_data) or dict(receipt.challenge or {})
    if not challenge:
        return _flow_result(False, "not_ready", error="world boss challenge missing", events=events)
    initial_elapsed_ms = _current_elapsed_ms(state_data, challenge)
    try:
        full_plan = build_world_boss_action_plan(challenge, rng=rng)
    except ValueError as exc:
        return _flow_result(False, "not_ready", error=exc, events=events)
    plan = filter_world_boss_action_plan(full_plan, initial_elapsed_ms)
    events.append({
        "step": "plan",
        "ok": bool(plan),
        "window_count": len(plan),
        "expired_window_count": len(full_plan) - len(plan),
        "current_elapsed_ms": initial_elapsed_ms,
    })
    if not plan:
        return _flow_result(False, "windows_expired", error="all world boss windows expired", events=events)

    timeline_origin = float(clock()) - (float(initial_elapsed_ms) / 1000.0)
    hit_payloads = []
    executed_actions = []
    for action in plan:
        current_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
        if current_elapsed_ms > int(action["elapsedMs"]):
            events.append({
                "step": "skip_expired_window",
                "ok": True,
                "windowId": action["windowId"],
                "current_elapsed_ms": current_elapsed_ms,
            })
            continue
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
        executed_actions.append(action)
        hit_payloads.append(hit_data)

    if not executed_actions:
        return _flow_result(False, "windows_expired", error="world boss windows expired before hit", events=events)
    proof = build_world_boss_proof(challenge, executed_actions, hit_payloads)
    finish_request = build_world_boss_miniapp_request(
        "finish", token=token, init_data=init_data, boss_proof=proof, adapter=adapter,
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
    """Backward-compatible single identity join followed by refreshed battle."""

    events = []
    receipt = join_world_boss_miniapp_lab(
        token=token,
        init_data=init_data,
        player_id=player_id,
        transport=transport,
        adapter=adapter,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        events=events,
    )
    if not receipt.joined:
        data = {"needsIdentitySelection": True, "identities": list(receipt.identity_choices)} if receipt.needs_identity_selection else {}
        return _flow_result(False, receipt.status, error=receipt.error, data=data, events=events)
    return run_world_boss_joined_battle_lab_flow(
        receipt,
        token=token,
        init_data=init_data,
        transport=transport,
        adapter=adapter,
        rng=rng,
        sleeper=sleeper,
        clock=clock,
        capture_sink=capture_sink,
        capture_source=capture_source,
        events=events,
    )


def run_world_boss_miniapp_batch_lab_flow(
    entries,
    *,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    clock=None,
    opened_at=None,
    join_window_sec=WORLD_BOSS_JOIN_WINDOW_SEC,
    capture_sink=None,
    capture_source="",
):
    """Join every account first, then run joined battles one at a time."""

    entries = [dict(entry or {}) for entry in (entries or ())]
    barrier = join_world_boss_batch_lab(
        entries,
        transport=transport,
        adapter=adapter,
        sleeper=sleeper,
        clock=clock,
        opened_at=opened_at,
        join_window_sec=join_window_sec,
        capture_sink=capture_sink,
        capture_source=capture_source,
    )
    battle_results = []
    for entry, receipt in zip(entries, barrier["receipts"]):
        if not receipt.joined:
            continue
        battle_results.append(run_world_boss_joined_battle_lab_flow(
            receipt,
            token=entry.get("token"),
            init_data=entry.get("init_data"),
            transport=transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper,
            clock=clock,
            capture_sink=capture_sink,
            capture_source=capture_source,
        ))
    all_battles_ok = bool(battle_results) and all(result.get("ok") for result in battle_results)
    overall_ok = bool(barrier["ok"]) and all_battles_ok
    return {
        "ok": overall_ok,
        "status": "settled" if overall_ok else barrier["status"],
        "barrier": barrier,
        "battle_results": battle_results,
    }
