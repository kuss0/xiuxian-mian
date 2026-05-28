function getDungeonSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.dungeon_join || {};
}

function getReplicaSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.replica || {};
}

function getDungeonRows() {
  const dungeon = getDungeonSnapshot();
  return Array.isArray(dungeon.rows) ? dungeon.rows : [];
}

function renderDungeonButton() {
  const button = document.querySelector('[data-open-dungeon]');
  if (!button) {
    return;
  }
  const dungeon = getDungeonSnapshot();
  const enabled = Number(dungeon.enabled_count || 0);
  const total = Number(dungeon.identity_count || 0);
  button.textContent = total > 0 ? ('副本 ' + enabled + '/' + total) : '副本';
  button.classList.toggle('queue-button-active', enabled > 0);
}

function dungeonStatusClass(text) {
  const raw = String(text || '');
  if (raw === '等待回复' || raw === '副本中' || raw === '已加入') {
    return 'dungeon-status-good';
  }
  if (raw === '冷却中' || raw === '冷却' || raw === '通关冷却') {
    return 'dungeon-status-warn';
  }
  if (raw === '失败') {
    return 'dungeon-status-bad';
  }
  return '';
}

function renderLightweightDungeonCommands() {
  const commands = [
    {label: '查询', command: '.查询副本'},
    {label: '开房', command: '.开启副本 @用户名 [虚天|苍坤|坠魔|黄龙]'},
    {label: '加入', command: '.加入副本 @用户名 @用户名'},
    {label: '解散', command: '.解散副本'}
  ];
  return '<div class="dungeon-command-flow">' + commands.map(function(item) {
    return '<div class="dungeon-command-step">'
      + '<span>' + escapeHtml(item.label) + '</span>'
      + '<code>' + escapeHtml(item.command) + '</code>'
      + '</div>';
  }).join('') + '</div>';
}

