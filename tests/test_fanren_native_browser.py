import asyncio
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from urllib.parse import urlencode

import pytest

from model.features import fanren_client_browser as runtime
from tools.fanren_browser_support.asset_cache import VerifiedAssetCache
from tools.fanren_browser_support.native_driver import NativeInputDriver

spec=importlib.util.spec_from_file_location('fanren_worker',Path(__file__).parents[1]/'tools/fanren_client_browser.py')
worker=importlib.util.module_from_spec(spec)
sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
spec.loader.exec_module(worker)


@pytest.fixture
def cache(tmp_path):
    content=b'verified-current-asset'
    digest=hashlib.sha256(content).hexdigest()
    (tmp_path/'files').mkdir()
    (tmp_path/'files'/digest).write_bytes(content)
    entries={'/staticweb/a.js':{'path':'/staticweb/a.js','sha256':digest,'bytes':len(content),
                               'mime':'application/javascript','selectors':['v=7','']}}
    (tmp_path/'verified-assets.json').write_text(json.dumps(entries))
    return VerifiedAssetCache(tmp_path),entries['/staticweb/a.js'],content


@pytest.mark.parametrize('url,method',[
    ('https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/start','POST'),
    ('https://asc.aiopenai.app/miniapp/xianxia-dwelling','GET'),
    ('https://oauth.telegram.org/staticweb/a.js?v=7','GET'),
    ('https://asc.aiopenai.app/staticweb/a.js?v=8','GET'),
    ('https://asc.aiopenai.app/staticweb/a.js?v=7&retry=1','GET'),
    ('https://asc.aiopenai.app/staticweb/a.js','GET'),
    ('https://asc.aiopenai.app/staticweb/%2e%2e/a.js?v=7','GET'),
])
def test_cache_never_replaces_dynamic_auth_or_unverified_versions(cache,url,method):
    store,_,_=cache
    assert store.choose(url,method)[0] is None


def test_current_server_digest_overrides_old_version_selector(cache):
    store,entry,_=cache
    store.set_grant({'expiresAt':time.time()*1000+10000,'assets':{'/staticweb/a.js':'0'*64}})
    assert store.choose('https://asc.aiopenai.app/staticweb/a.js?v=7')==(None,'server_version_changed')
    store.set_grant({'expiresAt':time.time()*1000+10000,'assets':{'/staticweb/a.js':entry['sha256']}})
    assert store.choose('https://asc.aiopenai.app/staticweb/a.js')[0]==entry


def test_corruption_and_range_do_not_serve_invalid_assets(cache):
    store,entry,content=cache
    status,headers,body=store.read(entry,'GET','bytes=2-6')
    assert status==206 and body==content[2:7] and headers['Content-Range']==f'bytes 2-6/{len(content)}'
    target=store.bundle/'files'/entry['sha256']
    target.write_bytes(b'x'*len(content))
    assert store.read(entry,'GET',None) is None


@pytest.mark.parametrize('change',[
    {'identity_verified':False},{'identity_id':99},{'browser_closed':False},
    {'unknown':True},{'pending':['cast']},{'server_terminal':False},
    {'receipts':[{'key':'op','ok':True,'player_id':99}]},
])
def test_ui_or_checkpoint_success_cannot_release_uncertain_operations(change):
    value={'identity_verified':True,'identity_id':42,'browser_closed':True,'unknown':False,'pending':[],
           'server_terminal':True,'intents':[{'key':'op','player_id':42}],
           'receipts':[{'key':'op','ok':True,'player_id':42}]}
    assert runtime.outcome_closed(value,42)
    value.update(change)
    assert not runtime.outcome_closed(value,42)


def test_terminal_receipt_must_belong_to_the_started_fish():
    intents=[{'family':'fishing','action':'cast'}]
    receipts=[{'ok':True,'action':'cast','session_id':'owned'},
              {'ok':True,'action':'fight','session_id':'other','terminal':True}]
    assert not worker.server_terminal(intents,receipts,'fishing')
    receipts[1]['session_id']='owned'
    assert worker.server_terminal(intents,receipts,'fishing')


