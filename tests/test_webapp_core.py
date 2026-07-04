import json
import unittest

from model import webapp_core


class WebAppCoreTests(unittest.TestCase):
    def test_summarize_webapp_url_redacts_start_param_and_init_data(self):
        summary = webapp_core.summarize_webapp_url(
            "https://example.com/app?startapp=stk_SECRET9999#tgWebAppData=query_id%3Dabc%26hash%3Dhidden",
            button_text="打开验证",
            message_text="天道审判 Mini App",
        )

        self.assertEqual("example.com", summary["host"])
        self.assertEqual("tiandao_judgement", summary["game_hint"])
        self.assertTrue(summary["has_start_param"])
        self.assertTrue(summary["has_sensitive_init_data"])
        self.assertEqual("stk", summary["start_param"]["kind"])
        self.assertEqual("9999", summary["start_param"]["suffix"])
        self.assertEqual("startapp", summary["start_param"]["key"])
        self.assertIn("tgWebAppData", summary["sensitive_keys"])
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("stk_SECRET9999", serialized)
        self.assertNotIn("query_id%3Dabc", serialized)
        self.assertNotIn("hidden", serialized)

    def test_summarize_webapp_url_detects_world_boss_without_token(self):
        summary = webapp_core.summarize_webapp_url(
            "https://boss.example/app",
            button_text="进入世界BOSS",
            message_text="真仙试锋已开启",
        )

        self.assertEqual("world_boss", summary["game_hint"])
        self.assertFalse(summary["has_start_param"])
        self.assertFalse(summary["has_sensitive_init_data"])


if __name__ == "__main__":
    unittest.main()

