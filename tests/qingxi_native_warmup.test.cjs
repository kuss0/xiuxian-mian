'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../tools/qingxi_native_warmup.js'), 'utf8');

async function drain() { for (let i = 0; i < 8; i++) await Promise.resolve(); }

function clock() {
  let now = 0, serial = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(callback, delay) { timers.set(++serial, {at: now + delay, callback}); return serial; },
    clearTimeout(id) { timers.delete(id); },
    timers,
    async advance(to) {
      await drain();
      let calls = 0;
      while (timers.size) {
        const [id, next] = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (next.at > to) break;
        assert.ok(++calls < 5000, 'unexpected polling rate');
        now = next.at; timers.delete(id); next.callback(); await drain();
      }
      now = to; await drain();
    },
  };
}

function fixture(options = {}) {
  const time = clock();
  const context = vm.createContext({performance: {now: time.now}, setTimeout: time.setTimeout,
    clearTimeout: time.clearTimeout});
  vm.runInContext(source, context);
  const api = context.qingxiWarmup;
  const material = {}, state = {currentProgram: {isReady: () => false}};
  const calls = [];
  const renderer = {
    compile(...args) {
      calls.push({receiver: this, args});
      if (options.compileError) throw options.compileError;
      return new Set([material]);
    },
    properties: {get() { if (options.propertyError) throw new Error('disposed property'); return state; }},
    extensions: {has: () => options.parallel !== false},
  };
  context.mt = renderer.properties;
  context.ht = {get: () => options.parallel !== false ? {} : null};
  // Pinned Three 0.160.1 function body, also used by the original-versus-guard fixture.
  const original = vm.runInContext('(function(t,e,n=null){const i=this.compile(t,e,n);return new Promise((e=>{function n(){i.forEach((function(t){mt.get(t).currentProgram.isReady()&&i.delete(t)})),0!==i.size?setTimeout(n,10):e(t)}null!==ht.get("KHR_parallel_shader_compile")?n():setTimeout(n,10)}))})', context);
  renderer.compileAsync = original;
  const runtime = {sceneHandles: () => ({renderer})};
  return {time, context, api, material, state, calls, original, renderer, runtime};
}

function observed(promise) {
  const result = {phase: 'pending'};
  promise.then(value => Object.assign(result, {phase: 'resolved', value}),
    error => Object.assign(result, {phase: 'rejected', error}));
  return result;
}

for (const parallel of [true, false]) {
  test(`normal compile readiness preserves arguments/result with parallel=${parallel}`, async () => {
    const f = fixture({parallel}), scene = {}, camera = {}, target = {};
    assert.equal(f.api.capture(f.runtime), true);
    const result = observed(f.renderer.compileAsync(scene, camera, target));
    assert.equal(result.phase, 'pending');
    assert.equal(f.calls[0].receiver, f.renderer);
    assert.deepEqual(f.calls[0].args, [scene, camera, target]);
    f.state.currentProgram.isReady = () => true;
    await f.time.advance(10);
    assert.equal(result.phase, 'resolved'); assert.equal(result.value, scene);
    assert.equal(f.api.snapshot().completed, 1); assert.equal(f.api.snapshot().inFlight, 0);
  });
}

for (const failure of ['missing', 'properties', 'program']) {
  test(`a disappearing/disposed material settles without a timer throw: ${failure}`, async () => {
    const f = fixture();
    f.api.capture(f.runtime);
    const result = observed(f.renderer.compileAsync({}, {}));
    if (failure === 'missing') delete f.state.currentProgram;
    if (failure === 'properties') f.renderer.properties.get = () => { throw new Error('disposed'); };
    if (failure === 'program') f.state.currentProgram.isReady = () => { throw new Error('disposed'); };
    await f.time.advance(10);
    assert.equal(result.phase, 'resolved'); assert.equal(f.time.timers.size, 0);
    assert.equal(f.api.snapshot().inFlight, 0);
    assert.equal(f.api.snapshot().timedOut, 0);
  });
}

test('an indefinitely unready program uses the official first tick past ten seconds', async () => {
  const f = fixture(); f.api.capture(f.runtime);
  const result = observed(f.renderer.compileAsync({}, {}));
  await f.time.advance(10000); assert.equal(result.phase, 'pending');
  await f.time.advance(10010); assert.equal(result.phase, 'resolved');
  assert.equal(f.api.snapshot().timedOut, 1); assert.equal(f.time.timers.size, 0);
});

