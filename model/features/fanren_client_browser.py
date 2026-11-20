"""Single-account native browser actions with durable ownership and real receipts."""

import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

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


async def action(payload):
    global _TASK
    kind=payload.get('action','status')
    if kind=='status':return True,'客户端浏览器状态',{'client_browser':copy.deepcopy(_STATUS)}
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
    process=None;launched=False;url_file=None;lock=cave._public_entry_lock(identity_id)
    record={'phase':'loading','job_id':job_id,'scenario':scenario,'started_at':time.time(),'identity_id':identity_id}
    def allowed():
        return owner.is_current() and get_global_enabled() and is_cave_public_identity_available(identity_id) and records().get(str(identity_id)) is record
    async def stop():
        if process is not None and process.returncode is None:
            process.send_signal(signal.SIGTERM)
            try:await asyncio.wait_for(process.wait(),15)
            except TimeoutError:os.killpg(process.pid,signal.SIGKILL);await process.wait()
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
                process=await asyncio.create_subprocess_exec(*command,stdout=log,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
                launched=True;deadline=time.monotonic()+1800;waiter=asyncio.create_task(process.wait())
                while process.returncode is None:
                    if not allowed() or time.monotonic()>deadline:await stop();break
                    try:await asyncio.wait_for(asyncio.shield(waiter),5)
                    except TimeoutError:pass
            value=json.loads(result.read_text()) if result.is_file() else {}
            record.update(phase='done' if outcome_closed(value,identity_id) else 'held',finished_at=time.time(),
                          result={k:value.get(k) for k in ['identity_verified','scenario_result','server_terminal','unknown','pending','browser_closed']})
        finally:
            # Drain the worker before another scene may acquire this account.
            await stop()
            lock.release()
    except BaseException as error:
        record.update(phase='held' if launched else 'failed',error=type(error).__name__,finished_at=time.time())
        if isinstance(error,asyncio.CancelledError):record['stop_requested']=True
    finally:
        await stop()
        if url_file is not None:url_file.unlink(missing_ok=True)
        # The task owns only its own record; never replace a later controller's state.
        if records().get(str(identity_id)) is record:persist()
        _STATUS.update(copy.deepcopy(record))
