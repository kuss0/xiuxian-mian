from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_storage_bag_search_preserves_input_during_filtering_and_ime_composition():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    assert "storageBagSearchComposing" in script
    assert "compositionstart" in script
    assert "compositionend" in script
    assert "renderStorageBagSearchResults" in script
    assert "renderStorageBagTable({ preserveToolbar: true })" in script

    input_handler = script.split("document.addEventListener('input'", 1)[1].split(
        "document.addEventListener('keydown'", 1
    )[0]
    search_branch = input_handler.split("const field = event.target.closest('[data-storage-transfer-field]');", 1)[0]
    assert "renderStorageBagSearchResults()" in search_branch
    assert "renderStorageBagTable()" not in search_branch


def test_storage_bag_frontend_has_explicit_category_overrides():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    assert "STORAGE_BAG_EXPLICIT_TAGS" in script
    assert "STORAGE_BAG_PINNED_ITEMS = ['天雷竹', '二级妖丹', '金精矿']" in script
    assert "comparePinnedItems(a.name, b.name)" in script
    assert "青竹蜂云剑" in script
    assert "元磁山核" in script
    assert "真仙试锋" in script
    assert "紫灵的轻吻" in script
    assert "稳控全场" in script


def test_storage_bag_transfer_presets_keep_normal_and_money_listing_items_separate():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    preferred_listing = script.split("function preferredListingItem", 1)[1].split(
        "function availableBatchSourceRows", 1
    )[0]
    money_preset = script.split("function applyMoneyPreset", 1)[1].split(
        "function storageBagViewState", 1
    )[0]

    assert "name === '凝血草'" in preferred_listing
    assert "state.listingItem = '黄芽丹'" not in preferred_listing
    assert "state.listingItem = '黄芽丹'" in money_preset
    assert "state.listingSyntax = 'compact'" in money_preset


def test_storage_bag_transfer_runtime_allows_queueing_next_plan():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    render_panel = script.split("function renderTransferPanel", 1)[1].split(
        "function resetTransferPreviewOnly", 1
    )[0]
    start_transfer = script.split("async function startTransfer", 1)[1].split(
        "async function cancelTransfer", 1
    )[0]

    assert 'data-storage-transfer-preview="1"${busy || syncBusy ? \' disabled\' : \'\'}' in render_panel
    assert 'data-storage-transfer-start="1"${syncBusy || startPending ? \' disabled\' : \'\'}' in render_panel
    assert 'data-storage-transfer-field="batchReserveCount"' in render_panel
    assert 'data-storage-transfer-field="batchMinTransferCount"' in render_panel
    assert "startPending" in render_panel
    assert "加入队列" in render_panel
    assert "previousTableScrollTop" in render_panel
    assert "if (state.busy) return" not in start_transfer
    assert "当前已有储物袋转移任务执行中" not in start_transfer


def test_storage_bag_transfer_keeps_user_selected_target_even_when_it_was_source():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")
    normalize_defaults = script.split("function normalizeTransferDefaults", 1)[1].split(
        "function resetTransferDraftToDefaults", 1
    )[0]
    change_handler = script.split("const field = event.target.closest('[data-storage-transfer-field]');", 1)[1].split(
        "const flag = event.target.closest('[data-storage-transfer-flag]');", 1
    )[0]

    assert "state.lastChangedField = key" in change_handler
    assert "String(state.lastChangedField || '') === 'targetId'" in normalize_defaults
    assert "state.sourceId = ids.find" in normalize_defaults
    assert "state.targetId = ids.find" in normalize_defaults


def test_storage_bag_batch_runtime_expands_pending_queue_details():
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    runtime_render = script.split("function renderBatchRuntimeHtml", 1)[1].split(
        "function renderTransferPanel", 1
    )[0]

    assert "function storageTransferTaskLine" in script
    assert "storage-bag-transfer-queue-list" in runtime_render
    assert "后续队列" in runtime_render
    assert "queue.slice(0, queueLimit)" in runtime_render


def test_storage_bag_frontend_has_peer_gift_entry_and_endpoints():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")

    assert "storage-bag-gift-open-btn" in html
    assert "储物袋赠送" in script
    assert "openGiftModal" in script
    assert "/api/storage-bag-gift-preview" in script
    assert "/api/storage-bag-gift-start" in script
    assert "giftMode ? '' : `<label class=\"field-label\">集中号上架物" in script


def test_storage_bag_ui_scripts_are_cache_busted_after_deploy():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")

    assert "/static/js/app.js?v={{asset_version}}" in html
    assert "/static/js/storage_bag_ui.js?v={{asset_version}}" in html
    assert "{{asset_version}}" in html


def test_quiz_ai_frontend_config_is_loaded_outside_minified_app():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "model/web/static/js/quiz_ai_ui.js").read_text(encoding="utf-8")

    assert "quiz-ai-modal" in html
    assert "/static/js/quiz_ai_ui.js" in html
    assert "data-open-quiz-ai-config" in script
    assert "/api/quiz-ai-config" in script
    assert "api_key_configured" in script
