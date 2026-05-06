import random
import time
from dataclasses import dataclass

from ..config import CD_BUFFER_SEC
from ..runtime import console_log, register_game_command_sent_observer, send_audit_log, send_game_command
from ..state import get_game_group_id, is_auto_delete_sent_messages_enabled, state
from ..timing import fmt_abs_ts, fmt_remaining


@dataclass(frozen=True)
class PhasefulSpec:
    enabled_key: str
    phase_key: str
    next_time_key: str
    last_command_key: str
    probe_pending_key: str
    summary_sent_at_key: str
    last_summary_msg_id_key: str
    waiting_logged_key: str
    protect_logged_key: str
    cd_sec: int
    protect_sec: int
    launching_timeout_sec: int
    post_summary_wait_sec: int
    summary_timeout_sec: int
    title: str
    summary_pending_label: str
    block_disabled: str
    block_waiting: str
    block_post_wait: str
    block_launching: str
    block_running: str
    block_protect: str
    phase_waiting: str
    phase_post_wait: str
    phase_launching: str
    phase_running: str
    phase_idle_cd: str
    phase_idle_protect: str
    phase_idle_ready: str
    waiting_on_log: str
    waiting_off_log: str
    protect_on_log: str
    protect_off_log: str
    launching_timeout_audit: str
    waiting_anomaly_audit: str
    waiting_timeout_audit: str
    post_wait_console: str
    running_due_console: str
    cd_due_console: str
    summary_received_console: str
    summary_trigger_command: str = "1"
    summary_passive_triggers: tuple = ()
    summary_passive_timeout_sec: int = 90
    summary_due_delay_min_sec: int = 30 * 60
    summary_due_delay_max_sec: int = 90 * 60
    summary_observe_sec: int = 180
    summary_retry_min_sec: int = 10 * 60
    summary_retry_max_sec: int = 30 * 60
    queued_launch_timeout_sec: int = 120
    blocks_ordinary_while_running: bool = False


SUMMARY_DUE_PHASES = {"summary_due", "observing_summary", "waiting_summary"}
# 游戏内阶段只保护本模块自身状态机，不作为普通模块的全局总闸。
# 日志验证显示深度闭关/元婴运行与结算期不会阻止大多数普通指令。
SUMMARY_BLOCKING_PHASES = {"queued_launch"}
_REGISTERED_SPECS = []


def register_phaseful_spec(spec):
    if spec not in _REGISTERED_SPECS:
        _REGISTERED_SPECS.append(spec)


def _phase(spec):
    return state.get(spec.phase_key, "idle")


def _default_next_time(spec, now):
    # 加 1-10 分钟抖动，避免多号同 CD 后同时到点触发"1"风暴
    jitter = random.uniform(60, 600)
    return now + spec.cd_sec + CD_BUFFER_SEC + jitter


def set_phase(spec, phase):
    state[spec.phase_key] = phase


def get_block_reason(spec, now=None):
    if now is None:
        now = time.time()

    phase = _phase(spec)
    if not state[spec.enabled_key]:
        return spec.block_disabled
    if phase == "summary_due":
        return "待结算，等待顺带触发（不阻塞普通指令）"
    if phase == "observing_summary":
        return "观察顺带触发结果"
    if phase == "waiting_summary":
        return spec.block_waiting
    if phase == "post_summary_wait":
        return spec.block_post_wait
    if phase == "queued_launch":
        return "已排队等待发送锁"
    if phase == "launching":
        return spec.block_launching
    if phase == "running":
        return spec.block_running
    if phase == "idle" and state[spec.next_time_key] > now:
        return spec.phase_idle_cd
    if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] < spec.protect_sec:
        return spec.block_protect
    return "无"


async def update_block_log_state(spec, waiting=None, protect=None):
    if waiting is not None:
        prev = state.get(spec.waiting_logged_key, False)
        if waiting and not prev:
            state[spec.waiting_logged_key] = True
            console_log(spec.waiting_on_log)
        elif not waiting and prev:
            state[spec.waiting_logged_key] = False
            console_log(spec.waiting_off_log)

    if protect is not None:
        prev = state.get(spec.protect_logged_key, False)
        if protect and not prev:
            state[spec.protect_logged_key] = True
            console_log(spec.protect_on_log)
        elif not protect and prev:
            state[spec.protect_logged_key] = False
            console_log(spec.protect_off_log)


