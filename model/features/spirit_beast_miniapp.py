"""Lab-only protocol surface for the 万兽谷·驭灵行迹 MiniApp.

This module deliberately stops at entry extraction, request construction, and
safe response summaries. It does not own runtime state, scheduling, or live
HTTP execution. Production automation needs a separate single-identity gate.
"""

from __future__ import annotations

from ..webapp_core import (
    MiniAppAdapter,
    MiniAppFlowPlan,
    MiniAppFlowStep,
    build_miniapp_http_request,
    build_miniapp_launch_request,
    iter_webapp_entry_links,
    sanitize_webapp_secret_text,
    summarize_webapp_url,
)


SPIRIT_BEAST_MINIAPP_GAME_KEY = "spirit_beast"
SPIRIT_BEAST_MINIAPP_LABEL = "万兽谷·驭灵行迹"
SPIRIT_BEAST_MINIAPP_DEFAULT_API_BASE_URL = "https://asc.aiopenai.app"
SPIRIT_BEAST_MINIAPP_DEFAULT_BOT_USERNAME = "fanrenxiuxian_bot"
SPIRIT_BEAST_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS = (r"hantianzun\d+_bot",)
SPIRIT_BEAST_MINIAPP_API_PATH_PREFIX = "/api/miniapp/xianxia-spirit-beast/"
SPIRIT_BEAST_MINIAPP_ENDPOINTS = {
    "start": f"{SPIRIT_BEAST_MINIAPP_API_PATH_PREFIX}start",
    "expedition_start": f"{SPIRIT_BEAST_MINIAPP_API_PATH_PREFIX}expedition/start",
    "expedition_choose": f"{SPIRIT_BEAST_MINIAPP_API_PATH_PREFIX}expedition/choose",
}
SPIRIT_BEAST_MINIAPP_START_PARAM_PATTERN = r"spiritbeast[_-][A-Za-z0-9_-]{8,180}"


def build_spirit_beast_miniapp_adapter(
    *,
    api_base_url=SPIRIT_BEAST_MINIAPP_DEFAULT_API_BASE_URL,
    bot_username=SPIRIT_BEAST_MINIAPP_DEFAULT_BOT_USERNAME,
):
    return MiniAppAdapter(
        game_key=SPIRIT_BEAST_MINIAPP_GAME_KEY,
        label=SPIRIT_BEAST_MINIAPP_LABEL,
        bot_username=bot_username,
        allowed_bot_username_patterns=SPIRIT_BEAST_MINIAPP_ALLOWED_BOT_USERNAME_PATTERNS,
        api_base_url=api_base_url,
        allowed_web_hosts=("t.me", "telegram.me", "asc.aiopenai.app"),
        allowed_api_hosts=("asc.aiopenai.app",),
        allowed_api_paths=(SPIRIT_BEAST_MINIAPP_API_PATH_PREFIX,),
        endpoints=dict(SPIRIT_BEAST_MINIAPP_ENDPOINTS),
        start_param_pattern=SPIRIT_BEAST_MINIAPP_START_PARAM_PATTERN,
        default_enabled=False,
        manual_only=True,
    )


def build_spirit_beast_miniapp_request(
    endpoint,
    *,
    token,
    init_data="",
    payload=None,
    adapter=None,
):
    adapter = adapter or build_spirit_beast_miniapp_adapter()
    request_payload = {"token": str(token or "").strip()}
    request_payload.update(dict(payload or {}))
    return build_miniapp_http_request(
        adapter,
        endpoint,
        request_payload,
        init_data=init_data,
    )


def summarize_spirit_beast_entry(url, *, button_text="", message_text=""):
    summary = summarize_webapp_url(url, button_text=button_text, message_text=message_text)
    if summary:
        summary["adapter_key"] = SPIRIT_BEAST_MINIAPP_GAME_KEY
        summary["manual_only"] = True
        summary["default_enabled"] = False
    return summary


def extract_spirit_beast_miniapp_launch(event, *, message_text=""):
    """Extract a safe launch candidate from buttons or text URL fallback."""
    adapter = build_spirit_beast_miniapp_adapter()
    for button_text, url in iter_webapp_entry_links(event, message_text=message_text):
        if not url:
            continue
        summary = summarize_spirit_beast_entry(
            url,
            button_text=button_text,
            message_text=message_text,
        )
        if not summary or summary.get("game_hint") != SPIRIT_BEAST_MINIAPP_GAME_KEY:
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


