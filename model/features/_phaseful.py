import asyncio
import random
import time
from dataclasses import dataclass

from ..config import CD_BUFFER_SEC, CMD_CONCUBINE_DREAM, CMD_CONCUBINE_VOYAGE_RETURN, CMD_TOWER, CMD_TREE_GUARD, CMD_TREE_WATER, CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
from ..runtime import _fire_and_forget, console_log, get_last_game_send_block, register_game_command_sent_observer, send_audit_log, send_game_command
from ..state import get_current_identity_id, get_game_group_id, get_pending_command, has_identity, is_auto_delete_sent_messages_enabled, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, get_day_key


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
    source_module: str = ""
    summary_trigger_command: str = "1"
    summary_passive_triggers: tuple = ()
    summary_passive_timeout_sec: int = 90
    summary_due_delay_min_sec: int = 30 * 60
    summary_due_delay_max_sec: int = 90 * 60
    summary_active_query_grace_sec: int = 0
    summary_due_timeout_action: str = "active_query"
    summary_observe_sec: int = 180
    summary_retry_min_sec: int = 10 * 60
    summary_retry_max_sec: int = 30 * 60
    queued_launch_timeout_sec: int = 120
    blocks_ordinary_while_running: bool = False
    ignore_summary_finalize_while_running_until_due_sec: int = 0
    passive_timeout_action: str = "active_query"
    queued_launch_timeout_action: str = "active_query"
    timeout_relaunch_min_sec: int = 2 * 60
    timeout_relaunch_max_sec: int = 5 * 60


SUMMARY_DUE_PHASES = {"summary_due", "observing_summary", "waiting_summary"}
# 游戏内阶段只保护本模块自身状态机，不作为普通模块的全局总闸。
# 日志验证显示深度闭关/元婴运行与结算期不会阻止大多数普通指令。
SUMMARY_BLOCKING_PHASES = {"queued_launch"}
_REGISTERED_SPECS = []
_SUMMARY_CONSUMED_COMMANDS = {}

SUMMARY_REPLAYABLE_COMMANDS = {CMD_TREE_WATER, CMD_TREE_GUARD, CMD_TOWER, CMD_CONCUBINE_DREAM, CMD_CONCUBINE_VOYAGE_RETURN}
SUMMARY_REPLAY_MAX_AGE_SEC = 10 * 60
SUMMARY_REPLAY_DELAY_MIN_SEC = 9
SUMMARY_REPLAY_DELAY_MAX_SEC = 18
SUMMARY_REPLAY_TREE_SKIP_GRACE_SEC = 60

_PHASEFUL_LAUNCH_LOCKS = {}


def _summary_replay_intent(send_as_id, msg_id, command):
    chain_id = f"phaseful_replay:{int(send_as_id or 0)}:{int(msg_id or 0)}"
    return {
        "op_id": f"{chain_id}:{str(command or '').strip()}",
        "chain_id": chain_id,
    }


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


def begin_post_summary_wait(spec, now, delay=None, *, confirmed=False):
    if delay is None:
        delay = spec.post_summary_wait_sec
    clear_summary_flags(spec)
    set_phase(spec, "post_summary_wait")
    # In post_summary_wait, the probe flag only records provenance. Relaunch is
    # driven by the business command after the buffer; abnormal/no-reply paths
    # use active status queries for calibration.
    state[spec.probe_pending_key] = bool(confirmed)
    state[spec.next_time_key] = now + delay
    save_state()


def begin_summary_wait(spec, now, *, launch_probe=False):
    set_phase(spec, "waiting_summary")
    state[spec.summary_sent_at_key] = now
    state[spec.probe_pending_key] = bool(launch_probe)
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


def _is_summary_replayable_command(command):
    command = str(command or "").strip()
    return command in SUMMARY_REPLAYABLE_COMMANDS


def _is_voyage_replay_command(command):
    command = str(command or "").strip()
    return command == CMD_CONCUBINE_VOYAGE_RETURN


def _is_replayable_summary_consumed_command(spec, command, reply_to=0):
    command = str(command or "").strip()
    if not command:
        return False
    if int(reply_to or 0) > 0:
        return False
    summary_commands = {str(spec.summary_trigger_command or "").strip(), *tuple(spec.summary_passive_triggers or ())}
    if command in summary_commands:
        return False
    return _is_summary_replayable_command(command)


