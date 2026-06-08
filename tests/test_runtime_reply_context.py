import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import state as state_module


class RuntimeReplyContextTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

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
