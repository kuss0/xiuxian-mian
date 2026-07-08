import atexit
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
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

from model import app
from model import app_message_log
from model import app_runtime
from model import state as state_module


class MessageLogButtonTests(unittest.TestCase):
    def tearDown(self):
        app_runtime._runtime_log_claims.clear()

    def test_extract_message_log_buttons_records_text_type_and_host(self):
        callback_button = SimpleNamespace(text="稳固道心", button=SimpleNamespace(text="稳固道心", data=b"ok"))
        url_button = SimpleNamespace(text="查看", button=SimpleNamespace(text="查看", url="https://example.com/path"))
        event = SimpleNamespace(message=SimpleNamespace(buttons=[[callback_button, url_button]]))

        buttons = app_message_log._extract_message_log_buttons(event)

        self.assertEqual("稳固道心", buttons[0][0]["text"])
        self.assertEqual("callback", buttons[0][0]["type"])
        self.assertTrue(buttons[0][0]["has_callback_data"])
        self.assertEqual("url", buttons[0][1]["type"])
        self.assertEqual("example.com", buttons[0][1]["url_host"])

    def test_han_tianzun_bot_sender_is_learned_as_game_bot(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state["game_bot_ids"] = []
            event = SimpleNamespace(
                sender_id=424242,
                sender=SimpleNamespace(bot=True, first_name="韩天尊", last_name=""),
            )
            with patch("model.app.save_state"), patch("model.app.send_audit_log", new=AsyncMock()) as audit_mock:
                handled = asyncio.run(app._is_game_bot_event(event))

            self.assertTrue(handled)
            self.assertTrue(getattr(event, "_xiuxian_sender_is_game_bot"))
            self.assertIn(424242, state_module.get_game_bot_ids())
            audit_mock.assert_awaited_once()
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_game_group_message_log_deduplicates_runtime_event(self):
        event = SimpleNamespace(
            id=91001,
            chat_id=-100910,
            sender_id=7900199668,
            raw_text="【测试】",
            reply_to=SimpleNamespace(reply_to_msg_id=123, reply_to_top_id=456),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_game_group_id", return_value=-100910):
            app_message_log._append_game_group_message_log(event, event_type="message")
            app_message_log._append_game_group_message_log(event, event_type="message")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(1, len(rows))
        self.assertEqual(91001, rows[0]["message_id"])
        self.assertEqual(123, rows[0]["reply_to_msg_id"])
        self.assertEqual(456, rows[0]["topic_id"])

    def test_game_group_message_log_deduplicates_across_runtime_claim_reset(self):
        event = SimpleNamespace(
            id=91021,
            chat_id=-100910,
            sender_id=7900199668,
            raw_text="【跨进程测试】",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_game_group_id", return_value=-100910):
            app_message_log._append_game_group_message_log(event, event_type="message")
            app_runtime._runtime_log_claims.clear()
            app_message_log._append_game_group_message_log(event, event_type="message")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(1, len(rows))
        self.assertEqual(91021, rows[0]["message_id"])

    def test_game_group_message_log_records_listener_account(self):
        listener_client = SimpleNamespace(name="listener")
        event = SimpleNamespace(
            id=91011,
            chat_id=-100910,
            sender_id=7900199668,
            client=listener_client,
            raw_text="【测试】",
            reply_to=SimpleNamespace(reply_to_msg_id=123, reply_to_top_id=456),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_game_group_id", return_value=-100910), \
                patch.object(app_message_log, "get_all_clients", return_value={301299112: listener_client}):
            app_message_log._append_game_group_message_log(event, event_type="message")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(1, len(rows))
        self.assertEqual(301299112, rows[0]["listener_account_id"])

    def test_game_group_message_log_listener_filter_does_not_consume_blocked_event(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        listener_client = SimpleNamespace(name="listener")
        other_client = SimpleNamespace(name="other")
        blocked_event = SimpleNamespace(
            id=91012,
            chat_id=-100910,
            sender_id=7900199668,
            client=other_client,
            raw_text="【测试】",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )
        allowed_event = SimpleNamespace(
            id=91012,
            chat_id=-100910,
            sender_id=7900199668,
            client=listener_client,
            raw_text="【测试】",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )
        try:
            state_module._meta_state["game_group_id"] = -100910
            state_module.set_game_listener_account_ids([301299112])
            with tempfile.TemporaryDirectory() as tmpdir, \
                    patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                    patch.object(app_message_log, "get_game_group_id", return_value=-100910), \
                    patch.object(app_message_log, "get_all_clients", return_value={301299112: listener_client, 8659059191: other_client}):
                app_message_log._append_game_group_message_log(blocked_event, event_type="message")
                app_message_log._append_game_group_message_log(allowed_event, event_type="message")
                rows = [
                    json.loads(line)
                    for line in next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").splitlines()
                ]
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

        self.assertEqual(1, len(rows))
        self.assertEqual(301299112, rows[0]["listener_account_id"])

    def test_game_group_listener_filter_uses_configured_account_ids(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        listener_client = SimpleNamespace(name="listener")
        other_client = SimpleNamespace(name="other")
        try:
            state_module._meta_state["game_group_id"] = -100910
            state_module.set_game_listener_account_ids([301299112, 7538826434])
            allowed_event = SimpleNamespace(chat_id=-100910, client=listener_client)
            blocked_event = SimpleNamespace(chat_id=-100910, client=other_client)

            with patch.object(app, "get_all_clients", return_value={301299112: listener_client, 8659059191: other_client}):
                self.assertTrue(app._is_game_group_listener_event(allowed_event))
                self.assertFalse(app._is_game_group_listener_event(blocked_event))

            state_module.set_game_listener_account_ids([])
            with patch.object(app, "get_all_clients", return_value={8659059191: other_client}):
                self.assertTrue(app._is_game_group_listener_event(blocked_event))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_game_group_edit_log_keeps_distinct_text_for_same_message(self):
        first_edit = SimpleNamespace(
            id=91002,
            chat_id=-100910,
            sender_id=7900199668,
            raw_text="【坠魔心劫·第1轮已定】\n【坠魔心劫·第2轮】",
            reply_to=SimpleNamespace(reply_to_msg_id=123, reply_to_top_id=456),
            message=SimpleNamespace(buttons=[]),
        )
        second_edit = SimpleNamespace(
            id=91002,
            chat_id=-100910,
            sender_id=7900199668,
            raw_text="【坠魔心劫·第2轮已定】\n【坠魔心劫·第3轮】",
            reply_to=SimpleNamespace(reply_to_msg_id=123, reply_to_top_id=456),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_game_group_id", return_value=-100910):
            app_message_log._append_game_group_message_log(first_edit, event_type="edit")
            app_message_log._append_game_group_message_log(second_edit, event_type="edit")
            app_message_log._append_game_group_message_log(second_edit, event_type="edit")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(2, len(rows))
        self.assertEqual([91002, 91002], [row["message_id"] for row in rows])
        self.assertIn("第2轮", rows[0]["text"])
        self.assertIn("第3轮", rows[1]["text"])

    def test_non_bot_game_group_edit_routes_replica_progress(self):
        event = SimpleNamespace(
            id=91003,
            chat_id=-100910,
            sender_id=8219248252,
            raw_text="[战利品结算·夺鼎]\n所有队员均获得5000修为和500贡献!",
            reply_to=SimpleNamespace(reply_to_msg_id=123, reply_to_top_id=456),
            message=SimpleNamespace(buttons=[]),
        )

        with patch.object(app, "_append_replica_group_message_log", return_value=False), \
                patch.object(app, "_append_replica_dispatch_group_message_log", return_value=False), \
                patch.object(app, "_append_game_group_message_log"), \
                patch.object(app, "get_game_group_id", return_value=-100910), \
                patch.object(app, "_is_game_bot_event", new=AsyncMock(return_value=False)), \
                patch.object(app, "_handle_replica_progress_event", new=AsyncMock(return_value=True)) as progress_mock, \
                patch.object(app, "_handle_suspected_game_bot_reply", new=AsyncMock(return_value=False)):
            asyncio.run(app.on_message_edited(event))

        progress_mock.assert_awaited_once()
        self.assertEqual("edit", progress_mock.await_args.kwargs["event_type"])

    def test_replica_group_message_log_deduplicates_but_still_claims_group(self):
        listener_client = SimpleNamespace(name="listener")
        event = SimpleNamespace(
            id=92001,
            chat_id=-100920,
            sender_id=7900199668,
            client=listener_client,
            raw_text="副本群消息",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_replica_group_ids", return_value=[-100920]), \
                patch.object(app_message_log, "get_replica_listener_account_map", return_value={"-100920": 93001}), \
                patch.object(app_message_log, "get_all_clients", return_value={93001: listener_client}):
            first = app_message_log._append_replica_group_message_log(event, event_type="message")
            second = app_message_log._append_replica_group_message_log(event, event_type="message")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("replica-*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, len(rows))
        self.assertEqual(93001, rows[0]["listener_account_id"])

    def test_replica_group_message_log_rejects_log_group_overlap(self):
        listener_client = SimpleNamespace(name="listener")
        event = SimpleNamespace(
            id=92004,
            chat_id=-100920,
            sender_id=7900199668,
            client=listener_client,
            raw_text=".查询副本",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )

        with patch.object(app_message_log, "LOG_GROUP_ID", -100920), \
                patch.object(app_message_log, "get_replica_group_ids", return_value=[-100920]), \
                patch.object(app_message_log, "get_replica_listener_account_map", return_value={"-100920": 93001}), \
                patch.object(app_message_log, "get_all_clients", return_value={93001: listener_client}):
            self.assertFalse(app_message_log._append_replica_group_message_log(event, event_type="message"))

    def test_replica_group_bot_429_backs_off_and_falls_back_without_audit_storm(self):
        class FallbackClient:
            async def send_message(self, chat_id, text, **kwargs):
                return SimpleNamespace(id=94001)

        async def run_case():
            app_message_log._REPLICA_BOT_BACKOFF_UNTIL = 0
            error_text = 'HTTP 429: {"ok":false,"parameters":{"retry_after":25}}'
            with patch.object(app_message_log, "LOG_SEND_MODE", "bot"), \
                    patch.object(app_message_log, "_send_replica_group_via_bot", return_value=(False, 0, error_text)), \
                    patch.object(app_message_log, "_append_sent_replica_group_message_log") as sent_log_mock, \
                    patch.object(app_message_log, "send_audit_log", new=AsyncMock()) as audit_mock:
                msg = await app_message_log._send_replica_group_message(
                    FallbackClient(),
                    -100920,
                    "坠魔谷结算",
                    listener_account_id=93001,
                )
                return msg, sent_log_mock, audit_mock, app_message_log._REPLICA_BOT_BACKOFF_UNTIL

        msg, sent_log_mock, audit_mock, backoff_until = asyncio.run(run_case())

        self.assertEqual(94001, msg.id)
        self.assertGreater(backoff_until, 0)
        sent_log_mock.assert_called_once()
        self.assertEqual("account", sent_log_mock.call_args.kwargs["sent_via"])
        audit_mock.assert_not_awaited()

    def test_replica_button_event_can_use_log_group_overlap_listener(self):
        listener_client = SimpleNamespace(name="listener")
        event = SimpleNamespace(
            id=92005,
            chat_id=-100920,
            sender_id=123456,
            client=listener_client,
            raw_text=".查询副本",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
            _replica_button_listener_account_id=93001,
        )

        with patch.object(app_message_log, "LOG_GROUP_ID", -100920), \
                patch.object(app_message_log, "get_replica_group_ids", return_value=[-100920]), \
                patch.object(app_message_log, "get_replica_listener_account_map", return_value={"-100920": 93001}), \
                patch.object(app_message_log, "get_all_clients", return_value={93001: listener_client}):
            self.assertEqual(93001, app_message_log._get_replica_event_listener_account_id(event))

    def test_replica_dispatch_group_message_log_uses_separate_claim_scope(self):
        listener_client = SimpleNamespace(name="dispatch-listener")
        event = SimpleNamespace(
            id=92002,
            chat_id=-100921,
            sender_id=424242,
            client=listener_client,
            raw_text=".苍坤洞府 123 @first",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(app_message_log, "MESSAGES_DIR", tmpdir), \
                patch.object(app_message_log, "get_replica_dispatch_group_ids", return_value=[-100921]), \
                patch.object(app_message_log, "get_replica_dispatch_listener_account_map", return_value={"-100921": 93002}), \
                patch.object(app_message_log, "get_all_clients", return_value={93002: listener_client}):
            first = app_message_log._append_replica_dispatch_group_message_log(event, event_type="message")
            second = app_message_log._append_replica_dispatch_group_message_log(event, event_type="message")
            rows = [
                json.loads(line)
                for line in next(Path(tmpdir).glob("replica-*.log")).read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, len(rows))
        self.assertEqual(93002, rows[0]["listener_account_id"])
        self.assertEqual("dispatch", rows[0]["replica_group_role"])

    def test_replica_dispatch_group_message_log_rejects_game_group_overlap(self):
        listener_client = SimpleNamespace(name="dispatch-listener")
        event = SimpleNamespace(
            id=92003,
            chat_id=-100922,
            sender_id=424242,
            client=listener_client,
            raw_text="真实游戏群回包",
            reply_to=SimpleNamespace(reply_to_msg_id=0, reply_to_top_id=0),
            message=SimpleNamespace(buttons=[]),
        )

        with patch.object(app_message_log, "get_game_group_id", return_value=-100922), \
                patch.object(app_message_log, "get_replica_dispatch_group_ids", return_value=[-100922]), \
                patch.object(app_message_log, "get_replica_dispatch_listener_account_map", return_value={"-100922": 93002}), \
                patch.object(app_message_log, "get_all_clients", return_value={93002: listener_client}):
            self.assertFalse(app_message_log._append_replica_dispatch_group_message_log(event, event_type="message"))

    def test_han_tianzun_name_variants_require_bot_identity(self):
        self.assertTrue(
            app._entity_is_han_tianzun_bot(
                SimpleNamespace(bot=True, first_name="韩", last_name="天尊")
            )
        )
        self.assertTrue(
            app._entity_is_han_tianzun_bot(
                SimpleNamespace(bot=True, first_name="韩 天 尊", last_name="")
            )
        )
        self.assertTrue(
            app._entity_is_han_tianzun_bot(
                SimpleNamespace(bot=True, first_name="", last_name="", title="韩天尊")
            )
        )
        self.assertTrue(
            app._entity_is_han_tianzun_bot(
                SimpleNamespace(bot=True, first_name="陆天尊", last_name="")
            )
        )
        self.assertFalse(
            app._entity_is_han_tianzun_bot(
                SimpleNamespace(bot=False, first_name="韩天尊", last_name="")
            )
        )

    def test_han_tianzun_sender_can_be_loaded_from_event(self):
        class LazySenderEvent:
            sender_id = 525252
            sender = None

            async def get_sender(self):
                return SimpleNamespace(bot=True, first_name="韩", last_name="天尊")

        snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state["game_bot_ids"] = []
            event = LazySenderEvent()
            with patch("model.app.save_state"), patch("model.app.send_audit_log", new=AsyncMock()):
                handled = asyncio.run(app._is_game_bot_event(event))

            self.assertTrue(handled)
            self.assertTrue(getattr(event, "_xiuxian_sender_is_game_bot"))
            self.assertIn(525252, state_module.get_game_bot_ids())
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_suspected_non_bot_sender_is_not_routed_as_game_bot(self):
        event = SimpleNamespace(
            id=616161,
            sender_id=8214307121,
            sender=SimpleNamespace(bot=False, first_name="韩天尊"),
        )

        with patch.object(app, "_resolve_identity_sender_id", return_value=0), \
                patch.object(app, "_resolve_event_reply", new=AsyncMock()) as resolve_mock:
            handled = asyncio.run(app._handle_suspected_game_bot_reply(
                event,
                "【问道】天机不可频繁窥探",
                1000.0,
            ))

        self.assertFalse(handled)
        resolve_mock.assert_not_awaited()

    def test_suspected_non_bot_candidate_is_not_learned_after_threshold(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        app._suspected_game_bot_hits.clear()
        try:
            state_module._meta_state["game_bot_ids"] = []
            with patch("model.app.save_state") as save_mock, \
                    patch("model.app.send_audit_log", new=AsyncMock()):
                for _ in range(app.UNKNOWN_GAME_BOT_LEARN_THRESHOLD):
                    asyncio.run(app._record_suspected_game_bot(
                        8214307121,
                        "wendao",
                        "【问道】天机不可频繁窥探",
                        verified_bot=False,
                    ))

            self.assertNotIn(8214307121, state_module.get_game_bot_ids())
            save_mock.assert_not_called()
        finally:
            app._suspected_game_bot_hits.clear()
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_suspected_verified_bot_candidate_is_learned_after_threshold(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        app._suspected_game_bot_hits.clear()
        try:
            state_module._meta_state["game_bot_ids"] = []
            with patch("model.app.save_state") as save_mock, \
                    patch("model.app.send_audit_log", new=AsyncMock()):
                for _ in range(app.UNKNOWN_GAME_BOT_LEARN_THRESHOLD):
                    asyncio.run(app._record_suspected_game_bot(
                        990990,
                        "wendao",
                        "【问道】天机不可频繁窥探",
                        verified_bot=True,
                    ))

            self.assertIn(990990, state_module.get_game_bot_ids())
            save_mock.assert_called_once()
        finally:
            app._suspected_game_bot_hits.clear()
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_negative_channel_identity_sender_resolves_known_identity(self):
        with patch("model.app.get_identity_ids", return_value=[3800619925]):
            self.assertEqual(3800619925, app._resolve_identity_sender_id(3800619925))
            self.assertEqual(3800619925, app._resolve_identity_sender_id(-1003800619925))
            self.assertEqual(0, app._resolve_identity_sender_id(-100123456789))
            self.assertEqual(0, app._resolve_identity_sender_id("bad"))

    def test_concubine_affinity_fallback_requires_identity_hint_at_dispatch(self):
        @contextmanager
        def fake_use_identity(_identity_id):
            yield

        event = SimpleNamespace(id=93001, chat_id=-100930)
        text = "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。"

        async def run_case():
            with patch.object(app, "_claim_runtime_event", return_value=True), \
                    patch.object(app, "get_identity_ids", return_value=[991101]), \
                    patch.object(app, "get_identity_enabled", return_value=True), \
                    patch.object(app, "use_identity", fake_use_identity), \
                    patch.object(app, "handle_concubine_affinity_event", new=AsyncMock(return_value=False)) as handler:
                await app._dispatch_concubine_affinity_fallbacks(event, text, 1_700_000_000.0)

            handler.assert_awaited_once()
            args, kwargs = handler.await_args
            self.assertIs(args[2], event)
            self.assertTrue(kwargs.get("require_identity_hint"))

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
