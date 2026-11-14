function getDungeonSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.dungeon_join || {};
}

function getReplicaSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.replica || {};
}

let lastDungeonModalRenderKey = '';

function getDungeonModalRenderKey() {
  try {
    return JSON.stringify({
      dungeon: getDungeonSnapshot(),
      replica: getReplicaSnapshot()
    });
  } catch (error) {
    return String(Date.now());
  }
}

function isDungeonModalOpen() {
  return !!document.getElementById('dungeon-modal')?.classList.contains('show');
}

function isReplicaConfigEditing() {
  const form = document.getElementById('replica-config-form');
  const expeditions = document.getElementById('expedition-config-form');
  const active = document.activeElement;
  if (expeditions && (expeditions.dataset.dirty === '1' || (active && expeditions.contains(active)))) return true;
  return !!(form && active && form.contains(active) && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName || ''));
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
    {label: '开房', command: '.开启副本 @用户名 <虚天|苍坤|坠魔|黄龙|昆吾>'},
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

function renderReplicaKindManualConfig(replica) {
  const identities = Array.isArray(replica.identity_options) ? replica.identity_options : [];
  const kindConfigs = replica.kind_configs || {};
  const orderedKinds = ['virtual_hall', 'zhuimo', 'huanglong', 'cangkun', 'kunwu', 'luoyun'];
  const fallbackText = '未单独勾选时沿用全局名单';
  if (!identities.length) {
    return '<div class="queue-empty">暂无身份。</div>';
  }
  return '<div class="replica-kind-config-list">' + orderedKinds.map(function(kind) {
    const config = kindConfigs[kind] || {};
    const enabled = config.enabled !== false;
    const localIds = new Set((config.participant_identity_ids || []).map(function(id) { return Number(id); }));
    const dispatchIds = new Set((config.dispatch_participant_identity_ids || []).map(function(id) { return Number(id); }));
    const name = config.name || kind;
    const summary = name + ' ｜ 推荐/开房 ' + localIds.size + ' ｜ 主线 ' + dispatchIds.size;
    const renderIdentityChecks = function(attr, selectedIds) {
      return '<div class="replica-kind-checks">' + identities.map(function(identity) {
        const identityId = Number(identity.identity_id || 0);
        const username = identity.username ? ('@' + String(identity.username).replace(/^@/, '')) : ('ID ' + identityId);
        const checked = selectedIds.has(identityId) ? ' checked' : '';
        const disabledText = identity.identity_enabled ? '' : '（停）';
        return '<label class="checkbox-inline checkbox-inline-small replica-kind-check">'
          + '<input type="checkbox" ' + attr + '="' + escapeHtml(kind) + '" value="' + escapeHtml(identityId) + '"' + checked + ' />'
          + '<span>' + escapeHtml((identity.display_name || identityId) + ' ' + username + disabledText) + '</span>'
          + '</label>';
      }).join('') + '</div>';
    };
    return '<details class="replica-kind-config">'
      + '<summary>'
      + '<strong>' + escapeHtml(summary) + '</strong>'
      + '<span>' + escapeHtml(fallbackText) + '</span>'
      + '</summary>'
      + '<div class="replica-kind-config-body">'
      + '<div><div class="queue-section-title">推荐/开房名单</div>' + renderIdentityChecks('data-replica-kind-participant', localIds) + '</div>'
      + '<div><div class="queue-section-title">主线拉人名单</div>' + renderIdentityChecks('data-replica-kind-dispatch-participant', dispatchIds) + '</div>'
      + '</div>'
      + '</details>';
  }).join('') + '</div>';
}

function renderReplicaKindEnableGrid(replica) {
  const kindConfigs = replica.kind_configs || {};
  const orderedKinds = ['virtual_hall', 'zhuimo', 'huanglong', 'cangkun', 'kunwu', 'luoyun'];
  return '<div class="replica-kind-enable-grid">' + orderedKinds.map(function(kind) {
    const config = kindConfigs[kind] || {};
    const enabled = config.enabled !== false;
    const name = config.name || kind;
    const short = config.short ? (' ' + config.short) : '';
    return '<label class="replica-kind-enable-item' + (enabled ? ' replica-kind-enable-on' : '') + '">'
      + '<input type="checkbox" data-replica-kind-enabled="' + escapeHtml(kind) + '"' + (enabled ? ' checked' : '') + ' />'
      + '<span><strong>' + escapeHtml(name) + '</strong><em>' + escapeHtml(short) + '</em></span>'
      + '</label>';
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
    let openCommands = Array.isArray(identity.open_commands) ? identity.open_commands : [];
    if (!openCommands.length && identity.preferred_open_label && identity.preferred_open_kind) {
      const shortMap = {virtual_hall: '虚', cangkun: '苍', zhuimo: '坠', huanglong: '黄'};
      const short = shortMap[identity.preferred_open_kind] || identity.preferred_open_label;
      openCommands = [{label: identity.preferred_open_label, command: '.开启副本 ' + selector + ' ' + short}];
    }
    const commandHtml = openCommands.length
      ? '<span class="replica-opener-commands">' + openCommands.map(function(item) {
        const label = item.label ? (String(item.label) + ' ') : '';
        return '<code title="' + escapeHtml(label + '开房') + '">' + escapeHtml(item.command || '-') + '</code>';
      }).join('') + '</span>'
      : '<span>无可用开房命令</span>';
    return '<div class="replica-opener-row">'
      + '<div><strong>' + escapeHtml(identity.display_name || identity.identity_id || '-') + '</strong><span>' + escapeHtml(username || ('ID ' + selector)) + '</span></div>'
      + '<span class="replica-ticket-summary">' + escapeHtml(identity.ticket_summary || '-') + '</span>'
      + '<span>' + escapeHtml(identity.preferred_open_label || '-') + '</span>'
      + commandHtml
      + '</div>';
  }).join('') + '</div>';
}

