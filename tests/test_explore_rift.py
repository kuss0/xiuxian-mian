import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import ui
from model.features import explore_rift, storage_bag
from model.real_message_replay import get_real_message_text


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class ExploreRiftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _prepare_identity(self, identity_id=8659059191, *, realm="元婴初期", xiuwei_current=1000):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username="walterwa2000",
            realm=realm,
            xiuwei_current=xiuwei_current,
            xiuwei_max=500000,
        )
        return identity_id

    def test_status_text_shows_quiet_lock_during_rebirth_recovery(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "requesting"
            state_module.state["explore_rift_rebirth_due_at"] = now + 30
            with patch.object(explore_rift.time, "time", return_value=now):
                status_text = explore_rift.get_explore_rift_status_text()

        self.assertIn("普通指令静默：是", status_text)
        self.assertIn("仅放行 .夺舍重生 / .重生 <编号>", status_text)
        self.assertIn("夺舍阶段：requesting", status_text)

    def test_status_text_does_not_show_quiet_lock_when_normal(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now + 3600
            with patch.object(explore_rift.time, "time", return_value=now):
                status_text = explore_rift.get_explore_rift_status_text()

        self.assertNotIn("普通指令静默", status_text)

    async def test_ui_set_rebirth_config_updates_snapshot(self):
        identity_id = self._prepare_identity()

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_explore_rift_rebirth_config(
                identity_id,
                {
                    "choice_mode": "root_first",
                    "preferred_root_type": "异灵根",
                    "preferred_attrs": "雷，冰",
                    "blind_index": "2",
                },
            )

        self.assertTrue(ok, message)
        with state_module.use_identity(identity_id):
            config = explore_rift.get_rebirth_choice_config()
        self.assertEqual("root_first", config["choice_mode"])
        self.assertEqual("异灵根", config["preferred_root_type"])
        self.assertEqual("雷、冰", config["preferred_attrs"])
        self.assertEqual(2, config["blind_index"])

        snapshot = ui.get_identity_ui_snapshot(identity_id)
        rebirth = snapshot["explore_rift_rebirth"]
        self.assertEqual("root_first", rebirth["choice_mode"])
        self.assertEqual("异灵根", rebirth["preferred_root_type"])
        self.assertEqual("雷、冰", rebirth["preferred_attrs"])
        self.assertEqual(2, rebirth["blind_index"])

    def test_parse_explore_rift_result_summary_counts_reward_tokens(self):
        summary, item_deltas = explore_rift.parse_explore_rift_result_summary(
            "【探寻成功】\n"
            "命盘【贪狼】照命，主偏财夺势。\n"
            "你的元婴满载而归，为你带来了：【法则碎片·木】, 【法则碎片·雷】, 【法则碎片·土】！"
        )

        self.assertEqual(
            "奖励：法则碎片·木x1、法则碎片·雷x1、法则碎片·土x1",
            summary,
        )
        self.assertEqual({"法则碎片·木": 1, "法则碎片·雷": 1, "法则碎片·土": 1}, item_deltas)

    def test_parse_real_beast_victory_summary_counts_reward_lines(self):
        summary, item_deltas = explore_rift.parse_explore_rift_result_summary(
            real_text("explore_rift.beast_victory.space_core")
        )

        self.assertEqual(
            "奖励：法则碎片·空间x1、四级妖丹x5、空间之核x1",
            summary,
        )
        self.assertEqual({"法则碎片·空间": 1, "四级妖丹": 5, "空间之核": 1}, item_deltas)

    def test_parse_fate_rewrite_summary_counts_reward_without_title_noise(self):
        summary, item_deltas = explore_rift.parse_explore_rift_result_summary(
            "【改命回天】\n"
            "命盘【太阴】照命，改命待发。\n"
            "你避开虚空噬体，修为未损，并平安带回：【法则碎片·木】x2、【空间之核】x1。"
        )

        self.assertEqual(
            "修为未损 ｜ 奖励：法则碎片·木x2、空间之核x1",
            summary,
        )
        self.assertEqual({"法则碎片·木": 2, "空间之核": 1}, item_deltas)

    def test_real_terminal_result_titles_are_identified_as_explore_rift_replies(self):
        for sample_id in (
            "explore_rift.failure.storm",
            "explore_rift.failure.beast_defeat",
            "explore_rift.beast_victory.space_core",
        ):
            with self.subTest(sample_id=sample_id):
                self.assertTrue(explore_rift.is_explore_rift_reply_text(real_text(sample_id)))
        self.assertTrue(explore_rift.is_explore_rift_reply_text("【改命回天】\n你避开虚空噬体，修为未损。"))

    def test_parse_rebirth_options_prefers_stable_default(self):
        options = explore_rift.parse_rebirth_options(
            "你面前出现了三具可供夺舍的肉身：\n\n"
            "1. 【夺舍 坤宁翁】\n"
            "   - 灵根: 天灵根(土)\n"
            "   - 命途: 稳妥之身\n"
            "   - 批命: 此身根基匀稳，命火安宁。\n"
            "2. 【夺舍 惊续玄】\n"
            "   - 灵根: 异灵根(雷)\n"
            "   - 命途: 承脉之身\n"
            "   - 批命: 此身与你前世灵机牵连最深。\n"
            "3. 【夺舍 青命行】\n"
            "   - 灵根: 天灵根(木)\n"
            "   - 命途: 赌命之身\n"
            "   - 批命: 此身命数躁烈。"
        )

        self.assertEqual(3, len(options))
        selected = explore_rift.choose_safe_rebirth_option(options)
        self.assertEqual(1, selected["index"])
        self.assertEqual("稳妥之身", selected["fate"])

    def test_parse_rebirth_options_tolerates_extra_lines_without_chasing_root_rank(self):
        options = explore_rift.parse_rebirth_options(
            "你面前出现了三具可供夺舍的肉身：\n\n"
            "1. 【夺舍 玄狂隐】\n"
            "   - 灵根: 天灵根(水)\n"
            "   - 批命: 此身命数躁烈，若能压住反噬，未必不能一步翻盘。\n"
            "   - 命途: 赌命之身\n"
            "2. 【夺舍 玄安翁】\n"
            "   - 灵根: 伪灵根(金火水土)\n"
            "   - 批命: 此身经络稳固、灵脉平和，最利重新立足。\n"
            "   - 命途: 稳妥之身\n"
            "3. 【夺舍 隐衍尘】\n"
            "   - 灵根: 异灵根(暗)\n"
            "   - 批命: 此身与你前世灵机牵连最深。\n"
            "   - 命途: 承脉之身"
        )

        self.assertEqual(3, len(options))
        selected = explore_rift.choose_safe_rebirth_option(options)
        self.assertEqual(2, selected["index"])
        self.assertEqual("稳妥之身", selected["fate"])

    def test_rebirth_choice_safe_first_prefers_root_only_inside_stable_options(self):
        options = explore_rift.parse_rebirth_options(
            "你面前出现了三具可供夺舍的肉身：\n\n"
            "1. 【夺舍 玄安翁】\n"
            "   - 灵根: 伪灵根(金火水土)\n"
            "   - 命途: 稳妥之身\n"
            "2. 【夺舍 隐衍尘】\n"
            "   - 灵根: 异灵根(雷)\n"
            "   - 命途: 承脉之身\n"
            "3. 【夺舍 青命行】\n"
            "   - 灵根: 天灵根(木)\n"
            "   - 命途: 稳妥之身\n"
        )
        config = {
            "choice_mode": "safe_first",
            "preferred_root_type": "异灵根",
            "preferred_attrs": "雷",
            "preferred_attrs_list": ["雷"],
            "blind_index": 1,
        }

        selected = explore_rift.choose_safe_rebirth_option(options, config=config)

        self.assertEqual(1, selected["index"])
        self.assertEqual("稳妥之身", selected["fate"])

    def test_rebirth_choice_root_first_can_pick_preferred_non_stable_body(self):
        options = explore_rift.parse_rebirth_options(
            "你面前出现了三具可供夺舍的肉身：\n\n"
            "1. 【夺舍 玄安翁】\n"
            "   - 灵根: 伪灵根(金火水土)\n"
            "   - 命途: 稳妥之身\n"
            "2. 【夺舍 隐衍尘】\n"
            "   - 灵根: 异灵根(雷)\n"
            "   - 命途: 承脉之身\n"
        )
        config = {
            "choice_mode": "root_first",
            "preferred_root_type": "异灵根",
            "preferred_attrs": "雷",
            "preferred_attrs_list": ["雷"],
            "blind_index": 1,
        }

        selected = explore_rift.choose_safe_rebirth_option(options, config=config)

        self.assertEqual(2, selected["index"])
        self.assertEqual("承脉之身", selected["fate"])

    async def test_fatal_result_waits_for_escape_edit_and_records_weak_period(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        fatal_text = "【大凶·虚空噬体】\n你运气不佳，竟一头撞入了空间裂缝最深处的风暴核心！无可抵挡的撕裂之力瞬间将你的肉身化为齑粉！"
        escape_text = (
            "【元婴遁逃·虚弱】\n"
            "千钧一发之际，你的元婴带着你的三魂七魄，从破碎的肉身中遁出！\n"
            "但你的神魂遭受重创，已陷入 6小时 的【虚弱期】！\n\n"
            "在此期间，你的修为将会持续逸散，且无法进行夺舍。请静待神魂稳固！"
        )
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 22027
            state_module.state["explore_rift_reply_due_at"] = now + 30
            with (
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
                patch.object(explore_rift.random, "uniform", return_value=0),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    fatal_text,
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )
                self.assertTrue(handled)
                self.assertEqual(22028, state_module.state["explore_rift_fatal_msg_id"])
                self.assertEqual(now + explore_rift.EXPLORE_RIFT_FATAL_GRACE_SEC, state_module.state["explore_rift_fatal_confirm_due_at"])

                handled = await explore_rift.handle_explore_rift_reply(
                    escape_text,
                    now + 4,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_fatal_msg_id"])
            self.assertTrue(state_module.state["explore_rift_rebirth_required"])
            self.assertEqual("weak", state_module.state["explore_rift_rebirth_phase"])
            self.assertEqual(now + 4 + 6 * 3600 + explore_rift.CD_BUFFER_SEC, state_module.state["explore_rift_nascent_escape_weak_until"])

    async def test_confirmed_fatal_without_escape_edit_enters_rebirth_recovery(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_fatal_msg_id"] = 22028
            state_module.state["explore_rift_fatal_confirm_due_at"] = now - 1
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["explore_rift_fatal_msg_id"])
            self.assertTrue(state_module.state["explore_rift_rebirth_required"])
            self.assertEqual("idle", state_module.state["explore_rift_rebirth_phase"])
            self.assertIn("待夺舍恢复", state_module.state["explore_rift_last_result"])

            fake_msg = SimpleNamespace(id=33001, sent_at=now + 5)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now + 5)

            send_mock.assert_awaited_once_with(".夺舍重生", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(33001, state_module.state["explore_rift_rebirth_request_msg_id"])

    async def test_rebirth_scheduler_sends_request_after_weak_period(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "weak"
            state_module.state["explore_rift_nascent_escape_weak_until"] = now - 1
            fake_msg = SimpleNamespace(id=33001, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_awaited_once_with(".夺舍重生", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(33001, state_module.state["explore_rift_rebirth_request_msg_id"])
            self.assertEqual("requesting", state_module.state["explore_rift_rebirth_phase"])

    async def test_rebirth_scheduler_blind_selects_stable_body_when_options_reply_times_out(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "requesting"
            state_module.state["explore_rift_rebirth_request_msg_id"] = 33001
            state_module.state["explore_rift_rebirth_due_at"] = now - 1
            fake_msg = SimpleNamespace(id=33002, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_awaited_once_with(".重生 1", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(0, state_module.state["explore_rift_rebirth_request_msg_id"])
            self.assertEqual(33002, state_module.state["explore_rift_rebirth_select_msg_id"])
            self.assertEqual(1, state_module.state["explore_rift_rebirth_selected_index"])
            self.assertEqual("blind_selecting", state_module.state["explore_rift_rebirth_phase"])

    async def test_rebirth_scheduler_uses_configured_blind_index_when_options_reply_times_out(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "requesting"
            state_module.state["explore_rift_rebirth_request_msg_id"] = 33001
            state_module.state["explore_rift_rebirth_due_at"] = now - 1
            state_module.state["explore_rift_rebirth_blind_index"] = 2
            fake_msg = SimpleNamespace(id=33002, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_awaited_once_with(".重生 2", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(2, state_module.state["explore_rift_rebirth_selected_index"])

    async def test_rebirth_scheduler_stops_after_rebirth_choice_confirmation_timeout(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "blind_selecting"
            state_module.state["explore_rift_rebirth_select_msg_id"] = 33002
            state_module.state["explore_rift_rebirth_selected_index"] = 1
            state_module.state["explore_rift_rebirth_due_at"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(0, state_module.state["explore_rift_rebirth_select_msg_id"])
            self.assertTrue(state_module.state["explore_rift_manual_required"])
            self.assertEqual("manual_required", state_module.state["explore_rift_rebirth_phase"])
            self.assertIn("停止自动重试", state_module.state["explore_rift_rebirth_last_error"])

    async def test_rebirth_options_send_stable_rebirth_choice(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        options_text = (
            "你面前出现了三具可供夺舍的肉身：\n\n"
            "1. 【夺舍 玄安翁】\n"
            "   - 灵根: 伪灵根(金火水土)\n"
            "   - 命途: 稳妥之身\n"
            "   - 批命: 此身经络稳固、灵脉平和，最利重新立足。\n"
            "2. 【夺舍 隐衍尘】\n"
            "   - 灵根: 异灵根(暗)\n"
            "   - 命途: 承脉之身\n"
            "   - 批命: 此身与你前世灵机牵连最深。\n"
            "3. 【夺舍 玄狂隐】\n"
            "   - 灵根: 废灵根\n"
            "   - 命途: 赌命之身\n"
            "   - 批命: 此身命火飘摇。"
        )
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "requesting"
            state_module.state["explore_rift_rebirth_request_msg_id"] = 33001
            fake_msg = SimpleNamespace(id=33002, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    options_text,
                    now,
                    reply_to=SimpleNamespace(id=33001, raw_text=".夺舍重生"),
                    matched_family="explore_rift",
                    result_msg_id=33003,
                )

            self.assertTrue(handled)
            send_mock.assert_awaited_once_with(".重生 1", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(33002, state_module.state["explore_rift_rebirth_select_msg_id"])
            self.assertEqual(1, state_module.state["explore_rift_rebirth_selected_index"])
            self.assertEqual("selecting", state_module.state["explore_rift_rebirth_phase"])

    async def test_rebirth_success_clears_required_state(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_rebirth_required"] = True
            state_module.state["explore_rift_rebirth_phase"] = "selecting"
            state_module.state["explore_rift_rebirth_select_msg_id"] = 33002
            state_module.state["explore_rift_rebirth_due_at"] = now + 30
            with (
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "夺舍成功！你的神魂与新肉身完美融合，所有被封存的神通宝物已尽数回归！",
                    now,
                    reply_to=SimpleNamespace(id=33002, raw_text=".重生 1"),
                    matched_family="explore_rift",
                    result_msg_id=33004,
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["explore_rift_rebirth_required"])
            self.assertEqual("restored", state_module.state["explore_rift_rebirth_phase"])
            self.assertEqual(0, state_module.state["explore_rift_rebirth_select_msg_id"])

    async def test_fate_rewrite_final_clears_pending_and_schedules_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 22027
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 22028
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【改命回天】\n"
                    "命盘【太阴】照命，改命待发。\n"
                    "你避开虚空噬体，修为未损，并平安带回：【法则碎片·木】x2、【空间之核】x1。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "修为未损 ｜ 奖励：法则碎片·木x2、空间之核x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(2, records[str(identity_id)]["items"]["法则碎片·木"])
            self.assertEqual(1, records[str(identity_id)]["items"]["空间之核"])

    async def test_tianxing_explore_result_reports_high_priority_before_normal_audit(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 22027
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 22028
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【改命回天】\n"
                    "命盘【贪狼】照命，改命待发。\n"
                    "你避开虚空噬体，修为未损，并平安带回：【法则碎片·木】x2。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

        self.assertTrue(handled)
        self.assertEqual(2, audit_mock.await_count)
        first_args, first_kwargs = audit_mock.await_args_list[0]
        second_args, second_kwargs = audit_mock.await_args_list[1]
        self.assertIn("🌌 天星探索结果｜探寻裂缝", first_args[0])
        self.assertEqual("high", first_kwargs["priority"])
        self.assertEqual("identity", first_kwargs["scope"])
        self.assertIn("🕳 探寻裂缝结果", second_args[0])
        self.assertNotIn("priority", second_kwargs)

    async def test_scheduler_sends_explore_rift_with_reply_tracking_metadata(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = False
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_awaited_once_with(".探寻裂缝", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(22027, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_REPLY_TIMEOUT_SEC, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual("已发送", state_module.state["explore_rift_last_result"])

    async def test_scheduler_does_not_directly_insert_tianxing_set_star_without_timeline(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴", "贪狼"],
                "fixed_star": "",
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_set_star_enabled": True,
                "strategy_dry_run_enabled": False,
                "star_priority": ["太阴", "贪狼"],
            }
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(".探寻裂缝", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(22027, state_module.state["explore_rift_reply_to_msg_id"])

    async def test_scheduler_requests_tianxing_timeline_before_explore_rift_when_enabled(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_awaited_once()
            self.assertEqual("探索", timeline_mock.await_args.kwargs["windows"][0]["route"])
            self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
            send_mock.assert_not_awaited()
            self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["explore_rift_last_result"])

    async def test_scheduler_prepares_tianxing_timeline_inside_future_explore_rift_lead_window(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        due_at = now + 240
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = due_at
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
                "route_prepare_lead_sec": 300,
            }
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_awaited_once()
            self.assertEqual("探索", timeline_mock.await_args.kwargs["windows"][0]["route"])
            self.assertTrue(timeline_mock.await_args.kwargs["windows"][0]["require_change_fate"])
            self.assertEqual(now, timeline_mock.await_args.kwargs["windows"][0]["start_at"])
            self.assertGreaterEqual(timeline_mock.await_args.kwargs["windows"][0]["end_at"], due_at)
            send_mock.assert_not_awaited()
            self.assertEqual(due_at, state_module.state["next_explore_rift_time"])
            self.assertEqual("天星时间线：sent_waiting_ack", state_module.state["explore_rift_last_result"])

    async def test_scheduler_does_not_insert_tianxing_predict_before_explore_rift_without_timeline(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "",
                "current_prediction": "",
                "tianji_value": 2,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "strategy_dry_run_enabled": False,
                "min_tianji_for_change": 6,
            }
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(".探寻裂缝", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(22027, state_module.state["explore_rift_reply_to_msg_id"])

    async def test_scheduler_sends_explore_rift_directly_when_tianxing_timeline_released(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼"],
                "fixed_star": "贪狼",
                "current_change": "探索",
                "current_change_until": now + 3600,
                "current_prediction": "探索",
                "current_prediction_until": now + 1800,
                "tianji_value": 9,
            }
            state_module.state["tianxing_auto_config"] = {
                "auto_change_fate_enabled": True,
                "auto_predict_enabled": True,
                "timeline_enabled": True,
                "strategy_dry_run_enabled": False,
            }
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 5, "plan_id": "test", "reason": "confirmed"},
                },
            }
            fake_msg = SimpleNamespace(id=22027, sent_at=now)
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_awaited_once_with(".探寻裂缝", track=False, max_retry=0, source_module="探寻裂缝")
            self.assertEqual(22027, state_module.state["explore_rift_reply_to_msg_id"])

    async def test_scheduler_blocks_explore_rift_when_other_prediction_active(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.update_send_as_profile(identity_id, sect_name="天星宗")
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 9,
            }
            with (
                patch.object(explore_rift, "run_tianxing_timeline_scheduler", new=AsyncMock()) as timeline_mock,
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            timeline_mock.assert_not_awaited()
            send_mock.assert_not_awaited()
            self.assertEqual(now + 1800 + explore_rift.CD_BUFFER_SEC, state_module.state["next_explore_rift_time"])
            self.assertIn("避免逆命", state_module.state["explore_rift_last_error"])

    async def test_pending_reply_clears_initial_timeout_and_waits_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10425942
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["next_explore_rift_time"] = now + 30
            with patch.object(explore_rift, "save_state"):
                handled = await explore_rift.handle_explore_rift_reply(
                    "你运转全身法力，撕开一道漆黑的空间裂缝，将元婴送入其中探寻机缘...",
                    now,
                    reply_to=SimpleNamespace(id=10425942, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10425944,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(10425944, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual("探寻中", state_module.state["explore_rift_last_result"])
            self.assertGreaterEqual(state_module.state["next_explore_rift_time"], now + explore_rift.EXPLORE_RIFT_CD)

            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now + explore_rift.EXPLORE_RIFT_REPLY_TIMEOUT_SEC + 1)

            send_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()
            self.assertNotIn("超时", state_module.state["explore_rift_last_error"])

    async def test_scheduler_blocks_auto_high_xiuwei_without_sending(self):
        identity_id = self._prepare_identity(xiuwei_current=500000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertTrue(state_module.state["explore_rift_enabled"])
            self.assertIn("auto模式", state_module.state["explore_rift_last_error"])
            self.assertGreater(state_module.state["next_explore_rift_time"], now)

    async def test_scheduler_blocks_missing_current_xiuwei_without_sending(self):
        identity_id = self._prepare_identity(xiuwei_current=0)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertTrue(state_module.state["explore_rift_enabled"])
            self.assertIn("修为未知", state_module.state["explore_rift_last_error"])
            self.assertGreater(state_module.state["next_explore_rift_time"], now)

    async def test_scheduler_blocks_below_yuanying_and_disables_module(self):
        identity_id = self._prepare_identity(realm="结丹后期", xiuwei_current=1000)
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = now - 1
            with (
                patch.object(explore_rift, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
                patch.object(explore_rift, "save_state"),
            ):
                await explore_rift.run_explore_rift_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["explore_rift_enabled"])
            self.assertIn("境界不符", state_module.state["explore_rift_last_error"])

    async def test_result_reply_updates_storage_bag_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 22027
            state_module.state["explore_rift_reply_due_at"] = now + 30
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【探寻成功】\n"
                    "你的元婴满载而归，为你带来了：【法则碎片·火】, 【法则碎片·金】, 【法则碎片·水】！",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·火x1、法则碎片·金x1、法则碎片·水x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·火"])
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·金"])
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·水"])

    async def test_real_storm_failure_clears_pending_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10425942
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10425944

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.failure.storm"),
                    now,
                    reply_to=SimpleNamespace(id=10425942, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10425944,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertIn("遭遇风暴", state_module.state["explore_rift_last_result"])

    async def test_real_beast_defeat_clears_pending_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10426277
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10426278

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.failure.beast_defeat"),
                    now,
                    reply_to=SimpleNamespace(id=10426277, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10426278,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertIn("不敌败退", state_module.state["explore_rift_last_result"])

    async def test_real_beast_victory_updates_storage_bag_and_schedules_default_cd(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10410001
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10410003

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.beast_victory.space_core"),
                    now,
                    reply_to=SimpleNamespace(id=10410001, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10410003,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·空间x1、四级妖丹x5、空间之核x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(identity_id)]["items"]["法则碎片·空间"])
            self.assertEqual(5, records[str(identity_id)]["items"]["四级妖丹"])
            self.assertEqual(1, records[str(identity_id)]["items"]["空间之核"])

    async def test_cd_reply_uses_real_wait_text(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            with (
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "空间裂缝尚未稳定，其中的空间风暴仍在肆虐。请在 1小时20分钟31秒 后再行探寻。",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + 4831 + explore_rift.CD_BUFFER_SEC, state_module.state["next_explore_rift_time"])
            self.assertEqual("冷却中", state_module.state["explore_rift_last_result"])

    async def test_realm_period_and_sub_soul_limit_replies_delay_without_retry_storm(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        for text in (
            "你的境界尚未达到元婴期，无法探寻空间裂缝。",
            "你只是主魂的一缕分神，无法承受空间裂缝之力。",
        ):
            with self.subTest(text=text):
                with state_module.use_identity(identity_id):
                    state_module.state["explore_rift_enabled"] = True
                    state_module.state["explore_rift_reply_to_msg_id"] = 22027
                    state_module.state["explore_rift_reply_due_at"] = now + 30
                    with (
                        patch.object(explore_rift, "save_state"),
                        patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
                    ):
                        handled = await explore_rift.handle_explore_rift_reply(
                            text,
                            now,
                            reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                            matched_family="explore_rift",
                            result_msg_id=22028,
                        )

                    self.assertTrue(handled)
                    self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
                    self.assertEqual(now + explore_rift.RETRY_MAX_SEC, state_module.state["next_explore_rift_time"])
                    self.assertIn("限制", state_module.state["explore_rift_last_error"])

    async def test_inventory_fenglei_wings_does_not_shorten_cd_without_equipped_signal(self):
        identity_id = self._prepare_identity(realm="化神初期")
        now = 1_700_000_000.0
        state_module.set_storage_bag_records({str(identity_id): {"items": {"风雷翅": 1}}})
        with state_module.use_identity(identity_id):
            state_module.state["explore_rift_enabled"] = True
            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    "【探寻成功】\n你的元婴满载而归，为你带来了：【法则碎片·金】！",
                    now,
                    reply_to=SimpleNamespace(id=22027, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=22028,
                )

            self.assertTrue(handled)
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])


if __name__ == "__main__":
    unittest.main()
