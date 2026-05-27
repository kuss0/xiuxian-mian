function getPassiveInboxSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.passive_inbox || {};
}

const PASSIVE_INBOX_LABELS = {
  checkin: '点卯',
  concubine: '侍妾',
  concubine_heart: '共历心劫',
  concubine_tianji: '天机代卜',
  no_change: '状态未变化',
  no_identity: '无法确认身份',
  passive: '被动消息',
  pet: '抚摸法宝',
  pet_trial: '器灵试炼',
  pet_warm: '温养器灵',
  second_soul: '第二元神',
  sect_teach: '宗门传功',
  small_world: '小世界',
  stargazer_collect: '观星收集',
  stargazer_guide: '观星牵引',
  stargazer_panel: '观星台',
  stargazer_soothe: '观星安抚',
  stargazer_sync: '观星同步',
  storage_bag: '储物袋',
  tianti: '登天阶',
  tower: '闯塔',
  tree: '灵树',
  tree_guard: '协同守山',
  tree_harvest: '采摘灵果',
  tree_panel: '灵树状态',
  unknown: '未知',
  wild_training: '野外历练'
};

function passiveInboxLabel(value) {
  const raw = String(value || '').trim();
  if (!raw) return '未知';
  return raw.split(',').map(function(part) {
    const key = part.trim();
    return PASSIVE_INBOX_LABELS[key] || key;
  }).join('、');
}

function formatPassiveInboxTs(ts) {
  const value = Number(ts || 0);
  if (!value) return '-';
  return new Date(value * 1000).toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
}

function passiveInboxEntries(obj) {
  return Object.entries(obj || {})
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0) || String(a[0]).localeCompare(String(b[0])))
    .slice(0, 8);
}

function renderPassiveInboxChips(entries, emptyText) {
  if (!entries.length) return '<span class="passive-readable-muted">' + escapeHtml(emptyText || '无') + '</span>';
  return entries.map(function(pair) {
    return '<span class="passive-readable-chip">' + escapeHtml(passiveInboxLabel(pair[0])) + '<strong>' + escapeHtml(pair[1]) + '</strong></span>';
  }).join('');
}

function renderPassiveInboxAttention(inbox) {
  const skips = passiveInboxEntries(inbox.skip_reasons || {});
  const noIdentity = Number((inbox.skip_reasons || {}).no_identity || 0);
  const noChange = Number((inbox.skip_reasons || {}).no_change || 0);
  const skipped = Number(inbox.skipped || 0);
  if (!skipped) {
    return '<div class="passive-readable-ok">暂无需要关注的异常。</div>';
  }
  const lines = [];
  if (noIdentity > 0) lines.push('有 ' + noIdentity + ' 条相关消息没能确认属于哪个身份。');
  if (noChange > 0) lines.push('有 ' + noChange + ' 条消息已识别，但没有带来状态变化。');
  const other = skips.filter(function(pair) { return pair[0] !== 'no_identity' && pair[0] !== 'no_change'; });
  other.forEach(function(pair) {
    lines.push(passiveInboxLabel(pair[0]) + '：' + pair[1] + ' 条');
  });
  return '<ul class="passive-readable-list">' + lines.map(function(line) {
    return '<li>' + escapeHtml(line) + '</li>';
  }).join('') + '</ul>';
}

function renderPassiveInboxRecent(inbox) {
  const recent = Array.isArray(inbox.recent) ? inbox.recent.slice(-12).reverse() : [];
  if (!recent.length) return '<div class="queue-empty">暂无最近更新。</div>';
  return recent.map(function(item) {
    const changed = item.kind === 'changed';
    const title = changed ? '已更新' : '已跳过';
    const subject = passiveInboxLabel(item.module || item.reason || 'unknown');
    const identity = item.identity_id ? ' ｜ ' + item.identity_id : '';
    const summary = item.summary ? ' ｜ ' + passiveInboxLabel(item.summary) : '';
    return '<div class="passive-readable-event">'
      + '<span>' + escapeHtml(formatPassiveInboxTs(item.ts)) + '</span>'
      + '<strong class="' + (changed ? 'passive-readable-good' : 'passive-readable-warn') + '">' + escapeHtml(title) + '</strong>'
      + '<span>' + escapeHtml(subject + identity + summary) + '</span>'
      + '</div>';
  }).join('');
}

function renderPassiveInboxModal() {
  const body = document.getElementById('passive-inbox-modal-body');
  if (!body) return;
  const inbox = getPassiveInboxSnapshot();
  const total = Number(inbox.total || 0);
  const changed = Number(inbox.changed || 0);
  const skipped = Number(inbox.skipped || 0);
  body.innerHTML = ''
    + '<div class="passive-readable-grid">'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(changed) + '</strong><span>被动更新</span></div>'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(skipped) + '</strong><span>需要关注</span></div>'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(total) + '</strong><span>相关消息</span></div>'
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">最近更新模块</div>'
    + '<div class="passive-readable-chips">' + renderPassiveInboxChips(passiveInboxEntries(inbox.modules || {}), '还没有被动更新') + '</div>'
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">需要关注</div>'
    + renderPassiveInboxAttention(inbox)
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">最近事件</div>'
    + '<div class="passive-readable-events">' + renderPassiveInboxRecent(inbox) + '</div>'
    + '</div>';
}

function openPassiveInboxModal() {
  renderPassiveInboxModal();
  const modal = document.getElementById('passive-inbox-modal');
  if (modal) modal.classList.add('show');
}

function closePassiveInboxModal() {
  const modal = document.getElementById('passive-inbox-modal');
  if (modal) modal.classList.remove('show');
}

document.addEventListener('click', function(event) {
  if (event.target.closest('[data-open-passive-inbox]')) {
    openPassiveInboxModal();
    return;
  }
  if (event.target.getAttribute('data-close-modal') === 'passive-inbox' || event.target.id === 'passive-inbox-modal') {
    closePassiveInboxModal();
  }
});