test('a synchronous shader compile error is preserved without leaving polling behind', () => {
  const error = new Error('native shader compile failed'), f = fixture({compileError: error});
  f.api.capture(f.runtime);
  assert.throws(() => f.renderer.compileAsync({}, {}), actual => actual === error);
  assert.equal(f.api.snapshot().compileErrors, 1);
  assert.equal(f.api.snapshot().inFlight, 0); assert.equal(f.time.timers.size, 0);
});

test('overlapping warmups retain separate material sets and settle independently', async () => {
  const f = fixture(), materials = [{}, {}], states = new Map();
  materials.forEach(material => states.set(material, {currentProgram: {isReady: () => false}}));
  let index = 0;
  f.renderer.compile = () => new Set([materials[index++]]);
  f.renderer.properties.get = material => states.get(material);
  f.api.capture(f.runtime);
  const first = observed(f.renderer.compileAsync({}, {})), second = observed(f.renderer.compileAsync({}, {}));
  states.get(materials[0]).currentProgram.isReady = () => true;
  await f.time.advance(10);
  assert.equal(first.phase, 'resolved'); assert.equal(second.phase, 'pending');
  assert.equal(f.api.snapshot().inFlight, 1);
  states.get(materials[1]).currentProgram.isReady = () => true;
  await f.time.advance(20);
  assert.equal(second.phase, 'resolved'); assert.equal(f.api.snapshot().inFlight, 0);
});

test('reinject and repeated capture share one owner and never wrap the guard again', () => {
  const f = fixture(); f.api.capture(f.runtime); const installed = f.renderer.compileAsync;
  vm.runInContext(source, f.context);
  assert.equal(f.context.qingxiWarmup, f.api);
  assert.equal(f.context.qingxiWarmup.capture(f.runtime), true);
  assert.equal(f.renderer.compileAsync, installed);
  assert.equal(f.api.snapshot().installed, 1); assert.equal(f.api.snapshot().duplicates, 1);
});

test('a new renderer releases the previous instance and receives its own guard', () => {
  const f = fixture(), second = fixture();
  const nativePrototype = Object.getPrototypeOf(f.renderer);
  f.api.capture(f.runtime);
  assert.equal(second.renderer.compileAsync, second.original);
  f.api.capture(second.runtime);
  assert.equal(f.renderer.compileAsync, f.original);
  assert.notEqual(second.renderer.compileAsync, f.renderer.compileAsync);
  assert.equal(Object.getPrototypeOf(f.renderer), nativePrototype);
  assert.equal(f.api.snapshot().activeRenderers, 1);
  f.api.revoke();
  assert.equal(f.renderer.compileAsync, f.original); assert.equal(second.renderer.compileAsync, second.original);
});

test('official replacement after capture is preserved on recapture, reinject and revoke', () => {
  const f = fixture(); f.api.capture(f.runtime);
  const officialGuard = function () { return Promise.resolve('official'); };
  f.renderer.compileAsync = officialGuard;
  assert.equal(f.api.capture(f.runtime), false);
  vm.runInContext(source, f.context);
  assert.equal(f.context.qingxiWarmup.capture(f.runtime), false);
  f.api.revoke(); assert.equal(f.renderer.compileAsync, officialGuard);
  assert.equal(f.api.snapshot().superseded, 1);
});

test('revoke restores future calls while a pending warmup remains bounded and can finish', async () => {
  const f = fixture(); f.api.capture(f.runtime);
  const installed = f.renderer.compileAsync, result = observed(f.renderer.compileAsync({}, {}));
  f.api.revoke();
  assert.equal(f.renderer.compileAsync, f.original);
  assert.equal(result.phase, 'pending');
  delete f.state.currentProgram;
  await f.time.advance(10);
  assert.equal(result.phase, 'resolved'); assert.equal(f.time.timers.size, 0);
  assert.equal(f.api.capture(f.runtime), true);
  assert.notEqual(f.renderer.compileAsync, installed);
  assert.equal(f.api.snapshot().installed, 2);
});

