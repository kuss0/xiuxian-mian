import atexit
import asyncio
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

from model import action_guard
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


PRAYER_DATA_ERROR_PANEL = (
    "【获赦之人_xi的小世界】\n\n"
    "⛩️ 神庙: Lv.4【千户灵祠】\n"
    "👥 人口: 100000 人\n"
    "🏙️ 承载上限: 100000 人\n"
    "🙏 信仰: 99 / 100\n"
    "⚖️ 稳定: 100 / 100\n"
    "☁️ 待收香火: 14.03\n"
    "🏺 香火库存: 4\n"
    "🔥 预计产出: 116.40 香火/小时\n"
    "🛡️ 护界禁制: 未开启\n"
    "🧠 神识强度: 17878\n\n"
    "祈愿数据异常。\n\n"
    "下一阶【诸城香殿】消耗：香火x45000、灵石x80000、空间之核x1\n\n"
    "指令: .收割香火 | .神识淬炼 <数量> | .神迹 赈灾/布道 | .升级神庙 | .护界禁制 | .神庙"
)


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._passive_stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_passive_snapshot = dict(passive_inbox._observed_passive_events)
        self._action_guard_recent_snapshot = dict(action_guard._recent_closed_command_guards)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        passive_inbox._passive_stats = self._passive_stats_snapshot
        passive_inbox._observed_passive_events = self._observed_passive_snapshot
        action_guard._recent_closed_command_guards.clear()
        action_guard._recent_closed_command_guards.update(self._action_guard_recent_snapshot)
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

    async def test_parse_prayer_data_error_panel_marks_cd_only(self):
        panel = small_world._parse_small_world_panel(PRAYER_DATA_ERROR_PANEL)

        self.assertIsNotNone(panel)
        self.assertEqual("获赦之人_xi", panel["owner"])
        self.assertFalse(panel["has_prayer"])
        self.assertFalse(panel["has_wait"])
        self.assertTrue(panel["prayer_data_error"])
        self.assertEqual("诸城香殿", panel["next_temple_name"])

    async def test_latest_temple_panel_updates_snapshot_and_wait(self):
        send_as_id = 8659059188
        now = 9000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            LATEST_SMALL_WORLD_PANEL.replace("⚖️ 稳定: 50 / 100", "⚖️ 稳定: 90 / 100")
        )

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
            self.assertEqual(90, snapshot["stability"])
            self.assertEqual(116.40, snapshot["hourly_output"])
            self.assertEqual("未开启", snapshot["barrier_status"])
            self.assertEqual("8分钟59秒", snapshot["wait_text"])

    async def test_low_stability_wait_panel_without_preach_enabled_only_schedules_wait(self):
        send_as_id = 8659059198
        now = 9050.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(LATEST_SMALL_WORLD_PANEL)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            self.assertFalse(state_module.state["small_world_preach_enabled"])
            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world, "save_state"),
                patch.object(small_world.random, "uniform", return_value=60),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            relief_mock.assert_not_awaited()
            preach_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(
                now + (8 * 60 + 59) + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_wait_panel_with_low_faith_prefers_preach_before_relief(self):
        send_as_id = 8659059189
        now = 9100.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【获赦之人_ta的小世界】\n\n"
            "⛩️ 神庙: Lv.1【草创神龛】\n"
            "👥 人口: 99830 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 92 / 100\n"
            "⚖️ 稳定: 49 / 100\n"
            "☁️ 待收香火: 598.75\n"
            "🏺 香火库存: 230\n"
            "🔥 预计产出: 109.66 香火/小时\n"
            "🛡️ 护界禁制: 未开启\n"
            "🧠 神识强度: 81\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时59分钟21秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=True)) as preach_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_awaited_once_with(now, "信仰 92/100，布道维护")
            relief_mock.assert_not_awaited()

    async def test_wait_panel_with_large_population_deficit_sends_relief(self):
        send_as_id = 8659059190
        now = 9200.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 94000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 100 / 100\n"
            "⚖️ 稳定: 90 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时25分钟57秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock(return_value=True)) as relief_mock,
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            relief_mock.assert_awaited_once_with(now, "人口 94000 缺口 6000，优先赈灾")
            preach_mock.assert_not_awaited()

    async def test_wait_panel_with_low_faith_only_sends_preach(self):
        send_as_id = 8659059199
        now = 9300.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 5 / 100\n"
            "⚖️ 稳定: 100 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时25分钟57秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=True)) as preach_mock,
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_awaited_once_with(now, "信仰 5/100，布道维护")
            relief_mock.assert_not_awaited()

    async def test_wait_panel_with_faith_below_max_sends_preach(self):
        send_as_id = 8659059200
        now = 9320.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 99 / 100\n"
            "⚖️ 稳定: 100 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时25分钟57秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=True)) as preach_mock,
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_awaited_once_with(now, "信仰 99/100，布道维护")
            relief_mock.assert_not_awaited()

    async def test_wait_panel_with_minor_stability_deficit_waits_without_relief(self):
        send_as_id = 8659059201
        now = 9340.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 100 / 100\n"
            "⚖️ 稳定: 99 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时25分钟57秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock(return_value=True)) as relief_mock,
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            relief_mock.assert_not_awaited()
            preach_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(
                now + (5 * 3600 + 25 * 60 + 57) + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_maintenance_waits_when_next_disaster_wave_is_near(self):
        send_as_id = 8659059202
        now = 20000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 99 / 100\n"
            "⚖️ 稳定: 100 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时25分钟57秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            relief_mock.assert_not_awaited()
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_MAINTENANCE,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertEqual(now + 45 * 60 + 60, state_module.state["next_small_world_time"])
            self.assertIn("让位下一波灾害", state_module.state["small_world_last_error"])

    async def test_barrier_sends_near_disaster_wave_when_stock_is_high(self):
        send_as_id = 8659059301
        now = 30000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【WalterWA2000的小世界】\n\n"
            "⛩️ 神庙: Lv.4【城隍法域】\n"
            "🙏 信仰: 100 / 100\n"
            "🏺 香火库存: 140000\n"
            "🛡️ 护界禁制: 未开启\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            with (
                patch.object(small_world, "_send_barrier", new=AsyncMock(return_value=True)) as barrier_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            barrier_mock.assert_awaited_once()
            self.assertIn("成本约 9600", barrier_mock.await_args.args[1])

    async def test_barrier_does_not_send_below_stock_threshold(self):
        send_as_id = 8659059302
        now = 30000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【WalterWA2000的小世界】\n\n"
            "⛩️ 神庙: Lv.4【城隍法域】\n"
            "🏺 香火库存: 120000\n"
            "🛡️ 护界禁制: 未开启\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            with (
                patch.object(small_world, "_send_barrier", new=AsyncMock()) as barrier_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            barrier_mock.assert_not_awaited()
            self.assertGreater(state_module.state["next_small_world_time"], now)

    async def test_barrier_does_not_send_when_already_active(self):
        send_as_id = 8659059303
        now = 30000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【WalterWA2000的小世界】\n\n"
            "⛩️ 神庙: Lv.4【城隍法域】\n"
            "🏺 香火库存: 150000\n"
            "🛡️ 护界禁制: 剩余 6小时12分钟\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            with (
                patch.object(small_world, "_send_barrier", new=AsyncMock()) as barrier_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            barrier_mock.assert_not_awaited()

    async def test_barrier_uses_query_when_snapshot_is_stale_even_if_next_cycle_future(self):
        send_as_id = 8659059304
        now = 30000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            state_module.state["next_small_world_time"] = now + 6 * 3600
            state_module.state["small_world_panel_snapshot"] = {
                "temple_level": 4,
                "stock": 150000,
                "barrier_status": "未开启",
                "updated_at": now - small_world.SMALL_WORLD_BARRIER_PANEL_MAX_AGE_SEC - 1,
            }
            with (
                patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock,
                patch.object(small_world, "_send_barrier", new=AsyncMock()) as barrier_mock,
            ):
                await small_world._run_small_world_scheduler(now)

            query_mock.assert_awaited_once()
            self.assertIn("临灾校准", query_mock.await_args.args[1])
            barrier_mock.assert_not_awaited()

    async def test_barrier_skips_unknown_temple_level_cost(self):
        send_as_id = 8659059305
        now = 30000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【WalterWA2000的小世界】\n\n"
            "⛩️ 神庙: Lv.2【乡土神庙】\n"
            "🏺 香火库存: 150000\n"
            "🛡️ 护界禁制: 未开启\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = True
            state_module.state["small_world_last_disaster_wave_at"] = now - (2 * 3600 + 40 * 60)
            with (
                patch.object(small_world, "_send_barrier", new=AsyncMock()) as barrier_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            barrier_mock.assert_not_awaited()

    async def test_pending_maintenance_clears_answered_query_phase_before_waiting(self):
        send_as_id = 8659059203
        now = 21000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 100 / 100\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 9901
            state_module.state["small_world_god_cooldown_until"] = now + 3600
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertEqual(now + 3600 + 60, state_module.state["next_small_world_time"])

    async def test_prayer_panel_drops_stale_maintenance_before_manifest(self):
        send_as_id = 8659059204
        now = 22000.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 100 / 100\n\n"
            "🔥 凡人祈愿：丰收祭典\n"
            "⚡ 显灵消耗: 灵石x200\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 9902
            state_module.state["small_world_pending_god_action"] = "preach"
            state_module.state["small_world_pending_god_reason"] = "信仰 96/100，布道维护"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_MAINTENANCE
            with (
                patch.object(small_world, "_send_manifest", new=AsyncMock(return_value=True)) as manifest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            manifest_mock.assert_awaited_once_with(now)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual("", state_module.state["small_world_pending_god_action"])

    async def test_prayer_panel_manifests_without_harvest_when_incense_ready(self):
        send_as_id = 8659059205
        now = 22100.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【铁笔客的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 1608.92\n"
            "🏺 香火库存: 4\n\n"
            "🔥 凡人祈愿：丰收祭典\n"
            "⚡ 显灵消耗: 灵石x200\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 9903
            with (
                patch.object(small_world, "_send_harvest_before_manifest", new=AsyncMock(return_value=True)) as harvest_mock,
                patch.object(small_world, "_send_manifest", new=AsyncMock(return_value=True)) as manifest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_not_awaited()
            manifest_mock.assert_awaited_once_with(now)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual("灵石x200", state_module.state["small_world_manifest_cost_text"])

    async def test_prayer_data_error_panel_waits_cd_without_refresh_or_tools(self):
        send_as_id = 8659059206
        now = 22200.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(PRAYER_DATA_ERROR_PANEL)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = False
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 9904
            state_module.state["small_world_refresh_count"] = 4
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "_send_query", new=AsyncMock()) as query_mock,
                patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
                patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
                patch.object(small_world, "_send_manifest", new=AsyncMock()) as manifest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            query_mock.assert_not_awaited()
            harvest_mock.assert_not_awaited()
            refine_mock.assert_not_awaited()
            manifest_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual(0, state_module.state["small_world_refresh_count"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_MANIFEST_CD_SEC + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            self.assertTrue(state_module.state["small_world_panel_snapshot"]["prayer_data_error"])

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
            state_module.state["next_small_world_time"] = now + 12345
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

    async def test_harvest_does_not_require_refine_enabled(self):
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
                patch.object(small_world, "_send_harvest", new=AsyncMock(return_value=True)) as harvest_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            harvest_mock.assert_awaited_once_with(now)

    async def test_manifest_refresh_rechecks_every_minute_until_prayer(self):
        send_as_id = 8659059195
        now = 3050.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 4.92\n"
            "🏺 香火库存: 80703\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual(1, state_module.state["small_world_refresh_count"])
            self.assertEqual(now + 60, state_module.state["next_small_world_time"])

    async def test_manifest_refresh_round_pauses_five_minutes_then_continues(self):
        send_as_id = 8659059195
        now = 3055.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 100 / 100\n"
            "☁️ 待收香火: 4.92\n"
            "🏺 香火库存: 80703\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_refresh_count"] = small_world.SMALL_WORLD_MAX_REFRESH_ATTEMPTS
            with (
                patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            audit_mock.assert_awaited_once()
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual(1, state_module.state["small_world_refresh_count"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_REFRESH_ROUND_PAUSE_SEC,
                state_module.state["next_small_world_time"],
            )
            self.assertIn("5 分钟后继续刷新", state_module.state["small_world_last_error"])

    async def test_manifest_refresh_preempts_daily_preach_maintenance(self):
        send_as_id = 8659059196
        now = 3060.0
        state_module.ensure_identity_registered(send_as_id)
        panel = small_world._parse_small_world_panel(
            "【清源子的小世界】\n\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 100 / 100\n"
            "☁️ 待收香火: 0.92\n"
            "🏺 香火库存: 80703\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world._handle_panel_decision(now, panel)

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual("", state_module.state["small_world_pending_god_action"])
            self.assertEqual(now + 60, state_module.state["next_small_world_time"])

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

    async def test_send_manifest_does_not_enter_generic_retry_queue(self):
        send_as_id = 8659059299
        now = 3250.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7605, sent_at=now + 1))) as send_mock,
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_manifest(now)

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(
                small_world.CMD_SMALL_WORLD_MANIFEST,
                track=False,
                max_retry=0,
                priority="chain",
            )
            self.assertEqual("manifest_pending", state_module.state["small_world_phase"])
            self.assertEqual(7605, state_module.state["small_world_manifest_msg_id"])
            self.assertEqual(now + 1 + small_world.SMALL_WORLD_PENDING_TIMEOUT_SEC, state_module.state["next_small_world_time"])

    async def test_send_query_does_not_enter_generic_retry_resend(self):
        send_as_id = 8659059299
        now = 3260.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7607, sent_at=now + 1))) as send_mock,
                patch.object(small_world, "save_state"),
                patch.object(small_world, "console_log"),
            ):
                sent = await small_world._send_query(now, "周期自查")

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(
                small_world.CMD_SMALL_WORLD_QUERY,
                track=True,
                max_retry=0,
                priority="chain",
                source_module="小世界",
            )
            self.assertEqual("query_pending", state_module.state["small_world_phase"])
            self.assertEqual(7607, state_module.state["small_world_query_msg_id"])
            self.assertEqual(now + 1 + small_world.SMALL_WORLD_PENDING_TIMEOUT_SEC, state_module.state["next_small_world_time"])

    async def test_send_query_defers_when_same_command_guard_active(self):
        send_as_id = 8659059298
        now = 3270.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_phase"] = "idle"
            action_guard.note_sent(small_world.CMD_SMALL_WORLD_QUERY, send_as_id, 7608, sent_at=now - 30)
            action_guard.close_action("small_world_query", send_as_id=send_as_id, now=now - 20)

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_query(now, "收割后复查")

            self.assertTrue(sent)
            send_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual(
                now - 30 + action_guard.POST_CLOSE_REPEAT_GUARD_SEC,
                state_module.state["next_small_world_time"],
            )
            self.assertIn("延后至安全窗后复查", state_module.state["small_world_last_error"])

    async def test_manifest_timeout_closes_chain_without_resending(self):
        send_as_id = 8659059300
        now = 3350.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "manifest_pending"
            state_module.state["small_world_manifest_msg_id"] = 7606
            state_module.state["small_world_manifest_cost_text"] = "修为x500"
            state_module.state["next_small_world_time"] = now - 1

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
                patch.object(small_world, "save_state"),
            ):
                await small_world.run_small_world_scheduler(now)

            send_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_manifest_msg_id"])
            self.assertEqual("", state_module.state["small_world_manifest_cost_text"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_CYCLE_CD_SEC + small_world.SMALL_WORLD_JITTER_MIN_SEC,
                state_module.state["next_small_world_time"],
            )
            self.assertIn("manifest_pending 等待回复超时", state_module.state["small_world_last_error"])

    async def test_god_action_send_is_module_managed_without_runtime_retry(self):
        send_as_id = 8659059191
        now = 3360.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7610, sent_at=now + 1))) as send_mock,
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_small_world_preach(now, "信仰维护")

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(
                small_world.CMD_SMALL_WORLD_PREACH,
                track=True,
                max_retry=0,
                source_module="小世界",
            )
            self.assertEqual("preach_pending", state_module.state["small_world_phase"])
            self.assertEqual(7610, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual("preach", state_module.state["small_world_last_god_action"])
            self.assertEqual(now + 1, state_module.state["small_world_last_god_sent_at"])
            self.assertEqual(now + 1 + small_world.SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC, state_module.state["next_small_world_time"])

            allowed, reason = action_guard.before_send(small_world.CMD_SMALL_WORLD_PREACH, send_as_id=send_as_id, now=now + 2)
            self.assertFalse(allowed)
            self.assertIn("等待神迹回执", reason)

    async def test_recent_god_action_send_is_suppressed_even_if_reply_tracking_was_cleared(self):
        send_as_id = 8659059191
        now = 3600.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_last_god_action"] = "preach"
            state_module.state["small_world_last_god_sent_at"] = now - 32
            state_module.state["small_world_preach_reply_to_msg_id"] = 0
            state_module.state["small_world_preach_due_at"] = 0

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(small_world, "save_state") as save_mock,
            ):
                sent = await small_world._send_small_world_preach(now, "信仰维护")

            self.assertTrue(sent)
            send_mock.assert_not_awaited()
            save_mock.assert_called_once()
            self.assertEqual(now - 32 + small_world.SMALL_WORLD_GOD_RESEND_GUARD_SEC, state_module.state["next_small_world_time"])
            self.assertIn("跳过重复发送", state_module.state["small_world_last_error"])

            allowed, reason = action_guard.before_send(small_world.CMD_SMALL_WORLD_PREACH, send_as_id=send_as_id, now=now)
            self.assertFalse(allowed)
            self.assertIn("短窗", reason)

    async def test_god_action_send_timeout_keeps_unknown_pending_and_guarded(self):
        send_as_id = 8659059314
        now = 3650.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_small_world_preach(now, "信仰维护")

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(
                small_world.CMD_SMALL_WORLD_PREACH,
                track=True,
                max_retry=0,
                source_module="小世界",
            )
            audit_mock.assert_not_awaited()
            self.assertEqual("preach_pending", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual(now + small_world.SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC, state_module.state["small_world_preach_due_at"])
            self.assertEqual("preach", state_module.state["small_world_last_god_action"])
            self.assertEqual(now, state_module.state["small_world_last_god_sent_at"])
            self.assertIn("结果未知", state_module.state["small_world_last_error"])

            allowed, reason = action_guard.before_send(small_world.CMD_SMALL_WORLD_PREACH, send_as_id=send_as_id, now=now + 1)
            self.assertFalse(allowed)
            self.assertIn("发送结果未知", reason)

    async def test_god_action_unknown_pending_times_out_without_message_id(self):
        send_as_id = 8659059315
        now = 3660.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 0
            state_module.state["small_world_preach_due_at"] = now - 1
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                await small_world.run_small_world_scheduler(now)

            audit_mock.assert_awaited_once()
            self.assertIn("消息ID=未知", audit_mock.await_args.args[0])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["small_world_preach_due_at"])
            self.assertEqual("relief", state_module.state["small_world_pending_god_action"])
            self.assertEqual(now + 60, state_module.state["next_small_world_time"])

    async def test_concurrent_god_action_send_is_suppressed_by_optimistic_guard(self):
        send_as_id = 8659059191
        now = 3700.0
        state_module.ensure_identity_registered(send_as_id)

        async def slow_send(*_args, **_kwargs):
            await asyncio.sleep(0)
            return SimpleNamespace(id=7611, sent_at=now + 1)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(side_effect=slow_send)) as send_mock,
                patch.object(small_world, "save_state"),
            ):
                first, second = await asyncio.gather(
                    small_world._send_small_world_preach(now, "灾害: 邪神蛊惑，布道安抚"),
                    small_world._send_small_world_preach(now + 30, "灾害: 邪神蛊惑，布道安抚"),
                )

            self.assertTrue(first)
            self.assertTrue(second)
            send_mock.assert_awaited_once_with(
                small_world.CMD_SMALL_WORLD_PREACH,
                track=True,
                max_retry=0,
                source_module="小世界",
            )
            self.assertEqual("preach", state_module.state["small_world_last_god_action"])
            self.assertEqual(now + 1, state_module.state["small_world_last_god_sent_at"])
            self.assertEqual(now + 1 + small_world.SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC, state_module.state["next_small_world_time"])
            self.assertIn("跳过重复发送", state_module.state["small_world_last_error"])

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

    async def test_harvest_before_manifest_timeout_rechecks_panel_with_manifest_reason(self):
        send_as_id = 8659059298
        now = 3320.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_phase"] = "harvest_before_manifest_sent"
            state_module.state["small_world_harvest_msg_id"] = 7607
            state_module.state["next_small_world_time"] = now - 1

            with patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock:
                await small_world.run_small_world_scheduler(now)

            query_mock.assert_awaited_once_with(now, "显灵前收割后复查")
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_harvest_msg_id"])
            self.assertIn("显灵前收割香火", state_module.state["small_world_last_error"])

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
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "【神音浩荡】\n"
                    "你消耗 2000 点修为，在天穹之上显化法相，传颂大道！\n"
                    "凡人狂热膜拜，信仰提升至 96，稳定提升至 90！",
                    now,
                    reply_to=None,
                    matched_family="small_world_preach",
                )

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            self.assertEqual(96, state_module.state["small_world_faith_value"])
            self.assertEqual(90, state_module.state["small_world_panel_snapshot"]["stability"])
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertEqual("信仰 96/100，布道维护", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_GOD_FOLLOWUP_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_relief_reply_updates_population_faith_stability_and_followup(self):
        send_as_id = 8659059301
        now = 4100.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7701
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_panel_snapshot"] = {
                "population": 99830,
                "capacity": 100000,
                "faith": 92,
                "faith_max": 100,
                "stability": 49,
                "stability_max": 100,
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "【天降甘霖】\n"
                    "你消耗了 2000 灵石，化作无边甘霖滋润凡间！\n"
                    "凡人感念神恩，人口恢复了 1435 人，信仰提升至 100，稳定提升至 75！",
                    now,
                    reply_to=None,
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertEqual(100000, snapshot["population"])
            self.assertEqual(100, snapshot["faith"])
            self.assertEqual(75, snapshot["stability"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual("relief", state_module.state["small_world_pending_god_action"])
            self.assertEqual("稳定 75/100，赈灾维护", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_GOD_FOLLOWUP_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_god_reply_clears_only_god_retry_pending_tasks(self):
        send_as_id = 8659059305
        now = 4150.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 8801
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["pending_tasks"] = {
                8801: {"cmd": small_world.CMD_SMALL_WORLD_RELIEF, "sent_at": now - 5, "retry": 0},
                8802: {"cmd": small_world.CMD_SMALL_WORLD_PREACH, "sent_at": now - 4, "retry": 0},
                8803: {"cmd": small_world.CMD_SMALL_WORLD_QUERY, "sent_at": now - 3, "retry": 0},
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "【天降甘霖】\n"
                    "凡人感念神恩，人口恢复了 1435 人，信仰提升至 100，稳定提升至 75！",
                    now,
                    reply_to=None,
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            self.assertEqual({8803}, set(state_module.state["pending_tasks"].keys()))
            self.assertEqual(small_world.CMD_SMALL_WORLD_QUERY, state_module.state["pending_tasks"][8803]["cmd"])

    async def test_god_cooldown_reply_clears_pending_and_uses_real_wait(self):
        send_as_id = 8659059302
        now = 4200.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7702
            state_module.state["small_world_preach_due_at"] = now + 30
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "凡间方才承受神谕，需再等待 2小时14分钟9秒。",
                    now,
                    reply_to=None,
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertIn("神迹冷却中", state_module.state["small_world_last_error"])
            self.assertEqual(
                now + (2 * 3600 + 14 * 60 + 9) + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_god_cooldown_reply_preserves_pending_action(self):
        send_as_id = 8659059306
        now = 4250.0
        wait_sec = 2 * 3600 + 14 * 60 + 9
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7706
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["small_world_pending_god_at"] = now - 10
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "凡间方才承受神谕，需再等待 2小时14分钟9秒。",
                    now,
                    reply_to=None,
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual("relief", state_module.state["small_world_pending_god_action"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertEqual(now + wait_sec + small_world.CD_BUFFER_SEC, state_module.state["small_world_god_cooldown_until"])
            self.assertEqual(
                now + wait_sec + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_relief_resource_shortage_falls_back_to_preach_without_long_pause(self):
        send_as_id = 8659059312
        now = 4255.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7712
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["pending_tasks"] = {
                7712: {"cmd": small_world.CMD_SMALL_WORLD_RELIEF, "sent_at": now - 5, "retry": 0},
                7713: {"cmd": small_world.CMD_SMALL_WORLD_PREACH, "sent_at": now - 4, "retry": 0},
                7714: {"cmd": small_world.CMD_SMALL_WORLD_QUERY, "sent_at": now - 3, "retry": 0},
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=True)) as preach_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "国库空虚！赈灾需要 4000 灵石。",
                    now,
                    reply_to=SimpleNamespace(id=7712, raw_text=small_world.CMD_SMALL_WORLD_RELIEF),
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            preach_mock.assert_awaited_once()
            self.assertEqual(now, preach_mock.await_args.args[0])
            self.assertIn("赈灾", preach_mock.await_args.args[1])
            self.assertIn("布道", preach_mock.await_args.args[1])
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertIn("布道", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertEqual({7714}, set(state_module.state["pending_tasks"].keys()))
            self.assertEqual("赈灾没钱转布道", state_module.state["small_world_pending_god_reason"])
            self.assertNotEqual(
                now + small_world.SMALL_WORLD_LONG_PAUSE_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            audit_mock.assert_not_awaited()

    async def test_relief_resource_shortage_keeps_preach_short_retry_when_send_fails(self):
        send_as_id = 8659059314
        now = 4256.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7718
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["pending_tasks"] = {
                7718: {"cmd": small_world.CMD_SMALL_WORLD_RELIEF, "sent_at": now - 5, "retry": 0},
                7719: {"cmd": small_world.CMD_SMALL_WORLD_QUERY, "sent_at": now - 3, "retry": 0},
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=False)) as preach_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "国库空虚！赈灾需要 4000 灵石。",
                    now,
                    reply_to=SimpleNamespace(id=7718, raw_text=small_world.CMD_SMALL_WORLD_RELIEF),
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            preach_mock.assert_awaited_once_with(now, "赈灾没钱转布道")
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertEqual("赈灾没钱转布道", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertNotIn("资源不足", state_module.state["small_world_last_error"])
            self.assertNotEqual(
                now + small_world.SMALL_WORLD_LONG_PAUSE_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            self.assertEqual({7719}, set(state_module.state["pending_tasks"].keys()))
            audit_mock.assert_not_awaited()

    async def test_preach_resource_shortage_keeps_long_pause_behavior(self):
        send_as_id = 8659059313
        now = 4257.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7715
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_pending_god_action"] = "preach"
            state_module.state["small_world_pending_god_reason"] = "灾害: 邪神蛊惑，布道安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["pending_tasks"] = {
                7715: {"cmd": small_world.CMD_SMALL_WORLD_PREACH, "sent_at": now - 5, "retry": 0},
                7716: {"cmd": small_world.CMD_SMALL_WORLD_RELIEF, "sent_at": now - 4, "retry": 0},
                7717: {"cmd": small_world.CMD_SMALL_WORLD_QUERY, "sent_at": now - 3, "retry": 0},
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "神迹布道需要 4000 灵石，当前灵石不足。",
                    now,
                    reply_to=SimpleNamespace(id=7715, raw_text=small_world.CMD_SMALL_WORLD_PREACH),
                    matched_family="small_world_preach",
                )

            self.assertTrue(handled)
            preach_mock.assert_not_awaited()
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual("", state_module.state["small_world_pending_god_action"])
            self.assertEqual("", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(0, state_module.state["small_world_pending_god_priority"])
            self.assertEqual({7717}, set(state_module.state["pending_tasks"].keys()))
            self.assertIn("资源不足", state_module.state["small_world_last_error"])
            self.assertIn("灵石不足", state_module.state["small_world_last_error"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_LONG_PAUSE_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            audit_mock.assert_awaited_once()
            self.assertIn("小世界神迹布道资源不足", audit_mock.await_args.args[0])

    async def test_god_success_clears_pending_when_snapshot_is_full(self):
        send_as_id = 8659059307
        now = 4260.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7707
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_pending_god_action"] = "preach"
            state_module.state["small_world_pending_god_reason"] = "信仰 95/100，布道维护"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_MAINTENANCE
            state_module.state["small_world_panel_snapshot"] = {
                "faith": 95,
                "faith_max": 100,
                "stability": 100,
                "stability_max": 100,
                "population": 100000,
                "capacity": 100000,
            }
            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    "【神音浩荡】\n信仰提升至 100，稳定提升至 100！",
                    now,
                    reply_to=None,
                    matched_family="small_world_preach",
                )

            self.assertTrue(handled)
            self.assertEqual("", state_module.state["small_world_pending_god_action"])
            self.assertEqual(0, state_module.state["small_world_pending_god_priority"])
            self.assertEqual(now + small_world.SMALL_WORLD_GOD_FOLLOWUP_SEC, state_module.state["small_world_god_cooldown_until"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_GOD_FOLLOWUP_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_scheduler_sends_due_pending_god_action_before_query_chain(self):
        send_as_id = 8659059308
        now = 4270.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["next_small_world_time"] = now - 1

            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock(return_value=True)) as relief_mock,
                patch.object(small_world, "_send_query", new=AsyncMock()) as query_mock,
            ):
                await small_world.run_small_world_scheduler(now)

            relief_mock.assert_awaited_once_with(now, "灾害: 地脉翻身，赈灾安抚")
            query_mock.assert_not_awaited()

    async def test_scheduler_sends_next_refresh_round_after_five_minute_pause(self):
        send_as_id = 8659059316
        now = 4275.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_phase"] = "refresh_wait"
            state_module.state["small_world_refresh_count"] = 1
            state_module.state["next_small_world_time"] = now - 1

            with patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock:
                await small_world.run_small_world_scheduler(now)

            query_mock.assert_awaited_once_with(
                now,
                f"祈愿刷新 1/{small_world.SMALL_WORLD_MAX_REFRESH_ATTEMPTS}",
                refresh_attempt=1,
            )

    async def test_due_disaster_god_action_preempts_chain_pending_phase(self):
        send_as_id = 8659059311
        now = 4280.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = 8808
            state_module.state["small_world_pending_god_action"] = "relief"
            state_module.state["small_world_pending_god_reason"] = "灾害: 地脉翻身，赈灾安抚"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER
            state_module.state["next_small_world_time"] = now + 600

            with patch.object(small_world, "_send_small_world_relief", new=AsyncMock(return_value=True)) as relief_mock:
                await small_world.run_small_world_scheduler(now)

            relief_mock.assert_awaited_once_with(now, "灾害: 地脉翻身，赈灾安抚")
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])

    async def test_god_pending_blocks_scheduler_until_reply_or_timeout(self):
        send_as_id = 8659059303
        now = 4300.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 7703
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["next_small_world_time"] = now - 1

            with (
                patch.object(small_world, "_send_query", new=AsyncMock()) as query_mock,
                patch.object(small_world, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                await small_world.run_small_world_scheduler(now)

            query_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            self.assertEqual("preach_pending", state_module.state["small_world_phase"])
            self.assertEqual(7703, state_module.state["small_world_preach_reply_to_msg_id"])

    async def test_disaster_pending_overrides_maintenance_pending(self):
        send_as_id = 8659059309
        now = 4320.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wxjerry", label="wxjerry")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_pending_god_action"] = "preach"
            state_module.state["small_world_pending_god_reason"] = "信仰 99/100，布道维护"
            state_module.state["small_world_pending_god_priority"] = small_world.SMALL_WORLD_GOD_PRIORITY_MAINTENANCE
            state_module.state["small_world_pending_god_at"] = now - 20
            state_module.state["small_world_god_cooldown_until"] = now + 600

            with (
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_disaster_broadcast(
                    "⚡ 【小世界·天降浩劫】 ⚡\n"
                    "道友 @wxjerry 的小世界遭遇 【地脉翻身】！\n"
                    "小世界地壳变动，大地震导致神庙倒塌，稳定动摇。\n"
                    "❌ 惨重代价: 稳定 -16 点\n"
                    "请速速查看 .小世界 并安抚信徒！",
                    now,
                    event=None,
                )

            self.assertTrue(handled)
            relief_mock.assert_not_awaited()
            self.assertEqual("relief", state_module.state["small_world_pending_god_action"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertIn("地脉翻身", state_module.state["small_world_pending_god_reason"])
            self.assertEqual(now + 600 + 60, state_module.state["next_small_world_time"])

    async def test_evil_god_incense_loss_queues_disaster_preach(self):
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
                patch.object(small_world, "_send_small_world_preach", new=AsyncMock(return_value=True)) as preach_mock,
                patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
                patch.object(small_world, "save_state"),
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
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])
            self.assertEqual(
                small_world.SMALL_WORLD_GOD_PRIORITY_DISASTER,
                state_module.state["small_world_pending_god_priority"],
            )
            self.assertIn("邪神蛊惑", state_module.state["small_world_pending_god_reason"])
            preach_mock.assert_awaited_once_with(now, "灾害: 邪神蛊惑，布道安抚")
            relief_mock.assert_not_awaited()

    async def test_unmapped_incense_loss_broadcast_schedules_short_calibration(self):
        send_as_id = 8659059310
        now = 5050.0
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
                    "道友 @wxjerry 的小世界遭遇 【香火失窃】！\n"
                    "一阵怪风卷过供台，窃取了你的香火！\n"
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

    async def test_passive_small_world_prayer_panel_updates_snapshot_without_manifest(self):
        send_as_id = 8659059397
        now = 7050.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="walterwa2000", label="wa2000", daohao="清源子")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955049)
        text = (
            "【清源子的小世界】\n\n"
            "⛩️ 神庙: Lv.1【草创神龛】\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 65 / 100\n"
            "☁️ 待收香火: 4754.97\n"
            "🏺 香火库存: 0\n"
            "🔥 凡人祈愿：瘟疫\n"
            "📝 一场恶疾在凡人城池中蔓延，凡人焚香祷告，求神仙救命。\n"
            "⚡ 显灵消耗: 清灵丹x2\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
            patch.object(small_world, "_send_manifest", new=AsyncMock()) as manifest_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        manifest_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("", state_module.state["small_world_manifest_cost_text"])
            self.assertEqual("清灵丹x2", state_module.state["small_world_panel_snapshot"]["manifest_cost"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_CYCLE_CD_SEC + small_world.SMALL_WORLD_JITTER_MIN_SEC,
                state_module.state["next_small_world_time"],
            )
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["modules"]["small_world"])

    async def test_passive_small_world_script_query_reply_does_not_duplicate_manifest(self):
        send_as_id = 8659059398
        now = 7060.0
        state_module.ensure_identity_registered(send_as_id)
        event = SimpleNamespace(chat_id=-1001680975844, id=8955050)
        text = (
            "【清源子的小世界】\n\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 65 / 100\n"
            "🔥 凡人祈愿：瘟疫\n"
            "⚡ 显灵消耗: 清灵丹x2\n"
            "请使用 .显灵 响应祈愿，或忽略之。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_query_msg_id"] = 8804
            state_module.state["my_msg_ids"] = {8804: now - 1}

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "_send_manifest", new=AsyncMock()) as manifest_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "small_world_query",
                    "reply_to_msg_id": 8804,
                    "root_msg_id": 8804,
                },
                event=event,
                event_type="message",
            )

        self.assertFalse(handled)
        manifest_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("", state_module.state["small_world_manifest_cost_text"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skip_reasons"]["no_change"])

    async def test_passive_small_world_active_query_panel_clears_pending_without_family(self):
        send_as_id = 8659059408
        now = 7065.0
        query_msg_id = 8809
        state_module.ensure_identity_registered(send_as_id)
        event = SimpleNamespace(chat_id=-1001680975844, id=8955058)
        text = (
            "【清源子的小世界】\n\n"
            "⛩️ 神庙: Lv.2【乡土神庙】\n"
            "👥 人口: 120696 人\n"
            "🏙️ 承载上限: 140000 人\n"
            "🙏 信仰: 89 / 100\n"
            "⚖️ 稳定: 100 / 100\n"
            "☁️ 待收香火: 1204.65\n"
            "🏺 香火库存: 2193\n"
            "🔥 预计产出: 185.30 香火/小时\n"
            "🛡️ 护界禁制: 未开启\n"
            "🧠 神识强度: 0\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_barrier_enabled"] = False
            state_module.state["small_world_phase"] = "query_pending"
            state_module.state["small_world_query_msg_id"] = query_msg_id
            state_module.state["small_world_refresh_count"] = 1
            state_module.state["next_small_world_time"] = now + small_world.SMALL_WORLD_PENDING_TIMEOUT_SEC

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
            patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
            patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "send_as_id": send_as_id,
                    "reply_to_msg_id": query_msg_id,
                    "root_msg_id": query_msg_id,
                },
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        harvest_mock.assert_not_awaited()
        refine_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("refresh_wait", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_query_msg_id"])
            self.assertEqual(2, state_module.state["small_world_refresh_count"])
            self.assertEqual(now + small_world.SMALL_WORLD_REFRESH_MIN_SEC, state_module.state["next_small_world_time"])
            self.assertEqual(2193, state_module.state["small_world_incense_stock"])

    async def test_passive_small_world_low_stability_updates_wait_without_relief(self):
        send_as_id = 8659059399
        now = 7070.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="walterwa2000", label="wa2000", daohao="清源子")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955051)
        text = (
            "【清源子的小世界】\n\n"
            "👥 人口: 99830 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 96 / 100\n"
            "⚖️ 稳定: 49 / 100\n"
            "☁️ 待收香火: 598.75\n"
            "🏺 香火库存: 230\n\n"
            "暂无祈愿，凡间风调雨顺。\n"
            "(下一次祈愿感应需等待: 5小时59分钟21秒)"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
            patch.object(small_world, "_send_small_world_relief", new=AsyncMock()) as relief_mock,
            patch.object(small_world, "_send_small_world_preach", new=AsyncMock()) as preach_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        relief_mock.assert_not_awaited()
        preach_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual(99830, state_module.state["small_world_panel_snapshot"]["population"])
            self.assertEqual(
                now
                + (5 * 3600 + 59 * 60 + 21)
                + small_world.CD_BUFFER_SEC
                + small_world.SMALL_WORLD_JITTER_MIN_SEC,
                state_module.state["next_small_world_time"],
            )
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["modules"]["small_world"])

    async def test_passive_small_world_prayer_data_error_waits_cd(self):
        send_as_id = 8659059401
        now = 7075.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="walterwa2000", label="wa2000", daohao="清源子")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955053)
        text = PRAYER_DATA_ERROR_PANEL.replace("获赦之人_xi", "清源子")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_refresh_enabled"] = True
            state_module.state["small_world_refresh_count"] = 3
            state_module.state["next_small_world_time"] = now - 1

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", return_value=60),
            patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
            patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        harvest_mock.assert_not_awaited()
        refine_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_refresh_count"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_MANIFEST_CD_SEC + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )
            self.assertTrue(state_module.state["small_world_panel_snapshot"]["prayer_data_error"])

    async def test_passive_small_world_panel_does_not_start_tool_chain(self):
        send_as_id = 8659059400
        now = 7080.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="walterwa2000", label="wa2000", daohao="清源子")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955052)
        text = (
            "【清源子的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 99 / 100\n"
            "⚖️ 稳定: 100 / 100\n"
            "☁️ 待收香火: 1608.92\n"
            "🏺 香火库存: 1038\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["next_small_world_time"] = now + 12345

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
            patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
            patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        harvest_mock.assert_not_awaited()
        refine_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(now + 12345, state_module.state["next_small_world_time"])

    async def test_passive_small_world_harvest_reply_updates_stock_without_followup(self):
        send_as_id = 8659059402
        now = 7085.0
        state_module.ensure_identity_registered(send_as_id)
        event = SimpleNamespace(chat_id=-1001680975844, id=8955063)
        text = "你大手一挥，将凡间供奉的 3855 点香火尽数收入紫府。\n当前香火库存: 20326"

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = False
            state_module.state["small_world_phase"] = "harvest_sent"
            state_module.state["small_world_harvest_msg_id"] = 8808
            state_module.state["small_world_pending_incense"] = 3855
            state_module.state["small_world_incense_stock"] = 16471
            state_module.state["next_small_world_time"] = now + 99

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "_send_query", new=AsyncMock()) as query_mock,
            patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "small_world_harvest",
                    "reply_to_msg_id": 8808,
                    "root_msg_id": 8808,
                },
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        query_mock.assert_not_awaited()
        refine_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_harvest_msg_id"])
            self.assertEqual(0, state_module.state["small_world_pending_incense"])
            self.assertEqual(20326, state_module.state["small_world_incense_stock"])
            self.assertEqual("", state_module.state["small_world_last_error"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["modules"]["small_world"])

    async def test_passive_small_world_no_wait_due_panel_schedules_next_cycle(self):
        send_as_id = 8659059401
        now = 7090.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="walterwa2000", label="wa2000", daohao="清源子")
        event = SimpleNamespace(chat_id=-1001680975844, id=8955053)
        text = (
            "【清源子的小世界】\n\n"
            "👥 人口: 100000 人\n"
            "🏙️ 承载上限: 100000 人\n"
            "🙏 信仰: 99 / 100\n"
            "⚖️ 稳定: 100 / 100\n"
            "☁️ 待收香火: 1.0\n"
            "🏺 香火库存: 3\n\n"
            "暂无祈愿，凡间风调雨顺。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["next_small_world_time"] = now - 1

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
            patch.object(small_world, "save_state"),
            patch.object(small_world.random, "uniform", side_effect=lambda min_sec, max_sec: min_sec),
            patch.object(small_world, "_send_harvest", new=AsyncMock()) as harvest_mock,
            patch.object(small_world, "_send_refine", new=AsyncMock()) as refine_mock,
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context=None,
                event=event,
                event_type="message",
            )

        self.assertTrue(handled)
        harvest_mock.assert_not_awaited()
        refine_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(
                now + small_world.SMALL_WORLD_CYCLE_CD_SEC + small_world.SMALL_WORLD_JITTER_MIN_SEC,
                state_module.state["next_small_world_time"],
            )

    async def test_manifest_expired_prayer_uses_real_wait_and_applies_delta(self):
        send_as_id = 8659059304
        now = 7100.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "manifest_pending"
            state_module.state["small_world_manifest_msg_id"] = 7804
            state_module.state["small_world_panel_snapshot"] = {
                "faith": 92,
                "faith_max": 100,
                "stability": 49,
                "stability_max": 100,
                "population": 99830,
                "capacity": 100000,
            }

            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_manifest_reply(
                    "这道凡人祈愿已经拖延超过 24 小时，天机已散，无法再显灵。\n"
                    "(信仰 -5, 稳定 -8, 人口 -200)\n"
                    "下一次凡人祈愿感应需等待 360 分钟。",
                    now,
                    reply_to=None,
                    matched_family="small_world_manifest",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_manifest_msg_id"])
            self.assertIn("天机已散", state_module.state["small_world_last_error"])
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertEqual(87, snapshot["faith"])
            self.assertEqual(41, snapshot["stability"])
            self.assertEqual(99630, snapshot["population"])
            self.assertFalse(snapshot.get("has_prayer"))
            self.assertEqual("", snapshot.get("prayer_name"))
            self.assertEqual("", snapshot.get("manifest_cost"))
            self.assertEqual(
                now + 360 * 60 + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_manifest_expired_prayer_with_new_prayer_waits_guard_then_retries(self):
        send_as_id = 8659059312
        now = 7150.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "manifest_pending"
            state_module.state["small_world_manifest_msg_id"] = 7806
            state_module.state["small_world_panel_snapshot"] = {
                "faith": 91,
                "faith_max": 100,
                "stability": 100,
                "stability_max": 100,
                "population": 53365,
                "capacity": 100000,
                "has_prayer": True,
                "prayer_name": "瘟疫",
                "manifest_cost": "清灵丹x2",
            }

            with patch.object(small_world, "save_state"):
                handled = await small_world.handle_small_world_manifest_reply(
                    "这道凡人祈愿已经拖延超过 72 小时，天机已散，无法再显灵。\n"
                    "(信仰 -10, 稳定 -8, 人口 -500)\n"
                    "旧愿已散，新的凡人祈愿已经传来：\n\n"
                    "🔥 凡人祈愿：大旱\n"
                    "📝 凡间遭遇百年大旱，赤地千里，无数生灵跪地祈雨。\n"
                    "⚡ 显灵消耗: 灵石x500\n"
                    "请再次使用 .显灵 响应新的祈愿。",
                    now,
                    reply_to=None,
                    matched_family="small_world_manifest",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertTrue(snapshot.get("has_prayer"))
            self.assertEqual("大旱", snapshot.get("prayer_name"))
            self.assertEqual("灵石x500", snapshot.get("manifest_cost"))
            self.assertEqual(now + small_world.SMALL_WORLD_SAME_COMMAND_GUARD_SEC, state_module.state["next_small_world_time"])
            self.assertIn("新的凡人祈愿", state_module.state["small_world_last_error"])

    async def test_manifest_send_timeout_does_not_overwrite_reply_handled_state(self):
        send_as_id = 8659059313
        now = 7160.0
        state_module.ensure_identity_registered(send_as_id)

        async def fake_send(*_args, **_kwargs):
            await small_world.handle_small_world_manifest_reply(
                "✅ 显灵成功！\n(信仰 +10, 稳定 +5, 人口 +500)\n下一次凡人祈愿感应需等待 360 分钟。",
                now + 1,
                reply_to=None,
                matched_family="small_world_manifest",
            )
            return None

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_panel_snapshot"] = {
                "has_prayer": True,
                "prayer_name": "大旱",
                "manifest_cost": "灵石x500",
                "faith": 90,
                "stability": 95,
                "population": 1000,
            }

            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(side_effect=fake_send)),
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_manifest(now)

            self.assertTrue(sent)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual("", state_module.state["small_world_last_error"])
            self.assertFalse(state_module.state["small_world_panel_snapshot"].get("has_prayer"))
            self.assertEqual(
                now + 1 + 360 * 60 + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

    async def test_manifest_send_timeout_unknown_lock_is_released_by_late_reply(self):
        send_as_id = 8659059317
        now = 7180.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            with (
                patch.object(small_world, "send_game_command", new=AsyncMock(return_value=None)),
                patch.object(small_world, "save_state"),
            ):
                sent = await small_world._send_manifest(now)

            self.assertTrue(sent)
            self.assertEqual("manifest_pending", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_manifest_msg_id"])
            self.assertIn("结果未知", state_module.state["small_world_last_error"])
            allowed, reason = action_guard.before_send(small_world.CMD_SMALL_WORLD_MANIFEST, send_as_id=send_as_id, now=now + 1)
            self.assertFalse(allowed)
            self.assertIn("发送结果未知", reason)

            with patch.object(small_world, "save_state"):
                handled = await small_world.handle_small_world_manifest_reply(
                    "这道凡人祈愿已经拖延超过 72 小时，天机已散，无法再显灵。\n"
                    "(信仰 -10, 稳定 -8, 人口 -500)\n"
                    "旧愿已散，新的凡人祈愿已经传来：\n\n"
                    "🔥 凡人祈愿：大旱\n"
                    "⚡ 显灵消耗: 灵石x500\n"
                    "请再次使用 .显灵 响应新的祈愿。",
                    now + 2,
                    reply_to=SimpleNamespace(id=0, raw_text=small_world.CMD_SMALL_WORLD_MANIFEST),
                    matched_family=None,
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(now + 2 + small_world.SMALL_WORLD_SAME_COMMAND_GUARD_SEC, state_module.state["next_small_world_time"])
            allowed, reason = action_guard.before_send(
                small_world.CMD_SMALL_WORLD_MANIFEST,
                send_as_id=send_as_id,
                now=now + 2 + small_world.SMALL_WORLD_SAME_COMMAND_GUARD_SEC + 1,
            )
            self.assertTrue(allowed, reason)

    async def test_manifest_failure_clears_cached_prayer_to_avoid_stale_retry_loop(self):
        send_as_id = 8659059310
        now = 7200.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_phase"] = "manifest_pending"
            state_module.state["small_world_manifest_msg_id"] = 7805
            state_module.state["small_world_panel_snapshot"] = {
                "has_prayer": True,
                "prayer_name": "大旱",
                "manifest_cost": "灵石x500",
                "updated_at": now - 30,
            }

            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_manifest_reply(
                    "显灵失败。\n(信仰 -5, 稳定 -8, 人口 -200)",
                    now,
                    reply_to=None,
                    matched_family="small_world_manifest",
                )

            self.assertTrue(handled)
            self.assertIn("显灵失败", state_module.state["small_world_last_error"])
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertFalse(snapshot.get("has_prayer"))
            self.assertEqual("", snapshot.get("prayer_name"))
            self.assertEqual("", snapshot.get("manifest_cost"))

    async def test_scheduler_manifests_again_when_new_panel_snapshot_has_ready_prayer(self):
        send_as_id = 8659059311
        now = 7300.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_manifest_enabled"] = True
            state_module.state["small_world_harvest_enabled"] = False
            state_module.state["small_world_refine_enabled"] = False
            state_module.state["small_world_phase"] = "idle"
            state_module.state["small_world_last_error"] = "显灵失败，停止本轮"
            state_module.state["next_small_world_time"] = now + 6 * 3600
            state_module.state["small_world_panel_snapshot"] = {
                "has_prayer": True,
                "prayer_name": "大旱",
                "manifest_cost": "灵石x500",
                "has_wait": False,
                "updated_at": now - 30,
            }

            with (
                patch.object(small_world, "_send_manifest", new=AsyncMock(return_value=True)) as manifest_mock,
                patch.object(small_world, "save_state"),
            ):
                await small_world.run_small_world_scheduler(now)

            manifest_mock.assert_awaited_once_with(now)
            self.assertEqual("灵石x500", state_module.state["small_world_manifest_cost_text"])

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
            self.assertEqual(
                now + 1 + small_world.SMALL_WORLD_MANIFEST_CD_SEC + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )

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
            self.assertEqual(
                now + 1 + small_world.SMALL_WORLD_MANIFEST_CD_SEC + small_world.CD_BUFFER_SEC + 60,
                state_module.state["next_small_world_time"],
            )


if __name__ == "__main__":
    unittest.main()
