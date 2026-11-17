"""Exercise the browser boundary in Node without network or live game state."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.qingxi_quest_browser import (GUARD, QUEST_COMBAT_ENTRY, QUEST_COMBAT_LOOT,
                                      QUEST_NAVIGATION_REVIEW, VENDOR, browser_script, finished_phase)


HARNESS = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const prefix='https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/';
function environment(config={},handler=null,stored=[]){
 let now=0;const calls=[],storage=new Map([['qingxi-local-pending:42',JSON.stringify(stored)]]);
 const c={URL,Request,Response,console,performance:{now:()=>now},
  setTimeout:(fn,ms)=>{now+=ms;queueMicrotask(fn)},location:{href:prefix,origin:'https://asc.aiopenai.app'},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  fetch:async(input,options)=>{
   const resource=new URL(input).pathname.split('/').at(-1),body=JSON.parse(options?.body||'{}');
   calls.push({resource,body,at:now,journal:storage.get('qingxi-local-pending:42')});
   const value=handler?await handler(resource,body):{ok:true,account:{playerId:42}};
   return value instanceof Response ? value : new Response(JSON.stringify(value));
  }};
 c.window=c;vm.createContext(c);vm.runInContext(GUARD.replace('__CONFIG__',JSON.stringify({player:42,...config})),c);
 const send=(resource,body)=>c.fetch(prefix+resource,{body:JSON.stringify(body)});
 return {c,calls,storage,send};
}
async function ready(config={},handler=null){const env=environment(config,handler);await env.send('start',{});return env;}
async function check(scenario){
 if(scenario==='acknowledged_disk_journal'){
  const row={resource:'field-cast',action:'events',operation_id:'field-cast:begin:123',at:10,
   events:[{k:'begin',s:123,key:'a:火弹',pos:[1,2]}]};
  for(const receipt of [null,{operation_id:'other',at:11},{operation_id:row.operation_id,at:9},
    {operation_id:row.operation_id,at:11}]){
   const e=environment({confirmedCastReceipts:receipt?[receipt]:[]},null,[row]);
   const confirmed=receipt?.operation_id===row.operation_id&&receipt.at>=row.at;
   assert.equal(e.c.__qx.error==='',confirmed);
   assert.deepEqual(JSON.parse(e.storage.get('qingxi-local-pending:42')),confirmed?[]:[row]);
  }
 } else if(scenario==='release_rejection'){
  for(const [action,status,error,ok=false] of [['field-release',409,'quest_too_far'],['turn-in',409,'quest_too_far'],
      ['field-release',503,'quest_too_far'],['field-release',409,'quest_operation_conflict'],
      ['field-release',409,'quest_too_far',true]]){
   const e=await ready({},(r,b)=>b.action?new Response(JSON.stringify({ok,error}),{status}):{ok:true,account:{playerId:42}});
   e.c.__autobot={quests:()=>[{id:'fixture',choices:[]}]};
   const body={action,operationId:'release-one',enemyId:'lantern:084',questId:'fixture'},response=await e.send('quests',body);
   const known=action==='field-release'&&status===409&&error==='quest_too_far'&&ok===false;
   assert.equal(response.status,status);assert.equal((await response.json()).ok,ok);
   assert.equal(e.c.__qx.receipts.at(-1).ok,false);
   assert.equal(e.c.__qx.pending.size,known?0:1);assert.equal(e.c.__qx.error==='',known);
   if(known){assert.equal(e.storage.get('qingxi-local-pending:42'),'[]');
    await e.send('quests',body);assert.equal(e.calls.length,2)}
  }
 } else if(scenario==='release_journal'){
  const row={resource:'quests',action:'field-release',operation_id:'one',enemy_id:'lantern:084',at:10};
  for(const old of [null,{...row,operation_id:'other'},{...row,at:11},{...row,action:'turn-in'},row]){
   const e=environment({reviewedReleasePending:old?[old]:[]},null,[row]);
   const same=old===row;assert.equal(e.c.__qx.error==='',same);
   assert.deepEqual(JSON.parse(e.storage.get('qingxi-local-pending:42')),same?[]:[row]);
  }
 } else if(scenario==='choices'){
  const e=await ready();e.c.__autobot={quests:()=>[{id:'q1',choices:[{id:'release'}]}]};
  await assert.rejects(e.send('quests',{action:'turn-in',questId:'q1',choice:'release',operationId:'one'}),/reward_choice_required/);
  assert.equal(e.calls.length,1);
  const a=await ready({choices:{q1:'release'}});a.c.__autobot=e.c.__autobot;
  await a.send('quests',{action:'turn-in',questId:'q1',choice:'release',operationId:'one'});
  assert.equal(a.calls.length,2);assert.equal(a.c.__qx.pending.size,0);
 } else if(scenario==='fishing'){
  const e=await ready();await assert.rejects(e.send('fishing',{action:'start'}),/fishing_plan_required/);
  assert.equal(e.calls.length,1);
 } else if(scenario==='durable_unknown'){
  const e=await ready({},(r,b)=>{if(b.action==='accept')throw Error('network');return {ok:true,account:{playerId:42}}});
  await assert.rejects(e.send('quests',{action:'accept',operationId:'one'}),/network/);
  assert.match(e.calls[1].journal,/one/);assert.equal(e.c.__qx.pending.size,1);
  await assert.rejects(e.send('quests',{action:'accept',operationId:'two'}),/unconfirmed/);
  await e.send('quests',{action:'status'});assert.equal(e.calls.length,3);assert.equal(e.c.__qx.pending.size,1);
 } else if(scenario==='duplicate'){
  const e=await ready();const body={action:'accept',operationId:'one'};
  await Promise.all([e.send('quests',body),e.send('quests',body)]);
  assert.equal(e.calls.length,2);assert.equal(e.c.__qx.pending.size,0);
 } else if(scenario==='singleflight'){
  const e=await ready();await Promise.all([e.send('quests',{action:'status'}),e.send('quests',{action:'status'})]);
  assert.equal(e.calls.length,2);
  await Promise.all([e.send('trial',{action:'status',arena:'longwind'}),e.send('trial',{action:'status',arena:'bamboo'})]);
  assert.equal(e.calls.length,4);assert.notEqual(e.calls[2].body.arena,e.calls[3].body.arena);
 } else if(scenario==='native_cast'){
  let release;const e=await ready({},async(r,b)=>{
   if(r==='quests')await new Promise(resolve=>{release=resolve});return {ok:true,account:{playerId:42}};
  });
  const status=e.send('quests',{action:'status'});while(!release)await new Promise(resolve=>setImmediate(resolve));
  await e.send('field-cast',{events:[{k:'use',s:1,pos:[0,0]}]});assert.equal(e.c.__qx.casts,1);assert.equal(e.calls.length,3);
  release();await status;
 } else if(scenario==='daily_full'){
  const e=await ready({},r=>({ok:true,account:{playerId:42},moonPalace:{claimed:true,hasKey:true}}));
  await assert.rejects(e.send('moon-palace',{action:'start'}),/daily_reward_full/);
  assert.equal(e.calls.length,2);assert.equal(e.calls[1].body.action,'status');
 } else if(scenario==='daily_retry'){
  const e=await ready({},r=>({ok:true,account:{playerId:42},moonPalace:{claimed:false,hasKey:true}}));
  await e.send('moon-palace',{action:'start'});
  await assert.rejects(e.send('moon-palace',{action:'start'}),/instance_retry_requires_review/);
  assert.equal(e.calls.filter(x=>x.resource==='moon-palace'&&x.body.action==='start').length,1);
 } else if(scenario==='pacing'){
  const e=await ready();await e.send('quests',{action:'accept',operationId:'one'});await e.send('quests',{action:'accept',operationId:'two'});
  assert.equal(e.calls[2].at-e.calls[1].at,5000);
 } else if(scenario==='status_cache'){
  const e=await ready();await e.send('quests',{action:'status'});await e.send('quests',{action:'status'});
  assert.equal(e.calls.length,2);
  await e.send('quests',{action:'accept',operationId:'one'});await e.send('quests',{action:'status'});
  assert.equal(e.calls.length,4);
 } else if(scenario==='status_failure'){
  let fail=true;const e=await ready({},(r,b)=>{if(b.action==='status'&&fail)throw Error('network');return {ok:true,account:{playerId:42}}});
  await assert.rejects(e.send('quests',{action:'status'}),/network/);
  assert.equal(e.c.__qx.error,'');assert.equal(e.c.__qx.pending.size,0);
  fail=false;await e.send('quests',{action:'status'});assert.equal(e.c.__qx.statusError,'');
 } else if(scenario==='readonly'){
  const e=await ready({readOnly:true});await e.send('quests',{action:'status'});
  for(const resource of ['overview','details','section','town/review']){
   await e.send(resource,resource==='section'?{section:'inventory'}:{});
   await assert.rejects(e.send(resource,{action:'claim'}),/read_only/);
  }
  await e.send('battle/doufa-profile',{token:'test',initData:'test',playerId:42,includeInventory:true});
  for(const body of [{skillSlots:[]},{unknown:true},{action:'save'},{action:'status',skillSlots:[]}]){
   await assert.rejects(e.send('battle/doufa-profile',body),/read_only/);
  }
  await assert.rejects(e.send('quests',{action:'accept',operationId:'one'}),/read_only/);
  await assert.rejects(e.send('field-cast',{events:[]}),/read_only/);
  await assert.rejects(e.send('flight',{action:'start'}),/read_only/);
  await assert.rejects(e.send('battle/doufa-profile',{playerId:43}),/player_mismatch/);
  await assert.rejects(e.send('details',{playerId:43}),/player_mismatch/);
  assert.equal(e.calls.length,7);assert.equal(e.c.__qx.pending.size,0);
 } else if(scenario==='identity'){
  const e=await ready();await assert.rejects(e.send('quests',{action:'status',playerId:43}),/player_mismatch/);
  assert.equal(e.calls.length,1);
 } else if(scenario==='journal_failure'){
  const e=await ready();e.c.localStorage.setItem=()=>{throw Error('disk')};
  await assert.rejects(e.send('quests',{action:'accept',operationId:'one'}),/journal_failed/);
  assert.equal(e.calls.length,1);
 } else if(scenario==='housekeeping'){
  const e=await ready({},(r,b)=>{if(r==='location')throw Error('keepalive');return {ok:true,account:{playerId:42}}});
  await assert.rejects(e.send('location',{action:'save'}),/keepalive/);
  assert.equal(e.c.__qx.error,'');assert.equal(e.c.__qx.pending.size,0);
  await e.send('quests',{action:'accept',operationId:'one'});assert.equal(e.calls.length,3);
 } else if(scenario==='flight_cleanup'){
  const e=await ready({},(r,b)=>{if(r==='flight'&&b.action==='tick')throw Error('heartbeat');return {ok:true,account:{playerId:42}}});
  await assert.rejects(e.send('flight',{action:'tick'}),/heartbeat/);
  assert.equal(e.c.__qx.error,'');assert.equal(e.c.__qx.pending.size,0);
  e.c.__qx.error='real_unknown';
  await e.send('flight',{action:'stop'});
  await assert.rejects(e.send('flight',{action:'start'}),/real_unknown/);
  await assert.rejects(e.send('quests',{action:'accept',operationId:'one'}),/real_unknown/);
  assert.equal(e.calls.length,3);
 } else if(scenario==='flight_journal'){
  const e=environment({},null,[{resource:'flight',action:'tick'}]);
  assert.equal(e.c.__qx.error,'');assert.equal(e.storage.get('qingxi-local-pending:42'),'[]');
  for(const row of [{resource:'flight',action:'start'},{resource:'quests',action:'turn-in'},{resource:'field-cast'}]){
   const a=environment({},null,[row]);assert.equal(a.c.__qx.error,'quest_browser_pending_requires_review');
  }
 } else if(scenario==='proof_body_once'){
  let reads=0;const event={k:'use',s:100,pos:[0,0]};
  const e=await ready({},r=>{
   if(r!=='field-cast')return {ok:true,account:{playerId:42}};
   const response=new Response('{}');response.json=async()=>{reads++;return {ok:true,off:true}};
   response.clone=()=>{throw Error('second network body read')};return response;
  });
  const response=await e.send('field-cast',{events:[event]});
  assert.equal((await response.json()).off,true);assert.equal(reads,1);
  const cached=await e.send('field-cast',{events:[event]});
  assert.equal((await cached.json()).off,true);assert.equal(e.calls.length,2);
  assert.equal(e.c.__qx.pending.size,0);
 } else if(scenario==='proof_initial_inflight'){
  let release;const event={k:'use',s:100,pos:[0,0]};
  const e=await ready({},async(r,b)=>{
   if(r==='field-cast')await new Promise(resolve=>{release=resolve});
   return {ok:true,account:{playerId:42},acked:b.events?.map(event=>event.k+':'+event.s)};
  });
  const cast=e.send('field-cast',{events:[event]});
  while(!release)await new Promise(resolve=>setImmediate(resolve));
  await e.send('quests',{action:'field-engage',operationId:'engage'});
  assert.equal(e.calls.length,3);assert.equal(e.c.__qx.error,'');
  release();await cast;assert.equal(e.c.__qx.pending.size,0);
 } else if(scenario==='proof_guards'){
  const event={k:'use',s:100,pos:[0,0],key:'pierce'};
  for(const mode of ['changed','prefix','quiescing','identity','unverified_player','other_unknown']){
   const a=await ready({},(r,b)=>{if(r==='field-cast')throw Error('lost');return {ok:true,account:{playerId:42}}});
   await assert.rejects(a.send('field-cast',{events:[event]}),/lost/);
   const body={events:[event]};
   if(mode==='changed')body.events=[{...event,pos:[1,0]}];
   if(mode==='prefix')body.events=[{...event,s:101}];
   if(mode==='quiescing')a.c.__qx.quiescing=true;
   if(mode==='identity')body.playerId=43;
   if(mode==='unverified_player')a.c.__qx.player=0;
   if(mode==='other_unknown')a.c.__qx.error='other_unknown';
   await assert.rejects(a.send('field-cast',body));
   assert.equal(a.calls.length,2);assert.equal(a.c.__qx.pending.size,1);
  }
  const a=await ready({readOnly:true});
  await assert.rejects(a.send('field-cast',{events:[event]}),/quest_runner_read_only/);
  assert.equal(a.calls.length,1);
 } else if(scenario==='reviewed_journal'){
  const cast={resource:'field-cast',action:'events',operation_id:'field-cast:4',at:10};
  const e=environment({reviewedCastPending:[cast]},null,[cast]);
  assert.equal(e.c.__qx.error,'');assert.equal(e.storage.get('qingxi-local-pending:42'),'[]');
  for(const row of [{...cast,at:11},{...cast,resource:'quests'}, {...cast,action:'other'}]){
   const e=environment({reviewedCastPending:[cast]},null,[row]);
   assert.equal(e.c.__qx.error,'quest_browser_pending_requires_review');
  }
 } else if(scenario.startsWith('cast_')){
  const event={k:'use',s:100,pos:[0,0],key:'pierce'},next={k:'use',s:101,pos:[1,0],key:'pierce'};
  let attempts=0;const blocked=[];
  const e=await ready({},async(r,b)=>{
   if(r!=='field-cast')return {ok:true,account:{playerId:42}};
   attempts++;
   assert.match(e.storage.get('qingxi-local-pending:42'),/"events":\[/);
   assert.match(e.storage.get('qingxi-local-pending:42'),new RegExp('"attempts":'+attempts));
   if(scenario==='cast_exhausted'||attempts===1){
    if(scenario==='cast_network'||scenario==='cast_exhausted')throw Error('network');
    if(scenario==='cast_abort'){
     const response=new Response('{}');response.json=async()=>{throw Object.assign(Error('deadline'),{name:'AbortError'})};return response;
    }
    if(scenario==='cast_bad_json')return new Response('<html>bad</html>',{headers:{'content-type':'text/html'}});
    if(scenario==='cast_partial')return {ok:true,acked:[]};
    if(scenario==='cast_429')return new Response(JSON.stringify({ok:false}),{status:429});
    if(scenario==='cast_503')return new Response(JSON.stringify({ok:false}),{status:503});
    if(scenario==='cast_fatal')return new Response(JSON.stringify({ok:false,error:'auth_required'}),{status:401});
   }
   return {ok:true,acked:b.events.map(event=>event.k+':'+event.s)};
  });
  vm.runInContext(CAST_TRANSPORT,e.c);
  const transport=e.c.createDwellingCastTransport({
   request:async events=>{
    // Match official postJson/worldCastRequest, including loss of HTTP status
    // on fetch rejection and non-retryable HTTP 200 body parse failures.
    let response;try{response=await e.send('field-cast',{events,token:'secret',initData:'secret'})}
    catch{throw Error('api_unreachable')}
    const data=await response.json().catch(()=>({ok:false,error:'bad_response'}));
    if(!response.ok||data.ok!==true)throw Object.assign(Error(data.error||'request_failed'),{status:response.status});
    return data;
   },sleep:async()=>{},onError:()=>{
    blocked.push(assert.rejects(e.send('quests',{action:'field-defeat',operationId:'no'}),/quest_cast_pending|quest_cast_/));
    if(attempts===1&&scenario==='cast_partial')transport.enqueue(next);
   },
  });
  transport.enqueue(event);
  const ok=await transport.flush();await Promise.all(blocked);
  if(scenario==='cast_exhausted'||scenario==='cast_fatal'){
   assert.equal(ok,false);assert.equal(attempts,scenario==='cast_fatal'?1:3);
   assert.equal(e.c.__qx.pending.size,1);assert.ok(e.c.__qx.error);
   await transport.retry();assert.equal(attempts,scenario==='cast_fatal'?1:3);
  } else {
   assert.equal(ok,true);assert.equal(attempts,2);assert.equal(e.c.__qx.pending.size,0);
   assert.equal(e.c.__qx.error,'');assert.equal(e.c.__qx.castError,'');
   if(scenario==='cast_partial')assert.equal(e.calls.at(-1).body.events.length,2);
   if(scenario==='cast_abort')assert.equal(e.c.__qx.receipts.find(row=>row.resource==='field-cast').parse_error,'AbortError');
   if(scenario==='cast_bad_json')assert.equal(e.c.__qx.receipts.find(row=>row.resource==='field-cast').content_type,'text/html');
  }
 }
}
check(SCENARIO).catch(e=>{console.error(e);process.exitCode=1});
"""


