import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_EXPLORE_RIFT,
    EXPLORE_RIFT_CD,
    EXPLORE_RIFT_JITTER_MAX_SEC,
    EXPLORE_RIFT_JITTER_MIN_SEC,
    EXPLORE_RIFT_REPLY_TIMEOUT_SEC,
    RETRY_MAX_SEC,
)
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import (
    REALM_SORT_INDEX,
    get_current_identity_id,
    get_send_as_profile,
    infer_realm_from_xiuwei_max,
    state,
)
from ..timing import cd_blocks, fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .storage_bag import apply_storage_bag_item_deltas


EXPLORE_RIFT_PENDING_KEYWORD = "撕开一道漆黑的空间裂缝"
EXPLORE_RIFT_RESULT_TITLE = "【探寻成功】"
EXPLORE_RIFT_SUCCESS_TITLES = (EXPLORE_RIFT_RESULT_TITLE, "【激战得胜】")
EXPLORE_RIFT_FAILURE_TITLES = ("【遭遇风暴】", "【不敌败退】")
EXPLORE_RIFT_CD_KEYWORD = "空间裂缝尚未稳定"
EXPLORE_RIFT_XIUWEI_LIMIT = 500_000
EXPLORE_RIFT_MIN_REALM = "元婴初期"
EXPLORE_RIFT_FAST_REALM = "化神初期"
EXPLORE_RIFT_WINGS_NAME = "风雷翅"
EXPLORE_RIFT_RECOVERY_MIN_SEC = 90
EXPLORE_RIFT_RECOVERY_MAX_SEC = 180
EXPLORE_RIFT_FALLBACK_CD_SEC = EXPLORE_RIFT_CD
EXPLORE_RIFT_FAST_CD_SEC = 9 * 3600
RE_EXPLORER_REWARD_LINE = re.compile(r"【([^】]+)】\s*[x×*＊]\s*([\d,]+)")
RE_EXPLORER_REWARD_TOKEN = re.compile(r"【([^】]+)】")
RE_EXPLORER_REWARD_CONTEXT = re.compile(r"(带来了|获得|获得了|奖励|馈赠|收获|寻得|掉落|获取)")
RE_EXPLORER_NOISE_PREFIX = re.compile(r"^[\-•·\s]+")
RE_EXPLORER_XIUWEI_GAIN = re.compile(r"修为(?:最终)?(?:增加了|增加)\s*([\d,]+)\s*点")
RE_EXPLORER_XIUWEI_LOSS = re.compile(r"修为(?:倒退了|倒退|暴跌了|损失)\s*([\d,]+)\s*点")


