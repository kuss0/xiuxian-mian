/* Keep the existing strategy's projectile estimates on the native fight clock. */
(() => {
  const bot = window.__lw?.bot;
  if (typeof bot?.trackShots !== 'function') throw Error('trial_projectile_timing_anchor_missing');
  const trackShots = bot.trackShots;
  let previous = null;

  bot.trackShots = function (swinging, position, wallTime) {
    // The native render loop bounds long frames. The shot still moves in the
    // scene when the old wall-clock estimate says it has already landed.
    const state = this.sNow;
    const wave = state?.wave;
    const usesFightTime = Number.isFinite(wave?.time) && wave.time >= 0;
    const time = usesFightTime ? wave.time * 1000 : wallTime;
    const scope = [usesFightTime, state?.arena, state?.trial?.run?.id,
      wave?.index, state?.metrics?.retries].join('/');
    if (!previous || scope !== previous.scope || time < previous.time) {
      this.shots = [];
      this.shotMark = {};
    }
    previous = {scope, time};
    return trackShots.call(this, swinging, position, time);
  };
})();
