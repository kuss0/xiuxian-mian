import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for browser-script execution")

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const listeners = {}, calls = [], intervals = [];
const element = {addEventListener(){}, classList:{contains(){return false;}}};
const context = {
  CHAOGU_BOOT_DATA:{snapshot:{identities:[]}}, POLL_INTERVAL_MS:8000,
  innerWidth:1280, matchMedia(){return {matches:false};},
  addEventListener(){}, setInterval(fn,ms){intervals.push({fn,ms});},
  setTimeout(){}, clearTimeout(){},
  document:{
    addEventListener(type,fn){(listeners[type] ||= []).push(fn);},
    getElementById(){return element;}, querySelector(){return null;}
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('model/web/static/js/app.js','utf8'),context);
for (const name of ['renderIdentityList','renderIdentitySelect','renderSummary','renderModules',
                    'renderPassiveInboxPanel','openStartupAlertsModalIfNeeded','setFlash','syncSelectedIdToUrl']) {
  context[name] = () => calls.push(name);
}
"""


@pytest.mark.parametrize("check", [
    r"""
    context.renderAll();
    assert.deepEqual(calls, []);
    assert.equal(intervals.length, 0);
    assert.equal(listeners.DOMContentLoaded.length, 1);
    listeners.DOMContentLoaded[0]();
    context.startApp();
    assert.equal(calls.filter(x=>x==='renderModules').length, 1);
    assert.equal(calls.filter(x=>x==='renderPassiveInboxPanel').length, 1);
    assert.equal(intervals.length, 1);
    assert.equal(intervals[0].ms, 8000);
    """,
    r"""
    context.registerRenderHook('one',()=>calls.push('obsolete'));
    context.registerRenderHook('two',()=>calls.push('second'));
    context.registerRenderHook('one',()=>calls.push('first'));
    context.startApp();
    assert.deepEqual(calls.slice(-2), ['first','second']);
    assert.equal(calls.includes('obsolete'), false);
    calls.length=0;
    context.renderAll();
    assert.deepEqual(calls.slice(-2), ['first','second']);
    assert.equal(calls.filter(x=>x==='renderPassiveInboxPanel').length,1);
    """,
    r"""
    context.startApp();
    let options;
    context.refreshState = value => {options=value;};
    intervals[0].fn();
    assert.equal(options.silent, true);
    assert.equal(options.keepFlash, true);
    """,
])
def test_console_startup_and_render_hooks(check):
    subprocess.run(["node", "-e", HARNESS + check], cwd=ROOT, check=True, capture_output=True, text=True)
