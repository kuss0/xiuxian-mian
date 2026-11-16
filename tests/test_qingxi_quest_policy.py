"""Exercise the browser boundary in Node without network or live game state."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.qingxi_quest_browser import GUARD, QUEST_COMBAT_ENTRY


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
 if(scenario==='choices'){
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
  await assert.rejects(e.send('quests',{action:'accept',operationId:'one'}),/read_only/);
  assert.equal(e.calls.length,2);
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
 } else if(scenario==='proof_guards'){
  const event={k:'use',s:100,pos:[0,0],key:'pierce'};
  for(const mode of ['changed','prefix','quiescing','identity','readonly','other_unknown']){
   const a=await ready({},(r,b)=>{if(r==='field-cast')throw Error('lost');return {ok:true,account:{playerId:42}}});
   await assert.rejects(a.send('field-cast',{events:[event]}),/lost/);
   const body={events:[event]};
   if(mode==='changed')body.events=[{...event,pos:[1,0]}];
   if(mode==='prefix')body.events=[{...event,s:101}];
   if(mode==='quiescing')a.c.__qx.quiescing=true;
   if(mode==='identity')body.playerId=43;
   if(mode==='readonly')a.c.__qx.player=0;
   if(mode==='other_unknown')a.c.__qx.error='other_unknown';
   await assert.rejects(a.send('field-cast',body));
   assert.equal(a.calls.length,2);assert.equal(a.c.__qx.pending.size,1);
  }
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


@pytest.mark.parametrize('scenario', [
    'choices', 'fishing', 'durable_unknown', 'duplicate', 'singleflight',
    'native_cast', 'daily_full', 'daily_retry', 'pacing', 'readonly',
    'identity', 'journal_failure', 'status_cache', 'status_failure', 'housekeeping',
    'flight_cleanup', 'flight_journal',
    'cast_network', 'cast_abort', 'cast_bad_json', 'cast_partial', 'cast_429',
    'cast_503', 'cast_fatal', 'cast_exhausted',
    'proof_guards', 'reviewed_journal',
])
def test_browser_policy(scenario, tmp_path):
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise real browser policy code')
    path = tmp_path / 'policy.cjs'
    transport = (Path(__file__).parent / 'fixtures/dwelling-cast-transport.js').read_text()
    path.write_text('const GUARD=' + json.dumps(GUARD) + ';const SCENARIO=' + json.dumps(scenario)
                    + ';const CAST_TRANSPORT=' + json.dumps(transport) + ';\n' + HARNESS)
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
