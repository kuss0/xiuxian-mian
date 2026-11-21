"""Single-daily native sequencing and fetch boundaries; Node, no browser/network."""
import json
import subprocess
from pathlib import Path

import pytest

from tools import qingxi_quest_browser as worker


SEQUENCER = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const ids=['moonpalace','bloodland','southgate','escort','mulanshrine','bqpuppet','jianzhong'];
const defs=[['moon','worldMoonRequest'],['blood','worldBloodRequest'],['siege','worldSiegeRequest'],
 ['escort','worldEscortRequest'],['shrine',null,'mulan-shrine','mulanShrine'],
 ['puppet',null,'baiqiao-puppet','baiqiaoPuppet'],['jianzhong',null,'jianzhong-trial','jianzhongTrial']];
const daily=ids.map(id=>({id,day:'2026-11-21',hasKey:true,claimed:false,status:'pending',exhausted:false}));
const quotas=Object.fromEntries(['longwind','bamboo'].map(arena=>[arena,
 {arena,day:'2026-11-21',used:0,remaining:3,limit:3,activeRun:null}]));
const q={player:42,error:'',quiescing:false,pending:new Map(),queue:Promise.resolve(),statusCache:new Map(),singleDailyReceipts:[]};
let native=null,calls=[];
const c={Date,Map,Set,Object,Array,JSON,console,__qx:q,localStorage:{getItem:()=>null},
 __fc:{stats:()=>({active:false,overworld:false})}};
const status=i=>({ok:true,[defs[i][0]]:{day:daily[i].day,hasKey:daily[i].hasKey,claimed:daily[i].claimed,
 run:null,failClaimed:daily[i].exhausted}});
defs.forEach((d,i)=>{if(d[1])c[d[1]]=async()=>status(i)});
c.qingxiInstanceRequest=async resource=>{const i=defs.findIndex(d=>d[2]===resource);return {ok:true,[defs[i][3]]:status(i)}};
c.worldTrialRequest=async(_action,{arena})=>({ok:true,status:JSON.parse(JSON.stringify(quotas[arena]))});
c.__autobot={checkDailyRewards:async()=>{},handoff:()=>({rewardRequests:{}}),
 report:()=>({error:'',dailyRewards:{verified:true,errors:0,day:'2026-11-21',rows:daily},batch:native}),
 daily:async()=>{throw Error('must not batch daily')},trials:async()=>{throw Error('must not run trials')},
 dailyOne:async id=>{
  calls.push(id);assert.equal(id,selected);
  if(scenario==='unknown') {q.pending.set('unconfirmed',{operation_id:'original'});throw Error('unknown result')}
  const resource=['moon-palace','blood-land','south-gate','refugee-escort','mulan-shrine','baiqiao-puppet','jianzhong-trial'][ids.indexOf(id)];
  const outcome=scenario==='defeat'?'defeat':'claimed';
  q.singleDailyReceipts=[{ok:true,resource,action:'start',run_id:'single-run',run_rewarded:true},
   {ok:true,resource,action:'end',run_id:'single-run',result_kind:outcome==='claimed'?'clear':'failure',
    result_rewarded:true,request_result:outcome==='claimed'?'victory':'defeat',grants:[{name:'灵石',quantity:12}],cultivation:0}];
  if(scenario==='wrong_run')q.singleDailyReceipts[1].run_id='other';
  if(scenario==='double_start')q.singleDailyReceipts.push({...q.singleDailyReceipts[0]});
  native={mode:'single_daily',status:'complete',outcome};
  if(outcome==='claimed'){const row=daily.find(r=>r.id===id);row.claimed=true;row.status='claimed'}
  if(scenario==='other_changed'){daily.find(r=>r.id!==id).claimed=true;daily.find(r=>r.id!==id).status='claimed'}
  if(scenario==='trial_changed'){quotas.bamboo.used++;quotas.bamboo.remaining--}
 }};
