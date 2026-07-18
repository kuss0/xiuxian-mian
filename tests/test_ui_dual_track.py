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
    assert "/static/js/ui_write_guard.js" in body
    assert "/static/js/storage_bag_ui.js" in body
    assert "/static/js/runtime_health_ui.js" in body
    assert "/static/js/miniapp_ui.js" in body
    assert "data-open-runtime-health='1'" in body
    assert "data-open-miniapp='1'" in body
    assert "id='runtime-health-modal'" in body
    assert "id='miniapp-modal'" in body
    assert "id='runtime-health-panel'" not in body
    assert "class='topbar-left'" in body
    assert (
        body.index("class='topbar-left'")
        < body.index("id='global-switch-container'")
        < body.index("class='topbar-spacer'")
    )
    assert body.index("class='topbar-spacer'") < body.index("class='topbar-actions'")
    assert "<span id='global-switch-container'></span>" in body
    assert "sidebar-global" not in body
    assert body.index("id='global-switch-container'") < body.index("data-open-logs='1'")
    assert "id='module-grid'" in body
    assert "模块详情" not in body
    assert "当前身份的自动化模块" not in body


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
    assert body.index("/static/js/app.js") < body.index("/static/js/ui_write_guard.js") < body.index("/static/js/module_cards_ui.js") < body.index("/static/js/fishing_ui.js")
    assert "/static/js/dungeon_ui.js" in body
    assert "data-open-dungeon='1'" in body
    assert "/static/js/passive_inbox_ui.js" in body
    assert "fonts.googleapis.com" not in body


def test_miniapp_ui_is_readonly_status_with_manual_probe():
    script = (PROJECT_ROOT / "model/web/static/js/miniapp_ui.js").read_text(encoding="utf-8")

    assert "/api/miniapp-status" in script
    assert "/api/miniapp-entry-probe" in script
    assert "/api/miniapp-manual-run" in script
    assert "data-miniapp-probe" in script
    assert "data-miniapp-run" in script
    assert "setInterval" not in script
    assert "runEntryProbe" in script
    assert "runManualMiniApp" in script
    assert "默认关闭" in script
    assert "手动优先" in script
    assert "renderCommandCatalog" in script
    assert "data-miniapp-command-catalog" in script
    assert "flow_replacement_uncatalogued" in script
    assert "external_entry_not_automated" in script
    assert "自动上线" not in script
    assert "已上线" not in script


