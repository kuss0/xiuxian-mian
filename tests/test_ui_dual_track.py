import atexit
import sys
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import ui


SNAPSHOT = {
    "generated_at": "2026-06-20 12:00:00",
    "global_enabled": True,
    "account_user_id": "123",
    "identities": [
        {
            "send_as_id": 1001,
            "display_name": "测试号",
            "identity_enabled": True,
            "identity_status_text": "运行中",
            "modules": [],
        }
    ],
}


def test_render_default_ui_uses_new_skin_without_mode_switch():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page()

    assert "class='ui-new'" in body
    assert "ui-mode-entry" not in body
    assert "href='/new?send_as_id=1001'" not in body
    assert "href='/?send_as_id=1001'" not in body
    assert "/static/css/app.css" in body
    assert "/static/css/ui_fixes.css" in body
    assert "/static-new/css/app.css" in body
    assert "/static/js/module_cards_ui.js" in body
    assert "/static/js/storage_bag_ui.js" in body
    assert "<span id='global-switch-container'></span>" in body
    assert "sidebar-global" not in body
    assert body.index("id='global-switch-container'") < body.index("data-open-logs='1'")


def test_legacy_variant_parameter_keeps_new_single_track_ui():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page(variant="legacy")

    assert "class='ui-new'" in body
    assert "class='ui-legacy'" not in body
    assert "ui-mode-entry" not in body
    assert "/static-new/css/app.css" in body


def test_render_new_ui_keeps_passive_inbox_on_home_without_legacy_link():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page(variant="new")

    assert "class='ui-new'" in body
    assert "ui-mode-entry" not in body
    assert "href='/?send_as_id=1001'" not in body
    assert "id='passive-inbox-modal'" in body
    assert "data-open-passive-inbox='1'" in body
    assert "/static-new/css/app.css" in body
    assert body.index("/static/js/app.js") < body.index("/static/js/module_cards_ui.js") < body.index("/static/js/fishing_ui.js")
    assert "/static/js/passive_inbox_ui.js" in body
    assert "fonts.googleapis.com" not in body


def test_module_card_override_groups_settings_and_keeps_toggles_on_card():
    script = (PROJECT_ROOT / "model/web/static/js/module_cards_ui.js").read_text(encoding="utf-8")

    assert "renderModules = function(identity)" in script
    assert "module-settings" in script
    assert "module-tools-primary" in script
    assert "module-main-switch" in script
    assert "renderSmallWorldFeature(identity,'manifest','显灵')" in script
    assert "renderSmallWorldBarrierConfig(identity)" in script
    assert "data-jiyin-choice=\"offer_soul\"" in script
    assert "data-open-pet-modal=\"1\"" in script


def test_module_card_css_uses_adaptive_detail_scroll_and_single_row_topbar():
    css = (PROJECT_ROOT / "model/web/static/css/ui_fixes.css").read_text(encoding="utf-8")
    new_css = (PROJECT_ROOT / "model/web_new/static/css/app.css").read_text(encoding="utf-8")

    assert "grid-auto-rows: minmax(260px, 260px);" in css
    assert "max-height: none;" in css
    assert "max-height: 170px;" not in css
    assert ".module-settings" in css
    assert "height: calc(100vh - 80px)" not in css
    assert "margin-top: 80px" not in css
    assert "flex: 0 1 100%" not in css
    assert "grid-auto-rows: minmax(260px, 260px);" in new_css
    assert "flex: 0 1 100%" not in new_css


def test_summary_card_script_keeps_role_resource_fields():
    script = (PROJECT_ROOT / "model/web/static/js/app.js").read_text(encoding="utf-8")

    assert "['香火',incenseText]" in script
    assert "['元婴',identity.yuanying_level_text||'未读取']" in script
    assert "['第二元神',identity.second_soul_level_text||'未读取']" in script
    assert "['洞府灵气',identity.cave_lingqi_text||'未读取']" in script


def test_new_static_asset_loader_serves_css_and_blocks_traversal():
    css_body, css_type = ui._load_new_static_asset("css/app.css")
    traversal_body, traversal_type = ui._load_new_static_asset("../web/static/js/app.js")

    assert css_body is not None
    assert css_type == "text/css; charset=utf-8"
    assert b"body.ui-new" in css_body
    assert b"grid-template-columns: repeat(auto-fit, minmax(min(100%, 288px), 1fr))" in css_body
    assert b"@media (max-width: 760px)" in css_body
    assert traversal_body is None
    assert traversal_type is None