if(scenario==='locked')daily.find(row=>row.id===selected).hasKey=false;
if(scenario==='claimed'){daily.find(row=>row.id===selected).claimed=true;daily.find(row=>row.id===selected).status='claimed'}
if(scenario==='locked')daily.find(row=>row.id===selected).status='locked';
c.window=c;vm.createContext(c);vm.runInContext(SOURCE,c);
(async()=>{
 const failed=['unknown','wrong_run','double_start','other_changed','trial_changed'].includes(scenario);
 if(failed){await assert.rejects(c.__qingxiRewardBatch.runOne(selected));assert.equal(c.__qingxiRewardBatch.oneState.phase,'held')}
 else {const proof=await c.__qingxiRewardBatch.runOne(selected);assert.equal(proof.daily_id,selected);
  assert.equal(proof.phase,scenario==='defeat'||scenario==='claimed'||scenario==='locked'?'partial':'complete')}
 assert.deepEqual(calls,['locked','claimed'].includes(scenario)?[]:[selected]);
 await assert.rejects(c.__qingxiRewardBatch.runOne(selected),/already_attempted/);
 assert.equal(calls.length,['locked','claimed'].includes(scenario)?0:1);
 assert.equal(c.__qingxiRewardBatch.oneState.running,false);
})().catch(e=>{console.error(e);process.exitCode=1});
"""


def run_node(tmp_path, source):
    script = tmp_path / 'single-daily.cjs'
    script.write_text(source)
    subprocess.run(['node', str(script)], check=True, capture_output=True, text=True, timeout=12)


@pytest.mark.parametrize('selected', ['moonpalace', 'bloodland', 'southgate', 'escort', 'mulanshrine', 'bqpuppet', 'jianzhong'])
def test_single_daily_runs_only_selected_native_entry(tmp_path, selected):
    batch = Path(worker.__file__).with_name('qingxi_reward_batch.js').read_text().replace('__PLAYER__', '42')
    run_node(tmp_path, 'const scenario="clear",selected=' + json.dumps(selected) + ';const SOURCE=' + json.dumps(batch) + ';' + SEQUENCER)


@pytest.mark.parametrize('scenario', ['defeat', 'unknown', 'wrong_run', 'double_start', 'other_changed', 'trial_changed', 'locked', 'claimed'])
def test_single_attempt_stops_on_defeat_unknown_or_changed_facts(tmp_path, scenario):
    batch = Path(worker.__file__).with_name('qingxi_reward_batch.js').read_text().replace('__PLAYER__', '42')
    run_node(tmp_path, 'const scenario=' + json.dumps(scenario) + ',selected="moonpalace";const SOURCE=' + json.dumps(batch) + ';' + SEQUENCER)


POLICY = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const base='https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/';
let now=0,calls=[];
const c={URL,Request,Response,Map,Set,Object,JSON,Array,console,
 Date:class extends Date {static now(){return Date.parse(scenario==='midnight'?'2026-11-21T15:50:00Z':'2026-11-21T04:00:00Z')}},
 performance:{now:()=>now},setTimeout:(f,n)=>{now+=n;queueMicrotask(f)},
 location:{href:base,origin:'https://asc.aiopenai.app'},
 localStorage:{getItem:()=>null,setItem:()=>{}},
 fetch:async(input,opts)=>{const resource=new URL(input).pathname.split('/').at(-1),body=JSON.parse(opts.body);calls.push([resource,body]);
  if(scenario==='unknown'&&resource==='moon-palace'&&body.action==='start')throw Error('offline');
  if(resource==='start')return new Response(JSON.stringify({ok:true,account:{playerId:42}}));
  const status={day:'2026-11-21',hasKey:true,claimed:false,run:null};
  return new Response(JSON.stringify({ok:true,moonPalace:{moon:status,run:body.action==='start'?{id:'single-run',rewarded:true}:null}}));
 }};
c.window=c;vm.createContext(c);vm.runInContext(GUARD.replace('__CONFIG__',JSON.stringify({player:42,rewardBatch:true,
 singleDaily:'moonpalace',singleDailyRunSeconds:3600})),c);
const send=(resource,body)=>c.fetch(base+resource,{body:JSON.stringify(body)});
(async()=>{
 await send('start',{});
 if(scenario==='unknown'){
  await assert.rejects(send('moon-palace',{action:'start',operationId:'original'}));
  await assert.rejects(send('moon-palace',{action:'start',operationId:'new'}));
  assert.equal(calls.filter(([r,b])=>r==='moon-palace'&&b.action==='start').length,1);
  assert.equal(c.__qx.pending.size,1);
 }else if(scenario==='midnight'){
  await assert.rejects(send('moon-palace',{action:'start',operationId:'original'}),/midnight_budget/);
  assert.equal(calls.filter(([r,b])=>r==='moon-palace'&&b.action==='start').length,0);
 }else{
  for(const resource of ['blood-land','south-gate','refugee-escort','mulan-shrine','baiqiao-puppet','jianzhong-trial','trial'])
   for(const action of ['start','checkpoint','end'])
    await assert.rejects(send(resource,{action,operationId:resource+'-'+action,arena:'longwind',tier:1}),/single_daily_write_scope/);
  assert.equal(calls.filter(([r])=>r!=='start').length,0);
  await send('blood-land',{action:'status'});
  assert.equal(calls.filter(([r,b])=>r==='blood-land'&&b.action==='status').length,1);
 }
})().catch(e=>{console.error(e);process.exitCode=1});
"""