def test_module_card_override_groups_settings_and_moves_dense_toggles_into_modal():
    script = (PROJECT_ROOT / "model/web/static/js/module_cards_ui.js").read_text(encoding="utf-8")

    assert "renderModules = function(identity)" in script
    assert "module-settings-modal" in script
    assert "data-open-module-settings" in script
    assert "settingSection(" in script
    assert "toolGroup((primaryTools || '') + settingsButton, 'module-tools-primary')" in script
    assert "details class=\"module-settings\"" not in script
    assert "module-tools-primary" in script
    assert "module-main-switch" in script
    assert "renderSmallWorldFeature(identity,'manifest','显灵')" in script
    assert "primaryTools =\n          renderSmallWorldFeature(identity,'manifest','显灵')+\n          renderSmallWorldFeature(identity,'harvest','收割');" in script
    assert "renderSmallWorldFeature(identity,'barrier','护界')+\n            renderSmallWorldBarrierConfig(identity)" in script
    assert "renderSmallWorldFeature(identity,'high_stock_silence','静默')" in script
    assert "renderSmallWorldBarrierConfig(identity)" in script
    assert "data-jiyin-choice=\"offer_soul\"" in script
    assert "data-save-pet-inline" in script
    assert "data-pet-inline-name" in script
    assert "renderModuleCard('法宝', moduleNote, primaryTools, settingsTools, compactDetails(['法宝','温养器灵','器灵试炼','布下剑阵']), null)" in script
    assert "renderModuleToggle('法宝','开关')" in script
    assert "renderModuleToggle('温养器灵','开关')" in script
    assert "wanxinCheckbox('moon_greet_enabled', '婉影问安'" in script
    assert "wanxinCheckbox('moon_seal_enabled', '同参封魂'" in script
    assert "wanxinCheckbox('moon_join_enabled', '月下合参'" in script
    assert "月殿寻痕由侍妾卡片的远航开关控制" in script
    assert "情缘至少184" in script
    assert "renderModuleToggle('器灵试炼','开关')" in script
    assert "renderModuleToggle('布下剑阵','开关')" in script
    assert "['野外历练','点卯','宗门传功','闯塔','深度闭关','卜筮问天','斗法']" in script
    assert "renderModuleToggle('卜筮问天','开关')" in script
    assert "renderModuleToggle('斗法','开关')" in script
    assert "renderDuelConfig(identity)" in script
    assert "data-duel-config=\"target\"" in script
    assert "多目标池" in script
    assert "目标轮转" in script
    assert "今日封顶" in script
    assert "日志校准" in script
    assert "duel_observed_manual_count" in script
    assert "data-duel-config=\"total_count\"" in script
    assert "data-duel-config=\"reserve_xiuwei\"" in script
    assert "data-duel-config=\"window_start_time\"" in script
    assert "data-duel-config=\"window_end_time\"" in script
    assert "data-duel-config=\"reset_progress\"" in script
    assert "data-save-duel-config" in script
    assert "data-apply-duel-preset" in script
    assert "/api/duel-config" in script
    assert "/api/duel-preset-apply" in script
    assert "submitDuelConfig" in script
    assert "submitApplyDuelPreset" in script
    assert "window_start_minute" in script
    assert "window_end_minute" in script
    assert "identity.duel_next_time" in script
    assert "identity.duel_last_error" in script
    assert "identity.duel_preset_band" in script
    assert "identity.duel_gate_hint" in script
    assert "identity.duel_reserve_xiuwei" in script
    assert "identity.duel_capacity" in script
    assert "identity.duel_preset_preview" in script
    assert "duel_preset_plan" in script
    assert "容量预检" in script
    assert "仅预估不拦截" in script
    assert "预设预览" in script
    assert "同目标负载" in script
    assert "reserve_xiuwei" in script
    assert "data-divination-daily-limit" in script
    assert "windowInlineConfig('点卯', checkinWin)" in script
    assert "windowInlineConfig('闯塔', towerWin)" in script
    assert "data-open-window-modal" not in script
    assert "renderModuleCard('元婴', moduleNote, primaryTools, settingsTools, compactDetails(['元婴','探寻裂缝']), null)" in script
    assert "renderModuleToggle('探寻裂缝','开关')" in script
    assert "renderExploreRiftRebirthConfig(rebirthConfig)" in script
    assert "data-explore-rift-rebirth-config=\"preferred_root_type\"" in script
    assert "data-save-explore-rift-rebirth-config" in script
    assert "/api/explore-rift-rebirth-config" in script
    assert "data-second-soul-purge-threshold" in script
    assert "data-save-second-soul-purge-threshold" in script
    assert "submitSecondSoulPurgeThreshold" in script
    assert "identity.jiyin_effective_choice_label" in script
    assert "identity.nanlong_effective_choice_label" in script
    assert "settingCheckbox('timeline_enabled', '启用时间线'" in script
    assert "settingCheckbox('timeline_dry_run_enabled', '时间线试运行'" in script
    assert "settingCheckbox('consume_conflicting_prediction_enabled', '冲突先消费'" in script
    assert "settingCheckbox('craft_farm_allow_unpredicted_override_enabled', '允许裸炼制'" in script
    assert "settingCheckbox('retreat_farm_auto_exchange_heqi_dan', '缺丹自动兑换'" in script
    assert "data-tianxing-config=\"retreat_farm_heqi_exchange_count\"" in script
    assert "settingCheckbox('retreat_farm_auto_donate_lingshi', '贡献不足捐灵石'" in script
    assert "data-tianxing-config=\"retreat_farm_donate_lingshi_count\"" in script
    assert "data-save-tianxing-config" in script
    assert "命星与路线" not in script
    assert "data-tianxing-config=\"farm_route\"" not in script
    assert "data-tianxing-config=\"star_priority\"" not in script
    assert "data-tianxing-config=\"route_priority\"" not in script
    assert "data-tianxing-config=\"predict_route\"" not in script
    assert "data-tianxing-config=\"change_route\"" not in script
    assert "data-tianxing-config=\"target_tianji_daily\"" in script
    assert "data-tianxing-config=\"ack_timeout_sec\"" in script
    assert "执行状态" in script
    assert "献魂偏收益" not in script
    assert "收敛偏保守" not in script
    assert "不确定收益" not in script
    assert "primaryTools =\n          renderModuleToggle('野外历练','野外')" not in script
    assert "primaryTools =\n          renderModuleToggle('玄骨考校','玄骨')" not in script
    assert "module-direct-settings-button" not in script
    assert "打开名称设置" not in script


