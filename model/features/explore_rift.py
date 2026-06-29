import hashlib
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_EXPLORE_RIFT,
    CMD_REBIRTH_REQUEST,
    CMD_REBIRTH_SELECT_PREFIX,
    EXPLORE_RIFT_CD,
    EXPLORE_RIFT_FATAL_GRACE_SEC,
    EXPLORE_RIFT_JITTER_MAX_SEC,
    EXPLORE_RIFT_JITTER_MIN_SEC,
    EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC,
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
from .tianxing import build_tianxing_consume_window, build_tianxing_route_preflight_plan, run_tianxing_timeline_scheduler


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
RE_EXPLORER_REWARD_LINE = re.compile(r"【([^】]+)】\s*[x×*＊]\s*([\d,]+)")
RE_EXPLORER_REWARD_TOKEN = re.compile(r"【([^】]+)】")
RE_EXPLORER_REWARD_CONTEXT = re.compile(r"(带来了|获得|获得了|奖励|馈赠|收获|寻得|掉落|获取|平安带回|带回了|截下)")
RE_EXPLORER_NOISE_PREFIX = re.compile(r"^[\-•·\s]+")
RE_EXPLORER_XIUWEI_GAIN = re.compile(r"修为(?:最终)?(?:增加了|增加)\s*([\d,]+)\s*点")
RE_EXPLORER_XIUWEI_LOSS = re.compile(r"修为(?:倒退了|倒退|暴跌了|损失|逸散了|逸散)\s*([\d,]+)\s*点")
RE_EXPLORER_XIUWEI_NO_LOSS = re.compile(r"(?:未损修为|未损失修为|修为未损)")
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
RE_REBIRTH_OPTION = re.compile(
    r"(?m)^\s*(?P<index>[123])\.\s*【夺舍\s+(?P<name>[^】]+)】\s*\n"
    r"\s*-\s*灵根:\s*(?P<root>[^\n]+)\n"
    r"\s*-\s*命途:\s*(?P<fate>[^\n]+)"
)
RE_ROOT_ATTRS = re.compile(r"\(([^)]*)\)")


def _parse_int(value, default=0):
    try:
        return int(str(value or "0").replace(",", ""))
    except (TypeError, ValueError):
        return default


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


def _explore_rift_next_time_blocks(now):
    return cd_blocks(state.get("next_explore_rift_time", 0), now, 0)


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
    options = []
    for match in RE_REBIRTH_OPTION.finditer(str(text or "")):
        root_text = str(match.group("root") or "").strip()
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
                "fate": str(match.group("fate") or "").strip(),
            }
        )
    return options