function renderReplicaConfig(replica) {
  const groupIds = Array.isArray(replica.group_ids) ? replica.group_ids : [];
  const dispatchGroupIds = Array.isArray(replica.dispatch_group_ids) ? replica.dispatch_group_ids : [];
  const dispatchEnabled = !!replica.dispatch_enabled;
  const aggregator = replica.query_aggregator_config || {};
  const cooldownHours = replica.success_cooldown_hours || {};
  const cangkunCooldownHours = Number(cooldownHours.cangkun || 2.5);
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
    + '<label class="form-label">主线拉人群 ID（已停用）<textarea class="text-input replica-groups-input" name="replica_dispatch_group_ids" rows="3">' + escapeHtml(dispatchGroupIds.join('\n')) + '</textarea></label>'
    + '<div class="replica-config-side">'
    + '<div class="queue-section-title">拉人群监听账号' + (dispatchEnabled ? '' : '（停用）') + '</div>'
    + renderReplicaGroupRows(replica, {
      groupIds: dispatchGroupIds,
      listenerMap: replica.dispatch_listener_account_map || {},
      listenerAttr: 'data-replica-dispatch-listener',
      emptyText: '暂无主线拉人群。'
    })
    + '</div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">外部汇聚接入' + (aggregator.enabled ? '（运行中）' : '（已关闭）') + '</div>'
    + '<label class="toggle-field"><input type="checkbox" data-replica-query-aggregator-toggle="1"' + (aggregator.enabled ? ' checked' : '') + ' /><span>提交查询与推荐到拉人汇聚服务</span></label>'
    + '<div class="replica-config-grid">'
    + '<label class="form-label">Base URL<input class="text-input" name="replica_query_aggregator_base_url" value="' + escapeHtml(aggregator.base_url || '') + '" autocomplete="off" /></label>'
    + '<label class="form-label">Client ID<input class="text-input" name="replica_query_aggregator_client_id" value="' + escapeHtml(aggregator.client_id || '') + '" autocomplete="off" /></label>'
    + '<label class="form-label">Secret<input class="text-input" name="replica_query_aggregator_secret" type="password" placeholder="' + escapeHtml(secretPlaceholder) + '" autocomplete="new-password" /></label>'
    + '</div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">副本冷却</div>'
    + '<div class="replica-config-grid">'
    + '<label class="form-label">苍坤成功冷却（小时）<input class="text-input" name="replica_cangkun_success_cooldown_hours" type="number" min="0.25" max="24" step="0.25" value="' + escapeHtml(cangkunCooldownHours) + '" /></label>'
    + '</div>'
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">开房开关</div>'
    + renderReplicaKindEnableGrid(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">副本手动配置</div>'
    + renderReplicaKindManualConfig(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">本地参与身份</div>'
    + renderReplicaParticipantRows(replica)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">主线参与身份' + (dispatchEnabled ? '' : '（停用）') + '</div>'
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

function renderDungeonModal(options) {
  options = options || {};
  const body = document.getElementById('dungeon-modal-body');
  if (!body) {
    return;
  }
  const force = !!options.force;
  const renderKey = getDungeonModalRenderKey();
  if (!force && isDungeonModalOpen()) {
    if (isReplicaConfigEditing()) {
      return;
    }
    if (renderKey === lastDungeonModalRenderKey) {
      return;
    }
  }
  lastDungeonModalRenderKey = renderKey;
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
    + renderExpeditionConfig(replica)
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">自动加入状态</div>'
    + renderDungeonRows(rows)
    + '</div>'
    + '<div class="dungeon-section">'
    + '<div class="queue-section-title">最近公告</div>'
    + renderDungeonAnnouncements(dungeon.recent_announcements)
    + '</div>';
}

function renderHuntTeamRow(team, replica) {
  team = team || {};
  const options = replica.identity_options || [];
  const members = team.member_identity_ids || [];
  const seas = team.seas || [team.sea || '外海', team.sea || '外海', team.sea || '外海'];
  const selectOptions = function(values, selected) {
    return values.map(function(value) {
      return '<option value="' + escapeHtml(value) + '"' + (String(value) === String(selected) ? ' selected' : '') + '>' + escapeHtml(value) + '</option>';
    }).join('');
  };
  return '<fieldset class="dungeon-section" data-hunt-team>'
    + '<div class="replica-config-grid"><label class="form-label">队伍名称<input class="text-input" data-hunt-name maxlength="24" value="' + escapeHtml(team.name || '') + '" /></label>'
    + '<label class="form-label">游戏群<select class="text-input" data-hunt-role><option value="primary"' + (team.group_role === 'primary' ? ' selected' : '') + '>主群</option><option value="secondary"' + (team.group_role !== 'primary' ? ' selected' : '') + '>副群</option></select></label></div>'
    + '<div class="replica-config-grid">' + [0, 1, 2].map(function(day) {
      return '<label class="form-label">第 ' + (day + 1) + ' 日航向<select class="text-input" data-hunt-sea>' + selectOptions(['近海', '暗礁', '外海'], seas[day]) + '</select></label>';
    }).join('') + '</div><p class="form-help">选择外海时优先外海；本轮船体低于36则退近海降低风险，船体缺失时暂停。</p><div class="replica-config-grid">'
    + [0, 1, 2, 3, 4].map(function(index) {
      return '<label class="form-label">' + (index ? '成员 ' + (index + 1) : '船长兼探路者') + '<select class="text-input" data-hunt-member><option value="">不选择</option>'
        + options.map(function(identity) {
          return '<option value="' + escapeHtml(identity.identity_id) + '"' + (String(identity.identity_id) === String(members[index]) ? ' selected' : '') + '>' + escapeHtml(identity.display_name || identity.username || identity.identity_id) + '</option>';
        }).join('') + '</select></label>';
    }).join('') + '</div><label class="form-label"><input type="checkbox" data-hunt-weekly' + (team.auto_weekly ? ' checked' : '') + '> 每周自动补满5次有奖出海</label>'
    + '<button type="button" class="btn btn-secondary" data-hunt-remove>删除队伍</button></fieldset>';
}

function renderWeeklyTeamRow(team, replica) {
  const options = replica.identity_options || [];
  return '<fieldset data-weekly-team><label class="form-label">队名<input class="text-input" data-weekly-team-name value="' + escapeHtml(team.name || '') + '" /></label>'
    + '<label class="checkbox-inline"><input type="checkbox" data-weekly-team-enabled' + (team.enabled ? ' checked' : '') + '>启用此队</label>'
    + '<label class="form-label">游戏群<select class="text-input" data-weekly-team-role>' + ['primary', 'secondary'].map(function(role) {
      return '<option value="' + role + '"' + ((team.group_role || 'secondary') === role ? ' selected' : '') + '>' + (role === 'primary' ? '主群' : '副群') + '</option>';
    }).join('') + '</select></label><div class="replica-config-grid">' + options.map(function(identity) {
      const id = String(identity.identity_id);
      return '<label class="form-label"><span><input type="checkbox" data-weekly-team-member value="' + escapeHtml(id) + '"' + ((team.participant_identity_ids || []).map(String).includes(id) ? ' checked' : '') + '>'
        + escapeHtml(identity.display_name || id) + '</span><input class="text-input" data-weekly-team-alias="' + escapeHtml(id) + '" value="' + escapeHtml((team.member_names || {})[id] || '') + '" placeholder="可选游戏道号" /></label>';
    }).join('') + '</div>' + [['tianlan', '天澜'], ['yuejing', '越京'], ['yinming', '阴冥'], ['qipai', '七派']].map(function(item) {
      return '<label class="checkbox-inline"><input type="checkbox" data-weekly-team-kind="' + item[0] + '"' + ((team.kinds || []).includes(item[0]) ? ' checked' : '') + '>' + item[1] + '</label>';
    }).join('') + '<div class="replica-config-grid">' + [0, 1, 2].map(function(index) {
      return '<label class="form-label">越京第' + (index + 1) + '轮查访<select class="text-input" data-weekly-team-route>' + ['城南道观', '散修集市', '秦府夜宴'].map(function(place) {
        return '<option' + ((team.route || ['城南道观', '散修集市', '秦府夜宴'])[index] === place ? ' selected' : '') + '>' + place + '</option>';
      }).join('') + '</select></label>';
    }).join('') + '</div><label class="checkbox-inline"><input type="checkbox" data-weekly-team-talisman' + (team.use_talisman !== false ? ' checked' : '') + '>需要时使用真凰符</label>'
    + '<button type="button" class="btn btn-secondary" data-weekly-team-remove>删除此队</button></fieldset>';
}

function activityIdentityOptions(replica, selected) {
  return '<option value="">请选择</option>' + (replica.identity_options || []).map(function(identity) {
    return '<option value="' + escapeHtml(identity.identity_id) + '"' + (String(identity.identity_id) === String(selected) ? ' selected' : '') + '>' + escapeHtml(identity.display_name || identity.identity_id) + '</option>';
  }).join('');
}

function activityRoleOptions(selected) {
  return ['primary', 'secondary'].map(function(role) {
    return '<option value="' + role + '"' + ((selected || 'primary') === role ? ' selected' : '') + '>' + (role === 'primary' ? '主群' : '副群') + '</option>';
  }).join('');
}

function renderWarmPairRow(pair, replica) {
  return '<fieldset data-warm-pair><label class="form-label">配对名称<input class="text-input" data-pair-name value="' + escapeHtml(pair.name || '') + '"></label>'
    + '<div class="replica-config-grid"><label class="form-label">A号发引子<select class="text-input" data-pair-source>' + activityIdentityOptions(replica, pair.source_identity_id) + '</select></label>'
    + '<label class="form-label">B号温养<select class="text-input" data-pair-target>' + activityIdentityOptions(replica, pair.target_identity_id) + '</select></label>'
    + '<label class="form-label">游戏群<select class="text-input" data-pair-role>' + activityRoleOptions(pair.group_role) + '</select></label></div>'
    + '<button type="button" class="btn btn-secondary" data-warm-remove>删除配对</button></fieldset>';
}

function renderPairedActivityConfig(config, replica) {
  const warm = config.warm_pairs || {};
  const duel = config.global_duel || {};
  return '<div class="dungeon-section"><h4>独立温养配对</h4><label class="checkbox-inline"><input type="checkbox" name="warm_pairs_enabled"' + (warm.enabled ? ' checked' : '') + '>自动温养，每轮结算后等待61–62分钟</label>'
    + '<p>B号已有合欢自动温养时，此入口不执行。请为每个身份只配置一组配对。</p><label class="form-label">A号引子<input class="text-input" name="warm_pairs_lead" value="' + escapeHtml(warm.lead_text || '修修') + '"></label><div id="warm-pair-rows">'
    + (warm.pairs || []).map(function(pair) { return renderWarmPairRow(pair, replica); }).join('') + '</div><button type="button" class="btn btn-secondary" data-warm-add>新增温养配对</button></div>'
    + '<div class="dungeon-section"><h4>全局对决</h4><p>仅支持两个已登录的本人账号。收到第一回合开启后，双方向游戏机器人私聊发送神通；每轮间隔5–6分钟。</p>'
    + '<label class="checkbox-inline"><input type="checkbox" name="global_duel_enabled"' + (duel.enabled ? ' checked' : '') + '>执行所选账号的对决任务</label><div class="replica-config-grid">'
    + '<label class="form-label">发起账号<select class="text-input" name="global_duel_source">' + activityIdentityOptions(replica, duel.source_identity_id) + '</select></label>'
    + '<label class="form-label">目标账号<select class="text-input" name="global_duel_target">' + activityIdentityOptions(replica, duel.target_identity_id) + '</select></label>'
    + '<label class="form-label">轮数<input class="text-input" name="global_duel_count" type="number" min="1" max="100" value="' + escapeHtml(duel.count || 1) + '"></label>'
    + '<label class="form-label">游戏群<select class="text-input" name="global_duel_role">' + activityRoleOptions(duel.group_role) + '</select></label></div>'
    + '<button type="button" class="btn btn-secondary" data-activity-restart>按已保存配置重新安排对决</button></div>'
    + Object.keys(config.additional_runs || {}).map(function(kind) {
      return Object.entries(config.additional_runs[kind] || {}).map(function(item) {
        const run = item[1];
        return '<p>' + escapeHtml(item[0] + '：' + (run.phase || '待安排') + '｜' + (run.result || run.last_error || run.spirit_beast_last_result || ''))
          + (run.phase === 'held' ? ' <button type="button" class="btn btn-secondary" data-activity-confirm="' + kind + '" data-run-key="' + escapeHtml(item[0]) + '" data-run-id="' + escapeHtml(run.id || run.started_at) + '">已核对游戏结束</button>' : '') + '</p>';
      }).join('');
    }).join('');
}

function renderAvatarConfig(avatars, replica) {
  return '<div class="dungeon-section"><h4>身外化身</h4><p>先扫描本人洞府身份，再登记分身。登记后默认关闭；启用后按身份设置中可用的群玩法调度，附体期间主魂暂停，回复结束后收回主魂。组队周常、猎妖和玄骨暂不接受分身。</p>'
    + '<label class="form-label">所属本人账号<select class="text-input" data-avatar-owner>' + activityIdentityOptions(replica, '') + '</select></label>'
    + '<button type="button" class="btn btn-secondary" data-avatar-action="scan">扫描所选账号分身</button> <button type="button" class="btn btn-secondary" data-avatar-action="return">收回所选账号主魂</button>'
    + Object.entries(avatars.scans || {}).map(function(item) {
      return (item[1].entries || []).map(function(entry) {
        return '<p>' + escapeHtml(entry.dao_name) + ' <button type="button" class="btn btn-secondary" data-avatar-action="register" data-avatar-owner-id="' + escapeHtml(item[0]) + '" data-avatar-player-id="' + escapeHtml(entry.player_id) + '">登记此分身</button></p>';
      }).join('');
    }).join('') + Object.entries(avatars.entries || {}).map(function(item) {
      return '<p>' + escapeHtml(item[1].dao_name) + ' <button type="button" class="btn btn-secondary" data-avatar-action="profile" data-avatar-identity-id="' + escapeHtml(item[0]) + '">刷新分身资料</button></p>';
    }).join('') + Object.entries(avatars.souls || {}).filter(function(item) { return item[1].mode !== 'main'; }).map(function(item) {
      return '<p>' + escapeHtml(item[0] + '：' + item[1].mode + '｜' + (item[1].last_error || '')) + '</p>';
    }).join('') + '</div>';
}

function renderExpeditionConfig(replica) {
  const config = replica.expeditions || {};
  const xuangu = config.xuangu || {};
  const changfeng = config.changfeng || {};
  const weeklyConfig = config.weekly || {};
  const war = config.zhengmo || {};
  const spirit = config.spirit_beast || {};
  const identities = replica.identity_options || [];
  const members = function(kind, selected) {
    return '<div class="replica-config-grid">' + identities.map(function(identity) {
      return '<label class="checkbox-inline"><input type="checkbox" data-expedition-member="' + kind + '" value="' + escapeHtml(identity.identity_id) + '"'
        + ((selected || []).map(String).includes(String(identity.identity_id)) ? ' checked' : '') + ' />' + escapeHtml(identity.display_name || identity.identity_id) + '</label>';
    }).join('') + '</div>';
  };
  const records = (config.hunt_runs || []).concat(config.xuangu_runs || [], config.changfeng_runs || []);
  const weeklyStates = {idle: '待核对', checking: '核对船况', starting: '准备出海', running: '出海中', done: '本周已满', held: '待人工核对'};
  const weekly = (config.hunt_teams || []).filter(function(team) { return team.auto_weekly; }).map(function(team) {
    const record = (config.hunt_weekly || {})[team.name] || {};
    const counts = (team.member_identity_ids || []).map(function(id) {
      const identity = identities.find(function(item) { return String(item.identity_id) === String(id); }) || {};
      const counter = (record.counters || {})[String(id)];
      return (identity.display_name || identity.username || id) + ' ' + (counter ? counter.used + '/5' : '待查询');
    });
    return '<p data-hunt-weekly-status>' + escapeHtml(team.name + '：' + (weeklyStates[record.phase] || '待核对') + '｜' + counts.join('，') + (record.last_error ? '｜' + record.last_error : '')) + '</p>';
  }).join('');
  return '<details class="dungeon-section"><summary class="queue-section-title">出海猎妖、玄骨遗府与长风试炼</summary><form id="expedition-config-form">'
    + '<div class="dungeon-section"><h4>出海猎妖</h4><p>每队选择 2–5 人。勾选周常自动补满后，每趟先核对游戏周次数，所有成员达到5/5后停止；异常结果待核对。未勾选时，在日志群发送「.猎妖 出发 队伍名」。</p>'
    + '<div id="hunt-team-rows">' + (config.hunt_teams || []).map(function(team) { return renderHuntTeamRow(team, replica); }).join('') + '</div>'
    + '<button type="button" class="btn btn-secondary" data-hunt-add>新增队伍</button>' + weekly + '</div>'
    + '<div class="dungeon-section"><h4>玄骨遗府</h4><label class="checkbox-inline"><input type="checkbox" name="xuangu_enabled"' + (xuangu.enabled ? ' checked' : '') + ' />开启集结与副群应对</label>'
    + '<p>在副本群发送「.开启玄骨 @队长 @队员1 @队员2 @队员3 @队员4」，五人须结丹以上，游戏操作使用副群。</p>'
    + '<label class="checkbox-inline"><input type="checkbox" name="xuangu_auto_daily"' + (xuangu.auto_daily ? ' checked' : '') + ' />有古府寻踪珠时自动组队，每天最多一次</label><label class="form-label">每周最多次数<input class="text-input" type="number" name="xuangu_weekly_limit" min="1" max="7" value="' + escapeHtml(xuangu.weekly_limit || 3) + '" /></label><p>默认每周最多三次；开房前刷新背包，入场前核对全队可领奖。无票跳过，不购买或兑换。</p>'
    + members('xuangu', xuangu.participant_identity_ids)
    + '<label class="form-label">收尾选择<select class="text-input" name="xuangu_finish">' + ['立即撤离', '收拢遗宝', '争取全收'].map(function(choice) {
      return '<option' + ((xuangu.finish_choice || '收拢遗宝') === choice ? ' selected' : '') + '>' + choice + '</option>';
    }).join('') + '</select></label></div>'
    + '<div class="dungeon-section"><h4>组队活动</h4><label class="checkbox-inline"><input type="checkbox" name="weekly_enabled"' + (weeklyConfig.enabled ? ' checked' : '') + ' />自动补满有奖场次</label><p>天澜、越京、阴冥每周两次；七派密议每天三局。天澜、阴冥要求元婴，越京要求筑基。按境界选人，遇到未知结果保留原局。</p>'
    + members('weekly', weeklyConfig.participant_identity_ids)
    + '<label class="form-label">游戏群<select class="text-input" name="weekly_role">' + ['primary', 'secondary'].map(function(role) {
      return '<option value="' + role + '"' + ((weeklyConfig.group_role || 'secondary') === role ? ' selected' : '') + '>' + (role === 'primary' ? '主群' : '副群') + '</option>';
    }).join('') + '</select></label><details><summary>成员游戏道号（与 Telegram 用户名不同时填写）</summary><div class="replica-config-grid">' + identities.map(function(identity) {
      return '<label class="form-label">' + escapeHtml(identity.display_name || identity.identity_id) + '<input class="text-input" data-weekly-name="' + escapeHtml(identity.identity_id) + '" value="' + escapeHtml((weeklyConfig.member_names || {})[String(identity.identity_id)] || '') + '" placeholder="游戏 @ 后的道号" /></label>';
    }).join('') + '</div></details>'
    + [['tianlan', '天澜草原'], ['yuejing', '越京夜行'], ['yinming', '阴冥之地'], ['qipai', '七派密议']].map(function(item) {
      const run = ((config.weekly_state || {}).runs || {})[item[0]] || {};
      const quotas = ((config.weekly_state || {}).quotas || {})[item[0]] || {};
      return '<label class="checkbox-inline"><input type="checkbox" data-weekly-kind="' + item[0] + '"' + ((weeklyConfig.kinds || []).includes(item[0]) ? ' checked' : '') + ' />' + item[1] + '</label><p>'
        + escapeHtml((run.phase || '待安排') + (run.last_error ? '｜' + run.last_error : '') + '｜' + Object.keys(quotas).map(function(id) {
          const identity = identities.find(function(row) { return String(row.identity_id) === id; }) || {};
          return (identity.display_name || id) + ' ' + quotas[id].used + (item[0] === 'qipai' ? '/3（' + quotas[id].day + '）' : '/2');
        }).join('，')) + '</p>';
    }).join('') + '</div>'
    + '<div class="dungeon-section"><h4>具名周常队伍</h4><p>配置具名队伍后按各队执行；不配置时沿用上方统一名单。同一身份共享次数上限。</p><div id="weekly-team-rows">'
    + (weeklyConfig.teams || []).map(function(team) { return renderWeeklyTeamRow(team, replica); }).join('')
    + '</div><button type="button" class="btn btn-secondary" data-weekly-team-add>新增周常队伍</button></div>'
    + '<div class="dungeon-section"><h4>正魔大战</h4><label class="checkbox-inline"><input type="checkbox" name="zhengmo_enabled"' + (war.enabled ? ' checked' : '') + ' />每日自动出征一次</label><p>沿用已有或宗门固定阵营，按本人今日军情推荐出征。无阵营跳过，不自动投效。未确认时先查询战况。</p>'
    + members('zhengmo', (war.identities || []).map(function(entry) { return entry.identity_id; }))
    + '<label class="checkbox-inline"><input type="checkbox" name="zhengmo_meeting"' + (war.auto_meeting !== false ? ' checked' : '') + ' />自动报名燕家堡夺宝大会</label><p>每季第4天21:00开赛，先查大会再报名，每主魂一号。报名结果不明时停止。押注、劫营、避战和倒戈不自动执行。</p>'
    + '<div class="replica-config-grid"><label class="form-label">北京时间开始小时<input class="text-input" type="number" name="zhengmo_hour" min="0" max="23" value="' + escapeHtml(war.hour === undefined ? 9 : war.hour) + '" /></label><label class="form-label">游戏群<select class="text-input" name="zhengmo_role">'
    + ['primary', 'secondary'].map(function(role) { return '<option value="' + role + '"' + ((war.group_role || 'primary') === role ? ' selected' : '') + '>' + (role === 'primary' ? '主群' : '副群') + '</option>'; }).join('') + '</select></label></div>'
    + Object.keys(config.zhengmo_runs || {}).map(function(root) {
      const run = config.zhengmo_runs[root];
      const meeting = run.meeting || {};
      const points = run.points === null || run.points === undefined ? '' : '｜上次查询战功 ' + run.points;
      const gain = run.personal_points === undefined ? '' : '｜本次出征 +' + run.personal_points;
      return '<p>' + escapeHtml(((run.members || [])[0] || {}).username || root) + '：'
        + escapeHtml('今日出征：' + (run.daily_phase || run.phase || '未执行') + points + gain + '｜' + (run.result || run.last_error || '')
          + (run.days >= 7 ? '｜本季出征已满7天，赛季宝箱资格达标' : '')
          + (meeting.phase ? '｜大会：' + meeting.phase + ' ' + (meeting.result || '') : '')) + '</p>';
    }).join('') + '</div>'
    + '<div class="dungeon-section"><h4>长风试炼</h4><label class="checkbox-inline"><input type="checkbox" name="changfeng_enabled"' + (changfeng.enabled ? ' checked' : '') + ' />开启每日试炼</label>'
    + '<p>使用洞府公共入口；默认每日一场。日志群可发送「.长风试炼 执行 @身份 麦田」「.长风试炼 核对 @身份 麦田」或「.长风试炼 状态」。未确认时停止并保留记录，核对只读取游戏状态。</p>'
    + members('changfeng', changfeng.participant_identity_ids)
    + '<div class="replica-config-grid"><label class="form-label">每日场次上限<input class="text-input" type="number" name="changfeng_limit" min="1" max="8" value="' + escapeHtml(changfeng.daily_limit || 1) + '" /></label>'
    + '<label class="form-label">评定档位<select class="text-input" name="changfeng_profile">' + [['clean', '甲上'], ['steady', '甲'], ['rough', '乙']].map(function(item) {
      return '<option value="' + item[0] + '"' + ((changfeng.report_profile || 'rough') === item[0] ? ' selected' : '') + '>' + item[1] + '</option>';
    }).join('') + '</select></label>'
    + ['longwind', 'bamboo'].map(function(arena) {
      return '<label class="checkbox-inline"><input type="checkbox" data-changfeng-arena="' + arena + '"' + ((changfeng.arenas || ['longwind', 'bamboo']).includes(arena) ? ' checked' : '') + ' />' + (arena === 'longwind' ? '麦田试炼' : '竹林试炼') + '</label>';
    }).join('') + '</div></div>'
    + '<div class="dungeon-section"><h4>驭灵行迹</h4><label class="checkbox-inline"><input type="checkbox" name="spirit_beast_enabled"' + (spirit.enabled ? ' checked' : '') + '>万灵宗账号自动互动、远征和探渊</label><p>按游戏剩余次数与体力安排；缺少确认时暂停。</p>'
    + members('spirit_beast', spirit.participant_identity_ids) + '</div>'
    + renderPairedActivityConfig(config, replica)
    + renderAvatarConfig(config.avatars || {}, replica)
    + '<div class="dungeon-section">' + records.map(function(record) {
      return '<p>' + escapeHtml(record.name || record.identity_id || '') + '：' + escapeHtml(record.phase || '') + ' ' + escapeHtml(record.result || record.outcome || record.last_error || '')
        + (record.requires_reconciliation ? '｜记录号 ' + escapeHtml(record.roster_id || record.started_at || '') : '') + '</p>';
    }).join('') + '</div><button type="submit" class="btn">保存活动配置</button></form></details>';
}

document.addEventListener('input', function(event) {
  const form = event.target.closest && event.target.closest('#expedition-config-form');
  if (form) form.dataset.dirty = '1';
});

document.addEventListener('click', function(event) {
  const form = document.getElementById('expedition-config-form');
  if (!form) return;
  if (event.target.closest('[data-hunt-add]')) {
    if (form.querySelectorAll('[data-hunt-team]').length >= 20) return;
    form.querySelector('#hunt-team-rows').insertAdjacentHTML('beforeend', renderHuntTeamRow({}, getReplicaSnapshot()));
    form.dataset.dirty = '1';
  }
  if (event.target.closest('[data-weekly-team-add]')) {
    if (form.querySelectorAll('[data-weekly-team]').length >= 20) return;
    form.querySelector('#weekly-team-rows').insertAdjacentHTML('beforeend', renderWeeklyTeamRow({}, getReplicaSnapshot()));
    form.dataset.dirty = '1';
  }
  const removeWeekly = event.target.closest('[data-weekly-team-remove]');
  if (removeWeekly) { removeWeekly.closest('[data-weekly-team]').remove(); form.dataset.dirty = '1'; }
  if (event.target.closest('[data-warm-add]')) {
    if (form.querySelectorAll('[data-warm-pair]').length >= 20) return;
    form.querySelector('#warm-pair-rows').insertAdjacentHTML('beforeend', renderWarmPairRow({}, getReplicaSnapshot()));
    form.dataset.dirty = '1';
  }
  const removeWarm = event.target.closest('[data-warm-remove]');
  if (removeWarm) { removeWarm.closest('[data-warm-pair]').remove(); form.dataset.dirty = '1'; }
  const remove = event.target.closest('[data-hunt-remove]');
  if (remove) { remove.closest('[data-hunt-team]').remove(); form.dataset.dirty = '1'; }
});

document.addEventListener('click', async function(event) {
  const button = event.target.closest('[data-avatar-action], [data-activity-confirm], [data-activity-restart]');
  if (!button || button.disabled) return;
  const form = button.closest('#expedition-config-form');
  if (!form) return;
  const payload = button.hasAttribute('data-activity-restart') ? {action: 'restart_global_duel'} : button.dataset.activityConfirm ? {
    action: 'confirm_finished', kind: button.dataset.activityConfirm, key: button.dataset.runKey, run_id: button.dataset.runId
  } : {action: button.dataset.avatarAction, owner_id: button.dataset.avatarOwnerId || form.querySelector('[data-avatar-owner]').value,
    player_id: button.dataset.avatarPlayerId, identity_id: button.dataset.avatarIdentityId};
  button.disabled = true;
  try {
    const result = await postJson('/api/additional-activity-action', payload);
    updateFlash(result.message || '已完成操作', false);
    if (result.snapshot && typeof applySnapshot === 'function') applySnapshot(result.snapshot, {keepFlash: true});
  } catch (error) { updateFlash(error.message || '活动操作失败', true); }
  finally { button.disabled = false; }
});

document.addEventListener('submit', async function(event) {
  const form = event.target;
  if (form.id !== 'expedition-config-form') return;
  event.preventDefault();
  const selectedMembers = function(kind) {
    return Array.from(form.querySelectorAll('[data-expedition-member="' + kind + '"]:checked')).map(function(input) { return input.value; });
  };
  const payload = {
    hunt_teams: Array.from(form.querySelectorAll('[data-hunt-team]')).map(function(row) {
      return {name: row.querySelector('[data-hunt-name]').value.trim(), group_role: row.querySelector('[data-hunt-role]').value,
        auto_weekly: row.querySelector('[data-hunt-weekly]').checked,
        seas: Array.from(row.querySelectorAll('[data-hunt-sea]')).map(function(select) { return select.value; }),
        member_identity_ids: Array.from(row.querySelectorAll('[data-hunt-member]')).map(function(select) { return select.value; }).filter(Boolean)};
    }),
    xuangu: {enabled: form.elements.xuangu_enabled.checked, auto_daily: form.elements.xuangu_auto_daily.checked, weekly_limit: Number(form.elements.xuangu_weekly_limit.value), finish_choice: form.elements.xuangu_finish.value, participant_identity_ids: selectedMembers('xuangu')},
    weekly: {enabled: form.elements.weekly_enabled.checked, participant_identity_ids: selectedMembers('weekly'), group_role: form.elements.weekly_role.value,
      member_names: Object.fromEntries(Array.from(form.querySelectorAll('[data-weekly-name]')).map(function(input) { return [input.dataset.weeklyName, input.value.trim()]; })),
      kinds: Array.from(form.querySelectorAll('[data-weekly-kind]:checked')).map(function(input) { return input.dataset.weeklyKind; }),
      teams: Array.from(form.querySelectorAll('[data-weekly-team]')).map(function(row) {
        return {name: row.querySelector('[data-weekly-team-name]').value.trim(), enabled: row.querySelector('[data-weekly-team-enabled]').checked,
          group_role: row.querySelector('[data-weekly-team-role]').value,
          participant_identity_ids: Array.from(row.querySelectorAll('[data-weekly-team-member]:checked')).map(function(input) { return input.value; }),
          member_names: Object.fromEntries(Array.from(row.querySelectorAll('[data-weekly-team-alias]')).map(function(input) { return [input.dataset.weeklyTeamAlias, input.value.trim()]; })),
          kinds: Array.from(row.querySelectorAll('[data-weekly-team-kind]:checked')).map(function(input) { return input.dataset.weeklyTeamKind; }),
          route: Array.from(row.querySelectorAll('[data-weekly-team-route]')).map(function(input) { return input.value; }),
          use_talisman: row.querySelector('[data-weekly-team-talisman]').checked};
      })},
    zhengmo: {enabled: form.elements.zhengmo_enabled.checked, hour: Number(form.elements.zhengmo_hour.value), group_role: form.elements.zhengmo_role.value,
      auto_meeting: form.elements.zhengmo_meeting.checked,
      identities: selectedMembers('zhengmo').map(function(id) { return {identity_id: id, faction: ''}; })},
    spirit_beast: {enabled: form.elements.spirit_beast_enabled.checked, participant_identity_ids: selectedMembers('spirit_beast')},
    warm_pairs: {enabled: form.elements.warm_pairs_enabled.checked, lead_text: form.elements.warm_pairs_lead.value,
      pairs: Array.from(form.querySelectorAll('[data-warm-pair]')).map(function(row) {
        return {name: row.querySelector('[data-pair-name]').value.trim(), source_identity_id: row.querySelector('[data-pair-source]').value,
          target_identity_id: row.querySelector('[data-pair-target]').value, group_role: row.querySelector('[data-pair-role]').value};
      })},
    global_duel: {enabled: form.elements.global_duel_enabled.checked, source_identity_id: form.elements.global_duel_source.value,
      target_identity_id: form.elements.global_duel_target.value, count: Number(form.elements.global_duel_count.value), group_role: form.elements.global_duel_role.value},
    changfeng: {enabled: form.elements.changfeng_enabled.checked, daily_limit: Number(form.elements.changfeng_limit.value), report_profile: form.elements.changfeng_profile.value, participant_identity_ids: selectedMembers('changfeng'),
      arenas: Array.from(form.querySelectorAll('[data-changfeng-arena]:checked')).map(function(input) { return input.dataset.changfengArena; })}
  };
  try {
    const result = await postJson('/api/expedition-config', payload);
    form.dataset.dirty = '';
    updateFlash(result.message || '已保存活动配置', false);
    if (result.snapshot && typeof applySnapshot === 'function') applySnapshot(result.snapshot, {keepFlash: true});
    else if (typeof refreshState === 'function') await refreshState({silent: true, keepFlash: true});
    renderDungeonModal({force: true});
  } catch (error) {
    updateFlash(error.message || '活动配置保存失败', true);
  }
});

function openDungeonModal() {
  renderDungeonModal({force: true});
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
    renderDungeonModal({force: true});
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
  const cangkunCooldown = (form.querySelector('input[name="replica_cangkun_success_cooldown_hours"]')?.value || '').trim();
  const participantIds = Array.from(form.querySelectorAll('[data-replica-participant]:checked')).map(function(input) {
    return input.getAttribute('data-replica-participant');
  });
  const dispatchParticipantIds = Array.from(form.querySelectorAll('[data-replica-dispatch-participant]:checked')).map(function(input) {
    return input.getAttribute('data-replica-dispatch-participant');
  });
  const kindConfigs = {};
  form.querySelectorAll('[data-replica-kind-enabled]').forEach(function(input) {
    const kind = String(input.getAttribute('data-replica-kind-enabled') || '').trim();
    if (!kind) {
      return;
    }
    kindConfigs[kind] = kindConfigs[kind] || {participant_identity_ids: [], dispatch_participant_identity_ids: []};
    kindConfigs[kind].enabled = !!input.checked;
  });
  form.querySelectorAll('[data-replica-kind-participant]:checked').forEach(function(input) {
    const kind = String(input.getAttribute('data-replica-kind-participant') || '').trim();
    if (!kind) {
      return;
    }
    kindConfigs[kind] = kindConfigs[kind] || {enabled: true, participant_identity_ids: [], dispatch_participant_identity_ids: []};
    kindConfigs[kind].participant_identity_ids.push(input.value);
  });
  form.querySelectorAll('[data-replica-kind-dispatch-participant]:checked').forEach(function(input) {
    const kind = String(input.getAttribute('data-replica-kind-dispatch-participant') || '').trim();
    if (!kind) {
      return;
    }
    kindConfigs[kind] = kindConfigs[kind] || {enabled: true, participant_identity_ids: [], dispatch_participant_identity_ids: []};
    kindConfigs[kind].dispatch_participant_identity_ids.push(input.value);
  });
  return {
    group_ids: groupIds,
    listener_account_map: listenerMap,
    dispatch_group_ids: dispatchGroupIds,
    dispatch_listener_account_map: dispatchListenerMap,
    query_aggregator_config: queryAggregatorConfig,
    success_cooldown_hours: {cangkun: cangkunCooldown},
    participant_identity_ids: participantIds,
    dispatch_participant_identity_ids: dispatchParticipantIds,
    kind_configs: kindConfigs,
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
    renderDungeonModal({force: true});
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
    renderDungeonModal({force: true});
  } catch (error) {
    updateFlash((error && error.message) || '金/雷 DPS 切换失败', true);
    if (typeof setFlash === 'function') {
      setFlash();
    }
    renderDungeonModal({force: true});
  }
}

async function toggleReplicaQueryAggregator(enabled) {
  try {
    const data = await postJson('/api/replica-query-aggregator-toggle', {enabled: !!enabled});
    updateFlash(data.message || '已更新拉人汇聚服务开关', false);
    if (data.snapshot && typeof applySnapshot === 'function') {
      applySnapshot(data.snapshot, {keepFlash: true});
    } else if (typeof refreshState === 'function') {
      await refreshState({silent: true, keepFlash: true});
    }
    renderDungeonModal({force: true});
  } catch (error) {
    updateFlash((error && error.message) || '拉人汇聚服务开关切换失败', true);
    if (typeof setFlash === 'function') {
      setFlash();
    }
    renderDungeonModal({force: true});
  }
}

registerRenderHook('dungeon', function() {
  renderDungeonButton();
  if (document.getElementById('dungeon-modal')?.classList.contains('show')) {
    renderDungeonModal();
  }
});

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

document.addEventListener('change', function(event) {
  const aggregatorToggle = event.target.closest('[data-replica-query-aggregator-toggle]');
  if (aggregatorToggle) {
    toggleReplicaQueryAggregator(aggregatorToggle.checked);
  }
});

document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    closeDungeonModal();
  }
});