@pytest.mark.parametrize('scenario', ['scope', 'unknown', 'midnight'])
def test_policy_blocks_every_other_dungeon_trial_and_unknown_reopen(tmp_path, scenario):
    run_node(tmp_path, 'const scenario=' + json.dumps(scenario) + ';const GUARD=' + json.dumps(worker.GUARD) + ';' + POLICY)


def test_single_native_injection_keeps_vendor_pinned_and_removes_defeat_restart(tmp_path):
    script = worker.browser_script(42, reward_batch=True, daily_id='moonpalace')
    assert 'async function completeSingleDaily(id)' in script
    start = script.index("if (s.state === 'defeat' && !q)")
    end = script.index("if (s.state === 'victory')", start)
    assert 'singleDefeat:true' in script[start:end]
    assert "act('start')" not in script[start:end]
    assert 'objective.drill === true' in script  # Existing practice mode is not reused.
    file = tmp_path / 'injected.js'
    file.write_text(script)
    subprocess.run(['node', '--check', str(file)], check=True, capture_output=True, timeout=10)


@pytest.mark.parametrize('daily_id,resource,envelope,key,native', [
    ('moonpalace','moon-palace','moonPalace','moon','worldMoonRequest'),
    ('bloodland','blood-land','bloodLand','blood','worldBloodRequest'),
    ('southgate','south-gate','southGate','siege','worldSiegeRequest'),
    ('escort','refugee-escort','refugeeEscort','escort','worldEscortRequest'),
    ('mulanshrine','mulan-shrine','mulanShrine','shrine',None),
    ('bqpuppet','baiqiao-puppet','baiqiaoPuppet','puppet',None),
    ('jianzhong','jianzhong-trial','jianzhongTrial','jianzhong',None),
])
@pytest.mark.parametrize('omit', ['', 'grants', 'cultivation', 'conflicting_run', 'conflicting_alias'])
def test_existing_native_reader_functions_preserve_end_envelopes_and_unknown_reward_shape(
        tmp_path, daily_id, resource, envelope, key, native, omit):
    # Actual native functions and status shapes from the pre-existing read-only
    # fixture; start/end bodies are mocks of fields consumed by the official
    # client, not newly obtained server success receipts.
    fixture = json.loads((Path(__file__).parent / 'fixtures/qingxi-native-reward-readers-20261120.json').read_text())
    status = next(row['status'] for row in fixture['reward_statuses'] if row['resource'] == resource)
    config = {'player':42,'rewardBatch':True,'singleDaily':daily_id,'singleDailyRunSeconds':3600}
    bootstrap = r"""
const state={preview:false,token:'fixture',initData:'fixture',sectionLoaded:{inventory:true}},worldQuests={refresh(){}};
const worldStoryContentAvailable=()=>true;
async function postJson(path,body,options){return await (await window.fetch(new URL(path,location.origin).href,
 {method:'POST',body:JSON.stringify(body),signal:options.signal})).json()}
""" + '\n'.join(fixture['functions'])
    request = native if native else '(action,extra)=>window.qingxiInstanceRequest(' + json.dumps(resource) + ',action,extra)'
    source = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');let now=0;
