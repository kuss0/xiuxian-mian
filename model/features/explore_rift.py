import asyncio
import json
import hashlib
import math
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ..config import (
    CD_BUFFER_SEC,
    CMD_EXPLORE_RIFT,
    CMD_TIANXING_PANEL,
    CMD_REBIRTH_REQUEST,
    CMD_REBIRTH_SELECT_PREFIX,
    EXPLORE_RIFT_CD,
    EXPLORE_RIFT_FATAL_GRACE_SEC,
    EXPLORE_RIFT_JITTER_MAX_SEC,
    EXPLORE_RIFT_JITTER_MIN_SEC,
    EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC,
    EXPLORE_RIFT_REPLY_TIMEOUT_SEC,
    MESSAGES_DIR,
    RETRY_MAX_SEC,
    TZ_LOCAL,
)
from ..persistence import mark_dirty, save_state
from ..message_keys import message_key_parts
from ..message_log_recovery import _read_log_tail_lines
from ..runtime import classify_game_send_block, console_log, send_audit_log, send_game_command
from ..state import (
    REALM_SORT_INDEX,
    get_current_identity_id,
    get_game_group_ids,
    get_global_enabled,
    get_identity_account,
    get_identity_enabled,
    get_identity_state,
    get_send_as_profile,
    has_identity,
    infer_realm_from_xiuwei_max,
    state,
)
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .storage_bag import apply_storage_bag_item_deltas
from .tianxing import (
    _latest_tianxing_log_replies,
    apply_tianxing_passive,
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    looks_like_tianxing_route_result,
    normalize_tianxing_observation,
    parse_tianxing_text,
    run_tianxing_consume_craft_prediction,
    run_tianxing_timeline_scheduler,
)


EXPLORE_RIFT_PENDING_KEYWORD = "撕开一道漆黑的空间裂缝"
EXPLORE_RIFT_RESULT_TITLE = "【探寻成功】"
EXPLORE_RIFT_FATAL_TITLE = "【大凶·虚空噬体】"
EXPLORE_RIFT_ESCAPE_WEAK_TITLE = "【元婴遁逃·虚弱】"
EXPLORE_RIFT_FATE_REWRITE_TITLE = "【改命回天】"
EXPLORE_RIFT_SUCCESS_TITLES = (EXPLORE_RIFT_RESULT_TITLE, "【激战得胜】", EXPLORE_RIFT_FATE_REWRITE_TITLE)
EXPLORE_RIFT_FAILURE_TITLES = ("【遭遇风暴】", "【不敌败退】")
EXPLORE_RIFT_FINAL_TITLES = EXPLORE_RIFT_SUCCESS_TITLES + EXPLORE_RIFT_FAILURE_TITLES + (
    EXPLORE_RIFT_FATAL_TITLE,
    EXPLORE_RIFT_ESCAPE_WEAK_TITLE,
)
EXPLORE_RIFT_CD_KEYWORD = "空间裂缝尚未稳定"
EXPLORE_RIFT_XIUWEI_LIMIT = 500_000
EXPLORE_RIFT_MIN_REALM = "元婴初期"
EXPLORE_RIFT_FAST_REALM = "化神初期"
EXPLORE_RIFT_WINGS_NAME = "风雷翅"
EXPLORE_RIFT_RECOVERY_MIN_SEC = 90
EXPLORE_RIFT_RECOVERY_MAX_SEC = 180
EXPLORE_RIFT_FALLBACK_CD_SEC = EXPLORE_RIFT_CD
EXPLORE_RIFT_FAST_CD_SEC = 9 * 3600
EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC = 60
EXPLORE_RIFT_TIANJI_WAIT_BUFFER_SEC = 5 * 60
EXPLORE_RIFT_TIANJI_WAIT_MAX_SEC = 2 * 3600
EXPLORE_RIFT_SEND_UNKNOWN_WAIT_SEC = 10 * 60
EXPLORE_RIFT_UNKNOWN_PANEL_REPLY_SEC = 180
EXPLORE_RIFT_PENDING_RESULT_STALE_SEC = 10 * 60
EXPLORE_RIFT_LOG_REPLAY_LOOKBACK_SEC = 15 * 60
EXPLORE_RIFT_PENDING_RESULT_LOG_LOOKBACK_SEC = 36 * 3600
_EXPLORE_RIFT_LOCKS = {}
RE_EXPLORER_REWARD_LINE = re.compile(r"【([^】]+)】\s*[x×*＊]\s*([\d,]+)")
RE_EXPLORER_REWARD_TOKEN = re.compile(r"【([^】]+)】")
RE_EXPLORER_REWARD_CONTEXT = re.compile(r"(带来了|获得|获得了|奖励|馈赠|收获|寻得|掉落|获取|平安带回|带回了|截下|卷回)")
RE_EXPLORER_NOISE_PREFIX = re.compile(r"^[\-•·\s]+")
RE_EXPLORER_XIUWEI_GAIN = re.compile(r"修为(?:最终)?(?:增加了|增加)\s*([\d,]+)\s*点")
RE_EXPLORER_XIUWEI_LOSS = re.compile(r"修为(?:倒退了|倒退|暴跌了|损失|逸散了|逸散)\s*([\d,]+)\s*点")
RE_EXPLORER_XIUWEI_NO_LOSS = re.compile(r"(?:未损修为|未损失修为|修为未损)")
RE_EXPLORER_TIANJI_GAIN = re.compile(r"天机值\s*([+-]\s*\d+)")
RE_EXPLORER_CONTRIB_GAIN = re.compile(r"宗门贡献\s*([+-]\s*\d+)")
EXPLORE_RIFT_NON_REWARD_TOKENS = {
    "探寻成功",
    "激战得胜",
    "遭遇风暴",
    "不敌败退",
    "大凶·虚空噬体",
    "元婴遁逃·虚弱",
    "改命回天",
    "推命命中",
    "改命待发",
    "命盘",
    "天星偏转",
    "贪狼",
    "紫微",
    "天府",
    "太阴",
    "虚弱期",
}
REBIRTH_WEAK_PREFIX = "你的元婴尚在虚弱之中"
REBIRTH_SEARCHING_PREFIX = "你虚弱的元婴在天地间游荡"
REBIRTH_OPTIONS_PREFIX = "你面前出现了三具可供夺舍的肉身"
REBIRTH_BODY_INTACT_PREFIX = "你肉身完好，神魂稳固"
REBIRTH_SUCCESS_PREFIX = "夺舍成功！"
REBIRTH_AUTO_SELECT_PREFIX = "【天道代择】"
REBIRTH_BLIND_SELECT_INDEX = 1
REBIRTH_CHOICE_MODES = ("safe_first", "root_first")
REBIRTH_ROOT_TYPES = ("", "天灵根", "异灵根", "伪灵根", "废灵根")
RE_REBIRTH_OPTION_HEADER = re.compile(r"(?m)^\s*(?P<index>[123])\.\s*【夺舍\s+(?P<name>[^】]+)】\s*$")
RE_REBIRTH_OPTION_FIELD = re.compile(r"(?m)^\s*-\s*(?P<key>灵根|命途)\s*[:：]\s*(?P<value>[^\n]+)\s*$")


def _explore_rift_lock():
    identity_id = int(get_current_identity_id() or 0)
    lock = _EXPLORE_RIFT_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _EXPLORE_RIFT_LOCKS[identity_id] = lock
    return lock
RE_ROOT_ATTRS = re.compile(r"\(([^)]*)\)")
RE_REBIRTH_ATTR_SPLIT = re.compile(r"[\s,，、/|]+")


def _parse_int(value, default=0):
    try:
        return int(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _parse_float(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_message_log_ts(raw_ts):
    raw = str(raw_ts or "").strip()
    for suffix in (" UTC+8", "+08:00"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=TZ_LOCAL).timestamp()
        except ValueError:
            continue
    return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    start_dt = datetime.fromtimestamp(max(0.0, float(start_ts or 0)), TZ_LOCAL).date()
    end_dt = datetime.fromtimestamp(max(float(start_ts or 0), float(end_ts or 0)), TZ_LOCAL).date()
    day = start_dt
    while day <= end_dt:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        for line in _read_log_tail_lines(log_path, max_bytes=512 * 1024):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                yield payload
        day += timedelta(days=1)


def _identity_sender_matches(sender_id, send_as_id):
    try:
        sender_id = int(sender_id or 0)
        send_as_id = int(send_as_id or 0)
    except (TypeError, ValueError):
        return False
    if sender_id == send_as_id:
        return True
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                return int(sender_abs[3:] or 0) == send_as_id
            except ValueError:
                return False
    return False


def _make_result_key(result_msg_id, title, text):
    digest = hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:16]
    return f"{int(result_msg_id or 0)}:{title}:{digest}"


def _profile_field(name, default=None):
    profile = get_send_as_profile(get_current_identity_id()) or {}
    return profile.get(name, default)


def _profile_realm():
    profile = get_send_as_profile(get_current_identity_id()) or {}
    realm = str(profile.get("realm") or "").strip()
    if realm:
        return realm
    return infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))


def _profile_realm_index():
    realm = _profile_realm()
    if not realm:
        return None
    return REALM_SORT_INDEX.get(realm)


def _realm_at_least(min_realm):
    realm_index = _profile_realm_index()
    min_index = REALM_SORT_INDEX.get(str(min_realm or "").strip())
    if realm_index is None or min_index is None:
        return False
    return realm_index >= min_index


def _profile_xiuwei_current():
    value = _parse_int(_profile_field("xiuwei_current", 0))
    return value if value > 0 else None


def _storage_has_fenglei_wings():
    # 背包持有不等于已装备；当前主线没有可信的本地“已装备风雷翅”字段。
    return False


def _resolve_cd_sec():
    if _realm_at_least(EXPLORE_RIFT_FAST_REALM) and _storage_has_fenglei_wings():
        return EXPLORE_RIFT_FAST_CD_SEC
    return EXPLORE_RIFT_FALLBACK_CD_SEC


def _schedule_next_explore_rift(now, delay_sec=None):
    if delay_sec is None:
        delay_sec = _resolve_cd_sec() + random.uniform(EXPLORE_RIFT_JITTER_MIN_SEC, EXPLORE_RIFT_JITTER_MAX_SEC)
    state["next_explore_rift_time"] = float(now + max(1, delay_sec))
    return state["next_explore_rift_time"]


def _clear_explore_rift_pending():
    state["explore_rift_reply_to_msg_id"] = 0
    state["explore_rift_reply_due_at"] = 0
    state["explore_rift_pending_result_msg_id"] = 0


def _clear_explore_rift_fatal_pending():
    state["explore_rift_fatal_msg_id"] = 0
    state["explore_rift_fatal_confirm_due_at"] = 0


def _clear_explore_rift_rebirth_pending():
    state["explore_rift_rebirth_due_at"] = 0
    state["explore_rift_rebirth_request_msg_id"] = 0
    state["explore_rift_rebirth_options_msg_id"] = 0
    state["explore_rift_rebirth_select_msg_id"] = 0


def _clear_explore_rift_rebirth_state():
    state["explore_rift_nascent_escape_weak_until"] = 0
    state["explore_rift_rebirth_required"] = False
    state["explore_rift_rebirth_phase"] = "idle"
    _clear_explore_rift_rebirth_pending()
    state["explore_rift_rebirth_options_text"] = ""
    state["explore_rift_rebirth_selected_index"] = 0
    state["explore_rift_rebirth_last_result"] = ""
    state["explore_rift_rebirth_last_error"] = ""


