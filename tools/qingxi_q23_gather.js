  // Q23 has a server-owned 300 s delivery window after the bottle is filled.
  // Reserve another 120 s by policy for collecting and the request receipts.
  const Q23_GATHER_RESERVE_MS = 420000;
  const Q23_BOTTLE = 'qxq_sealing_bottle';
  class Q23GatherDeferred extends Error {}

  function q23Unfilled(data) {
    const slot = data?.states?.qx_q23;
    const timerMap = slot?.timers === undefined ? {} : slot.timers;
    const progress = slot?.progress === undefined ? {} : slot.progress;
    const objectMap = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    return data?.world?.current === 'yue' && slot?.status === 'active' &&
      objectMap(timerMap) && Object.keys(timerMap).length === 0 &&
      objectMap(progress) && (!Object.hasOwn(progress, 'bottle') || progress.bottle === 0);
  }

  function q23NodeMatches(data, nodeId) {
    if (typeof nodeId !== 'string' || !nodeId || nodeId.startsWith('loot:') || !Array.isArray(data?.nodes)) return false;
    const nodes = data.nodes.filter(node => node?.id === nodeId);
    if (nodes.length !== 1) return false;
    const [node] = nodes;
    return (node.itemId || node.worldObject || node.id) === Q23_BOTTLE &&
      (!node.region || node.region === 'yue') && !!position3(node.position) &&
      !node.puzzle && !node.aerial && !(node.rest > 3);
  }

  async function q23StartGather(action, body, owner, startPosition) {
    const policy = window.__qx;
    const story = state.story;
    const nodeId = body.nodeId;
    const defer = reason => { throw new Q23GatherDeferred(reason); };
    if (!policy || policy.q23Gather || action !== 'gather-start') throw new Error('血煞封瓶请求归属无法核对');
    const lease = {nodeId, sent: false, deferred: null, beforeSend: null};
    const validate = request => {
      checkOwner(owner);
      if (window.__qx !== policy || state.story !== story || story?.current !== 'qx_q23' ||
          window.__rt !== story.runtime || window.__quests !== story.quests)
        throw new Error('血煞封瓶请求场景已变更');
      const data = storyData();
      if (!q23Unfilled(data)) defer('瓶任务状态待核对');
      if (!q23NodeMatches(data, nodeId)) defer('封煞瓶节点待核对');
      const deadline = Math.min(story.deadline, story.q23WorkerDeadline);
      if (!Number.isFinite(deadline) || deadline - Date.now() < Q23_GATHER_RESERVE_MS)
        defer('剩余时间不足以装瓶和交付');
      storyCheck(owner);
      const actor = readWorld()?.actorPosition;
      if (document.hidden || !shown('dwellingWorld') || shown('worldLoading') || readCombat()?.paused ||
          window.__rt.mobileHudState?.()?.blocked ||
          !position3(actor) || !position3(startPosition) || dist(actor, startPosition) > .16 ||
          Math.abs(actor[1] - startPosition[1]) > .2 || (request && (request.region !== 'yue' ||
            !position3(request.position) || dist(request.position, actor) > .16)))
        defer('封煞瓶采集位置或场景已变更');
    };
    validate(null);
    lease.beforeSend = request => {
      if (lease.deferred) throw lease.deferred;
      try { validate(request); }
      catch (error) {
        if (error instanceof Q23GatherDeferred) lease.deferred = error;
        throw error;
      }
    };
    policy.q23Gather = lease;
    try {
      const result = await story.quests.fieldRequest(action, body);
      if (!lease.sent) throw new Error('血煞封瓶请求未经过派发核对');
      return result;
    }
    catch (error) { throw (!lease.sent && lease.deferred) || error; }
    finally {
      if (policy.q23Gather === lease) delete policy.q23Gather;
    }
  }

  async function q23FinishDeferred(error, owner) {
    await finishInCave(owner);
    if (!caveIsSafe() || state.home?.owner !== owner || state.home?.verified !== true)
      throw new Error('封煞瓶暂缓后返洞府未确认');
    window.__qx.stopReason = {reason: __Q23_DEFER_REASON__, quest_id: 'qx_q23'};
    step('血煞封瓶 · ' + error.message + '，已返回洞府');
    return '血煞封瓶暂缓';
  }
