import asyncio
import random
import time

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
    iter_webapp_entry_links,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)
from .miniapp_common import (
    append_http_event as _append_http_event,
    build_pooled_miniapp_transport,
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
    request_payload = {"token": str(token or "").strip()}
    if player_id not in (None, ""):
        request_payload["playerId"] = player_id
    request_payload.update(dict(payload or {}))
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
    return str(challenge.get("challengeId") or challenge.get("id") or "").strip()


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


def _trial_result_from_finish(data):
    data = dict(data or {})
    return data.get("result") if isinstance(data.get("result"), dict) else data


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


def _finish_remaining_count(data):
    data = dict(data or {})
    containers = (
        data.get("dailyProgress"),
        data.get("nextTrial"),
        data.get("trial"),
        data.get("result"),
        data,
    )
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "remaining",
            "remainingToday",
            "dailyRemaining",
            "remaining_today",
            "remainingCount",
        ):
            if key not in container:
                continue
            try:
                value = int(float(container.get(key)))
            except (TypeError, ValueError, OverflowError):
                continue
            if value >= 0:
                return value
    return None


def _solve_and_finish_trial_challenge(
    *,
    challenge,
    token,
    init_data,
    player_id,
    transport,
    adapter,
    rng,
    sleeper,
    capture_sink,
    capture_source,
    events,
):
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
    if sleeper is not None:
        sleeper(float(proof["durationMs"]) / 1000.0)

    finish_request = build_trial_miniapp_request(
        "finish",
        token=token,
        init_data=init_data,
        player_id=player_id,
        payload={"trialProof": proof},
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
        status = classify_trial_miniapp_error(finish_result.error)
        return {
            "ok": False,
            "status": status,
            "error": sanitize_webapp_secret_text(finish_result.error),
            "data": {},
            "proof": proof,
            "finish_data": finish_result.data if isinstance(finish_result.data, dict) else {},
        }

    finish_data = finish_result.data if isinstance(finish_result.data, dict) else {}
    return {
        "ok": True,
        "status": "settled",
        "error": "",
        "data": _trial_result_from_finish(finish_data),
        "proof": proof,
        "finish_data": finish_data,
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
):
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_trial_miniapp_request(
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
        status = classify_trial_miniapp_error(start_result.error)
        return _flow_result(False, status, error=start_result.error, events=events)

    challenge, trial = _challenge_from_start(start_result.data)
    if not challenge:
        return _flow_result(False, "not_ready", data={"trial_keys": sorted(trial)}, events=events)
    round_result = _solve_and_finish_trial_challenge(
        challenge=challenge,
        token=token,
        init_data=init_data,
        player_id=player_id,
        transport=transport,
        adapter=adapter,
        rng=rng,
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        events=events,
    )
    return _flow_result(
        round_result["ok"],
        round_result["status"],
        error=round_result.get("error", ""),
        data=round_result.get("data") or {},
        events=events,
        proof=round_result.get("proof") or {},
    )


def _extract_next_trial_token(data):
    data = dict(data or {})
    token = str(data.get("token") or data.get("nextToken") or data.get("trialToken") or "").strip()
    return token


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

    start_request = build_trial_miniapp_request(
        "start",
        token=current_token,
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
        status = classify_trial_miniapp_error(start_result.error)
        return _flow_result(False, status, error=start_result.error, events=events)

    challenge, trial = _challenge_from_start(start_result.data)
    if not challenge:
        return _flow_result(False, "not_ready", data={"trial_keys": sorted(trial)}, events=events)

    for round_index in range(1, max_rounds + 1):
        round_result = _solve_and_finish_trial_challenge(
            challenge=challenge,
            token=current_token,
            init_data=init_data,
            player_id=player_id,
            transport=transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source,
            events=events,
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
        finish_data = dict(round_result.get("finish_data") or {})
        remaining = _finish_remaining_count(finish_data)
        if remaining == 0:
            break
        next_challenge, _next_trial = _challenge_from_finish(finish_data)
        if next_challenge:
            challenge = next_challenge
            continue

        next_request = build_trial_miniapp_request(
            "next",
            token=current_token,
            init_data=init_data,
            player_id=player_id,
            adapter=adapter,
        )
        next_result = execute_miniapp_http_request(
            next_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="next",
        )
        _append_http_event(events, "next", next_result)
        if not next_result.ok:
            status = classify_trial_miniapp_error(next_result.error)
            if status == "daily_limit":
                break
            data = {
                "results": results,
                "settled_count": len(results),
                "next_error": sanitize_webapp_secret_text(next_result.error),
            }
            return _flow_result(True, "next_unavailable", data=data, events=events)

        next_token = _extract_next_trial_token(next_result.data)
        next_challenge, _next_trial = _challenge_from_start(next_result.data)
        if next_challenge:
            if next_token:
                current_token = next_token
            challenge = next_challenge
            continue
        if not next_token:
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(True, "next_unavailable", data=data, events=events)
        current_token = next_token
        start_request = build_trial_miniapp_request(
            "start",
            token=current_token,
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
            status = classify_trial_miniapp_error(start_result.error)
            if status == "daily_limit":
                break
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(True, "partial", error=start_result.error, data=data, events=events)
        challenge, trial = _challenge_from_start(start_result.data)
        if not challenge:
            data = {"results": results, "settled_count": len(results)}
            return _flow_result(True, "next_unavailable", data=data, events=events)

    data = {"results": results, "settled_count": len(results)}
    return _flow_result(True, "settled", data=data, events=events)


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
):
    adapter = adapter or build_trial_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_trial_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        runner = run_trial_miniapp_loop_lab_flow if int(max_rounds or 1) > 1 else run_trial_miniapp_lab_flow
        kwargs = {
            "token": token,
            "init_data": init_data,
            "player_id": player_id,
            "transport": transport or build_pooled_miniapp_transport(
                adapter_key=adapter.game_key,
                identity_id=identity_id,
                timeout=TRIAL_MINIAPP_HTTP_TIMEOUT,
            ),
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
