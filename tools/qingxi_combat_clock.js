/* Observe the dt already passed to the native field controller. */
var qingxiCombatClock = (() => {
  'use strict';
  const key = Symbol.for('qingxi.nativeCombatClock.v1');
  if (globalThis[key]?.version === 1) return globalThis[key];
  const records = new WeakMap();
  let latest = 0;

  function capture(runtime) {
    if (!runtime || typeof runtime.step !== 'function') return false;
    const known = records.get(runtime);
    if (known) return runtime.step === known.observer;
    const descriptor = Object.getOwnPropertyDescriptor(runtime, 'step');
    if (!descriptor || !('value' in descriptor) || (!descriptor.writable && !descriptor.configurable)) return false;
    const record = {original: runtime.step, origin: Math.max(latest, performance.now() / 1000),
      elapsed: 0, steps: 0, lastDt: null, valid: true, observer: null};
    record.observer = function (...args) {
      let paused;
      try { paused = runtime.paused; } catch (_) { record.valid = false; }
      let result;
      try { result = Reflect.apply(record.original, this, args); }
      catch (error) { record.valid = false; throw error; }
      // Native step returns before its simulation when paused/disabled. An
      // enabled active field controller advances enemy time by this exact dt.
      try {
        if (paused === false && runtime.active === true && runtime.needsModels === true) {
          const dt = args[0];
          if (!Number.isFinite(dt) || dt < 0) record.valid = false;
          else {
            record.elapsed += dt; record.lastDt = dt;
            record.steps = Math.min(Number.MAX_SAFE_INTEGER, record.steps + 1);
            latest = Math.max(latest, record.origin + record.elapsed);
          }
        } else if (typeof paused !== 'boolean' || typeof runtime.active !== 'boolean'
                   || runtime.active && typeof runtime.needsModels !== 'boolean') record.valid = false;
      } catch (_) { record.valid = false; }
      return result;
    };
    try { Object.defineProperty(runtime, 'step', {...descriptor, value: record.observer}); }
    catch (_) { return false; }
    records.set(runtime, record);
    latest = Math.max(latest, record.origin);
    return true;
  }

  function requireClock(runtime) {
    const record = runtime && records.get(runtime);
    if (runtime !== globalThis.window?.__fc || !record?.valid || runtime.step !== record.observer)
      throw Error('无法核对野外原生帧时钟');
    return record.origin + record.elapsed;
  }

  function snapshot() {
    const runtime = globalThis.window?.__fc, record = runtime && records.get(runtime);
    if (!record) return {version: 1, captured: false};
    return {version: 1, captured: true, valid: record.valid && runtime.step === record.observer,
      elapsedSeconds: record.elapsed, steps: record.steps, lastDt: record.lastDt};
  }
  const api = {version: 1, capture, requireClock, snapshot};
  globalThis[key] = api;
  return api;
})();
window.qingxiCombatClock = qingxiCombatClock;
