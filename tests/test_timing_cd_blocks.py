from datetime import datetime

import pytest

from model.config import TZ_LOCAL
from model.timing import (
    CD_STATE_NO_RECORD,
    CD_STATE_ON_CD,
    CD_STATE_READY,
    CD_STATE_UNPARSEABLE,
    cd_blocks,
    cd_decision,
    cd_state,
)


def _ts(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL).timestamp()


def test_cd_blocks_no_record_values_are_ready():
    now = _ts("2026-06-14 12:00:00")

    for raw in (None, "", " ", "none", "None", "null", "undefined", 0, -1):
        assert cd_state(raw, now, 3600) == CD_STATE_NO_RECORD
        assert cd_blocks(raw, now, 3600) is False


def test_cd_blocks_numeric_timestamp_matrix():
    now = _ts("2026-06-14 12:00:00")

    assert cd_state(now - 3599, now, 3600) == CD_STATE_ON_CD
    assert cd_blocks(str(now - 3599), now, 3600) is True
    assert cd_state(now - 3600, now, 3600) == CD_STATE_READY
    assert cd_blocks(now - 3601, now, 3600) is False


def test_cd_decision_exposes_fail_closed_reason():
    now = _ts("2026-06-14 12:00:00")

    dirty = cd_decision("冷却中", now, 3600)
    assert dirty.state == CD_STATE_UNPARSEABLE
    assert dirty.blocks is True
    assert dirty.ready is False
    assert dirty.reason == CD_STATE_UNPARSEABLE
    assert dirty.last_at is None

    active = cd_decision(now - 30, now, 3600)
    assert active.state == CD_STATE_ON_CD
    assert active.blocks is True
    assert active.reason == "within_window"
    assert active.last_at == now - 30

    ready = cd_decision(None, now, 3600)
    assert ready.state == CD_STATE_NO_RECORD
    assert ready.blocks is False
    assert ready.ready is True


def test_cd_blocks_future_timestamp_fails_closed_as_on_cd():
    now = _ts("2026-06-14 12:00:00")

    assert cd_state(now + 1, now, 3600) == CD_STATE_ON_CD
    assert cd_blocks(now + 1, now, 3600) is True


def test_cd_blocks_parseable_local_datetime():
    now = _ts("2026-06-14 12:00:00")

    assert cd_state("2026-06-14 11:30:00", now, 3600) == CD_STATE_ON_CD
    assert cd_blocks("2026-06-14 11:30:00 UTC+8", now, 3600) is True
    assert cd_state("2026-06-14 10:59:59", now, 3600) == CD_STATE_READY
    assert cd_blocks("2026-06-14T10:59:59+08:00", now, 3600) is False


def test_cd_blocks_unparseable_non_empty_strings_fail_closed():
    now = _ts("2026-06-14 12:00:00")

    for raw in ("unknown", "not-a-date", "冷却中"):
        assert cd_state(raw, now, 3600) == CD_STATE_UNPARSEABLE
        assert cd_blocks(raw, now, 3600) is True


@pytest.mark.parametrize(
    "raw",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "inf",
        "+inf",
        "-inf",
        "Infinity",
        "-Infinity",
    ],
)
def test_cd_blocks_non_finite_raw_values_fail_closed(raw):
    now = _ts("2026-06-14 12:00:00")

    assert cd_state(raw, now, 3600) == CD_STATE_UNPARSEABLE
    assert cd_blocks(raw, now, 3600) is True
