"""Run the installed m19 engine with a native rewarded-entry gate.

The existing runner owns Chromium, input, per-wave retry, network receipts and
cleanup. This wrapper never constructs reports or starts a second game.
"""
import argparse
import importlib
from pathlib import Path
import sys
import time


ENTRY_CHECK = """(() => {
  const lw = window.__lw, combat = lw?.combat, state = combat?.stats?.();
  if (!state) return null;
  const run = state.trial?.run;
  if (state.active && !state.overworld && state.state === 'fighting' &&
      typeof run?.rewarded !== 'boolean' && !state.paused) {
    if (combat.command('pause') !== true) throw Error('trial_entry_pause_failed');
    lw.localRewardHold = true;
  }
  return {arena:state.arena, active:state.active, overworld:state.overworld,
          run_id:run?.id, rewarded:run?.rewarded, held:lw.localRewardHold === true,
          anomaly:state.trial?.anomaly || state.trial?.syncError || null};
})()"""
RESUME_ENTRY = """(() => {
  const lw = window.__lw, combat = lw?.combat;
  if (!lw?.localRewardHold) return true;
  if (combat?.stats?.().paused && combat.command('pause') !== true) return false;
  lw.localRewardHold = false;
  return combat?.stats?.().paused !== true;
})()"""
STOP_ENTRY = "window.__lw.bot.stop(); window.__lw.combat.command('stop')"


def verify_rewarded_entry(page, arena, runner, *, clock=time.monotonic, sleeper=time.sleep):
    deadline = clock() + 15
    reason = 'trial_browser_reward_unverified'
    while clock() < deadline:
        entry = page.js(ENTRY_CHECK) or {}
        if (entry.get('arena') != arena or entry.get('active') is not True
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
        result = original(page, arena, args)
        if not args.practice_only and not args.practice_ok:
            verify_rewarded_entry(page, arena, runner)
        return result

    runner.start_trial = start_trial


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--tool-root', type=Path, required=True)
    options, remaining = parser.parse_known_args(argv)
    sys.path.insert(0, str(options.tool_root / 'tools'))
    runner = importlib.import_module('longwind_trial_bot')
    install_entry_gate(runner)
    return runner.main(remaining)


if __name__ == '__main__':
    raise SystemExit(main())
