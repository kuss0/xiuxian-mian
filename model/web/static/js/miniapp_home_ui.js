(function () {
  if (typeof window === 'undefined') return;

  function refreshHome() {
    if (typeof window.refreshMiniAppHome === 'function') {
      window.refreshMiniAppHome();
    }
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-miniapp-home-refresh]')) {
      refreshHome();
      return;
    }
    if (event.target.closest('[data-focus-miniapp]')) {
      var home = document.getElementById('miniapp-home-body');
      var section = home && home.closest('.miniapp-home-section');
      if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      refreshHome();
      return;
    }
    if (event.target.closest('[data-select-identity]')) {
      window.setTimeout(refreshHome, 80);
    }
  });

  document.addEventListener('change', function (event) {
    if (event.target && event.target.id === 'identity-select-mobile') {
      window.setTimeout(refreshHome, 80);
    }
  });

  window.setTimeout(refreshHome, 0);
  window.setInterval(refreshHome, 30000);
})();
