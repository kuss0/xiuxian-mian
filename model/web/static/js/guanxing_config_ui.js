(function () {
  function getSnapshotValue(name, fallback) {
    if (typeof appState === 'undefined' || !appState.snapshot) return fallback;
    const value = appState.snapshot[name];
    return value == null ? fallback : value;
  }

  function getShiftDelayInput(form) {
    const root = form || document.getElementById('basic-config-form');
    return root ? root.querySelector('input[name="guanxing_shift_delay_sec"]') : null;
  }

  function fillGuanxingShiftDelay(form) {
    const input = getShiftDelayInput(form);
    if (!input) return;
    const value = Number(getSnapshotValue('guanxing_shift_delay_sec', 10));
    input.value = Number.isFinite(value) ? String(value) : '10';
  }

  function fillGameGroupRoute(form) {
    const root = form || document.getElementById('basic-config-form');
    if (!root) return;
    const route = getSnapshotValue('game_group_route_config', {}) || {};
    const backups = Array.isArray(route.backup_group_ids) ? route.backup_group_ids : [];
    const backupId = Number(backups[0] || 0);
    const topics = route.topic_id_by_group || {};
    const backupInput = root.querySelector('input[name="backup_game_group_id"]');
    const backupTopicInput = root.querySelector('input[name="backup_game_topic_id"]');
    if (backupInput) backupInput.value = backupId ? String(backupId) : '';
    if (backupTopicInput) {
      const topicId = Number(topics[String(backupId)] || 0);
      backupTopicInput.value = topicId > 0 ? String(topicId) : '';
    }
  }

  const originalOpenBasicConfigModal = window.openBasicConfigModal;
  window.openBasicConfigModal = function () {
    if (typeof originalOpenBasicConfigModal === 'function') {
      originalOpenBasicConfigModal.apply(this, arguments);
    }
    fillGuanxingShiftDelay();
    fillGameGroupRoute();
  };

  async function submitBasicConfigWithGuanxingDelay(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    const form = event.currentTarget;
    const payload = {
      game_group_id: form.querySelector('input[name="game_group_id"]').value,
      game_bot_ids: form.querySelector('input[name="game_bot_ids"]').value,
      game_topic_id: form.querySelector('input[name="game_topic_id"]').value,
      backup_game_group_id: form.querySelector('input[name="backup_game_group_id"]').value,
      backup_game_topic_id: form.querySelector('input[name="backup_game_topic_id"]').value,
      auto_delete_sent_messages: !!form.querySelector('input[name="auto_delete_sent_messages"]').checked,
      tiandao_judgement_enabled: !!form.querySelector('input[name="tiandao_judgement_enabled"]').checked,
      guanxing_monitor_enabled: !!form.querySelector('input[name="guanxing_monitor_enabled"]').checked,
      guanxing_shift_target: form.querySelector('input[name="guanxing_shift_target"]').value,
      guanxing_shift_delay_sec: form.querySelector('input[name="guanxing_shift_delay_sec"]').value,
      guanxing_monitor_targets: Array.from(form.querySelectorAll('input[name="guanxing_monitor_targets"]:checked')).map(function (input) {
        return input.value;
      }),
    };
    try {
      const data = await postJson('/api/basic-config', payload);
      appState._configPromptShown = false;
      updateFlash(data.message || '已更新基础配置', false);
      closeBasicConfigModal();
      applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    } catch (error) {
      updateFlash((error && error.message) || '基础配置更新失败', true);
      renderAll();
    }
  }

  const form = document.getElementById('basic-config-form');
  if (form) {
    form.addEventListener('submit', submitBasicConfigWithGuanxingDelay, true);
  }
})();
