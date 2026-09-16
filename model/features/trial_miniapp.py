import asyncio
from copy import deepcopy
import hashlib
import inspect
import random

from telethon import functions

from ..runtime import _get_identity_client_with_account, account_rpc_slot
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
    MiniAppFlowCancelled,
    append_http_event as _append_http_event,
    build_pooled_miniapp_transport,
    run_miniapp_blocking_flow,
)
from .trial_receipts import normalize_trial_challenge_id, normalize_trial_player_id, parse_trial_finish_receipt


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
TRIAL_MINIAPP_PLANARITY_MIN_NODE_DISTANCE = 10.0
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


def build_trial_miniapp_request(
    endpoint,
    *,
    token,
    init_data_session=None,
    init_data="",
    player_id=None,
    payload=None,
    adapter=None,
):
    adapter = adapter or build_trial_miniapp_adapter()
    request_payload = dict(payload or {})
    if {"token", "playerId", "initData"}.intersection(request_payload):
        raise ValueError("trial_payload_reserved")
    request_payload["token"] = str(token or "").strip()
    selected_player_id = normalize_trial_player_id(player_id)
    if selected_player_id is not None:
        request_payload["playerId"] = selected_player_id
    return build_miniapp_http_request(
        adapter,
        endpoint,
        request_payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )




def extract_trial_miniapp_launch(event, *, message_text=""):
    adapter = build_trial_miniapp_adapter()
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
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


async def request_trial_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None, operation_check=None):
    adapter = adapter or build_trial_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "trial miniapp launch not allowed")
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