@pytest.mark.parametrize('snapshot,phase', [
    ({'report': {'error': '主线运行达到设置的时限'}, 'overworld': True, 'combat_active': True}, 'bounded_stop'),
    ({'report': {'error': '主线运行达到设置的时限'}, 'overworld': False, 'combat_active': False}, 'bounded_stop'),
    ({'report': {'error': '主线运行达到设置的时限'}, 'overworld': False, 'combat_active': True}, 'held'),
    ({'report': {'error': '主线运行达到设置的时限'}}, 'held'),
    ({'report': {'error': '击杀结算未确认'}, 'overworld': True}, 'held'),
    ({'report': {'error': ''}}, 'done'),
])
def test_budget_stop_is_resumable_only_outside_an_active_instance(snapshot, phase):
    assert finished_phase(snapshot) == phase


@pytest.mark.parametrize('scenario', [
    'choices', 'fishing', 'durable_unknown', 'duplicate', 'singleflight',
    'native_cast', 'daily_full', 'daily_retry', 'pacing', 'readonly',
    'identity', 'journal_failure', 'status_cache', 'status_failure', 'housekeeping',
    'flight_cleanup', 'flight_journal',
    'cast_network', 'cast_abort', 'cast_bad_json', 'cast_partial', 'cast_429',
    'cast_503', 'cast_fatal', 'cast_exhausted',
    'proof_guards', 'reviewed_journal', 'proof_initial_inflight', 'proof_body_once', 'acknowledged_disk_journal',
    'release_rejection', 'release_journal',
])
def test_browser_policy(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise real browser policy code')
    path = tmp_path / 'policy.cjs'
    transport = (Path(__file__).parent / 'fixtures/dwelling-cast-transport.js').read_text()
    path.write_text('const GUARD=' + json.dumps(GUARD) + ';const SCENARIO=' + json.dumps(scenario)
                    + ';const CAST_TRANSPORT=' + json.dumps(transport) + ';\n' + HARNESS)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)