test('revoke reports a renderer that became immutable without interrupting shutdown', () => {
  const f = fixture(); f.api.capture(f.runtime);
  const installed = f.renderer.compileAsync;
  Object.freeze(f.renderer);
  assert.doesNotThrow(() => f.api.revoke());
  assert.equal(f.renderer.compileAsync, installed);
  assert.equal(f.api.snapshot().restoreErrors, 1);
  assert.equal(f.api.snapshot().activeRenderers, 0);
});

test('first capture leaves an existing fixed or unknown compile method alone', () => {
  const f = fixture();
  const officialGuard = async scene => scene;
  f.renderer.compileAsync = officialGuard;
  assert.equal(f.api.capture(f.runtime), false);
  assert.equal(f.renderer.compileAsync, officialGuard);
  assert.equal(f.api.snapshot().installed, 0);
  assert.equal(f.api.snapshot().unsupported, 1);
});

test('even a changed method containing both old poll signatures is not taken over', () => {
  const f = fixture();
  f.renderer.compileAsync = vm.runInContext('(' + f.original.toString().replace(
    'const i=this.compile', 'throw Error("changed engine");const i=this.compile') + ')', f.context);
  const changed = f.renderer.compileAsync;
  assert.equal(f.api.capture(f.runtime), false);
  assert.equal(f.renderer.compileAsync, changed);
  assert.equal(f.api.snapshot().installed, 0);
});

test('revoke leaves an unready in-flight task with its finite original budget', async () => {
  const f = fixture(); f.api.capture(f.runtime);
  const result = observed(f.renderer.compileAsync({}, {}));
  f.api.revoke(); await f.time.advance(10010);
  assert.equal(result.phase, 'resolved'); assert.equal(f.time.timers.size, 0);
  assert.equal(f.api.snapshot().inFlight, 0); assert.equal(f.api.snapshot().activeRenderers, 0);
});

test('revoke restores inherited methods without leaving an own-property override', () => {
  const f = fixture();
  const prototype = {compileAsync: f.original};
  delete f.renderer.compileAsync; Object.setPrototypeOf(f.renderer, prototype);
  f.api.capture(f.runtime);
  assert.equal(Object.hasOwn(f.renderer, 'compileAsync'), true);
  f.api.revoke();
  assert.equal(Object.hasOwn(f.renderer, 'compileAsync'), false);
  assert.equal(f.renderer.compileAsync, f.original); assert.equal(prototype.compileAsync, f.original);
});

test('unsupported runtime shapes fail closed and snapshot contains only fixed finite counters', () => {
  const f = fixture();
  for (const runtime of [null, {}, {sceneHandles() { throw new Error('private details'); }},
    {sceneHandles: () => ({renderer: {}})}]) assert.equal(f.api.capture(runtime), false);
  const expected = ['version', 'activeRenderers', 'installed', 'duplicates', 'unsupported', 'superseded',
    'calls', 'compileErrors', 'completed', 'missingPrograms', 'pollErrors', 'timedOut',
    'inFlight', 'restored', 'restoreErrors', 'revocations'];
  const value = f.api.snapshot();
  assert.deepEqual(Object.keys(value), expected);
  assert.equal(value.unsupported, 4);
  assert.equal(Object.values(value).every(item => Number.isFinite(item) && item >= 0), true);
  assert.equal(JSON.stringify(value).includes('private details'), false);
});

test('capture on synchronous factory return precedes actual async model warmup', async () => {
  const f = fixture();
  const status = {modelId: null, ready: false, loading: false, rendered: false};
  function nativeFactory() {
    return {...f.runtime, sync() {
      status.loading = true;
      return (async () => {
        await Promise.resolve(); // Native readModel awaits fetch/cache/parse before installModel.
        status.modelId = 'ngw'; status.ready = true;
        status.ready = false;
        await f.renderer.compileAsync({}, {}); // Native prepareEffects/prepareBlinkModel.
        status.rendered = true; status.ready = true; status.loading = false;
      })();
    }};
  }
  function capturedFactory() { const runtime = nativeFactory(); f.api.capture(runtime); return runtime; }
  const runtime = capturedFactory(), operation = observed(runtime.sync());
  await drain();
  assert.equal(status.modelId, 'ngw'); assert.equal(status.ready, false); assert.equal(status.loading, true);
  delete f.state.currentProgram; await f.time.advance(10);
  assert.equal(operation.phase, 'resolved'); assert.equal(status.ready, true);
  assert.equal(status.loading, false); assert.equal(status.rendered, true);
});
