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
    "begin": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}begin",
    "hit": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}hit",
    "finish": f"{WORLD_BOSS_MINIAPP_API_PATH_PREFIX}finish",
}
WORLD_BOSS_MINIAPP_START_PARAM_PATTERN = r"qyz_[A-Za-z0-9_-]{4,160}"
WORLD_BOSS_PROOF_MODE = "qyz_focus_burst_v2"
WORLD_BOSS_STANCE = "强攻"
WORLD_BOSS_PERFECT_HOLD_MIN_MS = 520
WORLD_BOSS_PERFECT_HOLD_MAX_MS = 1250
# The server accepts a broad perfect-charge band.  The lower edge is safe for
# timing, but it leaves substantial damage on the table.  Stay in the upper
# middle of that band without touching the 1.25s timeout boundary.
WORLD_BOSS_OPTIMAL_HOLD_MIN_MS = 1020
WORLD_BOSS_OPTIMAL_HOLD_MAX_MS = 1160
WORLD_BOSS_RELEASE_LEAD_MS = 120
WORLD_BOSS_DEFAULT_HIT_MS = 560
WORLD_BOSS_JOIN_WINDOW_SEC = 60.0
WORLD_BOSS_JOIN_READY_LEAD_SEC = 3.0
WORLD_BOSS_START_REFRESH_SEC = 6.0
WORLD_BOSS_START_REFRESH_NEAR_LOCK_SEC = 1.2
WORLD_BOSS_START_RECONNECT_SEC = 4.0
WORLD_BOSS_START_RATE_LIMIT_BACKOFF_SEC = 12.0
WORLD_BOSS_START_MAX_CONSECUTIVE_429 = 3
WORLD_BOSS_AUTO_START_DELAY_SEC = 0.28
WORLD_BOSS_SPAWN_AUTO_START_DELAY_SEC = 1.25
WORLD_BOSS_FINISH_GRACE_MS = 2200

