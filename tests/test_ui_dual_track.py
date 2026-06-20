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


def test_render_legacy_ui_keeps_old_assets_and_links_to_new_track():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page(variant="legacy")

    assert "class='ui-legacy'" in body
    assert "href='/new?send_as_id=1001'" in body
    assert "/static/css/app.css" in body
    assert "/static/css/ui_fixes.css" in body
    assert "/static-new/css/app.css" not in body
    assert "/static/js/storage_bag_ui.js" in body


def test_legacy_home_keeps_passive_inbox_in_secondary_modal():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page(variant="legacy")

    assert "data-open-passive-inbox='1'" in body
    assert "id='passive-inbox-modal'" in body
    assert "id='passive-inbox-modal-body'" in body
    assert "id='passive-inbox-panel'" not in body
    assert "passive-inbox-home-card" not in body


def test_render_new_ui_adds_isolated_skin_and_links_back_to_legacy():
    with patch.object(ui, "get_ui_snapshot", return_value=SNAPSHOT):
        body = ui.render_ui_page(variant="new")

    assert "class='ui-new'" in body
    assert "href='/?send_as_id=1001'" in body
    assert "/static-new/css/app.css" in body
    assert "/static/js/passive_inbox_ui.js" in body
    assert "fonts.googleapis.com" not in body


def test_new_static_asset_loader_serves_css_and_blocks_traversal():
    css_body, css_type = ui._load_new_static_asset("css/app.css")
    traversal_body, traversal_type = ui._load_new_static_asset("../web/static/js/app.js")

    assert css_body is not None
    assert css_type == "text/css; charset=utf-8"
    assert b"body.ui-new" in css_body
    assert b"grid-template-columns: repeat(auto-fit, minmax(260px, 1fr))" in css_body
    assert b"scrollbar-width: none" in css_body
    assert traversal_body is None
    assert traversal_type is None