def test_gather_start_receipt_does_not_close_collection():
    intents=[{'family':'npc','action':'gather-start'}]
    receipts=[{'ok':True,'family':'npc','action':'gather-start','gather_id':'owned'}]
    assert not worker.server_terminal(intents,receipts,'npc')
    receipts.append({'ok':True,'family':'npc','action':'gather-finish','gather_id':'other'})
    assert not worker.server_terminal(intents,receipts,'npc')
    receipts[-1]['gather_id']='owned'
    assert worker.server_terminal(intents,receipts,'npc')


def test_wrong_player_response_is_not_a_receipt():
    intent=worker.request_fact('https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/fishing/cast',
                             {'playerId':'42','operationId':'actual-client-generated'},42)
    assert intent['identity_matches']
    assert not worker.response_fact(intent,{'ok':True,'account':{'playerId':99}},200)['ok']
    assert not worker.response_fact(intent,'Cloudflare HTML',521)['ok']


def test_original_fishing_proof_is_observed_without_requiring_a_new_operation_id():
    body={'playerId':42,'sessionId':'real-session','fishingProof':{'durationMs':5200,'events':[{'t':100,'holding':True}]}}
    original=json.dumps(body)
    fact=worker.request_fact('https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/fishing/fight',body,42)
    assert fact['operation_id']=='real-session:5200'
    assert json.dumps(body)==original


def test_native_launcher_preserves_webview_auth_while_normalizing_official_verify_entry():
    source='https://asc.aiopenai.app/miniapp/xianxia-verify?token=entry#tgWebAppData=signed'
    assert worker.normalize_launch_url(source)=='https://asc.aiopenai.app/miniapp/xianxia-dwelling?token=entry#tgWebAppData=signed'
    for source in ['https://evil.invalid/miniapp/xianxia-verify#tgWebAppData=signed',
                   'https://asc.aiopenai.app/miniapp/xianxia-verify',
                   'https://user@asc.aiopenai.app/miniapp/xianxia-verify#tgWebAppData=signed']:
        with pytest.raises(RuntimeError):worker.normalize_launch_url(source)


def test_only_closed_prelogin_navigation_failure_is_unstarted():
    evidence={'identity_id':42,'stage':'loading_page','browser_closed':True,'identity_verified':False,
              'intents':[],'pending':[],'unknown':False}
    assert runtime.navigation_failed_before_entry(evidence,42)
    for change in [{'stage':'scenario'},{'browser_closed':False},{'intents':[{}]},{'unknown':True},{'identity_id':99}]:
        assert not runtime.navigation_failed_before_entry({**evidence,**change},42)


def test_driver_releases_physical_keys_when_identity_changes():
    class Keyboard:
        def __init__(self):self.calls=[]
        async def down(self,key):self.calls.append(('down',key))
        async def up(self,key):self.calls.append(('up',key))
        async def press(self,key):self.calls.append(('press',key))
    class Locator:
        async def focus(self):pass
    class Page:
        def __init__(self):self.keyboard=Keyboard();self.count=0
        def locator(self,_):return Locator()
        async def evaluate(self,source,*_):
            if '.stats()' in source:return {'gait':'walk'}
            self.count+=1
            if self.count==2:raise RuntimeError('identity_changed')
            return {'ready':True,'reached':False,'distance':2,'keys':['w'],'position':[0,0,0]}
    page=Page()
    with pytest.raises(RuntimeError,match='identity_changed'):
        asyncio.run(NativeInputDriver(page).move_to(2,0))
    assert page.keyboard.calls==[('down','w'),('up','w')]


def test_native_scene_record_blocks_all_other_activity(monkeypatch):
    from model import activity_occupancy as occupancy
    monkeypatch.setattr(occupancy,'get_expedition_run_state',lambda:{'fanren_browser':{'42':{'phase':'held'}}})
    assert '客户端浏览器' in occupancy.get_activity_block_reason(42)


