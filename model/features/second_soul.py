"""第二元神自动修炼模块（草稿，未集成）。

设计参考：
- tree.py 的扁平事件驱动 + scheduler 模式（不用 phaseful，因为没有"出窍-归窍-总结"流程）
- yuanying.py 的 broadcast 身份匹配（match_yuanying_summary_identity）

phase 状态机:
    idle              - 默认/窍中温养，next_second_soul_time 到点会查询状态
    status_pending    - 已发 .第二元神，等回复
    cultivating       - 修炼中，next_second_soul_time = 修炼结束时间 + buffer
    heart_demon_pending - 心魔试炼，等用户手动抉择，scheduler 不发任何命令
    injured           - 受伤，next_second_soul_time = 恢复时间 + buffer
    not_unlocked      - 尚未凝练第二元神，长冻结 7 天后重试

设计原则:
1. 先发 .第二元神 查状态，不直接发 .元神修炼（避开 tree 模块"成熟期还硬灌"的坑）
2. heart_demon_pending 永不自动 .抉择（破而后立失败会受伤 24h，保守）
3. heart_demon_pending 有 deadline 兜底（防 broadcast 漏接死锁）
4. 不使用 fire-and-forget 延迟调度（避开 tree 模块的 guard 漏洞）
"""

import re

from ..config import (
    CD_BUFFER_SEC,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
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
from ..persistence import save_state
from ..runtime import clear_pending_tasks_by_commands, console_log, mono, send_audit_log, send_game_command
from ..state import get_identity_display_name, get_identity_ids, get_send_as_tags, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


# 真实文本特征（来自历史日志样本扫描）：
RE_SECOND_SOUL_PANEL_HEAD = re.compile(r"【你的第二元神[：:]")
RE_SECOND_SOUL_STATUS_LINE = re.compile(r"状态[：:]\s*([^\n)]+?)(?:\s*[(（]剩余[：:]\s*([^)）\n]+)[)）])?\s*(?:\n|$)")
RE_AT_USERNAME = re.compile(r"@([A-Za-z0-9_]+)")


def _phase():
    return state.get("second_soul_phase", "idle")


def _set_phase(new_phase):
    if state.get("second_soul_phase") != new_phase:
        state["second_soul_phase"] = new_phase


def _clear_heart_demon():
    state["second_soul_heart_demon_msg_id"] = 0
    state["second_soul_heart_demon_deadline"] = 0.0
    state["second_soul_heart_demon_notified"] = False


def _next_pending_timeout(now):
    import random
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
    elif phase == "cultivating":
        lines.append("- 当前：修炼中")
        lines.append(f"- 修炼结束：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "injured":
        lines.append("- 当前：受伤")
        lines.append(f"- 恢复后：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")
    elif phase == "heart_demon_pending":
        deadline = state.get("second_soul_heart_demon_deadline", 0)
        lines.append("- ⚠️ 心魔试炼中，需人工抉择！")
        if deadline > 0:
            lines.append(f"- 抉择截止：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）")
        msg_id = state.get("second_soul_heart_demon_msg_id", 0)
        if msg_id:
            lines.append(f"- 警示消息：{msg_id}（回复 .抉择 强行突破/稳固道心）")
    elif phase == "not_unlocked":
        lines.append("- 未凝练第二元神")
        if next_time > 0:
            lines.append(f"- 下次重试：{fmt_abs_ts(next_time)}（{fmt_remaining(next_time)}）")

    last_err = state.get("second_soul_last_error", "")
    if last_err:
        lines.append(f"- 最近异常：{last_err}")
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
    if not state.get("second_soul_enabled", False):
        return False

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
    if _phase() != "status_pending":
        console_log(f"🌀 忽略非等待期的第二元神状态回复（phase={_phase()}）。")
        return True
    if not _is_current_reply(reply_to, "second_soul_status_msg_id"):
        console_log("🌀 忽略迟到的第二元神状态回复。")
        return True

    status, remain_sec = _parse_status_field(text)
    if status is None:
        return False

    state["second_soul_last_error"] = ""

    if status == "窍中温养":
        # 闲置且可修炼。立即发 .元神修炼
        # 注意：必须把 phase 转到一个 scheduler 不会再发 .第二元神 的状态
        # 否则 scheduler 看 phase=idle + next_time<=now 会立即又发一次 .第二元神
        # 用 status_pending 占位，等 train_reply 处理后再转 cultivating
        # next_time 推到 30-60 分钟兜底；若 bot 延迟，宁可慢查，不自动补发修炼指令。
        # train_reply 正常到达会立即覆盖到 24h
        _clear_heart_demon()
        state["next_second_soul_time"] = _next_pending_timeout(now)
        state["second_soul_status_msg_id"] = 0
        # 保持 phase = status_pending（之前查 status 时设的），不切回 idle
        save_state()
        msg = await send_game_command(CMD_SECOND_SOUL_TRAIN, track=False)
        if not msg:
            _set_phase("idle")
            state["second_soul_last_error"] = "发送 .元神修炼 失败"
            state["next_second_soul_time"] = now + 600
            state["second_soul_train_msg_id"] = 0
            save_state()
            await send_audit_log("❌ 第二元神修炼发送失败，10 分钟后重试。")
        else:
            state["second_soul_train_msg_id"] = int(getattr(msg, "id", 0) or 0)
            save_state()
        # train_reply 处理回复，phase 由那里设
        return True

    if status == "修炼中":
        _set_phase("cultivating")
        _clear_heart_demon()
        _clear_pending_msg_ids()
        if remain_sec > 0:
            state["next_second_soul_time"] = now + remain_sec + CD_BUFFER_SEC
        else:
            # 无剩余字段（比如刚开始或已快结束）：30-60min 后再查
            import random
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
    if _phase() != "status_pending":
        console_log(f"🌀 忽略非等待期的第二元神修炼回复（phase={_phase()}）。")
        return True
    if not _is_current_reply(reply_to, "second_soul_train_msg_id"):
        console_log("🌀 忽略迟到的第二元神修炼回复。")
        return True

    # 修炼成功
    if "你的第二元神已开始闭关修炼" in text and "24小时" in text:
        _set_phase("cultivating")
        state["next_second_soul_time"] = now + SECOND_SOUL_TRAIN_CD_SEC + CD_BUFFER_SEC
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
            import random
            state["next_second_soul_time"] = now + random.uniform(
                SECOND_SOUL_RECHECK_MIN, SECOND_SOUL_RECHECK_MAX
            )
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
        await send_audit_log(
            f"🔥 第二元神心魔试炼来袭！1 小时内回复警示消息 {event_msg_id}：\n"
            f"  .抉择 强行突破  (高风险高回报，失败受伤 24h)\n"
            f"  .抉择 稳固道心  (低风险低回报，几乎稳过)",
            scope="identity", send_as_id=target_id, limit=280,
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
            # 成功：直接进 idle，next 设为 now，让 scheduler 下次 tick 主动查状态
            _set_phase("idle")
            state["next_second_soul_time"] = now
            _clear_heart_demon()
            _clear_pending_msg_ids()
            save_state()
            label = "稳扎稳打·成功" if is_stable_success else "破而后立·成功"
            await send_audit_log(
                f"✨ 第二元神 {label}！本轮结算完成，将自动续修炼。",
                scope="identity", send_as_id=target_id,
            )
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
        _set_phase("idle")
        state["next_second_soul_time"] = now
        _clear_heart_demon()
        _clear_pending_msg_ids()
        state["second_soul_last_error"] = ""
        save_state()
        if is_heart_demon_dissolve:
            await send_audit_log(
                "🌫️ 第二元神心魔幻境消散（本次无收益），将自动续修炼。",
                scope="identity", send_as_id=target_id,
            )
        else:
            await send_audit_log(
                "🩹 第二元神受伤恢复，将自动续修炼。",
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

    if phase == "status_pending":
        # 第二元神不用通用 retry 补发，避免 bot 延迟时重复刷屏。
        # 到 30-60 分钟慢速自愈点后，清掉旧 pending，回到 idle 重新查一次。
        if state.get("next_second_soul_time", 0) > now:
            return
        clear_pending_tasks_by_commands({CMD_SECOND_SOUL_STATUS, CMD_SECOND_SOUL_TRAIN})
        _set_phase("idle")
        state["next_second_soul_time"] = now
        state["second_soul_last_error"] = "状态查询等待超时，已自愈重查"
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log("🌀 第二元神状态等待超时，已清理旧指令并重新查询。")

    # 到点：发 .第二元神 查状态
    if state.get("next_second_soul_time", 0) > now:
        return

    _set_phase("status_pending")
    state["next_second_soul_time"] = _next_pending_timeout(now)
    state["second_soul_status_msg_id"] = 0
    state["second_soul_train_msg_id"] = 0
    save_state()
    msg = await send_game_command(CMD_SECOND_SOUL_STATUS, track=False)
    if not msg:
        # 发送失败：回退到 idle，下个 scheduler tick 重试
        _set_phase("idle")
        state["next_second_soul_time"] = now + 60  # 1min 后重试
        state["second_soul_last_error"] = "发送 .第二元神 失败"
        _clear_pending_msg_ids()
        save_state()
        await send_audit_log("❌ 第二元神状态查询发送失败，1min 后重试。")
    else:
        state["second_soul_status_msg_id"] = int(getattr(msg, "id", 0) or 0)
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
    "handle_second_soul_heart_demon_warning_broadcast",
    "handle_second_soul_recovery_broadcast",
    "handle_second_soul_status_reply",
    "handle_second_soul_train_reply",
    "run_second_soul_bootstrap_check",
    "run_second_soul_scheduler",
]