def get_phase_text(spec, phase=None, now=None):
    if phase is None:
        phase = _phase(spec)
    if now is None:
        now = time.time()

    if phase == "waiting_summary":
        return spec.phase_waiting
    if phase == "summary_due":
        return "待结算，等待顺带触发"
    if phase == "observing_summary":
        return "观察顺带触发结果"
    if phase == "post_summary_wait":
        return spec.phase_post_wait
    if phase == "queued_launch":
        return "等待发送中"
    if phase == "launching":
        return spec.phase_launching
    if phase == "running":
        return spec.phase_running
    if phase == "idle":
        if state[spec.next_time_key] > now:
            return spec.phase_idle_cd
        if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] < spec.protect_sec:
            return spec.phase_idle_protect
        return spec.phase_idle_ready
    return spec.phase_idle_ready


def get_status_detail_text(spec):
    return (
        f"{spec.title}\n"
        f"- 当前阶段：{get_phase_text(spec)}\n"
        f"- 当前阻塞原因：{get_block_reason(spec)}\n"
        f"- 下次执行：{fmt_abs_ts(state[spec.next_time_key])}（{fmt_remaining(state[spec.next_time_key])}）\n"
        f"- {spec.summary_pending_label}：{'是' if _phase(spec) in SUMMARY_DUE_PHASES else '否'}｜30秒缓冲中：{'是' if _phase(spec) == 'post_summary_wait' else '否'}"
    )


def mark_success(spec, now, next_time=None):
    set_phase(spec, "running")
    state[spec.probe_pending_key] = False
    state[spec.summary_sent_at_key] = 0
    state[spec.last_summary_msg_id_key] = 0
    state[spec.last_command_key] = now
    if next_time is None:
        next_time = _default_next_time(spec, now)
    state[spec.next_time_key] = next_time


from ..persistence import save_state  # 延后导入，避免循环依赖


def clear_summary_flags(spec):
    state[spec.summary_sent_at_key] = 0
    state[spec.last_summary_msg_id_key] = 0
    if _phase(spec) in SUMMARY_DUE_PHASES:
        set_phase(spec, "idle")


def begin_post_summary_wait(spec, now, delay=None):
    if delay is None:
        delay = spec.post_summary_wait_sec
    clear_summary_flags(spec)
    set_phase(spec, "post_summary_wait")
    state[spec.probe_pending_key] = False
    state[spec.next_time_key] = now + delay
    save_state()


def begin_summary_wait(spec, now):
    set_phase(spec, "waiting_summary")
    state[spec.summary_sent_at_key] = now
    save_state()


def begin_summary_due(spec, now, reason=None):
    delay = random.uniform(spec.summary_due_delay_min_sec, spec.summary_due_delay_max_sec)
    set_phase(spec, "summary_due")
    state[spec.probe_pending_key] = False
    state[spec.summary_sent_at_key] = now
    state[spec.last_summary_msg_id_key] = 0
    state[spec.next_time_key] = now + delay
    save_state()
    if reason:
        console_log(f"{reason}，先挂起等待顺带触发→{fmt_remaining(state[spec.next_time_key])}")


def begin_queued_launch(spec, now):
    set_phase(spec, "queued_launch")
    state[spec.probe_pending_key] = False
    state[spec.summary_sent_at_key] = 0
    state[spec.last_summary_msg_id_key] = 0
    state[spec.last_command_key] = now
    state[spec.next_time_key] = now + spec.queued_launch_timeout_sec
    save_state()


def mark_launch_command_sent(spec, sent_at):
    set_phase(spec, "launching")
    state[spec.last_command_key] = sent_at
    state[spec.next_time_key] = _default_next_time(spec, sent_at)
    save_state()


def observe_phaseful_identity_message(send_as_id, text, now=None, msg_id=0):
    text = str(text or "").strip()
    if not text:
        return
    now = float(now if now is not None else time.time())
    try:
        send_as_id = int(send_as_id or 0)
    except Exception:
        send_as_id = 0
    if send_as_id <= 0:
        return

    from ..state import use_identity

    changed = False
    with use_identity(send_as_id):
        for spec in _REGISTERED_SPECS:
            if not state.get(spec.enabled_key):
                continue
            if not _is_summary_observation_text(spec, text):
                continue
            phase = _phase(spec)
            if phase == "summary_due":
                pass
            elif phase == "running" and float(state.get(spec.next_time_key, 0) or 0) <= now:
                console_log(f"{spec.title} 已到期，由同身份其他指令顺带触发，进入观察。")
            else:
                continue
            set_phase(spec, "observing_summary")
            state[spec.summary_sent_at_key] = now
            state[spec.last_summary_msg_id_key] = 0
            state[spec.next_time_key] = now + spec.summary_observe_sec
            changed = True
    if changed:
        save_state()