@pytest.mark.parametrize('repeat_cancel',[False,True])
def test_worker_cancellation_drains_under_account_lock_and_removes_launch_secret(monkeypatch,tmp_path,repeat_cancel):
    async def exercise():
        owner=SimpleNamespace(identity_id=42,account_id=42,is_current=lambda:True)
        records={};lock=asyncio.Lock();spawned=asyncio.Event();closed=asyncio.Event();signals=[]
        cleanup_started=asyncio.Event();finish_cleanup=asyncio.Event();finish_spawn=asyncio.Event()
        monkeypatch.setattr(runtime,'records',lambda:records)
        monkeypatch.setattr(runtime,'persist',Mock())
        monkeypatch.setattr(runtime,'STATE_DIR',str(tmp_path))
        monkeypatch.setattr(runtime,'get_global_enabled',lambda:True)
        monkeypatch.setattr(runtime,'is_cave_public_identity_available',lambda _id:True)
        monkeypatch.setattr(runtime,'get_activity_block_reason',lambda _id:'')
        monkeypatch.setattr(runtime.cave,'_public_entry_lock',lambda _id:lock)
        monkeypatch.setattr(runtime,'_entry_url',lambda:'fixture')
        monkeypatch.setattr(runtime.cave,'_parse_public_cave_entry_url',lambda _:('token','url',''))
        init=urlencode({'user':json.dumps({'id':42})})
        url='https://asc.aiopenai.app/miniapp/xianxia-dwelling#'+urlencode({'tgWebAppData':init})
        monkeypatch.setattr(runtime,'request_cave_treasure_miniapp_init_data',AsyncMock(return_value=url))
        process=SimpleNamespace(returncode=None,pid=123)
        async def cleanup(owned,owner_token):
            assert owned is process
            assert owner_token
            signals.append(lock.locked());cleanup_started.set()
            await finish_cleanup.wait()
            process.returncode=0;closed.set()
        async def wait():await closed.wait();return 0
        process.wait=wait
        monkeypatch.setattr(runtime,'_stop_native_process_group',cleanup)
        async def spawn(*_args,**kwargs):
            assert kwargs['start_new_session'] is True
            assert kwargs['env'][runtime._PROCESS_OWNER_ENV]
            spawned.set();await finish_spawn.wait();return process
        monkeypatch.setattr(runtime.asyncio,'create_subprocess_exec',spawn)
        task=asyncio.create_task(runtime.run(owner,'inspect','job'))
        await spawned.wait();task.cancel();finish_spawn.set();await cleanup_started.wait()
        if repeat_cancel:
            task.cancel();await asyncio.sleep(0)
        assert lock.locked() and not task.done()
        finish_cleanup.set();await task
        assert signals==[True]
        assert not lock.locked()
        assert records['42']['phase']=='held'
        assert records['42']['stop_requested']
        assert not list(tmp_path.rglob('launch.url'))
    asyncio.run(exercise())


@pytest.mark.parametrize('review',['review_closed','review_unstarted_inspect'])
def test_worker_cleanup_failure_keeps_closed_worker_held(monkeypatch,tmp_path,review):
    async def exercise():
        owner=SimpleNamespace(identity_id=42,account_id=42,is_current=lambda:True)
        records={};lock=asyncio.Lock()
        monkeypatch.setattr(runtime,'records',lambda:records)
        monkeypatch.setattr(runtime,'persist',Mock())
        monkeypatch.setattr(runtime,'STATE_DIR',str(tmp_path))
        monkeypatch.setattr(runtime,'_TASK',None)
        monkeypatch.setattr(runtime,'get_global_enabled',lambda:True)
        monkeypatch.setattr(runtime,'is_cave_public_identity_available',lambda _id:True)
        monkeypatch.setattr(runtime,'get_activity_block_reason',lambda _id:'')
        monkeypatch.setattr(runtime.cave,'_public_entry_lock',lambda _id:lock)
        monkeypatch.setattr(runtime,'_entry_url',lambda:'fixture')
        monkeypatch.setattr(runtime.cave,'_parse_public_cave_entry_url',lambda _:('token','url',''))
        init=urlencode({'user':json.dumps({'id':42})})
        url='https://asc.aiopenai.app/miniapp/xianxia-dwelling#'+urlencode({'tgWebAppData':init})
        monkeypatch.setattr(runtime,'request_cave_treasure_miniapp_init_data',AsyncMock(return_value=url))
        cleanup=AsyncMock(side_effect=RuntimeError('fixture_group_still_alive'))
        monkeypatch.setattr(runtime,'_stop_native_process_group',cleanup)
        evidence={'identity_id':42,'identity_verified':review=='review_closed','stage':'loading_page',
                  'intents':[],'pending':[],'unknown':False,'browser_closed':True}
        async def spawn(*_args,**kwargs):
            assert kwargs['start_new_session'] is True
            (tmp_path/'fanren_browser'/'42'/'job'/'result.json').write_text(json.dumps(evidence))
            return SimpleNamespace(pid=123,returncode=0,wait=AsyncMock(return_value=0))
        monkeypatch.setattr(runtime.asyncio,'create_subprocess_exec',spawn)
        await runtime.run(owner,'inspect','job')
        assert records['42']['phase']=='held'
        assert records['42']['cleanup_error']=='RuntimeError'
        assert not lock.locked() and not list(tmp_path.rglob('launch.url'))
        cleanup.assert_awaited_once()
        ok,message,_=await runtime.action({'action':review,'send_as_id':42})
        assert not ok and '进程组' in message and records['42']['phase']=='held'
    asyncio.run(exercise())