def _parse_int(value, default=0):
    try:
        return int(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return default


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


def _set_explore_rift_pending_result(result_msg_id, now=None):
    result_msg_id = int(result_msg_id or 0)
    if result_msg_id <= 0:
        return False
    now = float(now if now is not None else time.time())
    changed = False
    if int(state.get("explore_rift_reply_to_msg_id", 0) or 0) != 0:
        state["explore_rift_reply_to_msg_id"] = 0
        changed = True
    if int(state.get("explore_rift_pending_result_msg_id", 0) or 0) != result_msg_id:
        state["explore_rift_pending_result_msg_id"] = result_msg_id
        changed = True
    if int(state.get("explore_rift_last_msg_id", 0) or 0) != result_msg_id:
        state["explore_rift_last_msg_id"] = result_msg_id
        changed = True
    if float(state.get("explore_rift_reply_due_at", 0) or 0) != 0:
        state["explore_rift_reply_due_at"] = 0
        changed = True
    fallback_next_time = now + _resolve_cd_sec()
    if float(state.get("next_explore_rift_time", 0) or 0) < fallback_next_time:
        state["next_explore_rift_time"] = fallback_next_time
        changed = True
    return changed


def clear_explore_rift_state(*, persist=False, keep_last_error=False):
    last_error = state.get("explore_rift_last_error") if keep_last_error else ""
    state["next_explore_rift_time"] = 0
    _clear_explore_rift_pending()
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


def _explore_rift_next_time_blocks(now):
    return cd_blocks(state.get("next_explore_rift_time", 0), now, 0)


def _is_explore_rift_reply(reply_to=None, matched_family=None):
    if matched_family == "explore_rift":
        return True
    orig_cmd = str(getattr(reply_to, "raw_text", "") or "").strip()
    return orig_cmd == CMD_EXPLORE_RIFT or orig_cmd.startswith(f"{CMD_EXPLORE_RIFT} ")


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
        if not name or name in {"探寻成功", "激战得胜", "遭遇风暴", "不敌败退", "命盘", "天星偏转", "贪狼", "紫微", "天府", "太阴"}:
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


def _is_explore_rift_terminal_success(raw_text):
    return any(str(raw_text or "").strip().startswith(title) for title in EXPLORE_RIFT_SUCCESS_TITLES)


def _is_explore_rift_terminal_failure(raw_text):
    return any(str(raw_text or "").strip().startswith(title) for title in EXPLORE_RIFT_FAILURE_TITLES)


def is_explore_rift_reply_text(text):
    raw_text = str(text or "").strip()
    return (
        EXPLORE_RIFT_PENDING_KEYWORD in raw_text
        or _is_explore_rift_terminal_success(raw_text)
        or _is_explore_rift_terminal_failure(raw_text)
        or EXPLORE_RIFT_CD_KEYWORD in raw_text
        or "时空异兽" in raw_text
    )


def get_explore_rift_status_text():
    last_result = str(state.get("explore_rift_last_result") or "").strip() or "无"
    last_error = str(state.get("explore_rift_last_error") or "").strip() or "无"
    realm = _profile_realm() or "未知"
    xiuwei_current = _profile_xiuwei_current() or "未知"
    lines = [
        "🕳 探寻裂缝",
        f"- 已启用：{'是' if state.get('explore_rift_enabled') else '否'}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_explore_rift_time', 0))}（{fmt_remaining(state.get('next_explore_rift_time', 0))}）",
        f"- 当前境界：{realm}",
        f"- 当前修为：{xiuwei_current}",
        "- CD口径：默认 12h（化神初期+已确认装备风雷翅才 9h）",
        f"- 待回复命令ID：{int(state.get('explore_rift_reply_to_msg_id', 0) or 0) or '无'}",
        f"- 待编辑结果ID：{int(state.get('explore_rift_pending_result_msg_id', 0) or 0) or '无'}",
        f"- 回复超时：{fmt_abs_ts(state.get('explore_rift_reply_due_at', 0))}（{fmt_remaining(state.get('explore_rift_reply_due_at', 0))}）",
        f"- 最近结果：{last_result}",
        f"- 最近异常：{last_error}",
        f"- 人工确认：{'需要' if state.get('explore_rift_manual_required') else '否'}",
    ]
    return "\n".join(lines)


async def handle_explore_rift_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("explore_rift_enabled"):
        return False
    if not _is_explore_rift_reply(reply_to, matched_family=matched_family):
        return False

    raw_text = str(text or "").strip()
    result_msg_id = int(result_msg_id or 0)

    if EXPLORE_RIFT_CD_KEYWORD in raw_text and has_wait_time(raw_text):
        wait_sec = parse_wait_time(raw_text)
        state["next_explore_rift_time"] = float(now + wait_sec + CD_BUFFER_SEC)
        _clear_explore_rift_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = "冷却中"
        state["explore_rift_last_error"] = ""
        save_state()
        await send_audit_log(f"🕳 探寻裂缝 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    if EXPLORE_RIFT_PENDING_KEYWORD in raw_text:
        if result_msg_id > 0:
            _set_explore_rift_pending_result(result_msg_id, now=now)
        state["explore_rift_last_result"] = "探寻中"
        state["explore_rift_last_error"] = ""
        save_state()
        return True

    if _is_explore_rift_terminal_success(raw_text):
        result_summary, item_deltas = parse_explore_rift_result_summary(raw_text)
        _clear_explore_rift_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = result_summary
        state["explore_rift_last_error"] = ""
        state["explore_rift_manual_required"] = False
        _schedule_next_explore_rift(now)
        save_state()
        if item_deltas:
            apply_storage_bag_item_deltas(get_current_identity_id(), item_deltas)
        await send_audit_log(f"🕳 探寻裂缝结果：{result_summary}", scope="identity", limit=220)
        return True

    if _is_explore_rift_terminal_failure(raw_text):
        result_summary, _item_deltas = parse_explore_rift_result_summary(raw_text)
        _clear_explore_rift_pending()
        state["explore_rift_last_msg_id"] = result_msg_id or int(getattr(reply_to, "id", 0) or 0)
        state["explore_rift_last_result"] = result_summary
        state["explore_rift_last_error"] = ""
        state["explore_rift_manual_required"] = False
        _schedule_next_explore_rift(now)
        save_state()
        await send_audit_log(f"🕳 探寻裂缝结果：{result_summary}", scope="identity", limit=220)
        return True

    if any(keyword in raw_text for keyword in ("时空异兽", "探寻机缘", "成功捕获了几缕逸散的法则本源")):
        if result_msg_id > 0:
            _set_explore_rift_pending_result(result_msg_id, now=now)
        state["explore_rift_last_result"] = "探寻中"
        state["explore_rift_last_error"] = ""
        save_state()
        return True

    if any(keyword in raw_text for keyword in ("境界不足", "元婴初期", "未到元婴", "修为不足")):
        _clear_explore_rift_pending()
        _set_explore_rift_error("境界或修为不足，延后探寻", next_delay=RETRY_MAX_SEC, now=now)
        await send_audit_log("🕳 探寻裂缝被拦截：境界或修为不足，已延后。", scope="identity", limit=180)
        return True

    if "空间裂缝尚未稳定" in raw_text or "风暴" in raw_text:
        if not has_wait_time(raw_text):
            return False
        wait_sec = parse_wait_time(raw_text)
        state["next_explore_rift_time"] = float(now + wait_sec + CD_BUFFER_SEC)
        _clear_explore_rift_pending()
        state["explore_rift_last_result"] = "冷却中"
        state["explore_rift_last_error"] = ""
        save_state()
        await send_audit_log(f"🕳 探寻裂缝 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    return False


async def run_explore_rift_scheduler(now):
    if not state.get("explore_rift_enabled"):
        return

    reply_to_msg_id = int(state.get("explore_rift_reply_to_msg_id", 0) or 0)
    reply_due_at = float(state.get("explore_rift_reply_due_at", 0) or 0)
    if reply_to_msg_id > 0:
        if reply_due_at > now:
            return
        _clear_explore_rift_pending()
        state["next_explore_rift_time"] = float(now + RETRY_MAX_SEC)
        state["explore_rift_last_error"] = "探寻裂缝回复超时"
        save_state()
        await send_audit_log(f"⚠️ 探寻裂缝回复超时，消息ID={reply_to_msg_id}，稍后重试。", scope="identity", limit=220)
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

    if xiuwei_current >= EXPLORE_RIFT_XIUWEI_LIMIT:
        if not _explore_rift_next_time_blocks(now):
            _set_explore_rift_error("auto模式修为>=500000，暂不探寻", next_delay=RETRY_MAX_SEC, now=now)
        return

    if _explore_rift_next_time_blocks(now):
        return

    msg = await send_game_command(CMD_EXPLORE_RIFT, track=False, max_retry=0, source_module="探寻裂缝")
    if not msg:
        state["next_explore_rift_time"] = float(now + RETRY_MAX_SEC)
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


def schedule_explore_rift_initial_check(now, *, persist=False, keep_last_error=True):
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
    "EXPLORE_RIFT_PENDING_KEYWORD",
    "EXPLORE_RIFT_RESULT_TITLE",
    "clear_explore_rift_state",
    "get_explore_rift_status_text",
    "handle_explore_rift_reply",
    "is_explore_rift_reply_text",
    "parse_explore_rift_result_summary",
    "run_explore_rift_scheduler",
    "schedule_explore_rift_initial_check",
]
