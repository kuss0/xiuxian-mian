import json
import tempfile
import unittest
from pathlib import Path

from tools import miniapp_daily_report


class MiniAppDailyReportTests(unittest.TestCase):
    def _write(self, root: Path, game_key: str, day: str, records):
        path = root / f"{game_key}-{day}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_report_only_includes_game_rewards_not_technical_fields(self):
        day = "2026-07-07"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "trial", day, [{
                "ok": True,
                "step_key": "finish",
                "source": "trial_runtime:1001:1",
                "response": {"body": {
                    "event_id": "trial-1",
                    "result": {
                        "reward_trace": 9,
                        "expGain": 12,
                        "score": 97,
                        "rewards": [{"name": "天机砂", "qty": 2}],
                    },
                    "sessionId": "secret-session",
                }},
            }])
            self._write(root, "cave_treasure", day, [{
                "ok": True,
                "step_key": "action:settle",
                "source": "cave_runtime:1001:2",
                "response": {"body": {"huntResult": {
                    "foundMain": True,
                    "contribution": 3,
                    "score": 88,
                    "loot": [{"name": "凝血草", "quantity": 5}, {"name": "灵石", "qty": 39}],
                }}},
            }])
            self._write(root, "fishing", day, [{
                "ok": True,
                "step_key": "result",
                "response": {"body": {"result": {
                    "ready": True,
                    "sessionId": "fish-1",
                    "caught": True,
                    "fish": {"name": "银须灵鲢"},
                    "expGain": 4,
                    "bonusLoot": [{"name": "幸运符", "qty": 1}],
                }}},
            }])
            self._write(root, "tree", day, [{
                "ok": True,
                "step_key": "run_submit",
                "response": {"body": {"run": {"mode": "fly", "score": 126}, "score": 126}},
            }])
            self._write(root, "stargazer", day, [{
                "ok": True,
                "step_key": "action_soothe",
                "response": {"body": {"actionResult": {"score": 3}}},
            }])

            report = miniapp_daily_report.build_report(day, root)

        self.assertIn("银须灵鲢x1", report)
        self.assertIn("幸运符x1", report)
        self.assertIn("天机残痕+9", report)
        self.assertIn("经验+12", report)
        self.assertIn("天机砂x2", report)
        self.assertIn("洞府贡献+3", report)
        self.assertIn("凝血草x5", report)
        self.assertIn("灵石x39", report)
        self.assertIn("fly1次｜暂无物资入账", report)
        self.assertIn("soothe1次｜暂无收集物资", report)
        self.assertNotIn("secret-session", report)
        self.assertNotIn("HTTP", report)
        self.assertNotIn("session", report)
        self.assertNotIn("score", report)
        self.assertNotIn("最佳", report)


if __name__ == "__main__":
    unittest.main()
