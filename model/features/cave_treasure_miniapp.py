import asyncio
from collections import deque
import json
import os
import random
import re
import time

import requests
from telethon import functions

from ..config import CMD_YUANYING, CMD_YUANYING_STATUS, MESSAGES_DIR, TG_REQUESTS_PROXIES
from ..runtime import _get_identity_client_with_account, account_rpc_slot
from ..state import get_game_bot_ids
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


CAVE_TREASURE_MINIAPP_GAME_KEY = "cave_treasure"
CAVE_TREASURE_MINIAPP_LABEL = "洞府寻宝"
CAVE_TREASURE_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
CAVE_TREASURE_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS = (
    r"hantianzun\d{2}_bot",
)
CAVE_TREASURE_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-dwelling/"
CAVE_TREASURE_MINIAPP_ENDPOINTS = {
    "start": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}start",
    "details": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}details",
    "section": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}section",
    "external": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}external",
    "command_center": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}command-center",
    "deep_seclusion": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}deep-seclusion",
    "journey": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}journey",
    "small_world": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}small-world",
    "hunt": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt",
    "hunt_reveal": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt/reveal",
    "hunt_settle": f"{CAVE_TREASURE_MINIAPP_API_PATH_PREFIX}hunt/settle",
}
CAVE_TREASURE_MINIAPP_START_PARAM_PATTERN = r"(?:df_)?[A-Za-z0-9_-]{4,160}"
CAVE_TREASURE_MINIAPP_HTTP_TIMEOUT = (5, 20)
CAVE_TREASURE_SENDABLE_ACTIONS = {
    "enter",
    "search",
    "settle",
}
CAVE_DEEP_SECLUSION_ACTIONS = {
    "status",
    "settle",
    "start",
    "force",
}
CAVE_SMALL_WORLD_ACTIONS = frozenset({
    "collect",
    "manifest",
    "soothe",
    "miracle_relief",
    "miracle_sermon",
    "barrier",
    "refine_shenshi",
    "upgrade_temple",
    "foster_beast",
    "recall_beast",
})
CAVE_JOURNEY_ACTIONS = frozenset({"wild_experience", "set_encounter_mode"})
CAVE_WILD_EXPERIENCE_MODES = frozenset({"cautious", "balanced", "deep"})
CAVE_ENCOUNTER_MODES = frozenset({"cautious", "balanced", "plunder", "off"})
CAVE_TIANJIGE_ALLOWED_COMMANDS = frozenset({CMD_YUANYING, CMD_YUANYING_STATUS})
CAVE_EXTERNAL_ACTIONS = frozenset({"trial", "tianji_trial", "fishing", "pagoda"})

_RATIO_RE = re.compile(r"(?P<label>神识|出手|次数|游戏|局数)?\s*[:：]?\s*(?P<a>\d+)\s*/\s*(?P<b>\d+)")
_TARGET_RE = re.compile(r"(?:第|#)?\s*(?P<target>\d{1,2})\s*(?:个|号|处|位)")


def _recent_game_bot_id_for_username(username, *, max_lines=20000):
    username = str(username or "").strip().lstrip("@").casefold()
    if not username:
        return 0
    allowed_ids = set(get_game_bot_ids())
    try:
        names = os.listdir(MESSAGES_DIR)
    except OSError:
        return 0
    paths = sorted(
        (
            os.path.join(MESSAGES_DIR, name)
            for name in names if name.endswith(".log") and name[:4].isdigit()
        ),
        reverse=True,
    )[:2]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = deque(handle, maxlen=max(1, int(max_lines or 1)))
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            sender_id = int(payload.get("sender_id") or 0)
            if (
                str(payload.get("sender_username") or "").casefold() == username
                and bool(payload.get("sender_is_bot"))
                and sender_id in allowed_ids
            ):
                return sender_id
    return 0


def _recent_game_bot_usernames(*, exclude=(), max_lines=20000, limit=8):
    excluded = {str(item or "").strip().lstrip("@").casefold() for item in exclude}
    allowed_ids = set(get_game_bot_ids())
    try:
        names = os.listdir(MESSAGES_DIR)
    except OSError:
        return []
    paths = sorted(
        (
            os.path.join(MESSAGES_DIR, name)
            for name in names if name.endswith(".log") and name[:4].isdigit()
        ),
        reverse=True,
    )[:2]
    result = []
    seen = set(excluded)
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = deque(handle, maxlen=max(1, int(max_lines or 1)))
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            username = str(payload.get("sender_username") or "").strip().lstrip("@").casefold()
            sender_id = int(payload.get("sender_id") or 0)
            if (
                username in seen
                or not re.fullmatch(r"hantianzun\d{2}_bot", username, flags=re.IGNORECASE)
                or not bool(payload.get("sender_is_bot"))
                or sender_id not in allowed_ids
            ):
                continue
            seen.add(username)
            result.append(username)
            if len(result) >= max(1, int(limit or 1)):
                return result
    return result


