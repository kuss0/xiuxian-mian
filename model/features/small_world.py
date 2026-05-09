import asyncio
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_REFINE,
    SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_enabled, get_identity_ids, get_send_as_tags, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time

SMALL_WORLD_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
SMALL_WORLD_CHAIN_COMMANDS = {
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_REFINE,
}
SMALL_WORLD_CHAIN_PENDING = {"query_pending", "manifest_pending", "harvest_pending", "refine_pending"}
SMALL_WORLD_PENDING_TIMEOUT_SEC = 20 * 60
SMALL_WORLD_REFRESH_MIN_SEC = 5 * 60
SMALL_WORLD_REFRESH_MAX_SEC = 8 * 60
SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 7
SMALL_WORLD_CYCLE_CD_SEC = 8 * 3600
SMALL_WORLD_LONG_PAUSE_SEC = 8 * 3600
SMALL_WORLD_JITTER_MIN_SEC = 60
SMALL_WORLD_JITTER_MAX_SEC = 20 * 60
SMALL_WORLD_INITIAL_CHECK_MIN_SEC = 10 * 60
SMALL_WORLD_INITIAL_CHECK_MAX_SEC = 30 * 60
SMALL_WORLD_TOOL_STEP_MIN_SEC = 120
SMALL_WORLD_TOOL_STEP_MAX_SEC = 240
SMALL_WORLD_MIN_HARVEST_INCENSE = 1.0

RE_SMALL_WORLD_DISASTER = re.compile(r"【小世界·天降浩劫】")
RE_SMALL_WORLD_TARGET_TAG = re.compile(rf"道友\s*@({SMALL_WORLD_TARGET_TAG_PATTERN})\s*的小世界遭遇")
RE_SMALL_WORLD_FAITH_DAMAGE = re.compile(r"惨重代价\s*[:：]\s*信仰(?:崩塌|动摇)\s*-\s*\d+\s*点")
RE_SMALL_WORLD_PREACH_PANEL = re.compile(r"【神音浩荡】")
RE_SMALL_WORLD_FAITH_VALUE = re.compile(r"信仰值大幅提升至\s*(\d+)\s*[！!]")

RE_SMALL_WORLD_PANEL = re.compile(r"【(?P<owner>[^】]+)的小世界】")
RE_PANEL_FAITH = re.compile(r"信仰\s*[:：]\s*(\d+)\s*/\s*(\d+)")
RE_PENDING_INCENSE = re.compile(r"待收香火\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)")
RE_INCENSE_STOCK = re.compile(r"香火库存\s*[:：]\s*(\d+)")
RE_PRAYER = re.compile(r"凡人祈愿\s*[：:]\s*([^\n]+)")
RE_PRAYER_WAIT = re.compile(r"下一次祈愿感应需等待\s*[：:]\s*([^\n)）]+)")
RE_MANIFEST_COST = re.compile(r"显灵消耗\s*[:：]\s*([^\n]+)")
RE_HARVEST_STOCK = re.compile(r"当前香火库存\s*[:：]\s*(\d+)")
RE_REFINE_BURNED = re.compile(r"燃烧了\s*(\d+)\s*点香火")
RE_STOCK_SHORTAGE = re.compile(r"香火库存不足\s*[(（]\s*拥有\s*[:：]\s*(\d+)\s*[)）]")
RE_RESOURCE_NAME = re.compile(r"【([^】]+)】不足")

_SMALL_WORLD_SCHEDULER_LOCK = asyncio.Lock()


def _normalize_tag(text):
    return str(text or "").strip().lstrip("@").lower()


