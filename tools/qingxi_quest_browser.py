"""Bounded, identity-checked NPC tasks using the pinned upstream assistant.

Upstream owns movement, combat, puzzles and travel. This adapter owns local
policy, evidence, request bounds and shutdown. No trial starts or extra fishing.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from model.qingxi_quest_budget import (QUEST_ENTRY_SEC, QUEST_HEALTH_SEC, QUEST_HOME_SEC,
                                      QUEST_IDENTITY_SEC, QUEST_INSPECT_SEC, QUEST_MAINLINE_DRAIN_SEC,
                                      QUEST_MAINLINE_SEC)

VENDOR = ROOT / 'vendor/dwelling-autobot'
GUARD = Path(__file__).with_name('qingxi_quest_policy.js').read_text()
HEALTH_MINIMUM_RATIO = .8


NATIVE_HEALTH_HELPER = """window.__qingxiNativeHealth = (()=>{
    const ids=new WeakMap();let serial=0,lastKnownWorld=null;
    const identity=value=>{if(!value||(typeof value!=='object'&&typeof value!=='function'))return null;
      if(!ids.has(value))ids.set(value,++serial);return ids.get(value);};
    return {
    snapshot() {
      const world=readWorld(),combat=world?.combat,q=window.__qx,fc=window.__fc;
      const present=world&&Object.hasOwn(world,'combat')?
        (combat===null?false:combat&&typeof combat==='object'?true:null):null;
      const controller=present===true?fc?.stats?.():null;
      const matches=present===true?!!controller&&[
        'hp','hpMax','active','state','overworld','fieldPending','castOutbox'
      ].every(key=>controller[key]===combat[key]):null;
      // Read the HUD only after its combat controller already exists and matches.
      // Its mobile fallback then cannot initialize a controller while observing.
      const hud=matches===true?window.__rt?.mobileHudState?.():null;
      const blocked=globalThis.document?.hidden===true?true:present!==true?null:
        fc?.paused===true||hud?.blocked===true||hud?.portrait===true?true:
        fc?.paused===false&&hud?.blocked===false&&hud?.portrait===false?false:null;
      const region=window.__quests?.snapshot?.()?.world?.current;
      if(world?.environment?.tier===4&&typeof region==='string')lastKnownWorld=region;
      let stored=null;
      try { stored=JSON.parse(localStorage.getItem('qingxi-local-pending:'+q?.player)||'[]'); } catch {}
      return {player:q?.player,error:q?.error,status_error:q?.statusError,cast_error:q?.castError||combat?.castError||null,
        pending:q?.pending instanceof Map&&(present===false||combat)?
          (present===false?0:combat.fieldPending+combat.castOutbox)+q.pending.size:null,
        pending_operations:q?.pending instanceof Map?[...q.pending.values()]:null,stored_pending:stored,
        memory_pending_count:q?.pending instanceof Map?q.pending.size:null,
        stored_pending_count:Array.isArray(stored)?stored.length:null,
        scene_ready:sceneReady(),cave_safe:caveIsSafe(),town_safe:townIsSafe(),
        scene_tier:world?.environment?.tier,world_current:region,last_known_world:lastKnownWorld,
        position:world?.actorPosition,draws:world?.draws,control_mode:world?.controlMode,mode:world?.mode,
        paused:present===false?null:fc?.paused,ghost:fc?.isGhost,runtime_blocked:blocked,
        flight_active:world?.flight?.active,combat_present:present,combat_matches_runtime:matches,
        chariot_mounted:world?.flight?.chariotMounted,combat_active:present===false?false:combat?.active,
        combat_settlement_pending:present===false?false:caveSettlementPending(combat),
        overworld:combat?.overworld,state:combat?.state,
        engaged_enemies:present===false?0:Array.isArray(combat?.enemies)?
          combat.enemies.filter(e=>e?.hp>0&&e?.engaged).length:null,
        hp:combat?.hp,hp_max:combat?.hpMax,home:state.home?{...state.home}:null,
        automation_busy:state.busy,automation_running:state.running,
        runtime_id:identity(window.__rt),combat_id:identity(fc)};
    }
  };})();
  window.__autobot = {"""
HEALTH_SNAPSHOT = 'window.__qingxiNativeHealth?.snapshot()'
RETURN_CAVE_START = """window.__qingxiHealthReturn={done:false,started_at:Date.now()};
Promise.resolve(window.__autobot.returnCave()).then(value=>{
 Object.assign(window.__qingxiHealthReturn,{done:true,value});
},error=>{Object.assign(window.__qingxiHealthReturn,{done:true,error:String(error.message||error)});});void 0"""
RETURN_CAVE_DONE = 'window.__qingxiHealthReturn'
TOWN_REENTRY_START = """window.__qingxiHealthTown={done:false};
Promise.resolve(window.__autobot.enter()).then(value=>{
 Object.assign(window.__qingxiHealthTown,{done:true,value});
},error=>{Object.assign(window.__qingxiHealthTown,{done:true,error:String(error.message||error)});});void 0"""
TOWN_REENTRY_DONE = 'window.__qingxiHealthTown'


def provenance_version():
    return json.loads((VENDOR / 'source.json').read_text())['version']


QUEST_COMBAT_ENTRY = """step('准备郊外战斗 · ' + objective.text);
      await until(() => {
        const combat = readCombat(), runtime = window.__rt;
        if (combat?.overworld) return true;
        if (combat?.active) throw new Error('当前副本尚未结算');
        if (typeof runtime?.canStartQuestCombat !== 'function' ||
            !runtime.canStartQuestCombat(objective.arena)) throw new Error('尚未进入郊外战斗区域');
        return runtime.startQuestCombat?.(objective.arena) === true;
      }, 90000, '郊外敌人模型与战斗入口', owner);"""
QUEST_COMBAT_LOOT = """async function fightEnemy(enemyId, owner) {
    await fightEnemyCore(enemyId, owner);
    storyCheck(owner);
    if (state.story.collectingFieldLoot) return;
    const region = storyData()?.world?.current || 'yue', arena = readCombat()?.arena;
    const items = new Set();
    for (const quest of questCatalog?.quests || []) {
      const slot = storySlot(quest.id);
      if (slot?.status !== 'active' || (quest.region || 'yue') !== region) continue;
      for (const objective of quest.objectives || []) {
        const required = slot.required?.[objective.id] ?? objective.required;
        if (objective.type === 'collect' && objective.itemId && objective.sourceEnemyRole &&
            objective.arena === arena && Number.isInteger(required) && required > 0 &&
            (slot.progress?.[objective.id] || 0) < required) items.add(objective.itemId);
      }
    }
    state.story.collectingFieldLoot = true;
    try { for (const item of items) await pickupNeededCorpses(item, owner); }
    finally { if (owner === state.generation && state.story) state.story.collectingFieldLoot = false; }
  }
  async function fightEnemyCore(enemyId, owner) {"""
QUEST_FIGHT_TIMEOUT_DIAGNOSTIC = """{
          const combat=readCombat(),player=position3(myPos()),enemy=position3(target.position);
          const metrics=combat?.metrics||{};
          const receiptIds=new Set([enemyId,target.id,target.fieldId,target.spawnId].filter(Boolean));
          window.__qx.fieldFightTimeout={at:Date.now()/1000,startedAt:fightStartedAt/1000,
            deadlineAt:deadline/1000,requestedEnemyId:String(enemyId).slice(0,100),
            target:{id:String(target.id||'').slice(0,100),fieldId:target.fieldId||null,
              spawnId:target.spawnId||null,name:String(target.name||'').slice(0,80),role:String(target.role||'').slice(0,60),
              hp:Number.isFinite(target.hp)?target.hp:null,maxHp:Number.isFinite(target.maxHp)?target.maxHp:null,
              engaged:!!target.engaged,returning:!!target.returning,state:String(target.state||'').slice(0,40),
              move:String(target.move||'').slice(0,40),position:enemy,home:position3(target.home),
              leashRadius:Number.isFinite(target.leashRadius)?target.leashRadius:null},
            playerPosition:player,distance:player&&enemy?dist(player,enemy):null,
            combat:{active:combat?.active===true,state:String(combat?.state||'').slice(0,40),
              hp:Number.isFinite(combat?.hp)?combat.hp:null,hpMax:Number.isFinite(combat?.hpMax)?combat.hpMax:null,
              paused:combat?.paused===true,fieldPending:Number.isInteger(combat?.fieldPending)?combat.fieldPending:null,
              castOutbox:Number.isInteger(combat?.castOutbox)?combat.castOutbox:null,
              metrics:Object.fromEntries(['enemyHits','blocks','parries','dodges','potions','playerHits','swordHits','treasureHits']
                .filter(key=>Number.isFinite(metrics[key])).map(key=>[key,metrics[key]]))},
            recentSettlements:fieldSettlements.filter(row=>receiptIds.has(row.enemyId)).slice(-4).map(row=>({
              sequence:row.sequence,enemyId:row.enemyId,ok:row.ok,error:row.error,
              damage:row.damage,hits:row.hits,at:row.at}))};
          throw new Error('战斗超时：'+target.name);
        }"""
QUEST_NAVIGATION_REVIEW = """if (!path && obstacles.length && !obstaclesReviewed) {
            // Inferred obstacles can close the only exit beside real scenery.
            // Review them once; the game's collision/height checks still apply.
            const verified = findPath(p, goal, radius, fieldEnvironment, []);
            if (verified) {
              path = verified; obstacles.length = 0; obstaclesReviewed = true;
              log('疑似障碍挡住出口，按真实碰撞重新规划');
            }
          }
          if (!path) {
            walkRecovery = true;"""
QUEST_WALK_DEFENCE = """const defenceStartedAt = Date.now();
        if (options.defend !== false && await defendNearby(owner)) {
          // Confirmed defensive combat uses its own deadline. It is not walking
          // time; storyCheck still enforces the unchanged whole-run deadline.
          deadline += Date.now() - defenceStartedAt;
          path = null; movedAt = Date.now(); continue;
        }"""
QUEST_NATIVE_TAKEOFF_FALLBACK = """if (!position3(point) || !fieldEnvironment?.groundHeight || !fieldEnvironment?.canFly) return null;
    const y = fieldEnvironment.groundHeight(point[0], point[2], point[1]);
    if (!Number.isFinite(y) || Math.abs(y - point[1]) > .43) return null;
    // The game's beginFlightNow checks canFly at the actor's current height.
    // Preserve upstream's roof/landing checks above, then allow this native
    // fallback when walkability alone rejects a valid takeoff point.
    if (fieldEnvironment.canFly(point[0], point[2], .4, point[1])) return [point[0], y, point[2]];
    if (!raisedSurface(point)) return null;"""
QUEST_NAVIGATION_EVIDENCE = """if (!path) {
              window.__qx.navigationFailure = {position:p.slice(), goal:goal.slice(), radius,
                obstacles:obstacles.map(o=>o.slice()), obstaclesReviewed, recovery, at:Date.now()/1000};
              throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' +
                (state.story?.flightLastFailure || recovery.reason));
            }"""


def browser_script(player, choices=None, *, read_only=False, reviewed_cast_pending=None, confirmed_cast_receipts=None,
                   reviewed_release_pending=None, reviewed_preparation_pending=None, reviewed_flight_pending=None,
                   reward_batch=False):
    source = (VENDOR / 'trial-bot.user.js').read_bytes()
    provenance = json.loads((VENDOR / 'source.json').read_text())
    if hashlib.sha256(source).hexdigest() != provenance['sha256']:
        raise RuntimeError('qingxi_upstream_source_changed')
    config = {'player': player, 'choices': choices or {}, 'readOnly': read_only,
              'rewardBatch': reward_batch,
              'reviewedCastPending': reviewed_cast_pending or [],
              'reviewedReleasePending': reviewed_release_pending or [],
              'reviewedPreparationPending': reviewed_preparation_pending or [],
              'reviewedFlightPending': reviewed_flight_pending or [],
              'confirmedCastReceipts': confirmed_cast_receipts or []}
    script = source.decode()
    # Use upstream's own deferred-task path, while the fetch policy remains the
    # final guard. Missing choices/fishing must not prevent unrelated errands.
    patches = {
        'window.__autobot = {': NATIVE_HEALTH_HELPER,
        'if (!raisedSurface(point)) return null;\n'
        '    const y = fieldEnvironment.groundHeight(point[0], point[2], point[1]);':
            QUEST_NATIVE_TAKEOFF_FALLBACK,
        'const wanted = state.story.options.rewardChoices?.[q.id];':
            "const wanted = state.story.options.rewardChoices?.[q.id];\n"
            "        if (!wanted) throw Object.assign(new Error('任务奖励保留待选'), {code:'unsupported_quest_objective'});",
        'async function fishObjective(q, objective, owner) {':
            "async function fishObjective(q, objective, owner) {\n"
            "    throw Object.assign(new Error('河岸钓鱼等待现有日计划'), {code:'fishing_daily_limit_reached'});",
        "if (!window.__rt.startQuestCombat?.(objective.arena)) throw new Error('游戏未接受郊外战斗入口，请确认人物已进入区域');":
            QUEST_COMBAT_ENTRY,
        'async function fightEnemy(enemyId, owner) {': QUEST_COMBAT_LOOT,
        'const deadline = Date.now() + 5 * 60000;':
            'const fightStartedAt=Date.now(), deadline=fightStartedAt+5*60000;',
        "if (Date.now() > deadline) throw new Error('战斗超时：' + target.name);":
            "if (Date.now() > deadline) " + QUEST_FIGHT_TIMEOUT_DIAGNOSTIC,
        'const target = livingEnemies(s).find(e => e.id === enemyId) || fieldThreat(s);':
            'const requested = livingEnemies(s).find(e => e.id === enemyId);\n'
            '        const target = (requested?.engaged ? requested : null) || fieldThreat(s) || requested;',
        'let flightAttempt = null, walkRecovery = false;':
            'let flightAttempt = null, walkRecovery = false, obstaclesReviewed = false;',
        'const obstacles = [], deadline = Date.now() + Math.max(90000, dist(last, goal) * 1500);':
            'const obstacles = []; let deadline = Date.now() + Math.max(90000, dist(last, goal) * 1500);',
        'if (options.defend !== false && await defendNearby(owner)) { path = null; movedAt = Date.now(); continue; }':
            QUEST_WALK_DEFENCE,
        'if (!path) {\n            walkRecovery = true;': QUEST_NAVIGATION_REVIEW,
        "if (!path) throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' +\n"
        "              (state.story?.flightLastFailure || recovery.reason));":
            QUEST_NAVIGATION_EVIDENCE,
    }
    for before, after in patches.items():
        if script.count(before) != 1:
            raise RuntimeError('qingxi_upstream_policy_anchor_changed')
        script = script.replace(before, after)
    source = GUARD.replace('__CONFIG__', json.dumps(config, ensure_ascii=False)) + '\n' + script
    if reward_batch:
        source += '\n' + Path(__file__).with_name('qingxi_reward_batch.js').read_text().replace('__PLAYER__', str(player))
    return source


SNAPSHOT = r"""(() => {
 const q=window.__quests?.snapshot?.(),s=window.__fc?.stats?.(),r=window.__autobot?.report?.();
 return {player:window.__qx?.player,error:window.__qx?.error,stop_reason:window.__qx?.stopReason,
  requests:window.__qx?.requests,casts:window.__qx?.casts,status_error:window.__qx?.statusError,states:q?.states||{},
  cast_error:window.__qx?.castError,
  navigation_failure:window.__qx?.navigationFailure,
  world:q?.world,position:window.__rt?.stats?.().actorPosition,
  state:s?.state,combat_active:s?.active,overworld:s?.overworld,pending:(s?.fieldPending||0)+(s?.castOutbox||0)+(window.__qx?.pending.size||0),hp:s?.hp,
  pending_operations:[...(window.__qx?.pending.values()||[])],
  stored_pending:(()=>{try{return JSON.parse(localStorage.getItem('qingxi-local-pending:'+window.__qx?.player)||'[]')}
    catch{return null}})(),
  site:window.dwellingFishingSites?.town?.position,receipts:window.__qx?.receipts||[],
  reward_receipts:[...(window.__qx?.rewards.values()||[])],
  reward_batch:window.__qingxiRewardBatch?.state,field_fight_timeout:window.__qx?.fieldFightTimeout||null,
  report:r?{phase:r.phase,error:r.error,story:r.story,questSummary:r.questSummary,navigation:r.navigation,
    recent:r.recent?.slice(-12),fieldSettlements:r.fieldSettlements,retry:r.retry,batch:r.batch,dailyRewards:r.dailyRewards}:null,
  run:window.__qingxiRun?{done:window.__qingxiRun.done,value:window.__qingxiRun.value,error:window.__qingxiRun.error}:null};
})()"""


def write_result(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(temporary, path)


def health_recovery_state(snapshot, player):
    """Classify only source-backed safe scenes, never all of the town's tier."""
    if (not isinstance(snapshot, dict) or snapshot.get('player') != player
            or any(snapshot.get(key) for key in ('error', 'status_error', 'cast_error'))
            or snapshot.get('combat_settlement_pending') is not False
            or snapshot.get('scene_ready') is not True
            or snapshot.get('control_mode') != 'companion' or snapshot.get('mode') != 'walk'
            or snapshot.get('runtime_blocked') not in (False, None)
            or snapshot.get('flight_active') is not False or snapshot.get('chariot_mounted') is not False
            or type(snapshot.get('pending')) is not int or snapshot['pending'] != 0
            or snapshot.get('pending_operations') != [] or snapshot.get('stored_pending') != []
            or type(snapshot.get('engaged_enemies')) is not int or snapshot['engaged_enemies'] != 0
            or snapshot.get('automation_busy') is not False or snapshot.get('automation_running') is not False):
        return 'unsafe'
    draws = snapshot.get('draws')
    if type(draws) is not int or draws < 0:
        return 'unsafe'
    tier, position = snapshot.get('scene_tier'), snapshot.get('position')
    if (type(tier) is not int or not isinstance(position, list) or len(position) != 3
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in position)):
        return 'unsafe'
    if type(snapshot.get('runtime_id')) is not int or snapshot['runtime_id'] <= 0:
        return 'unsafe'
    region = snapshot.get('world_current')
    cave = (snapshot.get('cave_safe') is True and tier != 4 and snapshot.get('combat_active') is False
            and (region == 'yue' or region is None and snapshot.get('last_known_world') == 'yue'))
    # Returning to the cave can dispose the town combat controller. Its explicit
    # absence is safe for normal reentry, but supplies no health evidence.
    if (cave and snapshot.get('combat_present') is False and snapshot.get('paused') is None
            and snapshot.get('ghost') is not True):
        return 'safe_cave'
    if (snapshot.get('ghost') is not False or snapshot.get('combat_present') is not True
            or snapshot.get('combat_matches_runtime') is not True
            or snapshot.get('runtime_blocked') is not False
            or snapshot.get('paused') is not False
            or type(snapshot.get('combat_id')) is not int or snapshot['combat_id'] <= 0):
        return 'unsafe'
    hp, hp_max = snapshot.get('hp'), snapshot.get('hp_max')
    if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in (hp, hp_max)) or hp > hp_max):
        return 'unsafe'
    if cave:
        return 'safe_cave'
    if (snapshot.get('town_safe') is True and region == 'yue' and tier == 4
            and math.hypot(position[0], position[2]) < 300
            and snapshot.get('combat_active') is False):
        return 'safe_town'
    # A restored wilderness position may be idle while the region controller
    # remains active. Do not wait there: use the helper's real return entrance.
    if (region == 'yue' and type(snapshot.get('scene_tier')) is int and snapshot['scene_tier'] == 4
            and ((snapshot.get('combat_active') is True and snapshot.get('overworld') is True
                  and snapshot.get('state') == 'fighting')
                 or (snapshot.get('combat_active') is False and snapshot.get('state') == 'ready'))):
        return 'return_cave'
    return 'unsafe'


