'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {source}=JSON.parse(fs.readFileSync(0,'utf8'));
let checks=0;
function check(value,message){assert(value,message);checks++;}
function harness(){
 let now=100000,snapshot;
 const calls=[],results={hpPotion:false,manaPotion:false};
 const combat={stats:()=>snapshot,command:id=>{calls.push(id);return Object.hasOwn(results,id)?results[id]:true;},
  setGuard:value=>calls.push(value?'guard:on':'guard:off'),key:()=>true,start:()=>true};
 const c={console,URL,Request,performance:{now:()=>now},document:{querySelector:()=>null},
  fetch:()=>{throw Error('network must never be called')},location:{href:'https://offline.invalid'},
  localStorage:{setItem(){}},KeyboardEvent:class{constructor(type,init){this.type=type;Object.assign(this,init);}},
  dispatchEvent:()=>true,setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
  __lw:{combat,runtime:{townNetworkState:()=>({position:[65,0,1120]}),stats:()=>({yaw:0,cameraFollow:{inputBasis:null}})},
   log:[],events:[],trace:[],hooked:true,version:0}};
 c.window=c;vm.createContext(c);vm.runInContext(source,c);
 const bot=c.__lw.bot;bot.basis={fwd:0,rightSign:1};
 Object.assign(bot.cfg,{center:[65,1120],radius:24,leashMargin:6});
 for(const key of ['lastLock','lastSlot','lastAttack','lastDodge','lastHold','lastToggle'])bot[key]=-1e9;
 function state(extra={}){
  return Object.assign({state:'fighting',paused:false,overworld:false,hp:30,hpMax:120,mana:150,manaMax:200,
   stamina:100,momentum:0,potions:{hp:1,mana:0,carry:0},cooldown:{},action:null,guard:false,target:'boss',
   kit:[],arts:[],art:null,azure:true,swordVariant:'jinlei_gengjin',binding:false,metrics:{traps:0},
   wave:{index:3,time:200,count:4},enemies:[{id:'boss',role:'bloodboss',hp:500,maxHp:700,state:'attack',
    move:'bossLeap',time:.72,tell:0,position:[67.5,0,1120],hidden:false,guarding:false,
    posture:0,postureMax:230,phase:2}]},extra);
 }
 return {bot,results,state,tick(value,at=now){snapshot=value;now=at;calls.length=0;bot.tick();return {calls:[...calls],why:bot.why};},
  profile(value){c.__lw.combatOptions={getDoufaProfile:()=>value};}};
}

for(const defense of ['dodge','guard:on']){
 const h=harness(),s=h.state();
 if(defense==='guard:on')Object.assign(s.enemies[0],{move:'enemyAttack1',time:.60});
 const result=h.tick(s);
 check(result.calls.includes('hpPotion'),'low HP must attempt its original potion branch');
 check(result.calls.includes(defense),'rejected hp must continue to '+defense);
}
{
 const h=harness(),s=h.state();h.tick(s);
 check(!h.tick(s,100499).calls.includes('hpPotion'),'failed hp must wait 500ms');
 check(h.tick(s,100500).calls.includes('hpPotion'),'failed hp may retry at 500ms');
 check(s.wave.time===200,'throttle uses wall time without changing game time');
}
{
 const h=harness(),s=h.state();h.results.hpPotion=true;
 const first=h.tick(s);
 check(first.calls.includes('hpPotion')&&!first.calls.includes('dodge'),'accepted potion ends this tick');
 check(!h.tick(s,101999).calls.includes('hpPotion'),'stale success snapshot does not repeat');
 check(h.tick(s,102000).calls.includes('hpPotion'),'accepted potion may retry after two seconds');
}
{
 const h=harness(),s=h.state({mana:10,potions:{hp:1,mana:0,carry:3}});
 const result=h.tick(s);
 check(result.calls.includes('hpPotion')&&result.calls.includes('manaPotion')&&result.calls.includes('dodge'),
  'two independent refused medicines still permit defense');
 s.hp=120;h.results.manaPotion=true;
 check(!h.tick(s,100499).calls.includes('manaPotion'),'mana refusal has its own retry deadline');
 const accepted=h.tick(s,100500);
 check(accepted.calls.includes('manaPotion')&&!accepted.calls.includes('hpPotion'),'carry is used only through the mana command');
 s.hp=30;s.mana=150;
 check(h.tick(s,100501).calls.includes('hpPotion'),'mana success must not extend the hp retry deadline');
}
for(const stock of [{hp:0,mana:4,carry:3},{hp:0,mana:0,carry:0}]){
 const h=harness(),result=h.tick(h.state({potions:stock}));
 check(!result.calls.includes('hpPotion'),'carry must not become blood medicine');
 check(result.calls.includes('dodge'),'zero blood stock leaves defense available');
}
{
 const h=harness(),result=h.tick(h.state({cooldown:{hpPotion:1}}));
 check(!result.calls.includes('hpPotion')&&result.calls.includes('dodge'),'native cooldown takes precedence');
}
for(const kind of ['hp','mana']){
 const h=harness(),extra={gcdLeft:.8,casting:null};
 if(kind==='mana')Object.assign(extra,{hp:120,mana:10,potions:{hp:0,mana:0,carry:3}});
 const result=h.tick(h.state(extra));
 check(result.calls.includes(kind+'Potion')&&result.calls.includes('dodge'),'cast-aware rejection permits defense');
}
for(const kind of ['hp','mana']){
 const h=harness();h.profile({combat:{manaCostScale:4.035,fieldDamageScale:125.716505}});
 const extra=kind==='hp'?{hp:2000,hpMax:15086,mana:700,manaMax:807}:
  {hp:15086,hpMax:15086,mana:30,manaMax:807,potions:{hp:0,mana:0,carry:3}};
 h.results[kind+'Potion']=true;
 check(h.tick(h.state(extra)).calls.includes(kind+'Potion'),'existing growth-unit threshold stays effective');
}
{
 const h=harness();h.profile({combat:{manaCostScale:4.035,fieldDamageScale:125.716505}});
 const s=h.state({hp:9000,hpMax:15086,mana:220,manaMax:807,potions:{hp:1,mana:4,carry:3}});
 const result=h.tick(s);
 check(!result.calls.includes('hpPotion'),'59.66% HP must not acquire the share package 70% threshold');
 check(!result.calls.includes('manaPotion'),'27.26% mana must not acquire the share package 35% threshold');
}
console.log(`PASS ${checks} generated-script real bot.tick assertions`);
