import random
import time
from dataclasses import dataclass

from ..config import CD_BUFFER_SEC
from ..runtime import console_log, send_audit_log, send_game_command
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


def _phase(spec):
    return state.get(spec.phase_key, "idle")


def _default_next_time(spec, now):
    return now + spec.cd_sec + CD_BUFFER_SEC


def set_phase(spec, phase):
    state[spec.phase_key] = phase


def get_block_reason(spec, now=None):
    if now is None:
        now = time.time()

    phase = _phase(spec)
    if not state[spec.enabled_key]:
        return spec.block_disabled
    if phase == "waiting_summary":
        return spec.block_waiting
    if phase == "post_summary_wait":
        return spec.block_post_wait
    if phase == "launching":
        return spec.block_launching
    if phase == "running" or (phase == "idle" and state[spec.next_time_key] > now):
        return spec.block_running
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
    if phase == "post_summary_wait":
        return spec.phase_post_wait
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
        f"- {spec.summary_pending_label}：{'是' if _phase(spec) == 'waiting_summary' else '否'}｜30秒缓冲中：{'是' if _phase(spec) == 'post_summary_wait' else '否'}"
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
    if _phase(spec) == "waiting_summary":
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


async def delete_summary_trigger_msg(spec):
    msg_id = state.get(spec.last_summary_msg_id_key, 0)
    if not msg_id:
        return
    if is_auto_delete_sent_messages_enabled():
        try:
            from ..runtime import _get_identity_client
            await _get_identity_client().delete_messages(get_game_group_id(), [msg_id])
        except Exception:
            pass
    state["my_msg_ids"].pop(msg_id, None)


async def finalize_summary_broadcast(spec, now):
    await delete_summary_trigger_msg(spec)
    begin_post_summary_wait(spec, now, delay=spec.post_summary_wait_sec)
    await update_block_log_state(spec, waiting=False, protect=False)
    console_log(spec.summary_received_console)


async def run_phaseful_scheduler(spec, now, *, launch_command, schedule_probe):
    if not state[spec.enabled_key]:
        return

    if _phase(spec) == "launching":
        if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] >= spec.launching_timeout_sec:
            set_phase(spec, "idle")
            save_state()
            await send_audit_log(spec.launching_timeout_audit)
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] <= 0:
        await delete_summary_trigger_msg(spec)
        clear_summary_flags(spec)
        set_phase(spec, "launching")
        state[spec.last_command_key] = now
        state[spec.next_time_key] = _default_next_time(spec, now)
        save_state()
        await send_audit_log(spec.waiting_anomaly_audit)
        msg = await send_game_command(launch_command, track=False)
        if msg:
            await schedule_probe(random.uniform(8, 12))
        else:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] > 0 and now - state[spec.summary_sent_at_key] >= spec.summary_timeout_sec:
        await delete_summary_trigger_msg(spec)
        clear_summary_flags(spec)
        set_phase(spec, "launching")
        state[spec.last_command_key] = now
        state[spec.next_time_key] = _default_next_time(spec, now)
        save_state()
        await send_audit_log(spec.waiting_timeout_audit)
        msg = await send_game_command(launch_command, track=False)
        if msg:
            await schedule_probe(random.uniform(8, 12))
        else:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "post_summary_wait":
        await update_block_log_state(spec, waiting=False, protect=False)
        if now < state[spec.next_time_key]:
            return

        set_phase(spec, "launching")
        state[spec.last_command_key] = now
        state[spec.next_time_key] = _default_next_time(spec, now)
        save_state()
        console_log(spec.post_wait_console)
        msg = await send_game_command(launch_command)
        if not msg:
            set_phase(spec, "idle")
            save_state()
        return

    if _phase(spec) == "running" and state[spec.next_time_key] > 0 and now >= state[spec.next_time_key]:
        state[spec.probe_pending_key] = False
        begin_summary_wait(spec, now)
        console_log(spec.running_due_console)
        msg = await send_game_command("1", track=False)
        if msg:
            state[spec.last_summary_msg_id_key] = msg.id
            save_state()
        return

    if _phase(spec) == "waiting_summary":
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
        begin_summary_wait(spec, now)
        console_log(spec.cd_due_console)
        msg = await send_game_command("1", track=False)
        if msg:
            state[spec.last_summary_msg_id_key] = msg.id
            save_state()


__all__ = [
    "PhasefulSpec",
    "begin_post_summary_wait",
    "begin_summary_wait",
    "clear_summary_flags",
    "delete_summary_trigger_msg",
    "finalize_summary_broadcast",
    "get_block_reason",
    "get_phase_text",
    "get_status_detail_text",
    "mark_success",
    "run_phaseful_scheduler",
    "set_phase",
    "update_block_log_state",
]