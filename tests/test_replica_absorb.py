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
        app_runtime._runtime_event_claims.clear()

    def tearDown(self):
        app_runtime._runtime_event_claims.clear()
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

    def test_replica_group_branch_processes_game_text_before_commands(self):
        source_text = (PROJECT_ROOT / "model" / "app.py").read_text(encoding="utf-8")
        branch_pos = source_text.index('if _append_replica_group_message_log(event, event_type="message"):')
        auto_pos = source_text.index("_handle_virtual_hall_auto_game_event", branch_pos)
        progress_pos = source_text.index("_handle_replica_progress_event", branch_pos)
        command_pos = source_text.index("_handle_replica_group_command", branch_pos)

        self.assertLess(auto_pos, command_pos)
        self.assertLess(progress_pos, command_pos)

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
        now = 1000.0

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
        self.assertTrue(state_item["participating"])
        self.assertEqual("123", state_item["room_id"])
        self.assertEqual(["@leader"], state_item["team_usernames"])

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
            self.assertTrue(state_item["participating"])
            self.assertEqual(["@zhengyuan0213", "@walterwa2000", "@boxboxji"], state_item["team_usernames"])

    def test_runtime_resolves_cangkun_join_reply_family(self):
        self.assertEqual("replica_join", runtime.resolve_reply_family(".加入苍坤洞府 123"))

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

    def test_ticket_text_deltas_follow_real_gift_transfer_text(self):
        source_id = self._register_replica_identity(991201, "source")
        target_id = self._register_replica_identity(991202, "target")
        state_module.set_storage_bag_records({
            str(source_id): {"items": {"苍坤残图": 3}, "sections": {"法宝/丹药/杂物": {"苍坤残图": 3}}},
            str(target_id): {"items": {}, "sections": {}},
        })

        text = "【赠送成功】\n道友 @source 向 @target 赠送了 【苍坤残图】x2。\n并额外支付了 20 灵石作为因果税 (基础税率 10%)。"
        changed = app_replica.apply_replica_ticket_text_deltas(SimpleNamespace(id=25, chat_id=1), text, 1004.0)

        self.assertTrue(changed)
        records = state_module.get_storage_bag_records()
        self.assertEqual(1, records[str(source_id)]["items"]["苍坤残图"])
        self.assertEqual(2, records[str(target_id)]["items"]["苍坤残图"])

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
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 2}, "sections": {}},
            str(other_id): {"items": {}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertIn("@leader", reply)
        self.assertIn("虚x1", reply)
        self.assertIn("苍x2", reply)
        self.assertIn("虚:可", reply)
        self.assertIn("苍:可", reply)
        self.assertIn("可复制命令", reply)
        self.assertIn("可复制开房命令（按副本）", reply)
        self.assertIn("虚天殿：", reply)
        self.assertIn("苍坤洞府：", reply)
        self.assertIn(".开启副本 @leader 虚", reply)
        self.assertIn(".开启副本 @leader 苍", reply)
        self.assertIn(".加入副本 @用户名 @用户名", reply)
        self.assertIn(".解散副本", reply)
        self.assertNotIn("推荐配置：苍坤洞府", reply)
        opener_section = reply.split("可复制命令：", 1)[0]
        self.assertNotIn("@empty", opener_section)

        html_reply = app_replica._format_replica_ticket_query_reply(html=True)
        self.assertFalse(html_reply.startswith("<code>可开副本"))
        self.assertIn("<code>.开启副本 @leader 虚</code>", html_reply)
        self.assertIn("<code>.开启副本 @leader 苍</code>", html_reply)
        self.assertIn("<code>.加入副本 @用户名 @用户名</code>", html_reply)
        self.assertIn("<code>.解散副本</code>", html_reply)

    def test_ticket_query_shows_cd_and_hides_cd_open_command(self):
        leader_id = self._register_replica_identity(991201, "leader", root_attrs="金火", professions="破军")
        state_module.set_replica_participant_identity_ids([leader_id])
        state_module.set_storage_bag_records({
            str(leader_id): {"items": {"虚天残图": 1, "苍坤残图": 1}, "sections": {}},
        })
        app_replica._mark_replica_success_cooldown(
            [leader_id],
            time.time(),
            replica_kind=app_replica._REPLICA_KIND_VIRTUAL_HALL,
        )

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertRegex(reply, r"虚:\d+:\d{2}")
        self.assertIn("苍:可", reply)
        self.assertNotIn(".开启副本 @leader 虚", reply)
        self.assertIn(".开启副本 @leader 苍", reply)

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

        self.assertIn(".加入副本 @shield @healer @blade @curse", section)
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

        self.assertIn(".加入副本 @shield @healer @blade", section)
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

        self.assertIn(".加入副本 @attacker @healer @bladecurse", section)
        self.assertIn("缺职业：", section)
        self.assertNotIn("五职业已齐", section)

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

        self.assertIn(".加入副本 @shield @healer @blade @zz_heaven_curse", section)
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

        self.assertIn(".加入副本 @zz_taiyi_shield @healer @blade @curse", section)
        self.assertNotIn("@aa_normal_shield", section)

    def test_virtual_hall_recommendation_includes_copyable_route_advice(self):
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
        self.assertIn(".选择道路 冰", text)
        self.assertIn(".阵策 稳", text)
        self.assertIn(".争鼎 夺鼎", text)
        self.assertIn(".后殿抉择 冲关", text)
        self.assertIn(".争鼎 求稳", text)
        self.assertIn(".后殿抉择 收手", text)
        self.assertNotIn("脚本不会自动发送", text)

        html_text = app_replica._format_virtual_hall_recommendations("777", gua_record, recommendations, candidates, lightweight=True, html=True)
        self.assertIn("<code>.选择道路 冰</code>", html_text)
        self.assertIn("<code>.阵策 稳</code>", html_text)
        self.assertIn("<code>.争鼎 夺鼎</code>", html_text)
        self.assertIn("<code>.后殿抉择 冲关</code>", html_text)

    def test_virtual_hall_no_dps_suppresses_join_recommendations(self):
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

        self.assertIn("无DPS可用", text)
        self.assertIn("6 秒后自动解散", text)
        self.assertNotIn("全匹配", text)
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

        self.assertIn("无DPS可用", text)
        self.assertIn("存在金/雷候选，但未勾选金/雷 DPS", text)
        self.assertIn("6 秒后自动解散", text)
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
        self.assertIn("<code>.加入副本 @wa2000 @healer</code>", text)

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
        self.assertIn("<code>.加入副本", text)
        command_line = next(line for line in text.splitlines() if ".加入副本" in line and "推荐加入" in line)
        self.assertIn("@walterwa2000", command_line)
        self.assertNotIn("@myios7", command_line)

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
        event.raw_text = ".加入副本 " + command_line.rsplit(".加入副本 ", 1)[1].split("</code>", 1)[0]

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
        self.assertIn("<code>.加入副本 @wa2000 @healer</code>", text)
        self.assertIn("DPS：<code>@wa2000</code>", text)

    def test_ticket_query_displays_marked_thunder_dps(self):
        thunder_id = self._register_replica_identity(991202, "wa2000", root_attrs="雷", professions="破军")
        state_module.set_replica_participant_identity_ids([thunder_id])
        state_module.set_replica_gold_dps_enabled(thunder_id, True)
        state_module.set_storage_bag_records({
            str(thunder_id): {"items": {"虚天残图": 1}, "sections": {"材料": {"虚天残图": 1}}},
        })

        text = app_replica._format_replica_ticket_query_reply(html=True)

        self.assertIn("<code>@wa2000</code>", text)
        self.assertIn("雷DPS", text)

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
            str(listener_id): {"items": {"虚天残图": 9}, "sections": {}},
            str(leader_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(empty_id): {"items": {}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        self.assertIn("@leader", reply)
        self.assertIn("苍x1", reply)
        opener_section = reply.split("可复制命令：", 1)[0]
        self.assertNotIn("@listener", opener_section)
        self.assertNotIn("@empty", opener_section)
        self.assertEqual({"@leader": leader_id, "@empty": empty_id}, app_replica._get_replica_identity_ids_by_username())

    def test_ticket_query_excludes_low_realm_cangkun_only_opener(self):
        low_id = self._register_replica_identity(991201, "low", professions="破军", realm="筑基后期")
        ready_id = self._register_replica_identity(991202, "ready", professions="御山", realm="结丹初期")
        state_module.set_replica_participant_identity_ids([low_id, ready_id])
        state_module.set_storage_bag_records({
            str(low_id): {"items": {"苍坤残图": 1}, "sections": {}},
            str(ready_id): {"items": {"苍坤残图": 1}, "sections": {}},
        })

        reply = app_replica._format_replica_ticket_query_reply()

        opener_section = reply.split("可复制命令：", 1)[0]
        self.assertNotIn("@low", opener_section)
        self.assertIn("@ready", opener_section)
        self.assertIn(".开启副本 @ready 苍", reply)

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
                return handled, reply_text, send_args

        handled, reply_text, send_args = asyncio.run(run_test())
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
        pending = state_module.get_replica_run_state()["lightweight_dungeon"]["pending_open"]
        self.assertEqual(1, len(pending))
        flow = next(iter(pending.values()))
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, flow["replica_kind"])
        self.assertEqual(501, flow["open_command_msg_id"])

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
                return handled, reply_text

        handled, reply_text = asyncio.run(run_test())
        self.assertTrue(handled)
        self.assertIn("多种可开副本", reply_text)
        self.assertIn("请指定类型", reply_text)
        self.assertIn("避免默认误开虚天殿", reply_text)
        self.assertIn("<code>.开启副本 @leader 虚</code>", reply_text)
        self.assertIn("<code>.开启副本 @leader 苍</code>", reply_text)
        self.assertEqual({}, state_module.get_replica_run_state().get("lightweight_dungeon", {}).get("pending_open", {}))

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
        leader_id = self._register_replica_identity(991201, "leader")
        event = self._prepare_replica_group([leader_id])
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
                return await app_replica._handle_virtual_hall_auto_game_event(
                    SimpleNamespace(id=601, chat_id=1),
                    opened,
                    now,
                    reply_context={"reply_to_msg_id": 501, "send_as_id": leader_id},
                )

        self.assertTrue(asyncio.run(run_test()))
        room = app_replica._get_lightweight_last_room(event.chat_id, now=now)
        self.assertEqual("16", room["room_id"])
        self.assertEqual(app_replica._REPLICA_KIND_CANGKUN, room["replica_kind"])

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

    def test_open_failure_notice_includes_copyable_next_commands(self):
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
        self.assertIn("可复制命令", notice_text)
        self.assertIn("<code>.查询副本</code>", notice_text)
        self.assertIn("<code>.开启副本 @leader 苍</code>", notice_text)
        self.assertIn(".查询副本", notice_text)
        self.assertIn(".开启副本 @leader 苍", notice_text)

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

    def test_external_dispatch_full_reply_after_success_does_not_clear_joined_state(self):
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
        self.assertTrue(state_item["participating"])
        self.assertEqual("456", state_item["room_id"])
        self.assertNotIn("dispatch_pending_room_id", state_item)
        self.assertNotIn("dispatch_pending_msg_id", state_item)
        self.assertEqual(779, state_item["last_join_msg_id"])
        record = state_module.get_replica_run_state()["by_identity"][str(first_id)]
        self.assertEqual("joined", record["last_join_result"])
        self.assertEqual(779, record["last_join_msg_id"])

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

    def test_lightweight_join_and_dissolve_use_latest_room(self):
        leader_id = self._register_replica_identity(991201, "leader")
        first_id = self._register_replica_identity(991202, "first")
        second_id = self._register_replica_identity(991203, "second")
        event = self._prepare_replica_group([leader_id, first_id, second_id])
        event.sender_id = 4242
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
            event.raw_text = ".加入副本 @first @second"
            with patch("model.app_replica._get_replica_event_listener_account_id", return_value=9001), \
                    patch("model.app_replica._claim_runtime_event", return_value=True), \
                    patch("model.app_replica._send_replica_group_message", new=AsyncMock(return_value=SimpleNamespace(id=800))), \
                    patch("model.app_replica.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701))):
                handled = await app_replica._handle_lightweight_join_command(event)
                calls = app_replica.send_game_command.await_args_list
                reply_text = app_replica._send_replica_group_message.await_args.args[2]
                return handled, calls, reply_text

        handled, calls, join_reply = asyncio.run(run_join())
        self.assertTrue(handled)
        self.assertEqual(2, len(calls))
        self.assertEqual(".加入苍坤洞府 16", calls[0].args[0])
        self.assertEqual(first_id, calls[0].kwargs["send_as_id"])
        self.assertEqual(second_id, calls[1].kwargs["send_as_id"])
        self.assertEqual("自动副本", calls[0].kwargs["source_module"])
        self.assertEqual("keep", calls[0].kwargs["delete_policy"])
        self.assertIn("replica_lightweight_join", calls[0].kwargs["op_id"])
        self.assertEqual("replica_lightweight_room:cangkun:16", calls[0].kwargs["chain_id"])
        self.assertIn("已发送加入苍坤洞府 16", join_reply)
        self.assertIn("<code>.解散副本</code>", join_reply)
        self.assertIn(".解散副本", join_reply)

        low_id = self._register_replica_identity(991204, "low", realm="筑基后期")
        state_module.set_replica_participant_identity_ids([leader_id, first_id, second_id, low_id])

        async def run_low_join():
            event.raw_text = ".加入副本 @first @low"
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
        self.assertEqual(first_id, calls[0].kwargs["send_as_id"])
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