def prepare_native_health(page, result, result_path, player, *, timeout=None,
                          minimum_ratio=HEALTH_MINIMUM_RATIO, sleeper=None, monotonic=None, wall_time=None):
    """Use normal return/entry flows and observe native regeneration before a new field run."""
    sleeper, monotonic, wall_time = sleeper or time.sleep, monotonic or time.monotonic, wall_time or time.time
    timeout = QUEST_HEALTH_SEC if timeout is None else timeout
    recovery = {'phase': 'checking', 'started_at': wall_time(), 'minimum_ratio': minimum_ratio,
                'observations': []}
    result['health_recovery'] = recovery

    def observe(phase):
        try:
            snapshot = page.js(HEALTH_SNAPSHOT)
        except Exception as error:
            recovery['exception'] = type(error).__name__
            blocked('snapshot_error')
        safe = health_recovery_state(snapshot, player)
        recovery.update(phase=phase, observed_at=wall_time(), scene=safe)
        if isinstance(snapshot, dict):
            summary = {key: snapshot.get(key) for key in (
                'player', 'scene_tier', 'world_current', 'cave_safe', 'town_safe', 'scene_ready',
                'draws', 'hp', 'hp_max', 'paused', 'ghost', 'combat_active', 'overworld', 'state',
                'engaged_enemies', 'flight_active', 'chariot_mounted', 'pending', 'runtime_id', 'combat_id',
                'memory_pending_count', 'stored_pending_count', 'combat_present', 'combat_matches_runtime', 'runtime_blocked',
                'combat_settlement_pending', 'home')}
            recovery['latest'] = summary
            recovery['observations'] = [*recovery['observations'][-11:], summary]
        write_result(result_path, result)
        return snapshot, safe

    def blocked(reason):
        recovery.update(phase='blocked', reason=reason, observed_at=wall_time())
        write_result(result_path, result)
        raise RuntimeError('qingxi_native_health_' + reason)

    def keep_native_scene_visible():
        try:
            visible = page.keep_visible()
        except Exception as error:
            recovery['exception'] = type(error).__name__
            blocked('visibility_error')
        if visible is not True:
            blocked('hidden_scene')

    keep_native_scene_visible()
    snapshot, safe = observe('checking')
    if safe == 'unsafe':
        blocked('unsafe_scene')
    if safe == 'safe_cave':
        blocked('unexpected_cave')
    recovery['initial'] = dict(recovery['latest'])
    initial_hp, initial_max = snapshot.get('hp'), snapshot.get('hp_max')
    if safe == 'return_cave':
        recovery['phase'] = 'returning'
        write_result(result_path, result)
        started_at = wall_time() * 1000
        try:
            page.js(RETURN_CAVE_START)
            page.wait('window.__qingxiHealthReturn?.done', QUEST_HOME_SEC, '原生安全返回洞府', every=2)
            reply = page.js(RETURN_CAVE_DONE)
        except TimeoutError:
            blocked('return_timeout')
        except Exception as error:
            recovery['exception'] = type(error).__name__
            blocked('return_error')
        snapshot, safe = observe('returning')
        home = snapshot.get('home') if isinstance(snapshot, dict) else None
        if (not isinstance(reply, dict) or reply.get('done') is not True or reply.get('error')
                or reply.get('value') != '已返回洞府'
                or safe != 'safe_cave' or not isinstance(home, dict) or home.get('verified') is not True
                or home.get('destination') != 'cave'
                or type(home.get('at')) not in (int, float)
                or not math.isfinite(home['at']) or home['at'] < started_at):
            blocked('return_unverified')
    if safe == 'safe_cave':
        # Do not use a disposed old town controller's HP after returning home.
        # Enter through the normal helper and wait only in its current safe town.
        recovery['phase'] = 'entering_town'
        write_result(result_path, result)
        try:
            page.js(TOWN_REENTRY_START)
            page.wait('window.__qingxiHealthTown?.done', QUEST_ENTRY_SEC, '回血前原生入坊', every=2)
            reply = page.js(TOWN_REENTRY_DONE)
        except TimeoutError:
            blocked('reentry_timeout')
        except Exception as error:
            recovery['exception'] = type(error).__name__
            blocked('reentry_error')
        snapshot, safe = observe('entering_town')
        if (not isinstance(reply, dict) or reply.get('done') is not True or reply.get('error')
                or reply.get('value') != '已进入青溪坊'
                or safe != 'safe_town'):
            blocked('reentry_unverified')
    if initial_hp is None or initial_max is None:
        initial_hp, initial_max = snapshot['hp'], snapshot['hp_max']
    initially_low = initial_hp / initial_max < minimum_ratio
    first_draws, deadline = snapshot['draws'], monotonic() + timeout
    runtime_id, combat_id = snapshot['runtime_id'], snapshot['combat_id']
    waited_scene = safe
    while True:
        if safe not in {'safe_cave', 'safe_town'} or safe != waited_scene:
            blocked('scene_changed')
        if snapshot['runtime_id'] != runtime_id or snapshot['combat_id'] != combat_id:
            blocked('runtime_changed')
        if snapshot['hp_max'] != initial_max:
            blocked('profile_changed')
        if monotonic() >= deadline:
            blocked('recovery_timeout')
        recovery.update(draws_advanced=snapshot['draws'] > first_draws,
                        hp_increased=snapshot['hp'] > initial_hp)
        if (recovery['draws_advanced'] and snapshot['hp'] / snapshot['hp_max'] >= minimum_ratio
                and (not initially_low or recovery['hp_increased'])):
            break
        keep_native_scene_visible()
        sleeper(5)
        snapshot, safe = observe('recovering')
    recovery.update(phase='ready', finished_at=wall_time())
    write_result(result_path, result)
    return recovery


