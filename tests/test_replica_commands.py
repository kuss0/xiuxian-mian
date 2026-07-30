import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from model.features import replica_commands


OPEN_RE = re.compile(r"^\.开启副本(?:\s+(?P<rest>.*))?$")
JOIN_RE = re.compile(r"^\.加入副本(?:\s+(?P<rest>.*))?$")
ENTER_RE = re.compile(r"^\.(?P<command>进入昆吾山|进入虚天殿)$")


def _resolve_kind_alias(value):
    return {
        "昆": "kunwu",
        "昆吾": "kunwu",
        "虚": "virtual_hall",
    }.get(str(value or ""), "")


class ReplicaCommandBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_open_parser_separates_selector_and_kind(self):
        self.assertEqual(
            ("@leader", "kunwu"),
            replica_commands.parse_lightweight_open_command(
                ".开启副本 @leader 昆",
                OPEN_RE,
                _resolve_kind_alias,
            ),
        )
        self.assertEqual(
            ("@leader", ""),
            replica_commands.parse_lightweight_open_command(
                ".开启副本 @leader",
                OPEN_RE,
                _resolve_kind_alias,
            ),
        )

    def test_join_parser_deduplicates_aliases_without_reordering(self):
        self.assertEqual(
            ["@first", "second"],
            replica_commands.parse_lightweight_join_usernames(
                ".加入副本 @first, first、second @SECOND",
                JOIN_RE,
            ),
        )

    def test_group_command_match_keeps_kunwu_fast_path_only(self):
        context = replica_commands.ReplicaCommandMatchContext(
            query_command=".查询副本",
            open_pattern=OPEN_RE,
            enter_pattern=ENTER_RE,
            kunwu_kind="kunwu",
            kunwu_enter_command=".进入昆吾山",
            dissolve_command=".解散副本",
            is_xiaoji_query_command=lambda text: text == ".查询小极宫",
            resolve_kind_alias=_resolve_kind_alias,
        )

        self.assertTrue(replica_commands.is_replica_group_command_text(context, ".查询副本"))
        self.assertTrue(replica_commands.is_replica_group_command_text(context, ".查询小极宫"))
        self.assertTrue(replica_commands.is_replica_group_command_text(context, ".开启副本 @leader 昆"))
        self.assertTrue(replica_commands.is_replica_group_command_text(context, ".进入昆吾山"))
        self.assertTrue(replica_commands.is_replica_group_command_text(context, ".解散副本"))
        self.assertFalse(replica_commands.is_replica_group_command_text(context, ".开启副本 @leader 虚"))
        self.assertFalse(replica_commands.is_replica_group_command_text(context, ".进入虚天殿"))

    async def test_ticket_query_uses_injected_read_and_send_ports(self):
        send_mock = AsyncMock(return_value=SimpleNamespace(id=700))
        context = replica_commands.ReplicaTicketQueryContext(
            query_command=".查询副本",
            get_listener_account_id=Mock(return_value=9001),
            claim_event=Mock(return_value=True),
            cleanup_run_state=Mock(return_value={"rooms": {}}),
            format_ticket_reply=Mock(return_value="<b>副本</b>"),
            build_open_buttons=Mock(return_value=[["button"]]),
            strip_html=Mock(return_value="副本"),
            send_group_message=send_mock,
            now=Mock(return_value=1000.0),
        )
        event = SimpleNamespace(
            raw_text=".查询副本",
            chat_id=-100777,
            client=object(),
        )

        handled = await replica_commands.handle_ticket_query(context, event)

        self.assertTrue(handled)
        context.cleanup_run_state.assert_called_once_with(1000.0)
        context.build_open_buttons.assert_called_once_with(
            -100777,
            9001,
            now=1000.0,
            records={"rooms": {}},
        )
        send_mock.assert_awaited_once()
        self.assertEqual("副本", send_mock.await_args.kwargs["log_text"])

    async def test_open_command_keeps_unknown_send_in_injected_flow_store(self):
        send_game = AsyncMock(return_value=None)
        send_group = AsyncMock(return_value=SimpleNamespace(id=700))
        upsert_flow = Mock(return_value=True)
        remove_flow = Mock(return_value=True)
        schedule_retry = Mock(return_value=True)
        context = replica_commands.ReplicaOpenCommandContext(
            config=replica_commands.ReplicaOpenCommandConfig(
                command_pattern=OPEN_RE,
                cangkun_kind="cangkun",
                open_timeout_sec=60,
            ),
            runtime=replica_commands.ReplicaOpenRuntimePort(
                get_listener_account_id=Mock(return_value=9001),
                claim_event=Mock(return_value=True),
                send_game_command=send_game,
                build_send_intent=Mock(return_value={"source_module": "自动副本"}),
                schedule_fast_retry=schedule_retry,
                now=Mock(side_effect=[1000.0, 1001.0]),
            ),
            identity=replica_commands.ReplicaOpenIdentityPort(
                resolve_kind_alias=_resolve_kind_alias,
                resolve_identity=Mock(return_value=991201),
                is_identity_enabled=Mock(return_value=True),
                select_open_kind=Mock(return_value="cangkun"),
                format_ticket_counts=Mock(return_value="苍x1"),
                get_openable_kinds=Mock(return_value=["cangkun"]),
                is_open_requirement_available=Mock(return_value=True),
                format_open_requirement=Mock(return_value=""),
                get_ticket_count=Mock(return_value=1),
                get_identity_username=Mock(return_value="Leader"),
                get_kind_name=Mock(return_value="苍坤洞府"),
                get_open_game_command=Mock(return_value=".开启苍坤洞府"),
                get_identity_block_reason=Mock(return_value=""),
            ),
            state=replica_commands.ReplicaOpenStatePort(
                mark_notice_once=Mock(return_value=True),
                get_active_room=Mock(return_value=None),
                find_active_flow=Mock(return_value=None),
                is_flow_active=Mock(return_value=False),
                remove_flow=remove_flow,
                make_flow_id=Mock(return_value="flow-1"),
                upsert_flow=upsert_flow,
            ),
            view=replica_commands.ReplicaOpenViewPort(
                format_usage=Mock(return_value="<code>usage</code>"),
                format_next_commands=Mock(return_value="next"),
                format_open_commands_for_identity=Mock(return_value="open"),
                format_open_command_for_identity=Mock(return_value=".开启副本 @leader 苍"),
                format_existing_room_notice=Mock(return_value="room"),
                existing_room_buttons=Mock(return_value=[]),
                format_existing_open_notice=Mock(return_value="flow"),
                existing_open_buttons=Mock(return_value=[]),
                build_open_buttons=Mock(return_value=[["open"]]),
                build_flow_buttons=Mock(return_value=[["flow"]]),
                strip_html=Mock(side_effect=lambda text: text),
                send_group_message=send_group,
            ),
        )
        event = SimpleNamespace(
            raw_text=".开启副本 @leader 苍",
            chat_id=-100777,
            sender_id=42,
            id=600,
            client=object(),
        )

        handled = await replica_commands.handle_lightweight_open_command(context, event)

        self.assertTrue(handled)
        send_game.assert_awaited_once_with(
            ".开启苍坤洞府",
            track=False,
            send_as_id=991201,
            priority="urgent_reactive",
            source_module="自动副本",
        )
        self.assertEqual(2, upsert_flow.call_count)
        pending_flow = upsert_flow.call_args.args[0]
        self.assertEqual("flow-1", pending_flow["flow_id"])
        self.assertEqual(0, pending_flow["open_command_msg_id"])
        self.assertEqual(1001.0, pending_flow["open_send_unknown_at"])
        self.assertIn("发送结果未知", pending_flow["last_error"])
        schedule_retry.assert_not_called()
        remove_flow.assert_not_called()
        self.assertIn("等待开房广播", send_group.await_args.args[2])

    async def test_join_dispatch_uses_grouped_ports_and_updates_room(self):
        send_game = AsyncMock(return_value=SimpleNamespace(id=501, sent_at=1001.0))
        send_group = AsyncMock(return_value=SimpleNamespace(id=700))
        set_room = Mock(return_value=True)
        mark_sent = Mock()
        schedule_retry = Mock(return_value=True)
        context = replica_commands.ReplicaJoinDispatchContext(
            config=replica_commands.ReplicaJoinDispatchConfig(cangkun_kind="cangkun"),
            runtime=replica_commands.ReplicaJoinDispatchRuntimePort(
                send_game_command=send_game,
                build_send_intent=Mock(return_value={"source_module": "自动副本"}),
                schedule_fast_retry=schedule_retry,
                now=Mock(side_effect=[1000.0, 1002.0]),
            ),
            identity=replica_commands.ReplicaJoinDispatchIdentityPort(
                resolve_identity=Mock(return_value=991202),
                is_identity_enabled=Mock(return_value=True),
                is_cangkun_realm_available=Mock(return_value=True),
                format_cangkun_realm_requirement=Mock(return_value=""),
                get_identity_block_reason=Mock(return_value=""),
                get_identity_username=Mock(return_value="Member"),
            ),
            state=replica_commands.ReplicaJoinDispatchStatePort(
                reserve_join=Mock(return_value=(True, "")),
                mark_join_sent=mark_sent,
                set_room=set_room,
            ),
            view=replica_commands.ReplicaJoinDispatchViewPort(
                get_kind_name=Mock(return_value="虚天殿"),
                get_enter_command=Mock(return_value=".进入虚天殿"),
                is_room_enter_actionable=Mock(return_value=True),
                is_room_dissolve_actionable=Mock(return_value=True),
                get_room_team_identity_ids=Mock(return_value=[]),
                get_room_usernames=Mock(return_value=[]),
                format_team_notice_details=Mock(return_value=[]),
                format_cangkun_sense=Mock(return_value=""),
                format_next_commands=Mock(return_value="next"),
                build_room_buttons=Mock(return_value=[["room"]]),
                strip_html=Mock(side_effect=lambda text: text),
                send_group_message=send_group,
            ),
        )
        room = {
            "replica_kind": "virtual_hall",
            "room_id": "47",
            "leader_identity_id": 991201,
        }
        event = SimpleNamespace(chat_id=-100777, id=600, client=object())

        handled = await replica_commands.dispatch_lightweight_join_members(
            context,
            event,
            9001,
            room,
            ["@member"],
            ".加入虚天殿 47",
        )

        self.assertTrue(handled)
        send_game.assert_awaited_once_with(
            ".加入虚天殿 47",
            track=False,
            send_as_id=991202,
            priority="urgent_reactive",
            source_module="自动副本",
        )
        mark_sent.assert_called_once_with(991202, "virtual_hall", "47", 501, 1001.0)
        schedule_retry.assert_called_once_with(
            "join",
            991202,
            "virtual_hall",
            "47",
            ".加入虚天殿 47",
            -100777,
            600,
            501,
        )
        set_room.assert_called_once_with(room)
        self.assertEqual(["@member"], room["join_requested_usernames"])
        self.assertEqual(1002.0, room["updated_at"])
        self.assertIn("已发送加入虚天殿 47：@member", send_group.await_args.args[2])
        self.assertIn("next", send_group.await_args.args[2])

    async def test_join_preflight_returns_ready_payload_without_sending(self):
        room = {
            "replica_kind": "kunwu",
            "room_id": "47",
            "leader_identity_id": 991201,
        }
        send_group = AsyncMock()
        context = replica_commands.ReplicaJoinPreflightContext(
            config=replica_commands.ReplicaJoinPreflightConfig(
                command_pattern=JOIN_RE,
                virtual_hall_kind="virtual_hall",
                query_command=".查询副本",
                open_usage=".开启副本 @用户名",
                join_usage=".加入副本 @用户名 @用户名",
                dissolve_command=".解散副本",
            ),
            runtime=replica_commands.ReplicaJoinPreflightRuntimePort(
                get_listener_account_id=Mock(return_value=9001),
                claim_event=Mock(return_value=True),
                now=Mock(return_value=1000.0),
            ),
            state=replica_commands.ReplicaJoinPreflightStatePort(
                get_room=Mock(return_value=room),
                find_active_flow=Mock(return_value=None),
            ),
            view=replica_commands.ReplicaJoinPreflightViewPort(
                format_existing_open_notice=Mock(return_value="flow"),
                existing_open_buttons=Mock(return_value=[]),
                format_next_commands=Mock(return_value="next"),
                build_open_buttons=Mock(return_value=[]),
                existing_room_buttons=Mock(return_value=[]),
                get_join_command=Mock(return_value=".加入昆吾山"),
                is_room_enter_actionable=Mock(return_value=True),
                format_virtual_hall_not_actionable_notice=Mock(return_value="blocked"),
                build_room_buttons=Mock(return_value=[]),
                strip_html=Mock(side_effect=lambda text: text),
                send_group_message=send_group,
            ),
        )
        event = SimpleNamespace(
            raw_text=".加入副本 @first @second",
            chat_id=-100777,
            id=600,
            client=object(),
        )

        result = await replica_commands.prepare_lightweight_join_command(context, event)

        self.assertFalse(result.terminal)
        self.assertTrue(result.return_value)
        self.assertEqual(9001, result.listener_account_id)
        self.assertIs(room, result.room)
        self.assertEqual(("@first", "@second"), result.selectors)
        self.assertEqual(".加入昆吾山 47", result.command)
        send_group.assert_not_awaited()

    async def test_join_preflight_waits_for_existing_open_flow(self):
        active_flow = {"flow_id": "flow-1"}
        send_group = AsyncMock(return_value=SimpleNamespace(id=700))
        context = replica_commands.ReplicaJoinPreflightContext(
            config=replica_commands.ReplicaJoinPreflightConfig(
                command_pattern=JOIN_RE,
                virtual_hall_kind="virtual_hall",
                query_command=".查询副本",
                open_usage=".开启副本 @用户名",
                join_usage=".加入副本 @用户名 @用户名",
                dissolve_command=".解散副本",
            ),
            runtime=replica_commands.ReplicaJoinPreflightRuntimePort(
                get_listener_account_id=Mock(return_value=9001),
                claim_event=Mock(return_value=True),
                now=Mock(return_value=1000.0),
            ),
            state=replica_commands.ReplicaJoinPreflightStatePort(
                get_room=Mock(return_value=None),
                find_active_flow=Mock(return_value=active_flow),
            ),
            view=replica_commands.ReplicaJoinPreflightViewPort(
                format_existing_open_notice=Mock(return_value="等待开房广播"),
                existing_open_buttons=Mock(return_value=[["flow"]]),
                format_next_commands=Mock(return_value="next"),
                build_open_buttons=Mock(return_value=[]),
                existing_room_buttons=Mock(return_value=[]),
                get_join_command=Mock(return_value=""),
                is_room_enter_actionable=Mock(return_value=True),
                format_virtual_hall_not_actionable_notice=Mock(return_value="blocked"),
                build_room_buttons=Mock(return_value=[]),
                strip_html=Mock(side_effect=lambda text: text),
                send_group_message=send_group,
            ),
        )
        event = SimpleNamespace(
            raw_text=".加入副本 @first",
            chat_id=-100777,
            id=600,
            client=object(),
        )

        result = await replica_commands.prepare_lightweight_join_command(context, event)

        self.assertTrue(result.terminal)
        self.assertTrue(result.return_value)
        send_group.assert_awaited_once()
        self.assertEqual("等待开房广播", send_group.await_args.args[2])
        self.assertEqual([["flow"]], send_group.await_args.kwargs["buttons"])


if __name__ == "__main__":
    unittest.main()
