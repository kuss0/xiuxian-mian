import atexit
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests


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

from model import state as state_module
from model import inventory_delta
from model import storage_bag_api_client
from model import storage_bag_api_runtime
from model import ui


def _response(status_code=200, text="{}", session_value=""):
    headers = {}
    cookies = requests.cookies.cookiejar_from_dict({})
    if session_value:
        cookies = requests.cookies.cookiejar_from_dict({"session": session_value})
        headers["Set-Cookie"] = f"session={session_value}; Path=/"
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        headers=headers,
        cookies=cookies,
        json=lambda: __import__("json").loads(text),
    )


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"unexpected GET: {url}")
        return self.responses.pop(0)


class StorageBagApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self.identity_id = 1001
        state_module.ensure_identity_registered(self.identity_id)
        state_module.set_send_as_profile(self.identity_id, label="来源号", username="source", daohao="青源")
        state_module.set_storage_bag_records({})
        state_module.set_inventory_delta_records({})
        state_module.set_tianjige_dao_path_records({})
        state_module.set_storage_bag_api_config({})
        ui._storage_bag_api_state.update({
            "running": False,
            "running_kind": "",
            "last_ok": False,
            "last_message": "",
            "last_updated_at": 0,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
            "keepalive_running": False,
            "dao_path_last_ok": False,
            "dao_path_last_message": "",
            "dao_path_last_updated_at": 0,
            "dao_path_updated_count": 0,
            "dao_path_skipped_count": 0,
        })
        ui._storage_bag_miniapp_state.update({
            "running": False,
            "last_ok": False,
            "last_message": "",
            "last_updated_at": 0,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
        })

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        state_module.set_tianjige_dao_path_records({})
        state_module.set_inventory_delta_records({})
        ui._storage_bag_api_state.update({
            "running": False,
            "running_kind": "",
            "last_ok": False,
            "last_message": "",
            "last_updated_at": 0,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
            "keepalive_running": False,
            "dao_path_last_ok": False,
            "dao_path_last_message": "",
            "dao_path_last_updated_at": 0,
            "dao_path_updated_count": 0,
            "dao_path_skipped_count": 0,
        })
        ui._storage_bag_miniapp_state.update({
            "running": False,
            "last_ok": False,
            "last_message": "",
            "last_updated_at": 0,
            "updated_count": 0,
            "changed_count": 0,
            "skipped_count": 0,
        })

    def test_api_identity_candidates_and_lookup_include_username_aliases(self):
        state_module.update_send_as_profile(
            self.identity_id,
            username="source_new",
            username_aliases=["source"],
        )

        self.assertEqual(
            ["source_new", "source", "来源号", "青源"],
            storage_bag_api_runtime.storage_bag_api_cultivator_candidates(self.identity_id)[:4],
        )
        self.assertEqual(
            ["source_new", "source", "来源号", "青源"],
            ui._storage_bag_api_cultivator_candidates(self.identity_id)[:4],
        )
        self.assertEqual(
            self.identity_id,
            storage_bag_api_runtime.storage_bag_api_identity_lookup()["source"],
        )
        self.assertEqual(self.identity_id, ui._storage_bag_api_identity_lookup()["source"])

    async def test_miniapp_inventory_refresh_is_primary_and_serial(self):
        calls = []

        async def fake_run(identity_id, action, url):
            calls.append((identity_id, action, url))
            return True, "已刷新", {"changed": True}

        with patch.object(ui, "_cave_public_entry_urls_from_config", return_value=["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"]), \
                patch.object(ui, "ui_run_cave_public_entry", new=fake_run), \
                patch.object(ui, "get_identity_ids", return_value=[self.identity_id]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True):
            ok, message, snapshot = await ui.ui_refresh_storage_bag_from_miniapp({})

        self.assertTrue(ok)
        self.assertIn("洞府 MiniApp", message)
        self.assertEqual([(self.identity_id, "inventory", "")], calls)
        self.assertEqual(1, snapshot["updated_count"])
        self.assertEqual(1, snapshot["changed_count"])

    def test_miniapp_inventory_ui_exposes_primary_and_api_backup(self):
        html = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")
        script = (PROJECT_ROOT / "model/web/static/js/storage_bag_ui.js").read_text(encoding="utf-8")
        self.assertIn("storage-bag-miniapp-refresh-btn", html)
        self.assertIn("/api/storage-bag-miniapp-refresh", script)
        self.assertIn("entry_blocked", script)
        self.assertIn("天机阁 API 手动备用", script)

    def test_miniapp_inventory_snapshot_exposes_expired_entry_gate(self):
        url = "https://t.me/fanrenxiuxian_bot?startapp=df_EXPIRED"
        state_module.set_miniapp_auto_config({
            "cave_public_entry_url": url,
            "cave_public_entry_urls": [url],
            "cave_public_entry_token_blocked_signature": ui._cave_public_entry_urls_signature([url]),
            "cave_public_entry_token_blocked_at": 1_700_000_000,
            "cave_public_entry_token_retry_at": 1_700_003_600,
            "cave_public_entry_token_blocked_reason": "dwelling_token_expired",
        })

        with patch.object(ui.time, "time", return_value=1_700_000_100), \
                patch.object(ui, "get_identity_ids", return_value=[self.identity_id]), \
                patch.object(ui, "get_identity_enabled", return_value=True), \
                patch.object(ui, "is_cave_public_identity_available", return_value=True):
            snapshot = ui.get_storage_bag_miniapp_snapshot()

        self.assertTrue(snapshot["configured"])
        self.assertTrue(snapshot["entry_blocked"])
        self.assertEqual("dwelling_token_expired", snapshot["entry_block_reason"])
        self.assertNotEqual("未设置", snapshot["entry_retry_at"])

    async def test_single_identity_refresh_falls_back_to_previous_username(self):
        state_module.update_send_as_profile(
            self.identity_id,
            username="source_new",
            username_aliases=["source"],
        )
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-from-html",
        })
        old_name_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "dao_name": "青源",
                "cultivation_level": "化神中期",
                "cultivation_points": 987654,
                "status": "normal",
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/cultivator/source",
        )
        call_paths = []

        async def fake_fetch(config, path):
            call_paths.append(path)
            if path == "/api/cultivator/source_new":
                raise storage_bag_api_client.StorageBagApiError("HTTP 404", status_code=404)
            if path == "/api/cultivator/source":
                return old_name_result
            raise AssertionError(f"unexpected path: {path}")

        with patch("model.ui.fetch_storage_bag_result", new=fake_fetch):
            ok, message, _snapshot = await ui.ui_refresh_identity_from_api(self.identity_id)

        self.assertTrue(ok)
        self.assertIn("已更新 1 个身份", message)
        self.assertEqual(
            ["/api/cultivator/source_new", "/api/cultivator/source"],
            call_paths,
        )
        profile = state_module.get_send_as_profile(self.identity_id)
        self.assertEqual("source_new", profile["username"])
        self.assertIn("source", profile["username_aliases"])

    def test_storage_bag_api_snapshot_hides_credentials(self):
        ui.ui_set_storage_bag_api_config({
            "base_url": "https://example.invalid/",
            "api_token": "token-value",
            "cookie": "session=hidden",
        })

        snapshot = ui.get_storage_bag_api_snapshot()

        self.assertTrue(snapshot["configured"])
        self.assertEqual("https://example.invalid", snapshot["base_url"])
        self.assertEqual("/api/bootstrap", snapshot["verify_path"])
        self.assertEqual("/api/me", snapshot["refresh_path"])
        self.assertTrue(snapshot["api_token_configured"])
        self.assertTrue(snapshot["cookie_configured"])
        self.assertTrue(snapshot["keepalive_enabled"] is False)
        self.assertNotIn("api_token", snapshot)
        self.assertNotIn("cookie", snapshot)

    def test_cookie_header_paste_normalizes_to_session_cookie(self):
        cookie = storage_bag_api_client.normalize_storage_bag_api_cookie(
            "Cookie: cf_clearance=skip; session=SESSION_VALUE; theme=dark"
        )

        self.assertEqual("session=SESSION_VALUE", cookie)

    def test_cookie_viewer_session_dot_format_normalizes_to_cookie_assignment(self):
        cookie = storage_bag_api_client.normalize_storage_bag_api_cookie("session.eJwtSIGNED")

        self.assertEqual("session=.eJwtSIGNED", cookie)

    def test_storage_bag_api_client_bootstrap_extracts_token_and_rotated_cookie(self):
        html = '<html><script>window.DASHBOARD_API_TOKEN = "token-from-html";</script></html>'
        fake_session = _FakeSession([
            _response(200, html, "ROTATED"),
            _response(200, '{"game_items":{"mat_001":{"name":"灵石","type":"material"}}}', ""),
        ])
        with patch("model.storage_bag_api_client.requests.Session", return_value=fake_session):
            result = storage_bag_api_client._request_json_sync({
                "base_url": "https://example.invalid",
                "cookie": "session=OLD",
            }, "/api/bootstrap")

        self.assertEqual("token-from-html", result.api_token)
        self.assertEqual("session=ROTATED", result.cookie)
        self.assertEqual({"game_items": {"mat_001": {"name": "灵石", "type": "material"}}}, result.payload)
        self.assertEqual(2, len(fake_session.calls))
        self.assertIn("X-API-Token", fake_session.calls[1]["headers"])
        self.assertEqual("token-from-html", fake_session.calls[1]["headers"]["X-API-Token"])

    def test_storage_bag_api_client_uses_configured_token_without_homepage_probe(self):
        fake_session = _FakeSession([
            _response(200, '{"characters":[]}', "ROTATED"),
        ])
        with patch("model.storage_bag_api_client.requests.Session", return_value=fake_session):
            result = storage_bag_api_client._request_json_sync({
                "base_url": "https://example.invalid",
                "cookie": "session=OLD",
                "api_token": "configured-token",
            }, "/api/me")

        self.assertEqual({"characters": []}, result.payload)
        self.assertEqual("configured-token", result.api_token)
        self.assertEqual("session=ROTATED", result.cookie)
        self.assertEqual(1, len(fake_session.calls))
        self.assertEqual("https://example.invalid/api/me", fake_session.calls[0]["url"])
        self.assertEqual("configured-token", fake_session.calls[0]["headers"]["X-API-Token"])

    async def test_verify_enables_keepalive_and_persists_item_map(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
        })
        verify_result = {
            "ok": True,
            "verified": True,
            "cookie": "session=rotated",
            "api_token": "token-from-html",
            "item_name_map": {"mat_001": "灵石", "mat_101": "木髓"},
            "status_code": 200,
        }

        with patch("model.ui.verify_storage_bag_api", new=AsyncMock(return_value=verify_result)):
            ok, message, snapshot = await ui.ui_verify_storage_bag_api({})

        self.assertTrue(ok)
        self.assertIn("验证成功", message)
        self.assertTrue(snapshot["keepalive_enabled"])
        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated", config["cookie"])
        self.assertEqual("token-from-html", config["api_token"])
        self.assertEqual("灵石", config["item_name_map"]["mat_001"])
        self.assertGreater(config["verified_at"], 0)
        self.assertGreater(config["next_keepalive_at"], 0)

    async def test_manual_api_refresh_updates_storage_records_without_game_send(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
            "item_name_map": {"mat_001": "灵石", "mat_101": "木髓"},
        })
        api_payload = {
            "characters": [
                {
                    "telegram_id": self.identity_id,
                    "username": "source",
                    "dao_name": "青源",
                    "inventory": {
                        "items": [{"name": "青竹蜂云剑", "quantity": 1}],
                        "materials": {"mat_001": 5000, "mat_101": 3},
                    },
                }
            ]
        }
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload=api_payload,
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)), \
                patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
            ok, message, snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("已刷新 1 个身份", message)
        self.assertEqual(1, snapshot["updated_count"])
        self.assertEqual(1, snapshot["changed_count"])
        send_mock.assert_not_called()
        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated", config["cookie"])
        self.assertEqual("token-from-html", config["api_token"])
        self.assertTrue(config["verified_at"])
        self.assertTrue(config["keepalive_enabled"])
        self.assertTrue(config["last_keepalive_ok"])
        self.assertTrue(snapshot["verified"])
        self.assertTrue(snapshot["keepalive_enabled"])
        record = state_module.get_storage_bag_records()[str(self.identity_id)]
        self.assertEqual({"青竹蜂云剑": 1, "灵石": 5000, "木髓": 3}, record["items"])
        self.assertEqual("storage_bag_api_character", record["source"])

    async def test_storage_bag_snapshot_exposes_fresh_miniapp_delta_without_overwriting_items(self):
        state_module.set_storage_bag_records({
            str(self.identity_id): {
                "owner": "source",
                "items": {"灵石": 100},
                "sections": {"API": {"灵石": 100}},
                "updated_at": 1_700_000_000.0,
                "source": "storage_bag_api_character",
            }
        })
        result = inventory_delta.record_inventory_delta(
            self.identity_id,
            source="cave_treasure_miniapp",
            source_id="session-a",
            items={"灵石": 20, "凝血草": 2},
            now=1_700_000_100.0,
        )

        snapshot = ui.get_storage_bag_snapshot()
        row = next(item for item in snapshot["rows"] if item["identity_id"] == self.identity_id)

        self.assertTrue(result["changed"])
        self.assertEqual({"灵石": 100}, row["items"])
        self.assertEqual({"凝血草": 2, "灵石": 20}, row["pending_deltas"])
        self.assertEqual(120, row["merged_items"]["灵石"]["quantity"])
        self.assertEqual(2, row["merged_items"]["凝血草"]["quantity"])
        self.assertEqual("cave_treasure_miniapp", row["merged_items"]["灵石"]["freshness_source"])
        self.assertEqual("pending_inventory_confirm", row["merged_items"]["灵石"]["status"])
        self.assertEqual("delta_newer_than_snapshot", row["merged_items"]["灵石"]["freshness"])
        self.assertEqual(1, snapshot["inventory_freshness"]["pending_record_count"])

    async def test_inventory_delta_duplicate_source_is_idempotent_and_old_delta_is_stale(self):
        state_module.set_storage_bag_records({
            str(self.identity_id): {
                "owner": "source",
                "items": {"灵石": 100},
                "sections": {"API": {"灵石": 100}},
                "updated_at": 1_700_000_000.0,
                "source": "storage_bag_api_character",
            }
        })

        first = inventory_delta.record_inventory_delta(
            self.identity_id,
            source="cave_treasure_miniapp",
            source_id="session-a",
            items={"灵石": 20},
            now=1_699_999_900.0,
        )
        duplicate = inventory_delta.record_inventory_delta(
            self.identity_id,
            source="cave_treasure_miniapp",
            source_id="session-a",
            items={"灵石": 20},
            now=1_700_000_100.0,
        )
        snapshot = ui.get_storage_bag_snapshot()
        row = next(item for item in snapshot["rows"] if item["identity_id"] == self.identity_id)
        records = [record for key, record in state_module.get_inventory_delta_records().items() if key != "_meta"]

        self.assertTrue(first["changed"])
        self.assertFalse(duplicate["changed"])
        self.assertEqual(1, len(records))
        self.assertEqual({}, row["pending_deltas"])
        self.assertEqual({"灵石": 20}, row["inventory_freshness"]["stale_deltas"])
        self.assertEqual(100, row["merged_items"]["灵石"]["quantity"])
        self.assertEqual("storage_bag_api_character", row["merged_items"]["灵石"]["freshness_source"])
        self.assertEqual(1, snapshot["inventory_freshness"]["stale_record_count"])

    async def test_manual_api_refresh_reports_content_unchanged(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
        })
        state_module.set_storage_bag_records({
            str(self.identity_id): {
                "owner": "source",
                "items": {"青竹蜂云剑": 1},
            }
        })
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "characters": [
                    {
                        "telegram_id": self.identity_id,
                        "username": "source",
                        "inventory": {"items": [{"name": "青竹蜂云剑", "quantity": 1}]},
                    }
                ]
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)):
            ok, message, snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("内容未变化", message)
        self.assertEqual(1, snapshot["updated_count"])
        self.assertEqual(0, snapshot["changed_count"])

    async def test_manual_api_refresh_notifies_log_group_when_requested(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
        })
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "characters": [
                    {
                        "telegram_id": self.identity_id,
                        "username": "source",
                        "inventory": {"items": [{"name": "青竹蜂云剑", "quantity": 1}]},
                    }
                ]
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)), \
                patch("model.ui.send_audit_log", new=AsyncMock()) as audit_mock, \
                patch("model.ui._fire_and_forget") as fire_mock:
            ok, _message, _snapshot = await ui.ui_refresh_storage_bag_from_api(notify_log_group=True)

        self.assertTrue(ok)
        audit_mock.assert_called_once()
        audit_text = audit_mock.call_args.args[0]
        self.assertIn("储物袋 API 读取成功", audit_text)
        self.assertIn("刷新 1 个身份", audit_text)
        self.assertEqual("global", audit_mock.call_args.kwargs["scope"])
        self.assertEqual("medium", audit_mock.call_args.kwargs["priority"])
        fire_mock.assert_called_once()
        fire_mock.call_args.args[0].close()

    async def test_manual_api_refresh_accepts_direct_cultivator_shape(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
            "item_name_map": {"mat_001": "灵石"},
        })
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "dao_name": "青源",
                "inventory": {
                    "items": [{"item_id": "treasure_201", "name": "青竹蜂云剑", "quantity": 1}],
                    "materials": {"mat_001": 50},
                },
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/cultivator/source",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)):
            ok, message, _snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("已刷新 1 个身份", message)
        record = state_module.get_storage_bag_records()[str(self.identity_id)]
        self.assertEqual({"青竹蜂云剑": 1, "灵石": 50}, record["items"])
        self.assertEqual("storage_bag_api_cultivator", record["source"])

    async def test_manual_api_refresh_maps_fishing_bait_internal_keys_without_bootstrap_map(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
        })
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "inventory": {
                    "materials": {
                        "item_fishing_bait_plain": 2,
                        "item_fishing_bait_spirit_rice": 3,
                        "item_fishing_bait_demon_blood": 1,
                    },
                },
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/cultivator/source",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)):
            ok, message, _snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("已刷新 1 个身份", message)
        record = state_module.get_storage_bag_records()[str(self.identity_id)]
        self.assertEqual({"凡饵": 2, "灵米饵": 3, "妖血饵": 1}, record["items"])

    async def test_runtime_api_refresh_can_write_explicit_empty_inventory(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-from-html",
            "item_name_map": {"mat_001": "灵石"},
        })
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"旧物": 1}, "sections": {"旧": {"旧物": 1}}}})
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "inventory": {"items": [], "materials": {}},
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )
        call_paths = []

        async def fake_fetch(config, path):
            call_paths.append(path)
            return api_result

        result = await storage_bag_api_runtime.refresh_storage_bag_records_from_api(
            identity_ids=[self.identity_id],
            write_empty=True,
            fetch_func=fake_fetch,
        )

        self.assertEqual([storage_bag_api_client.REFRESH_PATH], call_paths)
        self.assertEqual([self.identity_id], result["updated_identity_ids"])
        record = state_module.get_storage_bag_records()[str(self.identity_id)]
        self.assertEqual({}, record["items"])
        self.assertTrue(record["empty"])
        self.assertEqual("storage_bag_api_cultivator", record["source"])

    async def test_manual_api_refresh_queries_other_roles_with_same_cookie(self):
        other_identity_id = 2002
        state_module.ensure_identity_registered(other_identity_id)
        state_module.set_send_as_profile(other_identity_id, label="外号", username="other", daohao="外道")
        with patch("model.ui.get_identity_ids", return_value=[self.identity_id, other_identity_id]):
            state_module.set_storage_bag_api_config({
                "base_url": "https://example.invalid",
                "cookie": "session=old",
                "api_token": "token-from-html",
                "item_name_map": {"mat_001": "灵石"},
            })
            me_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "characters": [
                        {
                            "telegram_id": self.identity_id,
                            "username": "source",
                            "dao_name": "青源",
                            "inventory": {
                                "items": [{"name": "青竹蜂云剑", "quantity": 1}],
                                "materials": {"mat_001": 5000},
                            },
                        }
                    ]
                },
                status_code=200,
                cookie="session=rotated-me",
                api_token="token-from-html",
                path="/api/me",
            )
            cultivator_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "username": "other",
                    "dao_name": "外道",
                    "inventory": {
                        "items": [{"item_id": "treasure_201", "name": "青竹蜂云剑", "quantity": 2}],
                        "materials": {"mat_001": 77},
                    },
                },
                status_code=200,
                cookie="session=rotated-other",
                api_token="token-from-html",
                path="/api/cultivator/other",
            )
            call_paths = []

            async def fake_fetch(config, path):
                call_paths.append(path)
                if path == storage_bag_api_client.REFRESH_PATH:
                    return me_result
                if path == storage_bag_api_client.build_cultivator_path("other"):
                    return cultivator_result
                raise AssertionError(f"unexpected path: {path}")

            with patch("model.ui.fetch_storage_bag_result", new=fake_fetch), \
                    patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
                ok, message, snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("已刷新 2 个身份", message)
        self.assertEqual([storage_bag_api_client.REFRESH_PATH, "/api/cultivator/other"], call_paths)
        send_mock.assert_not_called()
        records = state_module.get_storage_bag_records()
        self.assertEqual({"青竹蜂云剑": 1, "灵石": 5000}, records[str(self.identity_id)]["items"])
        self.assertEqual({"青竹蜂云剑": 2, "灵石": 77}, records[str(other_identity_id)]["items"])
        self.assertEqual("session=rotated-other", state_module.get_storage_bag_api_config()["cookie"])
        self.assertEqual(2, snapshot["updated_count"])
        self.assertEqual(2, snapshot["changed_count"])

    async def test_manual_api_refresh_skips_cultivator_404_without_disabling_cookie(self):
        other_identity_id = 2002
        state_module.ensure_identity_registered(other_identity_id)
        state_module.set_send_as_profile(other_identity_id, label="外号", username="other", daohao="外道")
        with patch("model.ui.get_identity_ids", return_value=[self.identity_id, other_identity_id]):
            state_module.set_storage_bag_api_config({
                "base_url": "https://example.invalid",
                "cookie": "session=old",
                "api_token": "token-from-html",
                "keepalive_enabled": True,
                "item_name_map": {"mat_001": "灵石"},
            })
            me_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "characters": [
                        {
                            "telegram_id": self.identity_id,
                            "username": "source",
                            "dao_name": "青源",
                            "inventory": {"materials": {"mat_001": 5000}},
                        }
                    ]
                },
                status_code=200,
                cookie="session=rotated-me",
                api_token="token-from-html",
                path="/api/me",
            )
            call_paths = []

            async def fake_fetch(config, path):
                call_paths.append(path)
                if path == storage_bag_api_client.REFRESH_PATH:
                    return me_result
                if str(path).startswith(storage_bag_api_client.CULTIVATOR_PATH_PREFIX):
                    raise storage_bag_api_client.StorageBagApiError(
                        "HTTP 404",
                        status_code=404,
                        cookie="session=rotated-404",
                        api_token="token-from-html",
                    )
                raise AssertionError(f"unexpected path: {path}")

            with patch("model.ui.fetch_storage_bag_result", new=fake_fetch):
                ok, message, snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertTrue(ok)
        self.assertIn("已刷新 1 个身份", message)
        self.assertEqual(storage_bag_api_client.REFRESH_PATH, call_paths[0])
        self.assertIn("/api/cultivator/other", call_paths)
        self.assertTrue(all(path == storage_bag_api_client.REFRESH_PATH or str(path).startswith(storage_bag_api_client.CULTIVATOR_PATH_PREFIX) for path in call_paths))
        self.assertEqual(1, snapshot["updated_count"])
        self.assertEqual(1, snapshot["skipped_count"])
        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated-404", config["cookie"])
        self.assertTrue(config["keepalive_enabled"])
        self.assertNotIn(str(other_identity_id), state_module.get_storage_bag_records())

    async def test_manual_api_refresh_does_not_mutate_on_unmatched_payload(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "",
        })
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"旧物": 1}}})
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "characters": [
                    {
                        "telegram_id": 9999,
                        "username": "other",
                        "inventory": {"items": [{"name": "木髓", "quantity": 3}]},
                    }
                ]
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)):
            ok, message, _snapshot = await ui.ui_refresh_storage_bag_from_api()

        self.assertFalse(ok)
        self.assertIn("未匹配", message)
        self.assertEqual({"旧物": 1}, state_module.get_storage_bag_records()[str(self.identity_id)]["items"])

    async def test_keepalive_scheduler_only_updates_cookie_status(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-old",
            "keepalive_enabled": True,
            "verified_at": 1,
            "last_keepalive_at": 1,
            "next_keepalive_at": 0,
            "item_name_map": {"mat_001": "灵石"},
        })
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"旧物": 1}}})
        verify_result = {
            "ok": True,
            "verified": True,
            "cookie": "session=rotated",
            "api_token": "token-new",
            "item_name_map": {"mat_001": "灵石"},
            "status_code": 200,
        }

        with patch("model.ui.verify_storage_bag_api", new=AsyncMock(return_value=verify_result)), \
                patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
            await ui.run_storage_bag_api_keepalive_scheduler(1000)

        send_mock.assert_not_called()
        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated", config["cookie"])
        self.assertEqual("token-new", config["api_token"])
        self.assertTrue(config["keepalive_enabled"])
        self.assertTrue(config["last_keepalive_ok"])
        self.assertGreater(config["next_keepalive_at"], 1000)
        self.assertEqual({"旧物": 1}, state_module.get_storage_bag_records()[str(self.identity_id)]["items"])

    async def test_game_identity_refresh_stays_on_game_command_path(self):
        with patch("model.ui.refresh_identity_info", new=AsyncMock(return_value=(True, "已开始获取角色信息，请等待"))) as refresh_mock:
            ok, message = await ui.ui_refresh_identity_info(self.identity_id, actor_id=123)

        self.assertTrue(ok)
        self.assertIn("已开始获取角色信息", message)
        refresh_mock.assert_awaited_once_with(self.identity_id, source="ui", actor_id=123)

    async def test_manual_dao_path_refresh_updates_readonly_snapshot_without_game_send_or_inventory_mutation(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-from-html",
            "item_name_map": {"mat_001": "灵石"},
        })
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"旧物": 1}}})
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "binding": {
                    "active_character_id": self.identity_id,
                    "bound_character_ids": [self.identity_id],
                    "bound_personal_character_ids": [self.identity_id],
                    "bound_channel_character_ids": [],
                    "personal_id": 999,
                    "verified_channel_ids": [],
                    "web_self_service_enabled": False,
                },
                "characters": [
                    {
                        "telegram_id": self.identity_id,
                        "username": "source",
                        "dao_name": "青源",
                        "cultivation_level": "元婴初期",
                        "cultivation_points": 123456,
                        "sect_id": 9,
                        "sect_name": "星宫",
                        "spirit_root": "异灵根(雷)",
                        "shenshi_points": 320,
                        "taiyi_shenshi_points": 100,
                        "yuanying": {"level": 13},
                        "second_soul": {"level": 34},
                        "battle_power_text": "211.48亿",
                        "inventory": {"items": [{"name": "青竹蜂云剑", "quantity": 1}], "materials": {"mat_001": 5000}},
                        "status": "normal",
                        "combat_status": "normal",
                        "dongfu": {
                            "lingqi_pool": 88.5,
                            "lingmai_level": 2,
                            "jingshi_level": 3,
                            "danfang_level": 3,
                            "dazhen_level": 4,
                            "dazhen_active": 1,
                            "dazhen_last_switch_time": "2026-04-23T15:56:19.457549+00:00",
                        },
                    }
                ],
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/me",
        )

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock(return_value=api_result)), \
                patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
            ok, message, snapshot = await ui.ui_refresh_tianjige_dao_path_from_api()

        self.assertTrue(ok)
        self.assertIn("已更新 1 个身份", message)
        self.assertTrue(snapshot["dao_path_last_ok"])
        send_mock.assert_not_called()
        self.assertEqual({"旧物": 1}, state_module.get_storage_bag_records()[str(self.identity_id)]["items"])
        records = state_module.get_tianjige_dao_path_records()
        record = records[str(self.identity_id)]
        self.assertEqual("青源", record["dao_name"])
        self.assertEqual("元婴初期", record["cultivation_level"])
        self.assertEqual("星宫", record["sect_name"])
        self.assertEqual("正常", record["state_label"])
        self.assertEqual(320, record["spiritual_sense"])
        self.assertEqual(100, record["taiyi_spiritual_sense"])
        self.assertEqual("13级", record["yuanying_level"])
        self.assertEqual("34级", record["second_soul_level"])
        self.assertEqual("88.5", record["cave_lingqi"])
        self.assertEqual(88.5, record["cave"]["lingqi_pool"])
        self.assertEqual(3, record["cave"]["danfang_level"])
        self.assertEqual(4, record["cave"]["dazhen_level"])
        self.assertNotIn("dongfu", record["cave"])
        self.assertNotIn("raw", record)
        profile = state_module.get_send_as_profile(self.identity_id)
        self.assertEqual("source", profile["username"])
        self.assertEqual("青源", profile["daohao"])
        self.assertEqual("元婴初期", profile["realm"])
        self.assertEqual(123456, profile["xiuwei_current"])
        self.assertEqual("星宫", profile["sect_name"])
        self.assertEqual("异灵根", profile["spiritual_root_type"])
        self.assertEqual("雷", profile["spiritual_root_attrs"])
        self.assertEqual("211.48亿", profile["battle_power_text"])
        self.assertEqual(21148000000, profile["battle_power_value"])
        self.assertGreater(profile["sect_updated_at"], 0)
        ui_snapshot = ui.get_tianjige_dao_path_snapshot()
        self.assertEqual(self.identity_id, ui_snapshot["binding"]["active_character_id"])
        self.assertEqual("青源", ui_snapshot["rows"][0]["dao_name"])
        self.assertEqual("正常", ui_snapshot["rows"][0]["state_label"])
        self.assertEqual(320, ui_snapshot["rows"][0]["spiritual_sense"])
        self.assertEqual(100, ui_snapshot["rows"][0]["taiyi_spiritual_sense"])
        self.assertEqual(
            "灵脉 2级｜静室 3级｜丹房 3级｜大阵 4级（已开启）｜灵气池 88.5",
            ui_snapshot["rows"][0]["cave_summary"],
        )
        with state_module.use_identity(self.identity_id):
            state_module.state["small_world_incense_stock"] = 45678
        identity_snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertEqual(45678, identity_snapshot["small_world_incense_stock"])
        self.assertEqual("13级", identity_snapshot["yuanying_level_text"])
        self.assertEqual("34级", identity_snapshot["second_soul_level_text"])
        self.assertEqual("88.5", identity_snapshot["cave_lingqi_text"])

    async def test_manual_identity_api_refresh_single_updates_profile_fields_without_storage_mutation(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-from-html",
        })
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"旧物": 1}}})
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "dao_name": "青源",
                "cultivation_level": "化神中期",
                "cultivation_points": 987654,
                "sect_name": "【太一门】",
                "spirit_root": "异灵根(雷)",
                "shenshi_points": 88,
                "taiyi_shenshi_points": 12,
                "battle_power": "211.48亿",
                "status": "normal",
                "combat_status": "normal",
                "dongfu": {"lingqi_pool": 12},
                "inventory": {"materials": {"mat_001": 9999}},
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/cultivator/source",
        )
        call_paths = []

        async def fake_fetch(config, path):
            call_paths.append(path)
            if path == storage_bag_api_client.build_cultivator_path("source"):
                return api_result
            raise AssertionError(f"unexpected path: {path}")

        with patch("model.ui.fetch_storage_bag_result", new=fake_fetch), \
                patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
            ok, message, snapshot = await ui.ui_refresh_identity_from_api(self.identity_id)

        self.assertTrue(ok)
        self.assertIn("已更新 1 个身份", message)
        self.assertEqual(["/api/cultivator/source"], call_paths)
        send_mock.assert_not_called()
        self.assertEqual({"旧物": 1}, state_module.get_storage_bag_records()[str(self.identity_id)]["items"])
        profile = state_module.get_send_as_profile(self.identity_id)
        self.assertEqual("source", profile["username"])
        self.assertEqual("青源", profile["daohao"])
        self.assertEqual("化神中期", profile["realm"])
        self.assertEqual(987654, profile["xiuwei_current"])
        self.assertEqual("太一门", profile["sect_name"])
        self.assertEqual("异灵根", profile["spiritual_root_type"])
        self.assertEqual("雷", profile["spiritual_root_attrs"])
        self.assertEqual("211.48亿", profile["battle_power_text"])
        self.assertEqual(21148000000, profile["battle_power_value"])
        self.assertTrue(snapshot["dao_path_last_ok"])
        record = state_module.get_tianjige_dao_path_records()[str(self.identity_id)]
        self.assertEqual("tianjige_cultivator", record["source"])
        self.assertEqual(88, record["spiritual_sense"])
        self.assertEqual(12, record["taiyi_spiritual_sense"])

    async def test_manual_identity_api_refresh_skips_malformed_cultivation_points_for_profile(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-from-html",
        })
        state_module.update_send_as_profile(self.identity_id, xiuwei_current=54321)
        api_result = storage_bag_api_client.StorageBagApiResult(
            payload={
                "telegram_id": self.identity_id,
                "username": "source",
                "dao_name": "青源",
                "cultivation_level": "化神中期",
                "cultivation_points": "bad-value",
                "sect_name": "太一门",
            },
            status_code=200,
            cookie="session=rotated",
            api_token="token-from-html",
            path="/api/cultivator/source",
        )

        async def fake_fetch(config, path):
            if path == storage_bag_api_client.build_cultivator_path("source"):
                return api_result
            raise AssertionError(f"unexpected path: {path}")

        with patch("model.ui.fetch_storage_bag_result", new=fake_fetch), \
                patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
            ok, _message, _snapshot = await ui.ui_refresh_identity_from_api(self.identity_id)

        self.assertTrue(ok)
        send_mock.assert_not_called()
        profile = state_module.get_send_as_profile(self.identity_id)
        self.assertEqual(54321, profile["xiuwei_current"])
        record = state_module.get_tianjige_dao_path_records()[str(self.identity_id)]
        self.assertEqual(0, record["cultivation_points"])

    async def test_dao_path_snapshot_tolerates_malformed_persisted_values(self):
        with patch("model.ui.get_identity_ids", return_value=[self.identity_id]):
            state_module.set_tianjige_dao_path_records({
                str(self.identity_id): {
                    "username": "source",
                    "dao_name": "青源",
                    "cultivation_points": "bad-value",
                    "updated_at": "bad-value",
                    "cave": [],
                    "status_fields": {},
                    "raw_keys": "bad-value",
                },
                "_meta": {"updated_at": "bad-value"},
            })

            snapshot = ui.get_tianjige_dao_path_snapshot()

        row = snapshot["rows"][0]
        self.assertEqual(0, row["cultivation_points"])
        self.assertEqual(0.0, row["updated_at_raw"])
        self.assertEqual("未设置", row["updated_at"])
        self.assertEqual({}, row["cave"])
        self.assertEqual([], row["status_fields"])
        self.assertEqual([], row["raw_keys"])
        self.assertEqual("未设置", snapshot["last_updated_at"])

    async def test_manual_dao_path_refresh_queries_other_roles_with_same_cookie(self):
        other_identity_id = 2002
        state_module.ensure_identity_registered(other_identity_id)
        state_module.set_send_as_profile(other_identity_id, label="外号", username="other", daohao="外道")
        with patch("model.ui.get_identity_ids", return_value=[self.identity_id, other_identity_id]):
            state_module.set_storage_bag_api_config({
                "base_url": "https://example.invalid",
                "cookie": "session=old",
                "api_token": "token-from-html",
            })
            me_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "characters": [
                        {
                            "telegram_id": self.identity_id,
                            "username": "source",
                            "dao_name": "青源",
                            "cultivation_level": "结丹后期",
                            "status": "normal",
                        }
                    ]
                },
                status_code=200,
                cookie="session=rotated-me",
                api_token="token-from-html",
                path="/api/me",
            )
            cultivator_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "username": "other",
                    "dao_name": "外道",
                    "cultivation_level": "化神初期",
                    "status": "normal",
                    "combat_status": "normal",
                },
                status_code=200,
                cookie="session=rotated-other",
                api_token="token-from-html",
                path="/api/cultivator/other",
            )
            call_paths = []

            async def fake_fetch(config, path):
                call_paths.append(path)
                if path == storage_bag_api_client.REFRESH_PATH:
                    return me_result
                if path == storage_bag_api_client.build_cultivator_path("other"):
                    return cultivator_result
                raise AssertionError(f"unexpected path: {path}")

            with patch("model.ui.fetch_storage_bag_result", new=fake_fetch), \
                    patch("model.ui.send_game_command", new=AsyncMock()) as send_mock:
                ok, message, snapshot = await ui.ui_refresh_identity_from_api(self.identity_id, refresh_all=True)

        self.assertTrue(ok)
        self.assertIn("已更新 2 个身份", message)
        self.assertEqual([storage_bag_api_client.REFRESH_PATH, "/api/cultivator/other"], call_paths)
        send_mock.assert_not_called()
        records = state_module.get_tianjige_dao_path_records()
        self.assertEqual("结丹后期", records[str(self.identity_id)]["cultivation_level"])
        self.assertEqual("化神初期", records[str(other_identity_id)]["cultivation_level"])
        self.assertEqual("青源", state_module.get_send_as_profile(self.identity_id)["daohao"])
        self.assertEqual("外道", state_module.get_send_as_profile(other_identity_id)["daohao"])
        self.assertEqual("session=rotated-other", state_module.get_storage_bag_api_config()["cookie"])
        self.assertEqual(2, snapshot["dao_path_updated_count"])

    async def test_manual_dao_path_refresh_skips_cultivator_404_without_disabling_cookie(self):
        other_identity_id = 2002
        state_module.ensure_identity_registered(other_identity_id)
        state_module.set_send_as_profile(other_identity_id, label="外号", username="other", daohao="外道")
        with patch("model.ui.get_identity_ids", return_value=[self.identity_id, other_identity_id]):
            state_module.set_storage_bag_api_config({
                "base_url": "https://example.invalid",
                "cookie": "session=old",
                "api_token": "token-from-html",
                "keepalive_enabled": True,
            })
            me_result = storage_bag_api_client.StorageBagApiResult(
                payload={
                    "characters": [
                        {
                            "telegram_id": self.identity_id,
                            "username": "source",
                            "dao_name": "青源",
                            "cultivation_level": "结丹后期",
                            "status": "normal",
                        }
                    ]
                },
                status_code=200,
                cookie="session=rotated-me",
                api_token="token-from-html",
                path="/api/me",
            )
            call_paths = []

            async def fake_fetch(config, path):
                call_paths.append(path)
                if path == storage_bag_api_client.REFRESH_PATH:
                    return me_result
                if str(path).startswith(storage_bag_api_client.CULTIVATOR_PATH_PREFIX):
                    raise storage_bag_api_client.StorageBagApiError(
                        "HTTP 404",
                        status_code=404,
                        cookie="session=rotated-404",
                        api_token="token-from-html",
                    )
                raise AssertionError(f"unexpected path: {path}")

            with patch("model.ui.fetch_storage_bag_result", new=fake_fetch):
                ok, message, snapshot = await ui.ui_refresh_identity_from_api(self.identity_id, refresh_all=True)

        self.assertTrue(ok)
        self.assertIn("已更新 1 个身份", message)
        self.assertEqual(storage_bag_api_client.REFRESH_PATH, call_paths[0])
        self.assertIn("/api/cultivator/other", call_paths)
        self.assertTrue(all(path == storage_bag_api_client.REFRESH_PATH or str(path).startswith(storage_bag_api_client.CULTIVATOR_PATH_PREFIX) for path in call_paths))
        self.assertEqual(1, snapshot["dao_path_updated_count"])
        self.assertEqual(1, snapshot["dao_path_skipped_count"])
        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated-404", config["cookie"])
        self.assertTrue(config["keepalive_enabled"])
        records = state_module.get_tianjige_dao_path_records()
        self.assertIn(str(self.identity_id), records)
        self.assertNotIn(str(other_identity_id), records)

    async def test_manual_api_actions_reject_during_keepalive(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-old",
        })
        ui._storage_bag_api_state["keepalive_running"] = True

        with patch("model.ui.fetch_storage_bag_result", new=AsyncMock()) as fetch_mock, \
                patch("model.ui.verify_storage_bag_api", new=AsyncMock()) as verify_mock:
            refresh_ok, refresh_message, refresh_snapshot = await ui.ui_refresh_storage_bag_from_api()
            verify_ok, verify_message, verify_snapshot = await ui.ui_verify_storage_bag_api()

        self.assertFalse(refresh_ok)
        self.assertFalse(verify_ok)
        self.assertIn("正在进行中", refresh_message)
        self.assertIn("正在进行中", verify_message)
        self.assertTrue(refresh_snapshot["running"])
        self.assertTrue(verify_snapshot["keepalive_running"])
        fetch_mock.assert_not_called()
        verify_mock.assert_not_called()

    async def test_keepalive_scheduler_skips_while_manual_api_running(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-old",
            "keepalive_enabled": True,
            "next_keepalive_at": 0,
        })
        ui._storage_bag_api_state["running"] = True

        with patch("model.ui.verify_storage_bag_api", new=AsyncMock()) as verify_mock:
            await ui.run_storage_bag_api_keepalive_scheduler(1000)

        self.assertTrue(ui._storage_bag_api_state["running"])
        self.assertFalse(ui._storage_bag_api_state["keepalive_running"])
        verify_mock.assert_not_called()

    async def test_keepalive_scheduler_disables_on_auth_failure(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-old",
            "keepalive_enabled": True,
            "verified_at": 1,
            "next_keepalive_at": 0,
        })
        exc = storage_bag_api_client.StorageBagApiError(
            "HTTP 401",
            status_code=401,
            auth_failed=True,
            cookie="session=rotated",
            api_token="token-new",
        )

        with patch("model.ui.verify_storage_bag_api", new=AsyncMock(side_effect=exc)):
            await ui.run_storage_bag_api_keepalive_scheduler(1000)

        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated", config["cookie"])
        self.assertEqual("token-new", config["api_token"])
        self.assertFalse(config["keepalive_enabled"])
        self.assertFalse(config["last_keepalive_ok"])
        self.assertIn("401", config["last_keepalive_error"])

    async def test_keepalive_scheduler_keeps_enabled_on_forbidden_backoff(self):
        state_module.set_storage_bag_api_config({
            "base_url": "https://example.invalid",
            "cookie": "session=old",
            "api_token": "token-old",
            "keepalive_enabled": True,
            "verified_at": 1,
            "next_keepalive_at": 0,
        })
        exc = storage_bag_api_client.StorageBagApiError(
            "HTTP 403",
            status_code=403,
            auth_failed=True,
            cookie="session=rotated",
            api_token="token-new",
        )

        with patch("model.ui.verify_storage_bag_api", new=AsyncMock(side_effect=exc)):
            await ui.run_storage_bag_api_keepalive_scheduler(1000)

        config = state_module.get_storage_bag_api_config()
        self.assertEqual("session=rotated", config["cookie"])
        self.assertEqual("token-new", config["api_token"])
        self.assertTrue(config["keepalive_enabled"])
        self.assertFalse(config["last_keepalive_ok"])
        self.assertGreater(config["next_keepalive_at"], 1000)
        self.assertIn("403", config["last_keepalive_error"])


if __name__ == "__main__":
    unittest.main()
