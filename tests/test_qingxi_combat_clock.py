import json
import shutil
import subprocess

import pytest

from tools import qingxi_quest_browser as worker


def test_observed_native_steps_preserve_calls_and_stabilize_low_fps_qi(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the native clock replay')
    path = tmp_path / 'clock.cjs'
    path.write_text('const generated=' + json.dumps(worker.browser_script(8659059191))
        + ',original=' + json.dumps((worker.VENDOR / 'trial-bot.user.js').read_text()) + ';\n' + r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
function fixture(source) {
  let wall=0;
  const window={addEventListener(){},location:{href:'https://fixture.invalid/',origin:'https://fixture.invalid'},fetch:()=>{throw Error('network');}};
  const document={hidden:false,readyState:'loading',addEventListener(){},getElementById(){return null;},querySelector(){return null;},querySelectorAll(){return [];}};
  const context=vm.createContext({window,document,console:{log(){},warn(){}},performance:{now:()=>wall*1000},
    Date:class extends Date{static now(){return wall*1000;}},localStorage:{getItem(){return null;},setItem(){}},
    setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},URL,URLSearchParams,Response,
    fetch:window.fetch,KeyboardEvent:class{}});
  vm.runInContext(source.replace('  // ============ 10.','window.test={qiThreats,qiFlights};\n  // ============ 10.'),context);
  let active=true,paused=false,enabled=true,elapsed=0,throwNow=false;
  const calls=[],result={native:true},args=[];
  const controller={get active(){return active;},get paused(){return paused;},get needsModels(){return enabled;},step(dt,extra){
    calls.push(this);args.push([dt,extra]);if(throwNow)throw Error('native failure');
    if(!paused&&active&&enabled)elapsed+=dt;return result;
  }};
  window.createDwellingFieldCombat=()=>controller;
  window.createDwellingFieldCombat(null,{});
  return {window,controller,calls,args,result,advance(dt){wall+=dt;},elapsed:()=>elapsed,
    setActive(value){active=value;},setPaused(value){paused=value;},setEnabled(value){enabled=value;},setThrow(){throwNow=true;}};
}
const ownership=fixture(generated),api=ownership.window.qingxiCombatClock;
const extra={literal:'unchanged'};
assert.equal(ownership.controller.step(.051,extra),ownership.result);
assert.equal(ownership.calls[0],ownership.controller);
assert.equal(ownership.args[0][0],.051);assert.equal(ownership.args[0][1],extra);
assert.equal(api.snapshot().elapsedSeconds,.051);
ownership.advance(100);assert.equal(api.snapshot().elapsedSeconds,.051);
ownership.setPaused(true);ownership.controller.step(.2);assert.equal(api.snapshot().elapsedSeconds,.051);
ownership.setPaused(false);ownership.setActive(false);ownership.controller.step(.2);assert.equal(api.snapshot().elapsedSeconds,.051);
ownership.setActive(true);ownership.setEnabled(false);ownership.controller.step(.2);assert.equal(api.snapshot().elapsedSeconds,.051);
ownership.setEnabled(true);
ownership.setActive(true);assert.equal(api.capture(ownership.controller),true);
ownership.controller.step(.049);assert.equal(api.snapshot().elapsedSeconds,.1);
assert.equal(ownership.calls.length,5,'duplicate capture must not double invoke native step');
ownership.window.__fc={};assert.throws(()=>api.requireClock(ownership.controller),/原生帧时钟/);
ownership.window.__fc=ownership.controller;
ownership.setThrow();assert.throws(()=>ownership.controller.step(.1),/native failure/);
assert.throws(()=>api.requireClock(ownership.controller),/原生帧时钟/);

function replay(source,wallStep) {
  const f=fixture(source),frames=[];let previous=null,resets=0;
  for(let n=0;n<6;n++) {
    f.advance(wallStep);f.controller.step(.25);
    const e={id:'boss',role:'boss',state:'attack',move:'bossQi',hp:100,maxHp:100,time:f.elapsed(),position:[0,0,10]};
    const out=f.window.test.qiThreats({overworld:true,wave:null,enemies:[e]},[n*.1,0,0],(n+1)*wallStep*1000);
    const shot=f.window.test.qiFlights.get('boss');if(shot!==previous){resets++;previous=shot;}
    frames.push(out[0]?.critical);
  }
  return {resets,frames};
}
assert.equal(replay(original,1/2.6).resets,3,'baseline must reproduce repeated shot capture');
const slow=replay(generated,1/2.6),fast=replay(generated,.25);
assert.equal(slow.resets,1);assert.deepEqual(slow,fast);
// Synthetic animation and motion inputs above do not stand in for actual hit receipts.
''')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True)


def test_frame_clock_hook_is_scoped_to_quest_workers():
    for options in ({'read_only': True}, {'reward_batch': True},
                    {'reward_batch': True, 'daily_id': 'moonpalace'}):
        source = worker.browser_script(8659059191, **options)
        assert 'qingxiCombatClock.capture(obj)' not in source
        assert 'qingxiCombatClock.requireClock(window.__fc)' not in source
