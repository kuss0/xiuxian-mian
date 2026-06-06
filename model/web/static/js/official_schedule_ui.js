function getOfficialScheduleSnapshot() {
  return window.appState?.snapshot || (typeof appState !== 'undefined' ? appState.snapshot : null) || {};
}

function getOfficialScheduleIdentities() {
  if (typeof getIdentities === 'function') {
    return getIdentities();
  }
  const snapshot = getOfficialScheduleSnapshot();
  return Array.isArray(snapshot.identities) ? snapshot.identities : [];
}

function getOfficialScheduleSelectedIdentity() {
  if (typeof getSelectedIdentity === 'function') {
    return getSelectedIdentity();
  }
  const identities = getOfficialScheduleIdentities();
  const active = document.querySelector('.identity-item-active[data-select-identity]');
  const mobile = document.getElementById('identity-select-mobile');
  const selectedId = Number((active && active.getAttribute('data-select-identity')) || (mobile && mobile.value) || new URLSearchParams(location.search).get('send_as_id') || 0);
  return identities.find(function(identity) { return Number(identity.send_as_id) === selectedId; }) || identities[0] || null;
}

function ensureOfficialScheduleModal() {
  let modal = document.getElementById('official-schedule-modal');
  if (modal) {
    return modal;
  }
  modal = document.createElement('div');
  modal.id = 'official-schedule-modal';
  modal.className = 'modal-backdrop';
  modal.innerHTML = '<div class="modal-card modal-card-wide official-schedule-modal-card">'
    + '<div class="modal-header">'
    + '<h3 class="modal-title">官方定时排班器</h3>'
    + '<button class="icon-btn" type="button" data-close-official-schedule="1">×</button>'
    + '</div>'
    + '<div class="official-schedule-grid">'
    + '<form id="official-schedule-form" class="official-schedule-form">'
    + '<label class="field-label">身份<select class="text-input" name="send_as_id"></select></label>'
    + '<label class="field-label">预设<select class="text-input" name="template_key">'
    + '<option value="deep_retreat">深度闭关：仅 .深度闭关</option>'
    + '<option value="pet_touch">抚摸法宝</option>'
    + '<option value="pet_warm">温养器灵</option>'
    + '<option value="pet_trial">器灵试炼</option>'
    + '</select></label>'
    + '<label class="field-label">法宝名<input class="text-input" name="pet_name" placeholder="留空使用该身份已配置名称" /></label>'
    + '<label class="field-label">排班天数<input class="text-input" name="horizon_days" type="number" min="1" max="7" value="3" /></label>'
    + '<label class="field-label">锚点时间<input class="text-input" name="anchor_at_text" type="datetime-local" /></label>'
    + '<div class="form-label">锚点留空时，后端会优先按该身份已有 CD 推断；当前阶段先预览和准备，不会自动替代原模块。</div>'
    + '<div class="official-schedule-actions">'
    + '<button type="button" class="btn btn-secondary" data-official-schedule-preview="1">预览</button>'
    + '<button type="button" class="btn" data-official-schedule-prepare="1">准备记录</button>'
    + '</div>'
    + '</form>'
    + '<div class="official-schedule-side">'
    + '<div class="queue-section-title">排班预览</div>'
    + '<div id="official-schedule-preview" class="official-schedule-preview queue-empty">尚未生成预览。</div>'
    + '</div>'
    + '</div>'
    + '<div class="queue-section-title queue-section-title-spaced">本地排班记录</div>'
    + '<div id="official-schedule-list" class="official-schedule-list"></div>'
    + '</div>';
  document.body.appendChild(modal);
  document.getElementById('official-schedule-form').addEventListener('submit', function(event) {
    event.preventDefault();
    previewOfficialSchedule();
  });
  return modal;
}

function renderOfficialScheduleButton() {
  const actions = document.querySelector('.topbar-actions');
  const refreshButton = document.querySelector('[data-refresh-now]');
  if (!actions || !refreshButton) {
    return;
  }
  let button = document.getElementById('official-schedule-button');
  if (!button) {
    button = document.createElement('button');
    button.id = 'official-schedule-button';
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.setAttribute('data-open-official-schedule', '1');
    actions.insertBefore(button, refreshButton);
  }
  const schedules = getOfficialScheduleSnapshot().official_schedules || [];
  const activeCount = Array.isArray(schedules) ? schedules.filter(function(item) {
    return item && !['deleted', 'failed'].includes(String(item.status || ''));
  }).length : 0;
  button.textContent = activeCount > 0 ? ('官方定时 ' + activeCount) : '官方定时';
}

function fillOfficialScheduleForm() {
  const modal = ensureOfficialScheduleModal();
  const form = modal.querySelector('#official-schedule-form');
  const select = form.querySelector('select[name="send_as_id"]');
  const identities = getOfficialScheduleIdentities();
  const selected = getOfficialScheduleSelectedIdentity();
  select.innerHTML = identities.map(function(identity) {
    const selectedAttr = selected && Number(selected.send_as_id) === Number(identity.send_as_id) ? ' selected' : '';
    return '<option value="' + escapeHtml(identity.send_as_id) + '"' + selectedAttr + '>' + escapeHtml(identity.display_name || identity.send_as_id) + '</option>';
  }).join('');
}