def build_trial_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=TRIAL_MINIAPP_GAME_KEY,
        label=TRIAL_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="lab-only trial declaration; production scheduler is not wired",
        replaces_commands=(".天机试炼",),
        state_outputs=("module_snapshot", "daily_counter", "reward_delta"),
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
                note="读取试炼 challenge，包括 mode 与题面字段",
            ),
            MiniAppFlowStep(
                key="solve",
                endpoint="local_solver",
                method="LOCAL",
                required_payload_keys=("challenge",),
                sends_init_data=False,
                note="本地按 mode 生成 trialProof；支持点穴、锁阵、忆阵、魔网、观星",
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


def _trial_duration_ms(challenge, *, rng):
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
    return int(rng.randint(lower, upper))


def _trial_event_times(count, duration_ms, *, rng, start_ms=250):
    count = max(0, int(count or 0))
    duration_ms = max(1, int(duration_ms or 1))
    if count <= 0:
        return []
    start_ms = max(0, min(int(start_ms or 0), max(0, duration_ms - 100)))
    usable_ms = max(1, duration_ms - start_ms - 100)
    step_ms = usable_ms / max(1, count)
    times = []
    last = 0
    for index in range(count):
        jitter = int(rng.randint(0, max(1, int(step_ms * 0.18))))
        value = start_ms + int(step_ms * (index + 0.55)) + jitter
        value = min(duration_ms, max(last + 20, value))
        times.append(value)
        last = value
    return times


def _trial_challenge_id(challenge):
    challenge = dict(challenge or {})
    values = [normalize_trial_challenge_id(challenge[key]) for key in ("challengeId", "id") if key in challenge]
    return values[0] if values and len(set(values)) == 1 and values[0] else ""


def _iter_trial_items(value):
    if isinstance(value, dict):
        items = []
        for item_key, item in value.items():
            if isinstance(item, dict):
                normalized = dict(item)
                if not _trial_item_id(normalized):
                    normalized["id"] = str(item_key)
                items.append(normalized)
            else:
                items.append(item)
        return items
    return value or ()


def _trial_item_id(item):
    item = dict(item or {})
    return str(item.get("id") or item.get("key") or item.get("name") or "").strip()


def _lights_out_size(challenge):
    try:
        size = int(round(float(challenge.get("gridSize", challenge.get("grid_size", 4)) or 4)))
    except (TypeError, ValueError, OverflowError):
        size = 4
    return min(5, max(4, size))


def _lights_out_target_state(challenge):
    value = challenge.get("targetState", challenge.get("target_state", 1))
    try:
        return 1 if int(value or 0) else 0
    except (TypeError, ValueError, OverflowError):
        return 1


def _lights_out_neighbors(index, size):
    row, col = divmod(int(index), int(size))
    result = [index]
    if row > 0:
        result.append(index - size)
    if row < size - 1:
        result.append(index + size)
    if col > 0:
        result.append(index - 1)
    if col < size - 1:
        result.append(index + 1)
    return result


def _toggle_lights_out(cells, index, size):
    for target in _lights_out_neighbors(index, size):
        cells[target] = 0 if int(cells[target]) else 1


def solve_lights_out_moves(challenge):
    challenge = dict(challenge or {})
    size = _lights_out_size(challenge)
    target = _lights_out_target_state(challenge)
    raw_cells = list(challenge.get("cells") or ())
    cells = [(1 if int(value or 0) else 0) for value in raw_cells[: size * size]]
    if len(cells) != size * size:
        cells = [target for _ in range(size * size)]

    best_moves = None
    best_cells = None
    for first_row_mask in range(1 << size):
        state = list(cells)
        moves = []
        for col in range(size):
            if first_row_mask & (1 << col):
                moves.append(col)
                _toggle_lights_out(state, col, size)
        for row in range(size - 1):
            for col in range(size):
                index = row * size + col
                if int(state[index]) != target:
                    press = (row + 1) * size + col
                    moves.append(press)
                    _toggle_lights_out(state, press, size)
        if all(int(value) == target for value in state):
            if best_moves is None or len(moves) < len(best_moves):
                best_moves = list(moves)
                best_cells = list(state)
    if best_moves is None:
        raise ValueError("lights-out challenge unsolved")
    return best_moves, best_cells or [target for _ in range(size * size)]


def _build_lights_out_proof(challenge, *, rng):
    challenge = dict(challenge or {})
    challenge_id = _trial_challenge_id(challenge)
    if not challenge_id:
        raise ValueError("challengeId missing")
    moves, final_cells = solve_lights_out_moves(challenge)
    duration_ms = _trial_duration_ms(challenge, rng=rng)
    interval = duration_ms / max(1, len(moves) + 1)
    events = []
    for offset, index in enumerate(moves, start=1):
        jitter = int(rng.randint(0, max(1, int(interval * 0.16))))
        events.append({"index": int(index), "t": min(duration_ms, int(interval * offset) + jitter)})
    return {
        "mode": "tianjiLightsOutV1",
        "challengeId": challenge_id,
        "durationMs": duration_ms,
        "events": events,
        "cells": final_cells,
    }


def _memory_pair_key(card):
    card = dict(card or {})
    for key in ("pair", "symbol", "name"):
        value = str(card.get(key) or "").strip()
        if value:
            return value
    card_id = str(card.get("id") or "").strip()
    return card_id.rsplit("_", 1)[0] if "_" in card_id else card_id


def _build_memory_proof(challenge, *, rng):
    challenge = dict(challenge or {})
    challenge_id = _trial_challenge_id(challenge)
    if not challenge_id:
        raise ValueError("challengeId missing")
    cards = [dict(card) for card in _iter_trial_items(challenge.get("cards")) if isinstance(card, dict)]
    pairs = {}
    for card in cards:
        key = _memory_pair_key(card)
        if key:
            pairs.setdefault(key, []).append(card)

    ordered_cards = []
    for group in pairs.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: int(item.get("index", 0) or 0))
        ordered_cards.extend(group[:2])
    if len(ordered_cards) != len(cards):
        seen = {_trial_item_id(card) for card in ordered_cards}
        for card in cards:
            if _trial_item_id(card) not in seen:
                ordered_cards.append(card)

    try:
        preview_ms = int(challenge.get("previewMs", challenge.get("preview_ms", 0)) or 0)
    except (TypeError, ValueError, OverflowError):
        preview_ms = 0
    duration_ms = max(_trial_duration_ms(challenge, rng=rng), preview_ms + len(ordered_cards) * 260 + 500)
    times = _trial_event_times(len(ordered_cards), duration_ms, rng=rng, start_ms=preview_ms + 180)
    events = []
    for index, card in enumerate(ordered_cards):
        events.append({
            "id": _trial_item_id(card),
            "index": index,
            "t": times[index] if index < len(times) else duration_ms,
        })
    return {
        "mode": "tianjiMemoryV1",
        "challengeId": challenge_id,
        "durationMs": duration_ms,
        "events": events,
        "mismatches": 0,
    }


def _build_stargaze_proof(challenge, *, rng):
    challenge = dict(challenge or {})
    challenge_id = _trial_challenge_id(challenge)
    if not challenge_id:
        raise ValueError("challengeId missing")
    angles = {}
    moves = 0
    for star in _iter_trial_items(challenge.get("stars")):
        if not isinstance(star, dict):
            continue
        star_id = _trial_item_id(star)
        if not star_id:
            continue
        target = star.get("targetAngle", star.get("target_angle", star.get("angle", 0)))
        try:
            target_angle = float(target or 0) % 360
        except (TypeError, ValueError, OverflowError):
            target_angle = 0.0
        angles[star_id] = target_angle
        try:
            current_angle = float(star.get("angle", 0) or 0) % 360
        except (TypeError, ValueError, OverflowError):
            current_angle = 0.0
        if abs(((current_angle - target_angle + 540) % 360) - 180) > 0.1:
            moves += 1
    return {
        "mode": "tianjiStargazeV1",
        "challengeId": challenge_id,
        "durationMs": _trial_duration_ms(challenge, rng=rng),
        "angles": angles,
        "moves": moves,
        "misses": 0,
    }