def test_module_card_css_uses_adaptive_detail_scroll_and_single_row_topbar():
    css = (PROJECT_ROOT / "model/web/static/css/ui_fixes.css").read_text(encoding="utf-8")
    new_css = (PROJECT_ROOT / "model/web_new/static/css/app.css").read_text(encoding="utf-8")

    assert "grid-auto-rows: minmax(260px, 260px);" in css
    assert "max-height: none;" in css
    assert "max-height: 170px;" not in css
    assert ".module-settings-modal-card" in css
    assert ".module-setting-section" in css
    assert ".module-inline-window" in css
    assert ".module-setting-current" in css
    assert ".module-name-input" in css
    assert ".module-settings summary" not in css
    assert "height: calc(100vh - 80px)" not in css
    assert "margin-top: 80px" not in css
    assert "flex: 0 1 100%" not in css
    assert ".replica-kind-enable-grid" in css
    assert "repeat(auto-fit, minmax(150px, 1fr))" in css
    assert ".topbar-left" in css
    assert ".app-topbar .topbar-actions > *" in css
    assert "grid-auto-rows: minmax(260px, 260px);" in new_css
    assert "body.ui-new .topbar-left" in new_css
    assert "body.ui-new .topbar-actions > *" in new_css
    assert "body.ui-new .module-setting-section" in new_css
    assert "body.ui-new .module-setting-current" in new_css
    assert "flex: 0 1 100%" not in new_css


def test_dungeon_ui_keeps_replica_open_switches_visible():
    script = (PROJECT_ROOT / "model/web/static/js/dungeon_ui.js").read_text(encoding="utf-8")

    assert "function renderReplicaKindEnableGrid(replica)" in script
    assert "开房开关" in script
    assert "data-replica-kind-enabled" in script
    assert "副本手动配置" in script
    assert "推荐/开房名单" in script
    assert "renderReplicaKindEnableGrid(replica)" in script
    assert 'data-replica-query-aggregator-toggle="1"' in script
    assert "提交查询与推荐到拉人汇聚服务" in script
    assert "/api/replica-query-aggregator-toggle" in script


def test_summary_card_script_keeps_role_resource_fields():
    script = (PROJECT_ROOT / "model/web/static/js/app.js").read_text(encoding="utf-8")

    assert "['香火',incenseText]" in script
    assert "['元婴',identity.yuanying_level_text||'未读取']" in script
    assert "['第二元神',identity.second_soul_level_text||'未读取']" in script
    assert "['洞府灵气',identity.cave_lingqi_text||'未读取']" in script


def test_ui_write_guard_blocks_stale_silent_refresh_snapshots():
    body = (PROJECT_ROOT / "model/web/static/js/ui_write_guard.js").read_text(encoding="utf-8")

    assert "writeInFlight" in body
    assert "lastWriteStartedAt" in body
    assert "lastUserEditAt" in body
    assert "unsavedUserEdit" in body
    assert "function hasBlockingUserEdit()" in body
    assert "document.addEventListener('input', markUserEdit, true)" in body
    assert "document.addEventListener('change', markUserEdit, true)" in body
    assert "window.postJson = async function" in body
    assert "window.refreshState = async function" in body
    assert "window.applySnapshot = function" in body
    assert "marker.silent && (hasBlockingWrite(marker.startedAt) || hasBlockingUserEdit())" in body
    assert "if(!marker || !marker.silent)" in body


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
