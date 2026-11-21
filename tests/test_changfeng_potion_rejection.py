"""Generate the real pinned script, then exercise its bot.tick offline in Node."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import changfeng_trial_browser as launcher


M19 = Path('/root/2026/qingxi-trial-m19-wa-canary-20261116/tools')
JS_SHA256 = '67d98870f531ae2642cf645054e0dd67cf9cf92441f74e7d7f617ed321a04e71'
PY_SHA256 = 'f5f113bb168f7327ec2af2a699e96a22505da928090d5df67ad591072ba738ec'


def native_generator():
    """Compile only the installed browser_script; never import its launcher."""
    if not all((M19 / name).is_file() for name in ('longwind_trial_bot.py', 'longwind_trial_bot.js')):
        pytest.skip('pinned m19 source is not installed on this host')
    assert hashlib.sha256((M19 / 'longwind_trial_bot.js').read_bytes()).hexdigest() == JS_SHA256
    assert hashlib.sha256((M19 / 'longwind_trial_bot.py').read_bytes()).hexdigest() == PY_SHA256
    tree = ast.parse((M19 / 'longwind_trial_bot.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'browser_script')
    namespace = {'JS_PATH': M19 / 'longwind_trial_bot.js'}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'pinned-browser-script.py', 'exec'), namespace)
    return namespace['browser_script']


def generate(*, adapted=True):
    runner = SimpleNamespace(browser_script=native_generator())
    launcher.install_projectile_timing(runner)
    if adapted:
        launcher.install_potion_rejection(runner)
    return runner.browser_script(False, 42)


def probe(program):
    test = Path(__file__).parent / 'fixtures/changfeng_potion_tick.cjs'
    result = subprocess.run(['node', str(test)], input=json.dumps({'source': program}), text=True,
                            capture_output=True, timeout=10)
    return result


def test_real_generated_script_keeps_guards_and_handles_potion_rejection():
    before = generate(adapted=False)
    program = generate()
    assert 'expected = 42' in program and 'trial_player_mismatch' in program
    assert 'window.__lw.holdStart = true' in program
    assert 'trial_projectile_timing_anchor_missing' in program
    result = probe(program)
    assert result.returncode == 0, result.stderr
    assert 'PASS 25' in result.stdout
    # Generating the wrapper leaves both installed source files untouched.
    assert hashlib.sha256((M19 / 'longwind_trial_bot.js').read_bytes()).hexdigest() == JS_SHA256
    assert hashlib.sha256((M19 / 'longwind_trial_bot.py').read_bytes()).hexdigest() == PY_SHA256
    # Removing only this adaptation reproduces the same real-tick failure.
    reverted = probe(before)
    assert reverted.returncode != 0
    assert 'rejected hp must continue to dodge' in reverted.stderr


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('drift', ['missing', 'duplicate'])
def test_pinned_branch_drift_refuses_to_generate_any_fallback(index, drift):
    source = '\n'.join(before for before, _after in launcher.POTION_BRANCHES)
    anchor = launcher.POTION_BRANCHES[index][0]
    source = source.replace(anchor, '') if drift == 'missing' else source + '\n' + anchor
    native = Mock(return_value=source)
    runner = SimpleNamespace(browser_script=native)
    launcher.install_potion_rejection(runner)
    with pytest.raises(RuntimeError, match='trial_potion_rejection_anchor_changed'):
        runner.browser_script(False, 42)
    native.assert_called_once_with(False, 42)


def test_helper_refuses_a_conflicting_existing_potion_method():
    helper = Path(launcher.__file__).with_name('changfeng_potion_rejection.js').read_text()
    source = 'global.window={__lw:{bot:{tick(){},tryPotion(){}}}};\n' + helper
    result = subprocess.run(['node', '-e', source], text=True, capture_output=True, timeout=10)
    assert result.returncode != 0 and 'trial_potion_rejection_bot_changed' in result.stderr


def test_launcher_installs_this_adaptation_without_replacing_entry_or_runner(monkeypatch, tmp_path):
    calls = []
    runner = SimpleNamespace(main=Mock(return_value=7))
    monkeypatch.setattr(launcher.importlib, 'import_module', Mock(return_value=runner))
    for name in ('install_projectile_timing', 'install_potion_rejection', 'install_entry_gate'):
        monkeypatch.setattr(launcher, name, lambda value, key=name: calls.append((key, value)))
    assert launcher.main(['--tool-root', str(tmp_path), '--fixture-only']) == 7
    assert calls == [(key, runner) for key in
                     ('install_projectile_timing', 'install_potion_rejection', 'install_entry_gate')]
    runner.main.assert_called_once_with(['--fixture-only'])
