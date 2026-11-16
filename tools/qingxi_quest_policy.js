/* Local policy around the unmodified, pinned upstream dwelling assistant. */
(() => {
  'use strict';
  const config = __CONFIG__;
  const original = window.fetch.bind(window);
  const q = window.__qx = {
    player: 0, error: '', stopReason: null, requests: 0, casts: 0,
    pending: new Map(), rewards: new Map(), receipts: [], responses: new Map(), status: new Map(), statusCache: new Map(),
    queue: Promise.resolve(), last: -Infinity, serial: 0, starts: new Set(), quiescing: false,
  };
  const instances = {'moon-palace': 'moonPalace', 'blood-land': 'bloodLand',
    'south-gate': 'southGate', 'refugee-escort': 'escort', 'mulan-shrine': 'mulanShrine',
    'baiqiao-puppet': 'baiqiaoPuppet', 'jianzhong-trial': 'jianzhongTrial'};
  const journalKey = 'qingxi-local-pending:' + config.player;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  function stop(reason, questId) {
    q.stopReason = {reason, quest_id: questId || ''};
    throw Error(reason);
  }
  function persist() {
    // Unknown writes also hold the host-level run; never restart them blindly.
    try { localStorage.setItem(journalKey, JSON.stringify([...q.pending.values()])); }
    catch { q.error = 'quest_journal_failed'; throw Error(q.error); }
  }
  const nativeCleanup = row => row?.resource === 'location' ||
    (row?.resource === 'flight' && ['tick', 'stop'].includes(row.action));
  const stored = JSON.parse(localStorage.getItem(journalKey) || '[]');
  const unresolved = Array.isArray(stored) ? stored.filter(row => !nativeCleanup(row)) : null;
  if (unresolved === null || unresolved.length) q.error = 'quest_browser_pending_requires_review';
  else if (stored.length) localStorage.setItem(journalKey, '[]');
  function check(resource, body) {
    const readonly = body.action === 'status';
    if (resource === 'trial' && !readonly) stop('quest_runner_trial_forbidden');
    if (body.playerId != null && String(body.playerId) !== String(config.player)) {
      q.error = 'quest_player_mismatch';
      throw Error(q.error);
    }
    if (readonly || resource === 'start') return;
    if (q.player !== config.player || q.error || q.quiescing) throw Error(q.error || 'quest_runner_guard');
    if (config.readOnly) stop('quest_runner_read_only');
    if (resource === 'fishing') stop('fishing_plan_required');
    if (resource === 'quests' && (typeof body.operationId !== 'string' || !body.operationId)) {
      stop('quest_operation_id_missing');
    }
    if (resource === 'quests' && body.action === 'turn-in') {
      const row = window.__autobot?.quests?.(true, '全部任务')?.find(row => row.id === body.questId);
      if (!row) stop('quest_catalog_unverified', body.questId);
      if (row.choices?.length && (!config.choices?.[body.questId] ||
          config.choices[body.questId] !== body.choice ||
          !row.choices.some(choice => choice.id === body.choice))) {
        stop('reward_choice_required', body.questId);
      }
    }
  }
  function receipt(resource, body, response, data, key) {
    const code = typeof data?.error === 'string' && /^[a-zA-Z0-9_:-]{1,100}$/.test(data.error) ? data.error : '';
    const payload = instances[resource] ? data?.[instances[resource]] : data;
    const row = {resource, action: body.action || 'events', operation_id: key,
      quest_id: body.questId, enemy_id: body.enemyId, ok: response.ok && data?.ok === true,
      http_status: response.status, error: code || (data ? '' : 'non_json'),
      grants: Array.isArray(payload?.grants) ? payload.grants.map(g => ({name: g.name, quantity: g.quantity})) : [],
      cultivation: Number.isInteger(payload?.cultivation) && payload.cultivation > 0 ? payload.cultivation : 0,
      pickedItems: Array.isArray(data?.pickedItems) ? data.pickedItems.map(g => ({itemId: g.itemId, quantity: g.quantity})) : [],
      shape: data && typeof data === 'object' ? Object.keys(data).slice(0, 25) : [],
      at: Date.now() / 1000};
    q.receipts.push(row);
    if (row.ok && (row.grants.length || row.cultivation) && key) {
      const runId = payload?.run?.id || body.runId || payload?.runId;
      row.reward_id = resource === 'quests' && body.action === 'turn-in' ? 'quests:' + body.questId :
        runId ? resource + ':' + runId + ':' + (payload?.run?.cleared ?? body.wave ?? body.action) : key;
      q.rewards.set(row.reward_id, row);
    }
    if (q.receipts.length > 180) q.receipts.splice(0, 60);
    if (!row.ok) {
      const error = 'quest_http_' + response.status + '_' + (code || (data ? 'unconfirmed' : 'non_json'));
      if (key || resource === 'field-cast') q.error = error;
      else q.statusError = error;
    }
    return row.ok;
  }
  window.fetch = async function(input, options) {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    const prefix = '/api/miniapp/xianxia-dwelling/';
    if (url.origin !== location.origin || !url.pathname.startsWith(prefix)) return original(input, options);
    const resource = url.pathname.slice(prefix.length);
    let body;
    try {
      const raw = options?.body ?? (input instanceof Request ? await input.clone().text() : '{}');
      body = JSON.parse(raw);
    } catch { throw Error('quest_request_unreadable'); }
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw Error('quest_request_unreadable');
    // Native flight heartbeats have a 3s deadline, three failures force landing,
    // and stop falls back to session expiry. Preserve that cleanup even when a
    // quest is held; takeoff, quest writes and combat remain guarded.
    if (nativeCleanup({resource, action: body.action}) ||
        (resource === 'players/social' && body.action === 'view')) {
      return original(input, options);
    }
    check(resource, body);
    const control = resource === 'quests';
    const cast = resource === 'field-cast';
    const tracked = control || cast || (body.action && resource !== 'start');
    const mutating = tracked && body.action !== 'status';
    const key = mutating && (body.operationId || resource + ':' + (++q.serial));
    const statusKey = body.action === 'status' ? JSON.stringify([resource, body.arena || '', body.instance || '']) : null;
    if (key && q.responses.has(key)) return q.responses.get(key).clone();
    if (key && q.pending.has(key)) throw Error('quest_operation_pending');
    if (statusKey && q.status.has(statusKey)) return (await q.status.get(statusKey)).clone();
    const cached = statusKey && q.statusCache.get(statusKey);
    if (cached && performance.now() - cached.at < 5000) return cached.response.clone();
    if (instances[resource] && body.action === 'start') {
      // Query through the native request envelope before spending an entry.
      const statusBody = {action: 'status'};
      for (const key of ['token', 'initData', 'playerId']) if (body[key] != null) statusBody[key] = body[key];
      const statusResponse = await window.fetch(url.href, {...options, body: JSON.stringify(statusBody)});
      const data = await statusResponse.json().catch(() => null), status = data?.[instances[resource]];
      if (!statusResponse.ok || data?.ok !== true || typeof status?.claimed !== 'boolean' || typeof status?.hasKey !== 'boolean') {
        stop('daily_reward_status_unverified');
      }
      if (status.claimed) stop('daily_reward_full');
      if (!status.hasKey) stop('daily_instance_locked');
      if (status.activeRun) stop('daily_instance_pending');
    }
    const dispatch = async () => {
      check(resource, body);
      if (key && q.responses.has(key)) return q.responses.get(key).clone();
      if (key && q.pending.has(key)) throw Error('quest_operation_pending');
      if (instances[resource] && body.action === 'start' && q.starts.has(resource)) stop('instance_retry_requires_review');
      if (control) {
        if (q.requests >= 240) stop('quest_request_budget');
        const wait = 5000 - (performance.now() - q.last);
        if (wait > 0) await sleep(wait);
        check(resource, body);
        q.last = performance.now();
        q.requests++;
      }
      if (cast && q.casts >= 1800) stop('quest_cast_budget');
      if (options?.signal?.aborted) stop('quest_request_expired_before_send');
      if (cast) q.casts++;
      if (instances[resource] && body.action === 'start') q.starts.add(resource);
      if (key) {
        q.statusCache.clear();
        q.pending.set(key, {resource, action: body.action || 'events', operation_id: key,
          quest_id: body.questId, enemy_id: body.enemyId, at: Date.now() / 1000});
        persist();
      }
      let response, data;
      try {
        response = await original(input, options);
        data = await response.clone().json().catch(() => null);
      } catch (error) {
        if (key || cast) q.error = 'quest_transport_unconfirmed';
        else q.statusError = 'quest_status_unavailable';
        q.receipts.push({resource, action: body.action || 'events', operation_id: key, ok: false,
          error: error?.name === 'AbortError' ? 'request_aborted' : 'transport_error', at: Date.now() / 1000});
        throw error;
      }
      if (resource === 'start' && data?.ok === true && data.account?.playerId) {
        q.player = Number(data.account.playerId);
        if (q.player !== config.player) q.error = 'quest_player_mismatch';
      }
      if (control || cast || key || instances[resource]) {
        if (receipt(resource, body, response, data, key)) {
          if (statusKey) {
            q.statusError = '';
            q.statusCache.set(statusKey, {at: performance.now(), response: response.clone()});
          }
          if (key) {
            q.pending.delete(key);
            persist();
            q.responses.set(key, response.clone());
            if (q.responses.size > 120) q.responses.delete(q.responses.keys().next().value);
          }
        }
      }
      return response;
    };
    // Native cast proof is already batched with an 8s timeout. Do not delay it
    // behind task queries; keep its native pacing and separate bounded budget.
    const task = control ? q.queue.then(dispatch) : dispatch();
    if (control) q.queue = task.catch(() => {});
    if (!statusKey) return task;
    q.status.set(statusKey, task);
    try { return (await task).clone(); }
    finally { q.status.delete(statusKey); }
  };
})();