@pytest.mark.parametrize('scenario', ['active', 'ready', 'wrong_region', 'full', 'server_required',
                                     'failed_battle', 'reentrant', 'pickup_failure'])
def test_successful_combat_collects_only_missing_active_quest_loot(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise the quest combat adapter')
    script = """const assert=require('node:assert/strict');
const state={generation:1,story:{}},slot={status:'active',progress:{goods:0},required:{}};
const objective={id:'goods',type:'collect',itemId:'cargo',sourceEnemyRole:'blade',arena:'longwind',required:30};
const quest={id:'q5',region:'yue',objectives:[objective,{...objective}]},questCatalog={quests:[quest]};
const storySlot=()=>slot,storyData=()=>({world:{current:'yue'}}),readCombat=()=>({arena:'longwind'});
let battles=0,picked=[];function storyCheck(owner){assert.equal(owner,state.generation)}
async function pickupNeededCorpses(item,owner){picked.push(item);
 if(SCENARIO==='reentrant')await fightEnemy('ambusher',owner);
 if(SCENARIO==='pickup_failure')throw Error('pickup unconfirmed');
}
BODY
 battles++;if(SCENARIO==='failed_battle')throw Error('battle unconfirmed');
}
(async()=>{
 if(SCENARIO==='ready')slot.status='ready';
 if(SCENARIO==='wrong_region')quest.region='jiuguo';
 if(SCENARIO==='full')slot.progress.goods=30;
 if(SCENARIO==='server_required'){slot.required.goods=2;slot.progress.goods=2}
 if(['failed_battle','pickup_failure'].includes(SCENARIO))await assert.rejects(fightEnemy('bandit',1),/unconfirmed/);
 else await fightEnemy('bandit',1);
 assert.equal(picked.length,['active','reentrant','pickup_failure'].includes(SCENARIO)?1:0);
 assert.equal(battles,SCENARIO==='reentrant'?2:1);assert.ok(!state.story.collectingFieldLoot);
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace('SCENARIO', json.dumps(scenario)).replace('BODY', QUEST_COMBAT_LOOT)
    path = tmp_path / 'loot.cjs'
    path.write_text(script)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)


@pytest.mark.parametrize('scenario', ['loading', 'overworld', 'wrong_area', 'instance', 'timeout'])
def test_native_quest_entry_waits_for_models_without_opening_a_trial(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise the browser entry adapter')
    script = """const assert=require('node:assert/strict');
let calls=0;const objective={arena:'longwind',text:'任务'},owner=1,step=()=>{};
const readCombat=()=>({active:SCENARIO==='instance',overworld:SCENARIO==='overworld'});
const window={__rt:{canStartQuestCombat:()=>SCENARIO!=='wrong_area',startQuestCombat:()=>++calls>=3&&SCENARIO!=='timeout'}};
async function until(fn,timeout,label){assert.equal(timeout,90000);for(let i=0;i<4;i++){if(fn())return;await Promise.resolve()}throw Error('timeout')}
async function entry(){ BODY }
(async()=>{
 if(SCENARIO==='wrong_area'||SCENARIO==='instance'){await assert.rejects(entry());assert.equal(calls,0)}
 else if(SCENARIO==='timeout'){await assert.rejects(entry(),/timeout/);assert.equal(calls,4)}
 else{await entry();assert.equal(calls,SCENARIO==='loading'?3:0)}
})().catch(e=>{console.error(e);process.exitCode=1});
""".replace('SCENARIO', json.dumps(scenario)).replace('BODY', QUEST_COMBAT_ENTRY)
    path = tmp_path / 'entry.cjs'
    path.write_text(script)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)


@pytest.mark.parametrize('scenario', ['inferred_blocker', 'already_reviewed', 'real_wall', 'real_cliff'])
def test_navigation_review_replays_live_collision_without_disabling_geometry(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise the actual upstream planner')
    source = (VENDOR / 'trial-bot.user.js').read_text()
    planner = source[source.index('  class MinHeap {'):source.index('  function chooseWalkInput(')]
    surfaces = source[source.index('  function raisedSurface('):source.index('  function landingGround(')]
    fixture = Path(__file__).parent / 'fixtures/qingxi-lantern-collision.json'
    script = """const assert=require('node:assert/strict'),f=FIXTURE;
const p=f.origin,goal=[p[0]+1.2,p[1],p[2]],radius=0;
const dist=(a,b)=>Math.hypot(a[0]-b[0],a[2]-b[2]);
const fieldEnvironment={
 groundHeight(x){return SCENARIO==='real_cliff'&&x>p[0]+.5?4:f.ground},
 canWalk(x,z){if(SCENARIO==='real_wall')return false;
  const xi=Math.round((x-p[0])/f.step)-f.x_min,zi=Math.round((z-p[2])/f.step)-f.z_min;
  return f.walkable[zi]?.[xi]==='1';}
};
PLANNER
SURFACES
const obstacles=[[p[0]+.6,p[1],p[2]]],log=()=>{};
let path=findPath(p,goal,radius,fieldEnvironment,obstacles),walkRecovery=false,
 obstaclesReviewed=SCENARIO==='already_reviewed';
assert.equal(path,null);
REVIEW
}
if(SCENARIO==='inferred_blocker'){
 assert.ok(path?.length);assert.equal(obstacles.length,0);assert.equal(obstaclesReviewed,true);
 let previous=p;for(const next of path){assert.ok(walkSegment(previous,next));previous=next}
 assert.ok(walkGoalReached(previous,goal,radius));
}else{assert.equal(path,null);assert.equal(obstacles.length,1);assert.equal(walkRecovery,true)}
""".replace('FIXTURE', fixture.read_text()).replace('SCENARIO', json.dumps(scenario))
    script = script.replace('PLANNER', planner).replace('SURFACES', surfaces).replace('REVIEW', QUEST_NAVIGATION_REVIEW)
    path = tmp_path / 'navigation.cjs'
    path.write_text(script)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)


@pytest.mark.parametrize('scenario', ['confirmed_defence', 'story_limit', 'walk_timeout', 'unconfirmed_defence'])
def test_walking_budget_excludes_confirmed_combat_but_preserves_other_deadlines(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise the actual walking loop')
    source = browser_script(42)
    walk = source[source.index('  async function walkTo('):source.index('  // 地点来自任务目录')]
    script = """const assert=require('node:assert/strict');
