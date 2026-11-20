"""Pure launcher entry-gate tests; no browser or transport is opened."""
import itertools
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import changfeng_trial_browser as launcher


NATIVE_TIMELINE = r"""
const fs=require('fs'), p=JSON.parse(fs.readFileSync(0,'utf8'));
const noop=()=>{}, actor={root:{position:{}},stopControl:noop};
let localPaused=false,active=false,state='ready',questMode=false,ghost=false,runTier=2,swordTarget=null;
let frameTicks=0,hp=100,startCalls=0,stopCalls=0,serverResolve;
const arena={id:'longwind'}, trial={token:0,ops:0,ending:null,status:{remaining:1,limit:1,used:0,tier:{next:2,best:2}}};
const moonRun={},bloodRun={},siegeRun={},driverRun={},potions={carry:0},waveStartPotions={carry:0};
const ELITE_ROLES=new Set(), enemies=[{inWave:true,hp:100,maxHp:100,baseHp:100,kind:{boss:false,name:'enemy'},
  role:'normal',label:{querySelector:()=>({})}}];
function frame(){if(active&&!localPaused){frameTicks++;hp--;}}
const options={getEncounter:()=>({kind:'trial',arena:'longwind'}),isPaused:()=>false,
 onStart:()=>queueMicrotask(frame),onStop:()=>queueMicrotask(frame),onPauseChange:()=>queueMicrotask(frame),
 trialRequest:()=>new Promise(resolve=>{serverResolve=resolve})};
const reference={impact:{unlock:noop},enable:noop};
const environment={setSwordAttacking:noop}; const canvas={focus:noop};
const enabled=()=>true,inArea=()=>true,getActor=()=>actor,modelsReady=()=>true,instanceArena=()=>false;
const loadEnemyModels=noop,endTrialRun=noop,endMoonRun=noop,endBloodRun=noop,endSiegeRun=noop;
const beginMoonRun=noop,beginBloodRun=noop,beginSiegeRun=noop,beginEscortRun=noop,beginDriverRun=noop;
const startWave=noop,armSwords=noop,applySky=noop,cancelPress=noop,renderHud=noop,say=noop;
const chosenTier=()=>2,trialOp=()=>`start-${++trial.ops}`,setGuard=noop;
function reset(){localPaused=false;}
function stop(){stopCalls++;active=false;state='ready';localPaused=false;}
eval(Object.values(p.functions).join('\n'));
function stats(){return {active,state,arena:arena.id,overworld:false,paused:localPaused,
  trial:{run:trial.run||null,status:trial.status,tier:{chosen:2,run:runTier}}};}
const combat={stats,start,command},lw={combat,runtime:{startTownCombat:()=>{startCalls++;return start();}}};
global.window={__lw:lw};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
(async()=>{
 const accepted=eval(p.start_expression);
 const startSnapshot=JSON.parse(JSON.stringify(stats()));
 await flush();frame();
 const beforeReply={frames:frameTicks,hp,paused:localPaused};
 if(p.scenario!=='unknown')serverResolve({run:{id:'native-run',status:'active',rewarded:p.scenario==='rewarded',
    scale:{normal:{hp:2,damage:1,level:10}}},status:{remaining:0,used:1,limit:1}});
 await flush();frame();
 const afterReply={frames:frameTicks,hp,paused:localPaused,enemyHp:enemies[0].hp};
 const entry=eval(p.entry_expression),resumed=eval(p.resume_expression),resumedAgain=eval(p.resume_expression);
 frame();
 const afterDecision={frames:frameTicks,hp};
 const stopped=p.scenario==='rewarded'?null:eval(p.stop_expression);
 console.log(JSON.stringify({accepted,startSnapshot,beforeReply,afterReply,entry,resumed,resumedAgain,
   afterDecision,stopped,startCalls,stopCalls}));
})().catch(error=>{console.error(error.stack);process.exit(1)});
"""


