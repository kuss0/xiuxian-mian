(function () {
  function selectedIdentity() {
    if (typeof getSelectedIdentity === 'function') {
      return getSelectedIdentity();
    }
    return null;
  }

  function clampLimit(value) {
    const parsed = parseInt(value, 10);
    if (!Number.isFinite(parsed)) {
      return 6;
    }
    return Math.max(1, Math.min(20, parsed));
  }

  function enhanceDivinationCard(identity) {
    const currentIdentity = identity || selectedIdentity();
    if (!currentIdentity) {
      return;
    }
    const cards = document.querySelectorAll('.module-card');
    cards.forEach(function (card) {
      const title = card.querySelector('.module-title');
      if (!title || title.textContent.trim() !== '卜筮问天') {
        return;
      }
      const tools = card.querySelector('.module-tools');
      if (!tools) {
        return;
      }
      const limit = clampLimit(currentIdentity.divination_daily_limit);
      const existingControl = tools.querySelector('[data-divination-config-ui]');
      if (existingControl) {
        const existingInput = existingControl.querySelector('[data-divination-daily-limit]');
        if (existingInput) {
          existingInput.value = String(limit);
        }
        return;
      }
      const control = document.createElement('label');
      control.className = 'module-subswitch divination-limit-control';
      control.setAttribute('data-divination-config-ui', '1');
      control.innerHTML = [
        '<span class="module-subswitch-label">次数</span>',
        '<input class="text-input divination-limit-input" type="number" min="1" max="20" step="1" value="' + String(limit) + '" data-divination-daily-limit="1" />'
      ].join('');
      tools.appendChild(control);
    });
  }

  const originalRenderModules = typeof renderModules === 'function' ? renderModules : null;
  if (originalRenderModules) {
    renderModules = function (identity) {
      originalRenderModules(identity);
      enhanceDivinationCard(identity);
    };
  }

  document.addEventListener('change', function (event) {
    const input = event.target.closest('[data-divination-daily-limit]');
    if (!input) {
      return;
    }
    const dailyLimit = clampLimit(input.value);
    input.value = String(dailyLimit);
    postJson('/api/divination-config', {
      send_as_id: appState.selectedId,
      daily_limit: dailyLimit
    }).then(function (data) {
      updateFlash(data.message || '已更新卜筮问天次数', false);
      applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    }).catch(function (error) {
      updateFlash((error && error.message) || '卜筮问天次数更新失败', true);
      renderAll();
    });
  });

  enhanceDivinationCard();
})();
