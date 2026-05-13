import atexit
import sys
import unittest
from pathlib import Path


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
                "ADMIN_ID=0",
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
    def test_deep_retreat_plan_uses_plain_trigger_then_command(self):
        anchor_at = 1000.0
        plan = official_schedule.build_preset_plan(
            official_schedule.PRESET_DEEP_RETREAT,
            anchor_at=anchor_at,
            horizon_days=1,
        )

        items = plan["items"]
        self.assertEqual(6, len(items))
        self.assertEqual("查看闭关", items[0]["command"])
        self.assertEqual(".深度闭关", items[1]["command"])
        self.assertEqual(anchor_at + DEEP_RETREAT_CD + 120, items[0]["schedule_at"])
        self.assertEqual(anchor_at + DEEP_RETREAT_CD + 180, items[1]["schedule_at"])

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


if __name__ == "__main__":
    unittest.main()
