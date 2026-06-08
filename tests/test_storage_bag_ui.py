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