WORLD_BOSS_ERROR_TYPES = (
    "boss_token_missing",
    "boss_event_closed",
    "boss_join_closed",
    "boss_action_limit",
    "boss_not_enough_participants",
    "boss_battle_not_started",
    "boss_token_used",
    "boss_token_expired",
    "boss_hit_outside_window",
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
    session_token: str = field(default="", repr=False)
    join_remaining_sec: float = 0.0
    join_until_ms: int = 0
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
            "has_session_token": bool(self.session_token),
            "join_remaining_sec": max(0.0, float(self.join_remaining_sec or 0.0)),
            "has_join_deadline": int(self.join_until_ms or 0) > 0,
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
                note="status-only room and boss updates; challenge does not come from this endpoint",
            ),
            MiniAppFlowStep(
                key="start_refresh",
                endpoint="start",
                required_payload_keys=("token", "initData", "playerId"),
                note="repeat the joined page lifecycle until the battle challenge is returned",
            ),
            MiniAppFlowStep(
                key="begin",
                endpoint="begin",
                required_payload_keys=("token", "initData", "challengeId"),
                note="calibrate the server battle clock before starting the local timeline",
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
    elif endpoint == "begin":
        payload["challengeId"] = str(challenge_id or "").strip()
    elif endpoint == "hit":
        payload.update({
            "challengeId": str(challenge_id or "").strip(),
            "windowId": str(window_id or "").strip(),
            "elapsedMs": _int_value(elapsed_ms),
            "holdMs": _int_value(hold_ms),
        })
    elif endpoint == "finish":
        payload["bossProof"] = dict(boss_proof or {})

    request = build_miniapp_http_request(
        adapter,
        endpoint,
        payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )
    request["global_priority"] = "world_boss"
    return request


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


def _float_value(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)


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
    session_token = str(data.get("sessionToken") or data.get("session_token") or "").strip()
    if not session_token:
        session = _mapping_from_payload(data, ("session",))
        session_token = str(session.get("token") or session.get("sessionToken") or "").strip()
    player = _mapping_from_payload(data, ("player", "identity"))
    room = _mapping_from_payload(data, ("room",))
    boss = _mapping_from_payload(data, ("boss",))
    join_remaining_sec = _world_boss_join_remaining_sec(data)
    join_until_ms = 0
    for mapping in (boss, room, data):
        for key in ("joinUntilMs", "join_until_ms"):
            if mapping.get(key) not in (None, ""):
                join_until_ms = max(0, _int_value(mapping.get(key), 0))
                break
        if join_until_ms:
            break
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
        session_token=session_token,
        join_remaining_sec=join_remaining_sec,
        join_until_ms=join_until_ms,
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


def build_world_boss_action_plan(challenge, *, rng=None, hold_range_ms=None):
    rng = rng or random
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise ValueError("challengeId missing")
    windows = challenge.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("challenge windows missing")
    plan = []
    seen = set()
    for window in windows:
        if not isinstance(window, dict):
            continue
        window_id = str(window.get("windowId") or window.get("id") or "").strip()
        center_ms = _window_center_ms(window)
        if not window_id or window_id in seen or center_ms < 0:
            continue
        seen.add(window_id)
        hit_ms = max(1, _int_value(window.get("hitMs"), WORLD_BOSS_DEFAULT_HIT_MS))
        # The outer ring changes into the hit color at center-hitMs. Begin
        # charging there and release around the brightest point (centerMs).
        if hold_range_ms is not None:
            try:
                hold_low, hold_high = hold_range_ms
                hold_low = max(WORLD_BOSS_PERFECT_HOLD_MIN_MS, int(hold_low))
                hold_high = min(WORLD_BOSS_PERFECT_HOLD_MAX_MS, int(hold_high))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("invalid hold range")
            if hold_low > hold_high:
                raise ValueError("invalid hold range")
            hold_ms = int(rng.randint(hold_low, hold_high))
        else:
            # Damage continues to scale inside the accepted perfect band.
            # Use a randomized upper-middle hold rather than the ring width
            # (usually ~620ms), while keeping a margin below the hard maximum.
            hold_ms = int(rng.randint(
                WORLD_BOSS_OPTIMAL_HOLD_MIN_MS,
                WORLD_BOSS_OPTIMAL_HOLD_MAX_MS,
            ))
        perfect_ms = max(0, _int_value(window.get("perfectMs"), 150))
        # The page holds a persistent connection; the HTTP automation still
        # pays a small dispatch/transport delay before the server evaluates the
        # hit. Submit slightly before the visual center and keep release timing
        # deterministic. Hold duration remains randomized inside the accepted
        # high-damage band.
        release_lead_ms = min(WORLD_BOSS_RELEASE_LEAD_MS, max(0, perfect_ms - 30))
        elapsed_ms = max(0, center_ms - release_lead_ms)
        plan.append({
            "challengeId": challenge_id,
            "windowId": window_id,
            "elapsedMs": elapsed_ms,
            "holdMs": hold_ms,
            "centerMs": center_ms,
            "perfectMs": perfect_ms,
            "hitMs": hit_ms,
            "chargeStartMs": max(0, elapsed_ms - hold_ms),
            "stance": WORLD_BOSS_STANCE,
        })
    plan.sort(key=lambda item: (item["elapsedMs"], item["windowId"]))
    if not plan:
        raise ValueError("challenge has no usable windows")
    return plan


def filter_world_boss_action_plan(plan, current_elapsed_ms, *, grace_ms=0):
    """Keep windows with enough time left for the minimum valid charge."""

    cutoff_ms = max(0, _int_value(current_elapsed_ms)) - max(0, _int_value(grace_ms))
    return [
        dict(action)
        for action in plan or ()
        if _int_value((action or {}).get("elapsedMs"), -1) - cutoff_ms >= WORLD_BOSS_PERFECT_HOLD_MIN_MS
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


def build_world_boss_proof(
    challenge,
    actions,
    hit_payloads,
    *,
    duration_ms=None,
    player_hp=None,
    dead=None,
    client_stats=None,
    realtime_damage_applied=None,
):
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
    if not clean_actions and duration_ms is None:
        raise ValueError("boss actions missing")
    payloads = [dict(item or {}) for item in hit_payloads or ()]
    if duration_ms is None:
        duration_ms = max(action["t"] + action["holdMs"] for action in clean_actions)
        duration_ms = max(duration_ms, _int_value(challenge.get("minDurationMs"), 0))
    if player_hp is None:
        player_hp = _latest_value(payloads, ("playerHp", "hp"), challenge.get("playerHp", 0))
    if dead is None:
        dead = _latest_value(payloads, ("dead", "isDead"), challenge.get("dead", False))
    if client_stats is None:
        client_stats = _client_stats(payloads)
    if realtime_damage_applied is None:
        realtime_damage_applied = bool(payloads)
    return {
        "mode": WORLD_BOSS_PROOF_MODE,
        "challengeId": challenge_id,
        "stance": WORLD_BOSS_STANCE,
        "durationMs": max(0, _int_value(duration_ms)),
        "playerHp": max(0, _int_value(player_hp)),
        "dead": bool(dead),
        "actions": clean_actions,
        "clientStats": {key: max(0, _int_value(dict(client_stats or {}).get(key), 0)) for key in _STAT_KEYS},
        "realtimeDamageApplied": bool(realtime_damage_applied),
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


def _append_business_capture(capture_sink, *, source, step, summary):
    if capture_sink is None:
        return
    record = {
        "adapter_key": WORLD_BOSS_MINIAPP_GAME_KEY,
        "step_key": f"{step}_business",
        "endpoint": step,
        "created_at": time.time(),
        "source": sanitize_webapp_secret_text(source, limit=120),
        "business": dict(summary or {}),
    }
    if hasattr(capture_sink, "append"):
        capture_sink.append(record)
    else:
        capture_sink(record)


def _world_boss_room_state(data):
    boss = _mapping_from_payload(data, ("boss",))
    room = _mapping_from_payload(data, ("room",))
    return boss, room


def _world_boss_join_remaining_sec(data):
    boss, room = _world_boss_room_state(data)
    for mapping in (boss, room, data if isinstance(data, dict) else {}):
        for key in ("joinRemainingSeconds", "join_remaining_seconds", "remainingSeconds"):
            if mapping.get(key) not in (None, ""):
                return max(0.0, _float_value(mapping.get(key), 0.0))
    return 0.0


def _world_boss_room_status(data):
    boss, room = _world_boss_room_state(data)
    return str(
        boss.get("roomStatus")
        or room.get("status")
        or room.get("roomStatus")
        or ""
    ).strip().lower()


def _world_boss_start_refresh_delay(data, override=None):
    if override is not None:
        return max(0.1, float(override or WORLD_BOSS_START_REFRESH_SEC))
    return (
        WORLD_BOSS_START_REFRESH_SEC
        if _world_boss_join_remaining_sec(data) > 5
        else WORLD_BOSS_START_REFRESH_NEAR_LOCK_SEC
    )


def _world_boss_challenge_duration_ms(challenge):
    challenge = dict(challenge or {})
    windows = challenge.get("windows") if isinstance(challenge.get("windows"), list) else []
    base_duration = max(0, _int_value(challenge.get("durationMs"), 28_000))
    max_duration = max(1_000, _int_value(challenge.get("maxDurationMs"), base_duration + 12_000))
    last_window_end = 0
    for window in windows:
        if not isinstance(window, dict):
            continue
        last_window_end = max(
            last_window_end,
            _window_center_ms(window) + max(1, _int_value(window.get("hitMs"), WORLD_BOSS_DEFAULT_HIT_MS)),
        )
    return max(1_000, min(max_duration, max(base_duration, last_window_end + 9_000)))


def _world_boss_requires_begin(challenge):
    challenge = dict(challenge or {})
    mode = str(challenge.get("mode") or "").strip().lower()
    return bool(
        mode.startswith("qyz_focus_burst")
        or isinstance(challenge.get("attacks"), list)
        or challenge.get("expiresIn") not in (None, "")
    )


def _world_boss_last_window_end_ms(challenge):
    last_window_end = 0
    for window in dict(challenge or {}).get("windows") or ():
        if not isinstance(window, dict):
            continue
        last_window_end = max(
            last_window_end,
            _window_center_ms(window) + max(1, _int_value(window.get("hitMs"), WORLD_BOSS_DEFAULT_HIT_MS)),
        )
    return max(0, last_window_end)


def _world_boss_player_max_hp(data, challenge):
    player = _mapping_from_payload(data, ("player", "identity"))
    return max(
        0,
        _int_value(
            player.get("maxHp")
            or player.get("playerHp")
            or dict(challenge or {}).get("playerHp")
            or 100
        ),
    )


def _world_boss_realtime_damage_applied(hit_payloads):
    for payload in hit_payloads or ():
        hit = _mapping_from_payload(payload, ("hit",))
        if _float_value(hit.get("damageYi") or hit.get("damage"), 0.0) > 0:
            return True
    return False


def _present_int(mapping, *keys):
    mapping = mapping if isinstance(mapping, dict) else {}
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return _int_value(mapping.get(key), 0)
    return None


def _present_float(mapping, *keys):
    mapping = mapping if isinstance(mapping, dict) else {}
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return _float_value(mapping.get(key), 0.0)
    return None


def _world_boss_start_business_summary(data, challenge=None):
    boss = _mapping_from_payload(data, ("boss",))
    player = _mapping_from_payload(data, ("player", "identity"))
    challenge = dict(challenge or _challenge_from_payload(data) or {})
    windows = [item for item in (challenge.get("windows") or ()) if isinstance(item, dict)]
    return {
        "action_limit": _present_int(boss, "actionLimit", "action_limit"),
        "actions_remaining": _present_int(boss, "actionsRemaining", "actions_remaining"),
        "actions_used": _present_int(boss, "actionsUsed", "actions_used"),
        "phase": _present_int(boss, "phase"),
        "boss_hp": _present_int(boss, "hp"),
        "boss_max_hp": _present_int(boss, "maxHp", "max_hp"),
        "player_max_hp": _present_int(player, "maxHp", "max_hp"),
        "player_attack_bonus": _present_float(player, "attackBonus", "attack_bonus"),
        "player_root": str(player.get("root") or player.get("spiritualRoot") or "")[:80],
        "window_count": len(windows),
        "windows": [
            {
                "center_ms": _window_center_ms(window),
                "hit_ms": max(0, _int_value(window.get("hitMs"), WORLD_BOSS_DEFAULT_HIT_MS)),
                "perfect_ms": max(0, _int_value(window.get("perfectMs"), 150)),
            }
            for window in windows
        ],
    }


def _world_boss_hit_business_summary(data):
    hit = _mapping_from_payload(data, ("hit",))
    boss = _mapping_from_payload(data, ("boss",))
    stats = _mapping_from_payload(data, ("clientStats", "stats"))
    attempt_consumed = hit.get("attemptConsumed")
    return {
        "attempt_consumed": bool(attempt_consumed) if attempt_consumed is not None else None,
        "hit_evidence_present": bool(hit) or _present_int(stats, "hits") not in (None, 0),
        "perfect": bool(hit.get("perfect")) if hit.get("perfect") is not None else None,
        "damage_yi": _present_float(hit, "damageYi", "damage"),
        "delta_ms": _present_float(hit, "deltaMs", "delta_ms"),
        "hold_ms": _present_float(hit, "holdMs", "hold_ms"),
        "boss_hp": _present_int(hit, "bossHp", "boss_hp"),
        "action_limit": _present_int(boss, "actionLimit", "action_limit"),
        "actions_remaining": _present_int(boss, "actionsRemaining", "actions_remaining"),
        "actions_used": _present_int(boss, "actionsUsed", "actions_used"),
    }


def _world_boss_hit_was_consumed(summary):
    summary = dict(summary or {})
    if summary.get("attempt_consumed") is not None:
        return bool(summary.get("attempt_consumed"))
    return bool(
        _float_value(summary.get("damage_yi"), 0.0) > 0
        or summary.get("perfect") is True
        or summary.get("hit_evidence_present") is True
    )


def _world_boss_finish_business_summary(data, *, hit_summary=None):
    result = _mapping_from_payload(data, ("result",))
    hit_summary = dict(hit_summary or {})
    summary = {
        "action": str(result.get("action") or "")[:40],
        "grade": str(result.get("grade") or "")[:40],
        "score": _present_int(result, "score"),
        "hits": _present_int(result, "hits"),
        "perfects": _present_int(result, "perfects"),
        "realtime_hit_count": _present_int(result, "realtime_hit_count", "realtimeHitCount"),
        "realtime_damage_yi": _present_float(result, "realtime_damage_yi", "realtimeDamageYi"),
        "realtime_damage_applied": (
            bool(result.get("realtime_damage_applied"))
            if result.get("realtime_damage_applied") is not None
            else (
                bool(result.get("realtimeDamageApplied"))
                if result.get("realtimeDamageApplied") is not None
                else None
            )
        ),
        "dead": bool(result.get("dead")) if result.get("dead") is not None else None,
        "player_hp": _present_int(result, "player_hp", "playerHp"),
        "quality_multiplier": _present_float(result, "quality_multiplier", "qualityMultiplier"),
        "sample_count": _present_int(result, "sample_count", "sampleCount"),
    }
    summary.update({
        "attempted_hit_count": max(0, _int_value(hit_summary.get("attempted_hit_count"), 0)),
        "accepted_hit_count": max(0, _int_value(hit_summary.get("accepted_hit_count"), 0)),
        "accepted_perfect_count": max(0, _int_value(hit_summary.get("accepted_perfect_count"), 0)),
        "accepted_damage_yi": max(0.0, _float_value(hit_summary.get("accepted_damage_yi"), 0.0)),
        "action_limit": hit_summary.get("action_limit"),
        "actions_remaining": hit_summary.get("actions_remaining"),
        "actions_used": hit_summary.get("actions_used"),
    })
    return summary


def _world_boss_has_effective_contribution(summary):
    summary = dict(summary or {})
    authoritative_keys = ("score", "hits", "realtime_hit_count", "realtime_damage_yi")
    if any(summary.get(key) is not None for key in authoritative_keys):
        return any(_float_value(summary.get(key), 0.0) > 0 for key in authoritative_keys)
    return any(
        _float_value(summary.get(key), 0.0) > 0
        for key in (
            "accepted_hit_count",
            "accepted_damage_yi",
        )
    )


def _world_boss_counter_damage(challenge):
    phase = max(1, _int_value(dict(challenge or {}).get("phase"), 1))
    return {1: 16, 2: 22, 3: 30}.get(phase, 22)


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
    battle_wait_timeout_sec=65.0,
    state_poll_interval_sec=None,
    entry_token="",
):
    """Wait for room lock, refresh the joined session, then execute one battle."""

    adapter = adapter or build_world_boss_miniapp_adapter()
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    events = events if events is not None else []
    if not isinstance(receipt, WorldBossJoinReceipt) or not receipt.joined:
        status = getattr(receipt, "status", "join_not_confirmed")
        error = getattr(receipt, "error", "join not confirmed")
        return _flow_result(False, status, error=error, events=events)

    entry_token = str(entry_token or token or "").strip()
    current_token = str(token or entry_token).strip()
    state_data = {}
    challenge = {}
    previous_room_status = ""
    join_until_ms = max(0, _int_value(getattr(receipt, "join_until_ms", 0), 0))
    if join_until_ms:
        initial_join_remaining = max(0.0, (join_until_ms / 1000.0) - time.time())
    else:
        initial_join_remaining = max(0.0, float(getattr(receipt, "join_remaining_sec", 0.0) or 0.0))
    if initial_join_remaining > WORLD_BOSS_JOIN_READY_LEAD_SEC:
        sleeper(initial_join_remaining - WORLD_BOSS_JOIN_READY_LEAD_SEC)
    wait_deadline = float(clock()) + max(0.0, float(battle_wait_timeout_sec or 0))
    consecutive_429 = 0
    while True:
        state_request = build_world_boss_miniapp_request(
            "start",
            token=current_token,
            init_data=init_data,
            player_id=receipt.player_id or receipt.identity_id,
            adapter=adapter,
        )
        state_result = execute_miniapp_http_request(
            state_request,
            transport,
            backoff_sec=(),
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="battle_start_refresh",
        )
        _append_http_event(events, "battle_start_refresh", state_result)
        if not state_result.ok:
            status = classify_world_boss_miniapp_error(state_result.error)
            retryable_wait_error = status == "boss_battle_not_started" or state_result.error_type == "transient"
            if int(state_result.status_code or 0) == 429:
                consecutive_429 += 1
                if consecutive_429 > WORLD_BOSS_START_MAX_CONSECUTIVE_429:
                    return _flow_result(
                        False,
                        "rate_limited",
                        error="world boss start polling rate limited",
                        data=state_result.data,
                        events=events,
                    )
            resettable_token_error = status in {
                "boss_token_used", "boss_token_expired", "boss_token_missing", "boss_event_closed",
            }
            if resettable_token_error and entry_token and current_token != entry_token:
                current_token = entry_token
                events.append({"step": "reset_entry_token", "ok": True, "status": status})
                retryable_wait_error = True
            if (retryable_wait_error or resettable_token_error) and float(clock()) < wait_deadline:
                reconnect_delay = (
                    WORLD_BOSS_START_RATE_LIMIT_BACKOFF_SEC * consecutive_429
                    if int(state_result.status_code or 0) == 429
                    else WORLD_BOSS_START_RECONNECT_SEC
                )
                sleeper(reconnect_delay)
                continue
            return _flow_result(False, status, error=state_result.error, data=state_result.data, events=events)
        consecutive_429 = 0
        state_data = state_result.data if isinstance(state_result.data, dict) else {}
        refreshed_session_token = str(
            state_data.get("sessionToken") or state_data.get("session_token") or ""
        ).strip()
        if refreshed_session_token:
            current_token = refreshed_session_token
        if _verification_required(state_data):
            return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
        payload_error = _error_from_payload(state_data)
        if payload_error and payload_error != "boss_battle_not_started":
            return _flow_result(False, payload_error, error=payload_error, data=state_data, events=events)
        challenge = _challenge_from_payload(state_data) or dict(receipt.challenge or {})
        if challenge:
            start_business = _world_boss_start_business_summary(state_data, challenge)
            _append_business_capture(
                capture_sink,
                source=capture_source,
                step="battle_start",
                summary=start_business,
            )
            if _int_value(start_business.get("actions_used"), 0) > 0:
                return _flow_result(
                    False,
                    "already_participated",
                    error="world boss identity already used actions",
                    data={"result": start_business},
                    events=events,
                )
            if start_business.get("actions_remaining") == 0:
                return _flow_result(
                    False,
                    "action_limit_reached",
                    error="world boss identity has no remaining actions",
                    data={"result": start_business},
                    events=events,
                )
            break
        if float(clock()) >= wait_deadline:
            return _flow_result(False, "battle_wait_timeout", error="world boss battle not started", events=events)
        previous_room_status = _world_boss_room_status(state_data) or previous_room_status
        sleeper(_world_boss_start_refresh_delay(state_data, state_poll_interval_sec))

    if not challenge:
        return _flow_result(False, "not_ready", error="world boss challenge missing", events=events)
    single_battle_protocol = _world_boss_requires_begin(challenge)
    # The page starts a fresh local performance.now() timeline after challenge
    # acquisition. Server room elapsed values are display state, not proof time.
    server_elapsed_ms = 0
    for payload in (state_data, challenge):
        if not isinstance(payload, dict):
            continue
        for key in ("elapsedMs", "currentElapsedMs", "battleElapsedMs"):
            if payload.get(key) not in (None, ""):
                server_elapsed_ms = max(0, _int_value(payload.get(key)))
                break
        if server_elapsed_ms:
            break
    initial_elapsed_ms = 0
    current_room_status = _world_boss_room_status(state_data)
    auto_start_delay = (
        WORLD_BOSS_SPAWN_AUTO_START_DELAY_SEC
        if previous_room_status in {"joining", "waiting"} and current_room_status == "battle"
        else WORLD_BOSS_AUTO_START_DELAY_SEC
    )
    sleeper(auto_start_delay)
    if single_battle_protocol:
        begin_started_at = float(clock())
        begin_request = build_world_boss_miniapp_request(
            "begin",
            token=current_token,
            init_data=init_data,
            challenge_id=str(challenge.get("challengeId") or ""),
            adapter=adapter,
        )
        begin_result = execute_miniapp_http_request(
            begin_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="begin",
        )
        _append_http_event(events, "begin", begin_result)
        if not begin_result.ok:
            status = classify_world_boss_miniapp_error(begin_result.error)
            return _flow_result(False, status, error=begin_result.error, data=begin_result.data, events=events)
        begin_data = begin_result.data if isinstance(begin_result.data, dict) else {}
        starts_in_ms = max(0.0, _float_value(begin_data.get("startsInMs"), 0.0))
        round_trip_ms = max(0.0, (float(clock()) - begin_started_at) * 1000.0)
        wait_ms = max(0.0, starts_in_ms - round_trip_ms / 2.0)
        events.append({
            "step": "begin_sync",
            "ok": True,
            "starts_in_ms": starts_in_ms,
            "round_trip_ms": round_trip_ms,
            "wait_ms": wait_ms,
        })
        if wait_ms > 0:
            sleeper(wait_ms / 1000.0)
    try:
        full_plan = build_world_boss_action_plan(challenge, rng=rng)
    except ValueError as exc:
        return _flow_result(False, "not_ready", error=exc, events=events)
    plan = filter_world_boss_action_plan(full_plan, initial_elapsed_ms)
    events.append({
        "step": "plan",
        "ok": True,
        "window_count": len(plan),
        "expired_window_count": len(full_plan) - len(plan),
        "current_elapsed_ms": initial_elapsed_ms,
        "server_elapsed_ms_ignored": server_elapsed_ms,
    })

    timeline_origin = float(clock()) - (float(initial_elapsed_ms) / 1000.0)
    hit_payloads = []
    executed_actions = []
    processed_window_ids = set()
    player_hp = _world_boss_player_max_hp(state_data, challenge)
    dead = False
    death_elapsed_ms = 0
    client_stats = {key: 0 for key in _STAT_KEYS}
    server_hit_summary = {
        "attempted_hit_count": 0,
        "accepted_hit_count": 0,
        "accepted_perfect_count": 0,
        "accepted_damage_yi": 0.0,
        "action_limit": None,
        "actions_remaining": None,
        "actions_used": None,
    }

    def process_missed_windows(current_elapsed_ms):
        nonlocal player_hp, dead, death_elapsed_ms
        if dead:
            return
        for missed_action in full_plan:
            window_id = missed_action["windowId"]
            if window_id in processed_window_ids:
                continue
            miss_at_ms = (
                _int_value(missed_action.get("centerMs"))
                + _int_value(missed_action.get("hitMs"), WORLD_BOSS_DEFAULT_HIT_MS)
                + 80
            )
            if current_elapsed_ms <= miss_at_ms:
                continue
            processed_window_ids.add(window_id)
            client_stats["combo"] = 0
            player_hp = max(0, player_hp - _world_boss_counter_damage(challenge))
            events.append({
                "step": "miss_window",
                "ok": True,
                "windowId": window_id,
                "miss_at_ms": miss_at_ms,
                "player_hp": player_hp,
            })
            if player_hp <= 0:
                dead = True
                death_elapsed_ms = miss_at_ms
                events.append({
                    "step": "player_dead",
                    "ok": True,
                    "elapsed_ms": death_elapsed_ms,
                })
                break

    process_missed_windows(initial_elapsed_ms)
    for action in plan:
        if dead:
            break
        current_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
        process_missed_windows(current_elapsed_ms)
        if dead:
            break
        available_charge_ms = int(action["elapsedMs"]) - current_elapsed_ms
        if available_charge_ms < WORLD_BOSS_PERFECT_HOLD_MIN_MS:
            events.append({
                "step": "skip_expired_window",
                "ok": True,
                "windowId": action["windowId"],
                "current_elapsed_ms": current_elapsed_ms,
            })
            continue
        hold_ms = min(int(action["holdMs"]), available_charge_ms, WORLD_BOSS_PERFECT_HOLD_MAX_MS)
        charge_start_ms = int(action["elapsedMs"]) - hold_ms
        wait_before_charge_ms = max(0, charge_start_ms - current_elapsed_ms)
        if wait_before_charge_ms:
            sleeper(wait_before_charge_ms / 1000.0)
            process_missed_windows(max(0, int((float(clock()) - timeline_origin) * 1000)))
            if dead:
                break
        if hold_ms:
            sleeper(hold_ms / 1000.0)
        release_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
        process_missed_windows(release_elapsed_ms)
        if dead or action["windowId"] in processed_window_ids:
            continue
        executed_action = {
            **action,
            "elapsedMs": release_elapsed_ms,
            "holdMs": hold_ms,
            "chargeStartMs": charge_start_ms,
        }
        events.append({
            "step": "release_window",
            "ok": True,
            "windowId": action["windowId"],
            "wait_before_charge_ms": wait_before_charge_ms,
            "hold_ms": hold_ms,
            "release_elapsed_ms": release_elapsed_ms,
        })
        hit_request = build_world_boss_miniapp_request(
            "hit",
            token=current_token,
            init_data=init_data,
            challenge_id=action["challengeId"],
            window_id=action["windowId"],
            elapsed_ms=executed_action["elapsedMs"],
            hold_ms=executed_action["holdMs"],
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
            if status == "boss_action_limit" and server_hit_summary["attempted_hit_count"] > 0:
                events.append({
                    "step": "action_limit_after_hits",
                    "ok": True,
                    "attempted_hit_count": server_hit_summary["attempted_hit_count"],
                    "accepted_hit_count": server_hit_summary["accepted_hit_count"],
                })
                break
            if status == "boss_hit_outside_window":
                # A single late release is a business miss, not a transport
                # failure. The server has already rejected this mutation, so
                # never retry it; record the page-equivalent miss and keep
                # the rest of the timeline eligible for one final finish.
                processed_window_ids.add(action["windowId"])
                client_stats["combo"] = 0
                player_hp = max(0, player_hp - _world_boss_counter_damage(challenge))
                events.append({
                    "step": "server_rejected_window",
                    "ok": True,
                    "windowId": action["windowId"],
                    "error": status,
                    "player_hp": player_hp,
                })
                if player_hp <= 0:
                    dead = True
                    death_elapsed_ms = release_elapsed_ms
                    events.append({
                        "step": "player_dead",
                        "ok": True,
                        "elapsed_ms": death_elapsed_ms,
                    })
                    break
                continue
            return _flow_result(False, status, error=hit_result.error, data=hit_result.data, events=events)
        hit_data = hit_result.data if isinstance(hit_result.data, dict) else {}
        if _verification_required(hit_data):
            return _flow_result(False, "verification_required", error="xianxia-verify required", events=events)
        hit_business = _world_boss_hit_business_summary(hit_data)
        server_hit_summary["attempted_hit_count"] += 1
        for key in ("action_limit", "actions_remaining", "actions_used"):
            if hit_business.get(key) is not None:
                server_hit_summary[key] = hit_business.get(key)
        consumed = _world_boss_hit_was_consumed(hit_business)
        if consumed:
            server_hit_summary["accepted_hit_count"] += 1
            if hit_business.get("perfect") is True:
                server_hit_summary["accepted_perfect_count"] += 1
            server_hit_summary["accepted_damage_yi"] += max(
                0.0,
                _float_value(hit_business.get("damage_yi"), 0.0),
            )
        _append_business_capture(
            capture_sink,
            source=capture_source,
            step="hit",
            summary={
                **hit_business,
                "window_index": server_hit_summary["attempted_hit_count"],
                "consumed": consumed,
            },
        )
        processed_window_ids.add(action["windowId"])
        # Keep bossProof clientStats identical to the official page: these are
        # local window-match stats, while realtime hit acceptance is accounted
        # separately from the server response above. The page never copies
        # damageYi into clientStats.damage.
        client_stats["dodges"] += 1
        client_stats["hits"] += 1
        client_stats["combo"] += 1
        client_stats["bestCombo"] = max(client_stats["bestCombo"], client_stats["combo"])
        local_perfect = (
            abs(_int_value(executed_action.get("elapsedMs")) - _int_value(executed_action.get("centerMs")))
            <= _int_value(executed_action.get("perfectMs"), 150)
            and WORLD_BOSS_PERFECT_HOLD_MIN_MS
            <= _int_value(executed_action.get("holdMs"))
            <= WORLD_BOSS_PERFECT_HOLD_MAX_MS
        )
        if local_perfect:
            client_stats["perfects"] += 1
        executed_actions.append(executed_action)
        hit_payloads.append(hit_data)
        if hit_business.get("actions_remaining") == 0 and not single_battle_protocol:
            events.append({
                "step": "action_limit_reached",
                "ok": True,
                "attempted_hit_count": server_hit_summary["attempted_hit_count"],
                "accepted_hit_count": server_hit_summary["accepted_hit_count"],
                "action_limit": hit_business.get("action_limit"),
            })
            break

    challenge_duration_ms = _world_boss_challenge_duration_ms(challenge)
    if dead:
        finish_target_ms = min(challenge_duration_ms, death_elapsed_ms + 1250)
    else:
        finish_target_ms = min(
            challenge_duration_ms,
            _world_boss_last_window_end_ms(challenge) + WORLD_BOSS_FINISH_GRACE_MS,
        )
    current_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
    if finish_target_ms > current_elapsed_ms:
        sleeper((finish_target_ms - current_elapsed_ms) / 1000.0)
    finish_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
    process_missed_windows(finish_elapsed_ms)
    if dead and death_elapsed_ms + 1250 > finish_elapsed_ms:
        sleeper((death_elapsed_ms + 1250 - finish_elapsed_ms) / 1000.0)
        finish_elapsed_ms = max(0, int((float(clock()) - timeline_origin) * 1000))
    proof = build_world_boss_proof(
        challenge,
        executed_actions,
        hit_payloads,
        duration_ms=finish_elapsed_ms,
        player_hp=player_hp,
        dead=dead,
        client_stats=client_stats,
        realtime_damage_applied=_world_boss_realtime_damage_applied(hit_payloads),
    )
    finish_request = build_world_boss_miniapp_request(
        "finish", token=current_token, init_data=init_data, boss_proof=proof, adapter=adapter,
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
    finish_business = _world_boss_finish_business_summary(finish_data, hit_summary=server_hit_summary)
    _append_business_capture(
        capture_sink,
        source=capture_source,
        step="finish",
        summary=finish_business,
    )
    effective = _world_boss_has_effective_contribution(finish_business)
    return _flow_result(
        effective,
        "settled" if effective else "settled_zero_contribution",
        error="" if effective else "world boss settled without effective contribution",
        data={"result": finish_business},
        events=events,
        proof=proof,
    )


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
        token=receipt.session_token or token,
        entry_token=token,
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
            token=receipt.session_token or entry.get("token"),
            entry_token=entry.get("token"),
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
