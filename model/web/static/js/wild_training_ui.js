document.addEventListener('change', function(event) {
  const select = event.target.closest('[data-wild-training-strategy]');
  if (!select) {
    return;
  }
  postJson('/api/wild-training-strategy', {
    send_as_id: appState.selectedId,
    choice: select.value
  }).then(function(data) {
    updateFlash(data.message || '已更新野外历练策略', false);
    applySnapshot(data.snapshot || appState.snapshot, { keepFlash: true });
  }).catch(function(error) {
    updateFlash((error && error.message) || '野外历练策略更新失败', true);
    renderAll();
  });
});
