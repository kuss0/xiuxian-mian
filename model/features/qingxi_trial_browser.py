"""Manual, one-game browser canary using the already registered account client."""
import asyncio
import copy
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
_TASK = None
_STATUS = {}


def snapshot():
    return copy.deepcopy(_STATUS)


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
    terminal = next((row for row in reversed(successful)
                     if isinstance(row.get('result'), dict) and row['result'].get('grade')), None)
    started = any(row.get('action') in {'start', 'checkpoint', 'end'} for row in network.get('requests') or [])
    # A pre-start status with activeRun=null cannot confirm an ambiguous start ended.
    latest_status = (successful[-1].get('status') or {}) if successful else {}
    closed = bool(started and run_id and 'activeRun' in latest_status and latest_status['activeRun'] is None)
    gains, cultivation, seen = {}, 0, set()
    for row in successful:
        current = row.get('run') or {}
        wave = current.get('cleared')
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
    title = f"⚔️ 青溪试炼·{arena}" + (f"·第{tier}重" if type(tier) is int and tier > 0 else '')
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
    if not (TOOL_ROOT / '.venv/bin/python').is_file() or not (TOOL_ROOT / 'local-qingxi/run_dungeon.py').is_file():
        return False, '本地试炼工具未安装', {}
    from . import cave_treasure_runtime as cave
    if cave._public_entry_lock(identity_id).locked():
        return False, '身份洞府入口忙', {}
    job_id = uuid.uuid4().hex
    _STATUS.clear()
    _STATUS.update(job_id=job_id, identity_id=identity_id, arena=arena, phase='preparing', started_at=time.time())
    _TASK = track_background_task(asyncio.create_task(_run(owner, arena, payload.get('practice_only', False), job_id)))
    return True, '已安排单次浏览器试跑，不启用自动化', {'browser_trial': snapshot()}


async def _run(owner, arena, practice_only, job_id):
    from . import cave_treasure_runtime as cave
    identity_id = owner.identity_id
    _STATUS.update(job_id=job_id, identity_id=identity_id, arena=arena)
    lock = cave._public_entry_lock(identity_id)
    record = trial._records().get(str(identity_id))
    if record is None:
        record = {'phase': 'idle', 'identity_id': identity_id}
        trial._records()[str(identity_id)] = record
    previous_phase = record.get('phase', 'idle')
    process = None
    url_file = None
    directory = TOOL_ROOT / '.runtime' / str(identity_id) / job_id
    network = {}
    network_loaded = False

    def allowed():
        return (owner.is_current() and is_cave_public_identity_available(identity_id) and get_global_enabled()
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
            record['phase'] = 'browser_trial'
            record['browser_trial'] = snapshot()
            trial._save()
            entry_url = trial._entry_url()
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
            command = [str(TOOL_ROOT / '.venv/bin/python'), str(TOOL_ROOT / 'local-qingxi/run_dungeon.py'),
                       arena, '--identity', str(identity_id), '--expected-player-id', str(identity_id),
                       '--profile-dir', str(directory.parent / 'profile'), '--url-file', str(url_file),
                       '--log', str(log_file), '--go', '--runs', '1', '--tier', 'best', '--retries', '1',
                       '--max-run-seconds', '1500', '--wait-login', '60']
            if practice_only:
                command.append('--practice-only')
            _STATUS.update(phase='running', evidence_dir=str(directory))
            record['browser_trial'] = snapshot()
            trial._save()
            with (directory / 'runner.log').open('w') as output:
                process = await asyncio.create_subprocess_exec(*command, stdout=output, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
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
            facts = summarize_network(network)
            results = [json.loads(line) for line in log_file.read_text().splitlines()] if log_file.is_file() else []
            _STATUS.update(phase='done' if facts['terminal'] else 'stopped', returncode=process.returncode,
                           finished_at=time.time(), kind=(results[-1].get('kind') if results else 'not_started'),
                           rewarded=facts['rewarded'], server_closed=facts['closed'],
                           server_cleared=facts['cleared'], run_id=facts['run_id'], tier=facts['tier'],
                           gains=facts['gains'], cultivation=facts['cultivation'],
                           server_result=(facts['terminal'] or {}).get('result'))
            if facts['rewarded']:
                day = trial._day(time.time())
                record['daily_used'] = (int(record.get('daily_used') or 0) if record.get('day') == day else 0) + 1
                record['day'] = day
            record['phase'] = previous_phase if facts['terminal'] or facts['closed'] or (network_loaded and not facts['started']) else 'held'
            if record['phase'] == 'held':
                record['last_error'] = '浏览器试跑已发开局但结果待核对'
    except asyncio.CancelledError:
        _STATUS.update(phase='interrupted')
        record['phase'] = 'held' if process is not None else previous_phase
        raise
    except Exception as error:
        _STATUS.update(phase='failed', error=type(error).__name__, finished_at=time.time())
        if record.get('phase') == 'browser_trial':
            record['phase'] = 'held' if process is not None else previous_phase
    finally:
        await stop_process()
        if url_file:
            url_file.unlink(missing_ok=True)
        if trial._records().get(str(identity_id)) is record:
            record['browser_trial'] = snapshot()
            trial._save()
    if trial._records().get(str(identity_id)) is record:
        await _notify_result(record)
