(function () {
  const state = {
    date: '',
    days: [],
    q: '',
    senderId: '',
    types: ['sent', 'message', 'edit'],
    offset: 0,
    limit: 80,
    total: 0,
    hasMore: false,
    loading: false,
    searchTimer: null,
    bound: false,
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

  function escapeRegex(value) {
    return String(value == null ? '' : value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function splitLogQueryTerms(query) {
    const seen = Object.create(null);
    return String(query || '').trim().split(/\s+/).filter(function (term) {
      const key = term.toLowerCase();
      if (!key || seen[key]) {
        return false;
      }
      seen[key] = true;
      return true;
    });
  }

  function highlightQuery(value) {
    const html = esc(value);
    const terms = splitLogQueryTerms(state.q);
    if (!terms.length) {
      return html;
    }
    const pattern = new RegExp(terms.map(escapeRegex).join('|'), 'gi');
    return html.replace(pattern, function (match) {
      return '<mark class="log-highlight">' + match + '</mark>';
    });
  }

  async function getJson(path) {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.message || '请求失败');
    }
    return data;
  }

  function modal() {
    return document.getElementById('logs-modal');
  }

  function setMeta(text, className) {
    const meta = document.getElementById('logs-meta');
    if (!meta) {
      return;
    }
    meta.textContent = text || '';
    meta.className = 'logs-meta' + (className ? ' ' + className : '');
  }

  function typeLabel(type) {
    if (type === 'sent') {
      return 'sent';
    }
    if (type === 'edit') {
      return 'edit';
    }
    return 'message';
  }

  function renderEntry(entry) {
    const text = String((entry || {}).text || '').trim();
    const firstLine = text.split(/\r?\n/, 1)[0] || '(空消息)';
    const detail = text.length > firstLine.length ? text : '';
    const eventType = String((entry || {}).event_type || '');
    return '<div class="logs-entry logs-entry-' + esc(eventType || 'unknown') + '">'
      + '<div class="logs-entry-head">'
      + '<span class="logs-badge">' + esc(typeLabel(eventType)) + '</span>'
      + '<span>' + esc((entry || {}).ts || '-') + '</span>'
      + '<span>sender ' + esc((entry || {}).sender_id || '-') + '</span>'
      + '<span>msg ' + esc((entry || {}).message_id || '-') + '</span>'
      + '<span>reply ' + esc((entry || {}).reply_to_msg_id || '-') + '</span>'
      + '</div>'
      + '<div class="logs-entry-title">' + highlightQuery(firstLine) + '</div>'
      + (detail ? '<pre class="logs-entry-text">' + highlightQuery(detail) + '</pre>' : '')
      + renderLogButtons((entry || {}).buttons || [])
      + '</div>';
  }

  function normalizeLogButtons(buttons) {
    if (!Array.isArray(buttons)) {
      return [];
    }
    return buttons.map(function (row) {
      if (!Array.isArray(row)) {
        return [];
      }
      return row.filter(function (button) {
        return button && typeof button === 'object';
      });
    }).filter(function (row) {
      return row.length > 0;
    });
  }

  function buttonTypeLabel(type) {
    if (type === 'callback') {
      return '回调';
    }
    if (type === 'url') {
      return '链接';
    }
    if (type === 'web_view') {
      return '网页';
    }
    if (type === 'switch_inline') {
      return '内联';
    }
    if (type === 'request_phone') {
      return '手机号';
    }
    if (type === 'request_geo') {
      return '位置';
    }
    if (type === 'request_poll') {
      return '投票';
    }
    if (type === 'game') {
      return '游戏';
    }
    if (type === 'buy') {
      return '支付';
    }
    return '按钮';
  }

  function renderLogButtons(buttons) {
    const rows = normalizeLogButtons(buttons);
    if (!rows.length) {
      return '';
    }
    return '<div class="logs-entry-buttons" aria-label="Telegram 消息按钮">'
      + '<div class="logs-buttons-title">按钮</div>'
      + rows.map(function (row) {
        return '<div class="logs-button-row">' + row.map(function (button) {
          const type = String(button.type || '');
          const host = type === 'url' && button.url_host ? ' ' + button.url_host : '';
          return '<span class="logs-button-pill">'
            + '<span class="logs-button-text">' + highlightQuery(button.text || '无文本按钮') + '</span>'
            + '<span class="logs-button-kind">' + esc(buttonTypeLabel(type) + host) + '</span>'
            + '</span>';
        }).join('') + '</div>';
      }).join('')
      + '</div>';
  }

  async function loadDays() {
    const select = document.getElementById('logs-date');
    if (!select) {
      return;
    }
    try {
      const data = await getJson('/api/logs/days');
      state.days = Array.isArray(data.days) ? data.days.slice() : [];
      if (!state.days.length) {
        select.innerHTML = '<option value="">暂无日志</option>';
        state.date = '';
        setMeta('暂无日志文件', 'logs-meta-empty');
        return;
      }
      if (!state.date || state.days.indexOf(state.date) < 0) {
        state.date = state.days[0];
      }
      select.innerHTML = state.days.map(function (day) {
        return '<option value="' + esc(day) + '">' + esc(day) + '</option>';
      }).join('');
      select.value = state.date;
    } catch (error) {
      setMeta('加载日期失败：' + ((error && error.message) || '未知错误'), 'logs-meta-error');
    }
  }

  function selectedTypes() {
    const box = document.getElementById('logs-types');
    if (!box) {
      return state.types.slice();
    }
    return Array.from(box.querySelectorAll('input[data-log-type]:checked')).map(function (input) {
      return input.getAttribute('data-log-type');
    }).filter(Boolean);
  }

  async function loadEntries(reset) {
    if (state.loading) {
      return;
    }
    const list = document.getElementById('logs-list');
    const loadMore = document.getElementById('logs-load-more-btn');
    if (!list) {
      return;
    }
    if (!state.date) {
      list.innerHTML = '';
      setMeta('请选择日志日期', 'logs-meta-empty');
      return;
    }
    if (reset) {
      state.offset = 0;
      list.innerHTML = '';
    }
    state.loading = true;
    if (loadMore) {
      loadMore.disabled = true;
    }
    setMeta('加载中...');
    try {
      const params = new URLSearchParams();
      params.set('date', state.date);
      params.set('offset', String(state.offset));
      params.set('limit', String(state.limit));
      if (state.q) {
        params.set('q', state.q);
      }
      if (state.senderId) {
        params.set('sender_id', state.senderId);
      }
      const types = selectedTypes();
      if (types.length) {
        params.set('types', types.join(','));
      }
      const data = await getJson('/api/logs/entries?' + params.toString());
      const entries = Array.isArray(data.entries) ? data.entries : [];
      state.total = Number(data.total || 0);
      state.hasMore = !!data.has_more;
      state.offset += entries.length;
      const html = entries.map(renderEntry).join('');
      if (reset) {
        list.innerHTML = html || '<div class="queue-empty">没有匹配的日志。</div>';
      } else if (html) {
        list.insertAdjacentHTML('beforeend', html);
      }
      setMeta('共匹配 ' + state.total + ' 条，已显示 ' + Math.min(state.offset, state.total) + ' 条。');
    } catch (error) {
      if (reset) {
        list.innerHTML = '';
      }
      setMeta('加载日志失败：' + ((error && error.message) || '未知错误'), 'logs-meta-error');
    } finally {
      state.loading = false;
      if (loadMore) {
        loadMore.disabled = !state.hasMore;
        loadMore.classList.toggle('hidden', !state.hasMore);
      }
    }
  }

  async function openLogsModal() {
    const el = modal();
    if (!el) {
      return;
    }
    bindHandlers();
    el.classList.add('show');
    await loadDays();
    await loadEntries(true);
    setTimeout(function () {
      const search = document.getElementById('logs-search');
      if (search) {
        search.focus();
      }
    }, 80);
  }

  function closeLogsModal() {
    const el = modal();
    if (el) {
      el.classList.remove('show');
    }
  }

  function scheduleSearchReload() {
    if (state.searchTimer) {
      clearTimeout(state.searchTimer);
    }
    state.searchTimer = setTimeout(function () {
      state.q = (document.getElementById('logs-search') || {}).value || '';
      state.senderId = (document.getElementById('logs-sender') || {}).value || '';
      loadEntries(true);
    }, 250);
  }

  function bindHandlers() {
    if (state.bound) {
      return;
    }
    state.bound = true;
    const dateSelect = document.getElementById('logs-date');
    const search = document.getElementById('logs-search');
    const sender = document.getElementById('logs-sender');
    const refresh = document.getElementById('logs-refresh-btn');
    const loadMore = document.getElementById('logs-load-more-btn');
    const types = document.getElementById('logs-types');

    if (dateSelect) {
      dateSelect.addEventListener('change', function () {
        state.date = dateSelect.value || '';
        loadEntries(true);
      });
    }
    if (search) {
      search.addEventListener('input', scheduleSearchReload);
    }
    if (sender) {
      sender.addEventListener('input', scheduleSearchReload);
    }
    if (refresh) {
      refresh.addEventListener('click', function () {
        loadEntries(true);
      });
    }
    if (loadMore) {
      loadMore.addEventListener('click', function () {
        loadEntries(false);
      });
    }
    if (types) {
      types.addEventListener('change', function () {
        loadEntries(true);
      });
    }
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-open-logs]')) {
      openLogsModal();
      return;
    }
    if (event.target.getAttribute('data-close-modal') === 'logs' || event.target.id === 'logs-modal') {
      closeLogsModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeLogsModal();
    }
  });
})();
