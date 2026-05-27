import atexit
import copy
import sys
import unittest
from pathlib import Path
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
from model.features import small_world, storage_bag


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class SmallWorldTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
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

    async def test_incense_theft_broadcast_backs_off_chain(self):
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
                patch.object(small_world.random, "uniform", return_value=600),
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
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_refine_msg_id"])
            self.assertEqual(5, state_module.state["small_world_incense_stock"])
            self.assertEqual(now + 600, state_module.state["next_small_world_time"])
            self.assertIn("库存香火失窃 3 点", state_module.state["small_world_last_error"])
            audit_mock.assert_awaited_once()

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


if __name__ == "__main__":
    unittest.main()
