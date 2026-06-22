import atexit
import ast
import asyncio
import copy
import json
import sys
import time
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

from model import app_replica, app_runtime, replica_query_aggregator_client, runtime
from model import app_message_log
from model import state as state_module


class ReplicaAbsorbTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["replica_participant_identity_ids"] = []
        state_module._meta_state["replica_run_state"] = {}
        state_module._meta_state["replica_group_ids"] = []
        state_module._meta_state["replica_group_id"] = 0
        state_module._meta_state["replica_listener_account_map"] = {}
        state_module._meta_state["replica_listener_account_id"] = 0
        state_module._meta_state["replica_dispatch_group_ids"] = []
        state_module._meta_state["replica_dispatch_listener_account_map"] = {}
        state_module._meta_state["replica_dispatch_participant_identity_ids"] = []
        state_module._meta_state["storage_bag_records"] = {}
        state_module._meta_state["tianjige_dao_path_records"] = {}
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()

    def tearDown(self):
        app_runtime._runtime_event_claims.clear()
        app_runtime._runtime_message_consumed.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_app_replica_sends_carry_send_intent_metadata(self):
        source_path = PROJECT_ROOT / "model" / "app_replica.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        missing_lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if func_name != "send_game_command":
                continue
            has_source_module = any(keyword.arg == "source_module" for keyword in node.keywords)
            has_replica_intent = any(
                keyword.arg is None
                and isinstance(keyword.value, ast.Call)
                and isinstance(keyword.value.func, ast.Name)
                and keyword.value.func.id == "_replica_send_intent"
                for keyword in node.keywords
            )
            if not has_source_module and not has_replica_intent:
                missing_lines.append(node.lineno)

        self.assertEqual([], missing_lines)

    def test_replica_group_branch_fast_tracks_explicit_commands(self):
        source_text = (PROJECT_ROOT / "model" / "app.py").read_text(encoding="utf-8")
        branch_pos = source_text.index('if _append_replica_group_message_log(event, event_type="message"):')
        fast_check_pos = source_text.index("is_replica_group_command_text(text)", branch_pos)
        fast_command_pos = source_text.index("_handle_replica_group_command(event)", fast_check_pos)
        reply_resolve_pos = source_text.index("_resolve_event_reply(event)", branch_pos)

        self.assertLess(fast_check_pos, reply_resolve_pos)
        self.assertLess(fast_command_pos, reply_resolve_pos)

    def test_replica_group_command_predicate_is_kunwu_scoped(self):
        self.assertTrue(app_replica.is_replica_group_command_text(".查询副本"))
        self.assertTrue(app_replica.is_replica_group_command_text(".开启副本 @leader 昆"))
        self.assertTrue(app_replica.is_replica_group_command_text(".进入昆吾山"))
        self.assertTrue(app_replica.is_replica_group_command_text(".解散副本"))
        self.assertFalse(app_replica.is_replica_group_command_text(".查询"))
        self.assertFalse(app_replica.is_replica_group_command_text(".查询 leader"))
        self.assertFalse(app_replica.is_replica_group_command_text(".匹配虚天殿 914"))
        self.assertFalse(app_replica.is_replica_group_command_text(".开启副本 @leader"))
        self.assertFalse(app_replica.is_replica_group_command_text(".开启副本 @leader 虚"))
        self.assertFalse(app_replica.is_replica_group_command_text(".进入虚天殿"))

    def test_replica_group_branch_processes_game_text_before_fallback_commands(self):
        source_text = (PROJECT_ROOT / "model" / "app.py").read_text(encoding="utf-8")
        branch_pos = source_text.index('if _append_replica_group_message_log(event, event_type="message"):')
        reply_resolve_pos = source_text.index("_resolve_event_reply(event)", branch_pos)
        auto_pos = source_text.index("_handle_virtual_hall_auto_game_event", reply_resolve_pos)
        progress_pos = source_text.index("_handle_replica_progress_event", reply_resolve_pos)
        command_pos = source_text.index("_handle_replica_group_command", progress_pos)

        self.assertLess(auto_pos, command_pos)
        self.assertLess(progress_pos, command_pos)

    def test_lightweight_open_usage_is_html_escaped(self):
        html_usage = app_replica._format_lightweight_open_usage(html=True)
        self.assertIn(
            "<code>.开启副本 @用户名 &lt;虚天|苍坤|坠魔|黄龙|昆吾|落云&gt;</code>",
            html_usage,
        )
        self.assertNotIn("<虚天|苍坤|坠魔|黄龙|昆吾|落云>", html_usage)

        fallback = app_replica._format_lightweight_next_commands(
            ".查询副本",
            app_replica._REPLICA_LIGHTWEIGHT_OPEN_USAGE,
            html=True,
        )
        self.assertIn(
            "<code>.开启副本 @用户名 &lt;虚天|苍坤|坠魔|黄龙|昆吾|落云&gt;</code>",
            fallback,
        )
        self.assertNotIn("<虚天|苍坤|坠魔|黄龙|昆吾|落云>", fallback)

    def _prepare_replica_identity(self, identity_id=991201, username="leader"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username=username,
            enabled=True,
            spiritual_root_attrs="金",
        )
        state_module.set_replica_participant_identity_ids([identity_id])
        return identity_id

    def _register_replica_identity(self, identity_id, username, root_attrs="金", professions="破军", realm="结丹初期", root_type="真灵根", sect_name=""):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username=username,
            enabled=True,
            realm=realm,
            spiritual_root_type=root_type,
            spiritual_root_attrs=root_attrs,
            replica_professions=professions,
            sect_name=sect_name,
        )
        return identity_id

    def _prepare_replica_group(self, participant_ids=None):
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": 9001})
        if participant_ids is not None:
            state_module.set_replica_participant_identity_ids(participant_ids)
        return SimpleNamespace(raw_text="", chat_id=-100777, sender_id=9001, id=100, client=SimpleNamespace(name="listener"))

    def _button_texts(self, buttons):
        texts = []
        for row in buttons or []:
            row_items = row if isinstance(row, (list, tuple)) else [row]
            for item in row_items:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(item["text"])
        return texts

    def _close_scheduled(self, coro):
        coro.close()

    def _button_payload_by_text(self, buttons, text):
        for row in buttons or []:
            row_items = row if isinstance(row, (list, tuple)) else [row]
            for item in row_items:
                if not isinstance(item, dict) or item.get("text") != text:
                    continue
                return app_replica._get_replica_button_action(item.get("callback_data") or "")[1].get("payload", {})
        return {}

    def test_replica_group_listener_falls_back_to_registered_event_client(self):
        listener_client = SimpleNamespace(name="listener")
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({})
        event = SimpleNamespace(chat_id=-100777, client=listener_client)

        with patch("model.app_message_log.get_all_clients", return_value={301299112: listener_client}):
            self.assertEqual(301299112, app_message_log._get_replica_event_listener_account_id(event))

    def test_replica_group_listener_empty_map_rejects_unregistered_client(self):
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({})
        event = SimpleNamespace(chat_id=-100777, client=SimpleNamespace(name="unknown"))

        with patch("model.app_message_log.get_all_clients", return_value={301299112: SimpleNamespace(name="other")}):
            self.assertEqual(0, app_message_log._get_replica_event_listener_account_id(event))

    def test_replica_query_aggregator_config_is_used_only_when_complete(self):
        state_module.set_replica_query_aggregator_config({
            "base_url": "https://example.invalid/api/",
            "client_id": "client-a",
            "secret": "secret-a",
        })

        self.assertEqual(
            {
                "base_url": "https://example.invalid/api",
                "client_id": "client-a",
                "secret": "secret-a",
            },
            app_replica._get_replica_query_aggregator_submit_config(),
        )

        state_module.set_replica_query_aggregator_config({"base_url": "https://example.invalid/api"})
        self.assertEqual({}, app_replica._get_replica_query_aggregator_submit_config())

    def test_replica_query_aggregator_body_carries_source_and_identity_metadata(self):
        body = replica_query_aggregator_client._build_query_result_body(
            query_message_id=456,
            query_text=".查询",
            query_filter="",
            source_chat_id=-100777,
            source_message_id=456,
            identity_id=9001,
            listener_account_id=9001,
            client_id="client-a",
            reply_text="@foo | 金 | 空闲",
            generated_at=123.0,
        )
        payload = json.loads(body.decode("utf-8"))

        self.assertEqual(-100777, payload["source_id"])
        self.assertEqual(-100777, payload["source_chat_id"])
        self.assertEqual(456, payload["source_message_id"])
        self.assertEqual(9001, payload["identity_id"])
        self.assertEqual(9001, payload["send_as_id"])
        self.assertEqual(9001, payload["listener_account_id"])
        self.assertEqual("client-a", payload["client_id"])

    def test_replica_query_command_submits_group_and_listener_metadata(self):
        state_module.set_replica_query_aggregator_config({
            "base_url": "https://example.invalid/api",
            "client_id": "client-a",
            "secret": "secret-a",
        })
        event = self._prepare_replica_group([])
        event.raw_text = ".查询"
        event.id = 456

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica.submit_replica_query_result", new=AsyncMock(return_value={"ok": True, "session_id": "s1", "accepted_lines": 0})) as submit_mock, \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_replica_query_command(event)
                return handled, submit_mock.await_args.kwargs, send_mock.await_count

        handled, submit_kwargs, send_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(-100777, submit_kwargs["source_id"])
        self.assertEqual(-100777, submit_kwargs["source_chat_id"])
        self.assertEqual(456, submit_kwargs["source_message_id"])
        self.assertEqual(9001, submit_kwargs["identity_id"])
        self.assertEqual(9001, submit_kwargs["send_as_id"])
        self.assertEqual(9001, submit_kwargs["listener_account_id"])
        self.assertEqual("client-a", submit_kwargs["client_id"])
        self.assertEqual(0, send_count)

    def test_replica_query_command_falls_back_when_aggregator_fails(self):
        state_module.set_replica_query_aggregator_config({
            "base_url": "https://example.invalid/api",
            "client_id": "client-a",
            "secret": "secret-a",
        })
        event = self._prepare_replica_group([])
        event.raw_text = ".查询"
        event.id = 457

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica.submit_replica_query_result", new=AsyncMock(side_effect=replica_query_aggregator_client.ReplicaQueryAggregatorError("down"))), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=705))) as send_mock:
                handled = await app_replica._handle_replica_query_command(event)
                return handled, send_mock.await_count, send_mock.await_args.args[2]

        handled, send_count, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(1, send_count)
        self.assertIn("当前没有已勾选且带 username 的副本参与身份", reply_text)

    def test_replica_query_command_replies_when_no_candidates(self):
        event = self._prepare_replica_group([])
        event.raw_text = ".查询"
        event.id = 321

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=701))):
                handled = await app_replica._handle_replica_query_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("当前没有已勾选且带 username 的副本参与身份", reply_text)

    def test_replica_query_command_deduplicates_same_runtime_event(self):
        event = self._prepare_replica_group([])
        event.raw_text = ".查询"
        event.id = 323

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=703))) as send_mock:
                first_handled = await app_replica._handle_replica_query_command(event)
                second_handled = await app_replica._handle_replica_query_command(event)
                return first_handled, second_handled, send_mock.await_count

        first_handled, second_handled, send_count = asyncio.run(run_test())
        self.assertTrue(first_handled)
        self.assertTrue(second_handled)
        self.assertEqual(1, send_count)

    def test_replica_group_command_dispatches_query_command(self):
        event = self._prepare_replica_group([])
        event.raw_text = ".查询"
        event.id = 322

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                handled = await app_replica._handle_replica_group_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("当前没有已勾选且带 username 的副本参与身份", reply_text)

    def test_replica_dispatch_group_command_dispatches_query_command(self):
        first_id = self._register_replica_identity(991204, "first")
        second_id = self._register_replica_identity(991205, "second")
        state_module.set_replica_participant_identity_ids([first_id, second_id])
        state_module.set_replica_dispatch_participant_identity_ids([first_id])
        event = SimpleNamespace(raw_text=".查询", chat_id=-100888, sender_id=4444, id=88004, client=SimpleNamespace(name="dispatch-listener"))

        async def run_test():
            with patch("model.app_replica._get_replica_dispatch_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=704))) as send_mock:
                handled = await app_replica._handle_replica_dispatch_group_command(event)
                reply_text = send_mock.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("@first", reply_text)
        self.assertNotIn("@second", reply_text)

    def test_replica_dispatch_group_command_still_allows_external_pull(self):
        first_id = self._register_replica_identity(991204, "first")
        second_id = self._register_replica_identity(991205, "second")
        state_module.set_replica_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_participant_identity_ids([first_id])
        event = SimpleNamespace(raw_text=".虚天殿 456 @first @second", chat_id=-100888, sender_id=4444, id=88005, client=SimpleNamespace(name="dispatch-listener"))

        async def run_test():
            with patch("model.app_replica._get_replica_dispatch_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=778))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                handled = await app_replica._handle_replica_dispatch_group_command(event)
                return handled, send_mock.await_args

        handled, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".加入副本 456", send_args.args[0])
        self.assertEqual(first_id, send_args.kwargs["send_as_id"])

    def test_replica_dispatch_group_command_ignores_non_dispatch_participants(self):
        first_id = self._register_replica_identity(991206, "first")
        second_id = self._register_replica_identity(991207, "second")
        state_module.set_replica_participant_identity_ids([first_id, second_id])
        state_module.set_replica_dispatch_participant_identity_ids([first_id])
        event = SimpleNamespace(raw_text=".虚天殿 456 @second", chat_id=-100888, sender_id=4444, id=88007, client=SimpleNamespace(name="dispatch-listener"))

        async def run_test():
            with patch("model.app_replica._get_replica_dispatch_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_replica_dispatch_group_command(event)
                return handled, send_mock.await_count

        handled, send_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(0, send_count)

    def test_replica_group_command_dispatches_virtual_hall_match(self):
        event = self._prepare_replica_group([])
        event.raw_text = ".匹配虚天殿 914"
        state_module.set_replica_virtual_hall_match_enabled(event.chat_id, True)

        def close_scheduled(coro):
            coro.close()

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._run_virtual_hall_match", new=AsyncMock(return_value=True)) as match_mock, \
                    patch("model.app_replica._fire_and_forget", side_effect=close_scheduled):
                handled = await app_replica._handle_replica_group_command(event)
                return handled, match_mock.call_args

        handled, call_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual("914", call_args.args[0])
        self.assertEqual(9001, call_args.kwargs["listener_account_id"])

    def test_virtual_hall_match_text_falls_back_when_aggregator_fails(self):
        state_module.set_replica_query_aggregator_config({
            "base_url": "https://example.invalid/api",
            "client_id": "client-a",
            "secret": "secret-a",
        })

        async def run_test():
            with patch("model.app_replica.submit_virtual_hall_recommendation", new=AsyncMock(side_effect=replica_query_aggregator_client.ReplicaQueryAggregatorError("down"))), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=706))) as send_mock:
                await app_replica._send_virtual_hall_match_text(
                    SimpleNamespace(name="listener"),
                    -100777,
                    "推荐配置：虚天殿",
                    listener_account_id=9001,
                    html=True,
                    room_id="914",
                    query_message_id=456,
                )
                return send_mock.await_count, send_mock.await_args.args[2]

        send_count, sent_text = asyncio.run(run_test())
        self.assertEqual(1, send_count)
        self.assertEqual("推荐配置：虚天殿", sent_text)

    def test_virtual_hall_query_wait_returns_first_seen_candidates(self):
        candidate = {"username": "@first", "username_key": "@first"}

        async def run_test():
            with patch("model.app_replica._find_replica_query_log_candidates", side_effect=[[], [candidate]]) as find_mock, \
                    patch("model.app_replica.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                result = await app_replica._wait_replica_query_log_candidates(
                    456,
                    time.time(),
                    timeout_sec=1,
                    chat_id=-100777,
                )
                return result, find_mock.call_count, sleep_mock.await_count

        result, find_count, sleep_count = asyncio.run(run_test())
        self.assertEqual([candidate], result)
        self.assertEqual(2, find_count)
        self.assertEqual(1, sleep_count)

    def test_virtual_hall_missing_dispatch_preclaims_before_send_returns(self):
        flow = {
            "flow_id": "flow-missing",
            "phase": "monitoring",
            "room_id": "456",
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "missing_join_requests": {},
        }
        accounting = {
            "missing_dispatch": ["@first"],
            "shortage": 0,
        }
        now = 1000.0
        send_calls = []
        requests_seen_during_send = {}
        second_result = []

        async def fake_send(send_flow, command, **_kwargs):
            send_calls.append(command)
            if len(send_calls) == 1:
                requests_seen_during_send.update(copy.deepcopy(send_flow.get("missing_join_requests") or {}))
                second_result.append(
                    await app_replica._maybe_send_virtual_hall_auto_missing_dispatch_command(
                        send_flow,
                        accounting,
                        now + 0.1,
                    )
                )
            return SimpleNamespace(id=901)

        async def run_test():
            with patch("model.app_replica._send_virtual_hall_auto_replica_notice", side_effect=fake_send), \
                    patch("model.app_replica._schedule_virtual_hall_auto_deferred_team_check", return_value=True):
                return await app_replica._maybe_send_virtual_hall_auto_missing_dispatch_command(flow, accounting, now)

        handled = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual([False], second_result)
        self.assertEqual([".虚天殿 456 @first"], send_calls)
        self.assertEqual(1, requests_seen_during_send["@first"]["count"])
        self.assertTrue(requests_seen_during_send["@first"]["pending"])
        self.assertEqual(1, flow["missing_join_requests"]["@first"]["count"])
        self.assertFalse(flow["missing_join_requests"]["@first"]["pending"])
        self.assertEqual(901, flow["missing_join_requests"]["@first"]["msg_id"])

    def test_virtual_hall_missing_dispatch_send_failure_rolls_back_preclaim(self):
        flow = {
            "flow_id": "flow-missing-fail",
            "phase": "monitoring",
            "room_id": "456",
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "missing_join_requests": {},
        }
        accounting = {
            "missing_dispatch": ["@first"],
            "shortage": 0,
        }
        now = time.time()

        async def run_test():
            with patch("model.app_replica._send_virtual_hall_auto_replica_notice", new=AsyncMock(return_value=None)):
                return await app_replica._maybe_send_virtual_hall_auto_missing_dispatch_command(flow, accounting, now)

        handled = asyncio.run(run_test())

        self.assertFalse(handled)
        request = flow["missing_join_requests"]["@first"]
        self.assertEqual(0, request["count"])
        self.assertEqual(0, request["last_sent_at"])
        self.assertFalse(request["pending"])
        self.assertEqual(now, request["send_failed_at"])

    def test_parse_cangkun_dispatch_command(self):
        replica_kind, room_id, usernames = app_replica._parse_replica_dispatch_command(".苍坤洞府 123 @foo @bar @foo")

        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, replica_kind)
        self.assertEqual("123", room_id)
        self.assertEqual(["@foo", "@bar"], usernames)

    def test_parse_cangkun_join_command(self):
        room_id, replica_kind = app_replica._parse_replica_join_command(".加入苍坤洞府 123")

        self.assertEqual("123", room_id)
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, replica_kind)

    def test_parse_luoyun_join_and_dispatch_command(self):
        room_id, replica_kind = app_replica._parse_replica_join_command(".加入落云秘圃 12")
        dispatch_kind, dispatch_room_id, usernames = app_replica._parse_replica_dispatch_command(".落云秘圃 12 @foo @bar")

        self.assertEqual("12", room_id)
        self.assertEqual(app_replica._REPLICA_KIND_LUOYUN, replica_kind)
        self.assertEqual(app_replica._REPLICA_KIND_LUOYUN, dispatch_kind)
        self.assertEqual("12", dispatch_room_id)
        self.assertEqual(["@foo", "@bar"], usernames)

    def test_mark_cangkun_team_joined_from_opened_text(self):
        identity_id = self._prepare_replica_identity(username="leader")

        changed = app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 123\n其他道友可使用 .加入苍坤洞府 123 加入队伍！",
            now=1000.0,
            msg_id=42,
        )

        self.assertTrue(changed)
        record = state_module.get_replica_run_state()["by_identity"][str(identity_id)]
        state_item = record["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
        self.assertFalse(state_item["participating"])
        self.assertEqual("123", state_item["room_id"])
        self.assertEqual(["@leader"], state_item["team_usernames"])
        self.assertEqual("opened", state_item["lobby_status"])
        self.assertEqual(1000.0 + app_replica._REPLICA_LOBBY_TTL_SEC, state_item["lobby_until"])

    def test_parse_cangkun_join_reply(self):
        parsed = app_replica._parse_replica_join_reply(
            "@bbtest 已成功加入苍坤洞府 123\n当前队伍 (2/5):\n - @leader\n - @bbtest",
            reply_to=SimpleNamespace(raw_text=".加入苍坤洞府 123"),
        )

        self.assertEqual("joined", parsed["kind"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, parsed["replica_kind"])
        self.assertEqual("123", parsed["room_id"])
        self.assertEqual(["@leader", "@bbtest"], parsed["team_usernames"])

    def test_parse_cangkun_real_join_reply(self):
        parsed = app_replica._parse_replica_join_reply(
            "@boxboxji 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @zhengyuan0213 (御山)\n"
            "- @WalterWA2000 (破军)\n"
            "- @boxboxji (灵医)",
            reply_to=SimpleNamespace(raw_text=".加入苍坤洞府 35"),
        )

        self.assertEqual("joined", parsed["kind"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, parsed["replica_kind"])
        self.assertEqual("35", parsed["room_id"])
        self.assertEqual(["@zhengyuan0213", "@walterwa2000", "@boxboxji"], parsed["team_usernames"])

    def test_parse_cangkun_real_join_cooldown_reply(self):
        parsed = app_replica._parse_replica_join_reply(
            "你尚在苍坤上人洞府独立冷却中，当前无法加入队伍。\n"
            "剩余时间：23分钟26秒\n"
            "冷却结束：2026-06-07 02:06:42 (Asia/Shanghai)",
            reply_to=SimpleNamespace(raw_text=".加入苍坤洞府 46"),
        )

        self.assertEqual("cooldown", parsed["kind"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, parsed["replica_kind"])
        self.assertEqual("46", parsed["room_id"])
        self.assertGreaterEqual(parsed["wait_sec"], 23 * 60)

    def test_mark_cangkun_team_joined_from_real_join_text(self):
        leader_id = self._register_replica_identity(991201, "zhengyuan0213", professions="御山|咒师")
        wa_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        box_id = self._register_replica_identity(991203, "boxboxji", professions="御山|灵医")
        state_module.set_replica_participant_identity_ids([leader_id, wa_id, box_id])

        changed = app_replica._mark_replica_team_joined_from_text(
            "@boxboxji 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @zhengyuan0213 (御山)\n"
            "- @WalterWA2000 (破军)\n"
            "- @boxboxji (灵医)",
            now=1000.0,
            msg_id=35,
        )

        self.assertTrue(changed)
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, wa_id, box_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertFalse(state_item["participating"])
            self.assertEqual(["@zhengyuan0213", "@walterwa2000", "@boxboxji"], state_item["team_usernames"])
            self.assertEqual("joined", state_item["lobby_status"])

    def test_opened_and_joined_lobby_do_not_show_as_running_until_entered(self):
        leader_id = self._register_replica_identity(991201, "xuruode1")
        member_id = self._register_replica_identity(991202, "member")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 1000.0

        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@xuruode1 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1333\n"
            "其他道友可使用 .加入副本 1333 加入队伍！(5人满)\n\n"
            "【卦象词条】 巽风上坎水下 · 二爻守中\n"
            "- 阵骨：土 必带\n"
            "- 主锋：木 x2（只认真位，不吃借生）\n"
            "- 引灵：水 位，可由 金 借生代行\n"
            "- 旁合：水 位更佳，若用 金 强顶只算偏配",
            now=now,
            msg_id=9940015,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@member 已成功加入副本 1333\n当前队伍 (2/5):\n - @xuruode1\n - @member",
            now=now + 1,
            msg_id=9940020,
        )

        records = state_module.get_replica_run_state()["by_identity"]
        self.assertEqual("虚:可 | 坠:可 | 黄:可 | 苍:可 | 昆:可 | 落:可", app_replica._format_replica_identity_statuses(leader_id, now + 2, records=records))
        self.assertEqual("虚:可 | 坠:可 | 黄:可 | 苍:可 | 昆:可 | 落:可", app_replica._format_replica_identity_statuses(member_id, now + 2, records=records))
        for identity_id in (leader_id, member_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
            self.assertFalse(state_item["participating"])
            self.assertEqual("1333", state_item["room_id"])
            self.assertGreater(state_item["lobby_until"], now)

    def test_entered_text_promotes_recent_lobby_to_running(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000")
        member_id = self._register_replica_identity(991202, "member")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@WalterWA2000 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1336\n"
            "其他道友可使用 .加入副本 1336 加入队伍！(5人满)\n\n"
            "【卦象词条】 乾天上艮山下 · 四爻转阵\n"
            "- 阵骨：土 必带\n"
            "- 主锋：金 x2（只认真位，不吃借生）\n"
            "- 引灵：土 位，可由 火 借生代行\n"
            "- 旁合：土 位更佳，若用 火 强顶只算偏配",
            now=now,
            msg_id=9942743,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@member 已成功加入副本 1336\n当前队伍 (2/5):\n - @WalterWA2000\n - @member",
            now=now + 1,
            msg_id=9942750,
        )

        handled = asyncio.run(app_replica._handle_replica_progress_event(
            SimpleNamespace(id=9942763, raw_text="队伍已进入虚天殿...石门缓缓关闭，前路凶险未知！"),
            now + 2,
        ))

        self.assertTrue(handled)
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, member_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
            self.assertTrue(state_item["participating"])
            self.assertEqual("1336", state_item["room_id"])
            self.assertNotIn("lobby_until", state_item)
            self.assertEqual("虚:中 | 坠:可 | 黄:可 | 苍:可 | 昆:可 | 落:可", app_replica._format_replica_identity_statuses(identity_id, now + 3, records=records))

    def test_auto_dissolved_lobby_clears_without_success_cooldown(self):
        leader_id = self._register_replica_identity(991201, "xuruode1")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@xuruode1 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1333\n"
            "其他道友可使用 .加入副本 1333 加入队伍！(5人满)\n\n"
            "【卦象词条】 巽风上坎水下 · 二爻守中\n"
            "- 阵骨：土 必带\n"
            "- 主锋：木 x2（只认真位，不吃借生）\n"
            "- 引灵：水 位，可由 金 借生代行\n"
            "- 旁合：水 位更佳，若用 金 强顶只算偏配",
            now=now,
            msg_id=9940015,
        )

        handled = asyncio.run(app_replica._handle_replica_progress_event(
            SimpleNamespace(id=9940358, raw_text="由 @xuruode1 开启的虚天殿（ID: 1333）因长时间未满员，已自动解散。"),
            now + 11 * 60,
        ))

        self.assertTrue(handled)
        record = state_module.get_replica_run_state()["by_identity"][str(leader_id)]
        state_item = record["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertFalse(state_item["participating"])
        self.assertEqual("dissolved", record["last_join_result"])
        self.assertEqual("1333", state_item["last_dissolved_room_id"])
        self.assertNotIn("lobby_until", state_item)
        self.assertEqual("虚:可 | 坠:可 | 黄:可 | 苍:可 | 昆:可 | 落:可", app_replica._format_replica_identity_statuses(leader_id, now + 11 * 60 + 1))

    def test_success_cooldown_clears_lobby_fields(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1333\n"
            "其他道友可使用 .加入副本 1333 加入队伍！(5人满)",
            now=now,
            msg_id=9940015,
        )
        state_item = state_module.get_replica_run_state()["by_identity"][str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertIn("lobby_until", state_item)

        app_replica._mark_replica_success_cooldown(
            [leader_id],
            now + 10,
            source_msg_id=9940100,
            replica_kind=app_replica._REPLICA_KIND_VIRTUAL_HALL,
        )

        record = state_module.get_replica_run_state()["by_identity"][str(leader_id)]
        state_item = record["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertEqual("success_cooldown", record["last_join_result"])
        self.assertEqual("", state_item["room_id"])
        self.assertEqual("1333", state_item["last_completed_room_id"])
        self.assertNotIn("lobby_until", state_item)
        self.assertNotIn("lobby_status", state_item)

    def test_cleanup_clears_inactive_cangkun_room_id_without_clearing_active_cd(self):
        expired_id = self._register_replica_identity(991201, "expired", professions="灵医")
        cooldown_id = self._register_replica_identity(991202, "cooldown", professions="破军")
        now = 1000.0
        state_module.set_replica_run_state({
            "by_identity": {
                str(expired_id): {
                    "replica_states": {
                        app_replica._REPLICA_KIND_CANGKUN: {
                            "participating": False,
                            "room_id": "47",
                            "team_usernames": [],
                            "team_identity_ids": [],
                            "joined_at": 0,
                            "active_until": 0,
                            "lobby_until": now - 1,
                            "cooldown_until": now - 1,
                        }
                    },
                    "last_join_result": "joined",
                    "updated_at": now - 10,
                },
                str(cooldown_id): {
                    "replica_states": {
                        app_replica._REPLICA_KIND_CANGKUN: {
                            "participating": False,
                            "room_id": "47",
                            "team_usernames": [],
                            "team_identity_ids": [],
                            "joined_at": 0,
                            "active_until": 0,
                            "cooldown_until": now + 600,
                        }
                    },
                    "last_join_result": "cooldown",
                    "updated_at": now - 10,
                },
            }
        })

        records = app_replica._cleanup_replica_run_state(now)

        expired_state = records[str(expired_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
        cooldown_state = records[str(cooldown_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
        self.assertEqual("", expired_state["room_id"])
        self.assertEqual(0, expired_state["cooldown_until"])
        self.assertEqual("可", app_replica._get_replica_identity_kind_status(expired_id, app_replica._REPLICA_KIND_CANGKUN, now + 1, records=records))
        self.assertEqual("", cooldown_state["room_id"])
        self.assertEqual(now + 600, cooldown_state["cooldown_until"])
        self.assertNotEqual("可", app_replica._get_replica_identity_kind_status(cooldown_id, app_replica._REPLICA_KIND_CANGKUN, now + 1, records=records))

    def test_failure_pending_clears_lobby_fields(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1333\n"
            "其他道友可使用 .加入副本 1333 加入队伍！(5人满)",
            now=now,
            msg_id=9940015,
        )
        state_item = state_module.get_replica_run_state()["by_identity"][str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertIn("lobby_until", state_item)

        app_replica._mark_replica_failure_pending(
            [leader_id],
            now + 10,
            replica_kind=app_replica._REPLICA_KIND_VIRTUAL_HALL,
        )

        record = state_module.get_replica_run_state()["by_identity"][str(leader_id)]
        state_item = record["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertEqual("failure_pending", record["last_join_result"])
        self.assertGreater(state_item["failure_pending_until"], now)
        self.assertNotIn("lobby_until", state_item)
        self.assertNotIn("lobby_status", state_item)

    def test_team_kicked_updates_lobby_before_entered(self):
        leader_id = self._register_replica_identity(991201, "leader")
        member_id = self._register_replica_identity(991202, "member")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【虚天殿已开启】\n"
            "@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1333\n"
            "其他道友可使用 .加入副本 1333 加入队伍！(5人满)",
            now=now,
            msg_id=9940015,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@member 已成功加入副本 1333\n当前队伍 (2/5):\n - @leader\n - @member",
            now=now + 1,
            msg_id=9940020,
        )

        changed = app_replica._mark_replica_team_kicked(
            "@leader",
            "@member",
            ["@leader"],
            now + 2,
            source_msg_id=9940025,
            replica_kind=app_replica._REPLICA_KIND_VIRTUAL_HALL,
        )

        self.assertTrue(changed)
        records = state_module.get_replica_run_state()["by_identity"]
        leader_state = records[str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        member_state = records[str(member_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertEqual(["@leader"], leader_state["team_usernames"])
        self.assertGreater(leader_state["lobby_until"], now)
        self.assertEqual([], member_state["team_usernames"])
        self.assertNotIn("lobby_until", member_state)
        self.assertEqual("kicked", records[str(member_id)]["last_join_result"])

    def test_ambiguous_entered_text_does_not_promote_multiple_lobbies(self):
        first_id = self._register_replica_identity(991201, "first")
        second_id = self._register_replica_identity(991202, "second")
        state_module.set_replica_participant_identity_ids([first_id, second_id])
        now = 1000.0
        for username, room_id, msg_id in (("@first", "1336", 1001), ("@second", "1337", 1002)):
            app_replica._mark_replica_team_joined_from_text(
                "【虚天殿已开启】\n"
                f"{username} 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
                f"副本ID: {room_id}\n"
                f"其他道友可使用 .加入副本 {room_id} 加入队伍！(5人满)\n\n"
                "【卦象词条】 乾天上艮山下 · 四爻转阵\n"
                "- 阵骨：土 必带\n"
                "- 主锋：金 x2（只认真位，不吃借生）\n"
                "- 引灵：土 位，可由 火 借生代行\n"
                "- 旁合：土 位更佳，若用 火 强顶只算偏配",
                now=now,
                msg_id=msg_id,
            )

        handled = asyncio.run(app_replica._handle_replica_progress_event(
            SimpleNamespace(id=1010, raw_text="队伍已进入虚天殿...石门缓缓关闭，前路凶险未知！"),
            now + 2,
        ))

        self.assertFalse(handled)
        records = state_module.get_replica_run_state()["by_identity"]
        self.assertFalse(records[str(first_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]["participating"])
        self.assertFalse(records[str(second_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]["participating"])

    def test_runtime_resolves_cangkun_join_reply_family(self):
        self.assertEqual("replica_join", runtime.resolve_reply_family(".加入苍坤洞府 123"))
        self.assertEqual("replica_join", runtime.resolve_reply_family(".加入落云秘圃 12"))

    def test_ticket_text_deltas_follow_real_open_and_return_texts(self):
        identity_id = self._prepare_replica_identity(username="leader")
        state_module.set_storage_bag_records({
            str(identity_id): {"items": {"虚天残图": 1}, "sections": {"材料": {"虚天残图": 1}}},
        })

        opened = "【虚天殿已开启】\n@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n副本ID: 847"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=11, chat_id=1), opened, 1000.0)

        self.assertTrue(changed)
        self.assertNotIn("虚天残图", state_module.get_storage_bag_records()[str(identity_id)]["items"])

        returned = "队长 @leader 已将副本房间（ID: 847）解散。\n因副本未曾开启，天道已将【虚天残图】归还至你的储物袋中。"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=12, chat_id=1), returned, 1001.0)

        self.assertTrue(changed)
        self.assertEqual(1, state_module.get_storage_bag_records()[str(identity_id)]["items"]["虚天残图"])

    def test_ticket_text_deltas_follow_real_cangkun_and_zhuimo_return_texts(self):
        leader_id = self._prepare_replica_identity(username="leader")
        state_module.set_storage_bag_records({
            str(leader_id): {
                "items": {"苍坤残图": 1, "坠魔谷禁制令": 1},
                "sections": {"法宝/丹药/杂物": {"苍坤残图": 1, "坠魔谷禁制令": 1}},
            },
        })

        cangkun_opened = "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 7"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=21, chat_id=1), cangkun_opened, 1000.0)

        self.assertTrue(changed)
        self.assertNotIn("苍坤残图", state_module.get_storage_bag_records()[str(leader_id)]["items"])

        cangkun_returned = "队长 @leader 已解散苍坤上人洞府房间（ID: 7）。\n因队伍尚未出发，天道已将【苍坤残图】归还。"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=22, chat_id=1), cangkun_returned, 1001.0)

        self.assertTrue(changed)
        self.assertEqual(1, state_module.get_storage_bag_records()[str(leader_id)]["items"]["苍坤残图"])

        zhuimo_opened = "【坠魔谷·集结】\n@leader 以【坠魔谷禁制令】撕开了封印裂隙！\n房间ID: 32"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=23, chat_id=1), zhuimo_opened, 1002.0)

        self.assertTrue(changed)
        self.assertNotIn("坠魔谷禁制令", state_module.get_storage_bag_records()[str(leader_id)]["items"])

        zhuimo_returned = "队长 @leader 已解散坠魔谷房间（ID: 32）。\n因队伍尚未出发，天道已将【坠魔谷禁制令】归还。"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=24, chat_id=1), zhuimo_returned, 1003.0)

        self.assertTrue(changed)
        self.assertEqual(1, state_module.get_storage_bag_records()[str(leader_id)]["items"]["坠魔谷禁制令"])

    def test_ticket_text_deltas_deduct_kunwu_ticket_from_real_open_text_without_item_name(self):
        leader_id = self._prepare_replica_identity(username="leader")
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"昆吾通行令": 2}, "sections": {"法宝/丹药/杂物": {"昆吾通行令": 2}}},
        })

        opened = (
            "【昆吾山·集结】\n"
            "道友 @leader 准备开启昆吾山试炼，正在召集同伴！(1/3)\n"
            "房间ID: 370\n\n"
            "其他道友可使用 .加入昆吾山 370 加入队伍。"
        )
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=27, chat_id=1), opened, 1006.0)

        self.assertTrue(changed)
        self.assertEqual(1, state_module.get_storage_bag_records()[str(leader_id)]["items"]["昆吾通行令"])

    def test_ticket_text_deltas_leave_gift_transfer_text_to_storage_bag_state_machine(self):
        source_id = self._register_replica_identity(991201, "source")
        target_id = self._register_replica_identity(991202, "target")
        state_module.set_storage_bag_records({
            str(source_id): {"items": {"苍坤残图": 3}, "sections": {"法宝/丹药/杂物": {"苍坤残图": 3}}},
            str(target_id): {"items": {}, "sections": {}},
        })

        text = "【赠送成功】\n道友 @source 向 @target 赠送了 【苍坤残图】x2。\n并额外支付了 20 灵石作为因果税 (基础税率 10%)。"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=25, chat_id=1), text, 1004.0)

        self.assertFalse(changed)
        records = state_module.get_storage_bag_records()
        self.assertEqual(3, records[str(source_id)]["items"]["苍坤残图"])
        self.assertNotIn("苍坤残图", records[str(target_id)]["items"])

    def test_ticket_text_deltas_skip_controlled_storage_bag_gift_reply(self):
        source_id = self._register_replica_identity(991201, "source")
        target_id = self._register_replica_identity(991202, "target")
        state_module.set_storage_bag_records({
            str(source_id): {"items": {"苍坤残图": 3}, "sections": {"法宝/丹药/杂物": {"苍坤残图": 3}}},
            str(target_id): {"items": {}, "sections": {}},
        })

        text = "【赠送成功】\n道友 @source 向 @target 赠送了 【苍坤残图】x2。\n并额外支付了 20 灵石作为因果税 (基础税率 10%)。"
        changed = app_replica.apply_replica_ticket_text_deltas(
            SimpleNamespace(id=26, chat_id=1),
            text,
            1005.0,
            reply_context={"family": "storage_bag_gift"},
        )

        self.assertFalse(changed)
        records = state_module.get_storage_bag_records()
        self.assertEqual(3, records[str(source_id)]["items"]["苍坤残图"])
        self.assertNotIn("苍坤残图", records[str(target_id)]["items"])

    def test_non_virtual_dissolve_progress_uses_real_text(self):
        leader_id = self._prepare_replica_identity(username="leader")
        member_id = self._register_replica_identity(991202, "member")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 1000.0

        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 7\n其他道友可使用 .加入苍坤洞府 7 加入队伍！",
            now=now,
            msg_id=31,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@member 已成功加入苍坤洞府 7\n当前队伍 (2/5):\n - @leader\n - @member",
            now=now + 1,
            msg_id=32,
        )

        event = SimpleNamespace(
            id=33,
            raw_text="队长 @leader 已解散苍坤上人洞府房间（ID: 7）。\n因队伍尚未出发，天道已将【苍坤残图】归还。",
        )
        handled = asyncio.run(app_replica._handle_replica_progress_event(event, now + 2))

        self.assertTrue(handled)
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, member_id):
            record = records[str(identity_id)]
            state_item = record["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertFalse(state_item["participating"])
            self.assertEqual("dissolved", record["last_join_result"])
            self.assertEqual("7", state_item["last_dissolved_room_id"])

    def test_ticket_query_reply_lists_openers_from_storage_bag(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        other_id = self._register_replica_identity(991202, "empty", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, other_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 2, "昆吾通行令": 1}, "sections": {}},
            str(other_id): {"items": {}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertIn("昆吾山自动副本：1 个身份", reply)
        self.assertIn("@leader", reply)
        self.assertIn("昆x1/可", reply)
        self.assertNotIn("虚x1", reply)
        self.assertNotIn("苍x2", reply)
        self.assertIn("开房兜底命令（按副本）", reply)
        self.assertIn("昆吾山：", reply)
        self.assertIn(".开启副本 @leader 昆", reply)
        self.assertNotIn(".开启副本 @leader 虚", reply)
        self.assertNotIn(".开启副本 @leader 苍", reply)
        self.assertNotIn(".加入副本 @用户名 @用户名", reply)
        self.assertNotIn(".解散副本", reply)
        self.assertNotIn("推荐配置：苍坤洞府", reply)
        opener_section = reply.split("开房兜底命令", 1)[0]
        self.assertNotIn("@empty", opener_section)

        html_reply = app_replica._format_replica_ticket_query_reply(html=True)
        self.assertFalse(html_reply.startswith("<code>昆吾山自动副本"))
        self.assertIn("<code>.开启副本 @leader 昆</code>", html_reply)
        self.assertNotIn("<code>.开启副本 @leader 虚</code>", html_reply)
        self.assertNotIn("<code>.开启副本 @leader 苍</code>", html_reply)
        self.assertNotIn("<code>.加入副本 @用户名 @用户名</code>", html_reply)
        self.assertNotIn("<code>.解散副本</code>", html_reply)

    def test_ticket_query_sends_open_choice_buttons(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        event = self._prepare_replica_group([leader_id])
        event.raw_text = ".查询副本"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 1, "昆吾通行令": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_replica_ticket_query_command(event)
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, self._button_texts(buttons)

        handled, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("开昆 @leader", button_texts)
        self.assertNotIn("开虚 @leader", button_texts)
        self.assertNotIn("开苍 @leader", button_texts)

    def test_log_group_replica_panel_offers_kunwu_open_button(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "昆吾通行令": 1}, "sections": {}},
        })

        panel = app_replica.build_log_group_replica_panel(".查询昆")

        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("房间：无", panel.get("text") or "")
        self.assertIn("昆吾山可开：1", panel.get("text") or "")
        self.assertLessEqual(len((panel.get("text") or "").splitlines()), 3)
        self.assertIn("开昆 @leader", button_texts)
        self.assertNotIn("开虚 @leader", button_texts)
        payload = self._button_payload_by_text(panel.get("buttons"), "开昆 @leader")
        self.assertEqual(".开启副本 @leader 昆", payload.get("command"))
        self.assertEqual(-100777, payload.get("chat_id"))
        self.assertEqual(9001, payload.get("listener_account_id"))

    def test_log_group_replica_summary_shows_all_kinds_and_buttons(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        cangkun_id = self._register_replica_identity(991202, "cang", root_attrs="土", professions="御山")
        luoyun_id = self._register_replica_identity(991203, "luoyun", realm="结丹后期", sect_name="落云宗")
        state_module.update_send_as_profile(luoyun_id, sect_contribution=420, sect_contribution_updated_at=1)
        self._prepare_replica_group([leader_id, cangkun_id, luoyun_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "昆吾通行令": 1}, "sections": {}},
            str(cangkun_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(luoyun_id): {"items": {}, "sections": {}},
        })

        panel = app_replica.build_log_group_replica_panel(".查询副本")

        text = panel.get("text") or ""
        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("虚天殿：可开 1", text)
        self.assertIn("苍坤洞府：可开 1", text)
        self.assertIn("昆吾山：可开 1", text)
        self.assertIn("落云秘圃：可开 1", text)
        self.assertIn("查虚", button_texts)
        self.assertIn("查昆", button_texts)
        self.assertIn("查苍", button_texts)
        self.assertIn("开虚 @leader", button_texts)
        self.assertIn("开昆 @leader", button_texts)
        self.assertIn("开苍 @cang", button_texts)
        self.assertIn("开落 @luoyun", button_texts)

    def test_log_group_replica_cd_overview_summarizes_ready_and_busy(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        cooldown_id = self._register_replica_identity(991202, "cool", root_attrs="木", professions="御山")
        active_id = self._register_replica_identity(991203, "active", root_attrs="水", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, cooldown_id, active_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "昆吾通行令": 1}, "sections": {}},
            str(cooldown_id): {"items": {"虚天残图": 1}, "sections": {}},
            str(active_id): {"items": {"昆吾通行令": 1}, "sections": {}},
        })
        now = 1000.0
        state_module.set_replica_run_state({
            "by_identity": {
                str(cooldown_id): {
                    "replica_states": {
                        app_replica._REPLICA_KIND_VIRTUAL_HALL: {
                            "cooldown_until": now + 600,
                        },
                    },
                },
                str(active_id): {
                    "replica_states": {
                        app_replica._REPLICA_KIND_KUNWU: {
                            "participating": True,
                            "joined_at": now - 10,
                            "active_until": now + 1200,
                        },
                    },
                },
            }
        })

        with patch("model.app_replica.time.time", return_value=now):
            text = app_replica.format_log_group_replica_cd_overview()

        self.assertIn("副本 CD 概览", text)
        self.assertIn("可开：虚1", text)
        self.assertIn("昆1", text)
        self.assertIn("冷却/占用：虚1｜昆1", text)
        self.assertIn("- @cool｜虚0:10", text)
        self.assertIn("- @active｜昆中", text)
        self.assertNotIn("@leader｜", text)

    def test_log_group_replica_summary_keeps_query_buttons_without_listener(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })

        panel = app_replica.build_log_group_replica_panel(".查询副本", fallback_chat_id=-100999)

        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("查虚", button_texts)
        self.assertIn("查昆", button_texts)
        self.assertNotIn("开虚 @leader", button_texts)

    def test_log_group_replica_specific_query_can_open_that_kind(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "昆吾通行令": 1}, "sections": {}},
        })

        panel = app_replica.build_log_group_replica_panel(".查询虚")

        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("虚天殿可开：1", panel.get("text") or "")
        self.assertIn("开虚 @leader", button_texts)
        self.assertNotIn("开昆 @leader", button_texts)
        payload = self._button_payload_by_text(panel.get("buttons"), "开虚 @leader")
        self.assertEqual(".开启副本 @leader 虚", payload.get("command"))

    def test_log_group_zhuimo_query_previews_first_opener_team(self):
        first_leader_id = self._register_replica_identity(991201, "firstleader", professions="御山")
        second_leader_id = self._register_replica_identity(991202, "secondleader", professions="御山")
        dps_id = self._register_replica_identity(991203, "dps", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991204, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991205, "blade", professions="影刃")
        baji_id = self._register_replica_identity(991206, "jfdffdddd", root_attrs="土木", professions="御山|灵医")
        self._prepare_replica_group([first_leader_id, second_leader_id, dps_id, healer_id, blade_id, baji_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        state_module.set_storage_bag_records({
            str(first_leader_id): {"items": {"坠魔谷禁制令": 1, "路线图": 1, "毒囊": 1}, "sections": {}},
            str(second_leader_id): {"items": {"坠魔谷禁制令": 1}, "sections": {}},
        })

        panel = app_replica.build_log_group_replica_panel(".查询坠")

        text = panel.get("text") or ""
        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("坠魔谷可开：2", text)
        self.assertIn("推荐配置：坠魔谷｜职业补位（开房 @firstleader）", text)
        self.assertNotIn("开房 @secondleader", text)
        self.assertIn("推荐加入：@dps @healer @blade @jfdffdddd", text)
        self.assertIn("心劫：@jfdffdddd 可满足坠魔心劫。", text)
        self.assertIn("优先：已带吧唧。", text)
        self.assertIn("开坠 @firstleader", button_texts)
        self.assertIn("开坠 @secondleader", button_texts)

    def test_log_group_cangkun_query_previews_multi_team_capacity(self):
        first_leader_id = self._register_replica_identity(991201, "firstleader", professions="破军")
        second_leader_id = self._register_replica_identity(991202, "secondleader", professions="破军")
        first_sense_id = self._register_replica_identity(991203, "firstsense", professions="御山", sect_name="太一门")
        second_sense_id = self._register_replica_identity(991204, "secondsense", professions="御山", sect_name="太一门")
        first_healer_id = self._register_replica_identity(991205, "firsthealer", professions="灵医")
        second_healer_id = self._register_replica_identity(991206, "secondhealer", professions="灵医")
        first_blade_id = self._register_replica_identity(991207, "firstblade", professions="影刃")
        second_blade_id = self._register_replica_identity(991208, "secondblade", professions="影刃")
        first_curse_id = self._register_replica_identity(991209, "firstcurse", professions="咒师")
        second_curse_id = self._register_replica_identity(991210, "secondcurse", professions="咒师")
        self._prepare_replica_group([
            first_leader_id,
            second_leader_id,
            first_sense_id,
            second_sense_id,
            first_healer_id,
            second_healer_id,
            first_blade_id,
            second_blade_id,
            first_curse_id,
            second_curse_id,
        ])
        state_module.set_storage_bag_records({
            str(first_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(second_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })
        state_module.set_tianjige_dao_path_records({
            str(first_sense_id): {"spiritual_sense": 1300},
            str(second_sense_id): {"spiritual_sense": 1200},
        })

        panel = app_replica.build_log_group_replica_panel(".查询苍")

        text = panel.get("text") or ""
        button_texts = self._button_texts(panel.get("buttons"))
        self.assertIn("苍坤洞府可开：2", text)
        self.assertIn("苍坤多队预览：可组 2 队", text)
        self.assertIn("队长 @firstleader｜神识 @firstsense 1300", text)
        self.assertIn("队长 @secondleader｜神识 @secondsense 1200", text)
        self.assertIn("策略：自动推荐主推 WA，可给无 WA 备选", text)
        self.assertIn("开苍 @firstleader", button_texts)
        self.assertIn("开苍 @secondleader", button_texts)

    def test_log_group_summary_query_button_refreshes_specific_panel(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })
        panel = app_replica.build_log_group_replica_panel(".查询副本")
        query_button = next(
            button
            for row in panel.get("buttons") or []
            for button in row
            if button.get("text") == "查虚"
        )
        _token, action = app_replica._get_replica_button_action(query_button["callback_data"])

        async def run_test():
            with patch("model.app_replica.reply_log_group_message", new=AsyncMock(return_value=True)) as reply_mock, \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock()) as replica_send_mock:
                callback_query = {"message": {"message_id": 9988, "chat": {"id": -100999}}}
                ok, message = await app_replica._execute_replica_button_action_with_callback(action, actor_id=123456, callback_query=callback_query)
                return ok, message, reply_mock.await_args, replica_send_mock.await_count

        ok, message, reply_args, replica_send_count = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn(".查询虚", message)
        self.assertEqual(-100999, reply_args.args[0].chat_id)
        self.assertEqual(9988, reply_args.args[0].id)
        self.assertIn("房间：无", reply_args.args[1])
        self.assertIn("开虚 @leader", self._button_texts(reply_args.kwargs["buttons"]))
        self.assertEqual(0, replica_send_count)

    def test_log_group_panel_refresh_button_is_reusable(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })
        panel = app_replica.build_log_group_replica_panel(".查询副本")
        query_button = next(
            button
            for row in panel.get("buttons") or []
            for button in row
            if button.get("text") == "查虚"
        )

        async def run_test():
            with patch("model.app_replica.ADMIN_IDS", frozenset({123456})), \
                    patch("model.app_replica.reply_log_group_message", new=AsyncMock(return_value=True)) as reply_mock, \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock()) as replica_send_mock, \
                    patch("model.app_replica.answer_log_bot_callback", new=AsyncMock()) as answer_mock:
                callback_query = {
                    "id": "cb-log-panel",
                    "data": query_button["callback_data"],
                    "from": {"id": 123456},
                    "message": {"chat": {"id": -100999}},
                }
                first = await app_replica.handle_replica_button_callback(callback_query)
                second = await app_replica.handle_replica_button_callback(callback_query)
                return first, second, reply_mock.await_count, replica_send_mock.await_count, answer_mock.await_args_list

        first, second, reply_count, replica_send_count, answer_calls = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(2, reply_count)
        self.assertEqual(0, replica_send_count)
        self.assertIn("已刷新：.查询虚", answer_calls[0].args[1])
        self.assertIn("已刷新：.查询虚", answer_calls[1].args[1])

    def test_dissolve_button_is_single_use_and_preserves_callback_source(self):
        button = app_replica._replica_command_action_button(
            "解散副本",
            ".解散副本",
            -100777,
            listener_account_id=9001,
            token_key="dissolve-room-47",
        )

        async def run_test():
            with patch("model.app_replica.ADMIN_IDS", frozenset({123456})), \
                    patch("model.app_replica._handle_replica_group_command", new=AsyncMock(return_value=True)) as handle_mock, \
                    patch("model.app_replica.answer_log_bot_callback", new=AsyncMock()) as answer_mock:
                callback_query = {
                    "id": "cb-dissolve",
                    "data": button["callback_data"],
                    "from": {"id": 123456},
                    "message": {"message_id": 7788, "chat": {"id": -100999}},
                }
                first = await app_replica.handle_replica_button_callback(callback_query)
                second = await app_replica.handle_replica_button_callback(callback_query)
                return first, second, handle_mock.await_args_list, answer_mock.await_args_list

        first, second, handle_calls, answer_calls = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, len(handle_calls))
        event = handle_calls[0].args[0]
        self.assertEqual(".解散副本", event.raw_text)
        self.assertEqual(7788, event.id)
        self.assertEqual(7788, event._replica_button_message_id)
        self.assertEqual(123456, event._replica_button_actor_id)
        self.assertIn("已触发：.解散副本", answer_calls[0].args[1])
        self.assertIn("已处理过", answer_calls[1].args[1])

    def test_log_group_room_panel_uses_log_refresh_button(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        self._prepare_replica_group([leader_id])
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": time.time(),
            "updated_at": time.time(),
            "expires_at": time.time() + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        panel = app_replica.build_log_group_replica_panel(".查询昆")

        self.assertIn("房间：昆吾山 47", panel.get("text") or "")
        self.assertIn("刷新面板", self._button_texts(panel.get("buttons")))
        refresh_payload = self._button_payload_by_text(panel.get("buttons"), "刷新面板")
        self.assertEqual(".查询昆", refresh_payload.get("query_text"))
        self.assertNotEqual(".查询副本", refresh_payload.get("command"))

    def test_executed_deterministic_button_is_not_reset_by_rerender(self):
        first = app_replica._replica_command_action_button(
            "开虚 @leader",
            ".开启副本 @leader 虚",
            -100777,
            listener_account_id=9001,
            token_key="summary-open:leader:virtual",
        )
        token, _action = app_replica._get_replica_button_action(first["callback_data"])
        self.assertTrue(app_replica._mark_replica_button_action_executed(token, 123456))

        second = app_replica._replica_command_action_button(
            "开虚 @leader",
            ".开启副本 @leader 虚",
            -100777,
            listener_account_id=9001,
            token_key="summary-open:leader:virtual",
        )
        _token, action = app_replica._get_replica_button_action(second["callback_data"])

        self.assertEqual(first["callback_data"], second["callback_data"])
        self.assertGreater(float(action.get("executed_at") or 0), 0)

    def test_executed_open_button_can_be_reused_after_room_closed(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        event = self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })
        context = {"replica_chat_id": event.chat_id, "listener_account_id": 9001}
        button = app_replica._lightweight_replica_command_button(
            context,
            "开苍 @leader",
            ".开启副本 @leader 苍",
            token_suffix=f"open:{leader_id}:{app_replica._REPLICA_KIND_CANGKUN}",
        )
        token, _action = app_replica._get_replica_button_action(button["callback_data"])
        self.assertTrue(app_replica._mark_replica_button_action_executed(token, 123456))

        async def run_test():
            with patch("model.app_replica.ADMIN_IDS", frozenset({123456})), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))) as send_mock, \
                    patch("model.app_replica.answer_log_bot_callback", new=AsyncMock()) as answer_mock:
                handled = await app_replica.handle_replica_button_callback({
                    "id": "cb-open-again",
                    "data": button["callback_data"],
                    "from": {"id": 123456},
                })
                return handled, send_mock.await_args, answer_mock.await_args_list

        handled, send_args, answer_calls = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启苍坤洞府", send_args.args[0])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])
        self.assertIn("已触发：.开启副本 @leader 苍", answer_calls[-1].args[1])

    def test_replica_command_buttons_with_same_exclusive_key_trigger_once(self):
        first = app_replica._replica_command_action_button(
            "加入推荐",
            ".加入副本 @first",
            -100777,
            listener_account_id=9001,
            token_key="join-primary",
            exclusive_key="lightweight_join:-100777:cangkun:47",
        )
        second = app_replica._replica_command_action_button(
            "加入备选",
            ".加入副本 @second",
            -100777,
            listener_account_id=9001,
            token_key="join-backup",
            exclusive_key="lightweight_join:-100777:cangkun:47",
        )
        _first_token, first_action = app_replica._get_replica_button_action(first["callback_data"])
        _second_token, second_action = app_replica._get_replica_button_action(second["callback_data"])

        async def run_test():
            with patch("model.app_replica._handle_replica_group_command", new=AsyncMock(return_value=True)) as handle_mock:
                first_ok, first_message = await app_replica._execute_replica_button_action(first_action, actor_id=123456)
                second_ok, second_message = await app_replica._execute_replica_button_action(second_action, actor_id=123456)
                return first_ok, first_message, second_ok, second_message, handle_mock.await_args_list

        first_ok, first_message, second_ok, second_message, handle_calls = asyncio.run(run_test())
        self.assertTrue(first_ok)
        self.assertIn(".加入副本 @first", first_message)
        self.assertTrue(second_ok)
        self.assertIn("本房间加入已处理过", second_message)
        self.assertEqual(1, len(handle_calls))
        self.assertEqual(".加入副本 @first", handle_calls[0].args[0].raw_text)

    def test_log_group_open_button_can_open_explicit_non_kunwu_kind(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        event = self._prepare_replica_group([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })
        panel = app_replica.build_log_group_replica_panel(".查询副本")
        open_button = next(
            button
            for row in panel.get("buttons") or []
            for button in row
            if button.get("text") == "开虚 @leader"
        )
        _token, action = app_replica._get_replica_button_action(open_button["callback_data"])

        async def run_test():
            with patch("model.app_replica.get_all_clients", return_value={9001: event.client}), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701))) as send_mock, \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                ok, message = await app_replica._execute_replica_button_action(action, actor_id=123456)
                return ok, message, send_mock.await_args

        ok, message, send_args = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn(".开启副本 @leader 虚", message)
        self.assertEqual(".开启虚天殿", send_args.args[0])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])

    def test_log_group_replica_short_queries_resolve_kinds(self):
        self.assertEqual(app_replica._REPLICA_KIND_KUNWU, app_replica._resolve_log_group_replica_query_kind(".查询昆"))
        self.assertEqual(app_replica._REPLICA_KIND_VIRTUAL_HALL, app_replica._resolve_log_group_replica_query_kind(".查询虚"))
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, app_replica._resolve_log_group_replica_query_kind(".查询苍"))
        self.assertEqual("", app_replica._resolve_log_group_replica_query_kind(".查询副本"))

    def test_replica_group_query_does_not_double_reply_ticket_query(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        event = self._prepare_replica_group([leader_id])
        event.raw_text = ".查询副本"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"昆吾通行令": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))) as send_mock:
                handled = await app_replica._handle_replica_group_command(event)
                reply_text = send_mock.await_args.args[2]
                return handled, send_mock.await_count, reply_text

        handled, send_count, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(1, send_count)
        self.assertIn("昆吾山自动副本", reply_text)

    def test_ticket_query_shows_cd_and_hides_cd_open_command(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"昆吾通行令": 1}, "sections": {}},
        })
        app_replica._mark_replica_success_cooldown(
            [leader_id],
            time.time(),
            replica_kind=app_replica._REPLICA_KIND_KUNWU,
        )

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertRegex(reply, r"昆x1/\d+:\d{2}")
        self.assertNotIn(".开启副本 @leader 昆", reply)

    def test_cangkun_recommendation_requires_five_professions_and_jiedan(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", realm="结丹初期")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃", realm="结丹中期")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师", realm="结丹后期")
        low_id = self._register_replica_identity(991206, "aaa_low", professions="咒师", realm="筑基后期")
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, curse_id, low_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@shield @healer @blade @curse", section)
        self.assertNotIn(".加入副本", section)
        self.assertIn("覆盖职业：破军、御山、灵医、影刃、咒师", section)
        self.assertIn("五职业已齐", section)
        self.assertNotIn("@aaa_low", section)

    def test_cangkun_recommendation_reports_missing_low_realm_profession(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", realm="结丹初期")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃", realm="结丹中期")
        low_curse_id = self._register_replica_identity(991205, "low_curse", professions="咒师", realm="筑基后期")
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, low_curse_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@shield @healer @blade", section)
        self.assertNotIn(".加入副本", section)
        self.assertIn("缺职业：咒师", section)
        self.assertNotIn("@low_curse", section)

    def test_cangkun_recommendation_does_not_count_one_identity_as_multiple_professions(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山|咒师", realm="结丹初期")
        attacker_id = self._register_replica_identity(991202, "attacker", professions="破军", realm="结丹初期")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_curse_id = self._register_replica_identity(991204, "bladecurse", professions="影刃|咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([leader_id, attacker_id, healer_id, blade_curse_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@attacker @healer @bladecurse", section)
        self.assertNotIn(".加入副本", section)
        self.assertIn("缺职业：", section)
        self.assertNotIn("五职业已齐", section)

    def test_cangkun_live_incomplete_multirole_team_has_no_action_buttons(self):
        leader_id = self._register_replica_identity(991201, "jfdffdddd", professions="咒师", realm="元婴后期", root_type="天灵根", root_attrs="火")
        box_id = self._register_replica_identity(991202, "boxboxji", professions="御山|灵医", realm="结丹后期", sect_name="太一门", root_attrs="土木")
        ding_id = self._register_replica_identity(991203, "dingfengbosushi", professions="咒师", realm="结丹后期", root_type="异灵根", root_attrs="暗")
        fan_id = self._register_replica_identity(991204, "fanb0x", professions="灵医|破军", realm="结丹后期", root_type="伪灵根", root_attrs="金木水")
        xue_id = self._register_replica_identity(991205, "xueuode5", professions="影刃", realm="结丹后期", root_type="异灵根", root_attrs="风")
        state_module.set_replica_participant_identity_ids([leader_id, box_id, ding_id, fan_id, xue_id])
        state_module.set_tianjige_dao_path_records({
            str(box_id): {"spiritual_sense": 6956},
        })
        room = {
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "room_id": "204",
            "leader_identity_id": leader_id,
            "leader_username": "@jfdffdddd",
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "join_requested_usernames": ["@boxboxji", "@dingfengbosushi", "@fanb0x", "@xueuode5"],
        }

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )
        buttons = app_replica._build_lightweight_room_action_buttons(
            room,
            join_command=join_command,
            include_enter=True,
            include_dissolve=True,
            include_query=True,
        )

        self.assertIn("缺职业：", section)
        self.assertNotIn("五职业已齐", section)
        self.assertEqual("", join_command)
        self.assertNotIn("加入推荐", self._button_texts(buttons))
        self.assertNotIn("进入苍坤洞府", self._button_texts(buttons))

    def test_lightweight_join_blocks_incomplete_cangkun_projection(self):
        leader_id = self._register_replica_identity(991201, "jfdffdddd", professions="咒师", realm="元婴后期")
        box_id = self._register_replica_identity(991202, "boxboxji", professions="御山|灵医", realm="结丹后期", sect_name="太一门")
        ding_id = self._register_replica_identity(991203, "dingfengbosushi", professions="咒师", realm="结丹后期")
        fan_id = self._register_replica_identity(991204, "fanb0x", professions="灵医|破军", realm="结丹后期")
        xue_id = self._register_replica_identity(991205, "xueuode5", professions="影刃", realm="结丹后期")
        event = self._prepare_replica_group([leader_id, box_id, ding_id, fan_id, xue_id])
        event.raw_text = ".加入副本 @boxboxji @dingfengbosushi @fanb0x @xueuode5"
        state_module.set_tianjige_dao_path_records({
            str(box_id): {"spiritual_sense": 6956},
        })
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "204",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@jfdffdddd",
            "expires_at": 9999999999,
            "updated_at": 1000,
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=800))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_join_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, self._button_texts(buttons)

        handled, reply_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("无法凑齐五职业", reply_text)
        self.assertIn("缺职业：", reply_text)
        self.assertNotIn("进入苍坤洞府", button_texts)

    def test_cangkun_multi_team_plan_rejects_raw_union_only_team(self):
        leader_id = self._register_replica_identity(991201, "jfdffdddd", professions="咒师", realm="元婴后期")
        box_id = self._register_replica_identity(991202, "boxboxji", professions="御山|灵医", realm="结丹后期", sect_name="太一门")
        ding_id = self._register_replica_identity(991203, "dingfengbosushi", professions="咒师", realm="结丹后期")
        fan_id = self._register_replica_identity(991204, "fanb0x", professions="灵医|破军", realm="结丹后期")
        xue_id = self._register_replica_identity(991205, "xueuode5", professions="影刃", realm="结丹后期")
        state_module.set_replica_participant_identity_ids([leader_id, box_id, ding_id, fan_id, xue_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })
        state_module.set_tianjige_dao_path_records({
            str(box_id): {"spiritual_sense": 6956},
        })

        plan = app_replica.build_cangkun_multi_team_plan()
        preview = app_replica._format_cangkun_multi_team_preview(plan)

        self.assertEqual(1, plan.get("upper_bound"))
        self.assertEqual([], plan.get("teams"))
        self.assertIn("暂不能形成完整队", preview)

    def test_cangkun_recommendation_prefers_root_grade_for_same_profession(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", realm="结丹初期")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃", realm="结丹初期")
        pseudo_curse_id = self._register_replica_identity(991205, "aa_pseudo_curse", professions="咒师", realm="结丹初期", root_type="伪灵根")
        true_curse_id = self._register_replica_identity(991206, "bb_true_curse", professions="咒师", realm="结丹初期", root_type="真灵根")
        exotic_curse_id = self._register_replica_identity(991207, "cc_exotic_curse", professions="咒师", realm="结丹初期", root_type="异灵根")
        heaven_curse_id = self._register_replica_identity(991208, "zz_heaven_curse", professions="咒师", realm="结丹初期", root_type="天灵根")
        state_module.set_replica_participant_identity_ids([
            leader_id,
            shield_id,
            healer_id,
            blade_id,
            pseudo_curse_id,
            true_curse_id,
            exotic_curse_id,
            heaven_curse_id,
        ])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@shield @healer @blade @zz_heaven_curse", section)
        self.assertNotIn(".加入副本", section)
        self.assertNotIn("@aa_pseudo_curse", section)
        self.assertNotIn("@bb_true_curse", section)
        self.assertNotIn("@cc_exotic_curse", section)

    def test_cangkun_recommendation_prefers_taiyi_for_same_profession(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        normal_shield_id = self._register_replica_identity(991202, "aa_normal_shield", professions="御山", realm="结丹初期", root_type="天灵根")
        taiyi_shield_id = self._register_replica_identity(991203, "zz_taiyi_shield", professions="御山", realm="结丹初期", root_type="伪灵根", sect_name="太一门")
        healer_id = self._register_replica_identity(991204, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991205, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991206, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([
            leader_id,
            normal_shield_id,
            taiyi_shield_id,
            healer_id,
            blade_id,
            curse_id,
        ])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@zz_taiyi_shield @healer @blade @curse", section)
        self.assertNotIn(".加入副本", section)
        self.assertNotIn("@aa_normal_shield", section)

    def test_cangkun_recommendation_prefers_high_sense_without_dps_marker(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        low_sense_shield_id = self._register_replica_identity(991202, "aa_low_sense_shield", professions="御山", realm="结丹初期", root_type="天灵根", sect_name="太一门")
        high_sense_shield_id = self._register_replica_identity(991203, "zz_high_sense_shield", professions="御山", realm="结丹初期", root_type="伪灵根", sect_name="太一门")
        healer_id = self._register_replica_identity(991204, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991205, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991206, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([
            leader_id,
            low_sense_shield_id,
            high_sense_shield_id,
            healer_id,
            blade_id,
            curse_id,
        ])
        state_module.set_tianjige_dao_path_records({
            str(low_sense_shield_id): {"spiritual_sense": 200, "taiyi_spiritual_sense": 0},
            str(high_sense_shield_id): {"spiritual_sense": 1200, "taiyi_spiritual_sense": 0},
        })

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@zz_high_sense_shield @healer @blade @curse", section)
        self.assertNotIn("@aa_low_sense_shield", section)
        self.assertIn("神识校验：@zz_high_sense_shield 太一门，可调神识 1200", section)
        self.assertIn("无需DPS标识", section)
        self.assertIn("默认路线 .苍坤抉择 1 / 3 / 2", section)
        self.assertNotIn("DPS：", section)

    def test_cangkun_multi_team_plan_spreads_high_sense_anchors(self):
        first_leader_id = self._register_replica_identity(991201, "firstleader", professions="破军", realm="结丹初期")
        second_leader_id = self._register_replica_identity(991202, "secondleader", professions="破军", realm="结丹初期")
        first_sense_id = self._register_replica_identity(991203, "firstsense", professions="御山", realm="结丹初期", sect_name="太一门")
        second_sense_id = self._register_replica_identity(991204, "secondsense", professions="御山", realm="结丹初期", sect_name="太一门")
        spare_sense_id = self._register_replica_identity(991205, "sparesense", professions="御山", realm="结丹初期", sect_name="太一门")
        first_healer_id = self._register_replica_identity(991206, "firsthealer", professions="灵医", realm="结丹初期")
        second_healer_id = self._register_replica_identity(991207, "secondhealer", professions="灵医", realm="结丹初期")
        first_blade_id = self._register_replica_identity(991208, "firstblade", professions="影刃", realm="结丹初期")
        second_blade_id = self._register_replica_identity(991209, "secondblade", professions="影刃", realm="结丹初期")
        first_curse_id = self._register_replica_identity(991210, "firstcurse", professions="咒师", realm="结丹初期")
        second_curse_id = self._register_replica_identity(991211, "secondcurse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([
            first_leader_id,
            second_leader_id,
            first_sense_id,
            second_sense_id,
            spare_sense_id,
            first_healer_id,
            second_healer_id,
            first_blade_id,
            second_blade_id,
            first_curse_id,
            second_curse_id,
        ])
        state_module.set_storage_bag_records({
            str(first_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(second_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })
        state_module.set_tianjige_dao_path_records({
            str(first_sense_id): {"spiritual_sense": 1300},
            str(second_sense_id): {"spiritual_sense": 1200},
            str(spare_sense_id): {"spiritual_sense": 1100},
        })

        plan = app_replica.build_cangkun_multi_team_plan()
        teams = plan.get("teams") or []
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_CANGKUN,
            first_leader_id,
        )
        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            first_leader_id,
        )

        self.assertEqual(2, len(teams))
        self.assertEqual(2, plan.get("upper_bound"))
        sense_usernames = [team.get("sense_username") for team in teams]
        self.assertIn("@firstsense", sense_usernames)
        self.assertIn("@secondsense", sense_usernames)
        for team in teams:
            team_sense_count = sum(
                1
                for identity_id in team.get("identity_ids") or []
                if app_replica._has_cangkun_required_spiritual_sense(identity_id)
            )
            self.assertEqual(1, team_sense_count)
        self.assertIn("@firstsense", join_command)
        self.assertNotIn("@secondsense", join_command)
        self.assertNotIn("@sparesense", join_command)
        self.assertIn("推荐配置：苍坤洞府｜职业补位（开房 @firstleader）", section)
        self.assertNotIn("多队规划", section)
        self.assertNotIn("规划：已按多队拆分保留其他神识号", section)

    def test_cangkun_multi_team_plan_reports_role_bottleneck(self):
        opener_ids = [
            self._register_replica_identity(991201, "firstleader", professions="破军", realm="结丹初期"),
            self._register_replica_identity(991202, "secondleader", professions="破军", realm="结丹初期"),
            self._register_replica_identity(991203, "thirdleader", professions="破军", realm="结丹初期"),
            self._register_replica_identity(991204, "fourthleader", professions="破军", realm="结丹初期"),
        ]
        sense_ids = [
            self._register_replica_identity(991205, "firstsense", professions="御山", realm="结丹初期", sect_name="太一门"),
            self._register_replica_identity(991206, "secondsense", professions="御山", realm="结丹初期", sect_name="太一门"),
            self._register_replica_identity(991207, "thirdsense", professions="御山", realm="结丹初期", sect_name="太一门"),
            self._register_replica_identity(991208, "fourthsense", professions="御山", realm="结丹初期", sect_name="太一门"),
        ]
        filler_ids = [
            self._register_replica_identity(991209, "firsthealer", professions="灵医", realm="结丹初期"),
            self._register_replica_identity(991210, "secondhealer", professions="灵医", realm="结丹初期"),
            self._register_replica_identity(991211, "firstblade", professions="影刃", realm="结丹初期"),
            self._register_replica_identity(991212, "secondblade", professions="影刃", realm="结丹初期"),
            self._register_replica_identity(991213, "firstcurse", professions="咒师", realm="结丹初期"),
            self._register_replica_identity(991214, "secondcurse", professions="咒师", realm="结丹初期"),
            self._register_replica_identity(991215, "thirdhealer", professions="灵医|咒师", realm="结丹初期"),
            self._register_replica_identity(991216, "fourthhealer", professions="灵医|咒师", realm="结丹初期"),
        ]
        state_module.set_replica_participant_identity_ids(opener_ids + sense_ids + filler_ids)
        state_module.set_storage_bag_records({
            str(identity_id): {"items": {"苍坤残图": 1}, "sections": {}}
            for identity_id in opener_ids
        })
        state_module.set_tianjige_dao_path_records({
            str(identity_id): {"spiritual_sense": 1200 + index}
            for index, identity_id in enumerate(sense_ids)
        })

        plan = app_replica.build_cangkun_multi_team_plan()
        preview = app_replica._format_cangkun_multi_team_preview(plan)

        self.assertEqual(4, plan.get("opener_count"))
        self.assertEqual(4, plan.get("sense_ok_count"))
        self.assertEqual(2, plan.get("upper_bound"))
        self.assertEqual(2, len(plan.get("teams") or []))
        self.assertIn("苍坤多队预览：可组 2 队｜可开4｜神识4｜理论上限2", preview)

    def test_cangkun_recommendation_treats_missing_sense_snapshot_as_unknown(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", realm="结丹初期")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, curse_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("五职业已齐。", section)
        self.assertIn("神识校验：队内无太一/化神身份，不能满足过千神识需求；不要自动进入。", section)
        self.assertNotIn("天机阁快照未确认", section)

    def test_cangkun_recommendation_marks_taiyi_missing_sense_snapshot_unknown(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", realm="结丹初期", sect_name="太一门")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, curse_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("五职业已齐。", section)
        self.assertIn("神识校验：队内有太一/化神候选，但天机阁快照未确认；刷新天机阁后再进入。", section)

    def test_cangkun_recommendation_shows_backup_without_wa2000(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        wa_id = self._register_replica_identity(991202, "WalterWA2000", professions="御山", realm="结丹初期", root_type="伪灵根", sect_name="太一门")
        backup_shield_id = self._register_replica_identity(991203, "shield", professions="御山", realm="化神初期", root_type="天灵根")
        healer_id = self._register_replica_identity(991204, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991205, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991206, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([
            leader_id,
            wa_id,
            backup_shield_id,
            healer_id,
            blade_id,
            curse_id,
        ])
        state_module.set_tianjige_dao_path_records({
            str(wa_id): {"spiritual_sense": 13610},
            str(backup_shield_id): {"spiritual_sense": 1200},
        })

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        self.assertIn("推荐加入：@walterwa2000 @healer @blade @curse", section)
        self.assertIn("神识校验：@walterwa2000 太一门，可调神识 13610", section)
        self.assertIn("备选加入（不带 @walterwa2000，可复制）：@shield @healer @blade @curse", section)
        self.assertIn("备选校验：五职业已齐；@shield 化神初期，可调神识 1200 已过千。", section)
        self.assertNotIn(".加入副本", section)

    def test_cangkun_room_buttons_include_backup_join_without_wa2000(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="破军", realm="结丹初期")
        wa_id = self._register_replica_identity(991202, "WalterWA2000", professions="御山", realm="结丹初期", root_type="伪灵根", sect_name="太一门")
        backup_shield_id = self._register_replica_identity(991203, "shield", professions="御山", realm="化神初期", root_type="天灵根")
        healer_id = self._register_replica_identity(991204, "healer", professions="灵医", realm="结丹初期")
        blade_id = self._register_replica_identity(991205, "blade", professions="影刃", realm="结丹初期")
        curse_id = self._register_replica_identity(991206, "curse", professions="咒师", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([
            leader_id,
            wa_id,
            backup_shield_id,
            healer_id,
            blade_id,
            curse_id,
        ])
        state_module.set_tianjige_dao_path_records({
            str(wa_id): {"spiritual_sense": 13610},
            str(backup_shield_id): {"spiritual_sense": 1200},
        })
        room = {
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "room_id": "47",
            "leader_identity_id": leader_id,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "join_requested_usernames": ["@WalterWA2000", "@healer", "@blade", "@curse"],
        }
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_CANGKUN,
            leader_id,
        )

        buttons = app_replica._build_lightweight_room_action_buttons(
            room,
            join_command=join_command,
            include_enter=True,
            include_dissolve=True,
            include_query=True,
        )

        button_texts = self._button_texts(buttons)
        self.assertIn("加入推荐", button_texts)
        self.assertIn("加入备选", button_texts)
        self.assertIn("进入苍坤洞府", button_texts)
        primary_payload = self._button_payload_by_text(buttons, "加入推荐")
        backup_payload = self._button_payload_by_text(buttons, "加入备选")
        self.assertEqual(".加入副本 @walterwa2000 @healer @blade @curse", primary_payload.get("command"))
        self.assertEqual(".加入副本 @shield @healer @blade @curse", backup_payload.get("command"))
        self.assertEqual("lightweight_join:-100777:cangkun:47", primary_payload.get("exclusive_key"))
        self.assertEqual(primary_payload.get("exclusive_key"), backup_payload.get("exclusive_key"))

    def test_zhuimo_recommendation_prefers_baji_requires_dps_heart_and_items(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山")
        dps_id = self._register_replica_identity(991202, "dps", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        baji_id = self._register_replica_identity(991205, "jfdffdddd", root_attrs="土木", professions="御山|灵医")
        state_module.set_replica_participant_identity_ids([leader_id, dps_id, healer_id, blade_id, baji_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"坠魔谷禁制令": 1, "路线图": 1, "毒囊": 1}, "sections": {}},
        })

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )

        self.assertIn("推荐配置：坠魔谷｜职业补位（开房 @leader）", section)
        self.assertIn("推荐加入：@dps @healer @blade @jfdffdddd", section)
        self.assertIn("覆盖职业：破军、御山、灵医、影刃、咒师", section)
        self.assertIn("五职业已齐。", section)
        self.assertIn("DPS：@dps", section)
        self.assertIn("心劫：@jfdffdddd 可满足坠魔心劫。", section)
        self.assertIn("优先：已带吧唧。", section)
        self.assertIn("队长储物袋提醒：入本前确认路线图、毒囊、阴环均在队长储物袋；本地记录缺 阴环。", section)
        self.assertIn("默认路线：2-1。", section)
        self.assertEqual(".加入副本 @dps @healer @blade @jfdffdddd", join_command)

    def test_zhuimo_leader_item_reminder_uses_exact_item_names(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山")
        state_module.set_storage_bag_records({
            str(leader_id): {
                "items": {
                    "坠魔谷禁制令": 1,
                    "苍坤路线残图": 1,
                    "毒囊残片": 1,
                    "阴环碎片": 1,
                },
                "sections": {},
            },
        })

        reminder = app_replica._format_zhuimo_leader_item_reminder(leader_id)

        self.assertEqual(
            "队长储物袋提醒：入本前确认路线图、毒囊、阴环均在队长储物袋；本地记录缺 路线图、毒囊、阴环。",
            reminder,
        )

    def test_zhuimo_recommendation_can_use_baji_without_zhuimo_ticket(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山")
        dps_id = self._register_replica_identity(991202, "dps", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        baji_id = self._register_replica_identity(991205, "jfdffdddd", root_attrs="土木", professions="御山|灵医")
        fallback_curse_id = self._register_replica_identity(991206, "curse", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, dps_id, healer_id, blade_id, baji_id, fallback_curse_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"坠魔谷禁制令": 1}, "sections": {}},
            str(dps_id): {"items": {}, "sections": {}},
            str(healer_id): {"items": {}, "sections": {}},
            str(blade_id): {"items": {}, "sections": {}},
            str(baji_id): {"items": {}, "sections": {}},
            str(fallback_curse_id): {"items": {"坠魔谷禁制令": 1}, "sections": {}},
        })

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )

        self.assertIn("推荐加入：@dps @healer @blade @jfdffdddd", section)
        self.assertNotIn("@curse", section)
        self.assertIn("心劫：@jfdffdddd 可满足坠魔心劫。", section)
        self.assertIn("优先：已带吧唧。", section)
        self.assertEqual(".加入副本 @dps @healer @blade @jfdffdddd", join_command)

    def test_zhuimo_recommendation_blocks_join_without_dps(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山", sect_name="星宫")
        attacker_id = self._register_replica_identity(991202, "attacker", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, attacker_id, healer_id, blade_id, curse_id])

        section = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )
        join_command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_ZHUIMO,
            leader_id,
        )

        self.assertIn("五职业已齐。", section)
        self.assertIn("缺 DPS：坠魔谷必须带已勾选的金/雷 DPS，当前不推荐入本。", section)
        self.assertIn("心劫：@leader 可满足坠魔心劫。", section)
        self.assertEqual("", join_command)

    def test_zhuimo_room_buttons_require_actionable_plan(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山", sect_name="星宫")
        attacker_id = self._register_replica_identity(991202, "attacker", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, attacker_id, healer_id, blade_id, curse_id])
        room = {
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "room_id": "47",
            "leader_identity_id": leader_id,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
        }

        blocked_action = app_replica._get_lightweight_room_recommendation_action(room)
        blocked_buttons = app_replica._build_lightweight_room_action_buttons(
            room,
            join_command=blocked_action["join_command"],
            include_enter=blocked_action["include_enter"],
            include_dissolve=True,
            include_query=True,
        )
        state_module.set_replica_gold_dps_enabled(attacker_id, True)
        ready_action = app_replica._get_lightweight_room_recommendation_action(room)
        ready_buttons = app_replica._build_lightweight_room_action_buttons(
            room,
            join_command=ready_action["join_command"],
            include_enter=ready_action["include_enter"],
            include_dissolve=True,
            include_query=True,
        )
        joined_room = dict(room, join_requested_usernames=["@attacker", "@healer", "@blade", "@curse"])
        joined_action = app_replica._get_lightweight_room_recommendation_action(joined_room)
        joined_buttons = app_replica._build_lightweight_room_action_buttons(
            joined_room,
            join_command=joined_action["join_command"],
            include_enter=joined_action["include_enter"],
            include_dissolve=True,
            include_query=True,
        )

        self.assertEqual("", blocked_action["join_command"])
        self.assertNotIn("加入推荐", self._button_texts(blocked_buttons))
        self.assertNotIn("进入坠魔谷", self._button_texts(blocked_buttons))
        self.assertEqual(".加入副本 @attacker @healer @blade @curse", ready_action["join_command"])
        self.assertIn("加入推荐", self._button_texts(ready_buttons))
        self.assertNotIn("进入坠魔谷", self._button_texts(ready_buttons))
        self.assertIn("进入坠魔谷", self._button_texts(joined_buttons))

    def test_lightweight_enter_command_blocks_zhuimo_without_dps(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山", sect_name="星宫")
        attacker_id = self._register_replica_identity(991202, "attacker", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        event = self._prepare_replica_group([leader_id, attacker_id, healer_id, blade_id, curse_id])
        event.raw_text = ".进入坠魔谷"
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@attacker", "@healer", "@blade", "@curse"],
            "opened_at": now,
            "expires_at": 9999999999,
            "updated_at": now,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now + 2), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_enter_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, self._button_texts(buttons)

        handled, reply_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("当前不建议自动进入", reply_text)
        self.assertIn("缺 DPS", reply_text)
        self.assertIn("心劫：<code>@leader</code> 可满足坠魔心劫。", reply_text)
        self.assertIn("默认路线：2-1。", reply_text)
        self.assertNotIn("进入坠魔谷", button_texts)
        self.assertIn("解散副本", button_texts)

    def test_log_group_zhuimo_room_line_shows_readiness(self):
        leader_id = self._register_replica_identity(991201, "leader", professions="御山", sect_name="星宫")
        dps_id = self._register_replica_identity(991202, "dps", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        baji_id = self._register_replica_identity(991205, "jfdffdddd", professions="御山|灵医")
        state_module.set_replica_participant_identity_ids([leader_id, dps_id, healer_id, blade_id, baji_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        room = {
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "room_id": "47",
            "phase": "opened",
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@dps", "@healer", "@blade", "@jfdffdddd"],
        }

        line = app_replica._format_log_group_replica_room_line(room)
        html_line = app_replica._format_log_group_replica_room_line(room, html=True)

        self.assertIn("房间：坠魔谷 47", line)
        self.assertIn("五职+DPS+心劫可进", line)
        self.assertIn("DPS @dps", line)
        self.assertIn("心劫 @leader @jfdffdddd", line)
        self.assertIn("路线2-1", line)
        self.assertIn("@jfdffdddd", html_line)
        self.assertNotIn("&lt;code&gt;", html_line)

    def test_virtual_hall_recommendation_summarizes_route_advice_without_commands(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        first_id = self._register_replica_identity(991202, "first", root_attrs="金", professions="破军")
        second_id = self._register_replica_identity(991203, "second", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        state_module.set_replica_gold_dps_enabled(first_id, True)
        gua_record = {
            "room_id": "777",
            "leader_username": "@leader",
            "gua_title": "兑泽上离火下 · 四爻转阵",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "金", "count": 1, "required": True},
                {"role": "引灵", "element": "木", "count": 1, "required": True},
            ],
        }
        candidates = app_replica._parse_replica_query_reply_text(app_replica._format_replica_query_reply(""))
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=1)

        text = app_replica._format_virtual_hall_recommendations("777", gua_record, recommendations, candidates, lightweight=True)

        self.assertIn("路策：冰路 / 稳策", text)
        self.assertIn("实测顺合", text)
        self.assertIn("正1", text)
        self.assertIn("后续候选：争鼎夺鼎 / 后殿冲关", text)
        self.assertNotIn(".选择道路 冰", text)
        self.assertNotIn(".阵策 稳", text)
        self.assertNotIn(".争鼎 夺鼎", text)
        self.assertNotIn(".后殿抉择 冲关", text)
        self.assertNotIn("脚本不会自动发送", text)

        html_text = app_replica._format_virtual_hall_recommendations("777", gua_record, recommendations, candidates, lightweight=True, html=True)
        self.assertIn("路策：冰路 / 稳策", html_text)
        self.assertNotIn("<code>.选择道路 冰</code>", html_text)
        self.assertNotIn("<code>.阵策 稳</code>", html_text)
        self.assertNotIn("<code>.争鼎 夺鼎</code>", html_text)
        self.assertNotIn("<code>.后殿抉择 冲关</code>", html_text)

    def test_passive_opened_virtual_hall_broadcast_posts_lightweight_notice(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        dps_id = self._register_replica_identity(991202, "dps", root_attrs="金", professions="破军")
        support_id = self._register_replica_identity(991203, "support", root_attrs="火", professions="咒师")
        earth_id = self._register_replica_identity(991204, "earth", root_attrs="土", professions="御山")
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": 9001})
        state_module.set_replica_participant_identity_ids([leader_id, dps_id, support_id, earth_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        opened = (
            "【虚天殿已开启】\n"
            "@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1336\n"
            "其他道友可使用 .加入副本 1336 加入队伍！(5人满)\n\n"
            "【卦象词条】 乾天上艮山下 · 四爻转阵\n"
            "- 阵骨：土 必带\n"
            "- 主锋：金 x1（只认真位，不吃借生）\n"
            "- 引灵：火 位，可由 木 借生代行\n"
            "- 旁合：土 位更佳，若用 火 强顶只算偏配\n"
            "- 行运：后续道路与阵策同样受卦象牵引，但不会直示吉路与吉策。\n"
            "- 爻意：四爻重转阵，若无人护阵则整局易散。"
        )
        event = SimpleNamespace(id=9942743, chat_id=-1001680975844)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    event,
                    opened,
                    2000.0,
                    reply_context={"reply_to_msg_id": 9942741, "send_as_id": 0},
                )
                notice_args = app_replica._send_lightweight_replica_notice.await_args
                buttons = notice_args.kwargs["buttons"]
                return handled, notice_args.args[0], notice_args.args[1], self._button_texts(buttons)

        handled, room, notice_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(-100777, room["replica_chat_id"])
        self.assertEqual(leader_id, room["leader_identity_id"])
        self.assertEqual("passive_game_broadcast", room["opened_source"])
        self.assertIn("推荐配置：虚天殿 1336", notice_text)
        self.assertIn("加入推荐", button_texts)
        self.assertIn("进入虚天殿", button_texts)
        self.assertIn("解散副本", button_texts)
        saved_room = app_replica._get_lightweight_last_room(-100777, now=2000.0)
        self.assertEqual("1336", saved_room["room_id"])
        self.assertEqual("passive_game_broadcast", saved_room["opened_source"])

    def test_passive_opened_virtual_hall_broadcast_ignores_external_leader(self):
        self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": 9001})
        state_module.set_replica_participant_identity_ids([991201])
        opened = (
            "【虚天殿已开启】\n"
            "@external 消耗了【虚天残图】，开启了前往虚天殿的传送门！\n"
            "副本ID: 1337\n"
            "其他道友可使用 .加入副本 1337 加入队伍！"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))) as notice:
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=9943000, chat_id=-1001680975844),
                    opened,
                    2000.0,
                )
                return handled, notice.await_count

        handled, notice_count = asyncio.run(run_test())
        self.assertFalse(handled)
        self.assertEqual(0, notice_count)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=2000.0))

    def test_xutian_real_prompt_sends_decision_buttons_only_once(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        text = "\n".join([
            "【第二关·冰火之路】",
            "队长 @leader 需选择前路。",
            ".选择道路 冰",
            ".选择道路 火",
        ])
        event = SimpleNamespace(id=8801, chat_id=-100777, raw_text=text)

        async def run_test():
            with patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                first = await app_replica._handle_replica_progress_event(event, 1000.0)
                second = await app_replica._handle_replica_progress_event(event, 1001.0)
                send_mock.assert_not_awaited()
                buttons = audit_mock.await_args.kwargs["buttons"]
                return first, second, audit_mock.await_count, audit_mock.await_args.args[0], self._button_texts(buttons), buttons

        first, second, audit_count, notice_text, button_texts, buttons = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, audit_count)
        self.assertIn("虚天后续抉择：第二关·冰火之路", notice_text)
        self.assertIn("冰路", button_texts)
        self.assertIn("火路", button_texts)
        action_payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".选择道路 冰", ".选择道路 火"}, {payload["command"] for payload in action_payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in action_payloads})

    def test_xutian_same_room_prompt_dedupes_across_message_ids(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "8801",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "entered_at": 900.0,
            "updated_at": 900.0,
            "expires_at": 5000.0,
        })
        text = "\n".join([
            "【第二关·冰火之路】",
            "队长 @leader 需选择前路。",
            ".选择道路 冰",
            ".选择道路 火",
        ])

        async def run_test():
            with patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock:
                first = await app_replica._handle_replica_progress_event(SimpleNamespace(id=8801, chat_id=-100777, raw_text=text), 1000.0)
                second = await app_replica._handle_replica_progress_event(SimpleNamespace(id=8802, chat_id=-100777, raw_text=text), 1001.0)
                return first, second, audit_mock.await_count

        first, second, audit_count = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, audit_count)

    def test_xutian_external_leader_prompt_does_not_send_local_buttons(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        state_module.set_replica_run_state({
            "by_identity": {
                str(leader_id): {
                    "replica_states": {
                        app_replica._REPLICA_KIND_VIRTUAL_HALL: {
                            "participating": True,
                            "room_id": "1325",
                            "joined_at": 900.0,
                            "active_until": 5000.0,
                            "team_usernames": ["@leader"],
                            "team_identity_ids": [leader_id],
                        }
                    },
                    "leader_username": "@leader",
                    "updated_at": 900.0,
                }
            },
            "room_gua": {
                app_replica._REPLICA_KIND_VIRTUAL_HALL: {
                    "1325": {
                        "room_id": "1325",
                        "leader_username": "@leader",
                        "opened_at": 900.0,
                        "updated_at": 900.0,
                        "expires_at": 5000.0,
                    }
                }
            },
        })
        text = "\n".join([
            "【鼎前抉择】",
            "队长 @TrickPlayer，请在 120秒 内抉择：",
            "- 点击下方按钮，或输入 .争鼎 求稳 / .争鼎 夺鼎",
        ])
        event = SimpleNamespace(id=8803, chat_id=-100777, raw_text=text)

        async def run_test():
            with patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, 1000.0)
                send_mock.assert_not_awaited()
                audit_mock.assert_not_awaited()
                return handled

        self.assertFalse(asyncio.run(run_test()))

    def test_xutian_decision_buttons_send_one_choice_per_stage(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        text = "【鼎前抉择】\n队长 @leader 需抉择。\n.争鼎 求稳\n.争鼎 夺鼎"
        event = SimpleNamespace(id=8802, chat_id=-100777, raw_text=text)

        async def run_test():
            with patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock:
                handled = await app_replica._handle_replica_progress_event(event, 1000.0)
                buttons = audit_mock.await_args.kwargs["buttons"]
            stable_button = buttons[0][0]
            rush_button = buttons[0][1]
            with patch("model.app_replica.ADMIN_IDS", frozenset({123456})), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=901))) as send_mock, \
                    patch("model.app_replica.answer_log_bot_callback", new=AsyncMock()) as answer_mock:
                first = await app_replica.handle_replica_button_callback({
                    "id": "cb1",
                    "data": rush_button["callback_data"],
                    "from": {"id": 123456},
                })
                second = await app_replica.handle_replica_button_callback({
                    "id": "cb2",
                    "data": stable_button["callback_data"],
                    "from": {"id": 123456},
                })
                return handled, first, second, send_mock.await_args_list, answer_mock.await_args_list

        handled, first, second, send_calls, answer_calls = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".争鼎 夺鼎", send_calls[0].args[0])
        self.assertEqual(leader_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_calls[0].kwargs["priority"])
        self.assertEqual("自动副本", send_calls[0].kwargs["source_module"])
        self.assertEqual("keep", send_calls[0].kwargs["delete_policy"])
        self.assertEqual("本阶段已处理过。", answer_calls[1].args[1])

    def test_cangkun_first_stage_marks_entered_and_sends_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        member_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        group_event = self._prepare_replica_group([leader_id, member_id])
        now = 1000.0
        opened_text = "【苍坤上人洞府·集结】\n@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 47"
        app_replica._mark_replica_team_joined_from_text(opened_text, now=now, msg_id=991)
        app_replica._mark_replica_team_joined_from_text(
            "@WalterWA2000 已加入苍坤上人洞府队伍！\n当前队伍 (2/5):\n - @gyurihero\n - @WalterWA2000",
            now=now + 1,
            msg_id=992,
        )
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": group_event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@walterwa2000"],
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【苍坤上人洞府·第一幕】\n"
            "你们沿慕兰草原边缘潜行而来，前方太妙神禁若隐若现，洞府外层灵压沉沉压下。\n"
            "持识者：@WalterWA2000、@gyurihero | 可调神识：100\n"
            "请队长使用 .苍坤抉择 1/2/3 做出第一步选择。"
        )
        event = SimpleNamespace(id=8804, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                room_handled = await app_replica._handle_virtual_hall_auto_game_event(event, text, now + 2)
                progress_handled = await app_replica._handle_replica_progress_event(event, now + 2)
                send_mock.assert_not_awaited()
                audit_mock.assert_not_awaited()
                buttons = notice_mock.await_args.kwargs["buttons"]
                return room_handled, progress_handled, notice_mock.await_args.args[1], self._button_texts(buttons), buttons

        room_handled, progress_handled, notice_text, button_texts, buttons = asyncio.run(run_test())
        self.assertTrue(room_handled)
        self.assertTrue(progress_handled)
        self.assertIn("苍坤后续抉择：第一幕", notice_text)
        self.assertIn("建议：选1 匿踪潜行", notice_text)
        self.assertIn("可调神识 100 偏低", notice_text)
        self.assertIn("@gyurihero", notice_text)
        self.assertEqual({"选1", "选2", "选3"}, set(button_texts))
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".苍坤抉择 1", ".苍坤抉择 2", ".苍坤抉择 3"}, {payload["command"] for payload in payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in payloads})
        saved_room = app_replica._get_lightweight_last_room(group_event.chat_id, now=now + 2)
        self.assertEqual("entered", saved_room["phase"])
        records = state_module.get_replica_run_state()["by_identity"]
        self.assertEqual("@gyurihero", records[str(leader_id)]["leader_username"])
        self.assertEqual("@gyurihero", records[str(member_id)]["leader_username"])
        self.assertTrue(records[str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]["participating"])
        self.assertTrue(records[str(member_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]["participating"])

    def test_cangkun_first_stage_confirmation_cancels_enter_fast_retry(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": [],
            "enter_requested_at": now,
            "enter_msg_id": 778,
            "entered_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【苍坤上人洞府·第一幕】\n"
            "队长 @gyurihero 率队沿慕兰草原边缘潜行而来。\n"
            "持识者：@gyurihero | 可调神识：1200\n"
            "请队长使用 .苍坤抉择 1/2/3 做出第一步选择。"
        )
        event = SimpleNamespace(id=8804, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)), \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, now + 2)
                retried = await app_replica._retry_lightweight_game_command_once(
                    "enter",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "47",
                    ".进入苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return handled, retried, send_mock.await_count

        handled, retried, send_count = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertFalse(retried)
        self.assertEqual(0, send_count)
        saved_room = app_replica._get_lightweight_last_room(-100777, now=now + 3)
        self.assertGreater(saved_room["enter_confirmed_at"], now)

    def test_zhuimo_first_stage_marks_entered_sends_buttons_and_cancels_enter_retry(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        member_id = self._register_replica_identity(991202, "jfdffdddd", professions="咒师")
        group_event = self._prepare_replica_group([leader_id, member_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "95",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": group_event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "join_requested_usernames": ["@jfdffdddd"],
            "enter_requested_at": now,
            "enter_msg_id": 778,
            "opened_at": now - 30,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【坠魔谷·第一幕：裂隙外谷】\n"
            "你们踏入谷口，黑雾翻涌，封印符纹时明时灭。\n"
            "路径1 · 破煞突进：强行冲阵。\n"
            "路径2 · 稳守阵眼：以阵法缓进。\n"
            "路径3 · 潜行搜魂：避开主裂隙。\n"
            "使用 .坠魔抉择 路径1/路径2/路径3 继续。"
        )
        event = SimpleNamespace(id=8806, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, now + 2)
                retried = await app_replica._retry_lightweight_game_command_once(
                    "enter",
                    leader_id,
                    app_replica._REPLICA_KIND_ZHUIMO,
                    "95",
                    ".进入坠魔谷",
                    group_event.chat_id,
                    88006,
                    778,
                    delay_sec=0,
                )
                buttons = notice_mock.await_args.kwargs["buttons"]
                return handled, retried, send_mock.await_count, notice_mock.await_args.args[1], self._button_texts(buttons), buttons

        handled, retried, send_count, notice_text, button_texts, buttons = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertFalse(retried)
        self.assertEqual(0, send_count)
        self.assertIn("坠魔后续抉择：第一幕", notice_text)
        self.assertIn("默认路线 2-1", notice_text)
        self.assertEqual({"路径1 破煞", "路径2 稳守", "路径3 潜行"}, set(button_texts))
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".坠魔抉择 路径1", ".坠魔抉择 路径2", ".坠魔抉择 路径3"}, {payload["command"] for payload in payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in payloads})
        saved_room = app_replica._get_lightweight_last_room(group_event.chat_id, now=now + 3)
        self.assertEqual("entered", saved_room["phase"])
        self.assertGreater(saved_room["enter_confirmed_at"], now)

    def test_zhuimo_second_stage_sends_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "95",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "opened_at": now - 60,
            "entered_at": now - 30,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【第一幕结果】\n"
            "你们先稳固阵眼，再层层推进，魔气回卷被有效压制。\n"
            "当前状态：魔染 8 / 封印 52 / 士气 122\n\n"
            "【第二幕：心魔镜域】\n"
            "1 · 斩念守心：集体压制心魔杂念。\n"
            "2 · 纵魔借力：短暂引魔入体换取爆发。\n"
            "使用 .坠魔抉择 1 或 .坠魔抉择 2。"
        )
        event = SimpleNamespace(id=8807, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, now + 2)
                send_mock.assert_not_awaited()
                buttons = notice_mock.await_args.kwargs["buttons"]
                return handled, notice_mock.await_args.args[1], self._button_texts(buttons), buttons

        handled, notice_text, button_texts, buttons = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertIn("坠魔后续抉择：第二幕", notice_text)
        self.assertIn("选1 斩念守心", notice_text)
        self.assertEqual({"选1 斩念", "选2 借力"}, set(button_texts))
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".坠魔抉择 1", ".坠魔抉择 2"}, {payload["command"] for payload in payloads})

    def test_zhuimo_old_stage_button_is_blocked_after_stage_advances(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "95",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "opened_at": now - 60,
            "entered_at": now - 30,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        first_text = (
            "【坠魔谷·第一幕：裂隙外谷】\n"
            "路径1 · 破煞突进：强行冲阵。\n"
            "路径2 · 稳守阵眼：以阵法缓进。\n"
            "路径3 · 潜行搜魂：避开主裂隙。\n"
            "使用 .坠魔抉择 路径1/路径2/路径3 继续。"
        )
        second_text = (
            "【第一幕结果】\n"
            "你们先稳固阵眼，再层层推进，魔气回卷被有效压制。\n\n"
            "【第二幕：心魔镜域】\n"
            "1 · 斩念守心：集体压制心魔杂念。\n"
            "2 · 纵魔借力：短暂引魔入体换取爆发。\n"
            "使用 .坠魔抉择 1 或 .坠魔抉择 2。"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                await app_replica._handle_replica_progress_event(SimpleNamespace(id=8806, chat_id=-100123, raw_text=first_text), now + 1)
                first_buttons = notice_mock.await_args.kwargs["buttons"]
                old_button = next(
                    button
                    for row in first_buttons
                    for button in row
                    if button.get("text") == "路径2 稳守"
                )
                await app_replica._handle_replica_progress_event(SimpleNamespace(id=8807, chat_id=-100123, raw_text=second_text), now + 2)
            _token, old_action = app_replica._get_replica_button_action(old_button["callback_data"])
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=901))) as send_mock:
                ok, message = await app_replica._execute_replica_button_action(old_action, actor_id=123456)
                return ok, message, send_mock.await_args_list

        ok, message, send_calls = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn("阶段已过期", message)
        self.assertEqual([], send_calls)

    def test_zhuimo_button_tracks_missing_progress_ack_without_resending(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "105",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "opened_at": now - 60,
            "entered_at": now - 30,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        first_text = (
            "【坠魔谷·第一幕：裂隙外谷】\n"
            "路径1 · 破煞突进：强行冲阵。\n"
            "路径2 · 稳守阵眼：以阵法缓进。\n"
            "路径3 · 潜行搜魂：避开主裂隙。\n"
            "使用 .坠魔抉择 路径1/路径2/路径3 继续。"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica._schedule_zhuimo_progress_ack_timeout", return_value=True) as schedule_mock:
                await app_replica._handle_replica_progress_event(SimpleNamespace(id=8806, chat_id=-100123, raw_text=first_text), now + 1)
                first_buttons = notice_mock.await_args.kwargs["buttons"]
                button = next(
                    button
                    for row in first_buttons
                    for button in row
                    if button.get("text") == "路径2 稳守"
                )
                _token, action = app_replica._get_replica_button_action(button["callback_data"])
                self.assertEqual(app_replica._REPLICA_KIND_ZHUIMO, action["payload"]["progress_ack"]["kind"])
                with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9901))) as send_mock:
                    ok, message = await app_replica._execute_replica_button_action(action, actor_id=123456)
                    send_mock.assert_awaited_once()
                schedule_mock.assert_called_once()
                ack_key = schedule_mock.call_args.args[0]
                with patch("model.app_replica._send_replica_kind_notice", new=AsyncMock(return_value=True)) as ack_notice_mock, \
                        patch("model.app_replica.send_game_command", new=AsyncMock()) as timeout_send_mock:
                    alerted = await app_replica._send_zhuimo_progress_ack_timeout_notice(
                        ack_key,
                        now=now + app_replica._ZHUIMO_PROGRESS_ACK_DELAY_SEC + 5,
                    )
                    timeout_send_mock.assert_not_awaited()
                return ok, message, alerted, ack_notice_mock.await_args.args[1], state_module.get_replica_run_state()["zhuimo_progress_acks"][ack_key]

        ok, message, alerted, notice_text, ack_item = asyncio.run(run_test())

        self.assertTrue(ok)
        self.assertIn("已发送：.坠魔抉择 路径2", message)
        self.assertTrue(alerted)
        self.assertIn("坠魔谷进度回执缺失", notice_text)
        self.assertIn("不要重复发送同一阶段命令", notice_text)
        self.assertEqual(".坠魔抉择 路径2", ack_item["command"])
        self.assertGreater(ack_item["alerted_at"], 0)

    def test_zhuimo_progress_ack_clears_when_second_stage_arrives(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "105",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "opened_at": now - 60,
            "entered_at": now - 30,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        stage_scope = "zhuimo:room:105"
        ack_key = app_replica._mark_zhuimo_progress_ack_pending(
            {
                "kind": app_replica._REPLICA_KIND_ZHUIMO,
                "stage_scope": stage_scope,
                "stage_key": "decision:zhuimo:room:105:first",
                "stage": "first",
                "stage_title": "第一幕：裂隙外谷",
                "expected": "第一幕结果/第二幕按钮",
                "room_id": "105",
                "leader_username": "@WalterWA2000",
            },
            {
                "command": ".坠魔抉择 路径2",
                "identity_id": leader_id,
                "stage_guard_scope": stage_scope,
                "stage_guard_key": "decision:zhuimo:room:105:first",
                "source_msg_id": 8806,
            },
            SimpleNamespace(id=9901),
            actor_id=123456,
            now=now,
        )
        self.assertTrue(ack_key)
        second_text = (
            "【第一幕结果】\n"
            "你们先稳固阵眼，再层层推进，魔气回卷被有效压制。\n\n"
            "【第二幕：心魔镜域】\n"
            "1 · 斩念守心：集体压制心魔杂念。\n"
            "2 · 纵魔借力：短暂引魔入体换取爆发。\n"
            "使用 .坠魔抉择 1 或 .坠魔抉择 2。"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)):
                return await app_replica._handle_replica_progress_event(SimpleNamespace(id=8807, chat_id=-100123, raw_text=second_text), now + 2)

        handled = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertNotIn(ack_key, state_module.get_replica_run_state().get("zhuimo_progress_acks", {}))

    def test_zhuimo_late_second_result_reports_progress_and_clears_ack(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        self._prepare_replica_group([leader_id])
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "105",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "opened_at": now - 120,
            "entered_at": now - 90,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        stage_scope = "zhuimo:room:105"
        ack_key = app_replica._mark_zhuimo_progress_ack_pending(
            {
                "kind": app_replica._REPLICA_KIND_ZHUIMO,
                "stage_scope": stage_scope,
                "stage_key": "decision:zhuimo:room:105:first",
                "stage": "first",
                "stage_title": "第一幕：裂隙外谷",
                "expected": "第一幕结果/第二幕按钮",
                "room_id": "105",
                "leader_username": "@WalterWA2000",
            },
            {
                "command": ".坠魔抉择 路径2",
                "identity_id": leader_id,
                "stage_guard_scope": stage_scope,
                "stage_guard_key": "decision:zhuimo:room:105:first",
                "source_msg_id": 8806,
            },
            SimpleNamespace(id=9901),
            actor_id=123456,
            now=now,
        )
        self.assertTrue(ack_key)
        progress_text = (
            "【第二幕结果】\n"
            "你们逆转功法借用魔气，战力暴涨，但识海边缘已出现坠魔裂痕。\n\n"
            "当前状态：魔染 34 / 封印 66 / 士气 114\n"
            "你们冲入祭坛核心，准备与古魔残识决战！"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(id=10543532, chat_id=-100123, raw_text=progress_text),
                    now + 79,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("坠魔谷进度：第二幕结果", notice_text)
        self.assertIn("魔染 34", notice_text)
        self.assertNotIn(ack_key, state_module.get_replica_run_state().get("zhuimo_progress_acks", {}))

    def test_zhuimo_progress_without_local_context_is_not_reported(self):
        now = time.time()
        progress_text = (
            "【第 7 回合】\n"
            "古魔施展了 裂隙震荡：士气 -5，魔染 +1，封印稳定度 -6\n"
            "> 你们补全封印阵纹，封印 +4。\n"
            "> 残识血量：0/149019124258 | 魔染：25 | 封印：88/100 | 士气：81"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(id=10565649, chat_id=-100123, raw_text=progress_text),
                    now,
                )
                return handled, notice_mock.await_count

        handled, notice_count = asyncio.run(run_test())

        self.assertFalse(handled)
        self.assertEqual(0, notice_count)

    def test_luoyun_first_stage_marks_entered_and_sends_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", realm="结丹后期", sect_name="落云宗")
        member_id = self._register_replica_identity(991202, "growrdick")
        group_event = self._prepare_replica_group([leader_id, member_id])
        now = 1000.0
        opened_text = (
            "【落云秘圃·集结】\n"
            "@gyurihero 预缴 420贡献，开启了落云宗后山秘圃的临时禁门。\n"
            "副本ID: 47\n"
            "其他道友可使用 .加入落云秘圃 47 加入队伍！(5人满)"
        )
        app_replica._mark_replica_team_joined_from_text(opened_text, now=now, msg_id=991)
        app_replica._mark_replica_team_joined_from_text(
            "@growrdick 已加入落云秘圃队伍！\n当前队伍 (2/5):\n - @gyurihero\n - @growrdick",
            now=now + 1,
            msg_id=992,
        )
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_LUOYUN,
            "replica_chat_id": group_event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@growrdick"],
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【落云秘圃·第一幕】\n"
            "队伍：@growrdick、@gyurihero\n"
            "队伍战力：83046868949 / 4250000 | 可调神识：36874 | 掌天瓶共鸣：有\n\n"
            "1 · 温养阵眼：以木水灵力慢慢修复外层阵基，降低伤根值，收益更稳。\n"
            "2 · 强破护傀：先击毁镇灵木傀再截枝，速度快，但伤根值明显上升。\n"
            "3 · 瓶灵共鸣：借掌天瓶气机寻找真正活脉节点；队伍无人持瓶时会退化为普通探脉。\n\n"
            "请队长使用 .落云抉择 1/2/3 选择护树截枝路线。"
        )
        event = SimpleNamespace(id=8805, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                room_handled = await app_replica._handle_virtual_hall_auto_game_event(event, text, now + 2)
                progress_handled = await app_replica._handle_replica_progress_event(event, now + 2)
                send_mock.assert_not_awaited()
                audit_mock.assert_not_awaited()
                buttons = notice_mock.await_args.kwargs["buttons"]
                return room_handled, progress_handled, notice_mock.await_args.args[1], self._button_texts(buttons), buttons

        room_handled, progress_handled, notice_text, button_texts, buttons = asyncio.run(run_test())
        self.assertTrue(room_handled)
        self.assertTrue(progress_handled)
        self.assertIn("落云后续抉择：第一幕", notice_text)
        self.assertIn("@gyurihero", notice_text)
        self.assertEqual({"1 温养阵眼", "2 强破护傀", "3 瓶灵共鸣"}, set(button_texts))
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".落云抉择 1", ".落云抉择 2", ".落云抉择 3"}, {payload["command"] for payload in payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in payloads})
        saved_room = app_replica._get_lightweight_last_room(group_event.chat_id, now=now + 2)
        self.assertEqual("entered", saved_room["phase"])
        records = state_module.get_replica_run_state()["by_identity"]
        self.assertTrue(records[str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_LUOYUN]["participating"])
        self.assertTrue(records[str(member_id)]["replica_states"][app_replica._REPLICA_KIND_LUOYUN]["participating"])

    def test_luoyun_later_stage_sends_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "growrdick", realm="结丹后期", sect_name="落云宗")
        member_id = self._register_replica_identity(991202, "jfdffdddd")
        group_event = self._prepare_replica_group([leader_id, member_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "65",
            "replica_kind": app_replica._REPLICA_KIND_LUOYUN,
            "replica_chat_id": group_event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@growrdick",
            "join_requested_usernames": ["@jfdffdddd"],
            "opened_at": now,
            "entered_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【第一幕·破禁入圃·结果】\n"
            "你们分作数路探明禁制薄弱处，虽耗了些神识，仍找到一条较稳的入圃路径。\n\n"
            "当前状态：伤根值 10 | 灵压稳定 66 | 累计判定分 32\n\n"
            "【落云秘圃·第二幕·护根稳压】\n"
            "深入侧根后，护根灵影开始回潮。此时要先稳住母树活脉，才有资格谈截枝。\n\n"
            "1 · 三才护根：以队伍站位镇住木、水、土三才位，修复前一幕伤根。\n"
            "2 · 引傀离根：诱出镇灵木傀残影再战，能提高掉落，但会继续伤根。\n"
            "3 · 瓶灵照脉：借掌天瓶气机照出真正活脉；队伍无人持瓶时效果大幅下降。\n\n"
            "请队长继续使用 .落云抉择 1/2/3 做出下一幕抉择。"
        )
        event = SimpleNamespace(id=10620545, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, now + 2)
                send_mock.assert_not_awaited()
                audit_mock.assert_not_awaited()
                buttons = notice_mock.await_args.kwargs["buttons"]
                return handled, notice_mock.await_args.args[1], self._button_texts(buttons)

        handled, notice_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("落云后续抉择：第二幕·护根稳压", notice_text)
        self.assertEqual({"1 三才护根", "2 引傀离根", "3 瓶灵照脉"}, set(button_texts))

    def test_cangkun_later_stage_without_dungeon_name_sends_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        member_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 2000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 47",
            now=now - 600,
            msg_id=991,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@WalterWA2000 已加入苍坤上人洞府队伍！\n当前队伍 (2/5):\n - @gyurihero\n - @WalterWA2000",
            now=now - 599,
            msg_id=992,
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now - 500,
            source_msg_id=993,
            leader_username="@gyurihero",
        )
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": 9001})
        text = (
            "【第四幕前夜·玉矶阁反目】\n"
            "背盟三人 @WalterWA2000、@gyurihero 趁卷轴现形，当场反手夺图。\n\n"
            "当前状态：神魂稳度 104 / 慕兰警戒 55 / 贪念 32 / 卷轴线索 2\n\n"
            "【第五幕·分宝脱身】\n"
            "1 · 平分速退：不再恋战，压住贪念，拿到什么就立刻往外撤。\n"
            "2 · 夺图先遁：优先把卷轴线索带出去，其余重宝先放一放。\n"
            "3 · 暗藏后手：想办法再顺走一件更值钱的遗宝，赌一手极限收益。\n\n"
            "请队长使用 .苍坤抉择 1/2/3 决定如何脱身。"
        )
        event = SimpleNamespace(id=8805, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                progress_handled = await app_replica._handle_replica_progress_event(event, now)
                send_mock.assert_not_awaited()
                audit_mock.assert_not_awaited()
                buttons = notice_mock.await_args.kwargs["buttons"]
                return progress_handled, notice_mock.await_args.args[1], buttons

        progress_handled, notice_text, buttons = asyncio.run(run_test())
        self.assertTrue(progress_handled)
        self.assertIn("苍坤后续抉择：第五幕·分宝脱身", notice_text)
        self.assertIn("建议：选2 夺图先遁", notice_text)
        self.assertIn("慕兰警戒 55", notice_text)
        self.assertIn("@gyurihero", notice_text)
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".苍坤抉择 1", ".苍坤抉择 2", ".苍坤抉择 3"}, {payload["command"] for payload in payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in payloads})

    def test_cangkun_fifth_stage_advice_blocks_greed_when_warning_high(self):
        text = (
            "当前状态：神魂稳度 94 / 慕兰警戒 63 / 贪念 35 / 卷轴线索 3\n\n"
            "【第五幕·分宝脱身】\n"
            "1 · 平分速退\n"
            "2 · 夺图先遁\n"
            "3 · 暗藏后手\n\n"
            "请队长使用 .苍坤抉择 1/2/3 决定如何脱身。"
        )
        stage_info = app_replica._get_cangkun_decision_stage(text)

        advice = app_replica._format_cangkun_decision_advice(stage_info, text)

        self.assertIn("选2 夺图先遁", advice)
        self.assertIn("慕兰警戒 63>=60，不走3", advice)

    def test_cangkun_team_stage_sends_per_identity_decision_buttons(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        second_id = self._register_replica_identity(991203, "myios17", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        now = 2000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 47",
            now=now - 600,
            msg_id=991,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@myios17 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @gyurihero (灵医)\n"
            "- @WalterWA2000 (破军)\n"
            "- @myios17 (咒师)",
            now=now - 599,
            msg_id=992,
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now - 500,
            source_msg_id=993,
            leader_username="@gyurihero",
        )
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": 9001})
        text = (
            "【第三幕结果】\n"
            "你们先撬开玉盒，洞府宝光顿时四散而起。\n\n"
            "【第四幕·玉矶阁反目】\n"
            "苍坤遗卷现形之后，阁中留下的古训却只容三人带着生门坐标先遁。\n\n"
            "1 · 守契护人：不愿当场反目，准备护住卷轴与同伴退路。\n"
            "2 · 夺图背盟：认定只有三人能带着生门坐标脱身，准备先夺卷轴再逼两人断后。\n\n"
            "此阶段需全员表态。每位队员都可使用 .苍坤抉择 1/2 决定立场。"
        )
        event = SimpleNamespace(id=8806, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                progress_handled = await app_replica._handle_replica_progress_event(event, now)
                buttons = notice_mock.await_args.kwargs["buttons"]
                return progress_handled, notice_mock.await_args.args[1], buttons

        progress_handled, notice_text, buttons = asyncio.run(run_test())
        self.assertTrue(progress_handled)
        self.assertIn("苍坤全员表态：第四幕·玉矶阁反目", notice_text)
        self.assertIn("建议：全员各点一次即可", notice_text)
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({leader_id, first_id, second_id}, {payload["identity_id"] for payload in payloads})
        self.assertEqual({".苍坤抉择 1", ".苍坤抉择 2"}, {payload["command"] for payload in payloads})
        stage_info = app_replica._get_cangkun_decision_stage(text)
        source_key = app_replica._make_cangkun_decision_notice_key(
            event,
            text,
            stage_info,
            leader_username="@gyurihero",
            now=now,
        )
        self.assertEqual(
            {f"cangkun:{source_key}:{identity_id}" for identity_id in (leader_id, first_id, second_id)},
            {payload["exclusive_key"] for payload in payloads},
        )

    def test_cangkun_team_stage_dedupes_same_stage_across_message_ids(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        second_id = self._register_replica_identity(991203, "myios17", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@WalterWA2000", "@myios17"],
            "entered_at": now - 60,
            "updated_at": now - 60,
            "expires_at": now + app_replica.REPLICA_ACTIVE_TTL_SEC,
        })
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 47",
            now=now - 600,
            msg_id=991,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@myios17 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @gyurihero (灵医)\n"
            "- @WalterWA2000 (破军)\n"
            "- @myios17 (咒师)",
            now=now - 599,
            msg_id=992,
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now - 500,
            source_msg_id=993,
            leader_username="@gyurihero",
        )
        text = (
            "【第三幕结果】\n"
            "你们先撬开玉盒，洞府宝光顿时四散而起。\n\n"
            "【第四幕·玉矶阁反目】\n"
            "苍坤遗卷现形之后，阁中留下的古训却只容三人带着生门坐标先遁。\n\n"
            "1 · 守契护人：不愿当场反目，准备护住卷轴与同伴退路。\n"
            "2 · 夺图背盟：认定只有三人能带着生门坐标脱身，准备先夺卷轴再逼两人断后。\n\n"
            "此阶段需全员表态。每位队员都可使用 .苍坤抉择 1/2 决定立场。"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                first = await app_replica._handle_replica_progress_event(SimpleNamespace(id=8806, chat_id=-100123, raw_text=text), now)
                second = await app_replica._handle_replica_progress_event(SimpleNamespace(id=8807, chat_id=-100123, raw_text=text), now + 1)
                return first, second, notice_mock.await_count

        first, second, notice_count = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, notice_count)

    def test_cangkun_old_stage_button_is_blocked_after_stage_advances(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        button = app_replica._game_command_action_button(
            "选1",
            ".苍坤抉择 1",
            leader_id,
            source_msg_id=8806,
            token_key="old-cangkun-stage",
            exclusive_key="cangkun:old-stage",
            stage_guard_scope="cangkun:room:47",
            stage_guard_key="old-stage",
        )
        _token, action = app_replica._get_replica_button_action(button["callback_data"])
        app_replica._set_cangkun_stage_guard_current("cangkun:room:47", "new-stage")

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=901))) as send_mock:
                ok, message = await app_replica._execute_replica_button_action(action, actor_id=123456)
                return ok, message, send_mock.await_args_list

        ok, message, send_calls = asyncio.run(run_test())
        self.assertTrue(ok)
        self.assertIn("阶段已过期", message)
        self.assertEqual([], send_calls)

    def test_kunwu_road_stage_auto_sends_preferred_choice(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now - 60,
            "updated_at": now - 60,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【昆吾山·登山道】\n"
            "@leader 踏入了昆吾山麓，山道间灵雾翻涌。\n\n"
            "【抵达第1层】\n"
            "岔路 1：前方隐有朱果清香，似有果树藏在雾中。\n"
            "岔路 2：石壁间传来空间波动，像是一处传送阵捷径。\n\n"
            "请队长使用 .选择 岔路1/2 继续前进。"
        )
        event = SimpleNamespace(id=8816, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock(return_value=True)) as audit_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=901))) as send_mock, \
                    patch("model.app_replica.console_log") as console_mock, \
                    patch("model.app_replica._fire_and_forget", side_effect=self._close_scheduled):
                handled = await app_replica._handle_replica_progress_event(event, now)
                audit_mock.assert_not_awaited()
                return handled, notice_mock.await_count, console_mock.call_args, send_mock.await_args_list

        handled, notice_count, console_args, send_calls = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(0, notice_count)
        self.assertIn("昆吾山自动抉择：昆吾山第1层", console_args.args[0])
        self.assertIn(".选择 岔路1", console_args.args[0])
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".选择 岔路1", send_calls[0].args[0])
        self.assertEqual(leader_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_calls[0].kwargs["priority"])
        self.assertEqual("自动副本", send_calls[0].kwargs["source_module"])
        saved_room = app_replica._get_lightweight_last_room(-100777, now=now)
        self.assertEqual("entered", saved_room["phase"])

    def test_kunwu_encounter_auto_prefers_force_pick(self):
        stage = app_replica._get_kunwu_decision_stage(
            "【奇遇：你们发现了一株即将成熟的【朱果】，但旁边有妖兽守护的痕迹。】\n"
            "你们遭遇了特殊事件，请队长做出抉择！\n"
            ".选择 强行摘取\n"
            ".选择 静待时机"
        )

        self.assertEqual("encounter", stage["stage"])
        self.assertEqual(".选择 强行摘取", app_replica._get_kunwu_auto_decision_command(stage))

    def test_kunwu_auto_choice_retry_skips_when_new_stage_is_current(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "entered_at": now - 5,
            "updated_at": now - 5,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        scope = "room:88"
        first_key = app_replica._make_kunwu_auto_choice_key(scope, "road:1", leader_id, ".选择 岔路1")
        second_key = app_replica._make_kunwu_auto_choice_key(scope, "road:2", leader_id, ".选择 岔路2")
        self.assertTrue(app_replica._mark_kunwu_auto_choice_once(first_key, scope, now))
        self.assertTrue(app_replica._mark_kunwu_auto_choice_once(second_key, scope, now + 1))

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                retried = await app_replica._retry_kunwu_auto_choice_once(
                    first_key,
                    scope,
                    leader_id,
                    "88",
                    ".选择 岔路1",
                    8816,
                    901,
                    f"kunwu_auto_choice:{scope}:{leader_id}:road:1",
                    delay_sec=0,
                )
                return retried, send_mock.await_count

        retried, send_count = asyncio.run(run_test())
        self.assertFalse(retried)
        self.assertEqual(0, send_count)

    def test_kunwu_auto_choice_retry_skips_after_first_reply(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "entered_at": now - 5,
            "updated_at": now - 5,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        scope = "room:88"
        key = app_replica._make_kunwu_auto_choice_key(scope, "road:1", leader_id, ".选择 岔路1")
        self.assertTrue(app_replica._mark_kunwu_auto_choice_once(key, scope, now))

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now + 3), \
                    patch("model.app_replica._has_recent_game_reply_to_message", return_value=True) as reply_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                retried = await app_replica._retry_kunwu_auto_choice_once(
                    key,
                    scope,
                    leader_id,
                    "88",
                    ".选择 岔路1",
                    8816,
                    901,
                    f"kunwu_auto_choice:{scope}:{leader_id}:road:1",
                    delay_sec=0,
                )
                return retried, reply_mock.call_args, send_mock.await_count

        retried, reply_call, send_count = asyncio.run(run_test())
        self.assertFalse(retried)
        self.assertEqual(901, reply_call.args[0])
        self.assertEqual(0, send_count)

    def test_kunwu_road_stage_falls_back_to_buttons_when_auto_send_fails(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now - 60,
            "updated_at": now - 60,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【昆吾山·登山道】\n"
            "@leader 踏入了昆吾山麓，山道间灵雾翻涌。\n\n"
            "【抵达第1层】\n"
            "岔路 1：前方隐有朱果清香，似有果树藏在雾中。\n"
            "岔路 2：石壁间传来空间波动，像是一处传送阵捷径。\n\n"
            "请队长使用 .选择 岔路1/2 继续前进。"
        )
        event = SimpleNamespace(id=8817, chat_id=-100123, raw_text=text)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=None)) as send_mock:
                handled = await app_replica._handle_replica_progress_event(event, now)
                buttons = notice_mock.await_args.kwargs["buttons"]
                return handled, send_mock.await_args_list, notice_mock.await_args.args[1], self._button_texts(buttons), buttons

        handled, send_calls, notice_text, button_texts, buttons = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".选择 岔路1", send_calls[0].args[0])
        self.assertIn("昆吾山抉择：昆吾山第1层", notice_text)
        self.assertIn("岔路1 朱果", button_texts)
        self.assertIn("岔路2 捷径", button_texts)
        payloads = [
            app_replica._get_replica_button_action(button["callback_data"])[1]["payload"]
            for row in buttons
            for button in row
        ]
        self.assertEqual({".选择 岔路1", ".选择 岔路2"}, {payload["command"] for payload in payloads})
        self.assertEqual({leader_id}, {payload["identity_id"] for payload in payloads})

    def test_cangkun_manual_decision_command_is_not_stage_prompt(self):
        self.assertEqual({}, app_replica._get_cangkun_decision_stage(".苍坤抉择 1"))

    def test_cangkun_log_notice_is_not_stage_prompt(self):
        self.assertEqual(
            {},
            app_replica._get_cangkun_decision_stage(
                "苍坤全员表态：后续抉择｜队长 @myios17\n"
                "请选择一个按钮；兜底命令：\n"
                ".苍坤抉择 1"
            ),
        )
        self.assertEqual(
            {},
            app_replica._get_cangkun_decision_stage(
                "【🍃 监控日志 23:16:00】\n"
                "[myios17] 苍坤全员表态：后续抉择｜队长 @myios17\n"
                "请选择一个按钮；兜底命令：\n"
                ".苍坤抉择 1"
            ),
        )
        self.assertEqual(
            {},
            app_replica._get_cangkun_decision_stage(
                "【🍃 监控日志 23:16:16】\n"
                "[myios17] 苍坤后续抉择：第五幕·分宝脱身｜队长 @myios17\n"
                "请选择一个按钮；兜底命令：\n"
                ".苍坤抉择 1\n"
                ".苍坤抉择 2\n"
                ".苍坤抉择 3"
            ),
        )

    def test_cangkun_final_failure_marks_team_cooldown(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        second_id = self._register_replica_identity(991203, "myios17", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n"
            "@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n"
            "房间ID: 47\n"
            "其他道友可使用 .加入苍坤洞府 47 加入队伍！",
            now=now,
            msg_id=9983048,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@myios17 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @gyurihero (灵医)\n"
            "- @WalterWA2000 (破军)\n"
            "- @myios17 (咒师)",
            now=now + 1,
            msg_id=9983059,
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now + 2,
            source_msg_id=9983074,
            leader_username="@gyurihero",
        )
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@walterwa2000", "@myios17"],
            "opened_at": now,
            "updated_at": now + 2,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9983204,
                        raw_text=(
                            "【苍坤上人洞府·脱身失败】\n"
                            "你们贪得太深，刚想把最后一件遗宝卷走，外层警戒与余禁便一齐压了回来。\n\n"
                            "虽未能把洞府遗宝尽数带出，你们仍各自获得 2200修为、140贡献。\n"
                            "最终禁制裂隙：106 | 神魂稳度：104 | 慕兰警戒：63 | 卷轴线索：2"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("苍坤结算：脱身失败", notice_text)
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 3 个身份 CD", notice_text)
        self.assertIn("2200修为", notice_text)
        self.assertIn("140贡献", notice_text)
        self.assertIn("最终禁制裂隙", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, first_id, second_id):
            record = records[str(identity_id)]
            state_item = record["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertFalse(state_item["participating"])
            self.assertEqual("", state_item["room_id"])
            self.assertEqual("47", state_item["last_completed_room_id"])
            self.assertGreaterEqual(state_item["cooldown_until"], now + 180 + app_replica.REPLICA_CANGKUN_SUCCESS_COOLDOWN_SEC)
            self.assertEqual("success_cooldown", record["last_join_result"])
            status = app_replica._get_replica_identity_kind_status(identity_id, app_replica._REPLICA_KIND_CANGKUN, now + 181)
            self.assertNotEqual("可", status)
            self.assertRegex(status, r"^\d+:\d{2}$")

    def test_cangkun_final_failure_clears_lightweight_room_without_identity_match(self):
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": 991201,
            "leader_username": "@gyurihero",
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        handled = asyncio.run(app_replica._handle_replica_progress_event(
            SimpleNamespace(
                id=9983204,
                raw_text=(
                    "【苍坤上人洞府·脱身失败】\n"
                    "你们贪得太深，刚想把最后一件遗宝卷走，外层警戒与余禁便一齐压了回来。\n\n"
                    "虽未能把洞府遗宝尽数带出，你们仍各自获得 2200修为、140贡献。\n"
                    "最终禁制裂隙：106 | 神魂稳度：104 | 慕兰警戒：63 | 卷轴线索：2"
                ),
            ),
            now + 180,
        ))

        self.assertTrue(handled)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))

    def test_cangkun_settlement_uses_lightweight_room_team_for_manual_run_cd(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        second_id = self._register_replica_identity(991203, "myios17", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@WalterWA2000", "@myios17"],
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9983204,
                        raw_text=(
                            "【苍坤上人洞府·脱身成功】\n"
                            "你们压住贪念，带着卷轴线索顺利脱身。\n\n"
                            "最终禁制裂隙：106 | 神魂稳度：104 | 慕兰警戒：63 | 卷轴线索：2"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 3 个身份 CD", notice_text)
        self.assertIn("最终禁制裂隙", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, first_id, second_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertFalse(state_item["participating"])
            self.assertEqual("", state_item["room_id"])
            self.assertEqual("47", state_item["last_completed_room_id"])
            self.assertGreaterEqual(state_item["cooldown_until"], now + 180 + app_replica.REPLICA_CANGKUN_SUCCESS_COOLDOWN_SEC)

    def test_zhuimo_settlement_uses_two_hour_cd_and_lightweight_team(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        first_id = self._register_replica_identity(991202, "growrdick", professions="御山")
        second_id = self._register_replica_identity(991203, "myios17", professions="灵医")
        third_id = self._register_replica_identity(991204, "xuruode1", professions="影刃")
        fourth_id = self._register_replica_identity(991205, "jfdffdddd", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id, third_id, fourth_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "95",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "join_requested_usernames": ["@growrdick", "@myios17", "@xuruode1", "@jfdffdddd"],
            "opened_at": now,
            "entered_at": now + 10,
            "updated_at": now + 10,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9983305,
                        raw_text=(
                            "【坠魔谷·封魔成功】\n"
                            "古魔残识被彻底镇压，封印重塑完成！\n\n"
                            "结算：每位队员获得 8000修为、650贡献\n"
                            "天命所归，幸运道友 @jfdffdddd 额外获得 【阴凝之晶】x2。\n"
                            "低魔染封印完成，全队额外获得【镇魔残篆】x1\n"
                            "最终魔染值：27 | 封印进度：124"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("坠魔谷结算：封魔成功", notice_text)
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 5 个身份 CD", notice_text)
        self.assertIn("最终魔染值", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, first_id, second_id, third_id, fourth_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_ZHUIMO]
            self.assertFalse(state_item["participating"])
            self.assertEqual("", state_item["room_id"])
            self.assertEqual("95", state_item["last_completed_room_id"])
            self.assertEqual(now + 180 + app_replica.REPLICA_ZHUIMO_SUCCESS_COOLDOWN_SEC, state_item["cooldown_until"])

    def test_zhuimo_failure_settlement_reports_to_lightweight_group(self):
        leader_id = self._register_replica_identity(991201, "WalterWA2000", professions="破军")
        first_id = self._register_replica_identity(991202, "growrdick", professions="御山")
        second_id = self._register_replica_identity(991203, "myios17", professions="灵医")
        third_id = self._register_replica_identity(991204, "xuruode1", professions="影刃")
        fourth_id = self._register_replica_identity(991205, "jfdffdddd", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id, third_id, fourth_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "105",
            "replica_kind": app_replica._REPLICA_KIND_ZHUIMO,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@WalterWA2000",
            "join_requested_usernames": ["@growrdick", "@myios17", "@xuruode1", "@jfdffdddd"],
            "opened_at": now,
            "entered_at": now + 10,
            "updated_at": now + 10,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9983306,
                        raw_text=(
                            "【坠魔谷·封魔失败】\n"
                            "虽击碎古魔残识，却未完成封印，魔潮二次爆发。\n\n"
                            "虽未能封魔，众人仍从死斗中有所感悟：每人获得 2000修为、120贡献。\n"
                            "最终魔染值：44 | 封印进度：79"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("坠魔谷结算：封魔失败", notice_text)
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 5 个身份 CD", notice_text)
        self.assertIn("最终魔染值：44", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))

    def test_virtual_hall_duoding_settlement_does_not_clear_active_cangkun_room(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id, first_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n"
            "@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n"
            "房间ID: 47",
            now=now,
            msg_id=9983048,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@WalterWA2000 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (2/5):\n"
            "- @gyurihero (灵医)\n"
            "- @WalterWA2000 (破军)",
            now=now + 1,
            msg_id=9983059,
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now + 2,
            source_msg_id=9983074,
            leader_username="@gyurihero",
        )
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@WalterWA2000"],
            "entered_at": now + 2,
            "updated_at": now + 2,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        text = (
            "【战利品结算·夺鼎】\n"
            "你们逆着鼎火冲入核心，试图在宝光消散前强夺最后一缕机缘。\n"
            "所有队员均获得 5000修为 和 500贡献！\n"
            "本次主殿迎战对象：蛮胡子之影。"
        )

        async def run_test():
            with patch("model.app_replica._send_replica_settlement_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(id=9984204, raw_text=text),
                    now + 180,
                )
                return handled, notice_mock.await_count

        handled, notice_count = asyncio.run(run_test())

        self.assertFalse(handled)
        self.assertEqual(0, notice_count)
        saved_room = app_replica._get_lightweight_last_room(-100777, now=now + 181)
        self.assertIsNotNone(saved_room)
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, saved_room["replica_kind"])
        self.assertEqual("47", saved_room["room_id"])

    def test_virtual_hall_settlement_notice_includes_result_excerpt(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first")
        state_module.set_replica_participant_identity_ids([leader_id, first_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@first"],
            "entered_at": now,
            "updated_at": now,
            "expires_at": now + 3600,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9984204,
                        raw_text=(
                            "【战利品结算·虚天殿】\n"
                            "@leader、@first 闯过后殿，灵光归匣。\n"
                            "获得：5000修为、260贡献、【玄晶】x1。"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("虚天殿结算", notice_text)
        self.assertIn("5000修为", notice_text)
        self.assertIn("玄晶", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))

    def test_kunwu_summit_settlement_clears_room_and_reports_result(self):
        leader_id = self._register_replica_identity(991201, "leader")
        state_module.set_replica_participant_identity_ids([leader_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "entered",
            "room_id": "332",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "entered_at": now,
            "updated_at": now,
            "expires_at": now + 3600,
        })
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_KUNWU,
            now,
            source_msg_id=9984300,
            leader_username="@leader",
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9984309,
                        raw_text=(
                            "【登顶昆吾山】\n"
                            "恭喜你们历经 10 回合，成功登顶！\n\n"
                            "最终收获:\n"
                            "- 每位队员获得 5000 点修为\n"
                            "- 队长 @leader 获得登顶至宝 【大挪移令】x1"
                        ),
                    ),
                    now + 180,
                )
                return handled, notice_mock.await_args.args[0], notice_mock.await_args.args[1]

        handled, notice_item, notice_text = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(-100777, notice_item["replica_chat_id"])
        self.assertIn("昆吾山结算：登顶昆吾山", notice_text)
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 1 个身份 CD", notice_text)
        self.assertIn("大挪移令", notice_text)
        self.assertIsNone(app_replica._get_lightweight_last_room(-100777, now=now + 181))
        records = state_module.get_replica_run_state()["by_identity"]
        state_item = records[str(leader_id)]["replica_states"][app_replica._REPLICA_KIND_KUNWU]
        self.assertFalse(state_item["participating"])
        self.assertEqual("", state_item["room_id"])
        self.assertEqual("332", state_item["last_completed_room_id"])

    def test_cangkun_final_failure_keeps_entered_lightweight_room_until_settlement(self):
        leader_id = self._register_replica_identity(991201, "gyurihero", professions="灵医")
        first_id = self._register_replica_identity(991202, "WalterWA2000", professions="破军")
        second_id = self._register_replica_identity(991203, "myios17", professions="咒师")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n"
            "@gyurihero 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n"
            "房间ID: 47\n"
            "其他道友可使用 .加入苍坤洞府 47 加入队伍！",
            now=now,
            msg_id=9983048,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@myios17 已加入苍坤上人洞府队伍！\n"
            "当前队伍 (3/5):\n"
            "- @gyurihero (灵医)\n"
            "- @WalterWA2000 (破军)\n"
            "- @myios17 (咒师)",
            now=now + 1,
            msg_id=9983059,
        )
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@gyurihero",
            "join_requested_usernames": ["@walterwa2000", "@myios17"],
            "opened_at": now,
            "updated_at": now + 1,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        entered_room = app_replica._mark_latest_lightweight_room_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now=now + 2,
            require_recent_enter_request=False,
            usernames=["@gyurihero", "@myios17"],
        )
        app_replica._mark_replica_team_entered(
            app_replica._REPLICA_KIND_CANGKUN,
            now + 2,
            source_msg_id=9983074,
            leader_username="@gyurihero",
        )

        self.assertGreater(entered_room["expires_at"], now + 180)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=True)) as lightweight_notice_mock, \
                    patch("model.app_replica._send_replica_kind_notice", new=AsyncMock(return_value=True)) as generic_notice_mock:
                handled = await app_replica._handle_replica_progress_event(
                    SimpleNamespace(
                        id=9983204,
                        raw_text=(
                            "【苍坤上人洞府·脱身失败】\n"
                            "你们贪得太深，刚想把最后一件遗宝卷走，外层警戒与余禁便一齐压了回来。\n\n"
                            "虽未能把洞府遗宝尽数带出，你们仍各自获得 2200修为、140贡献。\n"
                            "最终禁制裂隙：106 | 神魂稳度：104 | 慕兰警戒：63 | 卷轴线索：2"
                        ),
                    ),
                    now + 180,
                )
                return handled, lightweight_notice_mock.await_args.args[1], generic_notice_mock.await_count

        handled, notice_text, generic_notice_count = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertIn("已清理轻量房间记录", notice_text)
        self.assertIn("已记录 3 个身份 CD", notice_text)
        self.assertEqual(0, generic_notice_count)

    def test_manual_cangkun_enter_command_does_not_mark_success_cooldown(self):
        leader_id = self._register_replica_identity(991201, "leader")
        member_id = self._register_replica_identity(991202, "member")
        state_module.set_replica_participant_identity_ids([leader_id, member_id])
        now = 1000.0
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 47",
            now=now,
            msg_id=881,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@member 已加入苍坤上人洞府队伍！\n当前队伍 (2/5):\n - @leader\n - @member",
            now=now + 1,
            msg_id=882,
        )

        handled = asyncio.run(app_replica._handle_replica_progress_event(
            SimpleNamespace(id=883, sender_id=leader_id, raw_text=".进入苍坤洞府"),
            now + 2,
        ))

        self.assertFalse(handled)
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, member_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertNotEqual("success_cooldown", records[str(identity_id)].get("last_join_result"))
            self.assertEqual("joined", state_item["lobby_status"])

    def test_virtual_hall_no_local_dps_offers_self_dps_recommendation(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        first_id = self._register_replica_identity(991202, "first", root_attrs="火", professions="咒师")
        second_id = self._register_replica_identity(991203, "second", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id])
        gua_record = {
            "room_id": "914",
            "leader_username": "@leader",
            "gua_title": "坎水上乾天下 · 四爻转阵",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "金", "count": 1, "required": True},
                {"role": "引灵", "element": "木", "count": 1, "required": True},
            ],
        }
        candidates = app_replica._parse_replica_query_reply_text(app_replica._format_replica_query_reply(""))
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=3)

        text = app_replica._format_virtual_hall_recommendations("914", gua_record, recommendations, candidates, lightweight=True, html=True)

        self.assertIn("无本地DPS", text)
        self.assertIn("自找DPS：全匹配：<code>@second</code>｜DPS：自找大佬", text)
        self.assertNotIn("6 秒后自动解散", text)
        self.assertNotIn(".加入副本 @", text)

    def test_virtual_hall_thunder_candidate_without_dps_mark_counts_as_no_dps(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        thunder_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, thunder_id, healer_id])
        gua_record = {
            "room_id": "919",
            "leader_username": "@leader",
            "gua_title": "震雷上艮山下 · 二爻守中",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "金", "count": 1, "required": True},
                {"role": "引灵", "element": "木", "count": 1, "required": True},
            ],
        }
        candidates = app_replica._parse_replica_query_reply_text(app_replica._format_replica_query_reply(""))
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=3)

        text = app_replica._format_virtual_hall_recommendations("919", gua_record, recommendations, candidates, lightweight=True, html=True)

        self.assertIn("无本地DPS", text)
        self.assertIn("存在金/雷候选，但未勾选金/雷 DPS", text)
        self.assertIn("自找DPS：全匹配：<code>@healer</code>｜DPS：自找大佬", text)
        self.assertNotIn("6 秒后自动解散", text)
        self.assertNotIn("<code>.加入副本", text)
        self.assertNotIn("@wa2000", text)

    def test_virtual_hall_marked_thunder_dps_is_recommended(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        thunder_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, thunder_id, healer_id])
        state_module.set_replica_gold_dps_enabled(thunder_id, True)
        gua_record = {
            "room_id": "919",
            "leader_username": "@leader",
            "gua_title": "震雷上艮山下 · 二爻守中",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "金", "count": 1, "required": True},
                {"role": "引灵", "element": "木", "count": 1, "required": True},
            ],
        }
        candidates = app_replica._parse_replica_query_reply_text(app_replica._format_replica_query_reply(""))
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=3)

        text = app_replica._format_virtual_hall_recommendations("919", gua_record, recommendations, candidates, lightweight=True, html=True)

        self.assertNotIn("无DPS可用", text)
        self.assertNotIn("自动解散", text)
        self.assertIn("@wa2000", text)
        self.assertIn("推荐加入：全匹配：<code>@wa2000</code> <code>@healer</code>", text)
        self.assertNotIn("<code>.加入副本", text)

    def test_lightweight_virtual_hall_notice_hides_specific_join_fallback(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        dps_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", root_attrs="木", professions="灵医")
        water_id = self._register_replica_identity(991204, "water", root_attrs="水", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, dps_id, healer_id, water_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        event = self._prepare_replica_group([leader_id, dps_id, healer_id, water_id])
        now = 1000.0
        room = {
            "phase": "opened",
            "room_id": "919",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_msg_id": 601,
            "expires_at": 9999999999,
            "updated_at": now,
        }
        opened = "\n".join([
            "【虚天殿已开启】",
            "队长 @leader 开启虚天殿，房间ID: 919",
            "【卦象词条】震雷上艮山下 · 二爻守中",
            "阵骨：土必带",
            "主锋：金x1",
            "引灵：木位",
            "旁合：水位更佳",
        ])

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._send_lightweight_virtual_hall_recommendation(room, opened, now)
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, notice_text, self._button_payload_by_text(buttons, "加入推荐")

        handled, notice_text, join_payload = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("推荐加入：", notice_text)
        self.assertIn("@wa2000", notice_text)
        self.assertIn("@healer", notice_text)
        self.assertIn("@water", notice_text)
        self.assertIn("<code>.加入副本 @用户名 @用户名</code>", notice_text)
        self.assertNotIn("<code>.加入副本 @wa2000 @healer @water</code>", notice_text)
        self.assertEqual(".加入副本 @wa2000 @healer @water", join_payload.get("command"))

    def test_lightweight_virtual_hall_notice_offers_self_dps_join_button(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        healer_id = self._register_replica_identity(991202, "healer", root_attrs="木", professions="灵医")
        water_id = self._register_replica_identity(991203, "water", root_attrs="水", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, healer_id, water_id])
        event = self._prepare_replica_group([leader_id, healer_id, water_id])
        now = 1000.0
        room = {
            "phase": "opened",
            "room_id": "914",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_msg_id": 601,
            "expires_at": 9999999999,
            "updated_at": now,
        }
        opened = "\n".join([
            "【虚天殿已开启】",
            "队长 @leader 开启虚天殿，房间ID: 914",
            "【卦象词条】坎水上乾天下 · 四爻转阵",
            "阵骨：土必带",
            "主锋：金x1",
            "引灵：木位",
            "旁合：水位更佳",
        ])

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica._schedule_lightweight_room_auto_dissolve", return_value=True) as schedule:
                handled = await app_replica._send_lightweight_virtual_hall_recommendation(room, opened, now)
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, notice_text, buttons, schedule.call_count

        handled, notice_text, buttons, schedule_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(0, schedule_count)
        self.assertIn("自找DPS：全匹配：<code>@healer</code> <code>@water</code>｜DPS：自找大佬", notice_text)
        self.assertNotIn("自动解散", notice_text)
        self.assertIn("加入自找DPS", self._button_texts(buttons))
        self.assertIn("进入虚天殿", self._button_texts(buttons))
        join_payload = self._button_payload_by_text(buttons, "加入自找DPS")
        self.assertEqual(".加入副本 @healer @water", join_payload.get("command"))

    def test_existing_virtual_hall_room_buttons_fallback_to_self_dps_when_normal_is_not_actionable(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        dpswood_id = self._register_replica_identity(991202, "dpswood", root_attrs="金木", professions="破军|灵医")
        state_module.set_replica_participant_identity_ids([leader_id, dpswood_id])
        state_module.set_replica_gold_dps_enabled(dpswood_id, True)
        now = time.time()
        app_replica._mark_virtual_hall_gua_from_opened_text(
            "\n".join([
                "【虚天殿已开启】",
                "队长 @leader 开启虚天殿，房间ID: 1201",
                "【卦象词条】坎水上乾天下 · 四爻转阵",
                "阵骨：土必带",
                "主锋：金x1",
                "引灵：木位",
                "旁合：水位更佳",
            ]),
            now,
            "1201",
            leader_username="@leader",
            msg_id=601,
        )
        room = {
            "phase": "opened",
            "room_id": "1201",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": 9999999999,
            "updated_at": now,
        }

        self.assertTrue(app_replica._is_lightweight_room_enter_actionable(room))
        self.assertEqual(".加入副本 @dpswood", app_replica._get_lightweight_recommended_join_command_for_room(room))
        buttons = app_replica._lightweight_existing_room_notice_buttons(room)
        self.assertIn("加入自找DPS", self._button_texts(buttons))
        self.assertIn("进入虚天殿", self._button_texts(buttons))
        join_payload = self._button_payload_by_text(buttons, "加入自找DPS")
        self.assertEqual(".加入副本 @dpswood", join_payload.get("command"))

    def test_virtual_hall_core_match_allows_optional_missing_side_slot(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="火", professions="咒师")
        earth_id = self._register_replica_identity(991202, "earth", root_attrs="土木", professions="御山|灵医")
        dps_id = self._register_replica_identity(991203, "wa2000", root_attrs="雷", professions="破军")
        goldwood_id = self._register_replica_identity(991204, "goldwood", root_attrs="金木水", professions="灵医|破军")
        gold_id = self._register_replica_identity(991205, "gold", root_attrs="金水", professions="破军")
        wood_id = self._register_replica_identity(991206, "wood", root_attrs="木", professions="影刃")
        state_module.set_replica_participant_identity_ids([leader_id, earth_id, dps_id, goldwood_id, gold_id, wood_id])
        state_module.set_replica_gold_dps_enabled(dps_id, True)
        event = self._prepare_replica_group([leader_id, earth_id, dps_id, goldwood_id, gold_id, wood_id])
        now = time.time()
        room = {
            "phase": "opened",
            "room_id": "1415",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_msg_id": 601,
            "expires_at": 9999999999,
            "updated_at": now,
        }
        opened = "\n".join([
            "【虚天殿已开启】",
            "@leader 消耗了【虚天残图】，开启了前往虚天殿的传送门！",
            "副本ID: 1415",
            "【卦象词条】 震雷上乾天下 · 三爻争锋",
            "- 阵骨：土 必带",
            "- 主锋：金 x2（只认真位，不吃借生）",
            "- 引灵：金 位，可由 土 借生代行",
            "- 旁合：木 位更佳，若用 水 强顶只算偏配",
        ])

        async def run_notice():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._send_lightweight_virtual_hall_recommendation(room, opened, now)
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, notice_text, self._button_texts(buttons)

        handled, notice_text, button_texts = asyncio.run(run_notice())
        self.assertTrue(handled)
        self.assertIn("理想配置：土x1 金x3 木x1", notice_text)
        self.assertIn("队长 <code>@leader</code>(火) 不入本卦", notice_text)
        self.assertIn("推荐加入：未全匹配（缺旁合木）", notice_text)
        self.assertIn("加入推荐", button_texts)
        self.assertIn("进入虚天殿", button_texts)
        self.assertIn("解散副本", button_texts)

        app_replica._set_lightweight_last_room(room)
        event.raw_text = ".加入副本 @earth @wa2000 @goldwood @gold"
        async def run_join():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=800))) as notice, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_join_command(event)
                return handled, notice.await_args.args[2], send_mock.await_count

        join_handled, join_notice, send_count = asyncio.run(run_join())
        self.assertTrue(join_handled)
        self.assertEqual(4, send_count)
        self.assertIn("已发送加入虚天殿 1415", join_notice)

    def test_lightweight_virtual_hall_command_keeps_dps_when_leader_occupies_slot(self):
        leader_id = self._register_replica_identity(991201, "myios7", root_attrs="水木金土", professions="御山|灵医|破军")
        grow_id = self._register_replica_identity(991202, "growrdick", root_attrs="金木水土", professions="御山|灵医|破军")
        fan_id = self._register_replica_identity(991203, "fanb0x", root_attrs="金木水", professions="灵医|破军")
        jihe_id = self._register_replica_identity(991204, "jihejish", root_attrs="金木水", professions="灵医|破军")
        myios17_id = self._register_replica_identity(991205, "myios17", root_attrs="木火金土", professions="御山|灵医|破军|咒师")
        wa_id = self._register_replica_identity(991206, "walterwa2000", root_attrs="雷", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id, grow_id, fan_id, jihe_id, myios17_id, wa_id])
        state_module.set_replica_gold_dps_enabled(wa_id, True)
        gua_record = {
            "room_id": "1203",
            "leader_username": "@myios7",
            "gua_title": "巽风上坤地下 · 三爻争锋",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "木", "count": 2, "required": True},
                {"role": "引灵", "element": "土", "count": 1, "required": True, "fallback_element": "火", "fallback_type": "借生"},
                {"role": "旁合", "element": "金", "count": 1, "required": False, "fallback_element": "土", "fallback_type": "偏配"},
            ],
        }
        candidates = app_replica._parse_replica_query_reply_text(app_replica._format_replica_query_reply(""))
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=1)

        text = app_replica._format_virtual_hall_recommendations("1203", gua_record, recommendations, candidates, lightweight=True, html=True)

        self.assertIn("DPS：<code>@walterwa2000</code>", text)
        self.assertIn("@walterwa2000", app_replica._virtual_hall_recommendation_command_key(recommendations[0], leader_username="@myios7"))
        self.assertIn("<code>@walterwa2000</code>", text)
        self.assertNotIn("<code>@myios7</code>", text)
        self.assertNotIn("<code>.加入副本", text)

        event = self._prepare_replica_group([leader_id, grow_id, fan_id, jihe_id, myios17_id, wa_id])
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "1203",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@myios7",
            "expires_at": 9999999999,
            "updated_at": 1000,
        })
        join_command = app_replica._virtual_hall_join_command_from_recommendation(
            recommendations[0],
            leader_username="@myios7",
        )
        self.assertIn("@walterwa2000", join_command)
        self.assertNotIn("@myios7", join_command)
        event.raw_text = join_command

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=800))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701))):
                handled = await app_replica._handle_lightweight_join_command(event)
                return handled, app_replica.send_game_command.await_args_list

        handled, calls = asyncio.run(run_test())
        self.assertTrue(handled)
        sent_ids = [call.kwargs["send_as_id"] for call in calls]
        self.assertIn(wa_id, sent_ids)
        self.assertNotIn(leader_id, sent_ids)

    def test_virtual_hall_old_ticket_query_text_uses_local_dps_mark(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        thunder_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        healer_id = self._register_replica_identity(991203, "healer", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([leader_id, thunder_id, healer_id])
        state_module.set_replica_gold_dps_enabled(thunder_id, True)
        old_query_text = "\n".join([
            "可开副本：",
            "@leader | 虚:可 | 坠:可 | 黄:可 | 苍:可 | 土 | 御山",
            "@wa2000 | 苍x4 坠x1 | 虚:可 | 坠:可 | 黄:可 | 苍:可 | 雷 | 破军",
            "@healer | 虚:可 | 坠:可 | 黄:可 | 苍:可 | 木 | 灵医",
        ])
        gua_record = {
            "room_id": "919",
            "leader_username": "@leader",
            "gua_title": "震雷上艮山下 · 二爻守中",
            "requirements": [
                {"role": "阵骨", "element": "土", "count": 1, "required": True},
                {"role": "主锋", "element": "金", "count": 1, "required": True},
                {"role": "引灵", "element": "木", "count": 1, "required": True},
            ],
        }

        candidates = app_replica._parse_replica_query_reply_text(old_query_text)
        recommendations = app_replica._build_virtual_hall_recommendations(gua_record, candidates, limit=3)
        text = app_replica._format_virtual_hall_recommendations("919", gua_record, recommendations, candidates, lightweight=True, html=True)

        self.assertNotIn("无DPS可用", text)
        self.assertIn("推荐加入：全匹配：<code>@wa2000</code> <code>@healer</code>", text)
        self.assertNotIn("<code>.加入副本", text)
        self.assertIn("DPS：<code>@wa2000</code>", text)

    def test_ticket_query_keeps_non_kunwu_details_out_of_reply(self):
        thunder_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        state_module.set_replica_participant_identity_ids([thunder_id])
        state_module.set_replica_gold_dps_enabled(thunder_id, True)
        state_module.set_storage_bag_records({
            str(thunder_id): {"items": {"虚天残图": 1, "昆吾通行令": 1}, "sections": {"材料": {"虚天残图": 1, "昆吾通行令": 1}}},
        })

        text = app_replica._format_replica_ticket_query_reply(html=True)

        self.assertIn("<code>@wa2000</code>", text)
        self.assertIn("昆x1/可", text)
        self.assertNotIn("虚x1", text)
        self.assertNotIn("雷DPS", text)

    def test_virtual_hall_route_advice_uses_same_trigram_explicit_case(self):
        advice = app_replica._get_xutian_oracle_route_advice("震雷上巽风下 · 上爻游变")

        self.assertEqual("火路", advice["route"])
        self.assertEqual("势策", advice["strategy"])
        self.assertEqual("同卦系推断", advice["confidence"])

    def test_ticket_query_falls_back_to_all_identities_when_participants_empty(self):
        listener_id = self._register_replica_identity(301299112, "listener", root_attrs="金", professions="破军")
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        empty_id = self._register_replica_identity(991202, "empty", root_attrs="木", professions="灵医")
        state_module.set_replica_participant_identity_ids([])
        state_module.set_replica_group_ids([-100777])
        state_module.set_replica_listener_account_map({"-100777": listener_id})
        state_module.set_storage_bag_records({
            str(listener_id): {"items": {"昆吾通行令": 9}, "sections": {}},
            str(leader_id): {"items": {"昆吾通行令": 1, "苍坤残图": 1}, "sections": {}},
            str(empty_id): {"items": {}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertIn("@leader", reply)
        self.assertIn("昆x1/可", reply)
        self.assertNotIn("苍x1", reply)
        opener_section = reply.split("开房兜底命令", 1)[0]
        self.assertNotIn("@listener", opener_section)
        self.assertNotIn("@empty", opener_section)
        self.assertEqual({"@leader": leader_id, "@empty": empty_id}, app_replica._get_replica_identity_ids_by_username())

    def test_ticket_query_excludes_non_kunwu_ticket_only_opener(self):
        low_id = self._register_replica_identity(991201, "low", professions="破军", realm="筑基后期")
        ready_id = self._register_replica_identity(991202, "ready", professions="御山", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([low_id, ready_id])
        state_module.set_storage_bag_records({
            str(low_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(ready_id): {"items": {"昆吾通行令": 1, "苍坤残图": 1}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        opener_section = reply.split("开房兜底命令", 1)[0]
        self.assertNotIn("@low", opener_section)
        self.assertIn("@ready", opener_section)
        self.assertIn(".开启副本 @ready 昆", reply)
        self.assertNotIn(".开启副本 @ready 苍", reply)

    def test_lightweight_open_command_sends_open_with_selected_ticket(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 苍坤"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}}})

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, send_args, self._button_texts(buttons)

        handled, reply_text, send_args, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启苍坤洞府", send_args.args[0])
        self.assertFalse(send_args.kwargs["track"])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_args.kwargs["priority"])
        self.assertEqual("自动副本", send_args.kwargs["source_module"])
        self.assertEqual("keep", send_args.kwargs["delete_policy"])
        self.assertIn("replica_lightweight_open", send_args.kwargs["op_id"])
        self.assertIn("replica_lightweight_open:cangkun", send_args.kwargs["chain_id"])
        self.assertIn("已用 @leader 发送 .开启苍坤洞府", reply_text)
        self.assertIn("<code>.加入副本 @用户名 @用户名</code>", reply_text)
        self.assertIn("<code>.解散副本</code>", reply_text)
        self.assertIn(".加入副本 @用户名 @用户名", reply_text)
        self.assertIn(".解散副本", reply_text)
        self.assertIn("刷新副本", button_texts)
        self.assertIn("解散副本", button_texts)
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        self.assertEqual(1, len(pending))
        flow = next(iter(pending.values()))
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, flow["replica_kind"])
        self.assertEqual(501, flow["open_command_msg_id"])

    def test_lightweight_open_command_sends_kunwu_open_with_selected_ticket(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 昆"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"昆吾通行令": 1}, "sections": {}}})

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text, send_args

        handled, reply_text, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启昆吾山", send_args.args[0])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])
        self.assertIn("replica_lightweight_open:kunwu", send_args.kwargs["chain_id"])
        self.assertIn("已用 @leader 发送 .开启昆吾山", reply_text)
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        flow = next(iter(pending.values()))
        self.assertEqual(app_replica._REPLICA_KIND_KUNWU, flow["replica_kind"])
        self.assertEqual(501, flow["open_command_msg_id"])

    def test_lightweight_open_command_sends_luoyun_open_with_qualified_profile(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹后期", sect_name="落云宗")
        state_module.update_send_as_profile(leader_id, sect_contribution=420, sect_contribution_updated_at=1)
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 落云"

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text, send_args

        handled, reply_text, send_args = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启落云秘圃", send_args.args[0])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])
        self.assertIn("replica_lightweight_open:luoyun", send_args.kwargs["chain_id"])
        self.assertIn("已用 @leader 发送 .开启落云秘圃", reply_text)
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        flow = next(iter(pending.values()))
        self.assertEqual(app_replica._REPLICA_KIND_LUOYUN, flow["replica_kind"])
        self.assertEqual(501, flow["open_command_msg_id"])

    def test_luoyun_recommendation_requires_bottle_holder(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹后期", sect_name="落云宗")
        bottle_id = self._register_replica_identity(991202, "bottle")
        filler_id = self._register_replica_identity(991203, "filler")
        state_module.set_replica_participant_identity_ids([leader_id, bottle_id, filler_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {}, "sections": {}},
            str(bottle_id): {"items": {"掌天瓶": 1}, "sections": {}},
            str(filler_id): {"items": {}, "sections": {}},
        })

        command = app_replica._get_lightweight_profession_recommendation_join_command(
            app_replica._REPLICA_KIND_LUOYUN,
            leader_id,
        )
        text = app_replica._format_lightweight_profession_recommendation_section(
            app_replica._REPLICA_KIND_LUOYUN,
            leader_id,
        )

        self.assertIn("@bottle", command)
        self.assertIn("@bottle", text)
        self.assertIn("掌天瓶", text)

        state_module.set_storage_bag_records({
            str(leader_id): {"items": {}, "sections": {}},
            str(bottle_id): {"items": {}, "sections": {}},
            str(filler_id): {"items": {}, "sections": {}},
        })

        self.assertEqual(
            "",
            app_replica._get_lightweight_profession_recommendation_join_command(
                app_replica._REPLICA_KIND_LUOYUN,
                leader_id,
            ),
        )
        self.assertIn(
            "暂未找到本地储物袋缓存持有掌天瓶",
            app_replica._format_lightweight_profession_recommendation_section(
                app_replica._REPLICA_KIND_LUOYUN,
                leader_id,
            ),
        )

    def test_lightweight_open_command_blocks_luoyun_without_contribution(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹后期", sect_name="落云宗")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 落"

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_open_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text, send_mock.await_count

        handled, reply_text, send_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(0, send_count)
        self.assertIn("不能开启落云秘圃", reply_text)
        self.assertIn("贡献未知", reply_text)

    def test_lightweight_open_command_keeps_fresh_room_block(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 苍坤"
        now = 2000.0
        state_module.set_storage_bag_records({str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}}})
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now - 30,
            "updated_at": now - 30,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_open_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已有苍坤洞府房间 47", reply_text)

    def test_lightweight_open_command_allows_stale_opened_room_after_lobby_window(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 苍坤"
        now = 2000.0
        state_module.set_storage_bag_records({str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}}})
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now - app_replica._REPLICA_LOBBY_TTL_SEC - 1,
            "updated_at": now - app_replica._REPLICA_LOBBY_TTL_SEC - 1,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启苍坤洞府", send_args.args[0])
        self.assertIn("已用 @leader 发送 .开启苍坤洞府", reply_text)
        self.assertNotIn("已有苍坤洞府房间", reply_text)

    def test_lightweight_open_command_allows_stale_dissolve_pending_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 苍坤"
        now = 2000.0
        state_module.set_storage_bag_records({str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}}})
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_requested_at": now - app_replica._REPLICA_LIGHTWEIGHT_DISSOLVE_PENDING_SEC - 1,
            "dissolve_msg_id": 802,
            "updated_at": now - app_replica._REPLICA_LIGHTWEIGHT_DISSOLVE_PENDING_SEC - 1,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启苍坤洞府", send_args.args[0])
        self.assertIn("已用 @leader 发送 .开启苍坤洞府", reply_text)
        self.assertNotIn("已请求解散", reply_text)

    def test_lightweight_open_command_cangkun_room_does_not_block_virtual_hall(self):
        cangkun_leader_id = self._register_replica_identity(991201, "cangleader")
        virtual_leader_id = self._register_replica_identity(991202, "virtualleader")
        event = self._prepare_replica_group([cangkun_leader_id, virtual_leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @virtualleader 虚"
        now = 2000.0
        state_module.set_storage_bag_records({
            str(cangkun_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(virtual_leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "47",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": cangkun_leader_id,
            "leader_username": "@cangleader",
            "opened_at": now - 30,
            "updated_at": now - 30,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启虚天殿", send_args.args[0])
        self.assertIn("已用 @virtualleader 发送 .开启虚天殿", reply_text)
        self.assertNotIn("已有苍坤洞府房间", reply_text)

    def test_lightweight_open_command_rejects_ambiguous_multi_ticket_without_sending(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹初期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_open_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, self._button_texts(buttons)

        handled, reply_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("多种可开副本", reply_text)
        self.assertIn("请指定类型", reply_text)
        self.assertIn("避免默认误开虚天殿", reply_text)
        self.assertIn("<code>.开启副本 @leader 虚</code>", reply_text)
        self.assertIn("<code>.开启副本 @leader 苍</code>", reply_text)
        self.assertIn("开虚 @leader", button_texts)
        self.assertIn("开苍 @leader", button_texts)
        self.assertEqual({}, state_module.get_replica_run_state().get("lightweight_dungeon", {}).get("pending_open", {}))

    def test_lightweight_open_command_dedupes_repeated_ambiguous_notice(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹初期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))) as reply_mock, \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                first = await app_replica._handle_lightweight_open_command(event)
                event.id = 101
                second = await app_replica._handle_lightweight_open_command(event)
                send_mock.assert_not_awaited()
                return first, second, reply_mock.await_count

        first, second, reply_count = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, reply_count)

    def test_lightweight_open_command_allows_unambiguous_single_ticket_without_type(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹初期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启苍坤洞府", send_args.args[0])
        self.assertIn("已用 @leader 发送 .开启苍坤洞府", reply_text)

    def test_lightweight_open_command_accepts_short_kind_for_multi_ticket_opener(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹初期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 虚"
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 1}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启虚天殿", send_args.args[0])
        self.assertNotIn("多种可开副本", reply_text)
        self.assertIn("已用 @leader 发送 .开启虚天殿", reply_text)

    def test_lightweight_open_command_accepts_each_short_kind_alias(self):
        cases = [
            ("虚", "虚天残图", ".开启虚天殿", "虚天殿"),
            ("苍", "苍坤残图", ".开启苍坤洞府", "苍坤洞府"),
            ("坠", "坠魔谷禁制令", ".开启坠魔谷", "坠魔谷"),
            ("黄", "黄龙急援令", ".开启黄龙山", "黄龙山"),
        ]
        for index, (short_kind, ticket_item, expected_command, expected_name) in enumerate(cases, start=1):
            with self.subTest(short_kind=short_kind):
                state_module.set_replica_run_state({})
                leader_id = self._register_replica_identity(991200 + index, f"leader{index}", realm="结丹初期")
                event = self._prepare_replica_group([leader_id])
                event.sender_id = 4242
                event.id = 4300 + index
                event.raw_text = f".开启副本 @leader{index} {short_kind}"
                state_module.set_storage_bag_records({
                    str(leader_id): {
                        "items": {"虚天残图": 1, ticket_item: 1},
                        "sections": {},
                    },
                })

                async def run_test():
                    with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                            patch("model.app_replica._claim_runtime_event", return_value=True), \
                            patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                            patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                        handled = await app_replica._handle_lightweight_open_command(event)
                        send_args = app_replica.send_game_command.await_args
                        reply_text = app_replica._send_replica_group_message.await_args.args[2]
                        return handled, send_args, reply_text

                handled, send_args, reply_text = asyncio.run(run_test())
                self.assertTrue(handled)
                self.assertEqual(expected_command, send_args.args[0])
                self.assertIn(f"已用 @leader{index} 发送 {expected_command}", reply_text)
                self.assertNotIn("多种可开副本", reply_text)
                flow = next(iter(state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"].values()))
                self.assertEqual(expected_name, app_replica._REPLICA_KIND_META[flow["replica_kind"]]["name"])

    def test_lightweight_open_command_reports_global_pause_without_sending(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹初期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 虚"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"虚天残图": 1}, "sections": {}}})
        state_module.set_global_enabled(False)

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_open_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn(".开启虚天殿 未发送", reply_text)
        self.assertIn("全局暂停", reply_text)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])
        state_module.set_global_enabled(True)

    def test_lightweight_open_command_allows_retry_after_stale_pending_open(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 虚"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"虚天残图": 1}, "sections": {}}})
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "stale-flow",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "selector": "@leader",
            "replica_command_msg_id": 666,
            "open_command_msg_id": 501,
            "open_requested_at": 1000.0,
            "updated_at": 1000.0,
            "expires_at": 2000.0,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=2001.0), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=502))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启虚天殿", send_args.args[0])
        self.assertIn("已用 @leader 发送 .开启虚天殿", reply_text)
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        self.assertEqual(1, len(pending))
        flow = next(iter(pending.values()))
        self.assertNotEqual("stale-flow", flow["flow_id"])
        self.assertEqual(502, flow["open_command_msg_id"])

    def test_lightweight_open_command_blocks_immediate_duplicate_pending_open(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 虚"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"虚天残图": 1}, "sections": {}}})
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "fresh-flow",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "selector": "@leader",
            "replica_command_msg_id": 666,
            "open_command_msg_id": 501,
            "open_requested_at": 1000.0,
            "updated_at": 1000.0,
            "expires_at": 2000.0,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=1001.0), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=502))):
                handled = await app_replica._handle_lightweight_open_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已有虚天殿开房请求", reply_text)
        self.assertIn("如果确认开房指令被吞", reply_text)
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        self.assertEqual(["fresh-flow"], sorted(pending.keys()))

    def test_lightweight_open_command_cangkun_pending_does_not_block_virtual_hall(self):
        cangkun_leader_id = self._register_replica_identity(991201, "cangleader")
        virtual_leader_id = self._register_replica_identity(991202, "virtualleader")
        event = self._prepare_replica_group([cangkun_leader_id, virtual_leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @virtualleader 虚"
        state_module.set_storage_bag_records({
            str(cangkun_leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(virtual_leader_id): {"items": {"虚天残图": 1}, "sections": {}},
        })
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "cangkun-fresh-flow",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": cangkun_leader_id,
            "leader_username": "@cangleader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "selector": "@cangleader",
            "replica_command_msg_id": 666,
            "open_command_msg_id": 501,
            "open_requested_at": 1000.0,
            "updated_at": 1000.0,
            "expires_at": 2000.0,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=1001.0), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=502))):
                handled = await app_replica._handle_lightweight_open_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, send_args, reply_text

        handled, send_args, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".开启虚天殿", send_args.args[0])
        self.assertIn("已用 @virtualleader 发送 .开启虚天殿", reply_text)
        self.assertNotIn("已有苍坤洞府开房请求", reply_text)

    def test_lightweight_open_pending_timeout_stays_short(self):
        self.assertGreaterEqual(app_replica._REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC, 30)
        self.assertLessEqual(app_replica._REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC, 120)

    def test_lightweight_cleanup_drops_corrupt_persisted_state(self):
        state_module.set_replica_run_state({
            "lightweight_dungeon": {
                "pending_open": {
                    "bad-kind": {
                        "flow_id": "bad-kind",
                        "replica_chat_id": "-100777",
                        "leader_identity_id": "991201",
                        "replica_kind": "unknown",
                    },
                    "bad-identity": {
                        "flow_id": "bad-identity",
                        "replica_chat_id": "-100777",
                        "leader_identity_id": "0",
                        "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
                    },
                    "valid": {
                        "flow_id": "valid",
                        "replica_chat_id": "-100777",
                        "leader_identity_id": "991201",
                        "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
                        "expires_at": 9999999999,
                    },
                },
                "last_room_by_chat": {
                    "-100777": {
                        "room_id": "",
                        "replica_chat_id": "-100777",
                        "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
                    },
                    "-100888": {
                        "room_id": "16",
                        "replica_chat_id": "-100888",
                        "replica_kind": "unknown",
                    },
                    "-100999": {
                        "room_id": "17",
                        "replica_chat_id": "-100999",
                        "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
                        "expires_at": 9999999999,
                    },
                },
            }
        })

        cleaned = app_replica._cleanup_lightweight_dungeon_state(now=1000.0)

        self.assertEqual(["valid"], sorted(cleaned["pending_open"].keys()))
        self.assertEqual(-100777, cleaned["pending_open"]["valid"]["replica_chat_id"])
        self.assertEqual(991201, cleaned["pending_open"]["valid"]["leader_identity_id"])
        self.assertEqual(["-100999"], sorted(cleaned["last_room_by_chat"].keys()))
        self.assertEqual(-100999, cleaned["last_room_by_chat"]["-100999"]["replica_chat_id"])

    def test_lightweight_join_ignores_corrupt_persisted_room_without_crashing(self):
        first_id = self._register_replica_identity(991202, "first")
        event = self._prepare_replica_group([first_id])
        event.raw_text = ".加入副本 @first"
        state_module.set_replica_run_state({
            "lightweight_dungeon": {
                "last_room_by_chat": {
                    str(event.chat_id): {
                        "room_id": "16",
                        "replica_chat_id": event.chat_id,
                        "replica_kind": "unknown",
                        "expires_at": 9999999999,
                    }
                }
            }
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=804))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=703))):
                handled = await app_replica._handle_lightweight_join_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("没有已记录的副本房间", reply_text)

    def test_lightweight_open_cangkun_rejects_low_realm_with_reason(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="筑基后期")
        event = self._prepare_replica_group([leader_id])
        event.sender_id = 4242
        event.raw_text = ".开启副本 @leader 苍坤"
        state_module.set_storage_bag_records({str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}}})

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=501))):
                handled = await app_replica._handle_lightweight_open_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("不能开启苍坤洞府", reply_text)
        self.assertIn("苍坤要求结丹初期及以上，当前境界：筑基后期", reply_text)
        self.assertIn(".查询副本", reply_text)

    def test_opened_text_records_latest_room_for_lightweight_flow(self):
        leader_id = self._register_replica_identity(991201, "leader", sect_name="太一门")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        event = self._prepare_replica_group([leader_id])
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, curse_id])
        state_module.set_tianjige_dao_path_records({
            str(leader_id): {"spiritual_sense": 1200},
        })
        now = 1000.0
        flow = {
            "flow_id": "flow-1",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        opened = "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 16\n其他道友可使用 .加入苍坤洞府 16 加入队伍！"

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=601, chat_id=1),
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, self._button_texts(buttons), self._button_payload_by_text(buttons, "加入推荐")

        handled, button_texts, join_payload = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("加入推荐", button_texts)
        self.assertNotIn("进入苍坤洞府", button_texts)
        self.assertIn("解散副本", button_texts)
        self.assertIn("刷新副本", button_texts)
        self.assertEqual(".加入副本 @shield @healer @blade @curse", join_payload.get("command"))
        room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
        self.assertEqual("16", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, room["replica_kind"])

    def test_kunwu_opened_text_records_latest_room_for_lightweight_flow(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first", professions="御山")
        event = self._prepare_replica_group([leader_id, first_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-kunwu",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        opened = "【昆吾山·集结】\n@leader 持【昆吾通行令】开启入山门户。\n房间ID: 88\n其他道友可使用 .加入昆吾山 88 加入队伍！"

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=601, chat_id=1),
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, notice_text, self._button_texts(buttons)

        handled, notice_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已记录昆吾山房间 88", notice_text)
        self.assertIn("推荐配置：昆吾山", notice_text)
        self.assertIn("进入昆吾山", button_texts)
        self.assertIn("解散副本", button_texts)
        room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
        self.assertEqual("88", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_KUNWU, room["replica_kind"])

    def test_kunwu_plain_opened_text_clears_open_retry_flow(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first", professions="御山")
        event = self._prepare_replica_group([leader_id, first_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-kunwu-plain",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "open_command_msg_id": 501,
            "open_requested_at": now,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        opened = (
            "道友 @leader 准备开启昆吾山试炼。\n"
            "房间ID: 321\n"
            "其他道友可使用 .加入昆吾山 321 加入队伍！"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=601, chat_id=1),
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
            with patch("model.app_replica.time.time", return_value=now), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                retried = await app_replica._retry_lightweight_game_command_once(
                    "open",
                    leader_id,
                    app_replica._REPLICA_KIND_KUNWU,
                    "flow-kunwu-plain",
                    ".开启昆吾山",
                    event.chat_id,
                    event.id,
                    501,
                    delay_sec=0,
                )
                return handled, retried, send_mock.await_count

        handled, retried, send_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertFalse(retried)
        self.assertEqual(0, send_count)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])
        room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
        self.assertEqual("321", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_KUNWU, room["replica_kind"])

    def test_luoyun_opened_text_records_latest_room_for_lightweight_flow(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹后期", sect_name="落云宗")
        first_id = self._register_replica_identity(991202, "first")
        event = self._prepare_replica_group([leader_id, first_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {}, "sections": {}},
            str(first_id): {"items": {"掌天瓶": 1}, "sections": {}},
        })
        now = 1000.0
        flow = {
            "flow_id": "flow-luoyun",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_LUOYUN,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        opened = (
            "【落云秘圃·集结】\n"
            "@leader 预缴 420贡献，开启了落云宗后山秘圃的临时禁门。\n"
            "副本ID: 12\n"
            "其他道友可使用 .加入落云秘圃 12 加入队伍！(5人满)\n"
            "队长可在满员或达到最低人数后使用 .进入落云秘圃。"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=601, chat_id=1),
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                buttons = app_replica._send_lightweight_replica_notice.await_args.kwargs["buttons"]
                return handled, notice_text, self._button_texts(buttons)

        handled, notice_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已记录落云秘圃房间 12", notice_text)
        self.assertIn("轻量补位", notice_text)
        self.assertIn("@first", notice_text)
        self.assertIn("掌天瓶", notice_text)
        self.assertNotIn("进入落云秘圃", button_texts)
        self.assertIn("加入推荐", button_texts)
        self.assertIn("解散副本", button_texts)
        room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
        self.assertEqual("12", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_LUOYUN, room["replica_kind"])

    def test_luoyun_enter_blocks_without_bottle_holder(self):
        leader_id = self._register_replica_identity(991201, "leader", realm="结丹后期", sect_name="落云宗")
        first_id = self._register_replica_identity(991202, "first")
        event = self._prepare_replica_group([leader_id, first_id])
        event.raw_text = ".进入落云秘圃"
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "12",
            "replica_kind": app_replica._REPLICA_KIND_LUOYUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@first"],
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {}, "sections": {}},
            str(first_id): {"items": {}, "sections": {}},
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_enter_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, send_mock.await_count, self._button_texts(buttons)

        handled, reply_text, send_count, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(0, send_count)
        self.assertIn("缺掌天瓶", reply_text)
        self.assertNotIn("进入落云秘圃", button_texts)
        self.assertIn("解散副本", button_texts)

    def test_lightweight_enter_command_marks_cangkun_entered_once(self):
        leader_id = self._register_replica_identity(991201, "leader", sect_name="太一门")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        event = self._prepare_replica_group([leader_id, shield_id, healer_id, blade_id, curse_id])
        event.raw_text = ".进入苍坤洞府"
        now = 1000.0
        state_module.set_tianjige_dao_path_records({
            str(leader_id): {"spiritual_sense": 1200},
        })
        app_replica._mark_replica_team_joined_from_text(
            "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 16",
            now=now,
            msg_id=880,
        )
        app_replica._mark_replica_team_joined_from_text(
            "@curse 已加入苍坤上人洞府队伍！\n当前队伍 (5/5):\n - @leader\n - @shield\n - @healer\n - @blade\n - @curse",
            now=now + 1,
            msg_id=881,
        )
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@shield", "@healer", "@blade", "@curse"],
            "opened_at": now,
            "expires_at": 9999999999,
            "updated_at": now,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now + 2), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=901))):
                handled = await app_replica._handle_lightweight_enter_command(event)
                send_args = app_replica.send_game_command.await_args
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, send_args, reply_text, self._button_texts(buttons)

        handled, send_args, reply_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".进入苍坤洞府", send_args.args[0])
        self.assertEqual(leader_id, send_args.kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_args.kwargs["priority"])
        self.assertEqual("自动副本", send_args.kwargs["source_module"])
        self.assertIn("replica_lightweight_enter", send_args.kwargs["op_id"])
        self.assertEqual("replica_lightweight_room:cangkun:16", send_args.kwargs["chain_id"])
        self.assertIn("已按苍坤流程标记进入", reply_text)
        self.assertNotIn("进入苍坤洞府", button_texts)
        self.assertIn("解散副本", button_texts)
        saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now + 2)
        self.assertEqual("entered", saved_room["phase"])
        self.assertEqual(901, saved_room["enter_msg_id"])
        self.assertEqual(now + 2 + app_replica.REPLICA_ACTIVE_TTL_SEC, saved_room["expires_at"])
        records = state_module.get_replica_run_state()["by_identity"]
        for identity_id in (leader_id, shield_id, healer_id, blade_id, curse_id):
            state_item = records[str(identity_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
            self.assertTrue(state_item["participating"])
            self.assertEqual("entered", records[str(identity_id)]["last_join_result"])

        async def run_duplicate():
            with patch("model.app_replica.time.time", return_value=now + 3), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=701))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                duplicate_handled = await app_replica._handle_lightweight_enter_command(event)
                send_mock.assert_not_awaited()
                duplicate_text = app_replica._send_replica_group_message.await_args.args[2]
                return duplicate_handled, duplicate_text

        duplicate_handled, duplicate_text = asyncio.run(run_duplicate())
        self.assertTrue(duplicate_handled)
        self.assertIn("已确认进入", duplicate_text)

    def test_lightweight_enter_command_blocks_cangkun_confirmed_low_sense(self):
        leader_id = self._register_replica_identity(991201, "leader")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", sect_name="太一门")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        event = self._prepare_replica_group([leader_id, shield_id, healer_id, blade_id, curse_id])
        event.raw_text = ".进入苍坤洞府"
        now = 1000.0
        state_module.set_tianjige_dao_path_records({
            str(shield_id): {"spiritual_sense": 200},
        })
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "join_requested_usernames": ["@shield", "@healer", "@blade", "@curse"],
            "opened_at": now,
            "expires_at": 9999999999,
            "updated_at": now,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=now + 2), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock:
                handled = await app_replica._handle_lightweight_enter_command(event)
                send_mock.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, reply_text, self._button_texts(buttons)

        handled, reply_text, button_texts = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已确认未过千", reply_text)
        self.assertIn("未发送", reply_text)
        self.assertNotIn("进入苍坤洞府", button_texts)
        self.assertIn("解散副本", button_texts)

    def test_opened_text_recommendation_is_deduped_by_opened_message(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = time.time()
        flow = {
            "flow_id": "flow-dedupe",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        opened = "【苍坤上人洞府·集结】\n@leader 以【苍坤残图】锁定了太妙神禁的薄弱方位！\n房间ID: 16\n其他道友可使用 .加入苍坤洞府 16 加入队伍！"
        event_obj = SimpleNamespace(id=601, chat_id=1)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                first = await app_replica._handle_virtual_hall_auto_game_event(
                    event_obj,
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                state_item = app_replica._get_lightweight_dungeon_state()
                state_item["pending_open"]["flow-dedupe"] = dict(flow)
                app_replica._save_lightweight_dungeon_state(state_item)
                second = await app_replica._handle_virtual_hall_auto_game_event(
                    event_obj,
                    opened,
                    now + 1,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                return first, second, app_replica._send_lightweight_replica_notice.await_count

        first, second, notice_count = asyncio.run(run_test())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, notice_count)

    def test_unrelated_entered_text_does_not_expire_open_lightweight_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "1333",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
            "updated_at": now,
        })

        handled = asyncio.run(app_replica._handle_virtual_hall_auto_game_event(
            SimpleNamespace(id=901, chat_id=1),
            "队伍已进入虚天殿...石门缓缓关闭，前路凶险未知！",
            now + 30,
            reply_context={},
        ))

        self.assertFalse(handled)
        saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now + 120)
        self.assertEqual("1333", saved_room["room_id"])
        self.assertEqual("opened", saved_room["phase"])

    def test_entered_text_marks_only_pending_lightweight_enter(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "1333",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "enter_requested_at": now,
            "enter_msg_id": 901,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
            "updated_at": now,
        })

        handled = asyncio.run(app_replica._handle_virtual_hall_auto_game_event(
            SimpleNamespace(id=902, chat_id=1),
            "队伍已进入虚天殿...石门缓缓关闭，前路凶险未知！",
            now + 5,
            reply_context={},
        ))

        self.assertTrue(handled)
        saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now + 5)
        self.assertEqual("entered", saved_room["phase"])
        self.assertEqual(now + 5 + 60, saved_room["expires_at"])

    def test_virtual_hall_opened_without_dps_schedules_auto_dissolve(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="土", professions="御山")
        first_id = self._register_replica_identity(991202, "first", root_attrs="火", professions="咒师")
        event = self._prepare_replica_group([leader_id, first_id])
        now = 1000.0
        room = {
            "phase": "opened",
            "room_id": "914",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_msg_id": 601,
            "expires_at": now + 60,
            "updated_at": now,
        }
        opened = "【虚天殿已开启】\n队长 @leader 开启虚天殿，房间ID: 914\n【卦象词条】坎水上乾天下 · 四爻转阵\n阵骨：土1\n主锋：金1\n引灵：木1"

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))), \
                    patch("model.app_replica._schedule_lightweight_room_auto_dissolve", return_value=True) as schedule:
                handled = await app_replica._send_lightweight_virtual_hall_recommendation(room, opened, now)
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
                return handled, notice_text, saved_room, schedule.call_count

        handled, notice_text, saved_room, schedule_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(1, schedule_count)
        self.assertIn("无DPS可用", notice_text)
        self.assertIn("6 秒后自动解散", notice_text)
        self.assertIn("<code>.解散副本</code>", notice_text)
        self.assertNotIn("<code>.加入副本", notice_text)
        self.assertEqual("no_dps", saved_room["auto_dissolve_reason"])

    def test_lightweight_auto_dissolve_sends_room_dissolve_command(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        room = {
            "phase": "opened",
            "room_id": "914",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": 9999999999,
            "updated_at": 1000,
        }
        app_replica._set_lightweight_last_room(room)

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=802))):
                handled = await app_replica._run_lightweight_room_auto_dissolve(dict(room), 0)
                call_args = app_replica.send_game_command.await_args
                saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=time.time())
                return handled, call_args, saved_room

        handled, call_args, saved_room = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual(".解散副本", call_args.args[0])
        self.assertEqual(leader_id, call_args.kwargs["send_as_id"])
        self.assertEqual("自动副本", call_args.kwargs["source_module"])
        self.assertEqual("keep", call_args.kwargs["delete_policy"])
        self.assertIn("replica_lightweight_auto_dissolve", call_args.kwargs["op_id"])
        self.assertEqual("replica_lightweight_room:virtual_hall:914", call_args.kwargs["chain_id"])
        self.assertEqual("dissolve_requested", saved_room["phase"])

    def test_lightweight_manual_dissolve_does_not_resend_when_already_pending(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.raw_text = ".解散副本"
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "914",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_msg_id": 802,
            "expires_at": 9999999999,
            "updated_at": 1000,
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=803))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=804))) as send_mock:
                handled = await app_replica._handle_lightweight_dissolve_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text, send_mock.await_args_list

        handled, reply_text, send_calls = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertEqual([], send_calls)
        self.assertIn("已请求解散", reply_text)
        self.assertIn("未重复发送解散命令", reply_text)

    def test_lightweight_dissolve_confirmation_sends_realtime_notice(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "914",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": now + 60,
            "updated_at": now,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=805))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=901, chat_id=1),
                    "队长 @leader 已将副本房间（ID: 914）解散。\n因副本未曾开启，天道已将【虚天残图】归还至你的储物袋中。",
                    now,
                    reply_context={},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
                return handled, notice_text, saved_room

        handled, notice_text, saved_room = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已确认解散虚天殿房间 914", notice_text)
        self.assertIn("归还", notice_text)
        self.assertIn("<code>.查询副本</code>", notice_text)
        self.assertEqual("dissolved", saved_room["phase"])

    def test_lightweight_dissolve_already_closed_reply_marks_room_dissolved(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "58",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_requested_at": now,
            "dissolve_msg_id": 501,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=805))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=902, chat_id=1),
                    "你并非队长，或你开启的苍坤上人洞府房间已解散。",
                    now + 2,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=now + 2)
                return handled, notice_text, saved_room

        handled, notice_text, saved_room = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已确认解散苍坤洞府房间 58", notice_text)
        self.assertEqual("dissolved", saved_room["phase"])

    def test_log_group_replica_panel_ignores_stale_dissolve_pending_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 2000.0
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "58",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_requested_at": now - app_replica._REPLICA_LIGHTWEIGHT_DISSOLVE_PENDING_SEC - 1,
            "dissolve_msg_id": 501,
            "updated_at": now - app_replica._REPLICA_LIGHTWEIGHT_DISSOLVE_PENDING_SEC - 1,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        with patch("model.app_replica.time.time", return_value=now):
            panel_text = app_replica.format_log_group_replica_panel(".查询苍")
            buttons = app_replica._build_log_group_replica_panel_buttons(app_replica._REPLICA_KIND_CANGKUN, now=now)

        self.assertIn("房间：无", panel_text)
        self.assertNotIn("苍坤洞府 58", panel_text)
        self.assertEqual([], buttons)

    def test_open_failure_notice_includes_fallback_next_commands(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-failed",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=602, chat_id=1),
                    "你没有【苍坤残图】，无法开启苍坤上人洞府。",
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                return handled, notice_text

        handled, notice_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("开启苍坤洞府失败：缺少苍坤残图", notice_text)
        self.assertIn("兜底命令", notice_text)
        self.assertIn("<code>.查询副本</code>", notice_text)
        self.assertIn("<code>.开启副本 @leader 苍</code>", notice_text)
        self.assertIn(".查询副本", notice_text)
        self.assertIn(".开启副本 @leader 苍", notice_text)

    def test_kunwu_open_failure_notice_handles_missing_ticket(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-kunwu-failed",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=602, chat_id=1),
                    "你没有【昆吾通行令】，无法开启昆吾山。",
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                return handled, notice_text

        handled, notice_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("开启昆吾山失败：缺少昆吾通行令", notice_text)
        self.assertIn("<code>.查询副本</code>", notice_text)
        self.assertIn("<code>.开启副本 @leader 昆</code>", notice_text)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])

    def test_kunwu_open_failure_without_reply_context_clears_unique_flow(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-kunwu-failed",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_KUNWU,
            "open_command_msg_id": 501,
            "open_requested_at": now,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=602, chat_id=1),
                    "你没有【昆吾通行令】，无法开启登山道。",
                    now,
                    reply_context={},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
            with patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                retried = await app_replica._retry_lightweight_game_command_once(
                    "open",
                    leader_id,
                    app_replica._REPLICA_KIND_KUNWU,
                    "flow-kunwu-failed",
                    ".开启昆吾山",
                    event.chat_id,
                    event.id,
                    501,
                    delay_sec=0,
                )
                return handled, notice_text, retried, send_mock.await_count

        handled, notice_text, retried, send_count = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("开启昆吾山失败：缺少昆吾通行令", notice_text)
        self.assertFalse(retried)
        self.assertEqual(0, send_count)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])

    def test_cangkun_open_cooldown_failure_updates_identity_cooldown(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        now = 1000.0
        flow = {
            "flow_id": "flow-cooldown",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "open_command_msg_id": 501,
            "expires_at": now + 60,
            "updated_at": now,
        }
        app_replica._upsert_lightweight_open_flow(flow)
        text = (
            "你尚在苍坤上人洞府独立冷却中，无法立即开启新副本。\n"
            "剩余时间：2小时37分钟39秒\n"
            "冷却结束：2026-06-07 21:14:27 (Asia/Shanghai)"
        )

        async def run_test():
            with patch("model.app_replica._send_lightweight_replica_notice", new=AsyncMock(return_value=SimpleNamespace(id=700))):
                handled = await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=602, chat_id=1),
                    text,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )
                notice_text = app_replica._send_lightweight_replica_notice.await_args.args[1]
                return handled, notice_text

        handled, notice_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("开启苍坤洞府失败：开房冷却中", notice_text)
        self.assertIn("兜底命令", notice_text)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])
        status = app_replica._get_replica_identity_kind_status(
            leader_id,
            app_replica._REPLICA_KIND_CANGKUN,
            now,
        )
        self.assertNotEqual("可", status)
        self.assertTrue(status.startswith("2:"))

    def test_legacy_dispatch_replies_with_lightweight_commands_without_sending(self):
        first_id = self._register_replica_identity(991202, "first")
        second_id = self._register_replica_identity(991203, "second")
        event = self._prepare_replica_group([first_id, second_id])
        event.raw_text = ".苍坤洞府 123 @first @second"

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=802))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701))):
                handled = await app_replica._handle_replica_group_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("旧副本调度已关闭", reply_text)
        self.assertIn("<code>.查询副本</code>", reply_text)
        self.assertIn("<code>.加入副本 @first @second</code>", reply_text)
        self.assertIn("<code>.解散副本</code>", reply_text)
        self.assertIn(".查询副本", reply_text)
        self.assertIn(".加入副本 @first @second", reply_text)
        self.assertIn(".解散副本", reply_text)

        room = app_replica._get_lightweight_last_room(event.chat_id, now=1000.0)
        self.assertEqual("123", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, room["replica_kind"])
        self.assertEqual(0, room["leader_identity_id"])

    def test_external_dispatch_group_sends_join_and_pending_blocks_duplicate(self):
        first_id = self._register_replica_identity(991202, "first")
        listener_client = SimpleNamespace(name="dispatch-listener")
        state_module.set_replica_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9001})
        event = SimpleNamespace(
            raw_text=".苍坤洞府 123 @first",
            chat_id=-100888,
            sender_id=4444,
            id=88001,
            client=listener_client,
        )
        duplicate_event = SimpleNamespace(
            raw_text=".苍坤洞府 123 @first",
            chat_id=-100888,
            sender_id=4444,
            id=88002,
            client=listener_client,
        )

        async def run_test():
            with patch("model.app_message_log.get_all_clients", return_value={9001: listener_client}), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=777))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first_handled = await app_replica._handle_replica_external_dispatch_command(event)
                duplicate_handled = await app_replica._handle_replica_external_dispatch_command(duplicate_event)
                return first_handled, duplicate_handled, send_mock.await_args_list

        first_handled, duplicate_handled, send_calls = asyncio.run(run_test())

        self.assertTrue(first_handled)
        self.assertTrue(duplicate_handled)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".加入苍坤洞府 123", send_calls[0].args[0])
        self.assertFalse(send_calls[0].kwargs["track"])
        self.assertEqual(first_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_calls[0].kwargs["priority"])
        self.assertEqual("自动副本", send_calls[0].kwargs["source_module"])
        self.assertEqual("keep", send_calls[0].kwargs["delete_policy"])
        self.assertIn("replica_external_dispatch", send_calls[0].kwargs["op_id"])
        self.assertEqual("replica_external_dispatch:cangkun:123", send_calls[0].kwargs["chain_id"])

        state_item = state_module.get_replica_run_state()["by_identity"][str(first_id)]["replica_states"][app_replica._REPLICA_KIND_CANGKUN]
        self.assertEqual("123", state_item["dispatch_pending_room_id"])
        self.assertEqual(777, state_item["dispatch_pending_msg_id"])

    def test_external_dispatch_send_failure_clears_pending(self):
        first_id = self._register_replica_identity(991202, "first")
        listener_client = SimpleNamespace(name="dispatch-listener")
        state_module.set_replica_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9001})
        event = SimpleNamespace(
            raw_text=".虚天殿 456 @first",
            chat_id=-100888,
            sender_id=4444,
            id=88003,
            client=listener_client,
        )

        async def run_test():
            with patch("model.app_message_log.get_all_clients", return_value={9001: listener_client}), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=None)), \
                    patch("model.app_replica.console_log") as log_mock:
                handled = await app_replica._handle_replica_external_dispatch_command(event)
                return handled, log_mock.call_args_list

        handled, log_calls = asyncio.run(run_test())

        self.assertTrue(handled)
        state_item = state_module.get_replica_run_state()["by_identity"][str(first_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertNotIn("dispatch_pending_room_id", state_item)
        self.assertNotIn("dispatch_pending_until", state_item)
        self.assertTrue(any("主线拉人发送失败" in str(call.args[0]) for call in log_calls))

    def test_external_dispatch_unknown_user_logs_skip_not_send_failure(self):
        listener_client = SimpleNamespace(name="dispatch-listener")
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9001})
        event = SimpleNamespace(
            raw_text=".虚天殿 456 @missing",
            chat_id=-100888,
            sender_id=4444,
            id=88004,
            client=listener_client,
        )

        async def run_test():
            with patch("model.app_message_log.get_all_clients", return_value={9001: listener_client}), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.console_log") as log_mock:
                handled = await app_replica._handle_replica_external_dispatch_command(event)
                return handled, send_mock.await_args_list, log_mock.call_args_list

        handled, send_calls, log_calls = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual([], send_calls)
        self.assertTrue(any("主线拉人已跳过" in str(call.args[0]) for call in log_calls))
        self.assertFalse(any("主线拉人发送失败" in str(call.args[0]) for call in log_calls))

    def test_external_dispatch_fast_retry_resends_once_while_pending(self):
        first_id = self._register_replica_identity(991205, "first")
        event = SimpleNamespace(chat_id=-100888, id=88006)
        now = time.time()
        app_replica._reserve_external_dispatch_join(first_id, app_replica._REPLICA_KIND_VIRTUAL_HALL, "456", event, now)
        app_replica._mark_external_dispatch_join_sent(first_id, app_replica._REPLICA_KIND_VIRTUAL_HALL, "456", 778, now)

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779, sent_at=now + 3))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first = await app_replica._retry_external_dispatch_join_once(
                    first_id,
                    app_replica._REPLICA_KIND_VIRTUAL_HALL,
                    "456",
                    ".加入副本 456",
                    88006,
                    778,
                    delay_sec=0,
                )
                second = await app_replica._retry_external_dispatch_join_once(
                    first_id,
                    app_replica._REPLICA_KIND_VIRTUAL_HALL,
                    "456",
                    ".加入副本 456",
                    88006,
                    779,
                    delay_sec=0,
                )
                return first, second, send_mock.await_args

        first, second, send_args = asyncio.run(run_test())

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(".加入副本 456", send_args.args[0])
        self.assertFalse(send_args.kwargs["track"])
        self.assertEqual(first_id, send_args.kwargs["send_as_id"])
        self.assertEqual("urgent_reactive", send_args.kwargs["priority"])
        self.assertEqual("自动副本", send_args.kwargs["source_module"])
        state_item = state_module.get_replica_run_state()["by_identity"][str(first_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertEqual(1, state_item["dispatch_retry_count"])
        self.assertEqual(779, state_item["dispatch_pending_msg_id"])

    def test_lightweight_retry_delay_uses_collision_window_only_for_recent_settlement(self):
        now = 1000.0

        self.assertEqual(12.0, app_replica._get_lightweight_retry_delay_sec("open", now=now))
        self.assertEqual(8.0, app_replica._get_lightweight_retry_delay_sec("join", now=now))

        app_replica._note_replica_settlement_observed(now)

        self.assertEqual(2.0, app_replica._get_lightweight_retry_delay_sec("open", now=now + 3))
        self.assertEqual(12.0, app_replica._get_lightweight_retry_delay_sec("open", now=now + 9))

    def test_lightweight_open_fast_retry_resends_once_while_unconfirmed(self):
        leader_id = self._register_replica_identity(991201, "leader")
        now = time.time()
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "open-flow",
            "phase": "opening",
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "selector": "@leader",
            "replica_command_msg_id": 88006,
            "open_command_msg_id": 778,
            "open_requested_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC,
        })

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first = await app_replica._retry_lightweight_game_command_once(
                    "open",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "open-flow",
                    ".开启苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                second = await app_replica._retry_lightweight_game_command_once(
                    "open",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "open-flow",
                    ".开启苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return first, second, send_mock.await_args_list

        first, second, send_calls = asyncio.run(run_test())

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".开启苍坤洞府", send_calls[0].args[0])
        self.assertEqual(leader_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("retry", send_calls[0].kwargs["priority"])
        self.assertEqual("自动副本", send_calls[0].kwargs["source_module"])
        self.assertEqual("replica_lightweight_open:cangkun:open-flow", send_calls[0].kwargs["chain_id"])
        flow = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]["open-flow"]
        self.assertEqual(779, flow["open_retry_msg_id"])
        self.assertEqual(779, flow["open_command_msg_id"])

    def test_lightweight_open_fast_retry_skips_after_opened_room_is_recorded(self):
        leader_id = self._register_replica_identity(991201, "leader")
        now = time.time()
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "open-flow",
            "phase": "opening",
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "selector": "@leader",
            "replica_command_msg_id": 88006,
            "open_command_msg_id": 778,
            "open_requested_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_OPEN_TIMEOUT_SEC,
        })
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                handled = await app_replica._retry_lightweight_game_command_once(
                    "open",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "open-flow",
                    ".开启苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return handled, send_mock.await_count

        handled, send_count = asyncio.run(run_test())

        self.assertFalse(handled)
        self.assertEqual(0, send_count)

    def test_lightweight_join_fast_retry_resends_once_while_unconfirmed(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first")
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first = await app_replica._retry_lightweight_game_command_once(
                    "join",
                    first_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".加入苍坤洞府 16",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                second = await app_replica._retry_lightweight_game_command_once(
                    "join",
                    first_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".加入苍坤洞府 16",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return first, second, send_mock.await_args_list

        first, second, send_calls = asyncio.run(run_test())

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".加入苍坤洞府 16", send_calls[0].args[0])
        self.assertEqual(first_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("retry", send_calls[0].kwargs["priority"])
        self.assertEqual("自动副本", send_calls[0].kwargs["source_module"])
        self.assertEqual("replica_lightweight_room:cangkun:16", send_calls[0].kwargs["chain_id"])

    def test_lightweight_join_fast_retry_skips_after_join_success(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first")
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        app_replica._mark_replica_join_success(
            first_id,
            "16",
            ["@leader", "@first"],
            now + 1,
            msg_id=780,
            replica_kind=app_replica._REPLICA_KIND_CANGKUN,
        )

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                handled = await app_replica._retry_lightweight_game_command_once(
                    "join",
                    first_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".加入苍坤洞府 16",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return handled, send_mock.await_count

        handled, send_count = asyncio.run(run_test())

        self.assertFalse(handled)
        self.assertEqual(0, send_count)

    def test_lightweight_enter_fast_retry_resends_once_while_unconfirmed(self):
        leader_id = self._register_replica_identity(991201, "leader")
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "88",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "enter_requested_at": now,
            "enter_msg_id": 778,
            "opened_at": now,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first = await app_replica._retry_lightweight_game_command_once(
                    "enter",
                    leader_id,
                    app_replica._REPLICA_KIND_VIRTUAL_HALL,
                    "88",
                    ".进入虚天殿",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                second = await app_replica._retry_lightweight_game_command_once(
                    "enter",
                    leader_id,
                    app_replica._REPLICA_KIND_VIRTUAL_HALL,
                    "88",
                    ".进入虚天殿",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return first, second, send_mock.await_args_list

        first, second, send_calls = asyncio.run(run_test())

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".进入虚天殿", send_calls[0].args[0])
        saved_room = app_replica._get_lightweight_last_room(-100777, now=now + 1)
        self.assertEqual(779, saved_room["enter_retry_msg_id"])

    def test_lightweight_dissolve_fast_retry_resends_once_while_pending(self):
        leader_id = self._register_replica_identity(991201, "leader")
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_requested_at": now,
            "dissolve_msg_id": 778,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=779))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first = await app_replica._retry_lightweight_game_command_once(
                    "dissolve",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".解散苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                second = await app_replica._retry_lightweight_game_command_once(
                    "dissolve",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".解散苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return first, second, send_mock.await_args_list

        first, second, send_calls = asyncio.run(run_test())

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(send_calls))
        self.assertEqual(".解散苍坤洞府", send_calls[0].args[0])
        self.assertEqual(leader_id, send_calls[0].kwargs["send_as_id"])
        self.assertEqual("replica_lightweight_room:cangkun:16", send_calls[0].kwargs["chain_id"])
        saved_room = app_replica._get_lightweight_last_room(-100777, now=now + 1)
        self.assertEqual(779, saved_room["dissolve_retry_msg_id"])
        self.assertEqual(779, saved_room["dissolve_msg_id"])

    def test_lightweight_dissolve_fast_retry_skips_after_confirmation(self):
        leader_id = self._register_replica_identity(991201, "leader")
        now = time.time()
        app_replica._set_lightweight_last_room({
            "phase": "dissolve_requested",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": -100777,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "dissolve_requested_at": now,
            "dissolve_msg_id": 778,
            "updated_at": now,
            "expires_at": now + app_replica._REPLICA_LIGHTWEIGHT_ROOM_TTL_SEC,
        })
        app_replica._mark_lightweight_room_dissolved(
            "16",
            leader_username="@leader",
            replica_kind=app_replica._REPLICA_KIND_CANGKUN,
            now=now + 1,
        )

        async def run_test():
            with patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                handled = await app_replica._retry_lightweight_game_command_once(
                    "dissolve",
                    leader_id,
                    app_replica._REPLICA_KIND_CANGKUN,
                    "16",
                    ".解散苍坤洞府",
                    -100777,
                    88006,
                    778,
                    delay_sec=0,
                )
                return handled, send_mock.await_count

        handled, send_count = asyncio.run(run_test())

        self.assertFalse(handled)
        self.assertEqual(0, send_count)

    def test_external_dispatch_full_reply_after_success_does_not_clear_joined_lobby_state(self):
        first_id = self._register_replica_identity(991205, "first")
        listener_client = SimpleNamespace(name="dispatch-listener")
        state_module.set_replica_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9001})
        now = time.time()

        async def run_test():
            with patch("model.app_message_log.get_all_clients", return_value={9001: listener_client}), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=778, sent_at=now))) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                first_event = SimpleNamespace(raw_text=".虚天殿 456 @first", chat_id=-100888, sender_id=4444, id=88006, client=listener_client)
                later_event = SimpleNamespace(raw_text=".虚天殿 456 @first", chat_id=-100888, sender_id=4444, id=88007, client=listener_client)
                first_handled = await app_replica._handle_replica_external_dispatch_command(first_event)
                app_replica._mark_replica_join_success(first_id, "456", ["@leader", "@other", "@first"], now + 1, msg_id=779)
                app_replica._mark_replica_join_not_joined(first_id, "456", "full", now + 2, msg_id=780)
                later_handled = await app_replica._handle_replica_external_dispatch_command(later_event)
                return first_handled, later_handled, send_mock.await_args_list

        first_handled, later_handled, send_calls = asyncio.run(run_test())

        self.assertTrue(first_handled)
        self.assertTrue(later_handled)
        self.assertEqual(1, len(send_calls))
        state_item = state_module.get_replica_run_state()["by_identity"][str(first_id)]["replica_states"][app_replica._REPLICA_KIND_VIRTUAL_HALL]
        self.assertFalse(state_item["participating"])
        self.assertEqual("456", state_item["room_id"])
        self.assertNotIn("dispatch_pending_room_id", state_item)
        self.assertNotIn("dispatch_pending_msg_id", state_item)
        self.assertEqual(779, state_item["last_join_msg_id"])
        self.assertEqual("joined", state_item["lobby_status"])
        self.assertGreater(state_item["lobby_until"], now)
        record = state_module.get_replica_run_state()["by_identity"][str(first_id)]
        self.assertEqual("joined", record["last_join_result"])
        self.assertEqual(779, record["last_join_msg_id"])

    def test_external_dispatch_joined_lobby_blocks_duplicate_dispatch(self):
        first_id = self._register_replica_identity(991205, "first")
        listener_client = SimpleNamespace(name="dispatch-listener")
        state_module.set_replica_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_participant_identity_ids([first_id])
        state_module.set_replica_dispatch_group_ids([-100888])
        state_module.set_replica_dispatch_listener_account_map({"-100888": 9001})
        now = time.time()
        app_replica._mark_replica_join_success(first_id, "456", ["@leader", "@first"], now, msg_id=779)

        async def run_test():
            with patch("model.app_message_log.get_all_clients", return_value={9001: listener_client}), \
                    patch("model.app_replica.send_game_command", new=AsyncMock()) as send_mock, \
                    patch("model.app_replica.send_audit_log", new=AsyncMock()):
                event = SimpleNamespace(raw_text=".虚天殿 456 @first", chat_id=-100888, sender_id=4444, id=88007, client=listener_client)
                handled = await app_replica._handle_replica_external_dispatch_command(event)
                return handled, send_mock.await_count

        handled, send_count = asyncio.run(run_test())

        self.assertTrue(handled)
        self.assertEqual(0, send_count)

    def test_lightweight_dissolve_refuses_room_without_leader_identity(self):
        event = self._prepare_replica_group([])
        event.raw_text = ".解散副本"
        app_replica._set_lightweight_last_room({
            "phase": "legacy_dispatch_seen",
            "room_id": "123",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": 0,
            "leader_username": "",
            "expires_at": 9999999999,
            "updated_at": 1000,
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=803))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                handled = await app_replica._handle_lightweight_dissolve_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("缺少开房身份", reply_text)
        self.assertIn(".查询副本", reply_text)

    def test_lightweight_dissolve_cancels_pending_open_without_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.raw_text = ".解散副本"
        app_replica._upsert_lightweight_open_flow({
            "flow_id": "pending-open",
            "phase": "opening",
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "replica_kind": app_replica._REPLICA_KIND_VIRTUAL_HALL,
            "selector": "@leader",
            "replica_command_msg_id": 666,
            "open_command_msg_id": 501,
            "open_requested_at": 1000.0,
            "updated_at": 1000.0,
            "expires_at": 2000.0,
        })

        async def run_test():
            with patch("model.app_replica.time.time", return_value=1001.0), \
                    patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=803))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                handled = await app_replica._handle_lightweight_dissolve_command(event)
                app_replica.send_game_command.assert_not_awaited()
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("已取消等待中的虚天殿开房请求", reply_text)
        self.assertIn("未发送解散命令", reply_text)
        self.assertEqual({}, state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"])

    def test_lightweight_dissolve_records_button_source_metadata(self):
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
        event.raw_text = ".解散副本"
        event.id = 7788
        event._replica_button_message_id = 7788
        event._replica_button_actor_id = 123456
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": 9999999999,
            "updated_at": 1000,
        })

        async def run_test():
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=801))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                handled = await app_replica._handle_lightweight_dissolve_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                saved_room = app_replica._get_lightweight_last_room(event.chat_id, now=time.time())
                return handled, reply_text, saved_room

        handled, reply_text, saved_room = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("按钮消息 7788", reply_text)
        self.assertEqual("button", saved_room["dissolve_source"])
        self.assertEqual(7788, saved_room["dissolve_source_msg_id"])
        self.assertEqual(123456, saved_room["dissolve_actor_id"])

    def test_lightweight_join_and_dissolve_use_latest_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        shield_id = self._register_replica_identity(991202, "shield", professions="御山", sect_name="太一门")
        healer_id = self._register_replica_identity(991203, "healer", professions="灵医")
        blade_id = self._register_replica_identity(991204, "blade", professions="影刃")
        curse_id = self._register_replica_identity(991205, "curse", professions="咒师")
        event = self._prepare_replica_group([leader_id, shield_id, healer_id, blade_id, curse_id])
        event.sender_id = 4242
        state_module.set_tianjige_dao_path_records({
            str(shield_id): {"spiritual_sense": 1200},
        })
        app_replica._set_lightweight_last_room({
            "phase": "opened",
            "room_id": "16",
            "replica_kind": app_replica._REPLICA_KIND_CANGKUN,
            "replica_chat_id": event.chat_id,
            "listener_account_id": 9001,
            "leader_identity_id": leader_id,
            "leader_username": "@leader",
            "expires_at": 9999999999,
            "updated_at": 1000,
        })

        async def run_join():
            event.raw_text = ".加入副本 @shield @healer @blade @curse"
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=800))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701))):
                handled = await app_replica._handle_lightweight_join_command(event)
                calls = app_replica.send_game_command.await_args_list
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                buttons = app_replica._send_replica_group_message.await_args.kwargs["buttons"]
                return handled, calls, reply_text, self._button_texts(buttons)

        handled, calls, join_reply, button_texts = asyncio.run(run_join())
        self.assertTrue(handled)
        self.assertEqual(4, len(calls))
        self.assertEqual(".加入苍坤洞府 16", calls[0].args[0])
        self.assertEqual(shield_id, calls[0].kwargs["send_as_id"])
        self.assertEqual(healer_id, calls[1].kwargs["send_as_id"])
        self.assertEqual(blade_id, calls[2].kwargs["send_as_id"])
        self.assertEqual(curse_id, calls[3].kwargs["send_as_id"])
        self.assertEqual("自动副本", calls[0].kwargs["source_module"])
        self.assertEqual("keep", calls[0].kwargs["delete_policy"])
        self.assertIn("replica_lightweight_join", calls[0].kwargs["op_id"])
        self.assertEqual("replica_lightweight_room:cangkun:16", calls[0].kwargs["chain_id"])
        self.assertIn("已发送加入苍坤洞府 16", join_reply)
        self.assertIn("<code>.解散副本</code>", join_reply)
        self.assertIn(".解散副本", join_reply)
        self.assertIn("进入苍坤洞府", button_texts)
        self.assertIn("解散副本", button_texts)
        self.assertIn("刷新副本", button_texts)

        low_id = self._register_replica_identity(991206, "low", realm="筑基后期")
        state_module.set_replica_participant_identity_ids([leader_id, shield_id, healer_id, blade_id, curse_id, low_id])

        async def run_low_join():
            event.raw_text = ".加入副本 @shield @low"
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=804))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=703))):
                handled = await app_replica._handle_lightweight_join_command(event)
                calls = app_replica.send_game_command.await_args_list
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, calls, reply_text

        handled, calls, low_join_reply = asyncio.run(run_low_join())
        self.assertTrue(handled)
        self.assertEqual(1, len(calls))
        self.assertEqual(shield_id, calls[0].kwargs["send_as_id"])
        self.assertIn("@low(苍坤要求结丹初期及以上，当前境界：筑基后期)", low_join_reply)

        async def run_dissolve():
            event.raw_text = ".解散副本"
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=801))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=702))):
                handled = await app_replica._handle_lightweight_dissolve_command(event)
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, app_replica.send_game_command.await_args, reply_text

        handled, call_args, dissolve_reply = asyncio.run(run_dissolve())
        self.assertTrue(handled)
        self.assertEqual(".解散苍坤洞府", call_args.args[0])
        self.assertEqual(leader_id, call_args.kwargs["send_as_id"])
        self.assertEqual("自动副本", call_args.kwargs["source_module"])
        self.assertEqual("keep", call_args.kwargs["delete_policy"])
        self.assertIn("replica_lightweight_dissolve", call_args.kwargs["op_id"])
        self.assertEqual("replica_lightweight_room:cangkun:16", call_args.kwargs["chain_id"])
        self.assertIn("已用 @leader 发送 .解散苍坤洞府", dissolve_reply)
        self.assertIn("<code>.查询副本</code>", dissolve_reply)
        self.assertIn(".查询副本", dissolve_reply)


if __name__ == "__main__":
    unittest.main()
