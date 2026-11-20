"""Replay the real batch sequencer against isolated native-interface fixtures.

These are protocol regressions, not claims of live dungeon completion.
"""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tools import qingxi_quest_browser as worker
from model.features import qingxi_quests as quests


BATCH = Path(worker.__file__).with_name('qingxi_reward_batch.js').read_text().replace('__PLAYER__', '42')

HARNESS = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const ids=['moonpalace','bloodland','southgate','escort','mulanshrine','bqpuppet','jianzhong'];
const defs=[['moon','worldMoonRequest'],['blood','worldBloodRequest'],['siege','worldSiegeRequest'],
 ['escort','worldEscortRequest'],['shrine',null,'mulan-shrine','mulanShrine'],
 ['puppet',null,'baiqiao-puppet','baiqiaoPuppet'],['jianzhong',null,'jianzhong-trial','jianzhongTrial']];
let day='2026-11-20',dailyCalls=0,trialCalls=0,inspections=0,batch=null;
const calls=[],daily=ids.map(id=>({id,day,hasKey:true,claimed:false,status:'pending',exhausted:false}));
const quotas=Object.fromEntries(['longwind','bamboo'].map(arena=>[arena,
 {arena,day,used:0,remaining:3,limit:3,activeRun:null}]));
if(scenario==='already_claimed')daily.forEach(row=>{row.claimed=true;row.status='claimed'});
if(scenario==='already_exhausted')for(const q of Object.values(quotas)){q.used=3;q.remaining=0}
if(scenario==='longwind_exhausted'){quotas.longwind.used=3;quotas.longwind.remaining=0}
if(scenario==='locked'){daily[0].hasKey=false;daily[0].status='locked'}
if(scenario==='moon_failed')daily[0].exhausted=true;
if(scenario==='unknown_quota')delete quotas.bamboo.used;
if(scenario==='wrong_arena')quotas.bamboo.arena='longwind';
if(scenario==='wrong_limit')quotas.bamboo.limit=6;
if(scenario==='trial_pending')quotas.bamboo.activeRun={id:'old'};
if(scenario==='no_active_field')delete quotas.longwind.activeRun;
const q={player:scenario==='wrong_identity'?99:42,error:'',quiescing:false,pending:new Map(),
 queue:Promise.resolve(),statusCache:new Map()};
if(scenario==='pending')q.pending.set('old',{});
const c={console,Date,Map,Set,Object,Array,JSON,localStorage:{getItem:()=>scenario==='stored_pending'?'[{}]':'[]'},
 __qx:q,__fc:{stats:()=>({active:scenario==='combat_pending',overworld:false})}};
function dailyResponse(index){const row=daily[index],status={day:row.day,claimed:row.claimed,hasKey:row.hasKey,
 run:scenario==='daily_pending'&&index===0?{id:'old'}:null,failClaimed:row.exhausted};
 if(scenario==='daily_no_active_field'&&index===0)delete status.run;
 return {ok:true,[defs[index][0]]:status};}
