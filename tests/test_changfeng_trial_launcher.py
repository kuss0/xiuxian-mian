"""Pure launcher entry-gate tests; no browser or transport is opened."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import changfeng_trial_browser as launcher


@pytest.mark.parametrize('outcome', ['rewarded', 'no_rewards', 'unknown', 'wrong_arena', 'anomaly', 'resume_failed'])
def test_rewarded_entry_gate_uses_only_native_resume_or_stop(outcome):
    entry = {'active': True, 'arena': 'longwind', 'overworld': False,
             'run_id': 'owned-run', 'rewarded': outcome != 'no_rewards', 'held': True}
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
    original = Mock(return_value=({'remaining': 1}, 2, 2))
    runner = SimpleNamespace(start_trial=original)
    verify = Mock(side_effect=SystemExit('trial_browser_no_rewards'))
    monkeypatch.setattr(launcher, 'verify_rewarded_entry', verify)
    launcher.install_entry_gate(runner)
    page = object()
    args = SimpleNamespace(practice_only=False, practice_ok=False)
    with pytest.raises(SystemExit, match='no_rewards'):
        runner.start_trial(page, 'longwind', args)
    original.assert_called_once_with(page, 'longwind', args)
    verify.assert_called_once_with(page, 'longwind', runner)