def finished_phase(snapshot):
    if snapshot.get('stop_reason'):
        return 'waiting'
    error = (snapshot.get('report') or {}).get('error')
    if not error:
        return 'done'
    if (error == '主线运行达到设置的时限' and
            (snapshot.get('overworld') is True or snapshot.get('combat_active') is False)):
        return 'bounded_stop'
    return 'held'


def create_page(browser, cdp, player, choices, read_only, reviewed_cast_pending=None, confirmed_cast_receipts=None,
                reviewed_release_pending=None, reviewed_preparation_pending=None, reviewed_flight_pending=None,
                reward_batch=False):
    target = cdp.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True})['targetId']
    session = cdp.attach(target)
    for method in ('Page.enable', 'Runtime.enable', 'Network.enable', 'Page.bringToFront'):
        cdp.call(method, session=session)
    cdp.call('Page.addScriptToEvaluateOnNewDocument',
             {'source': browser_script(player, choices, read_only=read_only, reviewed_cast_pending=reviewed_cast_pending,
                                       confirmed_cast_receipts=confirmed_cast_receipts,
                                       reviewed_release_pending=reviewed_release_pending,
                                       reviewed_preparation_pending=reviewed_preparation_pending,
                                       reviewed_flight_pending=reviewed_flight_pending, reward_batch=reward_batch)}, session)
    return browser.Page(cdp, session, target)


