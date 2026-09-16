import math
import random
import time
from copy import deepcopy

from telethon import functions

from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..tree_score_policy import (
    TREE_MINIAPP_DEFAULT_TARGET_SCORE,
    TREE_MINIAPP_MAX_TARGET_SCORE,
    TREE_MINIAPP_MIN_TARGET_SCORE,
    TREE_MINIAPP_MODES,
    normalize_tree_score_records,
    normalize_tree_score_profile,
)
from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    MiniAppRequestAborted,
    MiniAppRequestBudget,
    _response_retry_after_sec,
    build_miniapp_http_request,
    build_miniapp_launch_request,
    build_request_webview_args,
    execute_miniapp_http_request,
    extract_miniapp_init_data_from_url,
    iter_webapp_entry_links,
    require_miniapp_operation,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)
from .miniapp_common import (
    append_http_event as _append_http_event,
    build_miniapp_transport,
    build_pooled_miniapp_transport,
    run_miniapp_blocking_flow,
)
from .tree_receipts import (
    parse_tree_allocation,
    parse_tree_rewards,
    parse_tree_submit,
    tree_integer as _quota_integer,
    tree_panel_context,
    tree_round_key,
)
from . import tree_operations


TREE_MINIAPP_GAME_KEY = "tree"
TREE_MINIAPP_LABEL = "灵眼之树"
TREE_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
TREE_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
TREE_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS = (r"hantianzun\d+_bot",)
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
TREE_MINIAPP_FLY_GRAVITY = 560.0
TREE_MINIAPP_FLY_IMPULSE = -255.0
TREE_MINIAPP_FLY_BASE_SPEED = 112.0
TREE_MINIAPP_FLY_SCORE_SPEED = 3.0
TREE_MINIAPP_FLY_SPEED_CAP = 70.0
TREE_MINIAPP_FLY_PLAYER_X = 86.0
TREE_MINIAPP_FLY_PLAYER_RADIUS = 15.0
TREE_MINIAPP_FLY_TOP_Y = 26.0
TREE_MINIAPP_FLY_BOTTOM_Y = 334.0
TREE_MINIAPP_FLY_GATE_GAP = 112.0
TREE_MINIAPP_FLY_GATE_WIDTH = 54.0
TREE_MINIAPP_FLY_GATE_SPACING = 174.0
# A consumed production proof replayed at score 17 with 1000/60ms, while the
# server verified 8. The same proof replays at the server score with 16ms.
TREE_MINIAPP_FLY_FRAME_MS = 16.0
TREE_MINIAPP_FLY_DEFAULT_BEAM_WIDTH = 420
TREE_MINIAPP_FLY_MAX_BEAM_WIDTH = 640
TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS = 120000
TREE_MINIAPP_FLY_MAX_PLAN_FRAMES = 7600
TREE_MINIAPP_FLY_MIN_VERIFIED_SCORE_RATIO = 0.8
TREE_MINIAPP_JUMP_MIN_VERIFIED_SCORE_RATIO = 0.8
TREE_MINIAPP_JUMP_CENTER_SCORE_CAP = 6
TREE_MINIAPP_JUMP_START = {"x": 116.0, "y": 246.0, "r": 34.0}
TREE_MINIAPP_FLY_HIT_POLYGON = (
    (-24.0, 2.0), (-18.0, -8.0), (-7.0, -12.0), (8.0, -12.0), (20.0, -8.0),
    (25.0, -2.0), (22.0, 7.0), (10.0, 11.0), (-6.0, 10.0), (-19.0, 7.0),
)
TREE_MINIAPP_STOP_ERROR_KEYWORDS = (
    "daily_limit",
    "no_remaining",
    "limit_reached",
    "次数已尽",
    "剩余 0",
    "season_closed",
    "reward_claimed",
)
# Turnstile is a server-side human-verification gate.  Treat it as a
# manual-intervention state so the daily flow does not retry or spend another
# jump/fly attempt after the gate is presented.
TREE_MINIAPP_VERIFICATION_ERROR_MARKERS = (
    "turnstile",
    "cloudflare challenge",
    "cloudflare_challenge",
    "cf_challenge",
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
        allowed_bot_username_patterns=TREE_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS,
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




def summarize_tree_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = TREE_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def extract_tree_miniapp_launch(event, *, message_text=""):
    adapter = build_tree_miniapp_adapter()
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
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


async def request_tree_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None, operation_check=None):
    require_miniapp_operation(operation_check)
    adapter = adapter or build_tree_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "tree miniapp launch not allowed")
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
    require_miniapp_operation(operation_check)
    init_data = extract_miniapp_init_data_from_url(getattr(result, "url", "") or "")
    if not init_data:
        raise RuntimeError("WebView URL 缺少 tgWebAppData")
    return init_data


_requests_transport = build_miniapp_transport(timeout=TREE_MINIAPP_HTTP_TIMEOUT)