def _set_explore_rift_pending_result(result_msg_id, now=None, *, reply_context=None):
    result_msg_id = int(result_msg_id or 0)
    if result_msg_id <= 0:
        return False
    now = float(now if now is not None else time.time())
    changed = False
    if _has_unknown_rift() and isinstance(reply_context, dict):
        _observed, snapshot = _unknown_rift_snapshot()
        snapshot.update(
            command_msg_id=_rift_log_id(reply_context.get("root_msg_id") or reply_context.get("reply_to_msg_id")),
            command_chat_id=_rift_log_id(reply_context.get("chat_id")),
        )
        _store_unknown_rift_snapshot(snapshot)
    if int(state.get("explore_rift_reply_to_msg_id", 0) or 0) != 0:
        state["explore_rift_reply_to_msg_id"] = 0
        changed = True
    if int(state.get("explore_rift_pending_result_msg_id", 0) or 0) != result_msg_id:
        state["explore_rift_pending_result_msg_id"] = result_msg_id
        changed = True
    if int(state.get("explore_rift_last_msg_id", 0) or 0) != result_msg_id:
        state["explore_rift_last_msg_id"] = result_msg_id
        changed = True
    result_due_at = now + EXPLORE_RIFT_REPLY_TIMEOUT_SEC
    if float(state.get("explore_rift_reply_due_at", 0) or 0) != result_due_at:
        state["explore_rift_reply_due_at"] = result_due_at
        changed = True
    fallback_next_time = now + _resolve_cd_sec()
    if float(state.get("next_explore_rift_time", 0) or 0) < fallback_next_time:
        state["next_explore_rift_time"] = fallback_next_time
        changed = True
    return changed


def _has_terminal_result_for_msg(result_msg_id):
    result_msg_id = int(result_msg_id or 0)
    if result_msg_id <= 0:
        return False
    return str(state.get("explore_rift_last_result_key") or "").startswith(f"{result_msg_id}:")


def clear_explore_rift_state(*, persist=False, keep_last_error=False):
    last_error = state.get("explore_rift_last_error") if keep_last_error else ""
    state["next_explore_rift_time"] = 0
    _clear_explore_rift_pending()
    _clear_explore_rift_fatal_pending()
    _clear_explore_rift_rebirth_state()
    state["explore_rift_last_msg_id"] = 0
    state["explore_rift_last_result"] = ""
    state["explore_rift_last_error"] = last_error or ""
    state["explore_rift_last_result_key"] = ""
    state["explore_rift_manual_required"] = False
    if persist:
        save_state()
    else:
        mark_dirty()


def _set_explore_rift_error(message, *, next_delay=None, now=None, persist=True):
    state["explore_rift_last_error"] = str(message or "").strip()
    if next_delay is not None:
        if now is None:
            now = time.time()
        state["next_explore_rift_time"] = float(now + max(1, next_delay))
    if persist:
        save_state()
    else:
        mark_dirty()


def _schedule_explore_rift_tianxing_prepare_retry(now, due_at, delay_sec=None):
    delay_sec = float(delay_sec or EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC)
    retry_at = float(now or 0) + max(1.0, delay_sec)
    due_at = float(due_at or now or 0)
    if due_at <= float(now or 0):
        state["next_explore_rift_time"] = retry_at
    else:
        state["explore_rift_tianxing_prepare_retry_at"] = min(due_at, retry_at)


def _next_explore_rift_tianji_retry_at(now):
    now = float(now or 0)
    candidates = []
    if state.get("wild_training_enabled"):
        next_wild = _parse_float(state.get("next_wild_training_time", 0), 0.0)
        if next_wild > now:
            candidates.append(next_wild + EXPLORE_RIFT_TIANJI_WAIT_BUFFER_SEC)
        else:
            candidates.append(now + EXPLORE_RIFT_TIANJI_WAIT_BUFFER_SEC)
    observed = state.get("tianxing_observation") if isinstance(state.get("tianxing_observation"), dict) else {}
    prediction_until = _parse_float(observed.get("current_prediction_until", 0), 0.0)
    if prediction_until > now:
        candidates.append(prediction_until + EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC)
    candidates.append(now + EXPLORE_RIFT_TIANJI_WAIT_MAX_SEC)
    return min(candidates) if candidates else now + RETRY_MAX_SEC


def _schedule_explore_rift_tianji_wait(now, due_at):
    retry_at = _next_explore_rift_tianji_retry_at(now)
    now = float(now or 0)
    due_at = float(due_at or now)
    if due_at <= now + EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC:
        state["next_explore_rift_time"] = retry_at
        state["explore_rift_tianxing_prepare_retry_at"] = 0
    else:
        state["explore_rift_tianxing_prepare_retry_at"] = min(due_at, retry_at)
    return retry_at


def _explore_rift_next_time_blocks(now):
    return cd_blocks(state.get("next_explore_rift_time", 0), now, 0)


def _tianxing_explore_change_ready(now):
    if not state.get("tianxing_enabled"):
        return False
    preflight = build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now, require_change_fate=True)
    return bool(
        preflight.get("route_allowed")
        and str(preflight.get("stage") or "") in {"change_fate_active", "timeline_released"}
    )


def _pull_ready_tianxing_explore_retry(now, next_explore_rift_time):
    if float(next_explore_rift_time or 0) <= float(now or 0):
        return False
    if int(state.get("explore_rift_reply_to_msg_id", 0) or 0) > 0:
        return False
    if int(state.get("explore_rift_pending_result_msg_id", 0) or 0) > 0:
        return False
    if not _tianxing_explore_change_ready(now):
        return False
    last_result = str(state.get("explore_rift_last_result") or "").strip()
    if not (
        last_result.startswith("天星时间线：")
        or last_result.startswith("天星先炼制消费推命：")
    ):
        return False
    if (
        "need_tianji_for_change" not in last_result
        and float(next_explore_rift_time or 0) - float(now or 0) > RETRY_MAX_SEC + 60
    ):
        return False
    state["next_explore_rift_time"] = float(now)
    state["explore_rift_tianxing_prepare_retry_at"] = 0
    state["explore_rift_last_error"] = ""
    save_state()
    console_log("🕳 探寻裂缝天星前置已就绪，拉回到期时间立即消费。", scope="identity")
    return True


def _is_explore_rift_reply(reply_to=None, matched_family=None):
    if matched_family == "explore_rift":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    if orig_cmd == CMD_EXPLORE_RIFT or orig_cmd.startswith(f"{CMD_EXPLORE_RIFT} "):
        return True
    if orig_cmd == CMD_REBIRTH_REQUEST:
        return True
    return orig_cmd == CMD_REBIRTH_SELECT_PREFIX or orig_cmd.startswith(f"{CMD_REBIRTH_SELECT_PREFIX} ")


def _reward_from_line(line):
    raw_line = RE_EXPLORER_NOISE_PREFIX.sub("", str(line or "").strip())
    if not raw_line:
        return {}
    item_deltas = {}
    explicit_spans = []
    for match in RE_EXPLORER_REWARD_LINE.finditer(raw_line):
        explicit_spans.append(match.span(1))
        name = str(match.group(1) or "").strip()
        count = _parse_int(match.group(2))
        if name and count > 0:
            item_deltas[name] = item_deltas.get(name, 0) + count
    if item_deltas and not RE_EXPLORER_REWARD_CONTEXT.search(raw_line):
        return item_deltas
    if not RE_EXPLORER_REWARD_CONTEXT.search(raw_line):
        return {}
    for match in RE_EXPLORER_REWARD_TOKEN.finditer(raw_line):
        name = str(match.group(1) or "").strip()
        if match.span(1) in explicit_spans:
            continue
        if not name or name in EXPLORE_RIFT_NON_REWARD_TOKENS:
            continue
        item_deltas[name] = item_deltas.get(name, 0) + 1
    return item_deltas


def _strip_title(raw_text):
    first_line = str(raw_text or "").strip().splitlines()[0] if str(raw_text or "").strip() else ""
    if first_line.startswith("【") and "】" in first_line:
        return first_line.split("】", 1)[0].strip("【")
    return ""


def parse_explore_rift_result_summary(text):
    raw_text = str(text or "").strip()
    parts = []
    item_deltas = {}
    title = _strip_title(raw_text)

    xiuwei_match = RE_EXPLORER_XIUWEI_GAIN.search(raw_text)
    if xiuwei_match:
        xiuwei_gain = _parse_int(xiuwei_match.group(1))
        if xiuwei_gain > 0:
            parts.append(f"修为 +{xiuwei_gain}")
    xiuwei_loss_match = RE_EXPLORER_XIUWEI_LOSS.search(raw_text)
    if xiuwei_loss_match:
        xiuwei_loss = _parse_int(xiuwei_loss_match.group(1))
        parts.append(f"修为 -{xiuwei_loss}")
    elif RE_EXPLORER_XIUWEI_NO_LOSS.search(raw_text):
        parts.append("修为未损")

    tianji_match = RE_EXPLORER_TIANJI_GAIN.search(raw_text)
    if tianji_match:
        parts.append(f"天机{tianji_match.group(1).replace(' ', '')}")
    contrib_match = RE_EXPLORER_CONTRIB_GAIN.search(raw_text)
    if contrib_match:
        parts.append(f"贡献{contrib_match.group(1).replace(' ', '')}")

    for line in raw_text.splitlines():
        line_deltas = _reward_from_line(line)
        if not line_deltas:
            continue
        for item_name, count in line_deltas.items():
            item_deltas[item_name] = item_deltas.get(item_name, 0) + count

    if item_deltas:
        parts.append("奖励：" + "、".join(f"{name}x{count}" for name, count in item_deltas.items()))

    if title in {"遭遇风暴", "不敌败退"} and parts:
        parts.insert(0, title)

    return (" ｜ ".join(parts) if parts else "探寻裂缝成功"), item_deltas


async def _send_tianxing_explore_rift_result_audit(raw_text, result_summary):
    if not looks_like_tianxing_route_result(raw_text):
        return False
    await send_audit_log(
        f"🌌 天星探索结果｜探寻裂缝：{result_summary or '未知结果'}",
        scope="identity",
        priority="high",
        limit=260,
    )
    return True


def _apply_tianxing_explore_rift_result(raw_text, now, *, reply_context=None):
    if looks_like_tianxing_route_result(raw_text):
        apply_tianxing_passive(raw_text, now=now, family="explore_rift", reply_context=reply_context)


def _is_explore_rift_terminal_success(raw_text):
    return any(str(raw_text or "").strip().startswith(title) for title in EXPLORE_RIFT_SUCCESS_TITLES)


def _is_explore_rift_terminal_failure(raw_text):
    return any(str(raw_text or "").strip().startswith(title) for title in EXPLORE_RIFT_FAILURE_TITLES)


def _explore_rift_final_title(raw_text):
    text = str(raw_text or "").strip()
    for title in EXPLORE_RIFT_FINAL_TITLES:
        if text.startswith(title):
            return title
    return ""


