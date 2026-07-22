import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tools import business_semantic_report as report


TZ_LOCAL = timezone(timedelta(hours=8))


def _write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_small_world_report_uses_script_roots_and_marks_unexplained_delta():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [
            {"ts": "2026-07-18 10:00:00 UTC+8", "event_type": "sent", "message_id": 10, "sender_id": 101, "text": ".小世界"},
            {"ts": "2026-07-18 10:00:01 UTC+8", "event_type": "message", "message_id": 11, "sender_is_bot": True, "reply_to_msg_id": 10, "text": "【甲的小世界】\n🙏 信仰: 98 / 100\n⚖️ 稳定: 100 / 100"},
            {"ts": "2026-07-18 10:01:00 UTC+8", "event_type": "sent", "message_id": 12, "sender_id": 101, "text": ".显灵"},
            {"ts": "2026-07-18 10:01:01 UTC+8", "event_type": "message", "message_id": 13, "sender_is_bot": True, "reply_to_msg_id": 12, "text": "显灵成功！(信仰 +2, 稳定 +3, 人口 +0)"},
            {"ts": "2026-07-18 10:02:00 UTC+8", "event_type": "sent", "message_id": 14, "sender_id": 101, "text": ".小世界"},
            {"ts": "2026-07-18 10:02:01 UTC+8", "event_type": "message", "message_id": 15, "sender_is_bot": True, "reply_to_msg_id": 14, "text": "【甲的小世界】\n🙏 信仰: 100 / 100\n⚖️ 稳定: 100 / 100"},
            {"ts": "2026-07-18 10:03:00 UTC+8", "event_type": "sent", "message_id": 16, "sender_id": 101, "text": ".小世界"},
            {"ts": "2026-07-18 10:03:01 UTC+8", "event_type": "message", "message_id": 17, "sender_is_bot": True, "reply_to_msg_id": 16, "text": "【甲的小世界】\n🙏 信仰: 90 / 100\n⚖️ 稳定: 100 / 100"},
            {"ts": "2026-07-18 10:04:00 UTC+8", "event_type": "message", "message_id": 18, "sender_is_bot": True, "reply_to_msg_id": 999, "text": "【玩家的小世界】\n🙏 信仰: 1 / 100"},
        ]
        _write_jsonl(root / "2026-07-18.log", rows)
        result = report.build_small_world_evidence(root, day="2026-07-18", days=1)

    assert result["script_roots"] == 4
    assert result["script_panels"] == 3
    assert result["summary"] == {"explained": 1, "partially_explained": 0, "unexplained": 1}
    assert result["deltas"][0]["status"] == "explained"
    assert result["deltas"][0]["expected_faith"] == 100
    assert result["deltas"][1]["status"] == "unexplained"
    assert result["deltas"][1]["expected_faith"] is None


def test_small_world_report_preserves_partial_disaster_and_theft_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [
            {
                "ts": "2026-07-18 09:00:00 UTC+8",
                "event_type": "message",
                "message_id": 1,
                "sender_id": 101,
                "sender_username": "old_name",
                "text": "普通消息",
            },
            {
                "ts": "2026-07-18 10:00:00 UTC+8",
                "event_type": "sent",
                "message_id": 10,
                "sender_id": 101,
                "sender_username": "new_name",
                "text": ".小世界",
            },
            {
                "ts": "2026-07-18 10:00:01 UTC+8",
                "event_type": "message",
                "message_id": 11,
                "sender_is_bot": True,
                "reply_to_msg_id": 10,
                "text": "【甲的小世界】\n🙏 信仰: 96 / 100\n⚖️ 稳定: 100 / 100",
            },
            {
                "ts": "2026-07-18 10:30:00 UTC+8",
                "event_type": "message",
                "message_id": 12,
                "sender_is_bot": True,
                "reply_to_msg_id": 0,
                "text": "道友 @old_name 的小世界遭遇地脉翻身！惨重代价: 信仰崩塌 -13 点",
            },
            {
                "ts": "2026-07-18 10:31:00 UTC+8",
                "event_type": "message",
                "message_id": 13,
                "sender_is_bot": True,
                "reply_to_msg_id": 0,
                "text": "道友 @old_name 的小世界遭遇邪神蛊惑！惨重代价: 库存香火损失 6550 点",
            },
            {
                "ts": "2026-07-18 11:00:00 UTC+8",
                "event_type": "sent",
                "message_id": 14,
                "sender_id": 101,
                "sender_username": "new_name",
                "text": ".小世界",
            },
            {
                "ts": "2026-07-18 11:00:01 UTC+8",
                "event_type": "message",
                "message_id": 15,
                "sender_is_bot": True,
                "reply_to_msg_id": 14,
                "text": "【甲的小世界】\n🙏 信仰: 82 / 100\n⚖️ 稳定: 90 / 100",
            },
        ]
        _write_jsonl(root / "2026-07-18.log", rows)
        result = report.build_small_world_evidence(root, day="2026-07-18", days=1)

    assert result["summary"] == {"explained": 0, "partially_explained": 1, "unexplained": 0}
    delta = result["deltas"][0]
    assert delta["expected_faith"] == 83
    assert delta["status"] == "partially_explained"
    assert {event["kind"] for event in delta["events"]} == {"faith_loss", "stock_loss"}
    assert result["events"][1]["incense_delta"] == -6550