defs.forEach((d,index)=>{if(d[1])c[d[1]]=async()=>dailyResponse(index)});
c.qingxiInstanceRequest=async resource=>{const index=defs.findIndex(d=>d[2]===resource);return {ok:true,[defs[index][3]]:dailyResponse(index)}};
c.worldTrialRequest=async(_action,{arena})=>({ok:true,status:JSON.parse(JSON.stringify(quotas[arena]))});
c.__autobot={
 async checkDailyRewards(){inspections++;calls.push('inspect');},
 report(){return {error:scenario==='native_error'&&dailyCalls?'failure':'',batch,
   dailyRewards:{verified:true,errors:0,day,rows:daily}}},
 handoff(){return {rewardRequests:{}}},
 async daily(){dailyCalls++;calls.push('daily');daily.forEach(row=>{if(row.hasKey&&!row.exhausted){row.claimed=true;row.status='claimed'}});
  batch={mode:'daily',status:'complete',skipped:[]};
  if(scenario==='day_changed'){day='2026-11-21';daily.forEach(row=>row.day=day)}},
 async trials(){trialCalls++;calls.push('trials');const completed=[];
  for(const [arena,quota]of Object.entries(quotas)){
   while(quota.remaining>0){const runId=arena+'-'+quota.used;
    completed.push({id:arena,day,tier:scenario==='higher_tier'?2:1,
     runId:scenario==='duplicate_run'?'same':runId,result:{verified:true,outcome:'victory',rewarded:true}});
    quota.used++;quota.remaining--;
   }
  }
  if(scenario==='no_consumption'){quotas.bamboo.used=0;quotas.bamboo.remaining=3}
  if(scenario==='false_victory')completed[0].result.rewarded=false;
  if(scenario==='postrun_pending')quotas.longwind.activeRun={id:'unknown'};
  batch={mode:'trials',status:'complete',completed,skipped:[]};}
};
c.window=c;vm.createContext(c);vm.runInContext(SOURCE,c);
(async()=>{
 if(scenario==='inspect_only'){
  const result=await c.__qingxiRewardBatch.inspect();
  assert.equal(result.verified,true);assert.equal(result.daily.length,7);
  assert.equal(result.trials.longwind.remaining+result.trials.bamboo.remaining,6);
  assert.equal(dailyCalls,0);assert.equal(trialCalls,0);return;
 }
 const failures=['unknown_quota','wrong_arena','wrong_limit','trial_pending','no_active_field',
  'wrong_identity','pending','stored_pending','combat_pending','daily_pending','daily_no_active_field',
  'day_changed','native_error','higher_tier','duplicate_run','no_consumption','false_victory','postrun_pending'];
 if(failures.includes(scenario)){
  await assert.rejects(c.__qingxiRewardBatch.run(),/reward_batch_/);
  assert.equal(c.__qingxiRewardBatch.state.phase,'held');
  if(['day_changed','native_error'].includes(scenario))assert.equal(trialCalls,0);
  else if(!['higher_tier','duplicate_run','no_consumption','false_victory','postrun_pending'].includes(scenario)){
   assert.equal(dailyCalls,0);assert.equal(trialCalls,0);
  }
 }else{
  await c.__qingxiRewardBatch.run();const state=c.__qingxiRewardBatch.state;
  assert.equal(state.phase,['locked','moon_failed'].includes(scenario)?'partial':'complete');
  assert.equal(state.claimed,['locked','moon_failed'].includes(scenario)?6:7);
  assert.equal(state.trial_used,6);assert.equal(state.remaining,0);
  assert.equal(state.trial_victories,scenario==='already_exhausted'?0:scenario==='longwind_exhausted'?3:6);
  assert.equal(dailyCalls,scenario==='already_claimed'?0:1);
  assert.equal(trialCalls,scenario==='already_exhausted'?0:1);
  if(scenario==='complete'){
   await c.__qingxiRewardBatch.run();assert.equal(dailyCalls,1);assert.equal(trialCalls,1);
  }
 }
 assert.equal(c.__qingxiRewardBatch.state.running,false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""


@pytest.mark.parametrize('scenario', [
    'inspect_only', 'complete', 'already_claimed', 'already_exhausted', 'longwind_exhausted', 'locked', 'moon_failed',
    'unknown_quota', 'wrong_arena', 'wrong_limit', 'trial_pending', 'no_active_field', 'wrong_identity',
    'pending', 'stored_pending', 'combat_pending', 'daily_pending', 'daily_no_active_field',
    'day_changed', 'native_error', 'higher_tier', 'duplicate_run', 'no_consumption', 'false_victory', 'postrun_pending',
])
def test_native_reward_batch_sequencing(tmp_path, scenario):
    script = tmp_path / 'batch.cjs'
    script.write_text('const SOURCE=' + json.dumps(BATCH) + ';const scenario=' + json.dumps(scenario) + ';\n' + HARNESS)
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_only_explicit_reward_workflow_installs_the_batch():
    assert 'window.__qingxiRewardBatch = {state, inspect, run}' not in worker.browser_script(42)
    script = worker.browser_script(42, reward_batch=True)
    assert 'window.__qingxiRewardBatch = {state, inspect, run}' in script
    assert '__PLAYER__' not in script
    assert '"rewardBatch": true' in script


@pytest.mark.parametrize('taint', ['', 'active_daily', 'missing_run', 'active_trial', 'wrong_day', 'wrong_quota'])
def test_protocol_reward_inspection_uses_nine_status_only_requests(taint):
    fixture = json.loads((Path(__file__).parent / 'fixtures/qingxi-native-reward-readers-20261120.json').read_text())
    specs = [
        ('moon-palace', 'moonPalace', 'moon'), ('blood-land', 'bloodLand', 'blood'),
        ('south-gate', 'southGate', 'siege'), ('refugee-escort', 'refugeeEscort', 'escort'),
        ('mulan-shrine', 'mulanShrine', 'shrine'), ('baiqiao-puppet', 'baiqiaoPuppet', 'puppet'),
        ('jianzhong-trial', 'jianzhongTrial', 'jianzhong'), ('trial', 'trial', 'status')]
    async def call(action, *, _api_path, **body):
        assert action == 'status' and set(body) <= {'arena'}
        resource = _api_path.rsplit('/', 1)[-1]
        row = next(row for row in fixture['reward_statuses'] if row['resource'] == resource and row['arena'] == body.get('arena'))
        status = copy.deepcopy(row['status'])
        if resource == 'moon-palace':
            if taint == 'active_daily':
                status['run'] = {'id': 'old'}
            if taint == 'missing_run':
                status.pop('run')
        if resource == 'trial' and body['arena'] == 'bamboo':
            if taint == 'active_trial':
                status['activeRun'] = {'id': 'old'}
            if taint == 'wrong_day':
                status['day'] = '2026-11-19'
            if taint == 'wrong_quota':
                status['remaining'] = 3
        _resource, envelope, key = next(spec for spec in specs if spec[0] == resource)
        return {'ok': True, envelope: {key: status}}
    reader = AsyncMock(side_effect=call)
    if taint:
        with pytest.raises(RuntimeError):
            asyncio.run(quests.inspect_reward_statuses(reader, 42))
    else:
        result = asyncio.run(quests.inspect_reward_statuses(reader, 42))
        assert reader.await_count == 9
        assert len(result['daily']) == 7 and result['trials']['bamboo']['remaining'] == 2


@pytest.fixture
def verified_batch():
    day = '2026-11-20'
    final = {'verified': True, 'player': 42, 'day': day,
             'daily': [{'id': identity, 'day': day, 'hasKey': True, 'claimed': True, 'activeRun': None}
                       for identity in ['moonpalace', 'bloodland', 'southgate', 'escort', 'mulanshrine', 'bqpuppet', 'jianzhong']],
             'trials': {arena: {'arena': arena, 'day': day, 'used': 3, 'remaining': 0, 'limit': 3, 'activeRun': None}
                        for arena in ['longwind', 'bamboo']}}
    return {'player': 42, 'server_checked': True, 'pending': 0, 'stored_pending': [],
            'reward_batch': {'phase': 'complete', 'running': False, 'before': {'day': day}, 'final': final}}


@pytest.mark.parametrize('taint', ['', 'held', 'running', 'wrong_player', 'day_changed', 'malformed_final',
                                  'missing_daily', 'duplicate_daily', 'daily_active', 'daily_no_active',
                                  'bad_boolean', 'missing_trial', 'trial_active', 'trial_no_active',
                                  'quota_sum', 'quota_bool', 'quota_limit', 'error'])
def test_host_requires_all_nine_native_statuses_before_releasing(verified_batch, taint):
    result = copy.deepcopy(verified_batch)
    batch = result['reward_batch']
    final = batch['final']
    if taint in {'held', 'running'}:
        batch['phase'] = taint
    elif taint == 'wrong_player':
        final['player'] = 99
    elif taint == 'day_changed':
        final['day'] = '2026-11-21'
    elif taint == 'malformed_final':
        batch['final'] = []
    elif taint == 'missing_daily':
        final['daily'].pop()
    elif taint == 'duplicate_daily':
        final['daily'][0] = final['daily'][1]
    elif taint == 'daily_active':
        final['daily'][0]['activeRun'] = {'id': 'old'}
    elif taint == 'daily_no_active':
        final['daily'][0].pop('activeRun')
    elif taint == 'bad_boolean':
        final['daily'][0]['claimed'] = 1
    elif taint == 'missing_trial':
        final['trials'].pop('bamboo')
    elif taint == 'trial_active':
        final['trials']['bamboo']['activeRun'] = {'id': 'old'}
    elif taint == 'trial_no_active':
        final['trials']['bamboo'].pop('activeRun')
    elif taint == 'quota_sum':
        final['trials']['bamboo']['remaining'] = 1
    elif taint == 'quota_bool':
        final['trials']['bamboo']['remaining'] = False
    elif taint == 'quota_limit':
        final['trials']['bamboo']['limit'] = 6
    elif taint == 'error':
        batch['error'] = 'unknown'
    assert quests.reward_batch_verified(result, 42) is (taint == '')


@pytest.mark.parametrize('valid', [True, False, 'pending', 'journal', 'status_error'])
@pytest.mark.parametrize('inspection', [False, True])
def test_reward_worker_shares_durable_ownership_and_requires_batch_evidence(monkeypatch, tmp_path, verified_batch, valid, inspection):
    from model.features import cave_treasure_miniapp, qingxi_trial_browser

    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data', AsyncMock(return_value='fixture'))
    record, saved, commands = {}, [], []
    monkeypatch.setattr(quests, 'save', lambda: saved.append(copy.deepcopy(record)))
    if inspection and valid:
        verified_batch['reward_inspection'] = verified_batch['reward_batch']['final']
    if not valid:
        verified_batch.pop('reward_batch')
    elif valid == 'pending':
        verified_batch['pending'] = 1
    elif valid == 'journal':
        verified_batch['stored_pending'] = [{'operation_id': 'unknown'}]
    elif valid == 'status_error':
        verified_batch['status_error'] = 'unknown'

    async def spawn(*command, **_kwargs):
        commands.append(command)
        assert saved[-1]['gameplay_uncertain'] is True
        assert saved[-1]['browser']['workflow'] == 'rewards'
        Path(command[command.index('--result') + 1]).write_text(json.dumps(verified_batch))
        return SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))

    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(quests, '_drain_browser_process', AsyncMock())
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': False}})
    flow = quests.run_gameplay(SimpleNamespace(identity_id=42), 'fixture', 'fixture', record, lambda: True,
                              verify_flight=flight, reward_batch=True, inspection_only=inspection)
    if valid is True:
        asyncio.run(flow)
        assert record['gameplay_uncertain'] is False
        flight.assert_awaited_once()
    else:
        with pytest.raises(RuntimeError, match='qingxi_reward_batch_requires_review'):
            asyncio.run(flow)
        assert record['gameplay_uncertain'] is True
        flight.assert_not_awaited()
    command = commands[0]
    assert command[command.index('--workflow') + 1] == 'rewards'
    assert command[command.index('--max-seconds') + 1] == ('900' if inspection else '10800')
    assert ('--read-only' in command) is inspection
    assert 'fixture' not in command
    assert not list(tmp_path.glob('.runtime/**/launch.url'))


def test_npc_only_review_cannot_release_a_reward_batch(monkeypatch):
    record = {'gameplay_uncertain': True, 'browser': {'workflow': 'rewards', 'server_checked': True}}
    evidence = Mock(side_effect=AssertionError('NPC evidence is insufficient to release a reward batch'))
    monkeypatch.setattr(quests, '_browser_evidence', evidence)
    asyncio.run(quests.review_browser_hold(AsyncMock(), record, 42))
    assert record['gameplay_uncertain'] is True
    evidence.assert_not_called()


def test_reward_review_does_not_replace_unresolved_npc_evidence(monkeypatch):
    from model.features import cave_treasure_runtime as cave
    owner = SimpleNamespace(identity_id=42, account_id=42, is_current=lambda: True)
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'workflow': 'quests'}}
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _id: owner)
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *_args, **_kwargs: '')
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _id: True)
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _id: asyncio.Lock())
    before = copy.deepcopy(record)
    ok, message = asyncio.run(quests.run_once(42, status_only=True, reward_batch=True))
    assert not ok and '不能用奖励状态解除占用' in message
    assert record == before