def build_cave_treasure_miniapp_adapter(
    *,
    api_base_url=CAVE_TREASURE_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=CAVE_TREASURE_MINIAPP_GAME_KEY,
        label=CAVE_TREASURE_MINIAPP_LABEL,
        bot_username=bot_username,
        allowed_bot_username_patterns=CAVE_TREASURE_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(CAVE_TREASURE_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(CAVE_TREASURE_MINIAPP_ENDPOINTS),
        start_param_pattern=CAVE_TREASURE_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_cave_treasure_miniapp_request(endpoint, *, token, init_data_session=None, init_data="", payload=None, adapter=None):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    request_payload = {"token": str(token or "").strip()}
    request_payload.update(dict(payload or {}))
    return build_miniapp_http_request(
        adapter,
        endpoint,
        request_payload,
        init_data_session=init_data_session,
        init_data=init_data,
    )


def normalize_cave_tianjige_command(command):
    normalized = re.sub(r"\s+", " ", str(command or "").strip())
    if normalized not in CAVE_TIANJIGE_ALLOWED_COMMANDS:
        raise ValueError("洞府天机阁自动化仅允许 .元婴状态 / .元婴出窍")
    return normalized


def build_cave_tianjige_command_request(
    command,
    *,
    token,
    init_data_session=None,
    init_data="",
    player_id=None,
    adapter=None,
):
    """Build a strictly whitelisted Tianjige command-center request."""
    normalized_command = normalize_cave_tianjige_command(command)
    return build_cave_treasure_miniapp_request(
        "command_center",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload={
            "command": normalized_command,
            **({"playerId": int(player_id)} if player_id not in (None, "") else {}),
        },
        adapter=adapter,
    )


def normalize_cave_external_action(action):
    normalized = re.sub(r"\s+", "_", str(action or "").strip().lower())
    if normalized not in CAVE_EXTERNAL_ACTIONS:
        raise ValueError("洞府外府动作不在白名单")
    return normalized


def build_cave_external_action_request(
    action,
    *,
    token,
    player_id,
    init_data_session=None,
    init_data="",
    adapter=None,
):
    return build_cave_treasure_miniapp_request(
        "external",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload={
            "action": normalize_cave_external_action(action),
            "playerId": str(player_id or "").strip(),
        },
        adapter=adapter,
    )


def normalize_cave_small_world_action(action):
    normalized = re.sub(r"\s+", "_", str(action or "").strip().lower())
    if normalized not in CAVE_SMALL_WORLD_ACTIONS:
        raise ValueError("洞府小世界动作不在白名单")
    return normalized


def normalize_cave_journey_action(action):
    normalized = re.sub(r"\s+", "_", str(action or "").strip().lower())
    if normalized not in CAVE_JOURNEY_ACTIONS:
        raise ValueError("洞府游历动作不在白名单")
    return normalized


def normalize_cave_wild_experience_mode(mode):
    normalized = str(mode or "").strip().lower()
    if normalized not in CAVE_WILD_EXPERIENCE_MODES:
        raise ValueError("野外历练策略不在白名单")
    return normalized


def build_cave_journey_action_request(
    action,
    *,
    mode,
    token,
    player_id=None,
    init_data_session=None,
    init_data="",
    adapter=None,
):
    normalized_action = normalize_cave_journey_action(action)
    normalized_mode = (
        normalize_cave_wild_experience_mode(mode)
        if normalized_action == "wild_experience"
        else str(mode or "").strip().lower()
    )
    if normalized_action == "set_encounter_mode" and normalized_mode not in CAVE_ENCOUNTER_MODES:
        raise ValueError("天机遭遇策略不在白名单")
    return build_cave_treasure_miniapp_request(
        "journey",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload={
            "action": normalized_action,
            "mode": normalized_mode,
            **({"playerId": int(player_id)} if player_id not in (None, "") else {}),
        },
        adapter=adapter,
    )


def build_cave_small_world_action_request(
    action,
    *,
    token,
    init_data_session=None,
    init_data="",
    payload=None,
    adapter=None,
):
    normalized_action = normalize_cave_small_world_action(action)
    action_payload = {"action": normalized_action}
    action_payload.update(dict(payload or {}))
    return build_cave_treasure_miniapp_request(
        "small_world",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload=action_payload,
        adapter=adapter,
    )


def _iter_event_buttons(event, *, message_text=""):
    yield from iter_webapp_entry_links(event, message_text=message_text)


def summarize_cave_treasure_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = CAVE_TREASURE_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def extract_cave_treasure_miniapp_launch(event, *, message_text=""):
    adapter = build_cave_treasure_miniapp_adapter()
    for button_text, url in _iter_event_buttons(event, message_text=message_text):
        if not url:
            continue
        summary = summarize_cave_treasure_entry(url, button_text=button_text, message_text=message_text)
        if not summary or summary.get("game_hint") != CAVE_TREASURE_MINIAPP_GAME_KEY:
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


def build_cave_treasure_launch_args(url, *, start_param="", bot_username=CAVE_TREASURE_MINIAPP_DEFAULT_BOT_USERNAME):
    adapter = build_cave_treasure_miniapp_adapter(bot_username=bot_username)
    request = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return request, build_request_webview_args(adapter, request) if request.allowed else {}


async def request_cave_treasure_miniapp_init_data(identity_id, *, token, webview_url="", adapter=None):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, webview_url, start_param=token)
    if not launch.allowed:
        raise ValueError(launch.reason or "cave treasure miniapp launch not allowed")
    account_id, client = _get_identity_client_with_account(identity_id)
    if client is None:
        raise RuntimeError("身份客户端不可用")
    async with account_rpc_slot(account_id=account_id, client_obj=client):
        primary_bot = launch.bot_username or adapter.bot_username
        bot_usernames = [primary_bot, *_recent_game_bot_usernames(exclude=(primary_bot,))]
        result = None
        last_bot_error = None
        recoverable_errors = {"BotInvalidError", "UsernameInvalidError", "UsernameNotOccupiedError"}
        for bot_username in bot_usernames:
            try:
                try:
                    bot = await client.get_entity(bot_username)
                except Exception as exc:
                    if type(exc).__name__ not in recoverable_errors:
                        raise
                    bot_id = _recent_game_bot_id_for_username(bot_username)
                    if bot_id <= 0:
                        raise
                    bot = await client.get_entity(bot_id)
                bot_input = await client.get_input_entity(bot)
                result = await client(functions.messages.RequestMainWebViewRequest(
                    peer=bot_input,
                    bot=bot_input,
                    platform=launch.platform or adapter.platform,
                    start_param=launch.start_param,
                ))
                break
            except Exception as exc:
                if type(exc).__name__ not in recoverable_errors:
                    raise
                last_bot_error = exc
        if result is None:
            if last_bot_error is not None:
                raise last_bot_error
            raise RuntimeError("没有可用的官方游戏 Bot 获取 WebView")
    init_data = extract_miniapp_init_data_from_url(getattr(result, "url", "") or "")
    if not init_data:
        raise RuntimeError("WebView URL 缺少 tgWebAppData")
    return init_data


def _requests_transport(request):
    return requests.request(
        str(request.get("method") or "POST"),
        request["url"],
        json=request.get("payload") or {},
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            **dict(request.get("headers") or {}),
        },
        proxies=TG_REQUESTS_PROXIES,
        timeout=CAVE_TREASURE_MINIAPP_HTTP_TIMEOUT,
    )


def build_cave_treasure_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=CAVE_TREASURE_MINIAPP_GAME_KEY,
        label=CAVE_TREASURE_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="洞府命令入口与公共入口并行；频道发言不可用时由公共入口承接",
        replaces_commands=(".洞府",),
        state_outputs=("module_snapshot", "daily_counter", "inventory_delta"),
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
                note="读取洞府页面、寻宝 tab、神识/游戏次数和提示文案",
            ),
            MiniAppFlowStep(
                key="decide_action",
                endpoint="local_decision",
                method="LOCAL",
                required_payload_keys=("state",),
                sends_init_data=False,
                note="按页面剩余次数决策；当前样本通常入府后最多 7 次探索，但不写死",
            ),
            MiniAppFlowStep(
                key="hunt",
                endpoint="hunt",
                required_payload_keys=("token", "initData"),
                note="入府开启一局寻宝，返回 huntRun/sessionId/cells",
            ),
            MiniAppFlowStep(
                key="reveal",
                endpoint="hunt_reveal",
                required_payload_keys=("token", "initData", "sessionId", "index"),
                note="按提示或随机翻开一个未探明石室",
            ),
            MiniAppFlowStep(
                key="settle",
                endpoint="hunt_settle",
                required_payload_keys=("token", "initData", "sessionId"),
                note="见好就收结算本局寻宝",
            ),
        ),
    )


def _find_nested_dict(data, candidate_keys):
    if not isinstance(data, dict):
        return {}
    queue = [data]
    seen = set()
    while queue:
        item = queue.pop(0)
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        if not isinstance(item, dict):
            continue
        lowered_keys = {str(key).lower() for key in item}
        if lowered_keys.intersection(candidate_keys):
            return item
        for value in item.values():
            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(child for child in value if isinstance(child, dict))
    return {}


