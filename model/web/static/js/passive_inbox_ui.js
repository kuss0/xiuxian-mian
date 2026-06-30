function getPassiveInboxSnapshot() {
  const snapshot = (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  return snapshot.passive_inbox || {};
}

const PASSIVE_INBOX_LABELS = {
  checkin: '点卯',
  concubine: '侍妾',
  concubine_voyage: '侍妾远航',
  concubine_heart: '共历心劫',
  concubine_tianji: '天机代卜',
  deep_retreat: '深度闭关',
  deep_retreat_summary_ambiguous: '深闭总结多身份',
  deep_retreat_summary_no_match: '深闭总结未匹配',
  external_identity_no_match: '外部身份不匹配',
  external_observation: '外部观察',
  external_owner_no_match: '外部归属不匹配',
  handler_gap: 'handler 未收口',
  handler_not_matched: 'handler 未匹配',
  no_change: '状态未变化',
  no_identity: '无法确认身份',
  no_reply_context: '缺少回复上下文',
  other: '其他缺口',
  passive: '被动消息',
  pet: '抚摸法宝',
  pet_trial: '器灵试炼',
  pet_warm: '温养器灵',
  pet_formation: '布下剑阵',
  reply_context_no_identity: '回复上下文无身份',
  second_soul: '第二元神',
  sect_teach: '宗门传功',
  small_world: '小世界',
  small_world_harvest: '小世界收香火',
  stargazer_collect: '观星收集',
  stargazer_guide: '观星牵引',
  stargazer_panel: '观星台',
  stargazer_soothe: '观星安抚',
  stargazer_sync: '观星同步',
  storage_bag: '储物袋',
  storage_bag_transfer: '储物袋转移',
  storage_bag_transfer_cancel_rejected: '转移取消被拒',
  storage_bag_transfer_gift_mismatch: '赠送结果不匹配',
  storage_bag_transfer_reply_mismatch: '转移回执不匹配',
  storage_bag_transfer_timeout: '转移等待超时',
  taiyi: '太一',
  taiyi_late_reply: '太一迟到回执',
  taiyi_msg_id_mismatch: '太一消息不匹配',
  taiyi_reply_timeout: '太一回执超时',
  taiyi_send_evidence_present: '太一出站已确认',
  taiyi_unrecognized_yindao_reply: '引道回执未识别',
  tianti: '登天阶',
  tower: '闯塔',
  tree: '灵树',
  tree_guard: '协同守山',
  tree_harvest: '采摘灵果',
  tree_panel: '灵树状态',
  unhandled_routed_reply: '回包未收口',
  unresolved_identity: '身份未归因',
  weak_owner_hint: '弱归属线索',
  unknown: '未知',
  wild_training: '野外历练',
  yinluo_collect: '阴罗收取',
  yinluo_refine: '阴罗炼化',
  yinluo_soothe: '阴罗安抚'
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

function passiveInboxExcerpt(value, limit) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  const max = Number(limit || 0) || 72;
  if (!text) return '';
  if (text.length <= max) return text;
  return text.slice(0, Math.max(1, max - 1)).trimEnd() + '…';
}

function passiveInboxMetaChips(item, options) {
  const opts = options || {};
  const chips = [];
  if (item.identity_id) chips.push({ label: '身份', value: item.identity_id });
  if (item.family) chips.push({ label: '链路', value: passiveInboxLabel(item.family) });
  if (opts.includeReason && item.reason) chips.push({ label: '原因', value: passiveInboxLabel(item.reason) });
  if (item.decision) chips.push({ label: '判定', value: passiveInboxLabel(item.decision) });
  if (item.msg_id) chips.push({ label: '消息', value: item.msg_id });
  if (item.reply_to_msg_id) chips.push({ label: '回复', value: item.reply_to_msg_id });
  return chips;
}

function renderPassiveInboxEventCard(item, options) {
  const opts = options || {};
  const title = opts.title || '';
  const titleClass = opts.titleClass || '';
  const subject = passiveInboxLabel(item.module || item.family || item.reason || 'unknown');
  const metaChips = passiveInboxMetaChips(item, { includeReason: !!opts.includeReason });
  const excerpt = passiveInboxExcerpt(item.matched_text || item.summary || '', opts.excerptLimit || 84);
  const excerptPrefix = item.matched_text ? '命中' : (item.summary ? '摘要' : '');
  return '<div class="passive-readable-event passive-readable-event-stack">'
    + '<div class="passive-readable-event-head">'
    + '<span>' + escapeHtml(formatPassiveInboxTs(item.ts)) + '</span>'
    + '<strong class="' + escapeHtml(titleClass) + '">' + escapeHtml(title) + '</strong>'
    + '<span class="passive-readable-subject">' + escapeHtml(subject) + '</span>'
    + '</div>'
    + (metaChips.length
      ? '<div class="passive-readable-event-meta">' + metaChips.map(function(chip) {
        return '<span class="passive-readable-meta-chip"><em>' + escapeHtml(chip.label) + '</em>' + escapeHtml(chip.value) + '</span>';
      }).join('') + '</div>'
      : '')
    + (excerpt
      ? '<div class="passive-readable-event-excerpt">' + (excerptPrefix ? '<em>' + escapeHtml(excerptPrefix) + '：</em>' : '') + escapeHtml(excerpt) + '</div>'
      : '')
    + '</div>';
}

function renderPassiveInboxAttention(inbox) {
  const attentionByReason = inbox.attention_by_reason || {};
  const attentionByClass = inbox.attention_by_class || {};
  const reasons = passiveInboxEntries(attentionByReason);
  const attention = Number(inbox.attention_total || 0);
  const unresolvedIdentity = Number(attentionByClass.unresolved_identity || 0);
  const handlerGap = Number(attentionByClass.handler_gap || 0);
  const other = Number(attentionByClass.other || 0);
  if (!attention) {
    return '<div class="passive-readable-ok">暂无需要关注的异常。</div>';
  }
  const lines = [];
  if (handlerGap > 0) lines.push('有 ' + handlerGap + ' 条 routed reply 命中 family 但还没被对应模块收口。');
  if (unresolvedIdentity > 0) lines.push('有 ' + unresolvedIdentity + ' 条相关消息没能确认属于哪个身份。');
  if (other > 0) lines.push('还有 ' + other + ' 条其他待归因缺口。');
  reasons.forEach(function(pair) {
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
    return renderPassiveInboxEventCard(item, {
      title: changed ? '已更新' : '已跳过',
      titleClass: changed ? 'passive-readable-good' : 'passive-readable-warn',
      includeReason: !changed,
      excerptLimit: 80
    });
  }).join('');
}

function renderPassiveInboxAttentionRecent(inbox) {
  const recent = Array.isArray(inbox.attention_recent) ? inbox.attention_recent.slice(-8).reverse() : [];
  if (!recent.length) return '<div class="queue-empty">暂无未收口缺口。</div>';
  return recent.map(function(item) {
    return renderPassiveInboxEventCard(item, {
      title: '待处理',
      titleClass: 'passive-readable-warn',
      includeReason: true,
      excerptLimit: 92
    });
  }).join('');
}

function renderPassiveInboxPanel() {
  const panel = document.getElementById('passive-inbox-panel');
  if (!panel) return;
  const inbox = getPassiveInboxSnapshot();
  const total = Number(inbox.total || 0);
  const changed = Number(inbox.changed || 0);
  const attention = Number(inbox.attention_total || 0);
  panel.innerHTML = ''
    + '<div class="passive-head">'
    + '<div><h2>消息盒子</h2><div class="meta">只读监听，不主动发送指令</div></div>'
    + '<button type="button" class="btn btn-secondary btn-compact" data-open-passive-inbox="1">展开</button>'
    + '</div>'
    + '<div class="passive-readable-grid passive-readable-grid-compact">'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(changed) + '</strong><span>被动更新</span></div>'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(attention) + '</strong><span>需要关注</span></div>'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(total) + '</strong><span>相关消息</span></div>'
    + '</div>'
    + '<div class="passive-columns">'
    + '<div><div class="queue-section-title">最近更新模块</div><div class="passive-readable-chips">' + renderPassiveInboxChips(passiveInboxEntries(inbox.modules || {}), '还没有被动更新') + '</div></div>'
    + '<div><div class="queue-section-title">待关注原因</div><div class="passive-readable-chips">' + renderPassiveInboxChips(passiveInboxEntries(inbox.attention_by_reason || {}), '暂无关注项') + '</div></div>'
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">需要关注</div>'
    + renderPassiveInboxAttention(inbox)
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">当前缺口</div>'
    + '<div class="passive-readable-events">' + renderPassiveInboxAttentionRecent({
      attention_recent: Array.isArray(inbox.attention_recent) ? inbox.attention_recent.slice(-4) : []
    }) + '</div>'
    + '</div>'
    + '<div class="passive-readable-section">'
    + '<div class="queue-section-title">最近事件</div>'
    + '<div class="passive-readable-events">' + renderPassiveInboxRecent({
      recent: Array.isArray(inbox.recent) ? inbox.recent.slice(-6) : []
    }) + '</div>'
    + '</div>';
}

function renderPassiveInboxModal() {
  const body = document.getElementById('passive-inbox-modal-body');
  if (!body) return;
  const inbox = getPassiveInboxSnapshot();
  const total = Number(inbox.total || 0);
  const changed = Number(inbox.changed || 0);
  const attention = Number(inbox.attention_total || 0);
  body.innerHTML = ''
    + '<div class="passive-readable-grid">'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(changed) + '</strong><span>被动更新</span></div>'
    + '<div class="passive-readable-stat"><strong>' + escapeHtml(attention) + '</strong><span>需要关注</span></div>'
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
    + '<div class="queue-section-title">当前缺口</div>'
    + '<div class="passive-readable-events">' + renderPassiveInboxAttentionRecent(inbox) + '</div>'
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

window.renderPassiveInboxPanel = renderPassiveInboxPanel;

if (typeof window.renderAll === 'function' && !window.renderAll._passiveInboxPanelWrapped) {
  const originalRenderAll = window.renderAll;
  const wrappedRenderAll = function() {
    const result = originalRenderAll.apply(this, arguments);
    renderPassiveInboxPanel();
    return result;
  };
  wrappedRenderAll._passiveInboxPanelWrapped = true;
  window.renderAll = wrappedRenderAll;
}

renderPassiveInboxPanel();
