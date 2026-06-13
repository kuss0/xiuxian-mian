(function () {
  const yinluoDraft = {
    collect_slot: '',
    convert_amount: '',
    refine_slot: '',
    refine_target: ''
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

  function renderYinluoPanel() {
    return [
      '<div class="yinluo-action-panel" data-yinluo-panel="1">',
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
      '<label class="yinluo-field yinluo-field-wide"><span>数量</span><input class="text-input yinluo-amount-input" type="number" min="1" max="10000" step="1" inputmode="numeric" value="' + esc(yinluoDraft.convert_amount) + '" data-yinluo-draft="convert_amount" /></label>',
      '<button type="button" class="btn btn-secondary" data-yinluo-action="convert">化煞</button>',
      '</div>',
      '</div>'
    ].join('');
  }

  function enhanceYinluoCard() {
    const card = findYinluoCard();
    if (!card || card.querySelector('[data-yinluo-panel]')) {
      return;
    }
    const moduleTop = card.querySelector('.module-top');
    if (!moduleTop) {
      return;
    }
    moduleTop.insertAdjacentHTML('afterend', renderYinluoPanel());
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

  document.addEventListener('click', function (event) {
    const button = event.target.closest('[data-yinluo-action]');
    if (!button) {
      return;
    }
    submitYinluoAction(button.getAttribute('data-yinluo-action'), button);
  });

  enhanceYinluoCard();
})();