function getOfficialSchedulePayload() {
  const form = document.getElementById('official-schedule-form');
  const anchorText = form.querySelector('input[name="anchor_at_text"]').value;
  const payload = {
    send_as_id: form.querySelector('select[name="send_as_id"]').value,
    template_key: form.querySelector('select[name="template_key"]').value,
    pet_name: form.querySelector('input[name="pet_name"]').value,
    horizon_days: form.querySelector('input[name="horizon_days"]').value
  };
  if (anchorText) {
    const parsed = new Date(anchorText);
    if (!Number.isNaN(parsed.getTime())) {
      payload.anchor_at = Math.floor(parsed.getTime() / 1000);
    }
  }
  return payload;
}

function renderOfficialSchedulePreview(plan) {
  const box = document.getElementById('official-schedule-preview');
  if (!box) {
    return;
  }
  if (!plan || !Array.isArray(plan.items) || !plan.items.length) {
    box.className = 'official-schedule-preview queue-empty';
    box.innerHTML = '没有可展示的排班。';
    return;
  }
  box.className = 'official-schedule-preview';
  const rows = plan.items.slice(0, 80).map(function(item, index) {
    return '<div class="official-schedule-row">'
      + '<span>' + String(index + 1) + '</span>'
      + '<span class="official-schedule-time">' + escapeHtml(item.schedule_text || '-') + '</span>'
      + '<span class="official-schedule-command">' + escapeHtml(item.command || '-') + '</span>'
      + '</div>';
  }).join('');
  box.innerHTML = '<div class="official-schedule-meta">预设：' + escapeHtml(plan.template_label || plan.template_key || '-')
    + ' ｜ 锚点：' + escapeHtml(plan.anchor_text || '-')
    + ' ｜ 共 ' + String(plan.items.length) + ' 条</div>'
    + '<div class="official-schedule-list">' + rows + '</div>';
}

function renderOfficialScheduleList() {
  const box = document.getElementById('official-schedule-list');
  if (!box) {
    return;
  }
  const snapshot = getOfficialScheduleSnapshot();
  const schedules = Array.isArray(snapshot.official_schedules) ? snapshot.official_schedules : [];
  if (!schedules.length) {
    box.innerHTML = '<div class="queue-empty">暂无本地排班记录。</div>';
    return;
  }
  const groups = new Map();
  schedules.slice(0, 160).forEach(function(item) {
    const key = String(item.batch_id || item.id || '0');
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(item);
  });
  box.innerHTML = Array.from(groups.entries()).map(function(entry) {
    const batchId = entry[0];
    const items = entry[1];
    const first = items[0] || {};
    const plannedCount = items.filter(function(item) { return ['planned', 'failed'].includes(String(item.status || '')); }).length;
    const canCreate = Number(batchId || 0) > 0 && plannedCount > 0;
    const header = '<div class="official-schedule-batch-head">'
      + '<div><strong>' + escapeHtml(first.template_label || first.template_key || '排班') + '</strong>'
      + '<span>批次 ' + escapeHtml(batchId) + ' ｜ ' + String(items.length) + ' 条 ｜ ' + escapeHtml(first.batch_status || '-') + '</span></div>'
      + '<div class="official-schedule-actions">'
      + (canCreate ? '<button type="button" class="btn btn-secondary btn-compact" data-create-official-schedule-batch="' + escapeHtml(batchId) + '">创建官方定时</button>' : '')
      + '<button type="button" class="btn btn-secondary btn-compact" data-delete-official-schedule-batch="' + escapeHtml(batchId) + '">删除批次</button>'
      + '</div>'
      + '</div>';
    const rows = items.map(function(item) {
      const officialText = Number(item.scheduled_msg_id || 0) > 0 ? ('官方ID ' + item.scheduled_msg_id) : '未创建官方定时';
      return '<div class="official-schedule-row official-schedule-record">'
        + '<span class="official-schedule-status">' + escapeHtml(item.status || '-') + '</span>'
        + '<span class="official-schedule-time">' + escapeHtml(item.schedule_text || '-') + '</span>'
        + '<span class="official-schedule-command">' + escapeHtml(item.command || '-') + '</span>'
        + '<span>' + escapeHtml(officialText) + '</span>'
        + '<button type="button" class="btn btn-secondary btn-compact" data-delete-official-schedule-record="' + escapeHtml(item.id) + '">删除</button>'
        + '</div>';
    }).join('');
    return '<div class="official-schedule-batch">' + header + '<div class="official-schedule-list">' + rows + '</div></div>';
  }).join('');
}

