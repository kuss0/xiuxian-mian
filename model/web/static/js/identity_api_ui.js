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

  function getDaoPathRow(sendAsId) {
    const data = snapshot().tianjige_dao_path || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    return rows.find(function (row) {
      return Number(row && row.identity_id || 0) === Number(sendAsId || 0);
    }) || null;
  }

  function formatField(value) {
    if (value == null || value === '') return '-';
    if (typeof value === 'object') {
      try {
        return JSON.stringify(value);
      } catch (_error) {
        return String(value);
      }
    }
    return String(value);
  }

  function formatAmount(value) {
    const number = Number(String(value == null ? '' : value).replace(/,/g, ''));
    if (!Number.isFinite(number)) return formatField(value);
    if (Number.isInteger(number)) return String(number);
    return number.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  }

  function collectionCount(value) {
    if (Array.isArray(value)) return value.length;
    if (value && typeof value === 'object') return Object.keys(value).length;
    if (typeof value === 'string') {
      const text = value.trim();
      if (text && (text[0] === '[' || text[0] === '{')) {
        try {
          return collectionCount(JSON.parse(text));
        } catch (_error) {
          return 0;
        }
      }
    }
    return 0;
  }

  function normalizeStatusText(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return '';
    const lower = text.toLowerCase();
    const map = {
      normal: '正常',
      idle: '空闲',
      busy: '忙碌',
      weak: '虚弱',
      dead: '死亡',
      combat: '战斗中',
      in_combat: '战斗中',
      retreat: '闭关中',
      '1': '已开启',
      '0': '未开启',
      true: '已开启',
      false: '未开启',
    };
    return map[lower] || text;
  }

  function formatStateLabel(row) {
    const raw = String(row && row.state_label ? row.state_label : '').trim();
    if (!raw) {
      return '未读取';
    }
    const parts = raw.split(/\s*\/\s*|[，,、]+/).map(function (part) {
      return normalizeStatusText(part);
    }).filter(Boolean);
    const seen = [];
    parts.forEach(function (part) {
      if (seen.indexOf(part) === -1) seen.push(part);
    });
    return seen.length ? seen.join(' / ') : normalizeStatusText(raw) || raw;
  }

  function formatCave(row) {
    const summary = String(row && row.cave_summary ? row.cave_summary : '').trim();
    if (summary) {
      return summary;
    }
    const cave = row && row.cave && typeof row.cave === 'object' ? row.cave : {};
    const keyMap = [
      { keys: ['lingmai_level', 'spirit_vein_level', 'spiritual_vein_level'], label: '灵脉', type: 'level' },
      { keys: ['jingshi_level', 'quiet_room_level', 'meditation_room_level'], label: '静室', type: 'level' },
      { keys: ['danfang_level', 'alchemy_room_level'], label: '丹房', type: 'level' },
      { keys: ['qishi_level', 'artifact_room_level'], label: '器室', type: 'level' },
      { keys: ['shouyuan_level', 'lifespan_room_level'], label: '兽园', type: 'level' },
      { keys: ['dazhen_level', 'formation_level'], label: '大阵', type: 'level' },
      { keys: ['lingqi_pool', 'spirit_pool', 'spiritual_pool', 'qi_pool'], label: '灵气池', type: 'amount' },
    ];
    const parts = [];
    const used = new Set();
    keyMap.forEach(function (item) {
      const key = item.keys.find(function (candidate) {
        return Object.prototype.hasOwnProperty.call(cave, candidate) && cave[candidate] != null && cave[candidate] !== '';
      });
      if (!key) return;
      used.add(key);
      const value = cave[key];
      if (item.type === 'amount') {
        parts.push(item.label + ' ' + formatAmount(value));
      } else {
        parts.push(item.label + ' ' + formatField(value) + '级');
      }
    });
    const activeKey = ['dazhen_active', 'formation_active'].find(function (candidate) {
      return Object.prototype.hasOwnProperty.call(cave, candidate) && cave[candidate] != null && cave[candidate] !== '';
    });
    if (activeKey && parts.some(function (part) { return part.indexOf('大阵') === 0; })) {
      const activeText = normalizeStatusText(cave[activeKey]) || (String(cave[activeKey]).trim() === '0' ? '未开启' : '已开启');
      const idx = parts.findIndex(function (part) { return part.indexOf('大阵') === 0; });
      if (idx >= 0 && parts[idx].indexOf('（') === -1) {
        const modeKey = ['dazhen_mode', 'formation_mode'].find(function (candidate) {
          return Object.prototype.hasOwnProperty.call(cave, candidate) && cave[candidate] != null && cave[candidate] !== '';
        });
        const modeText = modeKey ? String(cave[modeKey]).trim() : '';
        parts[idx] = parts[idx] + '（' + activeText + (modeText ? '·' + modeText : '') + '）';
        if (modeKey) used.add(modeKey);
      }
      used.add(activeKey);
    }
    const sceneryKey = ['scenery_slots', 'unlocked_scenery'].find(function (candidate) {
      return Object.prototype.hasOwnProperty.call(cave, candidate) && cave[candidate] != null && cave[candidate] !== '';
    });
    const sceneryCount = sceneryKey ? collectionCount(cave[sceneryKey]) : 0;
    if (sceneryCount > 0) {
      parts.push('景观 ' + sceneryCount + '个');
      used.add(sceneryKey);
    }
    const pavilionKey = ['pavilion_slots'].find(function (candidate) {
      return Object.prototype.hasOwnProperty.call(cave, candidate) && cave[candidate] != null && cave[candidate] !== '';
    });
    const pavilionCount = pavilionKey ? collectionCount(cave[pavilionKey]) : 0;
    if (pavilionCount > 0) {
      parts.push('亭台 ' + pavilionCount + '项');
      used.add(pavilionKey);
    }
    return parts.length ? parts.join('｜') : '未读取';
  }

  function enhanceSummary(identity) {
    if (!identity) return;
    const panel = document.getElementById('summary-panel');
    const section = panel && panel.querySelector('section.card');
    if (!section) return;

    const actions = section.querySelector('.summary-head-actions');
    if (actions) {
      const gameButton = actions.querySelector('[data-refresh-identity]');
      if (gameButton) {
        gameButton.textContent = identity.sect_refresh_pending ? '游戏读取中' : '游戏命令读取';
      }
      actions.querySelectorAll('[data-refresh-identity-api]').forEach(function (button) {
        button.remove();
      });
      const api = snapshot().storage_bag_api || {};
      const disabled = api.running || !api.configured;
      const disabledAttr = disabled ? ' disabled' : '';
      const singleText = api.running ? 'API读取中' : 'API单角色';
      const allText = api.running ? 'API读取中' : 'API全体';
      const deleteButton = actions.querySelector('[data-delete-identity]');
      const html = [
        '<button type="button" class="btn btn-secondary" data-refresh-identity-api="' + esc(identity.send_as_id) + '" data-scope="single"' + disabledAttr + '>' + esc(singleText) + '</button>',
        '<button type="button" class="btn btn-secondary" data-refresh-identity-api="' + esc(identity.send_as_id) + '" data-scope="all"' + disabledAttr + '>' + esc(allText) + '</button>',
      ].join('');
      if (deleteButton) {
        deleteButton.insertAdjacentHTML('beforebegin', html);
      } else {
        actions.insertAdjacentHTML('beforeend', html);
      }
    }

    const row = getDaoPathRow(identity.send_as_id);
    const oldLine = section.querySelector('.tianjige-summary-line');
    if (!row || !row.has_remote) {
      if (oldLine) oldLine.remove();
      return;
    }
    const line = oldLine || document.createElement('div');
    line.className = 'meta tianjige-summary-line';
    line.textContent = '角色状态：' + formatStateLabel(row) + ' ｜ 洞府：' + formatCave(row) + ' ｜ API更新：' + (row.updated_at || '未设置');
    if (!oldLine) {
      const firstMeta = section.querySelector('.meta');
      if (firstMeta) firstMeta.insertAdjacentElement('afterend', line);
    }
  }

  async function refreshIdentityInfoFromApi(sendAsId, scope) {
    try {
      const data = await postJson('/api/identity-refresh-api', {
        send_as_id: sendAsId,
        scope: scope || 'single',
      });
      updateFlash(data.message || '已通过天机阁更新角色信息', false);
      applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
    } catch (error) {
      updateFlash((error && error.message) || '天机阁角色信息读取失败', true);
      renderAll();
    }
  }

  const originalRenderSummary = window.renderSummary;
  if (typeof originalRenderSummary === 'function') {
    window.renderSummary = function (identity) {
      originalRenderSummary(identity);
      enhanceSummary(identity);
    };
    renderSummary = window.renderSummary;
    if (typeof renderAll === 'function') renderAll();
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-refresh-identity-api]');
    if (!button) return;
    refreshIdentityInfoFromApi(button.getAttribute('data-refresh-identity-api'), button.getAttribute('data-scope') || 'single');
  });
})();
