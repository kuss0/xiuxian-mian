from datetime import datetime

from model.config import CMD_NODE_SEARCH, TZ_LOCAL
from model.features import search_node
from model.timing import CD_STATE_NO_RECORD, CD_STATE_ON_CD, CD_STATE_READY, CD_STATE_UNPARSEABLE


def _ts(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()


def test_search_node_blocks_below_huashen_or_unknown_realm():
    now = _ts("2026-06-15 12:00:00")

    below = search_node.decide_search_node_api_fallback(
        {"cultivation_level": "元婴中期", "shenshi_points": 200, "last_node_search_time": None},
        now=now,
    )
    assert not below.should_send
    assert below.reason == "realm_blocked"

    unknown = search_node.decide_search_node_api_fallback(
        {"shenshi_points": 200, "last_node_search_time": None},
        now=now,
    )
    assert not unknown.should_send
    assert unknown.reason == "realm_blocked"


def test_search_node_blocks_missing_or_low_shenshi():
    now = _ts("2026-06-15 12:00:00")

    missing = search_node.decide_search_node_api_fallback(
        {"cultivation_level": "化神初期", "last_node_search_time": None},
        now=now,
    )
    assert not missing.should_send
    assert missing.reason == "shenshi_blocked"

    low = search_node.decide_search_node_api_fallback(
        {"cultivation_level": "化神初期", "shenshi_points": 99, "last_node_search_time": None},
        now=now,
    )
    assert not low.should_send
    assert low.reason == "shenshi_blocked"
    assert low.shenshi_points == 99


def test_search_node_allows_no_record_when_gates_pass():
    now = _ts("2026-06-15 12:00:00")

    decision = search_node.decide_search_node_api_fallback(
        {"cultivation_level": "化神初期", "shenshi_points": 100, "last_node_search_time": None},
        now=now,
    )

    assert decision.should_send
    assert decision.command == CMD_NODE_SEARCH
    assert decision.reason == "ready"
    assert decision.cd_state == CD_STATE_NO_RECORD


def test_search_node_infers_realm_from_xiuwei_max():
    now = _ts("2026-06-15 12:00:00")

    decision = search_node.decide_search_node_api_fallback(
        {"xiuwei_max": 4_000_000, "spiritual_sense_points": 120, "last_node_search_time": None},
        now=now,
    )

    assert decision.should_send
    assert decision.realm == "化神初期"
    assert decision.shenshi_points == 120


def test_search_node_respects_strict_cd():
    now = _ts("2026-06-15 12:00:00")

    blocked = search_node.decide_search_node_api_fallback(
        {
            "cultivation_level": "化神中期",
            "shenshi_points": 200,
            "last_node_search_time": "2026-06-15T01:00:00+08:00",
        },
        now=now,
    )
    assert not blocked.should_send
    assert blocked.reason == "cd_blocked"
    assert blocked.cd_state == CD_STATE_ON_CD
    assert blocked.cd_reason == "within_window"

    ready = search_node.decide_search_node_api_fallback(
        {
            "cultivation_level": "化神中期",
            "shenshi_points": 200,
            "last_node_search_time": "2026-06-15T00:00:00+08:00",
        },
        now=now,
    )
    assert ready.should_send
    assert ready.cd_state == CD_STATE_READY


def test_search_node_unparseable_cd_fails_closed():
    now = _ts("2026-06-15 12:00:00")

    decision = search_node.decide_search_node_api_fallback(
        {"cultivation_level": "化神中期", "shenshi_points": 200, "last_node_search_time": "冷却中"},
        now=now,
    )

    assert not decision.should_send
    assert decision.reason == "cd_blocked"
    assert decision.cd_state == CD_STATE_UNPARSEABLE
    assert decision.cd_reason == CD_STATE_UNPARSEABLE


def test_search_node_reads_primary_payload_without_live_query():
    now = _ts("2026-06-15 12:00:00")

    decision = search_node.decide_search_node_api_fallback(
        {
            "identity_info_primary_payload": {
                "cultivation_level": "化神中期",
                "shenshi_points": "150",
                "last_node_search_time": None,
            }
        },
        now=now,
    )

    assert decision.should_send
    assert decision.command == CMD_NODE_SEARCH
