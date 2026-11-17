"""Bounded, identity-checked NPC tasks using the pinned upstream assistant.

Upstream owns movement, combat, puzzles and travel. This adapter owns local
policy, evidence, request bounds and shutdown. No trial starts or extra fishing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/dwelling-autobot'
GUARD = Path(__file__).with_name('qingxi_quest_policy.js').read_text()
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
QUEST_NATIVE_TAKEOFF = """  function takeoffGround(point) {
    // beginFlightNow checks canFly at the actor's current height. Landing needs
    // walkable support; takeoff must not inherit the landing-only restriction.
    const env = fieldEnvironment;
    if (!position3(point) || !env?.groundHeight || !env?.canFly) return null;
    const y = env.groundHeight(point[0], point[2], point[1]);
    return Number.isFinite(y) && Math.abs(y - point[1]) <= .43 &&
      env.canFly(point[0], point[2], .4, point[1]) ? [point[0], y, point[2]] : null;
  }
"""


def browser_script(player, choices=None, *, read_only=False, reviewed_cast_pending=None, confirmed_cast_receipts=None,
                   reviewed_release_pending=None):
    source = (VENDOR / 'trial-bot.user.js').read_bytes()
    provenance = json.loads((VENDOR / 'source.json').read_text())
    if hashlib.sha256(source).hexdigest() != provenance['sha256']:
        raise RuntimeError('qingxi_upstream_source_changed')
    config = {'player': player, 'choices': choices or {}, 'readOnly': read_only,
              'reviewedCastPending': reviewed_cast_pending or [],
              'reviewedReleasePending': reviewed_release_pending or [],
              'confirmedCastReceipts': confirmed_cast_receipts or []}
    script = source.decode()
    # Use upstream's own deferred-task path, while the fetch policy remains the
    # final guard. Missing choices/fishing must not prevent unrelated errands.
    patches = {
        script[script.index('  function takeoffGround('):script.index('  function flightCombatBlocked(')]:
            QUEST_NATIVE_TAKEOFF,
        'const wanted = state.story.options.rewardChoices?.[q.id];':
            "const wanted = state.story.options.rewardChoices?.[q.id];\n"
            "        if (!wanted) throw Object.assign(new Error('任务奖励保留待选'), {code:'unsupported_quest_objective'});",
        'async function fishObjective(q, objective, owner) {':
            "async function fishObjective(q, objective, owner) {\n"
            "    throw Object.assign(new Error('河岸钓鱼等待现有日计划'), {code:'fishing_daily_limit_reached'});",
        "if (!window.__rt.startQuestCombat?.(objective.arena)) throw new Error('游戏未接受郊外战斗入口，请确认人物已进入区域');":
            QUEST_COMBAT_ENTRY,
        'async function fightEnemy(enemyId, owner) {': QUEST_COMBAT_LOOT,
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
        "if (!path) throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' + recovery.reason);":
            "if (!path) {\n"
            "              window.__qx.navigationFailure = {position:p.slice(), goal:goal.slice(), radius,\n"
            "                obstacles:obstacles.map(o=>o.slice()), obstaclesReviewed, recovery, at:Date.now()/1000};\n"
            "              throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' + recovery.reason);\n"
            "            }",
    }
    for before, after in patches.items():
        if script.count(before) != 1:
            raise RuntimeError('qingxi_upstream_policy_anchor_changed')
        script = script.replace(before, after)
    return GUARD.replace('__CONFIG__', json.dumps(config, ensure_ascii=False)) + '\n' + script


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
  report:r?{phase:r.phase,error:r.error,story:r.story,questSummary:r.questSummary,navigation:r.navigation,
    recent:r.recent?.slice(-12),fieldSettlements:r.fieldSettlements,retry:r.retry}:null,
  run:window.__qingxiRun?{done:window.__qingxiRun.done,value:window.__qingxiRun.value}:null};
})()"""


def write_result(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(temporary, path)


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
                reviewed_release_pending=None):
    target = cdp.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True})['targetId']
    session = cdp.attach(target)
    for method in ('Page.enable', 'Runtime.enable', 'Network.enable', 'Page.bringToFront'):
        cdp.call(method, session=session)
    cdp.call('Page.addScriptToEvaluateOnNewDocument',
             {'source': browser_script(player, choices, read_only=read_only, reviewed_cast_pending=reviewed_cast_pending,
                                       confirmed_cast_receipts=confirmed_cast_receipts,
                                       reviewed_release_pending=reviewed_release_pending)}, session)
    return browser.Page(cdp, session, target)


def enter_page(page, cdp, url, player):
    cdp.call('Page.navigate', {'url': url}, page.s)
    page.wait('window.__autobot && window.__qx?.player===' + str(player), 90, '任务身份校验', every=1)
    page.js('window.__qingxiEnter={done:false};window.__autobot.enter().then(value=>{window.__qingxiEnter={done:true,value}});void 0')
    page.wait('window.__qingxiEnter?.done', 650, '上游助手入坊', every=2)
    error = page.js('window.__autobot.report().error')
    if error:
        raise RuntimeError('qingxi_enter_failed: ' + str(error))
    page.loading = False


FINAL_REFRESH = """(async()=>{
 window.__qx.quiescing=true;await window.__qx.queue;
 window.__qx.statusCache.clear();
 const before=window.__qx.questStatusRevision;
 const response=await window.__quests.refresh();
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
    args = parser.parse_args()
    sys.path.insert(0, str(args.tool_root / 'tools'))
    import longwind_trial_bot as browser
    from qingxi_local import profile_lock, read_url_file, stop_browser
    options = json.loads(args.options_file.read_text()) if args.options_file else {}
    result = {'phase': 'preparing', 'upstream_version': '2.0.53', 'navigation_policy': 4}
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
                                   options.get('reviewed_release_pending'))
                result['entry_started_at'] = time.time()
                write_result(args.result, result)
                try:
                    enter_page(page, cdp, read_url_file(args.url_file), args.expected_player_id)
                except BaseException as exc:
                    result['entry_error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    raise
                page.js('window.__autobot.inspectQuests("全部任务")', timeout=60)
                if args.read_only:
                    result['phase'] = 'inspected'
                else:
                    opts = {'kind': '全部任务', 'maxMinutes': min(15, max(.5, args.max_seconds / 60)),
                            'maxTasks': 8, 'rewardChoices': options.get('choices', {})}
                    result['mainline_started_at'] = time.time()
                    write_result(args.result, result)
                    page.js('window.__qingxiRun={done:false};window.__autobot.mainline(' + json.dumps(opts)
                            + ').then(value=>{window.__qingxiRun={done:true,value}});void 0')
                    started = time.monotonic()
                    while time.monotonic() - started < min(900, max(30, args.max_seconds)) + 60:
                        snap = page.js(SNAPSHOT)
                        result.update(phase='running', **snap)
                        write_result(args.result, result)
                        if snap.get('error'):
                            raise RuntimeError(snap['error'])
                        if (snap.get('run') or {}).get('done'):
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
        write_result(args.result, result)
    return 0 if result.get('server_checked') and result['phase'] != 'held' else 1


if __name__ == '__main__':
    raise SystemExit(main())
