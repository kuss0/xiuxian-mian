import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from model.config import TZ_LOCAL
from model.features import rare_daily_report


def _items(events):
    return {event.item: event.count for event in events}


class RareDailyReportParserTest(unittest.TestCase):
    def test_counts_positive_production_texts(self):
        samples = [
            (
                "换取成功！\n你已成功献上祭品，获得了天道认可，【昆吾通行令】已放入你的储物袋！",
                {"昆吾通行令": 1},
            ),
            (
                "【乱星海远航·归】\n侍妾【白瑶怡】已自 冒险 航线归来，向你呈上收获：\n"
                "- 修为 +405\n- 灵石 +97\n- 阴凝之晶 x1\n- 养魂木 x4",
                {"阴凝之晶": 1},
            ),
            (
                "【苍坤残图·拼合成功】\n侍妾【白瑶怡】为你拼齐残图。\n你获得：苍坤残图 x1、修为 +635。",
                {"苍坤残图": 1},
            ),
            (
                "【入梦寻图】\n本次梦兆锁定：【虚天残图】 线路。\n"
                "你与侍妾【霓裳】共梦乱星海，获得 【虚天残图】 残纹 西极残纹（新残纹）。",
                {"虚天残图残纹": 1},
            ),
            (
                "📜 修士 @bzyyds001 元神归窍总结\n你的元婴在虚空中神游八小时，带回了以下收获：\n"
                "\n - 【法则碎片·土】x2\n - 【四级妖丹】x2",
                {"法则碎片·土": 2},
            ),
            (
                "【野外历练 · 灵机暗藏】\n获得修为 +12000，获得 【天凤之翎】x1。",
                {"天凤之翎": 1},
            ),
        ]
        for text, expected in samples:
            with self.subTest(text=text[:24]):
                self.assertEqual(expected, _items(rare_daily_report.parse_rare_material_events_from_text(text)))

    def test_counts_tower_summary_without_floor_double_count(self):
        text = (
            "【试炼古塔 - 战报】\n"
            "- 第 10 层: ✨ 击败强敌，额外缴获【空间之核】x1！\n"
            "\n总收获:\n"
            " - 获得了【法则碎片·空间】x1\n"
            " - 获得了【空间之核】x2\n"
            "\n本次塔相轨迹: 机缘层x1"
        )
        self.assertEqual(
            {"法则碎片·空间": 1, "空间之核": 2},
            _items(rare_daily_report.parse_rare_material_events_from_text(text)),
        )

    def test_counts_replica_settlement_rare_rewards(self):
        text = (
            "昆吾山结算：登顶昆吾山｜已清理轻量房间记录。\n\n"
            "结算成果：\n【登顶昆吾山】\n"
            "- 队长 @jihejish 获得登顶至宝 【大挪移令】x1\n"
            "- 队长 @jihejish 在废机关台中取出 【铁甲战傀图谱】x1\n"
            "兜底命令：\n.查询副本"
        )
        self.assertEqual(
            {"大挪移令": 1, "铁甲战傀图谱": 1},
            _items(rare_daily_report.parse_rare_material_events_from_text(text)),
        )

    def test_ignores_snapshots_trade_cost_return_and_reward_catalog(self):
        samples = [
            "@xxx 的储物袋\n\n材料:\n- 阴凝之晶 x 5\n- 昆吾通行令 x 2",
            "【赠送成功】\n道友 @a 向 @b 赠送了 【阴凝之晶】x1。",
            "因副本未曾开启，天道已将【虚天残图】归还至你的储物袋中。",
            "【坠魔谷奖励一览】\n- 【阴凝之晶】x2（幸运掉落，权重 18，约 16.2%）",
            "inventory give 721558145 虚天残图*1.虚天殿 1421",
            "📦 物资统计: 阴凝之晶\n📊 总计: 9",
        ]
        for text in samples:
            with self.subTest(text=text[:24]):
                self.assertEqual([], rare_daily_report.parse_rare_material_events_from_text(text))