def _truncate(text, limit=80):
    raw = str(text or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _phase():
    return str(state.get("small_world_phase") or "idle")


def _set_phase(value):
    state["small_world_phase"] = str(value or "idle")


def _chain_enabled():
    return any(
        bool(state.get(key))
        for key in (
            "small_world_manifest_enabled",
            "small_world_harvest_enabled",
            "small_world_refine_enabled",
            "small_world_refresh_enabled",
        )
    )


def _clear_preach_pending():
    state["small_world_preach_reply_to_msg_id"] = 0
    state["small_world_preach_due_at"] = 0
    if _phase() == "preach_pending":
        _set_phase("idle")


def _clear_chain_pending():
    state["small_world_query_msg_id"] = 0
    state["small_world_manifest_msg_id"] = 0
    state["small_world_harvest_msg_id"] = 0
    state["small_world_refine_msg_id"] = 0
    if _phase() in SMALL_WORLD_CHAIN_PENDING or _phase() in {"harvest_sent", "refine_sent"}:
        _set_phase("idle")


def _clear_all_runtime_pending():
    _clear_preach_pending()
    _clear_chain_pending()
    state["small_world_refresh_count"] = 0


def _schedule_after(now, min_sec, max_sec):
    state["next_small_world_time"] = float(now + random.uniform(float(min_sec), float(max_sec)))
    return state["next_small_world_time"]


def _schedule_next_cycle(now):
    return _schedule_after(now, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_CYCLE_CD_SEC + SMALL_WORLD_JITTER_MAX_SEC)


def _schedule_short_retry(now):
    return _schedule_after(now, 10 * 60, 30 * 60)


def _schedule_initial_check(now):
    return _schedule_after(now, SMALL_WORLD_INITIAL_CHECK_MIN_SEC, SMALL_WORLD_INITIAL_CHECK_MAX_SEC)


def _schedule_tool_step(now):
    return _schedule_after(now, SMALL_WORLD_TOOL_STEP_MIN_SEC, SMALL_WORLD_TOOL_STEP_MAX_SEC)


def _schedule_panel_wait(now, wait_sec):
    wait_sec = max(0, int(wait_sec or 0))
    state["next_small_world_time"] = float(now + wait_sec + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC))
    state["small_world_refresh_count"] = 0
    return state["next_small_world_time"]


def _schedule_resource_pause(now, label, raw_text):
    due_at = float(now + SMALL_WORLD_LONG_PAUSE_SEC + random.uniform(SMALL_WORLD_JITTER_MIN_SEC, SMALL_WORLD_JITTER_MAX_SEC))
    _clear_chain_pending()
    state["next_small_world_time"] = due_at
    state["small_world_last_error"] = f"{label}资源不足: {_truncate(raw_text)}"
    return due_at