@pytest.mark.parametrize('scenario', ['rewarded', 'no_rewards', 'unknown'])
def test_pinned_m19_start_and_native_async_receipt_freeze_before_first_frame(monkeypatch, scenario):
    """Execute real pinned Python start_trial and cached native JS control bodies.

    Models/readiness and the server transport are isolated. Native start(),
    beginTrialRun(), its Promise chain, scale application and pause commands are
    verbatim fixtures; queued frames probe whether gameplay is permitted.
    """
    fixture = json.loads((Path(__file__).parent / 'fixtures/longwind_native_entry_20261116.json').read_text())
    reports = []
    class Page:
        loading = False
        def snapshot(self):
            if reports:
                return reports[-1]['startSnapshot']
            return {'state': 'ready', 'trial': {'status': {'remaining': 1, 'tier': {'next': 2}}}}
        def js(self, code, **_kwargs):
            if 'const accepted = lw.runtime.startTownCombat(lw.startArena);' in code:
                payload = {'functions': fixture['functions'], 'scenario': scenario, 'start_expression': code,
                           'entry_expression': launcher.ENTRY_CHECK, 'resume_expression': launcher.RESUME_ENTRY,
                           'stop_expression': launcher.STOP_ENTRY}
                result = subprocess.run(['node', '-e', NATIVE_TIMELINE], input=json.dumps(payload),
                                        text=True, capture_output=True, check=False)
                assert result.returncode == 0, result.stderr
                reports.append(json.loads(result.stdout))
                return reports[-1]['accepted']
            if code == launcher.ENTRY_CHECK:
                return reports[-1]['entry']
            if code == launcher.RESUME_ENTRY:
                return reports[-1]['resumed']
            if code == launcher.STOP_ENTRY:
                return reports[-1]['stopped']
            return True  # Model calibration is outside this entry-timing test.
    namespace = {'Page': Page, 'time': SimpleNamespace(monotonic=lambda: 1, sleep=lambda _n: None),
                 'json': json, 'wait_scene_ready': lambda _page: None, 'mark_trial': lambda *_args: None,
                 'wait_readiness': lambda *_args: None, 'want_tier': lambda *_args: 2,
                 'choose_tier': lambda _page, tier: tier, 'log': lambda *_args: None}
    exec(compile(fixture['python_start_trial'], 'pinned-m19-start_trial.py', 'exec'), namespace)
    runner = SimpleNamespace(start_trial=namespace['start_trial'], wait_trial_closed=Mock(return_value=True))
    verify = launcher.verify_rewarded_entry
    ticks = itertools.count()
    monkeypatch.setattr(launcher, 'verify_rewarded_entry', lambda *args: verify(
        *args, clock=lambda: next(ticks), sleeper=lambda _n: None))
    launcher.install_entry_gate(runner)
    page = Page()
    page.cdp = SimpleNamespace(trial_receipts=[])
    args = SimpleNamespace(practice_only=False, practice_ok=False)
    if scenario == 'rewarded':
        runner.start_trial(page, 'longwind', args)
    else:
        with pytest.raises(SystemExit, match='trial_browser_'):
            runner.start_trial(page, 'longwind', args)
    report = reports[0]
    assert report['startCalls'] == 1
    assert report['startSnapshot']['paused'] is True
    assert report['beforeReply'] == {'frames': 0, 'hp': 100, 'paused': True}
    assert report['afterReply']['frames'] == 0 and report['afterReply']['hp'] == 100
    assert report['afterReply']['paused'] is True
    assert report['resumedAgain'] is False
    if scenario == 'rewarded':
        assert report['afterReply']['enemyHp'] == 200  # Actual async applyTrialScale ran while frozen.
        assert report['resumed'] is True and report['afterDecision']['frames'] == 1
    else:
        assert report['resumed'] is False and report['afterDecision']['frames'] == 0
        assert report['stopped'] is True and report['stopCalls'] == 1


@pytest.mark.parametrize('outcome', ['rewarded', 'no_rewards', 'unknown', 'wrong_arena', 'anomaly', 'resume_failed'])
def test_rewarded_entry_gate_uses_only_native_resume_or_stop(outcome):
    entry = {'active': True, 'arena': 'longwind', 'overworld': False,
             'run_id': 'owned-run', 'rewarded': outcome != 'no_rewards', 'held': True, 'owned': True}
    if outcome == 'unknown':
        entry['rewarded'] = None
    elif outcome == 'wrong_arena':
        entry['arena'] = 'bamboo'
    elif outcome == 'anomaly':
        entry['anomaly'] = 'bad_receipt'
    commands = []
    def js(code):
        commands.append(code)
        if code == launcher.ENTRY_CHECK:
            return entry
        return outcome != 'resume_failed'
    page = SimpleNamespace(js=js, cdp=SimpleNamespace(trial_receipts=[]))
    runner = SimpleNamespace(wait_trial_closed=Mock(return_value=True))
    ticks = iter(range(100))
    kwargs = {'clock': lambda: next(ticks), 'sleeper': lambda _seconds: None}
    if outcome == 'rewarded':
        launcher.verify_rewarded_entry(page, 'longwind', runner, **kwargs)
        assert commands == [launcher.ENTRY_CHECK, launcher.RESUME_ENTRY]
        runner.wait_trial_closed.assert_not_called()
    else:
        with pytest.raises(SystemExit, match='trial_browser_'):
            launcher.verify_rewarded_entry(page, 'longwind', runner, **kwargs)
        assert commands[-1] == launcher.STOP_ENTRY
        runner.wait_trial_closed.assert_called_once_with(page, 0, timeout=20)


def test_entry_wrapper_never_restarts_or_generates_reports(monkeypatch):
    def begin(page, _arena, _args):
        page.js(launcher.START_CALL)
        return ({'remaining': 1}, 2, 2)
    original = Mock(side_effect=begin)
    runner = SimpleNamespace(start_trial=original)
    verify = Mock(side_effect=SystemExit('trial_browser_no_rewards'))
    monkeypatch.setattr(launcher, 'verify_rewarded_entry', verify)
    launcher.install_entry_gate(runner)
    page = SimpleNamespace(js=Mock(return_value=True))
    args = SimpleNamespace(practice_only=False, practice_ok=False)
    with pytest.raises(SystemExit, match='no_rewards'):
        runner.start_trial(page, 'longwind', args)
    original.assert_called_once_with(page, 'longwind', args)
    verify.assert_called_once_with(page, 'longwind', runner)