def _coerce_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _ratio_from_value(value):
    if isinstance(value, dict):
        for first_key, second_key in (
            ("remaining", "limit"),
            ("remain", "limit"),
            ("available", "limit"),
            ("used", "limit"),
            ("count", "limit"),
            ("current", "total"),
            ("value", "max"),
        ):
            if first_key in value and second_key in value:
                return _coerce_int(value.get(first_key)), _coerce_int(value.get(second_key))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _coerce_int(value[0]), _coerce_int(value[1])
    match = _RATIO_RE.search(str(value or ""))
    if match:
        return _coerce_int(match.group("a")), _coerce_int(match.group("b"))
    return 0, 0


def _first_ratio_by_keys(source, keys):
    source = source if isinstance(source, dict) else {}
    for key in keys:
        if key in source:
            ratio = _ratio_from_value(source.get(key))
            if ratio != (0, 0):
                return ratio
    return 0, 0


def _ratios_from_text(text):
    result = {"sense": (0, 0), "games": (0, 0)}
    for match in _RATIO_RE.finditer(str(text or "")):
        ratio = (_coerce_int(match.group("a")), _coerce_int(match.group("b")))
        label = str(match.group("label") or "")
        if label in {"神识", "出手", "次数"} and result["sense"] == (0, 0):
            result["sense"] = ratio
        elif label in {"游戏", "局数"} and result["games"] == (0, 0):
            result["games"] = ratio
    return result


def _bool_from_any(*values):
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on", "active"}:
            return True
        if text in {"0", "false", "no", "n", "off", "inactive"}:
            return False
    return False


def _extract_hint_target(text):
    text = str(text or "")
    match = _TARGET_RE.search(text)
    if match:
        return max(1, _coerce_int(match.group("target"), 0))
    return 0


def _iter_cave_hint_markers(*sources):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for marker in source.get("markers") or ():
            if isinstance(marker, dict):
                yield marker
        hint = source.get("hint") if isinstance(source.get("hint"), dict) else {}
        for marker in hint.get("markers") or ():
            if isinstance(marker, dict):
                yield marker
        latest_hint = source.get("latestHint") if isinstance(source.get("latestHint"), dict) else {}
        for marker in latest_hint.get("markers") or ():
            if isinstance(marker, dict):
                yield marker


def _cave_marker_target(marker):
    if not isinstance(marker, dict):
        return 0
    for key in ("index", "cellIndex"):
        if key in marker:
            target = _coerce_int(marker.get(key), -1)
            return target + 1 if target >= 0 else 0
    for key in ("target", "targetIndex"):
        if key in marker:
            return max(1, _coerce_int(marker.get(key), 0))
    return 0


def _cave_marker_priority(marker):
    kind = str((marker or {}).get("kind") or "").strip().lower()
    if kind in {"treasure", "main", "main_treasure", "chest"}:
        return 30
    if kind in {"loot", "resource", "reward", "item", "material"}:
        return 20
    if kind in {"safe", "hint", "candidate", "mark"}:
        return 10
    return 0


def _safe_external_app_summary(app):
    if not isinstance(app, dict):
        return {}
    url_summary = summarize_webapp_url(
        app.get("url") or "",
        button_text=app.get("buttonText") or app.get("title") or "",
        message_text=app.get("description") or app.get("subtitle") or "",
    )
    return {
        "key": str(app.get("key") or "").strip(),
        "title": sanitize_webapp_secret_text(app.get("title") or "", limit=80),
        "status": str(app.get("status") or "").strip(),
        "available": bool(app.get("available")),
        "button_text": sanitize_webapp_secret_text(app.get("buttonText") or "", limit=40),
        "action": sanitize_webapp_secret_text(app.get("action") or "", limit=80),
        "game_hint": url_summary.get("game_hint", ""),
        "start_kind": ((url_summary.get("start_param") or {}).get("kind") or ""),
        "has_url": bool(app.get("url")),
    }


def _parse_external_apps(account):
    account = account if isinstance(account, dict) else {}
    external = account.get("externalApps") if isinstance(account.get("externalApps"), dict) else {}
    apps = []
    for group in external.get("groups") or ():
        if not isinstance(group, dict):
            continue
        group_key = str(group.get("key") or "").strip()
        group_title = sanitize_webapp_secret_text(group.get("title") or "", limit=80)
        for app in group.get("apps") or ():
            app_summary = _safe_external_app_summary(app)
            if not app_summary:
                continue
            app_summary["group_key"] = group_key
            app_summary["group_title"] = group_title
            apps.append(app_summary)
    return apps


def _safe_command_center_entry(entry):
    if not isinstance(entry, dict):
        return {}
    commands = []
    for command in entry.get("commands") or ():
        if not isinstance(command, str):
            continue
        safe_command = sanitize_webapp_secret_text(command, limit=80)
        if safe_command:
            commands.append(safe_command)
    return {
        "key": str(entry.get("key") or "").strip(),
        "title": sanitize_webapp_secret_text(entry.get("title") or "", limit=80),
        "status": str(entry.get("status") or "").strip(),
        "target_tab": str(entry.get("targetTab") or "").strip(),
        "button_text": sanitize_webapp_secret_text(entry.get("buttonText") or "", limit=40),
        "note": sanitize_webapp_secret_text(entry.get("note") or "", limit=140),
        "commands": commands,
    }


def _parse_command_center(account):
    account = account if isinstance(account, dict) else {}
    center = account.get("commandCenter") if isinstance(account.get("commandCenter"), dict) else {}
    entries = []
    for entry in center.get("entries") or ():
        summary = _safe_command_center_entry(entry)
        if summary:
            entries.append(summary)
    security = center.get("security") if isinstance(center.get("security"), dict) else {}
    tianjige_entries = [
        entry
        for entry in entries
        if entry.get("target_tab") == "command"
        or (entry.get("button_text") or "").strip() == "到天机阁"
    ]
    return {
        "entry_count": len(entries),
        "security": {
            "mode": sanitize_webapp_secret_text(security.get("mode") or "", limit=60),
            "direct_raw_command": bool(security.get("directRawCommand")),
            "max_input_length": _coerce_int(security.get("maxInputLength"), 0),
            "text": sanitize_webapp_secret_text(security.get("text") or "", limit=180),
        },
        "entries": entries,
        "tianjige_entries": tianjige_entries,
    }