def test_repeated_cancellation_drains_reward_spawn_under_scene_lock(monkeypatch, tmp_path):
    from model.features import cave_treasure_miniapp, qingxi_trial_browser
    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data', AsyncMock(return_value='fixture'))
    monkeypatch.setattr(quests, 'save', Mock())
    record = {}

    async def exercise():
        lock = asyncio.Lock()
        spawned, release_spawn, draining, release_drain = [asyncio.Event() for _ in range(4)]
        child = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
        async def spawn(*_command, **kwargs):
            assert kwargs['env']['XIUXIAN_FANREN_PROCESS_OWNER']
            spawned.set()
            await release_spawn.wait()
            return child
        async def drain(process, token):
            assert process is child and token and lock.locked()
            draining.set()
            await release_drain.wait()
        monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', spawn)
        monkeypatch.setattr(quests, '_drain_browser_process', drain)
        async def operation():
            async with lock:
                await quests.run_gameplay(SimpleNamespace(identity_id=42), 'fixture', 'fixture', record, lambda: True,
                    verify_flight=AsyncMock(), reward_batch=True)
        task = asyncio.create_task(operation())
        await spawned.wait()
        task.cancel()
        release_spawn.set()
        await draining.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert lock.locked() and not task.done()
        release_drain.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lock.locked()
    asyncio.run(exercise())
    assert record['phase'] == 'held' and record['gameplay_uncertain'] is True
    assert not list(tmp_path.glob('.runtime/**/launch.url'))


def test_batch_notification_uses_reward_identity_and_actual_gains(monkeypatch):
    record = {'browser': {'workflow': 'rewards'}, 'receipts': [
        {'operation_id': 'trial:1', 'reward_id': 'trial:run-a:0', 'resource': 'trial',
         'grants': [{'name': '灵石', 'quantity': 2}]}]}
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, 'send_audit_log', send)
    asyncio.run(quests.notify_rewards(42, record))
    assert send.call_args.args[0] == '青溪每日副本/试炼｜已确认收获｜灵石×2'
    assert send.call_args.kwargs['priority'] == 'normal'
    assert record['notice_attempted'] == ['trial:run-a:0']
    asyncio.run(quests.notify_rewards(42, record))
    assert send.await_count == 1
    record['receipts'].append({'operation_id': 'trial:1', 'reward_id': 'trial:run-b:0',
                               'grants': [{'name': '灵石', 'quantity': 5}]})
    asyncio.run(quests.notify_rewards(42, record))
    assert send.await_count == 2
    assert send.call_args.args[0].endswith('灵石×5')
