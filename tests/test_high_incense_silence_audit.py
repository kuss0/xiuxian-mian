import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import high_incense_silence_audit


TZ_LOCAL = timezone(timedelta(hours=8))


class HighIncenseSilenceAuditTests(unittest.TestCase):
    def _build_db(self, path):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE identities (
                send_as_id INTEGER PRIMARY KEY, username TEXT, label TEXT, enabled INTEGER
            );
            CREATE TABLE identity_module_state (
                send_as_id INTEGER PRIMARY KEY,
                small_world_enabled INTEGER,
                small_world_barrier_min_stock INTEGER,
                deep_retreat_enabled INTEGER,
                yuanying_enabled INTEGER,
                second_soul_enabled INTEGER,
                stargazer_enabled INTEGER,
                fishing_enabled INTEGER,
                tree_enabled INTEGER,
                pet_enabled INTEGER,
                pet_warm_enabled INTEGER,
                pet_trial_enabled INTEGER,
                pet_formation_enabled INTEGER,
                tianti_enabled INTEGER,
                checkin_enabled INTEGER,
                sect_teach_enabled INTEGER,
                tower_enabled INTEGER,
                quiz_enabled INTEGER,
                jiyin_enabled INTEGER,
                concubine_enabled INTEGER,
                nanlong_enabled INTEGER,
                ranch_enabled INTEGER,
                wild_training_enabled INTEGER,
                search_node_enabled INTEGER,
                wendao_enabled INTEGER,
                formation_enabled INTEGER,
                explore_rift_enabled INTEGER,
                duel_enabled INTEGER,
                mulan_enabled INTEGER
            );
            CREATE TABLE identity_runtime_state (
                send_as_id INTEGER PRIMARY KEY,
                small_world_incense_stock INTEGER,
                small_world_last_panel_at REAL
            );
            CREATE TABLE identity_timers (
                send_as_id INTEGER PRIMARY KEY,
                next_deep_retreat_time REAL,
                next_yuanying_time REAL,
                next_second_soul_time REAL,
                next_stargazer_panel_time REAL,
                next_fishing_time REAL,
                next_irr_time REAL,
                next_guard_time REAL,
                next_pet_time REAL,
                next_pet_warm_time REAL,
                next_pet_trial_time REAL,
                next_pet_formation_time REAL,
                next_tianti_status_time REAL,
                next_checkin_time REAL,
                next_sect_teach_time REAL,
                next_tower_time REAL,
                next_quiz_time REAL,
                next_jiyin_time REAL,
                next_concubine_time REAL,
                next_nanlong_time REAL,
                next_small_world_time REAL,
                next_ranch_time REAL,
                next_wild_training_time REAL,
                next_search_node_time REAL,
                next_wendao_time REAL,
                next_formation_time REAL,
                next_explore_rift_time REAL,
                next_duel_time REAL,
                next_mulan_time REAL
            );
            """
        )
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ_LOCAL).timestamp()
        config = {
            "cave_public_entry_urls": ["https://example.invalid/startapp=df_redacted"],
            "cave_public_deep_status_enabled": True,
            "cave_public_stargazer_enabled": True,
            "cave_public_fishing_enabled": True,
            "cave_public_fishing_identity_ids": [1001],
        }
        connection.execute("INSERT INTO meta VALUES ('miniapp_auto_config', ?)", (json.dumps(config),))
        for identity_id, stock in ((1001, 150000), (1002, 5000)):
            connection.execute("INSERT INTO identities VALUES (?, ?, ?, 1)", (identity_id, f"u{identity_id}", f"i{identity_id}"))
            flags = [identity_id, 1, 130000] + [1] * 26
            connection.execute(
                f"INSERT INTO identity_module_state VALUES ({','.join('?' for _ in flags)})",
                flags,
            )
            connection.execute("INSERT INTO identity_runtime_state VALUES (?, ?, ?)", (identity_id, stock, now - 60))
            timers = [identity_id] + [now + 3600] * 28
            connection.execute(
                f"INSERT INTO identity_timers VALUES ({','.join('?' for _ in timers)})",
                timers,
            )
        connection.commit()
        connection.close()

    def test_audit_marks_only_high_stock_identity_active_and_forecasts_policy(self):
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ_LOCAL)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            self._build_db(db_path)
            report = high_incense_silence_audit.build_audit(db_path, tmp, now=now)

        self.assertEqual(1, report["summary"]["active_silence_identities"])
        high = next(row for row in report["identities"] if row["identity_id"] == 1001)
        low = next(row for row in report["identities"] if row["identity_id"] == 1002)
        self.assertTrue(high["silence_active"])
        self.assertFalse(low["silence_active"])
        decisions = {row["module"]: row["decision"] for row in high["forecast"]}
        self.assertEqual("allow_conditional", decisions["深度闭关"])
        self.assertEqual("miniapp_only", decisions["观星台"])
        self.assertEqual("miniapp_only", decisions["灵溪垂钓"])
        self.assertEqual("block", decisions["野外历练"])

    def test_recent_blocked_send_is_violation_only_for_active_identity(self):
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ_LOCAL)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            self._build_db(db_path)
            path = Path(tmp) / "2026-07-15.log"
            rows = [
                {"ts": "2026-07-15 11:00:00 UTC+8", "event_type": "sent", "sender_id": 1001, "message_id": 10, "text": ".野外历练 深入"},
                {"ts": "2026-07-15 11:05:00 UTC+8", "event_type": "sent", "sender_id": 1001, "message_id": 11, "text": ".深度闭关"},
                {"ts": "2026-07-15 11:10:00 UTC+8", "event_type": "sent", "sender_id": 1002, "message_id": 12, "text": ".斗法 @target"},
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
            report = high_incense_silence_audit.build_audit(db_path, tmp, now=now)

        self.assertEqual(1, report["summary"]["active_violations"])
        self.assertEqual(10, report["active_violations"][0]["message_id"])

    def test_command_classifier_keeps_status_queries_out_of_long_cd_whitelist(self):
        self.assertEqual("allow_long_cd", high_incense_silence_audit.classify_command(".元婴闭关"))
        self.assertEqual("block_status_probe", high_incense_silence_audit.classify_command(".元婴状态"))
        self.assertEqual("block_status_probe", high_incense_silence_audit.classify_command(".查看闭关"))
        self.assertEqual("review_chain", high_incense_silence_audit.classify_command(".抉择 稳固道心"))


if __name__ == "__main__":
    unittest.main()
