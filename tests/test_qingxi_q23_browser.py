import json
import shutil
import subprocess

import pytest

from tools import qingxi_quest_browser as worker


def test_q23_browser_wire_gate(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the browser wire fixture')
    source = worker.browser_script(42)
    upstream = source.index('// ==UserScript==')
    policy = source[:source.index('/* Adapt dwelling-3d.js')]

    def extract(first, last):
        start = source.index(first, upstream)
        return source[start:source.index(last, start)]

    native = (extract('  async function storyRequest(', '  function nextStoryQuest(')
              + extract('  // Q23 has a server-owned', '  function rotateMask(')
              + extract('  async function executeStoryStep(', '  async function runStory('))
    path = tmp_path / 'q23.cjs'
    path.write_text('const policy=' + json.dumps(policy) + ', native=' + json.dumps(native) + ';\n' + r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');

function fixture({remaining = 420000, nativeRemaining = remaining, wait = 0, alter,
    fail = false, returnVerified = true, threats = 0, defendMs = 0} = {}) {
  let clock = 0, owner = 1;
  const endpoint = 'https://fixture.invalid/api/miniapp/xianxia-dwelling/quests';
  const ids = {quest: 'qx_q23', item: 'qxq_sealing_bottle', node: 'opaque-dynamic-q23-node'};
  const actorPosition = [40, 0, 60];
  const wire = [], persisted = [], store = new Map();
  const wireUrls = [];
  const slot = {status: 'active', progress: {bottle: 0}, required: {bottle: 1}};
  const target = {id: ids.node, itemId: ids.item, position: [40, 60], region: 'yue'};
  const data = {world: {current: 'yue'}, states: {[ids.quest]: slot}, nodes: [target]};
  const objective = {id: 'bottle', type: 'interact', worldObject: ids.item,
    progress: 0, required: 1, timeLimit: 300};
  const window = {
    __rt: {},
    __autobot: {quests: () => [{id: ids.quest, choices: []}]},
    fetch: async (_url, options) => {
      const raw = options?.body ?? (_url instanceof Request ? await Request.prototype.clone.call(_url).text() : '{}');
      const body = JSON.parse(raw);
      wire.push(body);
      wireUrls.push(_url instanceof Request ? _url.url : String(_url));
      if (fail) throw Error('response missing');
      return new Response(JSON.stringify({ok: true, gatherId: 'native-id', duration: .1}));
    },
  };
  let operation = 0;
  window.__quests = {
    snapshot: () => data,
    fieldRequest: async (action, body) => {
      const result = await window.fetch(endpoint, {body: JSON.stringify({action, ...body,
        operationId: 'original-' + (++operation), region: 'yue', position: actorPosition})});
      return result.json();
    },
  };
  const state = {story: {runtime: window.__rt, quests: window.__quests, current: ids.quest,
    deadline: nativeRemaining, q23WorkerDeadline: remaining}};
  const context = vm.createContext({window, state, URL, Request, Response, AbortSignal,
    document: {hidden: false}, location: new URL(endpoint),
    localStorage: {getItem: key => store.get(key), setItem(key, value) {store.set(key, value); persisted.push(JSON.parse(value));}},
    Date: {now: () => clock}, performance: {now: () => clock},
    setTimeout(callback, ms) {clock += ms; if (alter) alter({data, slot, target, actorPosition, window, invalidate: () => owner++}); callback();},
    checkOwner: expected => {if (owner !== expected) throw Error('owner lost');},
    storyData: () => data, storyQuest: id => ({id, giver: 'herbalist', receiver: 'herbalist'}),
    storyReady: async () => {}, storyCheck: () => {}, releaseKeys: () => {},
    storyWait: async ms => {clock += ms;}, readCombat: () => null,
    fieldThreat: () => threats-- > 0 ? {id: 'nearby-enemy'} : null,
    defendNearby: async () => {clock += defendMs;},
    position3: value => Array.isArray(value) && value.length >= 2 ? [value[0], 0, value.at(-1)] : null,
    dist: (a, b) => a && b ? Math.hypot(...a.map((v, i) => v - b[i])) : 0,
    readWorld: () => ({actorPosition}), shown: id => id === 'dwellingWorld',
    waitForStoryStill: async () => actorPosition.slice(), unsupportedObjective: () => null,
    approach: async () => {}, approachNpc: async () => {}, myPos: () => actorPosition,
    questNodeApproach: () => ({point: actorPosition, radius: 1}),
    caveIsSafe: () => returnVerified,
    finishInCave: async owner => {state.home = {owner, destination: 'cave', verified: returnVerified};},
    step: () => {},
  });
  vm.runInContext(policy, context);
  vm.runInContext(native, context);
  window.__qx.player = 42;
  window.__qx.last = wait ? -(5000 - wait) : -Infinity;
  return {wire, wireUrls, persisted, state, data, slot, target, window, endpoint,
    execute: (status = 'active', rowObjective = objective) => context.executeStoryStep(
      {id: ids.quest, title: '血煞封瓶', status, objectives: [rowObjective]}, 1),
    fetchGather: (nodeId, action = 'gather-start') => window.__quests.fieldRequest(action, {nodeId}),
    finishDeferred: () => context.q23FinishDeferred(Error('fixture'), 1),
    ids};
}

(async () => {
  for (const options of [
    {remaining: 419999}, {remaining: 420000, wait: 1}, {remaining: 450000, nativeRemaining: 419999},
    {remaining: 425000, wait: 1, alter: ({slot}) => {slot.timers = {bottle: 1};}},
    {remaining: 425000, wait: 1, alter: ({data}) => {data.nodes = [];}},
    {remaining: 425000, wait: 1, alter: ({target}) => {target.itemId = 'other';}},
    {remaining: 425000, wait: 1, alter: ({data, target}) => {data.nodes.push({...target});}},
    {remaining: 425000, wait: 1, alter: ({actorPosition}) => {actorPosition[0]++;}},
  ]) {
    const f = fixture(options);
    await assert.rejects(f.execute(), error => error.constructor.name === 'Q23GatherDeferred');
    assert.equal(f.wire.length, 0);
    assert.equal(f.persisted.length, 0);
    assert.equal(f.window.__qx.requests, 0);
  }
  for (const remaining of [420000, 420001]) {
    const f = fixture({remaining});
    await f.execute();
    assert.deepEqual(f.wire.map(body => body.action), ['gather-start', 'gather-finish']);
    assert.equal(f.wire[0].nodeId, f.ids.node);
    assert.equal(f.wire[1].gatherId, 'native-id');
  }
  const afterDefence = fixture({remaining: 430000, threats: 1, defendMs: 500});
  await afterDefence.execute();
  assert.deepEqual(afterDefence.wire.map(body => body.action), ['gather-start', 'gather-finish']);
  const defenceUsesBudget = fixture({remaining: 420001, threats: 1, defendMs: 500});
  await assert.rejects(defenceUsesBudget.execute(), error => error.constructor.name === 'Q23GatherDeferred');
  assert.equal(defenceUsesBudget.wire.length, 0);
  const ownerChanged = fixture({wait: 1, alter: ({invalidate}) => invalidate()});
  await assert.rejects(ownerChanged.execute(), /owner lost/);
  assert.equal(ownerChanged.wire.length, 0);
  const activeTimer = fixture();
  activeTimer.slot.timers = {bottle: 1};
  await assert.rejects(activeTimer.execute(), error => error.constructor.name === 'Q23GatherDeferred');
  assert.equal(activeTimer.wire.length, 0);
  const noLease = fixture();
  await assert.rejects(noLease.fetchGather(noLease.ids.node), /quest_q23_gather_scope/);
  assert.equal(noLease.wire.length, 0);
  await noLease.fetchGather('ordinary-node');
  assert.equal(noLease.wire[0].nodeId, 'ordinary-node');
  for (const [action, alter] of [
    ['gather-start', ({data}) => {data.nodes = [];}],
    ['gather-start', ({target}) => {target.itemId = 'ordinary-item';}],
    ['pickup', ({data}) => {data.nodes = [];}],
  ]) {
    const queued = fixture({wait: 1, alter});
    await assert.rejects(queued.fetchGather(queued.ids.node, action), /quest_q23_gather_scope/);
    assert.equal(queued.wire.length, 0);
    assert.equal(queued.persisted.length, 0);
    assert.equal(queued.window.__qx.requests, 0);
  }
  const newlyIdentified = fixture({wait: 1, alter: ({target}) => {target.itemId = 'qxq_sealing_bottle';}});
  newlyIdentified.target.itemId = 'ordinary-item';
  await assert.rejects(newlyIdentified.fetchGather(newlyIdentified.ids.node), /quest_q23_gather_scope/);
  assert.equal(newlyIdentified.wire.length, 0);
  assert.equal(newlyIdentified.persisted.length, 0);
  const frozenOptions = {body: JSON.stringify({action: 'gather-start', nodeId: 'ordinary-node', operationId: 'original-key'})};
  let mutableUrl;
  const queuedOrdinary = fixture({wait: 1, alter: ({target}) => {
    frozenOptions.body = JSON.stringify({action: 'gather-start', nodeId: target.id, operationId: 'changed-key'});
    mutableUrl.pathname = '/api/miniapp/xianxia-dwelling/location';
  }});
  mutableUrl = new URL(queuedOrdinary.endpoint);
  await queuedOrdinary.window.fetch(mutableUrl, frozenOptions);
  assert.deepEqual(queuedOrdinary.wire, [{action: 'gather-start', nodeId: 'ordinary-node', operationId: 'original-key'}]);
  assert.deepEqual(queuedOrdinary.wireUrls, [queuedOrdinary.endpoint]);
  assert.equal(queuedOrdinary.persisted[0][0].operation_id, 'original-key');
  const statusOptions = {body: JSON.stringify({action: 'status'})};
  const queuedStatus = fixture({wait: 1, alter: ({target}) => {
    statusOptions.body = JSON.stringify({action: 'gather-start', nodeId: target.id, operationId: 'changed-key'});
  }});
  await queuedStatus.window.fetch(queuedStatus.endpoint, statusOptions);
  assert.deepEqual(queuedStatus.wire, [{action: 'status'}]);
  assert.equal(queuedStatus.persisted.length, 0);
  const queuedRequest = fixture();
  const body = JSON.stringify({action: 'gather-start', nodeId: queuedRequest.ids.node, operationId: 'request-key'});
  const request = new Request(queuedRequest.endpoint, {method: 'POST', body});
  request.clone = () => ({async text() {queuedRequest.target.itemId = 'ordinary-item'; return body;}});
  await assert.rejects(queuedRequest.window.fetch(request), /quest_q23_gather_scope/);
  assert.equal(queuedRequest.wire.length, 0);
  assert.equal(queuedRequest.persisted.length, 0);
  const ready = fixture({remaining: 1});
  ready.slot.status = 'ready';
  ready.slot.timers = {bottle: 1};
  await ready.execute('ready');
  assert.deepEqual(ready.wire.map(body => body.action), ['turn-in']);
  const failure = fixture({fail: true});
  await assert.rejects(failure.execute(), /response missing/);
  assert.equal(failure.wire.length, 1);
  assert.equal(failure.persisted[0].length, 1);
  assert.equal(failure.window.__qx.pending.size, 1);
  assert.equal(failure.window.__qx.error, 'quest_transport_unconfirmed');
  assert.equal(failure.window.__qx.stopReason, null);
  await assert.rejects(failure.fetchGather(failure.ids.node), /quest_transport_unconfirmed/);
  assert.equal(failure.wire.length, 1);
  const verifyCave = fixture();
  assert.equal(await verifyCave.finishDeferred(), '血煞封瓶暂缓');
  assert.equal(verifyCave.window.__qx.stopReason.reason, 'q23_timed_gather_deferred');
  const badCave = fixture({returnVerified: false});
  await assert.rejects(badCave.finishDeferred(), /返洞府未确认/);
  assert.equal(badCave.window.__qx.stopReason, null);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')
    process = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize('taint', ['', 'server_checked', 'final_status', 'stop_reason', 'pending',
                                  'report', 'run', 'browser_shutdown_error'])
def test_q23_defer_needs_confirmed_safe_finish(taint):
    result = {'phase': 'waiting', 'server_checked': True, 'pending': 0, 'pending_operations': [],
              'stored_pending': [], 'combat_active': False,
              'final_status': {'fresh': True}, 'stop_reason': {'reason': worker.Q23_DEFER_REASON, 'quest_id': 'qx_q23'},
              'report': {'home': {'destination': 'cave', 'verified': True}},
              'run': {'done': True, 'value': '血煞封瓶暂缓'}}
    if taint:
        result[taint] = 'TimeoutError' if taint == 'browser_shutdown_error' else None
    assert worker.q23_defer_verified(result) is not bool(taint)
