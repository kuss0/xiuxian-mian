import atexit
import copy
import sys
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
from model.features import tree


class TreeStorageSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_harvest_success_syncs_reward_items_to_storage_bag(self):
        send_as_id = 8659059191
        state_module.ensure_identity_registered(send_as_id)
        reply_to = SimpleNamespace(id=11, raw_text=".灵树采摘")
        text = (
            "【灵果入腹 · 造化自生】\n"
            "你摘下一枚【赤霞灵果】。\n"
            "修为增长：+1,234\n"
            "你获得【木髓】x2、分得【妖丹】×3。"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["tree_enabled"] = True
            audit_mock = AsyncMock()
            with patch.object(tree, "save_state"), patch.object(tree, "send_audit_log", new=audit_mock):
                handled = await tree.handle_tree_harvest_reply(
                    text, 1000.0, reply_to, matched_family="tree_harvest", current_msg_id=101
                )
                duplicate = await tree.handle_tree_harvest_reply(
                    text, 1001.0, reply_to, matched_family="tree_harvest", current_msg_id=101
                )

        self.assertTrue(handled)
        self.assertTrue(duplicate)
        record = state_module.get_storage_bag_records()[str(send_as_id)]
        self.assertEqual(2, record["items"]["木髓"])
        self.assertEqual(3, record["items"]["妖丹"])
        self.assertEqual(1, audit_mock.await_count)


if __name__ == "__main__":
    unittest.main()
