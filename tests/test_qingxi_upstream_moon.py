import json
import shutil
import subprocess

import pytest

from tools import qingxi_quest_browser as worker


def test_pinned_moon_controller_mana_cast_and_interrupt_boundaries(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the pinned Moon Palace controller')
    source = worker.browser_script(42, reward_batch=True, daily_id='moonpalace')
    start = source.index('  function moonSword(s) {')
    native = source[start:source.index('  function goldMirror(', start)]
    path = tmp_path / 'moon.cjs'
    path.write_text('const native=' + json.dumps(native) + ';\n' + r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
function fixture({mana=251, cast=0, skill='safe', rule={}, phase=1, channel=null}={}) {
  const actions=[], movement=[], state={story:{},skillRetryAt:{},orbitDir:1,lastLockAt:0};
  const view={mode:'fight',phase,channel,counts:{interrupts:0,channels:0},swords:{}};
  const treasure={cost:50,damage:100,range:30,cast,...rule};
  const window={__rt:{mobileHudState:()=>({blocked:false,profile:{combat:{manaCostScale:1},mana:{regenPerSecond:0}}})},
    qingxiNgqBoss:{stats:()=>view},QingxiGiantSwordMath:{reach:14,strike:1.1},
    QingxiTreasureRules:{skills:{[skill]:treasure},templates:{},arts:{},giant:{cost:80,range:14}}};
  const snapshot={arena:'moonpalace',hp:1000,hpMax:1000,mana,wave:{time:100},target:'boss',
    paused:false,airborne:false,cooldown:{pierce:0,giant:0},arts:[],
    kit:[{skill:'qingzhu',category:'sword',cost:8,slot:1},
      {skill,category:'artifact',cost:50,slot:2,cooldown:0}],metrics:{playerHits:10000}};
  const target={id:'boss',position:[10,0,0],hp:1000}, step={want:0,critical:false,mobile:false,roll:false};
  const context=vm.createContext({window,state,document:{hidden:false},Date:{now:()=>100000},Math,Number,
    myPos:()=>[0,0,0],dist:(a,b)=>Math.hypot(a[0]-b[0],a[2]-b[2]),
    bearingTo:()=>0,groundHazards:()=>[],releaseKeys:()=>movement.push('release'),
    steerWalkable:()=>movement.push('walk'),dodgeThreat:()=>false,attack:()=>{},castUltimate:()=>false,
    combatAction:command=>{actions.push(command);return true;},act:()=>true,log:()=>{}});
  vm.runInContext(native,context);
  return {actions,movement,state,snapshot,step,view,
    frame:(time=100)=>{snapshot.wave.time=time;return context.moonCombatFrame(snapshot,target,step,{},time*1000);}};
}
for (const [mana,expected] of [[251,'slot2'],[250,'pierce'],[200,'pierce'],[8,'pierce'],[7,null]]) {
  const f=fixture({mana});assert.equal(f.frame(),true);
  assert.deepEqual(f.actions,expected?[expected]:[]);
  assert.equal(f.state.story.moonCombat.hits,10000); // High accumulated hits never stop point attacks.
}
// Native third-phase giant reserve still limits point attacks, independently of the 200 mana floor.
for (const [mana,expected] of [[88,'pierce'],[87,null]]) {
  const f=fixture({mana,phase:3});f.frame();
  assert.equal(f.state.story.moonCombat.manaReserve,80);
  assert.deepEqual(f.actions,expected?[expected]:[]);
}
// Blood-contract interrupts remain legal below the ordinary-skill mana floor.
const interrupt=fixture({mana:80,channel:.1});interrupt.frame();
assert.deepEqual(interrupt.actions,['swords']);
assert.equal(interrupt.state.story.moonCombat.interrupts,0); // Sending does not fabricate confirmation.
for (const options of [{rule:{dot:1}},{rule:{dotSeconds:1}},{rule:{kind:'sustain'}},
    {rule:{traits:{poison:1}}},{skill:'phantasm_wheel'}]) {
  const f=fixture(options);f.frame();assert.deepEqual(f.actions,['pierce']);
}
// Native safe casting first releases movement, waits, then manages the actual cast.
const cast=fixture({cast:1});cast.frame();assert.deepEqual(cast.actions,[]);
assert.ok(cast.state.story.moonCombat.preparingCast);
cast.frame(100.2);assert.deepEqual(cast.actions,['slot2']);
assert.equal(cast.state.story.moonCombat.managedCast.command,'slot2');
cast.snapshot.casting=true;cast.snapshot.castProgress=.1;
const walks=cast.movement.filter(x=>x==='walk').length;
cast.frame(100.3);assert.equal(cast.movement.filter(x=>x==='walk').length,walks);
cast.step.critical=true;cast.frame(100.4);
assert.ok(cast.movement.filter(x=>x==='walk').length>walks);
assert.equal(cast.state.story.moonCombat.managedCast,null);
// A first-time third-phase sample has no known safe pool/channel interval for starting a cast.
const unknown=fixture({cast:1,phase:3,mana:1000});unknown.frame();
assert.deepEqual(unknown.actions,['pierce']);
''')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True)