def test_pidfd_open_rechecks_birth_identity_before_signal(monkeypatch):
    stat=Mock(side_effect=[{'state':'R','starttime':11}, {'state':'R','starttime':12}])
    monkeypatch.setattr(runtime,'_proc_stat',stat)
    monkeypatch.setattr(runtime,'_proc_has_owner',lambda _pid,_token:True)
    monkeypatch.setattr(runtime.os,'pidfd_open',Mock(return_value=77))
    close=Mock();monkeypatch.setattr(runtime.os,'close',close)
    assert runtime._open_owned_process(424242,'old-token') is None
    close.assert_called_once_with(77)


@pytest.mark.parametrize('number,absent', [(errno.ESRCH,True),(errno.EPERM,False)])
def test_pidfd_open_error_is_classified_without_losing_ownership(monkeypatch,number,absent):
    monkeypatch.setattr(runtime,'_proc_stat',lambda _pid:{'state':'R','starttime':11})
    monkeypatch.setattr(runtime,'_proc_has_owner',lambda _pid,_token:True)
    monkeypatch.setattr(runtime.os,'pidfd_open',Mock(side_effect=OSError(number,'fixture')))
    if absent:
        assert runtime._open_owned_process(424242,'token') is None
    else:
        with pytest.raises(RuntimeError,match='pidfd_unavailable'):
            runtime._open_owned_process(424242,'token')


def test_pidfd_signal_targets_process_handle_not_numeric_pid(monkeypatch):
    send=Mock()
    monkeypatch.setattr(signal,'pidfd_send_signal',send)
    member=runtime._OwnedProcess(424242,11,77)
    runtime._signal_owned_processes([member],signal.SIGKILL)
    send.assert_called_once_with(77,signal.SIGKILL)


def test_cleanup_does_not_kill_reused_pid_after_term(monkeypatch):
    process=SimpleNamespace(pid=424242,returncode=0)
    old=runtime._OwnedProcess(424242,11,77)
    calls=[]
    def members(_token,_group):
        calls.append(len(calls))
        # The old member remains through TERM's grace check, then its PID is
        # reused by an unmarked process. Ownership scan must return no member.
        return [old] if len(calls)<4 else []
    signalled=[]
    monkeypatch.setattr(runtime,'_owned_processes',members)
    monkeypatch.setattr(runtime,'_signal_owned_processes',lambda rows,sig:signalled.append((rows,sig)))
    monkeypatch.setattr(runtime,'_close_owned_processes',lambda _rows:None)
    asyncio.run(runtime._stop_native_process_group(process,'old-token',grace_sec=0,kill_wait_sec=0))
    assert [sig for _rows,sig in signalled]==[signal.SIGTERM]


def test_process_group_cleanup_reports_group_surviving_kill(monkeypatch):
    member=runtime._OwnedProcess(424242,11,77)
    monkeypatch.setattr(runtime,'_owned_processes',lambda _token,_group:[member])
    monkeypatch.setattr(runtime,'_signal_owned_processes',Mock())
    monkeypatch.setattr(runtime,'_close_owned_processes',lambda _rows:None)
    with pytest.raises(RuntimeError,match='cleanup_timeout'):
        asyncio.run(runtime._stop_native_process_group(SimpleNamespace(pid=424242,returncode=0),'token',grace_sec=0,kill_wait_sec=0))


@pytest.mark.parametrize('group',[1,0,-1])
def test_process_group_cleanup_refuses_non_owned_groups(monkeypatch,group):
    scan=Mock()
    monkeypatch.setattr(runtime,'_owned_processes',scan)
    with pytest.raises(RuntimeError,match='not_owned'):
        asyncio.run(runtime._stop_native_process_group(SimpleNamespace(pid=group,returncode=0),'token'))
    scan.assert_not_called()


