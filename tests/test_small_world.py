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
                "ADMIN_ID=0",
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
from model.features import small_world


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


if __name__ == "__main__":
    unittest.main()