def _find_small_world_identity_id(text):
    matched = RE_SMALL_WORLD_TARGET_TAG.search(text or "")
    if not matched:
        return None

    target_key = _normalize_tag(matched.group(1))
    if not target_key:
        return None

    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        normalized_tags = {_normalize_tag(tag) for tag in get_send_as_tags(identity_id) if tag}
        if target_key in normalized_tags:
            matched_ids.append(identity_id)
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _reply_to_message_id(reply_to):
    try:
        return int(getattr(reply_to, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_reply_to_tracked_message(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    return expected_msg_id > 0 and _reply_to_message_id(reply_to) == expected_msg_id


def _is_current_query_reply(reply_to, matched_family):
    if _is_reply_to_tracked_message(reply_to, "small_world_query_msg_id"):
        return True
    return matched_family == "small_world_query" and _phase() == "query_pending"


def _get_preach_deadline():
    deadline = float(state.get("small_world_preach_due_at", 0) or 0)
    if deadline > 0:
        return deadline
    if int(state.get("small_world_preach_reply_to_msg_id", 0) or 0) > 0 and _phase() in {"idle", "preach_pending"}:
        return float(state.get("next_small_world_time", 0) or 0)
    return 0.0


def _has_active_small_world_pending(now):
    reply_to_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    deadline = _get_preach_deadline()
    return reply_to_msg_id > 0 and deadline > now


def _parse_small_world_panel(text):
    raw_text = str(text or "")
    if "境界不足" in raw_text and "紫府小世界" in raw_text:
        return {"realm_blocked": True}
    if not RE_SMALL_WORLD_PANEL.search(raw_text):
        return None

    faith = 0
    faith_max = 0
    matched = RE_PANEL_FAITH.search(raw_text)
    if matched:
        faith = int(matched.group(1))
        faith_max = int(matched.group(2))

    pending_incense = 0.0
    matched = RE_PENDING_INCENSE.search(raw_text)
    if matched:
        pending_incense = float(matched.group(1))

    stock = 0
    matched = RE_INCENSE_STOCK.search(raw_text)
    if matched:
        stock = int(matched.group(1))

    wait_sec = 0
    matched = RE_PRAYER_WAIT.search(raw_text)
    if matched:
        wait_text = matched.group(1)
        if has_wait_time(wait_text):
            wait_sec = parse_wait_time(wait_text)

    prayer_matched = RE_PRAYER.search(raw_text)
    cost_matched = RE_MANIFEST_COST.search(raw_text)
    return {
        "realm_blocked": False,
        "faith": faith,
        "faith_max": faith_max,
        "pending_incense": pending_incense,
        "stock": stock,
        "has_prayer": bool(prayer_matched),
        "prayer_name": prayer_matched.group(1).strip() if prayer_matched else "",
        "manifest_cost": cost_matched.group(1).strip() if cost_matched else "",
        "wait_sec": wait_sec,
        "has_wait": wait_sec > 0,
    }


def _calc_refine_amount(stock):
    try:
        stock = int(stock or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, (stock // 10) * 10)


def _is_resource_shortage_text(text):
    raw_text = str(text or "")
    return (
        "显灵所需" in raw_text and "不足" in raw_text
    ) or (
        "修为不足" in raw_text and "无法调动天地灵气" in raw_text
    ) or (
        "灵石不足" in raw_text
        or ("清灵丹" in raw_text and "不足" in raw_text)
        or "资源不足" in raw_text
        or "材料不足" in raw_text
    )


def _resource_label_from_text(text):
    raw_text = str(text or "")
    matched = RE_RESOURCE_NAME.search(raw_text)
    if matched:
        return matched.group(1).strip()
    if "修为不足" in raw_text:
        return "修为"
    if "灵石不足" in raw_text:
        return "灵石"
    if "清灵丹" in raw_text and "不足" in raw_text:
        return "清灵丹"
    return "资源"


async def _disable_for_realm(raw_text):
    _clear_all_runtime_pending()
    state["small_world_enabled"] = False
    state["next_small_world_time"] = 0
    state["small_world_last_error"] = "境界不足，已关闭小世界模块"
    clear_pending_tasks_by_commands(SMALL_WORLD_CHAIN_COMMANDS | {CMD_SMALL_WORLD_PREACH}, send_as_id=get_current_identity_id())
    save_state()
    await send_audit_log("⚠️ 小世界境界不足，已关闭该身份的小世界模块。", scope="identity")
    return True


async def _send_small_world_preach(now, reason):
    sent_msg = await send_game_command(CMD_SMALL_WORLD_PREACH, track=True, max_retry=1)
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
    if not sent_msg:
        state["small_world_last_error"] = "神迹布道指令发送失败"
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界布道发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("preach_pending")
    state["small_world_preach_reply_to_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
    state["small_world_preach_due_at"] = float(sent_at + SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC)
    state["small_world_last_error"] = ""
    save_state()
    console_log(f"🌍 小世界{reason}，已发送神迹布道。")
    return True


async def _send_query(now, reason, *, refresh_attempt=None):
    msg = await send_game_command(CMD_SMALL_WORLD_QUERY, track=True, max_retry=1, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = f"{reason}发送 .小世界 失败"
        _set_phase("idle")
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界查询发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("query_pending")
    state["small_world_query_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_small_world_time"] = sent_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
    if refresh_attempt is not None:
        state["small_world_refresh_count"] = max(0, int(refresh_attempt or 0))
    state["small_world_last_error"] = ""
    save_state()
    console_log(f"🌍 小世界查询已发送：{reason}。")
    return True


async def _send_manifest(now):
    msg = await send_game_command(CMD_SMALL_WORLD_MANIFEST, track=True, max_retry=1, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = "发送 .显灵 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界显灵发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("manifest_pending")
    state["small_world_manifest_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["next_small_world_time"] = sent_at + SMALL_WORLD_PENDING_TIMEOUT_SEC
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _send_harvest(now):
    msg = await send_game_command(CMD_SMALL_WORLD_HARVEST, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = "发送 .收割香火 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界收割香火发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("harvest_sent")
    state["small_world_harvest_msg_id"] = int(getattr(msg, "id", 0) or 0)
    estimated_stock = int(state.get("small_world_incense_stock", 0) or 0) + int(float(state.get("small_world_pending_incense", 0) or 0))
    state["small_world_incense_stock"] = max(0, estimated_stock)
    state["small_world_pending_incense"] = 0
    _schedule_tool_step(sent_at)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _send_refine(now, amount):
    amount = _calc_refine_amount(amount)
    if amount < 10:
        return await _send_query(now, "淬炼数量不足，复查小世界")

    command = f"{CMD_SMALL_WORLD_REFINE} {amount}"
    msg = await send_game_command(command, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        state["small_world_last_error"] = f"发送 {command} 失败"
        _clear_chain_pending()
        _schedule_short_retry(sent_at)
        save_state()
        await send_audit_log("❌ 小世界神识淬炼发送失败，稍后重试。", scope="identity")
        return False

    _set_phase("refine_sent")
    state["small_world_refine_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["small_world_incense_stock"] = max(0, int(state.get("small_world_incense_stock", 0) or 0) - amount)
    _schedule_tool_step(sent_at)
    state["small_world_last_error"] = ""
    save_state()
    return True


def _schedule_refresh(now):
    current_count = int(state.get("small_world_refresh_count", 0) or 0)
    if current_count >= SMALL_WORLD_MAX_REFRESH_ATTEMPTS:
        _clear_chain_pending()
        _schedule_next_cycle(now)
        state["small_world_last_error"] = f"祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次未出现，停止本轮"
        save_state()
        return False

    state["small_world_refresh_count"] = current_count + 1
    _set_phase("refresh_wait")
    _schedule_after(now, SMALL_WORLD_REFRESH_MIN_SEC, SMALL_WORLD_REFRESH_MAX_SEC)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _finish_no_prayer_panel(now, panel):
    _clear_chain_pending()
    if panel.get("has_wait"):
        _schedule_panel_wait(now, int(panel.get("wait_sec", 0) or 0) + CD_BUFFER_SEC)
        state["small_world_last_error"] = ""
        save_state()
        return True

    if state.get("small_world_refresh_enabled"):
        if not _schedule_refresh(now):
            await send_audit_log(
                f"🌍 小世界祈愿刷新 {SMALL_WORLD_MAX_REFRESH_ATTEMPTS} 次仍未出现，停止本轮，约 8 小时后再查。",
                scope="identity",
                limit=240,
            )
        return True

    _schedule_next_cycle(now)
    state["small_world_last_error"] = ""
    save_state()
    return True


async def _handle_panel_decision(now, panel):
    if panel.get("realm_blocked"):
        return await _disable_for_realm("境界不足")

    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(panel.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(panel.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(panel.get("stock", 0) or 0)

    if panel.get("has_prayer"):
        state["small_world_refresh_count"] = 0
        _clear_chain_pending()
        if state.get("small_world_manifest_enabled"):
            save_state()
            return await _send_manifest(now)
        _schedule_next_cycle(now)
        state["small_world_last_error"] = "检测到祈愿，但自动显灵未开启"
        save_state()
        return True

    if panel.get("has_wait"):
        return await _finish_no_prayer_panel(now, panel)

    if state.get("small_world_harvest_enabled") and float(panel.get("pending_incense", 0) or 0) >= SMALL_WORLD_MIN_HARVEST_INCENSE:
        save_state()
        return await _send_harvest(now)

    refine_amount = _calc_refine_amount(panel.get("stock", 0))
    if state.get("small_world_refine_enabled") and refine_amount >= 10:
        save_state()
        return await _send_refine(now, refine_amount)

    return await _finish_no_prayer_panel(now, panel)


def get_small_world_status_text():
    faith_value = int(state.get("small_world_faith_value", 0) or 0)
    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    next_time = float(state.get("next_small_world_time", 0) or 0)
    lines = [
        "🌍 小世界",
        f"- 已启用：{'是' if state.get('small_world_enabled') else '否'}",
        f"- 浩劫布道：{'开启' if state.get('small_world_preach_enabled', True) else '关闭'}",
        f"- 自动显灵：{'开启' if state.get('small_world_manifest_enabled') else '关闭'}",
        f"- 收割香火：{'开启' if state.get('small_world_harvest_enabled') else '关闭'}",
        f"- 神识淬炼：{'开启' if state.get('small_world_refine_enabled') else '关闭'}",
        f"- 祈愿刷新：{'开启' if state.get('small_world_refresh_enabled') else '关闭'}",
        f"- 当前阶段：{_phase()}",
        f"- 当前信仰：{faith_value if faith_value > 0 else '未记录'}",
        f"- 待收香火：{state.get('small_world_pending_incense', 0) or 0}",
        f"- 香火库存：{state.get('small_world_incense_stock', 0) or 0}",
        f"- 本轮刷新：{int(state.get('small_world_refresh_count', 0) or 0)}/{SMALL_WORLD_MAX_REFRESH_ATTEMPTS}",
        f"- 待布道消息ID：{preach_msg_id or '无'}",
        f"- 下次动作：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）",
        f"- 最近错误：{state.get('small_world_last_error') or '无'}",
    ]
    return "\n".join(lines)


def clear_small_world_state(*, persist=False, keep_last_error=False):
    _clear_all_runtime_pending()
    state["next_small_world_time"] = 0
    state["small_world_faith_value"] = 0
    state["small_world_pending_incense"] = 0
    state["small_world_incense_stock"] = 0
    state["small_world_last_panel_at"] = 0
    clear_pending_tasks_by_commands(SMALL_WORLD_CHAIN_COMMANDS | {CMD_SMALL_WORLD_PREACH}, send_as_id=get_current_identity_id())
    if not keep_last_error:
        state["small_world_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


def schedule_small_world_initial_check(now, *, persist=False, keep_last_error=True):
    _clear_all_runtime_pending()
    _schedule_initial_check(now)
    if not keep_last_error:
        state["small_world_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_small_world_time"]


async def handle_small_world_disaster_broadcast(text, now, event):
    if not state.get("small_world_enabled") or not state.get("small_world_preach_enabled", True):
        return False

    raw_text = text or ""
    if not RE_SMALL_WORLD_DISASTER.search(raw_text) or not RE_SMALL_WORLD_FAITH_DAMAGE.search(raw_text):
        return False

    identity_id = _find_small_world_identity_id(raw_text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    if _has_active_small_world_pending(now):
        return True

    return await _send_small_world_preach(now, "监听到信仰异常")


async def handle_small_world_preach_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_preach":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_preach_enabled", True):
        return False

    raw_text = text or ""
    if not RE_SMALL_WORLD_PREACH_PANEL.search(raw_text):
        return False

    matched = RE_SMALL_WORLD_FAITH_VALUE.search(raw_text)
    if not matched:
        state["small_world_last_error"] = "神迹布道回复未解析到信仰值"
        _clear_preach_pending()
        save_state()
        return True

    faith_value = int(matched.group(1))
    state["small_world_faith_value"] = faith_value
    _clear_preach_pending()
    state["small_world_last_error"] = ""
    save_state()

    if faith_value >= 100:
        await send_audit_log(f"🌍 小世界信仰已恢复至 {faith_value}", scope="identity")
        return True

    await _send_small_world_preach(now, f"信仰值 {faith_value}<100")
    return True


async def handle_small_world_query_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_query":
        return False
    if not state.get("small_world_enabled") or not _chain_enabled():
        return False

    raw_text = text or ""
    panel = _parse_small_world_panel(raw_text)
    if not panel:
        return False
    if not _is_current_query_reply(reply_to, matched_family):
        return False

    if panel.get("realm_blocked"):
        return await _disable_for_realm(raw_text)

    return await _handle_panel_decision(now, panel)


async def handle_small_world_manifest_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_manifest":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_manifest_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if matched_family != "small_world_manifest" and CMD_SMALL_WORLD_MANIFEST not in orig_cmd:
        return False

    if _is_resource_shortage_text(raw_text):
        label = _resource_label_from_text(raw_text)
        due_at = _schedule_resource_pause(now, f"显灵/{label}", raw_text)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界显灵资源不足（{label}），本轮停止，{fmt_time_after(max(0, due_at - now))} 后再查；请手动补资源。",
            scope="identity",
            limit=260,
        )
        return True

    if "当前没有凡人祈愿需要处理" in raw_text:
        _clear_chain_pending()
        if state.get("small_world_refresh_enabled"):
            _schedule_refresh(now)
        else:
            _schedule_next_cycle(now)
        state["small_world_last_error"] = ""
        save_state()
        return True

    if "显灵成功" in raw_text or "显灵失败" in raw_text:
        _clear_chain_pending()
        state["small_world_refresh_count"] = 0
        state["small_world_last_error"] = "" if "显灵成功" in raw_text else "显灵失败，停止本轮"
        _schedule_next_cycle(now)
        save_state()
        return True

    state["small_world_last_error"] = f"未识别的显灵回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def handle_small_world_harvest_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_harvest":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_harvest_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if _phase() not in {"harvest_sent", "harvest_pending"}:
        return False
    if matched_family != "small_world_harvest" and not _is_reply_to_tracked_message(reply_to, "small_world_harvest_msg_id") and CMD_SMALL_WORLD_HARVEST not in orig_cmd:
        return False

    if "境界不足" in raw_text and "紫府小世界" in raw_text:
        return await _disable_for_realm(raw_text)

    stock_match = RE_HARVEST_STOCK.search(raw_text)
    if stock_match:
        stock = int(stock_match.group(1))
        state["small_world_incense_stock"] = stock
        state["small_world_pending_incense"] = 0
        state["small_world_last_error"] = ""
        _clear_chain_pending()
        refine_amount = _calc_refine_amount(stock)
        save_state()
        if state.get("small_world_refine_enabled") and refine_amount >= 10:
            return await _send_refine(now, refine_amount)
        return await _send_query(now, "收割后复查")

    shortage_match = RE_STOCK_SHORTAGE.search(raw_text)
    if shortage_match:
        state["small_world_incense_stock"] = int(shortage_match.group(1))
        state["small_world_last_error"] = f"收割香火库存不足: {_truncate(raw_text)}"
        _clear_chain_pending()
        _schedule_next_cycle(now)
        save_state()
        return True

    state["small_world_last_error"] = f"未识别的收割回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def handle_small_world_refine_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_refine":
        return False
    if not state.get("small_world_enabled") or not state.get("small_world_refine_enabled"):
        return False

    raw_text = str(text or "")
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "")
    if _phase() not in {"refine_sent", "refine_pending"}:
        return False
    if matched_family != "small_world_refine" and not _is_reply_to_tracked_message(reply_to, "small_world_refine_msg_id") and CMD_SMALL_WORLD_REFINE not in orig_cmd:
        return False

    burned_match = RE_REFINE_BURNED.search(raw_text)
    if "神识淬炼" in raw_text and burned_match:
        burned = int(burned_match.group(1))
        state["small_world_incense_stock"] = max(0, int(state.get("small_world_incense_stock", 0) or 0) - burned)
        state["small_world_last_error"] = ""
        _clear_chain_pending()
        save_state()
        return await _send_query(now, "淬炼后复查")

    shortage_match = RE_STOCK_SHORTAGE.search(raw_text)
    if shortage_match:
        state["small_world_incense_stock"] = int(shortage_match.group(1))
        state["small_world_last_error"] = f"淬炼库存不足: {_truncate(raw_text)}"
        _clear_chain_pending()
        _schedule_next_cycle(now)
        save_state()
        await send_audit_log("⚠️ 小世界神识淬炼库存不足，已停止本轮，约 8 小时后再查。", scope="identity")
        return True

    if _is_resource_shortage_text(raw_text):
        label = _resource_label_from_text(raw_text)
        due_at = _schedule_resource_pause(now, f"神识淬炼/{label}", raw_text)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界神识淬炼资源不足（{label}），本轮停止，{fmt_time_after(max(0, due_at - now))} 后再查。",
            scope="identity",
            limit=240,
        )
        return True

    state["small_world_last_error"] = f"未识别的淬炼回复: {_truncate(raw_text)}"
    _clear_chain_pending()
    _schedule_next_cycle(now)
    save_state()
    return False


async def run_small_world_scheduler(now):
    if _SMALL_WORLD_SCHEDULER_LOCK.locked():
        return
    async with _SMALL_WORLD_SCHEDULER_LOCK:
        await _run_small_world_scheduler(now)


async def _run_small_world_scheduler(now):
    if not state.get("small_world_enabled"):
        return

    preach_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    preach_deadline = _get_preach_deadline()
    if preach_msg_id > 0 and preach_deadline > 0 and now >= preach_deadline:
        state["small_world_last_error"] = "神迹布道回复超时"
        _clear_preach_pending()
        save_state()
        await send_audit_log(f"⚠️ 小世界神迹布道回复超时，消息ID={preach_msg_id}", scope="identity")
        return

    phase = _phase()
    if phase in SMALL_WORLD_CHAIN_PENDING:
        deadline = float(state.get("next_small_world_time", 0) or 0)
        if deadline <= 0 or now < deadline:
            return
        state["small_world_last_error"] = f"{phase} 等待回复超时，停止本轮"
        _clear_chain_pending()
        _schedule_short_retry(now)
        save_state()
        await send_audit_log(
            f"⚠️ 小世界模块 {phase} 超时，已停止当前链路，{fmt_time_after(max(0, state['next_small_world_time'] - now))} 后再校准。",
            scope="identity",
            limit=260,
        )
        return

    if phase == "harvest_sent":
        next_time = float(state.get("next_small_world_time", 0) or 0)
        if next_time > 0 and now < next_time:
            return
        refine_amount = _calc_refine_amount(state.get("small_world_incense_stock", 0))
        if state.get("small_world_refine_enabled") and refine_amount >= 10:
            await _send_refine(now, refine_amount)
            return
        await _send_query(now, "收割后复查")
        return

    if phase == "refine_sent":
        next_time = float(state.get("next_small_world_time", 0) or 0)
        if next_time > 0 and now < next_time:
            return
        await _send_query(now, "淬炼后复查")
        return

    if not _chain_enabled():
        return

    next_time = float(state.get("next_small_world_time", 0) or 0)
    if next_time > 0 and now < next_time:
        return

    if phase == "refresh_wait":
        refresh_attempt = int(state.get("small_world_refresh_count", 0) or 0)
        await _send_query(now, f"祈愿刷新 {refresh_attempt}/{SMALL_WORLD_MAX_REFRESH_ATTEMPTS}", refresh_attempt=refresh_attempt)
        return

    state["small_world_refresh_count"] = 0
    await _send_query(now, "周期自查")


__all__ = [
    "clear_small_world_state",
    "get_small_world_status_text",
    "handle_small_world_disaster_broadcast",
    "handle_small_world_harvest_reply",
    "handle_small_world_manifest_reply",
    "handle_small_world_preach_reply",
    "handle_small_world_query_reply",
    "handle_small_world_refine_reply",
    "run_small_world_scheduler",
    "schedule_small_world_initial_check",
]