def _response_root(data):
    if not isinstance(data, dict):
        return {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    return root if isinstance(root, dict) else {}


def _as_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return int(default or 0)


def _attempts_snapshot(root):
    attempts = root.get("attempts") if isinstance(root.get("attempts"), dict) else {}
    used = max(0, _as_int(attempts.get("used")))
    limit = max(0, _as_int(attempts.get("limit")))
    remaining = max(0, _as_int(attempts.get("remaining"), max(0, limit - used)))
    return used, limit, remaining


def _eligible_beast(beast):
    if not isinstance(beast, dict):
        return False
    if bool(beast.get("canExpedition")):
        return True
    return str(beast.get("status") or "").strip() == "休息中" and _as_int(beast.get("stamina")) >= 24


def parse_spirit_beast_state(data):
    """Return non-secret counters and phase evidence from a start/choose reply."""
    root = _response_root(data)
    used, limit, remaining = _attempts_snapshot(root)
    beasts = root.get("beasts") if isinstance(root.get("beasts"), list) else []
    expedition = root.get("expedition") if isinstance(root.get("expedition"), dict) else {}
    return {
        "daily_used": used,
        "daily_limit": limit,
        "daily_remaining": remaining,
        "beast_count": sum(1 for item in beasts if isinstance(item, dict)),
        "eligible_beast_count": sum(1 for item in beasts if _eligible_beast(item)),
        "expedition_active": bool(expedition),
        "expedition_keys": sorted(str(key) for key in expedition if str(key) != "runToken"),
    }


def parse_spirit_beast_outcome(data):
    """Extract reward-shaped fields without returning raw session/run tokens."""
    root = _response_root(data)
    outcome = root.get("outcome") if isinstance(root.get("outcome"), dict) else {}
    history = root.get("history") if isinstance(root.get("history"), list) else []
    latest = next((item for item in history if isinstance(item, dict)), {})
    reward = root.get("reward") if isinstance(root.get("reward"), dict) else latest.get("reward")
    reward = reward if isinstance(reward, dict) else {}
    item_name = sanitize_webapp_secret_text(reward.get("itemName") or reward.get("name") or "", limit=80)
    grade = sanitize_webapp_secret_text(reward.get("grade") or "", limit=40)
    result = {
        "score": max(0, _as_int(outcome.get("score") or latest.get("score"))),
        "grade": grade,
        "reward_name": item_name,
        "reward_count": max(0, _as_int(reward.get("quantity") or reward.get("count"))),
    }
    return result


def build_spirit_beast_miniapp_flow_plan():
    return MiniAppFlowPlan(
        adapter_key=SPIRIT_BEAST_MINIAPP_GAME_KEY,
        label=SPIRIT_BEAST_MINIAPP_LABEL,
        manual_only=True,
        default_enabled=False,
        note="Gate A/B 协议层；无生产 scheduler，状态变更 POST 禁止传输层重试",
        read_scope="single_identity_public_entry",
        state_outputs=("daily_counter", "expedition_phase", "reward_delta"),
        steps=(
            MiniAppFlowStep(
                key="launch",
                endpoint="telegram_webview",
                method="TELEGRAM",
                required_payload_keys=("token",),
                sends_init_data=False,
                note="入口 token 只在短生命周期流程内使用",
            ),
            MiniAppFlowStep(
                key="start",
                endpoint="start",
                required_payload_keys=("token", "initData"),
                note="只读同步次数、灵兽和区域",
            ),
            MiniAppFlowStep(
                key="expedition_start",
                endpoint="expedition_start",
                required_payload_keys=("token", "initData", "beastId", "zoneKey"),
                note="非幂等行迹开局；Gate C 前不自动执行",
            ),
            MiniAppFlowStep(
                key="expedition_choose",
                endpoint="expedition_choose",
                required_payload_keys=("token", "initData", "runToken", "seq", "approach"),
                note="非幂等路线选择；只允许回包驱动的单身份状态机",
            ),
        ),
    )


__all__ = [
    "SPIRIT_BEAST_MINIAPP_ENDPOINTS",
    "SPIRIT_BEAST_MINIAPP_GAME_KEY",
    "build_spirit_beast_miniapp_adapter",
    "build_spirit_beast_miniapp_flow_plan",
    "build_spirit_beast_miniapp_request",
    "extract_spirit_beast_miniapp_launch",
    "parse_spirit_beast_outcome",
    "parse_spirit_beast_state",
    "summarize_spirit_beast_entry",
]