def classify_rebirth_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return "unknown"
    if raw_text.startswith(REBIRTH_WEAK_PREFIX):
        return "weak"
    if raw_text.startswith(REBIRTH_SEARCHING_PREFIX):
        return "searching"
    if raw_text.startswith(REBIRTH_OPTIONS_PREFIX):
        return "options"
    if raw_text.startswith(REBIRTH_BODY_INTACT_PREFIX):
        return "body_intact"
    if raw_text.startswith(REBIRTH_SUCCESS_PREFIX):
        return "success"
    if raw_text.startswith(REBIRTH_AUTO_SELECT_PREFIX):
        return "success"
    if "夺舍失败" in raw_text or "连接司命星君失败" in raw_text or "无效的选择" in raw_text or "请选择有效" in raw_text:
        return "failure"
    return "unknown"


def parse_rebirth_options(text):
    raw_text = str(text or "")
    options = []
    headers = list(RE_REBIRTH_OPTION_HEADER.finditer(raw_text))
    for idx, match in enumerate(headers):
        block_end = headers[idx + 1].start() if idx + 1 < len(headers) else len(raw_text)
        block = raw_text[match.end():block_end]
        fields = {}
        for field_match in RE_REBIRTH_OPTION_FIELD.finditer(block):
            fields[str(field_match.group("key") or "").strip()] = str(field_match.group("value") or "").strip()
        root_text = fields.get("灵根", "")
        attr_match = RE_ROOT_ATTRS.search(root_text)
        attrs = attr_match.group(1).strip() if attr_match else ""
        root_type = RE_ROOT_ATTRS.sub("", root_text).strip()
        options.append(
            {
                "index": _parse_int(match.group("index")),
                "name": str(match.group("name") or "").strip(),
                "root_text": root_text,
                "root_type": root_type,
                "attrs": attrs,
                "fate": fields.get("命途", ""),
            }
        )
    return options


def _normalize_rebirth_choice_mode(value):
    mode = str(value or "").strip().lower()
    return mode if mode in REBIRTH_CHOICE_MODES else "safe_first"


def _normalize_rebirth_root_type(value):
    root_type = str(value or "").strip()
    return root_type if root_type in REBIRTH_ROOT_TYPES else ""


def _normalize_rebirth_attrs(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = []
    for item in RE_REBIRTH_ATTR_SPLIT.split(raw):
        item = str(item or "").strip()
        if item and item not in parts:
            parts.append(item)
    return "、".join(parts)


def _normalize_rebirth_blind_index(value):
    index = _parse_int(value, REBIRTH_BLIND_SELECT_INDEX)
    return index if index in {1, 2, 3} else REBIRTH_BLIND_SELECT_INDEX


def get_rebirth_choice_config():
    def get_value(key, default):
        try:
            return state.get(key, default)
        except KeyError:
            return default

    attrs_text = _normalize_rebirth_attrs(get_value("explore_rift_rebirth_preferred_attrs", ""))
    return {
        "choice_mode": _normalize_rebirth_choice_mode(get_value("explore_rift_rebirth_choice_mode", "safe_first")),
        "preferred_root_type": _normalize_rebirth_root_type(get_value("explore_rift_rebirth_preferred_root_type", "")),
        "preferred_attrs": attrs_text,
        "preferred_attrs_list": [item for item in attrs_text.split("、") if item],
        "blind_index": _normalize_rebirth_blind_index(get_value("explore_rift_rebirth_blind_index", REBIRTH_BLIND_SELECT_INDEX)),
    }


def set_rebirth_choice_config(choice_mode=None, preferred_root_type=None, preferred_attrs=None, blind_index=None):
    config = get_rebirth_choice_config()
    if choice_mode is not None:
        config["choice_mode"] = _normalize_rebirth_choice_mode(choice_mode)
    if preferred_root_type is not None:
        config["preferred_root_type"] = _normalize_rebirth_root_type(preferred_root_type)
    if preferred_attrs is not None:
        attrs_text = _normalize_rebirth_attrs(preferred_attrs)
        config["preferred_attrs"] = attrs_text
        config["preferred_attrs_list"] = [item for item in attrs_text.split("、") if item]
    if blind_index is not None:
        config["blind_index"] = _normalize_rebirth_blind_index(blind_index)
    state["explore_rift_rebirth_choice_mode"] = config["choice_mode"]
    state["explore_rift_rebirth_preferred_root_type"] = config["preferred_root_type"]
    state["explore_rift_rebirth_preferred_attrs"] = config["preferred_attrs"]
    state["explore_rift_rebirth_blind_index"] = config["blind_index"]
    return config


def _rebirth_option_root_score(option, config):
    option = option or {}
    config = config or {}
    score = 0
    preferred_root_type = str(config.get("preferred_root_type") or "").strip()
    if preferred_root_type and str(option.get("root_type") or "").strip() == preferred_root_type:
        score += 10
    preferred_attrs = [str(item).strip() for item in config.get("preferred_attrs_list") or [] if str(item or "").strip()]
    if preferred_attrs:
        option_attrs_text = f"{option.get('attrs') or ''}{option.get('root_text') or ''}"
        attr_hits = sum(1 for item in preferred_attrs if item in option_attrs_text)
        if attr_hits:
            score += attr_hits
    return score


def _pick_best_rebirth_option(options, config, *, require_safe):
    candidates = []
    for order, option in enumerate(options or []):
        if require_safe and str((option or {}).get("fate") or "").strip() != "稳妥之身":
            continue
        score = _rebirth_option_root_score(option, config)
        candidates.append((score, -order, option))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if candidates[0][0] <= 0 and not require_safe:
        return None
    return candidates[0][2]


def choose_safe_rebirth_option(options, config=None):
    config = config or get_rebirth_choice_config()
    mode = _normalize_rebirth_choice_mode(config.get("choice_mode"))
    if mode == "root_first":
        preferred = _pick_best_rebirth_option(options, config, require_safe=False)
        if preferred:
            return preferred
    safe = _pick_best_rebirth_option(options, config, require_safe=True)
    if safe:
        return safe
    if mode == "root_first":
        return _pick_best_rebirth_option(options, config, require_safe=False)
    return None


def _is_rebirth_reply_context(reply_to, raw_text):
    reply_command = str(getattr(reply_to, "raw_text", "") or "").strip() if reply_to else ""
    if reply_command == CMD_REBIRTH_REQUEST:
        return True
    if reply_command == CMD_REBIRTH_SELECT_PREFIX or reply_command.startswith(f"{CMD_REBIRTH_SELECT_PREFIX} "):
        return True
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0) if reply_to else 0
    if reply_to_msg_id > 0 and reply_to_msg_id in {
        int(state.get("explore_rift_rebirth_request_msg_id", 0) or 0),
        int(state.get("explore_rift_rebirth_select_msg_id", 0) or 0),
        int(state.get("explore_rift_rebirth_options_msg_id", 0) or 0),
    }:
        return True
    return classify_rebirth_text(raw_text) != "unknown"


def is_explore_rift_reply_text(text):
    raw_text = str(text or "").strip()
    return (
        EXPLORE_RIFT_PENDING_KEYWORD in raw_text
        or bool(_explore_rift_final_title(raw_text))
        or EXPLORE_RIFT_CD_KEYWORD in raw_text
        or "时空异兽" in raw_text
        or classify_rebirth_text(raw_text) != "unknown"
    )


def _is_unknown_send_summary(value):
    summary = str(value or "").strip()
    return (
        summary.startswith("发送状态未知，等待被动回复")
        or summary.startswith("发送状态未知，等待天机盘消费校准")
        or summary.startswith("天机盘校准超时，等待迟到盘面")
        or summary.startswith("发送状态未知且未捞到反馈")
        or summary.startswith("天机盘校准矛盾")
        or summary.startswith("天机盘校准：探索保护均未消费")
    )


def _has_unknown_rift():
    observed = state.get("tianxing_observation")
    return (
        isinstance(observed, dict) and "explore_rift_unknown_snapshot" in observed
    ) or _is_unknown_send_summary(state.get("explore_rift_last_result"))


def has_unresolved_explore_rift():
    return _has_unknown_rift() or any(_rift_log_id(state.get(key)) > 0 for key in (
        "explore_rift_reply_to_msg_id", "explore_rift_pending_result_msg_id",
        "explore_rift_fatal_msg_id", "explore_rift_rebirth_request_msg_id",
        "explore_rift_rebirth_options_msg_id", "explore_rift_rebirth_select_msg_id",
    ))


def _store_unknown_rift_snapshot(snapshot):
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    observed["explore_rift_unknown_snapshot"] = dict(snapshot)
    state["tianxing_observation"] = observed
    mark_dirty()


def _unknown_rift_owner_matches(snapshot):
    identity_id = get_current_identity_id()
    return has_identity(identity_id) and all(
        key not in snapshot or _rift_log_id(snapshot[key]) == expected
        for key, expected in (
            ("identity_id", identity_id), ("account_id", get_identity_account(identity_id)),
        )
    )


def _mark_explore_rift_send_unknown(now):
    wait_until = float(now or 0) + EXPLORE_RIFT_SEND_UNKNOWN_WAIT_SEC
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    _observed, snapshot = _unknown_rift_snapshot(observed)
    msg_id = _rift_log_id(state.get("explore_rift_reply_to_msg_id"))
    command = _known_rift_command(msg_id, now) if msg_id > 0 and not snapshot else {}
    snapshot = snapshot or {
        "op_id": uuid4().hex,
        "identity_id": get_current_identity_id(),
        "account_id": get_identity_account(get_current_identity_id()),
        "command_msg_id": msg_id,
        "command_chat_id": command.get("chat_id", 0),
        "command_started_at": command.get("event_at", 0),
        "recorded_at": float(now or 0),
        "prediction": str(observed.get("current_prediction") or "").strip(),
        "prediction_set_at": float(observed.get("current_prediction_set_at", 0) or 0),
        "change": str(observed.get("current_change") or "").strip(),
        "change_set_at": float(observed.get("current_change_set_at", 0) or 0),
        "panel_sent_at": 0.0,
        "panel_msg_id": 0,
    }
    _store_unknown_rift_snapshot(snapshot)
    state["explore_rift_last_result"] = "发送状态未知，等待被动回复或冷却校准"
    state["explore_rift_last_error"] = "探寻裂缝发送状态未知，先等待被动结果，避免重复消耗"
    state["next_explore_rift_time"] = max(float(state.get("next_explore_rift_time", 0) or 0), wait_until)
    state["explore_rift_reply_due_at"] = wait_until
    state["explore_rift_tianxing_prepare_retry_at"] = 0
    state["explore_rift_manual_required"] = True
    return wait_until


def _unknown_rift_snapshot(observed=None):
    observed = normalize_tianxing_observation(
        state.get("tianxing_observation") if observed is None else observed
    )
    snapshot = observed.get("explore_rift_unknown_snapshot")
    return observed, dict(snapshot) if isinstance(snapshot, dict) else {}


def _clear_unknown_rift_snapshot(observed=None):
    observed = normalize_tianxing_observation(
        state.get("tianxing_observation") if observed is None else observed
    )
    observed.pop("explore_rift_unknown_snapshot", None)
    state["tianxing_observation"] = observed
    mark_dirty()
    return observed