def parse_cave_dwelling_overview(data):
    """Normalize the new dwelling MiniApp dashboard without leaking WebApp URLs."""

    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    account = root.get("account") if isinstance(root.get("account"), dict) else {}
    dwelling = root.get("dwelling") if isinstance(root.get("dwelling"), dict) else {}
    identity = root.get("identity") if isinstance(root.get("identity"), dict) else {}
    small_world = account.get("smallWorld") if isinstance(account.get("smallWorld"), dict) else {}
    journey = account.get("journey") if isinstance(account.get("journey"), dict) else {}
    hunt_panel = dwelling.get("hunt") if isinstance(dwelling.get("hunt"), dict) else {}
    meditation = dwelling.get("meditation") if isinstance(dwelling.get("meditation"), dict) else {}
    deep = meditation.get("deepSeclusion") if isinstance(meditation.get("deepSeclusion"), dict) else {}
    standard = meditation.get("standardCultivation") if isinstance(meditation.get("standardCultivation"), dict) else {}
    formation = dwelling.get("formation") if isinstance(dwelling.get("formation"), dict) else {}
    hunt_used = _coerce_int(hunt_panel.get("used"), 0)
    hunt_limit = _coerce_int(hunt_panel.get("limit"), 0)
    hunt_remaining = _coerce_int(hunt_panel.get("remaining"), max(0, hunt_limit - hunt_used) if hunt_limit else 0)
    return {
        "ok": bool(root.get("ok", data.get("ok", False))),
        "player_id": _coerce_int(account.get("playerId") or identity.get("selectedPlayerId"), 0),
        "username": sanitize_webapp_secret_text(account.get("username") or "", limit=80),
        "dao_name": sanitize_webapp_secret_text(account.get("daoName") or "", limit=80),
        "sect_name": sanitize_webapp_secret_text(account.get("sectName") or "", limit=80),
        "cultivation_level": sanitize_webapp_secret_text(account.get("cultivationLevel") or "", limit=80),
        "has_dwelling": bool(dwelling.get("hasDwelling")),
        "lingqi_pool": _coerce_float(dwelling.get("lingqiPool"), 0.0),
        "lingqi_pct": _coerce_float(dwelling.get("lingqiPct"), 0.0),
        "production_hint": _coerce_float(dwelling.get("productionHint"), 0.0),
        "visual_capacity": _coerce_float(dwelling.get("visualCapacity"), 0.0),
        "formation": {
            "active": bool(formation.get("active")),
            "level": _coerce_int(formation.get("level"), 0),
            "mode": sanitize_webapp_secret_text(formation.get("mode") or "", limit=40),
            "title": sanitize_webapp_secret_text(formation.get("title") or "", limit=80),
        },
        "hunt": {
            "used": hunt_used,
            "limit": hunt_limit,
            "remaining": hunt_remaining,
            "action_points": _coerce_int(hunt_panel.get("actionPoints"), 0),
        },
        "meditation": {
            "can_settle": bool(meditation.get("canSettle")),
            "projected_gain": _coerce_int(meditation.get("projectedGain"), 0),
            "consumable_lingqi": _coerce_float(meditation.get("consumableLingqi"), 0.0),
            "reason": sanitize_webapp_secret_text(meditation.get("reason") or "", limit=80),
            "reason_text": sanitize_webapp_secret_text(meditation.get("reasonText") or "", limit=120),
        },
        "deep_seclusion": {
            "active": bool(deep.get("active")),
            "completed": bool(deep.get("completed")),
            "can_start": bool(deep.get("canStart")),
            "can_force_exit": bool(deep.get("canForceExit")),
            "can_settle": bool(deep.get("canSettle")),
            "remaining_seconds": _coerce_int(deep.get("remainingSeconds"), 0),
            "end_ms": _coerce_int(deep.get("endMs"), 0),
            "status_text": sanitize_webapp_secret_text(deep.get("statusText") or "", limit=120),
        },
        "standard_cultivation": {
            "can_cultivate": bool(standard.get("canCultivate")),
            "reason": sanitize_webapp_secret_text(standard.get("reason") or "", limit=80),
            "cooldown_remaining_seconds": _coerce_int(standard.get("cooldownRemainingSeconds"), 0),
            "deep_seclusion_active": bool(standard.get("deepSeclusionActive")),
        },
        "small_world": _parse_cave_small_world_overview(small_world),
        "journey": _parse_cave_journey_overview(journey),
        "external_apps": _parse_external_apps(account),
        "command_center": _parse_command_center(account),
    }


def _parse_cave_journey_overview(journey):
    if not isinstance(journey, dict):
        return {}
    wild = journey.get("wildExperience") if isinstance(journey.get("wildExperience"), dict) else {}
    modes = []
    for item in wild.get("modes") or ():
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if key not in CAVE_WILD_EXPERIENCE_MODES:
            continue
        modes.append({
            "key": key,
            "label": sanitize_webapp_secret_text(item.get("label") or "", limit=20),
            "risk": sanitize_webapp_secret_text(item.get("risk") or "", limit=20),
            "reward": sanitize_webapp_secret_text(item.get("reward") or "", limit=40),
            "description": sanitize_webapp_secret_text(item.get("description") or "", limit=160),
        })
    return {
        "server_time": _coerce_int(journey.get("serverTime"), 0),
        "wild_experience": {
            "available": bool(wild.get("available")),
            "daily_count": _coerce_int(wild.get("dailyCount"), 0),
            "daily_limit": _coerce_int(wild.get("dailyLimit"), 0),
            "daily_remaining": _coerce_int(wild.get("dailyRemaining"), 0),
            "cooldown_hours": _coerce_float(wild.get("cooldownHours"), 0.0),
            "remaining_seconds": _coerce_int(wild.get("remainingSeconds"), 0),
            "last_at": _coerce_int(wild.get("lastAt"), 0),
            "ready_at": _coerce_int(wild.get("readyAt"), 0),
            "reset_at": _coerce_int(wild.get("resetAt"), 0),
            "modes": modes,
        },
    }


def _parse_cave_small_world_overview(small_world):
    if not isinstance(small_world, dict):
        return {}
    summary = small_world.get("summary") if isinstance(small_world.get("summary"), dict) else {}
    prayer = small_world.get("prayer") if isinstance(small_world.get("prayer"), dict) else {}
    actions = small_world.get("actions") if isinstance(small_world.get("actions"), dict) else {}
    temple = small_world.get("temple") if isinstance(small_world.get("temple"), dict) else {}
    prayer_cost = prayer.get("cost") if isinstance(prayer.get("cost"), list) else []
    missing_resources = []
    for item in prayer_cost:
        if not isinstance(item, dict):
            continue
        missing = _coerce_int(item.get("missing"), 0)
        if missing <= 0:
            owned = _coerce_int(item.get("owned"), 0)
            required = _coerce_int(item.get("required"), 0)
            missing = max(0, required - owned)
        if missing > 0:
            missing_resources.append({
                "name": sanitize_webapp_secret_text(item.get("name") or item.get("itemId") or "资源", limit=60),
                "missing": missing,
            })
    faith_cap = _coerce_int(summary.get("faithCap"), 100 if summary else 0)
    stability_cap = _coerce_int(summary.get("stabilityCap"), 100 if summary else 0)
    barrier_remaining = _coerce_int(actions.get("barrierRemainingSeconds"), 0)
    return {
        "available": bool(small_world) and not bool(small_world.get("locked")),
        "has_world": bool(small_world.get("hasWorld", bool(summary) or bool(small_world))),
        "level": _coerce_int(temple.get("level") or small_world.get("level"), 0),
        "temple_level": _coerce_int(temple.get("level") or small_world.get("templeLevel") or small_world.get("temple_level"), 0),
        "temple_name": sanitize_webapp_secret_text(temple.get("name") or "", limit=80),
        "population": _coerce_int(summary.get("population") or small_world.get("population"), 0),
        "population_cap": _coerce_int(summary.get("populationCap"), 0),
        "faith": _coerce_int(summary.get("faith") if summary else small_world.get("faith"), 0),
        "faith_cap": faith_cap,
        "stability": _coerce_int(summary.get("stability") if summary else small_world.get("stability"), 0),
        "stability_cap": stability_cap,
        "incense_stock": _coerce_int(summary.get("incensePoints") or small_world.get("incenseStock") or small_world.get("incense_stock"), 0),
        "pending_incense": _coerce_float(summary.get("uncollectedIncense") or small_world.get("pendingIncense") or small_world.get("pending_incense"), 0.0),
        "hourly_incense": _coerce_float(summary.get("hourlyIncense"), 0.0),
        "shenshi_text": sanitize_webapp_secret_text(summary.get("shenshiText") or "", limit=80),
        "has_prayer": bool(prayer),
        "prayer_title": sanitize_webapp_secret_text(prayer.get("title") or prayer.get("name") or "", limit=80),
        "prayer_description": sanitize_webapp_secret_text(prayer.get("description") or "", limit=180),
        "prayer_success_rate": _coerce_float(prayer.get("successRate"), 0.0),
        "prayer_expires_in_seconds": _coerce_int(prayer.get("expiresInSeconds"), 0),
        "prayer_missing_resources": missing_resources,
        "prayer_resources_ready": bool(prayer) and not missing_resources,
        "barrier_active": barrier_remaining > 0 or bool((small_world.get("barrier") or {}).get("active")),
        "barrier_remaining_seconds": barrier_remaining,
        "barrier_cost": _coerce_int(actions.get("barrierCost"), 0),
        "edict_remaining_seconds": _coerce_int(actions.get("edictRemainingSeconds"), 0),
        "prayer_remaining_seconds": _coerce_int(actions.get("prayerRemainingSeconds"), 0),
        "can_manifest": bool(actions.get("canManifest")),
        "can_harvest": bool(actions.get("canCollect") or actions.get("canHarvest")),
        "can_barrier": bool(actions.get("canBarrier")) or (
            barrier_remaining <= 0
            and _coerce_int(summary.get("incensePoints"), 0) >= _coerce_int(actions.get("barrierCost"), 0)
        ),
        "status_text": sanitize_webapp_secret_text(
            small_world.get("statusText") or small_world.get("status_text") or small_world.get("message") or actions.get("reasonText") or "",
            limit=160,
        ),
    }


