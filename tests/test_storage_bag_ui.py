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