class RareDailyReportBuildTest(unittest.TestCase):
    def _write_log(self, directory, name, rows):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def test_build_daily_report_reads_main_and_replica_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_log(
                tmpdir,
                "2026-06-11.log",
                [
                    {"message_id": 1, "chat_id": 1, "event_type": "message", "text": "@x 的储物袋\n- 阴凝之晶 x 9"},
                    {"message_id": 2, "chat_id": 1, "event_type": "message", "text": "换取成功！【昆吾通行令】已放入你的储物袋！"},
                    {
                        "message_id": 3,
                        "chat_id": 1,
                        "event_type": "message",
                        "text": "【虚天残图·拼合成功】\n你获得：虚天残图 x1、修为 +605。",
                    },
                    {
                        "message_id": 4,
                        "chat_id": 1,
                        "event_type": "message",
                        "text": "【入梦寻图】\n你与侍妾共梦乱星海，获得 【虚天残图】 残纹 东离残纹（新残纹）。",
                    },
                ],
            )
            self._write_log(
                tmpdir,
                "replica-2026-06-11.log",
                [
                    {
                        "message_id": 5,
                        "chat_id": 2,
                        "event_type": "sent",
                        "text": "结算成果：\n【登顶昆吾山】\n队长获得登顶至宝 【大挪移令】x1",
                    }
                ],
            )
            report = rare_daily_report.build_daily_rare_report("2026-06-11", messages_dir=tmpdir)
        self.assertEqual(1, report["counts"].get("昆吾通行令"))
        self.assertEqual(1, report["counts"].get("虚天残图"))
        self.assertEqual(1, report["counts"].get("虚天残图残纹"))
        self.assertEqual(1, report["counts"].get("大挪移令"))
        self.assertNotIn("阴凝之晶", report["counts"])

    def test_format_daily_report_is_concise(self):
        text = rare_daily_report.format_daily_rare_report(
            {
                "day": "2026-06-11",
                "counts": {
                    "阴凝之晶": 2,
                    "昆吾通行令": 1,
                    "虚天残图": 3,
                    "苍坤残图": 4,
                    "虚天残图残纹": 5,
                    "法则碎片·空间": 6,
                },
            }
        )
        self.assertIn("重点：阴凝之晶 +2｜昆吾令 +1｜虚天残图 +3｜苍坤残图 +4", text)
        self.assertIn("残纹：虚天残纹 +5", text)
        self.assertIn("其他：法则碎片·空间 +6", text)

    def test_build_daily_report_filters_to_configured_identity_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_log(
                tmpdir,
                "2026-06-11.log",
                [
                    {"message_id": 10, "chat_id": 1, "sender_id": 111, "event_type": "message", "text": ".远航归来"},
                    {
                        "message_id": 11,
                        "chat_id": 1,
                        "sender_id": 8888,
                        "reply_to_msg_id": 10,
                        "event_type": "message",
                        "text": "【乱星海远航·归】\n- 阴凝之晶 x1",
                    },
                    {"message_id": 20, "chat_id": 1, "sender_id": 222, "event_type": "message", "text": ".远航归来"},
                    {
                        "message_id": 21,
                        "chat_id": 1,
                        "sender_id": 8888,
                        "reply_to_msg_id": 20,
                        "event_type": "message",
                        "text": "【乱星海远航·归】\n- 阴凝之晶 x1",
                    },
                    {
                        "message_id": 30,
                        "chat_id": 1,
                        "sender_id": 8888,
                        "event_type": "message",
                        "text": "📜 修士 @ours 元神归窍总结\n - 【法则碎片·土】x2",
                    },
                    {
                        "message_id": 31,
                        "chat_id": 1,
                        "sender_id": 8888,
                        "event_type": "message",
                        "text": "📜 修士 @other 元神归窍总结\n - 【法则碎片·土】x2",
                    },
                ],
            )
            with patch.object(rare_daily_report, "get_identity_ids", return_value=[111]), \
                    patch.object(rare_daily_report, "get_send_as_profile", return_value={"username": "ours"}):
                report = rare_daily_report.build_daily_rare_report("2026-06-11", messages_dir=tmpdir)
        self.assertEqual(1, report["counts"].get("阴凝之晶"))
        self.assertEqual(2, report["counts"].get("法则碎片·土"))


class RareDailyReportSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_sends_once_after_21(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages_dir = os.path.join(tmpdir, "messages")
            state_dir = os.path.join(tmpdir, "state")
            os.makedirs(messages_dir)
            os.makedirs(state_dir)
            with open(os.path.join(messages_dir, "2026-06-11.log"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"message_id": 1, "chat_id": 1, "text": "换取成功！【昆吾通行令】已放入你的储物袋！"}, ensure_ascii=False) + "\n")
            send_mock = AsyncMock(return_value=True)
            now = datetime(2026, 6, 11, 21, 0, 0, tzinfo=TZ_LOCAL).timestamp()
            with patch.object(rare_daily_report, "MESSAGES_DIR", messages_dir), \
                    patch.object(rare_daily_report, "STATE_FILE", os.path.join(state_dir, "rare_daily_report_state.json")), \
                    patch.object(rare_daily_report, "_report_state_loaded", False), \
                    patch.object(rare_daily_report, "_report_state", {}), \
                    patch.object(rare_daily_report, "_last_sent_day_memory", ""), \
                    patch.object(rare_daily_report, "_next_retry_at", 0.0), \
                    patch.object(rare_daily_report, "send_audit_log", new=send_mock):
                self.assertTrue(await rare_daily_report.run_rare_daily_report_scheduler(now))
                self.assertFalse(await rare_daily_report.run_rare_daily_report_scheduler(now + 5))
            self.assertEqual(1, send_mock.await_count)
