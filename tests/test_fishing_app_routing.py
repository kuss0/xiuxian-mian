import copy
import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("XIUXIAN_TESTING", "1")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "00000000000000000000000000000000")
os.environ.setdefault("TG_PROXY_TYPE", "")
os.environ.setdefault("TG_PROXY_HOST", "127.0.0.1:7890")
os.environ.setdefault("LOG_GROUP_ID", "0")
os.environ.setdefault("LOG_SEND_MODE", "account")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("CHAOGU_UI_HOST", "127.0.0.1")
os.environ.setdefault("CHAOGU_UI_PORT", "3030")
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import state as state_module


FISHING_START_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：静候鱼讯
进度：□□□□□□□□□□ 0%

你挂上 【灵米饵】，抛竿入水，敛息坐定。
预计 47秒 内会有鱼讯。
可用：.钓鱼状态 / .收竿"""


class FishingAppRoutingTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_waiting_identity(self, identity_id, username, now):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["fishing_enabled"] = True
        identity_state["fishing_reply_to_msg_id"] = identity_id + 100
        identity_state["fishing_reply_due_at"] = now + 60

    def test_swallowed_reply_fallback_selects_single_waiting_identity(self):
        now = 1_700_000_000.0
        self._prepare_waiting_identity(10001, "walterwa2000", now)

        self.assertEqual([10001], app._candidate_fishing_swallowed_reply_identity_ids(FISHING_START_TEXT, now))

    def test_swallowed_reply_fallback_uses_username_when_multiple_waiting(self):
        now = 1_700_000_000.0
        self._prepare_waiting_identity(10001, "walterwa2000", now)
        self._prepare_waiting_identity(10002, "other", now)

        self.assertEqual([10001], app._candidate_fishing_swallowed_reply_identity_ids(FISHING_START_TEXT, now))

    def test_swallowed_reply_fallback_refuses_ambiguous_waiting_identities(self):
        now = 1_700_000_000.0
        self._prepare_waiting_identity(10001, "first", now)
        self._prepare_waiting_identity(10002, "second", now)

        text_without_name = FISHING_START_TEXT.replace("@WalterWA2000", "@unknown")

        self.assertEqual([], app._candidate_fishing_swallowed_reply_identity_ids(text_without_name, now))


if __name__ == "__main__":
    unittest.main()
