"""Single-account native browser actions with durable ownership and real receipts."""

import asyncio
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid
from dataclasses import dataclass

from ..activity_occupancy import get_activity_block_reason
from ..config import STATE_DIR
from ..persistence import mark_dirty, save_state
from ..runtime import track_background_task
from ..state import get_expedition_run_state, get_global_enabled, is_cave_public_identity_available
from ..webapp_core import extract_miniapp_init_data_from_url, parse_miniapp_init_data
from . import cave_treasure_runtime as cave
from .cave_treasure_miniapp import request_cave_treasure_miniapp_init_data
from .changfeng_trial import _entry_url
from .miniapp_common import MiniAppIdentityOwner

ROOT=Path(__file__).resolve().parents[2]
SCENARIOS={'inspect','walk','npc','fishing','combat'}
_TASK=None
_STATUS={}
_PROCESS_OWNER_ENV='XIUXIAN_FANREN_PROCESS_OWNER'


@dataclass
class _OwnedProcess:
    pid: int
    starttime: int
    pidfd: int


def _proc_stat(pid):
    try:
        raw=(Path('/proc')/str(pid)/'stat').read_text()
    except (FileNotFoundError,ProcessLookupError):
        return None
    try:
        fields=raw[raw.rfind(')')+2:].split()
        if len(fields)<20:
            return None
        return {
            'state':fields[0],
            'pgrp':int(fields[2]),
            'session':int(fields[3]),
            'starttime':int(fields[19]),
        }
    except (IndexError,ValueError):
        return None