def _unknown_rift_reply_matches(context, result_msg_id, now):
    if not isinstance(context, dict) or _rift_log_id(context.get("send_as_id")) != get_current_identity_id():
        return False
    root = _rift_log_id(context.get("root_msg_id") or context.get("reply_to_msg_id"))
    chat = _rift_log_id(context.get("chat_id"))
    event_at = _rift_log_time(context.get("server_event_at"))
    if (
        root <= 0 or not chat or _rift_log_id(context.get("msg_id")) != result_msg_id
        or context.get("event_type") not in ("message", "edit")
        or not 0 < event_at <= max(now, _rift_log_time(context.get("processed_at"))) + 1
        or (context.get("reply_to_msg_id") and _rift_log_id(context["reply_to_msg_id"]) != root)
    ):
        return False
    _observed, snapshot = _unknown_rift_snapshot()
    if not _unknown_rift_owner_matches(snapshot):
        return False
    expected_id = _rift_log_id(snapshot.get("command_msg_id")) or _rift_log_id(state.get("explore_rift_reply_to_msg_id"))
    expected_chat = _rift_log_id(snapshot.get("command_chat_id"))
    started_at = _rift_log_time(snapshot.get("command_started_at"))
    if expected_id and not expected_chat:
        command = _known_rift_command(expected_id, now)
        expected_chat = command.get("chat_id", 0)
        started_at = started_at or command.get("event_at", 0)
    if not expected_id:
        command = _find_recent_logged_explore_rift_command(now)
        if not command:
            return False
        expected_id, expected_chat = command["msg_id"], command["chat_id"]
        started_at = command.get("event_at", 0)
    return root == expected_id and chat == expected_chat and started_at <= event_at + 1


def _finish_unknown_rift():
    _clear_unknown_rift_snapshot()
    state["explore_rift_manual_required"] = False


def _reconcile_unknown_rift_from_panel(now):
    _observed, snapshot = _unknown_rift_snapshot()
    evidence = snapshot.get("panel_evidence")
    if not isinstance(evidence, dict) or evidence.get("key") == snapshot.get("panel_reported_key"):
        return ""
    event_at = _rift_log_time(evidence.get("at"))
    if not 0 < event_at <= now + 1 or not evidence.get("complete"):
        return ""
    # A panel describes effects, not whether this particular rift executed.
    snapshot["panel_reported_key"] = evidence.get("key")
    _store_unknown_rift_snapshot(snapshot)
    state["explore_rift_last_result"] = "裂缝结果未知，天机盘已校准；等待原命令回包"
    state["explore_rift_last_error"] = "盘面变化不能唯一证明裂缝结果，不自动重发探寻裂缝"
    state["explore_rift_manual_required"] = True
    return "unresolved_hold"


async def _request_unknown_rift_panel(now):
    _observed, snapshot = _unknown_rift_snapshot()
    identity_id = get_current_identity_id()
    identity = get_identity_state(identity_id)
    account_id = get_identity_account(identity_id)
    if (
        not snapshot or not _unknown_rift_owner_matches(snapshot)
        or not get_global_enabled() or not get_identity_enabled(identity_id)
        or not identity.get("explore_rift_enabled") or not identity.get("tianxing_enabled")
        or snapshot.get("panel_status") in ("sending", "sent", "unknown")
        or _rift_log_time(snapshot.get("panel_sent_at")) > 0
        or _rift_log_time(snapshot.get("panel_next_time")) > now
    ):
        return False
    operation_id = snapshot.setdefault("op_id", uuid4().hex)
    query_id = uuid4().hex
    snapshot.update(panel_status="sending", panel_started_at=now, panel_op_id=query_id)
    _store_unknown_rift_snapshot(snapshot)
    state["explore_rift_reply_due_at"] = now + EXPLORE_RIFT_UNKNOWN_PANEL_REPLY_SEC
    state["next_explore_rift_time"] = max(_rift_log_time(state.get("next_explore_rift_time")), state["explore_rift_reply_due_at"])
    if not save_state():
        snapshot.update(panel_status="unsent", panel_next_time=now + RETRY_MAX_SEC)
        _store_unknown_rift_snapshot(snapshot)
        state["explore_rift_last_error"] = "查盘等待状态未保存，本次未发送"
        return False

    def owns_query():
        if not has_identity(identity_id) or get_identity_state(identity_id) is not identity or get_identity_account(identity_id) != account_id:
            return False
        _latest, current = _unknown_rift_snapshot()
        return current.get("op_id") == operation_id and current.get("panel_op_id") == query_id

    def can_send():
        if not owns_query():
            return False
        _latest, current = _unknown_rift_snapshot()
        return current.get("panel_status") == "sending" and get_global_enabled() and get_identity_enabled(identity_id) and bool(identity.get("explore_rift_enabled")) and bool(identity.get("tianxing_enabled"))

    msg = await send_game_command(
        CMD_TIANXING_PANEL,
        track=False,
        max_retry=0,
        priority="chain",
        source_module="探寻裂缝",
        op_id=query_id,
        operation_check=can_send,
    )
    if not owns_query():
        return False
    _observed, snapshot = _unknown_rift_snapshot()
    msg_id = _rift_log_id(getattr(msg, "id", 0))
    sent_at = _rift_log_time(getattr(msg, "sent_at", 0))
    if not msg and _is_explore_rift_unsent_block(classify_game_send_block(identity_id, CMD_TIANXING_PANEL)):
        snapshot.update(panel_status="unsent", panel_next_time=now + RETRY_MAX_SEC)
        _store_unknown_rift_snapshot(snapshot)
        state["explore_rift_last_error"] = "裂缝状态未知，天机盘校准命令未发送，稍后低频重试"
        save_state()
        return False
    if msg_id > 0 and sent_at > 0:
        snapshot.update(panel_status="sent", panel_sent_at=sent_at, panel_msg_id=msg_id, panel_chat_id=_rift_log_id(getattr(msg, "chat_id", 0)))
    else:
        snapshot["panel_status"] = "unknown"
    _store_unknown_rift_snapshot(snapshot)
    state["explore_rift_last_result"] = "发送状态未知，等待天机盘消费校准"
    state["explore_rift_last_error"] = "查盘仅校准保护状态，裂缝仍等待原命令结果；不会重复查盘"
    save_state()
    return True


def _is_explore_rift_unsent_block(send_block):
    return str((send_block or {}).get("status") or "") == "unsent"


def _explore_rift_block_label(send_block):
    code = str((send_block or {}).get("code") or "runtime_block")
    reason = str((send_block or {}).get("reason") or "").strip()
    return f"{code}: {reason}" if reason else code


def _rift_log_id(value):
    return value if type(value) is int else 0


