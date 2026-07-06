"""太一门自动修炼模块。

设计原则：
1. 单线性流水线：引道 → (确认成功且开启则延迟 10-25s 联动搜寻) → (找到才定星)
2. 仅对"唯一 pending 身份"兜底识别未路由的引道回复，避免 topic reply 漏接
3. 三个 reply handler 都做 phase guard，迟到 reply 不推进流程
4. 太一引道/搜寻节点按固定 12h CD 运行，不因回复/定星耗时漂移
5. 失败熔断：24h 内 5 次失败自动停子开关
6. bot 吞回兜底：引道等不到 reply 时本轮最多短补发一次，仍无回复才按正常 12h 周期收口
7. 链路指令不走通用 retry；每一阶段只接受当前 msg_id 的回复
8. bot 全局宕机：复用现有 _bot_silence 机制（外部）
9. 仅落云宗 -> 灵树 那种"按宗门匹配"的 UI（state.py 的 get_available_module_names 处理）

phase 状态机：
    idle               - 等下次 cycle
    yindao_pending     - 已发 .引道 X，等回复（超时按 12h 周期兜底）
    search_scheduled   - 引道已确认成功，等待 10-25s 延迟后联动 .搜寻节点
    search_pending     - 已发 .搜寻节点，等回复（超时按 12h 周期兜底）
    define_pending     - 已发 .定星 <node>，等回复（超时按 12h 周期兜底）
    frozen             - 境界不足/非弟子，长冻结
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..config import (
    CD_BUFFER_SEC,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_YINDAO,
    MESSAGES_DIR,
    TAIYI_CYCLE_CD_SEC,
    TAIYI_DEFINE_DELAY_MAX,
    TAIYI_DEFINE_DELAY_MIN,
    TAIYI_FAILURE_LIMIT,
    TAIYI_FAILURE_WINDOW_SEC,
    TAIYI_FROZEN_RETRY_SEC,
    TAIYI_PHASE_TIMEOUT_SEC,
    TAIYI_RESOURCE_RETRY_SEC,
    TAIYI_VALID_ELEMENTS,
    TZ_LOCAL,
)
from ..persistence import save_state
from ..runtime import (
    _fire_and_forget,
    classify_game_send_block,
    console_log,
    get_bot_last_seen_at,
    mark_bot_health_suspect,
    note_identity_weakness,
    send_audit_log,
    send_game_command,
)
from ..state import get_current_identity_id, state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time
from . import workflow_log
from .resource_backoff import record_resource_shortage, reset_resource_shortage


RE_YINDAO_SUCCESS = re.compile(r"你引动【([金木水火土])之道】")
RE_YINDAO_CD = re.compile(r"大道感悟需循序渐进")
RE_NODE_NAME = re.compile(r"获得：【(空间节点·[^】]+)】")
RE_DEFINE_SUCCESS = re.compile(r"【定星成功】")

# 引道成功确认后，10-25s 随机延迟联动发 .搜寻节点（链式指令间隔）
TAIYI_LINKED_SEARCH_DELAY_MIN_SEC = 10
TAIYI_LINKED_SEARCH_DELAY_MAX_SEC = 25
# yindao_pending / search_pending / define_pending 等 reply：60s 没回后兜底；引道本轮最多短补发一次
TAIYI_REPLY_LOST_TIMEOUT_SEC = 60
TAIYI_PRESEND_RETRY_MIN_SEC = 60
TAIYI_PRESEND_RETRY_MAX_SEC = 300
TAIYI_YINDAO_RESEND_MIN_SEC = 2
TAIYI_YINDAO_RESEND_MAX_SEC = 3
TAIYI_YINDAO_RESEND_MAX_PER_CYCLE = 1
TAIYI_SEARCH_RESEND_MAX_PER_CYCLE = 1
TAIYI_NODE_SEARCH_RESOURCE_KEY = "taiyi_node_search"
TAIYI_SEND_EVIDENCE_LOOKBACK_SEC = 120
TAIYI_SEND_EVIDENCE_LOOKAHEAD_SEC = 30
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


def _record_taiyi_event(
    event,
    *,
    kind="changed",
    reason="",
    family="",
    command="",
    msg_id=0,
    reply_msg_id=0,
    phase="",
    detail="",
    matched_text="",
    decision="",
    route_source="taiyi",
):
    try:
        if not family and command:
            command_text = str(command or "").strip()
            if command_text == CMD_YINDAO or command_text.startswith(f"{CMD_YINDAO} "):
                family = "taiyi_yindao"
            elif command_text == CMD_NODE_SEARCH or command_text.startswith(f"{CMD_NODE_SEARCH} "):
                family = "taiyi_node_search"
            elif command_text == CMD_NODE_DEFINE or command_text.startswith(f"{CMD_NODE_DEFINE} "):
                family = "taiyi_node_define"
        parts = [str(event or "太一事件").strip() or "太一事件"]
        phase_text = str(phase or _phase() or "").strip()
        identity_id = get_current_identity_id()
        if phase_text:
            parts.append(f"phase={phase_text}")
        if msg_id:
            parts.append(f"msg_id={int(msg_id)}")
        if command:
            parts.append(str(command).strip())
        if detail:
            parts.append(str(detail).strip())
        workflow_log.append_workflow_event(
            "taiyi",
            op_id=f"{identity_id}:{phase_text}" if identity_id and phase_text else "",
            step=phase_text,
            event=str(event or "太一事件").strip() or "太一事件",
            status=kind,
            identity_id=identity_id,
            msg_id=msg_id,
            reply_to_msg_id=reply_msg_id,
            family=family,
            command=command,
            text=matched_text,
            decision=decision or str(event or "").strip(),
            detail={"reason": reason, "detail": detail},
            route_source=route_source,
            state_after=phase_text,
        )
        from . import passive_inbox

        return passive_inbox.record_passive_inbox_event(
            kind,
            module="taiyi",
            identity_id=identity_id,
            reason=reason,
            summary="｜".join(part for part in parts if part),
            family=family,
            msg_id=msg_id,
            reply_to_msg_id=reply_msg_id,
            route_source=route_source,
            matched_text=matched_text,
            decision=decision or str(event or "").strip(),
            state_after=phase_text,
            command=command,
        )
    except Exception:
        return False


def _is_current_reply(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if expected_msg_id <= 0 or reply_to_msg_id <= 0:
        return True
    return reply_to_msg_id == expected_msg_id


def _classify_taiyi_none_send(command):
    return classify_game_send_block(get_current_identity_id(), command)


def _mark_taiyi_unknown_send(command, phase, msg_key, sent_at, label):
    _set_phase(phase, sent_at)
    state[msg_key] = 0
    state["taiyi_last_error"] = f"{label}发送状态未知，等待回包或日志校准"
    _record_taiyi_event(
        f"{label}发送状态未知",
        kind="waiting",
        reason="send_status_unknown",
        command=command,
        phase=phase,
        decision="wait_for_reply_or_log_recovery",
    )
    save_state()


async def _send_taiyi_search(now):
    msg = await send_game_command(CMD_NODE_SEARCH, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        send_block = _classify_taiyi_none_send(CMD_NODE_SEARCH)
        if str(send_block.get("status") or "") == "unknown":
            _mark_taiyi_unknown_send(CMD_NODE_SEARCH, "search_pending", "taiyi_node_search_msg_id", sent_at, "搜寻节点")
            return True
        _set_phase("idle", sent_at)
        state["next_taiyi_cycle_time"] = sent_at + TAIYI_RESOURCE_RETRY_SEC
        state["taiyi_node_search_msg_id"] = 0
        _record_taiyi_event("搜寻节点发送失败", command=CMD_NODE_SEARCH, phase="idle")
        _record_failure(sent_at, "搜寻节点发送失败")
        save_state()
        await send_audit_log("❌ 太一搜寻节点发送失败，1h 后重试。")
        await _check_failure_breaker(sent_at)
        return False
    _set_phase("search_pending", sent_at)
    state["taiyi_node_search_msg_id"] = int(getattr(msg, "id", 0) or 0)
    _record_taiyi_event(
        "搜寻节点已发送",
        command=CMD_NODE_SEARCH,
        msg_id=state["taiyi_node_search_msg_id"],
        phase="search_pending",
    )
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


def _is_yindao_success_text(text):
    raw = str(text or "")
    return bool(RE_YINDAO_SUCCESS.search(raw) and "100点神识" in raw)


def _parse_message_log_ts(raw_ts):
    ts_text = str(raw_ts or "").strip()
    if not ts_text:
        return 0.0
    ts_text = ts_text.replace(" UTC+8", "")
    try:
        return datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()
    except ValueError:
        return 0.0


def _iter_message_log_entries_between(start_ts, end_ts):
    try:
        start_day = datetime.fromtimestamp(float(start_ts), TZ_LOCAL).date()
        end_day = datetime.fromtimestamp(float(end_ts), TZ_LOCAL).date()
    except (TypeError, ValueError, OSError):
        return
    day = start_day
    while day <= end_day:
        log_path = Path(MESSAGES_DIR) / f"{day.isoformat()}.log"
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
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
                return int(sender_abs[3:]) == send_as_id
            except ValueError:
                return False
    return False


def _has_yindao_send_evidence(send_as_id, msg_id, command, sent_at, now):
    try:
        msg_id = int(msg_id or 0)
    except (TypeError, ValueError):
        return False
    if msg_id <= 0:
        return False
    start_ts = max(0.0, float(sent_at or now) - TAIYI_SEND_EVIDENCE_LOOKBACK_SEC)
    end_ts = max(float(now or sent_at), float(sent_at or now)) + TAIYI_SEND_EVIDENCE_LOOKAHEAD_SEC
    expected_command = str(command or "").strip()
    for entry in _iter_message_log_entries_between(start_ts, end_ts):
        entry_ts = _parse_message_log_ts((entry or {}).get("ts"))
        if entry_ts <= 0 or entry_ts < start_ts or entry_ts > end_ts:
            continue
        if int((entry or {}).get("message_id") or 0) != msg_id:
            continue
        if str((entry or {}).get("text") or "").strip() != expected_command:
            continue
        if str((entry or {}).get("event_type") or "") != "sent":
            continue
        if _identity_sender_matches((entry or {}).get("sender_id"), send_as_id):
            return True
    return False


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


def _get_yindao_resend_count():
    try:
        return int(state.get("taiyi_yindao_resend_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _reset_yindao_resend_count():
    state["taiyi_yindao_resend_count"] = 0


def _mark_yindao_resend(reason):
    count = _get_yindao_resend_count() + 1
    state["taiyi_yindao_resend_count"] = count
    state["taiyi_last_error"] = f"引道补发 {count}/{TAIYI_YINDAO_RESEND_MAX_PER_CYCLE}: {reason}"
    return count


def _yindao_resend_exhausted():
    return _get_yindao_resend_count() >= TAIYI_YINDAO_RESEND_MAX_PER_CYCLE


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


async def _fallback_taiyi_pending_to_normal_cycle(now, elapsed, label, reason, *, record_failure=True):
    base_time = float(state.get("taiyi_phase_entered_at", 0) or now)
    _set_phase("idle", now)
    state["next_taiyi_cycle_time"] = _next_fixed_cycle(base_time)
    state["taiyi_pending_node_name"] = ""
    _reset_yindao_resend_count()
    _clear_chain_msg_ids()
    if record_failure:
        _record_failure(now, f"{label} reply {elapsed}s 未回")
    state["taiyi_last_error"] = f"{label} {reason}，按正常12h周期兜底"
    _record_taiyi_event(
        "reply超时兜底",
        kind="skipped",
        reason="taiyi_reply_timeout",
        phase="idle",
        detail=f"{label}｜{reason}｜elapsed={elapsed}s",
        decision="timeout_fallback_normal_cycle",
    )
    save_state()
    await send_audit_log(
        f"🧯 太一{label} {reason}（{elapsed}s），未补发，按正常12h周期兜底→{fmt_abs_ts(state['next_taiyi_cycle_time'])}",
        scope="identity",
    )
    if record_failure:
        await _check_failure_breaker(now)


async def _retry_taiyi_yindao_presend_boundary(
    now,
    elapsed,
    reason,
    *,
    msg_id=0,
    delay_min_sec=TAIYI_PRESEND_RETRY_MIN_SEC,
    delay_max_sec=TAIYI_PRESEND_RETRY_MAX_SEC,
    action_label="重试",
    decision="presend_boundary_short_retry",
):
    try:
        msg_id = int(msg_id or 0)
    except (TypeError, ValueError):
        msg_id = 0
    delay_sec = random.uniform(delay_min_sec, delay_max_sec)
    _mark_yindao_resend(reason)
    _set_phase("idle", now)
    state["next_taiyi_cycle_time"] = now + delay_sec
    state["taiyi_pending_node_name"] = ""
    _clear_chain_msg_ids()
    state["taiyi_last_error"] = f"引道{action_label}：{reason}，已安排短延迟{action_label}"
    _record_taiyi_event(
        f"引道{action_label}",
        kind="skipped",
        family="taiyi_yindao",
        phase="idle",
        msg_id=msg_id,
        detail=reason,
        decision=decision,
    )
    save_state()
    await send_audit_log(
        f"🧯 太一引道 {reason}（{elapsed}s，msg_id={msg_id}），已安排 {delay_sec:.1f}s 后{action_label}→{fmt_abs_ts(state['next_taiyi_cycle_time'])}",
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
    if changed:
        _record_taiyi_event(
            "天尊静默暂停补发",
            kind="skipped",
            reason="taiyi_bot_silent",
            phase=stale_phase,
            detail=f"{label}｜elapsed={elapsed}s",
        )
    save_state()
    if changed:
        await send_audit_log(
            f"🩺 太一{label} reply {elapsed}s 未回，且期间无天尊发言；暂停短补发，交给天尊健康恢复。",
            scope="identity",
        )
    return True


def _finish_yindao_without_search(now):
    _set_phase("idle", now)
    _reset_yindao_resend_count()
    _clear_chain_msg_ids()
    save_state()


async def _apply_yindao_success(now, *, sent_at=None, late=False, matched_text="", reply_msg_id=0):
    _reset_failures()
    _reset_search_resend_count()
    _reset_yindao_resend_count()
    base_time = sent_at if sent_at is not None else now
    state["next_taiyi_cycle_time"] = _next_fixed_cycle(base_time)
    if late:
        _set_phase("idle", now)
        _clear_chain_msg_ids()
        _record_taiyi_event(
            "引道手动/迟到成功",
            family="taiyi_yindao",
            phase="idle",
            reply_msg_id=reply_msg_id,
            detail=f"下次={fmt_abs_ts(state['next_taiyi_cycle_time'])}",
            matched_text=matched_text,
            decision="calibrate_manual_late_no_search",
        )
        save_state()
        await send_audit_log(f"🌟 太一引道成功（手动/迟到校准），下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return
    if state.get("taiyi_node_search_enabled", False):
        _set_phase("search_scheduled", now)
        _reset_yindao_resend_count()
        state["taiyi_yindao_msg_id"] = 0
        _record_taiyi_event(
            "引道成功已安排搜寻",
            family="taiyi_yindao",
            phase="search_scheduled",
            reply_msg_id=reply_msg_id,
            detail=f"下次={fmt_abs_ts(state['next_taiyi_cycle_time'])}",
            matched_text=matched_text,
            decision="yindao_success_schedule_search",
        )
        save_state()
        await send_audit_log(
            f"🌟 太一引道确认成功，{TAIYI_LINKED_SEARCH_DELAY_MIN_SEC}-{TAIYI_LINKED_SEARCH_DELAY_MAX_SEC}s 后联动搜寻｜下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}"
        )
        _fire_and_forget(_send_linked_search_after_success())
    else:
        _set_phase("idle", now)
        _clear_chain_msg_ids()
        _record_taiyi_event(
            "引道成功",
            family="taiyi_yindao",
            phase="idle",
            reply_msg_id=reply_msg_id,
            detail=f"下次={fmt_abs_ts(state['next_taiyi_cycle_time'])}",
            matched_text=matched_text,
            decision="yindao_success_cycle_only",
        )
        save_state()
        await send_audit_log(f"🌟 太一引道成功（+100 神识），下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")


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
    yindao_resend_count = _get_yindao_resend_count()
    if yindao_resend_count > 0:
        lines.append(f"- 本轮引道补发: {yindao_resend_count}/{TAIYI_YINDAO_RESEND_MAX_PER_CYCLE}")
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

    # phase guard：迟到/手动成功回复仍可校准 12h 节拍，但不再推进搜寻链路。
    if _phase() != "yindao_pending":
        if _is_yindao_success_text(text):
            console_log(f"⚠️ 引道 reply 迟到/手动成功（phase={_phase()}），校准太一周期。")
            await _apply_yindao_success(
                now,
                sent_at=now,
                late=True,
                matched_text=text,
                reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
            )
        else:
            console_log(f"⚠️ 引道 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
            _record_taiyi_event(
                "忽略迟到引道回执",
                kind="skipped",
                reason="taiyi_late_reply",
                family="taiyi_yindao",
                reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
                detail=f"reply_phase={_phase()}",
                matched_text=text,
                decision="late_reply_ignored",
            )
        return True
    if not _is_current_reply(reply_to, "taiyi_yindao_msg_id"):
        if _is_yindao_success_text(text):
            console_log("⚠️ 太一引道回复 msg_id 不匹配但确认成功，校准太一周期。")
            _record_taiyi_event(
                "引道回执msg_id不匹配但成功",
                family="taiyi_yindao",
                reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
                detail=f"reply_to_msg_id={int(getattr(reply_to, 'id', 0) or 0)}",
                matched_text=text,
                decision="mismatch_success_calibrate_no_search",
            )
            await _apply_yindao_success(
                now,
                sent_at=now,
                late=True,
                matched_text=text,
                reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
            )
        else:
            console_log("⚠️ 忽略迟到的太一引道回复。")
            _record_taiyi_event(
                "忽略迟到引道回执",
                kind="skipped",
                reason="taiyi_msg_id_mismatch",
                family="taiyi_yindao",
                reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
                detail=f"reply_to_msg_id={int(getattr(reply_to, 'id', 0) or 0)}",
                matched_text=text,
                decision="msg_id_mismatch_ignored",
            )
        return True

    # 成功
    if _is_yindao_success_text(text):
        sent_at = _current_yindao_sent_at(now)
        await _apply_yindao_success(
            now,
            sent_at=sent_at,
            matched_text=text,
            reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
        )
        return True

    # CD：大道感悟需循序渐进
    if RE_YINDAO_CD.search(text):
        _reset_search_resend_count()
        _reset_yindao_resend_count()
        wait_sec = parse_wait_time(text)
        if has_wait_time(text) and wait_sec > 0:
            state["next_taiyi_cycle_time"] = now + wait_sec + CD_BUFFER_SEC
        else:
            # 解析失败：保守回退 12h
            state["next_taiyi_cycle_time"] = now + TAIYI_CYCLE_CD_SEC
        _set_phase("idle", now)
        _clear_chain_msg_ids()
        _record_taiyi_event(
            "引道CD",
            family="taiyi_yindao",
            phase="idle",
            reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
            detail=f"下次={fmt_abs_ts(state['next_taiyi_cycle_time'])}",
            matched_text=text,
            decision="yindao_cd_scheduled",
        )
        save_state()
        await send_audit_log(f"⏳ 太一引道 CD→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 非弟子
    if "你并非太一门弟子" in text:
        _reset_yindao_resend_count()
        _freeze(now, "非太一门弟子")
        _clear_chain_msg_ids()
        _record_taiyi_event(
            "引道冻结",
            family="taiyi_yindao",
            phase="frozen",
            reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
            detail="非太一门弟子",
            matched_text=text,
            decision="freeze_non_taiyi_member",
        )
        save_state()
        await send_audit_log("ℹ️ 你并非太一门弟子，太一模块已冻结 7 天。")
        return True

    # 未识别的回复：记录为失败但不改 phase（让 phase 超时兜底处理）
    state["taiyi_last_error"] = f"未识别的引道回复: {text[:60]}"
    _record_taiyi_event(
        "引道回复未识别",
        kind="skipped",
        reason="taiyi_unrecognized_yindao_reply",
        family="taiyi_yindao",
        reply_msg_id=int(getattr(reply_to, "id", 0) or 0),
        detail=text[:60],
        matched_text=text,
        decision="unrecognized_yindao_reply",
    )
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
        _record_taiyi_event(
            "忽略迟到搜寻回执",
            kind="skipped",
            reason="taiyi_late_reply",
            detail=f"reply_phase={_phase()}",
        )
        if _is_node_search_disaster(text):
            await _close_node_search_disaster(text, now, source="taiyi_node_search_late")
        return True
    if not _is_current_reply(reply_to, "taiyi_node_search_msg_id"):
        console_log("⚠️ 忽略迟到的太一搜寻节点回复。")
        _record_taiyi_event(
            "忽略迟到搜寻回执",
            kind="skipped",
            reason="taiyi_msg_id_mismatch",
            detail=f"reply_to_msg_id={int(getattr(reply_to, 'id', 0) or 0)}",
        )
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
            command = f"{CMD_NODE_DEFINE} {node_name}"
            send_block = _classify_taiyi_none_send(command)
            if str(send_block.get("status") or "") == "unknown":
                _mark_taiyi_unknown_send(command, "define_pending", "taiyi_node_define_msg_id", sent_at, "定星")
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
        _record_taiyi_event(
            "忽略迟到定星回执",
            kind="skipped",
            reason="taiyi_late_reply",
            detail=f"reply_phase={_phase()}",
        )
        return True
    if not _is_current_reply(reply_to, "taiyi_node_define_msg_id"):
        console_log("⚠️ 忽略迟到的太一定星回复。")
        _record_taiyi_event(
            "忽略迟到定星回执",
            kind="skipped",
            reason="taiyi_msg_id_mismatch",
            detail=f"reply_to_msg_id={int(getattr(reply_to, 'id', 0) or 0)}",
        )
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

    # phase 超时兜底：引道 reply 60s 没回时本轮最多短补发一次；仍无回复再按正常周期收口。
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
            yindao_msg_id = int(state.get("taiyi_yindao_msg_id", 0) or 0)
            has_send_evidence = _has_yindao_send_evidence(
                get_current_identity_id(),
                yindao_msg_id,
                _resolve_yindao_command(),
                entered_at,
                now,
            )
            if _yindao_resend_exhausted():
                _record_taiyi_event(
                    "引道补发上限收口",
                    kind="skipped",
                    reason="taiyi_yindao_resend_exhausted",
                    family="taiyi_yindao",
                    msg_id=yindao_msg_id,
                    phase=stale_phase,
                    detail=f"elapsed={elapsed}s｜resend_count={_get_yindao_resend_count()}",
                    decision="timeout_fallback_after_yindao_resend",
                )
                await _fallback_taiyi_pending_to_normal_cycle(now, elapsed, "引道", "reply 未回")
                return
            if yindao_msg_id <= 0 or not has_send_evidence:
                await _retry_taiyi_yindao_presend_boundary(now, elapsed, "reply 未回且本地无真实出站记录", msg_id=yindao_msg_id)
                return
            _record_taiyi_event(
                "引道出站证据确认后补发",
                kind="skipped",
                reason="taiyi_send_evidence_present",
                family="taiyi_yindao",
                msg_id=yindao_msg_id,
                phase=stale_phase,
                detail=f"elapsed={elapsed}s｜event_type=sent",
                decision="send_evidence_present_fast_resend",
            )
            await _retry_taiyi_yindao_presend_boundary(
                now,
                elapsed,
                "reply 未回且已确认真实出站",
                msg_id=yindao_msg_id,
                delay_min_sec=TAIYI_YINDAO_RESEND_MIN_SEC,
                delay_max_sec=TAIYI_YINDAO_RESEND_MAX_SEC,
                action_label="补发",
                decision="send_evidence_present_fast_resend",
            )
            return

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
            await _fallback_taiyi_pending_to_normal_cycle(now, elapsed, "搜寻节点", "reply 未回")
            return

        elif stale_phase == "define_pending":
            node_name = state.get("taiyi_pending_node_name", "")
            if not node_name:
                await _fallback_taiyi_pending_to_normal_cycle(now, elapsed, "定星", "reply 未回且节点名丢失")
                return
            await _fallback_taiyi_pending_to_normal_cycle(now, elapsed, f"定星【{node_name}】", "reply 未回")
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
    state["taiyi_yindao_msg_id"] = 0
    state["taiyi_node_search_msg_id"] = 0
    state["taiyi_node_define_msg_id"] = 0
    msg = await send_game_command(cmd, track=False)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        send_block = _classify_taiyi_none_send(cmd)
        if str(send_block.get("status") or "") == "unknown":
            _mark_taiyi_unknown_send(cmd, "yindao_pending", "taiyi_yindao_msg_id", sent_at, "引道")
            return
        # 发送失败：回退 idle 短退避
        _set_phase("idle", sent_at)
        state["next_taiyi_cycle_time"] = sent_at + 60  # 1min 后重试
        _clear_chain_msg_ids()
        _record_taiyi_event("引道发送失败", command=cmd, phase="idle")
        _record_failure(sent_at, "引道发送失败")
        save_state()
        await send_audit_log("❌ 太一引道发送失败，1min 后重试。")
        await _check_failure_breaker(sent_at)
    else:
        yindao_msg_id = int(getattr(msg, "id", 0) or 0)
        # 不预写 next_taiyi_cycle_time；漏 reply 时由 phase 超时按正常 12h 周期兜底。
        state["taiyi_yindao_msg_id"] = yindao_msg_id
        state["taiyi_node_search_msg_id"] = 0
        state["taiyi_node_define_msg_id"] = 0
        state["taiyi_last_error"] = ""
        _set_phase("yindao_pending", sent_at)
        _record_taiyi_event(
            "引道已发送",
            command=cmd,
            msg_id=yindao_msg_id,
            phase="yindao_pending",
        )
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
