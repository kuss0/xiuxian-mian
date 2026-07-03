import copy
import sys
import unittest
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

    def test_parse_commission_existing_and_assist_success(self):
        existing = wanxin.parse_wanxin_text("你已有进行中的解咒委托（ID: 5），不可重复发布。")
        identify = wanxin.parse_wanxin_text("【阴罗辨咒】\n@sanshaoyedejian1 替 @jfdffdddd 锁定咒源。咒源 +20，咒师贡献 +120。")
        banner = wanxin.parse_wanxin_text("【借幡镇魂】\n@jfdffdddd 魂封 -13，月魄 +1；咒师贡献 +100。")

        self.assertEqual(("commission_existing", 5), (existing["type"], existing["commission_id"]))
        self.assertEqual(("assist_identify_success", "jfdffdddd", 20, 120), (identify["type"], identify["target_username"], identify["source_gain"], identify["contrib_gain"]))
        self.assertEqual(("assist_banner_success", "jfdffdddd", 13, 1), (banner["type"], banner["target_username"], banner["seal_down"], banner["moon_gain"]))

    def test_action_guard_resolves_wanxin_commands(self):
        self.assertEqual("wanxin_panel", action_guard.resolve_action_key(".婉心"))
        self.assertEqual("wanxin_visit", action_guard.resolve_action_key(".探望南宫婉"))
        self.assertEqual("wanxin_protect", action_guard.resolve_action_key(".护持神魂"))
        self.assertEqual("wanxin_deduce", action_guard.resolve_action_key(".推演封魂咒"))
        self.assertEqual("wanxin_commission", action_guard.resolve_action_key(".发布解咒委托 66"))
        self.assertEqual("wanxin_accept", action_guard.resolve_action_key(".接取解咒委托 5"))
        self.assertEqual("wanxin_assist_identify", action_guard.resolve_action_key(".辨认咒纹"))
        self.assertEqual("wanxin_assist_banner", action_guard.resolve_action_key(".借幡镇魂"))
        self.assertEqual("wanxin_assist_strip", action_guard.resolve_action_key(".剥离咒源"))

    async def test_scheduler_default_off_does_not_send(self):
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            with patch.object(wanxin, "send_game_command", new=AsyncMock()) as send_mock:
                await wanxin.run_wanxin_scheduler(1_800_000_000.0)

        send_mock.assert_not_awaited()

    async def test_scheduler_publishes_commission_with_configured_reward(self):
        identity_id = self._prepare_identity()
        now = 1_800_000_000.0
        fake_msg = SimpleNamespace(id=7001, sent_at=now)
        with state_module.use_identity(identity_id):
            state_module.state["wanxin_enabled"] = True
            state_module.state["wanxin_observation"] = {
                "auto_config": {"reward_lingshi": 66, "publish_enabled": True, "assist_enabled": True},
                "auto_next_time": now - 1,
            }
            with (
                patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(wanxin, "save_state"),
            ):
                await wanxin.run_wanxin_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".发布解咒委托 66", send_mock.await_args.args[0])
            self.assertEqual("publish", state_module.state["wanxin_observation"]["pending"]["action"])

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

    async def test_scheduler_replies_assist_to_owner_anchor_and_strip_default_off(self):
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
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
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
            self.assertEqual(".辨认咒纹", send_mock.await_args.args[0])
            self.assertEqual(helper_id, send_mock.await_args.kwargs["send_as_id"])
            self.assertEqual(11405889, send_mock.await_args.kwargs["reply_to"])

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


if __name__ == "__main__":
    unittest.main()