def test_small_world_report_parses_spaced_absolute_faith_reply():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [
            {"ts": "2026-07-18 10:00:00 UTC+8", "event_type": "sent", "message_id": 10, "sender_id": 101, "text": ".小世界"},
            {"ts": "2026-07-18 10:00:01 UTC+8", "event_type": "message", "message_id": 11, "sender_is_bot": True, "reply_to_msg_id": 10, "text": "【甲的小世界】\n🙏 信仰: 69 / 100\n⚖️ 稳定: 79 / 100"},
            {"ts": "2026-07-18 10:01:00 UTC+8", "event_type": "sent", "message_id": 12, "sender_id": 101, "text": ".神迹 布道"},
            {"ts": "2026-07-18 10:01:01 UTC+8", "event_type": "message", "message_id": 13, "sender_is_bot": True, "reply_to_msg_id": 12, "text": "凡人狂热膜拜，信仰提升至 85，稳定提升至 84！"},
            {"ts": "2026-07-18 10:02:00 UTC+8", "event_type": "sent", "message_id": 14, "sender_id": 101, "text": ".小世界"},
            {"ts": "2026-07-18 10:02:01 UTC+8", "event_type": "message", "message_id": 15, "sender_is_bot": True, "reply_to_msg_id": 14, "text": "【甲的小世界】\n🙏 信仰: 85 / 100\n⚖️ 稳定: 84 / 100"},
        ]
        _write_jsonl(root / "2026-07-18.log", rows)
        result = report.build_small_world_evidence(root, day="2026-07-18", days=1)

    assert result["deltas"][0]["status"] == "explained"
    assert result["deltas"][0]["expected_faith"] == 85
    assert result["deltas"][0]["events"][0]["kind"] == "god_absolute_faith"


def test_miniapp_rate_report_finds_saturation_and_error_types():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = datetime(2026, 7, 18, 10, 0, 0, tzinfo=TZ_LOCAL).timestamp()
        rows = [
            {"created_at": base + index * 0.5, "method": "POST", "adapter_key": "trial", "step_key": "finish", "ok": True, "error_type": ""}
            for index in range(90)
        ]
        rows.append({"created_at": base + 30, "adapter_key": "trial", "step_key": "finish_business", "ok": True, "error_type": ""})
        rows.append({"created_at": base + 120, "method": "POST", "adapter_key": "trial", "step_key": "finish", "ok": False, "error_type": "rate_limit"})
        _write_jsonl(root / "trial-2026-07-18.jsonl", rows)
        result = report.build_miniapp_rate_evidence(root, day="2026-07-18", days=1)

    assert result["records"] == 91
    assert result["ignored_non_http_records"] == 1
    assert result["max_window_count"] == 90
    assert result["status"] == "saturated"
    assert result["error_counts"] == {"rate_limit": 1}


def test_report_defaults_to_read_only_data_sources():
    result = report.build_report(
        messages_dir=Path("/does/not/exist"),
        capture_dir=Path("/does/not/exist"),
        day="2026-07-18",
        days=1,
    )
    assert result["small_world"]["script_roots"] == 0
    assert result["miniapp_rate"]["records"] == 0
