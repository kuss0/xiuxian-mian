function getPendingReplyQueueTasks() {
  if (typeof getIdentities !== 'function') {
    return [];
  }
  return getIdentities().flatMap(function(identity) {
    const tasks = Array.isArray(identity.pending_tasks) ? identity.pending_tasks : [];
    return tasks.map(function(task) {
      return Object.assign({}, task || {}, {
        identity_name: identity.display_name || identity.label || identity.username || identity.send_as_id || '-',
        identity_id: identity.send_as_id || '-',
        queue_type: 'reply'
      });
    });
  }).sort(function(a, b) {
    return String(a.sent_at || '').localeCompare(String(b.sent_at || ''));
  });
}

function getSendLockQueueTasks() {
  const snapshot = window.appState?.snapshot || (typeof appState !== 'undefined' ? appState.snapshot : null);
  const tasks = snapshot && Array.isArray(snapshot.game_send_queue) ? snapshot.game_send_queue : [];
  return tasks.map(function(task) {
    return Object.assign({}, task || {}, {
      identity_name: task.identity_name || task.identity_id || '-',
      queue_type: 'send'
    });
  });
}

function getAllQueueTasks() {
  return {
    send: getSendLockQueueTasks(),
    reply: getPendingReplyQueueTasks()
  };
}

function ensurePendingQueueModal() {
  let modal = document.getElementById('pending-queue-modal');
  if (modal) {
    return modal;
  }
  modal = document.createElement('div');
  modal.id = 'pending-queue-modal';
  modal.className = 'modal-backdrop';
  modal.innerHTML = '<div class="modal-card modal-card-wide queue-modal-card">'
    + '<div class="modal-header">'
    + '<h3 class="modal-title">指令队列</h3>'
    + '<button class="icon-btn" type="button" data-close-pending-queue="1">×</button>'
    + '</div>'
    + '<div id="pending-queue-modal-body"></div>'
    + '</div>';
  document.body.appendChild(modal);
  return modal;
}

function renderPendingQueueButton() {
  const actions = document.querySelector('.topbar-actions');
  const refreshButton = document.querySelector('[data-refresh-now]');
  if (!actions || !refreshButton) {
    return;
  }
  let button = document.getElementById('pending-queue-button');
  if (!button) {
    button = document.createElement('button');
    button.id = 'pending-queue-button';
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.setAttribute('data-open-pending-queue', '1');
    actions.insertBefore(button, refreshButton);
  }
  const queues = getAllQueueTasks();
  const count = queues.send.length + queues.reply.length;
  button.textContent = count > 0 ? ('队列 ' + count) : '队列';
  button.classList.toggle('queue-button-active', count > 0);
}

function renderPendingQueueModalBody() {
  ensurePendingQueueModal();
  const body = document.getElementById('pending-queue-modal-body');
  if (!body) {
    return;
  }
  const queues = getAllQueueTasks();
  if (!queues.send.length && !queues.reply.length) {
    body.innerHTML = '<div class="queue-empty">当前没有等待发送、待回复或待补发指令。</div>';
    return;
  }
  const sendRows = queues.send.length ? queues.send.map(function(task) {
      const status = task.status === 'sending' ? '发送中' : '等锁';
      const readyText = Number(task.ready_in_sec || 0) > 0 ? ('约 ' + String(task.ready_in_sec) + ' 秒后') : '可发送';
      return '<div class="queue-row queue-row-send">'
        + '<span class="queue-kind">发送锁</span>'
        + '<span class="queue-identity">' + escapeHtml(task.identity_name || '-') + '</span>'
        + '<span class="queue-cmd">' + escapeHtml(task.cmd || '-') + '</span>'
        + '<span>' + escapeHtml(status) + '</span>'
        + '<span>' + escapeHtml(task.priority || 'default') + '</span>'
        + '<span>' + escapeHtml(readyText) + '</span>'
        + '<span>' + escapeHtml(task.enqueued_at || '-') + '</span>'
        + '</div>';
    }).join('') : '<div class="queue-empty">当前没有等待全局发送锁的指令。</div>';
  const replyRows = queues.reply.length ? queues.reply.map(function(task) {
      const retryText = String(task.retry || 0) + '/' + String(task.max_retry || 0);
      const priority = task.priority || 'default';
      return '<div class="queue-row queue-row-reply">'
        + '<span class="queue-kind">等回复</span>'
        + '<span class="queue-identity">' + escapeHtml(task.identity_name || '-') + '</span>'
        + '<span class="queue-cmd">' + escapeHtml(task.cmd || '-') + '</span>'
        + '<span>msg ' + escapeHtml(task.msg_id || '-') + '</span>'
        + '<span>重试 ' + escapeHtml(retryText) + '</span>'
        + '<span>' + escapeHtml(priority) + '</span>'
        + '<span>' + escapeHtml(task.sent_at || '-') + '</span>'
        + '</div>';
    }).join('') : '<div class="queue-empty">当前没有已发出但待回复/待补发的指令。</div>';
  body.innerHTML = '<div class="queue-section-title">等待全局发送锁：' + queues.send.length + '</div>'
    + '<div class="queue-list">' + sendRows + '</div>'
    + '<div class="queue-section-title queue-section-title-spaced">待回复/待补发：' + queues.reply.length + '</div>'
    + '<div class="queue-list">' + replyRows + '</div>';
}

function openPendingQueueModal() {
  const modal = ensurePendingQueueModal();
  renderPendingQueueModalBody();
  modal.classList.add('show');
}

function closePendingQueueModal() {
  const modal = document.getElementById('pending-queue-modal');
  if (modal) {
    modal.classList.remove('show');
  }
}

if (typeof renderAll === 'function') {
  const originalRenderAll = renderAll;
  renderAll = function() {
    const result = originalRenderAll.apply(this, arguments);
    renderPendingQueueButton();
    if (document.getElementById('pending-queue-modal')?.classList.contains('show')) {
      renderPendingQueueModalBody();
    }
    return result;
  };
  renderPendingQueueButton();
}

document.addEventListener('click', function(event) {
  if (event.target.closest('[data-open-pending-queue]')) {
    openPendingQueueModal();
    return;
  }
  if (event.target.closest('[data-close-pending-queue]') || event.target.id === 'pending-queue-modal') {
    closePendingQueueModal();
  }
});

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    closePendingQueueModal();
  }
});
