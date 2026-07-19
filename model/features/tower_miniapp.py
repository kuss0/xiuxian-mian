import asyncio
import re
import time

import requests

from ..config import TG_REQUESTS_PROXIES
from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    build_miniapp_http_request,
    build_miniapp_launch_request,
    execute_miniapp_http_request,
    sanitize_webapp_secret_text,
)


TOWER_MINIAPP_GAME_KEY = "tower"
TOWER_MINIAPP_LABEL = "琉璃问心塔"
TOWER_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
TOWER_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
TOWER_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-pagoda/"
TOWER_MINIAPP_ENDPOINTS = {
    "start": f"{TOWER_MINIAPP_API_PATH_PREFIX}start",
    "challenge": f"{TOWER_MINIAPP_API_PATH_PREFIX}challenge",
    "reset": f"{TOWER_MINIAPP_API_PATH_PREFIX}reset",
}
TOWER_MINIAPP_START_PARAM_PATTERN = r"pagoda[_-][A-Za-z0-9_-]{4,160}"
TOWER_MINIAPP_HTTP_TIMEOUT = (5, 30)

_CULTIVATION_GAIN_RE = re.compile(r"修为(?:增加|获得|[+＋])\s*([\d,]+)")
_TOWER_MARK_GAIN_RE = re.compile(r"(?:获得)?塔印\s*(?:[+＋]|增加)?\s*([\d,]+)")
_ITEM_GAIN_RE = re.compile(r"(?:获得|奖励|掉落)\s*(?:【([^】]+)】|([^\s，。；;:：]{2,24}))\s*[xX×]\s*([\d,]+)")


def build_tower_miniapp_adapter(*, api_base_url=TOWER_MINIAPP_DEFAULT_API_BASE_URL):
    return MiniAppAdapter(
        game_key=TOWER_MINIAPP_GAME_KEY,
        label=TOWER_MINIAPP_LABEL,
        bot_username=TOWER_MINIAPP_DEFAULT_BOT_USERNAME,
        allowed_bot_username_patterns=(r"hantianzun\d{2}_bot",),
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(TOWER_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(TOWER_MINIAPP_ENDPOINTS),
        start_param_pattern=TOWER_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=False,
    )


def build_tower_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=TOWER_MINIAPP_GAME_KEY,
        label=TOWER_MINIAPP_LABEL,
        steps=(
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="读取当日塔层、战力与是否可挑战。",
            ),
            MiniAppFlowStep(
                key="challenge",
                endpoint="challenge",
                required_payload_keys=("token", "initData"),
                waits_for="start.canChallenge=true",
                note="服务器一次性结算全部楼层；禁止自动重铸道心。",
            ),
        ),
        manual_only=False,
        default_enabled=False,
        replaces_commands=(".闯塔", ".继续闯塔"),
        read_scope="single_identity_public_entry",
        state_outputs=("daily_counter", "tower_progress", "rewards"),
        note="复用旧闯塔开关与执行窗口，自动链仅执行 start -> challenge。",
    )


def build_tower_launch_args(url, *, start_param=""):
    adapter = build_tower_miniapp_adapter()
    launch = build_miniapp_launch_request(adapter, url, start_param=start_param)
    return launch, {}


def build_tower_miniapp_request(endpoint, *, token, init_data="", adapter=None):
    adapter = adapter or build_tower_miniapp_adapter()
    return build_miniapp_http_request(
        adapter,
        endpoint,
        {"token": str(token or "").strip()},
        init_data=init_data,
        timeout_sec=TOWER_MINIAPP_HTTP_TIMEOUT[1],
    )


def parse_tower_state(data):
    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    raw = root.get("state") if isinstance(root.get("state"), dict) else root
    if not isinstance(raw, dict):
        return {}

    def as_int(key, default=0):
        try:
            return int(float(str(raw.get(key, default) or default).replace(",", "")))
        except (TypeError, ValueError, OverflowError):
            return default

    return {
        "dao_name": sanitize_webapp_secret_text(raw.get("daoName") or raw.get("dao_name") or "", limit=80),
        "level": sanitize_webapp_secret_text(raw.get("level") or "", limit=80),
        "power": as_int("power"),
        "today_highest": as_int("todayHighest"),
        "record_highest": as_int("recordHighest"),
        "failed_floor": as_int("failedFloor"),
        "resets_today": as_int("resetsToday"),
        "reset_cost": as_int("resetCost"),
        "tower_marks": as_int("towerMarks"),
        "can_challenge": bool(raw.get("canChallenge")),
        "aura_name": sanitize_webapp_secret_text(
            (raw.get("aura") or {}).get("name") if isinstance(raw.get("aura"), dict) else "",
            limit=80,
        ),
    }