def test_process_group_cleanup_refuses_controller_group(monkeypatch):
    scan=Mock()
    monkeypatch.setattr(runtime,'_owned_processes',scan)
    with pytest.raises(RuntimeError,match='not_owned'):
        asyncio.run(runtime._stop_native_process_group(SimpleNamespace(pid=os.getpgrp(),returncode=0),'token'))
    scan.assert_not_called()


def test_process_group_cleanup_of_absent_group_sends_nothing(monkeypatch):
    scan=Mock(return_value=[])
    signal_mock=Mock()
    monkeypatch.setattr(runtime,'_owned_processes',scan)
    monkeypatch.setattr(runtime,'_signal_owned_processes',signal_mock)
    monkeypatch.setattr(runtime,'_close_owned_processes',lambda _rows:None)
    asyncio.run(runtime._stop_native_process_group(SimpleNamespace(pid=424242,returncode=0),'token'))
    signal_mock.assert_not_called()


@pytest.mark.parametrize('wrapper_exited',[False,True])
def test_process_group_cleanup_terminates_real_owned_children_only(tmp_path,wrapper_exited):
    async def exercise():
        child_ready=tmp_path/'child-ready'
        child_stopped=tmp_path/'child-stopped'
        wrapper_ready=tmp_path/'wrapper-ready'
        child_source='''import pathlib,signal,sys,time
def stop(*_):
    pathlib.Path(sys.argv[2]).write_text('terminated')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
pathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid()))
while True:time.sleep(.05)
'''
        wrapper_source='''import pathlib,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]])
def stop(*_):
    child.wait(timeout=2)
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while not pathlib.Path(sys.argv[2]).exists():time.sleep(.01)
pathlib.Path(sys.argv[4]).write_text(str(child.pid))
if sys.argv[5]=='exit':raise SystemExit(0)
while True:time.sleep(.05)
'''
        token='fixture-'+tmp_path.name
        wrapper=outside=None
        try:
            clean_env={key:value for key,value in os.environ.items() if key!=runtime._PROCESS_OWNER_ENV}
            outside=await asyncio.create_subprocess_exec(sys.executable,'-c','import time;time.sleep(30)',env=clean_env,start_new_session=True)
            child_env={**os.environ,runtime._PROCESS_OWNER_ENV:token}
            wrapper=await asyncio.create_subprocess_exec(sys.executable,'-c',wrapper_source,child_source,
                str(child_ready),str(child_stopped),str(wrapper_ready),'exit' if wrapper_exited else 'wait',env=child_env,start_new_session=True)
            deadline=time.monotonic()+5
            while not wrapper_ready.exists():
                assert time.monotonic()<deadline
                await asyncio.sleep(.02)
            if wrapper_exited:assert await wrapper.wait()==0
            await runtime._stop_native_process_group(wrapper,token,grace_sec=3)
            assert wrapper.returncode==0 and child_stopped.read_text()=='terminated'
            assert outside.returncode is None
        finally:
            for process in [wrapper,outside]:
                if process is not None:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await process.wait()
    asyncio.run(exercise())


def test_real_chromium_receives_the_process_owner_token(tmp_path):
    from playwright.async_api import async_playwright
    async def exercise():
        token=f'fixture-{tmp_path.name}'
        marker=f'{runtime._PROCESS_OWNER_ENV}={token}'.encode()
        async with async_playwright() as p:
            context=await p.chromium.launch_persistent_context(str(tmp_path/'profile'),executable_path='/usr/bin/chromium',
                headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--no-proxy-server'],
                env={**os.environ,runtime._PROCESS_OWNER_ENV:token})
            try:
                await context.pages[0].set_content('<title>offline ownership test</title>')
                found=[]
                for stat_path in Path('/proc').glob('[0-9]*/stat'):
                    try:
                        raw=stat_path.read_text();fields=raw[raw.rfind(')')+2:].split()
                        if fields[0] in {'Z','X'}:continue
                        env=(stat_path.parent/'environ').read_bytes().split(b'\0')
                        if marker in env:
                            found.append((int(stat_path.parent.name),int(fields[2]),int(fields[3])))
                    except (FileNotFoundError,ProcessLookupError):continue
                assert any(pgrp!=os.getpgrp() or session!=os.getsid(0) for _pid,pgrp,session in found)
            finally:
                await context.close()
    asyncio.run(exercise())