def _proc_has_owner(pid,owner_token):
    marker=f'{_PROCESS_OWNER_ENV}={owner_token}'.encode()
    try:
        return marker in (Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0')
    except (FileNotFoundError,ProcessLookupError):
        return False
    except (PermissionError,OSError) as error:
        raise RuntimeError('native_process_owner_unreadable') from error


def _open_owned_process(pid,owner_token):
    before=_proc_stat(pid)
    if before is None or before['state'] in {'Z','X'} or not _proc_has_owner(pid,owner_token):
        return None
    try:
        pidfd=os.pidfd_open(pid)
    except (AttributeError,NotImplementedError,OSError) as error:
        if isinstance(error,OSError) and error.errno==errno.ESRCH:
            return None
        raise RuntimeError('native_process_pidfd_unavailable') from error
    transferred=False
    try:
        after=_proc_stat(pid)
        if (after is None or after['state'] in {'Z','X'} or after['starttime']!=before['starttime']
                or not _proc_has_owner(pid,owner_token)):
            return None
        transferred=True
        return _OwnedProcess(pid,after['starttime'],pidfd)
    finally:
        if not transferred:
            try:os.close(pidfd)
            except OSError:pass


def _close_owned_processes(processes):
    for process in processes:
        try:os.close(process.pidfd)
        except OSError:pass


def _owned_processes(owner_token,group_id):
    if not owner_token:
        raise RuntimeError('native_process_owner_missing')
    owned=[]
    try:
        entries=list(Path('/proc').iterdir())
    except OSError as error:
        raise RuntimeError('native_process_proc_unreadable') from error
    try:
        for entry in entries:
            if not entry.name.isdigit():continue
            pid=int(entry.name)
            stat=_proc_stat(pid)
            if stat is None or stat['state'] in {'Z','X'}:continue
            # The token is deliberately propagated to detached Chromium
            # sessions; pgrp/session alone cannot see those descendants.
            if stat['pgrp']!=group_id and stat['session']!=group_id and not _proc_has_owner(pid,owner_token):
                continue
            member=_open_owned_process(pid,owner_token)
            if member is not None:owned.append(member)
    except BaseException:
        _close_owned_processes(owned)
        raise
    return owned


def _signal_owned_processes(processes,sig):
    if not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('native_process_pidfd_unavailable')
    for process in processes:
        try:signal.pidfd_send_signal(process.pidfd,sig)
        except ProcessLookupError:pass
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise RuntimeError('native_process_signal_failed') from error


def records():
    return get_expedition_run_state().setdefault('fanren_browser',{})


def persist():
    mark_dirty()
    if save_state() is False:raise RuntimeError('browser_state_save_failed')


def outcome_closed(value,identity_id):
    if not isinstance(value,dict) or value.get('identity_id')!=identity_id or value.get('identity_verified') is not True:
        return False
    intents=value.get('intents')
    if not isinstance(intents,list) or value.get('pending') or value.get('unknown') or value.get('browser_closed') is not True:
        return False
    if not intents:return True
    receipts=value.get('receipts')
    if not isinstance(receipts,list):return False
    confirmed={r.get('key') for r in receipts if isinstance(r,dict) and r.get('ok') is True and r.get('player_id')==identity_id}
    if any(not isinstance(row,dict) or not row.get('key') or row.get('player_id')!=identity_id or row['key'] not in confirmed for row in intents):
        return False
    # UI success and acknowledged checkpoints cannot close a consumed game run.
    return value.get('server_terminal') is True


def navigation_failed_before_entry(value,identity_id):
    return (isinstance(value,dict) and value.get('identity_id')==identity_id
            and value.get('stage')=='loading_page' and value.get('browser_closed') is True
            and value.get('identity_verified') is False and value.get('intents')==[]
            and value.get('pending')==[] and value.get('unknown') is False)


async def _stop_native_process_group(process,owner_token,grace_sec=15,kill_wait_sec=5):
    group_id=process.pid
    if group_id<=1 or group_id==os.getpgrp():raise RuntimeError('native_process_group_not_owned')
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('native_process_pidfd_unavailable')
    def send(sig):
        members=_owned_processes(owner_token,group_id)
        try:_signal_owned_processes(members,sig)
        finally:_close_owned_processes(members)
    async def wait_owned(timeout):
        deadline=time.monotonic()+timeout
        while True:
            members=_owned_processes(owner_token,group_id)
            alive=bool(members)
            _close_owned_processes(members)
            if not alive:return True
            if time.monotonic()>=deadline:return False
            await asyncio.sleep(.05)
    members=_owned_processes(owner_token,group_id)
    try:
        if members:send(signal.SIGTERM)
    finally:_close_owned_processes(members)
    if members and not await wait_owned(grace_sec):
        members=_owned_processes(owner_token,group_id)
        try:
            if members:send(signal.SIGKILL)
        finally:_close_owned_processes(members)
        if members and not await wait_owned(kill_wait_sec):
            raise RuntimeError('native_process_group_cleanup_timeout')
    if process.returncode is None:await asyncio.wait_for(process.wait(),kill_wait_sec)


async def _await_uncancelled(task):
    cancelled=False
    while not task.done():
        try:await asyncio.shield(task)
        except asyncio.CancelledError:cancelled=True
    return task.result(),cancelled


async def action(payload):
    global _TASK
    kind=payload.get('action','status')
    if kind=='status':return True,'客户端浏览器状态',{'client_browser':copy.deepcopy(_STATUS)}
    if kind=='review_closed':
        try:identity_id=int(payload.get('send_as_id') or 0)
        except (TypeError,ValueError):return False,'身份无效',{}
        if _TASK is not None and not _TASK.done():return False,'浏览器仍在运行',{}
        record=records().get(str(identity_id),{})
        if record.get('cleanup_error'):return False,'浏览器进程组仍待核对',{}
        directory=Path(STATE_DIR)/'fanren_browser'/str(identity_id)/str(record.get('job_id') or '')
        if record.get('phase')!='held' or str(directory)!=record.get('evidence_dir') or not (directory/'result.json').is_file():
            return False,'缺少本轮浏览器结束证据',{}
        value=json.loads((directory/'result.json').read_text())
        if not outcome_closed(value,identity_id):return False,'原操作或浏览器仍待核对',{}
        record.update(phase='done',reviewed_at=time.time(),review_reason='verified_closed_worker_receipts')
        persist();_STATUS.update(copy.deepcopy(record))
        return True,'已按本轮浏览器与原操作回执完成核对',{'client_browser':copy.deepcopy(_STATUS)}
    if kind=='review_unstarted_inspect':
        try:identity_id=int(payload.get('send_as_id') or 0)
        except (TypeError,ValueError):return False,'身份无效',{}
        if _TASK is not None and not _TASK.done():return False,'浏览器仍在运行',{}
        record=records().get(str(identity_id),{})
        if record.get('cleanup_error'):return False,'浏览器进程组仍待核对',{}
        directory=Path(record.get('evidence_dir') or '')
        expected=Path(STATE_DIR)/'fanren_browser'/str(identity_id)/str(record.get('job_id') or '')
        if record.get('phase')=='held' and record.get('scenario')=='inspect' and directory==expected and (directory/'result.json').is_file():
            value=json.loads((directory/'result.json').read_text())
            if navigation_failed_before_entry(value,identity_id):
                record.update(phase='failed',reviewed_at=time.time(),review_reason='navigation_failed_before_authenticated_entry')
                persist();_STATUS.update(copy.deepcopy(record))
                return True,'已核对为登录场景前的导航失败',{'client_browser':copy.deepcopy(_STATUS)}
        if (record.get('phase')!='held' or record.get('scenario')!='inspect' or directory!=expected
                or (directory/'result.json').exists() or (directory.parent/'profile').exists()):
            return False,'缺少开浏览器前失败的精确证据',{}
        log=directory/'runner.log'
        text=log.read_text() if log.is_file() else ''
        if "raise RuntimeError('invalid_launch_origin')" not in text or not text.rstrip().endswith('RuntimeError: invalid_launch_origin'):
            return False,'启动失败证据不匹配',{}
        record.update(phase='failed',reviewed_at=time.time(),review_reason='launcher_rejected_url_before_browser_exists')
        persist();_STATUS.update(copy.deepcopy(record))
        return True,'已核对为浏览器启动前的入口校验失败',{'client_browser':copy.deepcopy(_STATUS)}
    if kind=='stop':
        if _TASK is not None and not _TASK.done():_TASK.cancel()
        return True,'已请求停止，等待浏览器收尾',{'client_browser':copy.deepcopy(_STATUS)}
    if kind!='start' or payload.get('scenario') not in SCENARIOS:return False,'需要有效的浏览器操作类型',{}
    if _TASK is not None and not _TASK.done():return False,'已有客户端浏览器执行中',{}
    try:identity_id=int(payload.get('send_as_id') or 0)
    except (TypeError,ValueError,OverflowError):return False,'身份无效',{}
    owner=MiniAppIdentityOwner.capture(identity_id)
    if owner is None or owner.account_id!=identity_id or not is_cave_public_identity_available(identity_id):
        return False,'客户端操作仅支持可用的本人账号',{}
    if not get_global_enabled():return False,'全局已暂停',{}
    reason=get_activity_block_reason(identity_id)
    if reason:return False,reason,{}
    if importlib.util.find_spec('playwright') is None:return False,'浏览器依赖未安装',{}
    if cave._public_entry_lock(identity_id).locked():return False,'身份洞府入口忙',{}
    job_id=uuid.uuid4().hex
    _STATUS.clear();_STATUS.update(job_id=job_id,identity_id=identity_id,scenario=payload['scenario'],phase='preparing',started_at=time.time())
    _TASK=track_background_task(asyncio.create_task(run(owner,payload['scenario'],job_id)))
    return True,'已安排单次原生浏览器操作',{'client_browser':copy.deepcopy(_STATUS)}


async def run(owner,scenario,job_id):
    identity_id=owner.identity_id
    directory=Path(STATE_DIR)/'fanren_browser'/str(identity_id)/job_id
    process=None;spawn_task=None;launched=False;url_file=None;cleanup_task=None;lock=cave._public_entry_lock(identity_id)
    owner_token=uuid.uuid4().hex
    record={'phase':'loading','job_id':job_id,'scenario':scenario,'started_at':time.time(),'identity_id':identity_id}
    def allowed():
        return owner.is_current() and get_global_enabled() and is_cave_public_identity_available(identity_id) and records().get(str(identity_id)) is record
    async def stop():
        nonlocal cleanup_task,process,launched
        cancelled=False
        if spawn_task is not None and process is None:
            try:
                process,was_cancelled=await _await_uncancelled(spawn_task)
                launched=True;cancelled|=was_cancelled
            except Exception:
                return
        if process is None:return
        if cleanup_task is None:cleanup_task=asyncio.create_task(_stop_native_process_group(process,owner_token))
        while not cleanup_task.done():
            try:await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:cancelled=True
        cleanup_task.result()
        if cancelled:raise asyncio.CancelledError
    try:
        await lock.acquire()
        try:
            if not owner.is_current() or get_activity_block_reason(identity_id) or not get_global_enabled():raise RuntimeError('identity_became_busy')
            records()[str(identity_id)]=record;persist()
            token,webview,error=cave._parse_public_cave_entry_url(_entry_url())
            if error:raise RuntimeError('entry_unavailable')
            url=await request_cave_treasure_miniapp_init_data(identity_id,token=token,webview_url=webview,operation_check=allowed,return_webview_url=True)
            user=parse_miniapp_init_data(extract_miniapp_init_data_from_url(url)).get('user')
            user=json.loads(user) if isinstance(user,str) else user
            if not isinstance(user,dict) or int(user.get('id') or 0)!=owner.account_id or not allowed():raise RuntimeError('webview_account_mismatch')
            directory.mkdir(parents=True,mode=0o700)
            url_file=directory/'launch.url'
            with os.fdopen(os.open(url_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:stream.write(url)
            result=directory/'result.json'
            command=['xvfb-run','-a','-s','-screen 0 1024x768x24 -nolisten tcp',sys.executable,
                     str(ROOT/'tools/fanren_client_browser.py'),'--url-file',str(url_file),'--expected-player-id',str(identity_id),
                     '--scenario',scenario,'--result',str(result),'--profile-dir',str(directory.parent/'profile')]
            record.update(phase='running',evidence_dir=str(directory));_STATUS.update(record);persist()
            with (directory/'runner.log').open('w') as log:
                env={**os.environ,_PROCESS_OWNER_ENV:owner_token}
                spawn_task=asyncio.create_task(asyncio.create_subprocess_exec(
                    *command,stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True,env=env))
                process=await asyncio.shield(spawn_task)
                launched=True;deadline=time.monotonic()+1800;waiter=asyncio.create_task(process.wait())
                while process.returncode is None:
                    if not allowed() or time.monotonic()>deadline:await stop();break
                    try:await asyncio.wait_for(asyncio.shield(waiter),5)
                    except TimeoutError:pass
            value=json.loads(result.read_text()) if result.is_file() else {}
            phase='done' if outcome_closed(value,identity_id) else 'failed' if navigation_failed_before_entry(value,identity_id) else 'held'
            record.update(phase=phase,finished_at=time.time(),
                          result={k:value.get(k) for k in ['identity_verified','scenario_result','server_terminal','unknown','pending','browser_closed']})
        finally:
            # Drain the worker before another scene may acquire this account.
            try:await stop()
            finally:lock.release()
    except BaseException as error:
        record.update(phase='held' if launched else 'failed',error=type(error).__name__,finished_at=time.time())
        if isinstance(error,asyncio.CancelledError):record['stop_requested']=True
    finally:
        try:await stop()
        except BaseException as error:
            record.update(phase='held' if launched else 'failed',cleanup_error=type(error).__name__)
        if url_file is not None:url_file.unlink(missing_ok=True)
        result_file=directory/'result.json'
        if record.get('stop_requested') and not record.get('cleanup_error') and result_file.is_file():
            try:value=json.loads(result_file.read_text())
            except (OSError,ValueError):value={}
            if outcome_closed(value,identity_id):record.update(phase='done',review_reason='cancelled_worker_closed_with_verified_receipts')
        # The task owns only its own record; never replace a later controller's state.
        if records().get(str(identity_id)) is record:persist()
        _STATUS.update(copy.deepcopy(record))
