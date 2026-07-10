(function () {
  var currentMiniAppSnapshot = null;
  // The public URL contains a short-lived token. Keep it only in this page's
  // memory so status refreshes do not discard it, but never persist it.
  var cavePublicUrlDraft = '';

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function selectedIdentityId() {
    if (typeof getSelectedIdentity === 'function') {
      var identity = getSelectedIdentity();
      if (identity && identity.send_as_id != null) return identity.send_as_id;
    }
    if (window.appState && window.appState.selectedId != null) return window.appState.selectedId;
    return '';
  }

  function parseResponse(response) {
    if (typeof parseApiResponse === 'function') return parseApiResponse(response);
    return response.json().then(function (data) {
      if (!response.ok || !data.ok) throw new Error(data.error || data.message || '请求失败');
      return data;
    });
  }

  function post(path, payload) {
    if (typeof postJson === 'function') return postJson(path, payload);
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
      credentials: 'same-origin',
      cache: 'no-store'
    }).then(parseResponse);
  }

  function flash(message, isError) {
    if (typeof updateFlash === 'function') {
      updateFlash(message, !!isError);
      return;
    }
    var status = document.getElementById('miniapp-status-line');
    if (status) status.textContent = message || '';
  }

  function badge(text, tone) {
    return '<span class="miniapp-badge miniapp-badge-' + esc(tone || 'neutral') + '">' + esc(text) + '</span>';
  }

  function compactList(values, emptyText) {
    var list = Array.isArray(values) ? values.filter(Boolean) : [];
    return esc(list.length ? list.join(', ') : (emptyText || '-'));
  }

  function renderAdapter(adapter, probeByKey, runByKey, planByKey) {
    var key = adapter.game_key || '';
    var probe = probeByKey[key] || null;
    var runner = runByKey[key] || null;
    var plan = planByKey[key] || null;
    var steps = plan && Array.isArray(plan.steps) ? plan.steps : [];
    var actionButton = probe
      ? '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-probe="' + esc(key) + '">入口诊断</button>'
      : '';
    var runButton = runner
      ? (
        key === 'tree'
          ? '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-run="' + esc(key) + '" data-miniapp-run-mode="jump">跳一跳</button>'
            + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-run="' + esc(key) + '" data-miniapp-run-mode="fly">飞一飞</button>'
          : '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-run="' + esc(key) + '">手动执行</button>'
      )
      : '';
    var captureButton = '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-capture="' + esc(key) + '">协议摘要</button>';
    var stepText = steps.map(function (step) { return step.key || step.endpoint || ''; }).filter(Boolean).slice(0, 8).join(' → ');
    return ''
      + '<article class="miniapp-item">'
      + '<div class="miniapp-item-main">'
      + '<div class="miniapp-item-head"><strong>' + esc(adapter.label || key) + '</strong><span>' + esc(key) + '</span></div>'
      + '<div class="miniapp-item-meta">'
      + badge(adapter.default_enabled ? '默认开' : '默认关', adapter.default_enabled ? 'warn' : 'ok')
      + badge(adapter.manual_only ? '手动' : '调度', adapter.manual_only ? 'ok' : 'warn')
      + badge(adapter.ui_group_label || 'MiniApp合集', adapter.ui_group === 'sect' ? 'neutral' : 'ok')
      + (adapter.api_base_url ? badge(adapter.api_base_url, 'neutral') : '')
      + '</div>'
      + (stepText ? '<div class="miniapp-flow">' + esc(stepText) + '</div>' : '')
      + '</div>'
      + '<div class="miniapp-item-actions">' + actionButton + runButton + captureButton + '</div>'
      + '</article>';
  }

  function renderAdapterGroup(group, adapters, probeByKey, runByKey, planByKey) {
    var groupAdapters = adapters.filter(function (adapter) {
      return (adapter.ui_group || 'miniapp') === group.key;
    });
    if (!groupAdapters.length) return '';
    return ''
      + '<section class="miniapp-group">'
      + '<div class="miniapp-group-title">' + esc(group.label || group.key) + '</div>'
      + groupAdapters.map(function (adapter) {
        return renderAdapter(adapter, probeByKey, runByKey, planByKey);
      }).join('')
      + '</section>';
  }

  function targetScoreValue(config, mode) {
    var item = (config && config[mode]) || {};
    var range = Array.isArray(item.target_score_range) ? item.target_score_range : [];
    var low = Number(range[0] || 0);
    var high = Number(range[range.length - 1] || low || 0);
    var value = Math.round((low + high) / 2);
    return Number.isFinite(value) && value > 0 ? value : 24;
  }

  function renderTreeScoreControls(scoreControls) {
    var tree = scoreControls && scoreControls.tree;
    if (!tree) return '';
    var jumpMin = Number((tree.jump && tree.jump.min_target_score) || 4);
    var flyMin = Number((tree.fly && tree.fly.min_target_score) || 4);
    var jumpMax = Number((tree.jump && tree.jump.max_target_score) || 20);
    var flyMax = Number((tree.fly && tree.fly.max_target_score) || 20);
    return ''
      + '<section class="miniapp-score-config" data-miniapp-score-config="tree">'
      + '<div class="miniapp-score-title"><strong>灵树区间中值</strong><span>身份：' + esc(selectedIdentityId() || '-') + '</span></div>'
      + '<label><span>跳一跳</span><input type="number" min="' + esc(jumpMin) + '" max="' + esc(jumpMax) + '" step="1" data-tree-score-input="jump" value="' + esc(targetScoreValue(tree, 'jump')) + '"></label>'
      + '<label><span>飞一飞</span><input type="number" min="' + esc(flyMin) + '" max="' + esc(flyMax) + '" step="1" data-tree-score-input="fly" value="' + esc(targetScoreValue(tree, 'fly')) + '"></label>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-tree-score-save="1">保存</button>'
      + '</section>';
  }

  function renderCaptureSummary(summary) {
    var panel = document.getElementById('miniapp-capture-panel');
    if (!panel) return;
    if (!summary || !summary.game_key) {
      panel.innerHTML = '<div class="miniapp-empty">暂无协议摘要</div>';
      return;
    }
    var endpoints = Array.isArray(summary.endpoints) ? summary.endpoints : [];
    var endpointHtml = endpoints.length ? endpoints.map(function (item) {
      return ''
        + '<article class="miniapp-capture-item">'
        + '<div class="miniapp-capture-head"><strong>' + esc(item.method || 'POST') + ' ' + esc(item.url_path || '') + '</strong><span>' + esc(item.step_key || '') + '</span></div>'
        + '<div class="miniapp-item-meta">'
        + badge('样本 ' + (item.count || 0), 'neutral')
        + badge('OK ' + (item.ok_count || 0), 'ok')
        + (item.error_count ? badge('错误 ' + item.error_count, 'warn') : '')
        + (item.avg_elapsed_ms ? badge(String(item.avg_elapsed_ms) + 'ms', 'neutral') : '')
        + '</div>'
        + '<div class="miniapp-flow">请求：' + compactList(item.request_payload_keys) + '</div>'
        + '<div class="miniapp-flow">回包：' + compactList(item.response_keys) + '</div>'
        + (item.latest_error ? '<div class="miniapp-flow miniapp-error">最近错误：' + esc(item.latest_error) + '</div>' : '')
        + '</article>';
    }).join('') : '<div class="miniapp-empty">还没有 capture 样本</div>';
    panel.innerHTML = ''
      + '<div class="miniapp-capture-title">'
      + '<strong>协议摘要：' + esc(summary.game_key) + '</strong>'
      + '<span>' + esc(summary.day || '') + '｜样本 ' + esc(summary.scanned_records || 0) + '/' + esc(summary.total_records || 0) + '</span>'
      + '</div>'
      + '<div class="miniapp-flow">AI 交接：' + esc((summary.ai_handoff && summary.ai_handoff.rule) || '') + '</div>'
      + '<div class="miniapp-capture-list">' + endpointHtml + '</div>';
  }

  function renderBatchButtons(batchCommands) {
    var commands = Array.isArray(batchCommands) ? batchCommands : [];
    return commands.map(function (item) {
      if (!item || !item.endpoint) return '';
      return '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-batch-run="' + esc(item.endpoint) + '">' + esc(item.label || '批量执行') + '</button>';
    }).join('');
  }

  function renderCavePublicControls(automation, batch) {
    automation = automation || {};
    batch = batch || {};
    var running = !!batch.running;
    var completed = Number(batch.completed || 0);
    var total = Number(batch.total || 0);
    var succeeded = Number(batch.succeeded || 0);
    var failed = Number(batch.failed || 0);
    var status = running
      ? '运行中 ' + completed + '/' + total + (batch.current ? '｜' + batch.current : '')
      : (batch.batch_id ? '最近完成 ' + completed + '/' + total : '未启动');
    var result = batch.last_result || '';
    return ''
      + '<section class="miniapp-score-config miniapp-cave-public" data-cave-public-entry="1">'
      + '<div class="miniapp-score-title"><strong>洞府公共入口</strong><span>独立开关｜按动作身份策略串行</span></div>'
      + '<label><span>公共 URL</span><input type="url" data-cave-public-url="1" value="' + esc(cavePublicUrlDraft) + '" placeholder="' + (automation.cave_public_entry_url_configured ? '已配置，可留空沿用' : '粘贴洞府公共入口') + '" autocomplete="off"></label>'
      + '<label><span>动作间隔</span><input type="number" min="10" max="120" step="5" data-cave-public-delay="1" value="' + esc(automation.cave_public_delay_sec || 20) + '"></label>'
      + '<div class="miniapp-item-actions">'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-config-save="1">保存设置</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-batch-run="1"' + (running ? ' disabled' : '') + '>串行跑启用项</button>'
      + '</div>'
      + '<div class="miniapp-cave-switches">'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="small_world"' + (automation.cave_public_small_world_enabled ? ' checked' : '') + '><span>小世界</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="deep_status"' + (automation.cave_public_deep_status_enabled ? ' checked' : '') + '><span>闭关状态</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="treasure"' + (automation.cave_public_treasure_enabled ? ' checked' : '') + '><span>洞府寻宝</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="trial"' + (automation.cave_public_trial_enabled ? ' checked' : '') + '><span>天机试炼</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="stargazer"' + (automation.cave_public_stargazer_enabled ? ' checked' : '') + '><span>观星台</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="yuanying"' + (automation.cave_public_yuanying_enabled ? ' checked' : '') + '><span>元婴</span></label>'
      + '</div>'
      + '<div class="miniapp-cave-batch-status">'
      + badge(status, running ? 'warn' : 'neutral')
      + badge('成功 ' + succeeded, 'ok')
      + (failed ? badge('失败 ' + failed, 'warn') : '')
      + (result ? '<span>' + esc(result) + '</span>' : '')
      + '</div>'
      + '<div class="miniapp-item-actions miniapp-cave-single-actions">'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="small_world">小世界处理</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="treasure">洞府寻宝</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="trial">天机试炼</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="stargazer">观星台</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="yuanying">元婴状态/出窍</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_status">闭关状态</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_start">开始深闭</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_settle">结算深闭</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="yuanying">元婴出窍</button>'
      + '</div>'
      + '</section>';
  }

  function renderMiniAppStatus(snapshot) {
    var body = document.getElementById('miniapp-modal-body');
    if (!body) return;
    var miniapp = (snapshot && snapshot.miniapp) || {};
    currentMiniAppSnapshot = miniapp;
    var adapters = Array.isArray(miniapp.adapters) ? miniapp.adapters : [];
    var probes = Array.isArray(miniapp.entry_probe_commands) ? miniapp.entry_probe_commands : [];
    var runners = Array.isArray(miniapp.manual_run_commands) ? miniapp.manual_run_commands : [];
    var plans = miniapp.flow_plans || {};
    var groups = Array.isArray(miniapp.ui_groups) ? miniapp.ui_groups : [
      {key: 'miniapp', label: 'MiniApp合集'},
      {key: 'sect', label: '宗门玩法'}
    ];
    var probeByKey = {};
    var runByKey = {};
    probes.forEach(function (item) { probeByKey[item.game_key] = item; });
    runners.forEach(function (item) { runByKey[item.game_key] = item; });
    var policy = miniapp.policy || {};
    var scoreControls = miniapp.score_controls || {};
    var automation = miniapp.automation || {};
    var batchButtons = renderBatchButtons(miniapp.batch_run_commands);
    var trialDailyEffective = !!automation.trial_daily_effective_enabled;
    var autoText = trialDailyEffective
      ? '试炼自动 ' + (automation.trial_daily_window_text || '--')
      : '试炼自动关闭';
    var autoDoneText = automation.trial_daily_done_today ? '今日已跑' : (automation.trial_daily_in_window ? '窗口内待跑' : '等待窗口');
    body.innerHTML = ''
      + '<div class="miniapp-toolbar">'
      + '<div id="miniapp-status-line" class="form-label form-label-inline">身份：' + esc(selectedIdentityId() || '未选择') + '</div>'
      + batchButtons
      + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-refresh="1">刷新</button>'
      + '</div>'
      + '<div class="miniapp-policy">'
      + badge(policy.default_enabled ? '默认启用' : '默认关闭', policy.default_enabled ? 'warn' : 'ok')
      + badge(policy.manual_only ? '手动优先' : '允许调度', policy.manual_only ? 'ok' : 'warn')
      + badge(autoText, trialDailyEffective ? 'ok' : 'neutral')
      + badge(autoDoneText, automation.trial_daily_done_today ? 'ok' : 'neutral')
      + badge(policy.raw_init_data_persisted ? 'initData落盘' : 'initData不落盘', policy.raw_init_data_persisted ? 'warn' : 'ok')
      + badge(policy.raw_start_token_persisted ? 'token落盘' : 'token不落盘', policy.raw_start_token_persisted ? 'warn' : 'ok')
      + '</div>'
      + renderCavePublicControls(automation, miniapp.cave_public_batch || {})
      + renderTreeScoreControls(scoreControls)
      + '<div class="miniapp-list">'
      + (adapters.length ? groups.map(function (group) {
        return renderAdapterGroup(group, adapters, probeByKey, runByKey, plans);
      }).join('') : '<div class="miniapp-empty">暂无 MiniApp registry</div>')
      + '</div>'
      + '<div id="miniapp-capture-panel" class="miniapp-capture-panel"><div class="miniapp-empty">选择玩法查看协议摘要</div></div>';
  }

  async function refreshMiniAppStatus() {
    var body = document.getElementById('miniapp-modal-body');
    if (body) body.innerHTML = '<div class="miniapp-empty">加载中</div>';
    try {
      var identity = selectedIdentityId();
      var url = '/api/miniapp-status' + (identity ? '?send_as_id=' + encodeURIComponent(identity) : '');
      var response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
      var data = await parseResponse(response);
      if (!data) return;
      renderMiniAppStatus(data);
    } catch (error) {
      if (body) body.innerHTML = '<div class="miniapp-empty miniapp-error">' + esc((error && error.message) || '加载失败') + '</div>';
    }
  }

  function openMiniAppModal() {
    var modal = document.getElementById('miniapp-modal');
    if (!modal) return;
    modal.classList.add('show');
    refreshMiniAppStatus();
  }

  function closeMiniAppModal() {
    var modal = document.getElementById('miniapp-modal');
    if (modal) modal.classList.remove('show');
  }

  async function runEntryProbe(gameKey, button) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    if (button) button.disabled = true;
    try {
      var data = await post('/api/miniapp-entry-probe', {
        send_as_id: sendAsId,
        game_key: gameKey
      });
      flash(data.message || 'MiniApp 入口诊断已发送', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || 'MiniApp 入口诊断失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function runManualMiniApp(gameKey, button, mode) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    if (button) button.disabled = true;
    try {
      var payload = {
        send_as_id: sendAsId,
        game_key: gameKey
      };
      if (gameKey === 'tree' && mode) payload.mode = mode;
      var data = await post('/api/miniapp-manual-run', payload);
      flash(data.message || 'MiniApp 手动执行已发送', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || 'MiniApp 手动执行失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function runMiniAppBatch(button) {
    var endpoint = button && button.getAttribute('data-miniapp-batch-run');
    if (!endpoint) return;
    if (button) button.disabled = true;
    try {
      var data = await post(endpoint, {});
      flash(data.message || 'MiniApp 批量已启动', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || 'MiniApp 批量启动失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function saveTreeScoreConfig(button) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    var panel = document.querySelector('[data-miniapp-score-config="tree"]');
    if (!panel) return;
    var jumpInput = panel.querySelector('[data-tree-score-input="jump"]');
    var flyInput = panel.querySelector('[data-tree-score-input="fly"]');
    if (button) button.disabled = true;
    try {
      var data = await post('/api/miniapp-tree-score-config', {
        send_as_id: sendAsId,
        jump_target_score: jumpInput ? jumpInput.value : '',
        fly_target_score: flyInput ? flyInput.value : ''
      });
      flash(data.message || '灵树目标区间已保存', false);
      if (data.miniapp) renderMiniAppStatus({ miniapp: data.miniapp });
      else refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '灵树目标区间保存失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function runCavePublicEntry(button) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    var action = button && button.getAttribute('data-cave-public-action');
    var panel = document.querySelector('[data-cave-public-entry="1"]');
    var input = panel && panel.querySelector('[data-cave-public-url="1"]');
    var publicUrl = input ? input.value : '';
    cavePublicUrlDraft = publicUrl;
    var configured = !!(((currentMiniAppSnapshot || {}).automation || {}).cave_public_entry_url_configured);
    if (!publicUrl && !configured) {
      flash('缺少洞府公共入口 URL', true);
      return;
    }
    if (button) button.disabled = true;
    try {
      var data = await post('/api/cave-public-entry-run', {
        send_as_id: sendAsId,
        action: action,
        public_entry_url: publicUrl
      });
      flash(data.message || '洞府公共入口已执行', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '洞府公共入口执行失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function cavePublicConfigPayload(panel) {
    function enabled(key) {
      var input = panel && panel.querySelector('[data-cave-public-switch="' + key + '"]');
      return !!(input && input.checked);
    }
    var delayInput = panel && panel.querySelector('[data-cave-public-delay="1"]');
    return {
      small_world_enabled: enabled('small_world'),
      deep_status_enabled: enabled('deep_status'),
      treasure_enabled: enabled('treasure'),
      trial_enabled: enabled('trial'),
      stargazer_enabled: enabled('stargazer'),
      yuanying_enabled: enabled('yuanying'),
      public_entry_url: (panel && panel.querySelector('[data-cave-public-url="1"]') || {}).value || '',
      delay_sec: delayInput ? delayInput.value : ''
    };
  }

  function cavePublicEnabledActions(panel) {
    var config = cavePublicConfigPayload(panel);
    var actions = [];
    if (config.small_world_enabled) actions.push('small_world');
    if (config.deep_status_enabled) actions.push('deep_status');
    if (config.treasure_enabled) actions.push('treasure');
    if (config.trial_enabled) actions.push('trial');
    if (config.stargazer_enabled) actions.push('stargazer');
    if (config.yuanying_enabled) actions.push('yuanying');
    return actions;
  }

  async function saveCavePublicConfig(button) {
    var panel = document.querySelector('[data-cave-public-entry="1"]');
    if (!panel) return;
    if (button) button.disabled = true;
    try {
      var data = await post('/api/cave-public-config', cavePublicConfigPayload(panel));
      flash(data.message || '洞府公共入口设置已保存', false);
      if (data.miniapp) renderMiniAppStatus({ miniapp: data.miniapp });
      else refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '洞府公共入口设置保存失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function runCavePublicBatch(button) {
    var panel = document.querySelector('[data-cave-public-entry="1"]');
    var input = panel && panel.querySelector('[data-cave-public-url="1"]');
    var publicUrl = input ? input.value : '';
    cavePublicUrlDraft = publicUrl;
    var configured = !!(((currentMiniAppSnapshot || {}).automation || {}).cave_public_entry_url_configured);
    if (!publicUrl && !configured) {
      flash('缺少洞府公共入口 URL', true);
      return;
    }
    var config = cavePublicConfigPayload(panel);
    var actions = cavePublicEnabledActions(panel);
    if (!actions.length) {
      flash('请至少开启一个洞府公共入口动作', true);
      return;
    }
    if (button) button.disabled = true;
    try {
      // Persist the independent toggles first so this run and later UI refreshes
      // have the same source of truth. The URL remains browser-memory only.
      await post('/api/cave-public-config', config);
      var data = await post('/api/cave-public-entry-batch-run', {
        public_entry_url: publicUrl,
        actions: actions,
        delay_sec: config.delay_sec
      });
      flash(data.message || '洞府公共入口串行批次已启动', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '洞府公共入口串行批次启动失败', true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function loadCaptureSummary(gameKey, button) {
    if (!gameKey) return;
    if (button) button.disabled = true;
    var panel = document.getElementById('miniapp-capture-panel');
    if (panel) panel.innerHTML = '<div class="miniapp-empty">读取协议摘要中</div>';
    try {
      var response = await fetch('/api/miniapp-capture-summary?game_key=' + encodeURIComponent(gameKey) + '&limit=200', {
        credentials: 'same-origin',
        cache: 'no-store'
      });
      var data = await parseResponse(response);
      renderCaptureSummary(data.capture || null);
    } catch (error) {
      if (panel) panel.innerHTML = '<div class="miniapp-empty miniapp-error">' + esc((error && error.message) || '协议摘要读取失败') + '</div>';
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-miniapp]')) {
      openMiniAppModal();
      return;
    }
    if (event.target.getAttribute('data-close-modal') === 'miniapp' || event.target.id === 'miniapp-modal') {
      closeMiniAppModal();
      return;
    }
    if (event.target.closest('[data-miniapp-refresh]')) {
      refreshMiniAppStatus();
      return;
    }
    var batchBtn = event.target.closest('[data-miniapp-batch-run]');
    if (batchBtn) {
      runMiniAppBatch(batchBtn);
      return;
    }
    var scoreSaveBtn = event.target.closest('[data-miniapp-tree-score-save]');
    if (scoreSaveBtn) {
      saveTreeScoreConfig(scoreSaveBtn);
      return;
    }
    var caveConfigSaveBtn = event.target.closest('[data-cave-public-config-save]');
    if (caveConfigSaveBtn) {
      saveCavePublicConfig(caveConfigSaveBtn);
      return;
    }
    var caveBatchBtn = event.target.closest('[data-cave-public-batch-run]');
    if (caveBatchBtn) {
      runCavePublicBatch(caveBatchBtn);
      return;
    }
    var cavePublicBtn = event.target.closest('[data-cave-public-action]');
    if (cavePublicBtn) {
      runCavePublicEntry(cavePublicBtn);
      return;
    }
    var probeBtn = event.target.closest('[data-miniapp-probe]');
    if (probeBtn) {
      runEntryProbe(probeBtn.getAttribute('data-miniapp-probe'), probeBtn);
      return;
    }
    var runBtn = event.target.closest('[data-miniapp-run]');
    if (runBtn) {
      runManualMiniApp(
        runBtn.getAttribute('data-miniapp-run'),
        runBtn,
        runBtn.getAttribute('data-miniapp-run-mode') || ''
      );
      return;
    }
    var captureBtn = event.target.closest('[data-miniapp-capture]');
    if (captureBtn) {
      loadCaptureSummary(captureBtn.getAttribute('data-miniapp-capture'), captureBtn);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMiniAppModal();
  });

  document.addEventListener('input', function (event) {
    if (event.target && event.target.matches('[data-cave-public-url="1"]')) {
      cavePublicUrlDraft = event.target.value || '';
    }
  });
})();
