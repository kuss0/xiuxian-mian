'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const distance=(a,b)=>Math.hypot(a[0]-b[0],a[2]-b[2]);
function fixture({arena='field',overworld=true,road=false}={}) {
  let now=10000,lastTab=-Infinity,lastTarget=null;
  const world={actorPosition:[-7,0,450],yaw:0};
  const calls=[],hits=[];
  const document={hidden:false,readyState:'loading',addEventListener(){},getElementById:id=>id==='dwellingWorldCanvas'?{dispatchEvent(){}}:null,querySelector:()=>null,querySelectorAll:()=>[]};
  const context=vm.createContext({window:{addEventListener(){}},document,console:{log(){},warn(){}},performance:{now:()=>now},Date:class extends Date{static now(){return now;}},
    localStorage:{getItem(){return null;},setItem(){}},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},URL,URLSearchParams,Response,
    KeyboardEvent:class{constructor(type,o){Object.assign(this,{type},o);}}});
  const source=fs.readFileSync(`${__dirname}/trial-bot.user.js`,'utf8');
  vm.runInContext(source.replace('  // ============ 10.',`window.test={focusedEnemy,fieldThreat,lockEnemy,escortEnemy,escortBoundary,storyBattleFrame,keyHeading,movementBasis,state};\n  // ============ 10.`),context);
  const w=context.window;
  const s={active:true,arena,overworld,state:'fighting',wave:{index:3,time:10,interlude:0},target:null,targetSource:'auto',enemies:[],hp:1000,hpMax:1000,mana:1000,stamina:100,momentum:0,
    cooldown:{pierce:0,spell:1,dodge:0},potions:{},gcdLeft:0,casting:null,escort:{dist:320.68183916922493}};
  w.createDwellingFieldCombat=Object.assign(()=>({}),{questLocations:()=>({escort:{position:[-7,320],radius:215}})});
  w.createDwellingFieldCombat(null,{environment:{groundHeight:()=>0,canWalk:(x,z)=>!road||(x>=-18&&x<=4&&z>=210&&z<=535)}});
  w.__fc={stats:()=>({...s})};w.__rt={stats:()=>world,combatAction(id){
    calls.push(id);
    if(id==='lock') {
      const list=s.enemies.filter(e=>e.hp>0&&!e.hidden&&!e.returning&&distance(world.actorPosition,e.position)<=34).sort((a,b)=>distance(world.actorPosition,a.position)-distance(world.actorPosition,b.position));
      const cycle=(now-lastTab)/1000<1.5&&s.target===lastTarget;
      const next=list[cycle?(list.findIndex(e=>e.id===s.target)+1)%list.length:0];
      s.target=next?.id??null;s.targetSource=next?'manual':'auto';lastTab=now;lastTarget=s.target;return true;
    }
    if(id==='pierce') {
      const e=s.enemies.find(e=>e.id===s.target&&e.hp>0);
      if(!e||distance(world.actorPosition,e.position)>=32)return false;
      assert.equal(s.targetSource,'manual','原生自动目标未固定前不能输出');hits.push(e.id);s.cooldown.pierce=1.2;return true;
    }
    return false;
  }};
  const enemy=(id,x,z,extra={})=>({id,name:id,hp:10000,maxHp:10000,engaged:true,position:[x,0,z],state:'approach',...extra});
  function advance(ms=90){now+=ms;s.wave.time+=ms/1000;s.cooldown.pierce=Math.max(0,s.cooldown.pierce-ms/1000);}
  function frame(target,boundary){w.test.storyBattleFrame({...s},target,boundary);const keys=[...w.test.state.heldKeys];if(keys.length){const h=w.test.keyHeading(keys,w.test.movementBasis(world));world.actorPosition[0]+=Math.sin(h)*.54;world.actorPosition[2]+=Math.cos(h)*.54;}advance();}
  return {w,s,world,calls,hits,enemy,advance,frame};
}
test('野外全局保持首个交战怪，另一只更近也不换，死亡/隐身/回位才重选',()=>{
  for(const invalid of ['dead','hidden','returning']) {
    const f=fixture(),{w,s,enemy,world}=f;
    const a=enemy('a',-7,463),b=enemy('b',-7,470);s.enemies=[a,b];s.target='a';
    assert.equal(w.test.fieldThreat(s).id,'a');
    b.position=[-7,0,451];world.actorPosition=[-7,0,452];s.target='b';
    assert.equal(w.test.fieldThreat(s).id,'a');
    if(invalid==='dead')a.hp=0;else a[invalid]=true;
    assert.equal(w.test.fieldThreat(s).id,'b');
  }
});
test('任务明确指定另一个怪时可覆盖全局锁定，场景和操作代次变更不继承旧锁',()=>{
  const f=fixture(),{w,s,enemy}=f,a=enemy('a',-7,460),b=enemy('b',-7,470);s.enemies=[a,b];s.target='a';
  assert.equal(w.test.fieldThreat(s).id,'a');
  assert.equal(w.test.focusedEnemy(s,s.enemies,b,true).id,'b');
  assert.equal(w.test.fieldThreat(s).id,'b');
  w.test.state.generation++;assert.equal(w.test.fieldThreat(s).id,'a');
  w.test.focusedEnemy(s,s.enemies,b,true);w.__fc={stats:()=>s};assert.equal(w.test.fieldThreat(s).id,'a');
});
test('野外锁定必须由 auto 转为 manual，首下 Tab 和循环都读取原生确认后才点杀',()=>{
  const f=fixture(),{w,s,enemy,calls,hits}=f;
  s.enemies=[enemy('near',-7,455),enemy('middle',-7,460),enemy('wanted',-7,465)];s.target='wanted';
  const target=w.test.focusedEnemy(s,s.enemies,s.enemies[2],true);
  f.frame(target);
  assert.equal(s.target,'wanted');assert.equal(s.targetSource,'manual');assert.equal(calls.filter(x=>x==='lock').length,3);assert.deepEqual(hits,['wanted']);
  calls.length=0;f.advance(2000);f.frame(target);assert.equal(calls.includes('lock'),false);
});
test('目标暂不在 Tab 射程内不误打其他怪，仍继续向锁定目标移动',()=>{
  const f=fixture(),{w,s,enemy,world,calls,hits}=f;
  const a=enemy('wanted',-7,500),b=enemy('near',-7,460);s.enemies=[a,b];s.target='near';s.targetSource='manual';
  w.test.focusedEnemy(s,s.enemies,a,true);const before=distance(world.actorPosition,a.position);
  f.frame(a);assert.ok(calls.filter(x=>x==='lock').length<=s.enemies.length+1);assert.equal(hits.length,0);assert.ok(distance(world.actorPosition,a.position)<before);
});
test('原生拒绝或未改变锁定时循环有限，下一拍仍正常移动且不会把技能打到错误怪',()=>{
  const f=fixture(),{w,s,enemy,hits}=f,a=enemy('wanted',-7,465),b=enemy('near',-7,455);s.enemies=[a,b];s.target='near';s.targetSource='manual';
  let locks=0;w.__rt.combatAction=id=>{if(id==='lock')locks++;return false;};
  w.test.focusedEnemy(s,s.enemies,a,true);f.frame(a);assert.equal(locks,1);assert.equal(hits.length,0);assert.ok(w.test.state.heldKeys.size>0);
  f.frame(a);assert.equal(locks,1);
});
test('护送不因怪物离车队更近而频繁换怪，烧车劫车者进入车旁时优先处理',()=>{
  const f=fixture({arena:'escort',overworld:false}),{w,s,enemy,world}=f;
  const a=enemy('a',-7,470),b=enemy('b',-7,465);s.enemies=[a,b];s.target='a';
  assert.equal(w.test.escortEnemy(s,world.actorPosition).id,'b');
  a.position=[-7,0,451];assert.equal(w.test.escortEnemy(s,world.actorPosition).id,'b');
  a.role='ghostraider';assert.equal(w.test.escortEnemy(s,world.actorPosition).id,'a');
  a.hp=0;assert.equal(w.test.escortEnemy(s,world.actorPosition).id,'b');
});
test('护送末波在狭窄驿道持续交战三分钟，人物和追魂使不被一路拉到道路末端',()=>{
  const f=fixture({arena:'escort',overworld:false,road:true}),{w,s,enemy,world,hits}=f;
  world.actorPosition=[-7,0,468];
  const boss=enemy('boss',-7,480,{role:'ghostelder'});s.enemies=[boss];s.target=boss.id;s.targetSource='manual';
  let maxCart=0,maxZ=-Infinity;
  for(let n=0;n<2000;n++) {
    const boundary=w.test.escortBoundary(s),target=w.test.escortEnemy(s,world.actorPosition);f.frame(target,boundary);
    maxCart=Math.max(maxCart,distance(world.actorPosition,boundary[1].center));maxZ=Math.max(maxZ,world.actorPosition[2]);
    const d=distance(world.actorPosition,boss.position);
    if(d>8){boss.position[0]+=(world.actorPosition[0]-boss.position[0])/d*.19;boss.position[2]+=(world.actorPosition[2]-boss.position[2])/d*.19;}
  }
  assert.ok(maxCart<21,'前方交战范围：'+maxCart);assert.ok(maxZ<497,'不能一路拉到 529 米：'+maxZ);assert.ok(hits.length>100,'保持持续点杀');
});
test('护送从旧版拉远位置恢复时向前方战斗区收拢，多重边界不为追怪放宽',()=>{
  const f=fixture({arena:'escort',overworld:false,road:true}),{w,s,enemy,world}=f;
  world.actorPosition=[-7,0,525];const boss=enemy('boss',-7,510,{role:'ghostelder'});s.enemies=[boss];s.target=boss.id;s.targetSource='manual';
  const boundary=w.test.escortBoundary(s),before=distance(world.actorPosition,boundary[1].center);
  for(let n=0;n<180;n++)f.frame(boss,boundary);
  assert.ok(distance(world.actorPosition,boundary[1].center)<21);assert.ok(distance(world.actorPosition,boundary[1].center)<before-20);
});

