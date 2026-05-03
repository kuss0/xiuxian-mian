"""太一门自动修炼模块（草稿，未集成）。

设计原则：
1. 单线性流水线：引道 → (成功才搜寻) → (找到才定星)
2. 不监听任何 broadcast：别人的命令完全不影响本号状态
3. 三个 reply handler 都做 phase guard，迟到 reply 不推进流程
4. 多号防风暴：next_cycle 加 ±30min jitter
5. 失败熔断：24h 内 5 次失败自动停子开关
6. bot 临时延迟兜底：phase 15min 超时 reset
7. bot 全局宕机：复用现有 _bot_silence 机制（外部）
8. 仅落云宗 -> 灵树 那种"按宗门匹配"的 UI（state.py 的 get_available_module_names 处理）

phase 状态机：
    idle               - 等下次 cycle
    yindao_pending     - 已发 .引道 X，等回复
    search_pending     - 已发 .搜寻节点，等回复
    define_pending     - 已发 .定星 <node>，等回复
    frozen             - 境界不足/非弟子，长冻结
"""

import asyncio
import random
import re

from ..config import (
    CD_BUFFER_SEC,
    CMD_NODE_DEFINE,
    CMD_NODE_SEARCH,
    CMD_YINDAO,
    TAIYI_CYCLE_CD_SEC,
    TAIYI_CYCLE_JITTER_SEC,
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
from ..runtime import _fire_and_forget, console_log, send_audit_log, send_game_command
from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining, fmt_time_after, has_wait_time, parse_wait_time


RE_YINDAO_SUCCESS = re.compile(r"你引动【([金木水火土])之道】")
RE_YINDAO_CD = re.compile(r"大道感悟需循序渐进")
RE_NODE_NAME = re.compile(r"获得：【(空间节点·[^】]+)】")
RE_DEFINE_SUCCESS = re.compile(r"【定星成功】")


def _phase():
    return state.get("taiyi_phase", "idle")


def _set_phase(new_phase, now=None):
    """切换 phase 并记录进入时间，用于超时兜底。"""
    import time
    if now is None:
        now = time.time()
    state["taiyi_phase"] = new_phase
    state["taiyi_phase_entered_at"] = now


def _next_cycle_with_jitter(now):
    """正常成功后的下次 cycle 时间：12h ± 30min。"""
    jitter = random.uniform(-TAIYI_CYCLE_JITTER_SEC, TAIYI_CYCLE_JITTER_SEC)
    return now + TAIYI_CYCLE_CD_SEC + jitter


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
    if not is_relevant:
        return False

    # phase guard：迟到 reply 仅清 pending 不推进流程
    if _phase() != "yindao_pending":
        console_log(f"⚠️ 引道 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
        return True

    # 成功
    if RE_YINDAO_SUCCESS.search(text) and "100点神识" in text:
        _reset_failures()
        if state.get("taiyi_node_search_enabled", False):
            # 链式：成功后立即搜寻节点
            _set_phase("search_pending", now)
            save_state()
            msg = await send_game_command(CMD_NODE_SEARCH)
            if not msg:
                # 搜寻发送失败，回到 idle 短退避
                _set_phase("idle", now)
                state["next_taiyi_cycle_time"] = now + TAIYI_RESOURCE_RETRY_SEC
                _record_failure(now, "搜寻节点发送失败")
                save_state()
                await send_audit_log("❌ 太一搜寻节点发送失败，1h 后重试。")
                await _check_failure_breaker(now)
            else:
                await send_audit_log(f"🌟 太一引道→100 神识，已发搜寻节点。")
        else:
            _set_phase("idle", now)
            state["next_taiyi_cycle_time"] = _next_cycle_with_jitter(now)
            save_state()
            await send_audit_log(f"🌟 太一引道成功（+100 神识），下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # CD：大道感悟需循序渐进
    if RE_YINDAO_CD.search(text):
        wait_sec = parse_wait_time(text)
        if has_wait_time(text) and wait_sec > 0:
            state["next_taiyi_cycle_time"] = now + wait_sec + CD_BUFFER_SEC
        else:
            # 解析失败：保守回退 12h
            state["next_taiyi_cycle_time"] = now + TAIYI_CYCLE_CD_SEC
        _set_phase("idle", now)
        save_state()
        await send_audit_log(f"⏳ 太一引道 CD→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 非弟子
    if "你并非太一门弟子" in text:
        _freeze(now, "非太一门弟子")
        save_state()
        await send_audit_log("ℹ️ 你并非太一门弟子，太一模块已冻结 7 天。")
        return True

    # 未识别的回复：记录为失败但不改 phase（让 phase 超时兜底处理）
    state["taiyi_last_error"] = f"未识别的引道回复: {text[:60]}"
    save_state()
    return False


async def handle_taiyi_node_search_reply(text, now, reply_to, matched_family=None):
    """处理 .搜寻节点 命令的回复。"""
    if not state.get("taiyi_enabled", False):
        return False

    orig_cmd = (reply_to.raw_text or "") if reply_to else ""
    is_relevant = (
        matched_family == "taiyi_node_search"
        or CMD_NODE_SEARCH in orig_cmd
    )
    if not is_relevant:
        return False

    if _phase() != "search_pending":
        console_log(f"⚠️ 搜寻节点 reply 迟到（phase={_phase()}），仅清 pending 不改 state。")
        return True

    # 找到节点（含"获得：【空间节点·X】"）
    m = RE_NODE_NAME.search(text)
    if m:
        node_name = m.group(1).strip()
        if not _is_safe_node_name(node_name):
            _set_phase("idle", now)
            state["next_taiyi_cycle_time"] = _next_cycle_with_jitter(now)
            _record_failure(now, f"节点名解析异常: {node_name[:30]}")
            save_state()
            await send_audit_log(f"⚠️ 节点名异常已跳过定星: {node_name[:30]}")
            await _check_failure_breaker(now)
            return True

        # 链式：fire-and-forget 短延迟后定星（1.5-3.5s 模拟反应时间）
        state["taiyi_pending_node_name"] = node_name
        _set_phase("define_pending", now)
        save_state()

        async def delayed_define():
            await asyncio.sleep(random.uniform(TAIYI_DEFINE_DELAY_MIN, TAIYI_DEFINE_DELAY_MAX))
            # ★ guard：define_pending 期间且节点名仍是这个才发
            if _phase() != "define_pending":
                return
            if state.get("taiyi_pending_node_name", "") != node_name:
                return
            await send_game_command(f"{CMD_NODE_DEFINE} {node_name}")

        _fire_and_forget(delayed_define())
        await send_audit_log(f"🎯 太一搜寻发现【{node_name}】，将自动定星。")
        return True

    # 一无所获 / 击败天魔（虚空漫游、斩妖除魔）
    if "【虚空漫游】" in text or "【斩妖除魔】" in text:
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = _next_cycle_with_jitter(now)
        _reset_failures()  # 这是正常空轮，不算失败
        save_state()
        console_log(f"🌟 太一搜寻空轮，下次→{fmt_abs_ts(state['next_taiyi_cycle_time'])}")
        return True

    # 修为不足
    if "修为不足" in text and "神游太虚" in text:
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = now + TAIYI_RESOURCE_RETRY_SEC
        _record_failure(now, "修为不足")
        save_state()
        await send_audit_log(f"⚠️ 太一搜寻修为不足，1h 后重试。")
        await _check_failure_breaker(now)
        return True

    # 神识不足（理论上引道刚补 100 不应该发生，记录为异常）
    if "神识不足" in text and "虚空中定位" in text:
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = now + TAIYI_RESOURCE_RETRY_SEC
        _record_failure(now, "神识不足（异常）")
        save_state()
        await send_audit_log("⚠️ 太一搜寻神识不足（异常），1h 后重试。")
        await _check_failure_breaker(now)
        return True

    # 境界不足
    if "【境界不足】" in text and "化神期" in text:
        _freeze(now, "境界不足（化神期前）")
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

    if RE_DEFINE_SUCCESS.search(text):
        # 提取额外产出做 audit
        node_name = state.get("taiyi_pending_node_name", "?")
        state["taiyi_pending_node_name"] = ""
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = _next_cycle_with_jitter(now)
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
    state["next_taiyi_cycle_time"] = _next_cycle_with_jitter(now)
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

    # phase 超时兜底：非 idle 状态卡超过配置时间后强制 reset
    if phase != "idle":
        entered_at = state.get("taiyi_phase_entered_at", 0)
        if entered_at > 0 and now - entered_at > TAIYI_PHASE_TIMEOUT_SEC:
            stale_phase = phase
            stale_node = state.get("taiyi_pending_node_name", "")
            state["taiyi_pending_node_name"] = ""
            _set_phase("idle", now)
            state["next_taiyi_cycle_time"] = now + TAIYI_RESOURCE_RETRY_SEC
            _record_failure(now, f"phase {stale_phase} 超时")
            save_state()
            extra = f"（pending node={stale_node}）" if stale_node else ""
            await send_audit_log(f"⏰ 太一 phase '{stale_phase}' 超时，重置 idle{extra}")
            await _check_failure_breaker(now)
            phase = "idle"
        else:
            return  # 仍在等 reply

    # idle 且到点：发引道
    if state.get("next_taiyi_cycle_time", 0) > now:
        return

    cmd = _resolve_yindao_command()
    _set_phase("yindao_pending", now)
    save_state()
    msg = await send_game_command(cmd)
    if not msg:
        # 发送失败：回退 idle 短退避
        _set_phase("idle", now)
        state["next_taiyi_cycle_time"] = now + 60  # 1min 后重试
        _record_failure(now, "引道发送失败")
        save_state()
        await send_audit_log("❌ 太一引道发送失败，1min 后重试。")
        await _check_failure_breaker(now)


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
