"""Run the installed m19 engine with a native rewarded-entry gate.

The existing runner owns Chromium, input, per-wave retry, network receipts and
cleanup. This wrapper never constructs reports or starts a second game.
"""
import argparse
import importlib
from pathlib import Path
import sys
import time


START_CALL = 'return !!lw.runtime.startTownCombat(lw.startArena);'
ATOMIC_START = """const accepted = lw.runtime.startTownCombat(lw.startArena);
  if (accepted) {
    const combat = lw.combat, state = combat?.stats?.();
    const gate = {combat, arena:lw.startArena, runId:null, frozen:false, released:false};
    lw.localTrialEntry = gate;
    try {
      if (!state?.active || state.overworld || state.arena !== gate.arena || state.state !== 'fighting')
        throw Error('trial_entry_scene_changed');
      // start() synchronously reset localPaused. Freeze after that reset, in
      // this same JavaScript task, before any response continuation or frame.
      if (!state.paused && combat.command('pause') !== true) throw Error('trial_entry_pause_failed');
      if (combat.stats().paused !== true) throw Error('trial_entry_pause_unverified');
      gate.frozen = true;
    } catch (error) {
      try { combat?.command?.('stop'); } catch {}
      throw error;
    }
  }
  return !!accepted;"""

ENTRY_CHECK = """(() => {
  const lw = window.__lw, combat = lw?.combat, state = combat?.stats?.();
  if (!state) return null;
  const gate = lw.localTrialEntry, run = state.trial?.run;
  const owned = gate?.combat === combat && gate.arena === state.arena && gate.frozen && !gate.released;
  if (owned && state.active && !state.overworld && state.state === 'fighting' && !state.paused) {
    if (combat.command('pause') !== true) throw Error('trial_entry_pause_failed');
  }
  if (owned && run?.id && !gate.runId) gate.runId = run.id;
  return {arena:state.arena, active:state.active, overworld:state.overworld,
          run_id:run?.id, rewarded:run?.rewarded, held:combat.stats().paused,
          owned:owned && (!run?.id || gate.runId === run.id),
          anomaly:state.trial?.anomaly || state.trial?.syncError || null};
})()"""
RESUME_ENTRY = """(() => {
  const lw = window.__lw, combat = lw?.combat, gate = lw?.localTrialEntry;
  const state = combat?.stats?.(), run = state?.trial?.run;
  if (!gate?.frozen || gate.released || gate.combat !== combat || gate.arena !== state?.arena ||
      !state.active || !run?.id || run.id !== gate.runId || run.rewarded !== true ||
      state.trial?.anomaly || state.trial?.syncError) return false;
  if (state.paused && combat.command('pause') !== true) return false;
  if (combat.stats().paused !== false) return false;
  gate.released = true;
  return true;
})()"""
STOP_ENTRY = """(() => {
  const lw = window.__lw, combat = lw?.combat, gate = lw?.localTrialEntry;
  lw?.bot?.stop?.();
  if (gate?.combat !== combat || gate.arena !== combat?.stats?.()?.arena) return false;
  return combat.command('stop');
})()"""


def install_projectile_timing(runner):
    original = runner.browser_script
    timing = Path(__file__).with_name('changfeng_projectile_timing.js').read_text(encoding='utf-8')

    def browser_script(*args, **kwargs):
        return original(*args, **kwargs) + '\n' + timing

    runner.browser_script = browser_script


def verify_rewarded_entry(page, arena, runner, *, clock=time.monotonic, sleeper=time.sleep):
    deadline = clock() + 15
    reason = 'trial_browser_reward_unverified'
    while clock() < deadline:
        entry = page.js(ENTRY_CHECK) or {}
        if (entry.get('owned') is not True or entry.get('held') is not True
                or entry.get('arena') != arena or entry.get('active') is not True
                or entry.get('overworld') or entry.get('anomaly')):
            break
        if entry.get('run_id') and entry.get('rewarded') is True:
            if entry.get('held') and page.js(RESUME_ENTRY) is not True:
                reason = 'trial_browser_entry_resume_failed'
                break
            return
        if entry.get('run_id') and entry.get('rewarded') is False:
            reason = 'trial_browser_no_rewards'
            break
        sleeper(.25)
    # Only native stop is allowed after an unknown/no-reward start. Its real
    # receipt is collected by the installed runner's normal finally block.
    receipt_start = len(page.cdp.trial_receipts)
    try:
        page.js(STOP_ENTRY)
        runner.wait_trial_closed(page, receipt_start, timeout=20)
    finally:
        raise SystemExit(reason)


def install_entry_gate(runner):
    original = runner.start_trial

    def start_trial(page, arena, args):
        if args.practice_only or args.practice_ok:
            return original(page, arena, args)
        original_js = page.js
        injected = False

        def atomic_js(expression, *positional, **keywords):
            nonlocal injected
            if START_CALL in expression:
                if expression.count(START_CALL) != 1:
                    raise RuntimeError('trial_entry_start_anchor_changed')
                expression = expression.replace(START_CALL, ATOMIC_START)
                injected = True
            return original_js(expression, *positional, **keywords)

        page.js = atomic_js
        try:
            result = original(page, arena, args)
        finally:
            page.js = original_js
        if not injected:
            raise SystemExit('trial_entry_start_anchor_missing')
        verify_rewarded_entry(page, arena, runner)
        return result

    runner.start_trial = start_trial


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--tool-root', type=Path, required=True)
    options, remaining = parser.parse_known_args(argv)
    sys.path.insert(0, str(options.tool_root / 'tools'))
    runner = importlib.import_module('longwind_trial_bot')
    install_projectile_timing(runner)
    install_entry_gate(runner)
    return runner.main(remaining)


if __name__ == '__main__':
    raise SystemExit(main())
