"""Archived compatibility surface for the retired group-command tree flow.

The live tree game runs through ``tree_runtime``/``tree_miniapp``.  This module
keeps the old import and reply-handler names so historical message families and
control code can load without reconnecting any of the retired ``.灵树`` sends.
"""

from __future__ import annotations

import re
import time

from ..config import CD_BUFFER_SEC, FREEZE_CD
from ..state import get_send_as_tags, state
from .resource_backoff import reset_resource_shortage


TREE_ARCHIVE_REASON = (
    "旧版灵树群命令自动化已归档；当前灵树仅通过 MiniApp 独立开关和每日串行调度执行。"
)
TREE_IRRIGATION_RESOURCE_KEY = "tree_irrigation"
TREE_GUARD_RESOURCE_KEY = "tree_guard"
TREE_PULSE_RESOURCE_KEY = "tree_pulse"

RE_TREE_PULSE_PROGRESS = re.compile(r"([0-9]+(?:\.[0-9]+)?)%")
RE_TREE_PULSE_ELEMENTS = re.compile(
    r"主脉【([^】]+)】\s*/\s*辅脉【([^】]+)】\s*/\s*逆脉【([^】]+)】\s*/\s*平脉【([^】]+)】"
)
RE_TREE_PULSE_MAIN = re.compile(r"(?:主脉|主)\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_AUX = re.compile(r"(?:辅脉|辅)\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_REVERSE = re.compile(r"(?:逆脉|逆)\s*(?:[:：]\s*)?【?([金木水火土])】?")
RE_TREE_PULSE_NEUTRAL = re.compile(r"(?:平脉|中脉)\s*(?:[:：]\s*)?【?([金木水火土/、,，\s]+)】?")
RE_TREE_PULSE_STABILITY = re.compile(r"脉稳[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_STABILITY_CURRENT = re.compile(r"(?:脉稳|稳固|稳定)[^\n]*[（(]当前\s*(\d+)")
RE_TREE_PULSE_TURBIDITY = re.compile(r"(?:浊息|浊气)/紊乱[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_TURBIDITY_CURRENT = re.compile(r"(?:浊息|浊气)[^\n]*[（(]当前\s*(\d+)")
RE_TREE_PULSE_DAILY = re.compile(r"今日定脉令[:：]\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_RUSH = re.compile(r"冲脉\s*(\d+)\s*/\s*(\d+)")
RE_TREE_PULSE_COMMAND = re.compile(r"\.定脉(?:\s+[^\s/|｜，,。、]+){0,2}")


def _is_tree_pulse_panel(text):
    raw_text = str(text or "")
    return (
        "【落云宗 · 灵树玩法】" in raw_text
        or "云梦灵眼定脉" in raw_text
        or "今日脉象:" in raw_text
        or "今日脉象：" in raw_text
    ) and ("今日定脉令" in raw_text or "定脉令" in raw_text or "定脉玩法" in raw_text)


def _split_tree_elements(raw_value):
    return [part.strip() for part in re.split(r"[/、,，\s]+", str(raw_value or "")) if part.strip()]


def _parse_tree_pulse_available_commands(raw_text):
    commands = []
    seen = set()
    for match in RE_TREE_PULSE_COMMAND.finditer(str(raw_text or "")):
        command = re.sub(r"\s+", " ", match.group(0).strip())
        if not command or command in seen:
            continue
        seen.add(command)
        commands.append(command)
    return commands


def _tree_regex_text(pattern, raw_text):
    match = pattern.search(str(raw_text or ""))
    return match.group(1).strip() if match else ""


def _tree_regex_int_pair(pattern, raw_text):
    match = pattern.search(str(raw_text or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def parse_tree_pulse_panel(text):
    """Parse archived command-era panels for passive state compatibility."""
    raw_text = str(text or "")
    if not _is_tree_pulse_panel(raw_text):
        return None

    progress_match = RE_TREE_PULSE_PROGRESS.search(raw_text)
    element_match = RE_TREE_PULSE_ELEMENTS.search(raw_text)
    daily_match = RE_TREE_PULSE_DAILY.search(raw_text)
    rush_match = RE_TREE_PULSE_RUSH.search(raw_text)
    main = element_match.group(1).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_MAIN, raw_text)
    aux = element_match.group(2).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_AUX, raw_text)
    reverse = element_match.group(3).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_REVERSE, raw_text)
    neutral_raw = element_match.group(4).strip() if element_match else _tree_regex_text(RE_TREE_PULSE_NEUTRAL, raw_text)
    stability, stability_max = _tree_regex_int_pair(RE_TREE_PULSE_STABILITY, raw_text)
    if stability <= 0:
        current_stability = _tree_regex_text(RE_TREE_PULSE_STABILITY_CURRENT, raw_text)
        if current_stability:
            stability = int(current_stability)
            stability_max = 100
    turbidity, turbidity_max = _tree_regex_int_pair(RE_TREE_PULSE_TURBIDITY, raw_text)
    if turbidity <= 0:
        current_turbidity = _tree_regex_text(RE_TREE_PULSE_TURBIDITY_CURRENT, raw_text)
        if current_turbidity:
            turbidity = int(current_turbidity)
            turbidity_max = 100
    return {
        "progress": float(progress_match.group(1)) if progress_match else 0.0,
        "main": main,
        "aux": aux,
        "reverse": reverse,
        "neutral": neutral_raw,
        "neutral_elements": _split_tree_elements(neutral_raw),
        "stability": stability,
        "stability_max": stability_max,
        "turbidity": turbidity,
        "turbidity_max": turbidity_max,
        "daily_used": int(daily_match.group(1)) if daily_match else 0,
        "daily_limit": int(daily_match.group(2)) if daily_match else 0,
        "rush_used": int(rush_match.group(1)) if rush_match else 0,
        "rush_limit": int(rush_match.group(2)) if rush_match else 0,
        "available_commands": _parse_tree_pulse_available_commands(raw_text),
        "blocked": "已成熟" in raw_text or "正遭劫难" in raw_text or "不可定脉" in raw_text,
    }


def _apply_tree_pulse_panel(parsed, now):
    """Apply an observed panel without scheduling any follow-up action."""
    if not isinstance(parsed, dict):
        return False
    state["tree_pulse_mode_seen"] = True
    state["tree_pulse_last_panel_at"] = float(now or time.time())
    state["tree_pulse_progress"] = float(parsed.get("progress", 0.0) or 0.0)
    state["tree_pulse_main"] = str(parsed.get("main") or "")
    state["tree_pulse_aux"] = str(parsed.get("aux") or "")
    state["tree_pulse_reverse"] = str(parsed.get("reverse") or "")
    state["tree_pulse_neutral"] = str(parsed.get("neutral") or "")
    state["tree_pulse_stability"] = int(parsed.get("stability", 0) or 0)
    state["tree_pulse_stability_max"] = int(parsed.get("stability_max", 0) or 0)
    state["tree_pulse_turbidity"] = int(parsed.get("turbidity", 0) or 0)
    state["tree_pulse_turbidity_max"] = int(parsed.get("turbidity_max", 0) or 0)
    state["tree_pulse_daily_used"] = int(parsed.get("daily_used", 0) or 0)
    state["tree_pulse_daily_limit"] = int(parsed.get("daily_limit", 0) or 0)
    state["tree_pulse_rush_used"] = int(parsed.get("rush_used", 0) or 0)
    state["tree_pulse_rush_limit"] = int(parsed.get("rush_limit", 0) or 0)
    state["tree_pulse_available_commands"] = list(parsed.get("available_commands") or [])
    return True


def _is_tree_legacy_disabled_prompt(text):
    raw_text = str(text or "")
    return "当前管理员已关闭旧版【灵树灌溉】玩法" in raw_text or "请改用【云梦灵眼定脉】" in raw_text


def _is_tree_pulse_blocked_prompt(text):
    return "灵眼之树已成熟或正遭劫难，此刻不可定脉" in str(text or "")


def _is_tree_pulse_action_success(text):
    raw_text = str(text or "")
    if _is_tree_pulse_blocked_prompt(raw_text) or _is_tree_legacy_disabled_prompt(raw_text):
        return False
    if any(keyword in raw_text for keyword in ("冷却", "等待", "不足", "不可", "不能", "失败")):
        return False
    return any(keyword in raw_text for keyword in ("定脉", "注灵", "固脉", "净浊", "冲脉", "脉稳", "浊息", "进度", "宗门贡献"))


def _is_tree_irrigation_success(text):
    raw_text = str(text or "")
    return "灵树灌溉" in raw_text and any(marker in raw_text for marker in ("【💧", "【🌿", "【⛰️", "【🔥"))


def _is_tree_guard_success(text):
    raw_text = str(text or "")
    return "【守山成功】" in raw_text or "【守护成功！】" in raw_text or "攻势已被成功击退" in raw_text


def _normalize_tree_identity_text(text):
    return "".join(str(text or "").strip().lstrip("@").split()).casefold()


def _tree_panel_matches_current_identity(text):
    for line in str(text or "").splitlines():
        if "(你)" not in line and "（你）" not in line:
            continue
        compact_line = _normalize_tree_identity_text(line)
        for tag in get_send_as_tags():
            normalized_tag = _normalize_tree_identity_text(tag)
            if len(normalized_tag) >= 3 and normalized_tag in compact_line:
                return True
    return False


def _clear_legacy_bootstrap_flags():
    try:
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
    except KeyError:
        return False
    return True


def request_tree_bootstrap_check(now=None, *, min_sec=None, max_sec=None):
    """Discard a stale legacy bootstrap request without scheduling a send."""
    del now, min_sec, max_sec
    _clear_legacy_bootstrap_flags()
    return False


def get_tree_status_text():
    """Return an explicit tombstone instead of stale command-era state."""
    return "\n".join((
        "🌳 灵树",
        "- 旧版群命令自动化：已归档",
        "- 当前执行入口：MiniApp",
        f"- 说明：{TREE_ARCHIVE_REASON}",
    ))


async def handle_tree_invasion_start(text, now):
    del text, now
    return False


async def handle_tree_invasion_end(text, now, is_reply_to_me):
    del text, now, is_reply_to_me
    return False


async def handle_tree_rebirth_reset(text, now):
    del text, now
    return False


async def handle_tree_cd_fix(text, now, reply_to, matched_family=None):
    del text, now, reply_to, matched_family
    return False


async def handle_tree_exception_prompt(text, now=None):
    del text, now
    return False


async def handle_tree_panel(text, now, is_reply_to_me):
    del text, now, is_reply_to_me
    return False


async def handle_tree_harvest_reply(
    text,
    now,
    reply_to,
    matched_family=None,
    current_msg_id=0,
):
    del text, now, reply_to, matched_family, current_msg_id
    return False


async def run_tree_bootstrap_check(now):
    del now
    _clear_legacy_bootstrap_flags()
    return False


async def run_tree_scheduler(now):
    del now
    return False


__all__ = [
    "TREE_ARCHIVE_REASON",
    "get_tree_status_text",
    "handle_tree_cd_fix",
    "handle_tree_exception_prompt",
    "handle_tree_harvest_reply",
    "handle_tree_invasion_end",
    "handle_tree_invasion_start",
    "handle_tree_panel",
    "handle_tree_rebirth_reset",
    "request_tree_bootstrap_check",
    "run_tree_bootstrap_check",
    "run_tree_scheduler",
]