def choose_safe_rebirth_option(options):
    for option in options or []:
        if str((option or {}).get("fate") or "").strip() == "稳妥之身":
            return option
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
    fatal_due_at = float(state.get("explore_rift_fatal_confirm_due_at", 0) or 0)
    if fatal_due_at > 0:
        lines.append(f"- 大凶确认：{fmt_abs_ts(fatal_due_at)}（{fmt_remaining(fatal_due_at)}）")
    weak_until = float(state.get("explore_rift_nascent_escape_weak_until", 0) or 0)
    if weak_until > 0:
        lines.append(f"- 元婴虚弱至：{fmt_abs_ts(weak_until)}（{fmt_remaining(weak_until)}）")
    if state.get("explore_rift_rebirth_required"):
        lines.append(f"- 夺舍阶段：{state.get('explore_rift_rebirth_phase') or 'idle'}")
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

        command = f"{CMD_REBIRTH_SELECT_PREFIX} {int(selected['index'])}"
        msg = await send_game_command(command, track=False, max_retry=0, source_module="探寻裂缝")
        if not msg:
            state["explore_rift_rebirth_phase"] = "manual_required"
            state["explore_rift_manual_required"] = True
            state["explore_rift_rebirth_last_error"] = "重生命令发送失败"
            save_state()
            await send_audit_log("❌ 重生命令发送失败，请人工处理。", scope="identity", limit=240)
            return True
        state["explore_rift_rebirth_phase"] = "selecting"
        state["explore_rift_rebirth_select_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["explore_rift_rebirth_selected_index"] = int(selected["index"])
        state["explore_rift_rebirth_due_at"] = float(now + EXPLORE_RIFT_REBIRTH_REPLY_TIMEOUT_SEC)
        state["explore_rift_rebirth_last_result"] = f"已选稳妥 {selected['index']}｜{selected['name']}｜{selected['root_text']}"
        state["explore_rift_rebirth_last_error"] = ""
        state["explore_rift_manual_required"] = False
        save_state()
        await send_audit_log(
            f"🕳 自动重生选择稳妥之身：{selected['index']}｜{selected['name']}｜{selected['root_text']}",
            scope="identity",
            limit=360,
        )
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
    state["explore_rift_last_result"] = f"{EXPLORE_RIFT_FATAL_TITLE}｜肉身崩毁"
    state["explore_rift_last_error"] = ""
    state["explore_rift_last_result_key"] = _make_result_key(fatal_msg_id, EXPLORE_RIFT_FATAL_TITLE, EXPLORE_RIFT_FATAL_TITLE)
    next_time = _schedule_next_explore_rift(now)
    save_state()
    await send_audit_log(f"🕳 探寻裂缝大凶已确认｜下次 {fmt_abs_ts(next_time)}", scope="identity", limit=300)
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
    if request_msg_id > 0 or select_msg_id > 0:
        _clear_explore_rift_rebirth_pending()
        state["explore_rift_rebirth_phase"] = "idle"
        state["explore_rift_rebirth_last_error"] = "夺舍回复超时，准备重试"
        save_state()
        await send_audit_log("⚠️ 夺舍回复超时，准备重试。", scope="identity", limit=220)
        return True

    await _send_rebirth_request(now)
    return True


async def handle_explore_rift_reply(text, now, reply_to=None, matched_family=None, result_msg_id=0):
    if not state.get("explore_rift_enabled") and not state.get("explore_rift_rebirth_required") and not int(state.get("explore_rift_fatal_msg_id", 0) or 0):
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

    final_title = _explore_rift_final_title(raw_text)
    if final_title:
        result_key = _make_result_key(result_msg_id, final_title, raw_text)
        if state.get("explore_rift_last_result_key") == result_key:
            return True
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
        await send_audit_log(f"🕳 探寻裂缝结果：{result_summary}", scope="identity", limit=220)
        return True

    if any(keyword in raw_text for keyword in ("时空异兽", "探寻机缘", "成功捕获了几缕逸散的法则本源")):
        if result_msg_id > 0:
            _set_explore_rift_pending_result(result_msg_id, now=now)
        state["explore_rift_last_result"] = "探寻中"
        state["explore_rift_last_error"] = ""
        save_state()
        return True

    if any(keyword in raw_text for keyword in ("境界不足", "元婴初期", "元婴期", "未到元婴", "修为不足", "主魂的一缕分神")):
        _clear_explore_rift_pending()
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
        save_state()
        await send_audit_log(f"🕳 探寻裂缝 CD→{fmt_time_after(wait_sec + CD_BUFFER_SEC)}")
        return True

    return False


async def _prepare_explore_rift_tianxing_route(now, *, due_at=0):
    due_at = float(due_at or now)
    preflight = build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now)
    if preflight.get("route_allowed"):
        return True
    blocked_until = float(preflight.get("blocked_until", 0) or 0)
    if blocked_until > now:
        state["next_explore_rift_time"] = blocked_until + CD_BUFFER_SEC
        state["explore_rift_last_error"] = str(preflight.get("reason") or "天星预检阻断")
        save_state()
        return False
    if preflight.get("timeline_required"):
        windows = build_tianxing_consume_window("探索", now=now, due_at=max(due_at, now), reason="探寻裂缝")
        if not windows:
            return True
        timeline_result = await run_tianxing_timeline_scheduler(now, windows=windows)
        if due_at <= now:
            state["next_explore_rift_time"] = float(now + RETRY_MAX_SEC)
        state["explore_rift_last_result"] = f"天星时间线：{timeline_result.get('phase') or 'waiting'}"
        state["explore_rift_last_error"] = "" if timeline_result.get("changed") else str(preflight.get("reason") or "")
        save_state()
        return False
    if due_at <= now:
        state["next_explore_rift_time"] = float(now + RETRY_MAX_SEC)
    state["explore_rift_last_error"] = str(preflight.get("reason") or "天星预检阻断")
    save_state()
    return False


async def run_explore_rift_scheduler(now):
    if await _confirm_pending_fatal(now):
        return
    if await _run_rebirth_scheduler(now):
        return

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

    next_explore_rift_time = float(state.get("next_explore_rift_time", 0) or 0)
    if next_explore_rift_time > now:
        windows = build_tianxing_consume_window("探索", now=now, due_at=next_explore_rift_time, reason="探寻裂缝")
        if windows and not await _prepare_explore_rift_tianxing_route(now, due_at=next_explore_rift_time):
            return

    if _explore_rift_next_time_blocks(now):
        return

    if not await _prepare_explore_rift_tianxing_route(now, due_at=now):
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
    "EXPLORE_RIFT_ESCAPE_WEAK_TITLE",
    "EXPLORE_RIFT_FATAL_TITLE",
    "EXPLORE_RIFT_PENDING_KEYWORD",
    "EXPLORE_RIFT_RESULT_TITLE",
    "choose_safe_rebirth_option",
    "clear_explore_rift_state",
    "classify_rebirth_text",
    "get_explore_rift_status_text",
    "handle_explore_rift_reply",
    "is_explore_rift_reply_text",
    "parse_explore_rift_result_summary",
    "parse_rebirth_options",
    "run_explore_rift_scheduler",
    "schedule_explore_rift_initial_check",
]
