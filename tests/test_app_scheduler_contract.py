import copy
import asyncio
import sys
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import module_manifest
from model import state as state_module


IMPORTANT_RUNTIME_SCHEDULER_COVERAGE = {
    "run_pet_scheduler": {"pet", "pet_warm", "pet_trial", "pet_formation"},
    "run_ranch_scheduler": {"ranch"},
    "run_wild_training_scheduler": {"wild_training"},
    "run_formation_scheduler": {"formation"},
    "run_tianti_scheduler": {"tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"},
    "run_concubine_scheduler": {"concubine"},
    "run_hehuan_scheduler": {"hehuan", "hehuan_dual"},
    "run_tianxing_scheduler": {"tianxing"},
    "run_yinluo_scheduler": {"yinluo"},
    "run_mulan_scheduler": {"mulan"},
    "run_small_world_scheduler": {
        "small_world_preach",
        "small_world_relief",
        "small_world_query",
        "small_world_manifest",
        "small_world_harvest",
        "small_world_refine",
    },
    "run_explore_rift_scheduler": {"explore_rift"},
    "run_wendao_scheduler": {"wendao"},
    "run_fishing_scheduler": {"fishing"},
    "run_taiyi_scheduler": {"taiyi"},
    "miniapp_daily": {"miniapp", "trial"},
    "divination": {"divination"},
    "world_boss": {"world_boss"},
}
HELPER_SCHEDULERS = {
    "delayed_actions",
    "storage_bag_api_keepalive",
    "tiandao_judgement",
    "tianji_quiz",
    "huanglong_conscription",
    "luoyun_cd_reminder",
    "run_second_soul_bootstrap_check",
    "run_taiyi_bootstrap_check",
}


def _index(order, name):
    return order.index(name)


def _behavior_coverage_tokens(spec):
    return {
        spec.module,
        *tuple(spec.replay_modules or ()),
        *tuple(spec.reply_families or ()),
        *tuple(spec.workflow_names or ()),
    }


