"""太一门自动修炼模块。

设计原则：
1. 单线性流水线：引道 → (确认成功且开启则延迟 10-25s 联动搜寻) → (找到才定星)
2. 仅对"唯一 pending 身份"兜底识别未路由的引道回复，避免 topic reply 漏接
3. 三个 reply handler 都做 phase guard，迟到 reply 不推进流程
4. 太一引道/搜寻节点按固定 12h CD 运行，不因回复/定星耗时漂移
5. 失败熔断：24h 内 5 次失败自动停子开关
6. bot 吞回兜底：reply 60s 没回视为吞回，按当前 phase 短补发（带 jitter 防风暴）
7. 链路指令不走通用 retry；每一阶段只接受当前 msg_id 的回复
8. bot 全局宕机：复用现有 _bot_silence 机制（外部）
9. 仅落云宗 -> 灵树 那种"按宗门匹配"的 UI（state.py 的 get_available_module_names 处理）

phase 状态机：
    idle               - 等下次 cycle
    yindao_pending     - 已发 .引道 X，等回复（60s 没回则短补发）
    search_scheduled   - 引道已确认成功，等待 10-25s 延迟后联动 .搜寻节点
    search_pending     - 已发 .搜寻节点，等回复（60s 没回则短补发）
    define_pending     - 已发 .定星 <node>，等回复（60s 没回则短补发）
    frozen             - 境界不足/非弟子，长冻结
"""

import asyncio
import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_YINDAO,
    TAIYI_CYCLE_CD_SEC,
    TAIYI_DEFINE_DELAY_MAX,
    TAIYI_DEFINE_DELAY_MIN,
    TAIYI_FAILURE_LIMIT,
    TAIYI_FAILURE_WINDOW_SEC,
    TAIYI_FROZEN_RETRY_SEC,
    TAIYI_PHASE_TIMEOUT_SEC,
    TAIYI_RESOURCE_RETRY_SEC,
    TAIYI_VALID_ELEMENTS,
)
from ..persistence import save_state
from ..runtime import (
    _fire_and_forget,
    console_log,
    get_bot_last_seen_at,
    mark_bot_health_suspect,
    note_identity_weakness,
    send_audit_log,
    send_game_command,
)
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from .resource_backoff import record_resource_shortage, reset_resource_shortage


RE_YINDAO_SUCCESS = re.compile(r"你引动【([金木水火土])之道】")
RE_YINDAO_CD = re.compile(r"大道感悟需循序渐进")
RE_NODE_NAME = re.compile(r"获得：【(空间节点·[^】]+)】")
RE_DEFINE_SUCCESS = re.compile(r"【定星成功】")

# 引道成功确认后，10-25s 随机延迟联动发 .搜寻节点（链式指令间隔）
TAIYI_LINKED_SEARCH_DELAY_MIN_SEC = 10
TAIYI_LINKED_SEARCH_DELAY_MAX_SEC = 25
# yindao_pending / search_pending / define_pending 等 reply：60s 没回当吞了
TAIYI_REPLY_LOST_TIMEOUT_SEC = 60
# 补发前的 jitter：避免多身份同步爆发
TAIYI_REPLY_LOST_RESEND_JITTER_SEC = 30
TAIYI_SEARCH_RESEND_MAX_PER_CYCLE = 1
TAIYI_NODE_SEARCH_RESOURCE_KEY = "taiyi_node_search"
TAIYI_PENDING_PHASE_LABELS = {
    "yindao_pending": "引道",
    "search_pending": "搜寻节点",
    "define_pending": "定星",
}
TAIYI_MODULE_LOADED_AT = time.time()


def _phase():
    return state.get("taiyi_phase", "idle")


def _set_phase(new_phase, now=None):
    """切换 phase 并记录进入时间，用于超时兜底。"""
    import time
    if now is None:
        now = time.time()
    state["taiyi_phase"] = new_phase
    state["taiyi_phase_entered_at"] = now


