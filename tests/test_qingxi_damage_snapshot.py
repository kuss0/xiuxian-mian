"""Serialize the native damage ring without a browser, commands or network."""
import json
import shutil
import subprocess

import pytest

from tools import qingxi_quest_browser as worker


def test_native_damage_snapshot_is_bounded_typed_and_observation_only(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for the real snapshot expression')
    source = r"""
const assert=require('node:assert/strict');
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
let report=null,calls={report:0,combat:0,quests:0,network:0,commands:0};
global.fetch=()=>{calls.network++;throw Error('network forbidden');};
global.window={__qx:{player:42,pending:new Map(),rewards:new Map()},
 __autobot:{report(){calls.report++;return report;}},
 __fc:{stats(){calls.combat++;return {hp:9744,active:false,state:'ready'};},command(){calls.commands++;throw Error('command forbidden');}},
 __quests:{snapshot(){calls.quests++;return {states:{qx_q15:{status:'active'}}};}}};
global.localStorage={getItem(){return null;}};
const read=()=>eval(snapshot);
// Synthetic serializer fixture; no observed WA/Baji hit or attacker is claimed.
const nativeRow={time:533330.125,hpBefore:5953.68,hp:2163.36,hpMax:9744,mana:312.25,stamina:86.5,
 action:'cast',keys:'wa',position:[-33.412,0,-484.954],hpPotions:0,hpPotionCooldown:1.25,
 arena:'lantern',wave:null,phase:'野外反击：黑衣剑宗',treasureFx:['pierce'],shots:['qi'],
 enemies:[{id:'field-enemy-3',name:'黑衣剑宗',role:'boss',phase:2,move:'bossQi',time:.625,
 state:'attack',telegraph:true,distance:5.75,behavior:{secret:'never export'},token:'never export'}],
 authentication:'never export'};
report=freeze({phase:'已停止',damage:Array.from({length:15},(_,i)=>({...nativeRow,time:i}))});
const before=JSON.stringify(report),value=read();
assert.equal(JSON.stringify(report),before);
assert.equal(value.report.damage.length,12);
assert.deepEqual(value.report.damage.map(row=>row.time),Array.from({length:12},(_,i)=>i+3));
const last=value.report.damage.at(-1);
assert.equal(last.hp,2163.36);assert.equal(last.hpMax,9744);assert.equal(last.mana,312.25);
assert.equal(last.stamina,86.5);assert.equal(last.hpPotionCooldown,1.25);
assert.deepEqual(last.position,[-33.412,0,-484.954]);
assert.equal(last.enemies[0].time,.625);assert.equal(last.enemies[0].distance,5.75);
assert.equal(last.enemies[0].telegraph,true);assert.equal(last.wave,null);
assert.equal('authentication' in last,false);assert.equal('behavior' in last.enemies[0],false);
assert.equal('token' in last.enemies[0],false);
assert.deepEqual(value.states,{qx_q15:{status:'active'}});assert.equal(value.pending,0);
// Bounds apply independently to native rows, labels, coordinates and candidates.
const long='x'.repeat(1000),enemy={...nativeRow.enemies[0],id:long,name:long,role:long,move:long,state:long};
report=freeze({damage:[{...nativeRow,time:Infinity,hp:NaN,mana:'123',position:[1,NaN,3],
 action:long,keys:long,arena:long,phase:long,hpPotions:{secret:true},
 treasureFx:Array(100).fill(long),shots:[{},null,...Array(100).fill(long)],
 enemies:Array.from({length:100},(_,i)=>({...enemy,phase:NaN,time:-Infinity,distance:'1',telegraph:'true',id:String(i)+long}))}]});
const bounded=JSON.parse(JSON.stringify(read().report.damage))[0];
assert.equal(bounded.time,null);assert.equal(bounded.hp,null);assert.equal(bounded.mana,null);
assert.equal(bounded.position,null);assert.equal(bounded.hpPotions,null);
assert.equal(bounded.action.length,32);assert.equal(bounded.keys.length,32);
assert.equal(bounded.arena.length,40);assert.equal(bounded.phase.length,120);
assert.equal(bounded.treasureFx.length,8);assert.ok(bounded.shots.length<=8);
assert.ok([...bounded.treasureFx,...bounded.shots].every(label=>label.length<=40));
assert.equal(bounded.enemies.length,3);assert.ok(bounded.enemies[0].id.startsWith('0'));
assert.equal(bounded.enemies[0].id.length,100);assert.equal(bounded.enemies[0].name.length,80);
assert.equal(bounded.enemies[0].role.length,40);assert.equal(bounded.enemies[0].move.length,40);
assert.equal(bounded.enemies[0].state.length,40);assert.equal(bounded.enemies[0].phase,null);
assert.equal(bounded.enemies[0].time,null);assert.equal(bounded.enemies[0].distance,null);
assert.equal(bounded.enemies[0].telegraph,null);
assert.ok(JSON.stringify(bounded).length<5000);
for(const damage of [undefined,null,{},'not a ring',[]]){report={damage};assert.deepEqual(read().report.damage,[]);}
for(const position of [[1,2],[1,2,3,4],[1,'0',3]]){report={damage:[{...nativeRow,position}]};assert.equal(read().report.damage[0].position,null);}
report={damage:[null,1,'bad',[],{...nativeRow,enemies:[null,[],1]}]};
assert.equal(read().report.damage.length,1);assert.deepEqual(read().report.damage[0].enemies,[]);
report=null;assert.equal(read().report,null);
assert.equal(calls.network,0);assert.equal(calls.commands,0);
assert.equal(calls.report,calls.combat);assert.equal(calls.report,calls.quests);
"""
    path = tmp_path / 'damage-snapshot.cjs'
    path.write_text('const snapshot=' + json.dumps(worker.SNAPSHOT) + ';\n' + source)
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=10)
