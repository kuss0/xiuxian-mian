import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..config import (
    CMD_FISHING,
    CMD_FISHING_BASKET,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CANCEL,
    CMD_FISHING_CHUM,
    CMD_FISHING_LIFT,
    CMD_FISHING_OPEN,
    CMD_FISHING_PROBE,
    CMD_FISHING_STATUS,
    STATE_DIR,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..webapp_core import MiniAppCaptureStore, miniapp_retry_after_sec, sanitize_webapp_secret_text
from ..state import (
    get_current_identity_id,
    get_global_enabled,
    get_global_pause_source,
    get_identity_enabled,
    get_identity_display_name,
    get_identity_ids,
    get_identity_state,
    get_miniapp_auto_config,
    is_cave_public_auto_enabled,
    is_cave_public_identity_available,
    get_send_as_profile,
    get_storage_bag_records,
    set_storage_bag_records,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key
from . import fishing_behavior
from .fishing import parse_open_fish_result
from .fishing_miniapp import (
    extract_fishing_miniapp_catches,
    extract_fishing_miniapp_daily_progress as _extract_miniapp_daily_progress,
    extract_fishing_miniapp_gains,
    extract_fishing_miniapp_launch,
    extract_fishing_miniapp_rewards as _extract_miniapp_loose_rewards,
    run_fishing_miniapp_production_flow,
)
from .miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner, append_business_capture
from .storage_bag import apply_storage_bag_item_counts, apply_storage_bag_item_deltas, start_storage_bag_gift_batch
from . import fishing_operations


FISHING_REPLY_TIMEOUT_SEC = 90
FISHING_ACTION_DELAY_MIN_SEC = 5
FISHING_ACTION_DELAY_MAX_SEC = 12
FISHING_RECOVERY_MIN_SEC = 15
FISHING_RECOVERY_MAX_SEC = 45
FISHING_POST_ROD_DELAY_MIN_SEC = 3
FISHING_POST_ROD_DELAY_MAX_SEC = 5
FISHING_RESET_JITTER_MIN_SEC = 0
FISHING_RESET_JITTER_MAX_SEC = 12
FISHING_TRANSFER_RETRY_DELAY_SEC = 5 * 60
FISHING_VALUABLE_REMINDER_OFFSETS_SEC = (0, 3 * 3600, 6 * 3600)
FISHING_MINIAPP_FAILURE_BACKOFF_SEC = 30 * 60
FISHING_COMMON_OPEN_REWARD_ITEMS = {"灵石", "灵鱼肉", "灵鱼鳞", "清灵草", "水草"}
FISHING_VALUABLE_KEYWORDS = (
    "图纸",
    "丹方",
    "图谱",
    "功法",
    "剑诀",
    "法则",
    "残图",
    "通行令",
    "昆吾",
    "大衍诀",
    "空间节点",
    "坐标",
    "灵眼之树",
    "至宝",
    "真仙试锋",
)
FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS = fishing_behavior.FISHING_MAX_DAILY_LIMIT
_SEND_LOCKS = {}
_DAILY_REPORT_LOCK = asyncio.Lock()
# Kept as an empty compatibility surface for older tests and diagnostics. The
# active fishing runtime no longer records or sends text fishing commands.
_RECENT_COMMANDS = {}
FISHING_MINIAPP_CAPTURE_DIR = Path(STATE_DIR) / "miniapp_capture"
_FISHING_MINIAPP_PLAN_KEYS = (
    "next_fishing_time", "fishing_phase", "fishing_reply_to_msg_id", "fishing_reply_due_at",
    "fishing_status_msg_id", "fishing_pending_action", "fishing_last_msg_id",
    "fishing_last_result", "fishing_last_error",
)
_FISHING_MINIAPP_FACT_KEYS = frozenset({
    "fishing_daily_day", "fishing_daily_count", "fishing_daily_limit",
    "fishing_daily_catch_summary_json", "fishing_basket_calibrated_day",
})
_FISHING_RESULT_PLAN_KEYS = _FISHING_MINIAPP_PLAN_KEYS + (
    "fishing_enabled", "fishing_pond", "fishing_bait", "fishing_auto_open_fish_enabled",
    "fishing_auto_buy_bait_enabled", "fishing_transfer_target_id",
    "fishing_caught_fish_json", "fishing_transfer_due_at",
)
_FISHING_RESULT_STRING_KEYS = frozenset({
    "fishing_daily_day", "fishing_daily_catch_summary_json", "fishing_basket_calibrated_day",
    "fishing_phase", "fishing_pending_action", "fishing_last_result", "fishing_last_error",
    "fishing_forced_buy_bait", "fishing_caught_fish_json",
})
_FISHING_RESULT_INT_KEYS = frozenset({
    "fishing_daily_count", "fishing_daily_limit", "fishing_reply_to_msg_id",
    "fishing_status_msg_id", "fishing_last_msg_id", "fishing_forced_buy_count",
})
_FISHING_RESULT_TIME_KEYS = frozenset({"next_fishing_time", "fishing_reply_due_at", "fishing_transfer_due_at"})
_FISHING_RESULT_MAX_BYTES = 64 * 1024


class FishingMiniAppCommitError(RuntimeError):
    def __init__(self, reason="persistence_pending"):
        super().__init__(f"fishing result commit held: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class FishingMiniAppOperation:
    owner: MiniAppIdentityOwner
    require_enabled: bool
    pond_choice: str
    bait_choice: str
    schedule: dict

    @classmethod
    def capture(cls, identity_id, *, require_enabled=False):
        owner = MiniAppIdentityOwner.capture(identity_id)
        if owner is None:
            return None
        return cls(
            owner, require_enabled, str(owner.identity.get("fishing_pond") or ""),
            str(owner.identity.get("fishing_bait") or ""),
            {key: deepcopy(owner.identity.get(key)) for key in _FISHING_MINIAPP_PLAN_KEYS},
        )

    def is_current(self):
        return (
            self.owner.is_current()
            and is_cave_public_identity_available(self.owner.identity_id)
            and (not self.require_enabled or bool(self.owner.identity.get("fishing_enabled")))
            and str(self.owner.identity.get("fishing_pond") or "") == self.pond_choice
            and str(self.owner.identity.get("fishing_bait") or "") == self.bait_choice
            and all(self.owner.identity.get(key) == value for key, value in self.schedule.items())
        )


def _confirmed_fishing_round_count(result):
    if not isinstance(result, dict) or ("ok" in result and type(result["ok"]) is not bool):
        raise ValueError("invalid_fishing_result")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    counts = [mapping["settled_count"] for mapping in (result, data) if "settled_count" in mapping]
    if counts:
        if any(type(count) is not int or count < 0 or count != counts[0] for count in counts):
            raise ValueError("invalid_fishing_settled_count")
        count = counts[0]
    else:
        count = int(result.get("ok") is True and result.get("status") == "settled")
    if count > 0 and len(extract_fishing_miniapp_catches(data)) > count:
        raise ValueError("unconfirmed_fishing_catches")
    return count


def fishing_miniapp_has_confirmed_outcome(result):
    try:
        count = _confirmed_fishing_round_count(result)
    except ValueError:
        return False
    return count > 0 or result.get("status") in {"daily_limit", "no_rod"}




def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


def _parse_int(value, default=0):
    try:
        return int(str(value or default).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _state_snapshot():
    return dict(state.items())


def _cave_public_fishing_is_authoritative(send_as_id=None):
    try:
        identity_id = int(send_as_id or get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if identity_id <= 0:
        return False
    config = get_miniapp_auto_config()
    urls = config.get("cave_public_entry_urls") or config.get("cave_public_entry_url") or []
    if isinstance(urls, str):
        has_entry = bool(urls.strip())
    else:
        has_entry = any(str(value or "").strip() for value in urls)
    selected_ids = {
        int(value)
        for value in (config.get("cave_public_fishing_identity_ids") or ())
        if str(value or "").strip().lstrip("-").isdigit()
    }
    return bool(has_entry and identity_id in selected_ids)


def _retire_legacy_fishing_state(now):
    if fishing_operations.pending(state) or state.get("fishing_result_pending", {}) != {}:
        return False
    phase = str(state.get("fishing_phase") or "idle").strip()
    if phase == "miniapp":
        return False
    pending_action = str(state.get("fishing_pending_action") or "").strip()
    has_legacy_state = bool(
        phase not in {"", "idle"}
        or pending_action
        or _parse_int(state.get("fishing_reply_to_msg_id", 0)) > 0
        or _parse_int(state.get("fishing_status_msg_id", 0)) > 0
        or float(state.get("fishing_reply_due_at", 0) or 0) > 0
        or float(state.get("fishing_started_at", 0) or 0) > 0
    )
    if not has_legacy_state:
        return False
    last_result = str(state.get("fishing_last_result") or "").strip()
    updates = fishing_behavior.clear_pending_updates(keep_open_fish=True)
    updates.update({
        "fishing_started_at": 0,
        "fishing_last_result": last_result or "旧文本钓鱼等待已清理，主动链仅走公共 MiniApp",
        "fishing_last_error": "",
        "next_fishing_time": max(
            float(state.get("next_fishing_time", 0) or 0),
            _miniapp_failure_backoff(now),
        ),
    })
    _apply_updates(updates)
    mark_dirty()
    return True


def _format_count_map(counts):
    normalized = []
    for name, count in sorted((counts or {}).items(), key=lambda item: str(item[0])):
        name = str(name or "").strip()
        try:
            amount = int(count or 0)
        except (TypeError, ValueError):
            amount = 0
        if name and amount > 0:
            normalized.append(f"{name}x{amount}")
    return "、".join(normalized) if normalized else "无"


def _apply_updates(updates):
    for key, value in (updates or {}).items():
        state[key] = value


def _apply_effect(effect, *, persist=True):
    if not effect or not effect.handled:
        return False
    _apply_updates(effect.updates)
    if effect.storage_deltas:
        apply_storage_bag_item_deltas(get_current_identity_id(), dict(effect.storage_deltas))
    if effect.storage_counts:
        apply_storage_bag_item_counts(get_current_identity_id(), dict(effect.storage_counts))
    if persist:
        save_state()
    elif effect.updates:
        mark_dirty()
    return True


def _normalize_fishing_valuable_drop_reminders(value=None):
    raw = state.get("fishing_valuable_drop_reminders") if value is None else value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        raw = []
    reminders = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("event_at", "next_reminder_at"):
            try:
                entry[key] = float(entry.get(key, 0) or 0)
            except (TypeError, ValueError, OverflowError):
                entry[key] = 0.0
        try:
            entry["next_index"] = max(0, min(len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC), int(entry.get("next_index", 0) or 0)))
        except (TypeError, ValueError, OverflowError):
            entry["next_index"] = 0
        try:
            entry["result_msg_id"] = max(0, int(entry.get("result_msg_id", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            entry["result_msg_id"] = 0
        for key in ("event_id", "source", "item", "fish", "last_error"):
            entry[key] = str(entry.get(key) or "").strip()
        entry["done"] = bool(entry.get("done")) or entry["next_index"] >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC)
        if entry["item"]:
            reminders.append(entry)
    return reminders[-12:]


def _clean_fishing_reward_name(value):
    name = str(value or "").strip()
    while len(name) >= 2 and ((name[0], name[-1]) in {("【", "】"), ("[", "]"), ("(", ")"), ("（", "）")}):
        name = name[1:-1].strip()
    return name


def _is_common_fishing_reward_item(name):
    normalized = _clean_fishing_reward_name(name)
    return normalized in FISHING_COMMON_OPEN_REWARD_ITEMS or normalized in {"修为", "宗门贡献"}


def _is_valuable_fishing_reward_item(name, *, companion=False):
    normalized = _clean_fishing_reward_name(name)
    if not normalized or _is_common_fishing_reward_item(normalized):
        return False
    if companion:
        return True
    return any(keyword in normalized for keyword in FISHING_VALUABLE_KEYWORDS)


def _fishing_valuable_items_from_text(raw_text, open_result=None):
    text = str(raw_text or "")
    companion = "伴生机缘" in text
    parsed = open_result or parse_open_fish_result(text)
    items = []
    if parsed:
        for item_name in (parsed.items or {}).keys():
            normalized = _clean_fishing_reward_name(item_name)
            if _is_valuable_fishing_reward_item(normalized, companion=companion):
                items.append(normalized)
    for match in re.finditer(r"【(?P<name>[^】]+)】(?:x\d+)?", text):
        normalized = _clean_fishing_reward_name(match.group("name"))
        if _is_valuable_fishing_reward_item(normalized, companion=companion):
            items.append(normalized)
    deduped = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _queue_fishing_valuable_drop_reminders(raw_text, now, *, result_msg_id=0, open_result=None):
    items = _fishing_valuable_items_from_text(raw_text, open_result=open_result)
    if not items:
        return False
    parsed = open_result or parse_open_fish_result(raw_text)
    fish = str(getattr(parsed, "fish", "") or "").strip()
    item_text = "、".join(items)
    result_msg_id = int(result_msg_id or 0)
    event_key = result_msg_id if result_msg_id > 0 else int(float(now or 0) // 60)
    event_id = f"fishing-valuable:{event_key}:{fish}:{item_text}"
    reminders = _normalize_fishing_valuable_drop_reminders()
    existing_ids = {str(item.get("event_id") or "") for item in reminders if isinstance(item, dict)}
    if event_id in existing_ids:
        return False
    reminders.append({
        "event_id": event_id,
        "source": "灵溪垂钓伴生机缘",
        "item": item_text,
        "fish": fish,
        "event_at": float(now or time.time()),
        "next_index": 0,
        "next_reminder_at": float(now or time.time()),
        "done": False,
        "result_msg_id": result_msg_id,
        "last_error": "",
    })
    state["fishing_valuable_drop_reminders"] = reminders[-12:]
    mark_dirty()
    return True


def _format_fishing_valuable_reminder(event, index):
    labels = ("即时", "+3h", "+6h")
    index = int(index or 0)
    label = labels[index] if 0 <= index < len(labels) else "补发"
    item = str((event or {}).get("item") or "").strip() or "未解析宝物"
    fish = str((event or {}).get("fish") or "").strip()
    suffix = f"｜来源 {fish}" if fish else ""
    return f"🎣 灵溪垂钓伴生机缘提醒（第{index + 1}/3次，{label}）：{item}{suffix}"


async def _run_fishing_valuable_drop_reminders(now):
    reminders = _normalize_fishing_valuable_drop_reminders()
    changed = False
    sent_any = False
    for event in reminders:
        if not isinstance(event, dict) or event.get("done"):
            continue
        next_index = int(event.get("next_index", 0) or 0)
        if next_index >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            changed = True
            continue
        due_at = float(event.get("next_reminder_at", 0) or 0)
        if due_at <= 0:
            due_at = float(event.get("event_at", now) or now) + FISHING_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
            event["next_reminder_at"] = float(due_at)
            changed = True
        if float(now or 0) < due_at or sent_any:
            continue
        ok = await send_audit_log(
            _format_fishing_valuable_reminder(event, next_index),
            scope="identity",
            priority="high",
            limit=260,
        )
        sent_any = True
        changed = True
        if not ok:
            event["next_reminder_at"] = float(now + 5 * 60)
            event["last_error"] = "日志提醒发送失败，5分钟后重试"
            continue
        next_index += 1
        event["next_index"] = next_index
        event["last_error"] = ""
        if next_index >= len(FISHING_VALUABLE_REMINDER_OFFSETS_SEC):
            event["done"] = True
            event["next_reminder_at"] = 0
        else:
            event["next_reminder_at"] = float(event.get("event_at", now) or now) + FISHING_VALUABLE_REMINDER_OFFSETS_SEC[next_index]
    if changed:
        state["fishing_valuable_drop_reminders"] = reminders[-12:]
        save_state()
    return sent_any


def _fishing_reset_jitter_sec(send_as_id=None):
    min_sec = max(0, int(FISHING_RESET_JITTER_MIN_SEC or 0))
    max_sec = max(min_sec, int(FISHING_RESET_JITTER_MAX_SEC or 0))
    if max_sec <= min_sec:
        return float(min_sec)
    identity_id = int(send_as_id or get_current_identity_id() or 0)
    return float(min_sec + (abs(identity_id) % (max_sec - min_sec)))


def _is_fishing_reply(reply_to=None, matched_family=None):
    if matched_family == "fishing":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd in {
        CMD_FISHING,
        CMD_FISHING_STATUS,
        CMD_FISHING_PROBE,
        CMD_FISHING_LIFT,
        CMD_FISHING_CANCEL,
        CMD_FISHING_BASKET,
    } or orig_cmd.startswith((
        f"{CMD_FISHING} ",
        f"{CMD_FISHING_BUY_BAIT} ",
        f"{CMD_FISHING_CHUM} ",
        f"{CMD_FISHING_OPEN} ",
    ))


def _normalize_username(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith("@"):
        raw = f"@{raw}"
    return raw.lower()


def _current_identity_username():
    profile = get_send_as_profile(get_current_identity_id())
    return _normalize_username((profile or {}).get("username") or "")


def _explicit_fishing_angler(text):
    status = fishing_behavior.parse_fishing_status(text)
    if status:
        return _normalize_username(status.angler)
    catch = fishing_behavior.parse_fishing_catch(text)
    if catch:
        return _normalize_username(catch.angler)
    match = re.search(r"钓者[:：]\s*(?P<angler>@[A-Za-z0-9_]+)", str(text or ""))
    if match:
        return _normalize_username(match.group("angler"))
    return ""


def _is_open_fish_reply_to_command(reply_to=None):
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd.startswith(f"{CMD_FISHING_OPEN} ")


def _looks_like_fishing_miniapp_entry(text):
    raw = str(text or "")
    return "灵溪垂钓" in raw or "钓鱼" in raw


def _looks_like_fishing_miniapp_entry_prompt(text):
    raw = str(text or "")
    return (
        "灵溪垂钓" in raw
        and "点击下方" in raw
        and "进入灵溪垂钓" in raw
    )


def _extract_miniapp_numeric_gains(data):
    labels = {"expGain": "钓术经验", "lingShiGain": "灵石"}
    return {labels[key]: amount for key, amount in extract_fishing_miniapp_gains(data).items()}


def _dedupe_miniapp_rewards(rewards):
    merged = {}
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        merged[name] = merged.get(name, 0) + max(1, _parse_int(reward.get("qty"), 1))
    return [{"name": name, "qty": qty} for name, qty in sorted(merged.items())]


def _format_miniapp_reward_list(rewards, *, limit=6):
    parts = []
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        parts.append(f"{name}x{max(1, _parse_int(reward.get('qty'), 1))}")
    return "、".join(parts[:limit])


def _miniapp_material_summary(data):
    catches = extract_fishing_miniapp_catches(data)
    catch_text = _format_miniapp_catch_summary(catches)
    catch_rewards = [
        reward
        for catch in catches
        if isinstance(catch, dict)
        for reward in (catch.get("rewards") or ())
        if isinstance(reward, dict)
    ]
    standalone_rewards = _extract_miniapp_loose_rewards(data)
    reward_text = _format_miniapp_reward_list(_dedupe_miniapp_rewards(standalone_rewards))
    gain_parts = [
        f"{name}+{amount}"
        for name, amount in sorted(_extract_miniapp_numeric_gains(data).items())
        if int(amount or 0) > 0
    ]
    parts = []
    if catch_text:
        parts.append(catch_text)
    if reward_text:
        parts.append(f"奖励:{reward_text}")
    if gain_parts:
        parts.append("收益:" + "、".join(gain_parts[:4]))
    has_material = bool(catches or catch_rewards or standalone_rewards)
    return "｜".join(parts), has_material


def _miniapp_empty_rod_text(data):
    if not isinstance(data, dict):
        return ""
    for key in ("caught", "last_caught"):
        if data.get(key) is False:
            return "空竿｜无新增物资"
    for key in ("rarityLabel", "rarity_label", "last_rarityLabel", "last_rarity_label"):
        if "空竿" in str(data.get(key) or ""):
            return "空竿｜无新增物资"
    if isinstance(data.get("last_result"), dict):
        return _miniapp_empty_rod_text(data.get("last_result")) or ""
    return ""


def _format_miniapp_result_summary(result):
    result = dict(result or {})
    status = str(result.get("status") or "unknown").strip() or "unknown"
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if status == "no_rod":
        return "未持有鱼竿，今日跳过"
    try:
        settled_count = _confirmed_fishing_round_count(result)
    except ValueError:
        return "MiniApp 结果格式异常｜未确认结算"
    if settled_count <= 0:
        data = {}
        if status in {"settled", "partial_not_ready", "finish_submitted"}:
            return f"MiniApp {status}｜未确认结算"
    round_prefix = f"{settled_count}竿｜" if settled_count > 1 else ""
    if status == "partial_not_ready":
        material_text, _has_material = _miniapp_material_summary(data)
        detail = f"{round_prefix}{material_text}" if material_text else f"{round_prefix}已完成结算"
        return f"MiniApp 已完成本轮｜{detail}｜下一竿未就绪，停止本轮"
    if result.get("ok"):
        material_text, _has_material = _miniapp_material_summary(data)
        if material_text:
            return f"MiniApp {status}｜{round_prefix}{material_text}"
        empty_text = _miniapp_empty_rod_text(data)
        return f"MiniApp {status}｜{round_prefix}{empty_text or '未解析到新增物资'}"
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}｜{error or '未完成'}"


def _format_miniapp_catch_summary(catches):
    parts = []
    for item in catches or ():
        if not isinstance(item, dict):
            continue
        fish = str(item.get("fish") or "").strip()
        if not fish:
            continue
        grade = str(item.get("grade") or "").strip()
        weight = str(item.get("weight") or "").strip()
        text = fish
        extras = [part for part in (grade, weight) if part]
        if extras:
            text += f"（{'/'.join(extras)}）"
        rewards = []
        for reward in item.get("rewards") or ():
            if not isinstance(reward, dict):
                continue
            name = str(reward.get("name") or "").strip()
            if not name:
                continue
            qty = _parse_int(reward.get("qty"), 1)
            rewards.append(f"{name}x{max(1, qty)}")
        if rewards:
            text += "｜奖励:" + "、".join(rewards[:4])
        parts.append(text)
    return "渔获:" + "；".join(parts[:6]) if parts else ""


async def _send_fishing_miniapp_harvest_summary(result):
    result = dict(result or {})
    try:
        settled_count = _confirmed_fishing_round_count(result)
    except ValueError:
        return False
    if settled_count <= 0:
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    material_text, has_material = _miniapp_material_summary(data)
    if not has_material or not material_text:
        return False
    return await send_audit_log(
        f"🎣 灵溪垂钓 MiniApp 收获｜{material_text}",
        scope="identity",
        priority="normal",
        limit=260,
    )


def _miniapp_catch_counts(catches):
    counts = {}
    for item in catches or ():
        if not isinstance(item, dict):
            continue
        fish = str(item.get("fish") or "").strip()
        if fish:
            counts[fish] = counts.get(fish, 0) + 1
    return counts


def _normalize_fishing_daily_catch_summary(value=None):
    raw = state.get("fishing_daily_catch_summary_json") if value is None else value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    summary = {
        "day": str(raw.get("day") or "").strip(),
        "rods": _parse_int(raw.get("rods"), 0),
        "fish": {},
        "rewards": {},
    }
    for key in ("fish", "rewards"):
        values = raw.get(key)
        if not isinstance(values, dict):
            continue
        for name, amount in values.items():
            name = str(name or "").strip()
            count = _parse_int(amount, 0)
            if name and count > 0:
                summary[key][name] = count
    return summary


def _merge_fishing_daily_catches(raw_summary, day_key, catches, *, settled_count=0, extra_rewards=None):
    day_key = str(day_key or "").strip()
    summary = _normalize_fishing_daily_catch_summary(raw_summary)
    if not day_key:
        day_key = get_day_key(time.time())
    if summary["day"] != day_key:
        summary = {"day": day_key, "rods": 0, "fish": {}, "rewards": {}}
    catch_list = [item for item in (catches or ()) if isinstance(item, dict)]
    rod_count = max(_parse_int(settled_count, 0), len(catch_list))
    if rod_count > 0:
        summary["rods"] = _parse_int(summary.get("rods"), 0) + rod_count
    for catch in catch_list:
        fish = str(catch.get("fish") or "").strip()
        if fish:
            summary["fish"][fish] = _parse_int(summary["fish"].get(fish), 0) + 1
        for reward in catch.get("rewards") or ():
            if not isinstance(reward, dict):
                continue
            name = str(reward.get("name") or "").strip()
            if not name:
                continue
            qty = max(1, _parse_int(reward.get("qty"), 1))
            summary["rewards"][name] = _parse_int(summary["rewards"].get(name), 0) + qty
    for reward in extra_rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        qty = max(1, _parse_int(reward.get("qty"), 1))
        summary["rewards"][name] = _parse_int(summary["rewards"].get(name), 0) + qty
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _format_fishing_daily_completion_summary(day_key, count, limit, raw_summary):
    summary = _normalize_fishing_daily_catch_summary(raw_summary)
    fish_text = _format_count_map(summary.get("fish"))
    reward_text = _format_count_map(summary.get("rewards"))
    rod_text = f"{_parse_int(count, 0)}/{_parse_int(limit, 0)}竿"
    if _parse_int(summary.get("rods"), 0) > 0 and _parse_int(summary.get("rods"), 0) != _parse_int(count, 0):
        rod_text += f"｜已解析{_parse_int(summary.get('rods'), 0)}竿"
    parts = [f"🎣 灵溪垂钓日结｜{day_key}｜{rod_text}", f"渔获:{fish_text}"]
    if reward_text != "无":
        parts.append(f"奖励:{reward_text}")
    return "｜".join(parts)


def _enabled_fishing_daily_entries(now):
    day_key = get_day_key(now)
    entries = []
    changed = False
    for identity_id in get_identity_ids():
        identity_id = int(identity_id or 0)
        if identity_id <= 0:
            continue
        public_auto_enabled = is_cave_public_auto_enabled("fishing", identity_id)
        if not get_identity_enabled(identity_id) and not (
            public_auto_enabled and is_cave_public_identity_available(identity_id)
        ):
            continue
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if not identity_state.get("fishing_enabled") and not public_auto_enabled:
            continue
        entry_day, count, limit, daily_updates = fishing_behavior.normalize_daily_counter(dict(identity_state), now)
        if daily_updates and identity_state.get("fishing_result_pending", {}) == {} and not fishing_operations.pending(identity_state):
            identity_state.update(daily_updates)
            changed = True
        limit = _parse_int(limit, 0)
        if limit <= 0:
            continue
        summary = _normalize_fishing_daily_catch_summary(identity_state.get("fishing_daily_catch_summary_json"))
        phase = str(identity_state.get("fishing_phase") or "").strip()
        last_result = str(identity_state.get("fishing_last_result") or "").strip()
        active_followup = (
            _parse_int(identity_state.get("fishing_reply_to_msg_id"), 0) > 0
            or bool(str(identity_state.get("fishing_pending_action") or "").strip())
            or phase not in {"", "idle"}
            or identity_state.get("fishing_result_pending", {}) != {}
            or fishing_operations.pending(identity_state)
        )
        terminal_skip = (
            "今日跳过" in last_result
            and ("未持有鱼竿" in last_result or "无可用鱼饵" in last_result)
        )
        daily_exhausted = "daily_limit" in last_result.lower()
        reportable = (
            str(summary.get("day") or "").strip() == str(day_key or "").strip()
            and (
                _parse_int(summary.get("rods"), 0) > 0
                or bool(summary.get("fish"))
                or bool(summary.get("rewards"))
            )
        )
        entries.append({
            "identity_id": identity_id,
            "name": get_identity_display_name(identity_id),
            "day": str(entry_day or day_key),
            "count": _parse_int(count, 0),
            "limit": limit,
            "summary": summary,
            "summary_day": str(identity_state.get("fishing_daily_summary_day") or "").strip(),
            "active_followup": active_followup,
            "terminal_skip": terminal_skip,
            "daily_exhausted": daily_exhausted,
            "reportable": reportable,
        })
    return day_key, entries, changed


def _format_fishing_all_daily_completion_summary(day_key, entries):
    entries = list(entries or ())
    total_count = sum(_parse_int(item.get("count"), 0) for item in entries)
    total_limit = sum(_parse_int(item.get("limit"), 0) for item in entries)
    lines = [f"🎣 灵溪垂钓日结｜全体｜{day_key}｜{total_count}/{total_limit}竿｜角色{len(entries)}"]
    for item in entries:
        summary = _normalize_fishing_daily_catch_summary(item.get("summary"))
        fish_text = _format_count_map(summary.get("fish"))
        reward_text = _format_count_map(summary.get("rewards"))
        parts = [
            f"- {item.get('name') or item.get('identity_id')}: {_parse_int(item.get('count'), 0)}/{_parse_int(item.get('limit'), 0)}竿",
            f"渔获:{fish_text}",
        ]
        if reward_text != "无":
            parts.append(f"奖励:{reward_text}")
        lines.append("｜".join(parts))
    return "\n".join(lines)


async def _send_fishing_daily_completion_summary(now):
    if _DAILY_REPORT_LOCK.locked():
        return True
    async with _DAILY_REPORT_LOCK:
        return await _send_fishing_daily_completion_summary_locked(now)


async def _send_fishing_daily_completion_summary_locked(now):
    day_key, entries, changed = _enabled_fishing_daily_entries(now)
    if changed:
        mark_dirty()
    if not entries:
        return False
    if any(str(item.get("day") or "").strip() != str(day_key or "").strip() for item in entries):
        return False
    if any(item.get("active_followup") for item in entries):
        return False
    if any(
        _parse_int(item.get("count"), 0) < _parse_int(item.get("limit"), 0)
        and not item.get("terminal_skip")
        and not item.get("daily_exhausted")
        for item in entries
    ):
        return False
    if all(str(item.get("summary_day") or "").strip() == str(day_key or "").strip() for item in entries):
        return False
    report_entries = [item for item in entries if item.get("reportable")]
    if not report_entries:
        return False
    report_keys = set(_FISHING_MINIAPP_PLAN_KEYS) | _FISHING_MINIAPP_FACT_KEYS | {"fishing_daily_summary_day", "fishing_result_pending", "fishing_operation"}

    def capture(identity_id):
        owner = MiniAppIdentityOwner.capture(identity_id)
        snapshot = {key: deepcopy(owner.identity.get(key)) for key in report_keys} if owner else {}
        return owner, snapshot

    def current(record):
        owner, snapshot = record
        return owner is not None and owner.is_current() and all(
            owner.identity.get(key) == value for key, value in snapshot.items()
        )

    origin = capture(get_current_identity_id())
    recipients = [capture(int(item.get("identity_id") or 0)) for item in entries]
    message = _format_fishing_all_daily_completion_summary(day_key, report_entries)
    ok = await send_audit_log(message, scope="identity", priority="normal", limit=900)
    origin_current = current(origin)
    if not ok:
        if origin_current:
            origin[0].identity["fishing_last_error"] = "灵溪垂钓日结播报发送失败，稍后重试"
            mark_dirty()
        return True
    for record in recipients:
        if current(record):
            record[0].identity["fishing_daily_summary_day"] = str(day_key or "").strip()
    if origin_current:
        origin[0].identity["fishing_last_error"] = ""
    save_state()
    return True


def _miniapp_failure_backoff(now):
    return float(now + FISHING_MINIAPP_FAILURE_BACKOFF_SEC + random.uniform(0, FISHING_RECOVERY_MAX_SEC))


def _build_fishing_miniapp_projection(result, now, *, result_msg_id=0, update_schedule=True, public_entry=False, fact_at=None):
    result = dict(result or {})
    retry_after_sec = miniapp_retry_after_sec(result)
    ok = bool(result.get("ok"))
    status = str(result.get("status") or "").strip()
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    settled_hint = _confirmed_fishing_round_count(result)
    completed_ok = ok or status == "daily_limit" or settled_hint > 0
    if completed_ok and status == "not_ready" and settled_hint > 0:
        status = "partial_not_ready"
        result["status"] = status
    if status == "no_rod" and settled_hint <= 0:
        updates = fishing_behavior.clear_pending_updates(keep_open_fish=True)
        updates.update({
            "fishing_phase": "idle",
            "fishing_last_msg_id": int(result_msg_id or 0),
            "fishing_last_result": "未持有鱼竿，今日跳过",
            "fishing_last_error": "",
            "next_fishing_time": fishing_behavior.next_fishing_daily_limit_check_timestamp(
                now,
                random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
            ),
        })
        return {"summary": updates["fishing_last_result"], "updates": updates if update_schedule else {},
                "items": {}, "reminders": []}
    catches = extract_fishing_miniapp_catches(data) if settled_hint > 0 else []
    updates = fishing_behavior.clear_pending_updates(keep_open_fish=True)
    updates["fishing_phase"] = "idle"
    updates["fishing_last_msg_id"] = int(result_msg_id or 0)
    updates["fishing_last_result"] = sanitize_webapp_secret_text(_format_miniapp_result_summary(result), limit=2048)
    updates["fishing_last_error"] = "" if completed_ok else updates["fishing_last_result"]
    partial_statuses = {"next_failed", "next_unavailable", "not_ready", "failed", "shop_failed", "request_budget", "cancelled", "finish_submitted", "operation_pending"}
    settled_statuses = {"settled", "daily_limit", "partial_not_ready"}
    error_text = str(result.get("error") or data.get("next_error") or "").strip()
    missing_bait_error = status == "next_failed" and "bait_missing" in error_text
    if completed_ok and status in partial_statuses:
        updates["fishing_last_error"] = updates["fishing_last_result"]
    if ok and missing_bait_error:
        bait_name = str(data.get("next_bait_name") or state.get("fishing_bait") or "鱼饵").strip()
        updates["fishing_forced_buy_bait"] = bait_name
        updates["fishing_forced_buy_count"] = fishing_behavior.fishing_buy_bait_count(_state_snapshot())
        updates["fishing_last_error"] = f"缺少鱼饵：{bait_name}"

    day_key, count, limit, daily_updates = fishing_behavior.normalize_daily_counter(_state_snapshot(), now if fact_at is None else fact_at)
    updates.update(daily_updates)
    progress = _extract_miniapp_daily_progress(data) if settled_hint > 0 or status == "daily_limit" else {}
    if progress and (progress["used"] < count + settled_hint or (status == "daily_limit" and progress["remaining"] != 0)):
        progress = {}
    has_progress = bool(progress)
    if has_progress:
        limit, count = progress["limit"], progress["used"]
        updates["fishing_daily_limit"] = limit
    settled_count = settled_hint
    if completed_ok and (status in settled_statuses or settled_count > 0 or has_progress):
        if status == "daily_limit" and not has_progress:
            inferred_limit = max(int(limit or 0), int(count or 0) + settled_count, len(catches))
            if inferred_limit > int(limit or 0):
                limit = fishing_behavior.clamp_fishing_daily_limit(inferred_limit)
                updates["fishing_daily_limit"] = limit
        if not has_progress:
            count = min(int(limit or 0), int(count or 0) + settled_count)
        if status == "daily_limit":
            count = int(limit or count or 0)
        updates["fishing_daily_day"] = day_key
        updates["fishing_daily_count"] = count
        updates["fishing_daily_catch_summary_json"] = _merge_fishing_daily_catches(
            state.get("fishing_daily_catch_summary_json"),
            day_key,
            catches,
            settled_count=settled_count,
            extra_rewards=_extract_miniapp_loose_rewards(data) if settled_count > 0 else [],
        )
        catch_counts = _miniapp_catch_counts(catches)
        if catch_counts:
            updates["fishing_basket_calibrated_day"] = ""
            transfer_target_id = _parse_int(state.get("fishing_transfer_target_id", 0))
            if transfer_target_id > 0:
                pending = fishing_behavior.parse_pending_open_fish(state.get("fishing_caught_fish_json"))
                for fish, amount in catch_counts.items():
                    pending[fish] = pending.get(fish, 0) + int(amount or 0)
                updates["fishing_caught_fish_json"] = json.dumps(pending, ensure_ascii=False, sort_keys=True)
                if float(state.get("fishing_transfer_due_at", 0) or 0) <= 0:
                    updates["fishing_transfer_due_at"] = float(now + fishing_behavior.FISHING_TRANSFER_QUEUE_DELAY_SEC)
        if count >= int(limit or 0) or status == "daily_limit":
            if state.get("fishing_auto_open_fish_enabled") or _parse_int(state.get("fishing_transfer_target_id", 0)) > 0:
                updates["fishing_basket_calibrated_day"] = ""
                updates["next_fishing_time"] = float(now + random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC))
            else:
                updates["next_fishing_time"] = fishing_behavior.next_fishing_daily_limit_check_timestamp(
                    now,
                    random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
                )
        elif status in partial_statuses:
            if missing_bait_error:
                if state.get("fishing_auto_buy_bait_enabled"):
                    updates["next_fishing_time"] = float(now + random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC))
                else:
                    updates["next_fishing_time"] = float(now + fishing_behavior.FISHING_BLOCKED_RETRY_SEC)
            else:
                updates["next_fishing_time"] = _miniapp_failure_backoff(now)
        else:
            updates["next_fishing_time"] = float(
                now + max(
                    FISHING_POST_ROD_DELAY_MIN_SEC,
                    random.uniform(FISHING_POST_ROD_DELAY_MIN_SEC, FISHING_POST_ROD_DELAY_MAX_SEC),
                )
            )
    else:
        updates["next_fishing_time"] = _miniapp_failure_backoff(now)
    if retry_after_sec > 0 and (not completed_ok or status in partial_statuses):
        updates["next_fishing_time"] = max(
            float(updates.get("next_fishing_time", 0) or 0),
            float(now) + retry_after_sec,
        )
    if status == "no_rod":
        updates.update(
            fishing_last_result="未持有鱼竿，今日跳过", fishing_last_error="",
            next_fishing_time=fishing_behavior.next_fishing_daily_limit_check_timestamp(
                now, random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
            ),
        )
    if public_entry and update_schedule and status == "bait_missing":
        updates.update(
            fishing_last_result=f"{updates['fishing_last_result']}｜无可用鱼饵，今日跳过", fishing_last_error="",
            next_fishing_time=fishing_behavior.next_fishing_reset_timestamp(now, _fishing_reset_jitter_sec()),
        )
    summary = updates["fishing_last_result"]
    if not update_schedule:
        updates = {key: value for key, value in updates.items() if key in _FISHING_MINIAPP_FACT_KEYS}
    catch_counts = _miniapp_catch_counts(catches)
    reminders = []
    if catch_counts:
        for catch in catches if update_schedule else ():
            reward_text = "\n".join(
                f"- 伴生机缘：【{reward.get('name')}】x{max(1, _parse_int(reward.get('qty'), 1))}"
                for reward in (catch.get("rewards") or ())
                if isinstance(reward, dict) and str(reward.get("name") or "").strip()
            )
            if reward_text:
                text = f"【提竿成功】\n钓获：【{catch.get('fish')}】\n{reward_text}"
                reminders.append(text)
    return {"summary": summary, "updates": updates, "items": catch_counts, "reminders": reminders}


def _fishing_projection_digest(value):
    def normalized(item):
        if isinstance(item, dict):
            return {key: normalized(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalized(child) for child in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item

    payload = json.dumps(normalized(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fishing_result_basis(identity, keys):
    return _fishing_projection_digest({key: identity.get(key) for key in keys})


def _fishing_inventory_basis(identity_id, items):
    if not items:
        return ""
    record = get_storage_bag_records().get(str(identity_id), {})
    counts = record.get("items") or {}
    return _fishing_projection_digest({"updated_at": record.get("updated_at", 0),
                                       "items": {name: counts.get(name, 0) for name in items}})


def _valid_fishing_result_pending(record):
    fields = {"version", "identity_id", "account_id", "created_at", "result_msg_id", "plan", "facts",
              "inventory", "summary", "updates", "items", "reminders"}
    if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] not in {1, 2}:
        return False
    if record["version"] == 2:
        fields.add("operation")
        if not fishing_operations.valid_reference(record.get("operation")):
            return False
    if set(record) != fields:
        return False

    def safe_text(value, limit):
        return (isinstance(value, str) and len(value) <= limit
                and sanitize_webapp_secret_text(value, limit=limit + 1) == value)

    def nonnegative(value):
        return type(value) in (int, float) and 0 <= value < 1e12

    if (any(type(record[key]) is not int or record[key] < 0 for key in ("identity_id", "account_id", "result_msg_id"))
            or record["identity_id"] <= 0 or not nonnegative(record["created_at"]) or record["created_at"] <= 0
            or any(not isinstance(record[key], str) or not re.fullmatch(r"[a-f0-9]{64}", record[key]) for key in ("plan", "facts"))
            or not safe_text(record["summary"], 2048) or not record["summary"] or not isinstance(record["updates"], dict)
            or not isinstance(record["items"], dict) or len(record["items"]) > FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS
            or not isinstance(record["reminders"], list) or len(record["reminders"]) > FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS):
        return False
    for key, value in record["updates"].items():
        if key in _FISHING_RESULT_STRING_KEYS:
            if not safe_text(value, _FISHING_RESULT_MAX_BYTES):
                return False
        elif key in _FISHING_RESULT_INT_KEYS:
            if type(value) is not int or value < 0:
                return False
        elif key in _FISHING_RESULT_TIME_KEYS:
            if not nonnegative(value):
                return False
        else:
            return False
    for key in ("fishing_daily_catch_summary_json", "fishing_caught_fish_json"):
        if key not in record["updates"]:
            continue
        try:
            value = json.loads(record["updates"][key])
        except (ValueError, TypeError):
            return False
        if not isinstance(value, dict):
            return False
        if key == "fishing_daily_catch_summary_json":
            if (set(value) != {"day", "rods", "fish", "rewards"} or not isinstance(value["day"], str)
                    or type(value["rods"]) is not int or value["rods"] < 0
                    or any(not isinstance(value[field], dict) for field in ("fish", "rewards"))):
                return False
            counters = [value["fish"], value["rewards"]]
        else:
            counters = [value]
        if any(not name or not isinstance(name, str) or type(amount) is not int or amount <= 0
               for counts in counters for name, amount in counts.items()):
            return False
    if (any(not safe_text(name, 160) or not name or type(amount) is not int or not 0 < amount <= FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS
            for name, amount in record["items"].items())
            or any(not safe_text(text, 4096) for text in record["reminders"])
            or not isinstance(record["inventory"], str)
            or (not re.fullmatch(r"[a-f0-9]{64}", record["inventory"]) if record["items"] else record["inventory"] != "")):
        return False
    try:
        return len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= _FISHING_RESULT_MAX_BYTES
    except (TypeError, ValueError, OverflowError):
        return False


def _commit_fishing_result_pending(owner):
    record = owner.identity.get("fishing_result_pending")
    if not owner.is_current() or not _valid_fishing_result_pending(record):
        raise FishingMiniAppCommitError("invalid_projection")
    if record["identity_id"] != owner.identity_id or record["account_id"] != owner.account_id:
        raise FishingMiniAppCommitError("owner_changed")
    if record["version"] == 2:
        fishing_operations.check_reference(owner, record["operation"])
    elif fishing_operations.pending(owner.identity):
        raise FishingMiniAppCommitError("unowned_operation_projection")
    try:
        basis_current = (record["facts"] == _fishing_result_basis(owner.identity, _FISHING_MINIAPP_FACT_KEYS)
                         and record["inventory"] == _fishing_inventory_basis(owner.identity_id, record["items"]))
        update_schedule = (
            record["plan"] == _fishing_result_basis(owner.identity, _FISHING_RESULT_PLAN_KEYS)
            and is_cave_public_identity_available(owner.identity_id)
            and (get_global_enabled() or _miniapp_http_allowed_during_pause())
        )
    except (ValueError, TypeError, OverflowError, AttributeError):
        raise FishingMiniAppCommitError("invalid_basis") from None
    if not basis_current:
        raise FishingMiniAppCommitError("result_basis_changed")
    before, inventory = deepcopy(owner.identity), get_storage_bag_records()
    try:
        owner.identity.update({key: deepcopy(value) for key, value in record["updates"].items()
                               if update_schedule or key in _FISHING_MINIAPP_FACT_KEYS})
        if record["items"]:
            if apply_storage_bag_item_deltas(owner.identity_id, record["items"], persist=False) is False:
                raise FishingMiniAppCommitError("inventory_not_applied")
        with use_identity(owner.identity_id):
            for text in record["reminders"] if update_schedule else ():
                _queue_fishing_valuable_drop_reminders(text, record["created_at"], result_msg_id=record["result_msg_id"])
        owner.identity["fishing_result_pending"] = {}
        if record["version"] == 2:
            fishing_operations.advance_accounting(owner, record["operation"])
        if save_state() is False:
            raise FishingMiniAppCommitError()
    except Exception as exc:
        owner.identity.clear()
        owner.identity.update(before)
        set_storage_bag_records(inventory)
        mark_dirty()
        if isinstance(exc, FishingMiniAppCommitError):
            raise
        raise FishingMiniAppCommitError() from None
    return record["summary"]


def _fishing_result_commit_response(reason="persistence_pending", *, summary=""):
    return {"ok": bool(summary),
            "message": f"钓鱼结果已完成本地入账：{summary}" if summary else "钓鱼结果待本地入账，暂不启动新一轮",
            "extra": {"status": "result_committed" if summary else "persistence_pending",
                      "reason": "" if summary else reason, "persistence_only": True}}


def recover_fishing_result_pending(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return None
    recovered = None
    if owner.identity.get("fishing_result_pending", {}) != {}:
        try:
            recovered = _fishing_result_commit_response(summary=_commit_fishing_result_pending(owner))
        except FishingMiniAppCommitError as exc:
            return _fishing_result_commit_response(exc.reason)
    return fishing_operations.recover_local(identity_id, time.time()) or recovered


def _apply_fishing_miniapp_result(result, now, *, result_msg_id=0, update_schedule=True, public_entry=False):
    owner = MiniAppIdentityOwner.capture(get_current_identity_id())
    if owner is None or owner.identity.get("fishing_result_pending", {}) != {}:
        raise FishingMiniAppCommitError("unresolved_projection")
    operation_reference, fact_at = None, None
    if fishing_operations.pending(owner.identity):
        result, operation_reference, plan_current, fact_at = fishing_operations.projection(owner, result, now)
        update_schedule = update_schedule and plan_current
    try:
        projection = _build_fishing_miniapp_projection(
            result, now, result_msg_id=result_msg_id, update_schedule=update_schedule, public_entry=public_entry, fact_at=fact_at,
        )
        record = {"version": 1, "identity_id": owner.identity_id, "account_id": owner.account_id,
                  "created_at": now, "result_msg_id": int(result_msg_id or 0), **projection,
                  "plan": _fishing_result_basis(owner.identity, _FISHING_RESULT_PLAN_KEYS),
                  "facts": _fishing_result_basis(owner.identity, _FISHING_MINIAPP_FACT_KEYS),
                  "inventory": _fishing_inventory_basis(owner.identity_id, projection["items"])}
        if operation_reference is not None:
            record.update(version=2, operation=operation_reference)
    except Exception:
        owner.identity["fishing_result_pending"] = {"invalid": True}
        mark_dirty()
        raise FishingMiniAppCommitError("invalid_projection") from None
    # This is a prepared local projection, not authorization to repeat gameplay.
    owner.identity["fishing_result_pending"] = deepcopy(record) if _valid_fishing_result_pending(record) else {"invalid": True}
    mark_dirty()
    return _commit_fishing_result_pending(owner)


def _remaining_miniapp_chain_rounds(now):
    _day_key, count, limit, daily_updates = fishing_behavior.normalize_daily_counter(_state_snapshot(), now)
    if daily_updates:
        _apply_updates(daily_updates)
        mark_dirty()
    # Local daily counters are only a cached hint. The MiniApp /next response is
    # the authoritative stop signal and can change before the basket is synced.
    remaining = max(1, int(limit or 0) - int(count or 0))
    return max(remaining, FISHING_MINIAPP_CHAIN_PROTECT_ROUNDS)


def _fishing_miniapp_capture_store(now):
    day_key = get_day_key(now)
    path = FISHING_MINIAPP_CAPTURE_DIR / f"fishing-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


def _record_fishing_business_capture(capture_sink, result, *, source, now):
    result = dict(result or {})
    try:
        settled_count = _confirmed_fishing_round_count(result)
    except ValueError:
        return {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if settled_count <= 0:
        return {}
    catches = extract_fishing_miniapp_catches(data)
    catch_rows = [
        {
            "fish": str(item.get("fish") or "").strip(),
            "grade": str(item.get("grade") or "").strip(),
            "weight": str(item.get("weight") or "").strip(),
        }
        for item in catches
        if isinstance(item, dict) and str(item.get("fish") or "").strip()
    ]
    catch_rewards = [
        reward
        for item in catches
        if isinstance(item, dict)
        for reward in (item.get("rewards") or ())
        if isinstance(reward, dict)
    ]
    standalone_rewards = _extract_miniapp_loose_rewards(data)
    caught = len(catch_rows)
    return append_business_capture(
        capture_sink,
        adapter_key="fishing",
        detail={
            "settled_count": settled_count,
            "rods": settled_count,
            "caught": caught,
            "empty": max(0, settled_count - caught),
            "catches": catch_rows,
            "gains": _extract_miniapp_numeric_gains(data),
            "items": _dedupe_miniapp_rewards(catch_rewards + standalone_rewards),
        },
        source=source,
        created_at=now,
    )


async def hold_unclaimed_fishing_miniapp_entry(event, text, now, *, result_msg_id=0):
    if not state.get("fishing_enabled"):
        return False
    launch = extract_fishing_miniapp_launch(event, message_text=text)
    if not launch and not _looks_like_fishing_miniapp_entry_prompt(text):
        return False
    if fishing_operations.pending(state) or state.get("fishing_result_pending", {}) != {}:
        return True
    explicit_angler = _explicit_fishing_angler(text)
    current_username = _current_identity_username()
    if explicit_angler and current_username and explicit_angler != current_username:
        return False
    updates = fishing_behavior.clear_pending_updates()
    updates.update({
        "fishing_started_at": 0,
        "fishing_last_msg_id": int(result_msg_id or getattr(event, "id", 0) or 0),
        "fishing_last_result": "MiniApp 钓鱼入口未接管，本竿停止文本后续",
        "fishing_last_error": "MiniApp 钓鱼入口未接管：未进入 HTTP 流程，本竿不回退 .钓鱼状态/.提竿",
        "next_fishing_time": _miniapp_failure_backoff(now),
    })
    _apply_updates(updates)
    save_state()
    await send_audit_log(
        "🎣 灵溪垂钓 MiniApp 入口未接管，已停止本竿文本后续；等待下一轮入口，不回退 .钓鱼状态/.提竿。",
        scope="identity",
        priority="low",
        limit=240,
    )
    return True


async def handle_fishing_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0):
    identity_id = int(get_current_identity_id() or 0)
    if MiniAppIdentityOwner.capture(identity_id) is None:
        return False
    if not state.get("fishing_enabled"):
        return False
    launch = extract_fishing_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    is_routed_fishing = _is_fishing_reply(reply_to, matched_family=matched_family)
    if not is_routed_fishing and not _looks_like_fishing_miniapp_entry(text):
        return False
    explicit_angler = _explicit_fishing_angler(text)
    current_username = _current_identity_username()
    if explicit_angler and current_username and explicit_angler != current_username:
        return False
    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    identity_available = is_cave_public_identity_available(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_available:
        reason = "全局暂停" if not global_enabled else "身份已停用"
        state["fishing_last_result"] = f"{reason}，MiniApp HTTP 接管已跳过"
        state["fishing_last_error"] = ""
        state["next_fishing_time"] = float(now + random.uniform(10 * 60, 30 * 60))
        save_state()
        await send_audit_log(f"🎣 灵溪垂钓 MiniApp {reason}，已跳过 WebView/HTTP 接管。", scope="identity", priority="low", limit=180)
        return True

    lock = _fishing_send_lock(identity_id)
    if lock.locked():
        return True
    async with lock:
        if recover_fishing_result_pending(identity_id) is not None:
            recovery_operation = FishingMiniAppOperation.capture(identity_id, require_enabled=True)
            if recovery_operation is not None and fishing_operations.pending(recovery_operation.owner.identity):
                await fishing_operations.recover_entry(
                    identity_id, launch,
                    lambda: recovery_operation.is_current() and (get_global_enabled() or _miniapp_http_allowed_during_pause()),
                )
            return True
        initial = FishingMiniAppOperation.capture(identity_id, require_enabled=True)
        if initial is None or not initial.is_current():
            return True
        max_rounds = _remaining_miniapp_chain_rounds(now)
        state["fishing_phase"] = "miniapp"
        state["fishing_reply_to_msg_id"] = 0
        state["fishing_reply_due_at"] = 0
        state["fishing_last_msg_id"] = int(result_msg_id or getattr(event, "id", 0) or 0)
        state["fishing_last_result"] = "MiniApp 钓鱼接管中"
        state["fishing_last_error"] = ""
        state["next_fishing_time"] = float(now + FISHING_REPLY_TIMEOUT_SEC * max_rounds)
        try:
            saved = save_state() is not False
        except Exception:
            saved = False
        if not saved:
            initial.owner.identity.update(initial.schedule)
            mark_dirty()
            logging.getLogger(__name__).warning("Fishing start marker save failed; no HTTP started")
            return True
        operation = FishingMiniAppOperation.capture(identity_id, require_enabled=True)

        def can_continue():
            return operation.is_current() and (get_global_enabled() or _miniapp_http_allowed_during_pause())

        try:
            await send_audit_log(
                "🎣 灵溪垂钓 MiniApp 接管入口，开始 WebView/HTTP 流程。"
                + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
                scope="identity", send_as_id=identity_id, priority="low", limit=180,
            )
        except asyncio.CancelledError:
            # No game request has begun; undo only this callback's unchanged marker.
            if operation.owner.is_current() and all(
                operation.owner.identity.get(key) == value for key, value in operation.schedule.items()
            ):
                operation.owner.identity.update(initial.schedule)
                save_state()
            raise
        except Exception as exc:
            logging.getLogger(__name__).warning("Fishing announcement failed (%s)", type(exc).__name__)
        if not can_continue():
            return True
        capture_sink = _fishing_miniapp_capture_store(now)
        capture_source = f"fishing_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        cancelled_flow = None
        writer = fishing_operations.CheckpointWriter(operation, operation_check=can_continue)
        try:
            result = await run_fishing_miniapp_production_flow(
                identity_id, token=launch.get("token"), webview_url=launch.get("webview_url"),
                max_rounds=max_rounds, pond_choice=operation.pond_choice, bait_choice=operation.bait_choice,
                capture_sink=capture_sink, capture_source=capture_source,
                operation_check=lambda: can_continue() and writer.is_current(), checkpoint=writer,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
        result = dict(result or {})
        writer.finish(result)
        confirmed = fishing_miniapp_has_confirmed_outcome(result)
        if not operation.owner.is_current() or (not can_continue() and not confirmed):
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return True
        if cancelled_flow is not None and not confirmed:
            raise MiniAppFlowCancelled(result) from None
        if result.get("status") == "cancelled" and not confirmed:
            return True
        update_schedule = can_continue() and cancelled_flow is None and result.get("status") != "cancelled"
        _record_fishing_business_capture(capture_sink, result, source=capture_source, now=now)
        try:
            with use_identity(identity_id):
                summary = _apply_fishing_miniapp_result(
                    result, now, result_msg_id=int(result_msg_id or getattr(event, "id", 0) or 0),
                    update_schedule=update_schedule,
                )
        except FishingMiniAppCommitError as exc:
            logging.getLogger(__name__).warning("Fishing result not committed (%s); local recovery only", exc.reason)
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(dict(result, accounting_pending=True)) from None
            return True
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(result) from None
        if fishing_operations.pending(operation.owner.identity):
            return True
        if not update_schedule:
            return True
        notice = FishingMiniAppOperation.capture(identity_id, require_enabled=True)
        try:
            with use_identity(identity_id):
                await _send_fishing_miniapp_harvest_summary(result)
            if not notice.is_current() or not (get_global_enabled() or _miniapp_http_allowed_during_pause()):
                return True
            status = str(result.get("status") or "").strip()
            if (not result.get("ok") or status in {"next_failed", "next_unavailable", "failed", "shop_failed", "request_budget"}) and status != "no_rod":
                await send_audit_log(f"🎣 灵溪垂钓 MiniApp 异常：{summary}", scope="identity", send_as_id=identity_id, limit=240)
            else:
                with use_identity(identity_id):
                    await _send_fishing_daily_completion_summary(now)
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(result if operation.owner.is_current() else None) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Fishing notification failed (%s); result preserved", type(exc).__name__)
        return True


def _fishing_identity_key(send_as_id):
    return int(send_as_id or 0)


def _fishing_send_lock(send_as_id):
    key = _fishing_identity_key(send_as_id)
    lock = _SEND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SEND_LOCKS[key] = lock
    return lock


def is_fishing_reply_text(text):
    return fishing_behavior.is_fishing_reply_text(text)


def clear_fishing_state(*, persist=False, keep_last_error=False, keep_config=True):
    if fishing_operations.pending(state) or state.get("fishing_result_pending", {}) != {}:
        if persist:
            save_state()
        return
    last_error = state.get("fishing_last_error") if keep_last_error else ""
    config_values = {}
    if keep_config:
        for key in (
            "fishing_pond",
            "fishing_bait",
            "fishing_daily_limit",
            "fishing_auto_chum_enabled",
            "fishing_chum_name",
            "fishing_chum_names",
            "fishing_auto_buy_bait_enabled",
            "fishing_auto_buy_bait_count",
            "fishing_auto_probe_enabled",
            "fishing_auto_open_fish_enabled",
            "fishing_cancel_after_sec",
            "fishing_transfer_target_id",
        ):
            config_values[key] = state.get(key)
    updates = {
        "next_fishing_time": 0,
        **fishing_behavior.clear_pending_updates(keep_open_fish=False),
        "fishing_forced_buy_bait": "",
        "fishing_forced_buy_count": 0,
        "fishing_started_at": 0,
        "fishing_active_chum_name": "",
        "fishing_chum_rods_remaining": 0,
        "fishing_chum_day": "",
        "fishing_chum_counts": "",
        "fishing_last_msg_id": 0,
        "fishing_last_result": "",
        "fishing_last_error": last_error or "",
        "fishing_transfer_due_at": 0,
        "fishing_caught_fish_json": "",
        **config_values,
    }
    _apply_updates(updates)
    if persist:
        save_state()
    else:
        mark_dirty()


def get_fishing_status_text():
    snapshot = _state_snapshot()
    config = fishing_behavior.current_fishing_config(snapshot)
    _day_key, daily_count, daily_limit, daily_updates = fishing_behavior.normalize_daily_counter(snapshot, time.time())
    if daily_updates and state.get("fishing_result_pending", {}) == {} and not fishing_operations.pending(state):
        _apply_updates(daily_updates)
        mark_dirty()
        snapshot = _state_snapshot()
    active_chum = state.get("fishing_active_chum_name") or "无"
    configured_chums = ",".join(config.chum_names or ()) or "无"
    chum_rods = _parse_int(state.get("fishing_chum_rods_remaining", 0))
    transfer_target_id = _parse_int(state.get("fishing_transfer_target_id", 0))
    transfer_target = get_identity_display_name(transfer_target_id) if transfer_target_id in get_identity_ids() else "关"
    transfer_items = fishing_behavior.pending_fishing_transfer_items(snapshot)
    identity_id = int(get_current_identity_id() or 0)
    public_selected = _cave_public_fishing_is_authoritative(identity_id)
    public_auto_enabled = is_cave_public_auto_enabled("fishing", identity_id)
    if public_auto_enabled:
        runtime_status = "公共 MiniApp 自动运行"
    elif public_selected:
        runtime_status = "已选择此身份，MiniApp 自动运行关闭"
    else:
        runtime_status = "未接入公共 MiniApp"
    lines = [
        "🎣 灵溪垂钓",
        f"- 已启用：{'是' if state.get('fishing_enabled') else '否'}",
        f"- 主动出口：{runtime_status}",
        "- 文本回退：禁用",
        f"- 鱼塘/鱼饵：{config.pond}/{config.bait}",
        f"- 今日竿数：{daily_count}/{daily_limit}",
        f"- MiniApp 打窝配置：{configured_chums}",
        f"- 当前窝料：{active_chum}（剩余 {chum_rods} 竿）",
        f"- MiniApp 缺饵购买：{'开' if config.auto_buy_bait_enabled else '关'}x{config.auto_buy_bait_count}",
        f"- 鱼获赠送：{transfer_target}",
        f"- 待赠鱼获：{_format_count_map(transfer_items)}",
        f"- 阶段：{state.get('fishing_phase') or 'idle'}",
        f"- 下次动作：{fmt_abs_ts(state.get('next_fishing_time', 0))}（{fmt_remaining(state.get('next_fishing_time', 0))}）",
        f"- 最近结果：{state.get('fishing_last_result') or '无'}",
    ]
    if state.get("fishing_result_pending", {}) != {}:
        lines.append("- 本地入账：结果待恢复，暂不启动新一轮")
    if fishing_operations.pending(state):
        lines.append(f"- 运行恢复：{fishing_operations.status_text(state.get('fishing_operation'))}")
    if state.get("fishing_last_error"):
        lines.append(f"- 最近异常：{state['fishing_last_error']}")
    return "\n".join(lines)


async def _emit_effect_audits(effect, *, limit=180):
    for message in effect.audit_messages or ():
        await send_audit_log(message, scope="identity", limit=limit)


async def _run_pending_fishing_transfer(now):
    snapshot = _state_snapshot()
    if not snapshot.get("fishing_enabled"):
        return False
    transfer_items = fishing_behavior.pending_fishing_transfer_items(snapshot)
    if not transfer_items:
        return False
    due_at = float(snapshot.get("fishing_transfer_due_at", 0) or 0)
    if due_at > float(now or 0):
        return False
    if fishing_behavior.is_rod_in_progress(snapshot):
        return False
    if (
        _parse_int(snapshot.get("fishing_reply_to_msg_id", 0)) > 0
        and float(snapshot.get("fishing_reply_due_at", 0) or 0) > float(now or 0)
    ):
        return False

    source_id = int(get_current_identity_id() or 0)
    target_id = _parse_int(snapshot.get("fishing_transfer_target_id", 0))
    known_ids = {int(identity_id) for identity_id in get_identity_ids()}
    item_text = _format_count_map(transfer_items)
    if source_id <= 0 or target_id <= 0 or source_id == target_id or target_id not in known_ids:
        state["fishing_transfer_due_at"] = float(now + FISHING_TRANSFER_RETRY_DELAY_SEC)
        state["fishing_last_error"] = f"鱼获赠送目标无效，保留待赠鱼获：{item_text}"
        save_state()
        await send_audit_log(
            f"⚠️ 灵溪垂钓鱼获赠送目标无效，已保留队列：{item_text}",
            scope="identity",
            limit=220,
        )
        return True

    gift_items = [
        {"item_name": fish, "quantity": count, "method": "gift"}
        for fish, count in sorted(transfer_items.items())
        if str(fish or "").strip() and int(count or 0) > 0
    ]
    if not gift_items:
        state["fishing_transfer_due_at"] = 0
        state["fishing_caught_fish_json"] = ""
        mark_dirty()
        return False

    try:
        ok, message, _transfer = await start_storage_bag_gift_batch(
            [{
                "source_identity_id": source_id,
                "target_identity_id": target_id,
                "items": gift_items,
            }],
            target_identity_id=target_id,
            stop_on_error=True,
        )
    except Exception as exc:
        ok = False
        message = str(exc)

    target_label = get_identity_display_name(target_id)
    if ok:
        state["fishing_caught_fish_json"] = ""
        state["fishing_transfer_due_at"] = 0
        state["fishing_last_result"] = f"鱼获赠送已入队：{item_text} -> {target_label}"
        state["fishing_last_error"] = ""
        save_state()
        await send_audit_log(
            f"🎣 灵溪垂钓鱼获已加入储物袋赠送队列：{item_text} -> {target_label}",
            scope="identity",
            limit=240,
        )
        return True

    state["fishing_transfer_due_at"] = float(now + FISHING_TRANSFER_RETRY_DELAY_SEC)
    state["fishing_last_error"] = f"鱼获赠送入队失败：{message or '未知错误'}"
    save_state()
    await send_audit_log(
        f"⚠️ 灵溪垂钓鱼获赠送入队失败，5分钟后重试：{message or '未知错误'}",
        scope="identity",
        limit=240,
    )
    return True


async def handle_fishing_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    raw_text = str(text or "").strip()
    is_routed_fishing = _is_fishing_reply(reply_to, matched_family=matched_family)
    if not state.get("fishing_enabled"):
        # Manual bait/chum/basket commands should still keep the local bag mirror fresh
        # when their replies are routed by reply_to context.
        if not is_routed_fishing:
            return False
        if not (
            fishing_behavior.parse_buy_bait_result(raw_text)
            or fishing_behavior.parse_fishing_basket(raw_text)
            or fishing_behavior.parse_chum_success_detail(raw_text)
            or fishing_behavior.parse_chum_duplicate_active_reply(raw_text)
            or fishing_behavior.parse_chum_daily_limit_reply(raw_text)
            or fishing_behavior.parse_generic_resource_shortage(raw_text)
            or fishing_behavior.parse_chum_shortage(raw_text)
            or parse_open_fish_result(raw_text)
        ):
            return False
        effect = fishing_behavior.decide_reply(
            _state_snapshot(),
            raw_text,
            now,
            result_msg_id=int(result_msg_id or _parse_int(getattr(reply_to, "id", 0)) or 0),
            action_delay_sec=random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
            post_rod_delay_sec=random.uniform(FISHING_POST_ROD_DELAY_MIN_SEC, FISHING_POST_ROD_DELAY_MAX_SEC),
        )
        if not effect.handled:
            return False
        _apply_effect(effect)
        if _queue_fishing_valuable_drop_reminders(
            raw_text,
            now,
            result_msg_id=int(result_msg_id or _parse_int(getattr(reply_to, "id", 0)) or 0),
        ):
            save_state()
        _retire_legacy_fishing_state(now)
        await _emit_effect_audits(effect)
        return True

    snapshot = _state_snapshot()
    looks_like_fishing = fishing_behavior.is_fishing_reply_text(raw_text)
    active_pending = (
        _parse_int(snapshot.get("fishing_reply_to_msg_id", 0)) > 0
        and float(snapshot.get("fishing_reply_due_at", 0) or 0) >= float(now)
    )
    if not is_routed_fishing and not (looks_like_fishing and active_pending):
        return False

    reply_to_msg_id = _parse_int(getattr(reply_to, "id", 0))
    active_ids = fishing_behavior.active_fishing_anchor_ids(snapshot)
    swallowed_reply = reply_to_msg_id <= 0 and looks_like_fishing and active_pending
    explicit_angler = _explicit_fishing_angler(raw_text)
    current_username = _current_identity_username()
    explicit_angler_matches = bool(explicit_angler and current_username and explicit_angler == current_username)
    if explicit_angler and not explicit_angler_matches:
        return False
    allow_routed_by_angler = matched_family == "fishing" and looks_like_fishing and explicit_angler_matches
    allow_open_reply = matched_family == "fishing" and _is_open_fish_reply_to_command(reply_to)
    allow_basket_reply = matched_family == "fishing" and fishing_behavior.parse_fishing_basket(raw_text)
    if active_ids and reply_to_msg_id not in active_ids and not swallowed_reply and not allow_routed_by_angler and not allow_open_reply and not allow_basket_reply:
        return False

    result_msg_id = int(result_msg_id or reply_to_msg_id or 0)
    effect = fishing_behavior.decide_reply(
        snapshot,
        raw_text,
        now,
        result_msg_id=result_msg_id,
        action_delay_sec=random.uniform(FISHING_ACTION_DELAY_MIN_SEC, FISHING_ACTION_DELAY_MAX_SEC),
        post_rod_delay_sec=random.uniform(FISHING_POST_ROD_DELAY_MIN_SEC, FISHING_POST_ROD_DELAY_MAX_SEC),
    )
    if not effect.handled:
        return False

    _apply_effect(effect)
    if _queue_fishing_valuable_drop_reminders(raw_text, now, result_msg_id=result_msg_id):
        save_state()
    _retire_legacy_fishing_state(now)
    await _emit_effect_audits(effect)
    return True


async def run_fishing_scheduler(now):
    lock = _fishing_send_lock(get_current_identity_id())
    if lock.locked():
        return
    async with lock:
        if recover_fishing_result_pending(get_current_identity_id()) is not None:
            return
        if await _run_fishing_valuable_drop_reminders(now):
            return
        if await _send_fishing_daily_completion_summary(now):
            return
        if await _run_pending_fishing_transfer(now):
            return
        if _retire_legacy_fishing_state(now):
            save_state()


def schedule_fishing_initial_check(now, *, persist=False, keep_last_error=True):
    if fishing_operations.pending(state) or state.get("fishing_result_pending", {}) != {}:
        if persist:
            save_state()
        return
    last_error = state.get("fishing_last_error") if keep_last_error else ""
    had_legacy_state = bool(
        str(state.get("fishing_phase") or "idle").strip() not in {"", "idle"}
        or str(state.get("fishing_pending_action") or "").strip()
        or _parse_int(state.get("fishing_reply_to_msg_id", 0)) > 0
        or _parse_int(state.get("fishing_status_msg_id", 0)) > 0
    )
    updates = fishing_behavior.clear_pending_updates(keep_open_fish=True)
    updates["fishing_started_at"] = 0
    updates["fishing_last_error"] = last_error or ""
    if had_legacy_state:
        updates["fishing_last_result"] = "启动恢复已清理旧文本钓鱼等待"
    current_due_at = float(state.get("next_fishing_time", 0) or 0)
    updates["next_fishing_time"] = (
        current_due_at
        if current_due_at > float(now or 0)
        else float(now + random.uniform(FISHING_RECOVERY_MIN_SEC, FISHING_RECOVERY_MAX_SEC))
    )
    _apply_updates(updates)
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_fishing_time"]


__all__ = [
    "clear_fishing_state",
    "get_fishing_status_text",
    "get_day_key",
    "is_fishing_reply_text",
    "handle_fishing_miniapp_entry",
    "handle_fishing_reply",
    "hold_unclaimed_fishing_miniapp_entry",
    "run_fishing_scheduler",
    "schedule_fishing_initial_check",
    "TZ_LOCAL",
]