def test_cleanup_stops_detached_chromium_processes(tmp_path):
    async def exercise():
        token=f'fixture-{tmp_path.name}'
        child_script='''import asyncio,sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(sys.argv[1],executable_path='/usr/bin/chromium',
            headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--no-proxy-server'],env={**__import__('os').environ})
        page=await context.new_page()
        await page.set_content('<title>offline cleanup test</title>')
        print('READY',flush=True)
        await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
'''
        env={**os.environ,runtime._PROCESS_OWNER_ENV:token}
        process=await asyncio.create_subprocess_exec(sys.executable,'-c',child_script,str(tmp_path/'profile'),
            env=env,start_new_session=True,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            assert await asyncio.wait_for(process.stdout.readline(),30)==b'READY\n'
            members=runtime._owned_processes(token,process.pid)
            try:
                assert any(member.pid!=process.pid and runtime._proc_stat(member.pid)['session']!=process.pid for member in members)
            finally:runtime._close_owned_processes(members)
            await runtime._stop_native_process_group(process,token,grace_sec=3)
            # The owned wrapper is deliberately signalled during cleanup.
            # Receipt/process ownership, not a zero exit status, proves drain.
            assert process.returncode in (0,-signal.SIGTERM,-signal.SIGKILL)
            assert runtime._owned_processes(token,process.pid)==[]
        finally:
            if process.returncode is None:
                await runtime._stop_native_process_group(process,token,grace_sec=2)
    asyncio.run(exercise())


def test_real_chromium_csp_and_page_guard_preserve_original_inputs():
    from playwright.async_api import async_playwright
    async def exercise():
        forwarded=[];journal=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            try:
                context=await browser.new_context()
                async def handle(route):
                    request=route.request
                    if request.resource_type=='document':
                        await route.fulfill(status=200,content_type='text/html',headers={'Content-Security-Policy':"script-src 'self'"},body='<title>CSP integration fixture</title>')
                    elif request.url.endswith('/start'):
                        await route.fulfill(json={'ok':True,'account':{'playerId':42}})
                    else:
                        forwarded.append(request.post_data_json)
                        await route.fulfill(json={'ok':True})
                await context.route('**/*',handle)
                await context.expose_binding('__fanrenBeforeWrite',lambda _source,value:journal.append(value))
                await context.add_init_script(script=worker.init_script(42,'fishing'))
                page=await context.new_page()
                await page.goto('https://asc.aiopenai.app/miniapp/xianxia-dwelling')
                denied=await page.evaluate('''async () => {
                    try{await fetch('/api/miniapp/xianxia-dwelling/fishing/cast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({playerId:42,operationId:'not-dispatched'})});return false;}
                    catch{return true;}
                }''')
                assert denied and not forwarded and not journal
                await page.evaluate('''async () => {
                    await fetch('/api/miniapp/xianxia-dwelling/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
                }''')
                await page.wait_for_function('() => window.__fanrenOwner===true')
                payload={'playerId':42,'operationId':'original','position':[1,0,2],'fishingProof':{'durationMs':5200,'events':[{'t':100,'holding':True}]}}
                await page.evaluate('''body => fetch('/api/miniapp/xianxia-dwelling/fishing/cast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})''',payload)
                assert forwarded==[payload]
                assert journal[0]['body']==payload
                # The same native mouse path scrolls the real DOM and hits only
                # the visible button, while an overlay blocks accidental clicks.
                from tools.fanren_browser_support.human_input import HumanInput
                await page.set_content('<div style="height:260px;overflow:auto;width:300px" id="panel"><div style="height:700px"></div><button id="target">target</button></div>')
                await page.evaluate('''() => {window.trustedClick=false;document.querySelector('#target').onclick=e=>window.trustedClick=e.isTrusted;}''')
                await HumanInput(page).click(page.locator('#target'))
                assert await page.evaluate('window.trustedClick')
                await page.evaluate('''() => {const e=document.createElement('div');e.style='position:fixed;inset:0;background:white;z-index:100';document.body.append(e);}''')
                with pytest.raises(RuntimeError,match='target_obscured'):
                    await HumanInput(page).click(page.locator('#target'))
            finally:await browser.close()
    asyncio.run(exercise())
