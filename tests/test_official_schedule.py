import atexit
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

from model import official_schedule
from model.config import DEEP_RETREAT_CD


class OfficialSchedulePlanTests(unittest.TestCase):
    def test_deep_retreat_plan_uses_command_without_status_probe(self):
        anchor_at = 1000.0
        plan = official_schedule.build_preset_plan(
            official_schedule.PRESET_DEEP_RETREAT,
            anchor_at=anchor_at,
            horizon_days=1,
        )

        items = plan["items"]
        self.assertEqual(3, len(items))
        self.assertEqual(".深度闭关", items[0]["command"])
        self.assertEqual(anchor_at + DEEP_RETREAT_CD + 180, items[0]["schedule_at"])

    def test_pet_warm_plan_uses_configured_name_and_six_hour_interval(self):
        anchor_at = 2000.0
        plan = official_schedule.build_preset_plan(
            official_schedule.PRESET_PET_WARM,
            anchor_at=anchor_at,
            horizon_days=1,
            pet_name="青竹蜂云剑（庚金版）",
        )

        items = plan["items"]
        self.assertEqual(4, len(items))
        self.assertEqual(".温养器灵 青竹蜂云剑（庚金版）", items[0]["command"])
        self.assertEqual(anchor_at + 6 * 3600 + 180, items[0]["schedule_at"])
        self.assertEqual(anchor_at + 12 * 3600 + 180, items[1]["schedule_at"])


class OfficialScheduleRpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_scheduled_messages_uses_resolved_account(self):
        client = AsyncMock(return_value=SimpleNamespace(messages=[SimpleNamespace(
            id=701,
            message="scheduled command",
            date=datetime(2026, 9, 8, tzinfo=timezone.utc),
            from_id="identity",
        )]))
        with patch.object(
            official_schedule, "_resolve_schedule_context", new=AsyncMock(
                return_value=(client, "group-peer", "identity-peer", None, 42),
            ),
        ):
            items = await official_schedule.list_official_scheduled_messages(123)

        self.assertEqual([701], [item["scheduled_msg_id"] for item in items])
        self.assertEqual("scheduled command", items[0]["message"])
        client.assert_awaited_once()
        request = client.await_args.args[0]
        self.assertIsInstance(request, official_schedule.functions.messages.GetScheduledHistoryRequest)
        self.assertEqual("group-peer", request.peer)

    async def test_delete_scheduled_messages_uses_resolved_account(self):
        client = AsyncMock(return_value=None)
        with patch.object(
            official_schedule, "_resolve_schedule_context", new=AsyncMock(
                return_value=(client, "group-peer", "identity-peer", None, 42),
            ),
        ):
            deleted = await official_schedule.delete_official_scheduled_messages(123, [701, 702])

        self.assertEqual(2, deleted)
        client.assert_awaited_once()
        request = client.await_args.args[0]
        self.assertIsInstance(request, official_schedule.functions.messages.DeleteScheduledMessagesRequest)
        self.assertEqual("group-peer", request.peer)
        self.assertEqual([701, 702], request.id)

    async def test_empty_delete_does_not_resolve_or_contact_telegram(self):
        with patch.object(official_schedule, "_resolve_schedule_context", new=AsyncMock()) as resolve:
            self.assertEqual(0, await official_schedule.delete_official_scheduled_messages(123, []))
        resolve.assert_not_awaited()

    async def test_unbound_identity_cannot_fall_back_to_another_account(self):
        from model import runtime

        with patch.object(official_schedule, "get_identity_account", return_value=0), \
                patch.object(runtime, "_get_any_authed_client_with_account") as fallback:
            with self.assertRaisesRegex(RuntimeError, "账号"):
                await official_schedule._resolve_schedule_context(123)
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
