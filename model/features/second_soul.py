"""第二元神自动修炼模块。

设计参考：
- tree.py 的扁平事件驱动 + scheduler 模式（不用 phaseful，因为没有"出窍-归窍-总结"流程）
- yuanying.py 的 broadcast 身份匹配（match_yuanying_summary_identity）

phase 状态机:
    idle              - 默认/窍中温养，next_second_soul_time 到点会查询状态
    status_pending    - 已发 .第二元神，等回复
    ready_to_train    - 已确认窍中温养，等待 scheduler 安全发送 .元神修炼
    train_pending     - 已发 .元神修炼，等回复
    cultivating       - 修炼中，next_second_soul_time = 修炼结束时间 + buffer
    heart_demon_pending - 心魔试炼，已自动稳固道心，等结算 edit/broadcast
    injured           - 受伤，next_second_soul_time = 恢复时间 + buffer
    not_unlocked      - 尚未凝练第二元神，长冻结 7 天后重试

设计原则:
1. 未知状态先发 .第二元神 查状态；明确归位/窍中温养后，才进入修炼发送队列
2. heart_demon_pending 默认自动 .抉择 稳固道心；结算成功后进入安全修炼队列
3. heart_demon_pending 有 deadline 兜底（防 broadcast 漏接死锁）
4. 不使用 fire-and-forget 延迟调度（避开 tree 模块的 guard 漏洞）
"""

import random
import re
import time

from ..config import (
    CD_BUFFER_SEC,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_SECOND_SOUL_DEMON_STATUS,
    CMD_SECOND_SOUL_PURGE,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
    RE_WHITESPACE,
    SECOND_SOUL_HEART_DEMON_DEADLINE_SEC,
    SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC,
    SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC,
    SECOND_SOUL_PENDING_TIMEOUT_MAX,
    SECOND_SOUL_PENDING_TIMEOUT_MIN,
    SECOND_SOUL_RECHECK_MAX,
    SECOND_SOUL_RECHECK_MIN,
    SECOND_SOUL_TRAIN_CD_SEC,
)
from ..identity_levels import parse_second_soul_level_text, update_identity_level_record
from ..persistence import save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, mono, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_display_name, get_identity_ids, get_send_as_tags, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


# 真实文本特征（来自历史日志样本扫描）：
RE_SECOND_SOUL_PANEL_HEAD = re.compile(r"【你的第二元神[：:]")
RE_SECOND_SOUL_STATUS_LINE = re.compile(r"状态[：:]\s*([^\n)]+?)(?:\s*[(（]剩余[：:]\s*([^)）\n]+)[)）])?\s*(?:\n|$)")
RE_SECOND_SOUL_MORAN = re.compile(r"魔染(?:度)?\s*[:：]?\s*(\d+)(?:\s*(?:→|->|=>)\s*(\d+))?")
RE_AT_USERNAME = re.compile(r"@([A-Za-z0-9_]+)")
SECOND_SOUL_PURGE_THRESHOLD = 90
SECOND_SOUL_PURGE_REPLY_TIMEOUT_SEC = 120
SECOND_SOUL_PURGE_MAX_ATTEMPTS = 2
SECOND_SOUL_CHOICE_STRATEGIES = {
    "stable": ("稳固道心", CMD_SECOND_SOUL_CHOICE_STABLE),
    "break": ("强行突破", CMD_SECOND_SOUL_CHOICE_BREAK),
}


def _phase():
    return state.get("second_soul_phase", "idle")


def _set_phase(new_phase):
    if state.get("second_soul_phase") != new_phase:
        state["second_soul_phase"] = new_phase


def _clear_heart_demon():
    state["second_soul_heart_demon_msg_id"] = 0
    state["second_soul_heart_demon_deadline"] = 0.0
    state["second_soul_heart_demon_notified"] = False


def _choice_strategy():
    strategy = str(state.get("second_soul_choice_strategy") or "stable").strip().lower()
    return strategy if strategy in SECOND_SOUL_CHOICE_STRATEGIES else "stable"


def _choice_command():
    return SECOND_SOUL_CHOICE_STRATEGIES[_choice_strategy()][1]


def _choice_label():
    return SECOND_SOUL_CHOICE_STRATEGIES[_choice_strategy()][0]


def _next_pending_timeout(now):
    return now + random.uniform(SECOND_SOUL_PENDING_TIMEOUT_MIN, SECOND_SOUL_PENDING_TIMEOUT_MAX)


def _is_current_reply(reply_to, state_key):
    expected_msg_id = int(state.get(state_key, 0) or 0)
    reply_to_msg_id = int(getattr(reply_to, "id", 0) or 0)
    if expected_msg_id <= 0 or reply_to_msg_id <= 0:
        return True
    return reply_to_msg_id == expected_msg_id


def _clear_pending_msg_ids():
    state["second_soul_status_msg_id"] = 0
    state["second_soul_train_msg_id"] = 0


