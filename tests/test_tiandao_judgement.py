import atexit
import copy
import json
import sys
import asyncio
import tempfile
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
from model.features import tiandao_judgement, tiandao_miniapp
from model.features.tiandao_judgement import parse_tiandao_judgement_prompt


class TiandaoJudgementTests(unittest.TestCase):
    def test_parse_mod_arithmetic_with_token(self):
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n\n"
            "对象 【march_7777】，你被 【anderbowie】 举报使用了自动化傀儡法术（外挂脚本）！\n"
            "天道已向你降下迷障！你必须在 3分钟 内破除迷障，证明自己是拥有独立灵智的活人！\n\n"
            "文本题面：\n"
            "请直接计算：计算：(890+24×9) 除以 31 的余数 = ?\n"
            "🔐 本轮阵眼口令：U9EX\n\n"
            "👇 自证方式：\n"
            "发送：.自证 <阵眼口令> <答案>"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("march_7777", parsed["target"])
        self.assertEqual("U9EX", parsed["token"])
        self.assertEqual(".自证", parsed["command"])
        self.assertEqual("21", parsed["answer"])

    def test_parse_tiandao_wenxin_prompt_without_judgement_title(self):
        text = (
            "【天道问心】\n"
            "对象 【march_7777】\n"
            "请在 3分钟 内自证。\n"
            "天道问心：炼制玄铁剑消耗灵石 加 十 等于？\n"
            "阵眼口令：ABCD\n"
            ".自证 <阵眼口令> <答案>"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("march_7777", parsed["target"])
        self.assertEqual("ABCD", parsed["token"])
        self.assertEqual("20", parsed["answer"])

    def test_parse_new_verify_command_and_symbol_operator(self):
        text = (
            "【天道问心】\n"
            "对象 @foo【march_7777】\n"
            "请直接计算：敢问炼制玄铁剑消耗灵石 + 十 = ?\n"
            "阵眼口令：ZX9\n"
            "回复指令：.验证"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("march_7777", parsed["target"])
        self.assertEqual("ZX9", parsed["token"])
        self.assertEqual(".验证", parsed["command"])
        self.assertEqual("20", parsed["answer"])

    def test_parse_plain_arithmetic_expression(self):
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n"
            "对象 【march_7777】\n"
            "请直接计算：12＋8×（3-1） = ?\n"
            "阵眼口令：ZX9\n"
            "发送：.自证 <阵眼口令> <答案>"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("arithmetic", parsed["kind"])
        self.assertEqual("28", parsed["answer"])

    def test_parse_recent_image_result_prompt_and_direct_send_command(self):
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n"
            "对象 【march_7777】\n"
            "请在 3分钟 内自证。\n"
            "请计算图中结果：✨ 股市买入基础手续费 加 三 = ?\n"
            "阵眼口令：ZX9\n"
            "直接发送指令：.验证 <阵眼口令> <答案>"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("march_7777", parsed["target"])
        self.assertEqual("ZX9", parsed["token"])
        self.assertEqual(".验证", parsed["command"])
        self.assertEqual("5", parsed["answer"])

    def test_mini_app_transitional_text_is_not_prompt(self):
        self.assertFalse(tiandao_judgement._is_tiandao_judgement_prompt("Mini App 拖动验证生成中，请稍候..."))
        self.assertTrue(tiandao_judgement._is_tiandao_judgement_prompt("Mini App 拖动验证\n本轮挑战码：abc"))
        self.assertTrue(tiandao_judgement._is_tiandao_judgement_prompt("https://t.me/gamebot/app?startapp=rpt_abc"))
        self.assertTrue(tiandao_judgement._is_tiandao_judgement_prompt("https://t.me/gamebot/app?start_param=RPT_ABC"))

    def test_extract_miniapp_prompt_accepts_lowercase_token(self):
        text = (
            "Mini App 拖动验证\n"
            "对象 【march_7777】\n"
            "https://t.me/fanrenxiuxian_bot/app?startapp=rpt_abc12"
        )

        parsed = tiandao_judgement.extract_tiandao_miniapp_challenge(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("rpt_abc12", parsed["token"])
        self.assertEqual("rpt", parsed["kind"])
        self.assertEqual("march_7777", parsed["target"])

    def test_extract_miniapp_prompt_rejects_uncontrolled_tme_bot_url(self):
        text = (
            "Mini App 拖动验证\n"
            "对象 【march_7777】\n"
            "https://t.me/evil_bot/app?startapp=rpt_ABC12"
        )

        parsed = tiandao_miniapp.extract_tiandao_miniapp_challenge(text)

        self.assertIsNone(parsed)

    def test_miniapp_error_sanitizer_redacts_webapp_secrets(self):
        error = "failed tgWebAppData=query_id%3Dabc&hash=secret&user=42 initData=hidden token=stk_SECRET9"

        sanitized = tiandao_miniapp.sanitize_tiandao_miniapp_error(error)

        self.assertIn("tgWebAppData=<redacted>", sanitized)
        self.assertIn("initData=<redacted>", sanitized)
        self.assertIn("stk_<redacted>", sanitized)
        self.assertNotIn("secret", sanitized)
        self.assertNotIn("hidden", sanitized)
        self.assertNotIn("SECRET9", sanitized)

    def test_miniapp_token_summary_keeps_only_suffix(self):
        self.assertEqual("stk_...ET99", tiandao_miniapp.summarize_tiandao_miniapp_token("stk_SECRET99"))
        self.assertEqual("rpt_...bc12", tiandao_miniapp.summarize_tiandao_miniapp_token("rpt_abc12"))
        self.assertEqual("", tiandao_miniapp.summarize_tiandao_miniapp_token("bad_SECRET99"))

    def test_build_miniapp_drag_proof_respects_challenge_bounds(self):
        with patch.object(tiandao_miniapp.random, "uniform", return_value=0.0), \
                patch.object(tiandao_miniapp.random, "randint", return_value=2):
            proof = tiandao_miniapp.build_tiandao_miniapp_drag_proof({
                "challengeId": "c1",
                "targetRatio": 0.5,
                "minDurationMs": 800,
                "minPoints": 10,
            })

        self.assertEqual("c1", proof["challengeId"])
        self.assertEqual(328.0, proof["maxX"])
        self.assertEqual(164.0, proof["targetX"])
        self.assertEqual(164.0, proof["finalX"])
        self.assertGreaterEqual(proof["durationMs"], 1800.0)
        self.assertGreaterEqual(len(proof["points"]), 16)
        self.assertEqual(0.0, proof["points"][0]["x"])
        self.assertEqual(proof["finalX"], proof["points"][-1]["x"])
        self.assertTrue(all(0.0 <= point["x"] <= proof["maxX"] for point in proof["points"]))

    def test_extract_random_array_button_sequence_without_target(self):
        text = (
            "神识验证\n"
            "随机阵列验证\n"
            "点击顺序：乾 → 坤 → 离"
        )

        parsed = tiandao_judgement._extract_tiandao_button_sequence(text)

        self.assertEqual("", parsed["target"])
        self.assertEqual(["乾", "坤", "离"], parsed["sequence"])


class TiandaoJudgementIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        try:
            self._state_snapshot = copy.deepcopy(dict(state_module.state.items()))
        except KeyError:
            self._state_snapshot = None
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._miniapp_terminal_events_snapshot = dict(tiandao_judgement._tiandao_miniapp_terminal_events)
        tiandao_judgement._tiandao_miniapp_terminal_events.clear()

    def tearDown(self):
        if self._state_snapshot is not None:
            for key in list(state_module.state.keys()):
                if key not in self._state_snapshot:
                    state_module.state.pop(key, None)
            for key, value in self._state_snapshot.items():
                state_module.state[key] = copy.deepcopy(value)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        tiandao_judgement._tiandao_miniapp_terminal_events.clear()
        tiandao_judgement._tiandao_miniapp_terminal_events.update(self._miniapp_terminal_events_snapshot)
        super().tearDown()

    def _prepare_identity(self, identity_id=90001, account_id=80001, username="march_7777"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, label=username)
        state_module.set_identity_account(identity_id, account_id)
        return identity_id

    async def test_no_target_does_not_scan_whole_prompt_for_identity(self):
        self._prepare_identity()
        identity_id = await tiandao_judgement._resolve_tiandao_identity_id("", None)

        self.assertIsNone(identity_id)

    async def test_no_target_uses_reply_sender_identity(self):
        identity_id = self._prepare_identity()

        class Event:
            reply_to = SimpleNamespace(reply_to_msg_id=123)

            async def get_reply_message(self):
                return SimpleNamespace(sender_id=80001)

        resolved = await tiandao_judgement._resolve_tiandao_identity_id("", Event())

        self.assertEqual(identity_id, resolved)

    async def test_click_button_sequence_with_identity_client(self):
        clicked = []

        class Message:
            id = 321
            chat_id = -100
            buttons = [
                [SimpleNamespace(text="乾"), SimpleNamespace(text="坤")],
                [SimpleNamespace(text="离")],
            ]

            async def click(self, row_index, col_index):
                clicked.append((row_index, col_index))

        class Client:
            async def get_messages(self, chat_id, ids):
                self.seen = (chat_id, ids)
                return Message()

        client = Client()
        event = SimpleNamespace(message=Message(), chat_id=-100)
        with (
            patch.object(tiandao_judgement, "_get_identity_client", return_value=client),
            patch.object(asyncio, "sleep", AsyncMock()),
        ):
            ok, error = await tiandao_judgement._click_tiandao_judgement_buttons(
                event,
                90001,
                ["坤", "离"],
            )

        self.assertTrue(ok, error)
        self.assertEqual((-100, 321), client.seen)
        self.assertEqual([(0, 1), (1, 0)], clicked)

    async def test_button_sequence_without_target_does_not_use_current_identity(self):
        identity_id = self._prepare_identity()
        state_module.state["tiandao_judgement_enabled"] = True
        text = "神识验证\n随机阵列验证\n点击顺序：乾 → 坤 → 离"

        class Message:
            id = 321
            chat_id = -100
            buttons = [[SimpleNamespace(text="乾"), SimpleNamespace(text="坤"), SimpleNamespace(text="离")]]

        event = SimpleNamespace(message=Message(), chat_id=-100, id=321)
        with (
            state_module.use_identity(identity_id),
            patch.object(tiandao_judgement, "_get_identity_client") as client_mock,
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, 1_700_000_000.0, event)

        self.assertTrue(handled)
        client_mock.assert_not_called()
        self.assertIn("未匹配身份", audit_mock.await_args.args[0])

    async def test_button_sequence_without_target_uses_reply_sender_identity(self):
        clicked = []
        identity_id = self._prepare_identity(account_id=80001)
        state_module.state["tiandao_judgement_enabled"] = True
        text = "神识验证\n随机阵列验证\n点击顺序：乾 → 坤"

        class Message:
            id = 321
            chat_id = -100
            buttons = [[SimpleNamespace(text="乾"), SimpleNamespace(text="坤")]]

            async def click(self, row_index, col_index):
                clicked.append((row_index, col_index))

        class Event:
            id = 321
            chat_id = -100
            message = Message()
            reply_to = SimpleNamespace(reply_to_msg_id=123)

            async def get_reply_message(self):
                return SimpleNamespace(sender_id=80001)

        class Client:
            async def get_messages(self, chat_id, ids):
                return Message()

        with (
            state_module.use_identity(identity_id),
            patch.object(tiandao_judgement, "_get_identity_client", return_value=Client()),
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()),
            patch.object(asyncio, "sleep", AsyncMock()),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, 1_700_000_000.0, Event())

        self.assertTrue(handled)
        self.assertEqual([(0, 0), (0, 1)], clicked)

    async def test_miniapp_prompt_queues_drag_verification(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n"
            "对象 【march_7777】\n"
            "Mini App 拖动验证\n"
            "本轮挑战码：rpt_ABCD12\n"
            "https://t.me/fanrenxiuxian_bot?startapp=rpt_ABCD12"
        )
        event = SimpleNamespace(id=321, chat_id=-100, message=SimpleNamespace(buttons=[]))

        with (
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now, event)

        self.assertTrue(handled)
        pending = state_module.state["tiandao_judgement_pending"]
        self.assertIn("-100:321", pending)
        item = pending["-100:321"]
        self.assertEqual("miniapp_drag", item["kind"])
        self.assertEqual("rpt", item["miniapp_kind"])
        self.assertEqual("rpt_ABCD12", item["token"])
        self.assertEqual(identity_id, item["identity_id"])
        schedule_mock.assert_called_once()

    async def test_real_miniapp_prompt_uses_button_url_and_reply_identity(self):
        identity_id = self._prepare_identity(account_id=80001)
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        text = (
            "🤖 【天道迷障 · 神识验证】\n\n"
            "检测到高频灵力波动，请破除迷障证明你的道心！\n\n"
            "天道已降下【Mini App 拖动验证】。请在 3分钟 内打开入口并完成随机拖动交互。\n\n"
            "🧩 拖动验证入口：\n"
            "请点击下方 打开验证 按钮，完成一次随机拖动验证。\n"
            "验证会话已绑定，请直接完成页面内滑动。\n\n"
            "✅ 完成后自动通过；通过后 10分钟 内可继续交易，无需重复验证。"
        )
        button = SimpleNamespace(
            text="打开 Mini App 验证",
            button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot/app?startapp=stk_REAL123"),
        )

        class Event:
            id = 9429743
            chat_id = -1001680975844
            message = SimpleNamespace(buttons=[[button]])
            reply_to = SimpleNamespace(reply_to_msg_id=9429741)

            async def get_reply_message(self):
                return SimpleNamespace(sender_id=80001)

        with (
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now, Event())

        self.assertTrue(handled)
        pending = state_module.state["tiandao_judgement_pending"]
        self.assertIn("-1001680975844:9429743", pending)
        item = pending["-1001680975844:9429743"]
        self.assertEqual("miniapp_drag", item["kind"])
        self.assertEqual("stk", item["miniapp_kind"])
        self.assertEqual("stk_REAL123", item["token"])
        self.assertEqual(identity_id, item["identity_id"])
        schedule_mock.assert_called_once()

    async def test_real_miniapp_prompt_uses_negative_reply_sender_identity(self):
        identity_id = self._prepare_identity(identity_id=8658442054, account_id=80001, username="local_stock")
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        text = (
            "🤖 【天道迷障 · 神识验证】\n\n"
            "检测到高频抛售，请破除迷障证明你的道心！\n\n"
            "天道已降下【Mini App 拖动验证】。请在 3分钟 内打开入口并完成随机拖动交互。\n\n"
            "\n"
            "🧩 拖动验证入口：\n"
            "请点击下方 打开验证 按钮，完成一次随机拖动验证。\n"
            "验证会话已绑定，请直接完成页面内滑动。\n\n"
            "✅ 完成后自动通过；通过后 10分钟 内可继续交易，无需重复验证。"
        )
        button = SimpleNamespace(
            text="打开 Mini App 验证",
            button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot/app?startapp=stk_REAL123"),
        )

        class Event:
            id = 9452873
            chat_id = -1001680975844
            message = SimpleNamespace(buttons=[[button]])
            reply_to = SimpleNamespace(reply_to_msg_id=9452872)

            async def get_reply_message(self):
                return SimpleNamespace(sender_id=-1008658442054)

        with (
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now, Event())

        self.assertTrue(handled)
        pending = state_module.state["tiandao_judgement_pending"]
        self.assertIn("-1001680975844:9452873", pending)
        item = pending["-1001680975844:9452873"]
        self.assertEqual("miniapp_drag", item["kind"])
        self.assertEqual("stk", item["miniapp_kind"])
        self.assertEqual("stk_REAL123", item["token"])
        self.assertEqual(identity_id, item["identity_id"])
        schedule_mock.assert_called_once()

    async def test_real_miniapp_prompt_falls_back_to_local_message_log_sender(self):
        identity_id = self._prepare_identity(identity_id=8658442054, account_id=80001, username="local_stock")
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        text = (
            "🤖 【天道迷障 · 神识验证】\n\n"
            "检测到高频灵力波动，请破除迷障证明你的道心！\n\n"
            "天道已降下【Mini App 拖动验证】。请在 3分钟 内打开入口并完成随机拖动交互。\n\n"
            "🧩 拖动验证入口：\n"
            "请点击下方 打开验证 按钮，完成一次随机拖动验证。\n"
            "验证会话已绑定，请直接完成页面内滑动。\n\n"
            "✅ 完成后自动通过；通过后 10分钟 内可继续交易，无需重复验证。"
        )
        button = SimpleNamespace(
            text="打开 Mini App 验证",
            button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot/app?startapp=stk_LOG123"),
        )

        class Event:
            id = 9453000
            chat_id = -1001680975844
            message = SimpleNamespace(buttons=[[button]])
            reply_to = SimpleNamespace(reply_to_msg_id=9452999)

            async def get_reply_message(self):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-05-29.log"
            log_path.write_text(
                json.dumps(
                    {
                        "ts": "2026-05-29 08:19:19 UTC+8",
                        "event_type": "message",
                        "message_id": 9452999,
                        "chat_id": -1001680975844,
                        "sender_id": -1008658442054,
                        "text": ".买入 IDX_ORE 3764",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(tiandao_judgement, "MESSAGES_DIR", tmpdir),
                patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
                patch.object(tiandao_judgement, "save_state"),
            ):
                handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now, Event())

        self.assertTrue(handled)
        pending = state_module.state["tiandao_judgement_pending"]
        self.assertIn("-1001680975844:9453000", pending)
        item = pending["-1001680975844:9453000"]
        self.assertEqual("stk_LOG123", item["token"])
        self.assertEqual(identity_id, item["identity_id"])
        schedule_mock.assert_called_once()

    async def test_real_miniapp_prompt_for_external_reply_sender_is_skipped_without_audit(self):
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {}
        text = (
            "🤖 【天道迷障 · 神识验证】\n\n"
            "检测到高频抛售，请破除迷障证明你的道心！\n\n"
            "天道已降下【Mini App 拖动验证】。请在 3分钟 内打开入口并完成随机拖动交互。\n\n"
            "🧩 拖动验证入口：\n"
            "请点击下方 打开验证 按钮，完成一次随机拖动验证。\n"
            "验证会话已绑定，请直接完成页面内滑动。\n\n"
            "✅ 完成后自动通过；通过后 10分钟 内可继续交易，无需重复验证。"
        )
        button = SimpleNamespace(
            text="打开 Mini App 验证",
            button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot/app?startapp=stk_EXT123"),
        )

        class Event:
            id = 9453100
            chat_id = -1001680975844
            message = SimpleNamespace(buttons=[[button]])
            reply_to = SimpleNamespace(reply_to_msg_id=9453099)

            async def get_reply_message(self):
                return SimpleNamespace(sender_id=6049695503)

        with (
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tiandao_judgement, "console_log") as console_mock,
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now, Event())

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        audit_mock.assert_not_awaited()
        schedule_mock.assert_not_called()
        self.assertIn("外部身份验证，跳过", console_mock.call_args.args[0])

    async def test_miniapp_scheduler_submits_and_clears_pending(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {
            "1:10": {
                "kind": "miniapp_drag",
                "miniapp_kind": "stk",
                "target": "march_7777",
                "identity_id": identity_id,
                "token": "stk_ZZ99",
                "due_at": now - 1,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 10,
                "chat_id": 1,
                "retry_count": 0,
            }
        }

        with (
            patch.object(tiandao_judgement, "run_tiandao_miniapp_drag_verification", new=AsyncMock(return_value={"ok": True})) as verify_mock,
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)

        verify_mock.assert_awaited_once_with(identity_id, "stk_ZZ99")
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        self.assertIn("Mini App 验证已提交", audit_mock.await_args.args[0])
        self.assertIn("stk_...ZZ99", audit_mock.await_args.args[0])
        self.assertNotIn("stk_ZZ99", audit_mock.await_args.args[0])

    async def test_miniapp_success_terminal_event_blocks_requeue(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {
            "-100:321": {
                "kind": "miniapp_drag",
                "miniapp_kind": "rpt",
                "target": "march_7777",
                "identity_id": identity_id,
                "token": "rpt_ABCD12",
                "due_at": now - 1,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 321,
                "chat_id": -100,
                "retry_count": 0,
            }
        }
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n"
            "对象 【march_7777】\n"
            "Mini App 拖动验证\n"
            "本轮挑战码：rpt_ABCD12\n"
            "https://t.me/fanrenxiuxian_bot?startapp=rpt_ABCD12"
        )
        event = SimpleNamespace(id=321, chat_id=-100, message=SimpleNamespace(buttons=[]))

        with (
            patch.object(tiandao_judgement, "run_tiandao_miniapp_drag_verification", new=AsyncMock(return_value={"ok": True})),
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()),
            patch.object(tiandao_judgement, "save_state"),
        ):
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)

        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])

        with (
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(text, now + 1, event)

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        schedule_mock.assert_not_called()

    async def test_manual_miniapp_success_reply_clears_pending_and_blocks_requeue(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {
            "-100:321": {
                "kind": "miniapp_drag",
                "miniapp_kind": "rpt",
                "target": "march_7777",
                "identity_id": identity_id,
                "token": "rpt_ABCD12",
                "due_at": now + 10,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 321,
                "chat_id": -100,
                "retry_count": 0,
                "terminal_key": "-100:321:rpt_ABCD12",
            }
        }
        event = SimpleNamespace(reply_to=SimpleNamespace(reply_to_msg_id=321))

        with patch.object(tiandao_judgement, "save_state"):
            handled = await tiandao_judgement.handle_tiandao_judgement_punishment(
                "✅ Mini App 验证完成！\n你已通过本轮交易验证，接下来 10分钟 内可直接买卖。",
                now,
                event=event,
            )

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])

        prompt = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n"
            "对象 【march_7777】\n"
            "Mini App 拖动验证\n"
            "本轮挑战码：rpt_ABCD12\n"
            "https://t.me/fanrenxiuxian_bot?startapp=rpt_ABCD12"
        )
        prompt_event = SimpleNamespace(id=321, chat_id=-100, message=SimpleNamespace(buttons=[]))
        with (
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task") as schedule_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_prompt(prompt, now + 1, prompt_event)

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        schedule_mock.assert_not_called()

    async def test_manual_judgement_success_broadcast_clears_pending_by_target(self):
        identity_id = self._prepare_identity(username="cupaopao")
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_pending"] = {
            "-100:9436569": {
                "kind": "miniapp_drag",
                "miniapp_kind": "rpt",
                "target": "cupaopao",
                "identity_id": identity_id,
                "token": "rpt_CUPA",
                "due_at": now + 10,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 9436569,
                "chat_id": -100,
                "retry_count": 0,
            }
        }

        with patch.object(tiandao_judgement, "save_state"):
            handled = await tiandao_judgement.handle_tiandao_judgement_punishment(
                "⚖️ 【天道裁决 · 真相大白】 ⚖️\n\n"
                "对象 【cupaopao】 已完成本轮自证。\n\n"
                "裁决说明：\n- 案件来源：机器人回复后删指令自动巡查",
                now,
            )

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])

    async def test_miniapp_scheduler_retries_only_once(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {
            "1:10": {
                "kind": "miniapp_drag",
                "miniapp_kind": "rpt",
                "target": "march_7777",
                "identity_id": identity_id,
                "token": "rpt_FAIL",
                "due_at": now - 1,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 10,
                "chat_id": 1,
                "retry_count": 0,
            }
        }

        with (
            patch.object(
                tiandao_judgement,
                "run_tiandao_miniapp_drag_verification",
                new=AsyncMock(return_value={"ok": False, "error": "tgWebAppData=secret"}),
            ) as verify_mock,
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task"),
            patch.object(tiandao_judgement, "save_state"),
            patch.object(tiandao_judgement.time, "time", return_value=now),
        ):
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)
            pending = state_module.state["tiandao_judgement_pending"]
            self.assertIn("1:10", pending)
            self.assertEqual(1, pending["1:10"]["retry_count"])

            pending["1:10"]["due_at"] = now - 1
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)

        self.assertEqual(2, verify_mock.await_count)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        self.assertIn("Mini App 验证失败", audit_mock.await_args_list[-1].args[0])
        self.assertNotIn("secret", audit_mock.await_args_list[-1].args[0])

    async def test_external_punishment_uses_normal_priority_without_manual_attention(self):
        self._prepare_identity(username="local_user")
        text = (
            "⚡ 【天道裁决 · 斩妖除魔】 ⚡\n\n"
            "对象 【miyuemiyue】 面对天道迷障无言以对，已被确认为【挂机傀儡】！\n"
            "天罚降临，其神魂已被永久剥离，打入无尽死牢！"
        )

        with patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock:
            handled = await tiandao_judgement.handle_tiandao_judgement_punishment(text, 1_700_000_000.0)

        self.assertTrue(handled)
        audit_mock.assert_awaited_once()
        self.assertIn("天道裁决外部对象", audit_mock.await_args.args[0])
        self.assertIn("@miyuemiyue", audit_mock.await_args.args[0])
        self.assertNotIn("请手动", audit_mock.await_args.args[0])
        self.assertEqual("normal", audit_mock.await_args.kwargs.get("priority"))

    async def test_local_punishment_mentions_identity_and_uses_high_priority(self):
        identity_id = self._prepare_identity(username="march_7777")
        state_module.state["pending_tasks"] = {123: {"command": ".野外历练"}}
        state_module.state["tiandao_judgement_pending"] = {
            "1:10": {"identity_id": identity_id, "target": "march_7777"}
        }
        text = (
            "⚡ 【天道裁决 · 斩妖除魔】 ⚡\n\n"
            "对象 【march_7777】 面对天道迷障无言以对，已被确认为【挂机傀儡】！\n"
            "天罚降临，其神魂已被永久剥离，打入无尽死牢！"
        )

        with (
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tiandao_judgement, "save_state"),
        ):
            handled = await tiandao_judgement.handle_tiandao_judgement_punishment(text, 1_700_000_000.0)

        self.assertTrue(handled)
        self.assertFalse(state_module.get_identity_enabled(identity_id))
        self.assertEqual({}, state_module.state["pending_tasks"])
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        audit_mock.assert_awaited_once()
        self.assertIn("天道裁决命中本地身份", audit_mock.await_args.args[0])
        self.assertIn("@march_7777", audit_mock.await_args.args[0])
        self.assertEqual("high", audit_mock.await_args.kwargs.get("priority"))

    async def test_proof_send_failure_retries_only_once(self):
        identity_id = self._prepare_identity()
        now = 1_700_000_000.0
        state_module.state["tiandao_judgement_enabled"] = True
        state_module.state["tiandao_judgement_pending"] = {
            "1:10": {
                "target": "march_7777",
                "token": "ABCD",
                "command": ".自证",
                "identity_id": identity_id,
                "question": "炼制玄铁剑消耗灵石 加 十 等于？",
                "answer": "20",
                "due_at": now - 1,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 10,
                "chat_id": 1,
                "retry_count": 0,
            }
        }

        with (
            patch.object(tiandao_judgement, "send_game_command", new=AsyncMock(return_value=None)) as send_mock,
            patch.object(tiandao_judgement, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(tiandao_judgement, "_schedule_tiandao_judgement_due_task"),
            patch.object(tiandao_judgement, "save_state"),
            patch.object(tiandao_judgement.time, "time", return_value=now),
        ):
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)
            pending = state_module.state["tiandao_judgement_pending"]
            self.assertIn("1:10", pending)
            self.assertEqual(1, pending["1:10"]["retry_count"])

            pending["1:10"]["due_at"] = now - 1
            await tiandao_judgement.run_tiandao_judgement_scheduler(now)

        self.assertEqual(2, send_mock.await_count)
        self.assertEqual({}, state_module.state["tiandao_judgement_pending"])
        self.assertIn("已重试 1 次，停止重试", audit_mock.await_args_list[-1].args[0])


if __name__ == "__main__":
    unittest.main()
