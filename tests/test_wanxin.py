import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard
from model import state as state_module
from model.features import wanxin


class WanxinTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=301299112, *, username="jfdffdddd", sect_name=""):
        state_module.ensure_identity_registered(identity_id)
        state_module._meta_state["identity_states"][int(identity_id)] = state_module.new_identity_state()
        state_module.set_identity_account(identity_id, identity_id)
        state_module.update_send_as_profile(identity_id, username=username, label=username, sect_name=sect_name)
        return identity_id

    def test_parse_status_panel(self):
        parsed = wanxin.parse_wanxin_text(
            "【婉心封魂】\n阶段：月殿余咒（封魂未解）\n婉心：81\n魂封：13\n月魄：2\n咒源：31\n可用：.探望南宫婉、.护持神魂。",
            now=1_800_000_000.0,
            family="wanxin_panel",
        )

        self.assertEqual("panel", parsed["type"])
        self.assertEqual("月殿余咒（封魂未解）", parsed["values"]["stage"])
        self.assertEqual(81, parsed["values"]["wanxin"])
        self.assertEqual(13, parsed["values"]["soul_seal"])
        self.assertEqual(2, parsed["values"]["moon_soul"])
        self.assertEqual(31, parsed["values"]["curse_source"])

    def test_parse_awakened_pipe_values(self):
        parsed = wanxin.parse_wanxin_text(
            "【婉影觉醒】\n【南宫婉】已觉醒为 【南宫婉·月影】。\n"
            "婉心封魂：月殿余咒（封魂未解）\n婉心 82 | 魂封 25 | 月魄 1 | 咒源 11",
            now=1_800_000_000.0,
        )

        self.assertEqual("moon_awakened", parsed["type"])
        self.assertEqual("月殿余咒（封魂未解）", parsed["values"]["stage"])
        self.assertEqual(82, parsed["values"]["wanxin"])
        self.assertEqual(25, parsed["values"]["soul_seal"])
        self.assertEqual(1, parsed["values"]["moon_soul"])
        self.assertEqual(11, parsed["values"]["curse_source"])

    def test_parse_real_wanxin_cooldown_actions(self):
        now = 1_800_000_000.0
        strip = wanxin.parse_wanxin_text(
            "咒源剥离牵涉神魂反噬，不可连续施展。剥离咒源 冷却 8 小时，请在 7小时57分钟16秒 后再试。",
            now=now,
        )
        banner = wanxin.parse_wanxin_text(
            "此咒契刚借幡镇魂过，阴煞尚未归位。借幡镇魂 冷却 6 小时，请在 5小时59分钟10秒 后再试。",
            now=now,
        )
        deduce = wanxin.parse_wanxin_text(
            "封魂咒纹变化极慢，请在 7小时2分钟37秒 后再推演。",
            now=now,
        )

        self.assertEqual(("cooldown", "strip"), (strip["type"], strip["cooldown_action"]))
        self.assertEqual(("cooldown", "banner"), (banner["type"], banner["cooldown_action"]))
        self.assertEqual(("cooldown", "deduce"), (deduce["type"], deduce["cooldown_action"]))
        self.assertGreater(strip["next_time"], now + 7 * 3600)

    def test_parse_strip_failed_real_reply(self):
        parsed = wanxin.parse_wanxin_text(
            "【剥离咒源失败】\n阴罗残咒反噬，@Weeguu 魂封 +4。\n\n"
            "阶段：阴罗咒源（封魂未解）\n婉心：83\n魂封：4\n月魄：11\n咒源：60",
            now=1_800_000_000.0,
        )

        self.assertEqual("assist_strip_failed", parsed["type"])
        self.assertEqual("Weeguu", parsed["target_username"])
        self.assertEqual(4, parsed["values"]["soul_seal"])
        self.assertEqual(60, parsed["values"]["curse_source"])

    def test_parse_strip_success_and_resource_blocked_real_replies(self):
        success = wanxin.parse_wanxin_text(
            "【剥离咒源成功】\n"
            "@sanshaoyedejian1 以阴罗幡截住咒源反噬，替 @jfdffdddd 剥下一段阴罗残咒。\n"
            "魂封 -9，咒源 +14。@jfdffdddd：【阴罗残咒】x1；"
            "@sanshaoyedejian1：【封魂残煞】x1、报酬 1 灵石、贡献 +180。\n\n"
            "阶段：玄冰丹方（封魂未解）\n婉心：120\n魂封：0\n月魄：45\n咒源：120",
            now=1_800_000_000.0,
        )
        blocked = wanxin.parse_wanxin_text(
            "你的阴罗幡煞气不足，剥离咒源至少需要 120 点煞气。",
            now=1_800_000_000.0,
        )

        self.assertEqual("assist_strip_success", success["type"])
        self.assertEqual("jfdffdddd", success["target_username"])
        self.assertEqual((9, 14, 180), (success["seal_down"], success["source_gain"], success["contrib_gain"]))
        self.assertEqual("assist_strip_resource_blocked", blocked["type"])

    def test_parse_commission_existing_and_assist_success(self):
        existing = wanxin.parse_wanxin_text("你已有进行中的解咒委托（ID: 5），不可重复发布。")
        invalid = wanxin.parse_wanxin_text("你与对方没有有效的咒契协定。需先由对方发布委托，再由你接取。")
        identify = wanxin.parse_wanxin_text("【阴罗辨咒】\n@sanshaoyedejian1 替 @jfdffdddd 锁定咒源。咒源 +20，咒师贡献 +120。")
        banner = wanxin.parse_wanxin_text("【借幡镇魂】\n@jfdffdddd 魂封 -13，月魄 +1；咒师贡献 +100。")

        self.assertEqual(("commission_existing", 5), (existing["type"], existing["commission_id"]))
        self.assertEqual("commission_invalid", invalid["type"])
        self.assertEqual(("assist_identify_success", "jfdffdddd", 20, 120), (identify["type"], identify["target_username"], identify["source_gain"], identify["contrib_gain"]))
        self.assertEqual(("assist_banner_success", "jfdffdddd", 13, 1), (banner["type"], banner["target_username"], banner["seal_down"], banner["moon_gain"]))

    def test_action_guard_resolves_wanxin_commands(self):
        self.assertEqual("wanxin_panel", action_guard.resolve_action_key(".婉心"))
        self.assertEqual("wanxin_visit", action_guard.resolve_action_key(".探望南宫婉"))
        self.assertEqual("wanxin_protect", action_guard.resolve_action_key(".护持神魂"))
        self.assertEqual("wanxin_deduce", action_guard.resolve_action_key(".推演封魂咒"))
        self.assertEqual("wanxin_commission", action_guard.resolve_action_key(".发布解咒委托 66"))
        self.assertEqual("wanxin_cancel", action_guard.resolve_action_key(".取消解咒委托"))
        self.assertEqual("wanxin_accept", action_guard.resolve_action_key(".接取解咒委托 5"))
        self.assertEqual("wanxin_assist_identify", action_guard.resolve_action_key(".辨认咒纹"))
        self.assertEqual("wanxin_assist_banner", action_guard.resolve_action_key(".借幡镇魂"))
        self.assertEqual("wanxin_assist_strip", action_guard.resolve_action_key(".剥离咒源"))
        self.assertEqual("wanxin_assist_strip", action_guard.resolve_action_key(".剥离咒源 @jfdffdddd"))
        self.assertEqual("wanxin_moon_panel", action_guard.resolve_action_key(".婉影"))
        self.assertEqual("wanxin_moon_greet", action_guard.resolve_action_key(".婉影问安"))
        self.assertEqual("wanxin_moon_seal", action_guard.resolve_action_key(".同参封魂"))
        self.assertEqual("wanxin_moon_join", action_guard.resolve_action_key(".月下合参"))

    def test_parse_real_moon_actions_and_safe_defaults(self):
        panel = wanxin.parse_wanxin_text(
            "【月影同参】\n侍妾：【南宫婉·月影】（随行中）\n情缘：142\n共鸣：已觉醒"
        )
        already = wanxin.parse_wanxin_text("今日已与婉影问安。月魄需静养，不可频繁牵动。")
        greet = wanxin.parse_wanxin_text(
            "【婉影问安】\n你与【南宫婉·月影】于月下静坐片刻。\n情缘 +9。\n"
            "婉心 +1，魂封 -1。\n婉心 115 | 魂封 0 | 月魄 38 | 咒源 120"
        )
        seal = wanxin.parse_wanxin_text(
            "【同参封魂】\n你与【南宫婉·月影】合坐月下。\n消耗：500修为、24情缘。\n"
            "魂封 -6，月魄 +2，咒源 +4。"
        )
        cooldown = wanxin.parse_wanxin_text("婉影神念尚未平复，请在 6小时15分钟49秒 后再同参封魂。")
        blocked = wanxin.parse_wanxin_text("封魂咒尚未解除，月下合参不可贸然施展。需先完成 .解除封魂咒。")
        config = wanxin.normalize_wanxin_auto_config()

        self.assertEqual(("moon_panel", 142), (panel["type"], panel["affinity"]))
        self.assertEqual("moon_greet_already", already["type"])
        self.assertEqual(("moon_greet_success", 9), (greet["type"], greet["affinity_gain"]))
        self.assertEqual(("moon_seal_success", 24), (seal["type"], seal["affinity_cost"]))
        self.assertEqual(("cooldown", "moon_seal"), (cooldown["type"], cooldown["cooldown_action"]))
        self.assertEqual("moon_join_blocked", blocked["type"])
        self.assertTrue(config["moon_greet_enabled"])
        self.assertFalse(config["moon_seal_enabled"])
        self.assertFalse(config["moon_join_enabled"])

    async def test_moon_panel_syncs_shared_affinity_for_voyage_gate(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["concubine_affinity"] = 0
            state_module.state["wanxin_observation"] = {
                "moon_awakened": True,
                "pending": {
                    "action": "moon_status",
                    "family": "wanxin_moon_panel",
                    "msg_id": 7007,
                    "send_as_id": identity_id,
                    "reply_due_at": now + 90,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【月影同参】\n侍妾：【南宫婉·月影】（随行中）\n情缘：160\n共鸣：已觉醒",
                    now,
                    reply_to=SimpleNamespace(id=7007, raw_text=".婉影"),
                    matched_family="wanxin_moon_panel",
                )

            self.assertTrue(handled)
            self.assertEqual(160, state_module.state["concubine_affinity"])

    async def test_moon_greet_reply_updates_shared_affinity_and_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["concubine_affinity"] = 120
            state_module.state["wanxin_observation"] = {
                "moon_awakened": True,
                "pending": {
                    "action": "moon_greet",
                    "family": "wanxin_moon_greet",
                    "msg_id": 7008,
                    "send_as_id": identity_id,
                    "reply_due_at": now + 90,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【婉影问安】\n你与【南宫婉·月影】于月下静坐片刻。\n情缘 +9。\n"
                    "婉心 +1，魂封 -1。\n婉心 115 | 魂封 0 | 月魄 38 | 咒源 120",
                    now,
                    reply_to=SimpleNamespace(id=7008, raw_text=".婉影问安"),
                    matched_family="wanxin_moon_greet",
                )

            self.assertTrue(handled)
            self.assertEqual(129, state_module.state["concubine_affinity"])

    async def test_moon_greet_already_reply_closes_pending_until_next_day(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["concubine_affinity"] = 142
            state_module.state["wanxin_observation"] = {
                "moon_awakened": True,
                "pending": {
                    "action": "moon_greet",
                    "family": "wanxin_moon_greet",
                    "msg_id": 7010,
                    "send_as_id": identity_id,
                    "reply_due_at": now + 90,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "今日已与婉影问安。月魄需静养，不可频繁牵动。",
                    now,
                    reply_to=SimpleNamespace(id=7010, raw_text=".婉影问安"),
                    matched_family="wanxin_moon_greet",
                )

            self.assertTrue(handled)
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertEqual(now + wanxin.WANXIN_MOON_GREET_CD_SEC + wanxin.CD_BUFFER_SEC, observed["next_moon_greet_time"])
            self.assertEqual(142, state_module.state["concubine_affinity"])
            observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertTrue(observed["moon_awakened"])
            self.assertGreater(observed["next_moon_greet_time"], now)

    def test_moon_seal_reserves_160_affinity_for_voyage(self):
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            observed = wanxin.normalize_wanxin_observation({
                "moon_awakened": True,
                "auto_config": {"moon_seal_enabled": True},
            })
            state_module.state["concubine_affinity"] = 183
            self.assertFalse(wanxin._action_enabled(observed, wanxin.WANXIN_ACTION_MOON_SEAL))
            state_module.state["concubine_affinity"] = 184
            self.assertTrue(wanxin._action_enabled(observed, wanxin.WANXIN_ACTION_MOON_SEAL))

    async def test_scheduler_default_off_does_not_send(self):
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            with patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock:
                await wanxin.run_wanxin_scheduler(1_800_000_000.0)

        send_mock.assert_not_awaited()

    async def test_scheduler_publishes_commission_with_configured_reward(self):
        identity_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_000.0
        fake_msg = SimpleNamespace(id=7001, sent_at=now)
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_config": {"reward_lingshi": 66, "publish_enabled": True, "assist_enabled": True},
                "auto_next_time": now - 1,
                "soul_seal": 5,
                "assist": {"send_as_id": helper_id},
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".发布解咒委托 66", send_mock.await_args.args[0])
            self.assertEqual("publish", state_module.state["wanxin_observation"]["pending"]["action"])

    async def test_scheduler_publishes_when_strip_cycle_is_due_even_at_zero_seal(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        state_module.update_send_as_profile(owner_id, username_aliases=["WalterWA2000"])
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_010.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "last_observed_at": now - 60,
                "soul_seal": 0,
                "curse_source": 120,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "commission": {"id": 0, "owner_username": "WalterWA2000"},
                "assist": {
                    "send_as_id": helper_id,
                    "identify_enabled": False,
                    "banner_enabled": False,
                    "strip_enabled": True,
                    "next_strip_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7002, sent_at=now))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".发布解咒委托 1", send_mock.await_args.args[0])
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(0, observed["commission"]["id"])
            self.assertEqual("publish", observed["pending"]["action"])

    async def test_scheduler_does_not_publish_when_helper_identity_is_disabled(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        state_module.set_identity_enabled(helper_id, False)
        now = 1_800_000_015.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "commission": {"id": 0, "owner_username": "WalterWA2000"},
                "assist": {"send_as_id": helper_id, "strip_enabled": True, "next_strip_time": now - 1},
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()

    async def test_scheduler_waits_to_publish_until_strip_is_due(self):
        identity_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_025.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "commission": {"id": 0, "owner_username": "jfdffdddd"},
                "assist": {
                    "send_as_id": helper_id,
                    "identify_enabled": False,
                    "banner_enabled": False,
                    "strip_enabled": True,
                    "next_strip_time": now + 8 * 3600,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now + 3600, state_module.state["wanxin_observation"]["auto_next_time"])

    async def test_scheduler_default_starts_owner_action_without_publishing(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_050.0
        fake_msg = SimpleNamespace(id=7101, sent_at=now)
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {"auto_next_time": now - 1}
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".探望南宫婉", send_mock.await_args.args[0])
            self.assertEqual("visit", state_module.state["wanxin_observation"]["pending"]["action"])

    async def test_owner_action_send_timeout_uses_conservative_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_055.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now - 1,
                "next_deduce_time": now + 3600,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(wanxin, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout", "reason": ">25s"}),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertGreaterEqual(observed["next_protect_time"], now + wanxin.WANXIN_PROTECT_CD_SEC)
            self.assertEqual(observed["next_protect_time"], observed["auto_next_time"])
            self.assertIn("状态未知", observed["auto_last_result"])

    async def test_owner_action_send_exception_uses_conservative_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_055.5
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now - 1,
                "next_deduce_time": now + 3600,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(wanxin, "classify_game_send_block", return_value={"status": "unknown", "code": "send_exception", "reason": "rpc error"}),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertGreaterEqual(observed["next_protect_time"], now + wanxin.WANXIN_PROTECT_CD_SEC)
            self.assertEqual(observed["next_protect_time"], observed["auto_next_time"])
            self.assertIn("状态未知", observed["auto_last_result"])

    async def test_owner_action_queue_timeout_keeps_short_retry(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_056.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now - 1,
                "next_deduce_time": now + 3600,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(wanxin, "classify_game_send_block", return_value={"status": "unsent", "code": "send_queue_timeout", "reason": ">60s"}),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertEqual(now - 1, observed["next_protect_time"])
            self.assertLessEqual(observed["auto_next_time"], now + 15 * 60)
            self.assertIn(">60s", observed["auto_last_error"])

    async def test_action_guard_block_does_not_apply_action_cooldown(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_057.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd", "helper_username": "sanshaoyedejian1"},
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 8101,
                    "last_anchor_at": now,
                    "identify_enabled": True,
                    "banner_enabled": True,
                    "strip_enabled": False,
                    "next_identify_time": now - 1,
                    "next_banner_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                patch.object(wanxin, "classify_game_send_block", return_value={"status": "unsent", "code": "action_guard", "reason": "本轮已发送"}),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertEqual(now - 1, observed["assist"]["next_identify_time"])
            self.assertLessEqual(observed["auto_next_time"], now + 15 * 60)
            self.assertIn("本轮已发送", observed["auto_last_error"])

    async def test_assist_send_timeout_recovers_real_success_from_message_log(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_058.0
        fake_now = now + 30
        with tempfile.TemporaryDirectory() as tmpdir:
            day = datetime.fromtimestamp(now + 2, wanxin.TZ_LOCAL).date().isoformat()
            log_path = Path(tmpdir) / f"{day}.log"
            log_path.write_text(
                json.dumps(
                    {
                        "ts": datetime.fromtimestamp(now + 2, wanxin.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
                        "event_type": "message",
                        "message_id": 8202,
                        "text": "【借幡镇魂】\n@sanshaoyedejian1 借阴罗幡压住封魂咒反扑。\n@jfdffdddd 魂封 -15，月魄 +1；咒师贡献 +100。\n\n阶段：阴罗咒源（封魂未解）\n婉心：87\n魂封：0\n月魄：4\n咒源：89",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with state_module.use_identity(owner_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "auto_next_time": now - 1,
                    "soul_seal": 15,
                    "next_visit_time": now + 3600,
                    "next_protect_time": now + 3600,
                    "next_deduce_time": now + 3600,
                    "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd", "helper_username": "sanshaoyedejian1"},
                    "assist": {
                        "send_as_id": helper_id,
                        "last_anchor_msg_id": 8201,
                        "last_anchor_at": now,
                        "identify_enabled": True,
                        "banner_enabled": True,
                        "strip_enabled": False,
                        "identified_commission_id": 5,
                        "next_identify_time": now + 3600,
                        "next_banner_time": now - 1,
                    },
                }
                with (
                    patch.object(wanxin, "MESSAGES_DIR", tmpdir),
                    patch.object(wanxin.time, "time", return_value=fake_now),
                    patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                    patch.object(wanxin, "classify_game_send_block", return_value={"status": "unknown", "code": "send_timeout", "reason": ">25s"}),
                    patch.object(wanxin, "save_state"),
                ):
                    await wanxin.run_wanxin_scheduler(now)

                send_mock.assert_awaited_once()
                observed = state_module.state["wanxin_observation"]
                self.assertEqual("banner", observed["assist"]["last_action"])
                self.assertEqual("借幡镇魂成功", observed["assist"]["last_result"])
                self.assertEqual("", observed["auto_last_error"])
                self.assertEqual(0, observed["soul_seal"])
                self.assertEqual(4, observed["moon_soul"])
                self.assertGreater(observed["assist"]["next_banner_time"], now + wanxin.WANXIN_BANNER_CD_SEC)

    async def test_scheduler_yinluo_assist_identity_waits_without_owner_action(self):
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_060.0
        with state_module.use_identity(helper_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {"auto_next_time": now - 1}
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertIn("阴罗协助身份", state_module.state["wanxin_observation"]["auto_last_result"])

    async def test_phaseful_cleanup_only_clears_expired_pending(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_070.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "protect",
                    "family": "wanxin_protect",
                    "msg_id": 7201,
                    "send_as_id": identity_id,
                    "reply_due_at": now - 1,
                },
                "auto_next_time": now - 1,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "close_action_guard_by_family") as close_guard_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_phaseful_cleanup_scheduler(now)

            send_mock.assert_not_awaited()
            close_guard_mock.assert_called_once_with("wanxin_protect", send_as_id=identity_id, reason="wanxin_timeout", now=now)
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertIn("护持神魂 回复超时", observed["auto_last_error"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["next_protect_time"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["auto_next_time"])

    async def test_phaseful_cleanup_recovers_deduce_reply_before_timeout_cleanup(self):
        identity_id = self._prepare_identity(301299112, username="jfdffdddd")
        now = datetime(2026, 7, 6, 13, 49, tzinfo=wanxin.TZ_LOCAL).timestamp()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-06.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2026-07-06 13:46:51 UTC+8",
                            "event_type": "sent",
                            "message_id": 11533841,
                            "chat_id": state_module.get_game_group_id(),
                            "sender_id": identity_id,
                            "reply_to_msg_id": 0,
                            "text": ".推演封魂咒",
                            "family": "wanxin_deduce",
                        },
                        {
                            "ts": "2026-07-06 13:46:53 UTC+8",
                            "event_type": "message",
                            "message_id": 11533842,
                            "chat_id": state_module.get_game_group_id(),
                            "sender_id": 8609885831,
                            "reply_to_msg_id": 11533841,
                            "text": (
                                "【推演封魂咒】\n"
                                "你沿着素女禁纹反推咒源，隐约看见阴罗秘咒的残痕。\n"
                                "咒源 +16。\n\n"
                                "阶段：玄冰丹方（封魂未解）\n"
                                "婉心：92\n"
                                "魂封：0\n"
                                "月魄：14\n"
                                "咒源：120"
                            ),
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "pending": {
                        "action": "deduce",
                        "family": "wanxin_deduce",
                        "msg_id": 11533841,
                        "send_as_id": identity_id,
                        "reply_due_at": now - 1,
                    },
                    "auto_next_time": now - 1,
                    "next_deduce_time": now - 1,
                }
                with (
                    patch("model.message_log_recovery.MESSAGES_DIR", tmpdir),
                    patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(wanxin, "close_action_guard_by_family") as close_guard_mock,
                    patch.object(wanxin, "save_state"),
                ):
                    await wanxin.run_wanxin_phaseful_cleanup_scheduler(now)

                send_mock.assert_not_awaited()
                close_guard_mock.assert_called_once()
                self.assertEqual("wanxin_deduce", close_guard_mock.call_args.args[0])
                self.assertEqual(identity_id, close_guard_mock.call_args.kwargs["send_as_id"])
                self.assertEqual("wanxin_reply", close_guard_mock.call_args.kwargs["reason"])
                self.assertLess(close_guard_mock.call_args.kwargs["now"], now)
                observed = state_module.state["wanxin_observation"]
                self.assertEqual({}, observed["pending"])
                self.assertEqual("玄冰丹方（封魂未解）", observed["stage"])
                self.assertEqual(92, observed["wanxin"])
                self.assertEqual(0, observed["soul_seal"])
                self.assertEqual(14, observed["moon_soul"])
                self.assertEqual(120, observed["curse_source"])
                self.assertEqual("推演成功", observed["auto_last_result"])
                self.assertEqual("", observed["auto_last_error"])
                self.assertGreater(observed["next_deduce_time"], now + 7 * 3600)

    async def test_global_cleanup_clears_expired_pending_across_identities(self):
        first_id = self._prepare_identity(301299112, username="jfdffdddd")
        second_id = self._prepare_identity(8659059191, username="WalterWA2000")
        now = 1_800_000_080.0
        for identity_id, msg_id in ((first_id, 7301), (second_id, 7302)):
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "pending": {
                        "action": "protect",
                        "family": "wanxin_protect",
                        "msg_id": msg_id,
                        "reply_due_at": now - 1,
                    },
                    "auto_next_time": now - 1,
                }

        with (
            patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(wanxin, "save_state"),
        ):
            await wanxin.run_wanxin_global_cleanup_scheduler(now)

        send_mock.assert_not_awaited()
        for identity_id in (first_id, second_id):
            with state_module.use_identity(identity_id):
                observed = state_module.state["wanxin_observation"]
                self.assertEqual({}, observed["pending"])
                self.assertIn("护持神魂 回复超时", observed["auto_last_error"])
                self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["next_protect_time"])
                self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["auto_next_time"])

    async def test_scheduler_stops_after_pending_timeout_without_next_send(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_090.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "protect",
                    "family": "wanxin_protect",
                    "msg_id": 7401,
                    "send_as_id": identity_id,
                    "reply_due_at": now - 1,
                },
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now - 1,
                "next_deduce_time": now - 1,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "close_action_guard_by_family"),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["next_protect_time"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["auto_next_time"])

    async def test_deduce_pending_timeout_uses_short_backoff_not_full_cooldown(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_091.0
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "deduce",
                    "family": "wanxin_deduce",
                    "msg_id": 7402,
                    "send_as_id": identity_id,
                    "reply_due_at": now - 1,
                },
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now - 1,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "close_action_guard_by_family"),
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()
            observed = state_module.state["wanxin_observation"]
            self.assertEqual({}, observed["pending"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["next_deduce_time"])
            self.assertEqual(now + wanxin.WANXIN_RECOVERY_RETRY_SEC, observed["auto_next_time"])
            self.assertIn("未按技能冷却锁定", observed["auto_last_error"])

    async def test_scheduler_recovers_owner_pending_reply_from_message_log(self):
        identity_id = self._prepare_identity()
        now = datetime(2026, 7, 4, 6, 50, tzinfo=wanxin.TZ_LOCAL).timestamp()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-04.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2026-07-04 06:49:40 UTC+8",
                            "event_type": "message",
                            "message_id": 7401,
                            "chat_id": state_module.get_game_group_id(),
                            "sender_id": identity_id,
                            "reply_to_msg_id": 0,
                            "text": ".探望南宫婉",
                        },
                        {
                            "ts": "2026-07-04 06:49:42 UTC+8",
                            "event_type": "message",
                            "message_id": 7402,
                            "chat_id": state_module.get_game_group_id(),
                            "sender_id": 8609885831,
                            "reply_to_msg_id": 7401,
                            "text": "你探望南宫婉，婉心微动，封魂稍缓。",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "pending": {
                        "action": "visit",
                        "family": "wanxin_visit",
                        "msg_id": 7401,
                        "send_as_id": identity_id,
                        "reply_due_at": now - 1,
                    },
                    "auto_next_time": now - 1,
                    "next_visit_time": now - 1,
                    "next_protect_time": now + 3600,
                    "next_deduce_time": now + 3600,
                }
                with (
                    patch("model.message_log_recovery.MESSAGES_DIR", tmpdir),
                    patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(wanxin, "send_audit_log", new=AsyncMock()),
                    patch.object(wanxin, "save_state"),
                ):
                    await wanxin.run_wanxin_scheduler(now)

                send_mock.assert_not_awaited()
                observed = state_module.state["wanxin_observation"]
                self.assertEqual({}, observed["pending"])
                self.assertEqual("探望成功", observed["auto_last_result"])
                self.assertEqual("", observed["auto_last_error"])

    async def test_scheduler_accepts_commission_as_yinluo_helper(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_100.0
        fake_msg = SimpleNamespace(id=7002, sent_at=now)
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "commission": {"id": 5, "accepted": False, "owner_username": "jfdffdddd"},
                "assist": {"send_as_id": helper_id},
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".接取解咒委托 5", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])

    async def test_accept_contract_recovery_uses_reply_anchor_across_listener_and_aliases(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        listener_id = self._prepare_identity(301299112, username="jfdffdddd")
        now = 1_800_000_300.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "commission": {
                    "id": 99,
                    "accepted": False,
                    "owner_username": "WalterWA2000",
                },
                "assist": {"send_as_id": helper_id},
                "pending": {
                    "action": "accept",
                    "family": "wanxin_accept",
                    "msg_id": 169757,
                    "send_as_id": helper_id,
                    "reply_due_at": now + 60,
                },
            }

        with state_module.use_identity(listener_id), patch.object(wanxin, "save_state"), patch.object(
            wanxin, "close_action_guard_by_family"
        ) as close_guard:
            handled = await wanxin.handle_wanxin_reply(
                "【咒契协定已成】\n阴罗宗弟子 @sanshaoyedejian1 已接取 @WalterWA2000 的解咒委托。",
                now,
                reply_to=SimpleNamespace(id=169757, raw_text=".接取解咒委托 99"),
                matched_family="wanxin_accept",
                result_msg_id=169760,
            )

        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertTrue(observed["commission"]["accepted"])
            self.assertEqual(169760, observed["commission"]["accept_msg_id"])
            self.assertEqual({}, observed["pending"])
        self.assertIn(helper_id, [call.kwargs.get("send_as_id") for call in close_guard.call_args_list])

    async def test_external_helper_claim_waits_until_24h_cancel_boundary(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        state_module.update_send_as_profile(owner_id, username_aliases=["WalterWA2000"])
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        listener_id = self._prepare_identity(301299112, username="jfdffdddd")
        published_at = 1_800_000_000.0
        accepted_at = published_at + 3 * 3600
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "commission": {
                    "id": 144,
                    "published_at": published_at,
                    "owner_username": "WalterWA2000",
                },
                "assist": {"send_as_id": helper_id},
            }

        with state_module.use_identity(listener_id), patch.object(wanxin, "save_state"):
            handled = await wanxin.handle_wanxin_reply(
                "【咒契协定已成】\n阴罗宗弟子 @DaxCph 已接取 @WalterWA2000 的解咒委托。",
                accepted_at,
                matched_family="wanxin_accept",
                result_msg_id=217964,
            )

        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertTrue(observed["commission"]["claimed_elsewhere"])
            self.assertEqual("DaxCph", observed["commission"]["claim_helper_username"])
            self.assertEqual(
                published_at + wanxin.WANXIN_COMMISSION_TTL_SEC + wanxin.CD_BUFFER_SEC,
                observed["commission"]["cancel_due_at"],
            )
            self.assertEqual(observed["commission"]["cancel_due_at"], observed["auto_next_time"])

            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(observed["commission"]["cancel_due_at"] - 1)
            send_mock.assert_not_awaited()

    async def test_claimed_commission_cancels_after_24h_then_can_republish(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        published_at = 1_800_000_000.0
        due_at = published_at + wanxin.WANXIN_COMMISSION_TTL_SEC + wanxin.CD_BUFFER_SEC
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": due_at,
                "next_visit_time": due_at + 3600,
                "next_protect_time": due_at + 3600,
                "next_deduce_time": due_at + 3600,
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "commission": {
                    "id": 144,
                    "published_at": published_at,
                    "owner_username": "WalterWA2000",
                    "claimed_elsewhere": True,
                    "cancel_due_at": due_at,
                },
                "assist": {"send_as_id": helper_id, "strip_enabled": True, "next_strip_time": published_at},
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7601, sent_at=due_at))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(due_at)
            self.assertEqual(".取消解咒委托", send_mock.await_args.args[0])
            self.assertEqual("cancel", state_module.state["wanxin_observation"]["pending"]["action"])

            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "解咒委托已取消，已退回 1 灵石。",
                    due_at + 1,
                    reply_to=SimpleNamespace(id=7601, raw_text=".取消解咒委托"),
                    matched_family="wanxin_cancel",
                    result_msg_id=7602,
                )
            self.assertTrue(handled)
            observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertEqual(0, observed["commission"]["id"])
            self.assertFalse(observed["commission"]["claimed_elsewhere"])

    async def test_claimed_commission_completed_by_external_helper_is_not_cancelled(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        state_module.update_send_as_profile(owner_id, username_aliases=["WalterWA2000"])
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        published_at = datetime(2026, 7, 17, 8, 8, 53, tzinfo=wanxin.TZ_LOCAL).timestamp()
        due_at = published_at + wanxin.WANXIN_COMMISSION_TTL_SEC + wanxin.CD_BUFFER_SEC
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-17.log"
            log_path.write_text(
                json.dumps(
                    {
                        "ts": "2026-07-17 11:14:10 UTC+8",
                        "event_type": "message",
                        "message_id": 218120,
                        "text": "【剥离咒源成功】\n"
                        "@DaxCph 以阴罗幡截住咒源反噬，替 @WalterWA2000 剥下一段阴罗残咒。\n"
                        "魂封 -8，咒源 +14。\n\n阶段：玄冰丹方（封魂未解）\n"
                        "婉心：120\n魂封：0\n月魄：52\n咒源：120",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with state_module.use_identity(owner_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "auto_next_time": due_at,
                    "next_visit_time": due_at + 3600,
                    "next_protect_time": due_at + 3600,
                    "next_deduce_time": due_at + 3600,
                    "auto_config": {"publish_enabled": True, "assist_enabled": True},
                    "commission": {
                        "id": 145,
                        "published_at": published_at,
                        "owner_username": "WalterWA2000",
                        "claimed_elsewhere": True,
                        "claim_helper_username": "DaxCph",
                        "cancel_due_at": due_at,
                    },
                    "assist": {"send_as_id": helper_id, "strip_enabled": True},
                }
                with (
                    patch.object(wanxin, "MESSAGES_DIR", tmpdir),
                    patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(wanxin, "save_state"),
                ):
                    await wanxin.run_wanxin_scheduler(due_at)

                send_mock.assert_not_awaited()
                observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
                self.assertEqual(0, observed["commission"]["id"])
                self.assertFalse(observed["commission"]["claimed_elsewhere"])
                self.assertEqual("外部咒师已完成剥离，委托已结清", observed["auto_last_result"])
                self.assertEqual(0, observed["soul_seal"])
                self.assertEqual(52, observed["moon_soul"])

    async def test_no_cancelable_commission_reply_clears_stale_contract(self):
        owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        now = 1_800_000_400.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "cancel",
                    "family": "wanxin_cancel",
                    "msg_id": 248154,
                    "send_as_id": owner_id,
                    "reply_due_at": now + 90,
                },
                "commission": {
                    "id": 145,
                    "published_at": now - wanxin.WANXIN_COMMISSION_TTL_SEC,
                    "owner_username": "WalterWA20000",
                    "claimed_elsewhere": True,
                    "cancel_due_at": now,
                    "cancel_msg_id": 248154,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "你当前没有可取消的解咒委托。",
                    now,
                    reply_to=SimpleNamespace(id=248154, raw_text=".取消解咒委托"),
                    matched_family="wanxin_cancel",
                    result_msg_id=248155,
                )

            self.assertTrue(handled)
            observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertEqual(0, observed["commission"]["id"])
            self.assertFalse(observed["commission"]["claimed_elsewhere"])
            self.assertEqual({}, observed["pending"])
            self.assertEqual("当前无可取消委托", observed["auto_last_result"])
            self.assertEqual("", observed["auto_last_error"])

    async def test_scheduler_targets_identify_with_owner_mention(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_200.0
        fake_msg = SimpleNamespace(id=7003, sent_at=now)
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd", "helper_username": "sanshaoyedejian1"},
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 11405889,
                    "last_anchor_at": now,
                    "identify_enabled": True,
                    "banner_enabled": True,
                    "strip_enabled": False,
                    "next_identify_time": now - 1,
                    "next_banner_time": now + 3600,
                    "next_strip_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".辨认咒纹 @jfdffdddd", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])
            self.assertNotIn("reply_to", send_mock.await_args.kwargs)

    async def test_scheduler_sends_strip_with_owner_mention_without_reply_anchor(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_225.0
        fake_msg = SimpleNamespace(id=7004, sent_at=now)
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "soul_seal": 5,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "auto_config": {"assist_enabled": True},
                "commission": {
                    "id": 88,
                    "accepted": True,
                    "accepted_at": now - 60,
                    "owner_username": "jfdffdddd",
                    "helper_username": "sanshaoyedejian1",
                },
                "assist": {
                    "send_as_id": helper_id,
                    "identify_enabled": False,
                    "banner_enabled": False,
                    "strip_enabled": True,
                    "next_strip_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".剥离咒源 @jfdffdddd", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])
            self.assertNotIn("reply_to", send_mock.await_args.kwargs)
            self.assertEqual(0, state_module.state["wanxin_observation"]["pending"]["reply_to_msg_id"])

    async def test_commission_accept_and_targeted_strip_end_to_end(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_240.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "soul_seal": 5,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "auto_config": {"publish_enabled": True, "assist_enabled": True, "reward_lingshi": 1},
                "commission": {"id": 0, "owner_username": "jfdffdddd"},
                "assist": {
                    "send_as_id": helper_id,
                    "identify_enabled": False,
                    "banner_enabled": False,
                    "strip_enabled": True,
                    "next_strip_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7501, sent_at=now))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)
            self.assertEqual(".发布解咒委托 1", send_mock.await_args.args[0])

            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【解咒委托已发布】\n委托 ID：88\n报酬：1 灵石\n"
                    "阴罗宗玩家可用 .接取解咒委托 88 接取。",
                    now + 1,
                    reply_to=SimpleNamespace(id=7501, raw_text=".发布解咒委托 1"),
                    matched_family="wanxin_commission",
                    result_msg_id=7502,
                )
            self.assertTrue(handled)

            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7503, sent_at=now + 2))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now + 2)
            self.assertEqual(".接取解咒委托 88", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【咒契协定已成】\n阴罗宗弟子 @sanshaoyedejian1 已接取 "
                    "@jfdffdddd 的解咒委托。",
                    now + 3,
                    reply_to=SimpleNamespace(id=7503, raw_text=".接取解咒委托 88"),
                    matched_family="wanxin_accept",
                    result_msg_id=7504,
                )
        self.assertTrue(handled)

        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            observed["next_protect_time"] = now - 1
            state_module.state["wanxin_observation"] = observed
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7505, sent_at=now + 24))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now + 24)
            self.assertEqual(".剥离咒源 @jfdffdddd", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])
            self.assertNotIn("reply_to", send_mock.await_args.kwargs)

    async def test_strip_success_consumes_only_target_contract_and_sets_target_cooldown(self):
        owner_id = self._prepare_identity()
        other_owner_id = self._prepare_identity(7538826434, username="WalterWA2000")
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_275.0
        for identity_id, username, commission_id, msg_id in (
            (owner_id, "jfdffdddd", 88, 7101),
            (other_owner_id, "WalterWA2000", 89, 7201),
        ):
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "pending": {
                        "action": "strip",
                        "family": "wanxin_assist_strip",
                        "msg_id": msg_id,
                        "send_as_id": helper_id,
                        "reply_due_at": now + 60,
                    },
                    "commission": {
                        "id": commission_id,
                        "accepted": True,
                        "accepted_at": now - 60,
                        "owner_username": username,
                        "helper_username": "sanshaoyedejian1",
                    },
                    "assist": {"send_as_id": helper_id, "strip_enabled": True, "next_strip_time": 0},
                }

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【剥离咒源成功】\n"
                    "@sanshaoyedejian1 以阴罗幡截住咒源反噬，替 @jfdffdddd 剥下一段阴罗残咒。\n"
                    "魂封 -9，咒源 +14。@sanshaoyedejian1：报酬 1 灵石、贡献 +180。",
                    now,
                    reply_to=SimpleNamespace(id=7101, raw_text=".剥离咒源 @jfdffdddd"),
                    matched_family="wanxin_assist_strip",
                    result_msg_id=7102,
                )

        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(0, observed["commission"]["id"])
            self.assertFalse(observed["commission"]["accepted"])
            self.assertEqual("jfdffdddd", observed["commission"]["owner_username"])
            self.assertGreater(observed["assist"]["next_strip_time"], now + wanxin.WANXIN_STRIP_CD_SEC)
        with state_module.use_identity(other_owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(89, observed["commission"]["id"])
            self.assertTrue(observed["commission"]["accepted"])
            self.assertEqual(0, observed["assist"]["next_strip_time"])

    async def test_strip_failure_consumes_contract_but_resource_block_keeps_it(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_300.0

        def prepare(msg_id):
            with state_module.use_identity(owner_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "pending": {
                        "action": "strip",
                        "family": "wanxin_assist_strip",
                        "msg_id": msg_id,
                        "send_as_id": helper_id,
                        "reply_due_at": now + 60,
                    },
                    "commission": {
                        "id": 88,
                        "accepted": True,
                        "accepted_at": now - 60,
                        "owner_username": "jfdffdddd",
                        "helper_username": "sanshaoyedejian1",
                    },
                    "assist": {"send_as_id": helper_id, "strip_enabled": True, "next_strip_time": 0},
                }

        prepare(7301)
        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【剥离咒源失败】\n封魂咒骤然反扑，阴罗幡煞气被吞去 120 点，"
                    "@sanshaoyedejian1 修为折损 500，@jfdffdddd 魂封 +4。",
                    now,
                    reply_to=SimpleNamespace(id=7301, raw_text=".剥离咒源 @jfdffdddd"),
                    matched_family="wanxin_assist_strip",
                    result_msg_id=7302,
                )
        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(0, observed["commission"]["id"])
            self.assertFalse(observed["commission"]["accepted"])
            self.assertGreater(observed["assist"]["next_strip_time"], now + wanxin.WANXIN_STRIP_CD_SEC)

        prepare(7401)
        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "你的阴罗幡煞气不足，剥离咒源至少需要 120 点煞气。",
                    now,
                    reply_to=SimpleNamespace(id=7401, raw_text=".剥离咒源 @jfdffdddd"),
                    matched_family="wanxin_assist_strip",
                    result_msg_id=7402,
                )
        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(88, observed["commission"]["id"])
            self.assertTrue(observed["commission"]["accepted"])
            self.assertEqual(now + wanxin.WANXIN_STRIP_RESOURCE_BACKOFF_SEC, observed["assist"]["next_strip_time"])

    async def test_scheduler_refuses_assist_without_real_accept_evidence(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_250.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 11405889,
                    "last_anchor_at": now,
                    "identify_enabled": True,
                    "banner_enabled": True,
                    "next_identify_time": now - 1,
                },
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_not_awaited()
            observed = state_module.state["wanxin_observation"]
            self.assertFalse(observed["commission"]["accepted"])
            self.assertIn("真实接取证据", observed["auto_last_error"])
            self.assertEqual(now, observed["auto_next_time"])

    async def test_owner_reply_records_assist_anchor(self):
        owner_id = self._prepare_identity()
        now = 1_800_000_300.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "visit",
                    "family": "wanxin_visit",
                    "msg_id": 7004,
                    "reply_due_at": now + 60,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "探望南宫婉后，婉心微明，魂封略有松动。",
                    now,
                    reply_to=SimpleNamespace(id=7004, raw_text=".探望南宫婉"),
                    matched_family="wanxin_visit",
                    result_msg_id=7104,
                )

            self.assertTrue(handled)
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(7004, observed["assist"]["last_anchor_msg_id"])
            self.assertEqual({}, observed["pending"])

    async def test_owner_cooldown_reply_does_not_starve_due_assist(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_400.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "deduce",
                    "family": "wanxin_deduce",
                    "msg_id": 8001,
                    "send_as_id": owner_id,
                    "reply_due_at": now + 60,
                },
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd", "helper_username": "sanshaoyedejian1"},
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 8001,
                    "last_anchor_at": now,
                    "identify_enabled": True,
                    "banner_enabled": True,
                    "strip_enabled": False,
                    "next_identify_time": now - 1,
                    "next_banner_time": now - 1,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "推演封魂咒 冷却 8 小时，请在 7小时59分钟 后再试。",
                    now,
                    reply_to=SimpleNamespace(id=8001, raw_text=".推演封魂咒"),
                    matched_family="wanxin_deduce",
                    result_msg_id=8002,
                )

            self.assertTrue(handled)
            observed = state_module.state["wanxin_observation"]
            self.assertGreater(observed["next_deduce_time"], now + 7 * 3600)
            self.assertLessEqual(observed["auto_next_time"], now + wanxin.WANXIN_CHAIN_STEP_SEC)

    async def test_assist_cooldown_reply_sets_specific_action(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_500.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "pending": {
                    "action": "banner",
                    "family": "wanxin_assist_banner",
                    "msg_id": 8005,
                    "send_as_id": helper_id,
                    "reply_due_at": now + 60,
                },
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd", "helper_username": "sanshaoyedejian1"},
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 8001,
                    "last_anchor_at": now,
                    "next_banner_time": now - 1,
                },
            }
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "此咒契刚借幡镇魂过，阴煞尚未归位。借幡镇魂 冷却 6 小时，请在 5小时59分钟10秒 后再试。",
                    now,
                    reply_to=SimpleNamespace(id=8005, raw_text=".借幡镇魂"),
                    matched_family="wanxin_assist_banner",
                    result_msg_id=8006,
                )

            self.assertTrue(handled)
            observed = state_module.state["wanxin_observation"]
            self.assertGreater(observed["assist"]["next_banner_time"], now + 5 * 3600)
            self.assertEqual({}, observed["pending"])

    async def test_invalid_commission_reply_clears_stale_contract(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_000_600.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "pending": {
                    "action": "banner",
                    "family": "wanxin_assist_banner",
                    "msg_id": 8005,
                    "send_as_id": helper_id,
                    "reply_due_at": now + 60,
                },
                "commission": {
                    "id": 5,
                    "accepted": True,
                    "owner_username": "jfdffdddd",
                    "helper_username": "sanshaoyedejian1",
                    "publish_msg_id": 7001,
                    "accepted_at": now - 3600,
                },
                "assist": {
                    "send_as_id": helper_id,
                    "last_anchor_msg_id": 8001,
                    "last_anchor_at": now,
                },
            }
        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "你与对方没有有效的咒契协定。需先由对方发布委托，再由你接取。",
                    now,
                    reply_to=SimpleNamespace(id=8005, raw_text=".借幡镇魂"),
                    matched_family="wanxin_assist_banner",
                    result_msg_id=8006,
                )

        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(0, observed["commission"]["id"])
            self.assertFalse(observed["commission"]["accepted"])
            self.assertEqual("", observed["commission"]["helper_username"])
            self.assertEqual(0, observed["assist"]["last_anchor_msg_id"])
            self.assertEqual({}, observed["pending"])
            self.assertLessEqual(observed["auto_next_time"], now + wanxin.WANXIN_CHAIN_STEP_SEC)

    async def test_accepted_commission_runs_targeted_identify_banner_strip_in_order(self):
        owner_id = self._prepare_identity()
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_001_000.0
        with state_module.use_identity(owner_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_next_time": now - 1,
                "next_visit_time": now + 3600,
                "next_protect_time": now + 3600,
                "next_deduce_time": now + 3600,
                "auto_config": {"publish_enabled": True, "assist_enabled": True},
                "commission": {
                    "id": 88,
                    "accepted": True,
                    "accepted_at": now - 60,
                    "owner_username": "jfdffdddd",
                    "helper_username": "sanshaoyedejian1",
                },
                "assist": {
                    "send_as_id": helper_id,
                    "identify_enabled": True,
                    "banner_enabled": True,
                    "strip_enabled": True,
                },
            }

            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=now))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)
            self.assertEqual(".辨认咒纹 @jfdffdddd", send_mock.await_args.args[0])

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【阴罗辨咒】\n@sanshaoyedejian1 替 @jfdffdddd 锁定咒源。咒源 +20，咒师贡献 +120。",
                    now + 1,
                    reply_to=SimpleNamespace(id=9001, raw_text=".辨认咒纹 @jfdffdddd"),
                    matched_family="wanxin_assist_identify",
                    result_msg_id=9002,
                )
        self.assertTrue(handled)

        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(88, observed["assist"]["identified_commission_id"])
            observed["auto_next_time"] = now + 2
            state_module.state["wanxin_observation"] = observed
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9003, sent_at=now + 2))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now + 2)
            self.assertEqual(".借幡镇魂 @jfdffdddd", send_mock.await_args.args[0])

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【借幡镇魂】\n@sanshaoyedejian1 借阴罗幡压住封魂咒反扑。\n@jfdffdddd 魂封 -12，月魄 +1；咒师贡献 +100。",
                    now + 3,
                    reply_to=SimpleNamespace(id=9003, raw_text=".借幡镇魂 @jfdffdddd"),
                    matched_family="wanxin_assist_banner",
                    result_msg_id=9004,
                )
        self.assertTrue(handled)

        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(88, observed["assist"]["bannered_commission_id"])
            observed["auto_next_time"] = now + 4
            state_module.state["wanxin_observation"] = observed
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9005, sent_at=now + 4))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now + 4)
            self.assertEqual(".剥离咒源 @jfdffdddd", send_mock.await_args.args[0])

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【剥离咒源成功】\n@sanshaoyedejian1 替 @jfdffdddd 剥下一段阴罗残咒。\n魂封 -8，咒源 +12；贡献 +180。",
                    now + 5,
                    reply_to=SimpleNamespace(id=9005, raw_text=".剥离咒源 @jfdffdddd"),
                    matched_family="wanxin_assist_strip",
                    result_msg_id=9006,
                )
        self.assertTrue(handled)
        with state_module.use_identity(owner_id):
            observed = state_module.state["wanxin_observation"]
            self.assertEqual(0, observed["commission"]["id"])
            self.assertGreater(observed["assist"]["next_strip_time"], now + 7 * 3600)

    async def test_helper_cooldown_is_scoped_to_wanxin_owner_contract(self):
        owner_id = self._prepare_identity()
        other_owner_id = self._prepare_identity(8659059191, username="WalterWA20000")
        helper_id = self._prepare_identity(3907536807, username="sanshaoyedejian1", sect_name="阴罗宗")
        now = 1_800_002_000.0
        for identity_id, username, commission_id in (
            (owner_id, "jfdffdddd", 91),
            (other_owner_id, "WalterWA2000", 92),
        ):
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_enabled"] = True
                state_module.state["wanxin_observation"] = {
                    "auto_next_time": now - 1,
                    "next_visit_time": now + 3600,
                    "next_protect_time": now + 3600,
                    "next_deduce_time": now + 3600,
                    "auto_config": {"publish_enabled": True, "assist_enabled": True},
                    "commission": {
                        "id": commission_id,
                        "accepted": True,
                        "owner_username": username,
                        "helper_username": "sanshaoyedejian1",
                    },
                    "assist": {
                        "send_as_id": helper_id,
                        "identify_enabled": True,
                        "banner_enabled": True,
                        "strip_enabled": True,
                    },
                }

        with state_module.use_identity(helper_id):
            with patch.object(wanxin, "save_state"):
                handled = await wanxin.handle_wanxin_reply(
                    "【阴罗辨咒】\n@sanshaoyedejian1 替 @jfdffdddd 锁定咒源。咒源 +20，咒师贡献 +120。",
                    now,
                    matched_family="wanxin_assist_identify",
                    result_msg_id=9101,
                )
        self.assertTrue(handled)

        with state_module.use_identity(other_owner_id):
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9102, sent_at=now + 1))) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now + 1)
            send_mock.assert_awaited_once()
            self.assertEqual(".辨认咒纹 @WalterWA20000", send_mock.await_args.args[0])
        with state_module.use_identity(owner_id):
            owner_observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertGreater(owner_observed["assist"]["next_identify_time"], now + 3 * 3600)
        with state_module.use_identity(helper_id):
            helper_observed = wanxin.normalize_wanxin_observation(state_module.state["wanxin_observation"])
            self.assertEqual(0, helper_observed["assist"]["helper_next_identify_time"])

if __name__ == "__main__":
    unittest.main()
