import json
import shutil
import subprocess

import pytest

from tools import qingxi_quest_browser as worker


def test_native_gather_waits_for_fresh_stationary_frames(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the native settling replay')

    def extract(source):
        start = source.index('  async function waitForStoryStill(owner) {')
        return source[start:source.index('  function questNodeApproach(', start)]

    original = extract((worker.VENDOR / 'trial-bot.user.js').read_text())
    generated = extract(worker.browser_script(8659059191))
    path = tmp_path / 'settling.cjs'
    path.write_text('const original=' + json.dumps(original) + ', generated=' + json.dumps(generated) + ';\n' + r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const start = [65.73379324749719, 4.895181357524489, 1148.6359803051362];
const end = [65.95443647012115, 4.828114790387799, 1148.8933867628632];
function replay(source, mode = 'drift') {
  let clock = 0, owner = 1, released = 0;
  const runtime = {setFlightVerticalInput: value => assert.equal(value, 0)};
  const window = {__rt: runtime}, observations = [];
  const context = vm.createContext({window, state: {}, Number, Math,
    Date: {now: () => clock},
    storyReady: async expected => {if (expected !== owner) throw Error('owner lost');},
    releaseKeys: () => released++,
    position3: p => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite) ? p : null,
    dist: (a, b) => Math.hypot(a[0]-b[0], a[2]-b[2]),
    readWorld: () => {
      const moved = mode === 'drift' && clock >= 640;
      const draws = mode === 'missing' ? undefined : mode === 'frozen' || clock < 640 ? 244 :
        mode === 'slow' ? 244 + Math.floor(clock / 2000) : 244 + Math.floor((clock - 640) / 80);
      const world = {actorPosition: moved ? end.slice() : start.slice(), draws};
      observations.push({clock, ...world}); return world;
    },
    storyWait: async ms => {
      clock += ms;
      if (mode === 'owner' && clock >= 160) owner++;
      if (mode === 'runtime' && clock >= 160) window.__rt = {};
    },
  });
  vm.runInContext(source, context);
  return {run: async () => ({position: await context.waitForStoryStill(1), clock, released, observations})};
}
(async () => {
  const before = await replay(original).run();
  assert.equal(before.clock, 400);
  assert.deepEqual(Array.from(before.position), start);
  const after = await replay(generated).run();
  assert.ok(after.clock >= 960);
  assert.deepEqual(Array.from(after.position), end);
  assert.ok(after.released > before.released);
  // A slow but advancing renderer is supported within the bounded wait.
  const slow = await replay(generated, 'slow').run();
  assert.equal(slow.clock, 6000);
  assert.deepEqual(Array.from(slow.position), start);
  // No fresh frame, unavailable frame counter or lost ownership must never release gathering.
  await assert.rejects(replay(generated, 'frozen').run(), /游戏帧未更新/);
  await assert.rejects(replay(generated, 'missing').run(), /无法核对游戏帧/);
  await assert.rejects(replay(generated, 'owner').run(), /owner lost/);
  await assert.rejects(replay(generated, 'runtime').run(), /人物场景已变更/);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True)
