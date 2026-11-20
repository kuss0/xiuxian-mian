import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
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


def test_wrong_player_response_is_not_a_receipt():
    intent=worker.request_fact('https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/fishing/cast',
                             {'playerId':'42','operationId':'actual-client-generated'},42)
    assert intent['identity_matches']
    assert not worker.response_fact(intent,{'ok':True,'account':{'playerId':99}},200)['ok']
    assert not worker.response_fact(intent,'Cloudflare HTML',521)['ok']


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


def test_worker_cancellation_drains_under_account_lock_and_removes_launch_secret(monkeypatch,tmp_path):
    async def exercise():
        owner=SimpleNamespace(identity_id=42,account_id=42,is_current=lambda:True)
        records={};lock=asyncio.Lock();spawned=asyncio.Event();closed=asyncio.Event();signals=[]
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
        def terminate(_signal):
            signals.append(lock.locked());process.returncode=0;closed.set()
        async def wait():await closed.wait();return 0
        process.send_signal=terminate;process.wait=wait
        async def spawn(*_args,**_kwargs):spawned.set();return process
        monkeypatch.setattr(runtime.asyncio,'create_subprocess_exec',spawn)
        task=asyncio.create_task(runtime.run(owner,'inspect','job'))
        await spawned.wait();task.cancel();await task
        assert signals==[True]
        assert not lock.locked()
        assert records['42']['phase']=='held'
        assert records['42']['stop_requested']
        assert not list(tmp_path.rglob('launch.url'))
    asyncio.run(exercise())