def _remember_summary_consumed_command(
    send_as_id,
    spec,
    command,
    now,
    msg_id=0,
    *,
    track=True,
    reply_to=0,
    priority=None,
    max_retry=None,
    **send_intent,
):
    if not _is_replayable_summary_consumed_command(spec, command, reply_to=reply_to):
        return
    key = int(send_as_id or 0)
    if key <= 0:
        return
    previous = _SUMMARY_CONSUMED_COMMANDS.get(key)
    if previous and int(previous.get("msg_id", 0) or 0) == int(msg_id or 0):
        specs = set(previous.get("specs") or ())
        specs.add(spec.phase_key)
        previous["specs"] = sorted(specs)
        return
    _SUMMARY_CONSUMED_COMMANDS[key] = {
        "cmd": str(command or "").strip(),
        "msg_id": int(msg_id or 0),
        "sent_at": float(now or time.time()),
        "track": bool(track),
        "reply_to": int(reply_to or 0),
        "priority": priority,
        "max_retry": max_retry,
        "send_intent": {key: value for key, value in send_intent.items() if str(value or "").strip()},
        "specs": [spec.phase_key],
    }


def _has_other_summary_observation(spec=None):
    for other in _REGISTERED_SPECS:
        if spec is not None and other == spec:
            continue
        if not state.get(other.enabled_key):
            continue
        if _phase(other) in {"observing_summary", "waiting_summary"}:
            return True
    return False


def _has_pending_command(command, *, ignore_msg_id=0):
    command = str(command or "").strip()
    ignore_msg_id = int(ignore_msg_id or 0)
    for pending_msg_id, item in state.get("pending_tasks", {}).items():
        try:
            if int(pending_msg_id) == ignore_msg_id:
                continue
        except (TypeError, ValueError):
            pass
        if get_pending_command(item) == command:
            return True
    return False


def _prepare_replayed_command_state(command, now, *, old_msg_id=0):
    if command == CMD_CONCUBINE_DREAM:
        from ..config import CONCUBINE_PHASE_TIMEOUT_SEC
        from .concubine import _phase as concubine_phase
        from .concubine import _set_phase as set_concubine_phase

        if not state.get("concubine_enabled"):
            return False
        if concubine_phase() != "dream_pending":
            return False
        if int(state.get("concubine_dream_msg_id", 0) or 0) != int(old_msg_id or 0):
            return False
        state["concubine_dream_msg_id"] = 0
        state["next_concubine_time"] = float(now) + CONCUBINE_PHASE_TIMEOUT_SEC
        set_concubine_phase("idle")
        return True

    if _is_voyage_replay_command(command):
        from .concubine import _phase as concubine_phase
        from .concubine import _set_phase as set_concubine_phase

        if concubine_phase() != "voyage_return_pending":
            return False
        if int(state.get("concubine_voyage_msg_id", 0) or 0) != int(old_msg_id or 0):
            return False
        retry_count = int(state.get("concubine_voyage_retry_count", 0) or 0)
        if retry_count >= 1:
            return False
        state["concubine_voyage_msg_id"] = 0
        state["concubine_voyage_retry_count"] = 1
        state["next_concubine_time"] = float(now) + CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
        set_concubine_phase("idle")
        return True

    if command != CMD_TOWER:
        return True
    from .tower import TOWER_RETRY_LIMIT

    if not state.get("tower_enabled"):
        return False
    if str(state.get("last_tower_day") or "") == get_day_key(now):
        return False
    if _has_pending_command(CMD_TOWER, ignore_msg_id=old_msg_id):
        return False
    retry_count = int(state.get("tower_retry_count", 0) or 0)
    if retry_count >= TOWER_RETRY_LIMIT:
        return False
    if int(state.get("last_tower_msg_id", 0) or 0) == int(old_msg_id or 0):
        state["last_tower_msg_id"] = 0
    state["tower_reply_due_at"] = 0
    state["tower_retry_count"] = max(retry_count, 1)
    state["next_tower_time"] = float(now)
    return True


