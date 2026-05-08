function renderPendingQueuePanel() {
  const panel = document.getElementById('summary-panel');
  if (!panel || typeof getSelectedIdentity !== 'function') {
    return;
  }
  const identity = getSelectedIdentity();
  if (!identity) {
    return;
  }
  const tasks = Array.isArray(identity.pending_tasks) ? identity.pending_tasks : [];
  const existing = document.getElementById('pending-queue-panel');
  if (existing) {
    existing.remove();
  }
  const taskRows = tasks.length ? tasks.map(function(task) {
    const retryText = String(task.retry || 0) + '/' + String(task.max_retry || 0);
    const priority = task.priority || 'default';
    return '<div class="queue-row">'
      + '<span class="queue-cmd">' + escapeHtml(task.cmd || '-') + '</span>'
      + '<span>msg ' + escapeHtml(task.msg_id || '-') + '</span>'
      + '<span>重试 ' + escapeHtml(retryText) + '</span>'
      + '<span>' + escapeHtml(priority) + '</span>'
      + '<span>' + escapeHtml(task.sent_at || '-') + '</span>'
      + '</div>';
  }).join('') : '<div class="queue-empty">当前没有待回复/待补发指令。</div>';
  const section = document.createElement('section');
  section.id = 'pending-queue-panel';
  section.className = 'card queue-card';
  section.innerHTML = '<div class="summary-head"><h2>指令队列</h2><span class="form-label form-label-inline">'
    + tasks.length + ' 条 pending</span></div><div class="queue-list">' + taskRows + '</div>';
  panel.appendChild(section);
}

if (typeof renderAll === 'function') {
  const originalRenderAll = renderAll;
  renderAll = function() {
    const result = originalRenderAll.apply(this, arguments);
    renderPendingQueuePanel();
    return result;
  };
  renderPendingQueuePanel();
}
