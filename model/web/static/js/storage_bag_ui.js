(function () {
  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function snapshot() {
    return (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  }

  function storageData() {
    return snapshot().storage_bag || {};
  }

  function rows() {
    return Array.isArray(storageData().rows) ? storageData().rows : [];
  }

  function items() {
    return Array.isArray(storageData().items) ? storageData().items : [];
  }

  function selectedSyncIds() {
    if (typeof appState === 'undefined') return new Set();
    if (!appState.storageBagSelectedIds) appState.storageBagSelectedIds = new Set();
    return appState.storageBagSelectedIds;
  }

  function transferState() {
    if (typeof appState === 'undefined') return {};
    if (!appState.storageBagTransfer) {
      appState.storageBagTransfer = {
        sourceId: '',
        targetId: '',
        listingItem: '',
        selectedItems: {},
        manualText: '',
        preview: null,
        busy: false,
      };
    }
    if (!appState.storageBagTransfer.selectedItems) appState.storageBagTransfer.selectedItems = {};
    return appState.storageBagTransfer;
  }

  function setFlash(message, isError) {
    if (typeof updateFlash === 'function') {
      updateFlash(message, !!isError);
      return;
    }
    const flash = document.getElementById('flash');
    if (flash) {
      flash.textContent = message || '';
      flash.classList.toggle('hidden', !message);
    }
  }

  async function post(path, payload) {
    if (typeof postJson === 'function') return postJson(path, payload);
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || data.message || '请求失败');
    return data;
  }

  function identityOptions(selected) {
    return rows().map(function (row) {
      const id = Number(row.identity_id) || 0;
      const label = `${row.label || row.display_name || id}${row.protected ? '（保护）' : ''}`;
      return `<option value="${esc(id)}"${String(selected || '') === String(id) ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
  }

  function rowById(identityId) {
    const id = Number(identityId) || 0;
    return rows().find(function (row) { return (Number(row.identity_id) || 0) === id; }) || null;
  }

  function itemCount(identityId, itemName) {
    const row = rowById(identityId);
    return Number(((row || {}).items || {})[itemName] || 0);
  }

  function itemRule(itemName) {
    const rules = storageData().item_rules || {};
    return rules[itemName] || { method: 'unknown', method_label: '未知', transfer_selectable: true };
  }

  function methodLabel(method) {
    return { basic: '买卖', gift: '赠送', blocked: '不可转移', unknown: '未知' }[String(method || 'unknown')] || '未知';
  }

  function preferredListingItem(targetId) {
    const row = rowById(targetId);
    const inv = (row || {}).items || {};
    const names = Object.keys(inv).filter(function (name) { return Number(inv[name] || 0) > 0; });
    return names.find(function (name) { return name.indexOf('凝血草') >= 0; })
      || names.find(function (name) { return /草|花|果|芝|参|药/.test(name); })
      || names.find(function (name) { return name.indexOf('灵石') < 0; })
      || names[0]
      || '';
  }

  function parseManualItems(text) {
    return String(text || '').split(/[\n,，;；]+/).map(function (line) {
      const raw = line.trim();
      if (!raw) return null;
      const match = raw.match(/^(.+?)(?:\s*[x×*]\s*|\s+)(\d+)$/);
      if (!match) return { item_name: raw, quantity: 1 };
      return { item_name: match[1].trim(), quantity: Number(match[2]) || 0 };
    }).filter(function (item) { return item && item.item_name && item.quantity > 0; });
  }

  function selectedSnapshotItems() {
    const state = transferState();
    return Object.keys(state.selectedItems || {}).map(function (name) {
      return { item_name: name, quantity: Number(state.selectedItems[name]) || 0 };
    }).filter(function (item) { return item.quantity > 0; });
  }

  function transferPayload() {
    const state = transferState();
    const merged = new Map();
    selectedSnapshotItems().concat(parseManualItems(state.manualText)).forEach(function (item) {
      const name = String(item.item_name || '').trim();
      if (!name) return;
      merged.set(name, (Number(merged.get(name) || 0) + Number(item.quantity || 0)));
    });
    return {
      source_identity_id: Number(state.sourceId) || 0,
      target_identity_id: Number(state.targetId) || 0,
      listing_item: String(state.listingItem || '').trim(),
      items: Array.from(merged.entries()).map(function (entry) {
        return { item_name: entry[0], quantity: entry[1] };
      }),
    };
  }

  function normalizeTransferDefaults() {
    const state = transferState();
    const ids = rows().map(function (row) { return Number(row.identity_id) || 0; }).filter(Boolean);
    if (!ids.length) return;
    if (!ids.includes(Number(state.sourceId) || 0)) state.sourceId = ids[0];
    if (!ids.includes(Number(state.targetId) || 0) || Number(state.targetId) === Number(state.sourceId)) {
      state.targetId = ids.find(function (id) { return id !== Number(state.sourceId); }) || '';
    }
    if (!state.listingItem) state.listingItem = preferredListingItem(state.targetId);
  }

  function resetTransferDraftToDefaults() {
    const state = transferState();
    const currentRows = rows();
    const sourceRow = currentRows.find(function (row) { return !row.protected; }) || currentRows[0] || {};
    const sourceId = Number(sourceRow.identity_id) || 0;
    const targetRow = currentRows.find(function (row) {
      const id = Number(row.identity_id) || 0;
      return id && id !== sourceId;
    }) || {};
    state.sourceId = sourceId || '';
    state.targetId = Number(targetRow.identity_id) || '';
    state.listingItem = preferredListingItem(state.targetId);
    state.selectedItems = {};
    state.manualText = '';
    state.preview = null;
    state.busy = false;
  }

  function renderStorageBagTable() {
    const wrap = document.getElementById('storage-bag-table-wrap');
    if (!wrap) return;
    const data = storageData();
    const currentRows = rows();
    const currentItems = items();
    const totals = data.totals || {};
    const syncIds = selectedSyncIds();
    renderSyncControls();
    if (!currentRows.length) {
      wrap.innerHTML = '<div class="queue-empty">暂无身份。</div>';
      return;
    }
    const header = currentRows.map(function (row) {
      const id = Number(row.identity_id) || 0;
      const checkbox = row.protected ? '' : `<input type="checkbox" name="storage_bag_identity_id" value="${esc(id)}"${syncIds.has(id) ? ' checked' : ''} />`;
      const protectedText = row.protected ? '<span class="storage-bag-protected">保护</span>' : '';
      return `<th><label class="storage-bag-head">${checkbox}<span>${esc(row.label || row.display_name || id)}</span></label><small>${esc(row.updated_at || '未解析')} ${protectedText}</small></th>`;
    }).join('');
    const body = currentItems.length ? currentItems.map(function (item) {
      return `<tr><th>${esc(item)}</th><td class="storage-bag-total">${Number(totals[item] || 0).toLocaleString()}</td>${currentRows.map(function (row) {
        const count = Number((row.items || {})[item] || 0);
        return `<td>${count ? esc(count.toLocaleString()) : ''}</td>`;
      }).join('')}</tr>`;
    }).join('') : `<tr><th>暂无物品</th><td></td>${currentRows.map(function () { return '<td></td>'; }).join('')}</tr>`;
    wrap.innerHTML = `<table class="storage-bag-table"><thead><tr><th>物品</th><th>总量</th>${header}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function renderSyncControls() {
    const sync = snapshot().storage_bag_sync || {};
    const running = !!sync.running;
    const selectable = rows().filter(function (row) { return !row.protected; });
    const selected = selectedSyncIds();
    const selectedCount = selectable.filter(function (row) { return selected.has(Number(row.identity_id) || 0); }).length;
    const btn = document.getElementById('storage-bag-sync-btn');
    const selectAll = document.getElementById('storage-bag-select-all');
    const status = document.getElementById('storage-bag-sync-status');
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? '同步中' : '同步';
    }
    if (selectAll) {
      selectAll.checked = selectable.length > 0 && selectedCount === selectable.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < selectable.length;
      selectAll.disabled = running || selectable.length <= 0;
    }
    if (status) {
      const pending = Array.isArray(sync.pending_ids) ? sync.pending_ids.length : 0;
      const completed = Array.isArray(sync.completed_ids) ? sync.completed_ids.length : 0;
      status.textContent = running ? `排队 ${pending}，已发送 ${completed}` : '';
    }
  }

  function renderTransferPanel() {
    const panel = document.getElementById('storage-bag-transfer-panel');
    if (!panel) return;
    normalizeTransferDefaults();
    const state = transferState();
    const runtime = snapshot().storage_bag_transfer || {};
    const sourceRow = rowById(state.sourceId);
    const sourceItems = items().filter(function (name) {
      const rule = itemRule(name);
      return itemCount(state.sourceId, name) > 0 && String(rule.method || 'unknown') !== 'blocked';
    });
    const itemRows = sourceItems.length ? sourceItems.map(function (name) {
      const count = itemCount(state.sourceId, name);
      const checked = Object.prototype.hasOwnProperty.call(state.selectedItems, name);
      const qty = checked ? Number(state.selectedItems[name] || count) : count;
      const rule = itemRule(name);
      return `<tr><td><input type="checkbox" name="storage_bag_transfer_item" value="${esc(name)}"${checked ? ' checked' : ''} /></td><th>${esc(name)}</th><td>${esc(rule.method_label || methodLabel(rule.method))}</td><td>${esc(count.toLocaleString())}</td><td><input class="text-input storage-bag-qty-input" type="number" min="1" name="storage_bag_transfer_qty" data-storage-transfer-qty="${esc(name)}" value="${esc(qty)}"${checked ? '' : ' disabled'} /></td></tr>`;
    }).join('') : '<tr><td colspan="5" class="storage-bag-empty-cell">来源快照暂无可转移物品，可直接使用手填清单。</td></tr>';
    const preview = state.preview;
    const warnings = preview && Array.isArray(preview.warnings) && preview.warnings.length
      ? `<div class="storage-bag-transfer-warnings">${preview.warnings.map(function (item) { return `提示：${esc(item)}`; }).join('<br>')}</div>`
      : '';
    const previewHtml = preview
      ? `<div class="storage-bag-preview-summary">${esc(preview.summary || '已生成预览')}</div>${warnings}<pre>${esc((preview.commands || []).map(function (cmd) { return `${cmd.identity_id}｜${cmd.command}｜${cmd.note || ''}`; }).join('\n'))}</pre>`
      : '<div class="queue-empty">尚未生成预览。</div>';
    const logs = Array.isArray(runtime.logs) ? runtime.logs.slice(-8).map(function (log) {
      return `${log.ts || ''} ${log.message || ''}`;
    }).join('\n') : '';
    const running = !!runtime.running;
    const busy = !!state.busy;
    const sourceLabel = sourceRow ? (sourceRow.label || sourceRow.display_name || state.sourceId) : '来源';
    panel.innerHTML = `
      <div class="storage-bag-transfer-controls">
        <label class="field-label">资源号<select class="text-input" data-storage-transfer-field="sourceId">${identityOptions(state.sourceId)}</select></label>
        <label class="field-label">集中号<select class="text-input" data-storage-transfer-field="targetId">${identityOptions(state.targetId)}</select></label>
        <label class="field-label">集中号上架物<input class="text-input" data-storage-transfer-field="listingItem" value="${esc(state.listingItem || '')}" placeholder="如 凝血草" /></label>
      </div>
      <div class="storage-bag-transfer-grid">
        <section>
          <div class="form-label">${esc(sourceLabel)} 快照物品，可批量勾选。</div>
          <div class="storage-bag-transfer-table-wrap"><table class="storage-bag-transfer-table"><thead><tr><th>选</th><th>物品</th><th>方式</th><th>库存</th><th>数量</th></tr></thead><tbody>${itemRows}</tbody></table></div>
        </section>
        <section>
          <label class="field-label">手填清单<textarea class="text-input storage-bag-transfer-textarea" data-storage-transfer-field="manualText" placeholder="妖丹*10 木髓*5 或一行一个">${esc(state.manualText || '')}</textarea></label>
          <div class="form-label">快照不准时直接手填；脚本只提示，不阻塞，游戏回复兜底。</div>
        </section>
      </div>
      <div class="storage-bag-transfer-actions">
        <button type="button" class="btn btn-secondary" data-storage-transfer-preview="1"${running || busy ? ' disabled' : ''}>生成预览</button>
        <button type="button" class="btn" data-storage-transfer-start="1"${running || busy ? ' disabled' : ''}>执行转移</button>
        <button type="button" class="btn btn-secondary" data-storage-transfer-cancel="1"${running ? '' : ' disabled'}>取消任务</button>
      </div>
      <div id="storage-bag-transfer-preview" class="storage-bag-transfer-preview">${previewHtml}${logs ? `<pre>${esc(logs)}</pre>` : ''}</div>`;
  }

  function resetTransferPreviewOnly() {
    const preview = document.getElementById('storage-bag-transfer-preview');
    if (preview) preview.innerHTML = '<div class="queue-empty">尚未生成预览。</div>';
  }

  function openStorageBagModal() {
    renderStorageBagTable();
    const modal = document.getElementById('storage-bag-modal');
    if (modal) modal.classList.add('show');
  }

  function closeStorageBagModal() {
    const modal = document.getElementById('storage-bag-modal');
    if (modal) modal.classList.remove('show');
  }

  function openTransferModal() {
    const runtime = snapshot().storage_bag_transfer || {};
    if (!runtime.running) resetTransferDraftToDefaults();
    renderTransferPanel();
    const modal = document.getElementById('storage-bag-transfer-modal');
    if (modal) modal.classList.add('show');
  }

  function closeTransferModal() {
    const modal = document.getElementById('storage-bag-transfer-modal');
    if (modal) modal.classList.remove('show');
  }

  async function syncStorageBag() {
    const ids = Array.from(selectedSyncIds());
    if (!ids.length) {
      setFlash('请至少勾选一个非保护身份', true);
      renderStorageBagTable();
      return;
    }
    try {
      const data = await post('/api/storage-bag-sync', { identity_ids: ids });
      if (typeof appState !== 'undefined') appState.storageBagSelectedIds = new Set();
      setFlash(data.message || '已开始同步储物袋', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderStorageBagTable();
    } catch (error) {
      setFlash((error && error.message) || '储物袋同步失败', true);
      renderStorageBagTable();
    }
  }

  async function previewTransfer() {
    const state = transferState();
    if (state.busy) return;
    state.busy = true;
    renderTransferPanel();
    try {
      const data = await post('/api/storage-bag-transfer-preview', transferPayload());
      state.preview = data.preview || null;
      renderTransferPanel();
    } catch (error) {
      setFlash((error && error.message) || '生成转移预览失败', true);
      renderTransferPanel();
    } finally {
      state.busy = false;
      renderTransferPanel();
    }
  }

  async function startTransfer() {
    const state = transferState();
    if (state.busy) return;
    state.busy = true;
    renderTransferPanel();
    try {
      const data = await post('/api/storage-bag-transfer-start', transferPayload());
      setFlash(data.message || '已开始储物袋转移', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderTransferPanel();
    } catch (error) {
      setFlash((error && error.message) || '启动储物袋转移失败', true);
      renderTransferPanel();
    } finally {
      state.busy = false;
      renderTransferPanel();
    }
  }

  async function cancelTransfer() {
    try {
      const data = await post('/api/storage-bag-transfer-cancel', {});
      setFlash(data.message || '已取消储物袋转移', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderTransferPanel();
    } catch (error) {
      setFlash((error && error.message) || '取消储物袋转移失败', true);
    }
  }

  function isTransferModalOpen() {
    const modal = document.getElementById('storage-bag-transfer-modal');
    return !!(modal && modal.classList.contains('show'));
  }

  async function refreshTransferPanelIfOpen() {
    if (!isTransferModalOpen()) return;
    const runtime = snapshot().storage_bag_transfer || {};
    if (!runtime.running) return;
    if (typeof refreshState === 'function') await refreshState({ silent: true, keepFlash: true });
    renderTransferPanel();
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-storage-bag]')) return openStorageBagModal();
    if (event.target.closest('#storage-bag-transfer-open-btn')) return openTransferModal();
    if (event.target.closest('#storage-bag-sync-btn')) return syncStorageBag();
    if (event.target.closest('[data-storage-transfer-preview]')) return previewTransfer();
    if (event.target.closest('[data-storage-transfer-start]')) return startTransfer();
    if (event.target.closest('[data-storage-transfer-cancel]')) return cancelTransfer();
    if (event.target.getAttribute('data-close-modal') === 'storage-bag' || event.target.id === 'storage-bag-modal') return closeStorageBagModal();
    if (event.target.getAttribute('data-close-modal') === 'storage-bag-transfer' || event.target.id === 'storage-bag-transfer-modal') return closeTransferModal();
  });

  document.addEventListener('change', function (event) {
    const selectAll = event.target.closest('#storage-bag-select-all');
    if (selectAll) {
      const ids = selectedSyncIds();
      ids.clear();
      if (selectAll.checked) {
        rows().filter(function (row) { return !row.protected; }).forEach(function (row) {
          const id = Number(row.identity_id) || 0;
          if (id) ids.add(id);
        });
      }
      renderStorageBagTable();
      return;
    }
    const syncCheckbox = event.target.closest('input[name="storage_bag_identity_id"]');
    if (syncCheckbox) {
      const ids = selectedSyncIds();
      const id = Number(syncCheckbox.value) || 0;
      if (id) {
        if (syncCheckbox.checked) ids.add(id);
        else ids.delete(id);
      }
      renderSyncControls();
      return;
    }
    const field = event.target.closest('[data-storage-transfer-field]');
    if (field) {
      const state = transferState();
      const key = field.getAttribute('data-storage-transfer-field');
      state[key] = field.value;
      state.preview = null;
      if (key === 'sourceId') state.selectedItems = {};
      if (key === 'targetId') state.listingItem = preferredListingItem(state.targetId);
      renderTransferPanel();
      return;
    }
    const itemCheckbox = event.target.closest('input[name="storage_bag_transfer_item"]');
    if (itemCheckbox) {
      const state = transferState();
      const name = itemCheckbox.value;
      if (itemCheckbox.checked) state.selectedItems[name] = itemCount(state.sourceId, name);
      else delete state.selectedItems[name];
      state.preview = null;
      const row = itemCheckbox.closest('tr');
      const qtyInput = row ? row.querySelector('input[name="storage_bag_transfer_qty"]') : null;
      if (qtyInput) {
        qtyInput.disabled = !itemCheckbox.checked;
        if (itemCheckbox.checked) qtyInput.value = String(state.selectedItems[name] || itemCount(state.sourceId, name) || 1);
      }
      resetTransferPreviewOnly();
      return;
    }
    const qtyInput = event.target.closest('input[name="storage_bag_transfer_qty"]');
    if (qtyInput) {
      const name = qtyInput.getAttribute('data-storage-transfer-qty');
      transferState().selectedItems[name] = Number(qtyInput.value) || 0;
      transferState().preview = null;
    }
  });

  document.addEventListener('input', function (event) {
    const field = event.target.closest('[data-storage-transfer-field]');
    if (!field) return;
    const key = field.getAttribute('data-storage-transfer-field');
    if (key === 'manualText' || key === 'listingItem') {
      transferState()[key] = field.value;
      transferState().preview = null;
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeStorageBagModal();
      closeTransferModal();
    }
  });

  window.setInterval(function () {
    refreshTransferPanelIfOpen();
  }, 3000);
})();
