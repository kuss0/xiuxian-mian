import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.features import tower_miniapp
from model.webapp_core import validate_miniapp_flow_plan


class TowerMiniAppTests(unittest.TestCase):
    def test_adapter_and_plan_are_scoped_and_valid(self):
        adapter = tower_miniapp.build_tower_miniapp_adapter()
        plan = tower_miniapp.build_tower_miniapp_flow_plan()
        self.assertEqual([], validate_miniapp_flow_plan(plan, adapter))
        self.assertEqual(
            {"start", "challenge", "reset"},
            set(adapter.safe_summary()["endpoint_keys"]),
        )
        self.assertNotIn("reset", plan.safe_summary()["steps"][1]["endpoint"])

    def test_challenge_runs_only_when_start_allows_it(self):
        requests = []

        def transport(request):
            requests.append(request)
            if request["url"].endswith("/start"):
                return 200, {"ok": True, "state": {"canChallenge": True, "power": 123}}
            return 200, {
                "ok": True,
                "state": {"canChallenge": False, "todayHighest": 8},
                "replay": {
                    "clearedCount": 8,
                    "endFloor": 8,
                    "failedFloor": 9,
                    "report": "修为增加 1,260 点，获得塔印 42 点。",
                },
            }

        result = tower_miniapp.run_tower_miniapp_lab_flow(
            token="pagoda_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
            sleeper=lambda _delay: None,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("challenged", result["status"])
        self.assertEqual(["start", "challenge"], [item["step"] for item in result["events"]])
        self.assertEqual({"修为": 1260, "塔印": 42}, result["data"]["gains"])
        self.assertEqual(2, len(requests))
        self.assertTrue(all("reset" not in item["url"] for item in requests))
        safe = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("VERY_SECRET", safe)

    def test_start_done_today_does_not_challenge_or_reset(self):
        requests = []

        def transport(request):
            requests.append(request)
            return 200, {"ok": True, "state": {"canChallenge": False, "failedFloor": 21}}

        result = tower_miniapp.run_tower_miniapp_lab_flow(
            token="pagoda_SECRET999",
            init_data="query_id=abc&hash=VERY_SECRET",
            transport=transport,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("done_today", result["status"])
        self.assertEqual(["start"], [item["step"] for item in result["events"]])
        self.assertEqual(1, len(requests))

    def test_parser_extracts_structured_tower_materials(self):
        gains, rewards = tower_miniapp.extract_tower_materials({
            "replay": {"report": "修为增加 12,000 点，获得塔印 8 点，获得【玄骨化焰诀】x1。"},
        })
        self.assertEqual({"修为": 12000, "塔印": 8}, gains)
        self.assertEqual({"玄骨化焰诀": 1}, rewards)

    def test_parser_preserves_cultivation_loss_and_obtained_wording(self):
        gains, rewards = tower_miniapp.extract_tower_materials({
            "replay": {"report": "修为 损失了 7,506 点，获得塔印 49 点，获得了【灵石】x894。"},
        })
        self.assertEqual({"修为": -7506, "塔印": 49}, gains)
        self.assertEqual({"灵石": 894}, rewards)
        self.assertEqual("-7506", tower_miniapp.format_tower_delta(gains["修为"]))
