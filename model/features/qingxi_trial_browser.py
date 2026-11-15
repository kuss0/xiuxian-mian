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
from ..runtime import track_background_task
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
    receipts = network.get('receipts') or []
    rewarded = any(row.get('trial', {}).get('run', {}).get('rewarded') is True for row in receipts)
    terminal = next((row['trial'] for row in reversed(receipts)
                     if row.get('ok') is True and isinstance(row.get('trial', {}).get('result'), dict)
                     and row['trial']['result'].get('grade')), None)
    started = any(row.get('action') in {'start', 'checkpoint', 'end'} for row in network.get('requests') or [])
    return {'started': started, 'rewarded': rewarded, 'terminal': terminal}


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
                       '--max-run-seconds', '900', '--wait-login', '60']
            if practice_only:
                command.append('--practice-only')
            _STATUS.update(phase='running', evidence_dir=str(directory))
            record['browser_trial'] = snapshot()
            trial._save()
            with (directory / 'runner.log').open('w') as output:
                process = await asyncio.create_subprocess_exec(*command, stdout=output, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
                waiter = asyncio.create_task(process.wait())
                deadline = time.monotonic() + 1200
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
                           rewarded=facts['rewarded'], server_result=(facts['terminal'] or {}).get('result'))
            if facts['rewarded']:
                day = trial._day(time.time())
                record['daily_used'] = (int(record.get('daily_used') or 0) if record.get('day') == day else 0) + 1
                record['day'] = day
            record['phase'] = previous_phase if facts['terminal'] or (network_loaded and not facts['started']) else 'held'
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
