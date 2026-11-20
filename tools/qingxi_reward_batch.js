/* Run the pinned native assistant's seven dungeons and two first-tier trials.
 * This layer reads real status and controls sequencing; it never creates combat
 * reports, rewards, clocks or victory results. Unknown state stops the batch.
 */
(() => {
  'use strict';
  const dailyIds = ['moonpalace', 'bloodland', 'southgate', 'escort', 'mulanshrine', 'bqpuppet', 'jianzhong'];
  const dailyReaders = [
    ['moon','worldMoonRequest'], ['blood','worldBloodRequest'], ['siege','worldSiegeRequest'], ['escort','worldEscortRequest'],
    ['shrine',null,'mulan-shrine','mulanShrine'], ['puppet',null,'baiqiao-puppet','baiqiaoPuppet'],
    ['jianzhong',null,'jianzhong-trial','jianzhongTrial']
  ];
  const arenas = ['longwind', 'bamboo'];
  const state = {phase:'idle', before:null, final:null, daily:null, trials:null, error:'', running:false};
  const clone = value => JSON.parse(JSON.stringify(value));
  function requireValue(ok, reason) { if (!ok) throw Error(reason); }
  function ownership() {
    const q = window.__qx;
    requireValue(q?.player === __PLAYER__ && !q.error && !q.quiescing, 'reward_batch_identity_or_guard');
    requireValue(q.pending.size === 0 && !q.castSending, 'reward_batch_pending');
    const stored = JSON.parse(localStorage.getItem('qingxi-local-pending:' + __PLAYER__) || '[]');
    requireValue(Array.isArray(stored) && stored.length === 0, 'reward_batch_stored_pending');
  }
  async function inspect(expectedDay = null) {
    ownership();
    const q = window.__qx;
    await q.queue;
    q.statusCache.clear();
    await window.__autobot.checkDailyRewards();
    ownership();
    const daily = clone(window.__autobot.report().dailyRewards);
    requireValue(daily?.verified === true && daily.errors === 0 && daily.rows?.length === 7,
      'reward_batch_daily_unverified');
    const day = expectedDay || daily.day;
    requireValue(typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day) && daily.day === day,
      'reward_batch_day_changed');
    requireValue(!q.rewardDay || q.rewardDay === day, 'reward_batch_day_changed');
    q.rewardDay = day;
    requireValue(new Set(daily.rows.map(row => row.id)).size === 7 && dailyIds.every(id => daily.rows.some(row => row.id === id)),
      'reward_batch_daily_catalog');
    for (const row of daily.rows) {
      requireValue(row.day === day && typeof row.claimed === 'boolean' && typeof row.hasKey === 'boolean' &&
        ['claimed','pending','locked'].includes(row.status) &&
        row.status === (row.claimed ? 'claimed' : row.hasKey ? 'pending' : 'locked'), 'reward_batch_daily_status');
    }
    // The upstream reward overview omits activeRun. Re-read each native status
    // envelope so a claimed/locked row cannot hide an unresolved server run.
    const readers = window.__autobot.handoff?.().rewardRequests || {};
    for (const [index, definition] of dailyReaders.entries()) {
      const [key, global, resource, envelope] = definition;
      const request = readers[key] || window[global] || (resource && typeof window.qingxiInstanceRequest === 'function'
        ? (action, body) => window.qingxiInstanceRequest(resource, action, body) : null);
      requireValue(typeof request === 'function', 'reward_batch_daily_reader_missing');
      q.statusCache.clear();
      const response = await request('status', {});
      ownership();
      const payload = envelope ? response?.[envelope] : response, status = payload?.[key];
      requireValue(response?.ok !== false && !response?.error && payload?.ok !== false && !payload?.error && status && status.day === day &&
        typeof status.claimed === 'boolean' && typeof status.hasKey === 'boolean' &&
        Object.hasOwn(status, 'activeRun'), 'reward_batch_daily_status');
      requireValue(status.activeRun === null, 'reward_batch_daily_pending');
      const row = daily.rows.find(item => item.id === dailyIds[index]);
      requireValue(row.claimed === status.claimed && row.hasKey === status.hasKey &&
        !!row.exhausted === (index === 0 && status.failClaimed === true && !status.claimed && status.hasKey),
        'reward_batch_daily_changed');
      row.activeRun = null;
    }
    const request = window.__autobot.handoff?.().rewardRequests?.trial || window.worldTrialRequest;
    requireValue(typeof request === 'function', 'reward_batch_trial_reader_missing');
    const trials = {};
    for (const arena of arenas) {
      q.statusCache.clear();
      const response = await request('status', {arena});
      ownership();
      const status = response?.status;
      requireValue(response?.ok !== false && !response?.error && status && status.day === day && status.arena === arena &&
        status.limit === 3 && Number.isInteger(status.used) && Number.isInteger(status.remaining) &&
        status.used >= 0 && status.remaining >= 0 && status.used + status.remaining === 3 &&
        Object.hasOwn(status, 'activeRun'), 'reward_batch_trial_status');
      requireValue(status.activeRun === null, 'reward_batch_trial_pending');
      trials[arena] = clone(status);
    }
    const combat = window.__fc?.stats?.();
    requireValue(combat && (combat.active === false || combat.overworld === true), 'reward_batch_combat_pending');
    ownership();
    return {verified:true, player:__PLAYER__, day, checked_at:Date.now()/1000, daily:daily.rows, trials};
  }
  function finishedNative(mode) {
    ownership();
    const report = window.__autobot.report();
    requireValue(!report.error && report.batch?.mode === mode && report.batch.status === 'complete',
      'reward_batch_native_' + mode + '_unconfirmed');
    return clone(report.batch);
  }
  async function run() {
    requireValue(!state.running, 'reward_batch_already_running');
    requireValue(state.phase !== 'held', 'reward_batch_requires_review');
    Object.assign(state, {before:null, final:null, daily:null, trials:null, error:'',
      claimed:0, trial_victories:0, trial_used:0, remaining:null});
    state.running = true;
    state.phase = 'checking';
    try {
      state.before = await inspect();
      const day = state.before.day;
      if (state.before.daily.some(row => row.hasKey && !row.claimed && !row.exhausted)) {
        state.phase = 'daily';
        await window.__autobot.daily();
        state.daily = finishedNative('daily');
      }
      const afterDaily = await inspect(day);
      if (arenas.some(arena => afterDaily.trials[arena].remaining > 0)) {
        state.phase = 'trials';
        await window.__autobot.trials();
        state.trials = finishedNative('trials');
      }
      state.final = await inspect(day);
      const completed = state.trials?.completed || [], seen = new Set();
      for (const row of completed) {
        requireValue(arenas.includes(row.id) && row.day === day && row.tier === 1 && typeof row.runId === 'string' &&
          row.runId && !seen.has(row.runId) && row.result?.verified === true &&
          row.result.outcome === 'victory' && row.result.rewarded === true, 'reward_batch_trial_completion');
        seen.add(row.runId);
      }
      for (const arena of arenas) {
        const before = afterDaily.trials[arena], after = state.final.trials[arena];
        requireValue(after.used >= before.used && after.remaining <= before.remaining &&
          completed.filter(row => row.id === arena).length <= after.used - before.used, 'reward_batch_quota_not_consumed');
      }
      const consumed = arenas.reduce((sum, arena) => sum + state.final.trials[arena].used - afterDaily.trials[arena].used, 0);
      state.claimed = state.final.daily.filter(row => row.claimed).length;
      state.trial_victories = completed.length;
      state.trial_used = arenas.reduce((sum, arena) => sum + state.final.trials[arena].used, 0);
      state.remaining = state.final.daily.filter(row => row.hasKey && !row.claimed && !row.exhausted).length +
        arenas.reduce((sum, arena) => sum + state.final.trials[arena].remaining, 0);
      // Used trial entries are consumption evidence, not proof of victory or
      // rewards. Retain both counts; a partial batch never becomes 13 victories.
      state.phase = state.claimed === 7 && state.remaining === 0 &&
        completed.length === consumed && !(state.daily?.skipped?.length || state.trials?.skipped?.length) ? 'complete' : 'partial';
      return clone(state);
    } catch (error) {
      state.phase = 'held'; state.error = error.message;
      throw error;
    } finally { state.running = false; }
  }
  window.__qingxiRewardBatch = {state, inspect, run};
})();