def _finalize_replayed_command_state(command, msg):
    if command == CMD_CONCUBINE_DREAM and msg:
        from ..config import CONCUBINE_PHASE_TIMEOUT_SEC
        from .concubine import _set_phase as set_concubine_phase

        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        set_concubine_phase("dream_pending")
        state["concubine_dream_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["next_concubine_time"] = sent_at + CONCUBINE_PHASE_TIMEOUT_SEC
        state["concubine_last_error"] = ""
        return True

    if _is_voyage_replay_command(command) and msg:
        from .concubine import _set_phase as set_concubine_phase

        sent_at = float(getattr(msg, "sent_at", 0) or time.time())
        set_concubine_phase("voyage_return_pending")
        state["concubine_voyage_msg_id"] = int(getattr(msg, "id", 0) or 0)
        state["concubine_voyage_retry_count"] = max(int(state.get("concubine_voyage_retry_count", 0) or 0), 1)
        state["next_concubine_time"] = sent_at + CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
        state["concubine_voyage_last_error"] = ""
        return True

    if command != CMD_TOWER or not msg:
        return False
    from .tower import TOWER_REPLY_TIMEOUT_SEC

    sent_at = float(getattr(msg, "sent_at", 0) or time.time())
    due_at = sent_at + TOWER_REPLY_TIMEOUT_SEC
    state["last_tower_msg_id"] = int(getattr(msg, "id", 0) or 0)
    state["tower_reply_due_at"] = due_at
    state["next_tower_time"] = due_at
    return True


async def _replay_summary_consumed_command(send_as_id, payload):
    await asyncio.sleep(random.uniform(SUMMARY_REPLAY_DELAY_MIN_SEC, SUMMARY_REPLAY_DELAY_MAX_SEC))
    if not has_identity(send_as_id):
        return

    command = str((payload or {}).get("cmd") or "").strip()
    msg_id = int((payload or {}).get("msg_id", 0) or 0)
    sent_at = float((payload or {}).get("sent_at", 0) or 0)
    if not command or time.time() - sent_at > SUMMARY_REPLAY_MAX_AGE_SEC:
        return
    if not _is_summary_replayable_command(command):
        return

    track = bool((payload or {}).get("track", True))
    max_retry = (payload or {}).get("max_retry")
    priority = "retry"
    send_intent = {key: value for key, value in (payload or {}).get("send_intent", {}).items() if str(value or "").strip()}
    for key, value in _summary_replay_intent(send_as_id, msg_id, command).items():
        send_intent.setdefault(key, value)
    if command == CMD_TOWER:
        track = False
        max_retry = 0
        send_intent.setdefault("source_module", "闯塔")

    with use_identity(send_as_id):
        now = time.time()
        if command == CMD_TREE_WATER and float(state.get("next_irr_time", 0) or 0) > now + SUMMARY_REPLAY_TREE_SKIP_GRACE_SEC:
            return
        if command == CMD_TREE_GUARD and float(state.get("next_guard_time", 0) or 0) > now + SUMMARY_REPLAY_TREE_SKIP_GRACE_SEC:
            return
        if track and msg_id > 0 and msg_id not in state.get("pending_tasks", {}):
            return
        if msg_id > 0:
            state.get("pending_tasks", {}).pop(msg_id, None)
        if not _prepare_replayed_command_state(command, now, old_msg_id=msg_id):
            save_state()
            return
        save_state()

    msg = await send_game_command(
        command,
        track=track,
        send_as_id=send_as_id,
        priority=priority,
        max_retry=max_retry,
        **send_intent,
    )
    if msg:
        with use_identity(send_as_id):
            if _finalize_replayed_command_state(command, msg):
                save_state()
        await send_audit_log(
            f"↩️ 归位结算吃掉原指令，已补发一次：{command}",
            scope="identity",
            send_as_id=send_as_id,
            limit=180,
        )


async def _delayed_summary_consumed_command_replay_check(send_as_id, delay):
    await asyncio.sleep(max(1.0, float(delay or 1)))
    if not has_identity(send_as_id):
        return
    with use_identity(send_as_id):
        if _has_other_summary_observation():
            return
    payload = _SUMMARY_CONSUMED_COMMANDS.pop(int(send_as_id or 0), None)
    if payload:
        await _replay_summary_consumed_command(send_as_id, payload)


def _schedule_summary_consumed_command_replay(spec, now):
    send_as_id = int(get_current_identity_id() or 0)
    if send_as_id <= 0:
        return
    payload = _SUMMARY_CONSUMED_COMMANDS.get(send_as_id)
    if not payload:
        return
    if _has_other_summary_observation(spec):
        _fire_and_forget(
            _delayed_summary_consumed_command_replay_check(
                send_as_id,
                _other_observing_remaining(spec, now) + 1,
            )
        )
        return
    sent_at = float((payload or {}).get("sent_at", 0) or 0)
    if sent_at <= 0 or float(now or time.time()) - sent_at > SUMMARY_REPLAY_MAX_AGE_SEC:
        _SUMMARY_CONSUMED_COMMANDS.pop(send_as_id, None)
        return
    _SUMMARY_CONSUMED_COMMANDS.pop(send_as_id, None)
    _fire_and_forget(_replay_summary_consumed_command(send_as_id, payload))


def observe_phaseful_identity_message(
    send_as_id,
    text,
    now=None,
    msg_id=0,
    *,
    track=True,
    reply_to=0,
    priority=None,
    max_retry=None,
    **_send_intent,
):
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

    changed = False
    with use_identity(send_as_id):
        for spec in _REGISTERED_SPECS:
            if not state.get(spec.enabled_key):
                continue
            if not _is_summary_observation_text(spec, text, reply_to=reply_to):
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
            _remember_summary_consumed_command(
                send_as_id,
                spec,
                text,
                now,
                msg_id=msg_id,
                track=track,
                reply_to=reply_to,
                priority=priority,
                max_retry=max_retry,
                **_send_intent,
            )
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


def get_phaseful_summary_risk_reason(now=None, *, lead_sec=60):
    now = float(now if now is not None else time.time())
    lead_sec = max(0.0, float(lead_sec or 0))
    for spec in _REGISTERED_SPECS:
        if not state.get(spec.enabled_key):
            continue
        phase = _phase(spec)
        if phase in SUMMARY_DUE_PHASES:
            return f"{spec.title}待结算/观察中"
        if phase == "queued_launch":
            return f"{spec.title}正在排队发起"
        if phase == "launching":
            return f"{spec.title}发起后等待回复"
        if phase == "running":
            next_time = float(state.get(spec.next_time_key, 0) or 0)
            if next_time > 0 and next_time <= now + lead_sec:
                return f"{spec.title}临近归位结算（{fmt_remaining(next_time)}）"
    return ""


def _schedule_summary_trigger_retry(spec, now, *, preserve_started_at=False):
    set_phase(spec, "summary_due")
    if not preserve_started_at or float(state.get(spec.summary_sent_at_key, 0) or 0) <= 0:
        state[spec.summary_sent_at_key] = float(now)
    state[spec.last_summary_msg_id_key] = 0
    state[spec.next_time_key] = float(now + random.uniform(spec.summary_retry_min_sec, spec.summary_retry_max_sec))
    save_state()


def _summary_due_elapsed(spec, now):
    started_at = float(state.get(spec.summary_sent_at_key, 0) or 0)
    return max(0.0, float(now or 0) - started_at) if started_at > 0 else 0.0


async def _extend_summary_due_wait(spec, now):
    _schedule_summary_trigger_retry(spec, now, preserve_started_at=True)
    grace_sec = float(spec.summary_active_query_grace_sec or 0)
    started_at = float(state.get(spec.summary_sent_at_key, 0) or 0)
    if grace_sec > 0 and started_at > 0:
        grace_until = started_at + grace_sec
        if float(now or 0) < grace_until < float(state.get(spec.next_time_key, 0) or 0):
            state[spec.next_time_key] = grace_until
            save_state()
    if not state.get(spec.waiting_logged_key, False):
        state[spec.waiting_logged_key] = True
        save_state()
        console_log(f"{spec.title} 到期后继续等待真实消息顺带触发，暂不主动查询。")


def _summary_observation_commands(spec):
    commands = set()
    for command in (spec.summary_trigger_command, *(spec.summary_passive_triggers or ())):
        command = str(command or "").strip()
        if command:
            commands.add(command)
    return commands


def _is_summary_observation_text(spec, text, *, reply_to=0):
    text = str(text or "").strip()
    if not text:
        return False
    if text in _summary_observation_commands(spec):
        return True
    return _is_replayable_summary_consumed_command(spec, text, reply_to=reply_to)


async def _send_summary_trigger(spec, console_message):
    state[spec.probe_pending_key] = False
    console_log(console_message)
    command = spec.summary_trigger_command
    msg = await send_game_command(command, track=False, priority="chain", source_module=spec.source_module or None)
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _schedule_summary_trigger_retry(spec, sent_at)
        await send_audit_log(f"{spec.title} 总结触发指令未发出，已延后重试。")
        return False

    begin_summary_wait(spec, sent_at)
    state[spec.last_summary_msg_id_key] = int(msg.id)
    save_state()
    return True


def _get_phaseful_launch_lock(spec):
    key = (int(get_current_identity_id() or 0), str(spec.phase_key))
    lock = _PHASEFUL_LAUNCH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PHASEFUL_LAUNCH_LOCKS[key] = lock
    return lock


async def _send_summary_launch(spec, launch_command, console_message, now=None):
    async with _get_phaseful_launch_lock(spec):
        now = float(now if now is not None else time.time())
        previous_phase = _phase(spec)
        if previous_phase not in {"summary_due", "post_summary_wait"}:
            return False

        previous_summary_started_at = float(state.get(spec.summary_sent_at_key, 0) or 0)
        await delete_summary_trigger_msg(spec)
        begin_queued_launch(spec, now)
        console_log(console_message)
        msg = await send_game_command(
            launch_command,
            track=False,
            priority="chain",
            source_module=spec.source_module or None,
        )
        sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
        if not msg:
            send_block = get_last_game_send_block(get_current_identity_id(), launch_command)
            block_detail = ""
            if send_block:
                code = str(send_block.get("code") or "").strip()
                reason = str(send_block.get("reason") or "").strip()
                if code or reason:
                    block_detail = f"（{code or 'blocked'}: {reason or '无原因'}）"
            if previous_phase == "post_summary_wait":
                delay = random.uniform(spec.timeout_relaunch_min_sec, spec.timeout_relaunch_max_sec)
                begin_post_summary_wait(spec, sent_at, delay=delay, confirmed=True)
            else:
                if previous_summary_started_at > 0:
                    state[spec.summary_sent_at_key] = previous_summary_started_at
                _schedule_summary_trigger_retry(spec, sent_at, preserve_started_at=True)
            await send_audit_log(f"{spec.title} 续轮指令未发出{block_detail}，已延后重试。")
            return False

        if previous_phase == "post_summary_wait":
            mark_launch_command_sent(spec, sent_at)
        else:
            begin_summary_wait(spec, sent_at, launch_probe=True)
            state[spec.last_summary_msg_id_key] = int(msg.id)
            save_state()
        return True


async def _send_active_summary_query(spec, now, *, probe_reserved=False):
    if not probe_reserved:
        state[spec.probe_pending_key] = False
        save_state()
    await delete_summary_trigger_msg(spec)
    state[spec.probe_pending_key] = False
    msg = await send_game_command(
        spec.summary_trigger_command,
        track=False,
        priority="chain",
        source_module=spec.source_module or None,
    )
    sent_at = float(getattr(msg, "sent_at", 0) or time.time()) if msg else time.time()
    if not msg:
        _schedule_summary_trigger_retry(spec, sent_at)
        await send_audit_log(f"{spec.title} 状态查询未发出，已延后重试。")
        return False
    begin_summary_wait(spec, sent_at)
    state[spec.last_summary_msg_id_key] = int(msg.id)
    save_state()
    return True


async def _calibrate_probe_timeout_once(spec, now):
    async with _get_phaseful_launch_lock(spec):
        if _phase(spec) != "waiting_summary":
            return False
        if not state.get(spec.probe_pending_key):
            return False
        if state[spec.summary_sent_at_key] <= 0:
            return False
        if now - state[spec.summary_sent_at_key] < spec.summary_timeout_sec:
            return False
        state[spec.probe_pending_key] = False
        save_state()
        await send_audit_log(f"{spec.title} 续轮指令超时无确认，改用状态查询校准。")
        await _send_active_summary_query(spec, now, probe_reserved=True)
        return True


async def _delay_relaunch_without_status_query(spec, now, audit_text):
    await delete_summary_trigger_msg(spec)
    state[spec.probe_pending_key] = False
    delay = random.uniform(spec.timeout_relaunch_min_sec, spec.timeout_relaunch_max_sec)
    begin_post_summary_wait(spec, now, delay=delay, confirmed=True)
    await update_block_log_state(spec, waiting=False, protect=False)
    await send_audit_log(f"{audit_text}，不再状态查询，{int(delay / 60)}分钟后重新发起。")


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
    running_skip_sec = float(spec.ignore_summary_finalize_while_running_until_due_sec or 0)
    if running_skip_sec > 0 and _phase(spec) == "running":
        next_time = float(state.get(spec.next_time_key, 0) or 0)
        if next_time > float(now or 0) + running_skip_sec:
            return
    delay = max(float(spec.post_summary_wait_sec), _other_observing_remaining(spec, now))
    begin_post_summary_wait(spec, now, delay=delay, confirmed=True)
    await update_block_log_state(spec, waiting=False, protect=False)
    _schedule_summary_consumed_command_replay(spec, now)
    console_log(spec.summary_received_console)


async def _fallback_to_normal_cd(spec, now, audit_text):
    await delete_summary_trigger_msg(spec)
    clear_summary_flags(spec)
    set_phase(spec, "idle")
    state[spec.probe_pending_key] = False
    state[spec.next_time_key] = _default_next_time(spec, now)
    save_state()
    await update_block_log_state(spec, waiting=False, protect=False)
    await send_audit_log(f"{audit_text}，按正常CD兜底→{fmt_abs_ts(state[spec.next_time_key])}")


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
        if spec.queued_launch_timeout_action == "relaunch":
            await _delay_relaunch_without_status_query(spec, now, f"{spec.title} 发起排队等待过久")
        else:
            await send_audit_log(f"{spec.title} 发起排队等待过久，先查询状态确认，避免重复发起。")
            await _send_active_summary_query(spec, now)
        return

    if _phase(spec) == "launching":
        if state[spec.last_command_key] > 0 and now - state[spec.last_command_key] >= spec.launching_timeout_sec:
            from ..runtime import clear_pending_tasks_by_commands
            clear_pending_tasks_by_commands({launch_command})
            await send_audit_log(f"{spec.title} launching 超时，改用状态查询校准。")
            await _send_active_summary_query(spec, now)
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] <= 0:
        await _fallback_to_normal_cd(spec, now, spec.waiting_anomaly_audit)
        return

    if _phase(spec) == "waiting_summary" and state[spec.last_summary_msg_id_key] < 0:
        passive_elapsed = now - state[spec.summary_sent_at_key]
        if passive_elapsed >= spec.summary_passive_timeout_sec:
            if spec.passive_timeout_action == "relaunch":
                await _delay_relaunch_without_status_query(spec, now, f"{spec.title} 顺带触发后未观察到总结")
            else:
                await send_audit_log(f"{spec.title} 顺带触发后未观察到总结，改用状态查询确认。")
                await _send_active_summary_query(spec, now)
        return

    if _phase(spec) == "waiting_summary" and state[spec.summary_sent_at_key] > 0 and now - state[spec.summary_sent_at_key] >= spec.summary_timeout_sec:
        if state.get(spec.probe_pending_key):
            await _calibrate_probe_timeout_once(spec, now)
            return
        await _fallback_to_normal_cd(spec, now, spec.waiting_timeout_audit)
        return

    if _phase(spec) == "observing_summary":
        if now < state[spec.next_time_key]:
            return
        _schedule_summary_trigger_retry(spec, now)
        console_log(f"{spec.title} 顺带触发观察结束，稍后再次触发确认。")
        return

    if _phase(spec) == "summary_due":
        if now < state[spec.next_time_key]:
            return
        grace_sec = float(spec.summary_active_query_grace_sec or 0)
        if grace_sec > 0 and _summary_due_elapsed(spec, now) < grace_sec:
            await _extend_summary_due_wait(spec, now)
            return
        if spec.summary_due_timeout_action == "wait_passive":
            await _send_summary_launch(spec, launch_command, spec.cd_due_console, now=now)
            return
        await _send_summary_trigger(spec, spec.cd_due_console)
        return

    if _phase(spec) == "post_summary_wait":
        await update_block_log_state(spec, waiting=False, protect=False)
        if now < state[spec.next_time_key]:
            return

        await _send_summary_launch(spec, launch_command, spec.post_wait_console, now=now)
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
    "get_phaseful_summary_risk_reason",
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
