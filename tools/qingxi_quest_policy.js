/* Local policy around the unmodified, pinned upstream dwelling assistant. */
(() => {
  'use strict';
  const config = __CONFIG__;
  const original = window.fetch.bind(window);
  const q = window.__qx = {
    player: 0, error: '', stopReason: null, requests: 0, casts: 0, questStatusRevision: 0,
    pending: new Map(), rewards: new Map(), receipts: [], responses: new Map(), status: new Map(), statusCache: new Map(),
    queue: Promise.resolve(), last: -Infinity, serial: 0, starts: new Set(), quiescing: false,
    castEvents: new Map(), castSending: false, castError: '',
  };
  const instances = {'moon-palace': 'moonPalace', 'blood-land': 'bloodLand',
    'south-gate': 'southGate', 'refugee-escort': 'escort', 'mulan-shrine': 'mulanShrine',
    'baiqiao-puppet': 'baiqiaoPuppet', 'jianzhong-trial': 'jianzhongTrial'};
  const journalKey = 'qingxi-local-pending:' + config.player;
  const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
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
  async function readResponse(response) {
    let data, parseError = '';
    try { data = await response.json(); }
    catch (error) { parseError = error?.name || 'Error'; }
    // The native deadline covers fetch and body consumption. Once verified,
    // return a buffered body so that deadline cannot abort a second read.
    const buffered = parseError ? null : new Response(JSON.stringify(data), {
      status: response.status, statusText: response.statusText, headers: response.headers});
    return {data, parseError, buffered};
  }
  const nativeCleanup = row => row?.resource === 'location' ||
    (row?.resource === 'flight' && ['tick', 'stop'].includes(row.action));
  const stored = JSON.parse(localStorage.getItem(journalKey) || '[]');
  const reviewed = config.reviewedCastPending || [];
  const confirmedCast = row => row?.resource === 'field-cast' && row.action === 'events' &&
    typeof row.operation_id === 'string' && Number.isFinite(row.at) &&
    (config.confirmedCastReceipts || []).some(receipt =>
      receipt.operation_id === row.operation_id && Number.isFinite(receipt.at) && receipt.at >= row.at);
  const reviewedRelease = row => row?.resource === 'quests' && row.action === 'field-release' &&
    typeof row.operation_id === 'string' && Number.isFinite(row.at) &&
    (config.reviewedReleasePending || []).some(old => canonical(old) === canonical(row));
  const reviewedPreparation = row => ((row?.resource === 'quests' && row.action === 'field-engage') ||
    (row?.resource === 'field-cast' && row.action === 'events')) &&
    (config.reviewedPreparationPending || []).some(old => canonical(old) === canonical(row));
  const unresolved = Array.isArray(stored) ? stored.filter(row => !nativeCleanup(row) &&
    !confirmedCast(row) && !reviewedRelease(row) && !reviewedPreparation(row) &&
    !(row?.resource === 'field-cast' && row.action === 'events' &&
      reviewed.some(old => canonical(old) === canonical(row)))) : null;
  if (unresolved === null || unresolved.length) q.error = 'quest_browser_pending_requires_review';
  else if (stored.length) localStorage.setItem(journalKey, '[]');
  function check(resource, body) {
    // The official profile route also saves skillSlots without an action.
    // Only its known read envelope is safe during a read-only inspection.
    const profileRead = resource === 'battle/doufa-profile' && body.action == null &&
      Object.keys(body).every(key => ['token', 'initData', 'playerId', 'includeInventory'].includes(key));
    const readonly = resource === 'battle/doufa-profile' ? profileRead :
      body.action === 'status' || (body.action == null &&
        ['overview', 'details', 'section', 'town/review'].includes(resource));
    if (resource === 'trial' && !readonly) stop('quest_runner_trial_forbidden');
    if (body.playerId != null && String(body.playerId) !== String(config.player)) {
      q.error = 'quest_player_mismatch';
      throw Error(q.error);
    }
    if (readonly || resource === 'start') return;
    if (q.player !== config.player || q.error || q.quiescing) throw Error(q.error || 'quest_runner_guard');
    if (config.readOnly) stop('quest_runner_read_only');
    if (resource !== 'field-cast' && q.castError) {
      throw Error('quest_cast_pending');
    }
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
  function receipt(resource, body, response, data, key, parseError = '') {
    const code = typeof data?.error === 'string' && /^[a-zA-Z0-9_:-]{1,100}$/.test(data.error) ? data.error : '';
    const payload = instances[resource] ? data?.[instances[resource]] : data;
    const castAck = resource !== 'field-cast' || data?.off === true ||
      (Array.isArray(data?.acked) && body.events.every(event => data.acked.includes(event.k + ':' + event.s)));
    const row = {resource, action: body.action || 'events', operation_id: key,
      quest_id: body.questId, enemy_id: body.enemyId, ok: response.ok && data?.ok === true && castAck,
      http_status: response.status, error: code || (data ? (castAck ? '' : 'field_cast_unacknowledged') : 'non_json'),
      parse_error: parseError, content_type: (response.headers.get('content-type') || '').slice(0, 100),
      attempt: q.pending.get(key)?.attempts,
      grants: Array.isArray(payload?.grants) ? payload.grants.map(g => ({name: g.name, quantity: g.quantity})) : [],
      cultivation: Number.isInteger(payload?.cultivation) && payload.cultivation > 0 ? payload.cultivation : 0,
      pickedItems: Array.isArray(data?.pickedItems) ? data.pickedItems.map(g => ({itemId: g.itemId, quantity: g.quantity})) : [],
      shape: data && typeof data === 'object' ? Object.keys(data).slice(0, 25) : [],
      at: Date.now() / 1000};
    q.receipts.push(row);
    // The native client releases leashed enemies while leaving the field. A
    // distance rejection is a confirmed no-op, not an unknown reward/write.
    const rejectedRelease = resource === 'quests' && body.action === 'field-release' &&
      response.status === 409 && data?.ok === false && code === 'quest_too_far';
    if (rejectedRelease) row.rejected_noop = true;
    if (row.ok && resource === 'quests' && body.action === 'status') q.questStatusRevision++;
    if (row.ok && (row.grants.length || row.cultivation) && key) {
      const runId = payload?.run?.id || body.runId || payload?.runId;
      row.reward_id = resource === 'quests' && body.action === 'turn-in' ? 'quests:' + body.questId :
        runId ? resource + ':' + runId + ':' + (payload?.run?.cleared ?? body.wave ?? body.action) : key;
      q.rewards.set(row.reward_id, row);
    }
    if (q.receipts.length > 180) q.receipts.splice(0, 60);
    if (!row.ok && !rejectedRelease) {
      if (resource === 'field-cast') return false;
      const error = 'quest_http_' + response.status + '_' + (code || (data ? 'unconfirmed' : 'non_json'));
      if (key) q.error = error;
      else q.statusError = error;
    }
    return row.ok || rejectedRelease;
  }
  async function castRequest(input, options, body) {
    // The native ordered transport retries up to three times. It may append
    // newly queued events to the unresolved prefix, but cannot change that prefix.
    const events = body.events;
    if (!Array.isArray(events) || !events.length || events.length > 12 || events.some(event =>
        !event || !['use', 'begin', 'done', 'cancel'].includes(event.k) ||
        !Number.isSafeInteger(event.s) || event.s <= 0 ||
        Object.keys(event).some(key => !['k', 's', 'key', 'pos', 'free', 'track'].includes(key))) ||
        canonical(events).length > 64000) stop('quest_cast_events_invalid');
    const ids = events.map(event => event.k + ':' + event.s), signature = canonical(events);
    if (new Set(ids).size !== ids.length || events.some((event, i) =>
        q.castEvents.has(ids[i]) && q.castEvents.get(ids[i]) !== canonical(event))) {
      q.error = 'quest_cast_events_changed'; throw Error(q.error);
    }
    if (q.castSending) throw Error('quest_cast_inflight');
    const pending = [...q.pending.values()].find(row => row.resource === 'field-cast');
    if (pending && (!Array.isArray(pending.events) || pending.events.length > events.length ||
        pending.events.some((event, i) => canonical(event) !== canonical(events[i])))) {
      q.error = 'quest_cast_retry_changed'; throw Error(q.error);
    }
    const cacheKey = 'field-cast:' + signature;
    if (!pending && q.responses.has(cacheKey)) return q.responses.get(cacheKey).clone();
    if (pending?.attempts >= 3 || q.casts >= 1800) stop('quest_cast_budget');
    if (options?.signal?.aborted) throw Error('quest_request_expired_before_send');
    const key = pending?.operation_id || 'field-cast:' + ids[0];
    const row = {resource: 'field-cast', action: 'events', operation_id: key,
      events, attempts: (pending?.attempts || 0) + 1, at: Date.now() / 1000};
    q.pending.set(key, row);
    persist();
    events.forEach((event, i) => q.castEvents.set(ids[i], canonical(event)));
    q.statusCache.clear();
    q.casts++;
    q.castSending = true;
    let recorded = false;
    try {
      const response = await original(input, options);
      const {data, parseError, buffered} = await readResponse(response);
      const acknowledged = receipt('field-cast', body, response, data, key, parseError);
      recorded = true;
      if (acknowledged) {
        q.pending.delete(key);
        persist();
        q.castError = '';
        q.responses.set(cacheKey, buffered.clone());
        if (q.responses.size > 120) q.responses.delete(q.responses.keys().next().value);
      } else {
        q.castError = parseError ? 'quest_cast_response_unreadable' : 'quest_cast_unacknowledged';
        const fatal = response.status >= 400 && response.status < 500 && response.status !== 429 ||
          (data && data.ok !== true && !data.retryable && response.status < 400);
        if (fatal || row.attempts >= 3) q.error = q.castError;
        // Surface a body-read abort as a transport failure. Returning HTTP 200
        // here would make the native postJson classify it as non-retryable.
        if (parseError) throw Object.assign(Error(q.castError), {name: parseError});
      }
      return buffered;
    } catch (error) {
      if (q.pending.has(key)) {
        q.castError ||= 'quest_cast_transport_unconfirmed';
        if (row.attempts >= 3) q.error ||= q.castError;
        if (!recorded) {
          q.receipts.push({resource: 'field-cast', action: 'events', operation_id: key, ok: false,
            attempt: row.attempts, error: error?.name === 'AbortError' ? 'request_aborted' : 'transport_error', at: Date.now() / 1000});
        }
      }
      throw error;
    } finally { q.castSending = false; }
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
    if (resource === 'field-cast') return castRequest(input, options, body);
    const control = resource === 'quests';
    const tracked = control || (body.action && resource !== 'start');
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
      if (options?.signal?.aborted) stop('quest_request_expired_before_send');
      if (instances[resource] && body.action === 'start') q.starts.add(resource);
      if (key) {
        q.statusCache.clear();
        q.pending.set(key, {resource, action: body.action || 'events', operation_id: key,
          quest_id: body.questId, enemy_id: body.enemyId, at: Date.now() / 1000});
        persist();
      }
      let response, decoded;
      try {
        response = await original(input, options);
        decoded = await readResponse(response);
      } catch (error) {
        if (key) q.error = 'quest_transport_unconfirmed';
        else q.statusError = 'quest_status_unavailable';
        q.receipts.push({resource, action: body.action || 'events', operation_id: key, ok: false,
          error: error?.name === 'AbortError' ? 'request_aborted' : 'transport_error', at: Date.now() / 1000});
        throw error;
      }
      const {data, parseError, buffered} = decoded;
      if (resource === 'start' && data?.ok === true && data.account?.playerId) {
        q.player = Number(data.account.playerId);
        if (q.player !== config.player) q.error = 'quest_player_mismatch';
      }
      if (control || key || instances[resource]) {
        if (receipt(resource, body, response, data, key, parseError)) {
          if (statusKey) {
            q.statusError = '';
            q.statusCache.set(statusKey, {at: performance.now(), response: buffered.clone()});
          }
          if (key) {
            q.pending.delete(key);
            persist();
            q.responses.set(key, buffered.clone());
            if (q.responses.size > 120) q.responses.delete(q.responses.keys().next().value);
          }
        }
      }
      if (parseError) throw Object.assign(Error('quest_response_unreadable'), {name: parseError});
      return buffered;
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