def _clear_purge_msg_ids():
    state["second_soul_purge_msg_id"] = 0
    state["second_soul_purge_status_msg_id"] = 0
    state["second_soul_purge_due_at"] = 0


def _reset_purge_state(*, keep_moran=False):
    if not keep_moran:
        state["second_soul_moran_value"] = 0
    state["second_soul_purge_msg_id"] = 0
    state["second_soul_purge_status_msg_id"] = 0
    state["second_soul_purge_attempts"] = 0
    state["second_soul_purge_due_at"] = 0
    state["second_soul_purge_last_at"] = 0


def _mark_ready_to_train(now):
    """已确认可修炼。这里只改状态，不直接发命令，实际发送交给 scheduler + 全局锁。"""
    _set_phase("ready_to_train")
    state["next_second_soul_time"] = now
    state["second_soul_last_error"] = ""
    _clear_heart_demon()
    _clear_pending_msg_ids()


def _parse_moran_value(text):
    value = None
    for match in RE_SECOND_SOUL_MORAN.finditer(text or ""):
        raw_value = match.group(2) or match.group(1)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
    return value


def _remember_moran_from_text(text):
    moran = _parse_moran_value(text)
    if moran is not None:
        state["second_soul_moran_value"] = int(moran)
    return moran


def _purge_due_at(now):
    return float(now) + SECOND_SOUL_PURGE_REPLY_TIMEOUT_SEC


def _finish_purge_ready(now, *, moran=None, last_error=""):
    if moran is not None:
        state["second_soul_moran_value"] = int(moran)
    _reset_purge_state(keep_moran=True)
    _mark_ready_to_train(now)
    if last_error:
        state["second_soul_last_error"] = last_error


