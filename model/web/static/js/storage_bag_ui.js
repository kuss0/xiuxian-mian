(function () {
  function esc(value) {
    if (typeof escapeHtml === 'function') {
      return escapeHtml(value);
    }
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function snapshot() {
    return (typeof appState !== 'undefined' && appState.snapshot) ? appState.snapshot : {};
  }

  function selectedIds() {
    if (typeof appState === 'undefined') {
      return new Set();
    }
    if (!appState.storageBagSelectedIds) {
      appState.storageBagSelectedIds = new Set();
    }
    return appState.storageBagSelectedIds;
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
    if (typeof postJson === 'function') {
      return postJson(path, payload);
    }
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.message || '请求失败');
    }
    return data;
  }

  function getSyncState() {
    return snapshot().storage_bag_sync || {};
  }

  function itemCategory(name) {
    const text = String(name || '');
    if (!text) {
      return '其他';
    }
    if (text.indexOf('灵石') >= 0) {
      return '货币';
    }
    if (text.indexOf('妖丹') >= 0 || text.indexOf('兽核') >= 0 || text.indexOf('内丹') >= 0) {
      return '妖丹';
    }
    if (text.indexOf('木髓') >= 0 || text.indexOf('灵木') >= 0 || text.indexOf('神木') >= 0) {
      return '木髓';
    }
    if (text.indexOf('丹') >= 0 || text.indexOf('丸') >= 0 || text.indexOf('散') >= 0) {
      return '丹药';
    }
    if (text.indexOf('符') >= 0 || text.indexOf('阵') >= 0) {
      return '符阵';
    }
    if (text.indexOf('草') >= 0 || text.indexOf('花') >= 0 || text.indexOf('果') >= 0 || text.indexOf('芝') >= 0 || text.indexOf('参') >= 0 || text.indexOf('药') >= 0) {
      return '灵草';
    }
    if (text.indexOf('矿') >= 0 || text.indexOf('石') >= 0 || text.indexOf('晶') >= 0 || text.indexOf('砂') >= 0 || text.indexOf('铁') >= 0 || text.indexOf('铜') >= 0 || text.indexOf('金') >= 0) {
      return '矿材';
    }
    if (text.indexOf('皮') >= 0 || text.indexOf('骨') >= 0 || text.indexOf('血') >= 0 || text.indexOf('角') >= 0 || text.indexOf('鳞') >= 0 || text.indexOf('羽') >= 0) {
      return '兽材';
    }
    return '材料';
  }

  function groupItems(items) {
    const order = ['货币', '材料', '妖丹', '木髓', '丹药', '灵草', '矿材', '兽材', '符阵', '其他'];
    const grouped = {};
    (items || []).forEach(function (item) {
      const category = itemCategory(item);
      if (!grouped[category]) {
        grouped[category] = [];
      }
      grouped[category].push(item);
    });
    return order.filter(function (category) {
      return grouped[category] && grouped[category].length;
    }).map(function (category) {
      return { category: category, items: grouped[category] };
    });
  }

  function renderControls(rows) {
    const btn = document.getElementById('storage-bag-sync-btn');
    const selectAll = document.getElementById('storage-bag-select-all');
    const status = document.getElementById('storage-bag-sync-status');
    const syncState = getSyncState();
    const running = !!syncState.running;
    const selectableRows = (rows || []).filter(function (row) { return !row.protected; });
    const ids = selectedIds();
    const selectedCount = selectableRows.filter(function (row) {
      return ids.has(Number(row.identity_id) || 0);
    }).length;
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? '同步中' : '同步';
    }
    if (selectAll) {
      selectAll.checked = selectableRows.length > 0 && selectedCount === selectableRows.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < selectableRows.length;
      selectAll.disabled = running || selectableRows.length <= 0;
    }
    if (status) {
      const pending = Array.isArray(syncState.pending_ids) ? syncState.pending_ids.length : 0;
      const completed = Array.isArray(syncState.completed_ids) ? syncState.completed_ids.length : 0;
      status.textContent = running ? `排队 ${pending}，已发送 ${completed}` : '';
    }
  }

  function renderStorageBagTable() {
    const wrap = document.getElementById('storage-bag-table-wrap');
    if (!wrap) {
      return;
    }
    const data = snapshot().storage_bag || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const items = Array.isArray(data.items) ? data.items : [];
    const totals = data.totals || {};
    const ids = selectedIds();
    renderControls(rows);
    if (!rows.length) {
      wrap.innerHTML = '<div class="queue-empty">暂无身份。</div>';
      return;
    }
    const header = rows.map(function (row) {
      const identityId = Number(row.identity_id) || 0;
      const protectedText = row.protected ? '<span class="storage-bag-protected">保护</span>' : '';
      const checkbox = row.protected
        ? ''
        : `<input type="checkbox" name="storage_bag_identity_id" value="${esc(identityId)}"${ids.has(identityId) ? ' checked' : ''} />`;
      return `<th><label class="storage-bag-head">${checkbox}<span>${esc(row.label || identityId)}</span></label><small title="${esc(row.display_name || row.label || identityId)}">${esc(row.updated_at || '未解析')} ${protectedText}</small></th>`;
    }).join('');
    const groupedItems = groupItems(items);
    const body = groupedItems.length ? groupedItems.map(function (group) {
      const titleRow = `<tr class="storage-bag-category"><th>${esc(group.category)}</th><td></td>${rows.map(function () { return '<td></td>'; }).join('')}</tr>`;
      const itemRows = group.items.map(function (item) {
        return `<tr><th>${esc(item)}</th><td class="storage-bag-total">${Number(totals[item] || 0).toLocaleString()}</td>${rows.map(function (row) {
          const count = Number((row.items || {})[item] || 0);
          return `<td>${count ? esc(count.toLocaleString()) : ''}</td>`;
        }).join('')}</tr>`;
      }).join('');
      return titleRow + itemRows;
    }).join('') : `<tr><th>暂无物品</th><td></td>${rows.map(function () { return '<td></td>'; }).join('')}</tr>`;
    wrap.innerHTML = `<table class="storage-bag-table"><thead><tr><th>物品</th><th>总量</th>${header}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function openStorageBagModal() {
    if (typeof appState !== 'undefined') {
      appState.storageBagSelectedIds = new Set();
    }
    renderStorageBagTable();
    const modal = document.getElementById('storage-bag-modal');
    if (modal) {
      modal.classList.add('show');
    }
  }

  function closeStorageBagModal() {
    const modal = document.getElementById('storage-bag-modal');
    if (modal) {
      modal.classList.remove('show');
    }
  }

  async function syncStorageBag() {
    const ids = Array.from(selectedIds());
    if (!ids.length) {
      setFlash('请至少勾选一个非保护身份', true);
      renderStorageBagTable();
      return;
    }
    const btn = document.getElementById('storage-bag-sync-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '同步中';
    }
    try {
      const data = await post('/api/storage-bag-sync', { identity_ids: ids });
      if (typeof appState !== 'undefined') {
        appState.storageBagSelectedIds = new Set();
      }
      setFlash(data.message || '已开始同步储物袋', false);
      if (typeof applySnapshot === 'function') {
        applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      } else if (typeof appState !== 'undefined' && data.snapshot) {
        appState.snapshot = data.snapshot;
      }
      renderStorageBagTable();
    } catch (error) {
      setFlash((error && error.message) || '储物袋同步失败', true);
      renderStorageBagTable();
    }
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-storage-bag]')) {
      openStorageBagModal();
      return;
    }
    if (event.target.closest('#storage-bag-sync-btn')) {
      syncStorageBag();
      return;
    }
    if (event.target.getAttribute('data-close-modal') === 'storage-bag' || event.target.id === 'storage-bag-modal') {
      closeStorageBagModal();
    }
  });

  document.addEventListener('change', function (event) {
    const selectAll = event.target.closest('#storage-bag-select-all');
    if (selectAll) {
      const rows = ((snapshot().storage_bag || {}).rows || []).filter(function (row) { return !row.protected; });
      const ids = selectedIds();
      ids.clear();
      if (selectAll.checked) {
        rows.forEach(function (row) {
          const identityId = Number(row.identity_id) || 0;
          if (identityId) {
            ids.add(identityId);
          }
        });
      }
      renderStorageBagTable();
      return;
    }
    const checkbox = event.target.closest('input[name="storage_bag_identity_id"]');
    if (checkbox) {
      const ids = selectedIds();
      const identityId = Number(checkbox.value) || 0;
      if (identityId) {
        if (checkbox.checked) {
          ids.add(identityId);
        } else {
          ids.delete(identityId);
        }
      }
      renderControls((snapshot().storage_bag || {}).rows || []);
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeStorageBagModal();
    }
  });

  window.setInterval(function () {
    const modal = document.getElementById('storage-bag-modal');
    if (modal && modal.classList.contains('show')) {
      renderStorageBagTable();
    }
  }, 3000);
})();
