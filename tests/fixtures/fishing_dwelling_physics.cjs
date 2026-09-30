// Independent replay of the official stepFight formulas, not a Python port.
// Source: dwelling-fishing-controller.js?v=fishing-v14-world-moon-cue, 2026-09-30.
// SHA256: 07d36c165f9d62bda1c7f38a72cada16f63f4fca9fb02ecc345e67304dc95237
const fs = require('node:fs');

function replay(challenge, proof) {
  const saved = challenge.checkpoint?.details || {};
  const low = challenge.targetLow ?? 41, high = challenge.targetHigh ?? 68;
  let seedOffset = 0;
  for (const ch of String(challenge.fishSeed || 'seed')) seedOffset += ch.charCodeAt(0);
  const g = {
    elapsedMs: challenge.checkpoint?.durationMs || 0,
    progress: saved.progress || 0, tension: saved.tension ?? ((low + high) / 2 - 8),
    holding: saved.holding === true, targetLow: low, targetHigh: high,
    fishPower: challenge.fishPower ?? 1.7, seedOffset: seedOffset / 19,
    behaviorVersion: challenge.behaviorVersion ?? 1, behavior: challenge.behavior || 'steady',
    struggles: challenge.struggles || [], dangerMs: saved.danger_ms || 0,
    slackMs: saved.slack_ms || 0, samples: saved.samples || 0, stableSamples: saved.stable_samples || 0,
  };
  const events = proof.events.filter(e => e.t > g.elapsedMs);
  let index = 0;
  while (g.elapsedMs < proof.durationMs) {
    g.elapsedMs += 20;
    while (index < events.length && events[index].t <= g.elapsedMs) g.holding = events[index++].holding;
    const t = g.elapsedMs, dt = .02;
    const pulse = Math.sin(t * .0027 * g.fishPower + g.seedOffset);
    const surge = Math.max(0, Math.sin(t * .0041 + g.seedOffset * 1.7));
    let pull = g.fishPower * (.72 + pulse * .24 + surge * .42);
    if (g.behaviorVersion >= 2) {
      for (const struggle of g.struggles) {
        const elapsed = t - struggle.startMs;
        if (elapsed < 0 || elapsed >= struggle.durationMs) continue;
        const portion = elapsed / struggle.durationMs, wave = Math.sin(Math.PI * portion), strength = struggle.strength;
        if (g.behavior === 'steady') pull += g.fishPower * strength * .18 * wave;
        else if (g.behavior === 'leap') pull *= 1 + strength * .48 * wave;
        else if (g.behavior === 'surge') pull += g.fishPower * strength * .72 * (.55 + .45 * Math.sin(portion * Math.PI * 2));
        break;
      }
    }
    g.tension += g.holding ? (24 + pull * 3.1) * dt : (pull * 4.8 - 24) * dt;
    g.tension += Math.sin(t * .012 + g.seedOffset) * .24;
    g.tension = Math.max(0, Math.min(100, g.tension));
    if (g.tension >= g.targetLow && g.tension <= g.targetHigh) {
      g.stableSamples++;
      g.progress += (8.2 + g.fishPower * .7 + (g.holding ? 2.2 : .5)) * dt;
    } else if (g.tension > g.targetHigh) {
      g.dangerMs += 20; g.progress -= (1.5 + g.fishPower * .25) * dt;
    } else {
      g.slackMs += 20; g.progress -= .9 * dt;
    }
    if (g.holding && g.tension < g.targetLow) g.progress += 1.1 * dt;
    g.progress = Math.max(0, Math.min(100, g.progress));
    g.samples++;
  }
  return {
    progress: g.progress, tension: g.tension, holding: g.holding,
    danger_ms: g.dangerMs, slack_ms: g.slackMs, samples: g.samples, stable_samples: g.stableSamples,
  };
}

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(({challenge, proof}) => replay(challenge, proof))));