def _edge_crosses(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def between(p, q, r):
        return (
            min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
            and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
        )

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if o1 == 0 and between(a, c, b):
        return True
    if o2 == 0 and between(a, d, b):
        return True
    if o3 == 0 and between(c, a, d):
        return True
    if o4 == 0 and between(c, b, d):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _planarity_crossing_count(edges, positions):
    count = 0
    normalized = []
    for edge in edges:
        left = str(edge.get("from") or edge.get("source") or "").strip()
        right = str(edge.get("to") or edge.get("target") or "").strip()
        if left and right and left in positions and right in positions and left != right:
            normalized.append((left, right))
    for idx, (a, b) in enumerate(normalized):
        for c, d in normalized[idx + 1:]:
            if len({a, b, c, d}) < 4:
                continue
            if _edge_crosses(positions[a], positions[b], positions[c], positions[d]):
                count += 1
    return count


def _planarity_min_node_distance(positions):
    import math

    points = list((positions or {}).items())
    if len(points) < 2:
        return 999.0
    best = 999.0
    for index, (_left_id, left) in enumerate(points):
        for _right_id, right in points[index + 1:]:
            try:
                distance = math.hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))
            except (TypeError, ValueError, OverflowError, IndexError):
                distance = 0.0
            best = min(best, distance)
    return best


def _planarity_positions_are_safe(positions):
    return _planarity_min_node_distance(positions) >= TRIAL_MINIAPP_PLANARITY_MIN_NODE_DISTANCE


def _circle_positions(node_ids, *, radius=38, center=(50, 50)):
    import math

    total = max(1, len(node_ids))
    result = {}
    for index, node_id in enumerate(node_ids):
        angle = (2 * math.pi * index / total) - (math.pi / 2)
        result[node_id] = (
            center[0] + math.cos(angle) * radius,
            center[1] + math.sin(angle) * radius,
        )
    return result


def _order_planarity_outer_nodes(outer_ids, edges, center_id):
    outer_ids = list(outer_ids or [])
    outer_index = {node_id: index for index, node_id in enumerate(outer_ids)}
    outer_set = set(outer_ids)
    adjacency = {node_id: [] for node_id in outer_ids}
    for edge in edges:
        left = str(edge.get("from") or edge.get("source") or "").strip()
        right = str(edge.get("to") or edge.get("target") or "").strip()
        if center_id in {left, right}:
            continue
        if left in outer_set and right in outer_set:
            adjacency[left].append(right)
            adjacency[right].append(left)
    if not outer_ids or not all(len(adjacency[node_id]) == 2 for node_id in outer_ids):
        return outer_ids
    start = outer_ids[0]
    order = [start]
    previous = ""
    current = start
    while len(order) < len(outer_ids):
        candidates = [node_id for node_id in adjacency[current] if node_id != previous]
        candidates.sort(key=lambda node_id: outer_index.get(node_id, len(outer_ids)))
        next_id = candidates[0] if candidates else ""
        if not next_id or next_id in order:
            break
        previous, current = current, next_id
        order.append(current)
    return order if set(order) == outer_set else outer_ids


