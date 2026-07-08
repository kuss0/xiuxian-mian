import unittest

from model.features import tree_runtime


class TreeRuntimeSummaryTests(unittest.TestCase):
    def test_tree_success_summary_does_not_report_scores_as_rewards(self):
        summary = tree_runtime._format_tree_summary({
            "ok": True,
            "status": "settled",
            "data": {
                "mode": "jump",
                "proof_summary": {"score": 30, "targetScore": 30},
                "submit": {"score": 30},
            },
        })

        self.assertIn("MiniApp settled", summary)
        self.assertIn("跳一跳", summary)
        self.assertIn("未解析到新增物资", summary)
        self.assertNotIn("分数", summary)
        self.assertNotIn("目标", summary)
        self.assertNotIn("30", summary)

    def test_tree_mode_exhausted_summary_omits_best_score(self):
        summary = tree_runtime._format_tree_summary({
            "ok": False,
            "status": "mode_exhausted",
            "data": {
                "mode": "fly",
                "state": {
                    "jump": {"used": 3, "limit": 3, "best": 126},
                    "fly": {"used": 1, "limit": 1, "best": 88},
                },
            },
        })

        self.assertIn("飞一飞次数已用完", summary)
        self.assertIn("跳一跳 3/3", summary)
        self.assertIn("飞一飞 1/1", summary)
        self.assertNotIn("best", summary)
        self.assertNotIn("126", summary)
        self.assertNotIn("88", summary)


if __name__ == "__main__":
    unittest.main()