test('野外空快照不留下目标；目标重新进入不同场景时按当前交战怪重选',()=>{
  const f=fixture(),{w,s,enemy}=f,a=enemy('a',-7,465),b=enemy('b',-7,455);s.enemies=[a,b];s.target='a';
  assert.equal(w.test.focusedEnemy(s,s.enemies,a,true).id,'a');
  assert.equal(w.test.focusedEnemy(null),null);
  s.target='b';assert.equal(w.test.fieldThreat(s).id,'b');
});
test('护送末波战斗区位于车前，与两辆原生车体保留距离且不扩大驿道边界',()=>{
  const f=fixture({arena:'escort',overworld:false}),{w,s,enemy}=f;
  s.enemies=[enemy('boss',-7,480,{role:'ghostelder'})];
  const bounds=w.test.escortBoundary(s),local=bounds[1];
  assert.equal(bounds.length,2);assert.equal(bounds[0].radius,211);assert.equal(local.radius,20);
  assert.ok(local.center[2]>473&&local.center[2]<475);
  // 原生前车位于车头后 3.4 米，烧车半径 4.5 米。
  const frontCartZ=448.4078988609995-3.4;
  assert.ok(local.center[2]-local.radius-frontCartZ>8,'整个前方交战圆都应离开车体燃烧圈');
});