async def _send_second_soul_purge(send_as_id, now, *, reason=""):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return False
    with use_identity(send_as_id):
        attempts = int(state.get("second_soul_purge_attempts", 0) or 0)
        if attempts >= SECOND_SOUL_PURGE_MAX_ATTEMPTS:
            _finish_purge_ready(now, last_error="元神镇魔已达自动上限，等待人工确认")
            save_state()
            await send_audit_log(
                "⚠️ 第二元神魔染仍高，但元神镇魔自动次数已达上限，已停止补发。",
                scope="identity", send_as_id=send_as_id, limit=240,
            )
            return False
        attempts += 1
        state["second_soul_purge_attempts"] = attempts
        state["second_soul_purge_msg_id"] = 0
        state["second_soul_purge_status_msg_id"] = 0
        state["second_soul_purge_due_at"] = _purge_due_at(now)
        state["second_soul_last_error"] = ""
        _set_phase("purge_pending")
        save_state()
    msg = await send_game_command(
        CMD_SECOND_SOUL_PURGE,
        track=False,
        send_as_id=send_as_id,
        priority="chain",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    with use_identity(send_as_id):
        state["second_soul_purge_last_at"] = sent_at if msg else 0
        state["second_soul_purge_due_at"] = _purge_due_at(sent_at)
        if msg:
            state["second_soul_purge_msg_id"] = int(getattr(msg, "id", 0) or 0)
            state["second_soul_last_error"] = ""
            save_state()
            moran = int(state.get("second_soul_moran_value", 0) or 0)
            await send_audit_log(
                f"🌀 第二元神魔染 {moran}，已发送第 {attempts}/{SECOND_SOUL_PURGE_MAX_ATTEMPTS} 次元神镇魔。{reason}",
                scope="identity", send_as_id=send_as_id, limit=240,
            )
            return True
        state["second_soul_last_error"] = "发送 .元神镇魔 失败，稍后查魔染"
        save_state()
        await send_audit_log(
            "⚠️ 第二元神元神镇魔发送失败，稍后将查五子同心魔确认魔染。",
            scope="identity", send_as_id=send_as_id, limit=240,
        )
    return False


async def _send_second_soul_demon_status(send_as_id, now):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return False
    with use_identity(send_as_id):
        state["second_soul_purge_status_msg_id"] = 0
        state["second_soul_purge_due_at"] = _purge_due_at(now)
        _set_phase("purge_status_pending")
        save_state()
    msg = await send_game_command(
        CMD_SECOND_SOUL_DEMON_STATUS,
        track=False,
        send_as_id=send_as_id,
        priority="chain",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    with use_identity(send_as_id):
        state["second_soul_purge_due_at"] = _purge_due_at(sent_at)
        if msg:
            state["second_soul_purge_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
            state["second_soul_last_error"] = ""
            save_state()
            console_log("🌀 已发 .五子同心魔 查第二元神魔染。")
            return True
        state["second_soul_last_error"] = "发送 .五子同心魔 失败，停止自动镇魔"
        _finish_purge_ready(sent_at, last_error=state["second_soul_last_error"])
        save_state()
        await send_audit_log(
            "⚠️ 第二元神查魔染发送失败，已停止本轮自动镇魔并恢复修炼队列。",
            scope="identity", send_as_id=send_as_id, limit=240,
        )
    return False


def _broadcast_key(kind, text):
    compact = RE_WHITESPACE.sub("", text or "")
    return f"{kind}:{compact[:400]}"


def _is_recent_duplicate_broadcast(kind, text, now, window_sec=6 * 3600):
    key = _broadcast_key(kind, text)
    last_key = state.get("second_soul_last_broadcast_key", "")
    last_at = float(state.get("second_soul_last_broadcast_at", 0) or 0)
    return key == last_key and last_at > 0 and now - last_at < window_sec


def _remember_broadcast(kind, text, now):
    state["second_soul_last_broadcast_key"] = _broadcast_key(kind, text)
    state["second_soul_last_broadcast_at"] = now


def _recently_confirmed_training(now):
    started_at = float(state.get("second_soul_last_train_started_at", 0) or 0)
    if started_at <= 0:
        return False
    if now < started_at:
        return False
    if now - started_at > SECOND_SOUL_RECHECK_MAX:
        return False
    return state.get("next_second_soul_time", 0) > now + SECOND_SOUL_RECHECK_MAX


def get_second_soul_status_text():
    lines = ["🌀 第二元神"]
    if not state.get("second_soul_enabled", False):
        lines.append("- 未启用")
        return "\n".join(lines)

    phase = _phase()
    next_time = state.get("next_second_soul_time", 0)

    if phase == "idle":
        lines.append("- 当前：闲置（窍中温养）")
        if next_time > 0:
            lines.append(f"- 下次检查：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "status_pending":
        lines.append("- 当前：状态查询中…")
        if next_time > 0:
            lines.append(f"- 超时自愈：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "ready_to_train":
        lines.append("- 当前：已归位，等待修炼入队")
        if next_time > 0:
            lines.append(f"- 入队时间：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "train_pending":
        lines.append("- 当前：修炼指令已发送，等待确认…")
        if next_time > 0:
            lines.append(f"- 超时自愈：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "cultivating":
        lines.append("- 当前：修炼中")
        if "短复查" in str(state.get("second_soul_last_error") or ""):
            lines.append(f"- 下次复查：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
        else:
            lines.append(f"- 修炼结束：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "injured":
        lines.append("- 当前：受伤")
        lines.append(f"- 恢复后：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "heart_demon_pending":
        deadline = state.get("second_soul_heart_demon_deadline", 0)
        if state.get("second_soul_auto_choice_enabled", True):
            lines.append(f"- ⚠️ 心魔试炼中，自动抉择：{_choice_label()}")
        else:
            lines.append("- ⚠️ 心魔试炼中，自动抉择关闭")
        if deadline > 0:
            lines.append(f"- 抉择截止：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）")
        msg_id = state.get("second_soul_heart_demon_msg_id", 0)
        if msg_id:
            lines.append(f"- 警示消息：{msg_id}")
    elif phase == "purge_pending":
        lines.append("- 当前：魔染镇压中")
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > 0:
            lines.append(f"- 镇魔确认：{fmt_abs_ts(due_at)}（{fmt_remaining(due_at)}）")
        lines.append(f"- 镇魔次数：{int(state.get('second_soul_purge_attempts', 0) or 0)}/{SECOND_SOUL_PURGE_MAX_ATTEMPTS}")
    elif phase == "purge_status_pending":
        lines.append("- 当前：镇魔后查魔染")
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > 0:
            lines.append(f"- 查询确认：{fmt_abs_ts(due_at)}（{fmt_remaining(due_at)}）")
    elif phase == "not_unlocked":
        lines.append("- 未凝练第二元神")
        if next_time > 0:
            lines.append(f"- 下次重试：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")

    moran = int(state.get("second_soul_moran_value", 0) or 0)
    if moran:
        lines.append(f"- 魔染：{moran}")
    last_err = state.get("second_soul_last_error", "")
    if last_err:
        lines.append(f"- 最近异常：{last_err}")
    if phase != "heart_demon_pending":
        lines.append(f"- 心魔抉择：{'自动' if state.get('second_soul_auto_choice_enabled', True) else '手动'} / {_choice_label()}")
    return "\n".join(lines)


# ============== reply 路径 handlers ==============

def _is_second_soul_panel(text):
    return bool(RE_SECOND_SOUL_PANEL_HEAD.search(text or ""))


def _parse_status_field(text):
    """从面板文本里抠出 (status, remain_sec)。
    status 是简洁串：'窍中温养' / '修炼中' / '受伤' / '心魔试炼中'
    remain_sec 没有则 0。
    """
    m = RE_SECOND_SOUL_STATUS_LINE.search(text or "")
    if not m:
        return None, 0
    status = m.group(1).strip()
    remain_str = m.group(2)
    remain_sec = 0
    if remain_str and has_wait_time(remain_str):
        remain_sec = parse_wait_time(remain_str)
    return status, remain_sec


async def handle_second_soul_status_reply(text, now, reply_to, matched_family=None):
    """处理 .第二元神 命令的回复（面板）。
    也兼容 .元神修炼 收到面板（罕见但可能）。
    """
    if not _is_second_soul_panel(text):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family in ("second_soul_status", "second_soul_train")
        or CMD_SECOND_SOUL_STATUS in orig_cmd
        or CMD_SECOND_SOUL_TRAIN in orig_cmd
    )
    if not is_relevant:
        return False
    level_updated = False
    level_text = parse_second_soul_level_text(text)
    if level_text:
        level_updated = update_identity_level_record(
            get_current_identity_id(),
            "second_soul_level",
            level_text,
            now=now,
            source="second_soul_status",
        )
        if level_updated:
            save_state()
    if not state.get("second_soul_enabled", False):
        return level_updated
    phase = _phase()
    if phase == "status_pending" and not _is_current_reply(reply_to, "second_soul_status_msg_id"):
        console_log("🌀 忽略迟到的第二元神状态回复。")
        return True

    status, remain_sec = _parse_status_field(text)
    if status is None:
        return level_updated

    state["second_soul_last_error"] = ""

    if status == "窍中温养":
        if phase == "train_pending":
            console_log("🌀 修炼确认等待中，忽略窍中温养状态面板，避免重复 .元神修炼。")
            return True
        if phase == "ready_to_train":
            console_log("🌀 第二元神已在修炼入队状态，忽略重复的窍中温养面板。")
            return True
        if phase == "cultivating" and _recently_confirmed_training(now):
            console_log("🌀 已确认进入 24h 修炼态，忽略过早的窍中温养面板，避免旧回复回滚状态。")
            return True
        _mark_ready_to_train(now)
        save_state()
        await send_audit_log("🌀 第二元神已确认窍中温养，修炼指令进入安全队列。")
        return True

    if status == "修炼中":
        _set_phase("cultivating")
        _clear_heart_demon()
        _clear_pending_msg_ids()
        if remain_sec > 0:
            state["next_second_soul_time"] = now + remain_sec + CD_BUFFER_SEC
        else:
            # 无剩余字段（比如刚开始或已快结束）：30-60min 后再查
            state["next_second_soul_time"] = now + random.uniform(SECOND_SOUL_RECHECK_MIN, SECOND_SOUL_RECHECK_MAX)
        save_state()
        await send_audit_log(f"🌀 第二元神修炼中，下次检查→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    if status == "受伤":
        _set_phase("injured")
        _clear_heart_demon()
        _clear_pending_msg_ids()
        if remain_sec > 0:
            state["next_second_soul_time"] = now + remain_sec + CD_BUFFER_SEC
        else:
            state["next_second_soul_time"] = now + SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC
        save_state()
        await send_audit_log(f"🤕 第二元神受伤，恢复后→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    if status == "心魔试炼中":
        # 通过面板得知心魔——可能 broadcast 警示我们漏了
        _set_phase("heart_demon_pending")
        if state.get("second_soul_heart_demon_deadline", 0) <= 0:
            # 我们漏了警示 broadcast，没法精确知道剩余时间，给一个保守 deadline
            state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
        _clear_pending_msg_ids()
        save_state()
        if not state.get("second_soul_heart_demon_notified", False):
            state["second_soul_heart_demon_notified"] = True
            save_state()
            await send_audit_log(
                "⚠️ 第二元神心魔试炼中（通过状态查询发现）！需人工 .抉择 强行突破/稳固道心。"
            )
        return True

    return False


async def handle_second_soul_purge_reply(text, now, reply_to, matched_family=None):
    if not state.get("second_soul_enabled", False):
        return False
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "second_soul_purge"
        or CMD_SECOND_SOUL_PURGE in orig_cmd
    )
    if not is_relevant:
        return False
    if _phase() == "purge_pending" and not _is_current_reply(reply_to, "second_soul_purge_msg_id"):
        console_log("🌀 忽略迟到的第二元神镇魔回复。")
        return True
    moran = _remember_moran_from_text(text)
    if moran is not None:
        if moran >= SECOND_SOUL_PURGE_THRESHOLD and int(state.get("second_soul_purge_attempts", 0) or 0) < SECOND_SOUL_PURGE_MAX_ATTEMPTS:
            send_as_id = get_current_identity_id()
            save_state()
            await send_audit_log(
                f"🌀 第二元神镇魔后魔染仍为 {moran}，补发一次元神镇魔。",
                scope="identity", send_as_id=send_as_id, limit=220,
            )
            await _send_second_soul_purge(send_as_id, now, reason=f"镇魔回复显示魔染 {moran}。")
            return True
        _finish_purge_ready(now, moran=moran)
        save_state()
        await send_audit_log(
            f"🌀 第二元神镇魔回复已收口，当前魔染 {moran}，修炼指令恢复队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=220,
        )
        return True
    if "元神镇魔" in text or "镇魔" in text:
        _finish_purge_ready(now)
        save_state()
        await send_audit_log(
            "🌀 第二元神镇魔回复已收到，修炼指令恢复队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=220,
        )
        return True
    return False


async def handle_second_soul_demon_status_reply(text, now, reply_to, matched_family=None):
    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "second_soul_demon_status"
        or CMD_SECOND_SOUL_DEMON_STATUS in orig_cmd
    )
    if not is_relevant:
        return False
    if not state.get("second_soul_enabled", False):
        return False
    if _phase() == "purge_status_pending" and not _is_current_reply(reply_to, "second_soul_purge_status_msg_id"):
        console_log("🌀 忽略迟到的第二元神魔染查询回复。")
        return True
    moran = _remember_moran_from_text(text)
    if moran is None:
        _finish_purge_ready(now, last_error="五子同心魔回复未解析到魔染")
        save_state()
        return True
    if moran >= SECOND_SOUL_PURGE_THRESHOLD and int(state.get("second_soul_purge_attempts", 0) or 0) < SECOND_SOUL_PURGE_MAX_ATTEMPTS:
        send_as_id = get_current_identity_id()
        save_state()
        await _send_second_soul_purge(
            send_as_id,
            now,
            reason=f"五子同心魔确认魔染 {moran}。",
        )
        return True
    _finish_purge_ready(now, moran=moran)
    save_state()
    if moran >= SECOND_SOUL_PURGE_THRESHOLD:
        await send_audit_log(
            f"⚠️ 第二元神魔染仍为 {moran}，但自动镇魔已达上限，停止补发并恢复修炼队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=260,
        )
    else:
        await send_audit_log(
            f"🌀 第二元神魔染已低于阈值（{moran}），修炼指令恢复队列。",
            scope="identity", send_as_id=get_current_identity_id(), limit=220,
        )
    return True


async def handle_second_soul_train_reply(text, now, reply_to, matched_family=None):
    """处理 .元神修炼 命令的回复。"""
    if not state.get("second_soul_enabled", False):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "second_soul_train"
        or CMD_SECOND_SOUL_TRAIN in orig_cmd
    )
    if not is_relevant:
        return False
    phase = _phase()
    if phase == "train_pending" and not _is_current_reply(reply_to, "second_soul_train_msg_id"):
        console_log("🌀 忽略迟到的第二元神修炼回复。")
        return True

    # 修炼成功
    if "你的第二元神已开始闭关修炼" in text and "24小时" in text:
        _set_phase("cultivating")
        state["next_second_soul_time"] = now + SECOND_SOUL_TRAIN_CD_SEC + CD_BUFFER_SEC
        state["second_soul_last_train_started_at"] = now
        state["second_soul_last_error"] = ""
        _clear_heart_demon()
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log(f"🌀 第二元神已修炼→{fmt_abs_ts(state['next_second_soul_time'])}")
        return True

    # 各种"无法分心修炼"
    if "无法分心修炼" in text:
        if "(修炼中)" in text or "（修炼中）" in text:
            _set_phase("cultivating")
            state["next_second_soul_time"] = now + random.uniform(
                SECOND_SOUL_RECHECK_MIN, SECOND_SOUL_RECHECK_MAX
            )
            state["second_soul_last_error"] = "修炼中未返回剩余时间，短复查校准"
            _clear_pending_msg_ids()
            save_state()
            console_log("🌀 第二元神已在修炼中，稍后复查。")
            return True
        if "(受伤)" in text or "（受伤）" in text:
            _set_phase("injured")
            state["next_second_soul_time"] = now + SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC
            _clear_pending_msg_ids()
            save_state()
            console_log("🤕 第二元神受伤中，6h 后复查。")
            return True
        if "(心魔试炼中)" in text or "（心魔试炼中）" in text:
            _set_phase("heart_demon_pending")
            if state.get("second_soul_heart_demon_deadline", 0) <= 0:
                state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
            _clear_pending_msg_ids()
            save_state()
            if not state.get("second_soul_heart_demon_notified", False):
                state["second_soul_heart_demon_notified"] = True
                save_state()
                await send_audit_log("⚠️ 第二元神心魔试炼中（通过修炼指令发现）！需人工抉择。")
            return True

    # 尚未凝练
    if "尚未凝练第二元神" in text:
        _set_phase("not_unlocked")
        state["next_second_soul_time"] = now + SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC
        state["second_soul_last_error"] = "尚未凝练第二元神"
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log("ℹ️ 尚未凝练第二元神，已进入冻结，7 天后重试。")
        return True

    return False


# ============== broadcast 路径 handlers ==============

def _match_identity_by_at_username(text):
    """从 broadcast 文本里提取 @username，匹配到 enabled 的 identity。
    返回 (target_id, matched_ids)。target_id 仅在唯一匹配时非 None。
    """
    compact = RE_WHITESPACE.sub("", text or "")
    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state.get("second_soul_enabled", False):
                continue
            tags = get_send_as_tags(identity_id) or []
            compact_tags = {RE_WHITESPACE.sub("", tag) for tag in tags if tag}
            if any(tag and tag in compact for tag in compact_tags):
                matched_ids.append(identity_id)
    target = matched_ids[0] if len(matched_ids) == 1 else None
    return target, matched_ids


def _match_identity_by_phase(target_phase):
    """通过本地 phase 唯一性匹配身份（用于无 @username 的 broadcast，比如心魔结算）。"""
    matched_ids = []
    for identity_id in get_identity_ids():
        with use_identity(identity_id):
            if not state.get("second_soul_enabled", False):
                continue
            if _phase() == target_phase:
                matched_ids.append(identity_id)
    target = matched_ids[0] if len(matched_ids) == 1 else None
    return target, matched_ids


async def handle_second_soul_heart_demon_warning_broadcast(text, now, event_msg_id):
    """处理【天道警示·心魔试炼】broadcast。含 @username。
    event_msg_id 是这条警示自己的 msg_id（用于以后回复 .抉择）。
    """
    if "【天道警示·心魔试炼】" not in text:
        return False
    if "第二元神" not in text:
        return False

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 心魔警示命中多个身份，已跳过自动标记：{names}",
                scope="global", limit=280,
            )
        return False

    should_send_choice = False
    with use_identity(target_id):
        if _phase() == "heart_demon_pending":
            # 已经标记过（可能广播重复），不重复通知
            return True
        _set_phase("heart_demon_pending")
        state["second_soul_heart_demon_msg_id"] = int(event_msg_id or 0)
        state["second_soul_heart_demon_deadline"] = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
        state["second_soul_heart_demon_notified"] = True
        _clear_pending_msg_ids()
        save_state()
        should_send_choice = int(event_msg_id or 0) > 0 and bool(state.get("second_soul_auto_choice_enabled", True))
        choice_command = _choice_command()
        choice_label = _choice_label()
        await send_audit_log(
            (
                f"🔥 第二元神心魔试炼来袭，自动回复警示消息 {event_msg_id}：\n  {choice_command}"
                if should_send_choice
                else f"🔥 第二元神心魔试炼来袭，自动抉择已关闭，请人工回复警示消息 {event_msg_id}。"
            ),
            scope="identity", send_as_id=target_id, limit=280,
        )
    if should_send_choice:
        msg = await send_game_command(
            choice_command,
            track=False,
            reply_to=int(event_msg_id or 0),
            send_as_id=target_id,
            priority="reactive",
        )
        with use_identity(target_id):
            if msg:
                state["second_soul_last_error"] = ""
                save_state()
                await send_audit_log(
                    f"🔥 第二元神已自动抉择{choice_label}，等待结算编辑消息 {event_msg_id}。",
                    scope="identity", send_as_id=target_id, limit=220,
                )
            else:
                state["second_soul_last_error"] = f"自动抉择{choice_label}发送失败，等待人工或 deadline 自检"
                save_state()
                await send_audit_log(
                    f"⚠️ 第二元神自动抉择{choice_label}发送失败，请人工回复警示消息 {event_msg_id}。",
                    scope="identity", send_as_id=target_id, limit=260,
                )
    return True


async def handle_second_soul_choice_result_broadcast(text, now):
    """处理心魔结算结果 broadcast。无 @username，靠 phase 唯一性匹配。
    覆盖：稳扎稳打·成功 / 破而后立·成功 / 破而后立·失败
    """
    is_stable_success = "【稳扎稳打·成功】" in text
    is_break_success = "【破而后立·成功】" in text
    is_break_fail = "【破而后立·失败】" in text
    if not (is_stable_success or is_break_success or is_break_fail):
        return False

    target_id, matched = _match_identity_by_phase("heart_demon_pending")
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 心魔结算 broadcast 命中多个 heart_demon_pending 身份，跳过自动归属：{names}",
                scope="global", limit=280,
            )
        return False

    with use_identity(target_id):
        if is_break_fail:
            _set_phase("injured")
            state["next_second_soul_time"] = now + SECOND_SOUL_TRAIN_CD_SEC + CD_BUFFER_SEC
            _clear_heart_demon()
            _clear_pending_msg_ids()
            save_state()
            await send_audit_log(
                f"💀 第二元神破而后立失败，受伤 24 小时→{fmt_abs_ts(state['next_second_soul_time'])}",
                scope="identity", send_as_id=target_id,
            )
        else:
            # 成功即已结算并可继续修炼，交给 scheduler 通过全局锁发送 .元神修炼。
            _mark_ready_to_train(now)
            save_state()
            label = "稳扎稳打·成功" if is_stable_success else "破而后立·成功"
            await send_audit_log(
                f"✨ 第二元神 {label}！本轮结算完成，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    return True


async def handle_second_soul_return_broadcast(text, now):
    """处理正常修炼结束的【第二元神归位】广播。
    这是确定可修炼状态，但不在广播 handler 里直接发命令。
    """
    if "【第二元神归位】" not in text:
        return False
    if "已结束修炼" not in text or "回归窍中温养" not in text:
        return False

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 第二元神归位 broadcast 命中多个身份，跳过：{names}",
                scope="global", limit=280,
            )
        return False

    should_purge = False
    moran = None
    with use_identity(target_id):
        if _is_recent_duplicate_broadcast("return", text, now):
            return True
        _remember_broadcast("return", text, now)
        moran = _remember_moran_from_text(text)
        phase = _phase()
        if moran is not None and moran >= SECOND_SOUL_PURGE_THRESHOLD:
            _mark_ready_to_train(now)
            _reset_purge_state(keep_moran=True)
            should_purge = True
            save_state()
        elif phase in ("purge_pending", "purge_status_pending"):
            save_state()
            return True
        if phase in ("ready_to_train", "train_pending"):
            save_state()
            if not should_purge:
                return True
        elif phase == "cultivating" and _recently_confirmed_training(now):
            save_state()
            console_log("🌀 忽略迟到的第二元神归位广播：当前已是新的 24h 修炼态。")
            return True
        elif not should_purge:
            _mark_ready_to_train(now)
            save_state()
        if should_purge:
            await send_audit_log(
                f"🌀 第二元神已归位但魔染 {moran}，先镇魔再恢复修炼队列。",
                scope="identity", send_as_id=target_id, limit=240,
            )
        else:
            await send_audit_log(
                "🌀 第二元神已归位，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    if should_purge:
        await _send_second_soul_purge(target_id, now, reason="归位广播触发。")
    return True


async def handle_second_soul_recovery_broadcast(text, now):
    """处理两种带 @username 的恢复广播：
    - "你的第二元神已从【受伤】状态中恢复"
    - "心魔幻境已消散，元神已自动归位（本次修炼无收益）"
    """
    is_injury_recovery = "你的第二元神已从【受伤】状态中恢复" in text
    is_heart_demon_dissolve = "心魔幻境已消散" in text and "元神已自动归位" in text
    if not (is_injury_recovery or is_heart_demon_dissolve):
        return False

    target_id, matched = _match_identity_by_at_username(text)
    if target_id is None:
        if len(matched) > 1:
            names = ", ".join(mono(get_identity_display_name(i)) for i in matched)
            await send_audit_log(
                f"⚠️ 第二元神恢复 broadcast 命中多个身份，跳过：{names}",
                scope="global", limit=280,
            )
        return False

    with use_identity(target_id):
        if _is_recent_duplicate_broadcast("recovery", text, now):
            return True
        _remember_broadcast("recovery", text, now)
        if _phase() in ("ready_to_train", "train_pending"):
            save_state()
            return True
        if _phase() == "cultivating" and _recently_confirmed_training(now):
            save_state()
            console_log("🌀 忽略迟到的第二元神恢复广播：当前已是新的 24h 修炼态。")
            return True
        _mark_ready_to_train(now)
        save_state()
        if is_heart_demon_dissolve:
            await send_audit_log(
                "🌫️ 第二元神心魔幻境消散（本次无收益），修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
        else:
            await send_audit_log(
                "🩹 第二元神受伤恢复，修炼指令进入安全队列。",
                scope="identity", send_as_id=target_id,
            )
    return True


# ============== scheduler ==============

async def run_second_soul_scheduler(now):
    if not state.get("second_soul_enabled", False):
        return

    phase = _phase()

    # not_unlocked 长冻结：仅到点重试
    if phase == "not_unlocked":
        if state.get("next_second_soul_time", 0) > now:
            return
        # 到点：清状态重新查询
        _set_phase("idle")
        save_state()
        # 走下面的查询路径

    # heart_demon_pending：deadline 兜底防死锁
    if phase == "heart_demon_pending":
        deadline = state.get("second_soul_heart_demon_deadline", 0)
        if deadline <= 0:
            deadline = now + SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
            state["second_soul_heart_demon_deadline"] = deadline
            state["second_soul_last_error"] = "心魔 deadline 缺失，已补齐"
            save_state()
        if deadline > 0 and now > deadline + 60:
            # deadline 过了 1 分钟还没收到结果 broadcast，强制清状态自检
            _set_phase("idle")
            state["next_second_soul_time"] = now
            _clear_heart_demon()
            save_state()
            await send_audit_log(
                "⏰ 第二元神心魔抉择 deadline 已过仍无结算广播，强制重查状态。"
            )
            phase = "idle"
        else:
            return  # 仍在抉择期，不发任何命令

    if phase == "purge_pending":
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > now:
            return
        attempts = int(state.get("second_soul_purge_attempts", 0) or 0)
        if attempts <= 1:
            await send_audit_log("🌀 第二元神镇魔未收到回复，补查五子同心魔确认魔染。")
            await _send_second_soul_demon_status(get_current_identity_id(), now)
            return
        _finish_purge_ready(now, last_error="第二次元神镇魔未收到回复，停止自动镇魔")
        save_state()
        await send_audit_log(
            "⚠️ 第二元神第二次元神镇魔仍无回复，已停止本轮补发并恢复修炼队列。",
            limit=260,
        )
        return

    if phase == "purge_status_pending":
        due_at = float(state.get("second_soul_purge_due_at", 0) or 0)
        if due_at > now:
            return
        _finish_purge_ready(now, last_error="五子同心魔查询无回复，停止自动镇魔")
        save_state()
        await send_audit_log(
            "⚠️ 第二元神五子同心魔查询无回复，已停止本轮自动镇魔并恢复修炼队列。",
            limit=260,
        )
        return

    if phase in ("status_pending", "train_pending"):
        # 第二元神不用通用 retry 补发，避免 bot 延迟时重复刷屏。
        # 到 30-60 分钟慢速自愈点后，清掉旧 pending，回到 idle 重新查一次；
        # 修炼等待超时也不直接补发 .元神修炼，避免重复修炼指令。
        if state.get("next_second_soul_time", 0) > now:
            return
        clear_pending_tasks_by_commands({CMD_SECOND_SOUL_STATUS, CMD_SECOND_SOUL_TRAIN})
        _set_phase("idle")
        state["next_second_soul_time"] = now
        if phase == "train_pending":
            state["second_soul_last_error"] = "修炼确认等待超时，已自愈重查"
        else:
            state["second_soul_last_error"] = "状态查询等待超时，已自愈重查"
        _clear_pending_msg_ids()
        save_state()
        if phase == "train_pending":
            await send_audit_log("🌀 第二元神修炼确认等待超时，已清理旧指令并重新查询状态。")
        else:
            await send_audit_log("🌀 第二元神状态等待超时，已清理旧指令并重新查询。")
        phase = "idle"

    if phase == "ready_to_train":
        if state.get("next_second_soul_time", 0) > now:
            return
        _set_phase("train_pending")
        state["next_second_soul_time"] = _next_pending_timeout(now)
        state["second_soul_status_msg_id"] = 0
        state["second_soul_train_msg_id"] = 0
        save_state()
        msg = await send_game_command(CMD_SECOND_SOUL_TRAIN, track=False, priority="chain")
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            _set_phase("ready_to_train")
            state["second_soul_last_error"] = "发送 .元神修炼 失败"
            state["next_second_soul_time"] = sent_at + 600
            state["second_soul_train_msg_id"] = 0
            save_state()
            await send_audit_log("❌ 第二元神修炼发送失败，10 分钟后重试。")
        else:
            _set_phase("train_pending")
            state["second_soul_train_msg_id"] = int(getattr(msg, "id", 0) or 0)
            state["next_second_soul_time"] = _next_pending_timeout(sent_at)
            save_state()
            console_log("🌀 已发 .元神修炼，等待确认。")
        return

    # 到点：发 .第二元神 查状态
    if state.get("next_second_soul_time", 0) > now:
        return

    _set_phase("status_pending")
    state["next_second_soul_time"] = _next_pending_timeout(now)
    state["second_soul_status_msg_id"] = 0
    state["second_soul_train_msg_id"] = 0
    save_state()
    msg = await send_game_command(CMD_SECOND_SOUL_STATUS, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        # 发送失败：回退到 idle，下个 scheduler tick 重试
        _set_phase("idle")
        state["next_second_soul_time"] = sent_at + 60  # 1min 后重试
        state["second_soul_last_error"] = "发送 .第二元神 失败"
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log("❌ 第二元神状态查询发送失败，1min 后重试。")
    else:
        _set_phase("status_pending")
        state["second_soul_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["next_second_soul_time"] = _next_pending_timeout(sent_at)
        save_state()
        console_log("🌀 已发 .第二元神 查状态。")


async def run_second_soul_bootstrap_check(now):
    """已弃用：残留清理移至 control._restore_second_soul_runtime（启动一次性）。
    保留空函数以维持 scheduler 注册兼容。
    """
    return


__all__ = [
    "get_second_soul_status_text",
    "handle_second_soul_choice_result_broadcast",
    "handle_second_soul_demon_status_reply",
    "handle_second_soul_heart_demon_warning_broadcast",
    "handle_second_soul_purge_reply",
    "handle_second_soul_recovery_broadcast",
    "handle_second_soul_return_broadcast",
    "handle_second_soul_status_reply",
    "handle_second_soul_train_reply",
    "run_second_soul_bootstrap_check",
    "run_second_soul_scheduler",
]
