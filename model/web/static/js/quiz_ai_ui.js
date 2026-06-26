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

  function quizAiData() {
    return snapshot().quiz_ai || {};
  }

  function providerChoices(data) {
    const choices = Array.isArray(data.provider_choices) ? data.provider_choices : [];
    return choices.length ? choices : [
      { value: 'codex', label: 'Codex / OpenAI' },
      { value: 'claude', label: 'Claude / Anthropic' },
    ];
  }

  function quizAiProviders(data) {
    const providers = Array.isArray(data.providers) ? data.providers.slice(0, 6) : [];
    if (providers.length) return providers;
    return Array.from({ length: 5 }, function (_, index) {
      if (index > 0) {
        return {
          id: `ai${index + 1}`,
          enabled: false,
          label: `AI ${index + 1}`,
          provider: 'codex',
          base_url: '',
          model: '',
          api_key_configured: false,
          timeout_sec: data.timeout_sec || 20,
          temperature: 0,
        };
      }
      return {
      id: 'ai1',
      enabled: true,
      label: 'AI 1',
      provider: data.provider || 'codex',
      base_url: data.base_url || '',
      model: data.model || '',
      api_key_configured: !!data.api_key_configured,
      timeout_sec: data.timeout_sec || 20,
      temperature: data.temperature == null ? 0 : data.temperature,
      };
    });
  }

  function setFlash(message, isError) {
    if (typeof updateFlash === 'function') {
      updateFlash(message || '', !!isError);
      if (typeof renderAll === 'function') renderAll();
      return;
    }
    const flash = document.getElementById('flash');
    if (flash) {
      flash.textContent = message || '';
      flash.classList.toggle('hidden', !message);
      flash.classList.toggle('error', !!isError);
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

  function setRowStatus(row, message, isError) {
    const status = row ? row.querySelector('[data-quiz-ai-provider-status]') : null;
    if (!status) return;
    status.textContent = message || '';
    status.classList.toggle('error', !!isError);
  }

  function injectQuizAiButton() {
    const cards = Array.from(document.querySelectorAll('.module-card'));
    const card = cards.find(function (item) {
      const title = item.querySelector('.module-title');
      return title && title.textContent.trim() === '奇遇';
    });
    if (!card || card.querySelector('[data-open-quiz-ai-config]')) return;
    const tools = card.querySelector('.module-tools');
    if (!tools) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.setAttribute('data-open-quiz-ai-config', '1');
    button.textContent = 'AI';
    tools.insertBefore(button, tools.firstChild);
  }

  function renderQuizAiStatus() {
    const status = document.getElementById('quiz-ai-status');
    if (!status) return;
    const data = quizAiData();
    const providers = quizAiProviders(data);
    const enabledCount = providers.filter(function (provider) {
      return provider.enabled && provider.model;
    }).length;
    const keyCount = providers.filter(function (provider) {
      return provider.api_key_configured;
    }).length;
    const last = data.last_answer
      ? `最近 ${esc(data.last_provider || data.provider || '-')}:${esc(data.last_answer)} ${Number(data.last_confidence || 0).toFixed(2)}`
      : '最近无';
    const vote = data.last_vote_summary ? ` ｜ 投票 ${esc(data.last_vote_summary)}` : '';
    const results = Array.isArray(data.last_results) ? data.last_results.slice(0, 6).map(function (item) {
      const label = item.label || item.provider || '-';
      const answer = item.answer || (item.ok ? '-' : '失败');
      const elapsed = item.elapsed_ms ? `${item.elapsed_ms}ms` : '';
      const error = item.error ? ` ${item.error}` : '';
      return `${label}:${answer}${elapsed ? ` ${elapsed}` : ''}${error}`;
    }).join(' ｜ ') : '';
    const error = data.last_error ? ` ｜ ${esc(data.last_error)}` : '';
    status.innerHTML = `线路 ${enabledCount}/${providers.length} ｜ Key ${keyCount}/${providers.length} ｜ ${last}${vote}${error}${results ? `<br>${esc(results)}` : ''}`;
  }

  function renderProviderRows(data) {
    const list = document.getElementById('quiz-ai-provider-list');
    if (!list) return;
    const choices = providerChoices(data);
    const providers = quizAiProviders(data);
    list.innerHTML = providers.map(function (provider, index) {
      const configured = provider.api_key_configured ? '已保存，留空不变' : '';
      const options = choices.map(function (choice) {
        const selected = (provider.provider || 'codex') === choice.value ? ' selected' : '';
        return `<option value="${esc(choice.value)}"${selected}>${esc(choice.label)}</option>`;
      }).join('');
      return `
        <div class="quiz-ai-provider-row stack-8" data-provider-index="${index}">
          <input type="hidden" name="quiz_ai_provider_id" value="${esc(provider.id || `ai${index + 1}`)}" />
          <div class="form-grid form-grid-2">
            <label class="checkbox-inline checkbox-inline-small"><input type="checkbox" name="quiz_ai_provider_enabled" ${provider.enabled ? 'checked' : ''} /> 启用线路</label>
            <label class="field-label">名称<input class="text-input" name="quiz_ai_provider_label" value="${esc(provider.label || `AI ${index + 1}`)}" /></label>
          </div>
          <div class="form-grid form-grid-2">
            <label class="field-label">格式<select class="text-input" name="quiz_ai_provider_kind">${options}</select></label>
            <label class="field-label">Model<input class="text-input" name="quiz_ai_provider_model" value="${esc(provider.model || '')}" placeholder="例如 gpt-5-mini 或 claude-3-5-haiku-latest" /></label>
          </div>
          <div class="form-grid form-grid-2">
            <label class="field-label">模型列表<select class="text-input" name="quiz_ai_provider_model_select"><option value="">手动输入 / 先获取</option></select></label>
            <button class="btn btn-secondary" type="button" data-fetch-quiz-ai-models>获取模型</button>
          </div>
          <label class="field-label">Base URL<input class="text-input" name="quiz_ai_provider_base_url" value="${esc(provider.base_url || '')}" placeholder="留空使用格式默认地址" /></label>
          <div class="form-grid form-grid-2">
            <label class="field-label">API Key<input class="text-input" type="password" name="quiz_ai_provider_api_key" autocomplete="new-password" placeholder="${esc(configured)}" /></label>
            <label class="checkbox-inline checkbox-inline-small"><input type="checkbox" name="quiz_ai_provider_clear_api_key" /> 清空 Key</label>
          </div>
          <div class="form-grid form-grid-2">
            <label class="field-label">线路超时<input class="text-input" type="number" min="2" max="60" name="quiz_ai_provider_timeout_sec" value="${esc(provider.timeout_sec || data.timeout_sec || 20)}" /></label>
            <label class="field-label">Temperature<input class="text-input" type="number" step="0.1" min="0" max="2" name="quiz_ai_provider_temperature" value="${esc(provider.temperature == null ? 0 : provider.temperature)}" /></label>
          </div>
          <div class="form-label form-label-inline" data-quiz-ai-provider-status></div>
        </div>
      `;
    }).join('');
  }

  function openQuizAiModal() {
    const data = quizAiData();
    const form = document.getElementById('quiz-ai-form');
    if (form) {
      form.querySelector('input[name="quiz_ai_enabled"]').checked = !!data.enabled;
      form.querySelector('input[name="quiz_ai_auto_answer_enabled"]').checked = !!data.auto_answer_enabled;
      form.querySelector('input[name="quiz_ai_confidence_threshold"]').value = data.confidence_threshold == null ? '0.8' : data.confidence_threshold;
      form.querySelector('input[name="quiz_ai_decision_timeout_sec"]').value = data.decision_timeout_sec || 20;
      form.querySelector('input[name="quiz_ai_answer_safety_margin_sec"]').value = data.answer_safety_margin_sec || 12;
      renderProviderRows(data);
    }
    renderQuizAiStatus();
    const modal = document.getElementById('quiz-ai-modal');
    if (modal) modal.classList.add('show');
  }

  function closeQuizAiModal() {
    const modal = document.getElementById('quiz-ai-modal');
    if (modal) modal.classList.remove('show');
  }

  function collectProviderPayload(row, index) {
    return {
        id: row.querySelector('input[name="quiz_ai_provider_id"]').value || `ai${index + 1}`,
        enabled: !!row.querySelector('input[name="quiz_ai_provider_enabled"]').checked,
        label: row.querySelector('input[name="quiz_ai_provider_label"]').value.trim(),
        provider: row.querySelector('select[name="quiz_ai_provider_kind"]').value,
        base_url: row.querySelector('input[name="quiz_ai_provider_base_url"]').value.trim(),
        model: row.querySelector('input[name="quiz_ai_provider_model"]').value.trim(),
        api_key: row.querySelector('input[name="quiz_ai_provider_api_key"]').value.trim(),
        clear_api_key: !!row.querySelector('input[name="quiz_ai_provider_clear_api_key"]').checked,
        timeout_sec: row.querySelector('input[name="quiz_ai_provider_timeout_sec"]').value,
        temperature: row.querySelector('input[name="quiz_ai_provider_temperature"]').value,
      };
  }

  function collectPayload() {
    const form = document.getElementById('quiz-ai-form');
    if (!form) return {};
    const providers = Array.from(form.querySelectorAll('.quiz-ai-provider-row')).map(collectProviderPayload);
    return {
      enabled: !!form.querySelector('input[name="quiz_ai_enabled"]').checked,
      auto_answer_enabled: !!form.querySelector('input[name="quiz_ai_auto_answer_enabled"]').checked,
      confidence_threshold: form.querySelector('input[name="quiz_ai_confidence_threshold"]').value,
      decision_timeout_sec: form.querySelector('input[name="quiz_ai_decision_timeout_sec"]').value,
      answer_safety_margin_sec: form.querySelector('input[name="quiz_ai_answer_safety_margin_sec"]').value,
      providers,
    };
  }

  function populateModelSelect(row, models) {
    const select = row.querySelector('select[name="quiz_ai_provider_model_select"]');
    const input = row.querySelector('input[name="quiz_ai_provider_model"]');
    if (!select || !input) return;
    const current = input.value.trim();
    const options = ['<option value="">手动输入</option>'].concat((models || []).map(function (model) {
      const id = typeof model === 'string' ? model : model.id;
      const label = typeof model === 'string' ? model : (model.label || model.id);
      const selected = id && id === current ? ' selected' : '';
      return `<option value="${esc(id || '')}"${selected}>${esc(label || id || '')}</option>`;
    }));
    select.innerHTML = options.join('');
  }

  async function fetchProviderModels(row) {
    if (!row) return;
    const index = Number(row.getAttribute('data-provider-index') || 0);
    setRowStatus(row, '获取中...', false);
    try {
      const data = await post('/api/quiz-ai-models', {
        index,
        provider_config: collectProviderPayload(row, index),
      });
      const modelsPayload = data.models || {};
      const models = Array.isArray(modelsPayload.models) ? modelsPayload.models : [];
      populateModelSelect(row, models);
      setRowStatus(row, data.message || `已获取 ${models.length} 个模型`, false);
    } catch (error) {
      setRowStatus(row, (error && error.message) || '获取模型失败', true);
    }
  }

  async function saveQuizAiConfig(event) {
    event.preventDefault();
    try {
      const data = await post('/api/quiz-ai-config', collectPayload());
      if (typeof applySnapshot === 'function') applySnapshot(data.snapshot || snapshot(), { keepFlash: true });
      setFlash(data.message || '已更新玄骨 AI 辅助配置', false);
      closeQuizAiModal();
    } catch (error) {
      setFlash((error && error.message) || '保存玄骨 AI 配置失败', true);
      renderQuizAiStatus();
    }
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-quiz-ai-config]')) {
      openQuizAiModal();
      return;
    }
    const fetchButton = event.target.closest('[data-fetch-quiz-ai-models]');
    if (fetchButton) {
      fetchProviderModels(fetchButton.closest('.quiz-ai-provider-row'));
      return;
    }
    if (event.target.getAttribute('data-close-modal') === 'quiz-ai' || event.target.id === 'quiz-ai-modal') {
      closeQuizAiModal();
    }
  });

  document.addEventListener('change', function (event) {
    if (!event.target.matches('select[name="quiz_ai_provider_model_select"]')) return;
    const row = event.target.closest('.quiz-ai-provider-row');
    const input = row ? row.querySelector('input[name="quiz_ai_provider_model"]') : null;
    if (input && event.target.value) input.value = event.target.value;
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeQuizAiModal();
  });

  const form = document.getElementById('quiz-ai-form');
  if (form) form.addEventListener('submit', saveQuizAiConfig);
  const moduleGrid = document.getElementById('module-grid');
  if (moduleGrid && window.MutationObserver) {
    new MutationObserver(injectQuizAiButton).observe(moduleGrid, { childList: true, subtree: true });
  }
  injectQuizAiButton();
})();
