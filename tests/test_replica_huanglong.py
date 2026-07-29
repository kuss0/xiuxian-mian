import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from model.features import replica_huanglong


class ReplicaHuanglongTests(unittest.TestCase):
    def _context(self, *, send_result=None):
        storage = {}

        def get_run_state():
            return storage

        def save_run_state(value):
            snapshot = dict(value)
            storage.clear()
            storage.update(snapshot)

        context = replica_huanglong.HuanglongConscriptionContext(
            tz_local=timezone.utc,
            query_command=".黄龙山轮值",
            query_hour=12,
            query_minute=5,
            retry_interval_sec=3600,
            get_run_state=get_run_state,
            save_run_state=save_run_state,
            get_participant_identity_ids=lambda: [1001, 1002],
            get_identity_ids=lambda: [1002, 1003],
            get_identity_enabled=lambda identity_id: identity_id != 1001,
            get_identity_account=lambda identity_id: identity_id + 9000,
            is_account_offline=lambda account_id: account_id == 10002,
            send_audit_log=AsyncMock(return_value=True),
            send_game_command=AsyncMock(return_value=send_result),
        )
        return context, storage

    def test_parse_and_handle_conscription_notice_once(self):
        context, storage = self._context()
        text = (
            "【黄龙山轮值军报】\n"
            "黄龙山宗门征调 · 2026-07-29\n"
            "轮值宗门为【落云宗】\n"
            "当前阶段：征集中\n"
            "当前报名总数：3 人 / 可报名总数 8 人"
        )

        first = asyncio.run(replica_huanglong.handle_conscription_text(context, text, now=1000.0))
        duplicate = asyncio.run(replica_huanglong.handle_conscription_text(context, text, now=1001.0))

        self.assertTrue(first)
        self.assertFalse(duplicate)
        context.send_audit_log.assert_awaited_once()
        record = storage["huanglong_conscription"]["notified_days"]["2026-07-29"]
        self.assertEqual("落云宗", record["sect"])
        self.assertEqual(3, record["signup_count"])
        self.assertEqual(8, record["signup_total"])

    def test_scheduler_records_unknown_send_attempt_without_immediate_retry(self):
        context, storage = self._context(send_result=None)
        now = datetime(2026, 7, 29, 12, 6, tzinfo=timezone.utc).timestamp()

        first = asyncio.run(replica_huanglong.run_conscription_scheduler(context, now))
        duplicate = asyncio.run(replica_huanglong.run_conscription_scheduler(context, now + 60))

        self.assertEqual(0, first)
        self.assertEqual(0, duplicate)
        context.send_game_command.assert_awaited_once()
        day_state = storage["huanglong_conscription"]
        self.assertIn("2026-07-29", day_state["query_attempts"])
        self.assertNotIn("2026-07-29", day_state["query_sent_days"])

    def test_scheduler_marks_success_and_skips_same_day(self):
        now = datetime(2026, 7, 29, 12, 6, tzinfo=timezone.utc).timestamp()
        context, storage = self._context(send_result=SimpleNamespace(id=19, sent_at=now))

        first = asyncio.run(replica_huanglong.run_conscription_scheduler(context, now))
        duplicate = asyncio.run(replica_huanglong.run_conscription_scheduler(context, now + 7200))

        self.assertEqual(1, first)
        self.assertEqual(0, duplicate)
        context.send_game_command.assert_awaited_once()
        self.assertEqual(now, storage["huanglong_conscription"]["query_sent_days"]["2026-07-29"])


if __name__ == "__main__":
    unittest.main()
