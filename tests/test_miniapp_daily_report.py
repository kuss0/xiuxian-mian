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
        self.assertNotIn("fly1次", report)
        self.assertNotIn("soothe1次", report)
        self.assertNotIn("暂无物资入账", report)
        self.assertNotIn("暂无收集物资", report)
        self.assertNotIn("secret-session", report)
        self.assertNotIn("HTTP", report)
        self.assertNotIn("session", report)
        self.assertNotIn("score", report)
        self.assertNotIn("最佳", report)

    def test_report_counts_reused_capture_source_and_common_gain_aliases(self):
        day = "2026-07-08"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "trial", day, [
                {
                    "ok": True,
                    "step_key": "finish",
                    "source": "trial_runtime:1001:5001",
                    "response": {"body": {"result": {
                        "traceGain": 2,
                        "expGain": 3,
                        "items": {"灵脉砂": 1},
                        "score": 91,
                    }}},
                },
                {
                    "ok": True,
                    "step_key": "finish",
                    "source": "trial_runtime:1001:5001",
                    "response": {"body": {"result": {
                        "traceGain": 4,
                        "experienceGain": 5,
                        "bonusLoot": [{"name": "玄晶", "qty": 2}],
                        "sessionId": "same-entry-second-round",
                    }}},
                },
            ])
            self._write(root, "cave_treasure", day, [
                {
                    "ok": True,
                    "step_key": "action:settle",
                    "source": "cave_treasure_runtime:1001:6001",
                    "response": {"body": {"huntResult": {
                        "foundMain": True,
                        "cultivationGain": 10,
                        "lingshiGain": 20,
                        "contributionGain": 1,
                        "item_deltas": {"凝血草": 2},
                        "score": 88,
                    }}},
                },
                {
                    "ok": True,
                    "step_key": "action:settle",
                    "source": "cave_treasure_runtime:1001:6001",
                    "response": {"body": {"huntResult": {
                        "foundMain": False,
                        "xiuweiGain": 30,
                        "stoneGain": 40,
                        "contribution": 2,
                        "loot": [{"name": "古禁印痕", "quantity": 1}],
                        "score": 88,
                    }}},
                },
            ])
            self._write(root, "stargazer", day, [{
                "ok": True,
                "step_key": "action_collect",
                "response": {"body": {"actionResult": {
                    "item_deltas": {"星辰精华": 3},
                    "score": 3,
                }}},
            }])

            report = miniapp_daily_report.build_report(day, root)

        self.assertIn("🧪 天机试炼：2次成功", report)
        self.assertIn("天机残痕+6", report)
        self.assertIn("经验+8", report)
        self.assertIn("灵脉砂x1", report)
        self.assertIn("玄晶x2", report)
        self.assertIn("🕳️ 洞府寻宝：2局", report)
        self.assertIn("主宝1", report)
        self.assertIn("修为+40", report)
        self.assertIn("灵石+60", report)
        self.assertIn("洞府贡献+3", report)
        self.assertIn("凝血草x2", report)
        self.assertIn("古禁印痕x1", report)
        self.assertIn("🔭 观星台：奖励:星辰精华x3", report)
        self.assertNotIn("score", report)
        self.assertNotIn("session", report)

    def test_report_reads_business_records_from_shape_only_capture(self):
        day = "2026-07-29"
        protocol_record = {
            "ok": True,
            "step_key": "result",
            "response": {"body_shape": {"type": "object", "keys": ["ok", "result"]}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "fishing", day, [
                protocol_record,
                {
                    "ok": True,
                    "step_key": "business:fishing",
                    "business": {
                        "settled_count": 2,
                        "rods": 2,
                        "caught": 1,
                        "empty": 1,
                        "catches": [{"fish": "银须灵鲢", "grade": "珍稀", "weight": "2.8斤"}],
                        "gains": {"钓术经验": 4},
                        "items": [{"name": "幸运符", "qty": 1}],
                    },
                },
            ])
            self._write(root, "trial", day, [{
                "ok": True,
                "step_key": "business:trial",
                "business": {
                    "settled_count": 2,
                    "gains": {"天机残痕": 6, "经验": 8},
                    "items": {"天机砂": 2},
                },
            }])
            self._write(root, "cave_treasure", day, [{
                "ok": True,
                "step_key": "business:cave_treasure",
                "business": {
                    "settled_count": 3,
                    "found_main": 1,
                    "gains": {"贡献": 5, "修为": 40},
                    "items": {"凝血草": 2},
                },
            }])
            self._write(root, "stargazer", day, [{
                "ok": True,
                "step_key": "business:stargazer",
                "business": {"collect_count": 1, "items": {"星辰精华": 3}},
            }])
            self._write(root, "tree", day, [{
                "ok": True,
                "step_key": "business:tree",
                "business": {
                    "settled_count": 6,
                    "gains": {"经验": 7},
                    "items": {"灵树枝": 2},
                },
            }])

            report = miniapp_daily_report.build_report(day, root)

        self.assertIn("🎣 灵溪垂钓：2竿｜中鱼1｜空竿1", report)
        self.assertIn("银须灵鲢x1", report)
        self.assertIn("钓术经验+4", report)
        self.assertIn("幸运符x1", report)
        self.assertIn("🧪 天机试炼：2次成功", report)
        self.assertIn("天机残痕+6", report)
        self.assertIn("天机砂x2", report)
        self.assertIn("🕳️ 洞府寻宝：3局｜主宝1", report)
        self.assertIn("贡献+5", report)
        self.assertIn("凝血草x2", report)
        self.assertIn("🔭 观星台：奖励:星辰精华x3", report)
        self.assertIn("🌳 灵树：收益:经验+7｜奖励:灵树枝x2", report)
        self.assertNotIn("body_shape", report)
        self.assertNotIn("score", report)
        self.assertNotIn("session", report)

    def test_tree_report_omits_score_only_runs_but_keeps_real_rewards(self):
        day = "2026-07-08"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "tree", day, [
                {
                    "ok": True,
                    "step_key": "fly:run_submit",
                    "response": {"body": {
                        "run": {"mode": "fly", "score": 126},
                        "score": 126,
                        "verified": {"score": 126},
                    }},
                },
                {
                    "ok": True,
                    "step_key": "jump:run_submit",
                    "response": {"body": {
                        "run": {"mode": "jump"},
                        "result": {
                            "expGain": 7,
                            "item_deltas": {"灵树枝": 2},
                        },
                    }},
                },
            ])

            report = miniapp_daily_report.build_report(day, root)

        self.assertIn("🌳 灵树：收益:经验+7｜奖励:灵树枝x2", report)
        self.assertNotIn("fly1次", report)
        self.assertNotIn("jump1次", report)
        self.assertNotIn("score", report)
        self.assertNotIn("verified", report)


if __name__ == "__main__":
    unittest.main()
