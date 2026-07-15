import unittest

from model.timing import parse_wait_time


class TimingWaitTests(unittest.TestCase):
    def test_explicit_remaining_wait_wins_over_nominal_cooldown(self):
        text = "咒源剥离牵涉神魂反噬，不可连续施展。剥离咒源 冷却 8 小时，请在 6小时57分钟51秒 后再试。"
        self.assertEqual(6 * 3600 + 57 * 60 + 51, parse_wait_time(text))

    def test_wait_clause_variants(self):
        self.assertEqual(1 * 3600 + 2 * 60 + 3, parse_wait_time("请在 1小时2分钟3秒 后再来。"))
        self.assertEqual(2 * 3600 + 14 * 60 + 9, parse_wait_time("凡间方才承受神谕，需再等待 2小时14分钟9秒。"))
        self.assertEqual(5 * 60 + 6, parse_wait_time("剩余时间：5分6秒"))

    def test_nominal_duration_without_wait_marker_is_preserved(self):
        self.assertEqual(8 * 3600, parse_wait_time("此推命将在 8 小时 内生效。"))


if __name__ == "__main__":
    unittest.main()
