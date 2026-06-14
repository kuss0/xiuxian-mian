(function () {
  const yinluoDraft = {
    identity_id: null,
    collect_slot: '',
    convert_amount: '',
    refine_slot: '',
    refine_target: '',
    auto_refine_targets: ''
  };

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

  function selectedIdentity() {
    if (typeof getSelectedIdentity === 'function') {
      return getSelectedIdentity();
    }
    const identities = (typeof appState !== 'undefined' && appState.snapshot && Array.isArray(appState.snapshot.identities))
      ? appState.snapshot.identities
      : [];
    return identities.find(function (item) { return item.send_as_id === Number(appState.selectedId); }) || null;
  }

  function findYinluoCard() {
    const cards = document.querySelectorAll('.module-card');
    for (const card of cards) {
      const title = card.querySelector('.module-title');
      if (title && title.textContent.trim() === '阴罗宗') {
        return card;
      }
    }
    return null;
  }

  function getYinluoState(identity) {
    return (identity && identity.yinluo) || {};
  }

  function getYinluoConfig(identity) {
    const state = getYinluoState(identity);
    return state.auto_config || {};
  }

  function syncDraftDefaults(identity, config) {
    const identityId = identity && identity.send_as_id;
    if (yinluoDraft.identity_id === identityId) {
      return;
    }
    yinluoDraft.identity_id = identityId || null;
    yinluoDraft.collect_slot = '';
    yinluoDraft.convert_amount = config && config.convert_amount ? String(config.convert_amount) : '';
    yinluoDraft.refine_slot = '';
    const targets = Array.isArray(config && config.refine_targets) ? config.refine_targets : [];
    yinluoDraft.refine_target = targets[0] || '妖兽精魄';
    yinluoDraft.auto_refine_targets = targets.join(' ');
  }

  function renderAutoToggle(key, label, config) {
    const checked = !!(config && config[key]);
    return [
      '<label class="yinluo-auto-toggle">',
      '<input type="checkbox" data-yinluo-auto-key="' + esc(key) + '"' + (checked ? ' checked' : '') + ' />',
      '<span>' + esc(label) + '</span>',
      '</label>'
    ].join('');
  }

  function renderObservedSummary(identity) {
    const observed = getYinluoState(identity).observed || {};
    const ready = Array.isArray(observed.ready_slot_numbers) && observed.ready_slot_numbers.length ? observed.ready_slot_numbers.join(',') : '-';
    const empty = Array.isArray(observed.empty_slot_numbers) && observed.empty_slot_numbers.length ? observed.empty_slot_numbers.join(',') : '-';
    const refining = Array.isArray(observed.refining_slot_numbers) && observed.refining_slot_numbers.length ? observed.refining_slot_numbers.join(',') : '-';
    const stocks = observed.soul_stocks || {};
    const stockText = Object.keys(stocks).length
      ? Object.keys(stocks).map(function (name) { return name + ':' + stocks[name]; }).join(' ')
      : '未记录';
    const pending = observed.auto_collect_pending && Array.isArray(observed.auto_collect_pending.slots)
      ? observed.auto_collect_pending.slots.join(',')
      : '';
    const calibrate = observed.auto_calibrate_reason ? '<span class="yinluo-warn">校准: ' + esc(observed.auto_calibrate_reason) + '</span>' : '';
    return [
      '<div class="yinluo-observed">',
      '<span>煞气 ' + esc(observed.sha_current || 0) + '/' + esc(observed.sha_max || 0) + '</span>',
      '<span>成槽 ' + esc(ready) + '</span>',
      '<span>空槽 ' + esc(empty) + '</span>',
      '<span>炼中 ' + esc(refining) + '</span>',
      '<span>魂魄 ' + esc(stockText) + '</span>',
      pending ? '<span class="yinluo-warn">待收 ' + esc(pending) + '</span>' : '',
      calibrate,
      '</div>'
    ].join('');
  }

  function renderYinluoPanel(identity) {
    const config = getYinluoConfig(identity);
    syncDraftDefaults(identity, config);
    return [
      '<div class="yinluo-action-panel" data-yinluo-panel="1">',
      renderObservedSummary(identity),
      '<div class="yinluo-auto-panel">',
      '<div class="yinluo-section-label">自动策略</div>',
      '<div class="yinluo-auto-toggle-row">',
      renderAutoToggle('collect', '收取', config),
      renderAutoToggle('refine', '炼化', config),
      renderAutoToggle('blood_forest', '血洗', config),
      renderAutoToggle('demon_summon', '召魔', config),
      renderAutoToggle('convert', '化煞', config),
      '</div>',
      '<div class="yinluo-action-row yinluo-action-row-combo">',
      '<label class="yinluo-field yinluo-field-wide"><span>目标</span><input class="text-input yinluo-target-input" type="text" value="' + esc(yinluoDraft.auto_refine_targets) + '" placeholder="凶兽戾魄 妖兽精魄" data-yinluo-draft="auto_refine_targets" /></label>',
      '<label class="yinluo-field"><span>化煞</span><input class="text-input yinluo-amount-input" type="number" min="0" max="50000" step="1000" inputmode="numeric" value="' + esc(yinluoDraft.convert_amount) + '" data-yinluo-draft="convert_amount" /></label>',
      '<button type="button" class="btn btn-secondary" data-yinluo-config-save="1">保存策略</button>',
      '</div>',
      '</div>',
      '<div class="yinluo-manual-panel">',
      '<div class="yinluo-section-label">手动动作</div>',
      '<div class="yinluo-action-row yinluo-action-row-main">',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="banner">查幡</button>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="blood_forest">血洗</button>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="demon_summon">召魔</button>',
      '</div>',
      '<div class="yinluo-action-row">',
      '<label class="yinluo-field"><span>槽位</span><input class="text-input yinluo-slot-input" type="number" min="1" max="99" step="1" inputmode="numeric" value="' + esc(yinluoDraft.collect_slot) + '" data-yinluo-draft="collect_slot" /></label>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="collect">收取</button>',
      '</div>',
      '<div class="yinluo-action-row yinluo-action-row-combo">',
      '<label class="yinluo-field"><span>槽位</span><input class="text-input yinluo-slot-input" type="number" min="1" max="99" step="1" inputmode="numeric" value="' + esc(yinluoDraft.refine_slot) + '" data-yinluo-draft="refine_slot" /></label>',
      '<label class="yinluo-field yinluo-field-wide"><span>目标</span><input class="text-input yinluo-target-input" type="text" value="' + esc(yinluoDraft.refine_target) + '" placeholder="妖兽精魄" data-yinluo-draft="refine_target" /></label>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="refine">炼化</button>',
      '</div>',
      '<div class="yinluo-action-row">',
      '<label class="yinluo-field yinluo-field-wide"><span>数量</span><input class="text-input yinluo-amount-input" type="number" min="1000" max="50000" step="1000" inputmode="numeric" value="' + esc(yinluoDraft.convert_amount) + '" data-yinluo-draft="convert_amount" /></label>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="convert">化煞</button>',
      '</div>',
      '</div>',
      '</div>'
    ].join('');
  }

  function enhanceYinluoCard(identity) {
    const card = findYinluoCard();
    if (!card || card.querySelector('[data-yinluo-panel]')) {
      return;
    }
    const moduleTop = card.querySelector('.module-top');
    if (!moduleTop) {
      return;
    }
    moduleTop.insertAdjacentHTML('afterend', renderYinluoPanel(identity || selectedIdentity()));
  }

  function updateDraft(input) {
    const key = input && input.getAttribute('data-yinluo-draft');
    if (!key || !(key in yinluoDraft)) {
      return;
    }
    yinluoDraft[key] = input.value || '';
  }

  function argForAction(action) {
    if (action === 'collect') {
      return String(yinluoDraft.collect_slot || '').trim();
    }
    if (action === 'convert') {
      return String(yinluoDraft.convert_amount || '').trim();
    }
    if (action === 'refine') {
      const slot = String(yinluoDraft.refine_slot || '').trim();
      const target = String(yinluoDraft.refine_target || '').trim();
      return [slot, target].filter(Boolean).join(' ');
    }
    return '';
  }

  function configPayload(changedKey, enabled) {
    const config = Object.assign({
      collect: true,
      refine: true,
      blood_forest: true,
      demon_summon: true,
      convert: false,
      convert_amount: 0,
      refine_targets: '凶兽戾魄 妖兽精魄 修士残魂 怨魂'
    }, getYinluoConfig(selectedIdentity()));
    if (changedKey) {
      config[changedKey] = !!enabled;
    }
    config.convert_amount = Number(String(yinluoDraft.convert_amount || config.convert_amount || 0).trim()) || 0;
    config.refine_targets = String(yinluoDraft.auto_refine_targets || (Array.isArray(config.refine_targets) ? config.refine_targets.join(' ') : config.refine_targets) || '').trim();
    return config;
  }

  async function submitYinluoAction(action, button) {
    try {
      button.disabled = true;
      const data = await postJson('/api/yinluo-action', {
        send_as_id: appState.selectedId,
        action: action,
        arg: argForAction(action)
      });
      updateFlash(data.message || '已发送阴罗宗指令', false);
      applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    } catch (error) {
      updateFlash((error && error.message) || '阴罗宗指令发送失败', true);
      renderAll();
    } finally {
      button.disabled = false;
    }
  }

  async function submitYinluoConfig(config, control) {
    try {
      if (control) {
        control.disabled = true;
      }
      const data = await postJson('/api/yinluo-config', {
        send_as_id: appState.selectedId,
        config: config
      });
      updateFlash(data.message || '已更新阴罗自动策略', false);
      applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    } catch (error) {
      updateFlash((error && error.message) || '阴罗自动策略更新失败', true);
      renderAll();
    } finally {
      if (control) {
        control.disabled = false;
      }
    }
  }

  const originalRenderModules = typeof renderModules === 'function' ? renderModules : null;
  if (originalRenderModules) {
    renderModules = function (identity) {
      originalRenderModules(identity);
      enhanceYinluoCard(identity);
    };
  }

  document.addEventListener('input', function (event) {
    const input = event.target.closest('[data-yinluo-draft]');
    if (input) {
      updateDraft(input);
    }
  });

  document.addEventListener('compositionend', function (event) {
    const input = event.target.closest('[data-yinluo-draft]');
    if (input) {
      updateDraft(input);
    }
  });

  document.addEventListener('change', function (event) {
    const input = event.target.closest('[data-yinluo-auto-key]');
    if (!input) {
      return;
    }
    updateDraft(document.querySelector('[data-yinluo-draft="convert_amount"]'));
    updateDraft(document.querySelector('[data-yinluo-draft="auto_refine_targets"]'));
    submitYinluoConfig(configPayload(input.getAttribute('data-yinluo-auto-key'), input.checked), input);
  });

  document.addEventListener('click', function (event) {
    const saveButton = event.target.closest('[data-yinluo-config-save]');
    if (saveButton) {
      submitYinluoConfig(configPayload('', false), saveButton);
      return;
    }
    const button = event.target.closest('[data-yinluo-action]');
    if (!button) {
      return;
    }
    submitYinluoAction(button.getAttribute('data-yinluo-action'), button);
  });

  enhanceYinluoCard(selectedIdentity());
})();
