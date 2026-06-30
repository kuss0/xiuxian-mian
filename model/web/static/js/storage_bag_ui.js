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

  function storageApiData() {
    return snapshot().storage_bag_api || {};
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
        operation: 'transfer',
        sourceId: '',
        targetId: '',
        listingItem: '',
        listingCount: 1,
        listingUnitPrice: 0,
        listingSyntax: 'space',
        selectedItems: {},
        manualText: '',
        preview: null,
        busy: false,
        startPending: 0,
        batchMode: false,
        batchAllSources: true,
        batchSourceIds: [],
        batchQuantityMode: 'all',
        batchReserveCount: 0,
        batchMinTransferCount: 1,
        includeProtected: false,
        continueOnError: false,
      };
    }
    if (!appState.storageBagTransfer.selectedItems) appState.storageBagTransfer.selectedItems = {};
    if (!Array.isArray(appState.storageBagTransfer.batchSourceIds)) appState.storageBagTransfer.batchSourceIds = [];
    if (['transfer', 'gift'].indexOf(String(appState.storageBagTransfer.operation || 'transfer')) < 0) appState.storageBagTransfer.operation = 'transfer';
    if (!Number(appState.storageBagTransfer.startPending || 0)) appState.storageBagTransfer.startPending = 0;
    if (!Number(appState.storageBagTransfer.listingCount || 0)) appState.storageBagTransfer.listingCount = 1;
    if (!Number(appState.storageBagTransfer.listingUnitPrice || 0)) appState.storageBagTransfer.listingUnitPrice = 0;
    if (!Number(appState.storageBagTransfer.batchReserveCount || 0)) appState.storageBagTransfer.batchReserveCount = 0;
    if (!Number(appState.storageBagTransfer.batchMinTransferCount || 0)) appState.storageBagTransfer.batchMinTransferCount = 1;
    if (['space', 'compact'].indexOf(String(appState.storageBagTransfer.listingSyntax || 'space')) < 0) appState.storageBagTransfer.listingSyntax = 'space';
    if (!appState.storageBagTransfer.batchQuantityMode) appState.storageBagTransfer.batchQuantityMode = 'all';
    if (typeof appState.storageBagTransfer.batchAllSources !== 'boolean') appState.storageBagTransfer.batchAllSources = true;
    return appState.storageBagTransfer;
  }

  var storageBagSearchTimer = null;
  var storageBagSearchComposing = false;
  var STORAGE_BAG_EXPLICIT_TAGS = [
    { tag: '称号', test: function (name) { return name === '真仙试锋' || name === '紫灵的轻吻' || name.endsWith('第一人') || name.indexOf('称号') >= 0; } },
    { tag: '特殊', test: function (name) { return name === '稳控全场' || name.indexOf('残篇') >= 0; } },
    { tag: '材料', test: function (name) { return name.indexOf('元磁山核') >= 0; } },
    { tag: '装备武器防具', test: function (name) { return name.indexOf('青竹蜂云剑') >= 0 && name.indexOf('图纸') < 0; } },
  ];
  var STORAGE_BAG_PINNED_ITEMS = ['天雷竹', '二级妖丹', '金精矿'];

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

  function rowLabel(row) {
    return String((row || {}).label || (row || {}).display_name || (row || {}).identity_id || '').trim();
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

  function isGiftMode() {
    return transferState().operation === 'gift';
  }

  function operationLabel() {
    return isGiftMode() ? '赠送' : '转移';
  }

  function preferredListingItem(targetId) {
    const row = rowById(targetId);
    const inv = (row || {}).items || {};
    const names = Object.keys(inv).filter(function (name) { return Number(inv[name] || 0) > 0; });
    return names.find(function (name) { return name === '凝血草'; })
      || names.find(function (name) { return name.indexOf('凝血草') >= 0; })
      || names.find(function (name) { return /草|花|果|芝|参|药/.test(name); })
      || names.find(function (name) { return name.indexOf('灵石') < 0; })
      || names[0]
      || '';
  }

  function availableBatchSourceRows() {
    const state = transferState();
    const targetId = Number(state.targetId) || 0;
    return rows().filter(function (row) {
      const id = Number(row.identity_id) || 0;
      return id && id !== targetId && (state.includeProtected || !row.protected);
    });
  }

  function selectedBatchSourceIds() {
    const state = transferState();
    const availableIds = new Set(availableBatchSourceRows().map(function (row) {
      return Number(row.identity_id) || 0;
    }).filter(Boolean));
    return (Array.isArray(state.batchSourceIds) ? state.batchSourceIds : []).map(function (id) {
      return Number(id) || 0;
    }).filter(function (id, index, list) {
      return id && availableIds.has(id) && list.indexOf(id) === index;
    });
  }

  function effectiveBatchSourceRows() {
    const available = availableBatchSourceRows();
    if (transferState().batchAllSources) return available;
    const selected = new Set(selectedBatchSourceIds());
    return available.filter(function (row) {
      return selected.has(Number(row.identity_id) || 0);
    });
  }

  function batchItemStats(itemName) {
    const holders = effectiveBatchSourceRows().map(function (row) {
      return {
        row: row,
        count: Number(((row || {}).items || {})[itemName] || 0),
      };
    }).filter(function (holder) {
      return holder.count > 0;
    });
    const total = holders.reduce(function (sum, holder) { return sum + holder.count; }, 0);
    return {
      total: total,
      holderCount: holders.length,
      holderLabels: holders.slice(0, 3).map(function (holder) { return rowLabel(holder.row); }),
    };
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
    const giftMode = isGiftMode();
    const merged = new Map();
    selectedSnapshotItems().concat(parseManualItems(state.manualText)).forEach(function (item) {
      const name = String(item.item_name || '').trim();
      if (!name) return;
      merged.set(name, (Number(merged.get(name) || 0) + Number(item.quantity || 0)));
    });
    if (state.batchMode) {
      const payload = {
        batch: true,
        operation: state.operation,
        target_identity_id: Number(state.targetId) || 0,
        listing_item: giftMode ? '' : String(state.listingItem || '').trim(),
        listing_count: Math.max(1, Number(state.listingCount) || 1),
        listing_unit_price: Math.max(0, Number(state.listingUnitPrice) || 0),
        listing_syntax: String(state.listingSyntax || 'space') === 'compact' ? 'compact' : 'space',
        mode: String(state.batchQuantityMode || 'all') === 'fixed' ? 'fixed' : 'all',
        reserve_count: Math.max(0, Number(state.batchReserveCount) || 0),
        min_transfer_count: Math.max(1, Number(state.batchMinTransferCount) || 1),
        items: Array.from(merged.entries()).map(function (entry) {
          return { item_name: entry[0], quantity: entry[1] };
        }),
        include_protected: !!state.includeProtected,
        continue_on_error: !!state.continueOnError,
      };
      if (!state.batchAllSources) payload.source_identity_ids = selectedBatchSourceIds();
      return payload;
    }
    return {
      operation: state.operation,
      source_identity_id: Number(state.sourceId) || 0,
      target_identity_id: Number(state.targetId) || 0,
      listing_item: giftMode ? '' : String(state.listingItem || '').trim(),
      listing_count: Math.max(1, Number(state.listingCount) || 1),
      listing_unit_price: Math.max(0, Number(state.listingUnitPrice) || 0),
      listing_syntax: String(state.listingSyntax || 'space') === 'compact' ? 'compact' : 'space',
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
    if (!Number(state.listingCount || 0)) state.listingCount = 1;
    if (['space', 'compact'].indexOf(String(state.listingSyntax || 'space')) < 0) state.listingSyntax = 'space';
    if (!state.batchAllSources) {
      const selected = selectedBatchSourceIds();
      state.batchSourceIds = selected.length ? selected : availableBatchSourceRows().map(function (row) {
        return Number(row.identity_id) || 0;
      }).filter(Boolean);
    }
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
    state.listingCount = 1;
    state.listingSyntax = 'space';
    state.batchReserveCount = Math.max(0, Number(state.batchReserveCount) || 0);
    state.batchMinTransferCount = Math.max(1, Number(state.batchMinTransferCount) || 1);
    state.selectedItems = {};
    state.manualText = '';
    state.preview = null;
    state.busy = false;
    if (!state.batchAllSources) {
      state.batchSourceIds = availableBatchSourceRows().map(function (row) {
        return Number(row.identity_id) || 0;
      }).filter(Boolean);
    }
  }

  function applyMoneyPreset() {
    const state = transferState();
    state.listingItem = '黄芽丹';
    state.listingCount = 20;
    state.listingUnitPrice = 30000;
    state.listingSyntax = 'compact';
    state.selectedItems = { '灵石': 600000 };
    state.manualText = '';
    state.batchQuantityMode = 'fixed';
    state.preview = null;
    resetTransferPreviewOnly();
    renderTransferPanel();
  }

  function storageBagViewState() {
    if (typeof appState === 'undefined') return { query: '', tag: 'all', flag: 'all', sort: 'group', focusItem: '', focusIdentityId: 0 };
    if (!appState.storageBagView) {
      appState.storageBagView = { query: '', tag: 'all', flag: 'all', sort: 'group', focusItem: '', focusIdentityId: 0 };
    }
    const state = appState.storageBagView;
    state.query = String(state.query || '');
    state.tag = String(state.tag || 'all');
    state.flag = String(state.flag || 'all');
    state.sort = String(state.sort || 'group');
    state.focusItem = String(state.focusItem || '');
    state.focusIdentityId = Number(state.focusIdentityId || 0) || 0;
    return state;
  }

  function identityLabel(row) {
    return String((row || {}).label || (row || {}).display_name || (row || {}).identity_id || '').trim();
  }

  function identityTotal(row) {
    const itemMap = (row || {}).items || {};
    return Object.keys(itemMap).reduce(function (sum, name) {
      return sum + Number(itemMap[name] || 0);
    }, 0);
  }

  function identityUniqueCount(row) {
    const itemMap = (row || {}).items || {};
    return Object.keys(itemMap).filter(function (name) { return Number(itemMap[name] || 0) > 0; }).length;
  }

  function itemTags(itemName) {
    const name = String(itemName || '').trim();
    for (const rule of STORAGE_BAG_EXPLICIT_TAGS) {
      if (rule.test(name)) return [rule.tag];
    }
    const rule = itemRule(itemName);
    const tags = Array.isArray(rule.tags) ? rule.tags.map(function (tag) { return String(tag || '').trim(); }).filter(Boolean) : [];
    return tags.length ? tags : ['未知'];
  }

  function primaryItemTag(itemName) {
    return itemTags(itemName)[0] || '未知';
  }

  function pinnedItemRank(itemName) {
    const index = STORAGE_BAG_PINNED_ITEMS.indexOf(String(itemName || ''));
    return index >= 0 ? index : 999;
  }

  function comparePinnedItems(aName, bName) {
    const aRank = pinnedItemRank(aName);
    const bRank = pinnedItemRank(bName);
    if (aRank !== bRank) return aRank - bRank;
    return 0;
  }

  function buildStorageBagEntries(currentRows, currentItems, totals) {
    return currentItems.map(function (itemName) {
      const rule = itemRule(itemName);
      const total = Number(totals[itemName] || 0);
      const holders = currentRows.map(function (row) {
        return { row: row, count: Number(((row || {}).items || {})[itemName] || 0) };
      }).filter(function (holder) {
        return holder.count > 0;
      }).sort(function (a, b) {
        return b.count - a.count || identityLabel(a.row).localeCompare(identityLabel(b.row), 'zh-Hans-CN');
      });
      const topHolder = holders[0] || null;
      const concentration = total > 0 && topHolder ? topHolder.count / total : 0;
      const tags = itemTags(itemName);
      return {
        name: itemName,
        total: total,
        rule: rule,
        tags: tags,
        tag: tags[0] || '未知',
        method: String(rule.method || 'unknown'),
        methodLabel: String(rule.method_label || methodLabel(rule.method)),
        holders: holders,
        holderCount: holders.length,
        topHolder: topHolder,
        concentration: concentration,
        protectedHeld: holders.some(function (holder) { return !!holder.row.protected; }),
      };
    });
  }

  function matchesStorageBagQuery(entry, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return true;
    if (entry.name.toLowerCase().indexOf(q) >= 0) return true;
    if (entry.tags.some(function (tag) { return tag.toLowerCase().indexOf(q) >= 0; })) return true;
    if (entry.methodLabel.toLowerCase().indexOf(q) >= 0) return true;
    return entry.holders.some(function (holder) {
      return identityLabel(holder.row).toLowerCase().indexOf(q) >= 0
        || String(holder.row.identity_id || '').indexOf(q) >= 0;
    });
  }

  function matchesStorageBagFlag(entry, flag) {
    if (flag === 'uncategorized') return entry.method === 'unknown' || entry.tags.indexOf('未知') >= 0;
    if (flag === 'concentrated') return entry.total > 0 && entry.concentration >= 0.7;
    if (flag === 'single') return entry.holderCount === 1;
    if (flag === 'protected') return entry.protectedHeld;
    return true;
  }

  function sortStorageBagEntries(entries, view, defaultTags) {
    const tagOrder = new Map((defaultTags || []).map(function (tag, index) { return [String(tag), index]; }));
    const tagRank = function (tag) { return tagOrder.has(tag) ? tagOrder.get(tag) : 999; };
    const sorted = entries.slice();
    sorted.sort(function (a, b) {
      const pinned = comparePinnedItems(a.name, b.name);
      if (pinned) return pinned;
      if (view.sort === 'total') return b.total - a.total || a.name.localeCompare(b.name, 'zh-Hans-CN');
      if (view.sort === 'concentration') return b.concentration - a.concentration || b.total - a.total || a.name.localeCompare(b.name, 'zh-Hans-CN');
      if (view.sort === 'name') return a.name.localeCompare(b.name, 'zh-Hans-CN');
      return tagRank(a.tag) - tagRank(b.tag) || b.total - a.total || a.name.localeCompare(b.name, 'zh-Hans-CN');
    });
    return sorted;
  }

  function visibleStorageBagEntries(entries, view, data) {
    const filtered = entries.filter(function (entry) {
      if (!matchesStorageBagQuery(entry, view.query)) return false;
      if (view.tag !== 'all' && entry.tags.indexOf(view.tag) < 0) return false;
      return matchesStorageBagFlag(entry, view.flag);
    });
    return sortStorageBagEntries(filtered, view, data.default_tags || []);
  }

  function groupStorageBagEntries(entries) {
    const groups = [];
    const byTag = new Map();
    entries.forEach(function (entry) {
      if (!byTag.has(entry.tag)) {
        const group = { tag: entry.tag, entries: [], total: 0, concentrated: 0 };
        byTag.set(entry.tag, group);
        groups.push(group);
      }
      const group = byTag.get(entry.tag);
      group.entries.push(entry);
      group.total += entry.total;
      if (entry.concentration >= 0.7) group.concentrated += 1;
    });
    return groups;
  }

  function formatShortCount(value) {
    const n = Number(value || 0);
    if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1).replace(/\.0$/, '')}亿`;
    if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1).replace(/\.0$/, '')}万`;
    return n.toLocaleString();
  }

  function renderStatChip(label, value, note, attrs, active) {
    const tag = attrs ? 'button' : 'div';
    const attrText = attrs ? ` type="button" ${attrs}` : '';
    return `<${tag}${attrText} class="storage-bag-stat-chip${active ? ' active' : ''}"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</${tag}>`;
  }

  function renderStorageBagOverview(entries, visibleEntries, currentRows) {
    const panel = document.getElementById('storage-bag-overview-panel');
    if (!panel) return;
    const view = storageBagViewState();
    const protectedCount = currentRows.filter(function (row) { return row.protected; }).length;
    const totalQuantity = entries.reduce(function (sum, entry) { return sum + entry.total; }, 0);
    const uncategorizedCount = entries.filter(function (entry) { return entry.method === 'unknown' || entry.tags.indexOf('未知') >= 0; }).length;
    const concentratedCount = entries.filter(function (entry) { return entry.total > 0 && entry.concentration >= 0.7; }).length;
    const singleCount = entries.filter(function (entry) { return entry.holderCount === 1; }).length;
    const latestRow = currentRows.slice().sort(function (a, b) { return Number(b.updated_at_raw || 0) - Number(a.updated_at_raw || 0); })[0] || null;
    const topItem = entries.slice().sort(function (a, b) { return b.total - a.total; })[0] || null;
    const topIdentity = currentRows.slice().sort(function (a, b) { return identityTotal(b) - identityTotal(a); })[0] || null;
    panel.innerHTML = `
      <div class="storage-bag-overview-grid">
        ${renderStatChip('身份', currentRows.length, protectedCount ? `保护 ${protectedCount}` : '')}
        ${renderStatChip('物品', entries.length, `当前 ${visibleEntries.length}`)}
        ${renderStatChip('总量', formatShortCount(totalQuantity), topItem ? `最多 ${topItem.name}` : '')}
        ${renderStatChip('主仓', topIdentity ? identityLabel(topIdentity) : '-', topIdentity ? formatShortCount(identityTotal(topIdentity)) : '')}
        ${renderStatChip('未分类', uncategorizedCount, '规则待补', 'data-storage-bag-flag="uncategorized"', view.flag === 'uncategorized')}
        ${renderStatChip('高集中', concentratedCount, '70%以上', 'data-storage-bag-flag="concentrated"', view.flag === 'concentrated')}
        ${renderStatChip('独占', singleCount, '单身份持有', 'data-storage-bag-flag="single"', view.flag === 'single')}
        ${renderStatChip('最近更新', latestRow ? latestRow.updated_at : '-', latestRow ? identityLabel(latestRow) : '')}
      </div>`;
  }

  function updateStorageBagVisibleCount(visibleCount, totalCount) {
    const count = document.querySelector('.storage-bag-visible-count');
    if (count) count.textContent = `${visibleCount} / ${totalCount} 项`;
  }

  function renderStorageBagToolbar(entries, visibleEntries, data) {
    const toolbar = document.getElementById('storage-bag-toolbar');
    if (!toolbar) return;
    const view = storageBagViewState();
    const tagStats = new Map();
    entries.forEach(function (entry) {
      tagStats.set(entry.tag, (tagStats.get(entry.tag) || 0) + 1);
    });
    const defaultTags = (data.default_tags || []).filter(function (tag) { return tagStats.has(tag); });
    const extraTags = Array.from(tagStats.keys()).filter(function (tag) { return defaultTags.indexOf(tag) < 0; }).sort(function (a, b) { return a.localeCompare(b, 'zh-Hans-CN'); });
    const tagChips = ['all'].concat(defaultTags, extraTags).map(function (tag) {
      const label = tag === 'all' ? '全部' : tag;
      const count = tag === 'all' ? entries.length : (tagStats.get(tag) || 0);
      return `<button type="button" class="storage-bag-filter-chip${view.tag === tag ? ' active' : ''}" data-storage-bag-filter-tag="${esc(tag)}">${esc(label)}<strong>${esc(count)}</strong></button>`;
    }).join('');
    const flagChips = [
      ['all', '全局'],
      ['uncategorized', '未分类'],
      ['concentrated', '高集中'],
      ['single', '独占'],
      ['protected', '保护号'],
    ].map(function (item) {
      return `<button type="button" class="storage-bag-filter-chip${view.flag === item[0] ? ' active' : ''}" data-storage-bag-flag="${esc(item[0])}">${esc(item[1])}</button>`;
    }).join('');
    toolbar.innerHTML = `
      <div class="storage-bag-filter-row">
        <input class="text-input storage-bag-search" data-storage-bag-search="1" value="${esc(view.query)}" placeholder="搜索物品、身份、标签" />
        <select class="text-input storage-bag-sort" data-storage-bag-sort="1">
          <option value="group"${view.sort === 'group' ? ' selected' : ''}>按标签分组</option>
          <option value="total"${view.sort === 'total' ? ' selected' : ''}>按总量</option>
          <option value="concentration"${view.sort === 'concentration' ? ' selected' : ''}>按集中度</option>
          <option value="name"${view.sort === 'name' ? ' selected' : ''}>按名称</option>
        </select>
        <span class="storage-bag-visible-count">${esc(visibleEntries.length)} / ${esc(entries.length)} 项</span>
      </div>
      <div class="storage-bag-filter-chips">${tagChips}</div>
      <div class="storage-bag-filter-chips storage-bag-filter-flags">${flagChips}</div>`;
  }

  function renderStorageBagDetailPanel(entries, visibleEntries, currentRows) {
    const panel = document.getElementById('storage-bag-detail-panel');
    if (!panel) return;
    const view = storageBagViewState();
    const visibleFocusItem = visibleEntries.find(function (entry) { return entry.name === view.focusItem; }) || null;
    const fallbackItem = visibleEntries[0] || entries[0] || null;
    if ((!entries.some(function (entry) { return entry.name === view.focusItem; }) || (visibleEntries.length && !visibleFocusItem)) && fallbackItem) {
      view.focusItem = fallbackItem.name;
    }
    const focusItem = entries.find(function (entry) { return entry.name === view.focusItem; }) || fallbackItem;
    const fallbackIdentity = currentRows.slice().sort(function (a, b) { return identityTotal(b) - identityTotal(a); })[0] || null;
    if (!currentRows.some(function (row) { return Number(row.identity_id || 0) === Number(view.focusIdentityId || 0); }) && fallbackIdentity) {
      view.focusIdentityId = Number(fallbackIdentity.identity_id || 0) || 0;
    }
    const focusIdentity = currentRows.find(function (row) { return Number(row.identity_id || 0) === Number(view.focusIdentityId || 0); }) || fallbackIdentity;
    const holderRows = focusItem ? focusItem.holders.slice(0, 8).map(function (holder) {
      const pct = focusItem.total > 0 ? Math.round(holder.count / focusItem.total * 100) : 0;
      return `<div class="storage-bag-detail-row"><span>${esc(identityLabel(holder.row))}</span><strong>${esc(holder.count.toLocaleString())}</strong><small>${esc(pct)}%</small></div>`;
    }).join('') : '<div class="queue-empty">暂无物品焦点。</div>';
    const identityItems = focusIdentity ? Object.keys((focusIdentity.items || {})).map(function (name) {
      return { name: name, count: Number(focusIdentity.items[name] || 0) };
    }).filter(function (item) { return item.count > 0; }).sort(function (a, b) { return b.count - a.count || a.name.localeCompare(b.name, 'zh-Hans-CN'); }) : [];
    const identityRows = identityItems.slice(0, 8).map(function (item) {
      return `<div class="storage-bag-detail-row"><span>${esc(item.name)}</span><strong>${esc(item.count.toLocaleString())}</strong><small>${esc(primaryItemTag(item.name))}</small></div>`;
    }).join('') || '<div class="queue-empty">暂无身份焦点。</div>';
    panel.innerHTML = `
      <div class="storage-bag-detail-grid">
        <section class="storage-bag-detail-card">
          <div class="storage-bag-detail-head"><span>聚焦物品</span><strong>${esc(focusItem ? focusItem.name : '-')}</strong></div>
          <div class="storage-bag-detail-meta">
            ${focusItem ? `<span>${esc(focusItem.tag)}</span><span>${esc(focusItem.methodLabel)}</span><span>总量 ${esc(focusItem.total.toLocaleString())}</span><span>${esc(focusItem.holderCount)} 个身份</span>` : ''}
          </div>
          <div class="storage-bag-detail-list">${holderRows}</div>
        </section>
        <section class="storage-bag-detail-card">
          <div class="storage-bag-detail-head"><span>聚焦身份</span><strong>${esc(focusIdentity ? identityLabel(focusIdentity) : '-')}</strong></div>
          <div class="storage-bag-detail-meta">
            ${focusIdentity ? `<span>${focusIdentity.protected ? '保护号' : '可同步'}</span><span>${esc(focusIdentity.updated_at || '未解析')}</span><span>${esc(identityUniqueCount(focusIdentity))} 项</span><span>总量 ${esc(identityTotal(focusIdentity).toLocaleString())}</span>` : ''}
          </div>
          <div class="storage-bag-detail-list">${identityRows}</div>
        </section>
      </div>`;
  }

  function renderStorageBagTable(options) {
    options = options || {};
    const wrap = document.getElementById('storage-bag-table-wrap');
    if (!wrap) return;
    const data = storageData();
    const currentRows = rows();
    const currentItems = items();
    const totals = data.totals || {};
    const syncIds = selectedSyncIds();
    renderSyncControls();
    const view = storageBagViewState();
    const allEntries = currentRows.length ? buildStorageBagEntries(currentRows, currentItems, totals) : [];
    const visibleEntries = currentRows.length ? visibleStorageBagEntries(allEntries, view, data) : [];
    if (!currentRows.length) {
      wrap.innerHTML = '<div class="queue-empty">暂无身份。</div>';
    } else {
      const groups = groupStorageBagEntries(visibleEntries);
      const header = currentRows.map(function (row) {
        const id = Number(row.identity_id) || 0;
        const checkbox = row.protected ? '' : `<input type="checkbox" name="storage_bag_identity_id" value="${esc(id)}"${syncIds.has(id) ? ' checked' : ''} />`;
        const protectedText = row.protected ? '<span class="storage-bag-protected">保护</span>' : '';
        const activeClass = Number(view.focusIdentityId || 0) === id ? ' storage-bag-col-active' : '';
        return `<th class="${activeClass}" data-storage-bag-identity-focus="${esc(id)}"><label class="storage-bag-head">${checkbox}<span>${esc(row.label || row.display_name || id)}</span></label><small>${esc(identityUniqueCount(row))}项 / ${esc(formatShortCount(identityTotal(row)))} ${protectedText}</small><small>${esc(row.updated_at || '未解析')}</small></th>`;
      }).join('');
      const body = groups.length ? groups.map(function (group) {
        const groupHeader = `<tr class="storage-bag-category" data-storage-bag-filter-tag="${esc(group.tag)}"><th colspan="${2 + currentRows.length}"><span>${esc(group.tag)}</span><small>${esc(group.entries.length)}项 ｜ 总量 ${esc(group.total.toLocaleString())}${group.concentrated ? ` ｜ 高集中 ${esc(group.concentrated)}` : ''}</small></th></tr>`;
        const rowsHtml = group.entries.map(function (entry) {
          const rowActive = view.focusItem === entry.name ? ' storage-bag-row-active' : '';
          const itemMeta = `<div class="storage-bag-item-meta">${entry.tags.map(function (tag) { return `<span>${esc(tag)}</span>`; }).join('')}<span>${esc(entry.methodLabel)}</span><span>${esc(entry.holderCount)}人</span>${entry.concentration >= 0.7 ? '<span>高集中</span>' : ''}</div>`;
          return `<tr class="storage-bag-item-row${rowActive}" data-storage-bag-item-focus="${esc(entry.name)}"><th class="storage-bag-item-cell"><div class="storage-bag-item-name">${esc(entry.name)}</div>${itemMeta}</th><td class="storage-bag-total">${entry.total ? esc(entry.total.toLocaleString()) : ''}</td>${currentRows.map(function (row) {
            const count = Number((row.items || {})[entry.name] || 0);
            const colActive = Number(view.focusIdentityId || 0) === Number(row.identity_id || 0) ? ' storage-bag-col-active' : '';
            return `<td class="${count ? 'storage-bag-cell-filled' : ''}${colActive}">${count ? esc(count.toLocaleString()) : ''}</td>`;
          }).join('')}</tr>`;
        }).join('');
        return groupHeader + rowsHtml;
      }).join('') : `<tr><th>暂无匹配物品</th><td></td>${currentRows.map(function () { return '<td></td>'; }).join('')}</tr>`;
      wrap.innerHTML = `<table class="storage-bag-table"><thead><tr><th>物品</th><th>总量</th>${header}</tr></thead><tbody>${body}</tbody></table>`;
    }
    renderStorageBagOverview(allEntries, visibleEntries, currentRows);
    if (options.preserveToolbar) updateStorageBagVisibleCount(visibleEntries.length, allEntries.length);
    else renderStorageBagToolbar(allEntries, visibleEntries, data);
    renderStorageBagDetailPanel(allEntries, visibleEntries, currentRows);
    renderStorageBagApiPanel();
  }

  function renderStorageBagSearchResults() {
    renderStorageBagTable({ preserveToolbar: true });
  }

  function renderStorageBagApiPanel() {
    const panel = document.getElementById('storage-bag-api-panel');
    if (!panel) return;
    const api = storageApiData();
    const tokenPlaceholder = api.api_token_configured ? '已配置，留空不变' : '';
    const cookiePlaceholder = api.cookie_configured ? '已配置，留空不变' : '';
    const verifyLine = api.verified
      ? `<span class="storage-bag-api-status">已验证 ${esc(api.verified_at || '-')} ｜ 保活 ${api.keepalive_enabled ? '已启用' : '未启用'}${api.next_keepalive_at && api.next_keepalive_at !== '-' ? ' ｜ 下次 ' + esc(api.next_keepalive_at) : ''}</span>`
      : `<span class="storage-bag-api-status storage-bag-api-status-error">未验证</span>`;
    const keepaliveError = api.last_keepalive_error
      ? `<span class="storage-bag-api-status storage-bag-api-status-error">保活：${esc(api.last_keepalive_error)}</span>`
      : '';
    const lastLine = api.last_message
      ? `<span class="storage-bag-api-status${api.last_ok ? '' : ' storage-bag-api-status-error'}">${esc(api.last_message)}${api.last_updated_at && api.last_updated_at !== '-' ? ' ｜ ' + esc(api.last_updated_at) : ''}</span>`
      : '';
    panel.innerHTML = `
      <form id="storage-bag-api-form" class="storage-bag-api-form">
        <div class="storage-bag-api-grid">
          <label class="field-label">Base URL<input class="text-input" name="storage_bag_api_base_url" value="${esc(api.base_url || 'https://asc.aiopenai.app')}" autocomplete="off" /></label>
          <label class="field-label">API Token<input class="text-input" name="storage_bag_api_token" type="password" placeholder="${esc(tokenPlaceholder || '可留空，验证时自动读取')}" autocomplete="new-password" /></label>
          <label class="field-label storage-bag-api-cookie">Cookie<input class="text-input" name="storage_bag_api_cookie" type="password" placeholder="${esc(cookiePlaceholder || 'session=...')}" autocomplete="new-password" /></label>
        </div>
        <div class="storage-bag-api-actions">
          <button type="button" class="btn btn-secondary" data-storage-bag-api-save="1">保存 API</button>
          <button type="button" class="btn btn-secondary" data-storage-bag-api-verify="1"${api.running ? ' disabled' : ''}>${api.running ? '验证中' : '验证'}</button>
          <button type="button" class="btn" data-storage-bag-api-refresh="1"${api.running || !api.configured ? ' disabled' : ''}>${api.running ? '读取中' : 'API读取'}</button>
          ${verifyLine}
          ${keepaliveError}
          ${lastLine}
        </div>
      </form>`;
  }

  function renderSyncControls() {
    const sync = snapshot().storage_bag_sync || {};
    const transfer = snapshot().storage_bag_transfer || {};
    const transferBatch = transfer.batch || {};
    const running = !!sync.running;
    const transferRunning = !!transfer.running || !!transferBatch.running;
    const runningLabel = transfer.operation === 'gift' || transferBatch.operation === 'gift' ? '赠送中' : '转移中';
    const selectable = rows().filter(function (row) { return !row.protected; });
    const selected = selectedSyncIds();
    const selectedCount = selectable.filter(function (row) { return selected.has(Number(row.identity_id) || 0); }).length;
    const btn = document.getElementById('storage-bag-sync-btn');
    const selectAll = document.getElementById('storage-bag-select-all');
    const status = document.getElementById('storage-bag-sync-status');
    if (btn) {
      btn.disabled = running || transferRunning;
      btn.textContent = running ? '同步中' : (transferRunning ? runningLabel : '同步');
    }
    if (selectAll) {
      selectAll.checked = selectable.length > 0 && selectedCount === selectable.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < selectable.length;
      selectAll.disabled = running || transferRunning || selectable.length <= 0;
    }
    if (status) {
      const pending = Array.isArray(sync.pending_ids) ? sync.pending_ids.length : 0;
      const completed = Array.isArray(sync.completed_ids) ? sync.completed_ids.length : 0;
      status.textContent = running ? `排队 ${pending}，已发送 ${completed}` : (transferRunning ? `${runningLabel}暂停同步` : '');
    }
  }

  function renderTransferWarnings(preview) {
    return preview && Array.isArray(preview.warnings) && preview.warnings.length
      ? `<div class="storage-bag-transfer-warnings">${preview.warnings.map(function (item) { return `提示：${esc(item)}`; }).join('<br>')}</div>`
      : '';
  }

  function renderTransferPreviewHtml(preview) {
    if (!preview) return '<div class="queue-empty">尚未生成预览。</div>';
    if (Array.isArray(preview.tasks)) {
      const planLines = Array.isArray(preview.item_plans) ? preview.item_plans.map(function (plan) {
        const demand = Number(plan.requested_quantity || 0) > 0 ? `需求${Number(plan.requested_quantity || 0).toLocaleString()}` : '搬空可搬';
        const aggregate = Number(plan.aggregate_listing_count || 0) > 0
          ? `｜聚合${Number(plan.aggregate_listing_count || 0).toLocaleString()}份｜单价${Number(plan.aggregate_unit_price || 0).toLocaleString()}｜总价${Number(plan.aggregate_total_price || 0).toLocaleString()}｜实搬${Number(plan.aggregate_planned_quantity || 0).toLocaleString()}`
          : '';
        return `${plan.item_name}｜${demand}｜计划${Number(plan.planned_quantity || 0).toLocaleString()}｜来源${Number(plan.used_source_count || 0)}/${Number(plan.candidate_count || 0)}｜保留${Number(plan.reserve_count || 0)}｜起送${Number(plan.min_transfer_count || 1)}${aggregate}`;
      }) : [];
      const taskLines = preview.tasks.map(function (task, index) {
        const itemText = (task.items || []).map(function (item) {
          return `${item.item_name}x${Number(item.quantity || 0).toLocaleString()}`;
        }).join('、');
        const commandText = task.listing_command ? `｜${task.listing_command}` : '';
        const buyers = Array.isArray(task.aggregate_buyers) && task.aggregate_buyers.length
          ? `\n   购买分摊：${task.aggregate_buyers.map(function (buyer) {
              const buyerItem = ((buyer.items || [])[0] || {});
              return `${buyer.source_label || buyer.source_identity_id}买${Number(buyer.listing_count || 1)}份/${Number(buyerItem.quantity || 0).toLocaleString()}`;
            }).join('；')}`
          : '';
        return `${index + 1}. ${task.source_label || task.source_identity_id} -> ${task.target_label || task.target_identity_id}｜${itemText}${commandText}${buyers}`;
      });
      const skipped = Array.isArray(preview.skipped_source_ids) && preview.skipped_source_ids.length
        ? `<div class="form-label">已跳过无匹配库存来源：${esc(preview.skipped_source_ids.join('、'))}</div>`
        : '';
      const plans = planLines.length ? `<pre>${esc(planLines.join('\n'))}</pre>` : '';
      return `<div class="storage-bag-preview-summary">${esc(preview.summary || '已生成批量预览')}</div>${renderTransferWarnings(preview)}${plans}<pre>${esc(taskLines.join('\n'))}</pre>${skipped}`;
    }
    return `<div class="storage-bag-preview-summary">${esc(preview.summary || '已生成预览')}</div>${renderTransferWarnings(preview)}<pre>${esc((preview.commands || []).map(function (cmd) { return `${cmd.identity_id}｜${cmd.command}｜${cmd.note || ''}`; }).join('\n'))}</pre>`;
  }

  function storageTransferTaskLine(task, index) {
    const prefix = Number.isFinite(index) ? `${index}. ` : '';
    const itemText = ((task || {}).items || []).map(function (item) {
      return `${item.item_name}x${Number(item.quantity || 0).toLocaleString()}`;
    }).join('、') || '无物品';
    const listing = task && task.listing_item ? `｜上架 ${task.listing_item}x${Math.max(1, Number(task.listing_count) || 1)}` : '';
    const aggregate = Array.isArray((task || {}).aggregate_buyers) && (task || {}).aggregate_buyers.length
      ? `｜聚合购买${(task || {}).aggregate_buyers.length}源`
      : '';
    return `${prefix}${(task || {}).source_label || (task || {}).source_identity_id || '来源'} -> ${(task || {}).target_label || (task || {}).target_identity_id || '目标'}｜${itemText}${listing}${aggregate}`;
  }

  function renderBatchRuntimeHtml(batchRuntime) {
    const rawLogs = Array.isArray((batchRuntime || {}).logs) ? batchRuntime.logs : [];
    const status = String((batchRuntime || {}).status || '');
    if (!batchRuntime || (!batchRuntime.running && (!status || status === 'idle') && !rawLogs.length && !batchRuntime.last_message)) return '';
    const completed = Array.isArray(batchRuntime.completed) ? batchRuntime.completed.length : 0;
    const failed = Array.isArray(batchRuntime.failed) ? batchRuntime.failed.length : 0;
    const queue = Array.isArray(batchRuntime.queue) ? batchRuntime.queue : [];
    const queued = queue.length;
    const total = Number(batchRuntime.total || 0);
    const active = batchRuntime.active_task || null;
    const statusLine = [
      batchRuntime.running ? '批量运行中' : status,
      total ? `完成 ${completed}/${total}` : '',
      queued ? `待跑 ${queued}` : '',
      failed ? `失败 ${failed}` : '',
      active ? `当前 ${active.source_label || active.source_identity_id}` : '',
    ].filter(Boolean).join('｜');
    const logs = rawLogs.slice(-10).map(function (log) {
      return `${log.ts || ''} ${log.message || ''}`;
    }).join('\n');
    const queueLimit = 20;
    const activeHtml = active
      ? `<div class="storage-bag-transfer-runtime-section"><div class="form-label">当前执行</div><div class="storage-bag-transfer-queue-line">${esc(storageTransferTaskLine(active))}</div></div>`
      : '';
    const queueLines = queue.slice(0, queueLimit).map(function (task, index) {
      return `<li>${esc(storageTransferTaskLine(task, index + 1))}</li>`;
    }).join('');
    const moreLine = queued > queueLimit ? `<div class="form-label">还有 ${esc(queued - queueLimit)} 条未展开</div>` : '';
    const queueHtml = queued
      ? `<div class="storage-bag-transfer-runtime-section"><div class="form-label">后续队列 ${esc(queued)} 条</div><ol class="storage-bag-transfer-queue-list">${queueLines}</ol>${moreLine}</div>`
      : '';
    return `<div class="storage-bag-transfer-runtime"><div>${esc(statusLine || batchRuntime.last_message || '')}</div>${activeHtml}${queueHtml}${logs ? `<pre>${esc(logs)}</pre>` : ''}</div>`;
  }

  function renderTransferPanel() {
    const panel = document.getElementById('storage-bag-transfer-panel');
    if (!panel) return;
    const previousTableWrap = panel.querySelector('.storage-bag-transfer-table-wrap');
    const previousPreview = panel.querySelector('#storage-bag-transfer-preview');
    const previousTableScrollTop = previousTableWrap ? previousTableWrap.scrollTop : 0;
    const previousTableScrollLeft = previousTableWrap ? previousTableWrap.scrollLeft : 0;
    const previousPreviewScrollTop = previousPreview ? previousPreview.scrollTop : 0;
    const active = document.activeElement;
    const activeField = active && panel.contains(active) ? active.getAttribute('data-storage-transfer-field') : '';
    const activeQty = active && panel.contains(active) ? active.getAttribute('data-storage-transfer-qty') : '';
    const activeName = active && panel.contains(active) ? active.getAttribute('name') : '';
    const activeSelectionStart = active && typeof active.selectionStart === 'number' ? active.selectionStart : null;
    const activeSelectionEnd = active && typeof active.selectionEnd === 'number' ? active.selectionEnd : null;
    normalizeTransferDefaults();
    const state = transferState();
    const giftMode = isGiftMode();
    const runtime = snapshot().storage_bag_transfer || {};
    const batchRuntime = runtime.batch || {};
    const syncRuntime = snapshot().storage_bag_sync || {};
    const sourceRow = rowById(state.sourceId);
    const sourceRows = effectiveBatchSourceRows();
    const sourceItems = state.batchMode
      ? items().filter(function (name) {
        const rule = itemRule(name);
        return batchItemStats(name).total > 0 && String(rule.method || 'unknown') !== 'blocked';
      })
      : items().filter(function (name) {
        const rule = itemRule(name);
        return itemCount(state.sourceId, name) > 0 && String(rule.method || 'unknown') !== 'blocked';
      });
    const itemRows = sourceItems.length ? sourceItems.map(function (name) {
      const rule = itemRule(name);
      const checked = Object.prototype.hasOwnProperty.call(state.selectedItems, name);
      if (state.batchMode) {
        const stats = batchItemStats(name);
        const qty = checked ? Number(state.selectedItems[name] || stats.total || 1) : (stats.total || 1);
        const holderText = `${stats.holderCount}号${stats.holderLabels.length ? `｜${stats.holderLabels.join('、')}${stats.holderCount > stats.holderLabels.length ? '…' : ''}` : ''}`;
        return `<tr><td><input type="checkbox" name="storage_bag_transfer_item" value="${esc(name)}"${checked ? ' checked' : ''} /></td><th>${esc(name)}</th><td>${esc(giftMode ? '赠送' : (rule.method_label || methodLabel(rule.method)))}</td><td>${esc(stats.total.toLocaleString())}</td><td>${esc(holderText)}</td><td><input class="text-input storage-bag-qty-input" type="number" min="1" name="storage_bag_transfer_qty" data-storage-transfer-qty="${esc(name)}" value="${esc(qty)}"${checked ? '' : ' disabled'} /></td></tr>`;
      }
      const count = itemCount(state.sourceId, name);
      const qty = checked ? Number(state.selectedItems[name] || count) : count;
      return `<tr><td><input type="checkbox" name="storage_bag_transfer_item" value="${esc(name)}"${checked ? ' checked' : ''} /></td><th>${esc(name)}</th><td>${esc(giftMode ? '赠送' : (rule.method_label || methodLabel(rule.method)))}</td><td>${esc(count.toLocaleString())}</td><td><input class="text-input storage-bag-qty-input" type="number" min="1" name="storage_bag_transfer_qty" data-storage-transfer-qty="${esc(name)}" value="${esc(qty)}"${checked ? '' : ' disabled'} /></td></tr>`;
    }).join('') : `<tr><td colspan="${state.batchMode ? '6' : '5'}" class="storage-bag-empty-cell">${state.batchMode ? '当前来源范围暂无可转移物品，可直接使用手填清单。' : '来源快照暂无可转移物品，可直接使用手填清单。'}</td></tr>`;
    const preview = state.preview;
    const logs = Array.isArray(runtime.logs) ? runtime.logs.slice(-8).map(function (log) {
      return `${log.ts || ''} ${log.message || ''}`;
    }).join('\n') : '';
    const transferRunning = !!runtime.running || !!batchRuntime.running;
    const busy = !!state.busy;
    const startPending = Number(state.startPending || 0) > 0;
    const syncBusy = !!syncRuntime.running;
    const sourceLabel = sourceRow ? (sourceRow.label || sourceRow.display_name || state.sourceId) : '来源';
    const title = document.getElementById('storage-bag-transfer-title');
    const note = document.getElementById('storage-bag-transfer-note');
    if (title) title.textContent = giftMode ? '储物袋赠送' : '储物袋转移';
    if (note) note.textContent = giftMode ? '目标号先发定位消息，来源号回复该消息发送 .赠送；可用快照批量勾选，也可手填清单。' : '来源号提供物品，集中号上架小物；可用快照批量勾选，也可手填清单，快照只作辅助提示。';
    const startLabel = transferRunning || startPending
      ? (state.batchMode ? `加入批量${operationLabel()}队列` : '加入队列')
      : (state.batchMode ? `执行批量${operationLabel()}` : `执行${operationLabel()}`);
    const modeButtons = `
      <div class="storage-bag-transfer-mode">
        <button type="button" class="btn btn-secondary${state.batchMode ? '' : ' is-active'}" data-storage-transfer-mode="single"${syncBusy ? ' disabled' : ''}>单次</button>
        <button type="button" class="btn btn-secondary${state.batchMode ? ' is-active' : ''}" data-storage-transfer-mode="batch"${syncBusy ? ' disabled' : ''}>批量</button>
        ${giftMode ? '' : `<button type="button" class="btn btn-secondary" data-storage-transfer-money-preset="1"${syncBusy ? ' disabled' : ''}>洗钱预设</button>`}
      </div>`;
    const listingFormatControl = `
        <label class="field-label">上架数量<input class="text-input" type="number" min="1" data-storage-transfer-field="listingCount" value="${esc(Math.max(1, Number(state.listingCount) || 1))}" /></label>
        <label class="field-label">上架单价<input class="text-input" type="number" min="0" data-storage-transfer-field="listingUnitPrice" value="${esc(Math.max(0, Number(state.listingUnitPrice) || 0))}" placeholder="洗灵石如 30000" /></label>
        <label class="field-label">上架总价<input class="text-input" type="text" value="${esc((Math.max(1, Number(state.listingCount) || 1) * Math.max(0, Number(state.listingUnitPrice) || 0)).toLocaleString())}" disabled /></label>
        <label class="field-label">上架格式<select class="text-input" data-storage-transfer-field="listingSyntax"><option value="space"${state.listingSyntax !== 'compact' ? ' selected' : ''}>物品 数量</option><option value="compact"${state.listingSyntax === 'compact' ? ' selected' : ''}>物品*数量</option></select></label>`;
    const sourceControls = state.batchMode ? `
      <div class="storage-bag-transfer-controls storage-bag-transfer-controls-batch">
        <label class="field-label">集中号<select class="text-input" data-storage-transfer-field="targetId">${identityOptions(state.targetId)}</select></label>
        ${giftMode ? '' : `<label class="field-label">集中号上架物<input class="text-input" data-storage-transfer-field="listingItem" value="${esc(state.listingItem || '')}" placeholder="如 凝血草" /></label>${listingFormatControl}`}
        <label class="field-label">每号保留<input class="text-input" type="number" min="0" data-storage-transfer-field="batchReserveCount" value="${esc(Math.max(0, Number(state.batchReserveCount) || 0))}" /></label>
        <label class="field-label">起送阈值<input class="text-input" type="number" min="1" data-storage-transfer-field="batchMinTransferCount" value="${esc(Math.max(1, Number(state.batchMinTransferCount) || 1))}" /></label>
      </div>
      <div class="storage-bag-transfer-batch-options">
        <label><input type="checkbox" data-storage-transfer-flag="batchAllSources"${state.batchAllSources ? ' checked' : ''} />全部可用来源</label>
        <label><input type="checkbox" data-storage-transfer-flag="includeProtected"${state.includeProtected ? ' checked' : ''} />包含保护号</label>
        <label><input type="checkbox" data-storage-transfer-flag="continueOnError"${state.continueOnError ? ' checked' : ''} />失败后继续</label>
        <span>${esc(state.batchAllSources ? `来源 ${sourceRows.length}` : `已选 ${sourceRows.length}/${availableBatchSourceRows().length}`)}</span>
      </div>
      <div class="storage-bag-transfer-source-list">
        ${availableBatchSourceRows().map(function (row) {
          const id = Number(row.identity_id) || 0;
          const selected = state.batchAllSources || selectedBatchSourceIds().indexOf(id) >= 0;
          return `<label><input type="checkbox" name="storage_bag_batch_source" value="${esc(id)}"${selected ? ' checked' : ''}${state.batchAllSources ? ' disabled' : ''} />${esc(rowLabel(row) || id)}${row.protected ? '<span class="storage-bag-protected">保护</span>' : ''}</label>`;
        }).join('') || '<div class="queue-empty">暂无可用来源身份。</div>'}
      </div>`
      : `
      <div class="storage-bag-transfer-controls">
        <label class="field-label">资源号<select class="text-input" data-storage-transfer-field="sourceId">${identityOptions(state.sourceId)}</select></label>
        <label class="field-label">集中号<select class="text-input" data-storage-transfer-field="targetId">${identityOptions(state.targetId)}</select></label>
        ${giftMode ? '' : `<label class="field-label">集中号上架物<input class="text-input" data-storage-transfer-field="listingItem" value="${esc(state.listingItem || '')}" placeholder="如 凝血草" /></label>${listingFormatControl}`}
      </div>`;
    panel.innerHTML = `
      ${modeButtons}
      ${sourceControls}
      <div class="storage-bag-transfer-grid">
        <section>
          <div class="form-label">${state.batchMode ? `批量来源 ${esc(sourceRows.length)} 个，可勾选要${giftMode ? '赠送' : '集中'}的物品。` : `${esc(sourceLabel)} 快照物品，可批量勾选。`}</div>
          <div class="storage-bag-transfer-table-wrap"><table class="storage-bag-transfer-table"><thead>${state.batchMode ? '<tr><th>选</th><th>物品</th><th>方式</th><th>来源合计</th><th>持有号</th><th>数量</th></tr>' : '<tr><th>选</th><th>物品</th><th>方式</th><th>库存</th><th>数量</th></tr>'}</thead><tbody>${itemRows}</tbody></table></div>
        </section>
        <section>
          <label class="field-label">手填清单<textarea class="text-input storage-bag-transfer-textarea" data-storage-transfer-field="manualText" placeholder="妖丹*10 木髓*5 或一行一个">${esc(state.manualText || '')}</textarea></label>
          <div class="form-label">${state.batchMode ? `批量按物品需求规划；每号保留和起送阈值会在生成预览时生效。` : '快照不准时直接手填；脚本只提示，不阻塞，游戏回复兜底。'}</div>
        </section>
      </div>
      <div class="storage-bag-transfer-actions">
        <button type="button" class="btn btn-secondary" data-storage-transfer-preview="1"${busy || syncBusy ? ' disabled' : ''}>${state.batchMode ? `生成批量${operationLabel()}预览` : `生成${operationLabel()}预览`}</button>
        <button type="button" class="btn" data-storage-transfer-start="1"${syncBusy || startPending ? ' disabled' : ''}>${startLabel}</button>
        <button type="button" class="btn btn-secondary" data-storage-transfer-cancel="1"${transferRunning ? '' : ' disabled'}>取消任务</button>
      </div>
      <div id="storage-bag-transfer-preview" class="storage-bag-transfer-preview">${renderTransferPreviewHtml(preview)}${renderBatchRuntimeHtml(batchRuntime)}${logs ? `<pre>${esc(logs)}</pre>` : ''}</div>`;
    const nextTableWrap = panel.querySelector('.storage-bag-transfer-table-wrap');
    if (nextTableWrap) {
      nextTableWrap.scrollTop = previousTableScrollTop;
      nextTableWrap.scrollLeft = previousTableScrollLeft;
    }
    const nextPreview = panel.querySelector('#storage-bag-transfer-preview');
    if (nextPreview) nextPreview.scrollTop = previousPreviewScrollTop;
    let nextActive = null;
    if (activeField) nextActive = Array.from(panel.querySelectorAll('[data-storage-transfer-field]')).find(function (item) {
      return item.getAttribute('data-storage-transfer-field') === activeField;
    }) || null;
    else if (activeQty) nextActive = Array.from(panel.querySelectorAll('[data-storage-transfer-qty]')).find(function (item) {
      return item.getAttribute('data-storage-transfer-qty') === activeQty;
    }) || null;
    else if (activeName) nextActive = panel.querySelector(`[name="${activeName}"]`);
    if (nextActive && typeof nextActive.focus === 'function') {
      nextActive.focus();
      if (activeSelectionStart !== null && typeof nextActive.setSelectionRange === 'function') {
        try {
          nextActive.setSelectionRange(activeSelectionStart, activeSelectionEnd);
        } catch (_error) {}
      }
    }
  }

  function resetTransferPreviewOnly() {
    const preview = document.getElementById('storage-bag-transfer-preview');
    if (preview) preview.innerHTML = '<div class="queue-empty">尚未生成预览。</div>';
  }

  function openStorageBagModal() {
    renderStorageBagTable();
    renderStorageBagApiPanel();
    const modal = document.getElementById('storage-bag-modal');
    if (modal) modal.classList.add('show');
  }

  function closeStorageBagModal() {
    const modal = document.getElementById('storage-bag-modal');
    if (modal) modal.classList.remove('show');
  }

  function openTransferModal() {
    transferState().operation = 'transfer';
    const runtime = snapshot().storage_bag_transfer || {};
    const batchRuntime = runtime.batch || {};
    if (!runtime.running && !batchRuntime.running) resetTransferDraftToDefaults();
    renderTransferPanel();
    const modal = document.getElementById('storage-bag-transfer-modal');
    if (modal) modal.classList.add('show');
  }

  function openGiftModal() {
    transferState().operation = 'gift';
    const runtime = snapshot().storage_bag_transfer || {};
    const batchRuntime = runtime.batch || {};
    if (!runtime.running && !batchRuntime.running) resetTransferDraftToDefaults();
    transferState().operation = 'gift';
    transferState().listingItem = '';
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

  function collectStorageBagApiPayload() {
    const form = document.getElementById('storage-bag-api-form');
    if (!form) return {};
    return {
      base_url: (form.querySelector('input[name="storage_bag_api_base_url"]')?.value || '').trim(),
      api_token: (form.querySelector('input[name="storage_bag_api_token"]')?.value || '').trim(),
      cookie: (form.querySelector('input[name="storage_bag_api_cookie"]')?.value || '').trim(),
    };
  }

  async function saveStorageBagApiConfig() {
    try {
      const data = await post('/api/storage-bag-api-config', collectStorageBagApiPayload());
      setFlash(data.message || '已更新储物袋 API 配置', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderStorageBagApiPanel();
    } catch (error) {
      setFlash((error && error.message) || '保存储物袋 API 失败', true);
      renderStorageBagApiPanel();
    }
  }

  async function verifyStorageBagApi() {
    try {
      renderStorageBagApiPanel();
      const data = await post('/api/storage-bag-api-verify', collectStorageBagApiPayload());
      setFlash(data.message || '天机阁验证成功', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderStorageBagApiPanel();
    } catch (error) {
      setFlash((error && error.message) || '天机阁验证失败', true);
      if (typeof refreshState === 'function') await refreshState({ silent: true, keepFlash: true });
      renderStorageBagApiPanel();
    }
  }

  async function refreshStorageBagApi() {
    try {
      renderStorageBagApiPanel();
      const data = await post('/api/storage-bag-api-refresh', collectStorageBagApiPayload());
      setFlash(data.message || '已读取储物袋 API', false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderStorageBagTable();
    } catch (error) {
      setFlash((error && error.message) || '储物袋 API 读取失败', true);
      if (typeof refreshState === 'function') await refreshState({ silent: true, keepFlash: true });
      renderStorageBagTable();
    }
  }

  async function previewTransfer() {
    const state = transferState();
    if (state.busy) return;
    if (state.batchMode && !state.batchAllSources && selectedBatchSourceIds().length <= 0) {
      setFlash('请至少选择一个批量来源身份', true);
      return;
    }
    state.busy = true;
    renderTransferPanel();
    try {
      const data = await post(isGiftMode() ? '/api/storage-bag-gift-preview' : '/api/storage-bag-transfer-preview', transferPayload());
      state.preview = data.preview || null;
      renderTransferPanel();
    } catch (error) {
      setFlash((error && error.message) || `生成${operationLabel()}预览失败`, true);
      renderTransferPanel();
    } finally {
      state.busy = false;
      renderTransferPanel();
    }
  }

  async function startTransfer() {
    const state = transferState();
    if (state.batchMode && !state.batchAllSources && selectedBatchSourceIds().length <= 0) {
      setFlash('请至少选择一个批量来源身份', true);
      return;
    }
    const payload = transferPayload();
    state.startPending = Math.max(0, Number(state.startPending || 0)) + 1;
    renderTransferPanel();
    try {
      const data = await post(isGiftMode() ? '/api/storage-bag-gift-start' : '/api/storage-bag-transfer-start', payload);
      setFlash(data.message || `已开始储物袋${operationLabel()}`, false);
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      renderTransferPanel();
    } catch (error) {
      setFlash((error && error.message) || `启动储物袋${operationLabel()}失败`, true);
      renderTransferPanel();
    } finally {
      state.startPending = Math.max(0, Number(state.startPending || 0) - 1);
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
    const batchRuntime = runtime.batch || {};
    if (!runtime.running && !batchRuntime.running) return;
    if (typeof refreshState === 'function') await refreshState({ silent: true, keepFlash: true });
    renderTransferPanel();
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-storage-bag]')) return openStorageBagModal();
    if (event.target.closest('#storage-bag-transfer-open-btn')) return openTransferModal();
    if (event.target.closest('#storage-bag-gift-open-btn')) return openGiftModal();
    if (event.target.closest('#storage-bag-sync-btn')) return syncStorageBag();
    if (event.target.closest('[data-storage-bag-api-save]')) return saveStorageBagApiConfig();
    if (event.target.closest('[data-storage-bag-api-verify]')) return verifyStorageBagApi();
    if (event.target.closest('[data-storage-bag-api-refresh]')) return refreshStorageBagApi();
    if (event.target.closest('[data-storage-transfer-money-preset]')) return applyMoneyPreset();
    const modeButton = event.target.closest('[data-storage-transfer-mode]');
    if (modeButton) {
      const state = transferState();
      const nextMode = modeButton.getAttribute('data-storage-transfer-mode') || 'single';
      const nextBatchMode = nextMode === 'batch';
      if (state.batchMode !== nextBatchMode) {
        state.batchMode = nextBatchMode;
        state.selectedItems = {};
        state.preview = null;
        if (state.batchMode && !state.batchAllSources && selectedBatchSourceIds().length <= 0) {
          state.batchSourceIds = availableBatchSourceRows().map(function (row) {
            return Number(row.identity_id) || 0;
          }).filter(Boolean);
        }
      }
      renderTransferPanel();
      return;
    }
    const tagFilter = event.target.closest('[data-storage-bag-filter-tag]');
    if (tagFilter && !event.target.closest('input, select, textarea')) {
      storageBagViewState().tag = tagFilter.getAttribute('data-storage-bag-filter-tag') || 'all';
      renderStorageBagTable();
      return;
    }
    const flagFilter = event.target.closest('[data-storage-bag-flag]');
    if (flagFilter) {
      const view = storageBagViewState();
      const nextFlag = flagFilter.getAttribute('data-storage-bag-flag') || 'all';
      view.flag = view.flag === nextFlag && nextFlag !== 'all' ? 'all' : nextFlag;
      renderStorageBagTable();
      return;
    }
    const itemFocus = event.target.closest('[data-storage-bag-item-focus]');
    if (itemFocus && !event.target.closest('input, select, textarea, button, a')) {
      storageBagViewState().focusItem = itemFocus.getAttribute('data-storage-bag-item-focus') || '';
      renderStorageBagTable();
      return;
    }
    const identityFocus = event.target.closest('[data-storage-bag-identity-focus]');
    if (identityFocus && !event.target.closest('input, select, textarea, button, a')) {
      storageBagViewState().focusIdentityId = Number(identityFocus.getAttribute('data-storage-bag-identity-focus') || 0) || 0;
      renderStorageBagTable();
      return;
    }
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
    const sort = event.target.closest('[data-storage-bag-sort]');
    if (sort) {
      storageBagViewState().sort = sort.value || 'group';
      renderStorageBagTable();
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
      if (key === 'targetId' && state.batchMode && !state.batchAllSources) state.batchSourceIds = selectedBatchSourceIds();
      renderTransferPanel();
      return;
    }
    const flag = event.target.closest('[data-storage-transfer-flag]');
    if (flag) {
      const state = transferState();
      const key = flag.getAttribute('data-storage-transfer-flag');
      state[key] = !!flag.checked;
      state.preview = null;
      if (key === 'batchAllSources' && !state.batchAllSources && selectedBatchSourceIds().length <= 0) {
        state.batchSourceIds = availableBatchSourceRows().map(function (row) {
          return Number(row.identity_id) || 0;
        }).filter(Boolean);
      }
      if (key === 'includeProtected' && !state.batchAllSources) {
        state.batchSourceIds = selectedBatchSourceIds();
      }
      renderTransferPanel();
      return;
    }
    const batchSource = event.target.closest('input[name="storage_bag_batch_source"]');
    if (batchSource) {
      const state = transferState();
      const ids = new Set(selectedBatchSourceIds());
      const id = Number(batchSource.value) || 0;
      if (id) {
        if (batchSource.checked) ids.add(id);
        else ids.delete(id);
      }
      state.batchSourceIds = Array.from(ids);
      state.preview = null;
      renderTransferPanel();
      return;
    }
    const itemCheckbox = event.target.closest('input[name="storage_bag_transfer_item"]');
    if (itemCheckbox) {
      const state = transferState();
      const name = itemCheckbox.value;
      if (itemCheckbox.checked) {
        const stats = state.batchMode ? batchItemStats(name) : null;
        state.selectedItems[name] = state.batchMode
          ? Number((stats || {}).total || 1)
          : itemCount(state.sourceId, name);
      }
      else delete state.selectedItems[name];
      state.preview = null;
      const row = itemCheckbox.closest('tr');
      const qtyInput = row ? row.querySelector('input[name="storage_bag_transfer_qty"]') : null;
      if (qtyInput) {
        qtyInput.disabled = !itemCheckbox.checked;
        if (itemCheckbox.checked) {
          const stats = state.batchMode ? batchItemStats(name) : null;
          qtyInput.value = String(state.selectedItems[name] || (state.batchMode ? Number((stats || {}).total || 1) : itemCount(state.sourceId, name)) || 1);
        }
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
    const search = event.target.closest('[data-storage-bag-search]');
    if (search) {
      storageBagViewState().query = search.value || '';
      if (storageBagSearchTimer) window.clearTimeout(storageBagSearchTimer);
      if (event.isComposing || storageBagSearchComposing) {
        storageBagSearchTimer = null;
        return;
      }
      storageBagSearchTimer = window.setTimeout(function () {
        storageBagSearchTimer = null;
        renderStorageBagSearchResults();
      }, 120);
      return;
    }
    const field = event.target.closest('[data-storage-transfer-field]');
    if (!field) return;
    const key = field.getAttribute('data-storage-transfer-field');
    if (key === 'manualText' || key === 'listingItem' || key === 'listingCount' || key === 'listingUnitPrice' || key === 'batchReserveCount' || key === 'batchMinTransferCount') {
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

  document.addEventListener('compositionstart', function (event) {
    if (!event.target.closest('[data-storage-bag-search]')) return;
    storageBagSearchComposing = true;
    if (storageBagSearchTimer) {
      window.clearTimeout(storageBagSearchTimer);
      storageBagSearchTimer = null;
    }
  });

  document.addEventListener('compositionend', function (event) {
    const search = event.target.closest('[data-storage-bag-search]');
    if (!search) return;
    storageBagSearchComposing = false;
    storageBagViewState().query = search.value || '';
    if (storageBagSearchTimer) window.clearTimeout(storageBagSearchTimer);
    storageBagSearchTimer = window.setTimeout(function () {
      storageBagSearchTimer = null;
      renderStorageBagSearchResults();
    }, 0);
  });

  window.setInterval(function () {
    refreshTransferPanelIfOpen();
  }, 3000);
})();
