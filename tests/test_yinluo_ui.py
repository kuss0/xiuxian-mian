from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_yinluo_frontend_script_is_loaded_after_main_app():
    html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")

    app_pos = html.index("/static/js/app.js")
    yinluo_pos = html.index("/static/js/yinluo_ui.js")
    assert app_pos < yinluo_pos


def test_yinluo_module_card_keeps_main_switch_and_action_panel_hook():
    app_script = (PROJECT_ROOT / "model/web/static/js/app.js").read_text(encoding="utf-8")
    yinluo_script = (PROJECT_ROOT / "model/web/static/js/yinluo_ui.js").read_text(encoding="utf-8")

    hidden_modules_body = app_script.split("const hiddenModules=new Set([", 1)[1].split("]);", 1)[0]
    assert "阴罗宗" not in hidden_modules_body
    assert "data-toggle-module=\"1\"" in app_script
    assert "module-title" in app_script

    assert "data-yinluo-panel" in yinluo_script
    assert "textContent.trim() === '阴罗宗'" in yinluo_script
    assert "originalRenderModules(identity)" in yinluo_script
    assert "enhanceYinluoCard(identity)" in yinluo_script
    assert "/api/yinluo-action" in yinluo_script

    for action in ("banner", "blood_forest", "demon_summon", "collect", "refine", "convert"):
        assert f'data-yinluo-action="{action}"' in yinluo_script