register_game_command_sent_observer(observe_phaseful_identity_message)


def has_phaseful_summary_block(now=None):
    if now is None:
        now = time.time()
    for spec in _REGISTERED_SPECS:
        if not state.get(spec.enabled_key):
            continue
        phase = _phase(spec)
        if phase in SUMMARY_BLOCKING_PHASES:
            return True
        if phase == "running":
            if spec.blocks_ordinary_while_running:
                return True
    return False


def _schedule_summary_trigger_retry(spec, now):
    set_phase(spec, "summary_due")
    state[spec.summary_sent_at_key] = float(now)
    state[spec.last_summary_msg_id_key] = 0
    state[spec.next_time_key] = float(now + random.uniform(spec.summary_retry_min_sec, spec.summary_retry_max_sec))
    save_state()


def _is_summary_observation_text(spec, text):
    if text.startswith(".") or text == "1":
        return True
    return text in set(spec.summary_passive_triggers or ())


def _choose_passive_summary_trigger(spec):
    triggers = tuple(spec.summary_passive_triggers or ())
    if not triggers:
        return spec.summary_trigger_command
    weighted = []
    for trigger in triggers:
        weight = 1 if trigger == "1" else 3
        weighted.extend([trigger] * weight)
    return random.choice(weighted)


async def _send_summary_trigger(spec, console_message):
    state[spec.probe_pending_key] = False
    console_log(console_message)
    command = _choose_passive_summary_trigger(spec)
    is_passive = command != spec.summary_trigger_command
    msg = await send_game_command(command, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _schedule_summary_trigger_retry(spec, sent_at)
        await send_audit_log(f"{spec.title} 总结触发指令未发出，已延后重试。")
        return False

    begin_summary_wait(spec, sent_at)
    state[spec.last_summary_msg_id_key] = -int(msg.id) if is_passive else int(msg.id)
    save_state()
    return True


async def _send_active_summary_query(spec, now):
    await delete_summary_trigger_msg(spec)
    state[spec.probe_pending_key] = False
    msg = await send_game_command(spec.summary_trigger_command, track=False, priority="chain")
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _schedule_summary_trigger_retry(spec, sent_at)
        await send_audit_log(f"{spec.title} 状态查询未发出，已延后重试。")
        return False
    begin_summary_wait(spec, sent_at)
    state[spec.last_summary_msg_id_key] = int(msg.id)
    save_state()
    return True


def _other_observing_remaining(spec, now):
    remaining = 0.0
    for other in _REGISTERED_SPECS:
        if other == spec:
            continue
        if not state.get(other.enabled_key):
            continue
        if _phase(other) != "observing_summary":
            continue
        other_next = float(state.get(other.next_time_key, 0) or 0)
        if other_next > now:
            remaining = max(remaining, other_next - now)
    return remaining


async def delete_summary_trigger_msg(spec):
    msg_id = state.get(spec.last_summary_msg_id_key, 0)
    if not msg_id:
        return
    msg_id = abs(int(msg_id))
    if is_auto_delete_sent_messages_enabled():
        try:
            from ..runtime import _get_identity_client
            await _get_identity_client().delete_messages(get_game_group_id(), [msg_id])
        except Exception:
            pass
    state["my_msg_ids"].pop(msg_id, None)


async def finalize_summary_broadcast(spec, now):
    await delete_summary_trigger_msg(spec)
    delay = max(float(spec.post_summary_wait_sec), _other_observing_remaining(spec, now))
    begin_post_summary_wait(spec, now, delay=delay)
    await update_block_log_state(spec, waiting=False, protect=False)
    console_log(spec.summary_received_console)


async def run_phaseful_scheduler(spec, now, *, launch_command, schedule_probe):
    if not state[spec.enabled_key]:
        return

    if _phase(spec) == "queued_launch":
        deadline = float(state.get(spec.next_time_key, 0) or 0)
        queued_at = float(state.get(spec.last_command_key, 0) or 0)
        if deadline <= 0:
            deadline = queued_at + spec.queued_launch_timeout_sec if queued_at > 0 else now + spec.queued_launch_timeout_sec
            state[spec.next_time_key] = deadline
            save_state()
        if now < deadline:
            return
        await send_audit_log(f"{spec.title} 发送前排队超时，先查状态确认，避免重复发起。")
        await _send_active_summary_query(spec, now)
        return

    if _phase(spec) == "launching":
        if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] >= spec.launching_timeout_sec:
            from ..runtime import clear_pending_tasks_by_commands
            clear_pending_tasks_by_commands({launch_command})
            set_phase(spec, "idle")
            save_state()
            await send_audit_log(spec.launching_timeout_audit)
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] <= 0:
        await delete_summary_trigger_msg(spec)
        clear_summary_flags(spec)
        save_state()
        await send_audit_log(spec.waiting_anomaly_audit)
        begin_queued_launch(spec, now)
        msg = await send_game_command(launch_command, track=False, priority="chain")
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            mark_launch_command_sent(spec, sent_at)
            await schedule_probe(random.uniform(8, 12))
        else:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "waiting_summary" and state[spec.last_summary_msg_id_key] < 0:
        passive_elapsed = now - state[spec.summary_sent_at_key]
        if passive_elapsed >= spec.summary_passive_timeout_sec:
            await send_audit_log(f"{spec.title} 闲聊触发未见总结，改用状态查询兜底。")
            await _send_active_summary_query(spec, now)
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] > 0 and now - state[spec.summary_sent_at_key] >= spec.summary_timeout_sec:
        await delete_summary_trigger_msg(spec)
        clear_summary_flags(spec)
        save_state()
        await send_audit_log(spec.waiting_timeout_audit)
        begin_queued_launch(spec, now)
        msg = await send_game_command(launch_command, track=False, priority="chain")
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            mark_launch_command_sent(spec, sent_at)
            await schedule_probe(random.uniform(8, 12))
        else:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "observing_summary":
        if now < state[spec.next_time_key]:
            return
        _schedule_summary_trigger_retry(spec, now)
        console_log(f"{spec.title} 顺带触发观察结束，延后主动查询。")
        return

    if _phase(spec) == "summary_due":
        if now < state[spec.next_time_key]:
            return
        await _send_summary_trigger(spec, spec.cd_due_console)
        return

    if _phase(spec) == "post_summary_wait":
        await update_block_log_state(spec, waiting=False, protect=False)
        if now < state[spec.next_time_key]:
            return

        console_log(spec.post_wait_console)
        begin_queued_launch(spec, now)
        msg = await send_game_command(launch_command, track=False, priority="chain")
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            mark_launch_command_sent(spec, sent_at)
        else:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "running" and state[spec.next_time_key] > 0 and now >= state[spec.next_time_key]:
        begin_summary_due(spec, now, reason=spec.running_due_console)
        return

    if _phase(spec) in SUMMARY_DUE_PHASES:
        if state[spec.next_time_key] <= now:
            await update_block_log_state(spec, waiting=True, protect=False)
        else:
            await update_block_log_state(spec, waiting=False, protect=False)
        return
    else:
        await update_block_log_state(spec, waiting=False)

    if _phase(spec) == "running":
        await update_block_log_state(spec, protect=False)
        return

    if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] < spec.protect_sec:
        if state[spec.next_time_key] <= now:
            await update_block_log_state(spec, waiting=False, protect=True)
        else:
            await update_block_log_state(spec, protect=False)
        return
    else:
        await update_block_log_state(spec, protect=False)

    if now >= state[spec.next_time_key]:
        begin_summary_due(spec, now, reason=spec.cd_due_console)


__all__ = [
    "PhasefulSpec",
    "begin_post_summary_wait",
    "begin_queued_launch",
    "begin_summary_wait",
    "begin_summary_due",
    "clear_summary_flags",
    "delete_summary_trigger_msg",
    "finalize_summary_broadcast",
    "get_block_reason",
    "get_phase_text",
    "get_status_detail_text",
    "has_phaseful_summary_block",
    "mark_success",
    "mark_launch_command_sent",
    "observe_phaseful_identity_message",
    "register_phaseful_spec",
    "run_phaseful_scheduler",
    "set_phase",
    "update_block_log_state",
]
