import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from model import module_manifest
from model.real_message_candidates import build_candidate_sample_suggestions
from tools import real_message_candidate_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_fixture(path, rows=None):
    path.write_text(json.dumps(rows or {}, ensure_ascii=False), encoding="utf-8")


def test_candidate_report_suggests_missing_family_samples(tmp_path):
    source = tmp_path / "passive.jsonl"
    fixture = tmp_path / "fixture.json"
    _write_fixture(fixture)
    _write_jsonl(
        source,
        [
            {
                "family": "hehuan_escape",
                "event_type": "message",
                "msg_id": 1001,
                "matched_text": "你咬破舌尖，强行挣脱心印束缚。",
            }
        ],
    )

    report = build_candidate_sample_suggestions([source], fixture_path=fixture)

    assert report["total"] == 1
    assert report["by_family"] == {"hehuan_escape": 1}
    suggestion = report["suggestions"][0]
    assert suggestion["sample_id"].startswith("candidate.hehuan_escape.message.1001.")
    assert suggestion["payload"]["module"] == "hehuan"
    assert suggestion["payload"]["family"] == "hehuan_escape"
    assert "挣脱心印" in suggestion["payload"]["text"]


def test_candidate_report_reads_json_lines_from_log_files(tmp_path):
    source = tmp_path / "2026-07-18.log"
    fixture = tmp_path / "fixture.json"
    _write_fixture(fixture)
    _write_jsonl(
        source,
        [{"family": "hehuan_escape", "msg_id": 1003, "matched_text": "你强行挣脱心印束缚。"}],
    )

    report = build_candidate_sample_suggestions([source], fixture_path=fixture)

    assert report["total"] == 1
    assert report["suggestions"][0]["payload"]["family"] == "hehuan_escape"


def test_candidate_report_skips_archived_tree_family_by_default(tmp_path):
    source = tmp_path / "passive.jsonl"
    _write_jsonl(
        source,
        [
            {
                "family": "tree_panel",
                "event_type": "message",
                "msg_id": 1001,
                "matched_text": "【灵树状态】 灵气充盈，可进行灌溉。",
            }
        ],
    )

    default_report = build_candidate_sample_suggestions([source], fixture_path=FIXTURE_PATH)
    archived_report = build_candidate_sample_suggestions(
        [source],
        fixture_path=FIXTURE_PATH,
        include_covered=True,
        include_archived=True,
    )

    assert default_report["total"] == 0
    assert default_report["skipped"]["archived_family"] == 1
    assert archived_report["total"] == 1
    assert archived_report["suggestions"][0]["payload"]["module"] == "灵树"


def test_candidate_report_skips_covered_family_unless_requested(tmp_path):
    source = tmp_path / "passive.jsonl"
    fixture = tmp_path / "fixture.json"
    _write_fixture(
        fixture,
        {
            "hehuan.escape.covered": {
                "source": "unit",
                "module": "hehuan",
                "family": "hehuan_escape",
                "event_type": "message",
                "text": "你咬破舌尖，强行挣脱心印束缚。",
            }
        },
    )
    _write_jsonl(
        source,
        [
            {
                "family": "hehuan_escape",
                "event_type": "message",
                "msg_id": 1002,
                "matched_text": "你咬破舌尖，强行挣脱心印束缚。",
            }
        ],
    )

    default_report = build_candidate_sample_suggestions([source], fixture_path=fixture)
    include_report = build_candidate_sample_suggestions([source], fixture_path=fixture, include_covered=True)

    assert default_report["total"] == 0
    assert default_report["skipped"]["covered_family"] == 1
    assert include_report["total"] == 1
    assert include_report["suggestions"][0]["payload"]["module"] == "hehuan"


def test_candidate_report_is_conservative_without_family_or_known_mapping(tmp_path):
    source = tmp_path / "passive.jsonl"
    fixture = tmp_path / "fixture.json"
    _write_fixture(fixture)
    _write_jsonl(
        source,
        [
            {"matched_text": "【灵树状态】 灵气充盈。"},
            {"family": "unknown_family", "matched_text": "无法归属。"},
            {"family": "hehuan_escape", "matched_text": ""},
        ],
    )

    report = build_candidate_sample_suggestions([source], fixture_path=fixture)

    assert report["total"] == 0
    assert report["skipped"]["no_family"] == 1
    assert report["skipped"]["unknown_family"] == 1
    assert report["skipped"]["no_text"] == 1


def test_candidate_report_can_filter_by_module(tmp_path):
    source = tmp_path / "passive.jsonl"
    fixture = tmp_path / "fixture.json"
    _write_fixture(fixture)
    _write_jsonl(
        source,
        [
            {"family": "guanxing_query", "matched_text": "观星台星辉流转。"},
            {"family": "hehuan_escape", "matched_text": "你咬破舌尖，强行挣脱心印束缚。"},
        ],
    )

    report = build_candidate_sample_suggestions([source], fixture_path=fixture, module="合欢宗")

    assert report["total"] == 1
    assert report["suggestions"][0]["payload"]["family"] == "hehuan_escape"
    assert module_manifest.get_module_name_for_reply_family("hehuan_escape") == "合欢宗"


def test_candidate_cli_outputs_fixture_suggestions(tmp_path):
    source = tmp_path / "passive.jsonl"
    _write_jsonl(
        source,
        [{"family": "hehuan_escape", "msg_id": 22029, "matched_text": "你咬破舌尖，强行挣脱心印束缚。"}],
    )

    out = io.StringIO()
    with redirect_stdout(out):
        code = real_message_candidate_report.main([
            "--source",
            str(source),
            "--fixture-path",
            str(FIXTURE_PATH),
            "--include-covered",
            "--family",
            "hehuan_escape",
        ])

    assert code == 0
    text = out.getvalue()
    assert "【真实文案候选报告】" in text
    assert "候选: 1" in text
    assert "candidate.hehuan_escape.message.22029" in text
    assert "不写 fixture，不发送游戏命令，不读取 API" in text