def _clear_chain_msg_ids():
    state["taiyi_yindao_msg_id"] = 0
    state["taiyi_node_search_msg_id"] = 0
    state["taiyi_node_define_msg_id"] = 0


def _is_current_reply(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if expected_msg_id <= 0 or reply_to_msg_id <= 0:
        return True
    return reply_to_msg_id == expected_msg_id


async def _send_taiyi_search(now):
    msg = await send_game_command(CMD_NODE_SEARCH, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _set_phase("idle", sent_at)
        state["next_taiyi_cycle_time"] = sent_at + TAIYI_RESOURCE_RETRY_SEC
        state["taiyi_node_search_msg_id"] = 0
        _record_failure(sent_at, "搜寻节点发送失败")
        save_state()
        await send_audit_log("❌ 太一搜寻节点发送失败，1h 后重试。")
        await _check_failure_breaker(sent_at)
        return False
    _set_phase("search_pending", sent_at)
    state["taiyi_node_search_msg_id"] = int(getattr(msg, "id", 0) or 0)
    save_state()
    return True


def _next_fixed_cycle(base_time):
    """太一引道/搜寻节点固定 12h CD；只加很小缓冲防边界早发。"""
    return float(base_time or time.time()) + TAIYI_CYCLE_CD_SEC + CD_BUFFER_SEC


def _ensure_next_fixed_cycle(base_time):
    if state.get("next_taiyi_cycle_time", 0) <= time.time():
        state["next_taiyi_cycle_time"] = _next_fixed_cycle(base_time)
    return state["next_taiyi_cycle_time"]


def _current_yindao_sent_at(now):
    entered_at = float(state.get("taiyi_phase_entered_at", 0) or 0)
    if entered_at > 0 and now - entered_at <= TAIYI_PHASE_TIMEOUT_SEC:
        return entered_at
    return now


def _looks_like_yindao_reply(text):
    raw = text or ""
    return bool(
        RE_YINDAO_SUCCESS.search(raw)
        or RE_YINDAO_CD.search(raw)
        or "你并非太一门弟子" in raw
    )


def _looks_like_node_search_cd(text):
    raw = text or ""
    if not has_wait_time(raw):
        return False
    if "请在" not in raw and "后" not in raw:
        return False
    return any(keyword in raw for keyword in ("搜寻节点", "神游太虚", "虚空", "空间节点", "神识尚在恢复", "再行搜寻"))


def _is_node_search_disaster(text):
    raw = str(text or "")
    return (
        ("【大凶之兆】" in raw or "【大凶之兆！】" in raw)
        and ("虚空风暴" in raw or "空间乱流" in raw)
        and ("虚弱状态" in raw or "元气大伤" in raw)
    )


async def _close_node_search_disaster(text, now, *, source):
    _reset_search_resend_count()
    _set_phase("idle", now)
    state["next_taiyi_cycle_time"] = _next_fixed_cycle(now)
    _clear_chain_msg_ids()
    _reset_failures()
    reset_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY)
    state["taiyi_last_error"] = "搜寻节点遭遇大凶，按正常 12h 周期等待"
    save_state()
    note_identity_weakness(text, now, source=source)
    await send_audit_log(f"🌩️ 太一搜寻遭遇大凶，已收口；下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")


def _get_search_resend_count():
    try:
        return int(state.get("taiyi_search_resend_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _reset_search_resend_count():
    state["taiyi_search_resend_count"] = 0


def _mark_search_resend(reason):
    count = _get_search_resend_count() + 1
    state["taiyi_search_resend_count"] = count
    state["taiyi_last_error"] = f"搜寻节点补发 {count}/{TAIYI_SEARCH_RESEND_MAX_PER_CYCLE}: {reason}"
    return count


def _search_resend_exhausted():
    return _get_search_resend_count() >= TAIYI_SEARCH_RESEND_MAX_PER_CYCLE


async def _defer_taiyi_search_after_resend_limit(now, elapsed, reason):
    _set_phase("idle", now)
    state["next_taiyi_cycle_time"] = now + TAIYI_RESOURCE_RETRY_SEC
    _clear_chain_msg_ids()
    state["taiyi_last_error"] = f"{reason}，已达本轮补发上限，1h 后校准"
    save_state()
    await send_audit_log(
        f"🧯 太一搜寻{reason}（{elapsed}s），已达本轮补发上限，1h 后校准，避免搜寻节点风暴。",
        scope="identity",
    )


async def _pause_taiyi_retry_if_bot_silent(stale_phase, entered_at, now, elapsed):
    """如果阶段内没有任何 bot 发言，优先交给天尊健康熔断，不做太一短补发。"""
    if stale_phase not in TAIYI_PENDING_PHASE_LABELS:
        return False
    entered_at = float(entered_at or 0)
    if entered_at < TAIYI_MODULE_LOADED_AT:
        return False
    if entered_at <= 0 or get_bot_last_seen_at() >= entered_at:
        return False

    label = TAIYI_PENDING_PHASE_LABELS.get(stale_phase, stale_phase)
    changed = mark_bot_health_suspect(
        f"太一{label} {elapsed}s 未回且无 bot 发言",
        reference_at=entered_at,
        now=now,
    )
    state["taiyi_last_error"] = f"{label} {elapsed}s 未回且无 bot 发言，等待天尊健康恢复"
    save_state()
    if changed:
        await send_audit_log(
            f"🩺 太一{label} reply {elapsed}s 未回，且期间无天尊发言；暂停短补发，交给天尊健康恢复。",
            scope="identity",
        )
    return True


def _finish_yindao_without_search(now):
    _set_phase("idle", now)
    _clear_chain_msg_ids()
    save_state()


async def _send_linked_search_after_success():
    """引道确认成功后，10-25s 随机延迟联动发 .搜寻节点。
    走全局 send_game_command 锁；中途 phase 转移则中止。"""
    delay = random.uniform(TAIYI_LINKED_SEARCH_DELAY_MIN_SEC, TAIYI_LINKED_SEARCH_DELAY_MAX_SEC)
    await asyncio.sleep(delay)
    if _phase() != "search_scheduled":
        return
    if not state.get("taiyi_node_search_enabled", False):
        _finish_yindao_without_search(time.time())
        return
    await _send_taiyi_search(time.time())


def _record_failure(now, reason=""):
    """累积 24h 内失败次数，达阈值自动熔断。"""
    history = list(state.get("taiyi_failure_history", []))
    history.append(float(now))
    history = [t for t in history if now - t < TAIYI_FAILURE_WINDOW_SEC]
    state["taiyi_failure_history"] = history
    if reason:
        state["taiyi_last_error"] = reason
    return len(history)


def _reset_failures():
    state["taiyi_failure_history"] = []
    state["taiyi_last_error"] = ""


async def _check_failure_breaker(now):
    """24h 内累计 ≥ TAIYI_FAILURE_LIMIT 次：
    - 优先关闭 node_search 子开关（保留主引道）
    - 主引道也连续失败 → 关主开关
    """
    history = state.get("taiyi_failure_history", [])
    if len(history) < TAIYI_FAILURE_LIMIT:
        return False

    if state.get("taiyi_node_search_enabled", False):
        state["taiyi_node_search_enabled"] = False
        state["taiyi_failure_history"] = []
        save_state()
        await send_audit_log(
            f"🔥 太一搜寻节点 24h 内失败 {len(history)} 次，已自动关闭搜寻子开关。",
            scope="identity",
        )
        return True

    if state.get("taiyi_enabled", False):
        state["taiyi_enabled"] = False
        state["taiyi_failure_history"] = []
        _set_phase("idle", now)
        save_state()
        await send_audit_log(
            f"🔥 太一引道 24h 内失败 {len(history)} 次，已自动关闭模块。",
            scope="identity",
        )
        return True
    return False


def _freeze(now, reason, sec=TAIYI_FROZEN_RETRY_SEC):
    state["taiyi_freeze_until"] = now + sec
    state["taiyi_freeze_reason"] = reason
    _set_phase("frozen", now)


def _is_safe_node_name(name):
    """节点名 sanity check：长度 ≤ 30，仅含中文/字母/数字/·"""
    if not name or len(name) > 30:
        return False
    return bool(re.match(r"^[一-龥A-Za-z0-9·]+$", name))


def _resolve_yindao_command():
    """获取 .引道 X 命令字符串，校验 element 合法性。"""
    element = state.get("taiyi_yindao_element", "水") or "水"
    if element not in TAIYI_VALID_ELEMENTS:
        element = "水"
        state["taiyi_yindao_element"] = "水"
    return f"{CMD_YINDAO} {element}"


def get_taiyi_status_text():
    """UI 状态显示。"""
    if not state.get("taiyi_enabled", False):
        return "🌟 太一门 - 未启用"

    lines = ["🌟 太一门"]
    element = state.get("taiyi_yindao_element", "水")
    lines.append(f"- 主修: {element} (.引道 {element})")
    lines.append(f"- 自动搜寻节点: {'是' if state.get('taiyi_node_search_enabled', False) else '否'}")

    phase = _phase()
    phase_label = {
        "idle": "闲置",
        "yindao_pending": "引道中…",
        "search_scheduled": "等待联动搜寻节点…",
        "search_pending": "搜寻节点中…",
        "define_pending": "定星中…",
        "frozen": f"冻结（{state.get('taiyi_freeze_reason','?')}）",
    }.get(phase, phase)
    lines.append(f"- 当前阶段: {phase_label}")

    if phase == "frozen":
        until = state.get("taiyi_freeze_until", 0)
        if until > 0:
            lines.append(f"- 冻结至: {fmt_abs_ts(until)}（{fmt_remaining(until)}）")
    else:
        nc = state.get("next_taiyi_cycle_time", 0)
        if nc > 0:
            lines.append(f"- 下次循环: {fmt_abs_ts(nc)}（{fmt_remaining(nc)}）")

    fail_count = len(state.get("taiyi_failure_history", []))
    if fail_count > 0:
        lines.append(f"- 24h 失败计数: {fail_count}/{TAIYI_FAILURE_LIMIT}")
    resend_count = _get_search_resend_count()
    if resend_count > 0:
        lines.append(f"- 本轮搜寻补发: {resend_count}/{TAIYI_SEARCH_RESEND_MAX_PER_CYCLE}")

    last_err = state.get("taiyi_last_error", "")
    if last_err:
        lines.append(f"- 最近异常: {last_err}")
    return "\n".join(lines)


# ============== reply 路径 handlers（仅，不注册任何 broadcast）==============

async def handle_taiyi_yindao_reply(text, now, reply_to, matched_family=None):
    """处理 .引道 X 命令的回复。"""
    if not state.get("taiyi_enabled", False):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "taiyi_yindao"
        or CMD_YINDAO in orig_cmd
    )
    if not is_relevant and _phase() == "yindao_pending" and _looks_like_yindao_reply(text):
        is_relevant = True
    if not is_relevant:
        return False

    # phase guard：迟到 reply 仅清 pending 不推进流程
    if _phase() != "yindao_pending":
        console_log(f"⚠️ 引道 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
        return True
    if not _is_current_reply(reply_to, "taiyi_yindao_msg_id"):
        console_log("⚠️ 忽略迟到的太一引道回复。")
        return True

    # 成功
    if RE_YINDAO_SUCCESS.search(text) and "100点神识" in text:
        _reset_failures()
        _reset_search_resend_count()
        sent_at = _current_yindao_sent_at(now)
        # 引道确认成功才写 12h 节拍（不预写）
        state["next_taiyi_cycle_time"] = _next_fixed_cycle(sent_at)
        if state.get("taiyi_node_search_enabled", False):
            _set_phase("search_scheduled", now)
            state["taiyi_yindao_msg_id"] = 0
            save_state()
            await send_audit_log(
                f"🌟 太一引道确认成功，{TAIYI_LINKED_SEARCH_DELAY_MIN_SEC}-{TAIYI_LINKED_SEARCH_DELAY_MAX_SEC}s 后联动搜寻｜下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}"
            )
            _fire_and_forget(_send_linked_search_after_success())
        else:
            _set_phase("idle", now)
            _clear_chain_msg_ids()
            save_state()
            await send_audit_log(f"🌟 太一引道成功（+100 神识），下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # CD：大道感悟需循序渐进
    if RE_YINDAO_CD.search(text):
        _reset_search_resend_count()
        wait_sec = parse_wait_time(text)
        if has_wait_time(text) and wait_sec > 0:
            state["next_taiyi_cycle_time"] = now + wait_sec + CD_BUFFER_SEC
        else:
            # 解析失败：保守回退 12h
            state["next_taiyi_cycle_time"] = now + TAIYI_CYCLE_CD_SEC
        _set_phase("idle", now)
        _clear_chain_msg_ids()
        save_state()
        await send_audit_log(f"⏳ 太一引道 CD→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 非弟子
    if "你并非太一门弟子" in text:
        _freeze(now, "非太一门弟子")
        _clear_chain_msg_ids()
        save_state()
        await send_audit_log("ℹ️ 你并非太一门弟子，太一模块已冻结 7 天。")
        return True

    # 未识别的回复：记录为失败但不改 phase（让 phase 超时兜底处理）
    state["taiyi_last_error"] = f"未识别的引道回复: {text[:60]}"
    save_state()
    return True


async def handle_taiyi_node_search_reply(text, now, reply_to, matched_family=None):
    """处理 .搜寻节点 命令的回复。"""
    if not state.get("taiyi_enabled", False):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "taiyi_node_search"
        or CMD_NODE_SEARCH in orig_cmd
        or ("修为不足" in text and "神游太虚" in text)
        or ("神识不足" in text and "虚空中定位" in text)
        or _is_node_search_disaster(text)
    )
    if not is_relevant:
        return False

    if _phase() != "search_pending":
        console_log(f"⚠️ 搜寻节点 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
        if _is_node_search_disaster(text):
            await _close_node_search_disaster(text, now, source="taiyi_node_search_late")
        return True
    if not _is_current_reply(reply_to, "taiyi_node_search_msg_id"):
        console_log("⚠️ 忽略迟到的太一搜寻节点回复。")
        if _is_node_search_disaster(text):
            await _close_node_search_disaster(text, now, source="taiyi_node_search_late")
        return True

    # 大凶/虚空风暴是搜寻节点的有效结果：本轮结束，进入正常 12h 周期。
    if _is_node_search_disaster(text):
        await _close_node_search_disaster(text, now, source="taiyi_node_search")
        return True

    # 找到节点（含"获得：【空间节点·X】"）
    m = RE_NODE_NAME.search(text)
    if m:
        _reset_search_resend_count()
        node_name = m.group(1).strip()
        if not _is_safe_node_name(node_name):
            _set_phase("idle", now)
            _ensure_next_fixed_cycle(now)
            _clear_chain_msg_ids()
            _record_failure(now, f"节点名解析异常: {node_name[:30]}")
            save_state()
            await send_audit_log(f"⚠️ 节点名异常已跳过定星: {node_name[:30]}")
            await _check_failure_breaker(now)
            return True

        # 链式：fire-and-forget 短延迟后定星（1.5-3.5s 模拟反应时间）
        state["taiyi_pending_node_name"] = node_name
        state["taiyi_node_search_msg_id"] = 0
        state["taiyi_node_define_msg_id"] = 0
        reset_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY)
        _set_phase("define_pending", now)
        save_state()

        async def delayed_define():
            await asyncio.sleep(random.uniform(TAIYI_DEFINE_DELAY_MIN, TAIYI_DEFINE_DELAY_MAX))
            # ★ guard：define_pending 期间且节点名仍是这个才发
            if _phase() != "define_pending":
                return
            if state.get("taiyi_pending_node_name", "") != node_name:
                return
            msg = await send_game_command(f"{CMD_NODE_DEFINE} {node_name}", track=False, priority="chain")
            sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
            if _phase() != "define_pending" or state.get("taiyi_pending_node_name", "") != node_name:
                return
            if msg:
                _set_phase("define_pending", sent_at)
                state["taiyi_node_define_msg_id"] = int(getattr(msg, "id", 0) or 0)
                save_state()
                return
            state["taiyi_pending_node_name"] = ""
            state["taiyi_node_define_msg_id"] = 0
            _set_phase("idle", sent_at)
            state["next_taiyi_cycle_time"] = sent_at + TAIYI_RESOURCE_RETRY_SEC
            _record_failure(sent_at, "定星发送失败")
            save_state()
            await send_audit_log("❌ 太一定星发送失败，1h 后重试。")
            await _check_failure_breaker(sent_at)

        _fire_and_forget(delayed_define())
        await send_audit_log(f"🎯 太一搜寻发现【{node_name}】，将自动定星。")
        return True

    # 一无所获 / 击败天魔（虚空漫游、斩妖除魔）
    if "【虚空漫游】" in text or "【斩妖除魔】" in text:
        _reset_search_resend_count()
        _set_phase("idle", now)
        _ensure_next_fixed_cycle(now)
        _clear_chain_msg_ids()
        _reset_failures()  # 这是正常空轮，不算失败
        reset_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY)
        save_state()
        console_log(f"🌟 太一搜寻空轮，下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 搜寻节点固定 CD：如果本轮实际未到点，直接尊重 bot 给出的剩余时间。
    if _looks_like_node_search_cd(text):
        _reset_search_resend_count()
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            state["next_taiyi_cycle_time"] = now + wait_sec + CD_BUFFER_SEC
        else:
            state["next_taiyi_cycle_time"] = _next_fixed_cycle(now)
        _set_phase("idle", now)
        _clear_chain_msg_ids()
        reset_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY)
        save_state()
        await send_audit_log(f"⏳ 太一搜寻节点 CD→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 修为不足
    if "修为不足" in text and "神游太虚" in text:
        _reset_search_resend_count()
        backoff = record_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY, now, reason=text)
        due_at = float(backoff.get("next_at", 0) or 0)
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = due_at
        _clear_chain_msg_ids()
        _record_failure(now, "修为不足")
        save_state()
        await send_audit_log(
            f"⚠️ 太一搜寻修为不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        await _check_failure_breaker(now)
        return True

    # 神识不足（理论上引道刚补 100 不应该发生，记录为异常）
    if "神识不足" in text and "虚空中定位" in text:
        _reset_search_resend_count()
        backoff = record_resource_shortage(TAIYI_NODE_SEARCH_RESOURCE_KEY, now, reason=text)
        due_at = float(backoff.get("next_at", 0) or 0)
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = due_at
        _clear_chain_msg_ids()
        _record_failure(now, "神识不足（异常）")
        save_state()
        await send_audit_log(
            f"⚠️ 太一搜寻神识不足，第 {int(backoff.get('count', 1) or 1)} 档退避→{fmt_time_after(max(0, due_at - now))}"
        )
        await _check_failure_breaker(now)
        return True

    # 境界不足
    if "【境界不足】" in text and "化神期" in text:
        _reset_search_resend_count()
        _freeze(now, "境界不足（化神期前）")
        _clear_chain_msg_ids()
        save_state()
        await send_audit_log("ℹ️ 境界不足化神期，太一搜寻已冻结 7 天。")
        return True

    # 未识别
    state["taiyi_last_error"] = f"未识别的搜寻回复: {text[:60]}"
    save_state()
    return False


async def handle_taiyi_node_define_reply(text, now, reply_to, matched_family=None):
    """处理 .定星 X 命令的回复。"""
    if not state.get("taiyi_enabled", False):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "taiyi_node_define"
        or CMD_NODE_DEFINE in orig_cmd
    )
    if not is_relevant:
        return False

    if _phase() != "define_pending":
        console_log(f"⚠️ 定星 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
        return True
    if not _is_current_reply(reply_to, "taiyi_node_define_msg_id"):
        console_log("⚠️ 忽略迟到的太一定星回复。")
        return True

    if RE_DEFINE_SUCCESS.search(text):
        _reset_search_resend_count()
        # 提取额外产出做 audit
        node_name = state.get("taiyi_pending_node_name", "?")
        state["taiyi_pending_node_name"] = ""
        _set_phase("idle", now)
        _ensure_next_fixed_cycle(now)
        _clear_chain_msg_ids()
        _reset_failures()
        save_state()

        # 提取关键产出（逆灵通道坐标 / 虚灵丹丹方 / 法则碎片 / 万年灵乳 等）
        highlights = []
        for kw in ["逆灵通道坐标", "虚灵丹丹方", "法则碎片", "万年灵乳", "虚空尘埃"]:
            if kw in text:
                highlights.append(kw)

        msg = f"🎉 太一定星【{node_name}】成功"
        if highlights:
            msg += f"｜产出: {', '.join(highlights)}"
        msg += f"｜下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}"
        await send_audit_log(msg)
        return True

    # 未预期的失败/未识别（理论不会，定星免费成功）
    state["taiyi_last_error"] = f"未识别的定星回复: {text[:60]}"
    state["taiyi_pending_node_name"] = ""
    _set_phase("idle", now)
    _ensure_next_fixed_cycle(now)
    _clear_chain_msg_ids()
    _record_failure(now, "定星回复异常")
    save_state()
    await send_audit_log(f"⚠️ 太一定星回复异常: {text[:80]}")
    await _check_failure_breaker(now)
    return True


# ============== scheduler ==============

async def run_taiyi_scheduler(now):
    if not state.get("taiyi_enabled", False):
        return

    phase = _phase()

    # 冻结：到期则解冻自检
    if phase == "frozen":
        if state.get("taiyi_freeze_until", 0) > now:
            return
        # 解冻
        _set_phase("idle", now)
        state["taiyi_freeze_until"] = 0
        state["taiyi_freeze_reason"] = ""
        state["next_taiyi_cycle_time"] = now  # 立即查
        save_state()
        await send_audit_log("🔓 太一冻结期已过，恢复尝试。")
        phase = "idle"

    # phase 超时兜底：reply 60s 没回视为 bot 吞回，按 phase 类型补发
    if phase != "idle":
        entered_at = state.get("taiyi_phase_entered_at", 0)
        timeout_sec = TAIYI_REPLY_LOST_TIMEOUT_SEC
        if entered_at <= 0 or now - entered_at <= timeout_sec:
            return  # 仍在等 reply
        stale_phase = phase
        elapsed = int(now - entered_at)
        if await _pause_taiyi_retry_if_bot_silent(stale_phase, entered_at, now, elapsed):
            return

        if stale_phase == "yindao_pending":
            # 引道 reply 漏了：让下轮 scheduler 重发
            _set_phase("idle", now)
            state["next_taiyi_cycle_time"] = now + random.uniform(0, TAIYI_REPLY_LOST_RESEND_JITTER_SEC)
            _clear_chain_msg_ids()
            _record_failure(now, f"引道 reply {elapsed}s 未回")
            save_state()
            await send_audit_log(f"🩺 太一引道 reply {elapsed}s 未回，按吞回判定，将于 ≤{TAIYI_REPLY_LOST_RESEND_JITTER_SEC}s 内补发。")
            await _check_failure_breaker(now)
            phase = "idle"

        elif stale_phase == "search_scheduled":
            # 联动延迟任务挂了（如服务重启），立即补发搜寻
            if _search_resend_exhausted():
                await _defer_taiyi_search_after_resend_limit(now, elapsed, "延迟任务异常")
                return
            _mark_search_resend("延迟任务异常")
            await send_audit_log(f"🩺 太一搜寻延迟任务异常（卡 {elapsed}s），补发 .搜寻节点（本轮最多 {TAIYI_SEARCH_RESEND_MAX_PER_CYCLE} 次）。")
            await _send_taiyi_search(now)
            return

        elif stale_phase == "search_pending":
            # 搜寻 reply 漏了：补发搜寻
            if _search_resend_exhausted():
                await _defer_taiyi_search_after_resend_limit(now, elapsed, "reply 未回")
                return
            _mark_search_resend("reply 未回")
            await send_audit_log(f"🩺 太一搜寻 reply {elapsed}s 未回，按吞回判定补发 .搜寻节点（本轮最多 {TAIYI_SEARCH_RESEND_MAX_PER_CYCLE} 次）。")
            await _send_taiyi_search(now)
            return

        elif stale_phase == "define_pending":
            node_name = state.get("taiyi_pending_node_name", "")
            if not node_name:
                _set_phase("idle", now)
                _ensure_next_fixed_cycle(now)
                _clear_chain_msg_ids()
                save_state()
                await send_audit_log("🩺 太一定星 reply 吞回，但节点名丢失，跳过本轮。")
                return
            await send_audit_log(f"🩺 太一定星【{node_name}】reply {elapsed}s 未回，按吞回判定，补发。")
            msg = await send_game_command(f"{CMD_NODE_DEFINE} {node_name}", track=False, priority="chain")
            sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
            if msg:
                _set_phase("define_pending", sent_at)
                state["taiyi_node_define_msg_id"] = int(getattr(msg, "id", 0) or 0)
                save_state()
            else:
                state["taiyi_pending_node_name"] = ""
                _set_phase("idle", sent_at)
                _ensure_next_fixed_cycle(sent_at)
                _clear_chain_msg_ids()
                _record_failure(sent_at, "定星补发失败")
                save_state()
                await send_audit_log("❌ 太一定星补发失败，等下个 cycle。")
                await _check_failure_breaker(sent_at)
            return

        else:
            # 兜底（理论不到，frozen 已 return）
            _set_phase("idle", now)
            save_state()
            return

    # idle 且到点：发引道
    if state.get("next_taiyi_cycle_time", 0) > now:
        return

    cmd = _resolve_yindao_command()
    _reset_search_resend_count()
    _set_phase("yindao_pending", now)
    state["taiyi_yindao_msg_id"] = 0
    state["taiyi_node_search_msg_id"] = 0
    state["taiyi_node_define_msg_id"] = 0
    save_state()
    msg = await send_game_command(cmd, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        # 发送失败：回退 idle 短退避
        _set_phase("idle", sent_at)
        state["next_taiyi_cycle_time"] = sent_at + 60  # 1min 后重试
        _clear_chain_msg_ids()
        _record_failure(sent_at, "引道发送失败")
        save_state()
        await send_audit_log("❌ 太一引道发送失败，1min 后重试。")
        await _check_failure_breaker(sent_at)
    else:
        yindao_msg_id = int(getattr(msg, "id", 0) or 0)
        # 不预写 next_taiyi_cycle_time —— 等成功 reply 才写 12h；漏 reply 由 phase 超时短补发兜底
        state["taiyi_yindao_msg_id"] = yindao_msg_id
        state["taiyi_node_search_msg_id"] = 0
        state["taiyi_node_define_msg_id"] = 0
        state["taiyi_last_error"] = ""
        _set_phase("yindao_pending", sent_at)
        save_state()


async def run_taiyi_bootstrap_check(now):
    """已弃用：残留清理移至 control._restore_taiyi_runtime（启动一次性）。
    保留空函数以维持 scheduler 注册兼容。
    """
    return


__all__ = [
    "get_taiyi_status_text",
    "handle_taiyi_node_define_reply",
    "handle_taiyi_node_search_reply",
    "handle_taiyi_yindao_reply",
    "run_taiyi_bootstrap_check",
    "run_taiyi_scheduler",
]