def _build_planarity_positions(challenge, *, rng):
    nodes = []
    for raw_node in _iter_trial_items(challenge.get("nodes")):
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        node_id = _trial_item_id(node)
        if not node_id:
            continue
        node["id"] = node_id
        nodes.append(node)
    edges = [dict(edge) for edge in _iter_trial_items(challenge.get("edges")) if isinstance(edge, dict)]
    node_ids = [str(node.get("id") or "").strip() for node in nodes if str(node.get("id") or "").strip()]
    locked_ids = {str(item) for item in (challenge.get("lockedNodeIds") or challenge.get("locked_node_ids") or ())}
    locked_ids.update(str(node.get("id")) for node in nodes if node.get("locked"))
    initial = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        try:
            initial[node_id] = (float(node.get("x", 50) or 50), float(node.get("y", 50) or 50))
        except (TypeError, ValueError, OverflowError):
            initial[node_id] = (50.0, 50.0)

    degree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        left = str(edge.get("from") or edge.get("source") or "").strip()
        right = str(edge.get("to") or edge.get("target") or "").strip()
        if left in degree:
            degree[left] += 1
        if right in degree:
            degree[right] += 1

    unlocked = [node_id for node_id in node_ids if node_id not in locked_ids]
    best = dict(initial)
    best_count = _planarity_crossing_count(edges, best)
    best_distance = _planarity_min_node_distance(best)
    if best_count == 0 and _planarity_positions_are_safe(best):
        return best, 0

    # Wheel-like graphs are common in live trials. Preserve the actual outer
    # cycle order instead of sorting by degree/id, which can create crossings
    # and leave the random fallback with a zero-crossing but too-close layout.
    for center_id, center_degree in sorted(degree.items(), key=lambda item: item[1], reverse=True):
        if center_degree < 3 or center_id in locked_ids:
            continue
        outer_ids = [node_id for node_id in node_ids if node_id != center_id]
        outer_order = _order_planarity_outer_nodes(outer_ids, edges, center_id)
        candidate = dict(initial)
        for node_id, position in _circle_positions(outer_order, radius=36).items():
            if node_id not in locked_ids:
                candidate[node_id] = position
        candidate[center_id] = (50.0, 50.0)
        count = _planarity_crossing_count(edges, candidate)
        distance = _planarity_min_node_distance(candidate)
        if count < best_count or (count == best_count and distance > best_distance):
            best = candidate
            best_count = count
            best_distance = distance
        if count == 0 and _planarity_positions_are_safe(candidate):
            return candidate, 0

    candidates = []
    if unlocked:
        candidates.append((None, list(unlocked)))
        for center_id, _degree in sorted(degree.items(), key=lambda item: item[1], reverse=True)[:3]:
            if center_id in unlocked:
                candidates.append((center_id, [node_id for node_id in unlocked if node_id != center_id]))

    for center_id, ring_ids in candidates:
        ordered = sorted(ring_ids, key=lambda node_id: (-degree.get(node_id, 0), node_id))
        for attempt in range(160):
            trial_order = list(ordered)
            if attempt:
                rng.shuffle(trial_order)
            candidate = dict(initial)
            candidate.update(_circle_positions(trial_order))
            if center_id:
                candidate[center_id] = (50.0, 50.0)
            count = _planarity_crossing_count(edges, candidate)
            distance = _planarity_min_node_distance(candidate)
            if count < best_count or (count == best_count and distance > best_distance):
                best = candidate
                best_count = count
                best_distance = distance
                if count == 0 and _planarity_positions_are_safe(candidate):
                    return best, best_count

    for _attempt in range(800):
        candidate = dict(best)
        for node_id in unlocked:
            candidate[node_id] = (rng.uniform(8, 92), rng.uniform(8, 92))
        count = _planarity_crossing_count(edges, candidate)
        distance = _planarity_min_node_distance(candidate)
        if count < best_count or (count == best_count and distance > best_distance):
            best = candidate
            best_count = count
            best_distance = distance
            if count == 0 and _planarity_positions_are_safe(candidate):
                return best, best_count
    return best, best_count


def _build_planarity_proof(challenge, *, rng):
    challenge = dict(challenge or {})
    challenge_id = _trial_challenge_id(challenge)
    if not challenge_id:
        raise ValueError("challengeId missing")
    positions, crossing_count = _build_planarity_positions(challenge, rng=rng)
    if not positions:
        raise ValueError("planarity challenge has no valid nodes")
    if crossing_count:
        raise ValueError("planarity challenge unsolved")
    if not _planarity_positions_are_safe(positions):
        raise ValueError("planarity nodes too close")
    serializable_positions = {
        node_id: {"x": round(float(point[0]), 3), "y": round(float(point[1]), 3)}
        for node_id, point in positions.items()
    }
    unlocked_count = len([
        node for node in _iter_trial_items(challenge.get("nodes"))
        if isinstance(node, dict) and not node.get("locked")
    ])
    return {
        "mode": "tianjiPlanarityV1",
        "challengeId": challenge_id,
        "durationMs": _trial_duration_ms(challenge, rng=rng),
        "positions": serializable_positions,
        "moves": max(1, unlocked_count),
        "misses": 0,
    }