def parse_tower_replay(data):
    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    raw = root.get("replay") if isinstance(root.get("replay"), dict) else {}

    def as_int(key, default=0):
        try:
            return int(float(str(raw.get(key, default) or default).replace(",", "")))
        except (TypeError, ValueError, OverflowError):
            return default

    return {
        "start_floor": as_int("startFloor"),
        "end_floor": as_int("endFloor"),
        "failed_floor": as_int("failedFloor"),
        "cleared_count": as_int("clearedCount"),
        "record_broken": bool(raw.get("recordBroken")),
        "report": sanitize_webapp_secret_text(raw.get("report") or "", limit=1200),
    }


def extract_tower_materials(data):
    replay = parse_tower_replay(data)
    report = str(replay.get("report") or "")
    gains = {}
    rewards = {}
    cultivation = _CULTIVATION_GAIN_RE.search(report)
    marks = _TOWER_MARK_GAIN_RE.search(report)
    if cultivation:
        gains["修为"] = int(cultivation.group(1).replace(",", ""))
    if marks:
        gains["塔印"] = int(marks.group(1).replace(",", ""))
    for match in _ITEM_GAIN_RE.finditer(report):
        name = str(match.group(1) or match.group(2) or "").strip()
        if name:
            rewards[name] = rewards.get(name, 0) + int(match.group(3).replace(",", ""))
    return gains, rewards


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
        timeout=TOWER_MINIAPP_HTTP_TIMEOUT,
    )


def _http_event(step, result):
    return {
        "step": step,
        "ok": bool(result.ok),
        "status_code": int(result.status_code or 0),
        "error_type": str(result.error_type or ""),
        "attempts": int(result.attempts or 0),
        "error": sanitize_webapp_secret_text(result.error),
    }


def _flow_result(ok, status, *, error="", data=None, events=None):
    return {
        "ok": bool(ok),
        "status": str(status or ""),
        "error": sanitize_webapp_secret_text(error),
        "data": dict(data or {}),
        "events": list(events or ()),
    }


def run_tower_miniapp_lab_flow(
    *,
    token,
    init_data,
    transport,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter or build_tower_miniapp_adapter()
    token = str(token or "").strip()
    init_data = str(init_data or "").strip()
    if not token:
        return _flow_result(False, "failed", error="token missing")
    if not init_data:
        return _flow_result(False, "failed", error="initData missing")

    events = []
    start_result = execute_miniapp_http_request(
        build_tower_miniapp_request("start", token=token, init_data=init_data, adapter=adapter),
        transport,
        backoff_sec=(),
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="start",
    )
    events.append(_http_event("start", start_result))
    if not start_result.ok:
        return _flow_result(False, "failed", error=start_result.error, events=events)

    start_state = parse_tower_state(start_result.data)
    if not start_state:
        return _flow_result(False, "failed", error="MiniApp 返回不是琉璃问心塔状态", events=events)
    if not start_state.get("can_challenge"):
        return _flow_result(True, "done_today", data={
            "state": start_state,
            "replay": {},
            "challenged": False,
            "gains": {},
            "rewards": {},
        }, events=events)

    challenge_result = execute_miniapp_http_request(
        build_tower_miniapp_request("challenge", token=token, init_data=init_data, adapter=adapter),
        transport,
        backoff_sec=(),
        sleeper=sleeper,
        capture_sink=capture_sink,
        capture_source=capture_source,
        step_key="challenge",
    )
    events.append(_http_event("challenge", challenge_result))
    challenge_state = parse_tower_state(challenge_result.data)
    if not challenge_result.ok:
        if challenge_state and not challenge_state.get("can_challenge"):
            return _flow_result(True, "done_today", error=challenge_result.error, data={
                "state": challenge_state,
                "replay": parse_tower_replay(challenge_result.data),
                "challenged": True,
                "gains": {},
                "rewards": {},
            }, events=events)
        return _flow_result(False, "failed", error=challenge_result.error, data={"state": challenge_state}, events=events)

    replay = parse_tower_replay(challenge_result.data)
    gains, rewards = extract_tower_materials(challenge_result.data)
    return _flow_result(True, "challenged", data={
        "state": challenge_state or start_state,
        "replay": replay,
        "challenged": True,
        "gains": gains,
        "rewards": rewards,
    }, events=events)


async def run_tower_miniapp_production_flow(
    identity_id,
    *,
    token,
    init_data,
    transport=None,
    adapter=None,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    del identity_id
    try:
        return await asyncio.to_thread(
            run_tower_miniapp_lab_flow,
            token=token,
            init_data=init_data,
            transport=transport or _requests_transport,
            adapter=adapter or build_tower_miniapp_adapter(),
            sleeper=sleeper or time.sleep,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
    except Exception as exc:
        return _flow_result(False, "failed", error=exc)


__all__ = [
    "TOWER_MINIAPP_GAME_KEY",
    "build_tower_launch_args",
    "build_tower_miniapp_adapter",
    "build_tower_miniapp_flow_plan",
    "build_tower_miniapp_request",
    "extract_tower_materials",
    "parse_tower_replay",
    "parse_tower_state",
    "run_tower_miniapp_lab_flow",
    "run_tower_miniapp_production_flow",
]