class AppSchedulerContractTests(unittest.TestCase):
    def test_identity_username_refresh_preserves_previous_alias(self):
        identity_id = 990099112
        event = SimpleNamespace(
            sender_id=identity_id,
            sender=SimpleNamespace(username="jfdffdddd1"),
        )

        with patch.object(app, "get_send_as_profile", return_value={
            "username": "jfdffdddd",
            "username_aliases": [],
        }), patch.object(app, "update_send_as_profile") as update_mock, \
                patch.object(app, "mark_dirty"), patch.object(app, "save_state", return_value=True) as save_mock, \
                patch.object(app, "console_log"):
            changed = app._refresh_identity_username_from_event(event, identity_id)

        self.assertTrue(changed)
        update_mock.assert_called_once_with(identity_id, username="jfdffdddd1")
        save_mock.assert_called_once()

    def test_identity_username_refresh_ignores_external_sender(self):
        event = SimpleNamespace(
            sender_id=999999999,
            sender=SimpleNamespace(username="jfdffdddd1"),
        )
        with patch.object(app, "save_state") as save_mock:
            changed = app._refresh_identity_username_from_event(event)
        self.assertFalse(changed)
        save_mock.assert_not_called()

    def test_bot_health_accepts_observed_command_reply_message_object(self):
        command_msg_id = 13567
        app._observed_game_commands.clear()
        try:
            app._observe_game_command_for_bot_evidence(
                123456,
                ".点卯",
                command_msg_id,
                now=1000.0,
            )

            self.assertTrue(
                app._is_bot_health_reply_evidence(
                    "点卯成功！你获得了 100 点宗门贡献。",
                    SimpleNamespace(id=command_msg_id),
                    {},
                    now=1001.0,
                )
            )
        finally:
            app._observed_game_commands.clear()

    def test_unthreaded_tianxing_reply_binds_unique_recent_route_command(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        app._observed_game_commands.clear()
        identity_id = 990099113
        reply_text = (
            "你拨动司命盘，为 【斗法】 推下一段命数。\n"
            "此推命将在 8 小时 内生效；若你先去做别路之事，便会平添一层逆命劫。"
        )
        try:
            state_module.ensure_identity_registered(identity_id)
            app._observe_game_command_for_bot_evidence(
                identity_id,
                ".推命 斗法",
                74444,
                now=1000.0,
            )
            event = SimpleNamespace(
                id=74446,
                chat_id=-1001680975844,
                reply_to=None,
            )
            with patch.object(
                app,
                "get_reply_context",
                return_value={
                    "send_as_id": identity_id,
                    "family": "tianxing_predict",
                    "reply_to_msg_id": 74444,
                    "root_msg_id": 74444,
                },
            ):
                inferred = app._infer_unthreaded_tianxing_reply(event, reply_text, 1001.0)

            self.assertIsNotNone(inferred)
            reply_to, context = inferred
            self.assertEqual(74444, reply_to.id)
            self.assertEqual(identity_id, context["send_as_id"])
            self.assertTrue(context["inferred_unthreaded"])
        finally:
            app._observed_game_commands.clear()
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_unthreaded_tianxing_reply_refuses_ambiguous_same_route_commands(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        app._observed_game_commands.clear()
        try:
            for identity_id, msg_id in ((990099114, 74450), (990099115, 74451)):
                state_module.ensure_identity_registered(identity_id)
                app._observe_game_command_for_bot_evidence(
                    identity_id,
                    ".推命 斗法",
                    msg_id,
                    now=1000.0,
                )
            reply_text = (
                "你拨动司命盘，为 【斗法】 推下一段命数。\n"
                "此推命将在 8 小时 内生效。"
            )
            event = SimpleNamespace(id=74452, chat_id=-1001680975844, reply_to=None)
            self.assertEqual(2, len(app._candidate_unthreaded_tianxing_commands(reply_text, 1001.0, event_id=74452)))
            self.assertIsNone(app._infer_unthreaded_tianxing_reply(event, reply_text, 1001.0))
        finally:
            app._observed_game_commands.clear()
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_phaseful_identity_schedulers_are_before_ordinary_schedulers(self):
        contract = app.get_identity_scheduler_order_contract()

        self.assertEqual(
            ("run_deep_retreat_scheduler", "run_yuanying_scheduler"),
            contract["phaseful"],
        )
        self.assertNotIn("run_deep_retreat_scheduler", contract["ordinary"])
        self.assertNotIn("run_yuanying_scheduler", contract["ordinary"])

    def test_ordinary_identity_scheduler_keeps_current_key_module_order(self):
        ordinary = app.get_identity_scheduler_order_contract()["ordinary"]

        self.assertLess(_index(ordinary, "run_tianxing_scheduler"), _index(ordinary, "run_wild_training_scheduler"))
        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_small_world_scheduler"))
        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_mulan_scheduler"))
        self.assertLess(_index(ordinary, "run_mulan_scheduler"), _index(ordinary, "run_small_world_scheduler"))
        self.assertLess(_index(ordinary, "run_small_world_scheduler"), _index(ordinary, "run_explore_rift_scheduler"))
        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_wendao_scheduler"))
        self.assertLess(_index(ordinary, "run_wendao_scheduler"), _index(ordinary, "run_checkin_scheduler"))
        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_checkin_scheduler"))
        self.assertLess(_index(ordinary, "run_tower_scheduler"), _index(ordinary, "run_second_soul_bootstrap_check"))
        self.assertEqual(
            (
                "run_second_soul_bootstrap_check",
                "run_second_soul_scheduler",
                "run_taiyi_bootstrap_check",
                "run_taiyi_scheduler",
            ),
            ordinary[-4:],
        )

    def test_global_scheduler_order_starts_with_current_runtime_sequence(self):
        self.assertEqual(
            (
                "channel_send_as_health",
                "delayed_actions",
                "guanxing_monitor",
                "guanxing",
                "storage_bag_api_keepalive",
                "miniapp_daily",
                "storage_bag_transfer",
                "divination",
                "world_boss",
                "tiandao_judgement",
                "tianji_quiz",
                "huanglong_conscription",
                "luoyun_cd_reminder",
                "wanxin_cleanup",
            ),
            app.get_global_scheduler_order_contract(),
        )

    def test_scheduler_manifest_bridge_covers_current_runtime_schedulers(self):
        bridge = app.get_scheduler_manifest_bridge_contract()
        runtime_schedulers = {
            *app.get_identity_scheduler_order_contract()["phaseful"],
            *app.get_identity_scheduler_order_contract()["ordinary"],
            *app.get_global_scheduler_order_contract(),
        }

        self.assertEqual([], sorted(runtime_schedulers - set(bridge)))
        for scheduler_name in sorted(runtime_schedulers):
            entry = bridge[scheduler_name]
            if entry["helper"]:
                continue
            self.assertTrue(entry["manifest_names"], scheduler_name)

    def test_scheduler_manifest_bridge_targets_real_manifests_and_specs(self):
        bridge = app.get_scheduler_manifest_bridge_contract()

        missing_manifests = []
        missing_specs = []
        for scheduler_name, entry in bridge.items():
            if entry["helper"] and not entry["manifest_names"]:
                continue
            for manifest_name in entry["manifest_names"]:
                if not module_manifest.get_module_manifest(manifest_name):
                    missing_manifests.append((scheduler_name, manifest_name))
                if not module_manifest.get_behavior_spec(manifest_name):
                    missing_specs.append((scheduler_name, manifest_name))

        self.assertEqual([], missing_manifests)
        self.assertEqual([], missing_specs)

    def test_due_scan_outer_timeouts_cover_inner_send_queue_budgets(self):
        self.assertGreaterEqual(
            app.DUE_WILD_TRAINING_SCHEDULER_TIMEOUT_SEC,
            app.WILD_TRAINING_SCHEDULER_TIMEOUT_SEC + app.DUE_SCAN_TIMEOUT_MARGIN_SEC,
        )
        self.assertGreaterEqual(
            app.DUE_CONCUBINE_SCHEDULER_TIMEOUT_SEC,
            app.CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC + app.DUE_SCAN_TIMEOUT_MARGIN_SEC,
        )
        self.assertGreaterEqual(
            app.DUE_TIANXING_SCHEDULER_TIMEOUT_SEC,
            app.DUE_RECOVERY_SEND_QUEUE_TIMEOUT_SEC,
        )
        self.assertGreaterEqual(
            app.DUE_EXPLORE_RIFT_SCHEDULER_TIMEOUT_SEC,
            app.DUE_RECOVERY_SEND_QUEUE_TIMEOUT_SEC,
        )

    def test_bootstrap_and_helper_schedulers_are_explicitly_allowed(self):
        bridge = app.get_scheduler_manifest_bridge_contract()

        for scheduler_name in HELPER_SCHEDULERS:
            self.assertTrue(bridge[scheduler_name]["helper"], scheduler_name)
        self.assertEqual(("第二元神",), bridge["run_second_soul_bootstrap_check"]["manifest_names"])
        self.assertEqual(("太一",), bridge["run_taiyi_bootstrap_check"]["manifest_names"])

    def test_legacy_tree_schedulers_are_archived_out_of_runtime_order(self):
        ordinary = app.get_identity_scheduler_order_contract()["ordinary"]
        bridge = app.get_scheduler_manifest_bridge_contract()

        self.assertTrue(module_manifest.is_module_archived("灵树"))
        self.assertNotIn("run_tree_bootstrap_check", ordinary)
        self.assertNotIn("run_tree_scheduler", ordinary)
        self.assertNotIn("run_tree_bootstrap_check", bridge)
        self.assertNotIn("run_tree_scheduler", bridge)

    def test_important_runtime_scheduler_modules_have_behavior_spec_coverage(self):
        bridge = app.get_scheduler_manifest_bridge_contract()
        coverage_by_manifest = {
            spec.name: _behavior_coverage_tokens(spec)
            for spec in module_manifest.iter_behavior_specs()
        }

        for scheduler_name, expected_tokens in IMPORTANT_RUNTIME_SCHEDULER_COVERAGE.items():
            covered_tokens = set()
            for manifest_name in bridge[scheduler_name]["manifest_names"]:
                covered_tokens.update(coverage_by_manifest.get(manifest_name, set()))
            self.assertTrue(
                expected_tokens & covered_tokens,
                f"{scheduler_name} expected one of {sorted(expected_tokens)} in BehaviorSpec coverage, got {sorted(covered_tokens)}",
            )

    def test_behavior_spec_covers_key_scheduler_modules_but_is_not_runtime_source_yet(self):
        behavior_modules = {
            module
            for spec in module_manifest.execution_order()
            for module in ((spec.module,) + tuple(spec.replay_modules))
        }

        self.assertTrue(
            {
                "deep_retreat",
                "yuanying",
                "yinluo",
                "mulan",
                "small_world",
                "wendao",
                "taiyi",
                "divination",
                "world_boss",
            }.issubset(behavior_modules)
        )

        runtime_phaseful = app.get_identity_scheduler_order_contract()["phaseful"]
        behavior_phaseful = [
            spec.module
            for spec in module_manifest.execution_order()
            if spec.phaseful
        ]
        self.assertEqual(["yuanying", "deep_retreat"], behavior_phaseful)
        self.assertNotEqual(
            tuple(f"run_{module}_scheduler" for module in behavior_phaseful),
            runtime_phaseful,
            "BehaviorSpec.execution_order documents coverage/priority only; app scheduler helpers remain the runtime order contract.",
        )


class AppDelayedActionContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_scheduler_refreshes_now_before_each_module(self):
        first = AsyncMock()
        second = AsyncMock()
        with (
            patch.object(app, "get_identity_ids", return_value=[301299112]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "use_identity", side_effect=lambda _identity_id: nullcontext()),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "enforce_identity_module_availability"),
            patch.object(app, "_run_phaseful_identity_schedulers", new=AsyncMock()),
            patch.object(app, "_PHASEFUL_IDENTITY_SCHEDULERS", ()),
            patch.object(app, "_PHASEFUL_BLOCK_CLEANUP_SCHEDULERS", ()),
            patch.object(app, "_ORDINARY_IDENTITY_SCHEDULERS", (first, second)),
            patch.object(app, "has_phaseful_summary_block", return_value=False) as block_mock,
            patch.object(app.time, "time", side_effect=[100.0, 110.0, 120.0, 130.0]),
        ):
            await app._run_identity_schedulers(90.0)

        block_mock.assert_called_once_with(110.0)
        first.assert_awaited_once_with(120.0)
        second.assert_awaited_once_with(130.0)

    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)
        super().tearDown()

    async def test_delayed_action_results_run_inside_target_identity_context(self):
        identity_id = 991777
        state_module.ensure_identity_registered(identity_id)
        seen_identity_ids = []

        async def fake_delayed_scheduler(now, send_func):
            self.assertIs(send_func, app.send_game_command)
            return [
                {
                    "id": 1,
                    "status": "sent",
                    "send_as_id": identity_id,
                    "source_module": "jiyin",
                    "op_id": "jiyin_prompt_reply",
                }
            ]

        async def fake_jiyin_result_handler(result):
            seen_identity_ids.append(state_module.get_current_identity_id())
            return True

        with (
            patch.object(app, "_GLOBAL_SCHEDULERS", (("delayed_actions", fake_delayed_scheduler),)),
            patch.object(app, "handle_jiyin_delayed_action_result", new=AsyncMock(side_effect=fake_jiyin_result_handler)) as handler_mock,
        ):
            await app._run_global_schedulers(1_700_000_000.0)

        handler_mock.assert_awaited_once()
        self.assertEqual([identity_id], seen_identity_ids)

    async def test_phaseful_block_runs_cleanup_without_ordinary_schedulers(self):
        identity_id = 991778
        state_module.ensure_identity_registered(identity_id)
        seen = []

        async def fake_cleanup(now):
            seen.append(("cleanup", state_module.get_current_identity_id(), now))

        async def fake_ordinary(now):
            seen.append(("ordinary", state_module.get_current_identity_id(), now))

        now = 1_700_000_000.0
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_PHASEFUL_IDENTITY_SCHEDULERS", ()),
            patch.object(app, "_PHASEFUL_BLOCK_CLEANUP_SCHEDULERS", (fake_cleanup,)),
            patch.object(app, "_ORDINARY_IDENTITY_SCHEDULERS", (fake_ordinary,)),
            patch.object(app, "has_phaseful_summary_block", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_identity_schedulers(now)

        self.assertEqual([("cleanup", identity_id, now)], seen)

    async def test_identity_scheduler_enforces_module_availability_before_send_schedulers(self):
        identity_id = 991781
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="sanxiu", sect_name="散修", realm="元婴初期")
        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["tower_enabled"] = True
            state_module.state["next_stargazer_panel_time"] = 1
            state_module.state["next_tower_time"] = 1

        seen = []

        async def fake_ordinary(now):
            seen.append((
                state_module.get_current_identity_id(),
                bool(state_module.state.get("stargazer_enabled")),
                bool(state_module.state.get("tower_enabled")),
            ))

        now = 1_700_000_000.0
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_PHASEFUL_IDENTITY_SCHEDULERS", ()),
            patch.object(app, "_ORDINARY_IDENTITY_SCHEDULERS", (fake_ordinary,)),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch("model.control.save_state"),
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_identity_schedulers(now)

        self.assertEqual([(identity_id, False, False)], seen)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["next_stargazer_panel_time"])
            self.assertEqual(0, state_module.state["next_tower_time"])

    async def test_phaseful_catchup_runs_before_ordinary_when_due_during_long_cycle(self):
        identity_id = 991779
        state_module.ensure_identity_registered(identity_id)
        seen = []

        async def fake_phaseful(now):
            seen.append(("phaseful", state_module.get_current_identity_id(), now))

        async def fake_cleanup(now):
            seen.append(("cleanup", state_module.get_current_identity_id(), now))

        async def fake_ordinary(now):
            seen.append(("ordinary", state_module.get_current_identity_id(), now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_PHASEFUL_IDENTITY_SCHEDULERS", (fake_phaseful,)),
            patch.object(app, "_PHASEFUL_BLOCK_CLEANUP_SCHEDULERS", (fake_cleanup,)),
            patch.object(app, "_ORDINARY_IDENTITY_SCHEDULERS", (fake_ordinary,)),
            patch.object(app, "has_phaseful_summary_block", side_effect=lambda scheduler_now: scheduler_now >= 120.0),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app.time, "time", return_value=130.0),
        ):
            await app._run_identity_schedulers(100.0)

        self.assertEqual(
            [
                ("phaseful", identity_id, 130.0),
                ("phaseful", identity_id, 130.0),
                ("cleanup", identity_id, 130.0),
            ],
            seen,
        )

    async def test_small_world_identity_scheduler_runs_only_small_world_in_identity_context(self):
        identity_id = 991780
        state_module.ensure_identity_registered(identity_id)
        seen = []

        async def fake_small_world(now):
            seen.append(("small_world", state_module.get_current_identity_id(), now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_small_world_scheduler", new=AsyncMock(side_effect=fake_small_world)) as scheduler_mock,
            patch.object(app.time, "time", return_value=130.0),
        ):
            await app._run_small_world_identity_schedulers(100.0)

        scheduler_mock.assert_awaited_once_with(130.0)
        self.assertEqual([("small_world", identity_id, 130.0)], seen)

    async def test_small_world_identity_scheduler_skips_phaseful_block(self):
        identity_id = 991781
        state_module.ensure_identity_registered(identity_id)

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=True),
            patch.object(app, "run_small_world_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app.time, "time", return_value=130.0),
        ):
            await app._run_small_world_identity_schedulers(100.0)

        scheduler_mock.assert_not_awaited()

    async def test_due_wild_training_retry_fast_scan_runs_due_retry(self):
        first_identity_id = 991782
        second_identity_id = 991783
        now = 1_700_000_000.0
        for identity_id in (first_identity_id, second_identity_id):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wild_training_enabled"] = True
                state_module.state["wild_training_retry_count"] = 1
                state_module.state["wild_training_reply_to_msg_id"] = 0
                state_module.state["next_wild_training_time"] = now - 10

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[first_identity_id, second_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(first_identity_id, now)], seen)

    async def test_due_explore_rift_fast_scan_runs_tianxing_prepare_window(self):
        identity_id = 991780
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["tianxing_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 0
            state_module.state["explore_rift_pending_result_msg_id"] = 0
            state_module.state["next_explore_rift_time"] = now + 300
            state_module.state["explore_rift_tianxing_prepare_retry_at"] = 0

        seen = []

        async def fake_explore_rift_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "build_tianxing_consume_window", return_value=[{"route": "探索"}]),
            patch.object(app, "run_explore_rift_scheduler", new=AsyncMock(side_effect=fake_explore_rift_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_explore_rift_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(identity_id, now)], seen)

    async def test_due_explore_rift_fast_scan_skips_prepare_before_retry_time(self):
        identity_id = 991779
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["tianxing_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 0
            state_module.state["explore_rift_pending_result_msg_id"] = 0
            state_module.state["next_explore_rift_time"] = now + 300
            state_module.state["explore_rift_tianxing_prepare_retry_at"] = now + 60

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "build_tianxing_consume_window", return_value=[{"route": "探索"}]) as window_mock,
            patch.object(app, "run_explore_rift_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_explore_rift_schedulers(now, limit=1)

        window_mock.assert_not_called()
        scheduler_mock.assert_not_awaited()

    async def test_due_explore_rift_fast_scan_recovers_legacy_pending_result_without_due_at(self):
        identity_id = 991778
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 0
            state_module.state["explore_rift_reply_due_at"] = 0
            state_module.state["explore_rift_pending_result_msg_id"] = 22028
            state_module.state["next_explore_rift_time"] = now + 12 * 3600

        seen = []

        async def fake_explore_rift_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_explore_rift_scheduler", new=AsyncMock(side_effect=fake_explore_rift_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_explore_rift_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(identity_id, now)], seen)

    async def test_due_wild_training_fast_scan_defaults_to_small_batch(self):
        identity_ids = [991790 + idx for idx in range(3)]
        now = 1_700_000_000.0
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wild_training_enabled"] = True
                state_module.state["wild_training_retry_count"] = 0
                state_module.state["wild_training_reply_to_msg_id"] = 0
                state_module.state["next_wild_training_time"] = now - 10

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=identity_ids),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now)

        self.assertEqual(3, scheduler_mock.await_count)
        self.assertEqual([(identity_id, now) for identity_id in identity_ids], seen)

    async def test_due_wild_training_fast_scan_prioritizes_earliest_due(self):
        later_identity_id = 991796
        earlier_identity_id = 991797
        now = 1_700_000_000.0
        for identity_id, due_at in (
            (later_identity_id, now - 10),
            (earlier_identity_id, now - 300),
        ):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wild_training_enabled"] = True
                state_module.state["wild_training_retry_count"] = 0
                state_module.state["wild_training_reply_to_msg_id"] = 0
                state_module.state["next_wild_training_time"] = due_at

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[later_identity_id, earlier_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(earlier_identity_id, now)], seen)

    async def test_due_wild_training_fast_scan_timeout_does_not_block_next_candidate(self):
        stuck_identity_id = 991798
        next_identity_id = 991799
        now = 1_700_000_000.0
        for identity_id, due_at in (
            (stuck_identity_id, now - 300),
            (next_identity_id, now - 200),
        ):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wild_training_enabled"] = True
                state_module.state["wild_training_retry_count"] = 0
                state_module.state["wild_training_reply_to_msg_id"] = 0
                state_module.state["next_wild_training_time"] = due_at

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            current_id = state_module.get_current_identity_id()
            seen.append((current_id, scheduler_now))
            if current_id == stuck_identity_id:
                await asyncio.sleep(60)

        with (
            patch.object(app, "get_identity_ids", return_value=[stuck_identity_id, next_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app, "DUE_WILD_TRAINING_SCHEDULER_TIMEOUT_SEC", 0.01),
            patch.object(app, "DUE_WILD_TRAINING_DIAG_INTERVAL_SEC", 999999),
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=2)

        self.assertEqual(2, scheduler_mock.await_count)
        self.assertEqual(
            [(stuck_identity_id, now), (next_identity_id, now)],
            seen,
        )
        with state_module.use_identity(stuck_identity_id):
            self.assertEqual(now + 120, state_module.state["next_wild_training_time"])
            self.assertIn("执行超时", state_module.state["wild_training_last_error"])

    async def test_due_wild_training_fast_scan_runs_due_normal_cycle(self):
        identity_id = 991787
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["next_wild_training_time"] = now - 10

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(identity_id, now)], seen)

    async def test_due_wild_training_fast_scan_preserves_server_cooldown_after_release(self):
        identity_id = 991788
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["next_wild_training_time"] = now + 120
            state_module.state["wild_training_last_result"] = "天星时间线：sent_waiting_ack"
            state_module.state["wild_training_last_error"] = "野外历练 需等待天星时间线确认 探索 改命后放行。"

        seen = []

        async def fake_wild_training_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "is_tianxing_route_released", return_value=True),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock(side_effect=fake_wild_training_scheduler)) as scheduler_mock,
            patch.object(app, "mark_dirty") as mark_dirty_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_not_awaited()
        mark_dirty_mock.assert_not_called()
        self.assertEqual([], seen)
        with state_module.use_identity(identity_id):
            self.assertEqual(now + 120, state_module.state["next_wild_training_time"])
            self.assertIn("等待天星时间线", state_module.state["wild_training_last_error"])

    async def test_due_wild_training_fast_scan_does_not_clamp_released_tianxing_before_true_cd(self):
        identity_id = 991781
        now = 1_700_000_000.0
        completed_at = now - 3600
        true_due_at = now + 180
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["wild_training_last_completed_at"] = completed_at
            state_module.state["next_wild_training_time"] = true_due_at
            state_module.state["wild_training_last_result"] = "天星时间线：sent_waiting_ack"
            state_module.state["wild_training_last_error"] = ""

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "is_tianxing_route_released", return_value=True),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app, "mark_dirty") as mark_dirty_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_not_awaited()
        mark_dirty_mock.assert_not_called()
        with state_module.use_identity(identity_id):
            self.assertEqual(true_due_at, state_module.state["next_wild_training_time"])
            self.assertEqual("", state_module.state["wild_training_last_error"])

    async def test_due_wild_training_fast_scan_does_not_clamp_future_cd_for_released_tianxing_route(self):
        identity_id = 991789
        now = 1_700_000_000.0
        future_cd = now + 600
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["next_wild_training_time"] = future_cd
            state_module.state["wild_training_last_result"] = "天星时间线：downstream_released"
            state_module.state["wild_training_last_error"] = "野外历练 需等待天星时间线确认 探索 改命后放行。"

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "is_tianxing_route_released", return_value=True),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app, "mark_dirty") as mark_dirty_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now, limit=1)

        scheduler_mock.assert_not_awaited()
        mark_dirty_mock.assert_not_called()
        with state_module.use_identity(identity_id):
            self.assertEqual(future_cd, state_module.state["next_wild_training_time"])

    async def test_due_wild_training_retry_ignores_legacy_inflight_reply(self):
        identity_id = 991784
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 1
            state_module.state["wild_training_reply_to_msg_id"] = 11244715
            state_module.state["next_wild_training_time"] = now - 10

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now)

        scheduler_mock.assert_awaited_once()

    async def test_due_wild_training_retry_ignores_legacy_pending_with_future_cd(self):
        identity_id = 991786
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 0
            state_module.state["wild_training_reply_to_msg_id"] = 11294100
            state_module.state["wild_training_reply_due_at"] = now - 10
            state_module.state["next_wild_training_time"] = now + 20 * 60

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now)

        scheduler_mock.assert_not_awaited()

    async def test_due_wild_training_retry_fast_scan_preserves_miniapp_backoff(self):
        identity_id = 991785
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["wild_training_enabled"] = True
            state_module.state["wild_training_retry_count"] = 1
            state_module.state["wild_training_reply_to_msg_id"] = 0
            state_module.state["next_wild_training_time"] = now + 20 * 60

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_wild_training_scheduler", new=AsyncMock()) as scheduler_mock,
            patch.object(app, "mark_dirty") as mark_dirty_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_wild_training_retry_schedulers(now)

        scheduler_mock.assert_not_awaited()
        mark_dirty_mock.assert_not_called()
        with state_module.use_identity(identity_id):
            self.assertEqual(now + 20 * 60, state_module.state["next_wild_training_time"])

    async def test_due_concubine_fast_scan_prioritizes_earliest_due(self):
        later_identity_id = 991830
        earlier_identity_id = 991831
        now = 1_700_000_000.0
        for identity_id, due_at in (
            (later_identity_id, now - 10),
            (earlier_identity_id, now - 300),
        ):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["concubine_enabled"] = True
                state_module.state["next_concubine_time"] = due_at

        seen = []

        async def fake_concubine_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[later_identity_id, earlier_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_concubine_scheduler", new=AsyncMock(side_effect=fake_concubine_scheduler)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_concubine_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once_with(now)
        self.assertEqual([(earlier_identity_id, now)], seen)

    async def test_due_concubine_fast_scan_timeout_does_not_block_next_candidate(self):
        stuck_identity_id = 991832
        next_identity_id = 991833
        now = 1_700_000_000.0
        for identity_id, due_at in (
            (stuck_identity_id, now - 300),
            (next_identity_id, now - 200),
        ):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["concubine_enabled"] = True
                state_module.state["next_concubine_time"] = due_at

        seen = []

        async def fake_concubine_scheduler(scheduler_now):
            current_id = state_module.get_current_identity_id()
            seen.append((current_id, scheduler_now))
            if current_id == stuck_identity_id:
                await asyncio.sleep(60)

        with (
            patch.object(app, "get_identity_ids", return_value=[stuck_identity_id, next_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_concubine_scheduler", new=AsyncMock(side_effect=fake_concubine_scheduler)) as scheduler_mock,
            patch.object(app, "DUE_CONCUBINE_SCHEDULER_TIMEOUT_SEC", 0.01),
            patch.object(app, "DUE_CONCUBINE_DIAG_INTERVAL_SEC", 999999),
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_concubine_schedulers(now, limit=2)

        self.assertEqual(2, scheduler_mock.await_count)
        self.assertEqual(
            [(stuck_identity_id, now), (next_identity_id, now)],
            seen,
        )
        with state_module.use_identity(stuck_identity_id):
            self.assertEqual(now + 120, state_module.state["next_concubine_time"])
            self.assertIn("执行超时", state_module.state["concubine_last_result"])
            self.assertEqual("", state_module.state["concubine_last_error"])

    async def test_due_concubine_fast_scan_applies_send_queue_timeout(self):
        identity_id = 991834
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["concubine_tianji_enabled"] = True
            state_module.state["next_concubine_time"] = now - 1
            state_module.state["concubine_availability"] = "unknown"

        sent_msg = type("SentMsg", (), {"id": 902, "sent_at": now})()
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch("model.features.concubine.send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
        ):
            await app._run_due_concubine_schedulers(now, limit=1)

        send_mock.assert_awaited_once_with(
            ".我的侍妾",
            track=False,
            queue_timeout=app.CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC,
        )

    async def test_due_concubine_fast_scan_clears_legacy_timeout_error(self):
        identity_id = 991835
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["next_concubine_time"] = now + 120
            state_module.state["concubine_reply_to_msg_id"] = 0
            state_module.state["concubine_last_error"] = "到期侍妾扫描执行超时（>90s），已让出本轮避免阻塞其他身份"

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_concubine_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_concubine_schedulers(now, limit=1)

        scheduler_mock.assert_not_awaited()
        with state_module.use_identity(identity_id):
            self.assertIn("执行超时", state_module.state["concubine_last_result"])
            self.assertEqual("", state_module.state["concubine_last_error"])

    async def test_due_tianxing_fast_scan_runs_due_auto_time(self):
        identity_id = 991835
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {"auto_next_time": now - 1}

        seen = []

        async def fake_tianxing_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock(side_effect=fake_tianxing_scheduler)) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once()
        self.assertEqual([(identity_id, now)], seen)

    async def test_due_tianxing_fast_scan_prepares_upcoming_wild_training(self):
        identity_id = 991840
        now = 1_700_000_000.0
        due_at = now + 9 * 60
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = due_at
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}

        windows = [{"route": "探索", "kind": "consume", "reason": "野外历练"}]
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "build_tianxing_route_preflight_plan", return_value={"route_allowed": False, "timeline_required": True}),
            patch.object(app, "build_tianxing_consume_window", return_value=windows),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack"})) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_awaited_once_with(now, windows=windows)
        scheduler_mock.assert_awaited_once_with(now)

    async def test_due_tianxing_fast_scan_prepares_upcoming_explore_rift(self):
        identity_id = 991841
        now = 1_700_000_000.0
        due_at = now + 9 * 60
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = due_at
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}

        windows = [{"route": "探索", "kind": "consume", "reason": "探寻裂缝"}]
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "build_tianxing_route_preflight_plan", return_value={"route_allowed": False, "timeline_required": True}),
            patch.object(app, "build_tianxing_consume_window", return_value=windows),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack"})) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()),
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_awaited_once_with(now, windows=windows)

    async def test_due_tianxing_fast_scan_skips_downstream_when_explore_protection_is_fresh(self):
        identity_id = 991842
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = now + 9 * 60
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "build_tianxing_route_preflight_plan", return_value={"route_allowed": True}),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_not_awaited()
        scheduler_mock.assert_not_awaited()

    async def test_due_tianxing_fast_scan_skips_disabled_downstream_module(self):
        identity_id = 991843
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["wild_training_enabled"] = False
            state_module.state["next_wild_training_time"] = now + 9 * 60
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_not_awaited()
        scheduler_mock.assert_not_awaited()

    async def test_due_tianxing_fast_scan_preserves_zero_priority_and_tianji(self):
        urgent_identity_id = 991844
        routine_identity_id = 991845
        now = 1_700_000_000.0
        for identity_id in (urgent_identity_id, routine_identity_id):
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["tianxing_enabled"] = True

        def fake_due_info(_now):
            if state_module.get_current_identity_id() == urgent_identity_id:
                return {"due_at": now, "priority": 0, "tianji": 0}
            return {"due_at": now - 60, "priority": 3, "tianji": 1}

        seen = []

        async def fake_candidate(identity_id, scheduler_now):
            seen.append((identity_id, scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[routine_identity_id, urgent_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "_tianxing_fast_due_info", side_effect=fake_due_info),
            patch.object(app, "_run_due_tianxing_candidate", new=AsyncMock(side_effect=fake_candidate)) as candidate_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        candidate_mock.assert_awaited_once_with(urgent_identity_id, now)
        self.assertEqual([(urgent_identity_id, now)], seen)

    async def test_due_tianxing_fast_scan_allows_downstream_prepare_during_phaseful_summary(self):
        identity_id = 991846
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = now + 9 * 60
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}

        windows = [{"route": "探索", "kind": "consume", "reason": "野外历练"}]
        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=True),
            patch.object(app, "build_tianxing_route_preflight_plan", return_value={"route_allowed": False, "timeline_required": True}),
            patch.object(app, "build_tianxing_consume_window", return_value=windows),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack"})) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()),
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_awaited_once_with(now, windows=windows)

    async def test_due_tianxing_fast_scan_keeps_phaseful_block_for_routine_auto_work(self):
        identity_id = 991847
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {"auto_next_time": now - 1}

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=True),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        timeline_mock.assert_not_awaited()
        scheduler_mock.assert_not_awaited()

    async def test_due_tianxing_fast_scan_runs_craft_override_due(self):
        identity_id = 991836
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {"auto_next_time": now + 6 * 3600}
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "prediction_conflict",
                    "next_time": now + 6 * 3600,
                },
            }

        seen = []

        async def fake_tianxing_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "has_tianxing_timeline_due_work", return_value=False),
            patch.object(app, "has_tianxing_craft_farm_override_due", return_value=True),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock(side_effect=fake_tianxing_scheduler)) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once()
        self.assertEqual([(identity_id, now)], seen)

    async def test_due_tianxing_fast_scan_ignores_stale_craft_next_when_business_blocked(self):
        identity_id = 991837
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "auto_next_time": now + 6 * 3600,
                "tianji_value": 1,
            }
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "ready",
                    "next_time": now - 600,
                },
            }

        with (
            patch.object(app, "get_identity_ids", return_value=[identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "has_tianxing_timeline_due_work", return_value=False),
            patch.object(app, "has_tianxing_craft_farm_due", return_value=False),
            patch.object(app, "has_tianxing_craft_farm_override_due", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock()) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        scheduler_mock.assert_not_awaited()

    async def test_due_tianxing_fast_scan_prioritizes_lower_tianji_craft_farm(self):
        high_tianji_identity_id = 991838
        low_tianji_identity_id = 991839
        now = 1_700_000_000.0
        for identity_id in (high_tianji_identity_id, low_tianji_identity_id):
            state_module.ensure_identity_registered(identity_id)
        with state_module.use_identity(high_tianji_identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "auto_next_time": now + 6 * 3600,
                "tianji_value": 30,
            }
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "ready",
                    "next_time": now - 1800,
                },
            }
        with state_module.use_identity(low_tianji_identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "auto_next_time": now + 6 * 3600,
                "tianji_value": 2,
            }
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "ready",
                    "next_time": now - 60,
                },
            }

        seen = []

        async def fake_tianxing_scheduler(scheduler_now):
            seen.append((state_module.get_current_identity_id(), scheduler_now))

        with (
            patch.object(app, "get_identity_ids", return_value=[high_tianji_identity_id, low_tianji_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "has_tianxing_timeline_due_work", return_value=False),
            patch.object(app, "has_tianxing_craft_farm_due", return_value=True),
            patch.object(app, "has_tianxing_craft_farm_override_due", return_value=False),
            patch.object(app.time, "time", return_value=now),
            patch.object(app, "run_tianxing_scheduler", new=AsyncMock(side_effect=fake_tianxing_scheduler)) as scheduler_mock,
        ):
            await app._run_due_tianxing_schedulers(now, limit=1)

        scheduler_mock.assert_awaited_once()
        self.assertEqual([(low_tianji_identity_id, now)], seen)

    async def test_tianxing_daily_bootstrap_pending_does_not_consume_send_limit(self):
        first_identity_id = 991793
        second_identity_id = 991794
        third_identity_id = 991795
        now = 1_700_000_000.0
        for identity_id in (first_identity_id, second_identity_id, third_identity_id):
            state_module.ensure_identity_registered(identity_id)

        seen = []

        async def fake_tianxing_daily_bootstrap(scheduler_now):
            current_id = state_module.get_current_identity_id()
            seen.append((current_id, scheduler_now))
            if current_id == first_identity_id:
                return {"active": True, "reason": "pending"}
            return {"active": True, "action": "observe", "command": ".观命"}

        with (
            patch.object(app, "get_identity_ids", return_value=[first_identity_id, second_identity_id, third_identity_id]),
            patch.object(app, "get_identity_enabled", return_value=True),
            patch.object(app, "_is_identity_account_offline", return_value=False),
            patch.object(app, "is_identity_weak", return_value=False),
            patch.object(app, "has_phaseful_summary_block", return_value=False),
            patch.object(app, "run_tianxing_daily_bootstrap_scheduler", new=AsyncMock(side_effect=fake_tianxing_daily_bootstrap)) as scheduler_mock,
            patch.object(app.time, "time", return_value=now),
        ):
            await app._run_tianxing_daily_bootstrap_identity_schedulers(now, limit=2)

        self.assertEqual(3, scheduler_mock.await_count)
        self.assertEqual(
            [
                (first_identity_id, now),
                (second_identity_id, now),
                (third_identity_id, now),
            ],
            seen,
        )

    async def test_main_loop_runs_phaseful_pass_even_when_identity_background_is_separate(self):
        stop_event = asyncio.Event()
        seen = []

        async def fake_sleep(loop_stop_event, delay):
            seen.append(("sleep", delay))
            loop_stop_event.set()

        async def fake_phaseful(now):
            seen.append(("phaseful", now))

        async def fake_wild_retry(now):
            seen.append(("wild_retry", now))

        async def fake_tianxing_due(now):
            seen.append(("tianxing_due", now))

        async def fake_explore_rift_due(now):
            seen.append(("explore_rift_due", now))

        async def fake_concubine_retry(now):
            seen.append(("concubine_retry", now))

        async def fake_rare(now):
            seen.append(("rare", now))

        async def fake_global(now):
            seen.append(("global", now))

        def fake_start_identity(now):
            seen.append(("start_identity", now))

        async def noop_async(*_args, **_kwargs):
            return None

        with ExitStack() as stack:
            for ctx in (
                patch.object(app, "gc_my_msg_ids"),
                patch.object(app, "gc_ui_login_tokens"),
                patch.object(app, "gc_ui_sessions"),
                patch.object(app, "flush_if_dirty", return_value=True),
                patch.object(app, "has_persistence_write_failure", return_value=False),
                patch.object(app, "check_bot_health_timeout"),
                patch.object(app, "should_pause_for_bot_health", return_value=False),
                patch.object(app, "get_global_enabled", return_value=True),
                patch.object(app, "run_rare_daily_report_scheduler", new=AsyncMock(side_effect=fake_rare)),
                patch.object(app, "_run_global_schedulers", new=AsyncMock(side_effect=fake_global)),
                patch.object(app, "run_quiz_learning_scheduler", new=AsyncMock()),
                patch.object(app, "run_retry_scheduler", new=AsyncMock()),
                patch.object(app, "run_identity_info_followup_scheduler", new=AsyncMock()),
                patch.object(app, "_run_phaseful_identity_schedulers", new=AsyncMock(side_effect=fake_phaseful)),
                patch.object(app, "_run_due_tianxing_schedulers", new=AsyncMock(side_effect=fake_tianxing_due)),
                patch.object(app, "_run_due_explore_rift_schedulers", new=AsyncMock(side_effect=fake_explore_rift_due)),
                patch.object(app, "_run_due_wild_training_retry_schedulers", new=AsyncMock(side_effect=fake_wild_retry)),
                patch.object(app, "_run_due_concubine_schedulers", new=AsyncMock(side_effect=fake_concubine_retry)),
                patch.object(app, "_start_identity_schedulers_if_idle", side_effect=fake_start_identity),
                patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
                patch.object(app.time, "time", return_value=200.0),
            ):
                stack.enter_context(ctx)
            await app.main_loop(stop_event)

        self.assertEqual(
            [
                ("phaseful", 200.0),
                ("tianxing_due", 200.0),
                ("explore_rift_due", 200.0),
                ("wild_retry", 200.0),
                ("concubine_retry", 200.0),
                ("rare", 200.0),
                ("global", 200.0),
                ("phaseful", 200.0),
                ("start_identity", 200.0),
                ("sleep", 5),
            ],
            seen,
        )

    async def test_phaseful_scheduler_loop_runs_independently(self):
        stop_event = asyncio.Event()
        seen = []

        async def fake_phaseful(now):
            seen.append(("phaseful", now))

        async def fake_sleep(loop_stop_event, delay):
            seen.append(("sleep", delay))
            loop_stop_event.set()

        with (
            patch.object(app, "get_global_enabled", return_value=True),
            patch.object(app, "_run_phaseful_identity_schedulers", new=AsyncMock(side_effect=fake_phaseful)),
            patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
            patch.object(app.time, "time", return_value=300.0),
        ):
            await app._run_phaseful_scheduler_loop(stop_event)

        self.assertEqual([("phaseful", 300.0), ("sleep", 5)], seen)

    def test_bot_health_pause_preserves_pending_tasks(self):
        async def run_case():
            stop_event = asyncio.Event()

            async def fake_sleep(loop_stop_event, delay):
                loop_stop_event.set()

            with ExitStack() as stack:
                clear_mock = stack.enter_context(patch.object(app, "clear_all_pending_tasks", create=True))
                toggle_mock = stack.enter_context(patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))))
                cancel_mock = stack.enter_context(patch.object(app, "_cancel_identity_schedulers"))
                for ctx in (
                    patch.object(app, "gc_my_msg_ids"),
                    patch.object(app, "gc_ui_login_tokens"),
                    patch.object(app, "gc_ui_sessions"),
                    patch.object(app, "flush_if_dirty", return_value=True),
                    patch.object(app, "has_persistence_write_failure", return_value=False),
                    patch.object(app, "check_bot_health_timeout"),
                    patch.object(app, "should_pause_for_bot_health", return_value=True),
                    patch.object(app, "get_global_enabled", side_effect=[True, False]),
                    patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
                    patch.object(app.time, "time", return_value=400.0),
                ):
                    stack.enter_context(ctx)

                await app.main_loop(stop_event)

            toggle_mock.assert_awaited_once_with(False, source="bot_health_monitor")
            self.assertGreaterEqual(cancel_mock.call_count, 1)
            clear_mock.assert_not_called()

        asyncio.run(run_case())

    def test_quiesce_runtime_persists_module_pending_state_before_supervisor_drain(self):
        quiesce_event = asyncio.Event()
        with (
            patch.object(app, "set_game_send_quiesced") as quiesce_mock,
            patch.object(app, "_cancel_identity_schedulers") as cancel_mock,
            patch.object(app, "save_state") as save_mock,
        ):
            app._quiesce_runtime(quiesce_event)

        quiesce_mock.assert_called_once_with(True)
        cancel_mock.assert_called_once_with()
        save_mock.assert_called_once_with()
        self.assertTrue(quiesce_event.is_set())

    async def test_bot_health_auto_pause_recovers_global_after_probe_reply(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = True
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value="recover") as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            toggle_mock.assert_awaited_once_with(True, source="bot_health_recovery")
            health_mock.assert_called_once_with(1000.0, reply_to_msg_id=12345)
            recovered_mock.assert_called_once_with("bot 恢复确认完成")
            self.assertFalse(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_bot_health_recovery_event_recovers_when_pause_source_is_bot_health(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = False
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value="recover") as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="bot_health_monitor"),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            toggle_mock.assert_awaited_once_with(True, source="bot_health_recovery")
            health_mock.assert_called_once_with(1000.0, reply_to_msg_id=12345)
            recovered_mock.assert_called_once_with("bot 恢复确认完成")
            self.assertFalse(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_persisted_bot_health_pause_restores_probe_before_recovery(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = False
        probe_coro = object()
        try:
            with (
                patch.object(app, "note_game_bot_message", side_effect=[None, "probe"]),
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="bot_health_monitor"),
                patch.object(app, "should_pause_for_bot_health", return_value=False),
                patch.object(app, "restore_bot_health_auto_pause") as restore_mock,
                patch.object(app, "_fire_and_forget") as fire_mock,
                patch.object(app, "_send_bot_health_probe", new=MagicMock(return_value=probe_coro)) as probe_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            restore_mock.assert_called_once_with("恢复持久化天尊健康暂停态")
            probe_mock.assert_called_once()
            fire_mock.assert_called_once_with(probe_coro)
            self.assertTrue(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_persisted_bot_health_pause_does_not_reset_active_probe(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = True
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value=None) as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="bot_health_monitor"),
                patch.object(app, "should_pause_for_bot_health", return_value=True),
                patch.object(app, "restore_bot_health_auto_pause") as restore_mock,
                patch.object(app, "_fire_and_forget") as fire_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            health_mock.assert_called_once_with(1000.0, reply_to_msg_id=12345)
            restore_mock.assert_not_called()
            fire_mock.assert_not_called()
            self.assertTrue(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_persisted_bot_health_pause_does_not_rearm_after_process_pause_flag(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = True
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value=None) as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="bot_health_monitor"),
                patch.object(app, "should_pause_for_bot_health", return_value=False),
                patch.object(app, "restore_bot_health_auto_pause") as restore_mock,
                patch.object(app, "_fire_and_forget") as fire_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            health_mock.assert_called_once_with(1000.0, reply_to_msg_id=12345)
            restore_mock.assert_not_called()
            fire_mock.assert_not_called()
            self.assertTrue(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_manual_global_pause_does_not_auto_recover_on_ordinary_bot_reply(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = False
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value=None),
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="ui"),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            toggle_mock.assert_not_awaited()
            recovered_mock.assert_not_called()
            self.assertFalse(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_manual_global_pause_does_not_auto_recover_on_recover_event(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = False
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value="recover"),
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "get_global_pause_source", return_value="ui"),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "【野外历练】\n@alpha 选择【谨慎】策略。",
                    12345,
                    {"send_as_id": 301299112, "family": "wild_training"},
                    now=1000.0,
                )

            toggle_mock.assert_not_awaited()
            recovered_mock.assert_called_once_with("bot 恢复确认完成")
            self.assertFalse(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_bot_health_ignores_world_boss_broadcast_for_recovery(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = True
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value="recover") as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "━━━━━━━━━━━━━━━\n【世界通告｜真仙试锋开启】\n点击下方按钮进入真仙战场。",
                    12345,
                    {"send_as_id": 301299112, "family": "world_boss"},
                    now=1000.0,
                )

            health_mock.assert_not_called()
            toggle_mock.assert_not_awaited()
            recovered_mock.assert_not_called()
            self.assertTrue(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_bot_health_ignores_phaseful_summary_broadcast_for_recovery(self):
        old_flag = app._bot_silence_auto_paused
        app._bot_silence_auto_paused = True
        try:
            with (
                patch.object(app, "note_game_bot_message", return_value="recover") as health_mock,
                patch.object(app, "get_global_enabled", return_value=False),
                patch.object(app, "toggle_global_enabled", new=AsyncMock(return_value=(True, "ok"))) as toggle_mock,
                patch.object(app, "mark_bot_health_recovered") as recovered_mock,
            ):
                await app._note_game_bot_activity(
                    "📜 修士 @foo 深度闭关总结\n【深度闭关总结】\n本次结算时长: 8.0 小时",
                    12345,
                    {"send_as_id": 301299112, "family": "deep_retreat"},
                    now=1000.0,
                )

            health_mock.assert_not_called()
            toggle_mock.assert_not_awaited()
            recovered_mock.assert_not_called()
            self.assertTrue(app._bot_silence_auto_paused)
        finally:
            app._bot_silence_auto_paused = old_flag

    async def test_small_world_scheduler_loop_runs_independently(self):
        stop_event = asyncio.Event()
        seen = []

        async def fake_small_world(now):
            seen.append(("small_world", now))

        async def fake_sleep(loop_stop_event, delay):
            seen.append(("sleep", delay))
            loop_stop_event.set()

        with (
            patch.object(app, "get_global_enabled", return_value=True),
            patch.object(app, "_run_small_world_identity_schedulers", new=AsyncMock(side_effect=fake_small_world)),
            patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
            patch.object(app.time, "time", return_value=300.0),
        ):
            await app._run_small_world_scheduler_loop(stop_event)

        self.assertEqual([("small_world", 300.0), ("sleep", 10)], seen)

    async def test_small_world_scheduler_loop_respects_global_disabled(self):
        stop_event = asyncio.Event()
        seen = []

        async def fake_sleep(loop_stop_event, delay):
            seen.append(("sleep", delay))
            loop_stop_event.set()

        with (
            patch.object(app, "get_global_enabled", return_value=False),
            patch.object(app, "_run_small_world_identity_schedulers", new=AsyncMock()) as scheduler_mock,
            patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
            patch.object(app.time, "time", return_value=300.0),
        ):
            await app._run_small_world_scheduler_loop(stop_event)

        scheduler_mock.assert_not_awaited()
        self.assertEqual([("sleep", 10)], seen)


if __name__ == "__main__":
    unittest.main()
