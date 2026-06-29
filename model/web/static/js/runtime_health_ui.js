(function() {
  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function shortText(value, limit) {
    const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    const max = Number(limit || 80);
    return text.length > max ? text.slice(0, Math.max(0, max - 1)) + '…' : text;
  }

  function getRuntimeHealthSnapshot() {
    return (typeof appState !== 'undefined' && appState.snapshot && appState.snapshot.runtime_health) || {};
  }

  function levelClass(level) {
    const normalized = String(level || '').toLowerCase();
    if (normalized === 'ok') return 'runtime-health-ok';
    if (normalized === 'warn') return 'runtime-health-warn';
    if (normalized === 'error' || normalized === 'critical') return 'runtime-health-error';
    return 'runtime-health-unknown';
  }

  function renderRiskList(risks) {
    const items = Array.isArray(risks) ? risks.slice(0, 4) : [];
    if (!items.length) return '<div class="runtime-health-empty">暂无风险</div>';
    return items.map(function(item) {
      return '<div class="runtime-health-risk">'
        + '<span>' + esc(item.severity || 'warn') + '</span>'
        + '<strong>' + esc(shortText(item.message || item.code || '-', 72)) + '</strong>'
        + '</div>';
    }).join('');
  }

  function renderModuleList(modules) {
    const interesting = (Array.isArray(modules) ? modules : [])
      .filter(function(item) { return item && (item.status === 'error' || item.status === 'warn'); })
      .slice(0, 5);
    if (!interesting.length) return '<div class="runtime-health-empty">暂无异常模块</div>';
    return interesting.map(function(item) {
      const who = item.username || item.label || item.identity_id || '-';
      const details = Array.isArray(item.details) ? item.details.slice(0, 2).join('；') : '';
      return '<div class="runtime-health-module">'
        + '<span>' + esc(who) + '</span>'
        + '<strong>' + esc(item.module_label || item.module || '-') + '</strong>'
        + '<em>' + esc(item.status || '-') + '</em>'
        + '<small>' + esc(shortText(details || '-', 76)) + '</small>'
        + '</div>';
    }).join('');
  }

  function renderEvidence(evidenceRefs) {
    const refs = Array.isArray(evidenceRefs) ? evidenceRefs.slice(0, 3) : [];
    if (!refs.length) return '<div class="runtime-health-empty">暂无证据入口</div>';
    return refs.map(function(item) {
      if (!item) return '';
      const kind = item.kind || 'evidence';
      let text = item.path || item.service || item.command || '';
      if (kind === 'repeat_sample') text = (item.identity_id || '-') + ' ' + (item.command || '-') + ' x' + (item.count || 0);
      if (kind === 'journal') text = (item.service || '-') + ' hard=' + (item.hard_count || 0) + ' warn=' + (item.warn_count || 0);
      return '<div class="runtime-health-evidence"><span>' + esc(kind) + '</span><strong>' + esc(shortText(text, 96)) + '</strong></div>';
    }).join('');
  }

  function renderRuntimeHealthContent() {
    const snapshot = getRuntimeHealthSnapshot();
    const health = snapshot.health || {};
    const score = health.score == null ? '-' : health.score;
    const level = health.level || snapshot.status || 'unknown';
    const available = !!snapshot.available;
    const riskCount = Array.isArray(health.risk_reasons) ? health.risk_reasons.length : 0;
    return ''
      + '<div class="runtime-health-head">'
      + '<div><h2>运行健康</h2><div class="meta">只读审计包｜' + esc(snapshot.ts || '未生成') + '</div></div>'
      + '<div class="runtime-health-score ' + esc(levelClass(level)) + '"><strong>' + esc(score) + '</strong><span>' + esc(level) + '</span></div>'
      + '</div>'
      + '<div class="runtime-health-stats">'
      + '<div><strong>' + esc(snapshot.sent_count || 0) + '</strong><span>近窗发送</span></div>'
      + '<div><strong>' + esc(snapshot.pending_total || 0) + '</strong><span>pending</span></div>'
      + '<div><strong>' + esc(riskCount) + '</strong><span>风险</span></div>'
      + '</div>'
      + (available ? '' : '<div class="runtime-health-empty runtime-health-wide">health_observer 尚未生成 latest.json</div>')
      + '<div class="runtime-health-grid">'
      + '<div><div class="queue-section-title">主要风险</div>' + renderRiskList(health.risk_reasons) + '</div>'
      + '<div><div class="queue-section-title">异常模块</div>' + renderModuleList(snapshot.module_summary) + '</div>'
      + '<div><div class="queue-section-title">证据入口</div>' + renderEvidence(snapshot.evidence_refs) + '</div>'
      + '</div>';
  }

  function renderRuntimeHealthPanel() {
    const panel = document.getElementById('runtime-health-panel');
    if (!panel) return;
    panel.innerHTML = renderRuntimeHealthContent();
  }

  function renderRuntimeHealthModal() {
    const body = document.getElementById('runtime-health-modal-body');
    if (!body) return;
    body.innerHTML = renderRuntimeHealthContent();
  }

  function openRuntimeHealthModal() {
    renderRuntimeHealthModal();
    const modal = document.getElementById('runtime-health-modal');
    if (modal) modal.classList.add('show');
  }

  function closeRuntimeHealthModal() {
    const modal = document.getElementById('runtime-health-modal');
    if (modal) modal.classList.remove('show');
  }

  window.renderRuntimeHealthPanel = renderRuntimeHealthPanel;
  window.renderRuntimeHealthModal = renderRuntimeHealthModal;

  document.addEventListener('click', function(event) {
    if (event.target.closest('[data-open-runtime-health]')) {
      openRuntimeHealthModal();
      return;
    }
    if (event.target.getAttribute('data-close-modal') === 'runtime-health' || event.target.id === 'runtime-health-modal') {
      closeRuntimeHealthModal();
    }
  });

  if (typeof window.renderAll === 'function' && !window.renderAll._runtimeHealthPanelWrapped) {
    const originalRenderAll = window.renderAll;
    const wrappedRenderAll = function() {
      const result = originalRenderAll.apply(this, arguments);
      renderRuntimeHealthPanel();
      const modal = document.getElementById('runtime-health-modal');
      if (modal && modal.classList.contains('show')) renderRuntimeHealthModal();
      return result;
    };
    wrappedRenderAll._runtimeHealthPanelWrapped = true;
    window.renderAll = wrappedRenderAll;
  }

  renderRuntimeHealthPanel();
})();