async function previewOfficialSchedule() {
  try {
    const data = await postJson('/api/official-schedule-preview', getOfficialSchedulePayload());
    renderOfficialSchedulePreview(data.plan);
    updateFlash(data.message || '已生成官方定时预览', false);
  } catch (error) {
    updateFlash((error && error.message) || '官方定时预览失败', true);
  }
  if (typeof setFlash === 'function') {
    setFlash();
  }
}

async function prepareOfficialSchedule() {
  try {
    const data = await postJson('/api/official-schedule-prepare', getOfficialSchedulePayload());
    renderOfficialSchedulePreview(data.plan);
    updateFlash(data.message || '已准备官方定时排班', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else {
      await refreshOfficialScheduleState();
    }
  } catch (error) {
    updateFlash((error && error.message) || '官方定时准备失败', true);
  }
  if (typeof setFlash === 'function') {
    setFlash();
  }
}

async function deleteOfficialScheduleRecord(recordId) {
  if (!window.confirm('确认删除这条本地排班记录？如已创建官方定时，会同时尝试删除官方定时消息。')) {
    return;
  }
  try {
    const data = await postJson('/api/official-schedule-delete', {record_ids: [Number(recordId)], delete_official: true});
    updateFlash(data.message || '已删除官方定时记录', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else {
      await refreshOfficialScheduleState();
    }
  } catch (error) {
    updateFlash((error && error.message) || '删除官方定时失败', true);
  }
  if (typeof setFlash === 'function') {
    setFlash();
  }
}

async function deleteOfficialScheduleBatch(batchId) {
  if (!window.confirm('确认删除这个排班批次？如已创建官方定时，会同时尝试删除官方定时消息。')) {
    return;
  }
  try {
    const data = await postJson('/api/official-schedule-delete', {batch_id: Number(batchId), delete_official: true});
    updateFlash(data.message || '已删除官方定时批次', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else {
      await refreshOfficialScheduleState();
    }
  } catch (error) {
    updateFlash((error && error.message) || '删除官方定时批次失败', true);
  }
  if (typeof setFlash === 'function') {
    setFlash();
  }
}

async function createOfficialScheduleBatch(batchId) {
  if (!window.confirm('确认把这个批次创建为 Telegram 官方定时消息？创建后会出现在 Telegram 的定时消息列表里。')) {
    return;
  }
  try {
    const data = await postJson('/api/official-schedule-create', {
      batch_id: Number(batchId),
      confirm: 'CREATE_OFFICIAL_SCHEDULE'
    });
    updateFlash(data.message || '已创建官方定时消息', !data.ok);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else {
      await refreshOfficialScheduleState();
    }
  } catch (error) {
    updateFlash((error && error.message) || '创建官方定时失败', true);
  }
  if (typeof setFlash === 'function') {
    setFlash();
  }
}

async function refreshOfficialScheduleState() {
  if (typeof refreshState === 'function') {
    await refreshState({silent: true, keepFlash: true});
  }
  renderOfficialScheduleButton();
  renderOfficialScheduleList();
}

function openOfficialScheduleModal() {
  const modal = ensureOfficialScheduleModal();
  fillOfficialScheduleForm();
  renderOfficialScheduleList();
  modal.classList.add('show');
}

function closeOfficialScheduleModal() {
  const modal = document.getElementById('official-schedule-modal');
  if (modal) {
    modal.classList.remove('show');
  }
}

if (typeof renderAll === 'function') {
  const originalOfficialScheduleRenderAll = renderAll;
  renderAll = function() {
    const result = originalOfficialScheduleRenderAll.apply(this, arguments);
    renderOfficialScheduleButton();
    if (document.getElementById('official-schedule-modal')?.classList.contains('show')) {
      fillOfficialScheduleForm();
      renderOfficialScheduleList();
    }
    return result;
  };
  renderOfficialScheduleButton();
}

document.addEventListener('click', function(event) {
  if (event.target.closest('[data-open-official-schedule]')) {
    openOfficialScheduleModal();
    return;
  }
  if (event.target.closest('[data-close-official-schedule]') || event.target.id === 'official-schedule-modal') {
    closeOfficialScheduleModal();
    return;
  }
  if (event.target.closest('[data-official-schedule-preview]')) {
    previewOfficialSchedule();
    return;
  }
  if (event.target.closest('[data-official-schedule-prepare]')) {
    prepareOfficialSchedule();
    return;
  }
  const deleteButton = event.target.closest('[data-delete-official-schedule-record]');
  if (deleteButton) {
    deleteOfficialScheduleRecord(deleteButton.getAttribute('data-delete-official-schedule-record'));
    return;
  }
  const deleteBatchButton = event.target.closest('[data-delete-official-schedule-batch]');
  if (deleteBatchButton) {
    deleteOfficialScheduleBatch(deleteBatchButton.getAttribute('data-delete-official-schedule-batch'));
    return;
  }
  const createBatchButton = event.target.closest('[data-create-official-schedule-batch]');
  if (createBatchButton) {
    createOfficialScheduleBatch(createBatchButton.getAttribute('data-create-official-schedule-batch'));
  }
});

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    closeOfficialScheduleModal();
  }
});