def build_tree_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=TREE_MINIAPP_GAME_KEY,
        label=TREE_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="公共洞府入口生产自动化；单身份串行、结果未知不补发",
        replaces_commands=(".灵树",),
        state_outputs=("module_snapshot", "daily_counter", "score_policy"),
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
                optional_payload_keys=("targetScore", "targetScoreRange"),
                note="服务端开局，返回 runToken/seed；候选接口，未接生产",
            ),
            MiniAppFlowStep(
                key="run_submit",
                endpoint="run_submit",
                required_payload_keys=("token", "initData", "mode", "runToken", "proof"),
                optional_payload_keys=("targetScore", "targetScoreRange"),
                note="提交 jump/fly proof；目标分必须可调且默认低分，需主控复核后才可上线",
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


def _tree_fly_verification_mismatch(client_score, server_score, verification):
    """Stop spending fly quota when server replay diverges materially."""

    return _tree_verification_mismatch("fly", client_score, server_score, verification)


def _tree_verification_mismatch(mode, client_score, server_score, verification):
    """Stop spending one mode's quota when server replay diverges materially."""

    client_score = _int_value(client_score, 0)
    server_score = _int_value(server_score, 0)
    if client_score <= 0 or server_score <= 0:
        return False
    if not isinstance(verification, dict) or not verification:
        return False
    ratio = {
        "jump": TREE_MINIAPP_JUMP_MIN_VERIFIED_SCORE_RATIO,
        "fly": TREE_MINIAPP_FLY_MIN_VERIFIED_SCORE_RATIO,
    }.get(str(mode or "").strip().lower())
    if ratio is None:
        return False
    return server_score < client_score * ratio


def _float_value(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value, lower, upper):
    return max(float(lower), min(float(upper), float(value)))


def _int_between(value, default, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = int(default)
    return max(int(lower), min(int(upper), parsed))


def tree_miniapp_seed_hash(seed, index):
    """Match the WebView's FNV-style deterministic course generator."""

    text = f"{seed or 'luoyun'}:{int(index or 0)}"
    value = 2166136261
    for char in text:
        value ^= ord(char)
        value = (value * 16777619) & 0xFFFFFFFF
    return value / 4294967295.0


def _target_score(mode, rng, profile=None):
    profile = normalize_tree_score_profile(mode, profile)
    floor = int(TREE_MINIAPP_MIN_TARGET_SCORE.get(mode, 20))
    cap = int(TREE_MINIAPP_MAX_TARGET_SCORE.get(mode, 45))
    raw_range = profile.get("target_score_range") or TREE_MINIAPP_DEFAULT_TARGET_SCORE.get(mode, (3, 7))
    try:
        low, high = raw_range
    except (TypeError, ValueError):
        low, high = TREE_MINIAPP_DEFAULT_TARGET_SCORE.get(mode, (3, 7))
    low = max(floor, min(cap, int(low or floor)))
    high = max(low, min(cap, int(high or low)))
    return int(rng.randint(low, high))


def make_tree_fly_gate(seed, index, x):
    index = int(index or 0)
    return {
        "x": float(x),
        "gapY": 102.0 + tree_miniapp_seed_hash(seed, index * 19 + 5) * 152.0,
        "gap": TREE_MINIAPP_FLY_GATE_GAP,
        "width": TREE_MINIAPP_FLY_GATE_WIDTH,
        "passed": False,
        "index": index,
    }


def _initial_tree_fly_gates(seed):
    return [
        make_tree_fly_gate(seed, 1, 314.0),
        make_tree_fly_gate(seed, 2, 488.0),
        make_tree_fly_gate(seed, 3, 662.0),
    ]


def _tree_fly_polygon(y, vy):
    angle = _clamp(float(vy) / 520.0, -0.38, 0.48)
    cos_value = math.cos(angle)
    sin_value = math.sin(angle)
    return tuple(
        (
            TREE_MINIAPP_FLY_PLAYER_X + x * cos_value - point_y * sin_value,
            float(y) + x * sin_value + point_y * cos_value,
        )
        for x, point_y in TREE_MINIAPP_FLY_HIT_POLYGON
    )


def _tree_fly_hit_test(y, vy, gates):
    polygon = _tree_fly_polygon(y, vy)
    min_x = min(point_x for point_x, _point_y in polygon)
    max_x = max(point_x for point_x, _point_y in polygon)
    min_y = min(point_y for _point_x, point_y in polygon)
    max_y = max(point_y for _point_x, point_y in polygon)
    if min_y <= 12.0 or max_y >= 348.0:
        return True
    for gate in gates:
        if max_x < gate["x"] or min_x > gate["x"] + gate["width"]:
            continue
        gap_top = gate["gapY"] - gate["gap"] / 2.0
        gap_bottom = gate["gapY"] + gate["gap"] / 2.0
        if min_y < gap_top or max_y > gap_bottom:
            return True
    return False


def simulate_tree_fly_run(seed, flaps, *, max_duration_ms=30000, frame_ms=TREE_MINIAPP_FLY_FRAME_MS):
    """Replay the WebView fly physics from a list of flap timestamps."""

    flaps = sorted(max(0, int(round(item))) for item in (flaps or ()))
    frame_ms = max(8.0, float(frame_ms or TREE_MINIAPP_FLY_FRAME_MS))
    max_duration_ms = max(frame_ms, float(max_duration_ms or 30000))
    gates = _initial_tree_fly_gates(seed)
    player = {"x": TREE_MINIAPP_FLY_PLAYER_X, "y": 178.0, "vy": 0.0}
    score = 0
    flap_index = 0
    now_ms = 0.0
    game_over = False

    while now_ms <= max_duration_ms and not game_over:
        while flap_index < len(flaps) and flaps[flap_index] <= now_ms:
            player["vy"] = TREE_MINIAPP_FLY_IMPULSE
            flap_index += 1
        dt = frame_ms / 1000.0
        player["vy"] += TREE_MINIAPP_FLY_GRAVITY * dt
        player["y"] += player["vy"] * dt
        speed = TREE_MINIAPP_FLY_BASE_SPEED + min(TREE_MINIAPP_FLY_SPEED_CAP, score * TREE_MINIAPP_FLY_SCORE_SPEED)
        for gate in gates:
            gate["x"] -= speed * dt
            if not gate.get("passed") and gate["x"] + gate["width"] < player["x"] - 10:
                gate["passed"] = True
                score += 1
        last = gates[-1] if gates else None
        while gates and gates[0]["x"] < -80:
            gates.pop(0)
        while len(gates) < 3:
            tail = gates[-1] if gates else last or {"x": 300.0, "index": 0}
            gates.append(make_tree_fly_gate(seed, int(tail.get("index") or 0) + 1, float(tail.get("x") or 300.0) + TREE_MINIAPP_FLY_GATE_SPACING))

        game_over = _tree_fly_hit_test(player["y"], player["vy"], gates)
        now_ms += frame_ms

    return {
        "score": int(score),
        "durationMs": int(round(min(now_ms, max_duration_ms))),
        "gameOver": bool(game_over),
        "flapCount": len(flaps),
        "finalY": round(float(player["y"]), 3),
        "finalVy": round(float(player["vy"]), 3),
    }


def _step_tree_fly_state(seed, state_item, *, flap=False, frame_ms=TREE_MINIAPP_FLY_FRAME_MS):
    now_ms, y, vy, score, gates, flaps, last_flap_ms = state_item
    if flap:
        vy = TREE_MINIAPP_FLY_IMPULSE
        # A flap belongs to the current simulation frame. Rounding up delays it
        # until the following replay frame (for example 1266.67 -> 1267ms).
        flaps = tuple(list(flaps) + [int(math.floor(now_ms))])
        last_flap_ms = float(now_ms)
    frame_ms = max(8.0, float(frame_ms or TREE_MINIAPP_FLY_FRAME_MS))
    dt = frame_ms / 1000.0
    vy += TREE_MINIAPP_FLY_GRAVITY * dt
    y += vy * dt
    speed = TREE_MINIAPP_FLY_BASE_SPEED + min(TREE_MINIAPP_FLY_SPEED_CAP, score * TREE_MINIAPP_FLY_SCORE_SPEED)
    gates = [dict(gate) for gate in gates]
    for gate in gates:
        gate["x"] -= speed * dt
        if not gate.get("passed") and gate["x"] + gate["width"] < TREE_MINIAPP_FLY_PLAYER_X - 10:
            gate["passed"] = True
            score += 1
    last_gate = gates[-1] if gates else None
    while gates and gates[0]["x"] < -80:
        gates.pop(0)
    while len(gates) < 3:
        tail = gates[-1] if gates else last_gate or {"x": 300.0, "index": 0}
        gates.append(make_tree_fly_gate(seed, int(tail.get("index") or 0) + 1, float(tail.get("x") or 300.0) + TREE_MINIAPP_FLY_GATE_SPACING))

    if _tree_fly_hit_test(y, vy, gates):
        return None
    return (float(now_ms) + frame_ms, float(y), float(vy), int(score), gates, flaps, float(last_flap_ms))


def _tree_fly_state_quality(state_item):
    _now_ms, y, vy, score, gates, flaps, last_flap_ms = state_item
    candidates = [
        gate for gate in gates
        if gate["x"] + gate["width"] >= TREE_MINIAPP_FLY_PLAYER_X - 10
    ]
    gate = candidates[0] if candidates else gates[0]
    speed = TREE_MINIAPP_FLY_BASE_SPEED + min(TREE_MINIAPP_FLY_SPEED_CAP, score * TREE_MINIAPP_FLY_SCORE_SPEED)
    time_to_gate = max(0.0, gate["x"] + gate["width"] - (TREE_MINIAPP_FLY_PLAYER_X - 10)) / speed
    predicted_y = y + vy * time_to_gate + 280.0 * time_to_gate * time_to_gate
    distance = abs(predicted_y - gate["gapY"])
    return (
        int(score) * 10000.0
        - distance * 15.0
        - abs(y - gate["gapY"])
        - len(flaps) * 0.2
        - max(0.0, 35.0 - y) * 80.0
        - max(0.0, y - 315.0) * 80.0
        - float(last_flap_ms) * 0.00001
    )


def plan_tree_fly_flaps(seed, *, target_score, rng=None, profile=None):
    rng = rng or random
    profile = dict(profile or {})
    target_score = max(1, int(target_score or 1))
    beam_width = _int_between(
        profile.get("beam_width"),
        TREE_MINIAPP_FLY_DEFAULT_BEAM_WIDTH,
        80,
        TREE_MINIAPP_FLY_MAX_BEAM_WIDTH,
    )
    frame_ms = max(8.0, float(profile.get("frame_ms") or TREE_MINIAPP_FLY_FRAME_MS))
    requested_duration_ms = max(15000, int(profile.get("max_duration_ms") or max(45000, target_score * 1400)))
    max_duration_ms = min(requested_duration_ms, TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS)
    max_plan_frames = _int_between(
        profile.get("max_plan_frames"),
        TREE_MINIAPP_FLY_MAX_PLAN_FRAMES,
        1,
        TREE_MINIAPP_FLY_MAX_PLAN_FRAMES,
    )
    min_interval_ms = max(120.0, float(profile.get("min_interval_ms") or 160.0))
    initial = (
        0.0,
        178.0,
        TREE_MINIAPP_FLY_IMPULSE,
        0,
        _initial_tree_fly_gates(seed),
        (0,),
        0.0,
    )
    beam = [initial]
    best = initial
    for _frame in range(min(int(max_duration_ms / frame_ms), max_plan_frames)):
        candidates = []
        for state_item in beam:
            if int(state_item[3]) > int(best[3]) or (int(state_item[3]) == int(best[3]) and _tree_fly_state_quality(state_item) > _tree_fly_state_quality(best)):
                best = state_item
            steady = _step_tree_fly_state(seed, state_item, flap=False, frame_ms=frame_ms)
            if steady is not None:
                candidates.append(steady)
            now_ms, _y, _vy, _score, _gates, _flaps, last_flap_ms = state_item
            can_flap = (
                now_ms - last_flap_ms >= min_interval_ms
            )
            if can_flap:
                flapped = _step_tree_fly_state(seed, state_item, flap=True, frame_ms=frame_ms)
                if flapped is not None:
                    candidates.append(flapped)
        if not candidates:
            break
        buckets = {}
        for state_item in candidates:
            now_ms, y, vy, score, gates, _flaps, _last_flap_ms = state_item
            first_gate = gates[0]
            key = (
                int(score),
                int(round(y / 4.0)),
                int(round(vy / 20.0)),
                int(first_gate.get("index") or 0),
                int(round(float(first_gate.get("x") or 0) / 8.0)),
                int(round(float(state_item[6]) / min_interval_ms)),
            )
            old = buckets.get(key)
            if old is None or _tree_fly_state_quality(state_item) > _tree_fly_state_quality(old):
                buckets[key] = state_item
        beam = sorted(buckets.values(), key=_tree_fly_state_quality, reverse=True)[:beam_width]
        reached = [state_item for state_item in beam if int(state_item[3]) >= target_score]
        if reached:
            selected = max(reached, key=_tree_fly_state_quality)
            return list(selected[5]), selected
    return list(best[5]), best


def build_tree_fly_proof(run, *, rng=None, profile=None):
    rng = rng or random
    profile = dict(profile or {})
    seed = str((run or {}).get("seed") or profile.get("seed") or "").strip()
    if not seed:
        raise ValueError("fly seed missing")
    target_score = _target_score("fly", rng, profile)
    frame_ms = max(8.0, float(profile.get("frame_ms") or TREE_MINIAPP_FLY_FRAME_MS))
    requested_duration_ms = max(15000, int(profile.get("max_duration_ms") or max(45000, target_score * 1400)))
    max_duration_ms = min(requested_duration_ms, TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS)
    flaps, planned_state = plan_tree_fly_flaps(seed, target_score=target_score, rng=rng, profile=profile)
    planned_score = int(planned_state[3]) if planned_state else 0
    planned_duration_ms = int(round(planned_state[0])) if planned_state else 0
    duration_ms = max(planned_duration_ms, (flaps[-1] + 1) if flaps else 0)
    replay = simulate_tree_fly_run(
        seed,
        flaps,
        max_duration_ms=duration_ms,
        frame_ms=frame_ms,
    )
    replay_score = int(replay.get("score") or 0)
    if replay_score != planned_score:
        raise ValueError(
            f"fly proof replay mismatch: planned={planned_score} replay={replay_score}"
        )
    proof = {
        "flaps": [int(item) for item in flaps],
        "durationMs": int(duration_ms),
        "clientScore": replay_score,
    }
    summary = {
        "mode": "fly",
        "targetScore": int(target_score),
        "score": replay_score,
        "flapCount": len(flaps),
        "durationMs": proof["durationMs"],
        "gameOver": bool(replay.get("gameOver")),
        "profile": {
            "frame_ms": float(frame_ms),
            "beam_width": _int_between(
                profile.get("beam_width"),
                TREE_MINIAPP_FLY_DEFAULT_BEAM_WIDTH,
                80,
                TREE_MINIAPP_FLY_MAX_BEAM_WIDTH,
            ),
            "max_duration_ms": int(max_duration_ms),
            "min_interval_ms": int(profile.get("min_interval_ms") or 0),
            "planned_score": planned_score,
            "replay_score": replay_score,
            "forced_miss": False,
        },
    }
    return proof, summary


def _jump_type(seed, index):
    roll = tree_miniapp_seed_hash(seed, index * 7 + 3)
    if index > 0 and index % 7 == 0:
        return {"score": 1.5}
    if roll > 0.82:
        return {"score": 1.25}
    if roll > 0.62:
        return {"score": 1.12}
    if roll > 0.42:
        return {"score": 1.08}
    return {"score": 1.0}


def make_tree_jump_platform(seed, index, origin=None):
    base = dict(origin or TREE_MINIAPP_JUMP_START)
    index = int(index or 0)
    if index <= 0:
        return {"x": float(base["x"]), "y": float(base["y"]), "r": 34.0, "type": _jump_type(seed, 0)}
    return {
        "x": float(base["x"]) + 112.0 + tree_miniapp_seed_hash(seed, index * 11 + 1) * 58.0,
        "y": float(base["y"]) + -72.0 + tree_miniapp_seed_hash(seed, index * 13 + 2) * 136.0,
        "r": 29.0 + tree_miniapp_seed_hash(seed, index * 17 + 4) * 8.0,
        "type": _jump_type(seed, index),
    }


def _jump_distance_for_charge(charge):
    return 54.0 + _clamp(charge, 0, 1) * 245.0


def _estimate_jump_landing(current, next_platform, charge):
    dx = float(next_platform["x"]) - float(current["x"])
    dy = float(next_platform["y"]) - float(current["y"])
    dist = max(1.0, math.hypot(dx, dy))
    jump_dist = _jump_distance_for_charge(charge)
    return {
        "x": float(current["x"]) + dx / dist * jump_dist,
        "y": float(current["y"]) + dy / dist * jump_dist,
    }


def _score_jump_landing(next_platform, landing, center_combo):
    error = math.hypot(float(landing["x"]) - float(next_platform["x"]), float(landing["y"]) - float(next_platform["y"]))
    perfect = max(11.0, float(next_platform["r"]) * 0.32)
    edge = max(28.0, float(next_platform["r"]) * 0.86)
    hit = error <= edge
    center = error <= perfect
    next_center_combo = int(center_combo or 0) + 1 if center else 0
    points = 0
    if hit:
        points = min(next_center_combo * 2, TREE_MINIAPP_JUMP_CENTER_SCORE_CAP) if center else 1
    return {
        "hit": bool(hit),
        "center": bool(center),
        "error": float(error),
        "points": int(points),
        "centerCombo": int(next_center_combo),
    }


def _choose_jump_miss_charge(current, next_platform, center_combo, rng):
    dx = float(next_platform["x"]) - float(current["x"])
    dy = float(next_platform["y"]) - float(current["y"])
    dist = math.hypot(dx, dy)
    ideal = _clamp((dist - 54.0) / 245.0, 0.0, 1.0)
    deltas = [-0.32, 0.32, -0.45, 0.45, -0.6, 0.6]
    if rng.random() < 0.5:
        deltas.reverse()
    for delta in deltas:
        charge = round(float(_clamp(ideal + delta, 0.0, 1.0)), 4)
        landing = _estimate_jump_landing(current, next_platform, charge)
        result = _score_jump_landing(next_platform, landing, center_combo)
        if not result["hit"]:
            return charge
    return round(0.0 if ideal >= 0.5 else 1.0, 4)


def _choose_jump_edge_charge(current, next_platform, center_combo, rng):
    dx = float(next_platform["x"]) - float(current["x"])
    dy = float(next_platform["y"]) - float(current["y"])
    dist = math.hypot(dx, dy)
    ideal = _clamp((dist - 54.0) / 245.0, 0.0, 1.0)
    deltas = [0.052, -0.052, 0.064, -0.064, 0.078, -0.078, 0.092, -0.092]
    if rng.random() < 0.5:
        deltas.reverse()
    for delta in deltas:
        charge = round(float(_clamp(ideal + delta, 0.0, 1.0)), 4)
        landing = _estimate_jump_landing(current, next_platform, charge)
        result = _score_jump_landing(next_platform, landing, center_combo)
        if result["hit"] and not result["center"]:
            return charge
    return None


def simulate_tree_jump_run(seed, charges):
    seed = str(seed or "").strip()
    current = make_tree_jump_platform(seed, 0)
    next_platform = make_tree_jump_platform(seed, 1, current)
    score = 0
    center_combo = 0
    index = 0
    game_over = False
    last_result = {}
    for raw_charge in charges or ():
        charge = _clamp(raw_charge, 0, 1)
        landing = _estimate_jump_landing(current, next_platform, charge)
        result = _score_jump_landing(next_platform, landing, center_combo)
        last_result = result
        if not result["hit"]:
            game_over = True
            break
        score += int(result["points"])
        center_combo = int(result["centerCombo"])
        index += 1
        current = dict(next_platform)
        next_platform = make_tree_jump_platform(seed, index + 1, current)
    return {
        "score": int(score),
        "jumps": int(index),
        "gameOver": bool(game_over),
        "lastError": round(float(last_result.get("error", 0.0)), 3),
    }


def build_tree_jump_proof(run, *, rng=None, profile=None):
    rng = rng or random
    profile = dict(profile or {})
    seed = str((run or {}).get("seed") or profile.get("seed") or "").strip()
    if not seed:
        raise ValueError("jump seed missing")
    target_score = _target_score("jump", rng, profile)
    cap_score = target_score
    high_precision = bool(profile.get("high_precision")) or target_score >= 30
    default_max_jumps = max(14, int(target_score // 6) + 4) if high_precision else 14
    max_jumps = max(2, int(profile.get("max_jumps") or default_max_jumps))
    current = make_tree_jump_platform(seed, 0)
    next_platform = make_tree_jump_platform(seed, 1, current)
    charges = []
    score = 0
    center_combo = 0
    index = 0
    forced_miss = False
    while index < max_jumps:
        dx = float(next_platform["x"]) - float(current["x"])
        dy = float(next_platform["y"]) - float(current["y"])
        dist = math.hypot(dx, dy)
        ideal = _clamp((dist - 54.0) / 245.0, 0.0, 1.0)
        if score >= target_score:
            charge = _choose_jump_miss_charge(current, next_platform, center_combo, rng)
            forced_miss = True
        else:
            jitter = rng.uniform(-0.012, 0.012) if high_precision else rng.uniform(-0.045, 0.055)
            charge = _clamp(ideal + jitter, 0.0, 1.0)
            if not high_precision and rng.random() < 0.22:
                charge = _clamp(charge + rng.choice((-1, 1)) * rng.uniform(0.045, 0.075), 0.0, 1.0)
        charge = round(float(charge), 4)
        charges.append(charge)
        landing = _estimate_jump_landing(current, next_platform, charge)
        result = _score_jump_landing(next_platform, landing, center_combo)
        if result["hit"] and score + int(result["points"]) > cap_score:
            charge = None
            if high_precision and score < target_score:
                charge = _choose_jump_edge_charge(current, next_platform, center_combo, rng)
            if charge is None:
                charge = _choose_jump_miss_charge(current, next_platform, center_combo, rng)
                forced_miss = True
            charges[-1] = charge
            landing = _estimate_jump_landing(current, next_platform, charge)
            result = _score_jump_landing(next_platform, landing, center_combo)
        if not result["hit"]:
            break
        score += int(result["points"])
        center_combo = int(result["centerCombo"])
        index += 1
        current = dict(next_platform)
        next_platform = make_tree_jump_platform(seed, index + 1, current)
    replay = simulate_tree_jump_run(seed, charges)
    duration_ms = int(sum(max(220, min(1200, charge * 1200)) + rng.randint(520, 1350) for charge in charges) + rng.randint(800, 2200))
    proof = {
        "charges": list(charges),
        "durationMs": max(1200, duration_ms),
        "clientScore": int(replay["score"]),
    }
    summary = {
        "mode": "jump",
        "targetScore": int(target_score),
        "score": int(replay["score"]),
        "chargeCount": len(charges),
        "durationMs": proof["durationMs"],
        "gameOver": bool(replay["gameOver"]),
        "forced_miss": bool(forced_miss),
    }
    return proof, summary


def _parse_mode_quota(data, mode):
    empty = {"used": 0, "limit": 0, "remaining": 0, "best": 0}
    council = data.get("council") if isinstance(data, dict) else {}
    daily = council.get("daily") if isinstance(council, dict) else {}
    quota = daily.get(mode) if isinstance(daily, dict) else {}
    if any(isinstance(scope, dict) and "ok" in scope and scope["ok"] is not True
           for scope in (council, daily, quota)):
        return empty, "tree_quota_not_ok"
    if not isinstance(quota, dict) or not {"used", "limit"} <= quota.keys():
        return empty, "tree_quota_missing"
    counts = {key: _quota_integer(quota[key]) for key in ("used", "limit", "remaining") if key in quota}
    if None in counts.values():
        return empty, "tree_quota_invalid_count"
    used, limit = counts["used"], counts["limit"]
    remaining = counts.get("remaining", limit - used)
    if used > limit or remaining > limit or used + remaining != limit:
        return empty, "tree_quota_inconsistent"
    return {"used": used, "limit": limit, "remaining": remaining,
            "best": _quota_integer(quota.get("best")) or 0}, ""


def _tree_state_body(data, *, from_submit=False):
    if not isinstance(data, dict):
        return {}, "tree_state_missing"
    if "data" in data:
        if not isinstance(data["data"], dict) or "data" in data["data"]:
            return {}, "tree_state_invalid_envelope"
        if any(key in data for key in ("council", "seasonState", "tree", "ranking", "actions")):
            return {}, "tree_state_conflicting_envelopes"
        data = {"ok": data.get("ok"), **data["data"]}
    if "ok" in data and data["ok"] is not True:
        return {}, "tree_state_not_ok"
    # Only run/submit advertises seasonState as the current council snapshot.
    if from_submit and "seasonState" in data:
        if "council" in data or not isinstance(data["seasonState"], dict):
            return {}, "tree_state_conflicting_panels"
        data = {**data, "council": data["seasonState"]}
    return data, ""


def parse_tree_miniapp_state(data, *, from_submit=False):
    data, body_error = _tree_state_body(data, from_submit=from_submit)
    tree = data.get("tree") if isinstance(data.get("tree"), dict) else {}
    council = data.get("council") if isinstance(data.get("council"), dict) else {}
    season = council.get("season") if isinstance(council.get("season"), dict) else {}
    ranking = data.get("ranking") if isinstance(data.get("ranking"), dict) else {}
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    jump, jump_error = _parse_mode_quota(data, "jump")
    fly, fly_error = _parse_mode_quota(data, "fly")
    context, context_error = {}, ""
    if "season" in council and not isinstance(council["season"], dict):
        context_error = "tree_quota_invalid_season"
    if "seasonId" in season:
        value = season["seasonId"]
        if isinstance(value, str) and value.strip():
            context["season_id"] = value.strip()
        else:
            context_error = "tree_quota_invalid_season"
    if "dayIndex" in season:
        value = _quota_integer(season["dayIndex"])
        if value is not None:
            context["day_index"] = value
        else:
            context_error = "tree_quota_invalid_day"
    quota_error = {"jump": body_error or context_error or jump_error,
                   "fly": body_error or context_error or fly_error}
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
        "quota_known": {mode: not error for mode, error in quota_error.items()},
        "quota_error": quota_error,
        "quota_context": context,
        "my_contribution_points": _int_value(ranking.get("myContributionPoints"), 0),
        "branch_rank": _int_value(ranking.get("branchRank"), 0),
        "claimed": bool(ranking.get("claimed")),
        "can_run_game": bool((not quota_error["jump"] and jump["remaining"])
                             or (not quota_error["fly"] and fly["remaining"])),
        "can_claim_reward": bool(ranking.get("claimed") is False and str(season.get("status") or "") in {"ended", "settled", "claimable"}),
        "actions": {
            key: bool(actions.get(key))
            for key in ("canMeridian", "canRitual", "canDarkScheme", "canHarvest", "canIrrigate", "canGuard", "canOffer")
        },
    }


def classify_tree_miniapp_error(error):
    raw = str(error or "").strip()
    lowered = raw.lower()
    if any(marker in lowered for marker in TREE_MINIAPP_VERIFICATION_ERROR_MARKERS):
        return "verification_required"
    if any(keyword in lowered for keyword in TREE_MINIAPP_STOP_ERROR_KEYWORDS):
        return "daily_limit"
    return "failed"


def build_tree_game_proof(mode, run, *, rng=None, profile=None):
    normalized_mode = str(mode or "").strip().lower()
    profile = normalize_tree_score_profile(normalized_mode, profile)
    if normalized_mode == "fly":
        return build_tree_fly_proof(run, rng=rng, profile=profile)
    if normalized_mode == "jump":
        return build_tree_jump_proof(run, rng=rng, profile=profile)
    raise ValueError("tree miniapp mode must be jump or fly")


def _flow_result(ok, status, *, data=None, events=None, error="", request_budget=None,
                 outcome_unknown=False, open_run=False, retry_after_sec=0):
    return {
        "ok": bool(ok),
        "status": str(status or ""),
        "data": data if isinstance(data, dict) else {},
        "events": list(events or ()),
        "error": sanitize_webapp_secret_text(error),
        "request_budget": request_budget.safe_summary() if request_budget is not None else {},
        "outcome_unknown": bool(outcome_unknown),
        "open_run": bool(open_run),
        "retry_after_sec": float(retry_after_sec),
    }




def _non_idempotent_failure_status(result):
    if result.error_type == "operation_cancelled":
        return "cancelled"
    if result.error_type == "request_budget":
        return "request_budget"
    if int(result.attempts or 0) <= 0:
        return "failed"
    if (result.error_type == "app" and result.data.get("ok") is False
            and (200 <= result.status_code < 300 or 400 <= result.status_code < 500)
            and result.status_code not in {408, 425, 429}):
        return classify_tree_miniapp_error(result.error)
    return "result_unknown"


class _TreeFlow:
    """One tree operation's request budget and already-returned business facts."""

    def __init__(self, *, token, init_data, transport, adapter, sleeper, capture_sink,
                 capture_source, operation_check, request_budget=None, daily=False, player_id=None, checkpoint_sink=None):
        self.adapter = adapter or build_tree_miniapp_adapter()
        self.token, self.init_data = str(token or "").strip(), str(init_data or "").strip()
        self.transport = transport or _requests_transport
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.operation_check = operation_check
        self.budget = request_budget or MiniAppRequestBudget(self.adapter.request_policy, sleeper=self.sleep)
        self.capture_sink, self.capture_source = capture_sink, capture_source
        self.daily = daily
        self.player_id = player_id
        self.confirmed_rounds = set()
        self.events, self.data = [], {"state": {}}
        if daily:
            self.data.update(runs=[], rewards={"items": {}, "gains": {}}, errors=[])
        self.open_run = self.outcome_unknown = False
        self.retry_after_sec = 0.0
        self.checkpoint_sink, self.sequence = checkpoint_sink, 0
        self.pending, self.resolution, self.last_evidence = {}, {}, None
        self.dispatched = False
        self.checkpoint_failed = False

    def checkpoint(self, phase, result=None):
        evidence = {"version": 1, "sequence": self.sequence + 1, "phase": phase,
                    "pending": deepcopy(self.pending), "resolution": deepcopy(self.resolution),
                    "dispatched": self.dispatched,
                    "result": tree_operations.project_result(result or self.snapshot(False, "running"))}
        self.last_evidence = evidence
        try:
            accepted = self.checkpoint_sink is None or self.checkpoint_sink(evidence) is True
        except Exception:
            accepted = False
        if accepted:
            self.sequence += 1
        else:
            self.checkpoint_failed = True
        return accepted

    def allocated(self, context):
        self.pending = {**self.pending, "action": "allocated", "round_key": tree_round_key(context)}
        if not self.checkpoint("response"):
            raise MiniAppRequestAborted("tree_checkpoint_unavailable")

    def settled(self):
        self.pending, self.resolution = {}, {}
        if not self.checkpoint("settled"):
            raise MiniAppRequestAborted("tree_checkpoint_unavailable")

    def check(self):
        require_miniapp_operation(self.operation_check)

    def sleep(self, delay):
        self.check()
        self.sleeper(delay)
        self.check()

    def request(self, endpoint, *, step=None, payload=None):
        step = step or endpoint
        response_wait = 0.0
        mutation = endpoint in {"run_start", "run_submit"}
        entered = False
        previous = deepcopy(self.pending)
        request_intent = {}
        if mutation:
            self.check()
            request_intent = {"action": endpoint, "entry_key": tree_operations.digest(self.token),
                              "mode": payload["mode"],
                              "round_key": tree_round_key(payload) if endpoint == "run_submit" else ""}
            self.pending, self.resolution = request_intent, {}
            if not self.checkpoint("intent"):
                self.pending = previous
                raise MiniAppRequestAborted("tree_checkpoint_unavailable")

        def dispatch(request):
            nonlocal response_wait, entered
            self.check()
            entered = True
            if mutation:
                self.dispatched = True
            response = self.transport(request)
            response_wait = _response_retry_after_sec(response)
            return response

        result = execute_miniapp_http_request(
            build_tree_miniapp_request(endpoint, token=self.token, init_data=self.init_data,
                                       payload=payload, adapter=self.adapter),
            dispatch, backoff_sec=(), sleeper=self.sleep,
            capture_sink=self.capture_sink, capture_source=self.capture_source,
            step_key=step, request_budget=self.budget, operation_check=self.operation_check,
        )
        self.retry_after_sec = max(self.retry_after_sec, response_wait, result.retry_after_sec)
        _append_http_event(self.events, step, result)
        if response_wait > 0:
            self.events[-1]["retry_after_sec"] = max(response_wait, result.retry_after_sec)
        if mutation:
            if not entered or (not result.ok and _non_idempotent_failure_status(result) not in {"result_unknown", "cancelled", "request_budget"}):
                self.pending = previous
                self.resolution = {"kind": "not_sent" if not entered else "rejected", "request": request_intent}
                if not self.checkpoint("response"):
                    raise MiniAppRequestAborted("tree_checkpoint_unavailable")
            if result.ok and endpoint == "run_start":
                self.open_run = True
            elif not result.ok and _non_idempotent_failure_status(result) == "result_unknown":
                self.outcome_unknown = True
        return result

    def failed_request(self, result, *, mutation=False, status=None):
        if mutation:
            status = _non_idempotent_failure_status(result)
        elif result.error_type == "operation_cancelled":
            status = "cancelled"
        elif result.error_type == "request_budget":
            status = "request_budget"
        else:
            status = status or classify_tree_miniapp_error(result.error)
        return self.finish(False, status, error=result.error)

    def unconfirmed_submit(self, receipt):
        self.outcome_unknown = True
        if receipt.get("round_key"):
            partial = {"round_key": receipt["round_key"], "rewards": receipt["rewards"],
                       "error": receipt["error"], "material_error": receipt["material_error"]}
            self.data["partial_receipts"] = [partial]
            if self.daily:
                for kind in ("items", "gains"):
                    _merge_tree_reward_counts(self.data["rewards"][kind], receipt["rewards"][kind])
            else:
                self.data["rewards"] = receipt["rewards"]
                self.data["submit"] = {"confirmed": False, "score": None, "round_key": receipt["round_key"]}
        status = "score_unknown" if receipt["error"] == "tree_score_missing_or_invalid" else "result_unknown"
        return self.finish(False, status, error=receipt["error"])

    def snapshot(self, ok, status, *, error=""):
        if status == "result_unknown":
            self.outcome_unknown = True
        data = self.data
        if self.daily:
            error = sanitize_webapp_secret_text(error)
            if error and error not in data["errors"]:
                data["errors"].append(error)
            phase = "completed" if ok else "unknown" if self.outcome_unknown else "blocked"
            data = _daily_tree_data(phase=phase, state=data["state"], runs=data["runs"],
                                    rewards=data["rewards"], errors=data["errors"],
                                    partial_receipts=data.get("partial_receipts"))
        return _flow_result(
            ok, status, data=data, events=self.events, error=error, request_budget=self.budget,
            outcome_unknown=self.outcome_unknown or self.pending.get("action") in {"run_start", "run_submit"},
            open_run=self.open_run, retry_after_sec=self.retry_after_sec,
        )

    def finish(self, ok, status, *, error=""):
        if self.checkpoint_failed:
            ok, status, error = False, "persistence_pending", "tree_checkpoint_unavailable"
        result = self.snapshot(ok, status, error=error)
        if self.sequence and not self.checkpoint("complete", result):
            result.update(ok=False, status="persistence_pending", error="tree_checkpoint_unavailable")
        return {**result, "action_dispatched": self.dispatched, "checkpoint_sequence": self.sequence,
                "operation_evidence": deepcopy(self.last_evidence) if self.sequence else None}


def _authoritative_tree_state(data, *, from_submit=False):
    state = parse_tree_miniapp_state(data, from_submit=from_submit)
    return state if any(state["quota_known"].values()) else None


def _tree_quota_progressed(previous, current, mode):
    if not isinstance(current, dict) or not all(current.get("quota_known", {}).get(key) for key in TREE_MINIAPP_MODES):
        return False
    context = current.get("quota_context") or {}
    if any(context.get(key) != value for key, value in (previous.get("quota_context") or {}).items()):
        return False
    for key in TREE_MINIAPP_MODES:
        before, after = previous[key], current[key]
        if (after["limit"] != before["limit"] or after["used"] < before["used"]
                or after["remaining"] > before["remaining"]):
            return False
    return (current[mode]["used"] > previous[mode]["used"]
            and current[mode]["remaining"] < previous[mode]["remaining"])


def _merge_tree_reward_counts(target, source):
    for name, amount in dict(source or {}).items():
        clean_name = sanitize_webapp_secret_text(name, limit=80).strip()
        parsed_amount = _int_value(amount, 0)
        if clean_name and parsed_amount:
            target[clean_name] = int(target.get(clean_name, 0) or 0) + parsed_amount


def summarize_tree_rewards(data):
    """Summarize only explicitly counted rewards in the current response."""
    return parse_tree_rewards(data)[0]


def _daily_tree_data(*, phase, state=None, runs=None, rewards=None, errors=None, partial_receipts=None):
    state = state if isinstance(state, dict) else {}
    return {
        "phase": str(phase or ""),
        "quotas": {
            mode: dict(state.get(mode) or {})
            for mode in ("jump", "fly")
        },
        "quota_known": dict(state.get("quota_known") or {}),
        "runs": list(runs or ()),
        "partial_receipts": list(partial_receipts or ()),
        "rewards": dict(rewards or {"items": {}, "gains": {}}),
        "errors": list(errors or ()),
        "state": state,
    }


def tree_miniapp_ranking_target(data, mode, profile=None):
    mode = str(mode or "").strip().lower()
    profile = normalize_tree_score_profile(mode, profile)
    configured = list(profile.get("target_score_range") or ())
    fallback = int(round(sum(configured) / len(configured))) if configured else int(TREE_MINIAPP_MIN_TARGET_SCORE[mode])
    ranking = data.get("ranking") if isinstance(data, dict) and isinstance(data.get("ranking"), dict) else {}
    rows = ranking.get("branchTop") or ranking.get("rows") or []
    scores = sorted(
        (
            _int_value(item.get(mode), 0)
            for item in rows
            if isinstance(item, dict) and not item.get("self")
        ),
        reverse=True,
    )
    scores = [score for score in scores if score > 0]
    cap = int(TREE_MINIAPP_MAX_TARGET_SCORE[mode])
    floor = int(TREE_MINIAPP_MIN_TARGET_SCORE[mode])
    podium_scores = scores[:3]
    first_score = podium_scores[0] if podium_scores else 0
    reference_rank = 3 if len(podium_scores) >= 3 else 2 if len(podium_scores) >= 2 else 0
    reference_score = podium_scores[reference_rank - 1] if reference_rank else 0
    target = min(cap, max(floor, fallback))
    chase_possible = False
    if 0 < reference_score < cap and first_score > reference_score:
        # Prefer a low podium score and stay strictly below first place. Ties and
        # a compressed leaderboard are deliberately not chased because the
        # server tie-break can otherwise turn a second/third-place target into
        # an accidental first place.
        podium_target = min(reference_score + 1, first_score - 1, cap)
        if podium_target >= floor:
            target = podium_target
            chase_possible = True
    return {
        "target_score": int(target),
        "reference_rank": int(reference_rank),
        "reference_score": int(reference_score),
        "chase_possible": chase_possible,
        "top_scores": podium_scores,
    }


def run_tree_miniapp_daily_lab_flow(
    *,
    token,
    init_data,
    transport=None,
    adapter=None,
    rng=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    score_profiles=None,
    operation_check=None,
    request_budget=None,
    player_id=None,
    checkpoint_sink=None,
):
    """Run all server-advertised jump quota, then fly quota, using one initData."""

    flow = _TreeFlow(
        token=token, init_data=init_data, transport=transport, adapter=adapter, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source, operation_check=operation_check,
        request_budget=request_budget, daily=True, player_id=player_id, checkpoint_sink=checkpoint_sink,
    )
    try:
        flow.check()
        return _run_tree_daily(flow, rng=rng, score_profiles=score_profiles)
    except MiniAppRequestAborted as exc:
        return flow.finish(False, "cancelled", error=exc)
    except Exception as exc:
        return flow.finish(False, "failed", error=exc)


def _run_tree_daily(flow, *, rng=None, score_profiles=None):
    rng = rng or random
    runs, errors, rewards = flow.data["runs"], flow.data["errors"], flow.data["rewards"]
    profiles = dict(score_profiles or {})
    context, context_error = {}, ""
    if not flow.token or not flow.init_data:
        return flow.finish(False, "failed", error="token missing" if not flow.token else "initData missing")

    def read_state(step_key):
        nonlocal context_error
        result = flow.request("start", step=step_key)
        _, context_error = tree_panel_context(
            result.data, player_id=flow.player_id if flow.player_id is not None else context.get("player_id"),
        ) if result.ok else ({}, "")
        return result, _authoritative_tree_state(result.data) if result.ok and not context_error else None

    start_result, state = read_state("start")
    flow.data["state"] = state or {}
    if not start_result.ok:
        return flow.failed_request(start_result)
    if state is None or not all((state.get("quota_known") or {}).get(mode) for mode in ("jump", "fly")):
        return flow.finish(False, "quota_unknown", error=context_error or "server quota missing or invalid for jump/fly")
    context, _ = tree_panel_context(start_result.data, player_id=flow.player_id)

    flow.check()
    ranking_targets = {
        mode: tree_miniapp_ranking_target(start_result.data, mode, profiles.get(mode))
        for mode in ("jump", "fly")
    }
    verification_mismatch_modes = []

    for mode in ("jump", "fly"):
        flow.check()
        try:
            score_profile = normalize_tree_score_profile(mode, profiles.get(mode))
            score_profile["target_score_range"] = (
                ranking_targets[mode]["target_score"],
                ranking_targets[mode]["target_score"],
            )
        except Exception as exc:
            return flow.finish(False, "failed", error=exc)

        while _int_value((state.get(mode) or {}).get("remaining"), 0) > 0:
            flow.check()
            quota_before = dict(state.get(mode) or {})
            run_start_result = flow.request("run_start", step=f"{mode}:run_start", payload={"mode": mode})
            if not run_start_result.ok:
                return flow.failed_request(run_start_result, mutation=True)

            run, run_context, error = parse_tree_allocation(run_start_result.data, mode=mode, context=context)
            if error or tree_round_key(run_context) in flow.confirmed_rounds:
                return flow.finish(False, "result_unknown", error=error or "tree_round_already_submitted")
            flow.allocated(run_context)
            flow.check()
            try:
                proof, proof_summary = build_tree_game_proof(mode, run, rng=rng, profile=score_profile)
            except Exception as exc:
                return flow.finish(False, "solve_failed", error=exc)
            flow.check()

            score = _int_value(proof_summary.get("score"), 0)
            target_score = _int_value(proof_summary.get("targetScore"), 0)
            if score <= 0 or score > TREE_MINIAPP_MAX_TARGET_SCORE[mode] or target_score > TREE_MINIAPP_MAX_TARGET_SCORE[mode]:
                error = f"unsafe {mode} proof score={score} target={target_score}"
                return flow.finish(False, "unsafe_score", error=error)

            duration_ms = max(0, _int_value(proof.get("durationMs"), 0))
            if duration_ms:
                flow.sleep(float(duration_ms) / 1000.0)
            submit_result = flow.request(
                "run_submit", step=f"{mode}:run_submit", payload={
                    "mode": mode,
                    "runToken": str(run.get("runToken") or ""),
                    "proof": proof,
                },
            )
            if not submit_result.ok:
                return flow.failed_request(submit_result, mutation=True)

            receipt = parse_tree_submit(submit_result.data, context=run_context)
            if not receipt["confirmed"]:
                return flow.unconfirmed_submit(receipt)
            flow.open_run = False
            flow.confirmed_rounds.add(receipt["round_key"])
            server_verification = receipt["verification"]
            submitted_score = receipt["score"]
            client_score = _int_value(proof.get("clientScore"), score)
            run_verification_mismatch = _tree_verification_mismatch(
                mode,
                client_score,
                submitted_score,
                server_verification,
            )
            reward_summary = receipt["rewards"]
            _merge_tree_reward_counts(rewards["items"], reward_summary.get("items"))
            _merge_tree_reward_counts(rewards["gains"], reward_summary.get("gains"))
            runs.append({
                "round_key": receipt["round_key"],
                "mode": mode,
                "score": submitted_score,
                "client_score": client_score,
                "target_score": target_score,
                "run_seed": str(run.get("seed") or ""),
                "proof": dict(proof),
                "ranking_target": dict(ranking_targets.get(mode) or {}),
                "quota_before": quota_before,
                "rewards": reward_summary,
                "server_verification": server_verification,
                "verification_mismatch": run_verification_mismatch,
                "material_error": receipt["material_error"],
            })
            flow.settled()

            next_state = _authoritative_tree_state(submit_result.data, from_submit=True)
            progressed = _tree_quota_progressed(state, next_state, mode)
            if not progressed:
                reconcile_result, reconciled_state = read_state(f"{mode}:reconcile")
                if not reconcile_result.ok:
                    return flow.failed_request(reconcile_result, status="quota_unknown")
                next_state = reconciled_state
                progressed = _tree_quota_progressed(state, next_state, mode)
            if next_state is None or not progressed:
                return flow.finish(False, "quota_unknown", error=context_error or f"{mode} quota did not advance consistently after confirmed submit")
            state = next_state
            flow.data["state"] = state

            if submitted_score <= 0:
                error = f"{mode} server score is zero; stop remaining daily attempts"
                return flow.finish(False, "zero_score", error=error)
            if receipt["material_error"]:
                return flow.finish(False, "material_unknown", error=receipt["material_error"])
            if run_verification_mismatch:
                error = (
                    f"{mode} server verification mismatch; "
                    f"client={client_score} server={submitted_score}; "
                    f"stop remaining {mode} attempts"
                )
                errors.append(error)
                verification_mismatch_modes.append(mode)
                break

    if verification_mismatch_modes:
        modes = ",".join(verification_mismatch_modes)
        error = f"server verification mismatch in {modes}; affected modes stopped"
        return flow.finish(False, "verification_mismatch", error=error)

    complete = all(
        (state.get("quota_known") or {}).get(mode)
        and _int_value((state.get(mode) or {}).get("remaining"), -1) == 0
        for mode in ("jump", "fly")
    )
    status = "completed" if complete else "quota_unknown"
    return flow.finish(complete, status, error="" if complete else "daily quota not explicitly exhausted")


def run_tree_miniapp_start_lab_flow(
    *,
    token,
    init_data,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    request_budget=None,
    player_id=None,
):
    flow = _TreeFlow(
        token=token, init_data=init_data, transport=transport, adapter=adapter, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source, operation_check=operation_check,
        request_budget=request_budget, player_id=player_id,
    )
    try:
        flow.check()
        if not flow.token or not flow.init_data:
            return flow.finish(False, "failed", error="token missing" if not flow.token else "initData missing")
        result = flow.request("start")
        if not result.ok:
            return flow.failed_request(result)
        _, error = tree_panel_context(result.data, player_id=flow.player_id)
        if error:
            return flow.finish(False, "failed", error=error)
        flow.data["state"] = parse_tree_miniapp_state(result.data)
        return flow.finish(True, "ready")
    except MiniAppRequestAborted as exc:
        return flow.finish(False, "cancelled", error=exc)
    except Exception as exc:
        return flow.finish(False, "failed", error=exc)


def run_tree_miniapp_game_lab_flow(
    *,
    token,
    init_data,
    mode="fly",
    submit=False,
    transport=None,
    adapter=None,
    rng=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    score_profile=None,
    operation_check=None,
    request_budget=None,
    player_id=None,
    checkpoint_sink=None,
):
    flow = _TreeFlow(
        token=token, init_data=init_data, transport=transport, adapter=adapter, sleeper=sleeper,
        capture_sink=capture_sink, capture_source=capture_source, operation_check=operation_check,
        request_budget=request_budget, player_id=player_id, checkpoint_sink=checkpoint_sink,
    )
    try:
        flow.check()
        return _run_tree_game(flow, mode=mode, submit=submit, rng=rng, score_profile=score_profile)
    except MiniAppRequestAborted as exc:
        return flow.finish(False, "cancelled", error=exc)
    except Exception as exc:
        return flow.finish(False, "failed", error=exc)


def _run_tree_game(flow, *, mode, submit, rng, score_profile):
    rng = rng or random
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in TREE_MINIAPP_MODES:
        return flow.finish(False, "failed", error="tree miniapp mode must be jump or fly")
    if not flow.token or not flow.init_data:
        return flow.finish(False, "failed", error="token missing" if not flow.token else "initData missing")
    try:
        score_profile = normalize_tree_score_profile(normalized_mode, score_profile)
    except Exception as exc:
        return flow.finish(False, "failed", error=exc)

    events = flow.events
    start_result = flow.request("start")
    if not start_result.ok:
        return flow.failed_request(start_result)
    context, error = tree_panel_context(start_result.data, player_id=flow.player_id)
    if error:
        return flow.finish(False, "quota_unknown", error=error)

    state = parse_tree_miniapp_state(start_result.data)
    flow.data.update(state=state, mode=normalized_mode)
    if not state.get("quota_known", {}).get(normalized_mode):
        return flow.finish(False, "quota_unknown", error=f"server quota missing or invalid for {normalized_mode}")
    quota = state.get(normalized_mode) if isinstance(state, dict) else {}
    if _int_value((quota or {}).get("remaining"), 0) <= 0:
        return flow.finish(False, "mode_exhausted")

    run_start_result = flow.request("run_start", payload={"mode": normalized_mode})
    if not run_start_result.ok:
        return flow.failed_request(run_start_result, mutation=True)
    run, run_context, error = parse_tree_allocation(run_start_result.data, mode=normalized_mode, context=context)
    if error:
        return flow.finish(False, "result_unknown", error=error)
    flow.allocated(run_context)

    flow.check()
    try:
        proof, proof_summary = build_tree_game_proof(normalized_mode, run, rng=rng, profile=score_profile)
    except Exception as exc:
        events.append({
            "step": "solve",
            "ok": False,
            "mode": normalized_mode,
            "error": sanitize_webapp_secret_text(exc),
        })
        return flow.finish(False, "solve_failed", error=exc)

    flow.check()
    events.append({
        "step": "solve",
        "ok": True,
        "mode": normalized_mode,
        "score": int(proof_summary.get("score") or 0),
        "targetScore": int(proof_summary.get("targetScore") or 0),
        "durationMs": int(proof_summary.get("durationMs") or proof.get("durationMs") or 0),
    })
    data = flow.data
    data.update({
        "state": state,
        "mode": normalized_mode,
        "run": {
            "mode": str(run.get("mode") or normalized_mode),
            "used": _int_value(run.get("used"), 0),
            "limit": _int_value(run.get("limit"), 0),
            "runNo": _int_value(run.get("runNo"), 0),
            "seasonId": str(run.get("seasonId") or ""),
            "playDate": str(run.get("playDate") or ""),
        },
        "proof_summary": proof_summary,
        "score_profile": {
            "target_score_range": list(score_profile.get("target_score_range") or ()),
        },
    })
    proof_score = _int_value(proof_summary.get("score"), 0)
    if submit and proof_score <= 0:
        events.append({
            "step": "submit_guard",
            "ok": False,
            "mode": normalized_mode,
            "score": proof_score,
            "error": "local proof score <= 0; submit blocked",
        })
        return flow.finish(False, "unsafe_score", error="local proof score <= 0; submit blocked")
    if not submit:
        return flow.finish(True, "prepared")

    duration_ms = max(0, int(proof.get("durationMs") or proof_summary.get("durationMs") or 0))
    if duration_ms > 0:
        flow.sleep(float(duration_ms) / 1000.0)
    submit_result = flow.request(
        "run_submit", payload={
            "mode": normalized_mode,
            "runToken": str(run.get("runToken") or ""),
            "proof": proof,
        },
    )
    if not submit_result.ok:
        return flow.failed_request(submit_result, mutation=True)
    receipt = parse_tree_submit(submit_result.data, context=run_context)
    if not receipt["confirmed"]:
        return flow.unconfirmed_submit(receipt)
    flow.open_run = False
    server_verification, submitted_score = receipt["verification"], receipt["score"]
    data["rewards"] = receipt["rewards"]
    data["submit"] = {
        "confirmed": True,
        "round_key": receipt["round_key"],
        "score": submitted_score,
        "data_keys": sorted(str(key) for key in submit_result.data),
        "server_verification": server_verification,
        "material_error": receipt["material_error"],
    }
    flow.settled()
    season_state = submit_result.data.get("seasonState") if isinstance(submit_result.data.get("seasonState"), dict) else {}
    if season_state:
        data["season_state_keys"] = sorted(str(key) for key in season_state)
    if submitted_score <= 0:
        return flow.finish(False, "zero_score", error=f"{normalized_mode} server score is zero")
    if receipt["material_error"]:
        return flow.finish(False, "material_unknown", error=receipt["material_error"])
    return flow.finish(True, "settled")


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
    operation_check=None,
):
    adapter = adapter or build_tree_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        require_miniapp_operation(operation_check)
        init_data = await request_tree_miniapp_init_data(
            identity_id, token=token, webview_url=webview_url, adapter=adapter, operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            return run_tree_miniapp_start_lab_flow(
                token=token, init_data=init_data, adapter=adapter,
                player_id=identity_id,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key, identity_id=identity_id,
                    timeout=TREE_MINIAPP_HTTP_TIMEOUT, operation_check=operation.check,
                ),
                sleeper=operation.sleep, operation_check=operation.check,
                capture_sink=capture_sink, capture_source=capture_source,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppRequestAborted as exc:
        return _flow_result(False, "cancelled", error=exc)
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_tree_miniapp_game_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    mode="fly",
    submit=False,
    transport=None,
    adapter=None,
    sleeper=None,
    rng=None,
    capture_sink=None,
    capture_source="",
    score_profile=None,
    operation_check=None,
    checkpoint_sink=None,
):
    adapter = adapter or build_tree_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        require_miniapp_operation(operation_check)
        init_data = await request_tree_miniapp_init_data(
            identity_id, token=token, webview_url=webview_url, adapter=adapter, operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            return run_tree_miniapp_game_lab_flow(
                token=token, init_data=init_data, mode=mode, submit=submit,
                player_id=identity_id,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key, identity_id=identity_id,
                    timeout=TREE_MINIAPP_HTTP_TIMEOUT, operation_check=operation.check,
                ),
                adapter=adapter, rng=rng, score_profile=score_profile,
                checkpoint_sink=checkpoint_sink,
                sleeper=operation.sleep, operation_check=operation.check,
                capture_sink=capture_sink, capture_source=capture_source,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppRequestAborted as exc:
        return _flow_result(False, "cancelled", error=exc)
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_tree_miniapp_daily_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    transport=None,
    adapter=None,
    sleeper=None,
    rng=None,
    capture_sink=None,
    capture_source="",
    score_profiles=None,
    operation_check=None,
    checkpoint_sink=None,
):
    adapter = adapter or build_tree_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        require_miniapp_operation(operation_check)
        init_data = str(init_data or "").strip() or await request_tree_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
            operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            return run_tree_miniapp_daily_lab_flow(
                token=token, init_data=init_data,
                player_id=identity_id,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key, identity_id=identity_id,
                    timeout=TREE_MINIAPP_HTTP_TIMEOUT, operation_check=operation.check,
                ),
                adapter=adapter, rng=rng, score_profiles=score_profiles,
                checkpoint_sink=checkpoint_sink,
                sleeper=operation.sleep, operation_check=operation.check,
                capture_sink=capture_sink, capture_source=capture_source,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppRequestAborted as exc:
        return _flow_result(False, "cancelled", data=_daily_tree_data(phase="blocked"), error=exc)
    except Exception as exc:
        return _flow_result(
            False,
            "failed",
            data=_daily_tree_data(phase="blocked", errors=[sanitize_webapp_secret_text(exc)]),
            error=exc,
        )


__all__ = [
    "TREE_MINIAPP_ENDPOINTS",
    "TREE_MINIAPP_FLY_MAX_BEAM_WIDTH",
    "TREE_MINIAPP_FLY_MAX_PLAN_DURATION_MS",
    "TREE_MINIAPP_FLY_MAX_PLAN_FRAMES",
    "TREE_MINIAPP_FLY_MIN_VERIFIED_SCORE_RATIO",
    "TREE_MINIAPP_JUMP_MIN_VERIFIED_SCORE_RATIO",
    "TREE_MINIAPP_GAME_KEY",
    "TREE_MINIAPP_MAX_TARGET_SCORE",
    "TREE_MINIAPP_MIN_TARGET_SCORE",
    "build_tree_fly_proof",
    "build_tree_game_proof",
    "build_tree_jump_proof",
    "build_tree_launch_args",
    "build_tree_miniapp_adapter",
    "build_tree_miniapp_flow_plan",
    "build_tree_miniapp_request",
    "classify_tree_miniapp_error",
    "extract_tree_miniapp_launch",
    "make_tree_fly_gate",
    "make_tree_jump_platform",
    "normalize_tree_score_profile",
    "normalize_tree_score_records",
    "parse_tree_miniapp_state",
    "request_tree_miniapp_init_data",
    "run_tree_miniapp_daily_lab_flow",
    "run_tree_miniapp_daily_production_flow",
    "run_tree_miniapp_game_lab_flow",
    "run_tree_miniapp_game_production_flow",
    "run_tree_miniapp_start_lab_flow",
    "run_tree_miniapp_start_production_flow",
    "simulate_tree_fly_run",
    "simulate_tree_jump_run",
    "summarize_tree_rewards",
    "tree_miniapp_seed_hash",
    "summarize_tree_entry",
]