let now=0,position=[0,0,0],defended=false;const goal=[1,0,0],state={generation:1,story:{}};
const Date={now:()=>now},dist=(a,b)=>Math.hypot(a[0]-b[0],a[2]-b[2]),readWorld=()=>({actorPosition:position});
const position3=p=>Array.isArray(p)&&p.length===3&&p.every(Number.isFinite);
async function storyReady(){if(SCENARIO==='story_limit'&&now>80000)throw Error('story limit')}
const walkGoalReached=(p,g,r)=>dist(p,g)<=r,travelFlightDecision=()=>({allowed:false}),
 fieldEnvironment={},findPath=()=>[goal],walkSegment=()=>true,releaseKeys=()=>{},setKeys=()=>{},
 chooseWalkInput=()=>({keys:['w']}),CFG={tickMs:10},log=()=>{};
async function defendNearby(){
 if(SCENARIO==='walk_timeout'||defended)return false;
 defended=true;now+=100000;if(SCENARIO==='unconfirmed_defence')throw Error('battle unconfirmed');return true;
}
async function storyWait(){now+=10;if(SCENARIO==='walk_timeout')now+=100000;else position=goal.slice()}
WALK
(async()=>{
 if(SCENARIO==='confirmed_defence'){await walkTo(goal,0,1);assert.deepEqual(position,goal)}
 else await assert.rejects(walkTo(goal,0,1),SCENARIO==='story_limit'?/story limit/:
   SCENARIO==='walk_timeout'?/寻路超时/:/battle unconfirmed/);
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace('SCENARIO', json.dumps(scenario)).replace('WALK', walk)
    path = tmp_path / 'walking-budget.cjs'
    path.write_text(script)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)


@pytest.mark.parametrize('scenario', ['live_stall', 'requested_engaged', 'no_threat', 'requested_dead',
                                     'requested_returning', 'all_dead'])
def test_field_combat_targets_live_threat_before_disengaged_original(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to replay the real field target selection')
    source = browser_script(42)
    helpers = source[source.index('  function livingEnemies('):source.index('  function protectFieldHealth(')]
    selection = source[source.index('        const requested ='):source.index('        if (!target) {')]
    fixture = (Path(__file__).parent / 'fixtures/qingxi-disengaged-target.json').read_text()
    script = """const assert=require('node:assert/strict'),f=FIXTURE;
const myPos=()=>f.actor,dist=(a,b)=>Math.hypot(a[0]-b[0],a[2]-b[2]);
HELPERS
function choose(s,enemyId){SELECT;return target?.id||null}
const s={overworld:true,enemies:f.enemies},original=s.enemies.find(e=>e.id===f.requested);
if(SCENARIO==='requested_engaged')original.engaged=true;
if(SCENARIO==='no_threat')s.enemies.forEach(e=>e.engaged=false);
if(SCENARIO==='requested_dead')original.hp=0;
if(SCENARIO==='requested_returning')original.returning=true;
if(SCENARIO==='all_dead')s.enemies.forEach(e=>e.hp=0);
const expected=SCENARIO==='all_dead'?null:['requested_engaged','no_threat'].includes(SCENARIO)?f.requested:'field-enemy-3';
assert.equal(choose(s,f.requested),expected);
""".replace('FIXTURE', fixture).replace('HELPERS', helpers).replace('SELECT', selection)
    script = script.replace('SCENARIO', json.dumps(scenario))
    path = tmp_path / 'field-target.cjs'
    path.write_text(script)
    subprocess.run(['node', str(path)], capture_output=True, check=True, timeout=8)