const c={URL,URLSearchParams,Request,Response,Map,Set,Object,JSON,Array,console,AbortController,
 Date:class extends Date {static now(){return Date.parse('2026-11-21T04:00:00Z')}},
 performance:{now:()=>now},setTimeout:(f,ms)=>{if(ms===12000)return 0;now+=ms;queueMicrotask(f);return 1},clearTimeout:()=>{},
 location:{href:'https://asc.aiopenai.app/fixture',origin:'https://asc.aiopenai.app'},
 localStorage:{getItem:()=>null,setItem:()=>{}},
 fetch:async(input,options)=>{
  const body=JSON.parse(options.body);
  if(new URL(input).pathname.endsWith('/start'))return new Response(JSON.stringify({ok:true,account:{playerId:42}}));
  let data;
  if(body.action==='status')data={[KEY]:{...STATUS,day:'2026-11-21',claimed:false,hasKey:true,run:null}};
  else if(body.action==='start')data={run:{id:'native-fixture-run',rewarded:true}};
  else {data={kind:'clear',rewarded:true,grants:[{name:'灵石',quantity:12}],cultivation:100};
   if(OMIT==='conflicting_run')data.run={id:'other-run'};
   else if(OMIT==='conflicting_alias')data.runId='other-run';else if(OMIT)delete data[OMIT]}
  return new Response(JSON.stringify({ok:true,[ENVELOPE]:data}));
 }};
c.window=c;vm.createContext(c);vm.runInContext(GUARD,c);vm.runInContext(BOOTSTRAP,c);
(async()=>{
 await c.fetch(c.location.origin+'/api/miniapp/xianxia-dwelling/start',{body:'{}'});
 const request=vm.runInContext(REQUEST,c);
 await request('start',{operationId:'native-start'});
 const ended=await request('end',{operationId:'native-end',runId:'native-fixture-run',result:'victory'});
 if(OMIT.startsWith('conflicting_')){
  assert.equal(c.__qx.error,'single_daily_run_mismatch');assert.equal(c.__qx.pending.size,1);
  assert.equal(c.__qx.singleDailyReceipts.length,1);assert.ok(ended?.error);return;
 }
 const row=c.__qx.singleDailyReceipts[1];
 assert.equal(c.__qx.singleDailyReceipts.length,2);
 assert.equal(row.resource,RESOURCE);assert.equal(row.run_id,'native-fixture-run');
 assert.equal(row.result_kind,'clear');assert.equal(row.result_rewarded,true);
 assert.equal(row.request_result,'victory');
 if(OMIT)assert.equal(row[OMIT],null);
 else {assert.equal(row.grants[0].quantity,12);assert.equal(row.cultivation,100)}
 const payload=ENVELOPE==='moonPalace'||ENVELOPE==='bloodLand'||ENVELOPE==='southGate'||ENVELOPE==='refugeeEscort'?ended:ended[ENVELOPE];
 assert.equal(payload.kind,'clear');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    constants = {'KEY':key,'STATUS':status,'OMIT':omit,'ENVELOPE':envelope,'RESOURCE':resource,
                 'GUARD':worker.GUARD.replace('__CONFIG__', json.dumps(config)), 'BOOTSTRAP':bootstrap, 'REQUEST':request}
    run_node(tmp_path, '\n'.join('const '+name+'='+json.dumps(value)+';' for name,value in constants.items()) + source)
