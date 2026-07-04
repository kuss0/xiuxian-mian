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

        self.assertEqual("awakened", parsed["type"])
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
                patch.object(wanxin, "get_last_game_send_block", return_value={"code": "send_timeout", "reason": ">25s"}),
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
                patch.object(wanxin, "get_last_game_send_block", return_value={"code": "send_queue_timeout", "reason": ">60s"}),
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
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
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
                patch.object(wanxin, "get_last_game_send_block", return_value={"code": "action_guard", "reason": "本轮已发送"}),
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
                    "next_visit_time": now + 3600,
                    "next_protect_time": now + 3600,
                    "next_deduce_time": now + 3600,
                    "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
                    "assist": {
                        "send_as_id": helper_id,
                        "last_anchor_msg_id": 8201,
                        "last_anchor_at": now,
                        "identify_enabled": True,
                        "banner_enabled": True,
                        "strip_enabled": False,
                        "next_identify_time": now + 3600,
                        "next_banner_time": now - 1,
                    },
                }
                with (
                    patch.object(wanxin, "MESSAGES_DIR", tmpdir),
                    patch.object(wanxin.time, "time", return_value=fake_now),
                    patch.object(wanxin, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
                    patch.object(wanxin, "get_last_game_send_block", return_value={"code": "send_timeout", "reason": ">25s"}),
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
            self.assertGreaterEqual(observed["next_protect_time"], now + wanxin.WANXIN_PROTECT_CD_SEC)

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
                self.assertGreaterEqual(observed["next_protect_time"], now + wanxin.WANXIN_PROTECT_CD_SEC)

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
            self.assertGreaterEqual(observed["next_protect_time"], now + wanxin.WANXIN_PROTECT_CD_SEC)

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
                            "sender_id": identity_id,
                            "reply_to_msg_id": 0,
                            "text": ".探望南宫婉",
                        },
                        {
                            "ts": "2026-07-04 06:49:42 UTC+8",
                            "event_type": "message",
                            "message_id": 7402,
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
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
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
                "commission": {"id": 5, "accepted": True, "owner_username": "jfdffdddd"},
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

if __name__ == "__main__":
    unittest.main()
