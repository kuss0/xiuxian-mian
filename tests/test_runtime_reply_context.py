import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import state as state_module


class RuntimeReplyContextTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._reply_chain_tracker_snapshot = copy.deepcopy(runtime._reply_chain_tracker)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        runtime._reply_chain_tracker.clear()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        runtime._reply_chain_tracker.clear()
        runtime._reply_chain_tracker.update(copy.deepcopy(self._reply_chain_tracker_snapshot))

    def _register_identity(self, identity_id, username="@target"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, enabled=True)
        return identity_id

    def _write_message_log(self, tmpdir, payload):
        day = runtime.datetime.now(runtime.TZ_LOCAL).strftime("%Y-%m-%d")
        path = Path(tmpdir) / f"{day}.log"
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def test_reply_context_recovers_script_sent_message_from_log(self):
        identity_id = self._register_identity(991201)
        payload = {
            "event_type": "sent",
            "message_id": 7001,
            "sender_id": identity_id,
            "text": ".侍妾远航 冒险",
            "family": "concubine_voyage",
        }

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            self._write_message_log(tmpdir, payload)
            context = runtime.get_reply_context(reply_to_msg_id=7001)

        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("concubine_voyage", context["family"])
        self.assertEqual("sent_message_log", context["matched_via"])
        self.assertEqual(7001, context["root_msg_id"])

    def test_reply_context_filters_same_message_id_by_chat(self):
        first_identity = self._register_identity(991201)
        second_identity = self._register_identity(991202)
        day = runtime.datetime.now(runtime.TZ_LOCAL).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            path = Path(tmpdir) / f"{day}.log"
            path.write_text(
                "\n".join([
                    json.dumps({
                        "event_type": "sent", "message_id": 7001, "chat_id": -1001,
                        "sender_id": first_identity, "text": ".天机盘", "family": "tianxing_panel",
                    }, ensure_ascii=False),
                    json.dumps({
                        "event_type": "sent", "message_id": 7001, "chat_id": -1002,
                        "sender_id": second_identity, "text": ".世界boss", "family": "world_boss_status",
                    }, ensure_ascii=False),
                ]) + "\n",
                encoding="utf-8",
            )
            context = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1002)

        self.assertEqual(second_identity, context["send_as_id"])
        self.assertEqual("world_boss_status", context["family"])

    def test_reply_context_cold_recovers_sent_message_outside_hot_tail(self):
        identity_id = self._register_identity(991201)
        payload = {
            "event_type": "sent",
            "message_id": 7001,
            "chat_id": -1001680975844,
            "sender_id": identity_id,
            "text": ".小世界",
            "family": "small_world_query",
        }

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            day = runtime.datetime.now(runtime.TZ_LOCAL).strftime("%Y-%m-%d")
            path = Path(tmpdir) / f"{day}.log"
            filler = {"event_type": "message", "message_id": 9000, "text": "filler"}
            path.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n"
                + (json.dumps(filler) + "\n") * 12000,
                encoding="utf-8",
            )
            context = runtime.get_reply_context(
                reply_to_msg_id=7001,
                chat_id=-1001680975844,
            )

        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("small_world_query", context["family"])
        self.assertEqual("sent_message_log", context["matched_via"])

    def test_sent_message_chat_lookup_filters_same_message_id_by_identity(self):
        first_identity = self._register_identity(991201)
        second_identity = self._register_identity(991202)
        day = runtime.datetime.now(runtime.TZ_LOCAL).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            path = Path(tmpdir) / f"{day}.log"
            path.write_text(
                "\n".join([
                    json.dumps({
                        "event_type": "sent", "message_id": 7001, "chat_id": -1001,
                        "sender_id": first_identity, "text": ".天机盘",
                    }, ensure_ascii=False),
                    json.dumps({
                        "event_type": "sent", "message_id": 7001, "chat_id": -1002,
                        "sender_id": second_identity, "text": ".世界boss",
                    }, ensure_ascii=False),
                ]) + "\n",
                encoding="utf-8",
            )
            first_chat = runtime.get_sent_message_chat_id(7001, send_as_id=first_identity)
            second_chat = runtime.get_sent_message_chat_id(7001, send_as_id=second_identity)

        self.assertEqual(-1001, first_chat)
        self.assertEqual(-1002, second_chat)

    def test_reply_context_does_not_recover_manual_message_from_log(self):
        identity_id = self._register_identity(991201)
        payload = {
            "event_type": "message",
            "message_id": 7001,
            "sender_id": identity_id,
            "text": ".侍妾远航 冒险",
        }

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            self._write_message_log(tmpdir, payload)
            context = runtime.get_reply_context(reply_to_msg_id=7001)

        self.assertIsNone(context["send_as_id"])
        self.assertIsNone(context["family"])
        self.assertNotEqual("sent_message_log", context["matched_via"])
        self.assertEqual("", context["source"])

    def test_reply_context_preserves_manual_game_command_source(self):
        identity_id = self._register_identity(991201)

        tracked = runtime.track_reply_chain_message(
            7001,
            identity_id,
            "concubine_voyage",
            root_msg_id=7001,
            source="manual_game_command",
        )
        context = runtime.get_reply_context(reply_to_msg_id=7001)

        self.assertTrue(tracked)
        self.assertEqual("manual_game_command", runtime._reply_chain_tracker[(0, 7001, identity_id)]["source"])
        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("concubine_voyage", context["family"])
        self.assertEqual("reply_chain_tracker", context["matched_via"])
        self.assertEqual(7001, context["root_msg_id"])
        self.assertEqual("manual_game_command", context["source"])

    def test_reply_context_keeps_legacy_track_source_empty(self):
        identity_id = self._register_identity(991201)

        tracked = runtime.track_reply_chain_message(
            7001,
            identity_id,
            "concubine_voyage",
            root_msg_id=7001,
        )
        context = runtime.get_reply_context(reply_to_msg_id=7001)

        self.assertTrue(tracked)
        self.assertEqual("", runtime._reply_chain_tracker[(0, 7001, identity_id)]["source"])
        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("concubine_voyage", context["family"])
        self.assertEqual("reply_chain_tracker", context["matched_via"])
        self.assertEqual("", context["source"])

    def test_manual_echo_does_not_overwrite_script_reply_tracker(self):
        identity_id = self._register_identity(991201)

        tracked = runtime.track_reply_chain_message(
            7001,
            identity_id,
            "divination",
            root_msg_id=7001,
            source="",
        )
        echoed = runtime.track_reply_chain_message(
            7001,
            identity_id,
            "divination",
            root_msg_id=7001,
            source="manual_game_command",
        )
        context = runtime.get_reply_context(reply_to_msg_id=7001)

        self.assertTrue(tracked)
        self.assertTrue(echoed)
        self.assertEqual("", runtime._reply_chain_tracker[(0, 7001, identity_id)]["source"])
        self.assertEqual(identity_id, context["send_as_id"])
        self.assertEqual("divination", context["family"])
        self.assertEqual("", context["source"])

    def test_memory_tracker_keeps_both_chats_without_reading_logs(self):
        identity_id = self._register_identity(991201)
        runtime.track_reply_chain_message(7001, identity_id, "checkin", root_msg_id=6999, chat_id=-1001)
        runtime.track_reply_chain_message(7001, identity_id, "duel", root_msg_id=6998, chat_id=-1002)
        with patch.object(runtime, "_resolve_identity_from_sent_message_log") as lookup:
            first = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1001)
            second = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1002)
        lookup.assert_not_called()
        self.assertEqual((identity_id, "checkin", 6999, -1001), (
            first["send_as_id"], first["family"], first["root_msg_id"], first["chat_id"],
        ))
        self.assertEqual((identity_id, "duel", 6998, -1002), (
            second["send_as_id"], second["family"], second["root_msg_id"], second["chat_id"],
        ))

    def test_missing_chat_does_not_pick_one_of_two_memory_owners(self):
        first = self._register_identity(991201)
        second = self._register_identity(991202)
        runtime.track_reply_chain_message(7001, first, "checkin", chat_id=-1001)
        runtime.track_reply_chain_message(7001, second, "duel", chat_id=-1002)
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            context = runtime.get_reply_context(reply_to_msg_id=7001)
        self.assertIsNone(context["send_as_id"])

    def test_missing_chat_does_not_pick_latest_colliding_log_row(self):
        identity_id = self._register_identity(991201)
        day = runtime.datetime.now(runtime.TZ_LOCAL).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            path = Path(tmpdir) / f"{day}.log"
            path.write_text("\n".join(json.dumps({
                "event_type": "sent", "message_id": 7001, "chat_id": chat_id,
                "sender_id": identity_id, "text": command,
            }) for chat_id, command in ((-1001, ".宗门点卯"), (-1002, ".斗法 @test"))) + "\n", encoding="utf-8")
            context = runtime.get_reply_context(reply_to_msg_id=7001)
        self.assertIsNone(context["send_as_id"])

    def test_identity_info_family_does_not_depend_on_legacy_business_id(self):
        identity_id = self._register_identity(991201)
        state_module.get_identity_state(identity_id)["pending_tasks"][7001] = {
            "cmd": runtime.CMD_IDENTITY_INFO, "chat_id": -1001,
        }
        context = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1001)
        self.assertEqual("identity_info", context["family"])

    def test_foreign_chat_cannot_reuse_unscoped_message_or_business_ids(self):
        identity_id = self._register_identity(991201)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["my_msg_ids"][7001] = 100.0
        identity_state["last_checkin_msg_id"] = 7001
        runtime.track_reply_chain_message(7001, identity_id, "checkin", chat_id=-1001)
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            context = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1002)
        self.assertIsNone(context["send_as_id"])

    def test_reply_object_chat_is_used_when_caller_omits_it(self):
        identity_id = self._register_identity(991201)
        runtime.track_reply_chain_message(7001, identity_id, "checkin", chat_id=-1001)
        runtime.track_reply_chain_message(7001, identity_id, "duel", chat_id=-1002)
        reply = SimpleNamespace(id=7001, chat_id=-1001, raw_text="", sender_id=0)
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(runtime, "MESSAGES_DIR", tmpdir):
            context = runtime.get_reply_context(reply)
        self.assertEqual("checkin", context["family"])
        self.assertEqual(-1001, context["chat_id"])

    def test_clear_pending_is_anchored_to_root_and_chat(self):
        identity_id = self._register_identity(991201)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["pending_tasks"] = {
            6999: {"cmd": ".宗门点卯", "chat_id": -1001},
            7000: {"cmd": ".宗门点卯", "chat_id": -1002},
        }
        context = {"send_as_id": identity_id, "family": "checkin", "reply_to_msg_id": 7001,
                   "root_msg_id": 6999, "chat_id": -1001}
        result = runtime.clear_pending_by_reply(reply_context=context)
        self.assertEqual([6999], result["removed_ids"])
        self.assertIn(7000, identity_state["pending_tasks"])

    def test_clear_pending_rejects_same_id_in_wrong_chat_or_identity(self):
        identity_id = self._register_identity(991201)
        identity_state = state_module.get_identity_state(identity_id)
        item = {"cmd": ".宗门点卯", "chat_id": -1002}
        identity_state["pending_tasks"][7001] = item
        context = {"send_as_id": identity_id, "family": "checkin", "reply_to_msg_id": 7001, "chat_id": -1001}
        result = runtime.clear_pending_by_reply(reply_context=context)
        self.assertFalse(result["removed_ids"])
        self.assertEqual(item, identity_state["pending_tasks"][7001])
        context["chat_id"] = -1002
        result = runtime.clear_pending_by_reply(send_as_id=991202, reply_context=context)
        self.assertFalse(result["removed_ids"])
        self.assertIn(7001, identity_state["pending_tasks"])

    def test_clear_pending_does_not_assume_chat_for_legacy_row(self):
        identity_id = self._register_identity(991201)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["pending_tasks"][7001] = {"cmd": ".宗门点卯"}
        context = {"send_as_id": identity_id, "family": "checkin", "reply_to_msg_id": 7001, "chat_id": -1001}
        self.assertFalse(runtime.clear_pending_by_reply(reply_context=context)["removed_ids"])
        self.assertIn(7001, identity_state["pending_tasks"])