def enter_page(page, cdp, url, player):
    cdp.call('Page.navigate', {'url': url}, page.s)
    page.wait('window.__autobot && window.__qx?.player===' + str(player), QUEST_IDENTITY_SEC, '任务身份校验', every=1)
    page.js('window.__qingxiEnter={done:false};window.__autobot.enter().then(value=>{window.__qingxiEnter={done:true,value}});void 0')
    page.wait('window.__qingxiEnter?.done', QUEST_ENTRY_SEC, '上游助手入坊', every=2)
    error = page.js('window.__autobot.report().error')
    if error:
        raise RuntimeError('qingxi_enter_failed: ' + str(error))
    page.loading = False


FINAL_REFRESH = """(async()=>{
 window.__qx.quiescing=true;await window.__qx.queue;
 window.__qx.statusCache.clear();
 const before=window.__qx.questStatusRevision;
 let response=await window.__quests.refresh();
 // The native refresh can join an older request whose response was already
 // counted before the queue drained. Only that successful, stale join gets
 // one new read; failed requests and unknown writes are never retried here.
 if(response?.ok===true && window.__qx.questStatusRevision===before){
  await window.__qx.queue;window.__qx.statusCache.clear();
  response=await window.__quests.refresh();
 }
 return {response,player:window.__qx.player,
  fresh:window.__qx.questStatusRevision>before};
})()"""