def parse_cave_treasure_state(data):
    """Normalize cave treasure MiniApp payloads into decision state.

    The game currently reports two different ratio meanings:
    - 神识 8/8: remaining actions / total actions for the current round.
    - 游戏 0/3: used games / total games for the day.
    """

    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    overview = parse_cave_dwelling_overview(root)
    dwelling = root.get("dwelling") if isinstance(root.get("dwelling"), dict) else {}
    hunt_panel = dwelling.get("hunt") if isinstance(dwelling.get("hunt"), dict) else {}
    hunt_run = root.get("huntRun") if isinstance(root.get("huntRun"), dict) else {}
    latest_hint = hunt_run.get("latestHint") if isinstance(hunt_run.get("latestHint"), dict) else {}
    hunt_result = root.get("huntResult") if isinstance(root.get("huntResult"), dict) else {}
    treasure = _find_nested_dict(
        root,
        {
            "treasure",
            "hunt",
            "search",
            "shenshi",
            "sense",
            "gamecount",
            "games",
            "found",
            "phase",
            "tab",
        },
    ) or hunt_run or hunt_panel or root
    all_text = "\n".join(
        str(value)
        for key, value in treasure.items()
        if isinstance(value, str) and key.lower() in {"text", "message", "hint", "tips", "status", "phase", "tab", "title"}
    )
    outcome_text = "\n".join(
        str(value)
        for key, value in treasure.items()
        if isinstance(value, str) and key.lower() in {"text", "message", "result", "status", "phase", "title"}
    )
    if not all_text:
        all_text = sanitize_webapp_secret_text(str(data), limit=800)
    if not outcome_text:
        outcome_text = all_text

    sense_ratio = _first_ratio_by_keys(
        treasure,
        ("sense", "shenshi", "divineSense", "spiritSense", "actions", "attempts", "mind"),
    )
    if hunt_run:
        sense_ratio = (
            _coerce_int(hunt_run.get("ap"), sense_ratio[0]),
            _coerce_int(hunt_run.get("maxAp"), sense_ratio[1]),
        )
    elif hunt_panel:
        action_points = _coerce_int(hunt_panel.get("actionPoints"), 0)
        if action_points > 0:
            sense_ratio = (action_points, action_points)
    games_ratio = _first_ratio_by_keys(
        treasure,
        ("games", "gameCount", "rounds", "dailyGames", "playCount", "plays"),
    )
    if hunt_panel:
        overview_hunt = overview.get("hunt") if isinstance(overview.get("hunt"), dict) else {}
        games_ratio = (
            _coerce_int(overview_hunt.get("used"), games_ratio[0]),
            _coerce_int(overview_hunt.get("limit"), games_ratio[1]),
        )
    text_ratios = _ratios_from_text(all_text)
    if sense_ratio == (0, 0):
        sense_ratio = text_ratios["sense"]
    if games_ratio == (0, 0):
        games_ratio = text_ratios["games"]

    raw_cells = hunt_run.get("cells") if isinstance(hunt_run.get("cells"), list) else []
    board_size = _coerce_int(hunt_run.get("size"), 0)
    target_count = _coerce_int(
        treasure.get("targetCount")
        or treasure.get("npcCount")
        or treasure.get("dwarfCount")
        or treasure.get("pointCount")
        or treasure.get("gridCount")
        or (board_size * board_size if board_size > 0 else 0),
        0,
    )
    raw_targets = raw_cells or treasure.get("targets") or treasure.get("points") or treasure.get("dwarfs") or ()
    if not target_count and isinstance(raw_targets, list):
        target_count = len(raw_targets)
    if target_count <= 0:
        target_count = max(sense_ratio[1] or 0, 1)
    available_targets = []
    revealed_targets = set()
    if raw_cells:
        for fallback_index, cell in enumerate(raw_cells):
            if not isinstance(cell, dict):
                continue
            cell_index = _coerce_int(cell.get("index"), fallback_index) + 1
            if _bool_from_any(cell.get("revealed")):
                revealed_targets.add(cell_index)
                continue
            available_targets.append(cell_index)

    status = str(hunt_run.get("status") or treasure.get("status") or "").strip()
    in_round = _bool_from_any(
        treasure.get("inRound"),
        treasure.get("entered"),
        treasure.get("active"),
        treasure.get("started"),
    ) or bool(hunt_run) or any(keyword in all_text for keyword in ("寻宝中", "已入府", "正在寻宝"))
    on_treasure_tab = _bool_from_any(
        treasure.get("onTreasureTab"),
        treasure.get("treasureTab"),
    ) or bool(hunt_panel or hunt_run or hunt_result) or "寻宝" in all_text
    explicit_treasure_found = _bool_from_any(
        treasure.get("found"),
        treasure.get("treasureFound"),
        treasure.get("hit"),
        hunt_run.get("foundMain"),
        bool(hunt_result),
    )
    treasure_found = explicit_treasure_found or any(
        keyword in outcome_text
        for keyword in ("发现宝", "命中宝", "主宝", "秘宝", "宝物到手")
    )
    settled = bool(hunt_result) or _bool_from_any(treasure.get("settled"), treasure.get("finished")) or any(
        keyword in all_text for keyword in ("结算完成", "已结算", "今日寻宝已结算", "已收获")
    )

    hint_text = str(
        treasure.get("hint")
        or treasure.get("tips")
        or treasure.get("message")
        or treasure.get("text")
        or latest_hint.get("hint")
        or latest_hint.get("tips")
        or latest_hint.get("message")
        or latest_hint.get("text")
        or ""
    ).strip()
    hint_target = _coerce_int(treasure.get("hintTarget") or treasure.get("answer") or treasure.get("targetIndex"), 0)
    if hint_target <= 0:
        marker_targets = []
        for marker in _iter_cave_hint_markers(hunt_run, treasure, *raw_cells):
            marker_priority = _cave_marker_priority(marker)
            marker_target = _cave_marker_target(marker)
            if marker_priority > 0 and marker_target > 0:
                marker_targets.append((marker_priority, marker_target))
        available_set = set(available_targets)
        for _marker_priority, marker_target in sorted(marker_targets, reverse=True):
            if marker_target in revealed_targets:
                continue
            if available_set and marker_target not in available_set:
                continue
            hint_target = marker_target
            break
    if hint_target <= 0 and hint_text:
        hint_target = _extract_hint_target(hint_text)

    return {
        "on_treasure_tab": bool(on_treasure_tab),
        "in_round": bool(in_round),
        "session_id": str(hunt_run.get("sessionId") or treasure.get("sessionId") or "").strip(),
        "status": status,
        "action_remaining": max(0, sense_ratio[0]),
        "action_limit": max(0, sense_ratio[1]),
        "games_used": max(0, games_ratio[0]),
        "games_limit": max(0, games_ratio[1]),
        "treasure_found": bool(treasure_found),
        "settled": bool(settled),
        "hint_text": sanitize_webapp_secret_text(hint_text, limit=160),
        "hint_target": max(0, hint_target),
        "target_count": max(1, target_count),
        "available_targets": available_targets,
    }


