import time
import unittest
import tempfile
import sqlite3
from unittest.mock import patch

from tools import safety_watchdog


def _event(epoch, sender_id, text, reply_to_msg_id=0, family="", source_module="", priority="", op_id="", chain_id="", message_id=0):
    payload = {
        "event_type": "sent",
        "_epoch": float(epoch),
        "sender_id": int(sender_id),
        "text": text,
    }
    if message_id:
        payload["message_id"] = int(message_id)
    if reply_to_msg_id:
        payload["reply_to_msg_id"] = int(reply_to_msg_id)
    if family:
        payload["family"] = family
    if source_module:
        payload["source_module"] = source_module
    if priority:
        payload["priority"] = priority
    if op_id:
        payload["op_id"] = op_id
    if chain_id:
        payload["chain_id"] = chain_id
    return payload


def _world_boss_action_op_id(event_key, identity_id, action, action_seq, try_no):
    return f"world_boss:{event_key}:action:{identity_id}:{action}:{action_seq}:try{try_no}"


def _world_boss_status_op_id(event_key, identity_id, try_no, sent_ts):
    return f"world_boss:{event_key}:status:{identity_id}:try{try_no}:{int(sent_ts)}"


class SafetyWatchdogTests(unittest.TestCase):
    def _config(self):
        return safety_watchdog.WatchdogConfig(
            project_root=safety_watchdog.Path("/tmp/xiuxian-test"),
            service_name="xiuxian",
            interval_sec=15,
            action="stop",
            dry_run=True,
            max_lines=1000,
            min_any_gap_sec=0,
            total_2m_limit=99,
            total_5m_limit=99,
            total_15m_limit=99,
            same_command_gap_sec=60,
            guarded_repeat_gap_sec=90,
            guarded_max_attempts_45m=99,
            guarded_fourth_min_span_sec=14 * 60,
            refresh_repeat_gap_sec=4 * 60,
            refresh_max_attempts_90m=10,
            journal_check_interval_sec=60,
        )

    def test_small_world_tool_chain_does_not_fuse_repeat_query(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".小世界"),
            _event(now - 90, sender_id, ".收割香火"),
            _event(now - 64, sender_id, ".小世界"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_check_once_reports_legacy_xiuxian_process_before_log_scan(self):
        cfg = self._config()
        with patch.object(
            safety_watchdog,
            "find_legacy_xiuxian_processes",
            return_value=[{"pid": 123, "cmdline": "/opt/xiuxian/xiuxian.py"}],
        ):
            breach = safety_watchdog.check_once(cfg)

        self.assertIn("legacy xiuxian process: pid=123", breach)

    def test_small_world_direct_repeat_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".小世界"),
            _event(now - 64, sender_id, ".小世界"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("same command repeat", breach)

    def test_marked_small_world_refresh_chain_allows_short_repeat(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 90, sender_id, ".小世界", family="small_world_query", source_module="小世界", priority="chain"),
            _event(now, sender_id, ".小世界", family="small_world_query", source_module="小世界", priority="chain"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_marked_small_world_refresh_chain_does_not_cap_refresh_attempts(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(
                now - index * 60,
                sender_id,
                ".小世界",
                family="small_world_query",
                source_module="小世界",
                priority="chain",
            )
            for index in range(11)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_marked_small_world_refresh_chain_ignores_legacy_unmarked_query(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 11 * 60, sender_id, ".小世界"),
            *[
                _event(
                    now - index * 60,
                    sender_id,
                    ".小世界",
                    family="small_world_query",
                    source_module="小世界",
                    priority="chain",
                )
                for index in range(11)
            ],
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_unmarked_small_world_refresh_chain_still_caps_attempts(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - index * 5 * 60, sender_id, ".小世界")
            for index in range(11)
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("refresh command over attempts", breach)

    def test_reset_marker_filters_old_sent_events(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".加入副本 394"),
            _event(now - 90, sender_id, ".加入副本 394"),
            _event(now + 10, sender_id, ".小世界"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            marker_dir = root / "data" / "state"
            marker_dir.mkdir(parents=True)
            (marker_dir / "safety_watchdog_reset.json").write_text(
                safety_watchdog.json.dumps({"reset_at_epoch": now}),
                encoding="utf-8",
            )
            reset_after = safety_watchdog.get_reset_after_epoch(root)
            filtered = [
                item for item in events
                if float(item.get("_epoch", 0) or 0) >= reset_after
            ]

        self.assertEqual(1, len(filtered))
        self.assertEqual("", safety_watchdog.find_send_breach(filtered, now, self._config()))

    def test_read_recent_log_lines_tolerates_invalid_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = safety_watchdog.Path(tmpdir) / "2026-06-16.log"
            payload = {"ts": "2026-06-16 23:30:00", "event_type": "sent", "sender_id": 301299112, "text": ".小世界"}
            log_path.write_bytes(b"\xe5\n" + safety_watchdog.json.dumps(payload).encode("utf-8") + b"\n")

            rows = safety_watchdog.read_recent_log_lines(log_path, 10)

        self.assertEqual(1, len(rows))
        self.assertEqual(".小世界", rows[0]["text"])

    def test_dungeon_join_repeat_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 30, sender_id, ".加入副本 394"),
            _event(now - 28, sender_id, ".加入副本 394"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 0

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_dungeon_dissolve_open_gap_is_not_global_lock_breach(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 10, 3765328695, ".解散副本"),
            _event(now - 2, 3711993781, ".开启虚天殿"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_dungeon_fast_chain_is_not_send_burst(self):
        now = time.time()
        events = [
            _event(now - 55, 3765328695, ".开启虚天殿"),
            _event(now - 40, 3800619925, ".加入副本 969"),
            _event(now - 39, 8659059191, ".加入副本 969"),
            _event(now - 38, 3947749189, ".加入副本 969"),
            _event(now - 37, 3943773722, ".加入副本 969"),
            _event(now - 20, 3765328695, ".选择道路 冰"),
            _event(now - 12, 3765328695, ".阵策 势"),
            _event(now - 4, 3765328695, ".后殿阵策 卦"),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_dungeon_fast_chain_only_allows_known_choice_commands(self):
        self.assertTrue(safety_watchdog.is_dungeon_fast_chain_command(".选择 岔路1"))
        self.assertTrue(safety_watchdog.is_dungeon_fast_chain_command(".选择 强行摘取"))
        self.assertTrue(safety_watchdog.is_dungeon_fast_chain_command(".选择 静待时机"))
        self.assertFalse(safety_watchdog.is_dungeon_fast_chain_command(".选择 随便"))

    def test_virtual_hall_late_stage_fast_chain_is_not_global_lock_breach(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 10, 3581351795, ".争鼎 夺鼎"),
            _event(now, 3581351795, ".后殿抉择 冲关"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_replica_button_choice_repeat_with_distinct_stage_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3943773722
        events = [
            _event(
                now - 26,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_button:10001087:3943773722:.苍坤抉择 2",
            ),
            _event(
                now,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_button:10001149:3943773722:.苍坤抉择 2",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_replica_button_choice_repeat_with_same_stage_still_fuses(self):
        now = time.time()
        sender_id = 3943773722
        events = [
            _event(
                now - 26,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_button:10001087:3943773722:.苍坤抉择 2",
            ),
            _event(
                now,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_button:10001087:3943773722:.苍坤抉择 2",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_replica_button_choice_duplicate_op_id_fuses_even_after_repeat_gap(self):
        now = time.time()
        sender_id = 3943773722
        op_id = "replica_button:10001087:3943773722:.苍坤抉择 2"
        events = [
            _event(
                now - 180,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id=op_id,
            ),
            _event(
                now,
                sender_id,
                ".苍坤抉择 2",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id=op_id,
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("duplicate replica choice op_id", breach)

    def test_kunwu_auto_choice_repeat_with_distinct_stage_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3943773722
        events = [
            _event(
                now - 8,
                sender_id,
                ".选择 岔路1",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="kunwu_auto_choice:10001087:3943773722:a1",
            ),
            _event(
                now,
                sender_id,
                ".选择 岔路1",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="kunwu_auto_choice:10001149:3943773722:b2",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_kunwu_auto_choice_duplicate_op_id_still_fuses(self):
        now = time.time()
        sender_id = 3943773722
        op_id = "kunwu_auto_choice:10001087:3943773722:a1"
        events = [
            _event(
                now - 180,
                sender_id,
                ".选择 岔路1",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id=op_id,
            ),
            _event(
                now,
                sender_id,
                ".选择 岔路1",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id=op_id,
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("duplicate replica choice op_id", breach)

    def test_replica_lightweight_open_retry_does_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3872695780
        chain_id = "replica_lightweight_open:kunwu:-1003807888644:3872695780:1780938819036"
        events = [
            _event(
                now - 4,
                sender_id,
                ".开启昆吾山",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_lightweight_open:-1003807888644:17660:3872695780",
                chain_id=chain_id,
            ),
            _event(
                now,
                sender_id,
                ".开启昆吾山",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_lightweight_open_retry:-1003807888644:17660:3872695780",
                chain_id=chain_id,
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_replica_lightweight_open_retry_different_chain_still_fuses(self):
        now = time.time()
        sender_id = 3872695780
        events = [
            _event(
                now - 4,
                sender_id,
                ".开启昆吾山",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_lightweight_open:-1003807888644:17660:3872695780",
                chain_id="replica_lightweight_open:kunwu:flow-a",
            ),
            _event(
                now,
                sender_id,
                ".开启昆吾山",
                source_module="自动副本",
                priority="urgent_reactive",
                op_id="replica_lightweight_open_retry:-1003807888644:17660:3872695780",
                chain_id="replica_lightweight_open:kunwu:flow-b",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_dungeon_open_repeat_still_fuses(self):
        now = time.time()
        events = [
            _event(now - 40, 3922509228, ".开启虚天殿"),
            _event(now - 9, 3922509228, ".开启虚天殿"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_tower_controlled_retry_does_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3504367852
        events = [
            _event(now - 55, sender_id, ".闯塔", family="tower", source_module="闯塔", priority="normal"),
            _event(now, sender_id, ".闯塔", family="tower", source_module="闯塔", priority="retry"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_tower_retry_after_normal_command_does_not_global_fuse(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 11, 8659059191, ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）", family="pet", source_module="法宝", priority="normal"),
            _event(now, 3504367852, ".闯塔", family="tower", source_module="闯塔", priority="retry"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_unmarked_tower_repeat_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        events = [
            _event(now - 55, sender_id, ".闯塔", family="tower", source_module="闯塔", priority="normal"),
            _event(now, sender_id, ".闯塔", family="tower", source_module="闯塔", priority="normal"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_phaseful_dream_replay_does_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3907536807
        old_msg_id = 10067530
        events = [
            _event(
                now - 36,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="normal",
                message_id=old_msg_id,
            ),
            _event(
                now,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="retry",
                op_id=f"phaseful_replay:{sender_id}:{old_msg_id}:.入梦寻图",
                chain_id=f"phaseful_replay:{sender_id}:{old_msg_id}",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_unmarked_dream_retry_still_fuses(self):
        now = time.time()
        sender_id = 3907536807
        events = [
            _event(
                now - 36,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="normal",
                message_id=10067530,
            ),
            _event(
                now,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="retry",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_dream_replay_wrong_message_id_still_fuses(self):
        now = time.time()
        sender_id = 3907536807
        events = [
            _event(
                now - 36,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="normal",
                message_id=10067530,
            ),
            _event(
                now,
                sender_id,
                ".入梦寻图",
                family="concubine_dream",
                source_module="侍妾",
                priority="retry",
                op_id=f"phaseful_replay:{sender_id}:10067529:.入梦寻图",
                chain_id=f"phaseful_replay:{sender_id}:10067529",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_concubine_voyage_retry_does_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3870643893
        old_msg_id = 918
        events = [
            _event(
                now - 36,
                sender_id,
                ".远航归来",
                family="concubine_voyage",
                source_module="侍妾远航",
                priority="chain",
                message_id=old_msg_id,
            ),
            _event(
                now,
                sender_id,
                ".远航归来",
                family="concubine_voyage",
                source_module="侍妾远航",
                priority="retry",
                op_id=f"concubine_voyage_retry:{sender_id}:{old_msg_id}:.远航归来",
                chain_id=f"concubine_voyage_retry:{sender_id}:{old_msg_id}",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_unmarked_concubine_voyage_retry_still_fuses(self):
        now = time.time()
        sender_id = 3870643893
        events = [
            _event(
                now - 36,
                sender_id,
                ".远航归来",
                family="concubine_voyage",
                source_module="侍妾远航",
                priority="chain",
                message_id=918,
            ),
            _event(
                now,
                sender_id,
                ".远航归来",
                family="concubine_voyage",
                source_module="侍妾远航",
                priority="retry",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_divination_daily_query_chain_does_not_guarded_fourth_fuse(self):
        now = time.time()
        sender_id = 301299112
        day_key = "2026-06-09"
        events = [
            _event(
                now - 322,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:{day_key}:1:try1",
            ),
            _event(
                now - 228,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:{day_key}:2:try2",
            ),
            _event(
                now - 116,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:{day_key}:3:try3",
            ),
            _event(
                now,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:{day_key}:4:try4",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_divination_daily_query_chain_allows_observed_count_jump_after_retry(self):
        now = time.time()
        sender_id = 3930920736
        day_key = "2026-06-09"
        targets = [1, 2, 3, 4, 4, 6]
        events = [
            _event(
                now - (len(targets) - index - 1) * 70,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:{day_key}:{target}:try{index + 1}",
            )
            for index, target in enumerate(targets)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_unmarked_divination_fourth_attempt_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        events = [
            _event(now - 322, sender_id, ".卜筮问天"),
            _event(now - 228, sender_id, ".卜筮问天"),
            _event(now - 116, sender_id, ".卜筮问天"),
            _event(now, sender_id, ".卜筮问天"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("guarded retry too dense", breach)

    def test_marked_divination_exchange_after_query_does_not_global_fuse(self):
        now = time.time()
        sender_id = 3504367852
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(
                now - 10,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:2026-06-20:2:try2",
                message_id=10616546,
            ),
            _event(
                now,
                sender_id,
                ".换取",
                reply_to_msg_id=10616549,
                family="divination_exchange",
                source_module="卜筮问天",
                priority="urgent_reactive",
                op_id="divination_exchange:-1001680975844:10616549",
                chain_id="divination:-1001680975844:10616549",
                message_id=10616552,
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_unmarked_divination_exchange_after_query_still_global_fuses(self):
        now = time.time()
        sender_id = 3504367852
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(
                now - 10,
                sender_id,
                ".卜筮问天",
                source_module="卜筮问天",
                priority="normal",
                op_id=f"divination_query:{sender_id}:2026-06-20:2:try2",
            ),
            _event(now, sender_id, ".换取"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_tower_retry_without_intent_metadata_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        events = [
            _event(now - 55, sender_id, ".闯塔", family="tower", source_module="闯塔", priority="normal"),
            _event(now, sender_id, ".闯塔", priority="retry"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_uncontrolled_short_gap_still_global_fuses(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 11, 8659059191, ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）", family="pet", source_module="法宝", priority="normal"),
            _event(now, 3504367852, ".闯塔", family="tower", source_module="闯塔", priority="normal"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_non_dungeon_commands_still_send_burst(self):
        now = time.time()
        events = [
            _event(now - 50, 1001, ".我的侍妾"),
            _event(now - 45, 1002, ".小世界"),
            _event(now - 40, 1003, ".灵树状态"),
            _event(now - 35, 1004, ".储物袋"),
            _event(now - 30, 1005, ".状态"),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_marked_world_boss_event_burst_is_not_send_burst_or_gap(self):
        now = time.time()
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12
        event_key = "test"
        events = [
            _event(
                now - (9 - index) * 0.8,
                3000 + index,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, 3000 + index, "镇魂", 1, 0),
                chain_id=f"world_boss:{event_key}",
            )
            for index in range(10)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_marked_world_boss_break_flag_opening_burst_is_not_send_burst_or_gap(self):
        now = time.time()
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12
        event_key = "test-break"
        events = [
            _event(
                now - (10 - index) * 0.8,
                3000 + index,
                ".讨伐青元子 破幡",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, 3000 + index, "破幡", 1, 0),
                chain_id=f"world_boss:{event_key}",
            )
            for index in range(11)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_unmarked_world_boss_burst_still_fuses(self):
        now = time.time()
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - (5 - index) * 0.8, 3000 + index, ".讨伐青元子 镇魂")
            for index in range(6)
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_marked_fishing_short_window_chain_is_not_send_burst_or_gap(self):
        now = time.time()
        cfg = self._config()
        cfg.total_2m_limit = 5
        cfg.min_any_gap_sec = 12
        sender_id = 8659059191
        events = [
            _event(
                now - 3,
                sender_id,
                ".钓鱼状态",
                family="fishing",
                source_module="灵溪垂钓",
                priority="urgent_reactive",
            ),
            _event(
                now - 2,
                sender_id,
                ".试探咬饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now - 1,
                sender_id,
                ".提竿",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now - 0.5,
                sender_id,
                ".收竿",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now,
                sender_id,
                ".开鱼 银须灵鲢",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_marked_fishing_basket_open_chain_does_not_fuse_same_command_repeat(self):
        now = time.time()
        cfg = self._config()
        sender_id = 3820064579
        events = [
            _event(
                now - 56,
                sender_id,
                ".鱼篓",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now - 45,
                sender_id,
                ".开鱼 银须灵鲢 5",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now - 36,
                sender_id,
                ".开鱼 青鳞小鲫 13",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
            _event(
                now,
                sender_id,
                ".鱼篓",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_unmarked_fishing_basket_repeat_still_fuses(self):
        now = time.time()
        cfg = self._config()
        sender_id = 3820064579
        events = [
            _event(now - 56, sender_id, ".鱼篓", priority="event_burst"),
            _event(now, sender_id, ".鱼篓", priority="event_burst"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("same command repeat", breach)

    def test_unmarked_fishing_short_gap_still_global_fuses(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 1, 8659059191, ".钓鱼状态", priority="urgent_reactive"),
            _event(now, 8659059191, ".提竿", priority="event_burst"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_fishing_start_command_is_not_short_window_exempt(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(
                now - 1,
                8659059191,
                ".灵树状态",
                family="tree_panel",
                source_module="灵树",
                priority="normal",
            ),
            _event(
                now,
                8659059191,
                ".钓鱼 青溪浅滩 灵米饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="event_burst",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_marked_fishing_start_after_status_progress_does_not_same_command_fuse(self):
        now = time.time()
        cfg = self._config()
        sender_id = 8659059191
        events = [
            _event(
                now - 50,
                sender_id,
                ".钓鱼 青溪浅滩 灵米饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="normal",
            ),
            _event(
                now - 35,
                sender_id,
                ".钓鱼状态",
                family="fishing",
                source_module="灵溪垂钓",
                priority="urgent_reactive",
            ),
            _event(
                now,
                sender_id,
                ".钓鱼 青溪浅滩 灵米饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="normal",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_marked_fishing_start_without_progress_still_same_command_fuses(self):
        now = time.time()
        cfg = self._config()
        sender_id = 8659059191
        events = [
            _event(
                now - 50,
                sender_id,
                ".钓鱼 青溪浅滩 灵米饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="normal",
            ),
            _event(
                now,
                sender_id,
                ".钓鱼 青溪浅滩 灵米饵",
                family="fishing",
                source_module="灵溪垂钓",
                priority="normal",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("same command repeat", breach)

    def test_unmarked_fishing_start_after_status_still_same_command_fuses(self):
        now = time.time()
        cfg = self._config()
        sender_id = 8659059191
        events = [
            _event(now - 50, sender_id, ".钓鱼 青溪浅滩 灵米饵"),
            _event(
                now - 35,
                sender_id,
                ".钓鱼状态",
                family="fishing",
                source_module="灵溪垂钓",
                priority="urgent_reactive",
            ),
            _event(now, sender_id, ".钓鱼 青溪浅滩 灵米饵"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("same command repeat", breach)

    def test_world_boss_same_action_allows_try0_try1_try2(self):
        now = time.time()
        sender_id = 301299112
        event_key = "test"
        events = [
            _event(
                now - (2 - try_no) * 0.8,
                sender_id,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, sender_id, "镇魂", 1, try_no),
                chain_id=f"world_boss:{event_key}",
            )
            for try_no in range(3)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_world_boss_duplicate_try_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        event_key = "test"
        events = [
            _event(
                now - 1,
                sender_id,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, sender_id, "镇魂", 1, 1),
                chain_id=f"world_boss:{event_key}",
            ),
            _event(
                now,
                sender_id,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, sender_id, "镇魂", 1, 1),
                chain_id=f"world_boss:{event_key}",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("duplicate world boss try", breach)

    def test_world_boss_high_retry_number_is_allowed_for_unread_reply_resend(self):
        now = time.time()
        sender_id = 301299112
        event_key = "test"
        events = [
            _event(
                now,
                sender_id,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, sender_id, "镇魂", 1, 10),
                chain_id=f"world_boss:{event_key}",
            )
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_world_boss_sixth_action_seq_within_45m_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        event_key = "test"
        action = "镇魂"
        events = [
            _event(
                now - (5 - index) * 60,
                sender_id,
                f".讨伐青元子 {action}",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_action_op_id(event_key, sender_id, action, index + 1, 0),
                chain_id=f"world_boss:{event_key}",
            )
            for index in range(6)
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("world boss over attempts", breach)

    def test_marked_world_boss_status_retry_chain_does_not_same_command_fuse(self):
        now = int(time.time())
        sender_id = 301299112
        event_key = "test-status"
        events = [
            _event(
                now - 12,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_status_op_id(event_key, sender_id, 0, now - 12),
                chain_id=f"world_boss:{event_key}",
            ),
            _event(
                now - 6,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="retry",
                op_id=_world_boss_status_op_id(event_key, sender_id, 1, now - 6),
                chain_id=f"world_boss:{event_key}",
            ),
            _event(
                now,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="retry",
                op_id=_world_boss_status_op_id(event_key, sender_id, 2, now),
                chain_id=f"world_boss:{event_key}",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_marked_world_boss_status_retry_chain_does_not_send_burst_within_retry_cap(self):
        now = int(time.time())
        cfg = self._config()
        cfg.total_2m_limit = 5
        sender_id = 301299112
        event_key = "test-status"
        events = [
            _event(
                now - (2 - try_no) * 45,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst" if try_no == 0 else "retry",
                op_id=_world_boss_status_op_id(event_key, sender_id, try_no, now - (2 - try_no) * 45),
                chain_id=f"world_boss:{event_key}",
            )
            for try_no in range(3)
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_marked_world_boss_status_retry_above_cap_still_fuses(self):
        now = int(time.time())
        sender_id = 301299112
        event_key = "test-status"
        events = [
            _event(
                now - (3 - try_no) * 75,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst" if try_no == 0 else "retry",
                op_id=_world_boss_status_op_id(event_key, sender_id, try_no, now - (3 - try_no) * 75),
                chain_id=f"world_boss:{event_key}",
            )
            for try_no in range(4)
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("invalid world boss status retry", breach)

    def test_cross_day_world_boss_status_retry_still_fuses(self):
        now = int(safety_watchdog.datetime(2026, 6, 25, 7, 20, 0, tzinfo=safety_watchdog.TZ_LOCAL).timestamp())
        sender_id = 301299112
        events = [
            _event(
                now - 6,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_status_op_id("2026-06-23:10796803", sender_id, 0, now - 6),
                chain_id="world_boss:2026-06-23:10796803",
            ),
            _event(
                now,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="retry",
                op_id=_world_boss_status_op_id("2026-06-23:10796803", sender_id, 1, now),
                chain_id="world_boss:2026-06-23:10796803",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("invalid world boss status retry", breach)

    def test_unmarked_world_boss_status_retry_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        events = [
            _event(now - 6, sender_id, ".世界boss", family="world_boss", source_module="真仙试锋", priority="event_burst"),
            _event(now, sender_id, ".世界boss", family="world_boss", source_module="真仙试锋", priority="retry"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("invalid world boss status retry", breach)

    def test_marked_world_boss_status_burst_still_fuses(self):
        now = time.time()
        cfg = self._config()
        cfg.total_2m_limit = 5
        events = [
            _event(
                now - (5 - index),
                3000 + index,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_status_op_id("test", 3000 + index, 0, now - (5 - index)),
                chain_id="world_boss:test",
            )
            for index in range(6)
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_marked_world_boss_status_global_gap_still_fuses(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 2, 1001, ".小世界"),
            _event(
                now,
                1002,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=_world_boss_status_op_id("test", 1002, 0, now),
                chain_id="world_boss:test",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_marked_world_boss_status_repeat_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        events = [
            _event(
                now - 10,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=f"world_boss:test:status:{sender_id}:1",
                chain_id="world_boss:test",
            ),
            _event(
                now,
                sender_id,
                ".世界boss",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id=f"world_boss:test:status:{sender_id}:2",
                chain_id="world_boss:test",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_marked_world_boss_action_without_valid_op_id_still_fuses(self):
        now = time.time()
        sender_id = 301299112
        events = [
            _event(
                now,
                sender_id,
                ".讨伐青元子 镇魂",
                family="world_boss",
                source_module="真仙试锋",
                priority="event_burst",
                op_id="world_boss:test:action:bad",
                chain_id="world_boss:test",
            )
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("invalid world boss op_id", breach)

    def test_verified_marked_heart_choices_do_not_count_as_send_burst(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425

        def heart_choice(round_no, try_no):
            chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round{round_no}"
            return {
                "priority": "retry" if try_no else "urgent_reactive",
                "op_id": f"{chain_id}:try{try_no}:.稳",
                "chain_id": chain_id,
            }

        events = [
            _event(now - 90, 1001, ".我的侍妾"),
            _event(now - 80, 1002, ".小世界"),
            _event(now - 70, 1003, ".灵树状态"),
            _event(now - 20, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 0)),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 1)),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2, 0)),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_multi_sender_unmarked_heart_burst_is_not_exempt(self):
        now = time.time()
        events = [
            _event(now - 20, 1001, ".稳", 9754425, "concubine_heart", "共历心劫"),
            _event(now - 18, 1002, ".稳", 9754425, "concubine_heart", "共历心劫"),
            _event(now - 16, 1003, ".稳", 9754425, "concubine_heart", "共历心劫"),
            _event(now - 14, 1004, ".稳", 9754425, "concubine_heart", "共历心劫"),
            _event(now - 12, 1005, ".稳", 9754425, "concubine_heart", "共历心劫"),
            _event(now - 10, 1006, ".稳", 9754425, "concubine_heart", "共历心劫"),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_unmarked_alternating_heart_choices_are_not_exempt(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 20, sender_id, ".稳", 9754425),
            _event(now - 18, sender_id, ".狠", 9754425),
            _event(now - 16, sender_id, ".骗", 9754425),
            _event(now - 14, sender_id, ".稳", 9754425),
            _event(now - 12, sender_id, ".狠", 9754425),
            _event(now - 10, sender_id, ".骗", 9754425),
        ]
        cfg = self._config()
        cfg.total_2m_limit = 5

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("send burst", breach)

    def test_non_virtual_dungeon_join_repeat_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 30, sender_id, ".加入坠魔谷 394"),
            _event(now - 28, sender_id, ".加入坠魔谷 394"),
            _event(now - 26, sender_id, ".加入黄龙山 395"),
            _event(now - 24, sender_id, ".加入黄龙山 395"),
            _event(now - 22, sender_id, ".加入苍坤洞府 396"),
            _event(now - 20, sender_id, ".加入苍坤洞府 396"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 0

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_sect_teach_three_step_chain_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 120, sender_id, ".宗门传功"),
            _event(now - 70, sender_id, ".宗门传功"),
            _event(now - 25, sender_id, ".宗门传功"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_sect_teach_fourth_attempt_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        events = [
            _event(now - 180, sender_id, ".宗门传功"),
            _event(now - 130, sender_id, ".宗门传功"),
            _event(now - 80, sender_id, ".宗门传功"),
            _event(now - 30, sender_id, ".宗门传功"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("sect teach over attempts", breach)

    def test_concubine_recovery_status_recheck_is_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3922509228
        events = [
            _event(now - 90, sender_id, ".我的侍妾"),
            _event(now - 64, sender_id, ".每日问安"),
            _event(now - 31, sender_id, ".我的侍妾"),
            _event(now - 9, sender_id, ".储物袋"),
            _event(now - 1, sender_id, ".赠予侍妾 灵石*270"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_direct_concubine_status_repeat_still_fuses(self):
        now = time.time()
        sender_id = 3922509228
        events = [
            _event(now - 90, sender_id, ".我的侍妾"),
            _event(now - 31, sender_id, ".我的侍妾"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())
        self.assertIn("same command repeat", breach)

    def test_concubine_heart_choice_chain_does_not_fuse(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425
        events = [
            _event(now - 17, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 6, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 1, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_unmarked_heart_choice_after_unrelated_command_global_fuses(self):
        now = time.time()
        prompt_msg_id = 9754425
        events = [
            _event(now - 12, 3870643893, ".天机代卜", 0, "concubine_tianji", "天机代卜"),
            _event(now - 10, 8659059191, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 4, 8659059191, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 1, 8659059191, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("global lock breach", breach)

    def test_marked_heart_choice_after_other_heart_action_does_not_global_fuse(self):
        now = time.time()
        prompt_msg_id = 10475775
        sender_id = 3711993781
        chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round1"
        events = [
            _event(now - 2, 3800619925, ".共历心劫", 10475603, "concubine_heart", "共历心劫"),
            _event(
                now - 1,
                sender_id,
                ".稳",
                prompt_msg_id,
                "concubine_heart",
                "共历心劫",
                priority="urgent_reactive",
                op_id=f"{chain_id}:try0:.稳",
                chain_id=chain_id,
            ),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_marked_heart_choice_after_unrelated_action_does_not_global_fuse(self):
        now = time.time()
        prompt_msg_id = 10479257
        sender_id = 3943539390
        chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round1"
        events = [
            _event(
                now - 1,
                3872695780,
                ".侍妾远航 冒险",
                family="concubine_voyage",
                source_module="侍妾远航",
                priority="chain",
            ),
            _event(
                now,
                sender_id,
                ".稳",
                prompt_msg_id,
                "concubine_heart",
                "共历心劫",
                priority="urgent_reactive",
                op_id=f"{chain_id}:try0:.稳",
                chain_id=chain_id,
            ),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_concubine_heart_choice_fourth_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425
        events = [
            _event(now - 20, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
            _event(now - 1, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫"),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("same command repeat", breach)

    def test_marked_heart_choice_old_round_retry_is_not_safe(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425

        def heart_choice(round_no, try_no):
            chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round{round_no}"
            return {
                "priority": "retry" if try_no else "urgent_reactive",
                "op_id": f"{chain_id}:try{try_no}:.稳",
                "chain_id": chain_id,
            }

        events = [
            _event(now - 25, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 20, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 0)),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 1)),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2, 0)),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 1)),
        ]

        self.assertFalse(safety_watchdog.is_safe_marked_heart_choice_repeat(events[1:]))

    def test_marked_heart_choice_valid_sequence_still_safe(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425

        def heart_choice(round_no, try_no):
            chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round{round_no}"
            return {
                "priority": "retry" if try_no else "urgent_reactive",
                "op_id": f"{chain_id}:try{try_no}:.稳",
                "chain_id": chain_id,
            }

        events = [
            _event(now - 25, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 20, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 0)),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 1)),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2, 0)),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2, 1)),
            _event(now - 1, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(3, 0)),
        ]

        self.assertTrue(safety_watchdog.is_safe_marked_heart_choice_repeat(events[1:]))

    def test_marked_heart_choice_window_suffix_does_not_fuse(self):
        now = time.time()
        sender_id = 3943773722
        prompt_msg_id = 10477436

        def heart_choice(round_no):
            chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round{round_no}"
            return {
                "priority": "urgent_reactive",
                "op_id": f"{chain_id}:try0:.稳",
                "chain_id": chain_id,
            }

        events = [
            _event(now - 20, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2)),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(3)),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_concubine_heart_controlled_retry_does_not_fuse_fourth_choice(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425

        def heart_choice(round_no, try_no):
            chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round{round_no}"
            return {
                "priority": "retry" if try_no else "urgent_reactive",
                "op_id": f"{chain_id}:try{try_no}:.稳",
                "chain_id": chain_id,
            }

        events = [
            _event(now - 20, sender_id, ".共历心劫", 9754314, "concubine_heart", "共历心劫"),
            _event(now - 15, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 0)),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(1, 1)),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(2, 0)),
            _event(now - 1, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **heart_choice(3, 0)),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 12

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_concubine_heart_duplicate_controlled_retry_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425
        chain_id = f"concubine_heart_choice:{sender_id}:{prompt_msg_id}:round1"
        retry_kwargs = {
            "priority": "retry",
            "op_id": f"{chain_id}:try1:.稳",
            "chain_id": chain_id,
        }
        events = [
            _event(
                now - 15,
                sender_id,
                ".稳",
                prompt_msg_id,
                "concubine_heart",
                "共历心劫",
                priority="urgent_reactive",
                op_id=f"{chain_id}:try0:.稳",
                chain_id=chain_id,
            ),
            _event(now - 10, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **retry_kwargs),
            _event(now - 5, sender_id, ".稳", prompt_msg_id, "concubine_heart", "共历心劫", **retry_kwargs),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_unmarked_heart_choice_repeat_still_fuses(self):
        now = time.time()
        sender_id = 8659059191
        prompt_msg_id = 9754425
        events = [
            _event(now - 15, sender_id, ".稳", prompt_msg_id),
            _event(now - 10, sender_id, ".稳", prompt_msg_id),
            _event(now - 5, sender_id, ".稳", prompt_msg_id),
        ]
        cfg = self._config()
        cfg.min_any_gap_sec = 0

        breach = safety_watchdog.find_send_breach(events, now, cfg)

        self.assertIn("same command repeat", breach)

    def test_stale_fuse_marker_re_fuses_when_global_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '1')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "old", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            with patch.object(safety_watchdog, "send_log_via_bot", return_value="sent") as send_mock:
                safety_watchdog.perform_fuse(cfg, {"ADMIN_ID": "12345"}, "new breach")

            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                value = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()[0]
            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("0", value)
        self.assertEqual("new breach", payload["reason"])
        send_mock.assert_called_once()
        self.assertIn("tg://user?id=12345", send_mock.call_args.args[1])

    def test_existing_fuse_marker_keeps_disabled_global_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '0')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "old", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            safety_watchdog.perform_fuse(cfg, {}, "new breach")

            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("old", payload["reason"])

    def test_existing_same_reason_marker_reasserts_pause_without_notify(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('global_enabled', '1')")
                conn.commit()
            marker = state_dir / "safety_watchdog_fused.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reason": "same breach", "actions": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            cfg = self._config()
            cfg.project_root = root
            cfg.action = "soft"
            cfg.dry_run = False

            with patch.object(safety_watchdog, "send_log_via_bot", return_value="sent") as send_mock, \
                    patch("builtins.print") as print_mock:
                safety_watchdog.perform_fuse(cfg, {"ADMIN_ID": "12345"}, "same breach")

            with sqlite3.connect(str(state_dir / "chaogu_state.db")) as conn:
                value = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()[0]
            payload = safety_watchdog.json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual("0", value)
        self.assertEqual("same breach", payload["reason"])
        self.assertEqual([], payload["actions"])
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("already fused for same reason; refreshed global_enabled=0", printed)
        send_mock.assert_not_called()

    def test_fuse_message_mentions_all_configured_admins(self):
        message = safety_watchdog.format_fuse_message(
            "send burst: <bad>",
            "soft",
            ["global_enabled=0"],
            env={"ADMIN_ID": "123", "ADMIN_IDS": "456, bad,123"},
        )

        self.assertIn("send burst: &lt;bad&gt;", message)
        self.assertIn('tg://user?id=123', message)
        self.assertIn('tg://user?id=456', message)
        self.assertEqual(1, message.count('tg://user?id=123'))

    def test_send_log_via_bot_uses_html_parse_mode_for_mentions(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _limit):
                return b'{"ok":true}'

        def fake_urlopen(url, data=None, timeout=None):
            captured["url"] = url
            captured["payload"] = safety_watchdog.urllib.parse.parse_qs(data.decode())
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.object(safety_watchdog.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = safety_watchdog.send_log_via_bot(
                {"LOG_BOT_TOKEN": "token", "LOG_GROUP_ID": "-1001"},
                '<a href="tg://user?id=123">@管理员</a>',
            )

        self.assertIn("log bot ok", result)
        self.assertIn("/bottoken/sendMessage", captured["url"])
        self.assertEqual(["HTML"], captured["payload"]["parse_mode"])
        self.assertEqual(["-1001"], captured["payload"]["chat_id"])

    def test_soft_breach_requires_two_confirming_hits(self):
        state = safety_watchdog.BreachConfirmationState()
        reason = "same command repeat: 3943773722:.稳 gap 5.0s"

        self.assertFalse(safety_watchdog.should_fuse_breach(reason, state, 1000.0))
        self.assertEqual(1, state.hits)
        self.assertTrue(safety_watchdog.should_fuse_breach(reason, state, 1015.0))
        self.assertEqual(2, state.hits)

    def test_soft_breach_confirmation_resets_after_quiet_window(self):
        state = safety_watchdog.BreachConfirmationState()
        reason = "global lock breach: gap 2.0s between a and b"

        self.assertFalse(safety_watchdog.should_fuse_breach(reason, state, 1000.0))
        self.assertFalse(
            safety_watchdog.should_fuse_breach(
                "",
                state,
                1015.0,
            )
        )
        self.assertFalse(safety_watchdog.should_fuse_breach(reason, state, 1020.0))
        self.assertEqual(1, state.hits)

    def test_hard_breach_fuses_immediately(self):
        state = safety_watchdog.BreachConfirmationState()

        self.assertTrue(safety_watchdog.should_fuse_breach("send burst: 8+ sends in 120s", state, 1000.0))
        self.assertEqual(0, state.hits)

    def test_reset_marker_without_epoch_falls_back_to_file_mtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = safety_watchdog.Path(tmpdir)
            state_dir = root / "data" / "state"
            state_dir.mkdir(parents=True)
            marker = state_dir / "safety_watchdog_reset.json"
            marker.write_text(
                safety_watchdog.json.dumps({"reset_at": "2026-06-06 09:48:44 CST"}, ensure_ascii=False),
                encoding="utf-8",
            )

            reset_after = safety_watchdog.get_reset_after_epoch(root)

        self.assertGreater(reset_after, 0)

    def test_controlled_retry_same_command_generic_does_not_fuse(self):
        now = time.time()
        sender_id = 3504367852
        events = [
            _event(now - 55, sender_id, ".灵树灌溉", family="tree_water", source_module="灵树", priority="normal"),
            _event(now, sender_id, ".灵树灌溉", family="tree_water", source_module="灵树", priority="retry"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_controlled_retry_after_normal_command_does_not_global_fuse(self):
        now = time.time()
        cfg = self._config()
        cfg.min_any_gap_sec = 12
        events = [
            _event(now - 11, 8659059191, ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）", family="pet", source_module="法宝", priority="normal"),
            _event(now, 3504367852, ".灵树灌溉", family="tree_water", source_module="灵树", priority="retry"),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, cfg))

    def test_phaseful_chain_relaunch_does_not_same_command_fuse(self):
        now = time.time()
        sender_id = 3823558636
        events = [
            _event(
                now - 70,
                sender_id,
                ".深度闭关",
                family="deep_retreat",
                source_module="深度闭关",
                priority="chain",
            ),
            _event(
                now,
                sender_id,
                ".深度闭关",
                family="deep_retreat",
                source_module="深度闭关",
                priority="chain",
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_storage_bag_retry_chain_allows_three_fast_resends(self):
        now = time.time()
        sender_id = 3504367852
        chain_id = "storage_bag:test-op"
        command = ".上架 灵石*1 换 妖丹*3"
        events = [
            _event(
                now - 15,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="event_burst",
                op_id=f"{chain_id}:storage_bag_listing:send:0",
                chain_id=chain_id,
            ),
            _event(
                now - 10,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="retry",
                op_id=f"{chain_id}:storage_bag_listing:retry:1",
                chain_id=chain_id,
            ),
            _event(
                now - 5,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="retry",
                op_id=f"{chain_id}:storage_bag_listing:retry:2",
                chain_id=chain_id,
            ),
            _event(
                now,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="retry",
                op_id=f"{chain_id}:storage_bag_listing:retry:3",
                chain_id=chain_id,
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_storage_bag_retry_chain_allows_chain_priority_initial_send(self):
        now = time.time()
        sender_id = 3504367852
        chain_id = "storage_bag:test-op"
        command = ".上架 灵石*1 换 妖丹*3"
        events = [
            _event(
                now - 5,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="chain",
                op_id=f"{chain_id}:storage_bag_listing:send:0",
                chain_id=chain_id,
            ),
            _event(
                now,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="retry",
                op_id=f"{chain_id}:storage_bag_listing:retry:1",
                chain_id=chain_id,
            ),
        ]

        self.assertEqual("", safety_watchdog.find_send_breach(events, now, self._config()))

    def test_storage_bag_retry_chain_skipped_try_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        chain_id = "storage_bag:test-op"
        command = ".上架 灵石*1 换 妖丹*3"
        events = [
            _event(
                now - 5,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="event_burst",
                op_id=f"{chain_id}:storage_bag_listing:send:0",
                chain_id=chain_id,
            ),
            _event(
                now,
                sender_id,
                command,
                family="storage_bag_listing",
                source_module="储物袋",
                priority="retry",
                op_id=f"{chain_id}:storage_bag_listing:retry:2",
                chain_id=chain_id,
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_storage_bag_retry_duplicate_op_id_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        chain_id = "storage_bag:test-op"
        command = ".赠送 妖丹*3"
        retry_op_id = f"{chain_id}:storage_bag_gift:retry:1"
        events = [
            _event(
                now - 10,
                sender_id,
                command,
                family="storage_bag_gift",
                source_module="储物袋",
                priority="event_burst",
                op_id=f"{chain_id}:storage_bag_gift:send:0",
                chain_id=chain_id,
            ),
            _event(
                now - 5,
                sender_id,
                command,
                family="storage_bag_gift",
                source_module="储物袋",
                priority="retry",
                op_id=retry_op_id,
                chain_id=chain_id,
            ),
            _event(
                now,
                sender_id,
                command,
                family="storage_bag_gift",
                source_module="储物袋",
                priority="retry",
                op_id=retry_op_id,
                chain_id=chain_id,
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_storage_bag_retry_without_op_id_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        command = ".购买 22028"
        events = [
            _event(
                now - 5,
                sender_id,
                command,
                family="storage_bag_buy",
                source_module="储物袋",
                priority="event_burst",
            ),
            _event(
                now,
                sender_id,
                command,
                family="storage_bag_buy",
                source_module="储物袋",
                priority="retry",
            ),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)

    def test_retry_without_intent_metadata_still_fuses(self):
        now = time.time()
        sender_id = 3504367852
        events = [
            _event(now - 55, sender_id, ".灵树灌溉", family="tree_water", source_module="灵树", priority="normal"),
            _event(now, sender_id, ".灵树灌溉", priority="retry"),
        ]

        breach = safety_watchdog.find_send_breach(events, now, self._config())

        self.assertIn("same command repeat", breach)


if __name__ == "__main__":
    unittest.main()