def build_trial_proof(challenge, *, rng=None):
    rng = rng or random
    challenge = dict(challenge or {})
    challenge_id = _trial_challenge_id(challenge)
    if not challenge_id:
        raise ValueError("challengeId missing")
    mode = str(challenge.get("mode") or challenge.get("type") or "").strip()
    if mode == "tianjiLightsOutV1":
        return _build_lights_out_proof(challenge, rng=rng)
    if mode == "tianjiMemoryV1":
        return _build_memory_proof(challenge, rng=rng)
    if mode == "tianjiStargazeV1":
        return _build_stargaze_proof(challenge, rng=rng)
    if mode == "tianjiPlanarityV1":
        return _build_planarity_proof(challenge, rng=rng)

    sequence = list(challenge.get("sequence") or challenge.get("answer") or challenge.get("solution") or ())
    raw_points = challenge.get("points") or ()
    trap_ids = {str(item) for item in (challenge.get("trapIds") or ())}
    point_map = {}
    for point in _iter_trial_items(raw_points):
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("id") or point.get("key") or point.get("name") or "").strip()
        if point_id:
            point_map[point_id] = dict(point)
    taps = []
    trap_hits = 0
    for raw_point_id in sequence:
        point_id = str(raw_point_id).strip()
        if not point_id:
            continue
        point = point_map.get(point_id) or {}
        if point_id in trap_ids:
            trap_hits += 1
            continue
        taps.append({
            "id": point_id,
            "x": point.get("x", 50),
            "y": point.get("y", 50),
        })

    duration_ms = _trial_duration_ms(challenge, rng=rng)
    event_times = _trial_event_times(len(taps), duration_ms, rng=rng)
    proof = {
        "mode": mode or "tianjiMeridianV1",
        "challengeId": challenge_id,
        "durationMs": duration_ms,
        "events": [
            {"id": str(tap["id"]), "index": index, "t": event_times[index] if index < len(event_times) else duration_ms}
            for index, tap in enumerate(taps)
        ],
        "moves": len(taps),
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


def _trial_digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _trial_round_key(pending):
    return _trial_digest(pending["entry_key"] + ":" + pending["challenge_key"])


class _TrialRequestContext:
    def __init__(self, *, transport, adapter, init_data, player_id, sleeper, capture_sink,
                 capture_source, events, request_budget, operation_check, checkpoint):
        self.transport, self.adapter, self.init_data = transport, adapter, init_data
        self.player_id, self.sleeper = player_id, sleeper
        self.capture_sink, self.capture_source, self.events = capture_sink, capture_source, events
        self.request_budget, self.operation_check = request_budget, operation_check
        self.checkpoint, self.sequence, self.checkpoint_error = checkpoint, 0, ""
        self.pending, self.receipts, self.resolution = {}, [], {}
        self.dispatched, self.retry_after_sec = False, 0
        self.status, self.error = "running", ""

    def evidence(self):
        return {
            "action_dispatched": self.dispatched, "pending": deepcopy(self.pending),
            "outcome_unknown": bool(self.pending and self.pending["action"] not in {"challenge", "entry"}),
            "round_receipts": deepcopy(self.receipts), "retry_after_sec": self.retry_after_sec,
            "request_resolution": deepcopy(self.resolution),
            "status": self.status, "error": self.error,
        }

    def emit(self, phase, *, pending=None, force=False):
        if self.checkpoint_error and not force:
            return False
        if self.checkpoint is None:
            return True
        record = self.evidence()
        record.update(version=1, phase=phase, sequence=self.sequence + 1)
        if pending is not None:
            record.update(pending=deepcopy(pending), outcome_unknown=True)
        try:
            accepted = self.checkpoint(record)
            if inspect.iscoroutine(accepted):
                accepted.close()
            if accepted is not True:
                raise ValueError("trial_checkpoint_not_acknowledged")
        except Exception:
            self.checkpoint_error = "trial_checkpoint_failed"
            return False
        self.sequence += 1
        return True

    def request(self, endpoint, *, token, payload=None):
        if self.checkpoint_error:
            raise MiniAppRequestAborted(self.checkpoint_error)
        previous = deepcopy(self.pending)
        sent, intent_saved = False, False
        response_retry_after = 0
        proof = (payload or {}).get("trialProof") or {}
        pending = {"action": endpoint, "entry_key": _trial_digest(token),
                   "challenge_key": _trial_digest(proof["challengeId"]) if endpoint == "finish" else ""}

        def dispatch(request):
            nonlocal sent, intent_saved, response_retry_after
            self.resolution = {}
            if not self.emit("intent", pending=pending):
                raise MiniAppRequestAborted(self.checkpoint_error)
            intent_saved = True
            require_miniapp_operation(self.operation_check)
            sent, self.dispatched, self.pending = True, True, pending
            response = self.transport(request)
            response_retry_after = _response_retry_after_sec(response)
            return response

        result = execute_miniapp_http_request(
            build_trial_miniapp_request(endpoint, token=token, init_data=self.init_data,
                                        player_id=self.player_id, payload=payload, adapter=self.adapter),
            dispatch, sleeper=self.sleeper, backoff_sec=(), capture_sink=self.capture_sink,
            capture_source=self.capture_source, step_key=endpoint, request_budget=self.request_budget,
            operation_check=self.operation_check,
        )
        if sent and result.error_type == "app" and result.data.get("ok") is False and (
            200 <= result.status_code < 500 and result.status_code not in {408, 425, 429}
        ):
            self.pending = previous
            self.resolution = {"kind": "rejected", "request": pending}
        elif intent_saved and not sent:
            self.resolution = {"kind": "not_sent", "request": pending}
        self.retry_after_sec = max(self.retry_after_sec, result.retry_after_sec,
                                   response_retry_after if result.status_code in {408, 425} else 0)
        _append_http_event(self.events, endpoint, result)
        self.events[-1]["dispatched"] = sent
        if not result.ok and (intent_saved or result.retry_after_sec):
            self.emit("response")
        return result

    def challenge(self, token, challenge):
        challenge_id = _trial_challenge_id(challenge)
        if challenge_id:
            self.resolution = {}
            self.pending = {"action": "challenge", "entry_key": _trial_digest(token),
                            "challenge_key": _trial_digest(challenge_id)}
            self.emit("response")

    def next_token(self, token):
        self.resolution = {}
        self.pending = {"action": "entry", "entry_key": _trial_digest(token), "challenge_key": ""}
        self.emit("response")

    def settled(self, token, challenge_id, data):
        expected = {"action": "finish", "entry_key": _trial_digest(token),
                    "challenge_key": _trial_digest(challenge_id)}
        if self.pending != expected:
            raise ValueError("trial_receipt_operation_mismatch")
        key = _trial_round_key(expected)
        if key in {item["round_key"] for item in self.receipts}:
            raise ValueError("trial_receipt_already_recorded")
        self.receipts.append({"round_key": key, "data": deepcopy(data)})
        self.pending = {}
        self.resolution = {}
        self.emit("settled")




def _challenge_from_start(data):
    data = dict(data or {})
    trial = data.get("trial") if isinstance(data.get("trial"), dict) else {}
    for container in (data, data.get("data"), data.get("result"), trial):
        if not isinstance(container, dict):
            continue
        nested_trial = container.get("trial") if isinstance(container.get("trial"), dict) else {}
        if nested_trial and not trial:
            trial = nested_trial
        challenge = container.get("challenge") if isinstance(container.get("challenge"), dict) else {}
        if challenge:
            return challenge, trial or nested_trial
        if nested_trial and isinstance(nested_trial.get("challenge"), dict):
            return nested_trial["challenge"], nested_trial
    challenge = data.get("challenge") if isinstance(data.get("challenge"), dict) else {}
    return challenge, trial


def _challenge_from_finish(data):
    data = dict(data or {})
    for container in (data, data.get("data"), data.get("result")):
        if not isinstance(container, dict):
            continue
        challenge = (
            container.get("nextChallenge")
            or container.get("next_challenge")
            or container.get("challenge")
        )
        if isinstance(challenge, dict) and challenge:
            trial = (
                container.get("nextTrial")
                or container.get("next_trial")
                or container.get("trial")
                or {}
            )
            return challenge, trial if isinstance(trial, dict) else {}
    return {}, {}


def _solve_and_finish_trial_challenge(
    *,
    challenge,
    token,
    player_id,
    rng,
    sleeper,
    events,
    operation_check,
    requests,
):
    require_miniapp_operation(operation_check)
    try:
        proof = build_trial_proof(challenge, rng=rng)
    except Exception as exc:
        events.append({
            "step": "solve",
            "ok": False,
            "mode": sanitize_webapp_secret_text(challenge.get("mode") or "", limit=80),
            "error": sanitize_webapp_secret_text(exc),
        })
        return {
            "ok": False,
            "status": "solve_failed",
            "error": sanitize_webapp_secret_text(exc),
            "data": {"challenge_keys": sorted(str(key) for key in challenge)},
            "proof": {},
            "finish_data": {},
        }
    events.append({
        "step": "solve",
        "ok": True,
        "mode": proof["mode"],
        "sequence_len": len(proof.get("sequence") or ()),
        "trapHits": proof.get("trapHits", 0),
        "durationMs": proof["durationMs"],
    })
    require_miniapp_operation(operation_check)
    if sleeper is not None:
        sleeper(float(proof["durationMs"]) / 1000.0)
    require_miniapp_operation(operation_check)

    finish_result = requests.request("finish", token=token, payload={"trialProof": proof})
    if not finish_result.ok:
        status = _trial_http_failure_status(finish_result)
        return {
            "ok": False,
            "status": status,
            "error": sanitize_webapp_secret_text(finish_result.error),
            "data": {},
            "proof": proof,
            "finish_data": finish_result.data if isinstance(finish_result.data, dict) else {},
        }

    finish_data = finish_result.data if isinstance(finish_result.data, dict) else {}
    receipt = parse_trial_finish_receipt(
        finish_data, challenge_id=_trial_challenge_id(challenge), player_id=player_id,
    )
    if not receipt["confirmed"]:
        return {
            "ok": False, "status": "result_unconfirmed", "error": receipt["error"],
            "data": {}, "proof": proof, "finish_data": {},
        }
    requests.settled(token, _trial_challenge_id(challenge), receipt["result"])
    return {
        "ok": True,
        "status": "settled",
        "error": "",
        "data": receipt["result"],
        "proof": proof,
        "finish_data": receipt["body"],
        "quota": receipt["quota"],
        "quota_error": receipt["quota_error"],
        "material_error": receipt["material_error"],
    }


def run_trial_miniapp_lab_flow(
    *,
    token,
    init_data,
    player_id=None,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    request_budget=None,
    checkpoint=None,
):
    result = run_trial_miniapp_loop_lab_flow(
        token=token, init_data=init_data, player_id=player_id,
        transport=transport, adapter=adapter, rng=rng, sleeper=sleeper,
        max_rounds=1, capture_sink=capture_sink, capture_source=capture_source,
        operation_check=operation_check, request_budget=request_budget, checkpoint=checkpoint,
    )
    # Keep the single-round API shape while sharing the worker lifecycle.
    data = dict(result.get("data") or {})
    settlements = data.pop("results", [])
    data.pop("settled_count", None)
    result["data"] = dict(settlements[0]) if settlements else data
    result["events"] = [event for event in result["events"] if event.get("step") != "round"]
    return result


def _extract_next_trial_token(data):
    data = dict(data or {})
    containers = [item for item in (data, data.get("data"), data.get("result"), data.get("trial"))
                  if isinstance(item, dict)]
    containers += [item["trial"] for item in containers if isinstance(item.get("trial"), dict)]
    values = [item[key] for item in containers for key in ("token", "nextToken", "trialToken") if key in item]
    if not values:
        return ""
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("trial_next_token_invalid")
    tokens = {value.strip() for value in values}
    if len(tokens) != 1:
        raise ValueError("trial_next_token_invalid")
    return next(iter(tokens))


def _trial_http_failure_status(result):
    if result.error_type == "operation_cancelled":
        return "cancelled"
    return classify_trial_miniapp_error(result.error)


def run_trial_miniapp_loop_lab_flow(
    *,
    token,
    init_data,
    player_id=None,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    max_rounds=99,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    request_budget=None,
    checkpoint=None,
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
    proof = {}
    completed_challenges = set()
    if request_budget is None:
        request_budget = MiniAppRequestBudget(adapter.request_policy, sleeper=sleeper)
    requests = _TrialRequestContext(
        transport=transport, adapter=adapter, init_data=init_data, player_id=player_id,
        sleeper=sleeper, capture_sink=capture_sink, capture_source=capture_source,
        events=events, request_budget=request_budget, operation_check=operation_check, checkpoint=checkpoint,
    )

    def finish(status, *, error="", extra=None):
        requests.status, requests.error = status, sanitize_webapp_secret_text(error)
        requests.emit("complete", force=True)
        if requests.checkpoint_error:
            status = "partial" if results else "persistence_pending"
            error = requests.checkpoint_error
        result = _flow_result(
            bool(results), status, error=error, events=events,
            data={"results": results, "settled_count": len(results), **dict(extra or {})},
            proof=proof if max_rounds == 1 else None,
        )
        evidence = requests.evidence()
        evidence.pop("status")
        evidence.pop("error")
        return {**result, **evidence, "checkpoint_sequence": requests.sequence,
                "checkpoint_error": requests.checkpoint_error}

    def stop(status, *, error="", extra=None):
        if results and status not in {"cancelled", "next_unavailable"}:
            status = "partial"
        return finish(status, error=error, extra=extra)

    def request(endpoint):
        return requests.request(endpoint, token=current_token)

    try:
        start_result = request("start")
        if not start_result.ok:
            return stop(_trial_http_failure_status(start_result), error=start_result.error)
        challenge, trial = _challenge_from_start(start_result.data)
        if not challenge:
            return stop("not_ready", extra={"trial_keys": sorted(trial)})
        requests.challenge(current_token, challenge)

        for round_index in range(1, max_rounds + 1):
            challenge_id = _trial_challenge_id(challenge)
            if requests.checkpoint_error:
                return stop("persistence_pending", error=requests.checkpoint_error)
            if challenge_id in completed_challenges:
                return stop("duplicate_challenge", error="trial_challenge_already_settled")
            round_result = _solve_and_finish_trial_challenge(
                challenge=challenge, token=current_token, player_id=player_id,
                rng=rng, sleeper=sleeper, events=events, operation_check=operation_check,
                requests=requests,
            )
            proof = round_result.get("proof") or {}
            events.append({
                "step": "round", "round": round_index,
                "ok": bool(round_result.get("ok")),
                "status": str(round_result.get("status") or ""),
            })
            if not round_result.get("ok"):
                return stop(
                    round_result.get("status") or "failed", error=round_result.get("error", ""),
                    extra=round_result.get("data"),
                )

            # Retain the returned settlement before checking any later admission.
            results.append(dict(round_result.get("data") or {}))
            completed_challenges.add(challenge_id)
            if requests.checkpoint_error:
                return stop("persistence_pending", error=requests.checkpoint_error)
            finish_data = dict(round_result.get("finish_data") or {})
            if round_result.get("material_error"):
                return stop("material_unconfirmed", error=round_result["material_error"])
            quota_error = round_result.get("quota_error") or ""
            if quota_error:
                events.append({"step": "quota", "ok": False, "error": quota_error})
            if round_index == max_rounds:
                break
            if quota_error:
                return stop("quota_invalid", error=quota_error)
            if (round_result.get("quota") or {}).get("remaining") == 0:
                break
            next_challenge, _next_trial = _challenge_from_finish(finish_data)
            if next_challenge:
                challenge = next_challenge
                requests.challenge(current_token, challenge)
                continue

            next_result = request("next")
            if not next_result.ok:
                status = _trial_http_failure_status(next_result)
                if status == "daily_limit":
                    break
                return stop(
                    "cancelled" if status == "cancelled" else "next_unavailable",
                    error=next_result.error,
                    extra={"next_error": sanitize_webapp_secret_text(next_result.error)},
                )
            next_token = _extract_next_trial_token(next_result.data)
            next_challenge, _next_trial = _challenge_from_start(next_result.data)
            if next_challenge:
                if next_token:
                    current_token = next_token
                challenge = next_challenge
                requests.challenge(current_token, challenge)
                continue
            if not next_token:
                return stop("next_unavailable", error="next trial missing")
            current_token = next_token
            requests.next_token(current_token)
            start_result = request("start")
            if not start_result.ok:
                status = _trial_http_failure_status(start_result)
                if status == "daily_limit":
                    break
                return stop(status, error=start_result.error)
            challenge, trial = _challenge_from_start(start_result.data)
            if not challenge:
                return stop("next_unavailable", error="next challenge missing")
            requests.challenge(current_token, challenge)
    except MiniAppRequestAborted as exc:
        return stop("cancelled", error=exc)
    except Exception as exc:
        return stop("failed", error=exc)
    return finish("settled")


async def run_trial_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    player_id=None,
    max_rounds=1,
    transport=None,
    sleeper=None,
    adapter=None,
    capture_sink=None,
    capture_source="",
    operation_check=None,
    checkpoint=None,
):
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        player_id = normalize_trial_player_id(player_id)
        require_miniapp_operation(operation_check)
        init_data = str(init_data or "").strip() or await request_trial_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
            operation_check=operation_check,
        )
        require_miniapp_operation(operation_check)

        def run(operation):
            loop = int(max_rounds or 1) > 1
            runner = run_trial_miniapp_loop_lab_flow if loop else run_trial_miniapp_lab_flow
            kwargs = {"max_rounds": max_rounds} if loop else {}
            return runner(
                token=token, init_data=init_data, player_id=player_id,
                transport=transport or build_pooled_miniapp_transport(
                    adapter_key=adapter.game_key, identity_id=identity_id,
                    timeout=TRIAL_MINIAPP_HTTP_TIMEOUT, operation_check=operation.check,
                ),
                adapter=adapter, sleeper=operation.sleep,
                capture_sink=capture_sink, capture_source=capture_source,
                operation_check=operation.check, checkpoint=checkpoint, **kwargs,
            )

        return await run_miniapp_blocking_flow(run, operation_check=operation_check, sleeper=sleeper)
    except MiniAppFlowCancelled:
        raise
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(_flow_result(False, "cancelled", error="authorization_cancelled")) from None
    except MiniAppRequestAborted as exc:
        return _flow_result(False, "cancelled", error=exc)
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
