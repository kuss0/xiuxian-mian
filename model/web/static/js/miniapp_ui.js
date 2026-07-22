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
    var jumpMax = Number((tree.jump && tree.jump.max_target_score) || 45);
    var flyMax = Number((tree.fly && tree.fly.max_target_score) || 20);
    var daily = tree.daily_state || {};
    var coordinator = tree.coordinator || {};
    var dailyStatus = daily.day_key
      ? String(daily.day_key) + '｜' + String(daily.phase || 'unknown')
      : '今日尚未运行';
    var runningText = ['entry_pending', 'running'].indexOf(String(coordinator.phase || '')) >= 0
      ? '全局 ' + String(coordinator.phase) + '｜身份 ' + String(coordinator.identity_id || '-')
      : '';
    return ''
      + '<section class="miniapp-score-config" data-miniapp-score-config="tree">'
      + '<div class="miniapp-score-title"><strong>灵树 MiniApp</strong><span>身份：' + esc(selectedIdentityId() || '-') + '</span></div>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-tree-auto-enabled="1"' + (tree.auto_enabled ? ' checked' : '') + (tree.eligible || tree.auto_enabled ? '' : ' disabled') + '><span>每日自动跑完跳一跳与飞一飞</span></label>'
      + '<div class="miniapp-item-meta">'
      + badge(tree.eligible ? '落云宗资格正常' : (tree.eligibility_reason || '资格不符'), tree.eligible ? 'ok' : 'warn')
      + badge(dailyStatus, daily.phase === 'completed' ? 'ok' : 'neutral')
      + (runningText ? badge(runningText, 'warn') : '')
      + '</div>'
      + '<label><span>跳一跳</span><input type="number" min="' + esc(jumpMin) + '" max="' + esc(jumpMax) + '" step="1" data-tree-score-input="jump" value="' + esc(targetScoreValue(tree, 'jump')) + '"></label>'
      + '<label><span>飞一飞</span><input type="number" min="' + esc(flyMin) + '" max="' + esc(flyMax) + '" step="1" data-tree-score-input="fly" value="' + esc(targetScoreValue(tree, 'fly')) + '"></label>'
      + '<div class="miniapp-item-actions">'
      + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-tree-score-save="1">保存分数</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-tree-auto-save="1">保存自动开关</button>'
      + '</div>'
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

  function commandCatalogIssueLabel(issue) {
    var labels = {
      flow_replacement_uncatalogued: '现有 flow 未归类',
      external_entry_not_automated: '外部入口未自动化',
      unapproved_multi_surface: '未批准的跨入口重复',
      malformed_command: '命令格式异常'
    };
    return labels[issue && issue.code] || (issue && issue.code) || '目录问题';
  }

  function renderCommandCatalog(catalog, validation) {
    catalog = catalog || {};
    validation = validation || {};
    var summary = catalog.summary || {};
    var validationSummary = validation.summary || {};
    var categories = Array.isArray(catalog.categories) ? catalog.categories : [];
    var issues = Array.isArray(validation.issues) ? validation.issues : [];
    var issueHtml = issues.length ? issues.map(function (issue) {
      var tone = issue.level === 'error' || issue.level === 'warn' ? 'warn' : 'neutral';
      return '<div class="miniapp-flow">'
        + badge(String(issue.level || 'info').toUpperCase(), tone)
        + ' ' + esc(commandCatalogIssueLabel(issue))
        + (issue.command ? '：' + esc(issue.command) : '')
        + '</div>';
    }).join('') : '<div class="miniapp-flow">目录与现有 flow 一致</div>';
    var categoryHtml = categories.map(function (category) {
      var groups = Array.isArray(category.groups) ? category.groups : [];
      return ''
        + '<details class="miniapp-group miniapp-command-category">'
        + '<summary class="miniapp-group-title">' + esc(category.label || category.key)
        + '<span> ' + esc(category.command_count || 0) + '</span></summary>'
        + groups.map(function (group) {
          return '<div class="miniapp-flow"><strong>' + esc(group.label || group.key) + '</strong>｜'
            + compactList(group.commands) + '</div>';
        }).join('')
        + '</details>';
    }).join('');
    return ''
      + '<section class="miniapp-score-config miniapp-command-catalog" data-miniapp-command-catalog="1">'
      + '<div class="miniapp-score-title"><strong>命令迁移目录</strong><span>v' + esc(catalog.version || '-') + '</span></div>'
      + '<div class="miniapp-item-meta">'
      + badge(validation.status === 'error' ? '目录错误' : (validation.status === 'warn' ? '待复核' : '已通过'), validation.status === 'ok' ? 'ok' : 'warn')
      + badge('命令 ' + esc(summary.unique_commands || 0), 'neutral')
      + badge('flow ' + esc(validationSummary.catalogued_flow_replacements || 0) + '/' + esc(validationSummary.flow_replacement_commands || 0), validationSummary.warnings ? 'warn' : 'ok')
      + badge('外部自动化 ' + esc(validationSummary.automated_external_entries || 0) + '/' + esc(validationSummary.external_entry_commands || 0), 'neutral')
      + '</div>'
      + '<div class="miniapp-command-categories">' + categoryHtml + '</div>'
      + '<div class="miniapp-command-issues">' + issueHtml + '</div>'
      + '</section>';
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
    var fishingCandidates = Array.isArray(automation.cave_public_fishing_candidates) ? automation.cave_public_fishing_candidates : [];
    var fishingCandidateHtml = fishingCandidates.length
      ? '<div class="miniapp-candidate-list">' + fishingCandidates.map(function (item) {
          return '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-fishing-candidate="' + esc(item.identity_id) + '"' + (item.auto_enabled ? ' checked' : '') + '><span>' + esc(item.label || item.identity_id) + '</span></label>';
        }).join('') + '</div>'
      : '<span class="miniapp-empty">暂无频道身份候选</span>';
    return ''
      + '<section class="miniapp-score-config miniapp-cave-public" data-cave-public-entry="1">'
      + '<div class="miniapp-score-title"><strong>洞府公共入口</strong><span>独立开关｜候选入口串行兜底</span></div>'
      + '<label><span>公共 URL 候选</span><textarea data-cave-public-url="1" rows="3" placeholder="' + (automation.cave_public_entry_url_configured ? '已配置 ' + esc(automation.cave_public_entry_url_count || 1) + ' 个，可留空沿用；每行一个入口' : '粘贴洞府公共入口，每行一个') + '" autocomplete="off">' + esc(cavePublicUrlDraft) + '</textarea></label>'
      + '<label><span>动作间隔</span><input type="number" min="10" max="120" step="5" data-cave-public-delay="1" value="' + esc(automation.cave_public_delay_sec || 20) + '"></label>'
      + '<div class="miniapp-item-actions">'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-config-save="1">保存设置</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-batch-run="1"' + (running ? ' disabled' : '') + '>串行跑启用项</button>'
      + '</div>'
      + '<div class="miniapp-cave-switches">'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="small_world"' + (automation.cave_public_small_world_enabled ? ' checked' : '') + '><span>小世界</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="small_world_harvest"' + (automation.cave_public_small_world_harvest_enabled ? ' checked' : '') + '><span>香火收割 8h</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="deep_status"' + (automation.cave_public_deep_status_enabled ? ' checked' : '') + '><span>闭关状态</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="treasure"' + (automation.cave_public_treasure_enabled ? ' checked' : '') + '><span>洞府寻宝</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="trial"' + (automation.cave_public_trial_enabled ? ' checked' : '') + '><span>天机试炼</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="fishing"' + (automation.cave_public_fishing_enabled ? ' checked' : '') + '><span>频道钓鱼</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="stargazer"' + (automation.cave_public_stargazer_enabled ? ' checked' : '') + '><span>观星台</span></label>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-cave-public-switch="yuanying"' + (automation.cave_public_yuanying_enabled ? ' checked' : '') + '><span>元婴</span></label>'
      + '</div>'
      + '<div class="miniapp-score-title"><strong>频道钓鱼白名单</strong><span>仅走公共入口，不发送群命令</span></div>'
      + fishingCandidateHtml
      + '<div class="miniapp-cave-batch-status">'
      + badge(status, running ? 'warn' : 'neutral')
      + badge('入口 ' + (automation.cave_public_entry_url_count || 0), automation.cave_public_entry_url_configured ? 'ok' : 'neutral')
      + badge('成功 ' + succeeded, 'ok')
      + (failed ? badge('失败 ' + failed, 'warn') : '')
      + (result ? '<span>' + esc(result) + '</span>' : '')
      + '</div>'
      + '<div class="miniapp-item-actions miniapp-cave-single-actions">'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="small_world">小世界处理</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="small_world_harvest">收割香火</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="treasure">洞府寻宝</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="trial">天机试炼</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="fishing">频道钓鱼</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="stargazer">观星台</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="tree">落云灵树</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="yuanying">元婴状态/出窍</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="yinluo_status">阴罗幡状态</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="concubine_status">侍妾状态</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="beast_status">灵兽状态</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_status">闭关状态</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_start">开始深闭</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="deep_settle">结算深闭</button>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-cave-public-action="yuanying">元婴出窍</button>'
      + '</div>'
      + '</section>';
  }

  function renderWorldBossControls(automation) {
    automation = automation || {};
    var candidates = Array.isArray(automation.world_boss_candidates) ? automation.world_boss_candidates : [];
    var candidateHtml = candidates.length ? candidates.map(function (item) {
      return '<label class="miniapp-cave-switch">'
        + '<input type="checkbox" data-world-boss-candidate="' + esc(item.identity_id) + '"' + (item.auto_enabled ? ' checked' : '') + '>'
        + '<span>' + esc(item.label || item.identity_id) + '</span>'
        + '</label>';
    }).join('') : '<span class="miniapp-empty">暂无可用登录账户</span>';
    return ''
      + '<section class="miniapp-score-config" data-world-boss-auto="1">'
      + '<div class="miniapp-score-title"><strong>世界 Boss 自动化</strong><span>全局优先｜账户并行</span></div>'
      + '<label class="miniapp-cave-switch"><input type="checkbox" data-world-boss-enabled="1"' + (automation.world_boss_auto_enabled ? ' checked' : '') + '><span>自动参与</span></label>'
      + '<label><span>登录账户上限</span><input type="number" min="1" max="4" step="1" data-world-boss-account-limit="1" value="' + esc(automation.world_boss_auto_account_limit || 1) + '"></label>'
      + '<div class="miniapp-score-title"><strong>自动账户</strong><span>取消勾选则保留手动</span></div>'
      + '<div class="miniapp-cave-switches">' + candidateHtml + '</div>'
      + '<div class="miniapp-item-actions"><button type="button" class="btn btn-secondary btn-compact" data-world-boss-config-save="1">保存设置</button></div>'
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
    var globalRate = policy.global_rate_limit || {};
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
      + badge('全局请求 ' + esc(globalRate.request_count || 0) + '/' + esc(globalRate.limit || 90) + '·60s', globalRate.priority_active ? 'warn' : 'neutral')
      + '</div>'
      + renderCommandCatalog(miniapp.command_catalog, miniapp.command_catalog_validation)
      + renderCavePublicControls(automation, miniapp.cave_public_batch || {})
      + renderWorldBossControls(automation)
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

  async function saveTreeAutoConfig(button) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    var panel = document.querySelector('[data-miniapp-score-config="tree"]');
    var enabledInput = panel && panel.querySelector('[data-tree-auto-enabled="1"]');
    if (button) button.disabled = true;
    try {
      var data = await post('/api/miniapp-tree-auto-config', {
        send_as_id: sendAsId,
        enabled: !!(enabledInput && enabledInput.checked)
      });
      flash(data.message || '灵树自动开关已保存', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '灵树自动开关保存失败', true);
      refreshMiniAppStatus();
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
    var fishingIdentityIds = [];
    if (panel) {
      panel.querySelectorAll('[data-cave-public-fishing-candidate]').forEach(function (input) {
        if (input.checked) fishingIdentityIds.push(input.getAttribute('data-cave-public-fishing-candidate'));
      });
    }
    return {
      small_world_enabled: enabled('small_world'),
      small_world_harvest_enabled: enabled('small_world_harvest'),
      deep_status_enabled: enabled('deep_status'),
      treasure_enabled: enabled('treasure'),
      trial_enabled: enabled('trial'),
      fishing_enabled: enabled('fishing'),
      fishing_identity_ids: fishingIdentityIds,
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
    else if (config.small_world_harvest_enabled) actions.push('small_world_harvest');
    if (config.deep_status_enabled) actions.push('deep_status');
    if (config.treasure_enabled) actions.push('treasure');
    if (config.trial_enabled) actions.push('trial');
    if (config.fishing_enabled) actions.push('fishing');
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

  async function saveWorldBossConfig(button) {
    var panel = document.querySelector('[data-world-boss-auto="1"]');
    if (!panel) return;
    if (button) button.disabled = true;
    try {
      var excludedIds = [];
      panel.querySelectorAll('[data-world-boss-candidate]').forEach(function (input) {
        if (!input.checked) excludedIds.push(input.getAttribute('data-world-boss-candidate'));
      });
      var data = await post('/api/world-boss-miniapp-config', {
        enabled: !!(panel.querySelector('[data-world-boss-enabled="1"]') || {}).checked,
        account_limit: (panel.querySelector('[data-world-boss-account-limit="1"]') || {}).value || 1,
        excluded_identity_ids: excludedIds
      });
      flash(data.message || '世界 Boss MiniApp 设置已保存', false);
      if (data.miniapp) renderMiniAppStatus({ miniapp: data.miniapp });
      else refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || '世界 Boss MiniApp 设置保存失败', true);
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
    var treeAutoSaveBtn = event.target.closest('[data-miniapp-tree-auto-save]');
    if (treeAutoSaveBtn) {
      saveTreeAutoConfig(treeAutoSaveBtn);
      return;
    }
    var caveConfigSaveBtn = event.target.closest('[data-cave-public-config-save]');
    if (caveConfigSaveBtn) {
      saveCavePublicConfig(caveConfigSaveBtn);
      return;
    }
    var worldBossConfigSaveBtn = event.target.closest('[data-world-boss-config-save]');
    if (worldBossConfigSaveBtn) {
      saveWorldBossConfig(worldBossConfigSaveBtn);
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