function renderDungeonAnnouncements(announcements) {
  const items = Array.isArray(announcements) ? announcements.slice(-8).reverse() : [];
  if (!items.length) {
    return '<div class="queue-empty">最近没有副本公告入箱。</div>';
  }
  return '<div class="dungeon-announcement-list">' + items.map(function(item) {
    const dateText = Number(item.date || 0)
      ? new Date(Number(item.date || 0) * 1000).toLocaleTimeString('zh-CN', {hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'})
      : '-';
    return '<div class="dungeon-announcement-row">'
      + '<span>' + escapeHtml(dateText) + '</span>'
      + '<strong>' + escapeHtml(item.dungeon_name || '副本') + ' ' + escapeHtml(item.dungeon_id || '-') + '</strong>'
      + '<code>' + escapeHtml(item.join_command || '-') + '</code>'
      + '<span>msg ' + escapeHtml(item.msg_id || '-') + '</span>'
      + '</div>';
  }).join('') + '</div>';
}

function renderReplicaAccountSelect(groupId, accountOptions, selectedAccountId, listenerAttr) {
  listenerAttr = listenerAttr || 'data-replica-listener';
  const selectedId = Number(selectedAccountId || 0);
  const options = ['<option value="">未设置</option>'].concat((accountOptions || []).map(function(account) {
    const value = Number(account.account_id || 0);
    const selected = value === selectedId ? ' selected' : '';
    const suffix = account.offline ? ' 离线' : '';
    return '<option value="' + escapeHtml(value) + '"' + selected + '>'
      + escapeHtml((account.label || value) + ' ｜ ' + value + suffix)
      + '</option>';
  }));
  return '<select class="text-input" ' + listenerAttr + '="' + escapeHtml(groupId) + '">' + options.join('') + '</select>';
}

function renderReplicaGroupRows(replica, options) {
  options = options || {};
  const groupIds = Array.isArray(options.groupIds) ? options.groupIds : (Array.isArray(replica.group_ids) ? replica.group_ids : []);
  const accountOptions = Array.isArray(replica.account_options) ? replica.account_options : [];
  const listenerMap = options.listenerMap || replica.listener_account_map || {};
  const listenerAttr = options.listenerAttr || 'data-replica-listener';
  const emptyText = options.emptyText || '暂无副本群。';
  if (!groupIds.length) {
    return '<div class="queue-empty">' + escapeHtml(emptyText) + '</div>';
  }
  return '<div class="replica-group-list">' + groupIds.map(function(groupId) {
    const key = String(groupId);
    return '<div class="replica-group-row">'
      + '<code>' + escapeHtml(key) + '</code>'
      + renderReplicaAccountSelect(key, accountOptions, listenerMap[key], listenerAttr)
      + '</div>';
  }).join('') + '</div>';
}

function renderReplicaParticipantRows(replica, options) {
  options = options || {};
  const identities = Array.isArray(replica.identity_options) ? replica.identity_options : [];
  const sourceIds = Array.isArray(options.participantIds) ? options.participantIds : (replica.participant_identity_ids || []);
  const participantIds = new Set(sourceIds.map(function(id) { return Number(id); }));
  const participantAttr = options.participantAttr || 'data-replica-participant';
  if (!identities.length) {
    return '<div class="queue-empty">暂无身份。</div>';
  }
  return '<div class="replica-identity-list">' + identities.map(function(identity) {
    const identityId = Number(identity.identity_id || 0);
    const participantChecked = participantIds.has(identityId) ? ' checked' : '';
    const disabledText = identity.identity_enabled ? '' : ' ｜ 身份暂停';
    const username = identity.username ? ('@' + String(identity.username).replace(/^@/, '')) : '未设置 username';
    const goldAllowed = !!identity.gold_dps_allowed;
    const goldChecked = identity.gold_dps_enabled ? ' checked' : '';
    const goldDisabled = goldAllowed ? '' : ' disabled';
    const ticketSummary = identity.ticket_summary || '无票';
    const ticketClass = identity.can_open ? 'replica-ticket-summary' : 'replica-ticket-summary replica-ticket-empty';
    const openLabel = identity.preferred_open_label ? ('可开 ' + identity.preferred_open_label) : '不可开房';
    return '<div class="replica-identity-row' + (identity.can_open ? ' replica-identity-can-open' : '') + '">'
      + '<label class="checkbox-inline checkbox-inline-small">'
      + '<input type="checkbox" ' + participantAttr + '="' + escapeHtml(identityId) + '"' + participantChecked + ' />'
      + '<span class="replica-identity-main">'
      + '<span class="replica-identity-title"><strong>' + escapeHtml(identity.display_name || identityId) + '</strong><em>' + escapeHtml(username) + '</em></span>'
      + '<small>' + escapeHtml((identity.realm || '未获取') + ' ｜ ' + (identity.spiritual_root_attrs || '灵根未获取') + ' ｜ ' + (identity.replica_professions || '未匹配') + disabledText) + '</small>'
      + '</span>'
      + '</label>'
      + '<div class="replica-identity-side">'
      + '<span class="' + ticketClass + '" title="' + escapeHtml(openLabel) + '">' + escapeHtml(ticketSummary) + '</span>'
      + '<label class="checkbox-inline checkbox-inline-small replica-gold-toggle">'
      + '<input type="checkbox" data-replica-gold-dps="' + escapeHtml(identityId) + '"' + goldChecked + goldDisabled + ' /> 金/雷 DPS'
      + '</label>'
      + '</div>'
      + '</div>';
  }).join('') + '</div>';
}

function renderReplicaOpeners(replica) {
  const identities = Array.isArray(replica.identity_options) ? replica.identity_options : [];
  const participantIds = new Set((replica.participant_identity_ids || []).map(function(id) { return Number(id); }));
  const openers = identities.filter(function(identity) {
    return participantIds.has(Number(identity.identity_id || 0)) && identity.can_open;
  });
  if (!openers.length) {
    return '<div class="queue-empty">当前参与身份没有记录到副本门票。</div>';
  }
  return '<div class="replica-opener-list">' + openers.map(function(identity) {
    const username = identity.username ? ('@' + String(identity.username).replace(/^@/, '')) : '';
    const selector = username || String(identity.identity_id || '');
    return '<div class="replica-opener-row">'
      + '<div><strong>' + escapeHtml(identity.display_name || identity.identity_id || '-') + '</strong><span>' + escapeHtml(username || ('ID ' + selector)) + '</span></div>'
      + '<span class="replica-ticket-summary">' + escapeHtml(identity.ticket_summary || '-') + '</span>'
      + '<span>' + escapeHtml(identity.preferred_open_label || '-') + '</span>'
      + '<code>' + escapeHtml('.开启副本 ' + selector) + '</code>'
      + '</div>';
  }).join('') + '</div>';
}

function renderReplicaConfig(replica) {
  const groupIds = Array.isArray(replica.group_ids) ? replica.group_ids : [];
  const dispatchGroupIds = Array.isArray(replica.dispatch_group_ids) ? replica.dispatch_group_ids : [];
  const aggregator = replica.query_aggregator_config || {};
  const secretPlaceholder = aggregator.secret_configured ? '已配置，留空保留' : '未配置';
  return '<form id="replica-config-form" class="replica-config-form">'
    + '<div class="replica-config-grid">'
    + '<label class="form-label">副本群 ID<textarea class="text-input replica-groups-input" name="replica_group_ids" rows="4">' + escapeHtml(groupIds.join('\n')) + '</textarea></label>'
    + '<div class="replica-config-side">'
    + '<div class="queue-section-title">监听账号</div>'
    + renderReplicaGroupRows(replica)
    + '</div>'
    + '</div>'
    + '<div class="replica-config-grid">'
    + '<label class="form-label">主线拉人群 ID<textarea class="text-input replica-groups-input" name="replica_dispatch_group_ids" rows="3">' + escapeHtml(dispatchGroupIds.join('\n')) + '</textarea></label>'
    + '<div class="replica-config-side">'
    + '<div class="queue-section-title">拉人群监听账号</div>'
    + renderReplicaGroupRows(replica, {
      groupIds: dispatchGroupIds,
      listenerMap: replica.dispatch_listener_account_map || {},
      listenerAttr: 'data-replica-dispatch-listener',
      emptyText: '暂无主线拉人群。'
    })
    + '</div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">外部汇聚接入' + (aggregator.configured ? '（已配置）' : '（未配置）') + '</div>'
    + '<div class="replica-config-grid">'
    + '<label class="form-label">Base URL<input class="text-input" name="replica_query_aggregator_base_url" value="' + escapeHtml(aggregator.base_url || '') + '" autocomplete="off" /></label>'
    + '<label class="form-label">Client ID<input class="text-input" name="replica_query_aggregator_client_id" value="' + escapeHtml(aggregator.client_id || '') + '" autocomplete="off" /></label>'
    + '<label class="form-label">Secret<input class="text-input" name="replica_query_aggregator_secret" type="password" placeholder="' + escapeHtml(secretPlaceholder) + '" autocomplete="new-password" /></label>'
    + '</div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">本地参与身份</div>'
    + renderReplicaParticipantRows(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">主线参与身份</div>'
    + renderReplicaParticipantRows(replica, {
      participantIds: replica.dispatch_participant_identity_ids || [],
      participantAttr: 'data-replica-dispatch-participant'
    })
    + '</div>'
    + '<div class="modal-actions modal-actions-inline">'
    + '<button type="submit" class="btn">保存副本群配置</button>'
    + '</div>'
    + '</form>';
}

function renderDungeonRows(rows) {
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    return '<div class="queue-empty">暂无身份。</div>';
  }
  return '<div class="dungeon-row-list">' + items.map(function(row) {
    const moduleEnabled = !!row.module_enabled;
    const nextEnabled = moduleEnabled ? 0 : 1;
    const statusText = row.status_text || '空闲';
    const room = row.pending_room_id || row.room_id || '-';
    const until = row.pending_until !== '-' ? row.pending_until : (row.cooldown_until !== '-' ? row.cooldown_until : (row.active_until !== '-' ? row.active_until : '-'));
    const error = row.last_error ? (' ｜ ' + row.last_error) : '';
    const disabledText = row.identity_enabled ? '' : ' ｜ 身份暂停';
    return '<div class="dungeon-row">'
      + '<div class="dungeon-row-main">'
      + '<strong>' + escapeHtml(row.display_name || row.identity_id || '-') + '</strong>'
      + '<span>房间：' + escapeHtml(room) + ' ｜ 更新时间：' + escapeHtml(row.updated_at || '-') + error + disabledText + '</span>'
      + '</div>'
      + '<span class="dungeon-status ' + dungeonStatusClass(statusText) + '">' + escapeHtml(statusText) + '</span>'
      + '<span class="dungeon-until">' + escapeHtml(until || '-') + '</span>'
      + '<button type="button" class="btn btn-secondary btn-compact" data-toggle-dungeon-identity="' + escapeHtml(row.identity_id) + '" data-enabled="' + nextEnabled + '">'
      + (moduleEnabled ? '关闭' : '开启')
      + '</button>'
      + '</div>';
  }).join('') + '</div>';
}

function renderDungeonModal() {
  const body = document.getElementById('dungeon-modal-body');
  if (!body) {
    return;
  }
  const dungeon = getDungeonSnapshot();
  const replica = getReplicaSnapshot();
  const rows = getDungeonRows();
  const pendingCount = rows.filter(function(row) { return row && row.status_text === '等待回复'; }).length;
  const activeCount = rows.filter(function(row) { return row && (row.status_text === '副本中' || row.status_text === '已加入'); }).length;
  const cooldownCount = rows.filter(function(row) {
    const status = row && row.status_text;
    return status === '冷却中' || status === '冷却' || status === '通关冷却';
  }).length;
  body.innerHTML = ''
    + '<div class="dungeon-stats">'
    + '<div><strong>' + escapeHtml(dungeon.enabled_count || 0) + '</strong><span>自动加入开启</span></div>'
    + '<div><strong>' + escapeHtml(pendingCount + activeCount) + '</strong><span>进行中/待回复</span></div>'
    + '<div><strong>' + escapeHtml(cooldownCount) + '</strong><span>冷却中</span></div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">轻量调度指令</div>'
    + renderLightweightDungeonCommands()
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">谁能开房</div>'
    + renderReplicaOpeners(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">副本群配置</div>'
    + renderReplicaConfig(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">自动加入状态</div>'
    + renderDungeonRows(rows)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">最近公告</div>'
    + renderDungeonAnnouncements(dungeon.recent_announcements)
    + '</div>';
}

function openDungeonModal() {
  renderDungeonModal();
  const modal = document.getElementById('dungeon-modal');
  if (modal) {
    modal.classList.add('show');
  }
}

function closeDungeonModal() {
  const modal = document.getElementById('dungeon-modal');
  if (modal) {
    modal.classList.remove('show');
  }
}

async function toggleDungeonIdentity(identityId, enabled) {
  try {
    const data = await postJson('/api/toggle', {
      send_as_id: identityId,
      module: '自动副本',
      enabled: !!enabled
    });
    updateFlash(data.message || '已更新自动副本', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else if (typeof refreshState === 'function') {
      await refreshState({silent: true, keepFlash: true});
    }
    renderDungeonModal();
  } catch (error) {
    updateFlash((error && error.message) || '自动副本切换失败', true);
    if (typeof setFlash === 'function') {
      setFlash();
    }
  }
}

function collectReplicaConfigPayload() {
  const form = document.getElementById('replica-config-form');
  if (!form) {
    return {};
  }
  const groupText = form.querySelector('textarea[name="replica_group_ids"]')?.value || '';
  const groupIds = groupText.split(/[\s,，]+/).map(function(value) { return value.trim(); }).filter(Boolean);
  const dispatchGroupText = form.querySelector('textarea[name="replica_dispatch_group_ids"]')?.value || '';
  const dispatchGroupIds = dispatchGroupText.split(/[\s,，]+/).map(function(value) { return value.trim(); }).filter(Boolean);
  const listenerMap = {};
  form.querySelectorAll('[data-replica-listener]').forEach(function(select) {
    const groupId = String(select.getAttribute('data-replica-listener') || '').trim();
    if (groupId && select.value) {
      listenerMap[groupId] = select.value;
    }
  });
  const dispatchListenerMap = {};
  form.querySelectorAll('[data-replica-dispatch-listener]').forEach(function(select) {
    const groupId = String(select.getAttribute('data-replica-dispatch-listener') || '').trim();
    if (groupId && select.value) {
      dispatchListenerMap[groupId] = select.value;
    }
  });
  const queryAggregatorConfig = {
    base_url: (form.querySelector('input[name="replica_query_aggregator_base_url"]')?.value || '').trim(),
    client_id: (form.querySelector('input[name="replica_query_aggregator_client_id"]')?.value || '').trim(),
    secret: (form.querySelector('input[name="replica_query_aggregator_secret"]')?.value || '').trim()
  };
  const participantIds = Array.from(form.querySelectorAll('[data-replica-participant]:checked')).map(function(input) {
    return input.getAttribute('data-replica-participant');
  });
  const dispatchParticipantIds = Array.from(form.querySelectorAll('[data-replica-dispatch-participant]:checked')).map(function(input) {
    return input.getAttribute('data-replica-dispatch-participant');
  });
  return {
    group_ids: groupIds,
    listener_account_map: listenerMap,
    dispatch_group_ids: dispatchGroupIds,
    dispatch_listener_account_map: dispatchListenerMap,
    query_aggregator_config: queryAggregatorConfig,
    participant_identity_ids: participantIds,
    dispatch_participant_identity_ids: dispatchParticipantIds,
    virtual_hall_match_enabled_map: {}
  };
}

async function saveReplicaConfig(event) {
  event.preventDefault();
  try {
    const data = await postJson('/api/replica-config', collectReplicaConfigPayload());
    updateFlash(data.message || '已更新副本群配置', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else if (typeof refreshState === 'function') {
      await refreshState({silent: true, keepFlash: true});
    }
    renderDungeonModal();
  } catch (error) {
    updateFlash((error && error.message) || '副本群配置保存失败', true);
    if (typeof setFlash === 'function') {
      setFlash();
    }
  }
}

async function toggleReplicaGoldDps(identityId, enabled) {
  try {
    const data = await postJson('/api/replica-gold-dps-toggle', {
      send_as_id: identityId,
      enabled: !!enabled
    });
    updateFlash(data.message || '已更新金/雷 DPS', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else if (typeof refreshState === 'function') {
      await refreshState({silent: true, keepFlash: true});
    }
    renderDungeonModal();
  } catch (error) {
    updateFlash((error && error.message) || '金/雷 DPS 切换失败', true);
    if (typeof setFlash === 'function') {
      setFlash();
    }
    renderDungeonModal();
  }
}

if (typeof renderAll === 'function') {
  const originalRenderAllForDungeon = renderAll;
  renderAll = function() {
    const result = originalRenderAllForDungeon.apply(this, arguments);
    renderDungeonButton();
    if (document.getElementById('dungeon-modal')?.classList.contains('show')) {
      renderDungeonModal();
    }
    return result;
  };
  renderDungeonButton();
}

document.addEventListener('click', function(event) {
  if (event.target.closest('[data-open-dungeon]')) {
    openDungeonModal();
    return;
  }
  const toggleButton = event.target.closest('[data-toggle-dungeon-identity]');
  if (toggleButton) {
    toggleDungeonIdentity(toggleButton.getAttribute('data-toggle-dungeon-identity'), toggleButton.getAttribute('data-enabled') === '1');
    return;
  }
  const goldDpsInput = event.target.closest('[data-replica-gold-dps]');
  if (goldDpsInput) {
    toggleReplicaGoldDps(goldDpsInput.getAttribute('data-replica-gold-dps'), goldDpsInput.checked);
    return;
  }
  if (event.target.getAttribute('data-close-modal') === 'dungeon' || event.target.id === 'dungeon-modal') {
    closeDungeonModal();
  }
});

document.addEventListener('submit', function(event) {
  if (event.target && event.target.id === 'replica-config-form') {
    saveReplicaConfig(event);
  }
});

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    closeDungeonModal();
  }
});
