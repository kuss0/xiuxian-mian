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


def browser_script(player, choices=None, *, read_only=False):
    source = (VENDOR / 'trial-bot.user.js').read_bytes()
    provenance = json.loads((VENDOR / 'source.json').read_text())
    if hashlib.sha256(source).hexdigest() != provenance['sha256']:
        raise RuntimeError('qingxi_upstream_source_changed')
    config = {'player': player, 'choices': choices or {}, 'readOnly': read_only}
    script = source.decode()
    # Use upstream's own deferred-task path, while the fetch policy remains the
    # final guard. Missing choices/fishing must not prevent unrelated errands.
    patches = {
        'const wanted = state.story.options.rewardChoices?.[q.id];':
            "const wanted = state.story.options.rewardChoices?.[q.id];\n"
            "        if (!wanted) throw Object.assign(new Error('任务奖励保留待选'), {code:'unsupported_quest_objective'});",
        'async function fishObjective(q, objective, owner) {':
            "async function fishObjective(q, objective, owner) {\n"
            "    throw Object.assign(new Error('河岸钓鱼等待现有日计划'), {code:'fishing_daily_limit_reached'});",
        "if (!window.__rt.startQuestCombat?.(objective.arena)) throw new Error('游戏未接受郊外战斗入口，请确认人物已进入区域');":
            QUEST_COMBAT_ENTRY,
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
  world:q?.world,position:window.__rt?.stats?.().actorPosition,
  state:s?.state,overworld:s?.overworld,pending:(s?.fieldPending||0)+(window.__qx?.pending.size||0),hp:s?.hp,
  pending_operations:[...(window.__qx?.pending.values()||[])],
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


def open_page(browser, cdp, url, player, choices, read_only):
    target = cdp.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True})['targetId']
    session = cdp.attach(target)
    for method in ('Page.enable', 'Runtime.enable', 'Network.enable', 'Page.bringToFront'):
        cdp.call(method, session=session)
    cdp.call('Page.addScriptToEvaluateOnNewDocument',
             {'source': browser_script(player, choices, read_only=read_only)}, session)
    page = browser.Page(cdp, session, target)
    cdp.call('Page.navigate', {'url': url}, session)
    page.wait('window.__autobot && window.__qx?.player===' + str(player), 90, '任务身份校验', every=1)
    page.js('window.__qingxiEnter={done:false};window.__autobot.enter().then(value=>{window.__qingxiEnter={done:true,value}});void 0')
    page.wait('window.__qingxiEnter?.done', 650, '上游助手入坊', every=2)
    error = page.js('window.__autobot.report().error')
    if error:
        raise RuntimeError('qingxi_enter_failed')
    page.loading = False
    return page


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
    result = {'phase': 'preparing', 'upstream_version': '2.0.53'}
    cdp = process = page = None
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    previous_umask = os.umask(0o077)
    try:
        with profile_lock(args.profile_dir):
            try:
                process = browser.launch_chrome(browser.find_chrome(None), args.profile_dir, browser.free_port(), headless=True, backend='mesa')
                port = next(int(arg.split('=')[1]) for arg in process.args if arg.startswith('--remote-debugging-port='))
                cdp = browser.CDP(browser.cdp_version(port)['webSocketDebuggerUrl'])
                page = open_page(browser, cdp, read_url_file(args.url_file), args.expected_player_id,
                                 options.get('choices', {}), args.read_only)
                page.js('window.__autobot.inspectQuests("全部任务")', timeout=60)
                if args.read_only:
                    result['phase'] = 'inspected'
                else:
                    opts = {'kind': '全部任务', 'maxMinutes': min(15, max(.5, args.max_seconds / 60)),
                            'maxTasks': 8, 'rewardChoices': options.get('choices', {})}
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
                            result['phase'] = 'waiting' if result.get('stop_reason') else (
                                'held' if (snap.get('report') or {}).get('error') else 'done')
                            break
                        if snap.get('requests', 0) >= 200:
                            result['phase'] = 'bounded_stop'
                            break
                        time.sleep(2)
                    else:
                        result['phase'] = 'bounded_stop'
            finally:
                if page:
                    try:
                        page.js('window.__autobot.stop();window.__fc?.stop()')
                        page.wait('(window.__fc?.stats?.().fieldPending||0)===0 && window.__qx.pending.size===0',
                                  45, '真实战斗回执', every=1)
                        page.js('(async()=>{window.__qx.quiescing=true;await window.__qx.queue;window.__qx.statusCache.clear();return window.__quests.refresh()})()', timeout=30)
                        final = page.js(SNAPSHOT)
                        final['stop_reason'] = final.get('stop_reason') or result.get('stop_reason')
                        result.update(final)
                        result['server_checked'] = not result.get('error') and not result.get('pending')
                    except Exception:
                        result['server_checked'] = False
                if cdp:
                    cdp.close()
                stop_browser(process)
    except BaseException as exc:
        result.update(phase='held', error=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
    finally:
        os.umask(previous_umask)
        write_result(args.result, result)
    return 0 if result.get('server_checked') and result['phase'] != 'held' else 1


if __name__ == '__main__':
    raise SystemExit(main())
