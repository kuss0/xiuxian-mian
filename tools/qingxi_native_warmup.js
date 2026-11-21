/* Adapt dwelling-3d.js v200 guardCompileAsync for the affected Three r160 poll. */
var qingxiWarmup = (() => {
  'use strict';
  const key = Symbol.for('qingxi.nativeWarmup.v1');
  const previous = globalThis[key];
  if (previous && previous.version === 1) return previous;
  // Function text from the pinned official three-0.160.1.min.js. Matching the
  // body avoids taking ownership from a newer or already guarded renderer.
  const nativeCompileAsync = 'function(t,e,n=null){const i=this.compile(t,e,n);return new Promise((e=>{function n(){i.forEach((function(t){mt.get(t).currentProgram.isReady()&&i.delete(t)})),0!==i.size?setTimeout(n,10):e(t)}null!==ht.get("KHR_parallel_shader_compile")?n():setTimeout(n,10)}))}';

  const records = new WeakMap();
  let owned = null;
  const counts = {
    installed: 0, duplicates: 0, unsupported: 0, superseded: 0,
    calls: 0, compileErrors: 0, completed: 0, missingPrograms: 0,
    pollErrors: 0, timedOut: 0, inFlight: 0, restored: 0, restoreErrors: 0, revocations: 0,
  };
  const increment = name => { counts[name] = Math.min(Number.MAX_SAFE_INTEGER, counts[name] + 1); };
  const snapshot = () => ({version: 1, activeRenderers: owned ? 1 : 0, ...counts});

  function restore() {
    if (!owned) return;
    const record = owned;
    owned = null;
    try {
      if (record.renderer.compileAsync !== record.guard) { increment('superseded'); return; }
      if (record.descriptor) Object.defineProperty(record.renderer, 'compileAsync', record.descriptor);
      else delete record.renderer.compileAsync;
      record.revoked = true;
      increment('restored');
    } catch (_) { increment('restoreErrors'); }
    // The original poll closure keeps its finite budget if work is in flight.
  }

  function capture(runtime) {
    try { return captureRenderer(runtime?.sceneHandles?.()?.renderer); }
    catch (_) { restore(); increment('unsupported'); return false; }
  }

  function captureRenderer(renderer) {
    if (owned && owned.renderer !== renderer) restore();
    if (!renderer || (typeof renderer !== 'object' && typeof renderer !== 'function')) {
      increment('unsupported'); return false;
    }
    const known = records.get(renderer);
    if (known) {
      if (renderer.compileAsync === known.guard) {
        owned = known;
        increment('duplicates');
        return true;
      }
      // Another owner, including the official motion-authority guard, wins.
      if (!known.revoked || renderer.compileAsync !== known.original) {
        restore();
        return false;
      }
    }
    const descriptor = Object.getOwnPropertyDescriptor(renderer, 'compileAsync');
    if (typeof renderer.compileAsync !== 'function' || typeof renderer.compile !== 'function' ||
        typeof renderer.properties?.get !== 'function' || typeof renderer.extensions?.has !== 'function' ||
        (descriptor && (!('value' in descriptor) || (!descriptor.writable && !descriptor.configurable)))) {
      increment('unsupported'); return false;
    }
    const method = Function.prototype.toString.call(renderer.compileAsync);
    if (method !== nativeCompileAsync) {
      increment('unsupported');
      return false;
    }
    const parallel = renderer.extensions.has('KHR_parallel_shader_compile');
    const record = {renderer, original: renderer.compileAsync, descriptor, revoked: false, guard: null};
    record.guard = function (scene, camera, targetScene = null) {
      increment('calls');
      let pending;
      // As in the official method, a synchronous compile error stays synchronous.
      try { pending = this.compile(scene, camera, targetScene); }
      catch (error) { increment('compileErrors'); throw error; }
      const started = performance.now();
      counts.inFlight++;
      return new Promise(resolve => {
        const check = () => {
          pending.forEach(material => {
            let program;
            try { program = renderer.properties.get(material).currentProgram; }
            catch (_) { increment('pollErrors'); }
            try {
              if (!program) { increment('missingPrograms'); pending.delete(material); }
              else if (program.isReady()) pending.delete(material);
            } catch (_) { increment('pollErrors'); pending.delete(material); }
          });
          const expired = pending.size !== 0 && performance.now() - started > 10000;
          if (pending.size === 0 || expired) {
            if (expired) increment('timedOut');
            increment('completed'); counts.inFlight--;
            resolve(scene);
          } else setTimeout(check, 10);
        };
        if (parallel) check(); else setTimeout(check, 10);
      });
    };
    Object.defineProperty(renderer, 'compileAsync', descriptor ? {...descriptor, value: record.guard} : {
      value: record.guard, writable: true, configurable: true, enumerable: true,
    });
    records.set(renderer, record);
    owned = record;
    increment('installed');
    return true;
  }

  function revoke() {
    increment('revocations');
    restore();
    return snapshot();
  }

  const api = Object.freeze({version: 1, capture, snapshot, revoke});
  Object.defineProperty(globalThis, key, {value: api, configurable: true});
  return api;
})();