def choose_cave_treasure_action(state, *, rng=None):
    rng = rng or random
    state = dict(state or {})
    games_limit = _coerce_int(state.get("games_limit"), 0)
    games_used = _coerce_int(state.get("games_used"), 0)
    action_remaining = _coerce_int(state.get("action_remaining"), 0)
    target_count = max(1, _coerce_int(state.get("target_count"), 1))
    in_round = bool(state.get("in_round"))
    session_id = str(state.get("session_id") or "").strip()

    if games_limit > 0 and games_used >= games_limit and not in_round:
        return {"action": "done", "reason": "daily_games_exhausted"}
    if not in_round:
        return {"action": "enter", "reason": "not_in_round"}
    if state.get("treasure_found"):
        return {"action": "settle", "sessionId": session_id, "reason": "treasure_found"}
    if str(state.get("status") or "").strip() == "failed":
        return {"action": "settle", "sessionId": session_id, "reason": "round_failed"}
    if action_remaining > 0:
        target_index = _coerce_int(state.get("hint_target"), 0)
        candidates = [
            _coerce_int(item, 0)
            for item in state.get("available_targets") or ()
            if _coerce_int(item, 0) > 0
        ]
        if target_index > 0 and candidates and target_index not in candidates:
            target_index = 0
        reason = "hint_target" if target_index > 0 else "random_target"
        if target_index <= 0:
            target_index = int(rng.choice(candidates)) if candidates else int(rng.randint(1, target_count))
        return {
            "action": "search",
            "sessionId": session_id,
            "targetIndex": max(1, min(target_index, target_count)),
            "reason": reason,
        }
    return {"action": "settle", "sessionId": session_id, "reason": "round_actions_exhausted"}


def build_cave_treasure_action_request(decision, *, token, init_data_session=None, init_data="", adapter=None):
    decision = dict(decision or {})
    action = str(decision.get("action") or "").strip()
    if action not in CAVE_TREASURE_SENDABLE_ACTIONS:
        raise ValueError(f"cave treasure action not sendable: {action or 'missing'}")
    payload = {}
    endpoint = "hunt"
    if action == "search":
        endpoint = "hunt_reveal"
        payload["sessionId"] = str(decision.get("sessionId") or "").strip()
        payload["index"] = max(0, _coerce_int(decision.get("targetIndex"), 1) - 1)
    elif action == "settle":
        endpoint = "hunt_settle"
        payload["sessionId"] = str(decision.get("sessionId") or "").strip()
    return build_cave_treasure_miniapp_request(
        endpoint,
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload=payload,
        adapter=adapter,
    )


def build_cave_deep_seclusion_action_request(action, *, token, init_data_session=None, init_data="", adapter=None):
    action = str(action or "").strip()
    if action not in CAVE_DEEP_SECLUSION_ACTIONS:
        raise ValueError(f"cave deep seclusion action not allowed: {action or 'missing'}")
    return build_cave_treasure_miniapp_request(
        "deep_seclusion",
        token=token,
        init_data_session=init_data_session,
        init_data=init_data,
        payload={"action": action},
        adapter=adapter,
    )


def _carry_cave_treasure_context(state, previous_state):
    state = dict(state or {})
    previous_state = dict(previous_state or {})
    if _coerce_int(state.get("games_limit"), 0) <= 0 and _coerce_int(previous_state.get("games_limit"), 0) > 0:
        state["games_used"] = previous_state.get("games_used", 0)
        state["games_limit"] = previous_state.get("games_limit", 0)
    if _coerce_int(state.get("action_limit"), 0) <= 0 and _coerce_int(previous_state.get("action_limit"), 0) > 0:
        state["action_limit"] = previous_state.get("action_limit", 0)
    return state


