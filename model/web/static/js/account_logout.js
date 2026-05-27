function updateAccountLogoutButton() {
    const select = document.getElementById('identity-account-select');
    const btn = document.getElementById('logout-account-btn');
    if (!select || !btn) return;
    btn.classList.toggle('hidden', !select.value);
}

function getAccountLogoutAccounts() {
    const accounts = appState.snapshot && appState.snapshot.accounts || {};
    return accounts && typeof accounts === 'object' ? accounts : {};
}

function getAccountLogoutLabel(accountId) {
    const accounts = getAccountLogoutAccounts();
    const accountInfo = accounts[accountId] || {};
    return ((accountInfo && accountInfo.username) || accountId) + ' (' + accountId + ')';
}

function getAccountLogoutBoundIdentities(accountId) {
    return getIdentities().filter(function(item) {
        return String(item.account_id || '') === String(accountId);
    });
}

function updateAccountLogoutSummary() {
    const select = document.getElementById('logout-account-select');
    const summary = document.getElementById('logout-account-summary');
    const btn = document.getElementById('confirm-logout-account-btn');
    if (!select || !summary) return;
    const accountId = select.value;
    if (!accountId) {
        summary.textContent = '当前没有可退出的登录账号。';
        if (btn) btn.disabled = true;
        return;
    }
    const boundCount = getAccountLogoutBoundIdentities(accountId).length;
    summary.textContent = boundCount
        ? '该账号绑定身份 ' + boundCount + ' 个；退出后会暂停并解绑这些身份，但不会删除身份数据。'
        : '该账号当前没有绑定身份；退出只会移除本地登录态和 session 文件。';
    if (btn) btn.disabled = false;
}

function renderAccountLogoutModal() {
    const select = document.getElementById('logout-account-select');
    if (!select) return;
    const accounts = getAccountLogoutAccounts();
    const entries = Object.entries(accounts);
    const previousValue = select.value;
    if (!entries.length) {
        select.innerHTML = '<option value="">当前没有可退出的登录账号</option>';
        select.value = '';
        select.disabled = true;
        updateAccountLogoutSummary();
        return;
    }
    select.disabled = false;
    select.innerHTML = entries.map(function(entry) {
        const accountId = String(entry[0]);
        return '<option value="' + escapeHtml(accountId) + '">' + escapeHtml(getAccountLogoutLabel(accountId)) + '</option>';
    }).join('');
    if (entries.some(function(entry) { return String(entry[0]) === String(previousValue); })) {
        select.value = previousValue;
    }
    updateAccountLogoutSummary();
}

function openAccountLogoutModal() {
    const modal = document.getElementById('account-logout-modal');
    if (!modal) return;
    renderAccountLogoutModal();
    modal.classList.add('show');
    const select = document.getElementById('logout-account-select');
    if (select && !select.disabled) {
        select.focus();
    }
}

function closeAccountLogoutModal() {
    const modal = document.getElementById('account-logout-modal');
    if (modal) {
        modal.classList.remove('show');
    }
}

async function logoutSelectedAccount(source) {
    const useStandaloneModal = source === 'standalone';
    const select = document.getElementById(useStandaloneModal ? 'logout-account-select' : 'identity-account-select');
    const accountId = select && select.value;
    if (!accountId) return;

    const accountLabel = getAccountLogoutLabel(accountId);
    const boundIdentities = getAccountLogoutBoundIdentities(accountId);
    const boundText = boundIdentities.length
        ? '\n绑定身份 ' + boundIdentities.length + ' 个会被暂停并解绑，但不会删除身份数据。'
        : '';
    const confirmed = window.confirm(
        '确认退出账号 ' + accountLabel + '？\n\n' +
        '这会移除本地登录态并清理 session 文件，不会删除角色身份。' +
        boundText +
        '\n\n退出后绑定身份不会继续自动发送，重新登录后可再绑定。'
    );
    if (!confirmed) return;

    const btn = document.getElementById(useStandaloneModal ? 'confirm-logout-account-btn' : 'logout-account-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '退出中...';
    }
    try {
        const data = await postJson('/api/account-logout', { account_id: accountId });
        updateFlash(data.message || '已退出账号', false);
        if (useStandaloneModal) {
            closeAccountLogoutModal();
        } else {
            closeIdentityModal();
        }
        applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    } catch (error) {
        updateFlash((error && error.message) || '退出账号失败', true);
        renderAll();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '退出账号';
        }
        updateAccountLogoutButton();
        updateAccountLogoutSummary();
    }
}

document.addEventListener('change', function(event) {
    if (event.target && event.target.id === 'identity-account-select') {
        updateAccountLogoutButton();
    }
    if (event.target && event.target.id === 'logout-account-select') {
        updateAccountLogoutSummary();
    }
});

document.addEventListener('click', function(event) {
    if (event.target.closest('#logout-account-btn')) {
        logoutSelectedAccount('identity');
        return;
    }
    if (event.target.closest('#confirm-logout-account-btn')) {
        logoutSelectedAccount('standalone');
        return;
    }
    if (event.target.closest('[data-open-logout-account]')) {
        openAccountLogoutModal();
        return;
    }
    if (event.target.getAttribute('data-close-modal') === 'account-logout' || event.target.id === 'account-logout-modal') {
        closeAccountLogoutModal();
        return;
    }
    if (event.target.closest('[data-open-add-identity]')) {
        window.setTimeout(updateAccountLogoutButton, 0);
    }
});

document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        closeAccountLogoutModal();
    }
});
