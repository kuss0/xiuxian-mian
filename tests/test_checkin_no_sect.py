import atexit
import copy
import sys
import unittest
from datetime import datetime, timezone
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

from model import config
from model import state as state_module
from model.features import checkin, passive_inbox


class CheckinNoSectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self, send_as_id=991001):
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="loose", sect_name="星宫")
        with state_module.use_identity(send_as_id) as identity_state:
            for field_name in (
                "checkin_enabled",
                "tower_enabled",
                "tree_enabled",
                "ranch_enabled",
                "stargazer_enabled",
                "guanxing_enabled",
                "tianti_enabled",
                "taiyi_enabled",
                "taiyi_node_search_enabled",
            ):
                identity_state[field_name] = True
            identity_state["next_checkin_time"] = now
            identity_state["next_sect_teach_time"] = now + 10
            identity_state["sect_teach_reply_to_msg_id"] = 101
            identity_state["next_tower_time"] = now + 20
            identity_state["next_stargazer_panel_time"] = now + 30
            identity_state["next_tianti_climb_time"] = now + 40
            identity_state["next_taiyi_cycle_time"] = now + 50
            identity_state["pending_tasks"] = {
                101: {"cmd": config.CMD_CHECKIN, "sent_at": now, "retry": 0},
                102: {"cmd": config.CMD_SECT_TEACH, "sent_at": now, "retry": 0},
                103: {"cmd": config.CMD_TOWER, "sent_at": now, "retry": 0},
                104: {"cmd": config.CMD_GUANXING, "sent_at": now, "retry": 0},
                999: {"cmd": config.CMD_PET, "sent_at": now, "retry": 0},
            }
            identity_state["my_msg_ids"] = {101: now, 102: now, 103: now, 104: now, 999: now}
        return send_as_id, now

    async def test_no_sect_checkin_reply_disables_sect_modules(self):
        send_as_id, now = self._prepare_identity()
        reply = SimpleNamespace(id=101, raw_text=config.CMD_CHECKIN)

        with state_module.use_identity(send_as_id), \
             patch.object(checkin, "save_state"), \
             patch.object(checkin, "send_audit_log", new=AsyncMock()) as audit_mock:
            handled = await checkin.handle_checkin_reply(
                "散修无需点卯，速速寻一宗门拜入吧。",
                now,
                reply,
            )

        self.assertTrue(handled)
        audit_mock.assert_awaited_once()
        with state_module.use_identity(send_as_id) as identity_state:
            for field_name in (
                "checkin_enabled",
                "tower_enabled",
                "tree_enabled",
                "ranch_enabled",
                "stargazer_enabled",
                "guanxing_enabled",
                "tianti_enabled",
                "taiyi_enabled",
                "taiyi_node_search_enabled",
            ):
                self.assertFalse(identity_state[field_name], field_name)
            self.assertEqual(0, identity_state["next_checkin_time"])
            self.assertEqual(0, identity_state["next_sect_teach_time"])
            self.assertEqual(0, identity_state["next_tower_time"])
            self.assertEqual({999: {"cmd": config.CMD_PET, "sent_at": now, "retry": 0}}, identity_state["pending_tasks"])
            self.assertEqual({999: now}, identity_state["my_msg_ids"])

        self.assertEqual("散修", state_module.get_send_as_profile(send_as_id)["sect_name"])

    async def test_passive_no_sect_checkin_does_not_schedule_next_checkin(self):
        send_as_id, now = self._prepare_identity(send_as_id=991002)

        with state_module.use_identity(send_as_id):
            changed = passive_inbox._apply_checkin_passive(
                "散修无需点卯，速速寻一宗门拜入吧。",
                now,
                "checkin",
            )

        self.assertTrue(changed)
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertFalse(identity_state["checkin_enabled"])
            self.assertEqual("", identity_state["last_checkin_done_day"])
            self.assertEqual(0, identity_state["next_checkin_time"])
            self.assertEqual(0, identity_state["next_sect_teach_time"])

    async def test_no_sect_reply_still_disables_when_checkin_already_off(self):
        send_as_id, now = self._prepare_identity(send_as_id=991003)
        reply = SimpleNamespace(id=101, raw_text=config.CMD_CHECKIN)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["checkin_enabled"] = False
            self.assertTrue(identity_state["tower_enabled"])

        with state_module.use_identity(send_as_id), \
             patch.object(checkin, "save_state"), \
             patch.object(checkin, "send_audit_log", new=AsyncMock()):
            handled = await checkin.handle_checkin_reply(
                "散修无需点卯，速速寻一宗门拜入吧。",
                now,
                reply,
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertFalse(identity_state["checkin_enabled"])
            self.assertFalse(identity_state["tower_enabled"])
            self.assertEqual(0, identity_state["next_sect_teach_time"])

    async def test_checkin_send_records_anchor_immediately(self):
        send_as_id = 991004
        now = datetime(2026, 6, 20, 2, 30, tzinfo=timezone.utc).timestamp()
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="checkinanchor", sect_name="星宫")

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["checkin_enabled"] = True
            identity_state["sect_teach_enabled"] = False
            identity_state["checkin_teach_day"] = checkin.get_checkin_day_key(now)
            identity_state["last_checkin_done_day"] = ""
            identity_state["next_checkin_time"] = now - 1

            fake_msg = SimpleNamespace(id=7701, sent_at=now)
            with (
                patch.object(checkin, "send_game_command", new=AsyncMock(return_value=fake_msg)) as send_mock,
                patch.object(checkin, "save_state"),
            ):
                await checkin.run_checkin_scheduler(now)

            send_mock.assert_awaited_once_with(config.CMD_CHECKIN, max_retry=1)
            self.assertEqual(7701, identity_state["last_checkin_msg_id"])
            self.assertEqual(now, identity_state["my_msg_ids"][7701])
            self.assertIn(7701, identity_state["checkin_cleanup_msg_ids"])

    async def test_recent_checkin_anchor_blocks_duplicate_send_if_pending_was_lost(self):
        send_as_id = 991005
        now = datetime(2026, 6, 20, 2, 30, tzinfo=timezone.utc).timestamp()
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="checkinrecent", sect_name="星宫")

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["checkin_enabled"] = True
            identity_state["sect_teach_enabled"] = False
            identity_state["checkin_teach_day"] = checkin.get_checkin_day_key(now)
            identity_state["last_checkin_done_day"] = ""
            identity_state["last_checkin_msg_id"] = 7702
            identity_state["my_msg_ids"] = {7702: now - 10}
            identity_state["pending_tasks"] = {}
            identity_state["next_checkin_time"] = now - 1

            with patch.object(checkin, "send_game_command", new=AsyncMock()) as send_mock:
                await checkin.run_checkin_scheduler(now)

            send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
