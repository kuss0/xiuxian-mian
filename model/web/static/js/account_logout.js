function updateAccountLogoutButton() {
    const select = document.getElementById('identity-account-select');
    const btn = document.getElementById('logout-account-btn');
    if (!select || !btn) return;
    btn.classList.toggle('hidden', !select.value);
}

async function logoutSelectedAccount() {
    const select = document.getElementById('identity-account-select');
    const accountId = select && select.value;
    if (!accountId) return;

    const accounts = appState.snapshot && appState.snapshot.accounts || {};
    const accountInfo = accounts[accountId] || {};
    const accountLabel = ((accountInfo && accountInfo.username) || accountId) + ' (' + accountId + ')';
    const boundIdentities = getIdentities().filter(function(item) {
        return String(item.account_id || '') === String(accountId);
    });
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

    const btn = document.getElementById('logout-account-btn');
    btn.disabled = true;
    btn.textContent = '退出中...';
    try {
        const data = await postJson('/api/account-logout', { account_id: accountId });
        updateFlash(data.message || '已退出账号', false);
        closeIdentityModal();
        applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
    } catch (error) {
        updateFlash((error && error.message) || '退出账号失败', true);
        renderAll();
    } finally {
        btn.disabled = false;
        btn.textContent = '退出账号';
        updateAccountLogoutButton();
    }
}

document.addEventListener('change', function(event) {
    if (event.target && event.target.id === 'identity-account-select') {
        updateAccountLogoutButton();
    }
});

document.addEventListener('click', function(event) {
    if (event.target.closest('#logout-account-btn')) {
        logoutSelectedAccount();
        return;
    }
    if (event.target.closest('[data-open-add-identity]')) {
        window.setTimeout(updateAccountLogoutButton, 0);
    }
});