def _flow_result(ok, status, *, error="", data=None, events=None):
    return {
        "ok": bool(ok),
        "status": status,
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
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


def run_cave_treasure_miniapp_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    rng=None,
    sleeper=None,
    max_steps=32,
    capture_sink=None,
    capture_source="",
    player_id=None,
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_request = build_cave_treasure_miniapp_request(
        "start",
        token=token,
        init_data=init_data,
        payload={"playerId": int(player_id)} if player_id not in (None, "") else None,
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
        return _flow_result(False, "failed", error=start_result.error, events=events)

    current_data = start_result.data
    last_state = {}
    results = []
    for _step_index in range(max(1, int(max_steps or 1))):
        state = _carry_cave_treasure_context(parse_cave_treasure_state(current_data), last_state)
        last_state = state
        decision = choose_cave_treasure_action(state, rng=rng)
        events.append({
            "step": "decide",
            "ok": True,
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "action_remaining": state.get("action_remaining", 0),
            "games": f"{state.get('games_used', 0)}/{state.get('games_limit', 0)}",
        })
        if decision.get("action") == "done":
            return _flow_result(
                True,
                "daily_limit",
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )
        if decision.get("action") not in CAVE_TREASURE_SENDABLE_ACTIONS:
            return _flow_result(
                False,
                "blocked",
                error=f"unsendable action: {decision.get('action')}",
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )

        action_request = build_cave_treasure_action_request(
            decision,
            token=token,
            init_data=init_data,
            adapter=adapter,
        )
        action_result = execute_miniapp_http_request(
            action_request,
            transport,
            sleeper=sleeper,
            backoff_sec=(),
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"action:{decision.get('action')}",
        )
        _append_http_event(events, f"action:{decision.get('action')}", action_result)
        if not action_result.ok:
            return _flow_result(
                False,
                "failed",
                error=action_result.error,
                data={"state": last_state, "results": results, "settled_count": len(results)},
                events=events,
            )
        current_data = action_result.data
        if decision.get("action") == "settle":
            hunt_result = current_data.get("huntResult") if isinstance(current_data.get("huntResult"), dict) else {}
            results.append(dict(hunt_result or current_data or {}))

    return _flow_result(
        False,
        "step_limit",
        data={"state": last_state, "results": results, "settled_count": len(results)},
        events=events,
    )


async def run_cave_treasure_miniapp_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    transport=None,
    adapter=None,
    rng=None,
    sleeper=None,
    max_steps=32,
    capture_sink=None,
    capture_source="",
    init_data="",
    player_id=None,
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        return await asyncio.to_thread(
            run_cave_treasure_miniapp_lab_flow,
            token=token,
            init_data=init_data,
            transport=transport or _requests_transport,
            adapter=adapter,
            rng=rng,
            sleeper=sleeper or time.sleep,
            max_steps=max_steps,
            capture_sink=capture_sink,
            capture_source=capture_source,
            player_id=player_id,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_dwelling_start_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    init_data="",
    player_id=None,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        start_request = build_cave_treasure_miniapp_request(
            "start",
            token=token,
            init_data=init_data,
            payload={"playerId": int(player_id)} if player_id not in (None, "") else None,
            adapter=adapter,
        )
        start_result = await asyncio.to_thread(
            execute_miniapp_http_request,
            start_request,
            transport or _requests_transport,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key="dwelling_start",
        )
        if not start_result.ok:
            return _flow_result(False, "failed", error=start_result.error, events=[{"step": "dwelling_start", "ok": False}])
        return _flow_result(
            True,
            "ok",
            data={"overview": parse_cave_dwelling_overview(start_result.data), "raw": start_result.data},
            events=[{"step": "dwelling_start", "ok": True}],
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


def merge_cave_dwelling_snapshot_data(current, incoming):
    """Merge a split dwelling snapshot without erasing deferred domains."""

    base = current if isinstance(current, dict) else {}
    next_data = incoming if isinstance(incoming, dict) else {}
    merged = {**base, **next_data}
    base_account = base.get("account") if isinstance(base.get("account"), dict) else {}
    next_account = dict(next_data.get("account")) if isinstance(next_data.get("account"), dict) else {}
    base_dwelling = base.get("dwelling") if isinstance(base.get("dwelling"), dict) else {}
    next_dwelling = dict(next_data.get("dwelling")) if isinstance(next_data.get("dwelling"), dict) else {}
    snapshot = next_data.get("snapshot") if isinstance(next_data.get("snapshot"), dict) else {}
    snapshot_level = str(snapshot.get("level") or "").strip().lower()
    snapshot_domains = {
        str(item or "").strip().lower()
        for item in (snapshot.get("domains") or [])
        if str(item or "").strip()
    }

    if snapshot_level == "overview":
        for key in ("sect", "externalApps", "commandCenter", "journey", "smallWorld", "starPalace", "bagTreasure", "alchemy"):
            next_account.pop(key, None)
        for key in ("hunt", "messages", "facilities", "pavilion"):
            next_dwelling.pop(key, None)
    elif snapshot_level == "deferred":
        for key in ("bagTreasure", "alchemy"):
            next_account.pop(key, None)
        next_dwelling.pop("pavilion", None)
    elif snapshot_level == "action" and bool(snapshot.get("partial")):
        if "inventory" not in snapshot_domains:
            next_account.pop("bagTreasure", None)
        if "alchemy" not in snapshot_domains:
            next_account.pop("alchemy", None)
        if "pavilion" not in snapshot_domains:
            next_dwelling.pop("pavilion", None)

    base_profile = base_account.get("profile") if isinstance(base_account.get("profile"), dict) else {}
    next_profile = next_account.get("profile") if isinstance(next_account.get("profile"), dict) else {}
    if base_profile or next_profile:
        next_account["profile"] = {
            **base_profile,
            **next_profile,
            "status": {
                **(base_profile.get("status") if isinstance(base_profile.get("status"), dict) else {}),
                **(next_profile.get("status") if isinstance(next_profile.get("status"), dict) else {}),
            },
            "cultivation": {
                **(base_profile.get("cultivation") if isinstance(base_profile.get("cultivation"), dict) else {}),
                **(next_profile.get("cultivation") if isinstance(next_profile.get("cultivation"), dict) else {}),
            },
            "combatPower": {
                **(base_profile.get("combatPower") if isinstance(base_profile.get("combatPower"), dict) else {}),
                **(next_profile.get("combatPower") if isinstance(next_profile.get("combatPower"), dict) else {}),
            },
        }

    base_meditation = base_dwelling.get("meditation") if isinstance(base_dwelling.get("meditation"), dict) else {}
    next_meditation = next_dwelling.get("meditation") if isinstance(next_dwelling.get("meditation"), dict) else {}
    if base_meditation or next_meditation:
        next_dwelling["meditation"] = {
            **base_meditation,
            **next_meditation,
            "standardCultivation": {
                **(base_meditation.get("standardCultivation") if isinstance(base_meditation.get("standardCultivation"), dict) else {}),
                **(next_meditation.get("standardCultivation") if isinstance(next_meditation.get("standardCultivation"), dict) else {}),
            },
            "deepSeclusion": {
                **(base_meditation.get("deepSeclusion") if isinstance(base_meditation.get("deepSeclusion"), dict) else {}),
                **(next_meditation.get("deepSeclusion") if isinstance(next_meditation.get("deepSeclusion"), dict) else {}),
            },
        }

    merged["account"] = {**base_account, **next_account}
    merged["dwelling"] = {**base_dwelling, **next_dwelling}
    if not isinstance(next_data.get("identity"), dict) and isinstance(base.get("identity"), dict):
        merged["identity"] = base["identity"]
    return merged


async def run_cave_dwelling_snapshot_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    endpoint="details",
    init_data="",
    player_id=None,
    section="",
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    """Load one read-only deferred dwelling snapshot."""

    adapter = adapter or build_cave_treasure_miniapp_adapter()
    endpoint = str(endpoint or "details").strip().lower()
    if endpoint not in {"details", "section"}:
        return _flow_result(False, "failed", error="洞府分段接口不在白名单")
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        payload = {"playerId": int(player_id)} if player_id not in (None, "") else {}
        if endpoint == "section":
            normalized_section = str(section or "").strip().lower()
            if normalized_section not in {"inventory"}:
                return _flow_result(False, "failed", error="洞府分段 section 不在白名单")
            payload["section"] = normalized_section
        request = build_cave_treasure_miniapp_request(
            endpoint,
            token=token,
            init_data=init_data,
            payload=payload,
            adapter=adapter,
        )
        result = await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            transport or _requests_transport,
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"dwelling_{endpoint}",
        )
        if not result.ok:
            return _flow_result(False, "failed", error=result.error, data=result.data)
        return _flow_result(True, "ok", data=result.data, events=[{"step": f"dwelling_{endpoint}", "ok": True}])
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_small_world_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    action_planner,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    init_data="",
    player_id=None,
    initial_snapshot=None,
):
    """Read the dwelling once and execute at most one planned small-world action."""
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        snapshot_data = initial_snapshot if isinstance(initial_snapshot, dict) and initial_snapshot else None
        if snapshot_data is None:
            start_request = build_cave_treasure_miniapp_request(
                "start",
                token=token,
                init_data=init_data,
                payload={"playerId": int(player_id)} if player_id not in (None, "") else None,
                adapter=adapter,
            )
            start_result = await asyncio.to_thread(
                execute_miniapp_http_request,
                start_request,
                transport or _requests_transport,
                sleeper=sleeper or time.sleep,
                capture_sink=capture_sink,
                capture_source=capture_source,
                step_key="small_world:start",
            )
            if not start_result.ok:
                return _flow_result(False, "failed", error=start_result.error, data={"raw": start_result.data})
            snapshot_data = start_result.data

        before_overview = parse_cave_dwelling_overview(snapshot_data)
        plan = dict(action_planner(before_overview) or {})
        action = str(plan.get("action") or "").strip()
        if not action:
            return _flow_result(
                True,
                "noop",
                data={"overview": before_overview, "before_overview": before_overview, "plan": plan, "raw": snapshot_data},
                events=[{"step": "small_world:start", "ok": True}, {"step": "small_world:noop", "ok": True}],
            )

        action_request = build_cave_small_world_action_request(
            action,
            token=token,
            init_data=init_data,
            payload=plan.get("payload") or {},
            adapter=adapter,
        )
        action_result = await asyncio.to_thread(
            execute_miniapp_http_request,
            action_request,
            transport or _requests_transport,
            backoff_sec=(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"small_world:{normalize_cave_small_world_action(action)}",
        )
        merged_action_data = merge_cave_dwelling_snapshot_data(snapshot_data, action_result.data)
        after_overview = parse_cave_dwelling_overview(merged_action_data) or before_overview
        data = {
            "overview": after_overview,
            "before_overview": before_overview,
            "plan": plan,
            "action": normalize_cave_small_world_action(action),
            "action_result": dict(action_result.data.get("actionResult") or {}) if isinstance(action_result.data, dict) else {},
            "raw": merged_action_data,
        }
        return _flow_result(
            bool(action_result.ok),
            "acted" if action_result.ok else "action_failed",
            error=action_result.error,
            data=data,
            events=[{"step": "small_world:start", "ok": True}, {"step": f"small_world:{action}", "ok": bool(action_result.ok)}],
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_journey_action_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    action,
    mode,
    player_id=None,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    init_data="",
):
    """Execute one whitelisted journey action without replaying an uncertain POST."""
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        request = build_cave_journey_action_request(
            action,
            mode=mode,
            token=token,
            player_id=player_id,
            init_data=init_data,
            adapter=adapter,
        )
        result = await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            transport or _requests_transport,
            backoff_sec=(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"journey:{normalize_cave_journey_action(action)}",
        )
        if not result.ok:
            return _flow_result(False, "failed", error=result.error, data=result.data, events=[{"step": "journey", "ok": False}])
        return _flow_result(True, "acted", data=result.data, events=[{"step": "journey", "ok": True}])
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_tianjige_command_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    command,
    init_data="",
    player_id=None,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    """Execute one verified Tianjige command without HTTP retries.

    Command-center actions are state-changing. A transport timeout therefore has
    an unknown outcome and must be surfaced to the caller instead of replayed.
    """
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        request = build_cave_tianjige_command_request(
            command,
            token=token,
            init_data=init_data,
            player_id=player_id,
            adapter=adapter,
        )
        result = await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            transport or _requests_transport,
            backoff_sec=(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"command_center:{normalize_cave_tianjige_command(command)}",
        )
        if not result.ok:
            return _flow_result(False, "failed", error=result.error, events=[{"step": "command_center", "ok": False}])
        return _flow_result(True, "ok", data=result.data, events=[{"step": "command_center", "ok": True}])
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_external_action_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    action,
    player_id,
    init_data="",
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        request = build_cave_external_action_request(
            action,
            token=token,
            player_id=player_id,
            init_data=init_data,
            adapter=adapter,
        )
        result = await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            transport or _requests_transport,
            backoff_sec=(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"external:{normalize_cave_external_action(action)}",
        )
        if not result.ok:
            return _flow_result(False, "failed", error=result.error, data=result.data, events=[{"step": "external", "ok": False}])
        return _flow_result(True, "ok", data=result.data, events=[{"step": "external", "ok": True}])
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


async def run_cave_deep_seclusion_action_production_flow(
    identity_id,
    *,
    token,
    webview_url,
    action,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    init_data="",
):
    """Execute one deep-seclusion action without replaying an uncertain POST."""
    adapter = adapter or build_cave_treasure_miniapp_adapter()
    token = str(token or "").strip()
    webview_url = str(webview_url or "").strip()
    action = str(action or "").strip()
    try:
        init_data = str(init_data or "").strip() or await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            adapter=adapter,
        )
        request = build_cave_deep_seclusion_action_request(action, token=token, init_data=init_data, adapter=adapter)
        action_result = await asyncio.to_thread(
            execute_miniapp_http_request,
            request,
            transport or _requests_transport,
            backoff_sec=(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
            step_key=f"deep_seclusion:{action}",
        )
        if not action_result.ok:
            return _flow_result(False, "failed", error=action_result.error, events=[{"step": f"deep_seclusion:{action}", "ok": False}])
        return _flow_result(True, action, data=action_result.data, events=[{"step": f"deep_seclusion:{action}", "ok": True}])
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "CAVE_TREASURE_MINIAPP_GAME_KEY",
    "CAVE_TREASURE_MINIAPP_ENDPOINTS",
    "CAVE_EXTERNAL_ACTIONS",
    "CAVE_TIANJIGE_ALLOWED_COMMANDS",
    "build_cave_deep_seclusion_action_request",
    "build_cave_external_action_request",
    "build_cave_tianjige_command_request",
    "build_cave_treasure_action_request",
    "build_cave_treasure_launch_args",
    "build_cave_treasure_miniapp_adapter",
    "build_cave_treasure_miniapp_flow_plan",
    "build_cave_treasure_miniapp_request",
    "build_cave_journey_action_request",
    "choose_cave_treasure_action",
    "extract_cave_treasure_miniapp_launch",
    "normalize_cave_tianjige_command",
    "normalize_cave_external_action",
    "normalize_cave_journey_action",
    "normalize_cave_wild_experience_mode",
    "parse_cave_dwelling_overview",
    "parse_cave_treasure_state",
    "build_cave_small_world_action_request",
    "normalize_cave_small_world_action",
    "request_cave_treasure_miniapp_init_data",
    "run_cave_deep_seclusion_action_production_flow",
    "run_cave_dwelling_start_production_flow",
    "run_cave_external_action_production_flow",
    "run_cave_small_world_production_flow",
    "run_cave_journey_action_production_flow",
    "run_cave_tianjige_command_production_flow",
    "run_cave_treasure_miniapp_lab_flow",
    "run_cave_treasure_miniapp_production_flow",
    "summarize_cave_treasure_entry",
]
