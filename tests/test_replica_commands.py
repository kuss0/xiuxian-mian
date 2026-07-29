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


if __name__ == "__main__":
    unittest.main()
