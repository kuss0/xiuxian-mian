import atexit
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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
from model.features import passive_inbox, small_world, storage_bag


LATEST_SMALL_WORLD_PANEL = (
    "【获赦之人_cu的小世界】\n\n"
    "⛩️ 神庙: Lv.1【草创神龛】\n"
    "👥 人口: 100000 人\n"
    "🏙️ 承载上限: 100000 人\n"
    "🙏 信仰: 97 / 100\n"
    "⚖️ 稳定: 50 / 100\n"
    "☁️ 待收香火: 725.25\n"
    "🏺 香火库存: 4\n"
    "🔥 预计产出: 116.40 香火/小时\n"
    "🛡️ 护界禁制: 未开启\n"
    "🧠 神识强度: 17878\n\n"
    "暂无祈愿，凡间风调雨顺。\n"
    "(下一次祈愿感应需等待: 8分钟59秒)\n\n"
    "下一阶【乡土神庙】消耗：香火x3000、灵石x10000\n\n"
    "指令: .收割香火 | .神识淬炼 <数量> | .神迹 赈灾/布道 | .升级神庙 | .护界禁制 | .神庙"
)


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._passive_stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_passive_snapshot = dict(passive_inbox._observed_passive_events)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        passive_inbox._passive_stats = self._passive_stats_snapshot
        passive_inbox._observed_passive_events = self._observed_passive_snapshot
        super().tearDown()


class SmallWorldTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_parse_latest_temple_panel_fields(self):
        panel = small_world._parse_small_world_panel(LATEST_SMALL_WORLD_PANEL)

        self.assertIsNotNone(panel)
        self.assertEqual("获赦之人_cu", panel["owner"])
        self.assertEqual(1, panel["temple_level"])
        self.assertEqual("草创神龛", panel["temple_name"])
        self.assertEqual(100000, panel["population"])
        self.assertEqual(100000, panel["capacity"])
        self.assertEqual(97, panel["faith"])
        self.assertEqual(100, panel["faith_max"])
        self.assertEqual(50, panel["stability"])
        self.assertEqual(100, panel["stability_max"])
        self.assertEqual(725.25, panel["pending_incense"])
        self.assertEqual(4, panel["stock"])
        self.assertEqual(116.40, panel["hourly_output"])
        self.assertEqual("未开启", panel["barrier_status"])
        self.assertEqual(17878, panel["spiritual_strength"])
        self.assertFalse(panel["has_prayer"])
        self.assertTrue(panel["has_wait"])
        self.assertEqual(8 * 60 + 59, panel["wait_sec"])
        self.assertEqual("乡土神庙", panel["next_temple_name"])
        self.assertEqual("香火x3000、灵石x10000", panel["next_temple_cost"])

    async def test_latest_temple_panel_updates_snapshot_and_wait(self):
        send_as_id = 8659059188
        now = 9000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(LATEST_SMALL_WORLD_PANEL)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            with (
                patch.object(small_world, "save_state"),
                patch.object(small_world.random, "uniform", return_value=60),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(97, state_module.state["small_world_faith_value"])
            self.assertEqual(725.25, state_module.state["small_world_pending_incense"])
            self.assertEqual(4, state_module.state["small_world_incense_stock"])
            self.assertEqual(
                now + (8 * 60 + 59) + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertEqual("获赦之人_cu", snapshot["owner"])
            self.assertEqual("草创神龛", snapshot["temple_name"])
            self.assertEqual(50, snapshot["stability"])
            self.assertEqual(116.40, snapshot["hourly_output"])
            self.assertEqual("未开启", snapshot["barrier_status"])
            self.assertEqual("8分钟59秒", snapshot["wait_text"])

    async def test_no_prayer_small_incense_does_not_harvest_again(self):
        send_as_id = 8659059191
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "👥 人口: 100308 人\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 1.62\n"
            "🏺 香火库存: 1\n"
            "🧠 神识强度: 3556\n\n"
            "暂无祈愿，凡间风调雨顺。\n\n"
            "指令: .收割香火 | .神识淬炼 <数量>"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            with (
                patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
                patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_not_awaited()
            refine_mock.assert_not_awaited()
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual(1, state_module.state["small_world_refresh_count"])

    async def test_no_prayer_large_incense_can_harvest_as_refresh_tool(self):
        send_as_id = 8659059192
        now = 2000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 1608.92\n"
            "🏺 香火库存: 2\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            with (
                patch.object(small_world, "_send_harvest", new=AsyncMock(return_value=True)) as harvest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_awaited_once()

    async def test_refresh_round_does_not_harvest_again(self):
        send_as_id = 8659059292
        now = 2500.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 15.08\n"
            "🏺 香火库存: 2\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_refresh_count"] = 1
            with (
                patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
                patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_not_awaited()
            refine_mock.assert_not_awaited()
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual(2, state_module.state["small_world_refresh_count"])

    async def test_harvest_requires_refine_enabled(self):
        send_as_id = 8659059193
        now = 3000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 1608.92\n"
            "🏺 香火库存: 2\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = False
            state_module.state["small_world_refresh_enabled"] = True
            with (
                patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_not_awaited()
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])

    async def test_send_harvest_waits_for_reply_before_changing_inventory(self):
        send_as_id = 8659059293
        now = 3100.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_incense_stock"] = 2
            state_module.state["small_world_pending_incense"] = 1035.59

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7601, sent_at=now + 1))) as send_mock,
                patch.object(small_world.random, "uniform", return_value=120),
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_harvest(now)

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(small_world.CMD_SMALL_WORLD_HARVEST, track=False, priority="chain")
            self.assertEqual("harvest_sent", state_module.state["small_world_phase"])
            self.assertEqual(7601, state_module.state["small_world_harvest_msg_id"])
            self.assertEqual(2, state_module.state["small_world_incense_stock"])
            self.assertEqual(1035.59, state_module.state["small_world_pending_incense"])
            self.assertEqual(now + 1 + 120, state_module.state["next_small_world_time"])
            self.assertIn("等待回执", state_module.state["small_world_last_error"])

    async def test_send_refine_waits_for_reply_before_changing_stock(self):
        send_as_id = 8659059294
        now = 3200.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_incense_stock"] = 1038

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7602, sent_at=now + 1))) as send_mock,
                patch.object(small_world.random, "uniform", return_value=120),
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_refine(now, 1030)

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(f"{small_world.CMD_SMALL_WORLD_REFINE} 1030", track=False, priority="chain")
            self.assertEqual("refine_sent", state_module.state["small_world_phase"])
            self.assertEqual(7602, state_module.state["small_world_refine_msg_id"])
            self.assertEqual(1038, state_module.state["small_world_incense_stock"])
            self.assertEqual(now + 1 + 120, state_module.state["next_small_world_time"])
            self.assertIn("等待回执", state_module.state["small_world_last_error"])

    async def test_harvest_timeout_rechecks_panel_instead_of_refining_from_local_stock(self):
        send_as_id = 8659059296
        now = 3300.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_phase"] = "harvest_sent"
            state_module.state["small_world_harvest_msg_id"] = 7603
            state_module.state["small_world_incense_stock"] = 1038
            state_module.state["next_small_world_time"] = now - 1

            with (
                patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock,
                patch.object(small_world, "_send_refine", new=AsyncMock(return_value=True)) as refine_mock,
            ):
                await small_world.run_small_world_scheduler(now)

            query_mock.assert_awaited_once_with(now, "收割后复查")
            refine_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_harvest_msg_id"])
            self.assertIn("复查面板", state_module.state["small_world_last_error"])

    async def test_harvest_reply_updates_inventory_only_from_real_receipt(self):
        send_as_id = 8659059297
        now = 3400.0
        state_module.ensure_identity_registered(send_as_id)
        reply_to = SimpleNamespace(id=7604, raw_text=small_world.CMD_SMALL_WORLD_HARVEST)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = False
            state_module.state["small_world_phase"] = "harvest_sent"
            state_module.state["small_world_harvest_msg_id"] = 7604
            state_module.state["small_world_incense_stock"] = 2
            state_module.state["small_world_pending_incense"] = 1035.59

            with (
                patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_harvest_reply(
                    "你大手一挥，收割了凡人供奉的香火。\n当前香火库存: 1038",
                    now,
                    reply_to=reply_to,
                    matched_family="small_world_harvest",
                )

            self.assertTrue(handled)
            self.assertEqual(1038, state_module.state["small_world_incense_stock"])
            self.assertEqual(0, state_module.state["small_world_pending_incense"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            query_mock.assert_awaited_once_with(now, "收割后复查")

    async def test_preach_reply_uses_threshold_not_full_faith(self):
        send_as_id = 8659059194
        now = 4000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "【神音浩荡】\n信仰值大幅提升至 96！",
                    now,
                    reply_to=None,
                    matched_family="small_world_preach",
                )

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()

    async def test_incense_theft_broadcast_schedules_short_calibration(self):
        send_as_id = 8659059195
        now = 5000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wxjerry", label="wxjerry")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_phase"] = "refine_sent"
            state_module.state["small_world_refine_msg_id"] = 123
            state_module.state["small_world_incense_stock"] = 8
            state_module.state["next_small_world_time"] = now + 60

            with (
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
                patch.object(small_world.random, "uniform", return_value=45),
            ):
                handled = await small_world.handle_small_world_disaster_broadcast(
                    "⚡ 【小世界·天降浩劫】 ⚡\n"
                    "道友 @wxjerry 的小世界遭遇 【邪神蛊惑】！\n"
                    "有域外邪神潜入小世界传播伪教，窃取了你的香火！\n"
                    "❌ 惨重代价: 库存香火损失 3 点\n"
                    "请速速查看 .小世界 并安抚信徒！",
                    now,
                    event=None,
                )

            self.assertTrue(handled)
            self.assertEqual("calibration_wait", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_refine_msg_id"])
            self.assertEqual(5, state_module.state["small_world_incense_stock"])
            self.assertEqual(now + 45, state_module.state["next_small_world_time"])
            self.assertIn("等待面板校准", state_module.state["small_world_last_error"])
            audit_mock.assert_awaited_once()
            self.assertIn("校准面板", audit_mock.await_args.args[0])

    async def test_incense_theft_calibration_wait_sends_query_when_due(self):
        send_as_id = 8659059295
        now = 8000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_phase"] = "calibration_wait"
            state_module.state["next_small_world_time"] = now - 1

            with (
                patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock,
                patch.object(small_world, "save_state"),
            ):
                await small_world.run_small_world_scheduler(now)

            query_mock.assert_awaited_once_with(now, "失窃后校准")

    async def test_incense_theft_for_other_identity_is_ignored(self):
        send_as_id = 8659059196
        now = 6000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wxjerry", label="wxjerry")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_incense_stock"] = 8

            with (
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_disaster_broadcast(
                    "⚡ 【小世界·天降浩劫】 ⚡\n"
                    "道友 @someone_else 的小世界遭遇 【邪神蛊惑】！\n"
                    "有域外邪神潜入小世界传播伪教，窃取了你的香火！\n"
                    "❌ 惨重代价: 库存香火损失 3 点\n"
                    "请速速查看 .小世界 并安抚信徒！",
                    now,
                    event=None,
                )

            self.assertFalse(handled)
            self.assertEqual(8, state_module.state["small_world_incense_stock"])
            audit_mock.assert_not_awaited()

    async def test_passive_inbox_owner_hint_wins_over_body_at_mentions(self):
        send_as_id = 8659059396
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wxjerry", label="wxjerry")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955048)
        text = (
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 1608.92\n"
            "🏺 香火库存: 2\n\n"
            "旁注：@wxjerry 曾路过此界。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_faith_value"] = 7
            state_module.state["small_world_incense_stock"] = 8

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=7000.0,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertFalse(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual(7, state_module.state["small_world_faith_value"])
            self.assertEqual(8, state_module.state["small_world_incense_stock"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skip_reasons"]["external_owner_no_match"])

    async def test_manifest_success_deducts_cached_storage_cost_from_panel(self):
        send_as_id = 8659059197
        now = 7000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 815.83\n"
            "🏺 香火库存: 4\n\n"
            "🔥 凡人祈愿：瘟疫\n"
            "⚡ 显灵消耗: 清灵丹x2\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.set_storage_bag_records({
                str(send_as_id): {
                    "updated_at": 6900,
                    "items": {"清灵丹": 5},
                    "sections": {"法宝/丹药/杂物": {"清灵丹": 5}},
                }
            })

            with (
                patch.object(small_world, "_send_manifest", new=AsyncMock(return_value=True)) as manifest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled_panel = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled_panel)
            manifest_mock.assert_awaited_once()
            self.assertEqual("清灵丹x2", state_module.state["small_world_manifest_cost_text"])

            with (
                patch.object(small_world, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(small_world.random, "uniform", return_value=60),
            ):
                handled_manifest = await small_world.handle_small_world_manifest_reply(
                    "显灵成功，凡人祈愿已平息。",
                    now + 1,
                    reply_to=None,
                    matched_family="small_world_manifest",
                )

            self.assertTrue(handled_manifest)
            record = state_module.get_storage_bag_records()[str(send_as_id)]
            self.assertEqual(3, record["items"]["清灵丹"])
            self.assertEqual("", state_module.state["small_world_manifest_cost_text"])

    async def test_manifest_success_does_not_deduct_cultivation_as_storage_item(self):
        send_as_id = 8659059198
        now = 7200.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 815.83\n"
            "🏺 香火库存: 4\n\n"
            "🔥 凡人祈愿：妖兽袭村\n"
            "⚡ 显灵消耗: 修为x500、清灵丹x2\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.set_storage_bag_records({
                str(send_as_id): {
                    "updated_at": 7100,
                    "items": {"清灵丹": 5},
                    "sections": {"法宝/丹药/杂物": {"清灵丹": 5}},
                }
            })

            with (
                patch.object(small_world, "_send_manifest", new=AsyncMock(return_value=True)),
                patch.object(small_world, "save_state"),
            ):
                handled_panel = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled_panel)
            self.assertEqual("修为x500、清灵丹x2", state_module.state["small_world_manifest_cost_text"])

            with (
                patch.object(small_world, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(small_world.random, "uniform", return_value=60),
            ):
                handled_manifest = await small_world.handle_small_world_manifest_reply(
                    "显灵成功，凡人祈愿已平息。",
                    now + 1,
                    reply_to=None,
                    matched_family="small_world_manifest",
                )

            self.assertTrue(handled_manifest)
            record = state_module.get_storage_bag_records()[str(send_as_id)]
            self.assertEqual(3, record["items"]["清灵丹"])
            self.assertNotIn("修为", record["items"])
            self.assertEqual("", state_module.state["small_world_manifest_cost_text"])


if __name__ == "__main__":
    unittest.main()
