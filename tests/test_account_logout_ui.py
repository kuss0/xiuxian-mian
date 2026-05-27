from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_logout_entry_has_modal_and_handler():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "model/web/static/js/account_logout.js").read_text(encoding="utf-8")

    assert "data-open-logout-account" in html
    assert "id='account-logout-modal'" in html
    assert "id='logout-account-select'" in html
    assert "id='confirm-logout-account-btn'" in html
    assert "data-close-modal='account-logout'" in html

    assert "[data-open-logout-account]" in script
    assert "openAccountLogoutModal" in script
    assert "closeAccountLogoutModal" in script
    assert "logout-account-select" in script
    assert "confirm-logout-account-btn" in script
    assert "/api/account-logout" in script


def test_account_logout_script_loads_after_main_app_script():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")

    app_index = html.index("<script src='/static/js/app.js'></script>")
    logout_index = html.index("<script src='/static/js/account_logout.js'></script>")

    assert app_index < logout_index
