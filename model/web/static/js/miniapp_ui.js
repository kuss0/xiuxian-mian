(function () {
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
      ? '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-run="' + esc(key) + '">手动执行</button>'
      : '';
    var stepText = steps.map(function (step) { return step.key || step.endpoint || ''; }).filter(Boolean).slice(0, 8).join(' → ');
    return ''
      + '<article class="miniapp-item">'
      + '<div class="miniapp-item-main">'
      + '<div class="miniapp-item-head"><strong>' + esc(adapter.label || key) + '</strong><span>' + esc(key) + '</span></div>'
      + '<div class="miniapp-item-meta">'
      + badge(adapter.default_enabled ? '默认开' : '默认关', adapter.default_enabled ? 'warn' : 'ok')
      + badge(adapter.manual_only ? '手动' : '调度', adapter.manual_only ? 'ok' : 'warn')
      + (adapter.api_base_url ? badge(adapter.api_base_url, 'neutral') : '')
      + '</div>'
      + (stepText ? '<div class="miniapp-flow">' + esc(stepText) + '</div>' : '')
      + '</div>'
      + '<div class="miniapp-item-actions">' + actionButton + runButton + '</div>'
      + '</article>';
  }

  function renderMiniAppStatus(snapshot) {
    var body = document.getElementById('miniapp-modal-body');
    if (!body) return;
    var miniapp = (snapshot && snapshot.miniapp) || {};
    var adapters = Array.isArray(miniapp.adapters) ? miniapp.adapters : [];
    var probes = Array.isArray(miniapp.entry_probe_commands) ? miniapp.entry_probe_commands : [];
    var runners = Array.isArray(miniapp.manual_run_commands) ? miniapp.manual_run_commands : [];
    var plans = miniapp.flow_plans || {};
    var probeByKey = {};
    var runByKey = {};
    probes.forEach(function (item) { probeByKey[item.game_key] = item; });
    runners.forEach(function (item) { runByKey[item.game_key] = item; });
    var policy = miniapp.policy || {};
    body.innerHTML = ''
      + '<div class="miniapp-toolbar">'
      + '<div id="miniapp-status-line" class="form-label form-label-inline">身份：' + esc(selectedIdentityId() || '未选择') + '</div>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-miniapp-refresh="1">刷新</button>'
      + '</div>'
      + '<div class="miniapp-policy">'
      + badge(policy.default_enabled ? '默认启用' : '默认关闭', policy.default_enabled ? 'warn' : 'ok')
      + badge(policy.manual_only ? '手动优先' : '允许调度', policy.manual_only ? 'ok' : 'warn')
      + badge(policy.raw_init_data_persisted ? 'initData落盘' : 'initData不落盘', policy.raw_init_data_persisted ? 'warn' : 'ok')
      + badge(policy.raw_start_token_persisted ? 'token落盘' : 'token不落盘', policy.raw_start_token_persisted ? 'warn' : 'ok')
      + '</div>'
      + '<div class="miniapp-list">'
      + (adapters.length ? adapters.map(function (adapter) {
        return renderAdapter(adapter, probeByKey, runByKey, plans);
      }).join('') : '<div class="miniapp-empty">暂无 MiniApp registry</div>')
      + '</div>';
  }

  async function refreshMiniAppStatus() {
    var body = document.getElementById('miniapp-modal-body');
    if (body) body.innerHTML = '<div class="miniapp-empty">加载中</div>';
    try {
      var response = await fetch('/api/miniapp-status', { credentials: 'same-origin', cache: 'no-store' });
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

  async function runManualMiniApp(gameKey, button) {
    var sendAsId = selectedIdentityId();
    if (!sendAsId) {
      flash('请选择身份', true);
      return;
    }
    if (button) button.disabled = true;
    try {
      var data = await post('/api/miniapp-manual-run', {
        send_as_id: sendAsId,
        game_key: gameKey
      });
      flash(data.message || 'MiniApp 手动执行已发送', false);
      refreshMiniAppStatus();
    } catch (error) {
      flash((error && error.message) || 'MiniApp 手动执行失败', true);
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
    var probeBtn = event.target.closest('[data-miniapp-probe]');
    if (probeBtn) {
      runEntryProbe(probeBtn.getAttribute('data-miniapp-probe'), probeBtn);
      return;
    }
    var runBtn = event.target.closest('[data-miniapp-run]');
    if (runBtn) {
      runManualMiniApp(runBtn.getAttribute('data-miniapp-run'), runBtn);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMiniAppModal();
  });
})();
