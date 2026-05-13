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

from model.features.tiandao_judgement import parse_tiandao_judgement_prompt


class TiandaoJudgementTests(unittest.TestCase):
    def test_parse_mod_arithmetic_with_token(self):
        text = (
            "🚨 【天道审判 · 挂机嫌疑】 🚨\n\n"
            "对象 【march_7777】，你被 【anderbowie】 举报使用了自动化傀儡法术（外挂脚本）！\n"
            "天道已向你降下迷障！你必须在 3分钟 内破除迷障，证明自己是拥有独立灵智的活人！\n\n"
            "文本题面：\n"
            "请直接计算：计算：(890+24×9) 除以 31 的余数 = ?\n"
            "🔐 本轮阵眼口令：U9EX\n\n"
            "👇 自证方式：\n"
            "发送：.自证 <阵眼口令> <答案>"
        )

        parsed = parse_tiandao_judgement_prompt(text)

        self.assertIsNotNone(parsed)
        self.assertEqual("march_7777", parsed["target"])
        self.assertEqual("U9EX", parsed["token"])
        self.assertEqual("21", parsed["answer"])


if __name__ == "__main__":
    unittest.main()