def final_status_verified(check, snapshot, player):
    if not isinstance(check, dict):
        return False
    response = check.get('response')
    return (check.get('fresh') is True and check.get('player') == player
            and isinstance(response, dict) and response.get('ok') is True
            and (response.get('playerId') is None or str(response['playerId']) == str(player))
            and isinstance(response.get('quests'), dict)
            and isinstance(response['quests'].get('states'), dict)
            and snapshot.get('player') == player and snapshot.get('pending') == 0
            and snapshot.get('stored_pending') == []
            and not snapshot.get('error') and not snapshot.get('status_error'))


def finish_page(page, result, player):
    """Keep failure evidence even if stopping, draining or refreshing fails."""
    result['server_checked'] = False
    try:
        result.update(page.js(SNAPSHOT))
    except Exception as exc:
        result['snapshot_error'] = type(exc).__name__
    try:
        page.js('window.__autobot.stop();window.__fc?.stop()')
        page.wait('(window.__fc?.stats?.().fieldPending||0)===0 && (window.__fc?.stats?.().castOutbox||0)===0 && window.__qx.pending.size===0',
                  45, '真实战斗回执', every=1)
        check = page.js(FINAL_REFRESH, timeout=30)
        result['final_status'] = {'fresh': isinstance(check, dict) and check.get('fresh') is True,
                                'at': time.time()}
    except Exception as exc:
        check = None
        result['cleanup_error'] = type(exc).__name__
    try:
        final = page.js(SNAPSHOT)
        final['stop_reason'] = final.get('stop_reason') or result.get('stop_reason')
        result.update(final)
        result['server_checked'] = final_status_verified(check, final, player)
    except Exception as exc:
        result['snapshot_error'] = type(exc).__name__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tool-root', type=Path, required=True)
    parser.add_argument('--url-file', type=Path, required=True)
    parser.add_argument('--profile-dir', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--options-file', type=Path)
    parser.add_argument('--expected-player-id', type=int, required=True)
    parser.add_argument('--arena', default='longwind')
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--workflow', choices=('quests', 'rewards'), default='quests')
    args = parser.parse_args()
    sys.path.insert(0, str(args.tool_root / 'tools'))
    import longwind_trial_bot as browser
    from qingxi_local import profile_lock, read_url_file, stop_browser
    options = json.loads(args.options_file.read_text()) if args.options_file else {}
    result = {'phase': 'preparing', 'upstream_version': provenance_version(), 'navigation_policy': 4, 'transport_policy': 1}
    result['workflow'] = args.workflow
    cdp = process = page = None
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    previous_umask = os.umask(0o077)
    try:
        with profile_lock(args.profile_dir):
            try:
                process = browser.launch_chrome(browser.find_chrome(None), args.profile_dir, browser.free_port(), headless=True, backend='mesa')
                port = next(int(arg.split('=')[1]) for arg in process.args if arg.startswith('--remote-debugging-port='))
                cdp = browser.CDP(browser.cdp_version(port)['webSocketDebuggerUrl'])
                page = create_page(browser, cdp, args.expected_player_id, options.get('choices', {}),
                                   args.read_only, options.get('reviewed_cast_pending'), options.get('confirmed_cast_receipts'),
                                   options.get('reviewed_release_pending'), options.get('reviewed_preparation_pending'),
                                   options.get('reviewed_flight_pending'), reward_batch=args.workflow == 'rewards')
                result['entry_started_at'] = time.time()
                write_result(args.result, result)
                try:
                    enter_page(page, cdp, read_url_file(args.url_file), args.expected_player_id)
                except BaseException as exc:
                    result['entry_error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    raise
                if args.workflow == 'quests':
                    page.js('window.__autobot.inspectQuests("全部任务")', timeout=QUEST_INSPECT_SEC)
                if args.read_only:
                    if args.workflow == 'rewards':
                        inspection = page.js('window.__qingxiRewardBatch.inspect()', timeout=300)
                        result['reward_inspection'] = inspection
                    result['phase'] = 'inspected'
                else:
                    if args.workflow == 'quests':
                        prepare_native_health(page, result, args.result, args.expected_player_id)
                    opts = {'kind': '全部任务', 'maxMinutes': min(15, max(.5, args.max_seconds / 60)),
                            'maxTasks': 8, 'rewardChoices': options.get('choices', {})}
                    result['mainline_started_at'] = time.time()
                    write_result(args.result, result)
                    workflow = ('window.__qingxiRewardBatch.run()' if args.workflow == 'rewards'
                                else 'window.__autobot.mainline(' + json.dumps(opts) + ')')
                    page.js('window.__qingxiRun={done:false};' + workflow
                            + '.then(value=>{window.__qingxiRun={done:true,value}},error=>{'
                            'window.__qingxiRun={done:true,error:String(error.message||error)}});void 0')
                    started = time.monotonic()
                    budget = min(10800 if args.workflow == 'rewards' else QUEST_MAINLINE_SEC, max(30, args.max_seconds))
                    while time.monotonic() - started < budget + QUEST_MAINLINE_DRAIN_SEC:
                        snap = page.js(SNAPSHOT)
                        result.update(phase='running', **snap)
                        write_result(args.result, result)
                        if snap.get('error'):
                            raise RuntimeError(snap['error'])
                        if (snap.get('run') or {}).get('done'):
                            if (snap.get('run') or {}).get('error'):
                                raise RuntimeError(snap['run']['error'])
                            if args.workflow == 'rewards':
                                batch = snap.get('reward_batch') or {}
                                if batch.get('phase') not in {'complete', 'partial'} or not (batch.get('final') or {}).get('verified'):
                                    raise RuntimeError('qingxi_reward_batch_requires_review')
                            if snap.get('status_error') and (snap.get('report') or {}).get('error'):
                                result['stop_reason'] = {'reason': 'status_query_failed'}
                            result['phase'] = finished_phase({**snap, 'stop_reason': result.get('stop_reason')})
                            if result['phase'] == 'bounded_stop':
                                result['stop_reason'] = {'reason': 'quest_time_limit'}
                            break
                        if snap.get('requests', 0) >= 200:
                            result['phase'] = 'bounded_stop'
                            break
                        time.sleep(2)
                    else:
                        result['phase'] = 'bounded_stop'
            finally:
                if page:
                    finish_page(page, result, args.expected_player_id)
                if cdp:
                    try:
                        cdp.call('Browser.close')
                        process.wait(timeout=10)  # Let Chromium flush the localStorage journal.
                    except Exception as exc:
                        result['browser_shutdown_error'] = type(exc).__name__
                    try:
                        cdp.close()
                    except Exception as exc:
                        result['browser_close_error'] = type(exc).__name__
                stop_browser(process)
    except BaseException as exc:
        result.update(phase='held', error=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
    finally:
        os.umask(previous_umask)
        if args.workflow == 'rewards' and not args.read_only and (
                (result.get('reward_batch') or {}).get('phase') not in {'complete', 'partial'}):
            result['phase'] = 'held'
            result.setdefault('error', 'qingxi_reward_batch_requires_review')
        write_result(args.result, result)
    return 0 if result.get('server_checked') and result['phase'] != 'held' else 1


if __name__ == '__main__':
    raise SystemExit(main())
