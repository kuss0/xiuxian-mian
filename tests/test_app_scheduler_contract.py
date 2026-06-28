import copy
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import module_manifest
from model import state as state_module


IMPORTANT_RUNTIME_SCHEDULER_COVERAGE = {
    "run_pet_scheduler": {"pet", "pet_warm", "pet_trial"},
    "run_ranch_scheduler": {"ranch"},
    "run_wild_training_scheduler": {"wild_training"},
    "run_formation_scheduler": {"formation"},
    "run_tianti_scheduler": {"tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"},
    "run_concubine_scheduler": {"concubine"},
    "run_hehuan_scheduler": {"hehuan", "hehuan_dual"},
    "run_yinluo_scheduler": {"yinluo"},
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

        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_small_world_scheduler"))
        self.assertLess(_index(ordinary, "run_small_world_scheduler"), _index(ordinary, "run_explore_rift_scheduler"))
        self.assertLess(_index(ordinary, "run_yinluo_scheduler"), _index(ordinary, "run_wendao_scheduler"))
        self.assertLess(_index(ordinary, "run_wendao_scheduler"), _index(ordinary, "run_tree_bootstrap_check"))
        self.assertLess(_index(ordinary, "run_tree_bootstrap_check"), _index(ordinary, "run_tree_scheduler"))
        self.assertLess(_index(ordinary, "run_tree_scheduler"), _index(ordinary, "run_checkin_scheduler"))
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
                "delayed_actions",
                "guanxing_monitor",
                "guanxing",
                "storage_bag_api_keepalive",
                "storage_bag_transfer",
                "divination",
                "world_boss",
                "tiandao_judgement",
                "tianji_quiz",
                "huanglong_conscription",
                "luoyun_cd_reminder",
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

    def test_bootstrap_and_helper_schedulers_are_explicitly_allowed(self):
        bridge = app.get_scheduler_manifest_bridge_contract()

        for scheduler_name in HELPER_SCHEDULERS:
            self.assertTrue(bridge[scheduler_name]["helper"], scheduler_name)
        self.assertEqual(("第二元神",), bridge["run_second_soul_bootstrap_check"]["manifest_names"])
        self.assertEqual(("太一",), bridge["run_taiyi_bootstrap_check"]["manifest_names"])

    def test_new_tree_pulse_schedulers_are_in_runtime_order(self):
        ordinary = app.get_identity_scheduler_order_contract()["ordinary"]
        bridge = app.get_scheduler_manifest_bridge_contract()

        self.assertFalse(module_manifest.is_module_archived("灵树"))
        self.assertIn("run_tree_bootstrap_check", ordinary)
        self.assertIn("run_tree_scheduler", ordinary)
        self.assertEqual(("灵树",), bridge["run_tree_scheduler"]["manifest_names"])

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

    async def test_main_loop_runs_phaseful_pass_even_when_identity_background_is_separate(self):
        stop_event = asyncio.Event()
        seen = []

        async def fake_sleep(loop_stop_event, delay):
            seen.append(("sleep", delay))
            loop_stop_event.set()

        async def fake_phaseful(now):
            seen.append(("phaseful", now))

        def fake_start_identity(now):
            seen.append(("start_identity", now))

        async def noop_async(*_args, **_kwargs):
            return None

        with (
            patch.object(app, "gc_my_msg_ids"),
            patch.object(app, "gc_ui_login_tokens"),
            patch.object(app, "gc_ui_sessions"),
            patch.object(app, "flush_if_dirty", return_value=True),
            patch.object(app, "has_persistence_write_failure", return_value=False),
            patch.object(app, "check_bot_health_timeout"),
            patch.object(app, "should_pause_for_bot_health", return_value=False),
            patch.object(app, "get_global_enabled", return_value=True),
            patch.object(app, "run_rare_daily_report_scheduler", new=AsyncMock()),
            patch.object(app, "_run_global_schedulers", new=AsyncMock()),
            patch.object(app, "run_quiz_learning_scheduler", new=AsyncMock()),
            patch.object(app, "run_retry_scheduler", new=AsyncMock()),
            patch.object(app, "run_identity_info_followup_scheduler", new=AsyncMock()),
            patch.object(app, "_run_phaseful_identity_schedulers", new=AsyncMock(side_effect=fake_phaseful)),
            patch.object(app, "_start_identity_schedulers_if_idle", side_effect=fake_start_identity),
            patch.object(app, "_sleep_or_stop", new=AsyncMock(side_effect=fake_sleep)),
            patch.object(app.time, "time", return_value=200.0),
        ):
            await app.main_loop(stop_event)

        self.assertEqual(
            [("phaseful", 200.0), ("phaseful", 200.0), ("start_identity", 200.0), ("sleep", 5)],
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
