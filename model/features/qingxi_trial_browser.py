"""Bounded native-browser trials using the already registered account client."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import time
import uuid

from ..activity_occupancy import get_activity_block_reason
from ..runtime import send_audit_log, track_background_task
from ..state import get_global_enabled, is_cave_public_identity_available
from ..webapp_core import extract_miniapp_init_data_from_url, parse_miniapp_init_data
from . import changfeng_trial as trial
from .cave_treasure_miniapp import request_cave_treasure_miniapp_init_data
from .miniapp_common import MiniAppIdentityOwner

TOOL_ROOT = Path('/root/2026/qingxi-trial-v13-20261105')
# NPC automation imports TOOL_ROOT for its shared Linux/CDP helpers. Keep that
# installation stable; only trials select the independently verified m19 bot.
TRIAL_TOOL_ROOT = Path('/root/2026/qingxi-trial-m19-wa-canary-20261116')
TRIAL_STRATEGY_SHA256 = '67d98870f531ae2642cf645054e0dd67cf9cf92441f74e7d7f617ed321a04e71'
TRIAL_RUNNER_SHA256 = 'f5f113bb168f7327ec2af2a699e96a22505da928090d5df67ad591072ba738ec'
_TASK = None
_STATUS = {}
_ATTEMPT_FIELDS = (
    'run_id', 'operation_id', 'arena', 'result', 'grade', 'completed_at', 'receipts', 'report',
    'confirmed_wave', 'gains', 'cultivation', 'rewarded', 'tier', 'exhausted',
    'report_profile', 'observed_status', 'observed_at', 'observed_arenas', 'server_cleared',
    'browser_trial', 'browser_trial_notice', 'execution_engine', 'started_at',
)


def snapshot():
    return copy.deepcopy(_STATUS)


def _previous_attempt(record):
    return {key: copy.deepcopy(record[key]) for key in _ATTEMPT_FIELDS if key in record}


def _archive_previous_attempt(record, previous, current_run_id=None, *, active=None):
    if active is None:
        active = {key: copy.deepcopy(record[key]) for key in
                  ('operation_id', 'arena', 'execution_engine', 'started_at') if key in record}
    if previous:
        record['previous_attempt'] = previous
        if previous.get('run_id') and previous.get('run_id') != current_run_id:
            record['previous_run_id'] = previous['run_id']
    for key in _ATTEMPT_FIELDS:
        record.pop(key, None)
    record.update(active)


def _restore_previous_metadata(record, previous):
    for key in ('operation_id', 'arena', 'execution_engine', 'started_at'):
        if key in previous:
            record[key] = copy.deepcopy(previous[key])
        else:
            record.pop(key, None)


def _sync_main_result(record, status):
    server_started = status.get('server_started')
    # Records written before the state-sync fields were introduced can still
    # be reconciled when their run, reward and server closure are explicit.
    if server_started is None:
        server_started = bool(status.get('run_id') and
                              (status.get('rewarded') is True or status.get('server_closed') is True))
    if (record.get('phase') == 'held' or status.get('unknown_request')
            or not server_started):
        return False
    server_result = status.get('server_result')
    grade = server_result.get('grade') if isinstance(server_result, dict) else None
    terminal = isinstance(grade, str) and bool(grade)
    closed = status.get('server_closed') is True or status.get('server_cleared') is True
    if not (terminal or closed):
        return False
    run_id = status.get('run_id')
    if not run_id and not (status.get('exhausted') or status.get('no_rewards')):
        return False

    rewarded = status.get('rewarded') is True
    gains = status.get('gains') or {}
    if not isinstance(gains, dict):
        gains = {}
    gains = {name: count for name, count in gains.items()
             if isinstance(name, str) and type(count) is int and count > 0}
    cultivation = status.get('cultivation')
    cultivation = cultivation if type(cultivation) is int and cultivation > 0 else 0
    if rewarded:
        summary = '、'.join(f'{name}×{count}' for name, count in gains.items())
        if cultivation:
            summary += ('，' if summary else '') + f'修为+{cultivation}'
        outcome = '已通关' if status.get('server_cleared') else '已结束'
        record['result'] = f'{outcome}｜{summary or "无物品收获"}'
    elif status.get('exhausted') or status.get('no_rewards'):
        record['result'] = '今日有奖次数已用完'
    else:
        return False

    if run_id:
        record['run_id'] = run_id
    if status.get('job_id'):
        record['operation_id'] = 'browser-' + str(status['job_id'])
    arena = status.get('arena')
    if isinstance(arena, str) and arena in trial.TRIAL_ARENAS:
        record['arena'] = arena
    if status.get('scheduled'):
        record['execution_engine'] = 'browser-m19'
    if status.get('started_at'):
        record['started_at'] = status['started_at']
    tier = status.get('tier')
    if type(tier) is int and tier > 0:
        record['tier'] = tier
    elif 'tier' in record:
        record.pop('tier')
    if terminal:
        record['grade'] = grade
    else:
        record.pop('grade', None)
    confirmed_wave = status.get('confirmed_wave')
    if type(confirmed_wave) is int and confirmed_wave >= 0:
        record['confirmed_wave'] = confirmed_wave
    else:
        record.pop('confirmed_wave', None)
    record['rewarded'] = rewarded
    record['gains'] = gains if rewarded else {}
    record['cultivation'] = cultivation
    record['exhausted'] = bool(status.get('exhausted') or status.get('no_rewards'))
    record['server_cleared'] = status.get('server_cleared') is True
    finished_at = status.get('finished_at')
    if type(finished_at) in (int, float) and finished_at > 0:
        record['completed_at'] = finished_at
    elif type(record.get('completed_at')) not in (int, float) or record['completed_at'] <= 0:
        record['completed_at'] = time.time()
    record['phase'] = 'done'
    record['last_error'] = None
    return True


def reconcile_persisted_records():
    """Repair completed native records left with an older HTTP result."""
    changed = False
    for record in trial._records().values():
        if not isinstance(record, dict) or record.get('phase') != 'done':
            continue
        status = record.get('browser_trial')
        if not isinstance(status, dict) or not status.get('run_id'):
            continue
        probe = copy.deepcopy(record)
        if not _sync_main_result(probe, status) or probe == record:
            continue
        # Once the result belongs to this native run, repairing metadata must
        # not archive that same run over the actual previous HTTP attempt.
        if (record.get('run_id') == status.get('run_id')
                and isinstance(record.get('result'), str)
                and record['result'].startswith(('已通关｜', '已结束｜', '今日有奖次数已用完'))):
            record.update(probe)
            for key in _ATTEMPT_FIELDS:
                if key not in probe:
                    record.pop(key, None)
            changed = True
            continue
        previous = _previous_attempt(record)
        if record.get('run_id') == status.get('run_id') and record.get('previous_run_id'):
            previous['run_id'] = record['previous_run_id']
            for key in ('operation_id', 'browser_trial', 'browser_trial_notice', 'execution_engine', 'started_at'):
                previous.pop(key, None)
        notice = copy.deepcopy(record.get('browser_trial_notice'))
        active = {}
        if status.get('job_id'):
            active['operation_id'] = 'browser-' + str(status['job_id'])
        if status.get('arena') in trial.TRIAL_ARENAS:
            active['arena'] = status['arena']
        if status.get('scheduled'):
            active['execution_engine'] = 'browser-m19'
        if status.get('started_at'):
            active['started_at'] = status['started_at']
        _archive_previous_attempt(record, previous, status.get('run_id'), active=active)
        record['browser_trial'] = status
        if notice:
            record['browser_trial_notice'] = notice
        changed = _sync_main_result(record, status) or changed
    return changed


def _trial_tool_root():
    root = TRIAL_TOOL_ROOT
    if not all((root / part).is_file() for part in (
            '.venv/bin/python', 'tools/longwind_trial_bot.py', 'tools/longwind_trial_bot.js')):
        raise RuntimeError('trial_browser_dependency_missing')
    if hashlib.sha256((root / 'tools/longwind_trial_bot.js').read_bytes()).hexdigest() != TRIAL_STRATEGY_SHA256:
        raise RuntimeError('trial_browser_strategy_changed')
    if hashlib.sha256((root / 'tools/longwind_trial_bot.py').read_bytes()).hexdigest() != TRIAL_RUNNER_SHA256:
        raise RuntimeError('trial_browser_runner_changed')
    return root


def summarize_network(network):
    """Only genuine server results can establish completion or resource consumption."""
    successful = [row['trial'] for row in network.get('receipts') or []
                  if isinstance(row, dict) and row.get('ok') is True and isinstance(row.get('trial'), dict)
                  and not row['trial'].get('pending') and not row['trial'].get('anomaly')]
    run = next((row['run'] for row in reversed(successful)
                if isinstance(row.get('run'), dict) and row['run'].get('id')), {})
    run_id = run.get('id')
    successful = [row for row in successful if (row.get('run') or {}).get('id') in (None, run_id)]
    rewarded = any((row.get('run') or {}).get('rewarded') is True for row in successful)
    no_rewards = any((row.get('run') or {}).get('rewarded') is False for row in successful)
    terminal = next((row for row in reversed(successful)
                     if isinstance(row.get('result'), dict) and row['result'].get('grade')), None)
    started = any(isinstance(row, dict) and row.get('action') in {'start', 'checkpoint', 'end'}
                  for row in network.get('requests') or [])
    unknown_request = any(not isinstance(row, dict) or row.get('action') not in {'status', 'start', 'checkpoint', 'end'}
                          for row in network.get('requests') or [])
    # A pre-start status with activeRun=null cannot confirm an ambiguous start ended.
    latest_status = (successful[-1].get('status') or {}) if successful else {}
    closed = bool(started and run_id and 'activeRun' in latest_status and latest_status['activeRun'] is None)
    exhausted = (latest_status.get('remaining') == 0
                 and type(latest_status.get('remaining')) is int
                 and type(latest_status.get('used')) is int
                 and type(latest_status.get('limit')) is int
                 and latest_status['used'] == latest_status['limit'] > 0)
    requests, receipts = network.get('requests') or [], network.get('receipts') or []
    # Missing or undecodable request evidence is not proof that start was never
    # sent. CDP explicitly records action='unknown' when postData parsing fails.
    no_start_verified = bool(
        requests and receipts
        and all(isinstance(row, dict) and row.get('action') == 'status' for row in requests)
        and all(isinstance(row, dict) and row.get('ok') is True
                and isinstance(row.get('trial'), dict)
                and not row['trial'].get('run')
                and not row['trial'].get('pending') and not row['trial'].get('anomaly')
                and not (row['trial'].get('status') or {}).get('activeRun') for row in receipts)
        and 'activeRun' in latest_status and latest_status['activeRun'] is None)
    gains, cultivation, seen = {}, 0, set()
    confirmed_wave = -1
    for row in successful:
        current = row.get('run') or {}
        wave = current.get('cleared')
        if current.get('id') == run_id and type(wave) is int and wave >= 0:
            confirmed_wave = max(confirmed_wave, wave)
        if (not run_id or current.get('id') != run_id or current.get('rewarded') is not True
                or type(wave) is not int or not 0 <= wave <= 3 or wave in seen):
            continue
        grants = [grant for grant in row.get('grants') or []
                  if isinstance(grant, dict) and isinstance(grant.get('name'), str) and grant['name'].strip()
                  and type(grant.get('quantity')) is int and grant['quantity'] > 0]
        amount = row.get('cultivation')
        amount = amount if type(amount) is int and amount > 0 else 0
        if not grants and not amount:
            continue
        # Retransmitted checkpoint receipts are the same wave; separate stacks
        # within one receipt are real grants and must still be added together.
        seen.add(wave)
        for grant in grants:
            gains[grant['name']] = gains.get(grant['name'], 0) + grant['quantity']
        cultivation += amount
    return {'started': started, 'rewarded': rewarded, 'terminal': terminal, 'closed': closed,
            'run_id': run_id, 'tier': run.get('tier'), 'gains': gains, 'cultivation': cultivation,
            'exhausted': exhausted, 'no_rewards': no_rewards, 'no_start_verified': no_start_verified,
            'unknown_request': unknown_request, 'confirmed_wave': confirmed_wave,
            'cleared': bool(terminal and (terminal.get('run') or {}).get('status') == 'cleared')}


async def _notify_result(record):
    status = record.get('browser_trial') or {}
    run_id = status.get('run_id')
    gains, cultivation = status.get('gains') or {}, status.get('cultivation') or 0
    if (not run_id or not (gains or cultivation)
            or (record.get('browser_trial_notice') or {}).get('run_id') == run_id):
        return
    arena = trial.TRIAL_ARENAS[status['arena']]
    tier = status.get('tier')
    title = f"⚔️ 长风试炼·{arena}" + (f"·第{tier}重" if type(tier) is int and tier > 0 else '')
    outcome = '已通关' if status.get('server_cleared') else ('已结束' if status.get('server_closed') else '结算待核对')
    summary = '、'.join(f'{name}×{count}' for name, count in gains.items())
    if cultivation:
        summary += ('，' if summary else '') + f'修为+{cultivation}'
    notice = {'run_id': run_id, 'attempted_at': time.time(), 'state': 'attempted'}
    record['browser_trial_notice'] = notice
    # Persist intent before sending. An unknown Telegram outcome must not cause
    # another notice or, more importantly, another game run.
    trial._save()
    try:
        delivered = await send_audit_log(f'{title}{outcome}｜已确认收获：{summary}',
                                         scope='identity', send_as_id=status['identity_id'], priority='normal', limit=700)
        notice['state'] = 'sent' if delivered else 'unconfirmed'
    except Exception as error:
        notice.update(state='unconfirmed', error=type(error).__name__)
    finally:
        trial._save()


async def action(payload):
    global _TASK
    if payload.get('action') == 'status':
        return True, '浏览器单次试跑状态', {'browser_trial': snapshot()}
    if payload.get('action') != 'start':
        return False, '仅支持 start/status', {}
    if _TASK is not None and not _TASK.done():
        return False, '已有浏览器试跑执行中', {'browser_trial': snapshot()}
    try:
        identity_id = int(payload.get('send_as_id') or 0)
    except (ValueError, TypeError, OverflowError):
        return False, '身份无效', {}
    arena = payload.get('arena', 'longwind')
    if arena not in trial.TRIAL_ARENAS or type(payload.get('practice_only', False)) is not bool:
        return False, '场地或练习模式无效', {}
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None or owner.account_id != identity_id or not is_cave_public_identity_available(identity_id):
        return False, '单次浏览器试跑仅支持可用的本人账号', {}
    reason = get_activity_block_reason(identity_id)
    if not get_global_enabled() or reason:
        return False, reason or '全局已暂停', {}
    try:
        _trial_tool_root()
    except (OSError, RuntimeError):
        return False, '本地试炼工具未安装', {}
    from . import cave_treasure_runtime as cave
    if cave._public_entry_lock(identity_id).locked():
        return False, '身份洞府入口忙', {}
    job_id = uuid.uuid4().hex
    _STATUS.clear()
    _STATUS.update(job_id=job_id, identity_id=identity_id, arena=arena, phase='preparing', started_at=time.time())
    _TASK = track_background_task(asyncio.create_task(_run(owner, arena, payload.get('practice_only', False), job_id)))
    return True, '已安排单次浏览器试跑，不启用自动化', {'browser_trial': snapshot()}


async def run_scheduled_once(identity_id, arena='longwind'):
    """One scheduled native game, sharing manual ownership and local daily caps."""
    global _TASK
    identity_id = int(identity_id)
    config = copy.deepcopy(trial.get_expedition_config().get('changfeng', {}))
    if (not config.get('enabled') or identity_id not in config.get('participant_identity_ids', [])
            or arena not in trial.TRIAL_ARENAS or not get_global_enabled()):
        return False, '长风试炼未启用或场地不可用'
    if _TASK is not None and not _TASK.done():
        return False, '已有浏览器试跑执行中'
    owner = MiniAppIdentityOwner.capture(identity_id)
    if (owner is None or owner.account_id != identity_id
            or not is_cave_public_identity_available(identity_id)):
        return False, '浏览器试炼仅支持可用的本人账号'
    if get_activity_block_reason(identity_id):
        return False, '身份正在参加其他活动'
    record = trial._records().get(str(identity_id), {})
    now = time.time()
    if record.get('phase') not in {None, 'done', 'idle'}:
        return False, '旧试炼尚待核对'
    used = int(record.get('daily_used') or 0) if record.get('day') == trial._day(now) else 0
    if used >= int(config.get('daily_limit', 1)):
        return False, '已达到本地每日试炼限额'
    if float(record.get('next_at') or 0) > now:
        return False, '尚未到试炼时间'
    from . import cave_treasure_runtime as cave
    if cave._public_entry_lock(identity_id).locked():
        return False, '身份洞府入口忙'
    try:
        _trial_tool_root()
    except (OSError, RuntimeError) as error:
        return False, str(error)
    current = asyncio.current_task()
    _TASK = current
    job_id = uuid.uuid4().hex
    _STATUS.clear()
    _STATUS.update(job_id=job_id, identity_id=identity_id, arena=arena,
                   phase='preparing', started_at=now, scheduled=True)
    try:
        await _run(owner, arena, False, job_id, scheduled_config=config)
    finally:
        if _TASK is current:
            _TASK = None
    record = trial._records().get(str(identity_id), {})
    ok = record.get('phase') in {'idle', 'done'} and snapshot().get('phase') != 'failed'
    return ok, '本次浏览器试炼已结束' if ok else '浏览器试炼待核对'


async def _run(owner, arena, practice_only, job_id, *, scheduled_config=None):
    from . import cave_treasure_runtime as cave
    identity_id = owner.identity_id
    _STATUS.update(job_id=job_id, identity_id=identity_id, arena=arena)
    lock = cave._public_entry_lock(identity_id)
    record = trial._records().get(str(identity_id))
    if record is None:
        record = {'phase': 'idle', 'identity_id': identity_id}
        trial._records()[str(identity_id)] = record
    previous_phase = record.get('phase', 'idle')
    previous_attempt = _previous_attempt(record)
    scheduled = scheduled_config is not None
    tool_root = _trial_tool_root()
    started_at = time.time()
    entry_url = trial._entry_url()
    process = None
    launch_requested = False
    reserved = False
    url_file = None
    directory = tool_root / '.runtime' / str(identity_id) / job_id
    network = {}
    network_loaded = False

    def allowed():
        return (owner.is_current() and is_cave_public_identity_available(identity_id) and get_global_enabled()
                and not get_activity_block_reason(identity_id, include_changfeng=False)
                and cave._public_entry_allowed() and trial._entry_url() == entry_url
                and (not scheduled or trial.get_expedition_config().get('changfeng', {}) == scheduled_config)
                and trial._records().get(str(identity_id)) is record and record.get('phase') == 'browser_trial')

    async def stop_process():
        if process is not None and process.returncode is None:
            process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()

    try:
        async with lock:
            if not owner.is_current() or get_activity_block_reason(identity_id) or not get_global_enabled():
                raise RuntimeError('identity_became_busy')
            if record.get('phase', 'idle') not in {'idle', 'done'}:
                raise RuntimeError('trial_requires_review')
            if scheduled:
                used = int(record.get('daily_used') or 0) if record.get('day') == trial._day(started_at) else 0
                if used >= int(scheduled_config.get('daily_limit', 1)):
                    raise RuntimeError('trial_local_daily_limit')
                record.update(execution_engine='browser-m19', started_at=started_at, arena=arena,
                              operation_id='browser-' + job_id)
            record['phase'] = 'browser_trial'
            reserved = True
            record['browser_trial'] = snapshot()
            trial._save()
            token, webview_url, error = cave._parse_public_cave_entry_url(entry_url)
            if error:
                raise RuntimeError('entry_unavailable')
            launch_url = await request_cave_treasure_miniapp_init_data(
                identity_id, token=token, webview_url=webview_url, operation_check=allowed, return_webview_url=True)
            fields = parse_miniapp_init_data(extract_miniapp_init_data_from_url(launch_url))
            user = fields.get('user')
            user = json.loads(user) if isinstance(user, str) else user
            if not isinstance(user, dict) or int(user.get('id') or 0) != owner.account_id or not allowed():
                raise RuntimeError('webview_account_mismatch')
            directory.mkdir(parents=True, mode=0o700)
            url_file = directory / 'launch.url'
            with os.fdopen(os.open(url_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                stream.write(launch_url)
            log_file = directory / 'runs.jsonl'
            launcher = Path(__file__).resolve().parents[2] / 'tools/changfeng_trial_browser.py'
            command = [str(tool_root / '.venv/bin/python'), str(launcher), '--tool-root', str(tool_root),
                       '--arena', arena, '--identity', str(identity_id), '--expected-player-id', str(identity_id),
                       '--profile-dir', str(directory.parent / 'profile'), '--url-file', str(url_file),
                       '--log', str(log_file), '--go', '--runs', '1', '--tier', 'best', '--retries', '1',
                       '--max-run-seconds', '1500', '--wait-login', '60']
            if practice_only:
                command.append('--practice-only')
            _STATUS.update(phase='running', evidence_dir=str(directory))
            record['browser_trial'] = snapshot()
            trial._save()
            with (directory / 'runner.log').open('w') as output:
                launch_requested = True
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    *command, stdout=output, stderr=asyncio.subprocess.STDOUT, start_new_session=True))
                try:
                    process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # Recover the exact child before unwinding so cancellation
                    # cannot leave a newly created browser process unowned.
                    process = await spawn
                    raise
                waiter = asyncio.create_task(process.wait())
                # Keep the runner's normal 25-minute fight budget plus scene-loading time.
                # CPU rendering can make a valid one-game run much slower than the simulator.
                deadline = time.monotonic() + 2100
                while process.returncode is None:
                    if not allowed() or time.monotonic() >= deadline:
                        await stop_process()
                        break
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), 5)
                    except TimeoutError:
                        pass
            network_path = log_file.with_suffix('.network.json')
            if network_path.is_file():
                network = json.loads(network_path.read_text())
                network_loaded = True
            if any(row.get('playerId') is not None and str(row['playerId']) != str(identity_id)
                   for field in ('requests', 'receipts') for row in network.get(field, [])):
                raise RuntimeError('trial_browser_player_mismatch')
            facts = summarize_network(network)
            results = [json.loads(line) for line in log_file.read_text().splitlines()] if log_file.is_file() else []
            _STATUS.update(phase='done' if facts['terminal'] else 'stopped', returncode=process.returncode,
                           finished_at=time.time(), kind=(results[-1].get('kind') if results else 'not_started'),
                           rewarded=facts['rewarded'], server_closed=facts['closed'],
                           exhausted=facts['exhausted'], strategy_sha256=TRIAL_STRATEGY_SHA256,
                           server_cleared=facts['cleared'], run_id=facts['run_id'], tier=facts['tier'],
                           gains=facts['gains'], cultivation=facts['cultivation'],
                           server_started=facts['started'], no_rewards=facts['no_rewards'],
                           no_start_verified=facts['no_start_verified'], unknown_request=facts['unknown_request'],
                           confirmed_wave=facts['confirmed_wave'],
                           server_result=(facts['terminal'] or {}).get('result'))
            if scheduled and (facts['started'] or facts['unknown_request'] or not facts['no_start_verified']):
                _archive_previous_attempt(record, previous_attempt, facts['run_id'])
            elif scheduled:
                _restore_previous_metadata(record, previous_attempt)
            if facts['rewarded']:
                day = trial._day(started_at)
                record['daily_used'] = (int(record.get('daily_used') or 0) if record.get('day') == day else 0) + 1
                record['day'] = day
            if scheduled and facts['run_id']:
                record['run_id'] = facts['run_id']
            safely_closed = (not facts['unknown_request'] and (
                facts['terminal'] or facts['closed'] or (network_loaded and facts['no_start_verified'])))
            record['phase'] = previous_phase if safely_closed else 'held'
            if scheduled:
                if facts['started'] and not (facts['rewarded'] or facts['no_rewards']):
                    record['phase'] = 'held'
                record['next_at'] = (trial._next_day(time.time(), identity_id) if facts['exhausted'] or facts['rewarded'] or facts['no_rewards']
                                     else time.time() + 1800)
                record['exhausted'] = facts['exhausted'] or facts['no_rewards']
                if record['phase'] != 'held':
                    record['phase'] = 'done'
                    record['last_error'] = None
            if record['phase'] == 'held':
                record['last_error'] = '浏览器试跑已发开局但结果待核对'
    except asyncio.CancelledError:
        _STATUS.update(phase='interrupted')
        if reserved:
            if scheduled and launch_requested:
                _archive_previous_attempt(record, previous_attempt, snapshot().get('run_id'))
            record['phase'] = 'held' if launch_requested else previous_phase
        raise
    except Exception as error:
        _STATUS.update(phase='failed', error=type(error).__name__, finished_at=time.time())
        if record.get('phase') == 'browser_trial':
            if scheduled and launch_requested:
                _archive_previous_attempt(record, previous_attempt, snapshot().get('run_id'))
            record['phase'] = 'held' if launch_requested else previous_phase
    finally:
        await stop_process()
        if url_file:
            url_file.unlink(missing_ok=True)
        if reserved and trial._records().get(str(identity_id)) is record:
            record['browser_trial'] = snapshot()
            if scheduled:
                _sync_main_result(record, record['browser_trial'])
            trial._save()
    if trial._records().get(str(identity_id)) is record:
        await _notify_result(record)