def _rift_log_time(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return 0.0
    try:
        return float(value) if math.isfinite(value) and value > 0 else 0.0
    except (ValueError, OverflowError):
        return 0.0


def _rift_log_entries(now, lookback):
    start = max(0.0, now - lookback)
    entries = []
    for entry in _iter_message_log_entries_between(start, now + 1):
        received_at = _parse_message_log_ts(entry.get("ts"))
        if start <= received_at <= now + 1:
            entries.append(dict(entry, ts_epoch=received_at))
    return entries


def _rift_log_command_owners(entries, command, now):
    identity_id = get_current_identity_id()
    if identity_id <= 0 or not has_identity(identity_id):
        return {}
    account_id = get_identity_account(identity_id)
    allowed_chats = set(get_game_group_ids())
    owners = {}
    for key, pending in state.get("pending_tasks", {}).items():
        if not isinstance(pending, dict) or pending.get("cmd") != command:
            continue
        if "account_id" in pending and _rift_log_id(pending["account_id"]) != account_id:
            continue
        try:
            chat_id, msg_id = message_key_parts(key, pending)
        except (TypeError, ValueError, OverflowError):
            continue
        sent_at = _rift_log_time(pending.get("sent_at"))
        started_at = _rift_log_time(pending.get("send_started_at"))
        if not chat_id or msg_id <= 0 or not 0 < sent_at <= now + 1 or started_at > sent_at:
            continue
        owners[chat_id, msg_id] = {"chat_id": chat_id, "msg_id": msg_id, "ts": sent_at, "event_at": started_at, "text": command, "op_id": pending.get("op_id", "")}
    for entry in entries:
        if entry.get("event_type") not in ("sent", "message") or str(entry.get("text") or "").strip() != command:
            continue
        chat_id = _rift_log_id(entry.get("chat_id"))
        msg_id = _rift_log_id(entry.get("message_id"))
        sender_id = _rift_log_id(entry.get("sender_id"))
        if chat_id not in allowed_chats or msg_id <= 0 or not _identity_sender_matches(sender_id, identity_id):
            continue
        if "account_id" in entry and _rift_log_id(entry["account_id"]) != account_id:
            continue
        event_at = _rift_log_time(entry.get("server_event_at"))
        if entry["event_type"] == "message" and not 0 < event_at <= entry["ts_epoch"] + 1:
            continue
        key = chat_id, msg_id
        previous = owners.get(key, {})
        owners[key] = {
            "chat_id": chat_id, "msg_id": msg_id, "text": command,
            "ts": min(entry["ts_epoch"], previous.get("ts", entry["ts_epoch"])),
            "event_at": event_at or previous.get("event_at", 0),
            "op_id": entry.get("op_id") or previous.get("op_id", ""),
        }
    return owners


def _known_rift_command(msg_id, now):
    owners = _rift_log_command_owners([], CMD_EXPLORE_RIFT, now)
    for key, sent_at in state.get("my_msg_ids", {}).items():
        try:
            chat_id, recorded_id = message_key_parts(key)
        except (TypeError, ValueError, OverflowError):
            continue
        if chat_id and recorded_id == msg_id and _rift_log_time(sent_at):
            owners.setdefault((chat_id, msg_id), {"chat_id": chat_id, "msg_id": msg_id})
    matches = [entry for entry in owners.values() if entry["msg_id"] == msg_id]
    if not matches:
        entries = _rift_log_entries(now, EXPLORE_RIFT_PENDING_RESULT_LOG_LOOKBACK_SEC)
        matches = [entry for entry in _rift_log_command_owners(entries, CMD_EXPLORE_RIFT, now).values() if entry["msg_id"] == msg_id]
    return matches[0] if len(matches) == 1 else {}


def _find_owned_rift_log_reply(command, now, *, command_msg_id=0, command_chat_id=0, result_msg_id=0):
    entries = _rift_log_entries(now, EXPLORE_RIFT_PENDING_RESULT_LOG_LOOKBACK_SEC)
    owners = _rift_log_command_owners(entries, command, now)
    if command_msg_id:
        owners = {key: value for key, value in owners.items() if key[1] == command_msg_id and (not command_chat_id or key[0] == command_chat_id)}
        if len(owners) != 1:
            return None
    replies = []
    for entry in _latest_tianxing_log_replies(entries, now):
        root = _rift_log_id(entry.get("reply_to_msg_id"))
        owner = owners.get((entry["chat_id"], root))
        if not owner or (result_msg_id and entry["message_id"] != result_msg_id):
            continue
        if owner["event_at"] > float(entry["server_event_at"]) + 1:
            continue
        raw_text = str(entry.get("text") or "").strip()
        if command == CMD_TIANXING_PANEL:
            if "【天机盘】" not in raw_text:
                continue
        elif not is_explore_rift_reply_text(raw_text):
            continue
        replies.append({
            "ts": float(entry["server_event_at"]), "server_event_at": float(entry["server_event_at"]),
            "msg_id": entry["message_id"], "chat_id": entry["chat_id"], "root_msg_id": root,
            "event_type": entry["event_type"], "text": raw_text,
        })
    if len({(reply["chat_id"], reply["root_msg_id"]) for reply in replies}) != 1:
        return None
    return replies[0]


def _rift_log_reply_context(entry, now, family):
    return {
        "send_as_id": get_current_identity_id(), "family": family,
        "chat_id": entry["chat_id"], "root_msg_id": entry["root_msg_id"],
        "reply_to_msg_id": entry["root_msg_id"], "msg_id": entry["msg_id"],
        "server_event_at": entry["server_event_at"], "event_type": entry["event_type"],
        "processed_at": now,
    }


def _find_recent_logged_explore_rift_command(now):
    wait_until = _rift_log_time(state.get("explore_rift_reply_due_at"))
    start_ts = wait_until - EXPLORE_RIFT_SEND_UNKNOWN_WAIT_SEC - 60 if wait_until > 0 else now - EXPLORE_RIFT_LOG_REPLAY_LOOKBACK_SEC
    _observed, snapshot = _unknown_rift_snapshot()
    start_ts = _rift_log_time(snapshot.get("recorded_at")) or start_ts
    entries = _rift_log_entries(now, EXPLORE_RIFT_LOG_REPLAY_LOOKBACK_SEC)
    owners = _rift_log_command_owners(entries, CMD_EXPLORE_RIFT, now)
    candidates = [entry for entry in owners.values() if start_ts - 1 <= (entry["event_at"] or entry["ts"]) <= now + 1]
    return candidates[0] if len(candidates) == 1 else None


def _find_logged_explore_rift_reply(command_msg_id, now, *, command_chat_id=0):
    command_msg_id = _rift_log_id(command_msg_id)
    if command_msg_id <= 0:
        return None
    return _find_owned_rift_log_reply(CMD_EXPLORE_RIFT, now, command_msg_id=command_msg_id, command_chat_id=command_chat_id)


def _recover_unknown_rift_panel_from_message_log(now):
    _observed, snapshot = _unknown_rift_snapshot()
    if not _unknown_rift_owner_matches(snapshot):
        return False
    if not _rift_log_id(snapshot.get("panel_msg_id")) and snapshot.get("panel_op_id"):
        owners = _rift_log_command_owners(_rift_log_entries(now, EXPLORE_RIFT_PENDING_RESULT_LOG_LOOKBACK_SEC), CMD_TIANXING_PANEL, now)
        started_at = _rift_log_time(snapshot.get("panel_started_at"))
        matches = [entry for entry in owners.values() if (
            entry.get("op_id") == snapshot["panel_op_id"]
            and 0 < started_at <= (entry.get("event_at") or entry["ts"]) <= now + 1
        )]
        if len(matches) == 1:
            entry = matches[0]
            snapshot.update(panel_msg_id=entry["msg_id"], panel_chat_id=entry["chat_id"], panel_sent_at=entry["ts"], panel_status="sent")
            _store_unknown_rift_snapshot(snapshot)
    panel_msg_id = _rift_log_id(snapshot.get("panel_msg_id"))
    panel_sent_at = _rift_log_time(snapshot.get("panel_sent_at"))
    if panel_msg_id <= 0 or not 0 < panel_sent_at <= now + 1:
        return False
    found = _find_owned_rift_log_reply(CMD_TIANXING_PANEL, now, command_msg_id=panel_msg_id, command_chat_id=_rift_log_id(snapshot.get("panel_chat_id")))
    if not found:
        return False
    evidence_key = f"{found['chat_id']}:{found['ts']}:{_make_result_key(found['msg_id'], 'panel', found['text'])}"
    evidence = snapshot.get("panel_evidence")
    if isinstance(evidence, dict) and evidence.get("key") == evidence_key:
        return False
    if not apply_tianxing_passive(
        found["text"], now=found["ts"], family="tianxing_panel",
        reply_context=_rift_log_reply_context(found, now, "tianxing_panel"),
    ):
        return False
    parsed = parse_tianxing_text(found["text"], now=found["ts"], family="tianxing_panel") or {}
    complete = all(
        field in parsed and (not parsed[field] or _rift_log_time(parsed.get(f"{field}_until")) > found["ts"])
        for field in ("current_prediction", "current_change")
    )
    _observed, snapshot = _unknown_rift_snapshot()
    snapshot["panel_evidence"] = {"key": evidence_key, "at": found["ts"], "complete": complete}
    _store_unknown_rift_snapshot(snapshot)
    return True


def _find_logged_explore_rift_result_message(result_msg_id, now):
    result_msg_id = _rift_log_id(result_msg_id)
    if result_msg_id <= 0:
        return None
    return _find_owned_rift_log_reply(CMD_EXPLORE_RIFT, now, result_msg_id=result_msg_id)


async def _recover_explore_rift_from_message_log(now, *, command_msg_id=0):
    command_msg_id = int(command_msg_id or 0)
    _observed, snapshot = _unknown_rift_snapshot()
    if not _unknown_rift_owner_matches(snapshot):
        return ""
    command_msg_id = command_msg_id or _rift_log_id(snapshot.get("command_msg_id"))
    command_chat_id = _rift_log_id(snapshot.get("command_chat_id"))
    if command_msg_id <= 0:
        command_entry = _find_recent_logged_explore_rift_command(now)
        if not command_entry:
            return ""
        command_msg_id = int(command_entry.get("msg_id") or 0)
        command_chat_id = command_entry["chat_id"]
    reply_entry = _find_logged_explore_rift_reply(command_msg_id, now, command_chat_id=command_chat_id)
    if not reply_entry:
        return ""
    if _has_unknown_rift() and not snapshot.get("command_msg_id"):
        snapshot.update(command_msg_id=command_msg_id, command_chat_id=reply_entry["chat_id"])
        _store_unknown_rift_snapshot(snapshot)
    reply_to = SimpleNamespace(id=command_msg_id, chat_id=reply_entry["chat_id"], raw_text=CMD_EXPLORE_RIFT)
    handled = await handle_explore_rift_reply(
        reply_entry["text"],
        reply_entry["ts"] or now,
        reply_to=reply_to,
        matched_family="explore_rift",
        result_msg_id=reply_entry["msg_id"],
        reply_context=_rift_log_reply_context(reply_entry, now, "explore_rift"),
    )
    if not handled:
        return ""
    if int(state.get("explore_rift_pending_result_msg_id", 0) or 0) > 0 and not _explore_rift_final_title(reply_entry["text"]):
        return "pending"
    if str(state.get("explore_rift_last_result") or "") == "冷却中":
        return "cooldown"
    return "result"


async def _recover_pending_explore_rift_result_from_message_log(now):
    result_msg_id = int(state.get("explore_rift_pending_result_msg_id", 0) or 0)
    if result_msg_id <= 0:
        return ""
    reply_entry = _find_logged_explore_rift_result_message(result_msg_id, now)
    if not reply_entry:
        retry_at = float(now or 0) + 60
        if float(state.get("explore_rift_reply_due_at", 0) or 0) != retry_at:
            state["explore_rift_reply_due_at"] = retry_at
            save_state()
        return "pending"
    if _explore_rift_final_title(reply_entry["text"]) or (EXPLORE_RIFT_CD_KEYWORD in reply_entry["text"] and has_wait_time(reply_entry["text"])):
        reply_to = SimpleNamespace(id=reply_entry["root_msg_id"], chat_id=reply_entry["chat_id"], raw_text=CMD_EXPLORE_RIFT)
        handled = await handle_explore_rift_reply(
            reply_entry["text"],
            reply_entry["ts"] or now,
            reply_to=reply_to,
            matched_family="explore_rift",
            result_msg_id=result_msg_id,
            reply_context=_rift_log_reply_context(reply_entry, now, "explore_rift"),
        )
        if handled:
            return "result"
    if float(now or 0) - float(reply_entry["ts"] or 0) < EXPLORE_RIFT_PENDING_RESULT_STALE_SEC:
        retry_at = float(reply_entry["ts"] or now) + EXPLORE_RIFT_PENDING_RESULT_STALE_SEC
        if float(state.get("explore_rift_reply_due_at", 0) or 0) != retry_at:
            state["explore_rift_reply_due_at"] = retry_at
            save_state()
        return "pending"

    _observed, snapshot = _unknown_rift_snapshot()
    notify = not snapshot.get("result_wait_notified")
    if notify:
        if not snapshot:
            _mark_explore_rift_send_unknown(now)
            _observed, snapshot = _unknown_rift_snapshot()
        snapshot.update(command_msg_id=reply_entry["root_msg_id"], command_chat_id=reply_entry["chat_id"], result_wait_notified=True)
        _store_unknown_rift_snapshot(snapshot)
    state["explore_rift_reply_due_at"] = now + RETRY_MAX_SEC
    state["explore_rift_last_error"] = "探寻已开始，但最终编辑未留存；保留原消息等待结果，不按超时重发"
    state["explore_rift_manual_required"] = True
    save_state()
    if notify:
        await send_audit_log(f"⚠️ 探寻裂缝最终编辑未留存，消息ID={result_msg_id}；保留等待，不自动重发。", scope="identity", limit=220)
    return "pending"


def _log_explore_rift_recovery(recovered):
    recovered = str(recovered or "").strip()
    if not recovered:
        return
    console_log(
        f"🕳 探寻裂缝已从消息日志恢复：{state.get('explore_rift_last_result') or recovered}",
        scope="identity",
        limit=240,
    )


def get_explore_rift_status_text():
    last_result = str(state.get("explore_rift_last_result") or "").strip() or "无"
    last_error = str(state.get("explore_rift_last_error") or "").strip() or "无"
    realm = _profile_realm() or "未知"
    xiuwei_current = _profile_xiuwei_current() or "未知"
    now = time.time()
    rebirth_config = get_rebirth_choice_config()
    choice_mode_text = "灵根优先" if rebirth_config["choice_mode"] == "root_first" else "稳妥优先"
    preferred_root_text = rebirth_config["preferred_root_type"] or "不限"
    preferred_attrs_text = rebirth_config["preferred_attrs"] or "不限"
    lines = [
        "🕳 探寻裂缝",
        f"- 已启用：{'是' if state.get('explore_rift_enabled') else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_explore_rift_time', 0))}（{fmt_remaining(state.get('next_explore_rift_time', 0))}）",
        f"- 当前境界：{realm}",
        f"- 当前修为：{xiuwei_current}",
        "- CD口径：默认 12h（化神初期+已确认装备风雷翅才 9h）",
        f"- 夺舍选择：{choice_mode_text}｜灵根 {preferred_root_text}｜属性 {preferred_attrs_text}｜盲选 {rebirth_config['blind_index']}",
        f"- 待回复命令ID：{int(state.get('explore_rift_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 待编辑结果ID：{int(state.get('explore_rift_pending_result_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('explore_rift_reply_due_at', 0))}（{fmt_remaining(state.get('explore_rift_reply_due_at', 0))}）",
        f"- 最近结果：{last_result}",
        f"- 最近异常：{last_error}",
        f"- 人工确认：{'需要' if state.get('explore_rift_manual_required') else '否'}",
    ]
    fatal_due_at = float(state.get("explore_rift_fatal_confirm_due_at", 0) or 0)
    if fatal_due_at > 0:
        lines.append(f"- 大凶确认：{fmt_abs_ts(fatal_due_at)}（{fmt_remaining(fatal_due_at)}）")
    weak_until = float(state.get("explore_rift_nascent_escape_weak_until", 0) or 0)
    if weak_until > 0:
        lines.append(f"- 元婴虚弱至：{fmt_abs_ts(weak_until)}（{fmt_remaining(weak_until)}）")
    rebirth_required = bool(state.get("explore_rift_rebirth_required"))
    rebirth_phase = state.get("explore_rift_rebirth_phase") or "idle"
    quiet_reason = ""
    if _parse_int(state.get("explore_rift_fatal_msg_id", 0)) > 0 and fatal_due_at > now:
        quiet_reason = "大凶确认中"
    elif weak_until > now:
        quiet_reason = "元婴虚弱等待夺舍"
    elif rebirth_required:
        quiet_reason = f"夺舍恢复中({rebirth_phase})"
    if quiet_reason:
        lines.append(f"- 普通指令静默：是（{quiet_reason}，仅放行 .夺舍重生 / .重生 <编号>）")
    if rebirth_required:
        lines.append(f"- 夺舍阶段：{rebirth_phase}")
        lines.append(f"- 夺舍超时：{fmt_abs_ts(state.get('explore_rift_rebirth_due_at', 0))}（{fmt_remaining(state.get('explore_rift_rebirth_due_at', 0))}）")
        selected_index = int(state.get("explore_rift_rebirth_selected_index", 0) or 0)
        if selected_index:
            lines.append(f"- 已选择肉身编号：{selected_index}")
    if state.get("explore_rift_rebirth_last_result"):
        lines.append(f"- 最近夺舍：{state.get('explore_rift_rebirth_last_result')}")
    if state.get("explore_rift_rebirth_last_error"):
        lines.append(f"- 夺舍异常：{state.get('explore_rift_rebirth_last_error')}")
    return "\n".join(lines)


async def _mark_rebirth_restored(result_text, now):
    state["explore_rift_nascent_escape_weak_until"] = 0
    state["explore_rift_rebirth_required"] = False
    state["explore_rift_rebirth_phase"] = "restored"
    _clear_explore_rift_rebirth_pending()
    state["explore_rift_rebirth_last_result"] = str(result_text or "夺舍恢复完成").strip()
    state["explore_rift_rebirth_last_error"] = ""
    state["explore_rift_manual_required"] = False
    state["next_explore_rift_time"] = max(float(state.get("next_explore_rift_time", 0) or 0), float(now + RETRY_MAX_SEC))
    save_state()
    await send_audit_log(f"🕳 夺舍恢复完成：{state['explore_rift_rebirth_last_result']}", scope="identity", limit=240)


async def _send_rebirth_select(index, now, *, selected=None, blind=False):
    try:
        index = int(index or 0)
    except (TypeError, ValueError):
        index = 0
    if index not in {1, 2, 3}:
        return False
    command = f"{CMD_REBIRTH_SELECT_PREFIX} {index}"
    msg = await send_game_command(command, track=False, max_retry=0, source_module="探寻裂缝")
    if not msg:
        state["explore_rift_rebirth_phase"] = "manual_required"
        state["explore_rift_manual_required"] = True
        state["explore_rift_rebirth_last_error"] = "重生命令发送失败"
        save_state()
        await send_audit_log("❌ 重生命令发送失败，请人工处理。", scope="identity", priority="high", limit=240)
        return True

    state["explore_rift_rebirth_phase"] = "blind_selecting" if blind else "selecting"
    state["explore_rift_rebirth_select_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["explore_rift_rebirth_selected_index"] = index
    state["explore_rift_rebirth_due_at"] = float(now + EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC)
    if blind:
        state["explore_rift_rebirth_last_result"] = f"已盲选肉身 {index}"
        audit_text = f"🕳 夺舍选项回复超时，已盲选肉身：{command}"
    else:
        selected = selected or {}
        state["explore_rift_rebirth_last_result"] = (
            f"已选稳妥 {index}｜{selected.get('name') or '未知肉身'}｜{selected.get('root_text') or '未知灵根'}"
        )
        audit_text = (
            f"🕳 自动重生选择稳妥之身：{index}｜{selected.get('name') or '未知肉身'}｜"
            f"{selected.get('root_text') or '未知灵根'}"
        )
    state["explore_rift_rebirth_last_error"] = ""
    state["explore_rift_manual_required"] = False
    save_state()
    await send_audit_log(audit_text, scope="identity", priority="high" if blind else "auto", limit=360)
    return True


async def _handle_rebirth_reply(raw_text, now, incoming_msg_id=0):
    rebirth_kind = classify_rebirth_text(raw_text)
    if rebirth_kind == "weak":
        wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else 6 * 3600
        state["explore_rift_nascent_escape_weak_until"] = float(now + wait_sec + CD_BUFFER_SEC)
        state["explore_rift_rebirth_required"] = True
        state["explore_rift_rebirth_phase"] = "weak"
        _clear_explore_rift_rebirth_pending()
        state["explore_rift_rebirth_last_result"] = "虚弱温养中"
        state["explore_rift_rebirth_last_error"] = ""
        state["explore_rift_manual_required"] = False
        save_state()
        await send_audit_log(f"🕳 元婴虚弱→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}", scope="identity", limit=220)
        return True

    if rebirth_kind == "searching":
        state["explore_rift_rebirth_phase"] = "requesting"
        if incoming_msg_id > 0:
            state["explore_rift_rebirth_options_msg_id"] = int(incoming_msg_id)
        state["explore_rift_rebirth_last_result"] = "寻找肉身中"
        state["explore_rift_rebirth_last_error"] = ""
        save_state()
        return True

    if rebirth_kind == "options":
        options = parse_rebirth_options(raw_text)
        selected = choose_safe_rebirth_option(options)
        state["explore_rift_rebirth_options_text"] = raw_text
        state["explore_rift_rebirth_options_msg_id"] = int(incoming_msg_id or 0)
        if not selected:
            state["explore_rift_rebirth_required"] = True
            state["explore_rift_rebirth_phase"] = "manual_required"
            state["explore_rift_manual_required"] = True
            state["explore_rift_rebirth_last_error"] = "夺舍选项无法自动定位稳妥之身"
            _clear_explore_rift_rebirth_pending()
            save_state()
            await send_audit_log(f"⚠️ 夺舍选项无法自动定位稳妥之身，请人工处理：\n{raw_text}", scope="identity", limit=900)
            return True

        await _send_rebirth_select(int(selected["index"]), now, selected=selected, blind=False)
        return True

    if rebirth_kind in {"body_intact", "success"}:
        first_line = raw_text.splitlines()[0].strip() if raw_text.splitlines() else "夺舍成功"
        await _mark_rebirth_restored(first_line, now)
        return True

    if rebirth_kind == "failure":
        state["explore_rift_rebirth_required"] = True
        state["explore_rift_rebirth_phase"] = "manual_required"
        state["explore_rift_manual_required"] = True
        _clear_explore_rift_rebirth_pending()
        state["explore_rift_rebirth_last_error"] = raw_text[:160]
        save_state()
        await send_audit_log(f"⚠️ 夺舍恢复失败，请人工处理：{raw_text}", scope="identity", limit=700)
        return True

    return False


async def _handle_escape_weak(raw_text, now, result_msg_id):
    wait_sec = parse_wait_time(raw_text) if has_wait_time(raw_text) else 6 * 3600
    _clear_explore_rift_pending()
    _clear_explore_rift_fatal_pending()
    state["explore_rift_last_msg_id"] = int(result_msg_id or 0)
    state["explore_rift_last_result"] = f"{EXPLORE_RIFT_ESCAPE_WEAK_TITLE}｜虚弱 {fmt_time_after(wait_sec + CD_BUFFER_SEC)}"
    state["explore_rift_last_error"] = ""
    state["explore_rift_nascent_escape_weak_until"] = float(now + wait_sec + CD_BUFFER_SEC)
    state["explore_rift_rebirth_required"] = True
    state["explore_rift_rebirth_phase"] = "weak"
    _clear_explore_rift_rebirth_pending()
    state["explore_rift_rebirth_last_result"] = "等待虚弱期结束"
    state["explore_rift_rebirth_last_error"] = ""
    state["explore_rift_manual_required"] = False
    _schedule_next_explore_rift(now)
    save_state()
    await send_audit_log(
        f"🕳 元婴遁逃虚弱→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}，普通探寻暂停，虚弱结束后尝试夺舍重生。",
        scope="identity",
        limit=360,
    )


async def _confirm_pending_fatal(now):
    fatal_due_at = float(state.get("explore_rift_fatal_confirm_due_at", 0) or 0)
    fatal_msg_id = int(state.get("explore_rift_fatal_msg_id", 0) or 0)
    if fatal_due_at <= 0 or fatal_due_at > now:
        return False
    _clear_explore_rift_fatal_pending()
    state["explore_rift_last_msg_id"] = fatal_msg_id
    state["explore_rift_last_result"] = f"{EXPLORE_RIFT_FATAL_TITLE}｜肉身崩毁，待夺舍恢复"
    state["explore_rift_last_error"] = ""
    state["explore_rift_last_result_key"] = _make_result_key(fatal_msg_id, EXPLORE_RIFT_FATAL_TITLE, EXPLORE_RIFT_FATAL_TITLE)
    state["explore_rift_rebirth_required"] = True
    state["explore_rift_rebirth_phase"] = "idle"
    state["explore_rift_rebirth_due_at"] = 0
    state["explore_rift_rebirth_last_result"] = "肉身崩毁，等待夺舍重生"
    state["explore_rift_rebirth_last_error"] = ""
    state["explore_rift_manual_required"] = False
    next_time = _schedule_next_explore_rift(now)
    save_state()
    await send_audit_log(
        f"🕳 探寻裂缝大凶已确认，进入夺舍恢复静默｜裂缝下次 {fmt_abs_ts(next_time)}",
        scope="identity",
        priority="high",
        limit=360,
    )
    return True


async def _send_rebirth_request(now):
    msg = await send_game_command(CMD_REBIRTH_REQUEST, track=False, max_retry=0, source_module="探寻裂缝")
    if not msg:
        state["explore_rift_rebirth_due_at"] = float(now + RETRY_MAX_SEC)
        state["explore_rift_rebirth_last_error"] = "夺舍重生发送失败"
        save_state()
        await send_audit_log("❌ 夺舍重生发送失败，稍后重试。", scope="identity", limit=240)
        return False
    state["explore_rift_rebirth_phase"] = "requesting"
    state["explore_rift_rebirth_request_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["explore_rift_rebirth_due_at"] = float(now + EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC)
    state["explore_rift_rebirth_last_result"] = "已发送夺舍重生"
    state["explore_rift_rebirth_last_error"] = ""
    state["explore_rift_manual_required"] = False
    save_state()
    console_log(f"🕳 夺舍重生已发送，等待回复→{fmt_abs_ts(state['explore_rift_rebirth_due_at'])}", scope="identity", limit=180)
    return True


async def _run_rebirth_scheduler(now):
    weak_until = float(state.get("explore_rift_nascent_escape_weak_until", 0) or 0)
    if weak_until > now:
        if state.get("explore_rift_rebirth_phase") != "weak":
            state["explore_rift_rebirth_phase"] = "weak"
            mark_dirty()
        return True
    if not state.get("explore_rift_rebirth_required"):
        return False
    if str(state.get("explore_rift_rebirth_phase") or "") == "manual_required":
        return True

    request_msg_id = int(state.get("explore_rift_rebirth_request_msg_id", 0) or 0)
    select_msg_id = int(state.get("explore_rift_rebirth_select_msg_id", 0) or 0)
    due_at = float(state.get("explore_rift_rebirth_due_at", 0) or 0)
    if (request_msg_id > 0 or select_msg_id > 0) and due_at > now:
        return True

    if select_msg_id > 0:
        _clear_explore_rift_rebirth_pending()
        state["explore_rift_rebirth_phase"] = "manual_required"
        state["explore_rift_manual_required"] = True
        state["explore_rift_rebirth_last_error"] = "重生选择已发送但未读到确认，停止自动重试"
        save_state()
        await send_audit_log("⚠️ 重生选择已发送但未读到确认，已停止自动重试，请人工确认。", scope="identity", priority="high", limit=260)
        return True

    if request_msg_id > 0:
        _clear_explore_rift_rebirth_pending()
        state["explore_rift_rebirth_phase"] = "idle"
        state["explore_rift_rebirth_last_error"] = "夺舍选项回复超时，准备盲选"
        save_state()
        await _send_rebirth_select(get_rebirth_choice_config()["blind_index"], now, blind=True)
        return True

    await _send_rebirth_request(now)
    return True


async def handle_explore_rift_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0, *, reply_context=None):
    if not state.get("explore_rift_enabled") and not state.get("explore_rift_rebirth_required") and not has_unresolved_explore_rift():
        return False
    if not _is_explore_rift_reply(reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    result_msg_id = int(result_msg_id or 0)
    if not raw_text:
        return False

    if _is_rebirth_reply_context(reply_to, raw_text):
        handled_rebirth = await _handle_rebirth_reply(raw_text, now, incoming_msg_id=result_msg_id)
        if handled_rebirth:
            return True

    if _has_unknown_rift() and not _unknown_rift_reply_matches(reply_context, result_msg_id, now):
        return False
    if isinstance(reply_context, dict) and "server_event_at" in reply_context:
        event_at = _rift_log_time(reply_context["server_event_at"])
        if not 0 < event_at <= max(now, _rift_log_time(reply_context.get("processed_at"))) + 1:
            return False
        now = event_at

    if EXPLORE_RIFT_CD_KEYWORD in raw_text and has_wait_time(raw_text):
        wait_sec = parse_wait_time(raw_text)
        state["next_explore_rift_time"] = float(now + wait_sec + CD_BUFFER_SEC)
        _clear_explore_rift_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = "冷却中"
        state["explore_rift_last_error"] = ""
        _finish_unknown_rift()
        save_state()
        await send_audit_log(f"🕳 探寻裂缝 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if EXPLORE_RIFT_PENDING_KEYWORD in raw_text:
        if _has_terminal_result_for_msg(result_msg_id):
            return True
        if result_msg_id > 0:
            _set_explore_rift_pending_result(result_msg_id, now=now, reply_context=reply_context)
        state["explore_rift_last_result"] = "探寻中"
        state["explore_rift_last_error"] = ""
        save_state()
        return True

    final_title = _explore_rift_final_title(raw_text)
    if final_title:
        resolving_unknown = _has_unknown_rift()
        _finish_unknown_rift()
        result_key = _make_result_key(result_msg_id, final_title, raw_text)
        if state.get("explore_rift_last_result_key") == result_key:
            if resolving_unknown:
                save_state()
            return True
        _apply_tianxing_explore_rift_result(raw_text, now, reply_context=reply_context)
        if final_title == EXPLORE_RIFT_FATAL_TITLE:
            _clear_explore_rift_pending()
            state["explore_rift_fatal_msg_id"] = result_msg_id
            state["explore_rift_fatal_confirm_due_at"] = float(now + EXPLORE_RIFT_FATAL_GRACE_SEC)
            state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
            state["explore_rift_last_result"] = EXPLORE_RIFT_FATAL_TITLE
            state["explore_rift_last_error"] = ""
            save_state()
            await send_audit_log("🕳 探寻裂缝大凶，短暂等待是否元婴遁逃。", scope="identity", limit=240)
            return True
        if final_title == EXPLORE_RIFT_ESCAPE_WEAK_TITLE:
            await _handle_escape_weak(raw_text, now, result_msg_id)
            state["explore_rift_last_result_key"] = result_key
            save_state()
            return True

    if _is_explore_rift_terminal_success(raw_text):
        result_summary, item_deltas = parse_explore_rift_result_summary(raw_text)
        _clear_explore_rift_pending()
        _clear_explore_rift_fatal_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = result_summary
        state["explore_rift_last_error"] = ""
        state["explore_rift_last_result_key"] = _make_result_key(result_msg_id, final_title or _strip_title(raw_text), raw_text)
        state["explore_rift_manual_required"] = False
        _schedule_next_explore_rift(now)
        save_state()
        if item_deltas:
            apply_storage_bag_item_deltas(get_current_identity_id(), item_deltas)
        await _send_tianxing_explore_rift_result_audit(raw_text, result_summary)
        await send_audit_log(f"🕳 探寻裂缝结果：{result_summary}", scope="identity", limit=220)
        return True

    if _is_explore_rift_terminal_failure(raw_text):
        result_summary, _item_deltas = parse_explore_rift_result_summary(raw_text)
        _clear_explore_rift_pending()
        _clear_explore_rift_fatal_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = result_summary
        state["explore_rift_last_error"] = ""
        state["explore_rift_last_result_key"] = _make_result_key(result_msg_id, final_title or _strip_title(raw_text), raw_text)
        state["explore_rift_manual_required"] = False
        _schedule_next_explore_rift(now)
        save_state()
        await _send_tianxing_explore_rift_result_audit(raw_text, result_summary)
        await send_audit_log(f"🕳 探寻裂缝结果：{result_summary}", scope="identity", limit=220)
        return True

    if any(keyword in raw_text for keyword in ("时空异兽", "探寻机缘", "成功捕获了几缕逸散的法则本源")):
        if _has_terminal_result_for_msg(result_msg_id):
            return True
        if result_msg_id > 0:
            _set_explore_rift_pending_result(result_msg_id, now=now, reply_context=reply_context)
        state["explore_rift_last_result"] = "探寻中"
        state["explore_rift_last_error"] = ""
        save_state()
        return True

    if any(keyword in raw_text for keyword in ("境界不足", "元婴初期", "元婴期", "未到元婴", "修为不足", "主魂的一缕分神")):
        _clear_explore_rift_pending()
        _finish_unknown_rift()
        _set_explore_rift_error("境界/修为/分神限制，延后探寻", next_delay=RETRY_MAX_SEC, now=now)
        await send_audit_log("🕳 探寻裂缝被拦截：境界/修为/分神限制，已延后。", scope="identity", limit=180)
        return True

    if "空间裂缝尚未稳定" in raw_text or "风暴" in raw_text:
        if not has_wait_time(raw_text):
            return False
        wait_sec = parse_wait_time(raw_text)
        state["next_explore_rift_time"] = float(now + wait_sec + CD_BUFFER_SEC)
        _clear_explore_rift_pending()
        state["explore_rift_last_result"] = "冷却中"
        state["explore_rift_last_error"] = ""
        _finish_unknown_rift()
        save_state()
        await send_audit_log(f"🕳 探寻裂缝 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    return False


async def _prepare_explore_rift_tianxing_route(now, *, due_at=0):
    identity_id = get_current_identity_id()
    if not has_identity(identity_id):
        return False
    identity = get_identity_state(identity_id)
    account_id = get_identity_account(identity_id)
    expected = {key: identity.get(key) for key in (
        "explore_rift_enabled", "explore_rift_manual_required", "next_explore_rift_time",
        "explore_rift_reply_to_msg_id", "explore_rift_reply_due_at", "explore_rift_pending_result_msg_id",
        "explore_rift_tianxing_prepare_retry_at", "tianxing_enabled",
    )}
    started_at = time.monotonic()
    started_now = now

    def is_current():
        return bool(
            has_identity(identity_id) and get_identity_state(identity_id) is identity
            and get_identity_account(identity_id) == account_id
            and get_global_enabled() and get_identity_enabled(identity_id)
            and identity.get("explore_rift_enabled")
            and all(identity.get(key) == value for key, value in expected.items())
        )

    if not is_current():
        return False
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now, require_change_fate=True)
    if preflight.get("route_allowed"):
        state["explore_rift_tianxing_prepare_retry_at"] = 0
        return True
    if str(preflight.get("stage") or "") == "prediction_conflict":
        consume_result = await run_tianxing_consume_craft_prediction(
            now, reason="探寻裂缝前消费炼制推命", operation_check=is_current,
        )
        if not is_current():
            return False
        now = started_now + max(0.0, time.monotonic() - started_at)
        if consume_result.get("active"):
            if due_at <= now:
                _schedule_explore_rift_tianxing_prepare_retry(now, due_at)
            else:
                _schedule_explore_rift_tianxing_prepare_retry(now, due_at)
            state["explore_rift_last_result"] = f"天星先炼制消费推命：{consume_result.get('stage') or 'waiting'}"
            state["explore_rift_last_error"] = "" if consume_result.get("takeover") or consume_result.get("stage") == "waiting_reply" else str(consume_result.get("reason") or "")
            save_state()
            return False
        preflight = build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now, require_change_fate=True)
        if preflight.get("route_allowed"):
            state["explore_rift_tianxing_prepare_retry_at"] = 0
            return True
    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        current_due = float(state.get("next_explore_rift_time", 0) or due_at or now)
        # A stale route prediction can expire just before the rift cooldown.
        # Keep the game's rift deadline in that narrow window and wake the
        # preparation path at the real prediction expiry; otherwise the old
        # code moved the whole rift timer past its already-ready cooldown and
        # lost the chance to rebuild the route promptly.
        if due_at > now and blocked_until <= due_at:
            state["next_explore_rift_time"] = max(current_due, due_at)
            state["explore_rift_tianxing_prepare_retry_at"] = max(now, blocked_until)
        else:
            retry_at = float(blocked_until + CD_BUFFER_SEC)
            state["next_explore_rift_time"] = max(current_due, retry_at)
            state["explore_rift_tianxing_prepare_retry_at"] = 0
        state["explore_rift_last_error"] = str(preflight.get("reason") or "天星预检阻断")
        save_state()
        return False
    if preflight.get("timeline_required"):
        windows = build_tianxing_consume_window(
            "探索",
            now=now,
            due_at=max(due_at, now),
            reason="探寻裂缝",
            require_change_fate=True,
        )
        if not windows:
            return True
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows, operation_check=is_current)
        if not is_current():
            return False
        now = started_now + max(0.0, time.monotonic() - started_at)
        followup = build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now, require_change_fate=True)
        if followup.get("route_allowed"):
            state["explore_rift_tianxing_prepare_retry_at"] = 0
            return True
        phase = str(timeline_result.get("phase") or "").strip()
        if phase == "need_tianji_for_change":
            retry_at = _schedule_explore_rift_tianji_wait(now, due_at)
            reason_text = str(followup.get("reason") or preflight.get("reason") or "").strip()
            wait_text = f"天机不足，等待低风险探索补点后复查（{fmt_abs_ts(retry_at)}）。"
            state["explore_rift_last_result"] = "天星时间线：need_tianji_for_change"
            state["explore_rift_last_error"] = f"{reason_text}；{wait_text}" if reason_text else wait_text
            save_state()
            return False
        short_retry_phases = {"sending", "sent_waiting_ack", "state_confirmed", "downstream_released", "calibrating"}
        retry_delay = EXPLORE_RIFT_TIANXING_PREPARE_RETRY_SEC if phase in short_retry_phases or timeline_result.get("changed") else RETRY_MAX_SEC
        _schedule_explore_rift_tianxing_prepare_retry(now, due_at, retry_delay)
        state["explore_rift_last_result"] = f"天星时间线：{timeline_result.get('phase') or 'waiting'}"
        state["explore_rift_last_error"] = "" if timeline_result.get("changed") else str(followup.get("reason") or preflight.get("reason") or "")
        save_state()
        return False
    if due_at <= now:
        state["next_explore_rift_time"] = float(now + RETRY_MAX_SEC)
    else:
        state["explore_rift_tianxing_prepare_retry_at"] = float(now + RETRY_MAX_SEC)
    state["explore_rift_last_error"] = str(preflight.get("reason") or "天星预检阻断")
    save_state()
    return False


async def _run_explore_rift_scheduler_unlocked(now):
    if await _confirm_pending_fatal(now):
        return
    if await _run_rebirth_scheduler(now):
        return

    if not state.get("explore_rift_enabled"):
        return
    if (
        state.get("explore_rift_manual_required")
        and not has_unresolved_explore_rift()
    ):
        return

    reply_to_msg_id = int(state.get("explore_rift_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("explore_rift_reply_due_at", 0) or 0)
    pending_result_msg_id = int(state.get("explore_rift_pending_result_msg_id", 0) or 0)
    if reply_to_msg_id <= 0 and pending_result_msg_id > 0:
        if reply_due_at > now:
            return
        recovered = await _recover_pending_explore_rift_result_from_message_log(now)
        if recovered == "pending":
            return
        if recovered:
            save_state()
            _log_explore_rift_recovery(recovered)
            return
    if _has_unknown_rift():
        if reply_due_at > now:
            return
        identity_id = get_current_identity_id()
        identity = get_identity_state(identity_id)
        account_id = get_identity_account(identity_id)
        _observed, snapshot = _unknown_rift_snapshot()
        if not _unknown_rift_owner_matches(snapshot):
            return
        recovered = await _recover_explore_rift_from_message_log(now, command_msg_id=reply_to_msg_id)
        if recovered:
            save_state()
            _log_explore_rift_recovery(recovered)
            return
        _observed, snapshot = _unknown_rift_snapshot()
        if not snapshot:
            snapshot = {"op_id": uuid4().hex, "legacy_unanchored": True, "panel_status": "unknown"}
            _store_unknown_rift_snapshot(snapshot)
        _recover_unknown_rift_panel_from_message_log(now)
        panel_changed = _reconcile_unknown_rift_from_panel(now)
        requested = False
        operation_id = snapshot.get("op_id")
        if state.get("tianxing_enabled"):
            requested = await _request_unknown_rift_panel(now)
        if (
            not has_identity(identity_id) or get_identity_state(identity_id) is not identity
            or get_identity_account(identity_id) != account_id
        ):
            return
        if not _has_unknown_rift():
            return
        _observed, snapshot = _unknown_rift_snapshot()
        if operation_id is not None and snapshot.get("op_id") != operation_id:
            return
        should_notify = not snapshot.get("notified_at") or bool(panel_changed)
        if should_notify:
            snapshot["notified_at"] = now
            _store_unknown_rift_snapshot(snapshot)
        if not requested:
            state["explore_rift_reply_due_at"] = now + RETRY_MAX_SEC
            if not panel_changed and not snapshot.get("panel_evidence"):
                state["explore_rift_last_result"] = "裂缝结果未知，等待原命令回包"
                state["explore_rift_last_error"] = "未捞到可归属的最终反馈；保留在途记录，不自动重发"
        state["next_explore_rift_time"] = max(_rift_log_time(state.get("next_explore_rift_time")), state["explore_rift_reply_due_at"])
        state["explore_rift_manual_required"] = True
        save_state()
        if should_notify:
            await send_audit_log(f"⚠️ {state.get('explore_rift_last_result')}；不会自动重发探寻裂缝。", scope="identity", priority="high", limit=260)
        return
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        recovered = await _recover_explore_rift_from_message_log(now, command_msg_id=reply_to_msg_id)
        if recovered:
            save_state()
            _log_explore_rift_recovery(recovered)
            return
        _mark_explore_rift_send_unknown(now)
        _observed, snapshot = _unknown_rift_snapshot()
        snapshot["notified_at"] = now
        _store_unknown_rift_snapshot(snapshot)
        state["explore_rift_last_error"] = "探寻裂缝回复超时，保留原命令等待最终反馈"
        save_state()
        await send_audit_log(f"⚠️ 探寻裂缝回复超时，消息ID={reply_to_msg_id}，继续等待原回包，不自动重发。", scope="identity", limit=220)
        return

    realm = _profile_realm()
    if not realm:
        if not _explore_rift_next_time_blocks(now):
            _set_explore_rift_error("境界未知，等待身份资料确认后再探寻", next_delay=RETRY_MAX_SEC, now=now)
        return

    if not _realm_at_least(EXPLORE_RIFT_MIN_REALM):
        state["explore_rift_enabled"] = False
        _clear_explore_rift_pending()
        _set_explore_rift_error("境界不符，已关闭探寻裂缝", persist=True)
        await send_audit_log("🕳 探寻裂缝已关闭：身份资料显示当前境界不足。", scope="identity", limit=180)
        return

    xiuwei_current = _profile_xiuwei_current()
    if xiuwei_current is None:
        if not _explore_rift_next_time_blocks(now):
            _set_explore_rift_error("修为未知，等待身份资料确认后再探寻", next_delay=RETRY_MAX_SEC, now=now)
        return

    next_explore_rift_time = float(state.get("next_explore_rift_time", 0) or 0)
    if _pull_ready_tianxing_explore_retry(now, next_explore_rift_time):
        next_explore_rift_time = float(state.get("next_explore_rift_time", 0) or 0)
    if next_explore_rift_time > now:
        windows = build_tianxing_consume_window(
            "探索",
            now=now,
            due_at=next_explore_rift_time,
            reason="探寻裂缝",
            require_change_fate=True,
        )
        try:
            prepare_retry_at = float(state.get("explore_rift_tianxing_prepare_retry_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            prepare_retry_at = 0.0
        if windows and prepare_retry_at <= now and not await _prepare_explore_rift_tianxing_route(now, due_at=next_explore_rift_time):
            return

    if _explore_rift_next_time_blocks(now):
        return

    if xiuwei_current >= EXPLORE_RIFT_XIUWEI_LIMIT and not _tianxing_explore_change_ready(now):
        if state.get("tianxing_enabled"):
            if not await _prepare_explore_rift_tianxing_route(now, due_at=now):
                return
            if not _tianxing_explore_change_ready(now):
                return
        else:
            _set_explore_rift_error("auto模式修为>=500000，暂不探寻", next_delay=RETRY_MAX_SEC, now=now)
            return

    if not await _prepare_explore_rift_tianxing_route(now, due_at=now):
        return

    # Reply/edit handlers can confirm a long real cooldown while the route
    # preflight coroutine is awaiting. Never send from that stale snapshot.
    if _explore_rift_next_time_blocks(now):
        return

    msg = await send_game_command(CMD_EXPLORE_RIFT, track=False, max_retry=0, source_module="探寻裂缝")
    if not msg:
        send_block = classify_game_send_block(get_current_identity_id(), CMD_EXPLORE_RIFT)
        if send_block.get("status") == "unknown" or not str(send_block.get("code") or "").strip():
            wait_until = _mark_explore_rift_send_unknown(now)
            save_state()
            await send_audit_log(
                f"⚠️ 探寻裂缝发送状态未知，等待被动回复或冷却校准至 {fmt_abs_ts(wait_until)}。",
                scope="identity",
                priority="high",
                limit=240,
            )
            return
        if _is_explore_rift_unsent_block(send_block):
            state["next_explore_rift_time"] = max(
                float(state.get("next_explore_rift_time", 0) or 0),
                float(now + RETRY_MAX_SEC),
            )
            state["explore_rift_last_result"] = "探寻裂缝未发送，等待运行层恢复"
            state["explore_rift_last_error"] = f"探寻裂缝未发送: {_explore_rift_block_label(send_block)}"
            state["explore_rift_reply_to_msg_id"] = 0
            state["explore_rift_reply_due_at"] = 0
            state["explore_rift_pending_result_msg_id"] = 0
            save_state()
            await send_audit_log(
                f"⏳ 探寻裂缝未发送，{fmt_time_after(RETRY_MAX_SEC)} 后重试：{_explore_rift_block_label(send_block)}。",
                scope="identity",
                limit=220,
            )
            return
        state["next_explore_rift_time"] = max(
            float(state.get("next_explore_rift_time", 0) or 0),
            float(now + RETRY_MAX_SEC),
        )
        state["explore_rift_last_error"] = "探寻裂缝发送失败"
        save_state()
        await send_audit_log("❌ 探寻裂缝发送失败，稍后重试。", scope="identity", limit=180)
        return

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    state["explore_rift_reply_to_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["explore_rift_reply_due_at"] = sent_at + EXPLORE_RIFT_REPLY_TIMEOUT_SEC
    state["explore_rift_pending_result_msg_id"] = 0
    state["explore_rift_last_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["explore_rift_last_result"] = "已发送"
    state["explore_rift_last_error"] = ""
    state["explore_rift_manual_required"] = False
    state["next_explore_rift_time"] = state["explore_rift_reply_due_at"]
    save_state()
    console_log(f"🕳 探寻裂缝已发送，等待回复→{fmt_abs_ts(state['explore_rift_reply_due_at'])}", scope="identity", limit=180)


async def run_explore_rift_scheduler(now):
    async with _explore_rift_lock():
        return await _run_explore_rift_scheduler_unlocked(now)


def schedule_explore_rift_initial_check(now, *, persist=False, keep_last_error=True):
    if has_unresolved_explore_rift():
        return
    last_error = state.get("explore_rift_last_error") if keep_last_error else ""
    _clear_explore_rift_pending()
    state["explore_rift_last_error"] = last_error or ""
    state["next_explore_rift_time"] = float(now + random.uniform(EXPLORE_RIFT_RECOVERY_MIN_SEC, EXPLORE_RIFT_RECOVERY_MAX_SEC))
    if persist:
        save_state()
    else:
        mark_dirty()
    return state["next_explore_rift_time"]


__all__ = [
    "EXPLORE_RIFT_CD_KEYWORD",
    "EXPLORE_RIFT_ESCAPE_WEAK_TITLE",
    "EXPLORE_RIFT_FATAL_TITLE",
    "EXPLORE_RIFT_PENDING_KEYWORD",
    "EXPLORE_RIFT_RESULT_TITLE",
    "choose_safe_rebirth_option",
    "clear_explore_rift_state",
    "classify_rebirth_text",
    "get_explore_rift_status_text",
    "get_rebirth_choice_config",
    "handle_explore_rift_reply",
    "has_unresolved_explore_rift",
    "is_explore_rift_reply_text",
    "parse_explore_rift_result_summary",
    "parse_rebirth_options",
    "run_explore_rift_scheduler",
    "schedule_explore_rift_initial_check",
    "set_rebirth_choice_config",
]
